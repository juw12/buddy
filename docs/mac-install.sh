#!/bin/bash
# Buddy Lite for Mac: one-line install, free, no Apple developer account needed.
#
#   curl -fsSL https://juw12.github.io/buddy/mac-install.sh | bash
#
# Downloads the newest Buddy-mac.zip from GitHub, checks it, puts Buddy.app in /Applications (or
# ~/Applications if that isn't writable), clears the download quarantine so macOS opens it without the
# "unidentified developer" block, and starts it. Read it first if you like: it's short.
set -euo pipefail

# latest.json lists only files that exist, with their SHA-256. A release that is still building its Mac
# app keeps pointing at the previous one, so this never lands on a missing download.
MANIFEST="https://juw12.github.io/buddy/latest.json"
URL="https://github.com/juw12/buddy/releases/latest/download/Buddy-mac.zip"
SHA256=""
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

if curl -fsSL --max-time 20 "$MANIFEST" -o "$WORK/latest.json" 2>/dev/null; then
  if listed_url="$(plutil -extract mac.url raw -o - "$WORK/latest.json" 2>/dev/null)" && [ -n "$listed_url" ]; then
    URL="$listed_url"
    SHA256="$(plutil -extract mac.sha256 raw -o - "$WORK/latest.json" 2>/dev/null || true)"
  fi
fi

echo "Downloading Buddy Lite for Mac..."
curl -fL --progress-bar "$URL" -o "$WORK/Buddy-mac.zip"
if [ -n "$SHA256" ] && [ "$(shasum -a 256 "$WORK/Buddy-mac.zip" | cut -d' ' -f1)" != "$SHA256" ]; then
  echo "The download doesn't match its checksum. Nothing was installed; please try again." >&2
  exit 1
fi

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
