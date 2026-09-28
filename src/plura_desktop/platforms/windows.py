from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

from ..domain import ProfileLayout
from .base import DesktopPlatform, STABLE_FROZEN_RUNTIME_ENV


class WindowsPlatform(DesktopPlatform):
    platform_id = "windows"

    def _local_app_data(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", self.home / "AppData/Local"))

    def _roaming_app_data(self) -> Path:
        return Path(os.environ.get("APPDATA", self.home / "AppData/Roaming"))

    def layout(self, index: int) -> ProfileLayout:
        local = self._local_app_data()
        roaming = self._roaming_app_data()
        metadata = local / "PluraDesktop"
        return self._generic_layout(
            index,
            selector=roaming / f"Microsoft/Windows/Start Menu/Programs/ChatGPT Profile {index}.cmd",
            codex_home=self.home / f".codex-profile{index}",
            user_data=local / f"Codex-Profile{index}",
            metadata=metadata,
            selector_kind="file",
            runtime=metadata / f"profile-{index}-runtime",
        )

    def protected_paths(self) -> tuple[Path, ...]:
        return (self.home / ".codex", self._local_app_data() / "Codex")

    def default_user_data(self) -> Path:
        return self._local_app_data() / "Codex"

    def managed_roots(self) -> tuple[Path, ...]:
        return (self.home, self._local_app_data(), self._roaming_app_data())

    def resolve_executable(self, recorded: str | None = None) -> Path:
        candidates = [
            self.app_override,
            Path(recorded) if recorded else None,
            Path(os.environ["CHATGPT_EXECUTABLE"]).expanduser() if os.environ.get("CHATGPT_EXECUTABLE") else None,
        ]
        found = shutil.which("ChatGPT.exe") or shutil.which("ChatGPT")
        if found:
            candidates.append(Path(found))
        for candidate in candidates:
            if candidate and candidate.is_file():
                return candidate.resolve()
        raise RuntimeError(
            "ChatGPT executable not found. Pass --app PATH or set CHATGPT_EXECUTABLE."
        )

    def write_selector(
        self,
        layout: ProfileLayout,
        entrypoint: Path,
        package_dir: Path,
        executable: Path,
        *,
        runtime_executable: Path | None = None,
    ) -> None:
        runtime = next(entry.path for entry in layout.managed if entry.role == "runtime")
        if runtime_executable is None:
            self.copy_runtime(runtime, entrypoint, package_dir)
            runtime_command = f'"{sys.executable}" "{runtime / "plura_desktop_cli.py"}"'
        else:
            if not runtime_executable.is_file():
                raise RuntimeError(f"Standalone Plura runtime executable is missing: {runtime_executable}")
            self.reset_runtime(runtime)
            runtime_command = f'"{runtime_executable}"'
        stable_env = (
            f'set "{STABLE_FROZEN_RUNTIME_ENV}={runtime_executable}"\r\n'
            if runtime_executable is not None
            else ""
        )
        layout.selector.parent.mkdir(parents=True, exist_ok=True)
        layout.selector.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            f"{stable_env}"
            f'{runtime_command} launch '
            f'--profile {layout.index} --app "{executable}"\r\n',
            encoding="utf-8",
        )

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
            self.copy_runtime(runtime, entrypoint, package_dir)
            command = f'"{sys.executable}" "{runtime / "plura_desktop_cli.py"}"'
        else:
            if not runtime_executable.is_file():
                raise RuntimeError(f"Standalone Plura runtime executable is missing: {runtime_executable}")
            if runtime.exists():
                if self._is_linklike(runtime) or not runtime.is_dir() or not self.is_owned(runtime):
                    raise RuntimeError(f"Runtime directory is unsafe or replaced: {runtime}")
                self.ensure_no_mounts(runtime)
                shutil.rmtree(runtime)
            command = f'"{runtime_executable}"'
        stable_env = (
            f'set "{STABLE_FROZEN_RUNTIME_ENV}={runtime_executable}"\r\n'
            if runtime_executable is not None
            else ""
        )
        helper = metadata / "plura-desktop.cmd"
        if helper.exists():
            if self._is_linklike(helper) or not helper.is_file() or not self.is_owned(helper):
                raise RuntimeError(f"Control CLI is unsafe or replaced: {helper}")
        helper.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            f"{stable_env}"
            f'{command} %*\r\n',
            encoding="utf-8",
        )
        return helper

    def running(self, layout: ProfileLayout) -> list[int]:
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
        if not powershell:
            raise RuntimeError("PowerShell is required to verify profile process ownership")
        marker = ("--user-data-dir=" + str(layout.user_data)).replace("'", "''")
        script = (
            f"$m='{marker}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($m, [StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
            "ForEach-Object { $_.ProcessId }"
        )
        try:
            output = subprocess.check_output(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Unable to inspect running profile processes") from error
        return sorted(int(line) for line in output.splitlines() if line.strip().isdigit())

    def running_default(self) -> list[int]:
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
        if not powershell:
            raise RuntimeError("PowerShell is required to verify default profile process ownership")
        marker = ("--user-data-dir=" + str(self.default_user_data())).replace("'", "''")
        script = (
            f"$m='{marker}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($m, [StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
            "ForEach-Object { $_.ProcessId }"
        )
        try:
            output = subprocess.check_output(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Unable to inspect default profile processes") from error
        return sorted(int(line) for line in output.splitlines() if line.strip().isdigit())

    def pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
        if not powershell:
            return False
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "if (Get-Process -Id $args[0] -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }",
                str(pid),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def sanitized_environment(self, layout: ProfileLayout) -> dict[str, str]:
        keys = (
            "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
            "USERPROFILE", "USERNAME", "APPDATA", "LOCALAPPDATA",
        )
        env = {key: os.environ[key] for key in keys if os.environ.get(key)}
        env["USERPROFILE"] = str(self.home)
        env["CODEX_HOME"] = str(layout.codex_home)
        env["CODEX_ELECTRON_USER_DATA_PATH"] = str(layout.user_data)
        return env

    def launch(
        self,
        layout: ProfileLayout,
        executable: Path,
        *,
        rust_log: str | None,
        app_server_url: str | None = None,
        renderer_cdp_port: int | None = None,
    ) -> None:
        env = self.sanitized_environment(layout)
        if rust_log:
            env["RUST_LOG"] = rust_log
        if app_server_url:
            env["CODEX_APP_SERVER_WS_URL"] = app_server_url
            os.chdir(self.home)
            os.execve(
                str(executable),
                self.process_command(executable, layout) + self.renderer_cdp_args(renderer_cdp_port),
                env,
            )
            return
        creationflags = 0
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            self.process_command(executable, layout) + self.renderer_cdp_args(renderer_cdp_port),
            env=env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    def launch_default(
        self,
        executable: Path,
        *,
        app_server_url: str | None = None,
        renderer_cdp_port: int | None = None,
    ) -> None:
        env = dict(os.environ)
        env["USERPROFILE"] = str(self.home)
        env["CODEX_HOME"] = str(self.default_codex_home())
        env["CODEX_ELECTRON_USER_DATA_PATH"] = str(self.default_user_data())
        if app_server_url:
            env["CODEX_APP_SERVER_WS_URL"] = app_server_url
            os.chdir(self.home)
            os.execve(
                str(executable),
                [str(executable), "--user-data-dir=" + str(self.default_user_data())]
                + self.renderer_cdp_args(renderer_cdp_port),
                env,
            )
            return
        creationflags = 0
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [str(executable), "--user-data-dir=" + str(self.default_user_data())]
            + self.renderer_cdp_args(renderer_cdp_port),
            env=env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
