from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.client import HTTPConnection
import json
import re
from typing import Any
from urllib.parse import urlsplit


_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


@dataclass(frozen=True)
class ModelListOverlay:
    """One loopback, authenticated callback for an app-server model/list result."""

    url: str
    env_key: str
    credential: str
    fingerprint: str

    @classmethod
    def create(cls, url: str, env_key: str, credential: str) -> "ModelListOverlay":
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
            or parsed.path in {"", "/"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Model-list overlay must use an exact loopback HTTP URL")
        if not _ENV_KEY.fullmatch(env_key):
            raise ValueError("Model-list overlay environment key is invalid")
        if len(credential) < 32:
            raise ValueError("Model-list overlay credential is too short")
        hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        normalized = f"http://{hostname}:{parsed.port}{parsed.path}"
        fingerprint = hashlib.sha256(json.dumps({
            "url": normalized,
            "envKey": env_key,
            "credentialHash": hashlib.sha256(credential.encode()).hexdigest(),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return cls(normalized, env_key, credential, fingerprint)

    def apply(self, result: Any) -> Any:
        body = json.dumps({"contractVersion": 1, "result": result}, separators=(",", ":")).encode()
        if len(body) > 16 * 1024 * 1024:
            raise ValueError("Model-list result exceeds overlay bound")
        parsed = urlsplit(self.url)
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=2.0)
        try:
            connection.request(
                "POST", parsed.path, body=body,
                headers={"Authorization": f"Bearer {self.credential}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("Model-list overlay callback failed")
            raw = response.read(16 * 1024 * 1024 + 1)
        finally:
            connection.close()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("Model-list overlay response exceeds bound")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("contractVersion") != 1 or "result" not in value:
            raise ValueError("Model-list overlay response is invalid")
        return value["result"]
