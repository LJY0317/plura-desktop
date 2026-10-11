"""Bounded permission-only RPC evidence; never retain message payloads or raw identifiers."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import threading
from typing import Any, Callable
from uuid import uuid4

_METHODS = frozenset({"thread/start", "thread/resume", "thread/fork", "turn/start", "thread/settings/update"})
_NOTIFICATION = "thread/settings/updated"


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _choice(value: Any, known: set[str]) -> str:
    if value is None:
        return "absent"
    if isinstance(value, str):
        return value if value in known else "custom"
    return "structured" if isinstance(value, dict) else "invalid"


def _fields(value: dict) -> dict:
    sandbox = value.get("sandbox", value.get("sandboxPolicy"))
    if isinstance(sandbox, dict):
        sandbox = sandbox.get("type", "structured")
    profile = _object(value.get("activePermissionProfile")).get("id")
    config = _object(value.get("config"))
    return {
        "approval": _choice(value.get("approvalPolicy"), {"never", "on-request", "untrusted"}),
        "sandbox": _choice(sandbox, {"read-only", "workspace-write", "danger-full-access", "readOnly", "workspaceWrite", "dangerFullAccess", "externalSandbox"}),
        "permissions": _choice(value.get("permissions"), {":read-only", ":workspace", ":danger-full-access"}),
        "activeProfile": _choice(profile, {":read-only", ":workspace", ":danger-full-access"}),
        "configApproval": _choice(config.get("approval_policy"), {"never", "on-request", "untrusted"}),
        "configSandbox": _choice(config.get("sandbox_mode"), {"read-only", "workspace-write", "danger-full-access"}),
        "configPermissions": _choice(config.get("default_permissions"), {":read-only", ":workspace", ":danger-full-access"}),
    }


def _thread(value: dict) -> str | None:
    identity = value.get("threadId", _object(value.get("thread")).get("id"))
    if isinstance(identity, str) and len(identity) <= 256:
        return hashlib.sha256(identity.encode()).hexdigest()[:16]
    return None


def permission_resume_summary(snapshots: list[dict]) -> list[str]:
    """Re-allowlist persisted evidence; never render artifact text or identifiers."""
    latest = None
    truncated = False
    for snapshot in snapshots:
        if snapshot.get("kind") != "permission-rpc" or snapshot.get("version") != 1:
            continue
        events = snapshot.get("events")
        if not isinstance(events, list) or len(events) > 64:
            continue
        for event in reversed(events):
            if isinstance(event, dict) and event.get("method") == "thread/resume":
                latest = event
                truncated = snapshot.get("truncated") is True
                break
    if latest is None:
        return ["Permission resume evidence: not recorded in retained diagnostics."]
    known = {"absent", "custom", "structured", "invalid", "never", "on-request", "untrusted",
        "read-only", "workspace-write", "danger-full-access", "readOnly", "workspaceWrite",
        "dangerFullAccess", "externalSandbox", ":read-only", ":workspace", ":danger-full-access"}
    def fields(name: str) -> dict:
        value = _object(latest.get(name))
        return {key: _choice(value.get(key), known) for key in _fields({})}
    received, forwarded, native = fields("received"), fields("forwarded"), fields("native")
    overrides = any(value != "absent" for value in received.values())
    changed = "unknown" if not isinstance(latest.get("forwarded"), dict) else ("yes" if received != forwarded else "no")
    response = latest.get("response")
    result = response if response in ("result", "error") else "pending"
    lines = ["Latest retained resume (Native report; not an execution permission test):",
        f"  Explicit permission override: {'yes' if overrides else 'no'}; forwarded classifications changed: {changed}",
        f"  Response: {result}"]
    if result == "result":
        lines.append(f"  Native approval: {native['approval']}; sandbox: {native['sandbox']}; active profile: {native['activeProfile']}")
    if truncated:
        lines.append("  Collection limit reached; later events may be missing.")
    return lines


class PermissionDiagnostics:
    def __init__(self, sink: Callable[[dict], Any], *, limit: int = 64):
        if not 1 <= limit <= 64:
            raise ValueError("Permission diagnostic limit is invalid")
        self._sink = sink
        self._limit = limit
        self._events: list[dict] = []
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._connection = uuid4().hex
        self._disabled = False
        self._truncated = False

    def _publish(self) -> None:
        try:
            # Sink failures must never modify or stop permission RPC forwarding.
            self._sink(json.loads(json.dumps({"kind": "permission-rpc", "version": 1,
                "connection": self._connection, "truncated": self._truncated, "events": self._events})))
        except Exception:
            self._disabled = True
            self._pending.clear()

    def observe(self, before: str, after: str | None = None, *, experimental: bool = False) -> None:
        try:
            message = _object(json.loads(before))
            forwarded = _object(json.loads(after)) if after is not None else None
        except (ValueError, TypeError):
            return
        with self._lock:
            if self._disabled:
                return
            method = message.get("method")
            if isinstance(method, str) and (method in _METHODS or method == _NOTIFICATION):
                if len(self._events) >= self._limit:
                    if not self._truncated:
                        self._truncated = True
                        self._publish()
                    return
                params = _object(message.get("params"))
                event = {"method": method, "time": datetime.now(timezone.utc).isoformat(),
                    "experimental": experimental, "threadHash": _thread(params), "received": _fields(_object(params.get("threadSettings")) if method == _NOTIFICATION else params)}
                if forwarded is not None:
                    event["forwarded"] = _fields(_object(forwarded.get("params")))
                identity = message.get("id")
                if method in _METHODS and isinstance(identity, (str, int)) and not isinstance(identity, bool):
                    key = json.dumps(identity)
                    if len(key) <= 128:
                        self._pending[key] = event
                self._events.append(event)
                self._publish()
            elif "id" in message:
                identity = message["id"]
                if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                    return
                event = self._pending.pop(json.dumps(identity), None)
                if event is not None:
                    result = _object(message.get("result"))
                    event["response"] = "error" if "error" in message else "result"
                    event["native"] = _fields(result)
                    event["threadHash"] = event["threadHash"] or _thread(result)
                    self._publish()
