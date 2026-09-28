from __future__ import annotations

import sys

from .base import DesktopPlatform
from .linux import LinuxPlatform
from .macos import MacOSPlatform
from .windows import WindowsPlatform


def current_platform(*, app_override=None, home=None) -> DesktopPlatform:
    if sys.platform == "darwin":
        return MacOSPlatform(app_override=app_override, home=home)
    if sys.platform == "win32":
        return WindowsPlatform(app_override=app_override, home=home)
    if sys.platform.startswith("linux"):
        return LinuxPlatform(app_override=app_override, home=home)
    raise RuntimeError(f"Unsupported desktop platform: {sys.platform}")


__all__ = [
    "DesktopPlatform",
    "LinuxPlatform",
    "MacOSPlatform",
    "WindowsPlatform",
    "current_platform",
]
