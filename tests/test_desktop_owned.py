"""Native ownership and profile isolation; all processes and homes are synthetic."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_safety import FakePlatform
from plura_desktop.cli import build_parser, main
from plura_desktop.manager import Profile, launch_target, target_session
from plura_desktop.platforms.linux import LinuxPlatform
from plura_desktop.platforms.macos import MacOSPlatform
from plura_desktop.platforms.windows import WindowsPlatform


class DesktopOwnedTests(unittest.TestCase):
    def test_launch_preserves_profile_state_and_does_not_create_an_external_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "ChatGPT"
            app.write_text("fixture")
            platform = FakePlatform(home, app)
            profile = Profile(index=2, platform=platform)
            with redirect_stdout(io.StringIO()):
                profile.install()
            config = profile.layout.codex_home / "config.toml"
            config.write_text('model_provider = "fixture-provider"\n')
            before = config.read_bytes()
            with patch("subprocess.Popen") as child:
                result = launch_target(platform, profile.identifier)
            child.assert_not_called()
            self.assertTrue(platform.launched)
            self.assertEqual(result["contractVersion"], 2)
            self.assertEqual(result["state"], "launch-requested")
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse((profile.meta / "runtime-sessions").exists())
            self.assertNotIn("endpoint", result)

    def test_existing_app_is_reused_without_claiming_native_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "ChatGPT"
            app.touch()
            platform = FakePlatform(home, app)
            platform.default_running_pids = [123]
            platform.desktop_process_ids[str(platform.resolve_executable())] = 123
            with patch.object(platform, "launch_default") as launch, patch.object(
                platform, "request_desktop_activation",
            ) as activate:
                result = launch_target(platform, "default")
                activate.assert_not_called()
                self.assertEqual(launch_target(platform, "default", activate=True), result)
            launch.assert_not_called()
            activate.assert_called_once_with(platform.resolve_executable(), 123)
            self.assertEqual(result["state"], "running")
            self.assertEqual(result["desktopProcessID"], 123)
            self.assertNotIn("nativeReady", result)

    def test_only_the_interactive_mac_shortcut_reports_the_correct_profile_error(self):
        with tempfile.TemporaryDirectory() as directory:
            platform = MacOSPlatform(home=Path(directory))
            profile = platform.layout(3)
            failure = RuntimeError("fixture launch failed")
            command = ["launch-target", "--target", profile.identifier]
            with patch("plura_desktop.cli.current_platform", return_value=platform), patch(
                "plura_desktop.cli.launch_target", side_effect=failure,
            ), patch.object(platform, "show_launch_error") as show_error, redirect_stderr(io.StringIO()):
                self.assertEqual(main([*command, "--json"]), 1)
                show_error.assert_not_called()
                self.assertEqual(main([*command, "--profile", "3", "--activate"]), 1)
                show_error.assert_called_once_with(profile, failure)

    def test_selected_profile_does_not_inspect_default_or_other_profile_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            app = home / "ChatGPT"
            app.touch()
            platform = FakePlatform(home, app)
            profile = Profile(index=2, platform=platform)
            with redirect_stdout(io.StringIO()):
                profile.install()
            with patch.object(platform, "running_default", side_effect=AssertionError("unrelated profile")):
                self.assertEqual(target_session(platform, profile.identifier)["state"], "stopped")

    def test_launches_only_official_desktop_with_isolated_home_on_all_adapters(self):
        inherited = {"CODEX_APP_SERVER_WS_URL": "ws://127.0.0.1:19999",
                     "CODEX_EXECUTABLE": "/fixture/foreign-engine",
                     "CODEX_EXECUTABLE_SHA256": "a" * 64}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, inherited):
            home = Path(directory)
            app = home / "ChatGPT.app/Contents/MacOS/ChatGPT"
            for adapter in (MacOSPlatform, LinuxPlatform, WindowsPlatform):
                platform = adapter(home=home, app_override=app)
                for index in (None, 2, 3):
                    with self.subTest(adapter=platform.platform_id, profile=index):
                        layout = platform.layout(index) if index else None
                        with patch("subprocess.Popen") as launch:
                            if layout:
                                platform.launch(layout, app, rust_log=None)
                            else:
                                platform.launch_default(app)
                        args, kwargs = launch.call_args
                        command = args[0]
                        env = kwargs["env"]
                        expected_home = layout.codex_home if layout else platform.default_codex_home()
                        self.assertEqual(env["CODEX_HOME"], str(expected_home))
                        self.assertFalse(set(inherited) & set(env))
                        self.assertNotIn("--listen", command)
                        self.assertNotIn("app-server", command)
                        self.assertNotIn("--remote-debugging-port", " ".join(command))
                        self.assertNotIn("/fixture/foreign-engine", " ".join(command))
                        if platform.platform_id == "macos":
                            self.assertEqual(command[:3], ("/usr/bin/open", "-W", "-n"))
                            self.assertIn(f"CODEX_HOME={expected_home}", command)
                        else:
                            self.assertEqual(command[0], str(app))

    def test_retired_routing_and_supervisor_flags_are_not_silently_accepted(self):
        for argv in (("_supervise-target",), ("launch-target", "--renderer-cdp"),
                     ("launch-target", "--responses-base-url", "http://127.0.0.1:1234/v1")):
            with self.subTest(argv=argv), patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit):
                    build_parser().parse_args(argv)
