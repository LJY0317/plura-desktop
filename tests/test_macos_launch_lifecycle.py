"""LaunchServices identity/lifecycle regressions; no real application is launched."""
from itertools import chain, repeat
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from test_safety import FakePlatform
from plura_desktop.manager import supervise_target
from plura_desktop.platforms.macos import MacOSPlatform


class MacOSLaunchLifecycleTests(unittest.TestCase):
    def test_default_and_managed_launch_preserve_isolated_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            executable = home / "ChatGPT.app/Contents/MacOS/ChatGPT"
            platform = MacOSPlatform(home=home, app_override=executable)
            for index in (None, 2):
                with (self.subTest(index=index), patch("os.chdir"), patch("os.execve") as execute,
                      patch.object(platform, "_current_username", return_value="fixture-user")):
                    if index is None:
                        platform.launch_default(executable, app_server_url="ws://127.0.0.1:12345")
                        expected_home = home / ".codex"
                    else:
                        layout = platform.layout(index)
                        platform.launch(layout, executable, rust_log=None,
                                        app_server_url="ws://127.0.0.1:12345")
                        expected_home = layout.codex_home
                    path, argv, env = execute.call_args.args
                    self.assertEqual(path, "/usr/bin/open")
                    self.assertEqual(argv[1:3], ["-W", "-n"])
                    self.assertEqual(argv[argv.index("-a"):argv.index("--args") + 1],
                                     ["-a", str(executable.parents[2]), "--args"])
                    forwarded = dict(argv[i + 1].split("=", 1) for i, arg in enumerate(argv) if arg == "--env")
                    self.assertEqual(forwarded, {key: env[key] for key in
                        ("CODEX_HOME", "CODEX_ELECTRON_USER_DATA_PATH", "CODEX_APP_SERVER_WS_URL")})
                    self.assertNotIn("HOME", forwarded)
                    self.assertEqual(env["CODEX_HOME"], str(expected_home))
                    self.assertEqual(env["CODEX_APP_SERVER_WS_URL"], "ws://127.0.0.1:12345")
                    self.assertIn("--user-data-dir=" + env["CODEX_ELECTRON_USER_DATA_PATH"], argv)
                    self.assertNotIn("CODEX_EXECUTABLE", env)

    def test_direct_spec_has_same_os_lifecycle_and_no_launch_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            executable = home / "ChatGPT.app/Contents/MacOS/ChatGPT"
            platform = MacOSPlatform(home=home, app_override=executable)
            with (patch("os.execve") as execute, patch("os.chdir") as change,
                  patch.object(platform, "_current_username", return_value="fixture-user")):
                spec = platform.desktop_launch_spec(executable, layout=platform.layout(2),
                    app_server_url="ws://127.0.0.1:12345", rust_log="info")
            execute.assert_not_called()
            change.assert_not_called()
            self.assertEqual(spec.command[:3], ("/usr/bin/open", "-W", "-n"))
            forwarded = dict(spec.command[i + 1].split("=", 1)
                             for i, arg in enumerate(spec.command) if arg == "--env")
            self.assertEqual(forwarded, {key: spec.environment[key] for key in
                ("CODEX_HOME", "CODEX_ELECTRON_USER_DATA_PATH", "CODEX_APP_SERVER_WS_URL", "RUST_LOG")})
            self.assertNotIn("PATH", forwarded)
            self.assertEqual(spec.cwd, home)
            self.assertEqual(spec.environment["CODEX_HOME"], str(platform.layout(2).codex_home))
            self.assertEqual(spec.environment["RUST_LOG"], "info")
            self.assertNotIn("environment", repr(spec))

    def test_restored_clone_cannot_claim_another_profile_or_steal_supervisor_identity(self):
        from plura_desktop.manager import _load_target_session
        from plura_desktop.runtime import TargetSession
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            platform = MacOSPlatform(home=home)
            executable = home / "runtime.app/Contents/MacOS/ChatGPT"
            layout = platform.layout(2)
            rows = f"686 1 {executable}\n7498 1 {executable} --user-data-dir={layout.user_data}\n"
            with patch("plura_desktop.platforms.macos.subprocess.check_output", return_value=rows):
                self.assertIsNone(platform.desktop_process_id(executable))
                self.assertEqual(platform.desktop_process_id(executable, user_data=layout.user_data), 7498)
                self.assertEqual(platform.supervised_desktop_pid(executable, Mock(poll=lambda: None),
                    deadline=time.monotonic() + 1, user_data=layout.user_data), 7498)
                wrong = TargetSession("managed", "ws://127.0.0.1:12345", 1, 2, 686)
                correct = TargetSession("managed", "ws://127.0.0.1:12345", 1, 2, 7498)
                for candidate, expected in [(wrong, None), (correct, correct)]:
                    with patch("plura_desktop.manager.load_session", return_value=candidate):
                        self.assertEqual(_load_target_session(platform, home, {"id": "managed", "profileIndex": 2}), expected)

    def test_launcher_pid_is_not_published_as_desktop_identity(self):
        platform = MacOSPlatform(home=Path(tempfile.gettempdir()))
        launcher = Mock(pid=11)
        launcher.poll.return_value = None
        with patch.object(platform, "desktop_process_id", side_effect=[None, 22]), patch("time.sleep"):
            self.assertEqual(platform.supervised_desktop_pid(Path("fixture"), launcher,
                             deadline=time.monotonic() + 1), 22)

    def test_launcher_failure_and_missing_identity_fail_closed(self):
        platform = MacOSPlatform(home=Path(tempfile.gettempdir()))
        launcher = Mock(pid=11)
        launcher.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "launcher exited"):
            platform.supervised_desktop_pid(Path("fixture"), launcher, deadline=time.monotonic() + 1)
        with self.assertRaisesRegex(RuntimeError, "identity was not established"):
            platform.supervised_desktop_pid(Path("fixture"), launcher, deadline=0)

    def test_supervisor_binds_actual_app_pid_and_quits_it_on_backend_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            app = home / "ChatGPT"
            app.write_bytes(b"fixture")
            platform = FakePlatform(home, app)
            platform.desktop_process_ids[str(app)] = 303
            backend = Mock(pid=101, returncode=0)
            backend.poll.return_value = 0
            desktop = Mock(pid=202, returncode=None)
            desktop.poll.return_value = None
            captured = []
            def terminate(process):
                process.poll.return_value = 0
                process.returncode = 0
            # Backend must first be live for startup readiness, then exit.
            backend.poll.side_effect = chain([None], repeat(0))
            with patch("plura_desktop.manager.subprocess.Popen", side_effect=[backend, desktop]), \
                 patch("plura_desktop.manager.endpoint_ready", return_value=True), \
                 patch.object(platform, "supervised_desktop_pid", return_value=303), \
                 patch("plura_desktop.manager.atomic_write_session", side_effect=lambda _p, s: captured.append(s)), \
                 patch("plura_desktop.manager._terminate_owned", side_effect=terminate):
                supervise_target(platform, "default")
            self.assertEqual(captured[0].desktop_pid, 303)
            self.assertEqual(platform.quit_requests, [(str(app), 303)])
            desktop.wait.assert_called_once_with(timeout=5)


@unittest.skipUnless(
    sys.platform == "darwin" and os.environ.get("PLURA_MACOS_LS_E2E") == "1",
    "Opt-in graphical macOS AppKit fixture; never launches a real user app",
)
class ActualLaunchServicesIdentityTests(unittest.TestCase):
    def test_same_bundle_id_quits_only_the_proved_pid_and_preserves_peer(self):
        """A and B share the same bundle ID but must never share quit authority."""
        with tempfile.TemporaryDirectory(prefix="plura-two-appkit-fixtures-") as temp:
            root = Path(temp).resolve()
            source = root / "Fixture.m"
            source.write_text(
                '#import <AppKit/AppKit.h>\n'
                '@interface Delegate : NSObject<NSApplicationDelegate> @end\n'
                '@implementation Delegate\n'
                '-(void)applicationDidFinishLaunching:(NSNotification*)n '
                '{ [NSApp performSelector:@selector(terminate:) withObject:nil afterDelay:8.0]; }\n'
                '-(NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication*)app '
                '{ return NSTerminateNow; }\n'
                '@end\n'
                'int main(void){@autoreleasepool{[NSApplication sharedApplication];'
                'Delegate *d=[Delegate new];[NSApp setDelegate:d];'
                '[NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];'
                '[NSApp run];}return 0;}\n',
                encoding="utf-8",
            )
            adapters = []
            executables = []
            waiters = []
            try:
                for number in (1, 2):
                    bundle = root / f"ExactPidFixture{number}.app"
                    executable = bundle / "Contents/MacOS/ChatGPT"
                    executable.parent.mkdir(parents=True)
                    with (bundle / "Contents/Info.plist").open("wb") as stream:
                        plistlib.dump({
                            "CFBundleIdentifier": "local.plura.e2e.same-bundle-id",
                            "CFBundleExecutable": "ChatGPT",
                            "CFBundlePackageType": "APPL",
                            "CFBundleName": f"ExactPidFixture{number}",
                            "LSUIElement": True,
                        }, stream)
                    subprocess.run(
                        ["/usr/bin/clang", "-fobjc-arc", "-framework", "AppKit",
                         "-o", str(executable), str(source)],
                        check=True, capture_output=True, timeout=20,
                    )
                    executables.append(executable)
                    adapters.append(MacOSPlatform(home=root, app_override=executable))
                    waiters.append(subprocess.Popen(
                        ["/usr/bin/open", "-n", "-W", "-a", str(bundle)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    ))
                pids = [None, None]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    pids = [
                        adapter.desktop_process_id(executable)
                        for adapter, executable in zip(adapters, executables)
                    ]
                    if all(pids):
                        break
                    time.sleep(0.1)
                self.assertTrue(all(pids))
                self.assertNotEqual(*pids)
                self.assertTrue(set(pids).isdisjoint({p.pid for p in waiters}))
                adapters[1].request_desktop_quit(executables[1], pids[1])
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    if adapters[1].desktop_process_id(executables[1]) is None:
                        break
                    time.sleep(0.1)
                self.assertIsNone(adapters[1].desktop_process_id(executables[1]))
                self.assertEqual(adapters[0].desktop_process_id(executables[0]), pids[0])
                adapters[0].request_desktop_quit(executables[0], pids[0])
            finally:
                for adapter, executable in zip(adapters, executables):
                    # Only temporary fixture executables are ever addressed.
                    pid = adapter.desktop_process_id(executable)
                    if pid is not None:
                        try:
                            adapter.request_desktop_quit(executable, pid)
                        except (OSError, RuntimeError):
                            pass
                for waiter in waiters:
                    try:
                        waiter.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        # The fixture self-terminates after 8s; never signal
                        # a real ChatGPT or a connected DevSpace process.
                        waiter.terminate()
                        waiter.wait(timeout=3)
