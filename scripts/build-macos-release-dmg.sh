#!/bin/sh
set -eu

usage() {
  echo "Usage: $0 WHEEL RENDERED_INSTALLER STANDALONE_ARM64 STANDALONE_X86_64 OUTPUT_DMG VERSION" >&2
  exit 2
}

[ "$#" -eq 6 ] || usage
wheel=$1
installer=$2
standalone_arm64=$3
standalone_x86_64=$4
output=$5
version=$6

[ "$(uname -s)" = "Darwin" ] || { echo "Error: DMG builds require macOS." >&2; exit 2; }
[ -f "$wheel" ] || { echo "Error: wheel not found: $wheel" >&2; exit 2; }
[ -f "$installer" ] || { echo "Error: installer not found: $installer" >&2; exit 2; }
[ -f "$standalone_arm64" ] || { echo "Error: arm64 standalone runtime not found: $standalone_arm64" >&2; exit 2; }
[ -f "$standalone_x86_64" ] || { echo "Error: x86_64 standalone runtime not found: $standalone_x86_64" >&2; exit 2; }

tmp=$(mktemp -d "${TMPDIR:-/tmp}/plura-desktop-dmg.XXXXXX")
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT HUP INT TERM

app="$tmp/Install Plura Desktop.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$wheel" "$app/Contents/Resources/"
cp "$installer" "$app/Contents/Resources/install-plura-desktop-macos.sh"
cp "$standalone_arm64" "$app/Contents/Resources/plura-desktop-macos-arm64"
cp "$standalone_x86_64" "$app/Contents/Resources/plura-desktop-macos-x86_64"
chmod 700 "$app/Contents/Resources/install-plura-desktop-macos.sh"
chmod 755 "$app/Contents/Resources/plura-desktop-macos-arm64" "$app/Contents/Resources/plura-desktop-macos-x86_64"

cat > "$tmp/InstallerMain.swift" <<'EOF'
import Foundation

guard let resources = Bundle.main.resourceURL else {
    exit(2)
}

let installer = resources.appendingPathComponent("install-plura-desktop-macos.sh")
let process = Process()
process.executableURL = URL(fileURLWithPath: "/bin/sh")
process.arguments = [installer.path, "--gui"]

do {
    try process.run()
    process.waitUntilExit()
    exit(process.terminationStatus)
} catch {
    exit(2)
}
EOF

xcrun swiftc -O -target arm64-apple-macos14.0 "$tmp/InstallerMain.swift" -o "$tmp/installer-arm64"
xcrun swiftc -O -target x86_64-apple-macos14.0 "$tmp/InstallerMain.swift" -o "$tmp/installer-x86_64"
lipo -create "$tmp/installer-arm64" "$tmp/installer-x86_64" -output "$app/Contents/MacOS/Install Plura Desktop"
chmod 755 "$app/Contents/MacOS/Install Plura Desktop"

cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>Install Plura Desktop</string>
  <key>CFBundleExecutable</key><string>Install Plura Desktop</string>
  <key>CFBundleIdentifier</key><string>local.plura-desktop.installer</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Install Plura Desktop</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${version}</string>
  <key>CFBundleVersion</key><string>${version}</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
EOF

codesign --verify --strict --verbose=2 "$app/Contents/Resources/plura-desktop-macos-arm64"
codesign --verify --strict --verbose=2 "$app/Contents/Resources/plura-desktop-macos-x86_64"

if [ -n "${PLURA_CODESIGN_IDENTITY:-}" ]; then
  codesign --force --options runtime --timestamp --sign "$PLURA_CODESIGN_IDENTITY" "$app"
  codesign --verify --deep --strict --verbose=2 "$app"
else
  codesign --force --sign - --timestamp=none "$app"
fi

stage="$tmp/dmg"
mkdir -p "$stage"
cp -R "$app" "$stage/"
cat > "$stage/README.txt" <<EOF
Plura Desktop ${version}

1. Open "Install Plura Desktop.app".
2. The installer selects the bundled standalone runtime for this Mac and creates ChatGPT Profile 2.
3. Sign in normally in ChatGPT Profile 2.

No separate Python installation is required for the DMG path. The official ChatGPT.app is not modified or removed.
EOF

mkdir -p "$(dirname "$output")"
hdiutil create -quiet -volname "Plura Desktop ${version}" -srcfolder "$stage" -ov -format UDZO "$output"

echo "$output"
