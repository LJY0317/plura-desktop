"""Cross-platform core for isolated ChatGPT/Codex desktop profiles."""

from .domain import DEFAULT_MANAGED_PROFILE_INDEX, MANIFEST_SCHEMA
from .version import __version__

__all__ = ["DEFAULT_MANAGED_PROFILE_INDEX", "MANIFEST_SCHEMA", "__version__"]
