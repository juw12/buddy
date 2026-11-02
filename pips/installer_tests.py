"""Clean-room tests for the exact public Pips installer.

/PIPSTEST=1 uses sandbox shortcuts and no uninstall registry key. The very same
installer still extracts all files and creates its real native uninstaller.
No user Desktop/Start Menu entries or live Pips data are touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_installer(output: Path, manifest: dict) -> dict:
    output = Path(output).resolve()
    setup = output / "Pips-Setup.exe"
    installer_hash = sha256(setup)
    if installer_hash != manifest["installer"]["sha256"]:
        raise AssertionError("Installer does not match the public manifest")
    if setup.read_bytes()[:2] != b"MZ":
        raise AssertionError("Installer is not a Windows executable")

    test_root = Path(os.environ.get("PIPS_TEST_ROOT", output / "installer-tests")).resolve()
    test_root.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix="qa-", dir=test_root))
    app_dir = session / "Pips Test 测试"
    temporary = session / "temp"
    temporary.mkdir()
    report = {
        "passed": False,
        "version": manifest["version"],
        "installer_sha256": installer_hash,
        "checks": [],
        "sandbox": str(app_dir),
        "limitation": "Host Windows sandbox, not a separate clean Windows virtual machine.",
    }

    environment = dict(os.environ)
    windows = Path(environment.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join([str(windows / "System32"), str(windows)])
    environment["TEMP"] = str(temporary)
    environment["TMP"] = str(temporary)
    environment["PYTHONHOME"] = str(session / "nonexistent-host-python")
    environment["PYTHONPATH"] = str(session / "nonexistent-host-packages")

    def checked(name, command, timeout=150):
        result = subprocess.run(
            [str(part) for part in command],
            cwd=session,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000,
            timeout=timeout,
        )
        (session / f"{name}.log").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        if result.returncode:
            raise AssertionError(
                f"{name} failed ({result.returncode}):\n"
                f"{result.stdout[-3500:]}\n{result.stderr[-3500:]}"
            )
        return result.stdout

    def install(name):
        log_path = session / f"{name}-native.log"
        checked(name, [
            setup,
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/SP-",
            "/PIPSTEST=1",
            f"/DIR={app_dir}",
            f"/LOG={log_path}",
        ])
        log = log_path.read_text(encoding="utf-8-sig", errors="replace")
        if "Administrative install mode: No" not in log:
            raise AssertionError("Setup did not confirm per-user/non-admin installation mode")

    def runtime(name, code=None, args=None, timeout=120):
        command = [app_dir / "runtime" / "python.exe", "-X", "utf8", "-B"]
        if code is not None:
            command += ["-c", code]
        else:
            command += args or []
        return checked(name, command, timeout=timeout)

    install("fresh-install")
    for private in ("config.json", "data", "logs", "backup", ".release"):
        if (app_dir / private).exists():
            raise AssertionError(f"Private developer content was packaged: {private}")
    report["checks"].append("Fresh per-user install to a Unicode path with spaces; no private user files")

    smoke = r'''
import json
import sys
import ssl
import sqlite3
import tkinter as tk
from pathlib import Path
from PIL import Image, ImageTk
import PIL
import requests
import win32api
import win32gui
import win32process
import pythoncom
import win32com.client

root_dir = Path(sys.argv[1]).resolve()
assert sys.flags.isolated, sys.flags
assert sys.flags.ignore_environment, sys.flags
for entry in sys.path:
    assert Path(entry).resolve().is_relative_to(root_dir), entry
root = tk.Tk()
root.withdraw()
photo = ImageTk.PhotoImage(Image.new("RGB", (8, 8), "orange"), master=root)
root.update_idletasks()
del photo
root.destroy()
sqlite3.connect(":memory:").execute("select 1").fetchone()
ssl.create_default_context()
win32com.client.Dispatch("WScript.Shell")
from win32com.shell import shell
# Use the native Unicode IShellLink API. WScript's legacy shortcut loader can
# return empty values for Unicode link filenames even when the link is valid.
for folder in ("Desktop", "Start Menu"):
    path = root_dir / ".installer-test" / folder / "Pips.lnk"
    shortcut = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )
    shortcut.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path), 0)
    assert Path(shortcut.GetPath(0)[0]).resolve() == root_dir / "runtime" / "pythonw.exe"
    assert shortcut.GetArguments() == '"' + str(root_dir / "app.py") + '"'
    assert Path(shortcut.GetWorkingDirectory()).resolve() == root_dir
    assert Path(shortcut.GetIconLocation()[0]).resolve() == root_dir / "assets" / "widget.ico"
print(json.dumps({"python": sys.version.split()[0], "pillow": PIL.__version__,
                  "requests": requests.__version__, "isolated": bool(sys.flags.isolated),
                  "tk": tk.TkVersion, "shortcut_count": 2}))
'''
    smoke_result = checked("runtime-and-shortcuts", [
        app_dir / "runtime" / "python.exe", "-X", "utf8", "-B",
        "-c", smoke, app_dir,
    ])
    report["runtime"] = json.loads(smoke_result.strip())
    report["checks"].append("Bundled Tk/Pillow/pywin32/COM/SSL/SQLite/HTTP work without host Python or PATH")
    report["checks"].append("Desktop and Start Menu shortcuts target private pythonw with quoted app paths")

    version = runtime("version", args=[app_dir / "app.py", "--version"])
    if manifest["version"] not in version:
        raise AssertionError("Installed version is wrong")
    runtime("selftest", args=[app_dir / "app.py", "--selftest", "--no-track"])
    report["checks"].append("Installed app --version and graphical --selftest pass")

    fixtures = {
        "config.json": json.dumps({
            "language": "en",
            "pet": {"name": "Keep this pet", "auto_update": False},
        }, ensure_ascii=False).encode("utf-8"),
        "data/installer-preserve.bin": b"pet-progress-test\x00\x01",
        "logs/installer-preserve.txt": b"keep existing logs\n",
        "backup/installer-preserve.txt": b"keep existing backups\n",
        "output/installer-preserve.txt": b"keep existing output\n",
    }
    for relative, contents in fixtures.items():
        path = app_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    database = app_dir / "data" / "productivity.db"
    database_before = sha256(database)
    app_before = sha256(app_dir / "app.py")
    runtime_dll = app_dir / "runtime" / "python313.dll"
    runtime_before = sha256(runtime_dll)
    with (app_dir / "app.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# temporary repair test\n")
    install("reinstall")
    assert sha256(app_dir / "app.py") == app_before
    assert sha256(runtime_dll) == runtime_before
    assert sha256(database) == database_before
    for relative, contents in fixtures.items():
        assert (app_dir / relative).read_bytes() == contents, relative
    report["checks"].append("Reinstall repairs program files and preserves config, database, progress, logs and backups")

    update_test = r'''
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
import updater

app_dir = Path(sys.argv[1])
zip_path = Path(sys.argv[2])
manifest = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
app_file = app_dir / "app.py"
source = app_file.read_text(encoding="utf-8")
old_source = source.replace('APP_VERSION = "' + manifest["version"] + '"', 'APP_VERSION = "2.13.0"', 1)
assert source != old_source
app_file.write_text(old_source, encoding="utf-8")
config_before = (app_dir / "config.json").read_bytes()
runtime_before = (app_dir / "runtime" / "python313.dll").read_bytes()
uninstaller_before = (app_dir / "unins000.exe").read_bytes()

payload = zip_path.read_bytes()
updater._get = lambda url, timeout=60: payload
bad = dict(manifest, sha256="0" * 64)
try:
    updater.install(bad, app_dir, "2.13.0")
    raise AssertionError("Bad checksum was accepted")
except ValueError:
    pass
assert app_file.read_text(encoding="utf-8") == old_source

# Simulate an updater package accidentally containing reserved paths.
buffer = io.BytesIO(payload)
with zipfile.ZipFile(buffer, "a", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("Pips/CONFIG.JSON", "must never replace settings")
    archive.writestr("Pips/RUNTIME/python313.dll", "must never replace runtime")
    archive.writestr("Pips/unins000.exe", "must never replace uninstaller")
payload = buffer.getvalue()
manifest["sha256"] = hashlib.sha256(payload).hexdigest()
backup = Path(updater.install(manifest, app_dir, "2.13.0"))
assert app_file.read_text(encoding="utf-8") == source
assert (backup / "app.py").read_text(encoding="utf-8") == old_source
assert (app_dir / "config.json").read_bytes() == config_before
assert (app_dir / "runtime" / "python313.dll").read_bytes() == runtime_before
assert (app_dir / "unins000.exe").read_bytes() == uninstaller_before

buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as archive:
    archive.writestr("../x-escape/unsafe.txt", "no")
    archive.writestr("Pips/app.py", source)
payload = buffer.getvalue()
manifest["sha256"] = hashlib.sha256(payload).hexdigest()
try:
    updater.install(manifest, app_dir, "2.13.0")
    raise AssertionError("Archive traversal was accepted")
except ValueError:
    pass
print("Checksums, update backup, Windows case-insensitive protected paths and archive traversal passed")
'''
    checked("in-app-update", [
        app_dir / "runtime" / "python.exe", "-X", "utf8", "-B", "-c", update_test,
        app_dir, output / "Pips-Windows.zip", output / "latest.json",
    ])
    report["checks"].append("In-app ZIP update, checksum refusal, backup and protected runtime/user data verified")
    report["checks"].append("Unsafe update archive paths are refused")

    restart_probe = session / "restart probe 测试"
    restart_probe.mkdir()
    marker = restart_probe / "started.json"
    (restart_probe / "app.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "Path(__file__).with_name('started.json').write_text("
        "json.dumps({'executable': sys.executable, 'cwd': os.getcwd()}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    runtime("restart", code=f"import updater\nupdater.restart({str(restart_probe)!r}, delay_s=0.1)")
    deadline = time.monotonic() + 20
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    started = json.loads(marker.read_text(encoding="utf-8"))
    assert Path(started["executable"]).resolve() == app_dir / "runtime" / "pythonw.exe"
    assert Path(started["cwd"]).resolve() == restart_probe
    report["checks"].append("Updater restart launches bundled pythonw correctly, including spaces and Unicode")

    runtime("after-update-selftest", args=[app_dir / "app.py", "--selftest", "--no-track"])
    config_after_test = (app_dir / "config.json").read_bytes()
    database_before_uninstall = sha256(database)
    checked("uninstall", [
        app_dir / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES",
        "/NORESTART", "/PIPSTEST=1", f"/LOG={session / 'uninstall-native.log'}",
    ])
    deadline = time.monotonic() + 30
    while (app_dir / "runtime").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not (app_dir / "runtime").exists()
    assert not (app_dir / "app.py").exists()
    assert not (app_dir / ".installer-test").exists()
    assert (app_dir / "config.json").read_bytes() == config_after_test
    assert sha256(database) == database_before_uninstall
    for relative, contents in fixtures.items():
        if relative != "config.json":
            assert (app_dir / relative).read_bytes() == contents, relative
    report["checks"].append("Native uninstaller removes application/runtime/shortcuts but retains user data")
    report["passed"] = True
    return report
