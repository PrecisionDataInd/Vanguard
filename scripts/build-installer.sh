#!/usr/bin/env bash
# Builds the Windows installer on Linux without Wine: electron-builder packages
# the app, then NSIS (downloaded by electron-builder) wraps it.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(node -p "require('./package.json').version")
rm -rf dist
npx electron-builder --win dir --x64 --publish never -c.win.signAndEditExecutable=false
NS=$(ls -d "$HOME"/.cache/electron-builder/nsis/nsis-3*/ 2>/dev/null | head -1 || true)
if [ -z "$NS" ]; then
  # Triggers the NSIS download; the build itself stops at the Wine check.
  npx electron-builder --win nsis --x64 --publish never --prepackaged dist/win-unpacked -c.win.signAndEditExecutable=false || true
  NS=$(ls -d "$HOME"/.cache/electron-builder/nsis/nsis-3*/ | head -1)
fi
cp build/icon.ico dist/win-unpacked/resources/icon.ico
rm -f dist/*.exe
NSISDIR="$NS" "$NS/linux/makensis" -V2 -DVERSION="$VERSION" -DSRC="$PWD/dist/win-unpacked" \
  -DOUT="$PWD/dist/Vanguard-Setup-$VERSION.exe" build/installer.nsi
ls -la dist/*.exe
