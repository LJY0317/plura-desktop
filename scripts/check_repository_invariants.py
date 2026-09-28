#!/usr/bin/env python3
"""Fail CI when repository/public-release invariants are violated."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
RETIRED_PRODUCT_TOKENS = (
    "CodexMultiProfileLauncher",
    "Codex Multi-Profile Launcher",
    "local.codex-multi-profile-launcher",
)
RETIRED_PATHS = (
    "src/codex_profile.py",
    "scripts/codex-profile.sh",
    "scripts/codex-profile.ps1",
    "src/plura_desktop/migration.py",
)
LOCAL_ONLY_PATHS = {
    "AGENTS.md",
    "STATE.md",
    "MILESTONES.md",
}
FORBIDDEN_TRACKED_SUFFIXES = {
    ".dmg",
    ".pkg",
    ".p12",
    ".pem",
    ".key",
    ".log",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".whl",
}
PUBLIC_TEXT_PATTERNS = (
    ("absolute macOS home path", re.compile(r"/Users/[^/\s]+/")),
    ("absolute Windows home path", re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+\\\\")),
    ("email address", re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("OpenAI-style secret key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
)
PUBLIC_TEXT_ALLOWLIST = {
    "email address": ("plugins.codex-app-tools@openai-bundled.mcp",),
}
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024
CORE_FILES = (
    ROOT / "src/plura_desktop/domain.py",
    ROOT / "src/plura_desktop/manager.py",
    ROOT / "src/plura_desktop/runtime.py",
    ROOT / "src/plura_desktop/cli.py",
)
CORE_OBSERVATIONAL_TOKENS = (
    "codex_mcp::",
    "codex_core::tools::spec_plan",
    "logs_2.sqlite",
    "/Applications/ChatGPT.app",
    "ChatGPT.exe",
    "thread/start",
    "model_provider",
    "querySelector(",
    "querySelectorAll(",
    "aria-label=",
)
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def tracked_text_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
    )
    paths: list[Path] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        path = ROOT / raw.decode("utf-8")
        if path.is_file():
            paths.append(path)
    return paths


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def check_markdown_links(path: Path, text: str) -> list[str]:
    failures: list[str] = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_path = unquote(target.split("#", 1)[0])
        if not target_path:
            continue
        resolved = (path.parent / target_path).resolve()
        if not resolved.exists():
            failures.append(
                f"broken relative Markdown link in {path.relative_to(ROOT)}: {target}"
            )
    return failures


def main() -> int:
    failures: list[str] = []
    paths = tracked_text_files()
    for path in paths:
        relative = path.relative_to(ROOT)
        relative_text = relative.as_posix()
        if relative_text in LOCAL_ONLY_PATHS:
            failures.append(f"local-only project note is tracked: {relative_text}")
        if path.is_symlink():
            failures.append(f"symlink is tracked in public snapshot: {relative_text}")
        if path.stat().st_size > MAX_TRACKED_FILE_BYTES:
            failures.append(f"tracked file exceeds 5 MiB public-source limit: {relative_text}")
        if relative.suffix.lower() in FORBIDDEN_TRACKED_SUFFIXES:
            failures.append(f"generated/sensitive file type is tracked: {relative_text}")
        if any(part.endswith(".app") for part in relative.parts):
            failures.append(f"application bundle content is tracked: {relative_text}")
        if any(part in {"build", "dist", ".venv", "venv", "__pycache__"} for part in relative.parts):
            failures.append(f"generated/runtime directory is tracked: {relative_text}")
        for retired in RETIRED_PATHS:
            if relative_text == retired:
                failures.append(f"retired compatibility path is tracked: {relative_text}")
        text = read_text(path)
        if text is None:
            continue
        if relative.suffix.lower() == ".md":
            failures.extend(check_markdown_links(path, text))
        if relative == Path("scripts/check_repository_invariants.py"):
            continue
        for label, pattern in PUBLIC_TEXT_PATTERNS:
            scrubbed = text
            for allowed in PUBLIC_TEXT_ALLOWLIST.get(label, ()):
                scrubbed = scrubbed.replace(allowed, "")
            if pattern.search(scrubbed):
                failures.append(f"{label} found in tracked public text: {relative}")
        for token in RETIRED_PRODUCT_TOKENS:
            if token in text:
                failures.append(f"retired product token {token!r} found in {relative}")

    for path in CORE_FILES:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        for token in CORE_OBSERVATIONAL_TOKENS:
            if token in text:
                failures.append(f"observational integration token {token!r} leaked into core file {relative}")

    if failures:
        for failure in failures:
            print(f"Error: {failure}", file=sys.stderr)
        return 1
    print("Repository invariants: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
