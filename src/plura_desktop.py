#!/usr/bin/env python3
"""Canonical cross-platform Plura Desktop CLI entry point."""

import sys

from plura_desktop.cli import main
from plura_desktop.profile_diagnostics import main as diagnostic_main


def entrypoint() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_diagnose-cli":
        return diagnostic_main(sys.argv[2:])
    return main()


if __name__ == "__main__":
    raise SystemExit(entrypoint())
