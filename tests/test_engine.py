import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plura_desktop.engine import codex_engine_override
from plura_desktop.manager import launch_target
from plura_desktop.platforms.macos import MacOSPlatform
from plura_desktop.runtime import TargetSession


class EngineSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="plura-engine-")
        self.root = Path(self.temporary.name)
        self.engine = self.root / "codex"
        self.engine.write_bytes(b"fixture engine")
        self.engine.chmod(0o755)
        self.digest = hashlib.sha256(self.engine.read_bytes()).hexdigest()

    def tearDown(self):
        self.temporary.cleanup()

    def test_explicit_hash_is_checked_and_missing_override_never_falls_back(self):
        with patch.dict(os.environ, {"CODEX_EXECUTABLE": str(self.engine), "CODEX_EXECUTABLE_SHA256": self.digest}, clear=True):
            self.assertEqual(codex_engine_override(), (self.engine.resolve(), self.digest))
            self.engine.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                codex_engine_override()
        with patch.dict(os.environ, {"CODEX_EXECUTABLE_SHA256": self.digest}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "explicit executable"):
                codex_engine_override()

    def test_mac_explicit_selection_precedes_the_official_bundle(self):
        app = self.root / "ChatGPT.app" / "Contents" / "MacOS" / "ChatGPT"
        app.parent.mkdir(parents=True)
        app.write_bytes(b"official app")
        bundled = app.parents[1] / "Resources" / "codex"
        bundled.parent.mkdir()
        bundled.write_bytes(b"official engine")
        bundled.chmod(0o755)
        platform = MacOSPlatform(home=self.root)
        with patch.dict(os.environ, {"CODEX_EXECUTABLE": str(self.engine), "CODEX_EXECUTABLE_SHA256": self.digest}, clear=True):
            self.assertEqual(platform.resolve_codex_executable(app), self.engine.resolve())
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(platform.resolve_codex_executable(app), bundled)
        self.assertEqual(bundled.read_bytes(), b"official engine")

    def test_live_engine_mismatch_requires_normal_quit_without_stopping_anything(self):
        platform = MacOSPlatform(home=self.root)
        official = TargetSession("default", "ws://127.0.0.1:12345", 1, 2, 3)
        selected = TargetSession("default", "ws://127.0.0.1:12345", 1, 2, 3, engine_override_sha256=self.digest)
        with patch("plura_desktop.manager._target", return_value={"id": "default"}), patch(
            "plura_desktop.manager._metadata", return_value=self.root
        ), patch("plura_desktop.manager._start_detached") as start:
            with patch.dict(os.environ, {"CODEX_EXECUTABLE": str(self.engine), "CODEX_EXECUTABLE_SHA256": self.digest}, clear=True):
                with patch("plura_desktop.manager.load_session", return_value=official):
                    with self.assertRaisesRegex(RuntimeError, "different Codex engine"):
                        launch_target(platform, "default")
                with patch("plura_desktop.manager.load_session", return_value=selected):
                    self.assertEqual(launch_target(platform, "default"), selected)
            with patch.dict(os.environ, {}, clear=True), patch("plura_desktop.manager.load_session", return_value=selected):
                with self.assertRaisesRegex(RuntimeError, "different Codex engine"):
                    launch_target(platform, "default")
            start.assert_not_called()
