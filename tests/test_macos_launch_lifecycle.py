"""LaunchServices identity/lifecycle regressions; no real application is launched."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest

from plura_desktop.platforms.macos import MacOSPlatform


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
                    environment = os.environ.copy()
                    environment.pop("CODEX_HOME", None)
                    environment.pop("CODEX_ELECTRON_USER_DATA_PATH", None)
                    waiters.append(subprocess.Popen(
                        ["/usr/bin/open", "-n", "-W", "-a", str(bundle)],
                        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    ))
                pids = [None, None]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    pids = [
                        adapter.desktop_process_id(executable, user_data=adapter.default_user_data())
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
