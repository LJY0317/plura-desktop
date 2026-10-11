"""Offline tests for macOS profile-local conversation metadata diagnostics.

All paths are isolated inside TemporaryDirectory. Real ChatGPT/Codex profiles
are neither read nor modified by this test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from uuid import uuid4


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/diagnose_local_conversation_isolation.py"
spec = importlib.util.spec_from_file_location("conversation_isolation", SCRIPT)
assert spec is not None and spec.loader is not None
isolation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isolation)


class LocalConversationIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="plura-isolation-synthetic-")
        self.home = Path(self.tmp.name)
        for left, right, kind in isolation.PAIR_PATHS.values():
            for rel in (left, right):
                p = self.home / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if kind == "directory":
                    p.mkdir(exist_ok=True)
                else:
                    p.write_bytes(b"synthetic-metadata-only")
        for base in (".codex", ".codex-profile2"):
            (self.home / base / "installation_id").write_text(str(uuid4()))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_isolated_profile_stores_are_separate_without_reading_database(self) -> None:
        self.assertEqual(0, isolation.main(["--home", str(self.home)]))
        a = isolation.audit(self.home)
        self.assertEqual("separate", a["codex_storage"])
        self.assertEqual("separate", a["desktop_browser_storage"])
        self.assertEqual("distinct_valid_ids", a["installation_ids"])
        self.assertEqual(0, a["codex_rollouts"]["same_inode_files"])

    def test_shared_codex_db_hardlink_is_reported_not_silently_accepted(self) -> None:
        source = self.home / ".codex/thread_history_1.sqlite"
        dest = self.home / ".codex-profile2/thread_history_1.sqlite"
        dest.unlink()
        dest.hardlink_to(source)
        a = isolation.audit(self.home)
        self.assertEqual("shared_or_linked", a["codex_storage"])
        self.assertEqual("shared_physical_storage", a["path_checks"]["codex_paginated_index"]["status"])

    def test_work_browser_storage_symlink_does_not_get_followed(self) -> None:
        source = self.home / "Library/Application Support/Codex/Default/IndexedDB"
        dest = self.home / "Library/Application Support/Codex-Profile2/Default/IndexedDB"
        dest.rmdir()
        dest.symlink_to(source, target_is_directory=True)
        a = isolation.audit(self.home)
        self.assertEqual("shared_or_linked", a["desktop_browser_storage"])
        self.assertEqual("symlink", a["path_checks"]["desktop_indexeddb"]["profile2_kind"])

    def test_selected_rollout_hardlink_is_not_falsely_called_isolated(self) -> None:
        source = self.home / ".codex/sessions/rollout-synthetic.jsonl"
        source.write_text("test fixture only")
        dest = self.home / ".codex-profile2/sessions/rollout-synthetic.jsonl"
        dest.hardlink_to(source)
        a = isolation.audit(self.home)
        self.assertEqual("shared_or_linked", a["codex_storage"])
        self.assertEqual(1, a["codex_rollouts"]["same_inode_files"])

    def test_copied_rollout_name_is_not_equated_with_physical_storage(self) -> None:
        for base in (".codex", ".codex-profile2"):
            (self.home / base / "sessions/rollout-synthetic.jsonl").write_text("fixture")
        a = isolation.audit(self.home)
        self.assertEqual("separate", a["codex_storage"])
        self.assertEqual(1, a["codex_rollouts"]["same_named_files"])
        self.assertEqual(0, a["codex_rollouts"]["same_inode_files"])

    def test_missing_store_is_incomplete_not_assumed_safe(self) -> None:
        (self.home / ".codex-profile2/state_5.sqlite").unlink()
        a = isolation.audit(self.home)
        self.assertEqual("incomplete", a["codex_storage"])
        self.assertEqual("incomplete", a["path_checks"]["codex_state_db"]["status"])

    def test_same_installation_identity_is_not_reported_in_raw_form(self) -> None:
        p1 = self.home / ".codex/installation_id"
        p2 = self.home / ".codex-profile2/installation_id"
        p2.write_text(p1.read_text())
        a = isolation.audit(self.home)
        self.assertEqual("same_id", a["installation_ids"])
        self.assertNotIn(p1.read_text().strip(), str(a))

    def test_symlinked_rollout_directory_is_skipped_and_flagged(self) -> None:
        external = self.home / "private-other-sessions"
        external.mkdir()
        (external / "rollout-secret.jsonl").write_text("never opened")
        (self.home / ".codex/sessions/linked").symlink_to(
            external, target_is_directory=True,
        )
        a = isolation.audit(self.home)
        self.assertEqual("shared_or_linked", a["codex_storage"])
        self.assertEqual(1, a["codex_rollouts"]["symlink_entries_total"])
        self.assertEqual(0, a["codex_rollouts"]["profile1_count"])

    def test_codex_home_symlink_does_not_traverse_profile_tree(self) -> None:
        original = self.home / ".codex-profile2"
        shutil.rmtree(original)
        original.symlink_to(self.home / ".codex", target_is_directory=True)
        a = isolation.audit(self.home)
        self.assertEqual("shared_or_linked", a["codex_storage"])
        self.assertEqual("unsafe_ancestor", a["path_checks"]["codex_sessions"]["profile2_kind"])
        self.assertEqual("unverified", a["installation_ids"])
        self.assertEqual("not_available", a["codex_rollouts"]["profile2_scan"])


if __name__ == "__main__":
    unittest.main()
