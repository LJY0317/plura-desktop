from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit


_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_BODY_BYTES = 16 * 1024 * 1024


class ModelListOverlayError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelListOverlay:
    """Validated optional loopback callback for app-server `model/list` results.

    The callback is transport-only. Plura does not know or synthesize model semantics. A companion
    controller may transform the model-list result, while any callback failure leaves the first-party
    Native result untouched. The credential value stays in the inherited environment and only its
    hash participates in runtime ownership identity.
    """

    url: str
    env_key: str
    fingerprint: str

    @classmethod
    def create(cls, url: str, env_key: str, credential: str) -> "ModelListOverlay":
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Model-list overlay must use loopback http://")
        if parsed.port is None or not 1 <= parsed.port <= 65535:
            raise ValueError("Model-list overlay must include a valid loopback port")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Model-list overlay URL must not contain credentials, query, or fragment")
        if not parsed.path.startswith("/") or parsed.path == "/":
            raise ValueError("Model-list overlay URL must include an explicit callback path")
        if not _ENV_KEY.fullmatch(env_key):
            raise ValueError("Model-list overlay env key must be an uppercase environment variable name")
        if len(credential) < 32:
            raise ValueError("Model-list overlay credential must contain at least 32 characters")

        host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        assert host is not None
        rendered_host = f"[{host}]" if host == "::1" else host
        normalized = f"http://{rendered_host}:{parsed.port}{parsed.path}"
        material = json.dumps(
            {
                "url": normalized,
                "envKey": env_key,
                "credentialHash": hashlib.sha256(credential.encode("utf-8")).hexdigest(),
                "contractVersion": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            url=normalized,
            env_key=env_key,
            fingerprint=hashlib.sha256(material).hexdigest(),
        )

    def apply(self, result: Any) -> Any:
        credential = os.environ.get(self.env_key)
        if credential is None or len(credential) < 32:
            raise ModelListOverlayError("Model-list overlay credential is unavailable")
        payload = json.dumps(
            {"contractVersion": 1, "result": result},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _MAX_BODY_BYTES:
            raise ModelListOverlayError("Model-list overlay request exceeds bounded size")

        parsed = urlsplit(self.url)
        assert parsed.hostname is not None and parsed.port is not None
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2.0)
        try:
            connection.request(
                "POST",
                parsed.path,
                body=payload,
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            body = response.read(_MAX_BODY_BYTES + 1)
            if response.status < 200 or response.status >= 300:
                raise ModelListOverlayError("Model-list overlay callback rejected the request")
            if len(body) > _MAX_BODY_BYTES:
                raise ModelListOverlayError("Model-list overlay response exceeds bounded size")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ModelListOverlayError("Model-list overlay response is not valid JSON") from error
            if not isinstance(value, dict) or value.get("contractVersion") != 1 or "result" not in value:
                raise ModelListOverlayError("Model-list overlay response has an unsupported contract")
            return value["result"]
        except (OSError, http.client.HTTPException) as error:
            raise ModelListOverlayError("Model-list overlay callback is unavailable") from error
        finally:
            connection.close()
