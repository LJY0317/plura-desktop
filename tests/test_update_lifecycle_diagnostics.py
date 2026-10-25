"""Update and launcher integrity regression tests.

Only disposable fake ChatGPT bundles and synthetic managed profiles are used.
Neither the official app nor user login/conversations/Remote are accessed.
"""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from plura_desktop.manager import Profile, launch_target, supervise_target
from plura_desktop.diagnostic_artifacts import write_diagnostic_artifact
from plura_desktop.platforms.macos import MacOSPlatform
from test_safety import FakePlatform


def _incident_payloads(metadata: Path, profile: int = 2) -> list[dict]:
    incidents = metadata / "diagnostics" / f"profile-{profile}" / "incidents"
    if not incidents.is_dir():
        return []
    return [
        json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        for folder in sorted(incidents.iterdir())
    ]


class FakeProfileUpdateReceipts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="plura-update-log-test-")
        self.home = Path(self.tmp.name)
        (self.home / "state").mkdir()
        (self.home / "data").mkdir()
        self.app = self.home / "fake-chatgpt"
        self.app.write_bytes(b"fake-bundle-v1")
        self.platform = FakePlatform(self.home, self.app)
        self.profile = Profile(index=2, platform=self.platform)
        with redirect_stdout(io.StringIO()):
            self.profile.install()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_normal_unchanged_launch_and_status_do_not_emit_incident(self) -> None:
        self.assertEqual(self.profile.status_data()["selectorIntegrity"], "unknown")
        self.assertEqual(self.profile.status_data()["managedRuntimeIntegrity"], "unknown")
        self.profile.prepare_launch()
        self.assertEqual([], _incident_payloads(self.profile.meta))

    def test_failed_selector_write_records_stage_without_sensitive_exception_text(self) -> None:
        secret = "PRIVATE_PERSONAL_ACCOUNT_TOKEN_DO_NOT_RECORD"
        original = (self.profile.wrapper / "launcher.txt").read_bytes()
        with patch.object(self.platform, "write_selector", side_effect=OSError(secret)):
            with self.assertRaisesRegex(OSError, secret):
                self.profile.refresh()
        self.assertEqual(original, (self.profile.wrapper / "launcher.txt").read_bytes())
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(1, len(records))
        item = records[0]["updateLifecycle"]
        self.assertEqual("failed", item["outcome"])
        self.assertEqual("refresh", item["operation"])
        self.assertEqual("selector_staging", item["failedPhase"])
        self.assertEqual("OSError", item["errorClass"])
        self.assertNotIn(secret, json.dumps(records))
        self.assertNotIn(str(self.home), json.dumps(records))
        self.assertLess(len(json.dumps(records[0]).encode("utf-8")), 16 * 1024)
        with redirect_stdout(io.StringIO()) as output:
            self.profile.diagnostics_status()
        self.assertIn("Latest update incident: failed refresh at selector_staging", output.getvalue())
        self.assertNotIn(secret, output.getvalue())

    def test_changed_official_app_has_single_reconciliation_receipt(self) -> None:
        self.app.write_bytes(b"fake-bundle-v2")
        self.profile.prepare_launch()
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(1, len(records))
        item = records[0]["updateLifecycle"]
        self.assertEqual("reconciled", item["outcome"])
        self.assertTrue(item["flags"]["officialSourceChanged"])
        self.assertEqual("prepare_launch", item["operation"])
        self.profile.prepare_launch()
        self.assertEqual(1, len(_incident_payloads(self.profile.meta)))

    def test_running_profile_does_not_get_updated_and_records_safe_failure(self) -> None:
        old_baseline = self.profile.load()["official_baseline"]
        self.app.write_bytes(b"fake-bundle-v2")
        self.platform.running_pids = [123]
        with self.assertRaisesRegex(RuntimeError, "Quit ChatGPT Profile 2"):
            self.profile.prepare_launch()
        self.assertEqual(old_baseline, self.profile.load()["official_baseline"])
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(1, len(records))
        item = records[0]["updateLifecycle"]
        self.assertEqual("managed_process_check", item["failedPhase"])
        self.assertTrue(item["flags"].get("officialSourceChanged"))
        self.assertTrue(item["flags"]["officialSourceChanged"])

    def test_launch_child_failure_is_recorded_and_not_concealed(self) -> None:
        with patch(
            "plura_desktop.manager._start_detached",
            side_effect=OSError("PRIVATE_OWNED_SOCKET_PATH"),
        ):
            with self.assertRaisesRegex(OSError, "PRIVATE_OWNED_SOCKET_PATH"):
                launch_target(self.platform, self.profile.identifier)
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(1, len(records))
        item = records[0]["updateLifecycle"]
        self.assertEqual("launch_runtime", item["operation"])
        self.assertEqual("runtime_spawn", item["failedPhase"])
        self.assertNotIn("PRIVATE_OWNED_SOCKET_PATH", json.dumps(records))

    def test_canonical_supervisor_gui_identity_failure_is_safe_and_classified(self) -> None:
        """Never log backend args, account state or private errors on spawn failure."""
        backend = Mock(pid=301)
        backend.poll.return_value = None
        desktop = Mock(pid=302)
        desktop.poll.return_value = None
        private_error = "SECRET_PRIVATE_APP_SERVER_CREDENTIAL_MUST_NOT_APPEAR"
        with patch("plura_desktop.manager.subprocess.Popen", side_effect=[backend, desktop]), patch(
            "plura_desktop.manager.endpoint_ready", return_value=True,
        ), patch.object(
            self.platform, "supervised_desktop_pid", side_effect=RuntimeError(private_error),
        ), patch("plura_desktop.manager._terminate_owned"):
            with self.assertRaisesRegex(RuntimeError, private_error):
                supervise_target(self.platform, self.profile.identifier)
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(1, len(records))
        item = records[0]["updateLifecycle"]
        self.assertEqual("launch_supervisor", item["operation"])
        self.assertEqual("supervisor_gui_identity", item["failedPhase"])
        self.assertEqual("RuntimeError", item["errorClass"])
        self.assertNotIn(private_error, json.dumps(records))
        self.assertNotIn(str(self.home), json.dumps(records))

    def test_control_runtime_failure_has_distinct_phase_and_never_leaks_message(self) -> None:
        with patch.object(self.platform, "write_control_cli", side_effect=OSError("private control path")):
            with self.assertRaises(OSError):
                self.profile.refresh()
        item = _incident_payloads(self.profile.meta)[0]["updateLifecycle"]
        self.assertEqual("control_runtime_prepare", item["failedPhase"])
        self.assertNotIn("private control path", json.dumps(item))

    def test_incidents_have_bounded_retention_without_raw_tracebacks(self) -> None:
        with patch.object(self.platform, "write_selector", side_effect=RuntimeError("private")):
            for _ in range(13):
                with self.assertRaises(RuntimeError):
                    self.profile.refresh()
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(13, len(records))
        self.assertTrue(all(x["updateLifecycle"]["outcome"] == "failed" for x in records))
        self.assertNotIn("private", json.dumps(records))

    def test_diagnostic_writer_failure_cannot_mask_original_failure(self) -> None:
        with patch.object(self.platform, "write_selector", side_effect=RuntimeError("original failure")):
            with patch(
                "plura_desktop.lifecycle_diagnostics.write_diagnostic_artifact",
                side_effect=PermissionError("incident tree is unsafe"),
            ):
                with self.assertRaisesRegex(RuntimeError, "original failure"):
                    self.profile.refresh()
        self.assertEqual([], _incident_payloads(self.profile.meta))

    def test_uninstalled_profile_cannot_create_orphan_update_artifacts(self) -> None:
        self.profile.manifest.unlink()
        with self.assertRaises(RuntimeError):
            self.profile.refresh()
        self.assertEqual([], _incident_payloads(self.profile.meta))

    def test_diagnostics_status_never_prints_untrusted_incident_fields(self) -> None:
        forbidden = "CONFIDENTIAL_CHATGPT_CONTENT_MUST_NEVER_BE_PRINTED"
        write_diagnostic_artifact(
            self.profile.meta,
            2,
            {
                "updateLifecycle": {
                    "kind": "managed_profile_update_lifecycle",
                    "operation": "refresh",
                    "outcome": "failed",
                    "failedPhase": "unexpected-user-data-field",
                    "rawPrivateContent": forbidden,
                }
            },
        )
        with redirect_stdout(io.StringIO()) as output:
            self.profile.diagnostics_status()
        self.assertIn("none or unavailable", output.getvalue())
        self.assertNotIn(forbidden, output.getvalue())


@unittest.skipUnless(sys.platform == "darwin", "macOS selector bundle integration")
class MacSelectorIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="plura-selector-health-test-")
        self.home = Path(self.tmp.name)
        fake_bundle = self.home / "ChatGPT.app"
        self.executable = fake_bundle / "Contents/MacOS/ChatGPT"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"official-fixture")
        self.executable.chmod(0o755)
        resources = fake_bundle / "Contents/Resources"
        resources.mkdir()
        (resources / "electron.icns").write_bytes(b"synthetic-icon")
        (resources / "app.asar").write_bytes(b"synthetic-asar")
        with (fake_bundle / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleIconFile": "electron.icns"}, stream)
        self.platform = MacOSPlatform(home=self.home, app_override=self.executable)
        self.profile = Profile(index=2, platform=self.platform)
        with redirect_stdout(io.StringIO()):
            self.profile.install()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_proper_selector_is_verified_and_stopped_state_is_ready(self) -> None:
        data = self.profile.status_data()
        self.assertEqual("ready", data["selectorIntegrity"])
        self.assertEqual("ready", data["managedRuntimeIntegrity"])
        self.assertEqual("stopped", data["state"])
        self.assertEqual("official-default-app-only", data["chatgptUpdateAuthority"])
        self.assertEqual("may-remain-visible-not-supported", data["manualUpdaterMenu"])
        with redirect_stdout(io.StringIO()) as output:
            self.profile.status()
        self.assertIn("Selector integrity: ready", output.getvalue())
        self.assertIn("ChatGPT update authority: official Profile 1 only", output.getvalue())

    def test_update_guard_denies_second_writer_and_records_safe_lock_conflict(self) -> None:
        outer_identity = self.platform.identity(self.profile.wrapper)
        codex_identity = self.platform.identity(self.profile.codex)
        data_identity = self.platform.identity(self.profile.data)
        with self.platform.update_guard(self.profile.layout):
            with self.assertRaisesRegex(RuntimeError, "Another Plura profile refresh"):
                self.profile.refresh()
            with self.assertRaisesRegex(RuntimeError, "Another Plura profile refresh"):
                self.profile.prepare_launch()
        self.assertEqual(outer_identity, self.platform.identity(self.profile.wrapper))
        self.assertEqual(codex_identity, self.platform.identity(self.profile.codex))
        self.assertEqual(data_identity, self.platform.identity(self.profile.data))
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(2, len(records))
        self.assertTrue(all(record["updateLifecycle"]["failedPhase"] == "update_lock" for record in records))
        self.assertTrue(all(record["updateLifecycle"]["errorClass"] == "RuntimeError" for record in records))
        self.assertTrue(all(record["updateLifecycle"]["outcome"] == "failed" for record in records))
        # The metadata directory is the advisory lock: no persistent .lock
        # file is created to confuse profile ownership/uninstall.
        self.assertFalse(any(entry.name.endswith(".lock") for entry in self.profile.meta.iterdir()))

    def test_empty_contents_shell_is_detected_then_repaired_at_cold_launch(self) -> None:
        selector_identity = self.platform.identity(self.profile.wrapper)
        codex_id = self.platform.identity(self.profile.codex)
        data_id = self.platform.identity(self.profile.data)
        (self.profile.codex / "conversation-sentinel").write_text("preserved")
        (self.profile.data / "login-sentinel").write_text("preserved")
        info = self.profile.wrapper / "Contents/Info.plist"
        launcher = self.profile.wrapper / "Contents/MacOS/launcher"
        icon = self.profile.wrapper / "Contents/Resources/icon.icns"
        for path in (info, launcher, icon):
            path.unlink()
        self.assertEqual("incomplete", self.profile.status_data()["selectorIntegrity"])
        with redirect_stdout(io.StringIO()) as output:
            self.profile.status()
        self.assertIn("Recovery: plura-desktop refresh", output.getvalue())

        prepared = self.profile.prepare_launch()
        self.assertTrue(prepared.is_file())
        self.assertEqual("ready", self.profile.status_data()["selectorIntegrity"])
        self.assertEqual(selector_identity, self.platform.identity(self.profile.wrapper))
        self.assertEqual(codex_id, self.platform.identity(self.profile.codex))
        self.assertEqual(data_id, self.platform.identity(self.profile.data))
        self.assertEqual((self.profile.codex / "conversation-sentinel").read_text(), "preserved")
        self.assertEqual((self.profile.data / "login-sentinel").read_text(), "preserved")
        records = _incident_payloads(self.profile.meta)
        self.assertEqual(1, len(records))
        item = records[0]["updateLifecycle"]
        self.assertEqual("reconciled", item["outcome"])
        self.assertEqual("incomplete", item["before"]["selector"])
        self.assertEqual("ready", item["after"]["selector"])

    def test_cold_launch_refuses_to_repair_active_profile(self) -> None:
        info = self.profile.wrapper / "Contents/Info.plist"
        info.unlink()
        with patch.object(self.platform, "running", return_value=[123]):
            with self.assertRaisesRegex(RuntimeError, "Quit ChatGPT Profile 2"):
                self.profile.prepare_launch()
        self.assertFalse(info.exists())
        self.assertEqual("incomplete", self.profile.status_data()["selectorIntegrity"])
        self.assertEqual("failed", _incident_payloads(self.profile.meta)[0]["updateLifecycle"]["outcome"])

    def test_symlinked_plist_is_unsafe_and_not_repaired(self) -> None:
        target = self.home / "private-external-data"
        target.write_text("never inspect")
        info = self.profile.wrapper / "Contents/Info.plist"
        info.unlink()
        info.symlink_to(target)
        self.assertEqual("unsafe", self.profile.status_data()["selectorIntegrity"])
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            self.profile.prepare_launch()
        self.assertTrue(info.is_symlink())
        self.assertEqual(target.read_text(), "never inspect")

    def test_runtime_symlink_is_unsafe_and_not_rewritten(self) -> None:
        resources = (
            self.profile.meta / "runtime-apps/profile-2/ChatGPT.app/"
            "Contents/Resources"
        )
        import shutil
        backup = self.home / "untrusted-external-resource"
        shutil.copytree(resources, backup)
        shutil.rmtree(resources)
        resources.symlink_to(backup, target_is_directory=True)
        self.assertEqual("unsafe", self.profile.status_data()["managedRuntimeIntegrity"])
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            self.profile.prepare_launch()
        self.assertTrue(resources.is_symlink())
        self.assertTrue((backup / "app.asar").is_file())

    def test_missing_runtime_component_rebuilt_on_cold_launch(self) -> None:
        runtime_asar = (
            self.profile.meta / "runtime-apps/profile-2/ChatGPT.app/"
            "Contents/Resources/app.asar"
        )
        runtime_asar.unlink()
        self.assertEqual("incomplete", self.profile.status_data()["managedRuntimeIntegrity"])
        self.profile.prepare_launch()
        self.assertEqual("ready", self.profile.status_data()["managedRuntimeIntegrity"])
        events = _incident_payloads(self.profile.meta)
        self.assertEqual("reconciled", events[0]["updateLifecycle"]["outcome"])
        self.assertTrue(events[0]["updateLifecycle"]["flags"]["managedRuntimeRebuilt"])

    def test_managed_clone_mutation_is_replaced_and_diagnosed(self) -> None:
        runtime_executable = (
            self.profile.meta / "runtime-apps/profile-2/ChatGPT.app/"
            "Contents/MacOS/ChatGPT"
        )
        runtime_executable.write_bytes(b"invalid-self-updated-binary")
        self.assertNotEqual(runtime_executable.read_bytes(), self.executable.read_bytes())
        self.profile.prepare_launch()
        self.assertEqual(runtime_executable.read_bytes(), self.executable.read_bytes())
        reports = _incident_payloads(self.profile.meta)
        self.assertEqual(1, len(reports))
        item = reports[0]["updateLifecycle"]
        self.assertEqual("reconciled", item["outcome"])
        self.assertTrue(item["flags"]["managedRuntimeRebuilt"])
        self.assertFalse(item["flags"]["officialSourceChanged"])

    def test_selector_exchange_failure_is_recorded_but_original_stays_complete(self) -> None:
        launcher = self.profile.wrapper / "Contents/MacOS/launcher"
        old_launcher = launcher.read_bytes()
        with patch.object(
            self.platform, "_exchange_paths",
            side_effect=OSError("PRIVATE_STAGING_SWAP_PROBLEM"),
        ):
            with self.assertRaises(OSError):
                self.profile.refresh()
        self.assertEqual(old_launcher, launcher.read_bytes())
        self.assertEqual("ready", self.profile.status_data()["selectorIntegrity"])
        item = _incident_payloads(self.profile.meta)[0]["updateLifecycle"]
        self.assertEqual("selector_staging", item["failedPhase"])
        self.assertEqual("failed", item["outcome"])
        self.assertNotIn("PRIVATE_STAGING_SWAP_PROBLEM", json.dumps(item))


if __name__ == "__main__":
    unittest.main()
