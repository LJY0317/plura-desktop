#!/bin/sh
set -eu

# Release builds replace these tokens. Source-checkout/local tests can use --standalone or --wheel.
RELEASE_MODE="@@PLURA_RELEASE_MODE@@"
RELEASE_REPOSITORY="@@PLURA_REPOSITORY@@"
RELEASE_TAG="@@PLURA_TAG@@"
RELEASE_VERSION="@@PLURA_VERSION@@"
RELEASE_WHEEL="@@PLURA_WHEEL@@"
RELEASE_WHEEL_SHA256="@@PLURA_WHEEL_SHA256@@"
RELEASE_STANDALONE_ARM64="@@PLURA_STANDALONE_ARM64@@"
RELEASE_STANDALONE_ARM64_SHA256="@@PLURA_STANDALONE_ARM64_SHA256@@"
RELEASE_STANDALONE_X86_64="@@PLURA_STANDALONE_X86_64@@"
RELEASE_STANDALONE_X86_64_SHA256="@@PLURA_STANDALONE_X86_64_SHA256@@"

wheel=""
standalone=""
standalone_sha256=""
expected_version=""
expected_sha256=""
cli_only=0
gui=0

usage() {
  cat <<'EOF'
Usage: install-plura-desktop-macos.sh [options]

Options:
  --wheel PATH       Install from a local release wheel instead of downloading it.
  --standalone PATH  Install from a local standalone Plura binary (preferred on macOS).
  --standalone-sha256 HEX
                     Verify the local standalone binary against this SHA-256 digest.
  --version VERSION  Expected Plura Desktop version (required with an unrendered local script).
  --sha256 HEX       Verify the local wheel against this SHA-256 digest.
  --cli-only         Install/update the Plura CLI runtime without creating/refreshing profiles.
  --gui              Show macOS dialogs for the final result (used by the release installer app).
  -h, --help         Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --wheel)
      [ "$#" -ge 2 ] || { echo "Error: --wheel requires a path" >&2; exit 2; }
      wheel=$2
      shift 2
      ;;
    --standalone)
      [ "$#" -ge 2 ] || { echo "Error: --standalone requires a path" >&2; exit 2; }
      standalone=$2
      shift 2
      ;;
    --standalone-sha256)
      [ "$#" -ge 2 ] || { echo "Error: --standalone-sha256 requires a value" >&2; exit 2; }
      standalone_sha256=$2
      shift 2
      ;;
    --version)
      [ "$#" -ge 2 ] || { echo "Error: --version requires a value" >&2; exit 2; }
      expected_version=$2
      shift 2
      ;;
    --sha256)
      [ "$#" -ge 2 ] || { echo "Error: --sha256 requires a value" >&2; exit 2; }
      expected_sha256=$2
      shift 2
      ;;
    --cli-only)
      cli_only=1
      shift
      ;;
    --gui)
      gui=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Error: this installer is for macOS only." >&2
  exit 2
fi

notify() {
  title=$1
  message=$2
  echo "$title: $message"
  if [ "$gui" -eq 1 ] && command -v osascript >/dev/null 2>&1; then
    TITLE=$title MESSAGE=$message osascript <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run
  display alert (system attribute "TITLE") message (system attribute "MESSAGE") as informational
end run
APPLESCRIPT
  fi
}

fail() {
  message=$1
  notify "Plura Desktop installation failed" "$message"
  exit 1
}

current_uid=$(id -u)

ensure_owned_parent_directory() {
  path=$1
  if [ -e "$path" ] || [ -L "$path" ]; then
    [ ! -L "$path" ] && [ -d "$path" ] || fail "Installer directory is unsafe or replaced: $path"
    owner=$(stat -f '%u' "$path" 2>/dev/null || true)
    [ "$owner" = "$current_uid" ] || fail "Installer directory is not owned by the current user: $path"
  else
    mkdir -p "$path" || fail "Could not create installer directory: $path"
  fi
}

ensure_private_directory() {
  path=$1
  ensure_owned_parent_directory "$path"
  chmod 700 "$path" 2>/dev/null || true
}

validate_replaceable_file() {
  path=$1
  if [ -e "$path" ] || [ -L "$path" ]; then
    [ ! -L "$path" ] && [ -f "$path" ] || fail "Installer file is unsafe or replaced: $path"
    owner=$(stat -f '%u' "$path" 2>/dev/null || true)
    [ "$owner" = "$current_uid" ] || fail "Installer file is not owned by the current user: $path"
  fi
}

identity() {
  stat -f '%d:%i' "$1"
}

record_value() {
  file=$1
  key=$2
  awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$file"
}

has_entries() {
  directory=$1
  [ -n "$(find "$directory" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]
}

validate_recorded_file() {
  path=$1
  expected=$2
  label=$3
  [ -f "$path" ] && [ ! -L "$path" ] || fail "$label is missing or replaced: $path"
  owner=$(stat -f '%u' "$path" 2>/dev/null || true)
  [ "$owner" = "$current_uid" ] || fail "$label is not owned by the current user: $path"
  [ "$(identity "$path")" = "$expected" ] || fail "$label identity changed: $path"
}

python_ok() {
  candidate=$1
  [ -x "$candidate" ] || return 1
  "$candidate" -c 'import sys, venv; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  if [ -n "${PLURA_PYTHON:-}" ] && python_ok "$PLURA_PYTHON"; then
    printf '%s\n' "$PLURA_PYTHON"
    return 0
  fi
  for candidate in \
    "$(command -v python3 2>/dev/null || true)" \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3
  do
    [ -n "$candidate" ] || continue
    if python_ok "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if [ -z "$expected_version" ] && [ "$RELEASE_MODE" = "release" ]; then
  expected_version=$RELEASE_VERSION
fi
if [ -z "$expected_sha256" ] && [ "$RELEASE_MODE" = "release" ]; then
  expected_sha256=$RELEASE_WHEEL_SHA256
fi
if [ "$RELEASE_MODE" != "release" ] && [ -z "$expected_version" ]; then
  fail "A local/unrendered installer requires --version VERSION."
fi

tmp_root=$(mktemp -d "${TMPDIR:-/tmp}/plura-desktop-install.XXXXXX")
cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT HUP INT TERM

machine_arch=$(uname -m)
release_standalone_name=""
release_standalone_sha=""
case "$machine_arch" in
  arm64)
    release_standalone_name=$RELEASE_STANDALONE_ARM64
    release_standalone_sha=$RELEASE_STANDALONE_ARM64_SHA256
    ;;
  x86_64)
    release_standalone_name=$RELEASE_STANDALONE_X86_64
    release_standalone_sha=$RELEASE_STANDALONE_X86_64_SHA256
    ;;
esac

if [ "$RELEASE_MODE" = "release" ] && [ -n "$standalone" ] && [ -z "$standalone_sha256" ]; then
  standalone_sha256=$release_standalone_sha
fi

if [ -z "$standalone" ] && [ "$RELEASE_MODE" = "release" ] && [ -n "$release_standalone_name" ]; then
  script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  if [ -f "$script_dir/$release_standalone_name" ]; then
    standalone="$script_dir/$release_standalone_name"
  else
    command -v curl >/dev/null 2>&1 || fail "curl is required to download the standalone Plura runtime."
    standalone="$tmp_root/$release_standalone_name"
    url="https://github.com/$RELEASE_REPOSITORY/releases/download/$RELEASE_TAG/$release_standalone_name"
    echo "Downloading $url"
    curl -fL --retry 3 --proto '=https' --tlsv1.2 -o "$standalone" "$url" || fail "Could not download the standalone Plura runtime."
  fi
  standalone_sha256=$release_standalone_sha
fi

if [ -z "$standalone" ] && [ -z "$wheel" ]; then
  script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  if [ "$RELEASE_MODE" = "release" ] && [ -f "$script_dir/$RELEASE_WHEEL" ]; then
    wheel="$script_dir/$RELEASE_WHEEL"
  elif [ "$RELEASE_MODE" = "release" ]; then
    command -v curl >/dev/null 2>&1 || fail "curl is required to download the release wheel."
    wheel="$tmp_root/$RELEASE_WHEEL"
    url="https://github.com/$RELEASE_REPOSITORY/releases/download/$RELEASE_TAG/$RELEASE_WHEEL"
    echo "Downloading $url"
    curl -fL --retry 3 --proto '=https' --tlsv1.2 -o "$wheel" "$url" || fail "Could not download the Plura Desktop release wheel."
  else
    fail "This source installer is not bound to a release. Pass --standalone PATH --version VERSION or --wheel PATH --version VERSION."
  fi
fi

if [ -n "$standalone" ]; then
  [ -f "$standalone" ] && [ ! -L "$standalone" ] || fail "Standalone Plura runtime not found or unsafe: $standalone"
  if [ -n "$standalone_sha256" ]; then
    actual_standalone_sha256=$(shasum -a 256 "$standalone" | awk '{print $1}')
    [ "$actual_standalone_sha256" = "$standalone_sha256" ] || fail "Standalone Plura runtime checksum verification failed."
  fi
  if command -v lipo >/dev/null 2>&1; then
    standalone_arches=$(lipo -archs "$standalone" 2>/dev/null || true)
    case " $standalone_arches " in
      *" $machine_arch "*) ;;
      *) fail "Standalone Plura runtime does not support this Mac architecture ($machine_arch)." ;;
    esac
  fi
  codesign --verify --strict "$standalone" >/dev/null 2>&1 || fail "Standalone Plura runtime code-signature verification failed."
else
  [ -f "$wheel" ] || fail "Release wheel not found: $wheel"
  case "$wheel" in
    *.whl) ;;
    *) fail "Installer input must be a .whl release artifact." ;;
  esac
  if [ -n "$expected_sha256" ]; then
    actual_sha256=$(shasum -a 256 "$wheel" | awk '{print $1}')
    [ "$actual_sha256" = "$expected_sha256" ] || fail "Release wheel checksum verification failed."
  fi
fi

if [ "$gui" -eq 1 ] && command -v osascript >/dev/null 2>&1; then
  if ! osascript <<'APPLESCRIPT' >/dev/null 2>&1
display dialog "Plura Desktop will install or update its isolated CLI runtime and create/refresh managed ChatGPT profiles. It will not modify the official ChatGPT app or default profile." with title "Install Plura Desktop" buttons {"Cancel", "Install"} default button "Install" cancel button "Cancel" with icon note
APPLESCRIPT
  then
    exit 0
  fi
fi

meta="$HOME/Library/Application Support/PluraDesktop"
distribution="$meta/distribution"
runtime="$distribution/cli-runtime"
bin_dir="$meta/bin"
tools_dir="$HOME/Applications/Plura Desktop Tools"
ensure_owned_parent_directory "$HOME/Library"
ensure_owned_parent_directory "$HOME/Library/Application Support"
ensure_private_directory "$meta"
ensure_private_directory "$distribution"
ensure_private_directory "$bin_dir"
ensure_owned_parent_directory "$HOME/Applications"
ensure_private_directory "$tools_dir"

record="$distribution/install-record"
if [ -e "$record" ] || [ -L "$record" ]; then
  validate_replaceable_file "$record"
  [ "$(record_value "$record" schema)" = "2" ] || fail "Unsupported guided-install ownership record."
  [ -d "$runtime" ] && [ ! -L "$runtime" ] || fail "Recorded CLI runtime is missing or replaced: $runtime"
  runtime_owner=$(stat -f '%u' "$runtime" 2>/dev/null || true)
  [ "$runtime_owner" = "$current_uid" ] || fail "Recorded CLI runtime is not owned by the current user: $runtime"
  [ "$(identity "$runtime")" = "$(record_value "$record" runtime_identity)" ] || fail "Recorded CLI runtime identity changed."
  validate_recorded_file "$bin_dir/plura-desktop" "$(record_value "$record" cli_identity)" "Recorded CLI launcher"
  validate_recorded_file "$bin_dir/plura-desktop-diagnose" "$(record_value "$record" diag_identity)" "Recorded diagnostics launcher"
  validate_recorded_file "$tools_dir/Uninstall Plura Desktop.command" "$(record_value "$record" uninstall_identity)" "Recorded uninstall tool"
  recorded_update_identity=$(record_value "$record" update_identity)
  if [ -n "$recorded_update_identity" ]; then
    validate_recorded_file "$tools_dir/Update Plura Desktop.command" "$recorded_update_identity" "Recorded update tool"
  elif [ -e "$tools_dir/Update Plura Desktop.command" ] || [ -L "$tools_dir/Update Plura Desktop.command" ]; then
    fail "Unexpected update tool exists outside the ownership record."
  fi
  if [ "$(record_value "$record" mode)" = "standalone" ]; then
    runtime_binary="$runtime/plura-desktop"
    validate_recorded_file "$runtime_binary" "$(record_value "$record" runtime_binary_identity)" "Recorded standalone runtime"
    for entry in "$runtime"/*; do
      [ -e "$entry" ] || continue
      [ "$(basename "$entry")" = "plura-desktop" ] || fail "Unexpected standalone runtime artifact: $entry"
    done
  fi
  for entry in "$distribution"/*; do
    [ -e "$entry" ] || continue
    case "$(basename "$entry")" in cli-runtime|install-record) ;; *) fail "Unexpected guided distribution artifact: $entry" ;; esac
  done
  for entry in "$bin_dir"/*; do
    [ -e "$entry" ] || continue
    case "$(basename "$entry")" in plura-desktop|plura-desktop-diagnose) ;; *) fail "Unexpected guided CLI artifact: $entry" ;; esac
  done
  for entry in "$tools_dir"/*; do
    [ -e "$entry" ] || continue
    case "$(basename "$entry")" in 'Update Plura Desktop.command'|'Uninstall Plura Desktop.command') ;; *) fail "Unexpected guided tools artifact: $entry" ;; esac
  done
else
  if has_entries "$distribution" || has_entries "$bin_dir" || has_entries "$tools_dir"; then
    fail "Existing guided-install artifacts have no ownership record; refusing to adopt them."
  fi
fi

existing_control=""
if [ -e "$bin_dir/plura-desktop" ] || [ -L "$bin_dir/plura-desktop" ]; then
  validate_replaceable_file "$bin_dir/plura-desktop"
  existing_control="$bin_dir/plura-desktop"
elif [ -e "$meta/plura-desktop" ] || [ -L "$meta/plura-desktop" ]; then
  validate_replaceable_file "$meta/plura-desktop"
  existing_control="$meta/plura-desktop"
fi

if [ -n "$existing_control" ]; then
  for manifest in "$meta"/profile-*-install-manifest.json; do
    [ -f "$manifest" ] || continue
    name=$(basename "$manifest")
    index=${name#profile-}
    index=${index%-install-manifest.json}
    case "$index" in *[!0-9]*|'') continue ;; esac
    status_json=$("$existing_control" status --profile "$index" --json 2>/dev/null) || fail "Could not verify the current state of ChatGPT Profile $index before updating."
    if ! printf '%s\n' "$status_json" | grep -q '"state": "stopped"'; then
      fail "Quit ChatGPT Profile $index normally before installing/updating Plura Desktop. No running profile was modified."
    fi
  done
fi

staging="$distribution/.cli-runtime-$$"
backup="$distribution/.cli-runtime-backup-$$"
rm -rf "$staging" "$backup"

echo "Creating isolated Plura Desktop CLI runtime..."
runtime_mode="wheel"
if [ -n "$standalone" ]; then
  runtime_mode="standalone"
  mkdir -p "$staging"
  cp "$standalone" "$staging/plura-desktop"
  chmod 700 "$staging/plura-desktop"
  installed_version=$("$staging/plura-desktop" --version | awk '{print $NF}')
  "$staging/plura-desktop" _diagnose-cli --version >/dev/null || fail "Standalone diagnostic dispatch verification failed."
else
  python=$(find_python || true)
  if [ -z "$python" ]; then
    fail "Python 3.10 or later with venv support is required for the wheel fallback. Install current Python from python.org or Homebrew, then run the installer again."
  fi
  "$python" -m venv "$staging" || fail "Could not create the isolated Python runtime."
  "$staging/bin/python" -m pip install --disable-pip-version-check --no-deps --no-index "$wheel" >/dev/null || fail "Could not install the release wheel into the isolated runtime."
  installed_version=$("$staging/bin/python" -c 'from plura_desktop.version import __version__; print(__version__)')
fi
if [ -n "$expected_version" ] && [ "$installed_version" != "$expected_version" ]; then
  fail "Release version mismatch: expected $expected_version, installed $installed_version."
fi
if [ "$runtime_mode" = "standalone" ]; then
  "$staging/plura-desktop" --version >/dev/null || fail "Installed standalone CLI verification failed."
else
  "$staging/bin/python" -m plura_desktop --version >/dev/null || fail "Installed CLI verification failed."
fi

if [ -e "$runtime" ]; then
  [ ! -L "$runtime" ] && [ -d "$runtime" ] || fail "Existing CLI runtime is unsafe or replaced: $runtime"
  mv "$runtime" "$backup"
fi
if ! mv "$staging" "$runtime"; then
  [ -d "$backup" ] && mv "$backup" "$runtime" || true
  fail "Could not activate the new CLI runtime."
fi
rm -rf "$backup"

validate_replaceable_file "$bin_dir/plura-desktop"
validate_replaceable_file "$bin_dir/plura-desktop-diagnose"
cli_staging="$bin_dir/.plura-desktop.$$"
diag_staging="$bin_dir/.plura-desktop-diagnose.$$"
if [ "$runtime_mode" = "standalone" ]; then
cat > "$cli_staging" <<EOF
#!/bin/sh
set -eu
exec "${runtime}/plura-desktop" "\$@"
EOF
cat > "$diag_staging" <<EOF
#!/bin/sh
set -eu
exec "${runtime}/plura-desktop" _diagnose-cli "\$@"
EOF
else
cat > "$cli_staging" <<EOF
#!/bin/sh
set -eu
exec "${runtime}/bin/python" -m plura_desktop "\$@"
EOF
cat > "$diag_staging" <<EOF
#!/bin/sh
set -eu
exec "${runtime}/bin/python" -m plura_desktop.profile_diagnostics "\$@"
EOF
fi
chmod 700 "$cli_staging" "$diag_staging"
mv -f "$cli_staging" "$bin_dir/plura-desktop"
mv -f "$diag_staging" "$bin_dir/plura-desktop-diagnose"

repo_is_rendered=0
[ "$RELEASE_MODE" = "release" ] && repo_is_rendered=1

if [ "$repo_is_rendered" -eq 1 ]; then
  update_path="$tools_dir/Update Plura Desktop.command"
  validate_replaceable_file "$update_path"
  update_staging="$tools_dir/.update.$$"
  cat > "$update_staging" <<EOF
#!/bin/sh
set -eu
echo "Updating Plura Desktop from the latest published GitHub Release..."
tmp=\$(mktemp -d "\${TMPDIR:-/tmp}/plura-desktop-update.XXXXXX")
trap 'rm -rf "\$tmp"' EXIT HUP INT TERM
curl -fL --retry 3 --proto '=https' --tlsv1.2 \
  -H 'Accept: application/vnd.github+json' \
  -o "\$tmp/releases.json" \
  "https://api.github.com/repos/${RELEASE_REPOSITORY}/releases?per_page=1"
tag=\$(/usr/bin/plutil -extract 0.tag_name raw -o - "\$tmp/releases.json" 2>/dev/null) || {
  echo "Could not determine the latest published Plura Desktop release." >&2
  exit 1
}
case "\$tag" in
  v[0-9]*) ;;
  *) echo "Refusing unexpected Plura Desktop release tag: \$tag" >&2; exit 1 ;;
esac
curl -fL --retry 3 --proto '=https' --tlsv1.2 \
  -o "\$tmp/install.sh" \
  "https://github.com/${RELEASE_REPOSITORY}/releases/download/\$tag/install-plura-desktop-macos.sh"
/bin/sh -n "\$tmp/install.sh"
/bin/sh "\$tmp/install.sh"
if [ -t 0 ]; then
  echo
  echo "Update finished. Press Return to close."
  read answer
fi
EOF
  chmod 700 "$update_staging"
  mv -f "$update_staging" "$update_path"
fi

uninstall_path="$tools_dir/Uninstall Plura Desktop.command"
validate_replaceable_file "$uninstall_path"
uninstall_staging="$tools_dir/.uninstall.$$"
cat > "$uninstall_staging" <<EOF
#!/bin/sh
set -eu
META="${meta}"
DIST="${distribution}"
BIN="${bin_dir}"
CLI="${bin_dir}/plura-desktop"
TOOLS="${tools_dir}"
RECORD="${distribution}/install-record"

identity() {
  stat -f '%d:%i' "\$1"
}

record_value() {
  key=\$1
  awk -F= -v key="\$key" '\$1 == key {print substr(\$0, index(\$0, "=") + 1); exit}' "\$RECORD"
}

[ -f "\$RECORD" ] && [ ! -L "\$RECORD" ] || { echo "Refusing removal: installer ownership record is missing or unsafe." >&2; exit 1; }
[ "\$(record_value schema)" = "2" ] || { echo "Refusing removal: unsupported installer ownership record." >&2; exit 1; }
[ -d "${runtime}" ] && [ ! -L "${runtime}" ] || { echo "Refusing removal: CLI runtime is missing or replaced." >&2; exit 1; }
[ "\$(identity "${runtime}")" = "\$(record_value runtime_identity)" ] || { echo "Refusing removal: CLI runtime identity changed." >&2; exit 1; }
[ -f "\$CLI" ] && [ ! -L "\$CLI" ] || { echo "Refusing removal: CLI launcher is missing or replaced." >&2; exit 1; }
[ "\$(identity "\$CLI")" = "\$(record_value cli_identity)" ] || { echo "Refusing removal: CLI launcher identity changed." >&2; exit 1; }
[ -f "\$BIN/plura-desktop-diagnose" ] && [ ! -L "\$BIN/plura-desktop-diagnose" ] || { echo "Refusing removal: diagnostics launcher is missing or replaced." >&2; exit 1; }
[ "\$(identity "\$BIN/plura-desktop-diagnose")" = "\$(record_value diag_identity)" ] || { echo "Refusing removal: diagnostics launcher identity changed." >&2; exit 1; }
[ -f "\$TOOLS/Uninstall Plura Desktop.command" ] && [ ! -L "\$TOOLS/Uninstall Plura Desktop.command" ] || { echo "Refusing removal: uninstall tool is missing or replaced." >&2; exit 1; }
[ "\$(identity "\$TOOLS/Uninstall Plura Desktop.command")" = "\$(record_value uninstall_identity)" ] || { echo "Refusing removal: uninstall tool identity changed." >&2; exit 1; }
expected_update=\$(record_value update_identity)
if [ -n "\$expected_update" ]; then
  [ -f "\$TOOLS/Update Plura Desktop.command" ] && [ ! -L "\$TOOLS/Update Plura Desktop.command" ] || { echo "Refusing removal: update tool is missing or replaced." >&2; exit 1; }
  [ "\$(identity "\$TOOLS/Update Plura Desktop.command")" = "\$expected_update" ] || { echo "Refusing removal: update tool identity changed." >&2; exit 1; }
elif [ -e "\$TOOLS/Update Plura Desktop.command" ] || [ -L "\$TOOLS/Update Plura Desktop.command" ]; then
  echo "Refusing removal: unexpected update tool exists." >&2
  exit 1
fi
if [ "\$(record_value mode)" = "standalone" ]; then
  [ -f "${runtime}/plura-desktop" ] && [ ! -L "${runtime}/plura-desktop" ] || { echo "Refusing removal: standalone runtime is missing or replaced." >&2; exit 1; }
  [ "\$(identity "${runtime}/plura-desktop")" = "\$(record_value runtime_binary_identity)" ] || { echo "Refusing removal: standalone runtime identity changed." >&2; exit 1; }
  for entry in "${runtime}"/*; do
    [ -e "\$entry" ] || continue
    [ "\$(basename "\$entry")" = "plura-desktop" ] || { echo "Refusing removal: unexpected standalone runtime artifact: \$entry" >&2; exit 1; }
  done
fi

for entry in "\$DIST"/*; do
  [ -e "\$entry" ] || continue
  case "\$(basename "\$entry")" in cli-runtime|install-record) ;; *) echo "Refusing removal: unexpected distribution artifact: \$entry" >&2; exit 1 ;; esac
done
for entry in "\$BIN"/*; do
  [ -e "\$entry" ] || continue
  case "\$(basename "\$entry")" in plura-desktop|plura-desktop-diagnose) ;; *) echo "Refusing removal: unexpected CLI artifact: \$entry" >&2; exit 1 ;; esac
done
for entry in "\$TOOLS"/*; do
  [ -e "\$entry" ] || continue
  case "\$(basename "\$entry")" in 'Update Plura Desktop.command'|'Uninstall Plura Desktop.command') ;; *) echo "Refusing removal: unexpected tools artifact: \$entry" >&2; exit 1 ;; esac
done

[ -x "\$CLI" ] || { echo "Plura Desktop CLI runtime is missing or already removed." >&2; exit 1; }

profiles=""
for manifest in "\$META"/profile-*-install-manifest.json; do
  [ -f "\$manifest" ] || continue
  name=\$(basename "\$manifest")
  index=\${name#profile-}
  index=\${index%-install-manifest.json}
  case "\$index" in *[!0-9]*|'') continue ;; esac
  profiles="\$profiles \$index"
done

if [ -n "\$profiles" ]; then
  echo "The following managed profiles will be removed, including their isolated login/conversation state:"
  for index in \$profiles; do
    echo
    "\$CLI" uninstall --profile "\$index" || exit 1
  done
  echo
  printf "Type REMOVE to delete these managed profiles and the Plura Desktop CLI: "
  read answer
  [ "\$answer" = "REMOVE" ] || { echo "Cancelled."; exit 0; }
  for index in \$profiles; do
    "\$CLI" uninstall --profile "\$index" --yes || exit 1
  done
else
  printf "Type REMOVE to uninstall the Plura Desktop CLI: "
  read answer
  [ "\$answer" = "REMOVE" ] || { echo "Cancelled."; exit 0; }
fi

rm -rf "\$DIST" "\$BIN"
rm -rf "\$TOOLS"
rmdir "\$META" 2>/dev/null || true
echo "Plura Desktop was removed. The official ChatGPT app/default profile were not targeted."
EOF
chmod 700 "$uninstall_staging"
mv -f "$uninstall_staging" "$uninstall_path"

runtime_identity=$(identity "$runtime")
cli_identity=$(identity "$bin_dir/plura-desktop")
diag_identity=$(identity "$bin_dir/plura-desktop-diagnose")
uninstall_identity=$(identity "$uninstall_path")
update_identity=""
if [ -f "$tools_dir/Update Plura Desktop.command" ] && [ ! -L "$tools_dir/Update Plura Desktop.command" ]; then
  update_identity=$(identity "$tools_dir/Update Plura Desktop.command")
fi
runtime_binary_identity=""
if [ "$runtime_mode" = "standalone" ]; then
  runtime_binary_identity=$(identity "$runtime/plura-desktop")
fi
record_staging="$distribution/.install-record.$$"
cat > "$record_staging" <<EOF
schema=2
version=${installed_version}
mode=${runtime_mode}
runtime_identity=${runtime_identity}
runtime_binary_identity=${runtime_binary_identity}
cli_identity=${cli_identity}
diag_identity=${diag_identity}
update_identity=${update_identity}
uninstall_identity=${uninstall_identity}
EOF
chmod 600 "$record_staging"
mv -f "$record_staging" "$distribution/install-record"

cli="$bin_dir/plura-desktop"
result_message="Plura Desktop CLI runtime is ready."
if [ "$cli_only" -eq 0 ]; then
  manifests_found=0
  for manifest in "$meta"/profile-*-install-manifest.json; do
    [ -f "$manifest" ] || continue
    manifests_found=1
    name=$(basename "$manifest")
    index=${name#profile-}
    index=${index%-install-manifest.json}
    case "$index" in *[!0-9]*|'') continue ;; esac
    echo "Refreshing ChatGPT Profile $index..."
    "$cli" refresh --profile "$index" || fail "CLI updated, but Profile $index refresh failed. Existing profile data was preserved."
    result_message="Plura Desktop was updated and existing managed profiles were refreshed."
  done
  if [ "$manifests_found" -eq 0 ]; then
    if [ -x "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT" ]; then
      echo "Creating ChatGPT Profile 2..."
      "$cli" install --profile 2 || fail "CLI installed, but ChatGPT Profile 2 setup failed. Existing unrelated data was preserved."
      result_message="ChatGPT Profile 2 is ready in your Applications folder."
    else
      echo "Official ChatGPT.app was not found in /Applications; CLI installed without creating a managed profile."
      result_message="CLI installed. Install the official ChatGPT app, then run Profile setup."
    fi
  fi
fi

notify "Plura Desktop $installed_version installed" "$result_message Update/Uninstall tools are in ~/Applications/Plura Desktop Tools."
printf '%s\n' "CLI: $bin_dir/plura-desktop"
