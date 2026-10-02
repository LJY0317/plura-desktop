from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
from typing import Any
from urllib.parse import urlsplit

from .platforms import DesktopPlatform


@dataclass(frozen=True)
class TargetSession:
    target_id: str
    endpoint: str
    supervisor_pid: int
    backend_pid: int
    desktop_pid: int
    responses_route_fingerprint: str | None = None
    model_list_overlay_fingerprint: str | None = None
    renderer_cdp_endpoint: str | None = None


def valid_renderer_cdp_endpoint(value: str | None) -> bool:
    if value is None:
        return True
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and port is not None
        and 1 <= port <= 65535
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def route_key(target_id: str) -> str:
    return hashlib.sha256(target_id.encode("utf-8")).hexdigest()[:20]


def descriptor_path(metadata: Path, target_id: str) -> Path:
    return metadata / "runtime-sessions" / f"{route_key(target_id)}.json"


def claim_path(metadata: Path, target_id: str) -> Path:
    return metadata / "runtime-sessions" / f"{route_key(target_id)}.lock"


def acquire_runtime_claim(platform: DesktopPlatform, metadata: Path, target_id: str) -> Path:
    path = claim_path(metadata, target_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Canonical runtime claim was replaced; refusing launch")
            try:
                owner = int(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError) as error:
                raise RuntimeError("Canonical runtime claim is unreadable") from error
            if platform.pid_alive(owner):
                raise RuntimeError(f"Canonical runtime is already owned for target: {target_id}")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        finally:
            os.close(descriptor)
        return path
    raise RuntimeError(f"Unable to claim canonical runtime for target: {target_id}")


def release_runtime_claim(path: Path) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Canonical runtime claim changed while owned")
        path.unlink()
    except FileNotFoundError:
        pass


def endpoint_ready(endpoint: str) -> bool:
    try:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.port is None:
            return False
        with socket.create_connection(("127.0.0.1", parsed.port), timeout=0.25):
            return True
    except (OSError, ValueError):
        return False


def load_session(platform: DesktopPlatform, metadata: Path, target_id: str) -> TargetSession | None:
    path = descriptor_path(metadata, target_id)
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        session = TargetSession(
            target_id=str(raw["targetID"]),
            endpoint=str(raw["endpoint"]),
            supervisor_pid=int(raw["supervisorPID"]),
            backend_pid=int(raw["backendPID"]),
            desktop_pid=int(raw["desktopPID"]),
            responses_route_fingerprint=(
                str(raw["responsesRouteFingerprint"])
                if raw.get("responsesRouteFingerprint") is not None
                else None
            ),
            model_list_overlay_fingerprint=(
                str(raw["modelListOverlayFingerprint"])
                if raw.get("modelListOverlayFingerprint") is not None
                else None
            ),
            renderer_cdp_endpoint=(
                str(raw["rendererCDPEndpoint"])
                if raw.get("rendererCDPEndpoint") is not None
                else None
            ),
        )
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if session.target_id != target_id:
        return None
    if not valid_renderer_cdp_endpoint(session.renderer_cdp_endpoint):
        return None
    if not all(
        platform.pid_alive(pid)
        for pid in (session.supervisor_pid, session.backend_pid, session.desktop_pid)
    ):
        return None
    if not endpoint_ready(session.endpoint):
        return None
    return session


def atomic_write_session(path: Path, session: TargetSession) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "targetID": session.target_id,
                "endpoint": session.endpoint,
                "supervisorPID": session.supervisor_pid,
                "backendPID": session.backend_pid,
                "desktopPID": session.desktop_pid,
                **(
                    {"responsesRouteFingerprint": session.responses_route_fingerprint}
                    if session.responses_route_fingerprint is not None
                    else {}
                ),
                **(
                    {"modelListOverlayFingerprint": session.model_list_overlay_fingerprint}
                    if session.model_list_overlay_fingerprint is not None
                    else {}
                ),
                **(
                    {"rendererCDPEndpoint": session.renderer_cdp_endpoint}
                    if session.renderer_cdp_endpoint is not None
                    else {}
                ),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
