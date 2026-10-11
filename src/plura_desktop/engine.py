"""Optional caller-owned Codex executable selection; profile data stays Plura-owned."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re


def codex_engine_override() -> tuple[Path, str] | None:
    value = os.environ.get("CODEX_EXECUTABLE")
    expected = os.environ.get("CODEX_EXECUTABLE_SHA256")
    if not value:
        if expected:
            raise RuntimeError("Codex executable hash requires an explicit executable")
        return None
    candidate = Path(value).expanduser().resolve(strict=True)
    if not candidate.is_file() or (os.name != "nt" and not os.access(candidate, os.X_OK)):
        raise RuntimeError("Explicit Codex executable is not an executable regular file")
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if expected and (not re.fullmatch(r"[a-f0-9]{64}", expected) or expected != actual):
        raise RuntimeError("Explicit Codex executable hash mismatch")
    return candidate, actual
