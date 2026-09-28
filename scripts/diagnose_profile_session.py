#!/usr/bin/env python3
"""Source-checkout wrapper for the installed `plura-desktop-diagnose` command."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plura_desktop.profile_diagnostics import main


if __name__ == "__main__":
    raise SystemExit(main())
