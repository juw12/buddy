"""Write docs/latest.json (what Buddy's auto-updaters read) and docs/altstore.json (an AltStore / SideStore
source for the iPhone app) from the latest GitHub release.

    python tools/release_manifest.py                 # reads the GitHub API
    python tools/release_manifest.py --release r.json   # or a saved `gh api repos/juw12/buddy/releases/latest`

Only assets that really exist on the release (with GitHub's SHA-256 digest) end up in the manifest, so an
updater can never be sent to a missing or unverified file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import buddy_updater  # noqa: E402  (same network routes as the app)

REPO = "juw12/buddy"
ASSETS = {"windows": "Buddy-win64.zip", "mac": "Buddy-mac.zip", "ios": "Buddy-iOS.ipa"}
SITE = "https://juw12.github.io/buddy/"


def release_notes(body: str) -> list[str]:
    notes = []
    for line in (body or "").splitlines():
        line = line.strip()
        if line.startswith(("-", "*")):
            text = re.sub(r"[*_`]", "", line.lstrip("-* ")).strip()
            if text:
                notes.append(text)
    return notes[:8]


def build_manifest(release: dict) -> dict:
    version = str(release["tag_name"]).lstrip("vV")
    published = (release.get("published_at") or datetime.now(timezone.utc).isoformat())[:10]
    manifest = {"version": version, "date": published, "notes": release_notes(release.get("body", "")),
                "page": release.get("html_url") or f"https://github.com/{REPO}/releases/latest"}
    by_name = {asset["name"]: asset for asset in release.get("assets") or []}
    for platform, name in ASSETS.items():
        asset = by_name.get(name)
        if not asset:
            continue
        digest = str(asset.get("digest") or "")
        if not digest.startswith("sha256:"):
            continue                                    # unverifiable: leave it out
        manifest[platform] = {"version": version, "url": asset["browser_download_url"],
                              "size": asset["size"], "sha256": digest.split(":", 1)[1]}
    return manifest


def build_altstore(manifest: dict, release: dict) -> dict:
    ios = manifest.get("ios")
    versions = []
    if ios:
        versions.append({
            "version": ios["version"],
            "date": manifest["date"],
            "localizedDescription": "\n".join(f"\u2022 {note}" for note in manifest["notes"]) or "New version.",
            "downloadURL": ios["url"],
            "size": ios["size"],
            "minOSVersion": "17.0",
        })
    return {
        "name": "Buddy",
        "identifier": "com.juw12.buddy.source",
        "subtitle": "Buddy, your AI helper in the Dynamic Island",
        "website": SITE,
        "iconURL": SITE + "island/icons/icon-512.png",
        "tintColor": "#FF8424",
        "apps": [{
            "name": "Buddy",
            "bundleIdentifier": "com.juw12.buddy",
            "developerName": "Juwen Lie",
            "subtitle": "Ask anything from the Dynamic Island",
            "localizedDescription": (
                "Buddy lives in your Dynamic Island. Tap it to ask, or double-tap the back of your iPhone to send "
                "a screenshot - the answer appears right in the island while you stay in your app. Free brain, "
                "no account."),
            "iconURL": SITE + "island/icons/icon-512.png",
            "tintColor": "#FF8424",
            "category": "utilities",
            "screenshots": [SITE + "media/iphone-island-answer.png", SITE + "media/iphone-app.png"],
            "versions": versions,
            "appPermissions": {
                "entitlements": [],
                "privacy": {
                    "NSPhotoLibraryUsageDescription":
                        "Buddy looks at your latest screenshot only when you ask about it.",
                },
            },
        }],
        "news": [],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", help="saved release JSON instead of the GitHub API")
    parser.add_argument("--out", default=str(ROOT / "docs"))
    args = parser.parse_args(argv)
    if args.release:
        release = json.loads(Path(args.release).read_text(encoding="utf-8-sig"))
    else:
        release = buddy_updater._get_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    manifest = build_manifest(release)
    out = Path(args.out)
    (out / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "altstore.json").write_text(json.dumps(build_altstore(manifest, release), indent=2) + "\n",
                                       encoding="utf-8")
    platforms = [name for name in ASSETS if name in manifest]
    print(f"Buddy {manifest['version']}: {', '.join(platforms) or 'no verified downloads'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
