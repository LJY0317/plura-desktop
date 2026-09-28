"""Release/distribution tests that do not require GitHub or a real installer run."""

from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts/render-macos-release-installer.py"
TEMPLATE = ROOT / "scripts/install-plura-desktop-macos.sh"
RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
PYPI_WORKFLOW = ROOT / ".github/workflows/publish-pypi.yml"
WORKFLOW_DIR = ROOT / ".github/workflows"


class ReleaseDistributionTests(unittest.TestCase):
    def test_release_workflow_keeps_non_notarized_dmg_as_prerelease(self):
        source = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("! -name '*-macOS-signed.dmg'", source)
        self.assertIn("! -name '*-macOS-unsigned.dmg'", source)
        self.assertIn("--prerelease", source)
        self.assertIn("--latest", source)
        self.assertNotIn("--latest=false", source)
        self.assertNotIn("--prerelease=false", source)

    def test_release_workflow_is_immutable_and_attests_final_assets(self):
        source = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("workflow_dispatch:", source)
        self.assertIn("PLURA_RELEASE_TAG:", source)
        self.assertIn("ref: ${{ env.PLURA_RELEASE_TAG }}", source)
        self.assertIn("published releases are immutable", source)
        self.assertNotIn("--clobber", source)
        self.assertIn("--draft", source)
        self.assertIn('gh release upload "$PLURA_RELEASE_TAG" ./*', source)
        self.assertIn('gh release edit "$PLURA_RELEASE_TAG" --draft=false', source)
        self.assertEqual(source.count("gh release edit"), 1)
        self.assertIn("cleanup_draft", source)
        self.assertRegex(source, r"actions/attest@[0-9a-f]{40} # v4")
        self.assertIn("subject-checksums: release/SHA256SUMS", source)
        self.assertIn("attestations: write", source)
        self.assertIn("id-token: write", source)
        self.assertRegex(source, r"actions/upload-artifact@[0-9a-f]{40} # v7")
        self.assertRegex(source, r"actions/download-artifact@[0-9a-f]{40} # v8")
        self.assertIn("RELEASE-METADATA.json", source)
        self.assertIn('"sourceTag"', source)
        self.assertIn('"sourceCommit"', source)
        self.assertIn('"workflowCommit"', source)
        self.assertIn("verify-published-release:", source)
        self.assertIn("needs: publish", source)
        self.assertIn("attestations: read", source)
        self.assertIn('python scripts/verify-release.py "$PLURA_RELEASE_TAG"', source)

    def test_pypi_workflow_uses_manual_trusted_publishing_without_token_secret(self):
        source = PYPI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("ref: ${{ inputs.tag }}", source)
        self.assertIn("default: false", source)
        self.assertIn("if: ${{ inputs.publish }}", source)
        self.assertIn("name: pypi", source)
        self.assertIn("id-token: write", source)
        self.assertIn("attestations: read", source)
        self.assertIn("gh release download", source)
        self.assertIn(".immutable", source)
        self.assertIn("SHA256SUMS", source)
        self.assertIn("RELEASE-METADATA.json", source)
        self.assertIn("gh attestation verify", source)
        self.assertNotIn("python -m build", source)
        self.assertIn(
            "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
            source,
        )
        self.assertIn("packages-dir: dist/", source)
        self.assertNotIn("password:", source)

    def test_all_external_workflow_actions_are_pinned_to_commit_shas(self):
        pinned = re.compile(r"^\s*(?:-\s*)?uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#\s+\S.*)?$")
        for path in sorted(WORKFLOW_DIR.glob("*.yml")):
            with self.subTest(workflow=path.name):
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if "uses:" not in line:
                        continue
                    self.assertRegex(
                        line,
                        pinned,
                        msg=f"{path.name}:{line_number} must pin an immutable action commit SHA",
                    )

    def test_renderer_binds_release_metadata_without_leaving_template_tokens(self):
        with tempfile.TemporaryDirectory(prefix="plura-release-render-", dir=ROOT) as root:
            output = Path(root) / "install.sh"
            sha = "a" * 64
            subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--template",
                    str(TEMPLATE),
                    "--output",
                    str(output),
                    "--repository",
                    "example/plura-desktop",
                    "--tag",
                    "v0.1.0",
                    "--version",
                    "0.1.0",
                    "--wheel",
                    "plura_desktop-0.1.0-py3-none-any.whl",
                    "--sha256",
                    sha,
                    "--standalone-arm64",
                    "plura-desktop-macos-arm64",
                    "--standalone-arm64-sha256",
                    "c" * 64,
                    "--standalone-x86-64",
                    "plura-desktop-macos-x86_64",
                    "--standalone-x86-64-sha256",
                    "d" * 64,
                ],
                check=True,
            )
            rendered = output.read_text(encoding="utf-8")
            self.assertNotIn("@@PLURA_", rendered)
            self.assertIn('RELEASE_MODE="release"', rendered)
            self.assertIn('RELEASE_REPOSITORY="example/plura-desktop"', rendered)
            self.assertIn('RELEASE_TAG="v0.1.0"', rendered)
            self.assertIn(f'RELEASE_WHEEL_SHA256="{sha}"', rendered)
            self.assertIn('RELEASE_STANDALONE_ARM64="plura-desktop-macos-arm64"', rendered)
            self.assertIn('RELEASE_STANDALONE_X86_64="plura-desktop-macos-x86_64"', rendered)
            if os.name != "nt":
                self.assertTrue(output.stat().st_mode & 0o100)

    def test_renderer_rejects_tag_version_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="plura-release-render-bad-", dir=ROOT) as root:
            result = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--template",
                    str(TEMPLATE),
                    "--output",
                    str(Path(root) / "install.sh"),
                    "--repository",
                    "example/plura-desktop",
                    "--tag",
                    "v9.9.9",
                    "--version",
                    "0.1.0",
                    "--wheel",
                    "plura_desktop-0.1.0-py3-none-any.whl",
                    "--sha256",
                    "b" * 64,
                    "--standalone-arm64",
                    "plura-desktop-macos-arm64",
                    "--standalone-arm64-sha256",
                    "c" * 64,
                    "--standalone-x86-64",
                    "plura-desktop-macos-x86_64",
                    "--standalone-x86-64-sha256",
                    "d" * 64,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--tag must be exactly v<version>", result.stderr)

    def test_macos_installer_uses_checksum_bound_no_index_wheel_install(self):
        source = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("shasum -a 256", source)
        self.assertIn("--no-deps --no-index", source)
        self.assertIn("--standalone", source)
        self.assertIn("_diagnose-cli", source)
        self.assertIn("install-record", source)
        self.assertIn("codesign --verify --strict", source)
        self.assertIn("schema=2", source)
        self.assertNotIn("curl -fsSL", source)

    def test_guided_updater_can_follow_prerelease_only_distribution(self):
        source = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("api.github.com/repos/${RELEASE_REPOSITORY}/releases?per_page=1", source)
        self.assertIn("/usr/bin/plutil -extract 0.tag_name raw", source)
        self.assertIn("releases/download/\\$tag/install-plura-desktop-macos.sh", source)
        self.assertNotIn("releases/latest/download/install-plura-desktop-macos.sh", source)
        self.assertIn("if [ -t 0 ]; then", source)
        self.assertIn('echo "Update finished. Press Return to close."', source)

    @unittest.skipUnless(sys.platform == "darwin", "macOS installer integration test")
    def test_guided_update_refuses_running_profile_before_runtime_swap(self):
        with tempfile.TemporaryDirectory(prefix="plura-release-running-", dir=ROOT) as root:
            home = Path(root)
            meta = home / "Library/Application Support/PluraDesktop"
            meta.mkdir(parents=True)
            control = meta / "plura-desktop"
            control.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"state": "running"}\'\n', encoding="utf-8")
            control.chmod(0o700)
            (meta / "profile-2-install-manifest.json").write_text("{}\n", encoding="utf-8")
            source = home / "standalone.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            standalone = home / "plura-desktop-standalone"
            subprocess.run(["xcrun", "clang", str(source), "-o", str(standalone)], check=True)
            subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(standalone)], check=True)
            digest = hashlib.sha256(standalone.read_bytes()).hexdigest()
            env = dict(os.environ)
            env["HOME"] = str(home)
            result = subprocess.run(
                [
                    "/bin/sh",
                    str(TEMPLATE),
                    "--standalone",
                    str(standalone),
                    "--standalone-sha256",
                    digest,
                    "--version",
                    "0.1.0",
                    "--cli-only",
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Quit ChatGPT Profile 2 normally", result.stdout + result.stderr)
            self.assertFalse((meta / "distribution/cli-runtime").exists())


if __name__ == "__main__":
    unittest.main()
