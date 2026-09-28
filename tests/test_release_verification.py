"""Offline tests for the public release-verification helper."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify-release.py"
SPEC = importlib.util.spec_from_file_location("plura_release_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseVerificationTests(unittest.TestCase):
    def test_parse_checksums_and_verify_downloaded_asset_set(self):
        with tempfile.TemporaryDirectory(prefix="plura-verify-test-", dir=ROOT) as temporary:
            root = Path(temporary)
            asset = root / "artifact.bin"
            asset.write_bytes(b"plura")
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            (root / "SHA256SUMS").write_text(f"{digest}  artifact.bin\n", encoding="utf-8")
            checksums = MODULE.parse_checksums(root / "SHA256SUMS")
            self.assertEqual(checksums, {"artifact.bin": digest})
            MODULE.verify_downloaded_assets(root, checksums)

    def test_parse_checksums_rejects_unsafe_or_duplicate_asset_names(self):
        digest = "a" * 64
        with tempfile.TemporaryDirectory(prefix="plura-verify-test-", dir=ROOT) as temporary:
            path = Path(temporary) / "SHA256SUMS"
            for content in (
                f"{digest}  ../escape\n",
                f"{digest}  asset\n{digest}  asset\n",
            ):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(RuntimeError):
                        MODULE.parse_checksums(path)

    def test_validate_metadata_binds_repository_tag_and_source_commit(self):
        commit = "b" * 40
        with tempfile.TemporaryDirectory(prefix="plura-verify-test-", dir=ROOT) as temporary:
            metadata = Path(temporary) / "RELEASE-METADATA.json"
            metadata.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "repository": "example/plura-desktop",
                        "sourceTag": "v1.2.3",
                        "sourceCommit": commit,
                    }
                ),
                encoding="utf-8",
            )
            MODULE.validate_metadata(
                metadata,
                repository="example/plura-desktop",
                tag="v1.2.3",
                resolved_commit=commit,
            )
            with self.assertRaises(RuntimeError):
                MODULE.validate_metadata(
                    metadata,
                    repository="other/repo",
                    tag="v1.2.3",
                    resolved_commit=commit,
                )


if __name__ == "__main__":
    unittest.main()
