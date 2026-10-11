from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from pathlib import Path
from urllib.parse import urlsplit


_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_PROVIDER_ID = "openai"
_RUNTIME_HEADER = "OpenAI-Organization"
_RUNTIME_ENV_KEY = "OPENAI_ORGANIZATION"


def _toml_string(value: str) -> str:
    # JSON string syntax is valid TOML basic-string syntax for the characters accepted here.
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class ResponsesRoute:
    """Validated, secret-free launch-time route to a loopback Responses provider."""

    base_url: str
    env_key: str
    fingerprint: str
    runtime_header_name: str = _RUNTIME_HEADER
    model_catalog_json: str | None = None

    @classmethod
    def create(
        cls, base_url: str, env_key: str, credential: str, runtime_header_name: str | None = None, model_catalog_json: str | None = None
    ) -> "ResponsesRoute":
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Responses route must use loopback http://")
        if parsed.port is None or not 1 <= parsed.port <= 65535:
            raise ValueError("Responses route must include a valid loopback port")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Responses route must not contain credentials, query, or fragment")
        if parsed.path.rstrip("/") != "/v1":
            raise ValueError("Responses route path must be /v1")
        if not _ENV_KEY.fullmatch(env_key):
            raise ValueError("Responses route env key must be an uppercase environment variable name")
        if len(credential) < 32:
            raise ValueError("Responses route credential must contain at least 32 characters")
        if runtime_header_name is None:
            runtime_header_name = _RUNTIME_HEADER
        if runtime_header_name != _RUNTIME_HEADER:
            raise ValueError(f"Responses route runtime header must be {_RUNTIME_HEADER}")
        catalog_hash = None
        if model_catalog_json is not None:
            path = Path(model_catalog_json)
            if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Model catalog must be a bounded absolute regular JSON file")
            payload = path.read_bytes()
            catalog = json.loads(payload)
            if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list) or not catalog["models"]:
                raise ValueError("Model catalog must contain models")
            model_catalog_json = str(path.resolve(strict=True))
            catalog_hash = hashlib.sha256(payload).hexdigest()
        normalized = f"http://{parsed.hostname}:{parsed.port}/v1"
        credential_hash = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        material = json.dumps(
            {
                "baseUrl": normalized,
                "credentialHash": credential_hash,
                "envKey": env_key,
                "provider": _PROVIDER_ID,
                "runtimeHeaderName": runtime_header_name,
                **({"modelCatalogSha256": catalog_hash} if catalog_hash else {}),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            base_url=normalized,
            env_key=env_key,
            fingerprint=hashlib.sha256(material).hexdigest(),
            runtime_header_name=runtime_header_name,
            model_catalog_json=model_catalog_json,
        )

    def codex_config_args(self) -> tuple[str, ...]:
        values = (
            f"model_provider={_toml_string(_PROVIDER_ID)}",
            f"openai_base_url={_toml_string(self.base_url)}",
        )
        if self.model_catalog_json is not None:
            values += (f"model_catalog_json={_toml_string(self.model_catalog_json)}",)
        result: list[str] = []
        for value in values:
            result.extend(("-c", value))
        return tuple(result)

    def thread_config_overlay(self) -> dict[str, Any]:
        """Return the secret-free thread config needed to preserve this route."""

        return {"model_provider": _PROVIDER_ID, "openai_base_url": self.base_url}

    @property
    def runtime_env_key(self) -> str:
        return _RUNTIME_ENV_KEY

    @property
    def provider_id(self) -> str:
        return _PROVIDER_ID
