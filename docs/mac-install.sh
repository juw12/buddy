#!/bin/bash
# Buddy Lite for Mac: one-line install, free, no Apple developer account needed.
#
#   curl -fsSL https://juw12.github.io/buddy/mac-install.sh | bash
#
# Downloads the latest Buddy-mac.zip from GitHub, puts Buddy.app in /Applications (or ~/Applications
# if that isn't writable), clears the download quarantine so macOS opens it without the
# "unidentified developer" block, and starts it. Read it first if you like: it's 40 lines.
set -euo pipefail

URL="https://github.com/juw12/buddy/releases/latest/download/Buddy-mac.zip"
APP_NAME="Buddy.app"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This installer is for macOS. On Windows, download Buddy-win64.zip from https://juw12.github.io/buddy/" >&2
  exit 1
fi

DEST="/Applications"
if [ ! -w "$DEST" ]; then
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Downloading Buddy Lite for Mac..."
curl -fL --progress-bar "$URL" -o "$WORK/Buddy-mac.zip"

echo "Installing to $DEST..."
ditto -x -k "$WORK/Buddy-mac.zip" "$WORK"
pkill -x Buddy 2>/dev/null || true
rm -rf "$DEST/$APP_NAME"
ditto "$WORK/$APP_NAME" "$DEST/$APP_NAME"
xattr -dr com.apple.quarantine "$DEST/$APP_NAME" 2>/dev/null || true

echo "Starting Buddy..."
open "$DEST/$APP_NAME"
echo
echo "Done. Buddy is in your menu bar. Hold Control+Option+Space and ask, or tap it to type."
echo "macOS will ask for Screen Recording (to see your screen) and Microphone (to hear you)."
echo "Questions or bugs: https://github.com/juw12/buddy/issues/new?template=feedback.yml"
