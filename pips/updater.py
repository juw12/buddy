"""
updater.py - in-app updates for Pips.

The published site hosts a small manifest next to the download:

    https://juw12.github.io/projects/pips/download/latest.json
    {
      "version": "2.1.0",
      "date": "2026-10-28",
      "url": "https://juw12.github.io/projects/pips/download/Pips-Windows.zip",
      "sha256": "<hex>",
      "size": 199999,
      "notes": {"en": ["..."], "id": ["..."], "zh_TW": ["..."], "zh_CN": ["..."]},
      "guide": "https://juw12.github.io/projects/pips/download/Pips-Guide.pptx"
    }

`check()` compares it with the running version. `install()` downloads the zip,
verifies the SHA-256, backs up the current program files to backup/<version>/,
copies the new files over the app folder (config.json, data/, logs/ and backup/
are never touched) and `restart()` relaunches Pips.

Networking uses the Windows proxy settings first and falls back to a direct
connection, so it works on most office networks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger("updater")

MANIFEST_URL = "https://juw12.github.io/projects/pips/download/latest.json"
PROTECTED = {"config.json", "data", "logs", "backup", "output", "__pycache__", ".release", "runtime"}
USER_AGENT = "Pips-Updater"


def parse_version(text) -> tuple:
    parts = []
    for piece in str(text or "0").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _openers():
    """Default opener (env / Windows proxy) first, then a direct connection."""
    yield urllib.request.build_opener()
    yield urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str, timeout: float = 15.0) -> bytes:
    last_error = None
    for opener in _openers():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
            with opener.open(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # try the next route
            last_error = exc
    raise ConnectionError(f"Could not reach {url}: {last_error}")


def fetch_manifest(url: str = MANIFEST_URL, timeout: float = 10.0) -> dict:
    sep = "&" if "?" in url else "?"
    data = _get(f"{url}{sep}t={int(time.time())}", timeout=timeout)
    manifest = json.loads(data.decode("utf-8-sig"))
    if not manifest.get("version") or not manifest.get("url"):
        raise ValueError("update manifest is missing version/url")
    return manifest


def check(current_version: str, url: str = MANIFEST_URL) -> dict | None:
    """Return the manifest when a newer version is published, else None."""
    manifest = fetch_manifest(url)
    if is_newer(manifest["version"], current_version):
        return manifest
    return None


def notes_for(manifest: dict, lang: str) -> list:
    notes = manifest.get("notes") or {}
    if isinstance(notes, list):
        return notes
    return notes.get(lang) or notes.get("en") or []


def install(manifest: dict, app_dir, current_version: str, progress=None) -> str:
    """Download, verify and install an update. Returns the backup folder path."""
    app_dir = Path(app_dir)
    if (app_dir / "release.py").exists():
        # the developer's source folder (release.py is never shipped): installing the public
        # zip here would overwrite dev-only settings - publish with release.py instead
        raise RuntimeError("this is the Pips developer copy - it is updated with release.py, not the in-app updater")

    def report(stage):
        if progress:
            try:
                progress(stage)
            except Exception:
                pass

    report("download")
    payload = _get(manifest["url"], timeout=60)
    expected = (manifest.get("sha256") or "").lower()
    actual = hashlib.sha256(payload).hexdigest()
    if expected and actual != expected:
        raise ValueError("downloaded file failed its checksum - update cancelled")

    report("install")
    with tempfile.TemporaryDirectory(prefix="pips-update-") as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "update.zip"
        zip_path.write_bytes(payload)
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.namelist():
                target = (tmp_path / "x" / member).resolve()
                if not target.is_relative_to((tmp_path / "x").resolve()):
                    raise ValueError("unsafe path inside update archive")
            archive.extractall(tmp_path / "x")
        source = tmp_path / "x"
        entries = [p for p in source.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            source = entries[0]  # the zip contains a single top folder ("Pips/")
        if not (source / "app.py").exists():
            raise ValueError("update archive does not contain app.py")

        backup = app_dir / "backup" / f"v{current_version}-{time.strftime('%Y%m%d-%H%M%S')}"
        for item in source.rglob("*"):
            rel = item.relative_to(source)
            top_name = rel.parts[0].lower()
            if top_name in PROTECTED or top_name.startswith("unins"):
                continue
            dest = app_dir / rel
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            if dest.exists():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup / rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
    report("done")
    logger.info("Updated to %s (backup: %s)", manifest.get("version"), backup)
    return str(backup)


def restart(app_dir, delay_s: float = 1.5):
    """Relaunch Pips (pythonw, no console) after a short delay."""
    app_dir = Path(app_dir)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = str(pythonw if pythonw.exists() else exe)
    script = (
        "import time,subprocess,sys;"
        f"time.sleep({delay_s});"
        f"subprocess.Popen([{runner!r}, {str(app_dir / 'app.py')!r}], cwd={str(app_dir)!r})"
    )
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([runner, "-c", script], cwd=str(app_dir), creationflags=flags, close_fds=True)
