from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from .domain import (
    DEFAULT_MANAGED_PROFILE_INDEX,
    MANIFEST_SCHEMA,
    ManagedPath,
)
from .diagnostic_artifacts import validate_profile_diagnostic_tree
from .io import atomic_write_json
from .app_server_proxy import RoutePreservingAppServerProxy
from .platforms import DesktopPlatform, current_platform
from .platforms.base import STABLE_FROZEN_RUNTIME_ENV
from .routing import ResponsesRoute
from .runtime import (
    TargetSession,
    acquire_runtime_claim,
    atomic_write_session,
    descriptor_path,
    endpoint_ready,
    load_session,
    release_runtime_claim,
)


PACKAGE_DIR = Path(__file__).resolve().parent
ENTRYPOINT_SOURCE = PACKAGE_DIR / "_runtime_entrypoint.py"
SUPERVISOR_STARTUP_TIMEOUT_SECONDS = 30.0
SESSION_START_TIMEOUT_SECONDS = 35.0
def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _frozen_runtime_executable() -> Path | None:
    if not bool(getattr(sys, "frozen", False)):
        return None
    executable = Path(sys.executable).resolve()
    if not executable.is_file():
        raise RuntimeError(f"Frozen Plura runtime executable is missing: {executable}")
    stable_value = os.environ.get(STABLE_FROZEN_RUNTIME_ENV)
    if stable_value:
        stable = Path(stable_value)
        if not stable.is_absolute():
            raise RuntimeError(f"{STABLE_FROZEN_RUNTIME_ENV} must be an absolute path")
        if not stable.is_file():
            raise RuntimeError(f"Stable frozen Plura runtime is missing: {stable}")
        try:
            stable_target = stable.resolve(strict=True)
        except OSError as error:
            raise RuntimeError(f"Stable frozen Plura runtime is unreadable: {stable}") from error
        if stable_target != executable:
            raise RuntimeError(
                f"{STABLE_FROZEN_RUNTIME_ENV} does not resolve to the current frozen executable"
            )
        return stable
    return executable


def _python_runtime_command_prefix() -> list[str]:
    """Return an import-safe command prefix for non-frozen private child processes.

    Source checkouts, installed Python packages, and copied control runtimes have different
    filesystem layouts. A file inside ``plura_desktop/`` cannot be executed directly because its
    parent package root is then absent from ``sys.path``. Prefer the copied/source top-level
    launcher when present; otherwise use the installed package module entry point.
    """
    runtime_root = PACKAGE_DIR.parent
    copied_launcher = runtime_root / "plura_desktop_cli.py"
    if copied_launcher.is_file():
        return [sys.executable, str(copied_launcher)]
    source_launcher = runtime_root / "plura_desktop.py"
    if source_launcher.is_file():
        return [sys.executable, str(source_launcher)]
    return [sys.executable, "-m", "plura_desktop"]


class Profile:
    """Platform-neutral managed profile lifecycle.

    The profile owns only its selector and isolated state paths. It never owns the
    official ChatGPT installation and never creates a second/fallback writer for a
    running profile.
    """

    def __init__(
        self,
        home: str | Path | None = None,
        index: int = DEFAULT_MANAGED_PROFILE_INDEX,
        *,
        platform: DesktopPlatform | None = None,
        app_override: str | Path | None = None,
    ) -> None:
        self.platform = platform or current_platform(app_override=app_override, home=home)
        self.layout = self.platform.layout(index)
        self.home = self.platform.home
        self.index = self.layout.index
        self.identifier = self.layout.identifier
        self.display_name = self.layout.display_name
        self.wrapper = self.layout.selector
        self.codex = self.layout.codex_home
        self.data = self.layout.user_data
        self.meta = self.layout.metadata
        self.manifest = self.layout.manifest
        self.paths = list(self.layout.managed_paths)
        self.protected = list(self.platform.protected_paths())

    def _entry(self, path: Path) -> ManagedPath | None:
        return next((entry for entry in self.layout.managed if entry.path == path), None)

    def _assert_no_link_ancestors(self, path: Path) -> None:
        self.platform.assert_safe_ancestry(path)

    def safe(self, path: Path) -> None:
        if path not in self.layout.managed_paths + (self.meta,):
            raise RuntimeError(f"Path outside fixed allowlist: {path}")
        self._assert_no_link_ancestors(path)
        try:
            resolved = path.resolve(strict=False)
        except OSError as error:
            raise RuntimeError(f"Unable to resolve managed path: {path}") from error
        for protected in self.protected:
            protected_resolved = protected.resolve(strict=False)
            if (
                resolved == protected_resolved
                or protected_resolved in resolved.parents
                or resolved in protected_resolved.parents
            ):
                raise RuntimeError(f"Protected path refused: {path}")
        if not path.exists():
            return
        if not self.platform.is_owned(path):
            raise RuntimeError(f"Expected a current-user-owned path: {path}")
        if path == self.meta:
            if not path.is_dir():
                raise RuntimeError(f"Expected metadata directory: {path}")
            return
        entry = self._entry(path)
        if entry is None or not self.platform.check_kind(entry):
            raise RuntimeError(f"Managed path kind changed: {path}")

    def _ensure_parent(self, path: Path) -> None:
        self._assert_no_link_ancestors(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_no_link_ancestors(path.parent)

    def save(self, manifest: dict[str, Any]) -> None:
        self.safe(self.meta)
        atomic_write_json(self.manifest, manifest)

    def _read_manifest(self) -> dict[str, Any]:
        self.safe(self.meta)
        if not self.manifest.is_file() or _is_linklike(self.manifest):
            raise RuntimeError("Install manifest missing or symlinked")
        try:
            value = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Install manifest is unreadable") from error
        if not isinstance(value, dict):
            raise RuntimeError("Install manifest root must be an object")
        return value

    def _validate_diagnostics(self, manifest: dict[str, Any]) -> None:
        diagnostics = manifest.get("diagnostics")
        if diagnostics is None:
            return
        if (
            not isinstance(diagnostics, dict)
            or set(diagnostics) - {"tool_lifecycle_trace"}
            or not isinstance(diagnostics.get("tool_lifecycle_trace"), bool)
        ):
            raise RuntimeError("Invalid diagnostics settings")

    def _validate_v5(self, manifest: dict[str, Any], *, allow_incomplete: bool = False) -> dict[str, Any]:
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise RuntimeError("Unsupported install manifest schema")
        if manifest.get("id") != self.identifier or manifest.get("profile_index") != self.index:
            raise RuntimeError("Unknown manifest")
        if manifest.get("platform") != self.platform.platform_id:
            raise RuntimeError("Manifest belongs to a different operating-system adapter")
        if not self.platform.valid_identity(manifest.get("meta_identity")):
            raise RuntimeError("Invalid metadata directory identity")
        if manifest.get("meta_identity") != self.platform.identity(self.meta):
            raise RuntimeError("Metadata directory identity changed")
        if not isinstance(manifest.get("app_executable"), str) or not manifest["app_executable"]:
            raise RuntimeError("Manifest has no ChatGPT executable identity")
        if not isinstance(manifest.get("ready"), bool):
            raise RuntimeError("Invalid installation readiness state")
        self._validate_diagnostics(manifest)

        expected = {str(entry.path): entry for entry in self.layout.managed}
        entries = manifest.get("created")
        if not isinstance(entries, list):
            raise RuntimeError("Invalid manifest entries")
        seen: set[str] = set()
        for record in entries:
            if not isinstance(record, dict) or set(record) != {"role", "path", "kind", "identity"}:
                raise RuntimeError("Invalid manifest entry")
            path_text = record["path"]
            if not isinstance(path_text, str) or path_text in seen or path_text not in expected:
                raise RuntimeError("Invalid or duplicate manifest path")
            entry = expected[path_text]
            if record["role"] != entry.role or record["kind"] != entry.kind:
                raise RuntimeError("Manifest path role/kind mismatch")
            if not self.platform.valid_identity(record["identity"]):
                raise RuntimeError("Invalid managed path identity")
            seen.add(path_text)
            path = entry.path
            self.safe(path)
            if path.exists() and self.platform.identity(path) != record["identity"]:
                raise RuntimeError(f"Managed path replaced; preserving: {path}")
        if manifest["ready"] and seen != set(expected):
            raise RuntimeError("Ready manifest path set is incomplete")
        if not manifest["ready"] and not allow_incomplete and seen != set(expected):
            raise RuntimeError("Installation is incomplete")
        return manifest

    def load(self) -> dict[str, Any]:
        return self._validate_v5(self._read_manifest())

    @staticmethod
    def tool_lifecycle_diagnostics_enabled(manifest: dict[str, Any]) -> bool:
        diagnostics = manifest.get("diagnostics")
        return isinstance(diagnostics, dict) and diagnostics.get("tool_lifecycle_trace") is True

    def _new_manifest(self, executable: Path, baseline: dict[str, str]) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            "id": self.identifier,
            "profile_index": self.index,
            "platform": self.platform.platform_id,
            "meta_identity": self.platform.identity(self.meta),
            "created": [],
            "official_baseline": baseline,
            "app_executable": str(executable),
            "diagnostics": {"tool_lifecycle_trace": False},
            "ready": False,
        }

    def _create_managed_path(self, entry: ManagedPath) -> None:
        self._ensure_parent(entry.path)
        if entry.kind == "directory":
            entry.path.mkdir(mode=0o700)
        else:
            descriptor = os.open(entry.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o700)
            os.close(descriptor)

    def _write_platform_integration(self, executable: Path) -> None:
        runtime_executable = _frozen_runtime_executable()
        self.platform.write_selector(
            self.layout,
            ENTRYPOINT_SOURCE,
            PACKAGE_DIR,
            executable,
            runtime_executable=runtime_executable,
        )
        self.platform.write_control_cli(
            self.meta,
            ENTRYPOINT_SOURCE,
            PACKAGE_DIR,
            runtime_executable=runtime_executable,
        )

    def install(self) -> None:
        executable = self.platform.resolve_executable()
        for entry in self.layout.managed:
            self.safe(entry.path)
            if entry.path.exists():
                raise RuntimeError(f"Existing path preserved; install refused: {entry.path}")
        self.safe(self.meta)
        if self.manifest.exists():
            raise RuntimeError("Existing profile manifest preserved; install refused")
        if self.meta.exists():
            if not self.meta.is_dir() or not self.platform.is_owned(self.meta):
                raise RuntimeError("Expected an owned metadata directory")
        else:
            self._ensure_parent(self.meta)
            self.meta.mkdir(mode=0o700)

        baseline = self.platform.fingerprint(executable)
        manifest = self._new_manifest(executable, baseline)
        self.save(manifest)

        for entry in self.layout.managed:
            self._create_managed_path(entry)
            manifest["created"].append(
                {
                    "role": entry.role,
                    "path": str(entry.path),
                    "kind": entry.kind,
                    "identity": self.platform.identity(entry.path),
                }
            )
            self.save(manifest)

        self.platform.prepare_profile_runtime(
            self.layout,
            executable,
            source_fingerprint=baseline,
            profile_running=False,
        )
        self._write_platform_integration(executable)
        if self.platform.fingerprint(executable) != baseline:
            raise RuntimeError("Official application changed during install; inspect before launch")
        manifest["ready"] = True
        self.save(manifest)
        self.load()
        print(f"Installed Profile {self.index}: {self.layout.selector}")

    def refresh(self) -> None:
        manifest = self.load()
        if not manifest["ready"]:
            raise RuntimeError("Installation incomplete; refusing refresh")
        executable = self.platform.resolve_executable(manifest["app_executable"])
        source_fingerprint = self.platform.fingerprint(executable)
        self.platform.prepare_profile_runtime(
            self.layout,
            executable,
            source_fingerprint=source_fingerprint,
            profile_running=bool(self.running()),
        )
        before = {str(entry.path): self.platform.identity(entry.path) for entry in self.layout.managed}
        self._write_platform_integration(executable)
        for entry in self.layout.managed:
            if self.platform.identity(entry.path) != before[str(entry.path)]:
                raise RuntimeError(f"Managed path identity changed during refresh: {entry.path}")
        manifest["official_baseline"] = source_fingerprint
        manifest["app_executable"] = str(executable)
        self.save(manifest)
        self.load()
        print(f"Refreshed Profile {self.index} selector/runtime without replacing profile state")

    def running(self) -> list[int]:
        return self.platform.running(self.layout)

    def launch(
        self,
        *,
        app_server_url: str | None = None,
        renderer_cdp_port: int | None = None,
    ) -> None:
        manifest = self.load()
        if not manifest["ready"]:
            raise RuntimeError("Installation incomplete")
        for entry in self.layout.managed:
            if not entry.path.exists() or not self.platform.check_kind(entry):
                raise RuntimeError(f"Managed path missing; refusing implicit recreation: {entry.path}")
        executable = self.platform.resolve_executable(manifest["app_executable"])
        executable = self.platform.profile_executable(
            self.layout,
            executable,
            source_fingerprint=manifest["official_baseline"],
        )
        rust_log = None
        if self.tool_lifecycle_diagnostics_enabled(manifest):
            rust_log = self.platform.tool_lifecycle_diagnostic_rust_log()
            if rust_log is None:
                raise RuntimeError(
                    f"Tool-lifecycle diagnostics are unsupported on {self.platform.platform_id}"
                )
        self.platform.launch(
            self.layout,
            executable,
            rust_log=rust_log,
            app_server_url=app_server_url,
            renderer_cdp_port=renderer_cdp_port,
        )

    def set_tool_lifecycle_diagnostics(self, enabled: bool) -> None:
        if enabled and self.platform.tool_lifecycle_diagnostic_rust_log() is None:
            raise RuntimeError(
                f"Tool-lifecycle diagnostics are unsupported on {self.platform.platform_id}"
            )
        manifest = self.load()
        manifest["diagnostics"] = {"tool_lifecycle_trace": bool(enabled)}
        self.save(manifest)
        self.load()
        state = "enabled" if enabled else "disabled"
        print(f"Profile {self.index} tool-lifecycle diagnostics: {state}")
        if self.running():
            print(
                f"{self.display_name} is already running; the change takes effect "
                "after its next normal quit and launch."
            )

    def diagnostics_status(self) -> None:
        manifest = self.load()
        enabled = self.tool_lifecycle_diagnostics_enabled(manifest)
        supported = self.platform.tool_lifecycle_diagnostic_rust_log() is not None
        print(f"Profile {self.index} native diagnostics supported: {'yes' if supported else 'no'}")
        print(f"Profile {self.index} tool-lifecycle diagnostics: {'enabled' if enabled else 'disabled'}")
        print("Mode: narrow event logging only; no polling, screenshots, profile DB reads, or background sampler.")

    @property
    def diagnostic_artifacts(self) -> Path:
        return self.meta / "diagnostics" / f"profile-{self.index}"

    def _validate_diagnostic_artifacts(self) -> bool:
        path = self.diagnostic_artifacts
        self._assert_no_link_ancestors(path)
        if not validate_profile_diagnostic_tree(self.meta, self.index):
            return False
        self.platform.ensure_no_mounts(path)
        return True

    def _remove_diagnostic_artifacts(self) -> None:
        if not self._validate_diagnostic_artifacts():
            return
        shutil.rmtree(self.diagnostic_artifacts)
        diagnostics_root = self.diagnostic_artifacts.parent
        try:
            diagnostics_root.rmdir()
        except OSError:
            pass

    def status_data(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": self.identifier,
            "displayName": self.display_name,
            "role": "managed",
            "profileIndex": self.index,
            "platform": self.platform.platform_id,
            "ownership": "official-desktop-profile",
            "backendPolicy": "single-authoritative-profile-runtime",
        }
        if not self.manifest.exists():
            return {**base, "state": "not-installed", "manifestSchema": None}
        manifest = self._read_manifest()
        base["manifestSchema"] = manifest.get("schema")
        try:
            manifest = self._validate_v5(manifest, allow_incomplete=True)
            running = self.running()
            state = "running" if running else ("stopped" if manifest["ready"] else "incomplete")
            return {
                **base,
                "state": state,
                "ready": manifest["ready"],
                "runningProcessCount": len(running),
                "diagnosticsEnabled": self.tool_lifecycle_diagnostics_enabled(manifest),
            }
        except (OSError, RuntimeError, ValueError):
            return {**base, "state": "invalid", "ready": False}

    def status(self, *, json_output: bool = False) -> None:
        data = self.status_data()
        if json_output:
            print(json.dumps(data, indent=2, sort_keys=True))
            return
        print("Platform:", self.platform.platform_id)
        print("Managed profile index:", self.index)
        print("State:", data["state"])
        print("Manifest schema:", data.get("manifestSchema"))
        for entry in self.layout.managed:
            print(f"{entry.role}: {entry.path} ({'present' if entry.path.exists() else 'absent'})")
        print("Metadata:", self.meta, "(present)" if self.meta.exists() else "(absent)")
        if self.manifest.exists() and data["state"] not in {"invalid", "not-installed"}:
            manifest = self.load()
            executable = self.platform.resolve_executable(manifest["app_executable"])
            print("Official/app executable:", executable)
            print(
                "Application core matches installation:",
                self.platform.fingerprint(executable) == manifest.get("official_baseline"),
            )
            print("Process IDs:", self.running())

    def _cleanup_shared_metadata(self) -> None:
        manifests = [
            path
            for path in self.meta.glob("profile-*-install-manifest.json")
            if path.is_file() and not _is_linklike(path)
        ]
        if manifests:
            return
        for generated in (
            self.meta / "plura-desktop",
            self.meta / "plura-desktop.cmd",
            self.meta / "control-runtime",
            self.meta / "runtime-sessions",
        ):
            if not generated.exists():
                continue
            if _is_linklike(generated) or not self.platform.is_owned(generated):
                raise RuntimeError(f"Generated helper was replaced; preserving metadata: {generated}")
            if generated.is_dir():
                self.platform.ensure_no_mounts(generated)
                shutil.rmtree(generated)
            elif generated.is_file():
                generated.unlink()
            else:
                raise RuntimeError(f"Unexpected metadata artifact: {generated}")
        try:
            self.meta.rmdir()
        except OSError:
            print("Preserved nonempty metadata directory:", self.meta)

    def uninstall(self, yes: bool = False) -> None:
        manifest = self._read_manifest()
        manifest = self._validate_v5(manifest, allow_incomplete=True)
        if self.running():
            raise RuntimeError(
                f"Quit {self.display_name} first. No processes will be terminated automatically."
            )
        expected = {str(entry.path): entry for entry in self.layout.managed}
        targets = [
            expected[record["path"]]
            for record in manifest["created"]
            if expected[record["path"]].path.exists()
        ]
        for entry in targets:
            self.safe(entry.path)
            if entry.kind == "directory":
                self.platform.ensure_no_mounts(entry.path)
        has_diagnostic_artifacts = self._validate_diagnostic_artifacts()
        print(f"Remove only these installed paths (including Profile {self.index} conversations/login):")
        for entry in targets:
            print(entry.path)
        if has_diagnostic_artifacts:
            print(self.diagnostic_artifacts)
        print(self.manifest)
        if not yes:
            print("Dry run only. Re-run uninstall with --yes to apply.")
            return

        self._validate_v5(self._read_manifest(), allow_incomplete=True)
        for entry in targets:
            self.platform.remove_managed(entry)
        self.platform.remove_profile_runtime(self.layout)
        self._remove_diagnostic_artifacts()
        self.manifest.unlink()
        self._cleanup_shared_metadata()
        print(f"Uninstalled Profile {self.index}. Official app/default profile were not targeted.")

def public_targets(platform: DesktopPlatform) -> dict[str, Any]:
    metadata = platform.layout(DEFAULT_MANAGED_PROFILE_INDEX).metadata
    try:
        default_running = platform.running_default()
        default_state = "running" if default_running else "stopped"
    except (OSError, RuntimeError, ValueError):
        default_running = []
        default_state = "invalid"
    try:
        default_executable = platform.resolve_executable()
        platform.resolve_codex_executable(default_executable)
        default_shared_app_server = True
    except (OSError, RuntimeError, ValueError):
        default_shared_app_server = False
    targets: list[dict[str, Any]] = [
        {
            "id": "default",
            "displayName": "ChatGPT",
            "role": "default",
            "managed": False,
            "ownership": "official-desktop-profile",
            "backendPolicy": "single-authoritative-profile-runtime",
            "state": default_state,
            "runningProcessCount": len(default_running),
            "sharedAppServerSupported": default_shared_app_server,
            "responsesRouteSupported": default_shared_app_server,
            "rendererCDPSupported": default_shared_app_server,
        }
    ]
    if metadata.is_dir() and not _is_linklike(metadata):
        for manifest_path in sorted(metadata.glob("profile-*-install-manifest.json")):
            try:
                if _is_linklike(manifest_path) or not manifest_path.is_file():
                    continue
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                index = raw.get("profile_index")
                if not isinstance(index, int):
                    continue
                profile = Profile(index=index, platform=platform)
                target = {**profile.status_data(), "managed": True}
                try:
                    executable = platform.resolve_executable(raw.get("app_executable"))
                    platform.resolve_codex_executable(executable)
                    target["sharedAppServerSupported"] = True
                    target["responsesRouteSupported"] = True
                    target["rendererCDPSupported"] = True
                except (OSError, RuntimeError, ValueError):
                    target["sharedAppServerSupported"] = False
                    target["responsesRouteSupported"] = False
                    target["rendererCDPSupported"] = False
                targets.append(target)
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                continue
    for target in targets:
        target["sessionState"] = _session_state(platform, target)
        target["rendererCDPState"] = _renderer_cdp_state(platform, target)
    return {
        "contractVersion": 1,
        "platform": platform.platform_id,
        "targets": targets,
    }


def _validate_loopback_websocket_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Shared app-server URL must be a loopback ws:// URL")
    if parsed.port is None or not 1 <= parsed.port <= 65535:
        raise ValueError("Shared app-server URL must include a valid TCP port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Shared app-server URL must not include a path, query, or fragment")
    return value


def _target(platform: DesktopPlatform, target_id: str) -> dict[str, Any]:
    contract = public_targets(platform)
    matches = [target for target in contract["targets"] if target.get("id") == target_id]
    if len(matches) != 1:
        raise RuntimeError(f"Unknown target: {target_id}")
    return matches[0]


def _metadata(platform: DesktopPlatform) -> Path:
    return platform.layout(DEFAULT_MANAGED_PROFILE_INDEX).metadata


def _session_state(platform: DesktopPlatform, target: dict[str, Any]) -> str:
    target_id = target.get("id")
    if not isinstance(target_id, str) or not target_id:
        return "unavailable"
    if load_session(platform, _metadata(platform), target_id) is not None:
        return "ready"
    if target.get("sharedAppServerSupported") is not True:
        return "unsupported"
    if target.get("state") == "stopped":
        return "available"
    if target.get("state") == "running":
        return "restart-required"
    return "unavailable"


def _renderer_cdp_state(platform: DesktopPlatform, target: dict[str, Any]) -> str:
    target_id = target.get("id")
    if not isinstance(target_id, str) or not target_id:
        return "unavailable"
    session = load_session(platform, _metadata(platform), target_id)
    if session is not None:
        return "ready" if session.renderer_cdp_endpoint is not None else "restart-required"
    if target.get("rendererCDPSupported") is not True:
        return "unsupported"
    if target.get("state") == "stopped":
        return "available"
    if target.get("state") == "running":
        return "restart-required"
    return "unavailable"


def target_session(platform: DesktopPlatform, target_id: str) -> dict[str, Any]:
    target = _target(platform, target_id)
    session = load_session(platform, _metadata(platform), target_id)
    state = "ready" if session is not None else _session_state(platform, target)
    value: dict[str, Any] = {
        "contractVersion": 1,
        "targetID": target_id,
        "state": state,
        "rendererCDPState": _renderer_cdp_state(platform, target),
    }
    if session is not None:
        value["endpoint"] = session.endpoint
        value["desktopProcessID"] = session.desktop_pid
        if session.responses_route_fingerprint is not None:
            value["responsesRouteFingerprint"] = session.responses_route_fingerprint
        if session.renderer_cdp_endpoint is not None:
            value["rendererCDPEndpoint"] = session.renderer_cdp_endpoint
    elif state == "restart-required":
        executable: Path | None = None
        if target_id == "default":
            executable = platform.resolve_executable()
        else:
            index = target.get("profileIndex")
            if isinstance(index, int):
                profile = Profile(index=index, platform=platform)
                manifest = profile.load()
                source = platform.resolve_executable(manifest["app_executable"])
                executable = platform.profile_executable(
                    profile.layout,
                    source,
                    source_fingerprint=manifest["official_baseline"],
                )
        if executable is not None:
            desktop_pid = platform.desktop_process_id(executable)
            if desktop_pid is not None:
                value["desktopProcessID"] = desktop_pid
    return value


def _target_executable(platform: DesktopPlatform, target: dict[str, Any]) -> Path:
    target_id = target.get("id")
    if target_id == "default":
        return platform.resolve_executable()
    index = target.get("profileIndex")
    if not isinstance(index, int):
        raise RuntimeError(f"Managed target has no profile index: {target_id}")
    profile = Profile(index=index, platform=platform)
    manifest = profile.load()
    source = platform.resolve_executable(manifest["app_executable"])
    return platform.profile_executable(
        profile.layout,
        source,
        source_fingerprint=manifest["official_baseline"],
    )


def quit_target(
    platform: DesktopPlatform,
    target_id: str,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Request a normal quit for one exact canonical/default desktop target.

    This never kills a desktop process. The platform adapter must provide a normal application quit
    request, after which the canonical supervisor is allowed to observe desktop exit and clean up its
    owned app-server/proxy processes.
    """
    target = _target(platform, target_id)
    session = target_session(platform, target_id)
    state = session.get("state")
    if state in {"available", "unavailable", "unsupported"}:
        return session
    desktop_pid = session.get("desktopProcessID")
    if not isinstance(desktop_pid, int) or desktop_pid <= 0:
        raise RuntimeError(f"Exact desktop process identity is unavailable for target: {target_id}")
    executable = _target_executable(platform, target)
    observed = platform.desktop_process_id(executable)
    if observed != desktop_pid:
        raise RuntimeError(f"Desktop process identity is ambiguous or changed for target: {target_id}")
    platform.request_desktop_quit(executable, desktop_pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not platform.pid_alive(desktop_pid):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError(f"Target did not exit after normal quit request: {target_id}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = target_session(platform, target_id)
        if current.get("state") in {"available", "unavailable", "unsupported"}:
            return current
        time.sleep(0.1)
    raise RuntimeError(f"Target did not become relaunchable after desktop quit: {target_id}")


def _private_command(platform: DesktopPlatform, command: str, target_id: str, *extra: str) -> list[str]:
    runtime_executable = _frozen_runtime_executable()
    if runtime_executable is None:
        values = [*_python_runtime_command_prefix(), command, "--target", target_id, *extra]
    else:
        values = [str(runtime_executable), command, "--target", target_id, *extra]
    if platform.app_override is not None:
        values.extend(["--app", str(platform.app_override)])
    return values


def _start_detached(command: list[str]) -> subprocess.Popen[bytes]:
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        options["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def _wait_for_session(
    platform: DesktopPlatform,
    target_id: str,
    *,
    expected_responses_route_fingerprint: str | None = None,
    expect_renderer_cdp: bool = False,
    timeout: float = SESSION_START_TIMEOUT_SECONDS,
) -> TargetSession:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = load_session(platform, _metadata(platform), target_id)
        if session is not None:
            if (
                expected_responses_route_fingerprint is not None
                and session.responses_route_fingerprint != expected_responses_route_fingerprint
            ):
                raise RuntimeError(
                    f"Canonical target runtime started with a different Responses route: {target_id}"
                )
            if expect_renderer_cdp and session.renderer_cdp_endpoint is None:
                raise RuntimeError(
                    f"Canonical target runtime started without renderer CDP: {target_id}"
                )
            return session
        time.sleep(0.1)
    raise RuntimeError(f"Canonical target runtime did not become ready: {target_id}")


def launch_target(
    platform: DesktopPlatform,
    target_id: str,
    *,
    responses_route: ResponsesRoute | None = None,
    renderer_cdp: bool = False,
) -> TargetSession:
    target = _target(platform, target_id)
    existing = load_session(platform, _metadata(platform), target_id)
    if existing is not None:
        if responses_route is not None and existing.responses_route_fingerprint != responses_route.fingerprint:
            raise RuntimeError(
                f"Target is already running with a different Responses route; quit it normally once: {target_id}"
            )
        if renderer_cdp and existing.renderer_cdp_endpoint is None:
            raise RuntimeError(
                f"Target is already running without renderer CDP; quit it normally once: {target_id}"
            )
        return existing
    if target.get("state") == "invalid":
        raise RuntimeError(f"Target state is invalid: {target_id}")
    if target.get("sharedAppServerSupported") is not True:
        raise RuntimeError(f"Target does not support canonical runtime sessions: {target_id}")
    if responses_route is not None and target.get("responsesRouteSupported") is not True:
        raise RuntimeError(f"Target does not support launch-time Responses routing: {target_id}")
    if target.get("state") != "stopped":
        raise RuntimeError(
            f"Target is already running outside the canonical runtime; quit it normally once: {target_id}"
        )
    descriptor = descriptor_path(_metadata(platform), target_id)
    try:
        descriptor.unlink()
    except FileNotFoundError:
        pass
    extra: list[str] = []
    if responses_route is not None:
        extra.extend((
            "--responses-base-url",
            responses_route.base_url,
            "--responses-env-key",
            responses_route.env_key,
        ))
    if renderer_cdp:
        extra.append("--renderer-cdp")
    _start_detached(_private_command(platform, "_supervise-target", target_id, *extra))
    return _wait_for_session(
        platform,
        target_id,
        expected_responses_route_fingerprint=(responses_route.fingerprint if responses_route else None),
        expect_renderer_cdp=renderer_cdp,
    )


def _launch_target_attached(
    platform: DesktopPlatform,
    target_id: str,
    app_server_url: str,
    *,
    renderer_cdp_port: int | None = None,
) -> None:
    app_server_url = _validate_loopback_websocket_url(app_server_url)
    target = _target(platform, target_id)
    if target.get("state") != "stopped":
        raise RuntimeError(f"Target must be stopped before canonical runtime launch: {target_id}")
    if target_id == "default":
        platform.launch_default(
            platform.resolve_executable(),
            app_server_url=app_server_url,
            renderer_cdp_port=renderer_cdp_port,
        )
        return
    index = target.get("profileIndex")
    if not isinstance(index, int):
        raise RuntimeError(f"Managed target has no profile index: {target_id}")
    Profile(index=index, platform=platform).launch(
        app_server_url=app_server_url,
        renderer_cdp_port=renderer_cdp_port,
    )


def serve_target(
    platform: DesktopPlatform,
    target_id: str,
    listen_url: str,
    *,
    responses_route: ResponsesRoute | None = None,
) -> None:
    listen_url = _validate_loopback_websocket_url(listen_url)
    target = _target(platform, target_id)
    if target.get("sharedAppServerSupported") is not True:
        raise RuntimeError(f"Target does not expose a supported shared app-server runtime: {target_id}")
    if target.get("state") != "stopped":
        raise RuntimeError(f"Target must be stopped before serving a shared app-server: {target_id}")
    if target_id == "default":
        codex_home = platform.default_codex_home()
    else:
        index = target.get("profileIndex")
        if not isinstance(index, int):
            raise RuntimeError(f"Managed target has no profile index: {target_id}")
        profile = Profile(index=index, platform=platform)
        manifest = profile.load()
        if not manifest.get("ready"):
            raise RuntimeError(f"Target is not ready: {target_id}")
        codex_home = profile.codex
    platform.run_app_server(codex_home, listen_url, responses_route=responses_route)


def _allocate_loopback_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    return f"ws://127.0.0.1:{port}"


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _loopback_port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _terminate_owned(process: subprocess.Popen[bytes], timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        process.kill()
    process.wait()


def supervise_target(
    platform: DesktopPlatform,
    target_id: str,
    *,
    responses_route: ResponsesRoute | None = None,
    renderer_cdp: bool = False,
) -> int:
    claim = acquire_runtime_claim(platform, _metadata(platform), target_id)
    backend: subprocess.Popen[bytes] | None = None
    desktop: subprocess.Popen[bytes] | None = None
    route_proxy: RoutePreservingAppServerProxy | None = None
    descriptor = descriptor_path(_metadata(platform), target_id)
    try:
        target = _target(platform, target_id)
        if target.get("state") != "stopped":
            raise RuntimeError(f"Target must be stopped before canonical runtime supervision: {target_id}")
        startup_deadline = time.monotonic() + SUPERVISOR_STARTUP_TIMEOUT_SECONDS
        backend_endpoint = _allocate_loopback_endpoint()
        endpoint = backend_endpoint
        if responses_route is not None:
            endpoint = _allocate_loopback_endpoint()
            while endpoint == backend_endpoint:
                endpoint = _allocate_loopback_endpoint()
        route_args: list[str] = []
        if responses_route is not None:
            route_args.extend((
                "--responses-base-url",
                responses_route.base_url,
                "--responses-env-key",
                responses_route.env_key,
            ))
        backend = subprocess.Popen(
            _private_command(
                platform,
                "_serve-target",
                target_id,
                "--listen",
                backend_endpoint,
                *route_args,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = min(startup_deadline, time.monotonic() + 10.0)
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError("Canonical app-server exited before becoming ready")
            if endpoint_ready(backend_endpoint):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Canonical app-server readiness timeout")

        if responses_route is not None:
            route_proxy = RoutePreservingAppServerProxy(
                endpoint,
                backend_endpoint,
                responses_route,
            )
            route_proxy.start()
            deadline = min(startup_deadline, time.monotonic() + 5.0)
            while time.monotonic() < deadline:
                if route_proxy.fatal_error is not None:
                    raise RuntimeError("Canonical app-server route proxy failed during startup") from route_proxy.fatal_error
                if endpoint_ready(endpoint):
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("Canonical app-server route proxy readiness timeout")

        renderer_cdp_port = _allocate_loopback_port() if renderer_cdp else None
        renderer_cdp_endpoint = (
            f"http://127.0.0.1:{renderer_cdp_port}"
            if renderer_cdp_port is not None
            else None
        )
        desktop_args = ["--app-server-url", endpoint]
        if renderer_cdp_port is not None:
            desktop_args.extend(("--renderer-cdp-port", str(renderer_cdp_port)))

        desktop = subprocess.Popen(
            _private_command(
                platform,
                "_launch-target-attached",
                target_id,
                *desktop_args,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if renderer_cdp_port is not None:
            deadline = startup_deadline
            while time.monotonic() < deadline:
                if desktop.poll() is not None:
                    raise RuntimeError("Canonical desktop exited before renderer CDP became ready")
                if backend.poll() is not None:
                    raise RuntimeError("Canonical app-server exited before renderer CDP became ready")
                if _loopback_port_ready(renderer_cdp_port):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Renderer CDP readiness timeout")
        atomic_write_session(
            descriptor,
            TargetSession(
                target_id=target_id,
                endpoint=endpoint,
                supervisor_pid=os.getpid(),
                backend_pid=backend.pid,
                desktop_pid=desktop.pid,
                responses_route_fingerprint=(responses_route.fingerprint if responses_route else None),
                renderer_cdp_endpoint=renderer_cdp_endpoint,
            ),
        )
        while True:
            backend_status = backend.poll()
            desktop_status = desktop.poll()
            proxy_failed = route_proxy is not None and not route_proxy.is_alive
            if backend_status is not None or desktop_status is not None or proxy_failed:
                break
            time.sleep(0.2)
        if route_proxy is not None and not route_proxy.is_alive:
            if desktop.poll() is None:
                _terminate_owned(desktop)
            if backend.poll() is None:
                _terminate_owned(backend)
            if route_proxy.fatal_error is not None:
                raise RuntimeError("Canonical app-server route proxy failed") from route_proxy.fatal_error
        if backend.poll() is not None and desktop.poll() is None:
            _terminate_owned(desktop)
        if desktop.poll() is not None and backend.poll() is None:
            _terminate_owned(backend)
        return desktop.returncode if desktop.returncode is not None else (backend.returncode or 0)
    finally:
        try:
            descriptor.unlink()
        except FileNotFoundError:
            pass
        if route_proxy is not None:
            route_proxy.close()
        if desktop is not None and desktop.poll() is None:
            _terminate_owned(desktop)
        if backend is not None and backend.poll() is None:
            _terminate_owned(backend)
        release_runtime_claim(claim)
