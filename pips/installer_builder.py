"""Build Pips' self-contained, per-user Windows installer.

Only the sanitized public stage is packaged. The runtime is fetched from
SHA-256-pinned official Python, PyPI and Inno Setup downloads, never copied
from the developer's Python installation. PIPS_BUILD_CACHE can put the build
cache anywhere; no global Python, PATH or registry changes are required.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
SOURCES = HERE / ".release" / "installer"
SETUP_NAME = "Pips-Setup.exe"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def run(command, *, cwd=None, env=None, timeout=180):
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"Installer build command failed ({result.returncode}):\n"
            f"{result.stdout[-6000:]}\n{result.stderr[-6000:]}"
        )
    return result


def fetch_locked(entry: dict, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / entry["filename"]
    if destination.exists() and digest(destination) == entry["sha256"]:
        return destination
    request = urllib.request.Request(
        entry["url"], headers={"User-Agent": "Pips-release-builder"}
    )
    partial = destination.with_suffix(destination.suffix + ".partial")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            with partial.open("wb") as output:
                shutil.copyfileobj(response, output)
        if digest(partial) != entry["sha256"]:
            raise RuntimeError(f"Checksum mismatch for {entry['filename']}")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def copy_archive_entry(archive, member, destination):
    path = PurePosixPath(member.filename)
    if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
        raise RuntimeError("Unsafe path in a pinned runtime archive")
    if member.is_dir():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)


def prepare_compiler(lock: dict, cache: Path) -> Path:
    compiler_dir = cache / "inno"
    compiler = compiler_dir / "ISCC.exe"
    marker = compiler_dir / ".pips-compiler-version"
    expected = lock["compiler"]["version"]
    if compiler.exists() and marker.exists() and marker.read_text() == expected:
        return compiler
    installer = fetch_locked(lock["compiler"], cache)
    run([
        installer,
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        "/PORTABLE=1",
        "/CURRENTUSER",
        "/NOICONS",
        f"/DIR={compiler_dir}",
        f"/LOG={cache / 'compiler-setup.log'}",
    ])
    if not compiler.exists():
        raise RuntimeError("The portable Inno Setup compiler was not created")
    marker.write_text(expected, encoding="utf-8")
    return compiler


# Bump when the private runtime layout changes (cached runtimes are rebuilt).
# 2: sitecustomize.py keeps the per-user site-packages out of sys.path.
RUNTIME_LAYOUT = 2
SITECUSTOMIZE = '"""Pips private runtime: never load packages from the per-user site-packages of another\nPython 3.13 on this PC (a python313._pth runtime still enables the user site)."""\nimport os\nimport site\nimport sys\n\n_user_site = getattr(site, "USER_SITE", None) or ""\nif _user_site:\n    _prefix = os.path.normcase(os.path.abspath(_user_site))\n    sys.path[:] = [\n        entry for entry in sys.path\n        if not os.path.normcase(os.path.abspath(entry or os.curdir)).startswith(_prefix)\n    ]\nsite.ENABLE_USER_SITE = False\n'


def prepare_runtime(lock: dict, cache: Path) -> Path:
    lock_hash = hashlib.sha256(
        json.dumps(lock, sort_keys=True).encode("utf-8")
    ).hexdigest()
    runtime = cache / "runtime"
    marker = runtime / "runtime-info.json"
    if marker.exists():
        info = json.loads(marker.read_text(encoding="utf-8"))
        if info.get("lock_sha256") == lock_hash and info.get("layout") == RUNTIME_LAYOUT:
            return runtime

    python_package = fetch_locked(lock["python"], cache)
    tk_package = fetch_locked(lock["tk"], cache)
    wheels = [fetch_locked(entry, cache / "wheels") for entry in lock["wheels"]]

    with tempfile.TemporaryDirectory(prefix="runtime-build-", dir=cache) as temporary:
        temporary = Path(temporary)
        stage = temporary / "runtime"
        stage.mkdir()
        with zipfile.ZipFile(python_package) as archive:
            for member in archive.infolist():
                parts = PurePosixPath(member.filename).parts
                if not parts or parts[0] != "tools" or len(parts) < 2:
                    continue
                relative = Path(*parts[1:])
                if relative.parts[0] in {"include", "libs"}:
                    continue
                if "site-packages" in relative.parts:
                    continue
                if any(part in {"test", "tests", "__pycache__", "ensurepip", "idlelib"}
                       for part in relative.parts):
                    continue
                copy_archive_entry(archive, member, stage / relative)

        tk_payload = temporary / "tk"
        tk_payload.mkdir()
        # Administrative extraction only: no Python installation or registration.
        run([
            "msiexec.exe", "/a", tk_package, "/qn",
            f"TARGETDIR={tk_payload}",
            "/L*v", cache / "tk-extract.log",
        ])
        for name in ("DLLs", "Lib", "tcl"):
            if not (tk_payload / name).exists():
                raise RuntimeError(f"Tk payload is missing {name}")
            shutil.copytree(tk_payload / name, stage / name, dirs_exist_ok=True)

        site_packages = stage / "Lib" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    parts = PurePosixPath(member.filename).parts
                    if not parts:
                        continue
                    # pywin32's optional system-registration/test scripts are not
                    # part of this private runtime. No postinstall is executed.
                    if parts[0].endswith(".data"):
                        if len(parts) > 1 and parts[1] == "scripts":
                            continue
                        raise RuntimeError(f"Unexpected wheel data layout: {member.filename}")
                    copy_archive_entry(archive, member, site_packages / Path(*parts))

        # Ignore host Python installations, environment variables and user packages.
        # import site runs pywin32's bundled .pth bootstrap for its private DLLs.
        (stage / "python313._pth").write_text(
            ".\nDLLs\nLib\nLib\\site-packages\n..\nimport site\n",
            encoding="utf-8",
        )
        # ._pth implies isolated mode but NOT -s: drop the user's site-packages explicitly.
        (site_packages / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        for directory in sorted(stage.rglob("__pycache__"), reverse=True):
            shutil.rmtree(directory)
        for file in stage.rglob("*.pyc"):
            file.unlink()
        (stage / "runtime-info.json").write_text(
            json.dumps({
                "lock_sha256": lock_hash,
                "layout": RUNTIME_LAYOUT,
                "python_version": lock["python_version"],
                "packages": [
                    {key: entry[key] for key in ("name", "version", "sha256")}
                    for entry in lock["wheels"]
                ],
                "source": "Official Python and PyPI distributions; see runtime-lock.json.",
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        for required in (
            "python.exe", "pythonw.exe", "python313.dll", "LICENSE.txt",
            "DLLs/_tkinter.pyd", "tcl/tcl8.6/init.tcl", "Lib/tkinter/__init__.py",
        ):
            if not (stage / required).is_file():
                raise RuntimeError(f"Incomplete private runtime: {required}")
        if runtime.exists():
            shutil.rmtree(runtime)
        shutil.move(str(stage), runtime)
    return runtime


def build_installer(app_stage: Path, output: Path, version: str) -> dict:
    if os.name != "nt":
        raise RuntimeError("The Pips Windows installer must be built on Windows")
    app_stage = Path(app_stage).resolve()
    output = Path(output).resolve()
    for forbidden in ("config.json", "data", "logs", "backup", ".release"):
        if (app_stage / forbidden).exists():
            raise RuntimeError(f"Private files must not enter the installer: {forbidden}")
    if not (app_stage / "app.py").is_file():
        raise RuntimeError("Build the sanitized public application first")

    lock = json.loads((SOURCES / "runtime-lock.json").read_text(encoding="utf-8"))
    cache = Path(os.environ.get("PIPS_BUILD_CACHE", HERE / ".release" / "installer-cache"))
    cache = cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    compiler = prepare_compiler(lock, cache)
    runtime = prepare_runtime(lock, cache)
    print(f"[installer] compiling Pips {version} with Python {lock['python_version']}", flush=True)
    result = run([
        compiler,
        "/Qp",
        f"/DAppVersion={version}",
        f"/DAppStage={app_stage}",
        f"/DRuntimeStage={runtime}",
        f"/DOutputPath={output}",
        SOURCES / "Pips.iss",
    ], timeout=600)
    (output / "installer-build.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    setup = output / SETUP_NAME
    if not setup.exists() or setup.stat().st_size >= 50_000_000:
        raise RuntimeError("Installer missing or above the 50 MB distribution budget")
    return {
        "filename": SETUP_NAME,
        "sha256": digest(setup),
        "size": setup.stat().st_size,
        "architecture": "x64",
        "python_version": lock["python_version"],
        "requires_admin": False,
    }
