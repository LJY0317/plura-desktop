from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plura_desktop.model_list_overlay import ModelListOverlay  # noqa: E402


class ModelListOverlayTests(unittest.TestCase):
    def test_exact_loopback_callback_carries_native_result_and_bearer(self) -> None:
        observed: list[dict[str, object]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_POST(self) -> None:
                size = int(self.headers["Content-Length"])
                observed.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(size)),
                })
                body = json.dumps({
                    "contractVersion": 1,
                    "result": {"data": [{"id": "native"}, {"id": "web"}]},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            overlay = ModelListOverlay.create(
                f"http://127.0.0.1:{server.server_port}/model-list-overlay",
                "TELA_CALLBACK_TOKEN", "s" * 48,
            )
            self.assertEqual(overlay.apply({"data": [{"id": "native"}]}), {
                "data": [{"id": "native"}, {"id": "web"}],
            })
            self.assertEqual(observed, [{
                "path": "/model-list-overlay",
                "authorization": f"Bearer {'s' * 48}",
                "body": {"contractVersion": 1, "result": {"data": [{"id": "native"}]}},
            }])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_non_loopback_and_invalid_contract_fail_closed(self) -> None:
        for url in (
            "https://example.com/model-list-overlay",
            "http://localhost:1234/model-list-overlay",
            "http://127.0.0.1:1234/",
            "http://127.0.0.1:1234/model-list-overlay?next=elsewhere",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ModelListOverlay.create(url, "TELA_CALLBACK_TOKEN", "s" * 48)
