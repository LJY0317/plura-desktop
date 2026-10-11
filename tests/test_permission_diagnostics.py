from __future__ import annotations
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plura_desktop.permission_diagnostics import PermissionDiagnostics, permission_resume_summary

class PermissionDiagnosticsTests(unittest.TestCase):
    def test_resume_report_uses_latest_snapshot_and_sanitizes_persisted_values(self):
        snapshots = []
        recorder = PermissionDiagnostics(snapshots.append, limit=1)
        message = '{"id":1,"method":"thread/resume","params":{}}'
        recorder.observe(message, message)
        recorder.observe('{"id":1,"result":{"approvalPolicy":"never","sandbox":{"type":"dangerFullAccess"},"activePermissionProfile":{"id":":danger-full-access"}}}')
        recorder.observe('{"id":2,"method":"turn/start","params":{}}')
        report = "\n".join(permission_resume_summary(snapshots))
        self.assertIn("override: no", report)
        self.assertIn("changed: no", report)
        self.assertIn("Native approval: never", report)
        self.assertIn("Collection limit reached", report)
        snapshots[-1]["events"][0]["native"]["approval"] = "private-secret"
        self.assertNotIn("private-secret", "\n".join(permission_resume_summary(snapshots)))
        self.assertIn("not recorded", permission_resume_summary([])[0])
        snapshots[-1]["events"][0]["response"] = "error"
        report = "\n".join(permission_resume_summary(snapshots))
        self.assertIn("Response: error", report)
        self.assertNotIn("Native approval", report)

    def test_choices_and_native_response_without_payload_or_identity(self):
        snapshots = []
        recorder = PermissionDiagnostics(snapshots.append)
        source = {"id": "secret-request", "method": "thread/resume", "params": {"threadId": "private-thread", "sandbox": "danger-full-access", "approvalPolicy": "never", "cwd": "/private/path", "input": "private-prompt", "config": {"default_permissions": "private-custom-profile", "sandbox_workspace_write": {"writable_roots": ["/secret/root"]}}}}
        forwarded = json.loads(json.dumps(source))
        forwarded["params"].pop("sandbox")
        forwarded["params"]["permissions"] = ":danger-full-access"
        recorder.observe(json.dumps(source), json.dumps(forwarded), experimental=True)
        recorder.observe(json.dumps({"id": "secret-request", "result": {"sandbox": {"type": "workspaceWrite", "writableRoots": ["/secret/root"]}, "approvalPolicy": "on-request", "activePermissionProfile": {"id": ":workspace"}, "thread": {"id": "private-thread", "preview": "private-prompt"}}}))
        event = snapshots[-1]["events"][0]
        self.assertEqual(event["received"]["sandbox"], "danger-full-access")
        self.assertEqual(event["forwarded"]["permissions"], ":danger-full-access")
        self.assertEqual(event["native"]["activeProfile"], ":workspace")
        self.assertEqual(event["native"]["approval"], "on-request")
        for secret in ("private-thread", "secret-request", "/private/path", "private-prompt", "/secret/root", "private-custom-profile"):
            self.assertNotIn(secret, json.dumps(snapshots))
        self.assertIsNotNone(event["threadHash"])
        self.assertNotIn("native", snapshots[0]["events"][0])

    def test_bounded_and_failures_cannot_block_forwarding(self):
        snapshots = []
        recorder = PermissionDiagnostics(snapshots.append, limit=2)
        for n in range(20):
            recorder.observe(json.dumps({"id": n, "method": "turn/start", "params": {}}))
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(snapshots[-1]["truncated"])
        self.assertEqual(len(snapshots[-1]["events"]), 2)
        calls = []
        def fail(value):
            calls.append(value)
            raise OSError("private path")
        broken = PermissionDiagnostics(fail)
        for _ in range(10):
            broken.observe('{"id":1,"method":"thread/start","params":{}}')
        self.assertEqual(len(calls), 1)

    def test_unrelated_malformed_and_unknown_fields_not_retained(self):
        snapshots = []
        recorder = PermissionDiagnostics(snapshots.append)
        for text in ('not json', '[]', '{"method":{"secret":"value"}}', '{"method":"exec","params":{"command":"secret"}}', '{"id":true,"result":{"secret":"value"}}'):
            recorder.observe(text)
        self.assertEqual(snapshots, [])
        recorder.observe('{"method":"thread/settings/updated","params":{"threadId":"x","threadSettings":{"permissions":"private-name","approvalPolicy":{"secret":"value"}}}}')
        event = snapshots[-1]["events"][0]
        self.assertEqual(event["received"]["permissions"], "custom")
        self.assertNotIn("private-name", json.dumps(snapshots))

if __name__ == "__main__":
    unittest.main()
