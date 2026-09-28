#!/usr/bin/env python3
"""Copied runtime entry point for installed Plura Desktop selectors/control runtimes."""

import sys

from plura_desktop.cli import main
from plura_desktop.profile_diagnostics import main as diagnostic_main


def entrypoint() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_diagnose-cli":
        return diagnostic_main(sys.argv[2:])
    return main()


if __name__ == "__main__":
    raise SystemExit(entrypoint())
