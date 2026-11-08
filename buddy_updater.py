"""buddy_updater.py - Buddy for Windows keeps itself up to date.

The website publishes a small manifest (docs/latest.json):

    https://juw12.github.io/buddy/latest.json
    {"version": "1.1.0", "date": "2026-11-08", "notes": ["..."],
     "windows": {"url": ".../Buddy-win64.zip", "sha256": "<hex>", "size": 166274549},
     "mac": {...}, "ios": {...}}

How an update happens (only in the built Buddy.exe, never in a source checkout):

1. check()    - is the published version newer than the running one?
2. prepare()  - download the zip next to the install (<parent>/.buddy-update/), verify size + SHA-256,
                unpack it, check it really is Buddy <version>, then run the NEW Buddy.exe --selftest
                (with a throw-away settings folder). A build that fails its self-test is never installed.
3. apply()    - start a small hidden PowerShell helper and quit. The helper waits until Buddy has closed,
                copies your Pips data (focus history, pet, settings) into the new folder, swaps the folders
                (the old one is kept as "<install>.previous"), and starts the new Buddy. If anything goes
                wrong it puts the old folder back and starts that instead.

Your Buddy settings in %APPDATA%\\Buddy are never touched by an update.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger("buddy.updater")

MANIFEST_URL = "https://juw12.github.io/buddy/latest.json"
RELEASE_API = "https://api.github.com/repos/juw12/buddy/releases/latest"
USER_AGENT = "Buddy-Updater"
WINDOWS_ASSET = "Buddy-win64.zip"
# Pips keeps its data next to its code: these survive every update (relative to the install folder)
KEEP = ("app/pips/config.json", "app/pips/data", "app/pips/logs", "app/pips/output", "app/pips/backup",
        "app/pips/runtime", "app/pips/.release")
SELFTEST_TIMEOUT_S = 300
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


class UpdateError(Exception):
    pass


# ----------------------------------------------------------------------------- versions
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


# ----------------------------------------------------------------------------- network
def _openers():
    """Every route an office PC might need, in order: the environment proxy (or, without one, the Windows
    proxy), then the Windows Internet Options proxy explicitly (urllib skips it whenever any *_proxy /
    no_proxy variable exists), then a direct connection."""
    yield urllib.request.build_opener()
    try:
        registry = urllib.request.getproxies_registry() if os.name == "nt" else {}
    except Exception:
        registry = {}
    registry = {scheme: url for scheme, url in registry.items() if scheme in ("http", "https")}
    if registry and registry != {k: v for k, v in urllib.request.getproxies().items() if k in ("http", "https")}:
        yield urllib.request.build_opener(urllib.request.ProxyHandler(registry))
    yield urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(url: str, timeout: float):
    last_error = None
    for opener in _openers():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
            return opener.open(request, timeout=timeout)
        except Exception as error:          # try the next route
            last_error = error
    raise UpdateError(f"could not reach {url}: {last_error}")


def _get_json(url: str, timeout: float = 15.0) -> dict:
    if url.startswith("http"):                                  # skip caches between us and the site
        url = f"{url}{'&' if '?' in url else '?'}t={int(time.time())}"
    with _open(url, timeout) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def manifest_from_release(release: dict) -> dict:
    """GitHub's 'latest release' JSON -> the same shape as latest.json (fallback when the site is down)."""
    manifest = {"version": str(release.get("tag_name", "")).lstrip("vV"),
                "notes": [line.strip("-* ").strip() for line in (release.get("body") or "").splitlines()
                          if line.strip().startswith(("-", "*"))][:6]}
    for asset in release.get("assets") or []:
        if asset.get("name") == WINDOWS_ASSET:
            digest = str(asset.get("digest") or "")
            manifest["windows"] = {"url": asset.get("browser_download_url"), "size": asset.get("size"),
                                   "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else ""}
    return manifest


def fetch_manifest(url: str = MANIFEST_URL) -> dict:
    try:
        manifest = _get_json(url)
    except Exception as error:
        logger.info("Update manifest unavailable (%s) - asking GitHub", error)
        manifest = manifest_from_release(_get_json(RELEASE_API))
    if not manifest.get("version") or not (manifest.get("windows") or {}).get("url"):
        raise UpdateError("the update manifest has no Windows download")
    return manifest


def check(current_version: str, url: str = MANIFEST_URL) -> dict | None:
    """The manifest when a newer Buddy is published, else None."""
    manifest = fetch_manifest(url)
    return manifest if is_newer(manifest["version"], current_version) else None


# ----------------------------------------------------------------------------- install folder
def install_dir() -> Path | None:
    """The folder holding Buddy.exe, or None when running from source (updates are for the built app)."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def work_dir(install: Path) -> Path:
    """Next to the install (same drive, so the final swap is a quick rename)."""
    return install.parent / ".buddy-update"


def built_version(folder: Path) -> str:
    """BUDDY_VERSION written in <folder>/app/buddy_host.py."""
    text = (folder / "app" / "buddy_host.py").read_text(encoding="utf-8-sig", errors="replace")
    match = re.search(r'^BUDDY_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise UpdateError("the new build has no version number")
    return match.group(1)


# ----------------------------------------------------------------------------- prepare
def download(url: str, target: Path, expected_sha256: str = "", expected_size: int | None = None,
             progress=None) -> Path:
    """Stream the zip to disk while hashing it; refuse it if the size or SHA-256 don't match."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    received = 0
    last_report = 0.0
    with _open(url, timeout=60) as response, open(partial, "wb") as handle:
        total = int(response.headers.get("Content-Length") or expected_size or 0)
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            if progress and time.monotonic() - last_report > 1.0:
                last_report = time.monotonic()
                progress("download", received, total)
    if expected_size and received != int(expected_size):
        partial.unlink(missing_ok=True)
        raise UpdateError(f"download incomplete ({received} of {expected_size} bytes)")
    if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
        partial.unlink(missing_ok=True)
        raise UpdateError("the download failed its SHA-256 check - update cancelled")
    partial.replace(target)
    return target


def unpack(zip_path: Path, destination: Path) -> Path:
    """Extract safely (no paths outside destination); returns the folder that holds Buddy.exe."""
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if not (destination / member).resolve().is_relative_to(root):
                raise UpdateError("unsafe path inside the update archive")
        archive.extractall(destination)
    if (destination / "Buddy.exe").exists():
        return destination
    folders = [item for item in destination.iterdir() if item.is_dir() and (item / "Buddy.exe").exists()]
    if len(folders) == 1:
        return folders[0]
    raise UpdateError("the update archive has no Buddy.exe")


def selftest(folder: Path, timeout: float = SELFTEST_TIMEOUT_S) -> None:
    """Run the new Buddy.exe --selftest with a throw-away settings folder; raise if it fails."""
    with tempfile.TemporaryDirectory(prefix="buddy-update-selftest-") as home:
        env = dict(os.environ, KEYIT_HOME=home)
        try:
            result = subprocess.run([str(folder / "Buddy.exe"), "--selftest"], cwd=str(folder), env=env,
                                    timeout=timeout, capture_output=True,
                                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            raise UpdateError("the new version's self-test took too long") from None
    if result.returncode != 0:
        raise UpdateError(f"the new version failed its self-test (exit {result.returncode})")


def prepare(manifest: dict, install: Path, progress=None, run_selftest=True) -> Path:
    """Download + verify + unpack + self-test. Returns the ready-to-install folder."""
    version = manifest["version"]
    windows = manifest["windows"]
    work = work_dir(install)
    zip_path = work / f"Buddy-{version}.zip"
    if progress:
        progress("download", 0, int(windows.get("size") or 0))
    download(windows["url"], zip_path, windows.get("sha256", ""), windows.get("size"), progress)
    if progress:
        progress("unpack", 0, 0)
    ready = unpack(zip_path, work / version)
    found = built_version(ready)
    if parse_version(found) != parse_version(version):
        raise UpdateError(f"the download is Buddy {found}, expected {version}")
    if run_selftest:
        if progress:
            progress("selftest", 0, 0)
        selftest(ready)
        # the self-test may have created fresh Pips data in the new folder: the user's data replaces it
        for relative in KEEP:
            target = ready / relative
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
    zip_path.unlink(missing_ok=True)
    return ready


# ----------------------------------------------------------------------------- apply
HELPER = r"""
$ErrorActionPreference = 'Stop'
$WaitPid = __PID__
$Install = '__INSTALL__'
$New = '__NEW__'
$Version = '__VERSION__'
$Log = '__LOG__'
$Keep = @(__KEEP__)
$Previous = "$Install.previous"
function Note($text) { Add-Content -LiteralPath $Log -Value ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $text) }
function StartBuddy($folder) { Start-Process -FilePath (Join-Path $folder 'Buddy.exe') -WorkingDirectory $folder }
if (-not (Test-Path -LiteralPath (Join-Path $New 'Buddy.exe'))) {
    Note "update to ${Version} skipped - the new version is not there (already installed?); nothing was touched"
    exit 0
}
Note "update to ${Version} - waiting for Buddy (pid ${WaitPid}) to close"
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Milliseconds 300 }
if (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) { Note 'Buddy did not close in time - stopping it'; Stop-Process -Id $WaitPid -Force }
Get-Process -Name Buddy -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Install + '\', 'OrdinalIgnoreCase') } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
try {
    foreach ($item in $Keep) {
        $from = Join-Path $Install $item
        $to = Join-Path $New $item
        if (Test-Path -LiteralPath $from) {
            if (Test-Path -LiteralPath $to) { Remove-Item -LiteralPath $to -Recurse -Force }
            New-Item -ItemType Directory -Force -Path (Split-Path $to -Parent) | Out-Null
            Copy-Item -LiteralPath $from -Destination $to -Recurse -Force
        }
    }
    Note 'kept the Pips data'
    if (Test-Path -LiteralPath $Previous) { Remove-Item -LiteralPath $Previous -Recurse -Force }
    $swapped = $false
    for ($try = 0; $try -lt 10 -and -not $swapped; $try++) {
        try { Move-Item -LiteralPath $Install -Destination $Previous; $swapped = $true }
        catch { Start-Sleep -Seconds 1 }
    }
    if ($swapped) {
        Move-Item -LiteralPath $New -Destination $Install
        Note 'swapped folders'
    } else {
        Note 'the folder is in use - copying the new files over it instead'
        robocopy $Install $Previous /E /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
        robocopy $New $Install /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
        Remove-Item -LiteralPath $New -Recurse -Force -ErrorAction SilentlyContinue
    }
    StartBuddy $Install
    Note "started Buddy $Version"
} catch {
    Note ("update failed: " + $_.Exception.Message)
    if (-not (Test-Path -LiteralPath (Join-Path $Install 'Buddy.exe')) -and (Test-Path -LiteralPath $Previous)) {
        if (Test-Path -LiteralPath $Install) { Remove-Item -LiteralPath $Install -Recurse -Force -ErrorAction SilentlyContinue }
        Move-Item -LiteralPath $Previous -Destination $Install
        Note 'put the old version back'
    }
    StartBuddy $Install
}
"""


def _ps_quote(text) -> str:
    return str(text).replace("'", "''")


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input (Windows); 0 elsewhere."""
    if os.name != "nt":
        return 0.0
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000.0


def helper_script(pid: int, install: Path, ready: Path, version: str, log: Path) -> str:
    keep = ", ".join(f"'{_ps_quote(item.replace('/', chr(92)))}'" for item in KEEP)
    return (HELPER.replace("__PID__", str(int(pid)))
            .replace("__INSTALL__", _ps_quote(install))
            .replace("__NEW__", _ps_quote(ready))
            .replace("__VERSION__", _ps_quote(version))
            .replace("__LOG__", _ps_quote(log))
            .replace("__KEEP__", keep))


def launch_hidden(args: list[str], cwd: Path | None = None) -> subprocess.Popen:
    """Start a hidden helper that outlives Buddy.

    No DETACHED_PROCESS: on some PCs a console program started without any console never runs at all
    (seen live: the swap helper silently did nothing). A hidden console (CREATE_NO_WINDOW) always works.
    It also tries to leave Buddy's job object, so a launcher that cleans up its job can't take the helper down.
    """
    base = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    last_error = None
    for flags in (base | CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            return subprocess.Popen(args, creationflags=flags, close_fds=True, cwd=str(cwd) if cwd else None,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as error:                        # the job does not allow breakaway: stay in it
            last_error = error
    raise UpdateError(f"could not start the update helper: {last_error}")


def apply(install: Path, ready: Path, version: str, log: Path | None = None) -> None:
    """Start the hidden helper that swaps the folders once this Buddy has quit. The caller must quit next."""
    if not (Path(ready) / "Buddy.exe").exists():
        raise UpdateError(f"nothing to install: {ready} has no Buddy.exe")
    log = log or (work_dir(install) / "update.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    script = helper_script(os.getpid(), install, ready, version, log)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")    # no .ps1 file: not blocked by policy
    launch_hidden(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                   "-EncodedCommand", encoded], cwd=install.parent)
    logger.info("Update helper started: %s -> %s (Buddy %s)", ready, install, version)


def cleanup(install: Path) -> None:
    """After a successful update: drop leftovers of older downloads (keeps update.log and .previous)."""
    work = work_dir(install)
    if not work.exists():
        return
    for item in work.iterdir():
        if item.name == "update.log":
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)
