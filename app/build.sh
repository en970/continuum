#!/bin/sh
# Builds Continuum.app and installs it into /Applications.
# Usage: ./build.sh
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
APP="${CONTINUUM_APP_DIR:-/Applications}/Continuum.app"
BUILD="$HERE/.build"
BIN="$BUILD/Continuum"

if ! command -v swiftc >/dev/null; then
  echo "swiftc not found — install Xcode or the Command Line Tools." >&2
  exit 1
fi

echo "generating icon..."
mkdir -p "$BUILD"
swift "$HERE/makeicon.swift" "$BUILD/icon1024.png" >/dev/null

ICONSET="$BUILD/Continuum.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$BUILD/icon1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$BUILD/icon1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD/Continuum.icns"

echo "compiling..."
ARCH=$(uname -m)
swiftc -O -parse-as-library \
  -target "${ARCH}-apple-macos14.0" \
  "$HERE/ContinuumApp.swift" \
  -o "$BIN"

echo "packaging -> $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Continuum"
cp "$BUILD/Continuum.icns" "$APP/Contents/Resources/Continuum.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Continuum</string>
    <key>CFBundleDisplayName</key><string>Continuum</string>
    <key>CFBundleIdentifier</key><string>com.continuum.app</string>
    <key>CFBundleExecutable</key><string>Continuum</string>
    <key>CFBundleIconFile</key><string>Continuum</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# Ad-hoc signature so Gatekeeper does not complain about an unsigned binary
codesign --force --deep --sign - "$APP" 2>/dev/null || true

echo "done: $APP"
echo "Open it with Spotlight (Cmd+Space) and type 'Continuum'."
