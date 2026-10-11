"""Bounded, event-triggered diagnostic receipts for profile update failures.

No background recorder, tracebacks, user-supplied strings, conversation data,
environment, authentication, network requests, process argv, or raw paths.
This is NOT the opt-in Native tool-lifecycle trace feature.

Only an already installed/ready profile can write an incident, through the
existing manifest-verified and retention-bounded product artifact store.
The reporter always fails open with respect to the *original exception*:
logging failure must not change an update's outcome or discard profile data.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import TYPE_CHECKING

from .diagnostic_artifacts import (
    DIAGNOSTIC_ARTIFACT_MAX_BYTES,
    diagnostic_incidents_root,
    validate_profile_diagnostic_tree,
    write_diagnostic_artifact,
)
from .version import __version__

if TYPE_CHECKING:
    from .platforms.base import DesktopPlatform
    from .domain import ProfileLayout


_HEALTH_VALUES = frozenset(("ready", "missing", "incomplete", "unsafe", "unknown"))
_ERROR_TYPES = frozenset((
    "RuntimeError", "OSError", "PermissionError", "FileNotFoundError",
    "FileExistsError", "NotADirectoryError", "IsADirectoryError",
    "ValueError", "KeyError", "TypeError", "TimeoutError",
    "CalledProcessError", "JSONDecodeError",
))
_OPERATIONS = frozenset(("refresh", "prepare_launch", "launch_runtime"))
_PHASES = frozenset((
    "begin", "update_lock", "manifest_validation", "official_source_validation",
    "managed_process_check", "runtime_prepare", "selector_staging", "control_runtime_prepare",
    "ownership_validation", "manifest_commit", "final_validation",
    "launch_preflight", "runtime_spawn", "runtime_session_wait", "complete",
))


def _safe_health(platform: DesktopPlatform, layout: ProfileLayout) -> dict[str, str]:
    value: dict[str, str] = {}
    for key, method in (
        ("selector", platform.selector_health),
        ("runtime", platform.managed_runtime_health),
    ):
        try:
            state = method(layout)
        except Exception:
            state = "unknown"
        value[key] = state if state in _HEALTH_VALUES else "unknown"
    return value


class UpdateLifecycleReceipt:
    """Tiny in-memory timeline, persisted only on failure or actual reconciliation."""

    def __init__(
        self, platform: DesktopPlatform, layout: ProfileLayout, operation: str
    ) -> None:
        if operation not in _OPERATIONS:
            raise ValueError("Unknown update lifecycle operation")
        self.platform = platform
        self.layout = layout
        self.operation = operation
        self.started = time.monotonic()
        self.phases: list[dict[str, object]] = []
        self.phase = "begin"
        self.before = _safe_health(platform, layout)
        self.flags: dict[str, bool] = {}
        self.reconciled = False
        self.stage("begin")

    def stage(self, name: str) -> None:
        if name not in _PHASES:
            raise ValueError("Invalid diagnostic phase")
        self.phase = name
        if len(self.phases) < 20:
            self.phases.append({
                "name": name,
                "elapsedMs": max(0, min(int((time.monotonic() - self.started) * 1000), 3600000)),
            })

    def flag(self, name: str, value: bool) -> None:
        if name not in (
            "officialSourceChanged", "managedProfileRunning",
            "selectorRepairNeeded", "managedRuntimeRebuilt",
        ):
            raise ValueError("Invalid update diagnostic flag")
        self.flags[name] = bool(value)

    def note_reconciliation(self) -> None:
        self.reconciled = True

    def _record(self, outcome: str, error: Exception | None = None) -> None:
        report: dict[str, object] = {
            "kind": "managed_profile_update_lifecycle",
            "version": 1,
            "productVersion": __version__,
            "operation": self.operation,
            "outcome": outcome,
            "failedPhase": self.phase if error is not None else None,
            "steps": list(self.phases),
            "flags": dict(self.flags),
            "before": dict(self.before),
            "after": _safe_health(self.platform, self.layout),
            "durationMs": max(0, min(int((time.monotonic() - self.started) * 1000), 3600000)),
            "privacy": "structural_metadata_only",
        }
        if error is not None:
            name = type(error).__name__
            report["errorClass"] = name if name in _ERROR_TYPES else "OtherError"
            if isinstance(error, OSError) and isinstance(error.errno, int) and 0 < error.errno < 4096:
                report["errno"] = error.errno
        try:
            # Only bounded canonical artifacts in already-installed profile
            # metadata. User secrets/paths are never passed to this writer.
            write_diagnostic_artifact(
                self.layout.metadata, self.layout.index,
                {"updateLifecycle": report},
            )
        except Exception:
            # A corrupt/non-owned diagnostics tree is never deleted/replaced
            # to force logging. The original update failure remains primary.
            return

    def failure(self, error: Exception) -> None:
        self._record("failed", error)

    def complete(self) -> None:
        self.stage("complete")
        if self.reconciled:
            self._record("reconciled")


def latest_update_receipt(metadata: Path, profile: int) -> dict[str, str] | None:
    """Read only allowlisted *structural* fields from the newest update incident.

    Historical diagnostic artifacts may contain other data, so never forward
    arbitrary JSON keys, exception text, paths, tracebacks, or source IDs to
    the status UI. Treat unsafe/corrupt diagnostic trees as unavailable.
    """
    try:
        if not validate_profile_diagnostic_tree(metadata, profile):
            return None
        root = diagnostic_incidents_root(metadata, profile)
        if not root.is_dir():
            return None
        for folder in sorted(root.iterdir(), key=lambda path: path.name, reverse=True):
            summary = folder / "summary.json"
            if summary.stat().st_size > DIAGNOSTIC_ARTIFACT_MAX_BYTES:
                return None
            data = json.loads(summary.read_text(encoding="utf-8"))
            record = data.get("updateLifecycle") if isinstance(data, dict) else None
            if not isinstance(record, dict) or record.get("kind") != "managed_profile_update_lifecycle":
                continue
            operation = record.get("operation")
            outcome = record.get("outcome")
            phase = record.get("failedPhase")
            if operation not in _OPERATIONS or outcome not in {"failed", "reconciled"}:
                return None
            if phase is not None and phase not in _PHASES:
                return None
            return {
                "operation": operation,
                "outcome": outcome,
                "phase": phase if phase in _PHASES else "complete",
            }
    except (OSError, ValueError, RuntimeError, TypeError, UnicodeError):
        return None
    return None
