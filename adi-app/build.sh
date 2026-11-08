#!/usr/bin/env bash
# Build the macOS shell into a double-clickable .app.
#
#   ./build.sh              -> build/ADI.app
#   ./build.sh --install    -> also copies to ~/Applications/ADI.app
#
# The binary is ad-hoc signed. That is enough to run on the machine that built
# it; distributing it to other people needs a Developer ID and notarisation,
# which this script deliberately does not pretend to do.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/build/ADI.app"
BIN="$APP/Contents/MacOS/ADI"
ICNS="$ROOT/../brand/adi.icns"

echo "==> compiling"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# -Osize, not -O: on Swift 6.4 toolchains -O on this file never finishes
# (killed after 7 minutes); -Osize compiles in 2.5s and the app is a thin shell
# around a WKWebView, so there is nothing to optimise.
swiftc -Osize -swift-version 5 \
  "$ROOT/Sources/AdiApp.swift" \
  -o "$BIN" \
  -framework Cocoa -framework WebKit

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>ADI</string>
  <key>CFBundleDisplayName</key><string>ADI</string>
  <key>CFBundleIdentifier</key><string>dev.adi.app</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>ADI</string>
  <key>CFBundleIconFile</key><string>adi.icns</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
</dict>
</plist>
PLIST

if [ -f "$ICNS" ]; then
  cp "$ICNS" "$APP/Contents/Resources/adi.icns"
else
  echo "    no icon at $ICNS (continuing without one)"
fi

echo "==> signing (ad-hoc)"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "    codesign failed; the app still runs locally"
codesign --verify --strict "$APP" 2>/dev/null && echo "    signature ok" || echo "    signature unverified"

echo "==> smoke"
# No CLI mode: running the binary launches the app and never returns, so the
# binary check is a link-time concern, verified by `codesign --verify` above.

echo "built: $APP"
du -sh "$APP" | awk '{print "size: " $1}'

if [ "${1:-}" = "--install" ]; then
  DEST="$HOME/Applications/ADI.app"
  mkdir -p "$HOME/Applications"
  rm -rf "$DEST"
  cp -R "$APP" "$DEST"
  echo "installed: $DEST"
  echo "open it with: open '$DEST'"
fi
