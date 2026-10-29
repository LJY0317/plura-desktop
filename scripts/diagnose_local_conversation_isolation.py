#!/usr/bin/env python3
"""Read-only macOS Plura profile-local storage isolation audit.

NEVER reads private conversation bodies, authentication files, browser
databases, cookies, Remote enrollment records, application logs or settings.
It inspects only directory/file metadata plus opaque installation_id validity
and equality. No credential, path containing a conversation ID, or raw UUID
is reported. Work history contents/Remote permissions are NOT diagnosed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import uuid


# Deliberately a fixed set of known locations; do not recursively probe a
# whole user home or inspect Browser/SQLite data in an attempt to guess Work IDs.
PAIR_PATHS = {
    "codex_home": (".codex", ".codex-profile2", "directory"),
    "codex_sessions": (".codex/sessions", ".codex-profile2/sessions", "directory"),
    "codex_writer_locks": (
        ".codex/thread-writer-locks", ".codex-profile2/thread-writer-locks",
        "directory",
    ),
    "codex_paginated_index": (
        ".codex/thread_history_1.sqlite",
        ".codex-profile2/thread_history_1.sqlite", "file",
    ),
    "codex_state_db": (
        ".codex/state_5.sqlite", ".codex-profile2/state_5.sqlite", "file",
    ),
    "desktop_user_data": (
        "Library/Application Support/Codex",
        "Library/Application Support/Codex-Profile2", "directory",
    ),
    "desktop_browser_profile": (
        "Library/Application Support/Codex/Default",
        "Library/Application Support/Codex-Profile2/Default", "directory",
    ),
    "desktop_indexeddb": (
        "Library/Application Support/Codex/Default/IndexedDB",
        "Library/Application Support/Codex-Profile2/Default/IndexedDB",
        "directory",
    ),
    "desktop_local_storage": (
        "Library/Application Support/Codex/Default/Local Storage",
        "Library/Application Support/Codex-Profile2/Default/Local Storage",
        "directory",
    ),
    "desktop_session_storage": (
        "Library/Application Support/Codex/Default/Session Storage",
        "Library/Application Support/Codex-Profile2/Default/Session Storage",
        "directory",
    ),
    "desktop_artifact_sessions": (
        "Library/Application Support/Codex/artifact-sessions",
        "Library/Application Support/Codex-Profile2/artifact-sessions",
        "directory",
    ),
}
MAX_DIRECTORY_ENTRIES = 100_000


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None


def _kind(value: os.stat_result | None) -> str:
    if value is None:
        return "missing_or_unreadable"
    if stat.S_ISLNK(value.st_mode):
        return "symlink"
    if stat.S_ISDIR(value.st_mode):
        return "directory"
    if stat.S_ISREG(value.st_mode):
        return "file"
    return "other"


def _has_symlink_ancestor(home: Path, rel: str) -> bool:
    """Stop before following a symlink in any known child location."""
    parent = home
    for component in Path(rel).parts[:-1]:
        parent /= component
        if _kind(_lstat(parent)) == "symlink":
            return True
    return False


def _pair(home: Path, left: str, right: str, expected: str) -> dict[str, object]:
    unsafe_a = _has_symlink_ancestor(home, left)
    unsafe_b = _has_symlink_ancestor(home, right)
    a = None if unsafe_a else _lstat(home / left)
    b = None if unsafe_b else _lstat(home / right)
    ak = "unsafe_ancestor" if unsafe_a else _kind(a)
    bk = "unsafe_ancestor" if unsafe_b else _kind(b)
    if "symlink" in (ak, bk) or "unsafe_ancestor" in (ak, bk):
        status = "linked_or_unsafe"
    elif ak == bk == expected and a is not None and b is not None:
        status = (
            "shared_physical_storage"
            if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
            else "separate"
        )
    else:
        status = "incomplete"
    return {"profile1_kind": ak, "profile2_kind": bk, "status": status}


def _opaque_installation_ids(home: Path) -> str:
    ids: list[uuid.UUID] = []
    for base in (".codex", ".codex-profile2"):
        if _kind(_lstat(home / base)) != "directory":
            return "unverified"
        path = home / base / "installation_id"
        if _kind(_lstat(path)) != "file":
            return "unverified"
        try:
            # Fixed, non-credential identity file. Never report its contents.
            if path.stat().st_size > 128:
                return "unverified"
            ids.append(uuid.UUID(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError, UnicodeError):
            return "unverified"
    return "distinct_valid_ids" if ids[0] != ids[1] else "same_id"


def _rollouts(root: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "count": None,
        "symlink_entries": 0,
        "scan": "not_available",
    }
    if _kind(_lstat(root)) != "directory":
        return {**result, "_inodes": set(), "_names": set()}
    visited = set()
    names = set()
    inodes = set()
    stack = [root]
    entries_seen = 0
    try:
        while stack:
            folder = stack.pop()
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    entries_seen += 1
                    if entries_seen > MAX_DIRECTORY_ENTRIES:
                        raise OverflowError
                    if entry.is_symlink():
                        result["symlink_entries"] = int(result["symlink_entries"]) + 1
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        # Protection against accidental directory hardlinks or
                        # loops in unsupported/virtual filesystem layouts.
                        # DirEntry.stat reports zero device/inode on Windows.
                        item = os.stat(entry.path, follow_symlinks=False)
                        key = (item.st_dev, item.st_ino)
                        if key not in visited:
                            visited.add(key)
                            stack.append(Path(entry.path))
                    elif (entry.name.startswith("rollout-")
                          and entry.name.endswith(".jsonl")
                          and entry.is_file(follow_symlinks=False)):
                        item = os.stat(entry.path, follow_symlinks=False)
                        inodes.add((item.st_dev, item.st_ino))
                        names.add(entry.name)
        result.update(count=len(names), scan="complete")
    except (OSError, OverflowError):
        result.update(count=None, scan="incomplete")
    return {**result, "_inodes": inodes, "_names": names}


def audit(home: Path) -> dict[str, object]:
    pairs = {
        name: _pair(home, left, right, expected)
        for name, (left, right, expected) in PAIR_PATHS.items()
    }
    # Never descend through a symlinked CODEX_HOME, even though the
    # individual sessions child itself could appear to be a real directory.
    a = _rollouts(
        home / ".codex/sessions"
        if _kind(_lstat(home / ".codex")) == "directory"
        else home / ".plura-isolation-unavailable-profile1"
    )
    b = _rollouts(
        home / ".codex-profile2/sessions"
        if _kind(_lstat(home / ".codex-profile2")) == "directory"
        else home / ".plura-isolation-unavailable-profile2"
    )
    shared_rollouts = (
        len(a["_inodes"] & b["_inodes"])
        if a["scan"] == b["scan"] == "complete"
        else None
    )
    # A name collision alone is not proof of live sharing: two separate
    # copies can have identical rollout names.
    same_named = (
        len(a["_names"] & b["_names"])
        if a["scan"] == b["scan"] == "complete"
        else None
    )
    codex_checks = [
        pairs[k]["status"] for k in (
            "codex_home", "codex_sessions", "codex_writer_locks",
            "codex_paginated_index", "codex_state_db",
        )
    ]
    desktop_checks = [
        pairs[k]["status"] for k in (
            "desktop_user_data", "desktop_browser_profile",
            "desktop_indexeddb", "desktop_local_storage",
            "desktop_session_storage",
        )
    ]

    def verdict(checks: list[object]) -> str:
        if any(c in ("shared_physical_storage", "linked_or_unsafe") for c in checks):
            return "shared_or_linked"
        if all(c == "separate" for c in checks):
            return "separate"
        return "incomplete"

    codex_verdict = verdict(codex_checks)
    if shared_rollouts:
        codex_verdict = "shared_or_linked"
    if any(x["symlink_entries"] for x in (a, b)):
        codex_verdict = "shared_or_linked"
    return {
        "scope": "read_only_local_storage_metadata_not_work_thread_contents_or_account_access",
        "codex_storage": codex_verdict,
        "desktop_browser_storage": verdict(desktop_checks),
        "installation_ids": _opaque_installation_ids(home),
        "path_checks": pairs,
        "codex_rollouts": {
            "profile1_count": a["count"],
            "profile2_count": b["count"],
            "same_inode_files": shared_rollouts,
            "same_named_files": same_named,
            "profile1_scan": a["scan"],
            "profile2_scan": b["scan"],
            "symlink_entries_total": (
                int(a["symlink_entries"]) + int(b["symlink_entries"])
            ),
        },
        "limits": [
            "Does not determine which specific Work conversations are local or cloud synced.",
            "Separate browser storage does not prove Work cross-account permission behavior.",
            "Does not inspect login, Remote enrollment, or allowlist authorization.",
            "Does not read conversation bodies or infer backend ownership from matching filenames.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="Home directory to audit (default: current user).")
    args = parser.parse_args(argv)
    report = audit(args.home)
    print(json.dumps(report, sort_keys=True, indent=2))
    if ("shared_or_linked" in (
        report["codex_storage"], report["desktop_browser_storage"]
    ) or report["installation_ids"] == "same_id"):
        return 3
    if ("incomplete" in (
        report["codex_storage"], report["desktop_browser_storage"]
    ) or report["installation_ids"] == "unverified"
            or report["codex_rollouts"]["profile1_scan"] != "complete"
            or report["codex_rollouts"]["profile2_scan"] != "complete"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
