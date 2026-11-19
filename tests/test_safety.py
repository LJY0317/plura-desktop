"""Safety/lifecycle tests that never touch real ChatGPT or Codex profile data."""

from __future__ import annotations

import json
import io
import os
from pathlib import Path
import plistlib
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys

sys.path.insert(0, str(SRC))

from plura_desktop.domain import MANIFEST_SCHEMA
from plura_desktop.manager import Profile, STABLE_FROZEN_RUNTIME_ENV, _frozen_runtime_executable, _target, launch_target, public_targets, quit_target, target_session
from plura_desktop.platforms.base import DesktopPlatform
from plura_desktop.platforms.linux import LinuxPlatform
from plura_desktop.platforms.macos import MacOSPlatform
from plura_desktop.platforms.windows import WindowsPlatform
from plura_desktop.cli import main as cli_main


class FakePlatform(DesktopPlatform):
    platform_id = "test"

    def __init__(self, home: Path, app: Path):
        super().__init__(home=home, app_override=app)
        self.running_pids: list[int] = []
        self.default_running_pids: list[int] = []
        self.launched = False
        self.default_launched = False
        self.desktop_process_ids: dict[str, int] = {}
        self.quit_requests: list[tuple[str, int]] = []

    def layout(self, index):
        meta = self.home / "state/PluraDesktop"
        return self._generic_layout(
            index,
            selector=self.home / f"selectors/profile-{index}",
            codex_home=self.home / f".codex-profile{index}",
            user_data=self.home / f"data/Codex-Profile{index}",
            metadata=meta,
            selector_kind="directory",
        )

    def protected_paths(self):
        return (self.home / ".codex", self.home / "data/Codex")

    def default_user_data(self):
        return self.home / "data/Codex"

    def resolve_executable(self, recorded=None):
        candidate = self.app_override or (Path(recorded) if recorded else None)
        if candidate is None or not candidate.is_file():
            raise RuntimeError("fixture app missing")
        return candidate


    def tool_lifecycle_diagnostic_rust_log(self):
        return "info,test_structural_target=trace"

    def write_selector(
        self,
        layout,
        entrypoint,
        package_dir,
        executable,
        *,
        runtime_executable=None,
    ):
        runtime_line = f"runtime={runtime_executable}\n" if runtime_executable else "runtime=python-package\n"
        (layout.selector / "launcher.txt").write_text(
            f"profile={layout.index}\napp={executable.name}\n{runtime_line}", encoding="utf-8"
        )

    def running(self, layout):
        return list(self.running_pids)

    def running_default(self):
        return list(self.default_running_pids)

    def launch(self, layout, executable, *, rust_log):
        self.launched = True

    def launch_default(self, executable):
        self.default_launched = True

    def desktop_process_id(self, executable, *, user_data=None, codex_home=None):
        return self.desktop_process_ids.get(str(executable))

    def request_desktop_quit(self, executable, desktop_pid):
        self.quit_requests.append((str(executable), desktop_pid))
        self.desktop_process_ids.pop(str(executable), None)

    def pid_alive(self, pid):
        return pid == os.getpid() or any(value == pid for value in self.desktop_process_ids.values())


class SafetyTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plura-desktop-test-", dir=ROOT)
        self.home = Path(self.tmp.name)
        (self.home / "state").mkdir()
        (self.home / "data").mkdir()
        self.app = self.home / "fake-chatgpt"
        self.app.write_bytes(b"fake-app")
        if os.name != "nt":
            self.app.chmod(0o755)
        self.platform = FakePlatform(self.home, self.app)
        self.profile = Profile(index=2, platform=self.platform)
        for path in self.platform.protected_paths():
            path.mkdir(parents=True)
            (path / "sentinel").write_text("original", encoding="utf-8")

    def tearDown(self):
        for path in self.platform.protected_paths():
            self.assertEqual((path / "sentinel").read_text(encoding="utf-8"), "original")
        self.tmp.cleanup()

    def test_install_manifest_is_platform_neutral_and_uninstall_is_dry_run(self):
        self.profile.install()
        manifest = self.profile.load()
        self.assertEqual(manifest["schema"], MANIFEST_SCHEMA)
        self.assertEqual(manifest["platform"], "test")
        self.assertEqual(
            {entry["role"] for entry in manifest["created"]},
            {"selector", "codex_home", "user_data"},
        )
        self.assertEqual(list(self.profile.codex.iterdir()), [])
        self.assertEqual(list(self.profile.data.iterdir()), [])
        self.profile.uninstall()
        self.assertTrue(self.profile.wrapper.exists())
        self.profile.uninstall(True)
        self.assertFalse(self.profile.wrapper.exists())
        self.assertFalse(self.profile.codex.exists())
        self.assertFalse(self.profile.data.exists())

    def test_removal_preserves_tela_resources_without_a_tela_dependency(self):
        tela = self.home / "state" / "Tela" / "runtime-fixture"
        tela.parent.mkdir(parents=True)
        tela.write_bytes(b"tela-owned-fixture")
        original_app = self.app.read_bytes()
        self.profile.install()
        self.profile.uninstall()
        self.assertTrue(self.profile.wrapper.exists())
        self.profile.uninstall(True)
        self.assertFalse(self.profile.wrapper.exists())
        self.assertEqual(tela.read_bytes(), b"tela-owned-fixture")
        self.assertEqual(self.app.read_bytes(), original_app)

    def test_control_runtime_self_refresh_stages_before_replacing_running_source(self):
        metadata = self.home / "state/PluraDesktop"
        runtime = metadata / "control-runtime"
        package = runtime / "plura_desktop"
        package.mkdir(parents=True)
        entrypoint = runtime / "plura_desktop_cli.py"
        entrypoint.write_text("print('old entrypoint')\n", encoding="utf-8")
        (package / "__init__.py").write_text("VALUE = 'old'\n", encoding="utf-8")

        helper = self.platform.write_control_cli(metadata, entrypoint, package)

        self.assertTrue(helper.is_file())
        self.assertIn(sys.executable, helper.read_text(encoding="utf-8"))
        self.assertEqual((runtime / "plura_desktop_cli.py").read_text(encoding="utf-8"), "print('old entrypoint')\n")
        self.assertEqual((runtime / "plura_desktop/__init__.py").read_text(encoding="utf-8"), "VALUE = 'old'\n")
        self.assertEqual(list(metadata.glob(".control-runtime-*-*")), [])


    def test_frozen_runtime_accepts_validated_stable_absolute_path(self):
        standalone = self.home / "plura-desktop-standalone"
        standalone.write_text("standalone\n", encoding="utf-8")
        standalone.chmod(0o700)
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(standalone)),
            patch.dict(os.environ, {STABLE_FROZEN_RUNTIME_ENV: str(standalone)}),
        ):
            self.assertEqual(_frozen_runtime_executable(), standalone)

    def test_frozen_runtime_preserves_validated_stable_symlink(self):
        standalone = self.home / "Cellar/plura-desktop/0.1.8/bin/plura-desktop"
        standalone.parent.mkdir(parents=True)
        standalone.write_text("standalone\n", encoding="utf-8")
        standalone.chmod(0o700)
        stable = self.home / "opt/plura-desktop/bin/plura-desktop"
        stable.parent.mkdir(parents=True)
        try:
            stable.symlink_to(standalone)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable on this runner")
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(standalone)),
            patch.dict(os.environ, {STABLE_FROZEN_RUNTIME_ENV: str(stable)}),
        ):
            self.assertEqual(_frozen_runtime_executable(), stable)

    def test_validated_stable_frozen_path_is_written_to_selector_and_control_cli(self):
        standalone = self.home / "Cellar/plura-desktop/0.1.8/libexec/plura-desktop"
        standalone.parent.mkdir(parents=True)
        standalone.write_text("standalone\n", encoding="utf-8")
        standalone.chmod(0o700)
        stable = self.home / "opt/plura-desktop/libexec/plura-desktop"
        stable.parent.mkdir(parents=True)
        try:
            stable.symlink_to(standalone)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable on this runner")
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(standalone)),
            patch.dict(os.environ, {STABLE_FROZEN_RUNTIME_ENV: str(stable)}),
        ):
            self.profile.install()
        selector = (self.profile.wrapper / "launcher.txt").read_text(encoding="utf-8")
        helper = (self.profile.meta / "plura-desktop").read_text(encoding="utf-8")
        self.assertIn(f"runtime={stable}", selector)
        self.assertIn(str(stable), helper)
        self.assertIn(STABLE_FROZEN_RUNTIME_ENV, helper)
        self.assertNotIn(str(standalone), selector)
        self.assertNotIn(str(standalone), helper)
        self.profile.uninstall(True)

    def test_frozen_runtime_rejects_invalid_stable_override(self):
        standalone = self.home / "plura-desktop-standalone"
        standalone.write_text("standalone\n", encoding="utf-8")
        standalone.chmod(0o700)
        other = self.home / "other-runtime"
        other.write_text("other\n", encoding="utf-8")
        other.chmod(0o700)
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(standalone)),
            patch.dict(os.environ, {STABLE_FROZEN_RUNTIME_ENV: str(other)}),
            self.assertRaisesRegex(RuntimeError, "does not resolve to the current frozen executable"),
        ):
            _frozen_runtime_executable()


    def test_existing_state_is_never_adopted(self):
        self.profile.codex.mkdir()
        (self.profile.codex / "keep").write_text("keep", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.profile.install()
        self.assertEqual((self.profile.codex / "keep").read_text(encoding="utf-8"), "keep")

    def test_symlink_or_junction_path_is_refused_when_available(self):
        target = self.home / "target"
        target.mkdir()
        try:
            self.profile.codex.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable on this runner")
        with self.assertRaises(RuntimeError):
            self.profile.install()

    def test_manifest_cannot_redirect_uninstall_to_protected_state(self):
        self.profile.install()
        manifest = self.profile.load()
        manifest["created"][0]["path"] = str(self.home / ".codex")
        self.profile.save(manifest)
        with self.assertRaises(RuntimeError):
            self.profile.uninstall(True)
        self.assertTrue(self.profile.wrapper.exists())

    def test_replaced_managed_path_is_preserved(self):
        self.profile.install()
        preserved = self.home / "preserved-data"
        self.profile.data.rename(preserved)
        self.profile.data.mkdir()
        with self.assertRaises(RuntimeError):
            self.profile.uninstall(True)
        self.assertTrue(preserved.exists())

    def test_running_profile_allows_selector_refresh_but_blocks_destructive_uninstall(self):
        self.profile.install()
        self.platform.running_pids = [123]
        self.profile.refresh()
        with self.assertRaisesRegex(RuntimeError, "Quit ChatGPT Profile 2 first"):
            self.profile.uninstall(True)
        self.assertEqual(self.platform.running_pids, [123])

    def test_refresh_preserves_state_and_managed_path_identity(self):
        self.profile.install()
        identities = {path: self.platform.identity(path) for path in self.profile.paths}
        (self.profile.codex / "conversation-sentinel").write_text("keep", encoding="utf-8")
        (self.profile.data / "login-sentinel").write_text("keep", encoding="utf-8")
        (self.profile.wrapper / "launcher.txt").write_text("stale", encoding="utf-8")
        self.profile.refresh()
        self.assertEqual((self.profile.codex / "conversation-sentinel").read_text(), "keep")
        self.assertEqual((self.profile.data / "login-sentinel").read_text(), "keep")
        for path, before in identities.items():
            self.assertEqual(self.platform.identity(path), before)


    def test_updated_official_app_never_refreshes_a_running_managed_profile(self):
        self.profile.install()
        baseline = self.profile.load()["official_baseline"]
        self.app.write_bytes(b"updated official fixture")
        self.platform.running_pids = [123]

        with self.assertRaisesRegex(RuntimeError, "Quit ChatGPT Profile 2"):
            self.profile.prepare_launch()

        self.assertEqual(self.profile.load()["official_baseline"], baseline)

    def test_diagnostics_setting_is_manifest_state_not_background_work(self):
        self.profile.install()
        self.assertFalse(self.profile.tool_lifecycle_diagnostics_enabled(self.profile.load()))
        self.profile.set_tool_lifecycle_diagnostics(True)
        self.assertTrue(self.profile.tool_lifecycle_diagnostics_enabled(self.profile.load()))
        self.profile.set_tool_lifecycle_diagnostics(False)
        self.assertFalse(self.profile.tool_lifecycle_diagnostics_enabled(self.profile.load()))

    def test_uninstall_removes_only_the_selected_profile_diagnostic_artifacts(self):
        self.profile.install()
        selected = self.profile.meta / "diagnostics/profile-2/incidents/20260928T120000Z-aaaaaaaa"
        selected.mkdir(parents=True)
        (selected / "summary.json").write_text("{}\n", encoding="utf-8")
        other = self.profile.meta / "diagnostics/profile-3/incidents/20260928T120000Z-bbbbbbbb"
        other.mkdir(parents=True)
        (other / "summary.json").write_text("{}\n", encoding="utf-8")

        self.profile.uninstall(True)

        self.assertFalse((self.profile.meta / "diagnostics/profile-2").exists())
        self.assertTrue((self.profile.meta / "diagnostics/profile-3").is_dir())


    def test_uninstall_refuses_unexpected_profile_diagnostic_artifacts_before_deleting_state(self):
        self.profile.install()
        unexpected = self.profile.meta / "diagnostics/profile-2/unexpected.txt"
        unexpected.parent.mkdir(parents=True)
        unexpected.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unexpected artifacts"):
            self.profile.uninstall(True)
        self.assertEqual(unexpected.read_text(), "preserve")
        self.assertTrue(self.profile.wrapper.exists())
        self.assertTrue(self.profile.codex.exists())
        self.assertTrue(self.profile.data.exists())
        self.assertTrue(self.profile.manifest.exists())

    def test_documented_cli_lifecycle_smoke_from_not_installed_through_uninstall(self):
        with patch("plura_desktop.cli.current_platform", return_value=self.platform):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli_main(["status", "--profile", "2", "--json"]), 0)
            self.assertIn('"state": "not-installed"', output.getvalue())

            self.assertEqual(cli_main(["install", "--profile", "2"]), 0)
            self.assertTrue(self.profile.manifest.is_file())

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli_main(["targets", "--json"]), 0)
            self.assertIn("local.plura-desktop.profile2", output.getvalue())

            self.assertEqual(cli_main(["diagnostics-status", "--profile", "2"]), 0)
            self.assertEqual(cli_main(["refresh", "--profile", "2"]), 0)
            self.assertEqual(cli_main(["uninstall", "--profile", "2"]), 0)
            self.assertTrue(self.profile.manifest.is_file())
            self.assertEqual(cli_main(["uninstall", "--profile", "2", "--yes"]), 0)
            self.assertFalse(self.profile.manifest.exists())

    def test_public_target_contract_hides_platform_paths(self):
        self.profile.install()
        contract = public_targets(self.platform)
        self.assertEqual(contract["contractVersion"], 2)
        managed = next(target for target in contract["targets"] if target.get("managed"))
        rendered = json.dumps(managed)
        self.assertEqual(managed["backendPolicy"], "official-desktop-owned")
        self.assertNotIn("endpoint", managed)
        self.assertNotIn(str(self.home), rendered)
        self.assertNotIn("CODEX_HOME", rendered)


    def test_selected_target_rechecks_replaced_manifest(self):
        self.profile.install()
        self.assertEqual(_target(self.platform, self.profile.identifier)["state"], "stopped")
        manifest = json.loads(self.profile.manifest.read_text())
        manifest["id"] = "replaced"
        self.profile.manifest.write_text(json.dumps(manifest))
        self.assertEqual(_target(self.platform, self.profile.identifier)["state"], "invalid")
        with patch("test_safety.FakePlatform.launch") as start:
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                launch_target(self.platform, self.profile.identifier)
        start.assert_not_called()


    def test_updated_app_never_refreshes_a_running_profile(self):
        self.profile.install()
        baseline = self.profile.load()["official_baseline"]
        self.app.write_bytes(b"updated official fixture")
        self.platform.running_pids = [123]
        with self.assertRaisesRegex(RuntimeError, "Quit"):
            self.profile.prepare_launch()
        self.assertEqual(self.profile.load()["official_baseline"], baseline)


    def test_target_session_exposes_desktop_identity_for_noncanonical_running_target(self):
        self.profile.install()
        executable = self.platform.resolve_executable(self.profile.load()["app_executable"])
        self.platform.running_pids = [123]
        self.platform.desktop_process_ids[str(executable)] = 123
        value = target_session(self.platform, self.profile.identifier)
        self.assertEqual(value["state"], "running")
        self.assertEqual(value["desktopProcessID"], 123)
        self.assertNotIn("endpoint", value)


    def test_quit_target_requests_normal_quit_for_exact_desktop_identity(self):
        self.profile.install()
        executable = self.platform.resolve_executable(self.profile.load()["app_executable"])
        self.platform.desktop_process_ids[str(executable)] = 123
        ready = {
            "contractVersion": 1,
            "targetID": self.profile.identifier,
            "state": "ready",
            "endpoint": "ws://127.0.0.1:18762",
            "desktopProcessID": 123,
        }
        available = {
            "contractVersion": 1,
            "targetID": self.profile.identifier,
            "state": "stopped",
        }
        with patch("plura_desktop.manager.target_session", side_effect=[ready, available]):
            result = quit_target(self.platform, self.profile.identifier, expected_desktop_pid=123)
        self.assertEqual(result, available)
        self.assertEqual(self.platform.quit_requests, [(str(executable), 123)])


    def test_quit_target_is_idempotent_when_target_is_not_running(self):
        self.profile.install()
        available = {
            "contractVersion": 1,
            "targetID": self.profile.identifier,
            "state": "stopped",
        }
        with patch("plura_desktop.manager.target_session", return_value=available):
            self.assertEqual(quit_target(self.platform, self.profile.identifier), available)
        self.assertEqual(self.platform.quit_requests, [])

    def test_quit_target_refuses_changed_desktop_identity(self):
        self.profile.install()
        executable = self.platform.resolve_executable(self.profile.load()["app_executable"])
        self.platform.desktop_process_ids[str(executable)] = 456
        ready = {
            "contractVersion": 1,
            "targetID": self.profile.identifier,
            "state": "ready",
            "endpoint": "ws://127.0.0.1:18762",
            "desktopProcessID": 123,
        }
        with patch("plura_desktop.manager.target_session", return_value=ready):
            with self.assertRaisesRegex(RuntimeError, "ambiguous or changed"):
                quit_target(self.platform, self.profile.identifier)
        self.assertEqual(self.platform.quit_requests, [])

    def test_quit_target_refuses_replacement_after_confirmation(self):
        self.profile.install()
        executable = self.platform.resolve_executable(self.profile.load()["app_executable"])
        self.platform.desktop_process_ids[str(executable)] = 456
        ready = {"contractVersion": 1, "targetID": self.profile.identifier,
                 "state": "ready", "desktopProcessID": 456}
        with patch("plura_desktop.manager.target_session", return_value=ready):
            with self.assertRaisesRegex(RuntimeError, "changed since confirmation"):
                quit_target(self.platform, self.profile.identifier, expected_desktop_pid=123)
        self.assertEqual(self.platform.quit_requests, [])

    def test_quit_target_cli_emits_public_session_contract(self):
        session_value = {
            "contractVersion": 1,
            "targetID": self.profile.identifier,
            "state": "stopped",
        }
        output = io.StringIO()
        with patch(
            "plura_desktop.cli.current_platform", return_value=self.platform
        ), patch(
            "plura_desktop.cli.quit_target", return_value=session_value
        ) as quit_, redirect_stdout(output):
            result = cli_main([
                "quit-target",
                "--target",
                self.profile.identifier,
                "--json",
            ])
        self.assertEqual(result, 0)
        quit_.assert_called_once_with(self.platform, self.profile.identifier, expected_desktop_pid=None)
        self.assertEqual(json.loads(output.getvalue()), session_value)


    def test_profile_three_scales_without_fixed_two_profile_branch(self):
        profile = Profile(index=3, platform=self.platform)
        profile.install()
        self.assertEqual(profile.display_name, "ChatGPT Profile 3")
        self.assertIn("profile3", profile.identifier)

    def test_launch_uses_one_manifest_owned_profile_and_no_fallback(self):
        self.profile.install()
        self.profile.launch()
        self.assertTrue(self.platform.launched)
        self.assertEqual(self.profile.status_data()["ownership"], "official-desktop-profile")


class PlatformContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plura-desktop-platform-test-", dir=ROOT)
        self.home = Path(self.tmp.name)
        self.app = self.home / ("ChatGPT.exe" if os.name == "nt" else "ChatGPT")
        self.app.write_bytes(b"fixture")
        if os.name != "nt":
            self.app.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def test_linux_layout_uses_xdg_boundaries_and_explicit_executable(self):
        with patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.home / "xdg-data"),
                "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
                "XDG_STATE_HOME": str(self.home / "xdg-state"),
            },
            clear=False,
        ):
            platform = LinuxPlatform(home=self.home, app_override=self.app)
            layout = platform.layout(2)
            self.assertEqual(layout.selector, self.home / "xdg-data/applications/chatgpt-profile-2.desktop")
            self.assertEqual(layout.user_data, self.home / "xdg-config/Codex-Profile2")
            self.assertEqual(layout.metadata, self.home / "xdg-state/PluraDesktop")
            self.assertEqual(layout.identifier, "local.plura-desktop.profile2")
            self.assertEqual(platform.resolve_executable(), self.app)

    def test_linux_discovers_documented_chatgpt_command_on_path(self):
        executable = self.home / "bin/chatgpt"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        executable.chmod(0o755)
        platform = LinuxPlatform(home=self.home)
        with patch(
            "plura_desktop.platforms.linux.shutil.which",
            side_effect=lambda name: str(executable) if name == "chatgpt" else None,
        ):
            self.assertEqual(platform.resolve_executable(), executable.resolve())


    def test_linux_preserves_only_required_desktop_session_environment(self):
        with patch.dict(
            os.environ,
            {
                "DISPLAY": ":88",
                "WAYLAND_DISPLAY": "wayland-7",
                "XDG_RUNTIME_DIR": "/run/user/1234",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1234/bus",
                "XAUTHORITY": "/tmp/xauth",
                "XDG_SESSION_TYPE": "wayland",
                "XDG_CURRENT_DESKTOP": "GNOME",
                "PLURA_UNRELATED_SECRET": "do-not-copy",
            },
            clear=False,
        ):
            platform = LinuxPlatform(home=self.home, app_override=self.app)
            env = platform.sanitized_environment(platform.layout(2))
        self.assertEqual(env["DISPLAY"], ":88")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-7")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1234")
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1234/bus")
        self.assertEqual(env["XAUTHORITY"], "/tmp/xauth")
        self.assertEqual(env["XDG_SESSION_TYPE"], "wayland")
        self.assertEqual(env["XDG_CURRENT_DESKTOP"], "GNOME")
        self.assertNotIn("PLURA_UNRELATED_SECRET", env)

    def test_linux_refuses_macos_only_tool_lifecycle_diagnostics(self):
        with patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.home / "xdg-data"),
                "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
                "XDG_STATE_HOME": str(self.home / "xdg-state"),
            },
            clear=False,
        ):
            platform = LinuxPlatform(home=self.home, app_override=self.app)
            profile = Profile(index=2, platform=platform)
            profile.install()
            with self.assertRaisesRegex(RuntimeError, "unsupported on linux"):
                profile.set_tool_lifecycle_diagnostics(True)

    def test_observational_diagnostic_targets_do_not_leak_into_core_domain(self):
        core = (ROOT / "src/plura_desktop/domain.py").read_text(encoding="utf-8") + (
            ROOT / "src/plura_desktop/manager.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("codex_mcp::", core)
        self.assertNotIn("codex_core::tools::spec_plan", core)

    def test_windows_layout_uses_per_user_standard_boundaries(self):
        with patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.home / "Local"),
                "APPDATA": str(self.home / "Roaming"),
            },
            clear=False,
        ):
            platform = WindowsPlatform(home=self.home, app_override=self.app)
            layout = platform.layout(2)
            self.assertEqual(layout.user_data, self.home / "Local/Codex-Profile2")
            self.assertIn("Start Menu", str(layout.selector))
            self.assertEqual(platform.resolve_executable(), self.app)

    def test_linux_standalone_selector_preserves_stable_runtime_environment(self):
        standalone = self.home / "opt/plura-desktop/libexec/plura-desktop"
        standalone.parent.mkdir(parents=True)
        standalone.write_text("standalone\n", encoding="utf-8")
        standalone.chmod(0o755)
        with patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.home / "xdg-data"),
                "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
                "XDG_STATE_HOME": str(self.home / "xdg-state"),
            },
            clear=False,
        ):
            platform = LinuxPlatform(home=self.home, app_override=self.app)
            layout = platform.layout(2)
            platform.write_selector(
                layout,
                ROOT / "src/plura_desktop.py",
                ROOT / "src/plura_desktop",
                self.app,
                runtime_executable=standalone,
            )
        launcher = next(entry.path for entry in layout.managed if entry.role == "runtime") / "launch-profile"
        text = launcher.read_text(encoding="utf-8")
        self.assertIn(STABLE_FROZEN_RUNTIME_ENV, text)
        self.assertIn(str(standalone), text)
        self.assertIn("launch-target", text)
        self.assertIn("--target local.plura-desktop.profile2", text)
        self.assertNotIn("--renderer-cdp", text)
        self.assertNotIn(" launch --profile ", text)

    def test_windows_standalone_helpers_preserve_stable_runtime_environment(self):
        standalone = self.home / "opt/plura-desktop/plura-desktop.exe"
        standalone.parent.mkdir(parents=True)
        standalone.write_text("standalone\n", encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.home / "Local"),
                "APPDATA": str(self.home / "Roaming"),
            },
            clear=False,
        ):
            platform = WindowsPlatform(home=self.home, app_override=self.app)
            layout = platform.layout(2)
            platform.write_selector(
                layout,
                ROOT / "src/plura_desktop.py",
                ROOT / "src/plura_desktop",
                self.app,
                runtime_executable=standalone,
            )
            helper = platform.write_control_cli(
                layout.metadata,
                ROOT / "src/plura_desktop.py",
                ROOT / "src/plura_desktop",
                runtime_executable=standalone,
            )
        selector_text = layout.selector.read_text(encoding="utf-8")
        helper_text = helper.read_text(encoding="utf-8")
        expected = f'set "{STABLE_FROZEN_RUNTIME_ENV}={standalone}"'
        self.assertIn(expected, selector_text)
        self.assertIn(expected, helper_text)


    def test_noncanonical_manifest_schema_is_refused(self):
        platform = FakePlatform(self.home, self.app)
        profile = Profile(index=2, platform=platform)
        profile.install()
        manifest = profile.load()
        manifest["schema"] = MANIFEST_SCHEMA - 1
        profile.save(manifest)
        with self.assertRaisesRegex(RuntimeError, "Unsupported install manifest schema"):
            profile.load()


    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_selector_self_refresh_stages_before_replacing_its_runtime_source(self):
        fake_bundle = self.home / "ChatGPT.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        executable.chmod(0o755)
        resources = fake_bundle / "Contents/Resources"
        resources.mkdir(parents=True)
        (resources / "electron.icns").write_bytes(b"icon")
        (resources / "app.asar").write_bytes(b"asar-fixture")
        with (fake_bundle / "Contents/Info.plist").open("wb") as file:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, file)

        platform = MacOSPlatform(home=self.home, app_override=executable)
        profile = Profile(index=2, platform=platform)
        profile.install()
        selector_identity = platform.identity(profile.wrapper)
        old_contents_inode = (profile.wrapper / "Contents").stat().st_ino
        copied_entrypoint = profile.wrapper / "Contents/Resources/plura_desktop_cli.py"
        copied_package = profile.wrapper / "Contents/Resources/plura_desktop"

        # A selector launched from its copied Python runtime refreshes using sources inside its
        # own current Contents tree. The replacement must be complete before that tree is swapped.
        platform.write_selector(
            profile.layout,
            copied_entrypoint,
            copied_package,
            executable,
        )

        self.assertEqual(platform.identity(profile.wrapper), selector_identity)
        self.assertNotEqual((profile.wrapper / "Contents").stat().st_ino, old_contents_inode)
        self.assertTrue((profile.wrapper / "Contents/MacOS/launcher").is_file())
        self.assertTrue((profile.wrapper / "Contents/Resources/plura_desktop_cli.py").is_file())
        self.assertTrue((profile.wrapper / "Contents/Resources/plura_desktop/__init__.py").is_file())
        self.assertEqual(list(profile.wrapper.parent.glob(f".{profile.wrapper.name}.next-*")), [])

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_selector_build_failure_preserves_previous_complete_bundle(self):
        fake_bundle = self.home / "ChatGPT.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        executable.chmod(0o755)
        resources = fake_bundle / "Contents/Resources"
        resources.mkdir(parents=True)
        (resources / "electron.icns").write_bytes(b"icon")
        (resources / "app.asar").write_bytes(b"asar-fixture")
        with (fake_bundle / "Contents/Info.plist").open("wb") as file:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, file)

        platform = MacOSPlatform(home=self.home, app_override=executable)
        profile = Profile(index=2, platform=platform)
        profile.install()
        launcher = profile.wrapper / "Contents/MacOS/launcher"
        info = profile.wrapper / "Contents/Info.plist"
        selector_identity = platform.identity(profile.wrapper)
        contents_identity = platform.identity(profile.wrapper / "Contents")
        before_launcher = launcher.read_bytes()
        before_info = info.read_bytes()

        with patch(
            "plura_desktop.platforms.macos.shutil.copy2",
            side_effect=OSError("injected selector build failure"),
        ):
            with self.assertRaisesRegex(OSError, "injected selector build failure"):
                platform.write_selector(
                    profile.layout,
                    SRC / "plura_desktop/_runtime_entrypoint.py",
                    SRC / "plura_desktop",
                    executable,
                )

        self.assertEqual(platform.identity(profile.wrapper), selector_identity)
        self.assertEqual(platform.identity(profile.wrapper / "Contents"), contents_identity)
        self.assertEqual(launcher.read_bytes(), before_launcher)
        self.assertEqual(info.read_bytes(), before_info)
        self.assertEqual(list(profile.wrapper.parent.glob(f".{profile.wrapper.name}.next-*")), [])

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_selector_exchange_failure_preserves_previous_complete_bundle(self):
        fake_bundle = self.home / "ChatGPT.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        executable.chmod(0o755)
        resources = fake_bundle / "Contents/Resources"
        resources.mkdir(parents=True)
        (resources / "electron.icns").write_bytes(b"icon")
        (resources / "app.asar").write_bytes(b"asar-fixture")
        with (fake_bundle / "Contents/Info.plist").open("wb") as file:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, file)

        platform = MacOSPlatform(home=self.home, app_override=executable)
        profile = Profile(index=2, platform=platform)
        profile.install()
        launcher = profile.wrapper / "Contents/MacOS/launcher"
        info = profile.wrapper / "Contents/Info.plist"
        contents_identity = platform.identity(profile.wrapper / "Contents")
        before_launcher = launcher.read_bytes()
        before_info = info.read_bytes()

        with patch.object(platform, "_exchange_paths", side_effect=OSError("injected exchange failure")):
            with self.assertRaisesRegex(OSError, "injected exchange failure"):
                platform.write_selector(
                    profile.layout,
                    SRC / "plura_desktop/_runtime_entrypoint.py",
                    SRC / "plura_desktop",
                    executable,
                )

        self.assertEqual(platform.identity(profile.wrapper / "Contents"), contents_identity)
        self.assertEqual(launcher.read_bytes(), before_launcher)
        self.assertEqual(info.read_bytes(), before_info)
        self.assertEqual(list(profile.wrapper.parent.glob(f".{profile.wrapper.name}.next-*")), [])

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_standalone_runtime_selector_does_not_copy_python_package(self):
        fake_bundle = self.home / "ChatGPT-Standalone.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        executable.chmod(0o755)
        icon = fake_bundle / "Contents/Resources/electron.icns"
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b"icon")
        (fake_bundle / "Contents/Resources/app.asar").write_bytes(b"asar-fixture")
        with (fake_bundle / "Contents/Info.plist").open("wb") as file:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, file)

        standalone = self.home / "plura-desktop-standalone"
        standalone.write_text("standalone\n", encoding="utf-8")
        standalone.chmod(0o700)
        platform = MacOSPlatform(home=self.home, app_override=executable)
        profile = Profile(index=2, platform=platform)
        with patch("plura_desktop.manager._frozen_runtime_executable", return_value=standalone):
            profile.install()
            launcher = (profile.wrapper / "Contents/MacOS/launcher").read_text(encoding="utf-8")
            self.assertIn(str(standalone), launcher)
            self.assertIn(STABLE_FROZEN_RUNTIME_ENV, launcher)
            self.assertNotIn("plura_desktop_cli.py", launcher)
            resources = profile.wrapper / "Contents/Resources"
            self.assertFalse((resources / "plura_desktop_cli.py").exists())
            self.assertFalse((resources / "plura_desktop").exists())
            self.assertFalse((profile.meta / "control-runtime").exists())
            self.assertIn(str(standalone), (profile.meta / "plura-desktop").read_text(encoding="utf-8"))
            profile.uninstall(True)

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_refresh_replaces_stale_runtime_only_after_profile_quits(self):
        fake_bundle = self.home / "ChatGPT.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture-v1")
        executable.chmod(0o755)
        resources = fake_bundle / "Contents/Resources"
        resources.mkdir(parents=True)
        (resources / "electron.icns").write_bytes(b"icon")
        (resources / "app.asar").write_bytes(b"asar-v1")
        with (fake_bundle / "Contents/Info.plist").open("wb") as file:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, file)
        platform = MacOSPlatform(home=self.home, app_override=executable)
        profile = Profile(index=2, platform=platform)
        profile.install()
        runtime_executable = profile.meta / "runtime-apps/profile-2/ChatGPT.app/Contents/MacOS/ChatGPT"
        self.assertEqual(runtime_executable.read_bytes(), b"fixture-v1")

        executable.write_bytes(b"fixture-v2")
        (resources / "app.asar").write_bytes(b"asar-v2")
        with patch.object(platform, "running", return_value=[123]):
            with self.assertRaisesRegex(RuntimeError, "Quit ChatGPT Profile 2"):
                profile.refresh()
        self.assertEqual(runtime_executable.read_bytes(), b"fixture-v1")

        with patch.object(platform, "running", return_value=[]):
            profile.refresh()
        self.assertEqual(runtime_executable.read_bytes(), b"fixture-v2")
        self.assertEqual(
            platform.profile_executable(
                profile.layout,
                executable,
                source_fingerprint=profile.load()["official_baseline"],
            ),
            runtime_executable,
        )

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_managed_runtime_mutation_is_discarded_in_favor_of_official_app(self):
        fake_bundle = self.home / "ChatGPT.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"official-fixture")
        executable.chmod(0o755)
        resources = fake_bundle / "Contents/Resources"
        resources.mkdir(parents=True)
        (resources / "electron.icns").write_bytes(b"icon")
        (resources / "app.asar").write_bytes(b"official-asar")
        with (fake_bundle / "Contents/Info.plist").open("wb") as file:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, file)

        platform = MacOSPlatform(home=self.home, app_override=executable)
        profile = Profile(index=2, platform=platform)
        profile.install()
        profile_state_identities = {
            "codex": platform.identity(profile.codex),
            "data": platform.identity(profile.data),
        }
        official_before = {
            relative: (fake_bundle / relative).read_bytes()
            for relative in (
                Path("Contents/Info.plist"),
                Path("Contents/MacOS/ChatGPT"),
                Path("Contents/Resources/app.asar"),
            )
        }
        runtime_bundle = profile.meta / "runtime-apps/profile-2/ChatGPT.app"
        runtime_executable = runtime_bundle / "Contents/MacOS/ChatGPT"
        runtime_executable.write_bytes(b"managed-self-update")

        prepared = profile.prepare_launch()

        self.assertEqual(prepared, runtime_executable)
        self.assertEqual(runtime_executable.read_bytes(), b"official-fixture")
        self.assertEqual(platform.identity(profile.codex), profile_state_identities["codex"])
        self.assertEqual(platform.identity(profile.data), profile_state_identities["data"])
        for relative, before in official_before.items():
            self.assertEqual((fake_bundle / relative).read_bytes(), before)

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_stale_managed_runtime_path_remains_addressable_for_normal_quit(self):
        fake_bundle = self.home / "ChatGPT.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"official-v1")
        executable.chmod(0o755)
        resources = fake_bundle / "Contents/Resources"
        resources.mkdir(parents=True)
        (resources / "electron.icns").write_bytes(b"icon")
        (resources / "app.asar").write_bytes(b"asar-v1")
        with (fake_bundle / "Contents/Info.plist").open("wb") as file:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, file)

        platform = MacOSPlatform(home=self.home, app_override=executable)
        profile = Profile(index=2, platform=platform)
        profile.install()
        runtime_executable = profile.meta / "runtime-apps/profile-2/ChatGPT.app/Contents/MacOS/ChatGPT"

        executable.write_bytes(b"official-v2")
        (resources / "app.asar").write_bytes(b"asar-v2")

        self.assertEqual(
            platform.profile_process_executable(profile.layout, executable),
            runtime_executable,
        )
        with self.assertRaisesRegex(RuntimeError, "missing or stale"):
            platform.profile_executable(
                profile.layout,
                executable,
                source_fingerprint=profile.load()["official_baseline"],
            )

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_managed_sparkle_runtime_suppresses_only_automatic_update_checks(self):
        fake_bundle = self.home / "ChatGPT.app"
        executable = fake_bundle / "Contents/MacOS/ChatGPT"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        executable.chmod(0o755)
        (fake_bundle / "Contents/Frameworks/Sparkle.framework").mkdir(parents=True)
        platform = MacOSPlatform(home=self.home, app_override=executable)
        layout = platform.layout(2)

        command = platform.process_command(executable, layout)

        self.assertIn("-SUEnableAutomaticChecks", command)
        self.assertIn("-SUAutomaticallyUpdate", command)
        self.assertEqual(command[command.index("-SUEnableAutomaticChecks") + 1], "NO")
        self.assertEqual(command[command.index("-SUAutomaticallyUpdate") + 1], "NO")

        no_sparkle_bundle = self.home / "ChatGPT-NoSparkle.app"
        no_sparkle_executable = no_sparkle_bundle / "Contents/MacOS/ChatGPT"
        no_sparkle_executable.parent.mkdir(parents=True)
        no_sparkle_executable.write_bytes(b"fixture")
        no_sparkle_executable.chmod(0o755)
        command_without_sparkle = platform.process_command(no_sparkle_executable, layout)
        self.assertNotIn("-SUEnableAutomaticChecks", command_without_sparkle)
        self.assertNotIn("-SUAutomaticallyUpdate", command_without_sparkle)

    @unittest.skipUnless(sys.platform == "darwin", "macOS adapter integration test")
    def test_macos_running_default_matches_finder_app_and_excludes_wrong_profile_and_helpers(self):
        executable = self.home / "ChatGPT.app/Contents/MacOS/ChatGPT"
        platform = MacOSPlatform(home=self.home, app_override=executable)
        managed = platform.layout(2)
        managed_executable = platform._profile_runtime_executable(managed)
        rows = "\n".join((
            f"101 {executable}",
            f"202 {executable}",
            f"303 {executable}",
            f"404 {managed_executable}",
            f"505 {executable} Helper",
        ))
        settings = {
            101: ([], managed.codex_home),  # Official executable started from profile 2.
            202: ([], None),  # Finder launch has no explicit profile arguments.
            303: ([managed.user_data], None),
            404: ([managed.user_data], managed.codex_home),
        }
        with patch("plura_desktop.platforms.macos.subprocess.check_output", return_value=rows), patch.object(
            platform, "_desktop_settings", side_effect=lambda pid, executable: settings[pid],
        ):
            self.assertEqual(platform.running_default(), [202])
            self.assertEqual(platform.desktop_process_id(executable, user_data=platform.default_user_data()), 202)
            self.assertEqual(platform.running(managed), [404])
            self.assertEqual(platform.desktop_process_id(
                managed_executable, user_data=managed.user_data, codex_home=managed.codex_home,
            ), 404)


    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux adapter integration test")
    def test_linux_adapter_installs_xdg_desktop_entry_with_owned_runtime(self):
        with patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.home / "xdg-data"),
                "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
                "XDG_STATE_HOME": str(self.home / "xdg-state"),
            },
            clear=False,
        ):
            platform = LinuxPlatform(home=self.home, app_override=self.app)
            profile = Profile(index=2, platform=platform)
            profile.install()
            selector = profile.layout.selector.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", selector)
            runtime = next(entry.path for entry in profile.layout.managed if entry.role == "runtime")
            self.assertTrue((runtime / "launch-profile").is_file())
            launcher = (runtime / "launch-profile").read_text(encoding="utf-8")
            self.assertIn("launch-target", launcher)
            self.assertIn("--target local.plura-desktop.profile2", launcher)
            self.assertNotIn("--renderer-cdp", launcher)
            self.assertTrue((profile.meta / "plura-desktop").is_file())
            self.assertEqual(profile.load()["platform"], "linux")
            profile.uninstall(True)

    @unittest.skipUnless(sys.platform == "win32", "Windows adapter integration test")
    def test_windows_adapter_installs_start_menu_command_with_owned_runtime(self):
        with patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.home / "Local"),
                "APPDATA": str(self.home / "Roaming"),
            },
            clear=False,
        ):
            platform = WindowsPlatform(home=self.home, app_override=self.app)
            profile = Profile(index=2, platform=platform)
            profile.install()
            self.assertIn("plura_desktop_cli.py", profile.layout.selector.read_text(encoding="utf-8"))
            self.assertTrue((profile.meta / "plura-desktop.cmd").is_file())
            self.assertEqual(profile.load()["platform"], "windows")
            with patch.object(platform, "running", return_value=[]):
                profile.uninstall(True)


class MacOSQuitTests(unittest.TestCase):
    def test_normal_quit_targets_only_the_proved_desktop_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "ChatGPT.app/Contents/MacOS/ChatGPT"
            platform = MacOSPlatform(home=Path(directory), app_override=executable)
            with patch.object(platform, "desktop_process_id", return_value=301), patch(
                "plura_desktop.platforms.macos.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "normal_quit_requested\n", ""),
            ) as run:
                platform.request_desktop_quit(executable, 301)
            args = run.call_args.args[0]
            self.assertEqual(args[:4], ["/usr/bin/osascript", "-l", "JavaScript", "-e"])
            self.assertIn("runningApplicationWithProcessIdentifier(301)", args[4])
            self.assertIn("stringByResolvingSymlinksInPath", args[4])
            self.assertNotIn("tell application", args[4])


if __name__ == "__main__":
    unittest.main()
