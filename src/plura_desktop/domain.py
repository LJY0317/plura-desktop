from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROFILE_IDENTIFIER_PREFIX = "local.plura-desktop.profile"
MANIFEST_SCHEMA = 5
DEFAULT_MANAGED_PROFILE_INDEX = 2
MIN_MANAGED_PROFILE_INDEX = 2
MAX_MANAGED_PROFILE_INDEX = 99

@dataclass(frozen=True)
class ManagedPath:
    role: str
    path: Path
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in {"directory", "file"}:
            raise ValueError(f"Unsupported managed path kind: {self.kind}")


@dataclass(frozen=True)
class ProfileLayout:
    index: int
    identifier: str
    display_name: str
    selector: Path
    codex_home: Path
    user_data: Path
    metadata: Path
    manifest: Path
    managed: tuple[ManagedPath, ...]

    @property
    def managed_paths(self) -> tuple[Path, ...]:
        return tuple(entry.path for entry in self.managed)


def validate_profile_index(index: int) -> int:
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or index < MIN_MANAGED_PROFILE_INDEX
        or index > MAX_MANAGED_PROFILE_INDEX
    ):
        raise ValueError(
            f"Managed profile index must be between {MIN_MANAGED_PROFILE_INDEX} "
            f"and {MAX_MANAGED_PROFILE_INDEX}"
        )
    return index


def profile_identifier(index: int) -> str:
    validate_profile_index(index)
    return f"{PROFILE_IDENTIFIER_PREFIX}{index}"
