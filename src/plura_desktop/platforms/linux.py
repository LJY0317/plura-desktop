from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import sys

from ..domain import ProfileLayout
from .base import DesktopPlatform, STABLE_FROZEN_RUNTIME_ENV


class LinuxPlatform(DesktopPlatform):
    platform_id = "linux"

    _SESSION_ENV_KEYS = (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "XAUTHORITY",
        "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP",
    )

    def _xdg(self, name: str, fallback: str) -> Path:
        value = os.environ.get(name)
        return Path(value).expanduser() if value else self.home / fallback

    def layout(self, index: int) -> ProfileLayout:
        data_home = self._xdg("XDG_DATA_HOME", ".local/share")
        config_home = self._xdg("XDG_CONFIG_HOME", ".config")
        metadata = self._xdg("XDG_STATE_HOME", ".local/state") / "PluraDesktop"
        return self._generic_layout(
            index,
            selector=data_home / f"applications/chatgpt-profile-{index}.desktop",
            codex_home=self.home / f".codex-profile{index}",
            user_data=config_home / f"Codex-Profile{index}",
            metadata=metadata,
            selector_kind="file",
            runtime=metadata / f"profile-{index}-runtime",
        )

    def protected_paths(self) -> tuple[Path, ...]:
        config_home = self._xdg("XDG_CONFIG_HOME", ".config")
        return (self.home / ".codex", config_home / "Codex")

    def default_user_data(self) -> Path:
        return self._xdg("XDG_CONFIG_HOME", ".config") / "Codex"

    def managed_roots(self) -> tuple[Path, ...]:
        return (
            self.home,
            self._xdg("XDG_DATA_HOME", ".local/share"),
            self._xdg("XDG_CONFIG_HOME", ".config"),
            self._xdg("XDG_STATE_HOME", ".local/state"),
        )

    @staticmethod
    def _official_package_root(executable: Path) -> Path | None:
        resolved = executable.resolve()
        root = resolved.parent
        if (
            root.name == "chatgpt"
            and (root / "ChatGPT").is_file()
            and (root / "codex-launcher").is_file()
        ):
            return root
        return None

    def resolve_executable(self, recorded: str | None = None) -> Path:
        candidates = [
            self.app_override,
            Path(recorded) if recorded else None,
            Path(os.environ["CHATGPT_EXECUTABLE"]).expanduser() if os.environ.get("CHATGPT_EXECUTABLE") else None,
        ]
        for name in ("chatgpt", "ChatGPT"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
        for candidate in candidates:
            if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
                resolved = candidate.resolve()
                package_root = self._official_package_root(resolved)
                if package_root is not None and resolved.name == "codex-launcher":
                    desktop = package_root / "ChatGPT"
                    if os.access(desktop, os.X_OK):
                        return desktop.resolve()
                    raise RuntimeError("Official ChatGPT executable is not executable")
                return resolved
        raise RuntimeError(
            "ChatGPT executable not found. Pass --app PATH or set CHATGPT_EXECUTABLE."
        )

    def resolve_codex_executable(self, chatgpt_executable: Path) -> Path:
        # The official Linux package exposes /usr/bin/chatgpt as a symlink to
        # /usr/lib/chatgpt/codex-launcher, while the bundled Codex executable lives at
        # /usr/lib/chatgpt/resources/codex. Resolve relative to the real launcher so Plura
        # stays coupled to the same official package instead of silently picking up an
        # unrelated `codex` from PATH.
        if os.environ.get("CODEX_EXECUTABLE"):
            return super().resolve_codex_executable(chatgpt_executable)

        resolved = chatgpt_executable.resolve()
        package_root = self._official_package_root(resolved)
        if package_root is None:
            return super().resolve_codex_executable(chatgpt_executable)
        bundled = package_root / "resources" / "codex"
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return bundled.resolve()
        raise RuntimeError("Bundled Codex executable is missing or not executable")

    def sanitized_environment(self, layout: ProfileLayout) -> dict[str, str]:
        env = super().sanitized_environment(layout)
        # Linux desktop applications need the active X11/Wayland and D-Bus session boundary.
        # Preserve only the narrow session variables required to join that GUI session rather
        # than inheriting the caller's entire environment.
        for key in self._SESSION_ENV_KEYS:
            value = os.environ.get(key)
            if value:
                env[key] = value
        return env

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
            command_values = (
                sys.executable,
                str(runtime / "plura_desktop_cli.py"),
                "launch-target",
                "--target",
                layout.identifier,
                "--renderer-cdp",
            )
        else:
            if not runtime_executable.is_file():
                raise RuntimeError(f"Standalone Plura runtime executable is missing: {runtime_executable}")
            self.reset_runtime(runtime)
            command_values = (
                str(runtime_executable),
                "launch-target",
                "--target",
                layout.identifier,
                "--renderer-cdp",
            )
        layout.selector.parent.mkdir(parents=True, exist_ok=True)
        command = " ".join(
            shlex.quote(value)
            for value in command_values
        )
        launcher = runtime / "launch-profile"
        if runtime_executable is None:
            launch = "exec " + command
        else:
            launch = (
                f"{STABLE_FROZEN_RUNTIME_ENV}={shlex.quote(str(runtime_executable))} "
                "exec " + command
            )
        launcher.write_text("#!/bin/sh\nset -eu\n" + launch + "\n", encoding="utf-8")
        launcher.chmod(0o700)
        desktop_exec = str(launcher).replace("\\", "\\\\").replace('"', '\\"')
        layout.selector.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={layout.display_name}\n"
            f'Exec="{desktop_exec}"\n'
            "Terminal=false\n"
            "Categories=Network;Utility;\n",
            encoding="utf-8",
        )
        layout.selector.chmod(0o755)

    def running(self, layout: ProfileLayout) -> list[int]:
        proc = Path("/proc")
        if not proc.is_dir():
            raise RuntimeError("/proc is unavailable; refusing to guess profile process state")
        marker = ("--user-data-dir=" + str(layout.user_data)).encode()
        matches: list[int] = []
        for child in proc.iterdir():
            if not child.name.isdigit():
                continue
            try:
                command = (child / "cmdline").read_bytes().replace(b"\x00", b" ")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if marker in command:
                matches.append(int(child.name))
        return sorted(matches)

    def running_default(self) -> list[int]:
        proc = Path("/proc")
        if not proc.is_dir():
            raise RuntimeError("/proc is unavailable; refusing to guess default profile process state")
        marker = ("--user-data-dir=" + str(self.default_user_data())).encode()
        matches: list[int] = []
        for child in proc.iterdir():
            if not child.name.isdigit():
                continue
            try:
                command = (child / "cmdline").read_bytes().replace(b"\x00", b" ")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if marker in command:
                matches.append(int(child.name))
        return sorted(matches)

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
        self.launch_subprocess(
            executable,
            layout,
            env,
            extra_args=tuple(self.renderer_cdp_args(renderer_cdp_port)),
        )

    def launch_default(
        self,
        executable: Path,
        *,
        app_server_url: str | None = None,
        renderer_cdp_port: int | None = None,
    ) -> None:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
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
        subprocess.Popen(
            [str(executable), "--user-data-dir=" + str(self.default_user_data())]
            + self.renderer_cdp_args(renderer_cdp_port),
            env=env,
            cwd=self.home,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
