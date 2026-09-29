from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
from typing import Any
from urllib.parse import urlsplit

from .model_list_overlay import ModelListOverlay, ModelListOverlayError
from .routing import ResponsesRoute


_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_HTTP_HEADER_BYTES = 64 * 1024
_MAX_MESSAGE_BYTES = 16 * 1024 * 1024
_ROUTED_THREAD_METHODS = frozenset({"thread/start", "thread/resume", "thread/fork"})
_MODEL_LIST_METHOD = "model/list"


class AppServerProxyError(RuntimeError):
    pass


def rewrite_app_server_request(message: str, route: ResponsesRoute) -> str:
    """Overlay one Responses route on thread lifecycle requests only.

    Unrelated JSON-RPC messages are returned byte-for-byte. The route overlay is secret-free: the
    backend process already owns the credential environment variable and only its name crosses the
    loopback transport boundary.
    """

    try:
        value = json.loads(message)
    except json.JSONDecodeError:
        return message
    if not isinstance(value, dict) or value.get("method") not in _ROUTED_THREAD_METHODS:
        return message
    params = value.get("params")
    if not isinstance(params, dict):
        raise AppServerProxyError("Routed app-server request has non-object params")

    existing_config = params.get("config")
    if existing_config is None:
        config: dict[str, Any] = {}
    elif isinstance(existing_config, dict):
        config = dict(existing_config)
    else:
        raise AppServerProxyError("Routed app-server request has non-object config")

    overlay = route.thread_config_overlay()
    existing_providers = config.get("model_providers")
    if existing_providers is None:
        providers: dict[str, Any] = {}
    elif isinstance(existing_providers, dict):
        providers = dict(existing_providers)
    else:
        raise AppServerProxyError("Routed app-server request has non-object model_providers")
    providers.update(overlay["model_providers"])
    config["model_provider"] = overlay["model_provider"]
    config["model_providers"] = providers

    routed_params = dict(params)
    routed_params["config"] = config
    routed_params["modelProvider"] = route.provider_id
    routed = dict(value)
    routed["params"] = routed_params
    return json.dumps(routed, ensure_ascii=False, separators=(",", ":"))


def _rpc_id_key(value: Any) -> str | None:
    if value is None or isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return None


def model_list_request_key(message: str) -> str | None:
    try:
        value = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("method") != _MODEL_LIST_METHOD or "id" not in value:
        return None
    return _rpc_id_key(value.get("id"))


def overlay_model_list_response(
    message: str,
    pending_ids: set[str],
    overlay: ModelListOverlay,
) -> str:
    try:
        value = json.loads(message)
    except json.JSONDecodeError:
        return message
    if not isinstance(value, dict) or "id" not in value:
        return message
    key = _rpc_id_key(value.get("id"))
    if key is None or key not in pending_ids:
        return message
    pending_ids.discard(key)
    if "result" not in value:
        return message
    try:
        augmented = overlay.apply(value["result"])
    except ModelListOverlayError:
        return message
    routed = dict(value)
    routed["result"] = augmented
    return json.dumps(routed, ensure_ascii=False, separators=(",", ":"))


def _read_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("websocket peer closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_http_head(sock: socket.socket) -> tuple[str, dict[str, str]]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            if not data:
                raise EOFError("connection closed before websocket handshake")
            raise AppServerProxyError("incomplete websocket handshake")
        data.extend(chunk)
        if len(data) > _MAX_HTTP_HEADER_BYTES:
            raise AppServerProxyError("websocket handshake headers exceed limit")
    head, trailing = bytes(data).split(b"\r\n\r\n", 1)
    if trailing:
        raise AppServerProxyError("unexpected bytes after websocket handshake")
    lines = head.decode("latin-1").split("\r\n")
    if not lines or not lines[0]:
        raise AppServerProxyError("missing websocket handshake start line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            raise AppServerProxyError("malformed websocket handshake header")
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return lines[0], headers


def _websocket_accept(key: str) -> str:
    digest = hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _parse_ws_endpoint(value: str) -> tuple[str, int, str]:
    parsed = urlsplit(value)
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("App-server proxy endpoint must be loopback ws://")
    if parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ValueError("App-server proxy endpoint must include a valid loopback port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("App-server proxy endpoint must not include path, query, fragment, or credentials")
    return parsed.hostname, parsed.port, "/"


class _WebSocketPeer:
    def __init__(self, sock: socket.socket, *, send_masked: bool, expect_masked: bool) -> None:
        self.sock = sock
        self.send_masked = send_masked
        self.expect_masked = expect_masked
        self._write_lock = threading.Lock()

    def send_frame(self, opcode: int, payload: bytes) -> None:
        first = 0x80 | (opcode & 0x0F)
        length = len(payload)
        mask_bit = 0x80 if self.send_masked else 0
        if length < 126:
            header = bytes((first, mask_bit | length))
        elif length <= 0xFFFF:
            header = bytes((first, mask_bit | 126)) + struct.pack("!H", length)
        else:
            header = bytes((first, mask_bit | 127)) + struct.pack("!Q", length)
        if self.send_masked:
            mask = os.urandom(4)
            encoded = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            frame = header + mask + encoded
        else:
            frame = header + payload
        with self._write_lock:
            self.sock.sendall(frame)

    def recv_message(self) -> tuple[int, bytes] | None:
        message_opcode: int | None = None
        parts: list[bytes] = []
        total = 0
        while True:
            first, second = _read_exact(self.sock, 2)
            fin = bool(first & 0x80)
            if first & 0x70:
                raise AppServerProxyError("websocket extensions are not supported")
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            if masked != self.expect_masked:
                raise AppServerProxyError("websocket frame masking does not match peer role")
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", _read_exact(self.sock, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", _read_exact(self.sock, 8))[0]
            if opcode >= 0x8 and (not fin or length > 125):
                raise AppServerProxyError("invalid websocket control frame")
            mask = _read_exact(self.sock, 4) if masked else None
            payload = _read_exact(self.sock, length) if length else b""
            if mask is not None:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x8:
                try:
                    self.send_frame(0x8, payload)
                except OSError:
                    pass
                return None
            if opcode == 0x9:
                self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in {0x1, 0x2}:
                if message_opcode is not None:
                    raise AppServerProxyError("new websocket message started before prior fragments completed")
                message_opcode = opcode
            elif opcode == 0x0:
                if message_opcode is None:
                    raise AppServerProxyError("unexpected websocket continuation frame")
            else:
                raise AppServerProxyError("unsupported websocket opcode")
            parts.append(payload)
            total += len(payload)
            if total > _MAX_MESSAGE_BYTES:
                raise AppServerProxyError("websocket message exceeds proxy limit")
            if fin:
                assert message_opcode is not None
                return message_opcode, b"".join(parts)


def _connect_upstream(endpoint: str, requested_protocols: str | None) -> tuple[socket.socket, str | None]:
    host, port, path = _parse_ws_endpoint(endpoint)
    sock = socket.create_connection((host, port), timeout=5.0)
    sock.settimeout(None)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if requested_protocols:
        lines.append(f"Sec-WebSocket-Protocol: {requested_protocols}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    start, headers = _read_http_head(sock)
    if not start.startswith("HTTP/1.1 101 "):
        sock.close()
        raise AppServerProxyError("upstream app-server rejected websocket upgrade")
    if headers.get("upgrade", "").lower() != "websocket":
        sock.close()
        raise AppServerProxyError("upstream app-server returned an invalid websocket upgrade header")
    connection_tokens = {part.strip().lower() for part in headers.get("connection", "").split(",")}
    if "upgrade" not in connection_tokens:
        sock.close()
        raise AppServerProxyError("upstream app-server returned an invalid websocket connection header")
    if headers.get("sec-websocket-accept") != _websocket_accept(key):
        sock.close()
        raise AppServerProxyError("upstream app-server returned an invalid websocket accept key")
    selected_protocol = headers.get("sec-websocket-protocol")
    if selected_protocol is not None:
        requested = tuple(part.strip() for part in (requested_protocols or "").split(",") if part.strip())
        if "," in selected_protocol or selected_protocol.strip() != selected_protocol or selected_protocol not in requested:
            sock.close()
            raise AppServerProxyError("upstream app-server selected an unrequested websocket subprotocol")
    return sock, selected_protocol


class RoutePreservingAppServerProxy:
    """Loopback WebSocket proxy for launch-time route and optional model-list projection."""

    def __init__(
        self,
        listen_url: str,
        upstream_url: str,
        route: ResponsesRoute | None,
        model_list_overlay: ModelListOverlay | None = None,
    ) -> None:
        if route is None and model_list_overlay is None:
            raise ValueError("App-server proxy requires a Responses route or model-list overlay")
        self.listen_url = listen_url
        self.upstream_url = upstream_url
        self.route = route
        self.model_list_overlay = model_list_overlay
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        self._fatal_error: BaseException | None = None

    @property
    def fatal_error(self) -> BaseException | None:
        return self._fatal_error

    @property
    def is_alive(self) -> bool:
        thread = self._accept_thread
        return thread is not None and thread.is_alive() and self._fatal_error is None

    def start(self) -> None:
        if self._listener is not None:
            raise RuntimeError("App-server route proxy is already started")
        host, port, _ = _parse_ws_endpoint(self.listen_url)
        if host == "::1":
            listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("::1", port))
        else:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
        listener.listen(16)
        listener.settimeout(0.5)
        self._listener = listener
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="app-server-route-proxy",
            daemon=True,
        )
        self._accept_thread.start()

    def close(self) -> None:
        self._stop.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._connections_lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        thread = self._accept_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _accept_loop(self) -> None:
        try:
            assert self._listener is not None
            while not self._stop.is_set():
                try:
                    client, _ = self._listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    raise
                threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()
        except BaseException as error:
            if not self._stop.is_set():
                self._fatal_error = error

    def _track(self, sock: socket.socket) -> None:
        with self._connections_lock:
            self._connections.add(sock)

    def _untrack(self, sock: socket.socket) -> None:
        with self._connections_lock:
            self._connections.discard(sock)

    def _handle_client(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        self._track(client)
        try:
            client.settimeout(5.0)
            start, headers = _read_http_head(client)
            parts = start.split(" ")
            if len(parts) != 3 or parts[0] != "GET" or parts[1] != "/" or parts[2] != "HTTP/1.1":
                raise AppServerProxyError("invalid websocket request line")
            if headers.get("upgrade", "").lower() != "websocket":
                raise AppServerProxyError("missing websocket upgrade header")
            connection_tokens = {part.strip().lower() for part in headers.get("connection", "").split(",")}
            if "upgrade" not in connection_tokens:
                raise AppServerProxyError("missing websocket connection upgrade token")
            key = headers.get("sec-websocket-key")
            if not key or headers.get("sec-websocket-version") != "13":
                raise AppServerProxyError("invalid websocket version or key")
            upstream, selected_protocol = _connect_upstream(
                self.upstream_url,
                headers.get("sec-websocket-protocol"),
            )
            self._track(upstream)
            response = [
                "HTTP/1.1 101 Switching Protocols",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Accept: {_websocket_accept(key)}",
            ]
            if selected_protocol:
                response.append(f"Sec-WebSocket-Protocol: {selected_protocol}")
            client.sendall(("\r\n".join(response) + "\r\n\r\n").encode("latin-1"))
            client.settimeout(None)

            downstream_peer = _WebSocketPeer(client, send_masked=False, expect_masked=True)
            upstream_peer = _WebSocketPeer(upstream, send_masked=True, expect_masked=False)
            done = threading.Event()
            pending_model_lists: set[str] = set()
            pending_lock = threading.Lock()

            def pump(source: _WebSocketPeer, destination: _WebSocketPeer, *, client_to_server: bool) -> None:
                try:
                    while not done.is_set():
                        message = source.recv_message()
                        if message is None:
                            return
                        opcode, payload = message
                        if opcode == 0x1 and client_to_server:
                            text = payload.decode("utf-8")
                            if self.model_list_overlay is not None:
                                request_key = model_list_request_key(text)
                                if request_key is not None:
                                    with pending_lock:
                                        pending_model_lists.add(request_key)
                            if self.route is not None:
                                text = rewrite_app_server_request(text, self.route)
                            payload = text.encode("utf-8")
                        elif opcode == 0x1 and not client_to_server and self.model_list_overlay is not None:
                            text = payload.decode("utf-8")
                            with pending_lock:
                                text = overlay_model_list_response(
                                    text,
                                    pending_model_lists,
                                    self.model_list_overlay,
                                )
                            payload = text.encode("utf-8")
                        destination.send_frame(opcode, payload)
                except (EOFError, OSError, UnicodeDecodeError, AppServerProxyError):
                    return
                finally:
                    done.set()
                    for owned in (client, upstream):
                        try:
                            owned.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass

            server_to_client = threading.Thread(
                target=pump,
                args=(upstream_peer, downstream_peer),
                kwargs={"client_to_server": False},
                daemon=True,
            )
            server_to_client.start()
            pump(downstream_peer, upstream_peer, client_to_server=True)
            server_to_client.join(timeout=1.0)
        except (EOFError, OSError, AppServerProxyError):
            return
        finally:
            for owned in (client, upstream):
                if owned is None:
                    continue
                self._untrack(owned)
                try:
                    owned.close()
                except OSError:
                    pass
