from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from ..domain import ManagedPath, ProfileLayout, profile_identifier, validate_profile_index
from ..routing import ResponsesRoute


STABLE_FROZEN_RUNTIME_ENV = "PLURA_DESKTOP_STABLE_RUNTIME"


class DesktopPlatform(ABC):
    platform_id: str

    def __init__(self, *, app_override: str | Path | None = None, home: str | Path | None = None):
        self.home = Path(home).expanduser() if home is not None else Path.home()
        self.app_override = Path(app_override).expanduser().resolve() if app_override else None

    @abstractmethod
    def layout(self, index: int) -> ProfileLayout:
        raise NotImplementedError

    @abstractmethod
    def protected_paths(self) -> tuple[Path, ...]:
        raise NotImplementedError

    @abstractmethod
    def resolve_executable(self, recorded: str | None = None) -> Path:
        raise NotImplementedError

    @abstractmethod
    def write_selector(
        self,
        layout: ProfileLayout,
        entrypoint: Path,
        package_dir: Path,
        executable: Path,
        *,
        runtime_executable: Path | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def running(self, layout: ProfileLayout) -> list[int]:
        raise NotImplementedError

    @abstractmethod
    def launch(
        self,
        layout: ProfileLayout,
        executable: Path,
        *,
        rust_log: str | None,
        app_server_url: str | None = None,
        renderer_cdp_port: int | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def default_user_data(self) -> Path:
        raise NotImplementedError

    @abstractmethod
    def running_default(self) -> list[int]:
        raise NotImplementedError

    @abstractmethod
    def launch_default(
        self,
        executable: Path,
        *,
        app_server_url: str | None = None,
        renderer_cdp_port: int | None = None,
    ) -> None:
        raise NotImplementedError

    def desktop_process_id(self, executable: Path) -> int | None:
        """Return the top-level desktop process for one exact app executable when observable.

        This is intentionally optional for platform adapters. It is used only to surface an already
        running, non-canonical desktop instance for user-facing activation; it never grants runtime
        ownership or converts that process into a canonical session.
        """
        return None

    def request_desktop_quit(self, executable: Path, desktop_pid: int) -> None:
        """Ask one exact desktop process to quit through the platform's normal application lifecycle.

        Platforms must not implement this as an unconditional process kill. Callers use it as the
        public normal-shutdown boundary before tearing down the owned app-server runtime.
        """
        raise RuntimeError(f"Normal desktop quit is not supported on {self.platform_id}")

    def pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def default_codex_home(self) -> Path:
        return self.home / ".codex"

    def resolve_codex_executable(self, chatgpt_executable: Path) -> Path:
        override = os.environ.get("CODEX_EXECUTABLE")
        if override:
            candidate = Path(override).expanduser().resolve()
            if candidate.is_file():
                return candidate
        for name in ("codex", "codex.exe"):
            found = shutil.which(name)
            if found:
                return Path(found).resolve()
        raise RuntimeError(
            "Codex executable not found. Set CODEX_EXECUTABLE to the official bundled Codex executable."
        )

    def run_app_server(
        self,
        codex_home: Path,
        listen_url: str,
        *,
        responses_route: ResponsesRoute | None = None,
    ) -> None:
        chatgpt = self.resolve_executable()
        codex = self.resolve_codex_executable(chatgpt)
        env = dict(os.environ)
        env["CODEX_HOME"] = str(codex_home)
        if responses_route is not None and not env.get(responses_route.env_key):
            raise RuntimeError(
                f"Responses route credential environment variable is missing: {responses_route.env_key}"
            )
        command = [
            str(codex),
            "-c",
            "features.code_mode_host=true",
            "app-server",
            "--analytics-default-enabled",
            "-c",
            "plugins.codex-app-tools@openai-bundled.mcp_servers.codex_app.enabled=true",
            "--listen",
            listen_url,
        ]
        if responses_route is not None:
            command[1:1] = responses_route.codex_config_args()
        os.execve(str(codex), command, env)

    def show_launch_error(self, layout: ProfileLayout, error: Exception) -> None:
        print(f"{layout.display_name} launch failed: {error}", file=os.sys.stderr)

    def selector_entry(self, layout: ProfileLayout) -> ManagedPath:
        return next(entry for entry in layout.managed if entry.role == "selector")

    def prepare_profile_runtime(
        self,
        layout: ProfileLayout,
        executable: Path,
        *,
        source_fingerprint: dict[str, str],
        profile_running: bool,
    ) -> None:
        """Prepare any platform-specific derived desktop runtime.

        Generic platforms launch the recorded executable directly. macOS overrides this to keep a
        byte-identical signed runtime bundle at a distinct application URL so LaunchServices can
        distinguish managed profiles from the official default ChatGPT application.
        """

    def profile_executable(
        self,
        layout: ProfileLayout,
        executable: Path,
        *,
        source_fingerprint: dict[str, str],
    ) -> Path:
        return executable

    def remove_profile_runtime(self, layout: ProfileLayout) -> None:
        """Remove only platform-generated per-profile runtime artifacts."""

    def tool_lifecycle_diagnostic_rust_log(self) -> str | None:
        """Return an adapter-scoped structural trace filter when explicitly supported."""
        return None

    def write_control_cli(
        self,
        metadata: Path,
        entrypoint: Path,
        package_dir: Path,
        *,
        runtime_executable: Path | None = None,
    ) -> Path:
        runtime = metadata / "control-runtime"
        if runtime_executable is None:
            staging = Path(tempfile.mkdtemp(prefix=".control-runtime-next-", dir=metadata))
            backup = staging.with_name(staging.name.replace("-next-", "-old-", 1))
            swapped = False
            try:
                # Build the replacement without touching the currently executing control runtime.
                # This matters when `plura-desktop refresh` is itself running from control-runtime.
                self.copy_runtime(staging, entrypoint, package_dir)
                if runtime.exists():
                    if self._is_linklike(runtime) or not runtime.is_dir() or not self.is_owned(runtime):
                        raise RuntimeError(f"Runtime directory is unsafe or replaced: {runtime}")
                    self.ensure_no_mounts(runtime)
                    runtime.rename(backup)
                try:
                    staging.rename(runtime)
                    swapped = True
                except Exception:
                    if backup.exists() and not runtime.exists():
                        backup.rename(runtime)
                    raise
                if backup.exists():
                    self.ensure_no_mounts(backup)
                    shutil.rmtree(backup)
            finally:
                if not swapped and staging.exists():
                    self.ensure_no_mounts(staging)
                    shutil.rmtree(staging)
        else:
            if not runtime_executable.is_file():
                raise RuntimeError(f"Standalone Plura runtime executable is missing: {runtime_executable}")
            if runtime.exists():
                if self._is_linklike(runtime) or not runtime.is_dir() or not self.is_owned(runtime):
                    raise RuntimeError(f"Runtime directory is unsafe or replaced: {runtime}")
                self.ensure_no_mounts(runtime)
                shutil.rmtree(runtime)
        helper = metadata / "plura-desktop"
        if helper.exists():
            if self._is_linklike(helper) or not helper.is_file() or not self.is_owned(helper):
                raise RuntimeError(f"Control CLI is unsafe or replaced: {helper}")
        temporary_helper = metadata / f".plura-desktop-{os.getpid()}.tmp"
        if runtime_executable is None:
            command = f'{shlex.quote(sys.executable)} "$(dirname "$0")/control-runtime/plura_desktop_cli.py"'
            launch = f'exec {command} "$@"'
        else:
            command = shlex.quote(str(runtime_executable))
            stable = shlex.quote(str(runtime_executable))
            launch = f'{STABLE_FROZEN_RUNTIME_ENV}={stable} exec {command} "$@"'
        temporary_helper.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            f"{launch}\n",
            encoding="utf-8",
        )
        temporary_helper.chmod(0o700)
        os.replace(temporary_helper, helper)
        return helper

    def managed_roots(self) -> tuple[Path, ...]:
        return (self.home,)

    @staticmethod
    def _is_linklike(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())

    def assert_safe_ancestry(self, path: Path) -> None:
        roots = [
            root
            for root in self.managed_roots()
            if path == root or root in path.parents
        ]
        if not roots:
            raise RuntimeError(f"Managed path is outside platform user roots: {path}")
        root = max(roots, key=lambda item: len(item.parts))
        candidate = path
        while candidate != root:
            if candidate.exists() and self._is_linklike(candidate):
                raise RuntimeError(f"Symlink/junction path refused: {candidate}")
            candidate = candidate.parent

    def is_owned(self, path: Path) -> bool:
        if os.name == "nt" or not hasattr(os, "getuid"):
            return True
        return path.stat().st_uid == os.getuid()

    def identity(self, path: Path) -> dict[str, Any]:
        value = path.stat()
        return {"kind": "stat", "device": int(value.st_dev), "inode": int(value.st_ino)}

    def valid_identity(self, value: object) -> bool:
        return (
            isinstance(value, dict)
            and value.get("kind") == "stat"
            and isinstance(value.get("device"), int)
            and isinstance(value.get("inode"), int)
            and value["inode"] > 0
        )

    def fingerprint(self, executable: Path) -> dict[str, str]:
        if not executable.is_file():
            raise RuntimeError(f"ChatGPT executable missing: {executable}")
        digest = hashlib.sha256()
        with executable.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        return {"executable_sha256": digest.hexdigest()}

    def sanitized_environment(self, layout: ProfileLayout) -> dict[str, str]:
        keys = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
        env = {key: os.environ[key] for key in keys if os.environ.get(key)}
        env["HOME"] = str(self.home)
        env["CODEX_HOME"] = str(layout.codex_home)
        env["CODEX_ELECTRON_USER_DATA_PATH"] = str(layout.user_data)
        return env

    def _generic_layout(
        self,
        index: int,
        *,
        selector: Path,
        codex_home: Path,
        user_data: Path,
        metadata: Path,
        selector_kind: str,
        runtime: Path | None = None,
    ) -> ProfileLayout:
        index = validate_profile_index(index)
        display_name = f"ChatGPT Profile {index}"
        managed = [
            ManagedPath("selector", selector, selector_kind),
            ManagedPath("codex_home", codex_home, "directory"),
            ManagedPath("user_data", user_data, "directory"),
        ]
        if runtime is not None:
            managed.append(ManagedPath("runtime", runtime, "directory"))
        return ProfileLayout(
            index=index,
            identifier=profile_identifier(index),
            display_name=display_name,
            selector=selector,
            codex_home=codex_home,
            user_data=user_data,
            metadata=metadata,
            manifest=metadata / f"profile-{index}-install-manifest.json",
            managed=tuple(managed),
        )

    def copy_runtime(self, runtime: Path, entrypoint: Path, package_dir: Path) -> None:
        self.reset_runtime(runtime)
        shutil.copy2(entrypoint, runtime / "plura_desktop_cli.py")
        shutil.copytree(
            package_dir,
            runtime / "plura_desktop",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if os.name != "nt":
            (runtime / "plura_desktop_cli.py").chmod(0o700)

    def reset_runtime(self, runtime: Path) -> None:
        if runtime.exists():
            if self._is_linklike(runtime) or not runtime.is_dir() or not self.is_owned(runtime):
                raise RuntimeError(f"Runtime directory is unsafe or replaced: {runtime}")
        runtime.mkdir(parents=True, exist_ok=True)
        for child in list(runtime.iterdir()):
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                self.ensure_no_mounts(child)
                shutil.rmtree(child)
            else:
                raise RuntimeError(f"Unexpected runtime entry: {child}")

    def ensure_no_mounts(self, path: Path) -> None:
        if not path.is_dir():
            return
        for root, dirs, _ in os.walk(path, followlinks=False):
            if os.path.ismount(root):
                raise RuntimeError(f"Mounted subtree refused: {root}")
            for directory in dirs:
                candidate = Path(root) / directory
                if not candidate.is_symlink() and os.path.ismount(candidate):
                    raise RuntimeError(f"Mounted subtree refused: {candidate}")

    def remove_managed(self, entry: ManagedPath) -> None:
        path = entry.path
        if entry.kind == "directory":
            self.ensure_no_mounts(path)
            shutil.rmtree(path)
        else:
            path.unlink()

    def check_kind(self, entry: ManagedPath) -> bool:
        if entry.kind == "directory":
            return entry.path.is_dir()
        return entry.path.is_file()

    def process_command(self, executable: Path, layout: ProfileLayout) -> list[str]:
        return [str(executable), "--user-data-dir=" + str(layout.user_data)]

    @staticmethod
    def renderer_cdp_args(port: int | None) -> list[str]:
        if port is None:
            return []
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Renderer CDP port must be between 1 and 65535")
        origin = f"http://127.0.0.1:{port}"
        return [
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--remote-allow-origins={origin}",
        ]

    def launch_subprocess(
        self,
        executable: Path,
        layout: ProfileLayout,
        env: dict[str, str],
        *,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        subprocess.Popen(
            self.process_command(executable, layout) + list(extra_args),
            env=env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
        )
