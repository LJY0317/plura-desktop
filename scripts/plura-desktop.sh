#!/bin/sh
set -eu
exec python3 "$(dirname "$0")/../src/plura_desktop.py" "$@"
