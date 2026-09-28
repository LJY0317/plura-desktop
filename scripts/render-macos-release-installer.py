#!/usr/bin/env python3
"""Render the macOS bootstrap installer with immutable GitHub Release metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


TOKENS = {
    "@@PLURA_RELEASE_MODE@@": None,
    "@@PLURA_REPOSITORY@@": "repository",
    "@@PLURA_TAG@@": "tag",
    "@@PLURA_VERSION@@": "version",
    "@@PLURA_WHEEL@@": "wheel",
    "@@PLURA_WHEEL_SHA256@@": "sha256",
    "@@PLURA_STANDALONE_ARM64@@": "standalone_arm64",
    "@@PLURA_STANDALONE_ARM64_SHA256@@": "standalone_arm64_sha256",
    "@@PLURA_STANDALONE_X86_64@@": "standalone_x86_64",
    "@@PLURA_STANDALONE_X86_64_SHA256@@": "standalone_x86_64_sha256",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--standalone-arm64", required=True)
    parser.add_argument("--standalone-arm64-sha256", required=True)
    parser.add_argument("--standalone-x86-64", dest="standalone_x86_64", required=True)
    parser.add_argument("--standalone-x86-64-sha256", dest="standalone_x86_64_sha256", required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("--repository must be OWNER/REPO")
    if args.tag != f"v{args.version}":
        parser.error("--tag must be exactly v<version>")
    if not re.fullmatch(r"plura_desktop-[A-Za-z0-9_.+-]+-py3-none-any\.whl", args.wheel):
        parser.error("--wheel must be the canonical universal wheel filename")
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256):
        parser.error("--sha256 must be a lowercase SHA-256 digest")
    for name, digest in (
        (args.standalone_arm64, args.standalone_arm64_sha256),
        (args.standalone_x86_64, args.standalone_x86_64_sha256),
    ):
        if not re.fullmatch(r"plura-desktop-macos-(arm64|x86_64)", name):
            parser.error("standalone filenames must be canonical macOS architecture names")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            parser.error("standalone SHA-256 values must be lowercase digests")

    values = vars(args)
    rendered = args.template.read_text(encoding="utf-8")
    for token, key in TOKENS.items():
        if token not in rendered:
            raise RuntimeError(f"Installer template is missing token {token}")
        rendered = rendered.replace(token, "release" if key is None else str(values[key]))
    if "@@PLURA_" in rendered:
        raise RuntimeError("Installer rendering left unresolved release tokens")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    args.output.chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
