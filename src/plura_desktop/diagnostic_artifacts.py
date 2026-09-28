from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil

from .domain import MANIFEST_SCHEMA, profile_identifier, validate_profile_index


DIAGNOSTIC_ARTIFACT_SCHEMA = 1
DIAGNOSTIC_ARTIFACT_MAX_BYTES = 256 * 1024
DIAGNOSTIC_ARTIFACT_MAX_INCIDENTS = 10
INCIDENT_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def diagnostic_incidents_root(metadata: Path, profile: int) -> Path:
    validate_profile_index(profile)
    return metadata / "diagnostics" / f"profile-{profile}" / "incidents"


def new_incident_id(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    stamp = value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def _owned_by_current_user(path: Path) -> bool:
    return not hasattr(os, "getuid") or path.stat().st_uid == os.getuid()


def _require_owned_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir() or not _owned_by_current_user(path):
        raise RuntimeError(f"{label} is unsafe or replaced: {path}")


def _ensure_private_directory(path: Path) -> None:
    if path.exists():
        _require_owned_directory(path, "Diagnostic artifact directory")
        return
    path.mkdir(mode=0o700)


def _validate_installed_profile(metadata: Path, profile: int) -> None:
    if not metadata.exists():
        raise RuntimeError("PluraDesktop metadata root is missing; install the managed profile first")
    _require_owned_directory(metadata, "PluraDesktop metadata root")
    manifest = metadata / f"profile-{profile}-install-manifest.json"
    if manifest.is_symlink() or not manifest.is_file() or not _owned_by_current_user(manifest):
        raise RuntimeError("Managed profile manifest is missing or unsafe")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Managed profile manifest is unreadable") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != MANIFEST_SCHEMA
        or value.get("profile_index") != profile
        or value.get("id") != profile_identifier(profile)
        or value.get("ready") is not True
    ):
        raise RuntimeError("Managed profile manifest is not a ready canonical Plura Desktop install")


def _atomic_private_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_incident_directory(path: Path) -> None:
    _require_owned_directory(path, "Diagnostic incident directory")
    entries = list(path.iterdir())
    if len(entries) != 1 or entries[0].name != "summary.json":
        raise RuntimeError(f"Diagnostic incident directory contains unexpected artifacts: {path}")
    summary = entries[0]
    if summary.is_symlink() or not summary.is_file() or not _owned_by_current_user(summary):
        raise RuntimeError(f"Diagnostic summary is unsafe or replaced: {summary}")


def validate_profile_diagnostic_tree(metadata: Path, profile: int) -> bool:
    """Validate the exact persistent diagnostic tree before writing or destructive cleanup."""
    validate_profile_index(profile)
    diagnostics = metadata / "diagnostics"
    profile_root = diagnostics / f"profile-{profile}"
    if not profile_root.exists():
        return False
    _require_owned_directory(metadata, "PluraDesktop metadata root")
    _require_owned_directory(diagnostics, "Diagnostic artifact root")
    _require_owned_directory(profile_root, "Profile diagnostic artifact root")
    profile_entries = list(profile_root.iterdir())
    if any(entry.name != "incidents" for entry in profile_entries):
        raise RuntimeError(f"Profile diagnostic artifact root contains unexpected artifacts: {profile_root}")
    if not profile_entries:
        return True
    incidents = profile_root / "incidents"
    _require_owned_directory(incidents, "Diagnostic incidents directory")
    for candidate in sorted(incidents.iterdir(), key=lambda item: item.name):
        if not INCIDENT_ID_RE.fullmatch(candidate.name):
            raise RuntimeError(f"Diagnostic incidents directory contains an unexpected artifact: {candidate}")
        _safe_incident_directory(candidate)
    return True


def write_diagnostic_artifact(
    metadata: Path,
    profile: int,
    output: dict,
    *,
    incident_id: str | None = None,
    max_bytes: int = DIAGNOSTIC_ARTIFACT_MAX_BYTES,
    max_incidents: int = DIAGNOSTIC_ARTIFACT_MAX_INCIDENTS,
) -> dict:
    validate_profile_index(profile)
    if max_bytes < 1024 or max_incidents < 1:
        raise RuntimeError("Diagnostic artifact policy is invalid")
    incident = incident_id or new_incident_id()
    if not INCIDENT_ID_RE.fullmatch(incident):
        raise RuntimeError("Diagnostic incident id is invalid")

    _validate_installed_profile(metadata, profile)
    artifact = {
        "schema": DIAGNOSTIC_ARTIFACT_SCHEMA,
        "incident": incident,
        "profile": profile,
        "maxBytes": max_bytes,
        "retentionMaxIncidents": max_incidents,
    }
    payload = {**output, "artifact": artifact}
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > max_bytes:
        raise RuntimeError(
            f"Diagnostic summary exceeds bounded artifact size ({len(data)} > {max_bytes} bytes)"
        )

    diagnostics = metadata / "diagnostics"
    profile_root = diagnostics / f"profile-{profile}"
    incidents = diagnostic_incidents_root(metadata, profile)
    for directory in (diagnostics, profile_root, incidents):
        _ensure_private_directory(directory)
    validate_profile_diagnostic_tree(metadata, profile)
    retained = sorted(incidents.iterdir(), key=lambda item: item.name)

    incident_root = incidents / incident
    if incident_root.exists():
        raise RuntimeError(f"Diagnostic incident already exists: {incident}")
    incident_root.mkdir(mode=0o700)
    _atomic_private_write(incident_root / "summary.json", data)
    retained.append(incident_root)
    retained.sort(key=lambda item: item.name)
    while len(retained) > max_incidents:
        oldest = retained.pop(0)
        shutil.rmtree(oldest)

    return {**artifact, "sizeBytes": len(data)}
