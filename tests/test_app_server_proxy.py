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
)
from plura_desktop.routing import ResponsesRoute  # noqa: E402


def _free_ws_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"ws://127.0.0.1:{port}"


class AppServerProxyTests(unittest.TestCase):
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
                                "model_provider": "legacy_fixture",
                                "model_providers": {
                                    "legacy_fixture": {"name": "Legacy"},
                                },
                            },
                            "modelProvider": "legacy_fixture",
                        },
                    }
                )
                routed = json.loads(rewrite_app_server_request(source, self.route))
                params = routed["params"]
                self.assertEqual(params["config"]["model_provider"], "external_responses_runtime")
                self.assertEqual(params["modelProvider"], "external_responses_runtime")
                self.assertEqual(params["config"]["service_tier"], "default")
                self.assertIn("legacy_fixture", params["config"]["model_providers"])
                provider = params["config"]["model_providers"]["external_responses_runtime"]
                self.assertEqual(provider["base_url"], self.route.base_url)
                self.assertEqual(provider["env_key"], self.route.env_key)
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
                    peer.send_frame(0x1, b'{"id":1,"result":{"ok":true}}')

        upstream_thread = threading.Thread(target=upstream_server, daemon=True)
        upstream_thread.start()
        self.assertTrue(upstream_ready.wait(timeout=2.0))

        proxy = RoutePreservingAppServerProxy(proxy_endpoint, upstream_endpoint, self.route)
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
                response = peer.recv_message()
                self.assertEqual(response, (0x1, b'{"id":1,"result":{"ok":true}}'))

            routed = received.get(timeout=2.0)
            self.assertEqual(routed["method"], "thread/start")
            params = routed["params"]
            assert isinstance(params, dict)
            config = params["config"]
            assert isinstance(config, dict)
            self.assertEqual(config["service_tier"], "default")
            self.assertEqual(config["model_provider"], "external_responses_runtime")
            self.assertNotIn(self.secret, json.dumps(routed))
        finally:
            proxy.close()
            upstream_thread.join(timeout=2.0)
        self.assertFalse(proxy.is_alive)


if __name__ == "__main__":
    unittest.main()
