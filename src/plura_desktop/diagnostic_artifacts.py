from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import time

from .domain import MANIFEST_SCHEMA, profile_identifier, validate_profile_index


DIAGNOSTIC_ARTIFACT_SCHEMA = 1
DIAGNOSTIC_ARTIFACT_MAX_BYTES = 256 * 1024
DIAGNOSTIC_TOTAL_MAX_BYTES = 32 * 1024 * 1024
DIAGNOSTIC_RETENTION_DAYS = 30
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
    max_total_bytes: int = DIAGNOSTIC_TOTAL_MAX_BYTES,
    retention_days: int = DIAGNOSTIC_RETENTION_DAYS,
) -> dict:
    validate_profile_index(profile)
    if max_bytes < 1024 or max_total_bytes < 1024 or retention_days < 1:
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
        "retentionMaxBytes": max_total_bytes,
        "retentionDays": retention_days,
    }
    payload = {**output, "artifact": artifact}
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(data) > min(max_bytes, max_total_bytes):
        raise RuntimeError(
            f"Diagnostic summary exceeds bounded artifact size ({len(data)} > {max_bytes} bytes)"
        )

    diagnostics = metadata / "diagnostics"
    profile_root = diagnostics / f"profile-{profile}"
    incidents = diagnostic_incidents_root(metadata, profile)
    for directory in (diagnostics, profile_root, incidents):
        _ensure_private_directory(directory)
    validate_profile_diagnostic_tree(metadata, profile)
    return _write_bounded_incident(metadata, incidents, incident, data, artifact, max_total_bytes, retention_days)


def read_diagnostic_artifacts(metadata: Path, profile: int) -> list[dict]:
    """Read only the bounded, owned summaries on an explicit status request."""
    root = diagnostic_incidents_root(metadata, profile)
    if not root.exists():
        return []
    _validate_installed_profile(metadata, profile)
    candidates = list(root.iterdir())
    validate_profile_diagnostic_tree(metadata, profile)
    summaries = []
    read_bytes = 0
    for candidate in sorted(candidates, key=lambda item: (item / "summary.json").stat().st_mtime_ns):
        path = candidate / "summary.json"
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            identity = os.fstat(stream.fileno())
            if not stat.S_ISREG(identity.st_mode) or (hasattr(os, "getuid") and identity.st_uid != os.getuid()):
                raise RuntimeError("Diagnostic summary ownership is invalid")
            data = stream.read(DIAGNOSTIC_ARTIFACT_MAX_BYTES + 1)
        read_bytes += len(data)
        if read_bytes > DIAGNOSTIC_TOTAL_MAX_BYTES or len(data) > DIAGNOSTIC_ARTIFACT_MAX_BYTES:
            raise RuntimeError("Diagnostic size limit exceeded")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            continue
        if isinstance(value, dict):
            summaries.append(value)
    return summaries


def _diagnostic_summary_paths(metadata: Path) -> list[Path]:
    root = metadata / "diagnostics"
    _require_owned_directory(root, "Diagnostic root")
    summaries = []
    for namespace in root.iterdir():
        if namespace.name == "runtime":
            runtime = validate_runtime_diagnostic_tree(metadata)
            if runtime is not None:
                summaries.extend(runtime.glob("*/incidents/*/summary.json"))
        elif re.fullmatch(r"profile-[0-9]+", namespace.name):
            profile = int(namespace.name.split("-")[1])
            validate_profile_diagnostic_tree(metadata, profile)
            summaries.extend(namespace.glob("incidents/*/summary.json"))
        else:
            raise RuntimeError("Unexpected diagnostic namespace")
    return summaries


def _write_bounded_incident(metadata: Path, incidents: Path, incident: str, data: bytes, artifact: dict, max_total_bytes: int, retention_days: int) -> dict:
    for candidate in incidents.iterdir():
        if not INCIDENT_ID_RE.fullmatch(candidate.name):
            raise RuntimeError("Unexpected diagnostic artifact")
        _safe_incident_directory(candidate)
    _diagnostic_summary_paths(metadata)

    incident_root = incidents / incident
    if incident_root.exists():
        raise RuntimeError(f"Diagnostic incident already exists: {incident}")
    incident_root.mkdir(mode=0o700)
    _atomic_private_write(incident_root / "summary.json", data)
    retained = _diagnostic_summary_paths(metadata)
    total_bytes = sum(path.stat().st_size for path in retained)
    cutoff = time.time() - retention_days * 86400
    # Newest write wins even when two IDs share a timestamp with random suffixes.
    for summary in sorted(retained, key=lambda p: (p.stat().st_mtime_ns, p.parent.name)):
        if summary.parent == incident_root:
            continue
        if summary.stat().st_mtime < cutoff or total_bytes > max_total_bytes:
            size = summary.stat().st_size
            shutil.rmtree(summary.parent)
            total_bytes -= size

    return {**artifact, "sizeBytes": len(data)}



def write_runtime_diagnostic_artifact(metadata: Path, target_id: str, supervisor_pid: int, output: dict) -> dict:
    """Default and managed runtime evidence, authorized by the exact live owner descriptor."""
    from .runtime import descriptor_path, route_key
    _require_owned_directory(metadata, "Runtime metadata")
    descriptor = descriptor_path(metadata, target_id)
    if descriptor.is_symlink() or not descriptor.is_file() or not _owned_by_current_user(descriptor):
        raise RuntimeError("Runtime diagnostic owner is unavailable")
    if descriptor.stat().st_size > 16 * 1024:
        raise RuntimeError("Runtime descriptor exceeds bound")
    owner = json.loads(descriptor.read_text(encoding="utf-8"))
    if owner.get("targetID") != target_id or owner.get("supervisorPID") != supervisor_pid:
        raise RuntimeError("Runtime diagnostic owner changed")
    incident = new_incident_id()
    artifact = {"schema": 1, "incident": incident, "targetHash": route_key(target_id),
                "maxBytes": DIAGNOSTIC_ARTIFACT_MAX_BYTES, "retentionMaxBytes": DIAGNOSTIC_TOTAL_MAX_BYTES,
                "retentionDays": DIAGNOSTIC_RETENTION_DAYS}
    data = (json.dumps({**output, "artifact": artifact}, sort_keys=True, indent=2) + "\n").encode()
    if len(data) > DIAGNOSTIC_ARTIFACT_MAX_BYTES:
        raise RuntimeError("Runtime diagnostic exceeds bound")
    root = metadata / "diagnostics"
    runtime = root / "runtime"
    target = runtime / route_key(target_id)
    incidents = target / "incidents"
    for directory in (root, runtime, target, incidents):
        _ensure_private_directory(directory)
    if any(path.name != "incidents" for path in target.iterdir()):
        raise RuntimeError("Runtime diagnostic namespace changed")
    return _write_bounded_incident(metadata, incidents, incident, data, artifact, DIAGNOSTIC_TOTAL_MAX_BYTES, DIAGNOSTIC_RETENTION_DAYS)


def validate_runtime_diagnostic_tree(metadata: Path) -> Path | None:
    root = metadata / "diagnostics" / "runtime"
    if not root.exists():
        return None
    for directory in (metadata, root.parent, root):
        _require_owned_directory(directory, "Runtime diagnostic namespace")
    for target in root.iterdir():
        if not re.fullmatch(r"[0-9a-f]{20}", target.name):
            raise RuntimeError("Runtime diagnostic target changed")
        _require_owned_directory(target, "Runtime diagnostic target")
        if [entry.name for entry in target.iterdir()] != ["incidents"]:
            raise RuntimeError("Runtime diagnostic target contains unexpected artifacts")
        incidents = target / "incidents"
        _require_owned_directory(incidents, "Runtime incidents")
        for incident in incidents.iterdir():
            if not INCIDENT_ID_RE.fullmatch(incident.name):
                raise RuntimeError("Runtime incident changed")
            _safe_incident_directory(incident)
            summary = incident / "summary.json"
            if summary.stat().st_size > DIAGNOSTIC_ARTIFACT_MAX_BYTES:
                raise RuntimeError("Runtime summary exceeds bound")
            artifact = json.loads(summary.read_text(encoding="utf-8")).get("artifact", {})
            if artifact.get("schema") != 1 or artifact.get("targetHash") != target.name or artifact.get("incident") != incident.name:
                raise RuntimeError("Runtime summary ownership changed")
    return root
