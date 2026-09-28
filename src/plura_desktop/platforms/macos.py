from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

from ..domain import ProfileLayout
from ..version import __version__
from .base import DesktopPlatform, STABLE_FROZEN_RUNTIME_ENV


DEFAULT_APP_BUNDLE = Path("/Applications/ChatGPT.app")
DEFAULT_EXECUTABLE = DEFAULT_APP_BUNDLE / "Contents/MacOS/ChatGPT"
TOOL_LIFECYCLE_DIAGNOSTIC_RUST_LOG = (
    "info,"
    "codex_mcp::connection_manager::tool_catalog=trace,"
    "codex_core::tools::spec_plan=trace,"
    "rmcp::service=info,"
    "codex_app_server::request_processors::thread_lifecycle=debug,"
    "codex_app_server::thread_state=debug,"
    "codex_core::session::world_state=trace"
)


class MacOSPlatform(DesktopPlatform):
    @staticmethod
    def _command_has_exact_argument(command: str, argument: str) -> bool:
        """Match one exact argv-shaped token in `ps ... args=` output.

        macOS `ps` flattens argv into a display string and does not preserve a machine-readable
        delimiter. Requiring whitespace/start before the argument and whitespace/end after it keeps
        the profile-1 marker `.../Codex` from matching managed paths such as `.../Codex-Profile2`.
        """
        return re.search(r"(?:^|\s)" + re.escape(argument) + r"(?=$|\s)", command) is not None

    platform_id = "macos"

    @staticmethod
    def _current_username() -> str:
        if not hasattr(os, "getuid"):
            raise RuntimeError("macOS user identity is unavailable on this platform")
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name

    def tool_lifecycle_diagnostic_rust_log(self) -> str | None:
        # These target names are an observational macOS diagnostic boundary, not a core contract.
        return TOOL_LIFECYCLE_DIAGNOSTIC_RUST_LOG

    def layout(self, index: int) -> ProfileLayout:
        metadata = self.home / "Library/Application Support/PluraDesktop"
        return self._generic_layout(
            index,
            selector=self.home / f"Applications/ChatGPT Profile {index}.app",
            codex_home=self.home / f".codex-profile{index}",
            user_data=self.home / f"Library/Application Support/Codex-Profile{index}",
            metadata=metadata,
            selector_kind="directory",
        )

    @staticmethod
    def _bundle_for_executable(executable: Path) -> Path:
        try:
            if executable.name != "ChatGPT" or executable.parent.name != "MacOS":
                raise RuntimeError
            bundle = executable.parents[2]
        except (IndexError, RuntimeError) as error:
            raise RuntimeError(f"ChatGPT executable is not inside an application bundle: {executable}") from error
        if bundle.suffix != ".app":
            raise RuntimeError(f"ChatGPT executable is not inside an application bundle: {executable}")
        return bundle

    def _profile_runtime_root(self, layout: ProfileLayout) -> Path:
        return layout.metadata / "runtime-apps" / f"profile-{layout.index}"

    def _profile_runtime_bundle(self, layout: ProfileLayout) -> Path:
        return self._profile_runtime_root(layout) / "ChatGPT.app"

    def _profile_runtime_executable(self, layout: ProfileLayout) -> Path:
        return self._profile_runtime_bundle(layout) / "Contents/MacOS/ChatGPT"

    @staticmethod
    def _core_paths(bundle: Path) -> tuple[Path, ...]:
        return (
            bundle / "Contents/Info.plist",
            bundle / "Contents/MacOS/ChatGPT",
            bundle / "Contents/Resources/app.asar",
        )

    def _core_stats(self, executable: Path) -> dict[str, list[int]]:
        bundle = self._bundle_for_executable(executable)
        paths = self._core_paths(bundle)
        if not all(path.is_file() for path in paths):
            value = executable.stat()
            return {"Contents/MacOS/ChatGPT": [int(value.st_size), int(value.st_mtime_ns)]}
        return {
            str(path.relative_to(bundle)): [int(path.stat().st_size), int(path.stat().st_mtime_ns)]
            for path in paths
        }

    def _runtime_marker(self, layout: ProfileLayout) -> Path:
        return self._profile_runtime_root(layout) / "runtime.json"

    @staticmethod
    def _fingerprints_equal(left: object, right: object) -> bool:
        return isinstance(left, dict) and left == right

    def _verify_runtime_signature(self, bundle: Path) -> None:
        if self.app_override is not None:
            return
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _load_runtime_marker(self, layout: ProfileLayout) -> dict[str, object] | None:
        marker = self._runtime_marker(layout)
        if not marker.is_file() or self._is_linklike(marker):
            return None
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        return value

    def _runtime_matches(
        self,
        layout: ProfileLayout,
        executable: Path,
        source_fingerprint: dict[str, str],
    ) -> bool:
        runtime_executable = self._profile_runtime_executable(layout)
        marker = self._load_runtime_marker(layout)
        if not runtime_executable.is_file() or marker is None:
            return False
        if marker.get("sourceFingerprint") != source_fingerprint:
            return False
        if marker.get("sourceStats") != self._core_stats(executable):
            return False
        try:
            self._verify_runtime_signature(self._profile_runtime_bundle(layout))
        except (OSError, subprocess.SubprocessError):
            return False
        return self.fingerprint(runtime_executable) == source_fingerprint

    def prepare_profile_runtime(
        self,
        layout: ProfileLayout,
        executable: Path,
        *,
        source_fingerprint: dict[str, str],
        profile_running: bool,
    ) -> None:
        runtime_root = self._profile_runtime_root(layout)
        runtime_parent = runtime_root.parent
        self.assert_safe_ancestry(runtime_parent)
        runtime_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._is_linklike(runtime_parent) or not runtime_parent.is_dir() or not self.is_owned(runtime_parent):
            raise RuntimeError(f"Managed runtime parent is unsafe or replaced: {runtime_parent}")

        if self._runtime_matches(layout, executable, source_fingerprint):
            return
        if runtime_root.exists():
            if self._is_linklike(runtime_root) or not runtime_root.is_dir() or not self.is_owned(runtime_root):
                raise RuntimeError(f"Managed runtime is unsafe or replaced: {runtime_root}")
            if profile_running:
                raise RuntimeError(
                    f"Quit {layout.display_name} before refreshing its signed ChatGPT runtime."
                )

        source_bundle = self._bundle_for_executable(executable)
        temporary_root = Path(tempfile.mkdtemp(prefix=f".profile-{layout.index}-runtime-", dir=runtime_parent))
        temporary_bundle = temporary_root / "ChatGPT.app"
        try:
            subprocess.run(
                ["/bin/cp", "-cR", str(source_bundle), str(temporary_bundle)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            temporary_executable = temporary_bundle / "Contents/MacOS/ChatGPT"
            self._verify_runtime_signature(temporary_bundle)
            if self.fingerprint(temporary_executable) != source_fingerprint:
                raise RuntimeError("Managed ChatGPT runtime clone does not match the official application")
            marker = {
                "version": 1,
                "sourceFingerprint": source_fingerprint,
                "sourceStats": self._core_stats(executable),
            }
            (temporary_root / "runtime.json").write_text(
                json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            backup = runtime_root.with_name(f".{runtime_root.name}.previous-{os.getpid()}")
            if backup.exists():
                self.ensure_no_mounts(backup)
                shutil.rmtree(backup)
            moved_old = False
            if runtime_root.exists():
                runtime_root.rename(backup)
                moved_old = True
            try:
                temporary_root.rename(runtime_root)
            except Exception:
                if moved_old and not runtime_root.exists() and backup.exists():
                    backup.rename(runtime_root)
                raise
            if backup.exists():
                self.ensure_no_mounts(backup)
                shutil.rmtree(backup)
        finally:
            if temporary_root.exists():
                self.ensure_no_mounts(temporary_root)
                shutil.rmtree(temporary_root)

        if not self._runtime_matches(layout, executable, source_fingerprint):
            raise RuntimeError("Managed ChatGPT runtime verification failed after refresh")

    def profile_executable(
        self,
        layout: ProfileLayout,
        executable: Path,
        *,
        source_fingerprint: dict[str, str],
    ) -> Path:
        if not self._runtime_matches(layout, executable, source_fingerprint):
            raise RuntimeError(
                f"{layout.display_name} signed ChatGPT runtime is missing or stale; run refresh first."
            )
        return self._profile_runtime_executable(layout)

    def remove_profile_runtime(self, layout: ProfileLayout) -> None:
        runtime_root = self._profile_runtime_root(layout)
        if not runtime_root.exists():
            return
        self.assert_safe_ancestry(runtime_root)
        if self._is_linklike(runtime_root) or not runtime_root.is_dir() or not self.is_owned(runtime_root):
            raise RuntimeError(f"Managed runtime is unsafe or replaced; preserving: {runtime_root}")
        self.ensure_no_mounts(runtime_root)
        shutil.rmtree(runtime_root)
        runtime_parent = runtime_root.parent
        try:
            runtime_parent.rmdir()
        except OSError:
            pass

    def protected_paths(self) -> tuple[Path, ...]:
        return (
            DEFAULT_APP_BUNDLE,
            self.home / ".codex",
            self.home / "Library/Application Support/Codex",
        )

    def resolve_executable(self, recorded: str | None = None) -> Path:
        executable = self.app_override or DEFAULT_EXECUTABLE
        if recorded and self.app_override is None and Path(recorded) != DEFAULT_EXECUTABLE:
            raise RuntimeError("macOS manifest executable is not the canonical official ChatGPT executable")
        if not executable.is_file():
            raise RuntimeError(f"Official ChatGPT executable missing: {executable}")
        return executable

    def identity(self, path: Path) -> dict[str, object]:
        value = path.stat()
        return {"volume_uuid": self.volume_uuid(path), "inode": int(value.st_ino)}

    def valid_identity(self, value: object) -> bool:
        if not isinstance(value, dict) or set(value) != {"volume_uuid", "inode"}:
            return False
        try:
            uuid.UUID(value["volume_uuid"])
        except (AttributeError, TypeError, ValueError):
            return False
        return isinstance(value["inode"], int) and value["inode"] > 0

    @staticmethod
    def volume_uuid(path: Path) -> str:
        try:
            df = subprocess.check_output(
                ["/bin/df", "-P", str(path)],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            lines = [line for line in df.splitlines() if line.strip()]
            if len(lines) < 2:
                raise RuntimeError("Filesystem identity lookup returned no mount data")
            device = lines[-1].split()[0]
            if not device.startswith("/dev/"):
                raise RuntimeError("Managed path is not on a local disk volume")
            output = subprocess.check_output(
                ["/usr/sbin/diskutil", "info", "-plist", device],
                stderr=subprocess.DEVNULL,
            )
            info = plistlib.loads(output)
        except (OSError, plistlib.InvalidFileException, subprocess.SubprocessError) as error:
            raise RuntimeError("APFS volume identity lookup failed") from error
        if info.get("FilesystemType") != "apfs":
            raise RuntimeError("Managed path is not on APFS")
        try:
            return str(uuid.UUID(info.get("VolumeUUID"))).upper()
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError("APFS Volume UUID unavailable") from error

    def write_selector(
        self,
        layout: ProfileLayout,
        entrypoint: Path,
        package_dir: Path,
        executable: Path,
        *,
        runtime_executable: Path | None = None,
    ) -> None:
        contents = executable.parents[1]
        info_path = contents / "Info.plist"
        try:
            with info_path.open("rb") as file:
                app_info = plistlib.load(file)
        except (OSError, plistlib.InvalidFileException) as error:
            raise RuntimeError(f"Official ChatGPT Info.plist is unreadable: {info_path}") from error
        icon_name = app_info.get("CFBundleIconFile")
        if not isinstance(icon_name, str) or not icon_name:
            raise RuntimeError("Official ChatGPT bundle does not declare CFBundleIconFile")
        if Path(icon_name).suffix == "":
            icon_name += ".icns"
        icon = contents / "Resources" / icon_name
        if not icon.is_file():
            raise RuntimeError(f"Official ChatGPT icon missing: {icon}")
        self.ensure_no_mounts(layout.selector)
        for child in list(layout.selector.iterdir()):
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                self.ensure_no_mounts(child)
                shutil.rmtree(child)
            else:
                raise RuntimeError(f"Unexpected selector entry: {child}")
        resources = layout.selector / "Contents/Resources"
        macos = layout.selector / "Contents/MacOS"
        resources.mkdir(parents=True, exist_ok=True)
        macos.mkdir(exist_ok=True)
        if runtime_executable is None:
            shutil.copy2(entrypoint, resources / "plura_desktop_cli.py")
            shutil.copytree(
                package_dir,
                resources / "plura_desktop",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        shutil.copy2(icon, resources / "icon.icns")
        if runtime_executable is None:
            command = f'{shlex.quote(sys.executable)} "$(dirname "$0")/../Resources/plura_desktop_cli.py"'
            launch = f'exec {command} launch-target --target {layout.identifier} --renderer-cdp'
        else:
            if not runtime_executable.is_file():
                raise RuntimeError(f"Standalone Plura runtime executable is missing: {runtime_executable}")
            command = shlex.quote(str(runtime_executable))
            stable = shlex.quote(str(runtime_executable))
            launch = (
                f'{STABLE_FROZEN_RUNTIME_ENV}={stable} exec {command} '
                f'launch-target --target {layout.identifier} --renderer-cdp'
            )
        launcher = (
            "#!/bin/sh\n"
            "set -eu\n"
            f"{launch}\n"
        )
        launcher_path = macos / "launcher"
        launcher_path.write_text(launcher, encoding="utf-8")
        launcher_path.chmod(0o755)
        info = {
            "CFBundleIdentifier": layout.identifier,
            "CFBundleDisplayName": layout.display_name,
            "CFBundleName": layout.display_name,
            "CFBundleExecutable": "launcher",
            "CFBundleIconFile": "icon.icns",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": __version__,
            "CFBundleShortVersionString": __version__,
            "LSUIElement": True,
        }
        with (layout.selector / "Contents/Info.plist").open("wb") as file:
            plistlib.dump(info, file)
        plutil = Path("/usr/bin/plutil")
        if plutil.is_file():
            subprocess.run([str(plutil), "-lint", str(layout.selector / "Contents/Info.plist")], check=True)

    def running(self, layout: ProfileLayout) -> list[int]:
        try:
            rows = subprocess.check_output(["ps", "-axo", "pid=,args="], text=True)
        except subprocess.SubprocessError as error:
            raise RuntimeError("Unable to inspect running profile processes") from error
        marker = "--user-data-dir=" + str(layout.user_data)
        return [
            int(row.strip().split(None, 1)[0])
            for row in rows.splitlines()
            if self._command_has_exact_argument(row, marker)
        ]

    def default_user_data(self) -> Path:
        return self.home / "Library/Application Support/Codex"

    def running_default(self) -> list[int]:
        try:
            rows = subprocess.check_output(["ps", "-axo", "pid=,args="], text=True)
        except subprocess.SubprocessError as error:
            raise RuntimeError("Unable to inspect default profile processes") from error
        marker = "--user-data-dir=" + str(self.default_user_data())
        return [
            int(row.strip().split(None, 1)[0])
            for row in rows.splitlines()
            if self._command_has_exact_argument(row, marker)
        ]

    def desktop_process_id(self, executable: Path) -> int | None:
        try:
            rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,args="], text=True)
        except subprocess.SubprocessError as error:
            raise RuntimeError("Unable to inspect ChatGPT desktop process identity") from error
        expected = str(executable)
        matches: list[int] = []
        for row in rows.splitlines():
            stripped = row.strip()
            parts = stripped.split(None, 2)
            if len(parts) != 3 or not parts[0].isdigit():
                continue
            command = parts[2]
            if command == expected or command.startswith(expected + " "):
                matches.append(int(parts[0]))
        return matches[0] if len(matches) == 1 else None

    def request_desktop_quit(self, executable: Path, desktop_pid: int) -> None:
        bundle = self._bundle_for_executable(executable)
        observed = self.desktop_process_id(executable)
        if observed != desktop_pid:
            raise RuntimeError("ChatGPT desktop process identity changed before normal quit")
        escaped = str(bundle).replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "{escaped}" to quit'
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Unable to request normal ChatGPT quit") from error
        if result.returncode != 0:
            detail = " ".join(result.stderr.split())[:240]
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"ChatGPT normal quit request failed{suffix}")

    def sanitized_environment(self, layout: ProfileLayout) -> dict[str, str]:
        username = self._current_username()
        env = {
            "HOME": str(self.home),
            "USER": username,
            "LOGNAME": username,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "CODEX_HOME": str(layout.codex_home),
            "CODEX_ELECTRON_USER_DATA_PATH": str(layout.user_data),
        }
        if os.environ.get("TMPDIR"):
            env["TMPDIR"] = os.environ["TMPDIR"]
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

    def launch_default(
        self,
        executable: Path,
        *,
        app_server_url: str | None = None,
        renderer_cdp_port: int | None = None,
    ) -> None:
        username = self._current_username()
        env = {
            "HOME": str(self.home),
            "USER": username,
            "LOGNAME": username,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "CODEX_HOME": str(self.default_codex_home()),
            "CODEX_ELECTRON_USER_DATA_PATH": str(self.default_user_data()),
        }
        if os.environ.get("TMPDIR"):
            env["TMPDIR"] = os.environ["TMPDIR"]
        if app_server_url:
            env["CODEX_APP_SERVER_WS_URL"] = app_server_url
        os.chdir(self.home)
        os.execve(
            str(executable),
            [str(executable), "--user-data-dir=" + str(self.default_user_data())]
            + self.renderer_cdp_args(renderer_cdp_port),
            env,
        )

    def resolve_codex_executable(self, chatgpt_executable: Path) -> Path:
        resources = chatgpt_executable.parents[1] / "Resources"
        legacy = resources / "codex"
        if legacy.is_file() and os.access(legacy, os.X_OK):
            return legacy

        package_root = resources / "codex-cli"
        package_manifest = package_root / "codex-package.json"
        if package_manifest.is_file():
            try:
                package = json.loads(package_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("Bundled Codex package manifest is unreadable") from error
            entrypoint = package.get("entrypoint")
            if not isinstance(entrypoint, str) or not entrypoint or Path(entrypoint).is_absolute():
                raise RuntimeError("Bundled Codex package manifest has an invalid entrypoint")
            root = package_root.resolve()
            candidate = (package_root / entrypoint).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise RuntimeError("Bundled Codex package entrypoint escapes its package root") from error
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
            raise RuntimeError("Bundled Codex package entrypoint is missing or not executable")
        return super().resolve_codex_executable(chatgpt_executable)

    def show_launch_error(self, layout: ProfileLayout, error: Exception) -> None:
        detail = " ".join(str(error).split())[:240]
        message = (
            f"{layout.display_name}을 시작하지 못했습니다.\n\n"
            "설치 상태를 status 명령으로 확인하고 selector/runtime 갱신이 필요하면 "
            "refresh 명령을 실행하세요.\n\n"
            "오류: " + detail
        )
        script = (
            "on run argv\n"
            f'display alert "{layout.display_name} 실행 실패" message (item 1 of argv) '
            'as critical buttons {"확인"} default button "확인"\n'
            "end run"
        )
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", script, message],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def fingerprint(self, executable: Path) -> dict[str, str]:
        try:
            bundle = self._bundle_for_executable(executable)
            paths = self._core_paths(bundle)
        except RuntimeError:
            return super().fingerprint(executable)
        if all(path.is_file() for path in paths):
            return {
                str(path.relative_to(bundle)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths
            }
        return super().fingerprint(executable)
