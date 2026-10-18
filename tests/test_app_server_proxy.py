from __future__ import annotations

import json
from pathlib import Path
import queue
import socket
import sys
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plura_desktop.app_server_proxy import (  # noqa: E402
    AppServerProxyError,
    RoutePreservingAppServerProxy,
    _WebSocketPeer,
    _read_http_head,
    _websocket_accept,
    rewrite_app_server_request,
    model_list_request_id,
    overlay_model_list_response,
)
from plura_desktop.model_list_overlay import ModelListOverlay  # noqa: E402
from plura_desktop.routing import ResponsesRoute  # noqa: E402


def _free_ws_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"ws://127.0.0.1:{port}"


class AppServerProxyTests(unittest.TestCase):
    def test_model_list_overlay_is_exact_and_failure_preserves_native_result(self) -> None:
        class Callback:
            calls = 0

            def apply(self, result: object) -> object:
                self.calls += 1
                return {"data": [*result["data"], {"id": "web"}]}

        callback = Callback()
        pending = {model_list_request_id('{"id":7,"method":"model/list","params":{}}')}
        self.assertEqual(pending, {"7"})
        native = '{"id":7,"result":{"data":[{"id":"native"}]}}'
        augmented = json.loads(overlay_model_list_response(native, pending, callback))
        self.assertEqual([row["id"] for row in augmented["result"]["data"]], ["native", "web"])
        self.assertEqual(callback.calls, 1)
        self.assertEqual(pending, set())
        self.assertEqual(overlay_model_list_response(native, pending, callback), native)

        class FailingCallback:
            def apply(self, _result: object) -> object:
                raise TimeoutError("callback unavailable")

        self.assertEqual(overlay_model_list_response(native, {"7"}, FailingCallback()), native)
        self.assertIsNone(model_list_request_id('{"id":7,"method":"turn/start"}'))

    def test_model_list_overlay_rejects_non_loopback_callback(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            ModelListOverlay.create("https://example.com/model-list-overlay", "TEST_KEY", "s" * 48)

    def test_proxy_normalizes_localhost_listener_to_literal_loopback(self):
        route = ResponsesRoute.create(
            "http://127.0.0.1:9000/v1",
            "TEST_KEY",
            "s" * 48,
        )
        proxy = RoutePreservingAppServerProxy(
            "ws://localhost:19001",
            "ws://127.0.0.1:19002",
            route,
        )
        with patch("plura_desktop.app_server_proxy.socket.socket") as socket_factory:
            listener = socket_factory.return_value
            proxy.start()
            proxy.close()
        listener.bind.assert_called_once_with(("127.0.0.1", 19001))

    def setUp(self) -> None:
        self.secret = "s" * 48
        self.route = ResponsesRoute.create(
            "http://127.0.0.1:18741/v1",
            "CHATGPT_TELA_RUNTIME_TOKEN",
            self.secret,
        )

    def test_rewrite_overlays_only_thread_lifecycle_requests(self) -> None:
        for method in ("thread/start", "thread/resume", "thread/fork"):
            with self.subTest(method=method):
                source = json.dumps(
                    {
                        "id": 7,
                        "method": method,
                        "params": {
                            "threadId": "fixture-thread",
                            "config": {
                                "service_tier": "default",
                                "model_provider": "external_responses_runtime",
                                "model_providers": {
                                    "external_responses_runtime": {"name": "Legacy route"},
                                },
                            },
                            "modelProvider": "external_responses_runtime",
                        },
                    }
                )
                routed = json.loads(rewrite_app_server_request(source, self.route))
                params = routed["params"]
                self.assertEqual(params["config"]["model_provider"], "openai")
                self.assertEqual(params["config"]["openai_base_url"], self.route.base_url)
                self.assertEqual(params["modelProvider"], "openai")
                self.assertEqual(params["config"]["service_tier"], "default")
                self.assertEqual(
                    params["config"]["model_providers"]["external_responses_runtime"],
                    {"name": "Legacy route"},
                )
                self.assertNotIn(self.secret, json.dumps(routed))

        unrelated = '{"id":9,"method":"turn/start","params":{"threadId":"t"}}'
        self.assertEqual(rewrite_app_server_request(unrelated, self.route), unrelated)

    def test_rewrite_fails_closed_on_invalid_routed_config_shapes(self) -> None:
        with self.assertRaisesRegex(AppServerProxyError, "non-object params"):
            rewrite_app_server_request('{"method":"thread/start","params":null}', self.route)
        with self.assertRaisesRegex(AppServerProxyError, "non-object config"):
            rewrite_app_server_request(
                '{"method":"thread/start","params":{"config":"invalid"}}',
                self.route,
            )
        with self.assertRaisesRegex(AppServerProxyError, "non-object model_providers"):
            rewrite_app_server_request(
                '{"method":"thread/start","params":{"config":{"model_providers":[]}}}',
                self.route,
            )

    def test_loopback_proxy_rewrites_client_request_and_forwards_response(self) -> None:
        self._exercise_loopback()

    def test_failure_evidence_omits_payload_and_callback_failure_does_not_block_close(self):
        self._exercise_loopback(failure=True, callback_fails=True)

    def test_message_limit_records_exact_size_without_body(self):
        with patch("plura_desktop.app_server_proxy._MAX_MESSAGE_BYTES", 1500):
            self._exercise_loopback(failure=True, limit_failure=True)

    def _exercise_loopback(self, *, failure: bool = False, callback_fails: bool = False, limit_failure: bool = False) -> None:
        upstream_endpoint = _free_ws_endpoint()
        proxy_endpoint = _free_ws_endpoint()
        while proxy_endpoint == upstream_endpoint:
            proxy_endpoint = _free_ws_endpoint()
        upstream_port = int(upstream_endpoint.rsplit(":", 1)[1])
        received: queue.Queue[dict[str, object]] = queue.Queue()
        upstream_ready = threading.Event()

        def upstream_server() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", upstream_port))
                listener.listen(1)
                upstream_ready.set()
                connection, _ = listener.accept()
                with connection:
                    start, headers = _read_http_head(connection)
                    self.assertEqual(start, "GET / HTTP/1.1")
                    key = headers["sec-websocket-key"]
                    connection.sendall(
                        (
                            "HTTP/1.1 101 Switching Protocols\r\n"
                            "Upgrade: websocket\r\n"
                            "Connection: Upgrade\r\n"
                            f"Sec-WebSocket-Accept: {_websocket_accept(key)}\r\n\r\n"
                        ).encode("latin-1")
                    )
                    peer = _WebSocketPeer(connection, send_masked=False, expect_masked=True)
                    message = peer.recv_message()
                    assert message is not None
                    opcode, payload = message
                    self.assertEqual(opcode, 0x1)
                    received.put(json.loads(payload.decode("utf-8")))
                    peer.send_frame(0x1, b"x" * 2000 if limit_failure else b"\xffprivate-payload" if failure else b'{"id":1,"result":{"ok":true}}')
                    peer.send_frame(0x8, b"\x03\xe8")

        upstream_thread = threading.Thread(target=upstream_server, daemon=True)
        upstream_thread.start()
        self.assertTrue(upstream_ready.wait(timeout=2.0))

        failures = []
        def report_failure(event):
            failures.append(event)
            if callback_fails:
                raise OSError("private callback error")

        proxy = RoutePreservingAppServerProxy(proxy_endpoint, upstream_endpoint, self.route,
            transport_failure=report_failure)
        proxy.start()
        try:
            proxy_port = int(proxy_endpoint.rsplit(":", 1)[1])
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=2.0) as client:
                key = "dGhlIHNhbXBsZSBub25jZQ=="
                client.sendall(
                    (
                        "GET / HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{proxy_port}\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\n"
                        "Sec-WebSocket-Version: 13\r\n\r\n"
                    ).encode("latin-1")
                )
                start, headers = _read_http_head(client)
                self.assertTrue(start.startswith("HTTP/1.1 101 "))
                self.assertEqual(headers["sec-websocket-accept"], _websocket_accept(key))
                peer = _WebSocketPeer(client, send_masked=True, expect_masked=False)
                peer.send_frame(
                    0x1,
                    b'{"id":1,"method":"thread/start","params":{"config":{"service_tier":"default"}}}',
                )
                if failure:
                    try:
                        peer.recv_message()
                    except (EOFError, OSError):
                        pass
                else:
                    response = peer.recv_message()
                    self.assertEqual(response, (0x1, b'{"id":1,"result":{"ok":true}}'))
                    with self.assertRaises((EOFError, OSError)):
                        peer.recv_message()

            routed = received.get(timeout=2.0)
            self.assertEqual(routed["method"], "thread/start")
            params = routed["params"]
            assert isinstance(params, dict)
            config = params["config"]
            assert isinstance(config, dict)
            self.assertEqual(config["service_tier"], "default")
            self.assertEqual(config["model_provider"], "openai")
            self.assertEqual(config["openai_base_url"], self.route.base_url)
            self.assertNotIn(self.secret, json.dumps(routed))
        finally:
            proxy.close()
            upstream_thread.join(timeout=2.0)
        self.assertFalse(proxy.is_alive)


if __name__ == "__main__":
    unittest.main()
