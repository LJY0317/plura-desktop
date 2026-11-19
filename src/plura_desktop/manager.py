from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any

from .domain import (
    DEFAULT_MANAGED_PROFILE_INDEX,
    MANIFEST_SCHEMA,
    ManagedPath,
)
from .diagnostic_artifacts import validate_profile_diagnostic_tree, validate_runtime_diagnostic_tree
from .lifecycle_diagnostics import UpdateLifecycleReceipt, latest_update_receipt
from .io import atomic_write_json
from .platforms import DesktopPlatform, current_platform
from .platforms.base import STABLE_FROZEN_RUNTIME_ENV


PACKAGE_DIR = Path(__file__).resolve().parent
ENTRYPOINT_SOURCE = PACKAGE_DIR / "_runtime_entrypoint.py"
NORMAL_QUIT_TIMEOUT_SECONDS = 60.0
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

    def _write_platform_integration(
        self, executable: Path, *, receipt: UpdateLifecycleReceipt | None = None
    ) -> None:
        runtime_executable = _frozen_runtime_executable()
        if receipt is not None:
            receipt.stage("selector_staging")
        self.platform.write_selector(
            self.layout,
            ENTRYPOINT_SOURCE,
            PACKAGE_DIR,
            executable,
            runtime_executable=runtime_executable,
        )
        if receipt is not None:
            receipt.stage("control_runtime_prepare")
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
        receipt = UpdateLifecycleReceipt(self.platform, self.layout, "refresh")
        try:
            receipt.stage("update_lock")
            with self.platform.update_guard(self.layout):
                self._refresh_under_guard(receipt)
            receipt.complete()
        except Exception as error:
            receipt.failure(error)
            raise

    def _refresh_under_guard(self, receipt: UpdateLifecycleReceipt) -> None:
        receipt.stage("manifest_validation")
        manifest = self.load()
        if not manifest["ready"]:
            raise RuntimeError("Installation incomplete; refusing refresh")
        if receipt.before["selector"] == "unsafe" or receipt.before["runtime"] == "unsafe":
            raise RuntimeError("Managed profile runtime or selector is unsafe; refusing refresh")
        receipt.stage("official_source_validation")
        executable = self.platform.resolve_executable(manifest["app_executable"])
        source_fingerprint = self.platform.fingerprint(executable)
        changed = source_fingerprint != manifest["official_baseline"]
        receipt.flag("officialSourceChanged", changed)
        if changed or receipt.before["selector"] == "incomplete" or receipt.before["runtime"] == "incomplete":
            receipt.note_reconciliation()
        receipt.stage("managed_process_check")
        running = bool(self.running())
        receipt.flag("managedProfileRunning", running)
        receipt.stage("runtime_prepare")
        previous_generation = self.platform.runtime_generation(self.layout)
        self.platform.prepare_profile_runtime(
            self.layout,
            executable,
            source_fingerprint=source_fingerprint,
            profile_running=running,
        )
        new_generation = self.platform.runtime_generation(self.layout)
        if new_generation is not None and previous_generation != new_generation:
            receipt.flag("managedRuntimeRebuilt", True)
            receipt.note_reconciliation()
        receipt.stage("ownership_validation")
        before = {str(entry.path): self.platform.identity(entry.path) for entry in self.layout.managed}
        self._write_platform_integration(executable, receipt=receipt)
        receipt.stage("ownership_validation")
        for entry in self.layout.managed:
            if self.platform.identity(entry.path) != before[str(entry.path)]:
                raise RuntimeError(f"Managed path identity changed during refresh: {entry.path}")
        receipt.stage("manifest_commit")
        manifest["official_baseline"] = source_fingerprint
        manifest["app_executable"] = str(executable)
        self.save(manifest)
        receipt.stage("final_validation")
        self.load()
        print(f"Refreshed Profile {self.index} selector/runtime without replacing profile state")

    def running(self) -> list[int]:
        return self.platform.running(self.layout)

    def prepare_launch(self) -> Path:
        """Converge disposable managed runtime state on the current official application.

        The official/default ChatGPT installation is the sole application-update authority.
        Managed profile state is persistent, while selectors and per-profile runtimes are derived
        artifacts that may be refreshed only when that managed profile is stopped.
        """
        receipt = UpdateLifecycleReceipt(self.platform, self.layout, "prepare_launch")
        try:
            receipt.stage("update_lock")
            with self.platform.update_guard(self.layout):
                prepared = self._prepare_launch_under_guard(receipt)
            receipt.complete()
            return prepared
        except Exception as error:
            receipt.failure(error)
            raise

    def _prepare_launch_under_guard(self, receipt: UpdateLifecycleReceipt) -> Path:
        if receipt.before["runtime"] in {"missing", "incomplete"}:
            receipt.note_reconciliation()
        receipt.stage("manifest_validation")
        manifest = self.load()
        if not manifest["ready"]:
            raise RuntimeError("Installation incomplete")
        for entry in self.layout.managed:
            if not entry.path.exists() or not self.platform.check_kind(entry):
                raise RuntimeError(f"Managed path missing; refusing implicit recreation: {entry.path}")

        receipt.stage("official_source_validation")
        executable = self.platform.resolve_executable(manifest["app_executable"])
        source_fingerprint = self.platform.fingerprint(executable)
        changed = source_fingerprint != manifest["official_baseline"]
        receipt.flag("officialSourceChanged", changed)
        health = self.platform.selector_health(self.layout)
        if health == "unsafe":
            raise RuntimeError("Managed profile selector is unsafe; refusing automatic repair")
        if self.platform.managed_runtime_health(self.layout) == "unsafe":
            raise RuntimeError("Managed profile runtime is unsafe; refusing automatic repair")
        selector_broken = health == "incomplete"
        receipt.flag("selectorRepairNeeded", selector_broken)
        if changed or selector_broken:
            receipt.stage("managed_process_check")
            running = bool(self.running())
            receipt.flag("managedProfileRunning", running)
            if running:
                raise RuntimeError(
                    f"Quit {self.display_name} before refreshing its desktop runtime and selector"
                )
            receipt.note_reconciliation()
            # Preserve the manifest-owned outer selector directory and all
            # login/conversation state. Only derived code is regenerated.
            self._refresh_under_guard(receipt)
            manifest = self.load()

        receipt.stage("runtime_prepare")
        # A managed clone can change via its own updater even if the
        # official app has not changed. It remains a disposable artifact.
        previous_generation = self.platform.runtime_generation(self.layout)
        prepared_executable = self.platform.prepare_profile_runtime(
            self.layout,
            executable,
            source_fingerprint=manifest["official_baseline"],
            profile_running=bool(self.running()),
        )
        new_generation = self.platform.runtime_generation(self.layout)
        if new_generation is not None and previous_generation != new_generation:
            receipt.flag("managedRuntimeRebuilt", True)
            receipt.note_reconciliation()
        receipt.stage("final_validation")
        # prepare_profile_runtime returned this same-launch verified identity.
        # A second profile_executable() would repeat macOS codesign every
        # launch and can add noticeable startup latency.
        return prepared_executable

    def launch(self) -> None:
        executable = self.prepare_launch()
        manifest = self.load()
        rust_log = None
        if self.tool_lifecycle_diagnostics_enabled(manifest):
            rust_log = self.platform.tool_lifecycle_diagnostic_rust_log()
            if rust_log is None:
                raise RuntimeError(f"Tool-lifecycle diagnostics are unsupported on {self.platform.platform_id}")
        receipt = UpdateLifecycleReceipt(self.platform, self.layout, "launch_desktop")
        try:
            receipt.stage("desktop_spawn")
            self.platform.launch(self.layout, executable, rust_log=rust_log)
            receipt.complete()
        except Exception as error:
            receipt.failure(error)
            raise

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
        print("Selector integrity:", self.platform.selector_health(self.layout))
        print("Managed runtime integrity:", self.platform.managed_runtime_health(self.layout))
        update = latest_update_receipt(self.meta, self.index)
        if update is None:
            print("Latest update incident: none or unavailable")
        else:
            print(
                "Latest update incident:",
                update["outcome"], update["operation"], "at", update["phase"],
            )

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
            "backendPolicy": "official-desktop-owned",
            "selectorIntegrity": self.platform.selector_health(self.layout),
            "managedRuntimeIntegrity": self.platform.managed_runtime_health(self.layout),
        }
        if self.platform.platform_id == "macos":
            base["chatgptUpdateAuthority"] = "official-default-app-only"
            base["managedUpdateTiming"] = "next-cold-launch-after-normal-quit"
            base["manualUpdaterMenu"] = "may-remain-visible-not-supported"
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
        print("Selector integrity:", data.get("selectorIntegrity", "unknown"))
        print("Managed runtime integrity:", data.get("managedRuntimeIntegrity", "unknown"))
        if self.platform.platform_id == "macos":
            print("ChatGPT update authority: official Profile 1 only")
            print("Managed profile updater menu: may be visible; do not use it")
        if data.get("selectorIntegrity") == "incomplete" and data.get("state") == "stopped":
            print(f"Recovery: plura-desktop refresh --profile {self.index} (preserves profile state)")
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
        runtime_diagnostics = validate_runtime_diagnostic_tree(self.meta)
        if runtime_diagnostics is not None:
            self.platform.ensure_no_mounts(runtime_diagnostics)
            shutil.rmtree(runtime_diagnostics)
            try:
                runtime_diagnostics.parent.rmdir()
            except OSError:
                pass
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
        runtime_diagnostics = None
        if len(list(self.meta.glob("profile-*-install-manifest.json"))) == 1:
            runtime_diagnostics = validate_runtime_diagnostic_tree(self.meta)
            if runtime_diagnostics is not None:
                self.platform.ensure_no_mounts(runtime_diagnostics)
        print(f"Remove only these installed paths (including Profile {self.index} conversations/login):")
        for entry in targets:
            print(entry.path)
        if has_diagnostic_artifacts:
            print(self.diagnostic_artifacts)
        if runtime_diagnostics is not None:
            print(runtime_diagnostics)
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

def _target_inventory(platform: DesktopPlatform, target_id: str | None = None) -> dict[str, Any]:
    targets = [{
        "id": "default", "displayName": "ChatGPT", "role": "default", "managed": False,
        "ownership": "official-desktop-profile", "backendPolicy": "official-desktop-owned",
        "state": "running" if platform.running_default() else "stopped",
    }] if target_id in {None, "default"} else []
    metadata = platform.layout(DEFAULT_MANAGED_PROFILE_INDEX).metadata
    if target_id != "default" and metadata.is_dir() and not _is_linklike(metadata):
        for path in sorted(metadata.glob("profile-*-install-manifest.json")):
            if _is_linklike(path) or not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                profile = Profile(index=raw["profile_index"], platform=platform)
                if target_id is None or profile.identifier == target_id:
                    targets.append({**profile.status_data(), "managed": True})
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                continue
    return {"contractVersion": 2, "platform": platform.platform_id, "targets": targets}


def public_targets(platform: DesktopPlatform) -> dict[str, Any]:
    return _target_inventory(platform)


def _target(platform: DesktopPlatform, target_id: str) -> dict[str, Any]:
    targets = [target for target in _target_inventory(platform, target_id)["targets"] if target["id"] == target_id]
    if len(targets) != 1:
        raise RuntimeError(f"Unknown target: {target_id}")
    return targets[0]


def _target_executable(platform: DesktopPlatform, target: dict[str, Any]) -> Path:
    if target["id"] == "default":
        return platform.resolve_executable()
    profile = Profile(index=target["profileIndex"], platform=platform)
    source = platform.resolve_executable(profile.load()["app_executable"])
    return platform.profile_process_executable(profile.layout, source)


def _target_desktop_pid(platform: DesktopPlatform, target: dict[str, Any], executable: Path) -> int | None:
    if target["id"] == "default":
        return platform.desktop_process_id(executable, user_data=platform.default_user_data(),
                                           codex_home=platform.default_codex_home())
    layout = platform.layout(target["profileIndex"])
    return platform.desktop_process_id(executable, user_data=layout.user_data, codex_home=layout.codex_home)


def target_session(platform: DesktopPlatform, target_id: str) -> dict[str, Any]:
    target = _target(platform, target_id)
    value = {"contractVersion": 2, "targetID": target_id, "state": target["state"],
             "backendPolicy": "official-desktop-owned"}
    if target["state"] == "running":
        pid = _target_desktop_pid(platform, target, _target_executable(platform, target))
        if pid is not None:
            value["desktopProcessID"] = pid
    return value


def launch_target(platform: DesktopPlatform, target_id: str, *, activate: bool = False) -> dict[str, Any]:
    target = _target(platform, target_id)
    if target["state"] == "running":
        session = target_session(platform, target_id)
        if activate:
            pid = session.get("desktopProcessID")
            if pid is None:
                raise RuntimeError(f"Exact desktop process identity is unavailable for target: {target_id}")
            platform.request_desktop_activation(_target_executable(platform, target), pid)
        return session
    if target["state"] != "stopped":
        raise RuntimeError(f"Target state is invalid: {target_id}")
    if target_id == "default":
        platform.launch_default(platform.resolve_executable())
    else:
        Profile(index=target["profileIndex"], platform=platform).launch()
    # OS launch acceptance is not evidence that Native tools or account sign-in are ready.
    return {"contractVersion": 2, "targetID": target_id, "state": "launch-requested",
            "backendPolicy": "official-desktop-owned"}


def quit_target(platform: DesktopPlatform, target_id: str, *,
                timeout: float = NORMAL_QUIT_TIMEOUT_SECONDS,
                expected_desktop_pid: int | None = None) -> dict[str, Any]:
    target = _target(platform, target_id)
    session = target_session(platform, target_id)
    pid = session.get("desktopProcessID")
    if expected_desktop_pid is not None and (type(expected_desktop_pid) is not int
            or expected_desktop_pid <= 0 or pid != expected_desktop_pid):
        raise RuntimeError("Desktop changed since confirmation; current application preserved")
    if session["state"] == "stopped":
        return session
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeError(f"Exact desktop process identity is unavailable for target: {target_id}")
    executable = _target_executable(platform, target)
    if _target_desktop_pid(platform, target, executable) != pid:
        raise RuntimeError(f"Desktop process identity is ambiguous or changed for target: {target_id}")
    platform.request_desktop_quit(executable, pid)
    deadline = time.monotonic() + timeout
    while platform.pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if platform.pid_alive(pid):
        raise RuntimeError(f"Target did not exit after normal quit request: {target_id}")
    return target_session(platform, target_id)
