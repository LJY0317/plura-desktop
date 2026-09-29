from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plura_desktop.model_list_overlay import (  # noqa: E402
    ModelListOverlay,
    ModelListOverlayError,
)


class _FakeResponse:
    def __init__(self, status: int, value: object) -> None:
        self.status = status
        self._body = json.dumps(value).encode("utf-8")

    def read(self, _limit: int) -> bytes:
        return self._body


class ModelListOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "s" * 48

    def test_identity_is_loopback_only_and_secret_free(self) -> None:
        first = ModelListOverlay.create(
            "http://localhost:18741/v1/app-server-model-list",
            "CHATGPT_TELA_RUNTIME_TOKEN",
            self.secret,
        )
        second = ModelListOverlay.create(
            "http://127.0.0.1:18741/v1/app-server-model-list",
            "CHATGPT_TELA_RUNTIME_TOKEN",
            self.secret,
        )
        self.assertEqual(first.url, "http://127.0.0.1:18741/v1/app-server-model-list")
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertRegex(first.fingerprint, r"^[a-f0-9]{64}$")
        self.assertNotIn(self.secret, json.dumps(first.__dict__))

        with self.assertRaisesRegex(ValueError, "loopback"):
            ModelListOverlay.create(
                "https://example.com/v1/app-server-model-list",
                "CHATGPT_TELA_RUNTIME_TOKEN",
                self.secret,
            )
        with self.assertRaisesRegex(ValueError, "explicit callback path"):
            ModelListOverlay.create(
                "http://127.0.0.1:18741/",
                "CHATGPT_TELA_RUNTIME_TOKEN",
                self.secret,
            )
        with self.assertRaisesRegex(ValueError, "32 characters"):
            ModelListOverlay.create(
                "http://127.0.0.1:18741/v1/app-server-model-list",
                "CHATGPT_TELA_RUNTIME_TOKEN",
                "short",
            )

    def test_apply_sends_bearer_only_to_exact_loopback_callback_and_returns_result(self) -> None:
        overlay = ModelListOverlay.create(
            "http://127.0.0.1:18741/v1/app-server-model-list",
            "CHATGPT_TELA_RUNTIME_TOKEN",
            self.secret,
        )
        connection = unittest.mock.MagicMock()
        connection.getresponse.return_value = _FakeResponse(200, {
            "contractVersion": 1,
            "result": {"data": [{"id": "native"}, {"id": "web"}]},
        })
        with patch.dict(os.environ, {"CHATGPT_TELA_RUNTIME_TOKEN": self.secret}, clear=False), patch(
            "plura_desktop.model_list_overlay.http.client.HTTPConnection",
            return_value=connection,
        ) as factory:
            result = overlay.apply({"data": [{"id": "native"}]})

        factory.assert_called_once_with("127.0.0.1", 18741, timeout=2.0)
        request = connection.request.call_args
        self.assertEqual(request.args[:2], ("POST", "/v1/app-server-model-list"))
        headers = request.kwargs["headers"]
        self.assertEqual(headers["Authorization"], f"Bearer {self.secret}")
        self.assertEqual(result, {"data": [{"id": "native"}, {"id": "web"}]})

    def test_callback_failure_is_typed_for_proxy_native_fallback(self) -> None:
        overlay = ModelListOverlay.create(
            "http://127.0.0.1:18741/v1/app-server-model-list",
            "CHATGPT_TELA_RUNTIME_TOKEN",
            self.secret,
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ModelListOverlayError, "credential"):
                overlay.apply({"data": []})

        connection = unittest.mock.MagicMock()
        connection.getresponse.return_value = _FakeResponse(503, {"error": "unavailable"})
        with patch.dict(os.environ, {"CHATGPT_TELA_RUNTIME_TOKEN": self.secret}, clear=False), patch(
            "plura_desktop.model_list_overlay.http.client.HTTPConnection",
            return_value=connection,
        ):
            with self.assertRaisesRegex(ModelListOverlayError, "rejected"):
                overlay.apply({"data": []})


if __name__ == "__main__":
    unittest.main()
