from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit


_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_PROVIDER_ID = "external_responses_runtime"


def _toml_string(value: str) -> str:
    # JSON string syntax is valid TOML basic-string syntax for the characters accepted here.
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class ResponsesRoute:
    """Validated, secret-free launch-time route to a loopback Responses provider."""

    base_url: str
    env_key: str
    fingerprint: str
    runtime_header_name: str | None = None

    @classmethod
    def create(
        cls,
        base_url: str,
        env_key: str,
        credential: str,
        *,
        runtime_header_name: str | None = None,
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
        if runtime_header_name is not None:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", runtime_header_name):
                raise ValueError("Responses runtime header name is invalid")
            if runtime_header_name.lower() in {"authorization", "host", "cookie"}:
                raise ValueError("Responses runtime header must not replace first-party authorization")
        normalized = f"http://{parsed.hostname}:{parsed.port}/v1"
        credential_hash = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        material = json.dumps(
            {
                "baseUrl": normalized,
                "credentialHash": credential_hash,
                "envKey": env_key,
                "provider": _PROVIDER_ID,
                **({"runtimeHeaderName": runtime_header_name} if runtime_header_name is not None else {}),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            base_url=normalized,
            env_key=env_key,
            fingerprint=hashlib.sha256(material).hexdigest(),
            runtime_header_name=runtime_header_name,
        )

    def _provider_config(self) -> dict[str, Any]:
        provider: dict[str, Any] = {
            "name": "External Responses Runtime",
            "base_url": self.base_url,
            "wire_api": "responses",
        }
        if self.runtime_header_name is None:
            provider["env_key"] = self.env_key
            provider["requires_openai_auth"] = False
        else:
            provider["model_catalog_url"] = f"{self.base_url}/models"
            provider["env_http_headers"] = {self.runtime_header_name: self.env_key}
            provider["requires_openai_auth"] = True
            provider["supports_websockets"] = False
        return provider

    def codex_config_args(self) -> tuple[str, ...]:
        prefix = f"model_providers.{_PROVIDER_ID}"
        values = [f"model_provider={_toml_string(_PROVIDER_ID)}"]
        for key, value in self._provider_config().items():
            if isinstance(value, dict):
                for header, env_key in value.items():
                    values.append(f"{prefix}.{key}.{_toml_string(header)}={_toml_string(env_key)}")
            elif isinstance(value, bool):
                values.append(f"{prefix}.{key}={str(value).lower()}")
            else:
                values.append(f"{prefix}.{key}={_toml_string(value)}")
        result: list[str] = []
        for value in values:
            result.extend(("-c", value))
        return tuple(result)

    def thread_config_overlay(self) -> dict[str, Any]:
        """Return the secret-free thread config needed to preserve this route."""

        return {"model_provider": _PROVIDER_ID, "model_providers": {_PROVIDER_ID: self._provider_config()}}

    @property
    def provider_id(self) -> str:
        return _PROVIDER_ID
