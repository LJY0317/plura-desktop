#!/bin/sh
set -eu

usage() {
  echo "Usage: $0 OUTPUT_DIR ARCH" >&2
  echo "ARCH must be arm64 or x86_64 and match the current runner." >&2
  exit 2
}

[ "$#" -eq 2 ] || usage
output_dir=$1
arch=$2

[ "$(uname -s)" = "Darwin" ] || { echo "Error: standalone macOS builds require macOS." >&2; exit 2; }
case "$arch" in arm64|x86_64) ;; *) usage ;; esac
[ "$(uname -m)" = "$arch" ] || { echo "Error: requested $arch but runner is $(uname -m)." >&2; exit 2; }

command -v pyinstaller >/dev/null 2>&1 || { echo "Error: pyinstaller is required." >&2; exit 2; }

tmp=$(mktemp -d "${TMPDIR:-/tmp}/plura-standalone.XXXXXX")
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT HUP INT TERM

mkdir -p "$output_dir"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-14.0}"

set --
if [ -n "${PLURA_CODESIGN_IDENTITY:-}" ]; then
  set -- --codesign-identity "$PLURA_CODESIGN_IDENTITY"
fi

pyinstaller \
  --noconfirm \
  --clean \
  --onedir \
  "$@" \
  --name plura-desktop \
  --paths src \
  --distpath "$tmp/dist" \
  --workpath "$tmp/work" \
  --specpath "$tmp/spec" \
  src/plura_desktop.py >/dev/null

runtime="$tmp/dist/plura-desktop"
source_binary="$runtime/plura-desktop"
target="$output_dir/plura-desktop-macos-$arch.zip"

actual_arches=$(lipo -archs "$source_binary")
case " $actual_arches " in
  *" $arch "*) ;;
  *) echo "Error: standalone binary architecture mismatch: $actual_arches" >&2; exit 1 ;;
esac

"$source_binary" --version
"$source_binary" _diagnose-cli --version
codesign --verify --strict --verbose=2 "$source_binary"
# Keep the runtime unpacked after installation; only the release transport is compressed.
ditto -c -k --keepParent "$runtime" "$target"
echo "$target"
