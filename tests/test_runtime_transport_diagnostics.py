from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plura_desktop.diagnostic_artifacts import write_runtime_diagnostic_artifact, validate_runtime_diagnostic_tree
from plura_desktop.runtime import descriptor_path, route_key


class RuntimeDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        (self.root / "runtime-sessions").mkdir()
        self.descriptor = descriptor_path(self.root, "default")
        self.descriptor.write_text(json.dumps({"targetID": "default", "supervisorPID": 123}))

    def test_default_owner_without_managed_manifest_bounded_retention_and_cleanup_validation(self):
        for _ in range(12):
            write_runtime_diagnostic_artifact(self.root, "default", 123, {"reason": "message_limit"})
        tree = validate_runtime_diagnostic_tree(self.root)
        files = list(tree.glob("*/incidents/*/summary.json"))
        self.assertEqual(len(files), 10)
        self.assertEqual(json.loads(files[0].read_text())["artifact"]["targetHash"], route_key("default"))
        if os.name != "nt":
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        (tree / "unexpected").write_text("preserve")
        with self.assertRaises(RuntimeError):
            validate_runtime_diagnostic_tree(self.root)
        self.assertTrue((tree / "unexpected").exists())

    def test_changed_owner_and_oversized_evidence_do_not_write(self):
        with self.assertRaises(RuntimeError):
            write_runtime_diagnostic_artifact(self.root, "default", 456, {})
        with self.assertRaises(RuntimeError):
            write_runtime_diagnostic_artifact(self.root, "default", 123, {"large": "x" * 262144})
        self.assertFalse((self.root / "diagnostics").exists())

    @unittest.skipIf(os.name == "nt", "symlink privileges vary")
    def test_symlink_namespace_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "diagnostics").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            write_runtime_diagnostic_artifact(self.root, "default", 123, {})
        self.assertEqual(list(outside.iterdir()), [])
