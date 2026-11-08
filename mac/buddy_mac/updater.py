"""Buddy Lite for Mac keeps itself up to date (no PyObjC here, so it is unit-tested anywhere).

Same manifest as Windows (https://juw12.github.io/buddy/latest.json, "mac" entry). When a newer version is
published: download Buddy-mac.zip, check its size + SHA-256, unpack it with ditto, check that it really is
Buddy <version>, then a tiny shell helper waits for this Buddy to quit, swaps Buddy.app (the old one goes to
the Trash-like "Buddy.previous.app" next to it), clears the download quarantine flag and opens the new one.
Settings in ~/Library/Application Support/Buddy are never touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

MANIFEST_URL = "https://juw12.github.io/buddy/latest.json"
USER_AGENT = "Buddy-Mac-Updater"


class UpdateError(Exception):
    pass


def parse_version(text) -> tuple:
    parts = []
    for piece in str(text or "0").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote, local) -> bool:
    return parse_version(remote) > parse_version(local)


def _openers():
    yield urllib.request.build_opener()
    yield urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(url: str, timeout: float):
    last = None
    for opener in _openers():
        try:
            return opener.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)
        except Exception as error:                      # noqa: BLE001 - try the next route
            last = error
    raise UpdateError(f"could not reach {url}: {last}")


def fetch_manifest(url: str = MANIFEST_URL) -> dict:
    if url.startswith("http"):
        url = f"{url}{'&' if '?' in url else '?'}t={int(time.time())}"
    with _open(url, 15) as response:
        manifest = json.loads(response.read().decode("utf-8-sig"))
    if not (manifest.get("mac") or {}).get("url"):
        raise UpdateError("the update manifest has no Mac download")
    return manifest


def check(current_version: str, url: str = MANIFEST_URL) -> dict | None:
    manifest = fetch_manifest(url)
    version = manifest["mac"].get("version") or manifest.get("version")
    return manifest if is_newer(version, current_version) else None


def app_bundle(executable: str | None = None) -> Path | None:
    """/Applications/Buddy.app when running from a built app, None from source."""
    if not getattr(sys, "frozen", False) and executable is None:
        return None
    path = Path(executable or sys.executable).resolve()
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return None


def bundle_version(app: Path) -> str:
    with open(app / "Contents" / "Info.plist", "rb") as handle:
        return str(plistlib.load(handle).get("CFBundleShortVersionString", ""))


def download(url: str, target: Path, sha256: str = "", size: int | None = None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    received = 0
    with _open(url, 60) as response, open(target, "wb") as handle:
        while chunk := response.read(256 * 1024):
            handle.write(chunk)
            digest.update(chunk)
            received += len(chunk)
    if size and received != int(size):
        target.unlink(missing_ok=True)
        raise UpdateError(f"download incomplete ({received} of {size} bytes)")
    if sha256 and digest.hexdigest().lower() != sha256.lower():
        target.unlink(missing_ok=True)
        raise UpdateError("the download failed its SHA-256 check")
    return target


def prepare(manifest: dict, work: Path | None = None) -> Path:
    """Download + verify + unpack. Returns the new Buddy.app (in a temporary folder)."""
    mac = manifest["mac"]
    version = mac.get("version") or manifest["version"]
    work = Path(work or tempfile.mkdtemp(prefix="buddy-update-"))
    zip_path = download(mac["url"], work / "Buddy-mac.zip", mac.get("sha256", ""), mac.get("size"))
    unpacked = work / "new"
    unpacked.mkdir(exist_ok=True)
    subprocess.run(["ditto", "-x", "-k", str(zip_path), str(unpacked)], check=True)
    apps = list(unpacked.glob("*.app"))
    if len(apps) != 1:
        raise UpdateError("the update has no Buddy.app")
    found = bundle_version(apps[0])
    if parse_version(found) != parse_version(version):
        raise UpdateError(f"the download is Buddy {found}, expected {version}")
    return apps[0]


def helper_script(pid: int, current: Path, new: Path, log: Path) -> str:
    q = shlex.quote
    previous = current.with_name(current.stem + ".previous.app")
    return "\n".join([
        "#!/bin/sh",
        f"exec >>{q(str(log))} 2>&1",
        f"echo \"$(date) update: waiting for Buddy ({pid})\"",
        f"while kill -0 {int(pid)} 2>/dev/null; do sleep 0.3; done",
        f"rm -rf {q(str(previous))}",
        f"if mv {q(str(current))} {q(str(previous))} && ditto {q(str(new))} {q(str(current))}; then",
        f"  xattr -dr com.apple.quarantine {q(str(current))} 2>/dev/null",
        "  echo \"$(date) update: swapped\"",
        "else",
        "  echo \"$(date) update failed - putting the old version back\"",
        f"  rm -rf {q(str(current))}; mv {q(str(previous))} {q(str(current))}",
        "fi",
        f"open {q(str(current))}",
    ]) + "\n"


def apply(current: Path, new: Path, log: Path) -> None:
    """Start the helper; the caller quits right after."""
    script = Path(tempfile.mkdtemp(prefix="buddy-swap-")) / "swap.sh"
    script.write_text(helper_script(os.getpid(), current, new, log), encoding="utf-8")
    script.chmod(0o755)
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True)


def idle_seconds() -> float:
    try:
        import Quartz

        return float(Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGAnyInputEventType))
    except Exception:                                   # noqa: BLE001
        return 1e9


def cleanup(current: Path) -> None:
    previous = current.with_name(current.stem + ".previous.app")
    shutil.rmtree(previous, ignore_errors=True)
