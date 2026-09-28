#!/usr/bin/env python3
"""Verify one immutable Plura Desktop GitHub Release end to end.

This is a convenience wrapper around GitHub's native release verification,
the repository's SHA256SUMS file, release metadata, and GitHub Actions
artifact attestations. It intentionally depends only on Python stdlib and
the GitHub CLI so the manual procedure in docs/VERIFY_RELEASE.md remains an
independent fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


DEFAULT_REPOSITORY = "LJY0317/plura-desktop"
TAG_PATTERN = re.compile(r"v[0-9]+(?:\.[0-9]+)+")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


def fail(message: str) -> None:
    raise RuntimeError(message)


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            fail(f"invalid SHA256SUMS line {line_number}")
        digest, filename = parts
        filename = filename.lstrip("*")
        if not SHA256_PATTERN.fullmatch(digest):
            fail(f"invalid SHA-256 on line {line_number}")
        candidate = Path(filename)
        if candidate.name != filename or filename in {"", ".", ".."}:
            fail(f"unsafe asset name in SHA256SUMS: {filename!r}")
        if filename in checksums:
            fail(f"duplicate asset in SHA256SUMS: {filename}")
        checksums[filename] = digest
    if not checksums:
        fail("SHA256SUMS contains no assets")
    return checksums


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_downloaded_assets(root: Path, checksums: dict[str, str]) -> None:
    downloaded = {path.name for path in root.iterdir() if path.is_file()}
    expected = set(checksums) | {"SHA256SUMS"}
    if downloaded != expected:
        missing = sorted(expected - downloaded)
        unexpected = sorted(downloaded - expected)
        fail(f"release asset set mismatch; missing={missing}, unexpected={unexpected}")
    for filename, expected_digest in sorted(checksums.items()):
        actual = sha256(root / filename)
        if actual != expected_digest:
            fail(f"SHA-256 mismatch for {filename}: expected {expected_digest}, got {actual}")


def validate_metadata(
    metadata_path: Path,
    *,
    repository: str,
    tag: str,
    resolved_commit: str,
) -> None:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schemaVersion") != 1:
        fail("unsupported RELEASE-METADATA.json schemaVersion")
    if metadata.get("repository") != repository:
        fail("release metadata repository mismatch")
    if metadata.get("sourceTag") != tag:
        fail("release metadata sourceTag mismatch")
    source_commit = metadata.get("sourceCommit")
    if not isinstance(source_commit, str) or not COMMIT_PATTERN.fullmatch(source_commit):
        fail("release metadata sourceCommit is not a full commit SHA")
    if source_commit != resolved_commit:
        fail(
            "release metadata sourceCommit mismatch: "
            f"expected {resolved_commit}, got {source_commit}"
        )


def verify_release(repository: str, tag: str) -> None:
    if shutil.which("gh") is None:
        fail("GitHub CLI (`gh`) is required")
    if not TAG_PATTERN.fullmatch(tag):
        fail("tag must look like v0.1.13")

    immutable = run(
        "gh",
        "api",
        f"repos/{repository}/releases/tags/{tag}",
        "--jq",
        ".immutable",
        capture=True,
    )
    if immutable != "true":
        fail(f"{tag} is not a repository-native immutable GitHub Release")

    resolved_commit = run(
        "gh",
        "api",
        f"repos/{repository}/commits/{tag}",
        "--jq",
        ".sha",
        capture=True,
    )
    if not COMMIT_PATTERN.fullmatch(resolved_commit):
        fail(f"unable to resolve {tag} to a full commit SHA")

    run("gh", "release", "verify", tag, "--repo", repository)

    with tempfile.TemporaryDirectory(prefix="plura-release-verify-") as temporary:
        root = Path(temporary)
        run("gh", "release", "download", tag, "--repo", repository, "--dir", str(root))

        checksum_path = root / "SHA256SUMS"
        metadata_path = root / "RELEASE-METADATA.json"
        if not checksum_path.is_file():
            fail("release is missing SHA256SUMS")
        if not metadata_path.is_file():
            fail("release is missing RELEASE-METADATA.json")

        checksums = parse_checksums(checksum_path)
        verify_downloaded_assets(root, checksums)
        validate_metadata(
            metadata_path,
            repository=repository,
            tag=tag,
            resolved_commit=resolved_commit,
        )

        for filename in sorted(checksums):
            run("gh", "attestation", "verify", str(root / filename), "--repo", repository)

    print(f"Verified {repository} {tag}: immutable release, checksums, metadata, attestations OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="immutable release tag, for example v0.1.13")
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
        help=f"GitHub repository (default: {DEFAULT_REPOSITORY})",
    )
    args = parser.parse_args(argv)
    try:
        verify_release(args.repository, args.tag)
    except (RuntimeError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
