"""Buddy's self-updater (buddy_updater.py at the repo root): versions, manifest, download checks,
safe unpacking, and the real PowerShell swap helper on a fake install folder."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import buddy_updater  # noqa: E402


def test_versions():
    assert buddy_updater.parse_version("v1.10.2") == (1, 10, 2)
    assert buddy_updater.parse_version("2") == (2, 0, 0)
    assert buddy_updater.is_newer("1.1.0", "1.0.9")
    assert buddy_updater.is_newer("1.10.0", "1.9.9")
    assert not buddy_updater.is_newer("1.1.0", "1.1.0")
    assert not buddy_updater.is_newer("0.9", "1.0")


def test_app_version_matches_what_updates_read():
    host = (ROOT / "buddy_host.py").read_text(encoding="utf-8")
    assert buddy_updater.built_version(_fake_build_tree(ROOT)) == _host_version(host)


def _host_version(text):
    import re

    return re.search(r'^BUDDY_VERSION\s*=\s*"([^"]+)"', text, re.M).group(1)


def _fake_build_tree(root):
    """built_version() reads <folder>/app/buddy_host.py; the repo root has buddy_host.py directly."""
    class View:
        def __truediv__(self, name):
            return root if name == "app" else root / name
    return View()


def test_manifest_from_github_release():
    release = {
        "tag_name": "v1.2.0",
        "body": "Buddy 1.2.0\n- Faster island\n- Auto updates\nnot a note",
        "assets": [
            {"name": "Buddy-mac.zip", "browser_download_url": "https://x/mac.zip", "size": 1, "digest": "sha256:aa"},
            {"name": "Buddy-win64.zip", "browser_download_url": "https://x/win.zip", "size": 42,
             "digest": "sha256:" + "b" * 64},
        ],
    }
    manifest = buddy_updater.manifest_from_release(release)
    assert manifest["version"] == "1.2.0"
    assert manifest["windows"] == {"url": "https://x/win.zip", "size": 42, "sha256": "b" * 64}
    assert manifest["notes"] == ["Faster island", "Auto updates"]


def test_check_uses_manifest(tmp_path):
    manifest = {"version": "9.0.0", "windows": {"url": "https://x/win.zip", "sha256": "", "size": 1}}
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    url = path.as_uri()
    assert buddy_updater.check("1.0.0", url)["version"] == "9.0.0"
    assert buddy_updater.check("9.0.0", url) is None


def test_download_verifies_size_and_hash(tmp_path):
    payload = b"buddy" * 1000
    source = tmp_path / "Buddy-win64.zip"
    source.write_bytes(payload)
    good = hashlib.sha256(payload).hexdigest()
    target = buddy_updater.download(source.as_uri(), tmp_path / "out" / "a.zip", good, len(payload))
    assert target.read_bytes() == payload
    with pytest.raises(buddy_updater.UpdateError):
        buddy_updater.download(source.as_uri(), tmp_path / "out" / "b.zip", "0" * 64, len(payload))
    with pytest.raises(buddy_updater.UpdateError):
        buddy_updater.download(source.as_uri(), tmp_path / "out" / "c.zip", good, len(payload) + 1)
    assert not (tmp_path / "out" / "b.zip").exists()


def _make_build_zip(path, version, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Buddy.exe", b"MZ fake")
        archive.writestr("app/buddy_host.py", f'BUDDY_VERSION = "{version}"\n')
        archive.writestr("app/pips/app.py", "# pips\n")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)


def test_unpack_and_version(tmp_path):
    zip_path = tmp_path / "u.zip"
    _make_build_zip(zip_path, "1.2.3")
    folder = buddy_updater.unpack(zip_path, tmp_path / "x")
    assert (folder / "Buddy.exe").exists()
    assert buddy_updater.built_version(folder) == "1.2.3"


def test_unpack_refuses_escaping_paths(tmp_path):
    zip_path = tmp_path / "evil.zip"
    _make_build_zip(zip_path, "1.0.0", {"../evil.txt": "x"})
    with pytest.raises(buddy_updater.UpdateError):
        buddy_updater.unpack(zip_path, tmp_path / "x")


def test_prepare_end_to_end_without_selftest(tmp_path):
    zip_path = tmp_path / "Buddy-win64.zip"
    _make_build_zip(zip_path, "2.0.0", {"app/pips/data/fresh.db": "new"})
    install = tmp_path / "Buddy"
    install.mkdir()
    manifest = {"version": "2.0.0", "windows": {"url": zip_path.as_uri(), "size": zip_path.stat().st_size,
                                                "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest()}}
    stages = []
    ready = buddy_updater.prepare(manifest, install, progress=lambda stage, *_: stages.append(stage),
                                  run_selftest=False)
    assert ready.parent == tmp_path / ".buddy-update"
    assert buddy_updater.built_version(ready) == "2.0.0"
    assert "download" in stages and "unpack" in stages
    wrong = dict(manifest, version="3.0.0")
    with pytest.raises(buddy_updater.UpdateError):
        buddy_updater.prepare(wrong, install, run_selftest=False)


def test_helper_script_quotes_paths():
    script = buddy_updater.helper_script(123, Path(r"C:\Users\o'neil\Buddy"), Path(r"C:\x\new"), "1.2.0",
                                         Path(r"C:\x\update.log"))
    assert "$WaitPid = 123" in script
    assert "'C:\\Users\\o''neil\\Buddy'" in script
    assert "'app\\pips\\data'" in script
    assert "__" not in script.replace("__PID__", "")      # every placeholder was filled


@pytest.mark.skipif(os.name != "nt", reason="the swap helper is PowerShell on Windows")
def test_helper_swaps_folders_and_keeps_user_data(tmp_path):
    install = tmp_path / "Buddy"
    (install / "app" / "pips" / "data").mkdir(parents=True)
    (install / "app" / "pips" / "data" / "focus.db").write_text("my history", encoding="utf-8")
    (install / "app" / "pips" / "config.json").write_text('{"name": "Mochi"}', encoding="utf-8")
    (install / "app" / "buddy_host.py").write_text('BUDDY_VERSION = "1.0.0"\n', encoding="utf-8")
    new = tmp_path / ".buddy-update" / "2.0.0"
    (new / "app" / "pips").mkdir(parents=True)
    (new / "app" / "buddy_host.py").write_text('BUDDY_VERSION = "2.0.0"\n', encoding="utf-8")
    # a harmless stand-in for Buddy.exe: the helper starts it at the end
    stand_in = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
    (new / "Buddy.exe").write_bytes(stand_in.read_bytes())
    (install / "Buddy.exe").write_bytes(stand_in.read_bytes())
    log = tmp_path / "update.log"
    finished = subprocess.run(["cmd", "/c", "exit 0"])                     # a process id that is gone
    script = buddy_updater.helper_script(999999, install, new, "2.0.0", log)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, timeout=120)
    assert finished.returncode == 0
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    text = log.read_text(encoding="utf-8")
    assert "started Buddy 2.0.0" in text, text
    assert buddy_updater.built_version(install) == "2.0.0"
    assert (install / "app" / "pips" / "data" / "focus.db").read_text(encoding="utf-8") == "my history"
    assert "Mochi" in (install / "app" / "pips" / "config.json").read_text(encoding="utf-8")
    assert buddy_updater.built_version(tmp_path / "Buddy.previous") == "1.0.0"
    assert not new.exists()


def test_idle_seconds_is_a_number():
    assert buddy_updater.idle_seconds() >= 0


def test_apply_refuses_when_the_new_version_is_missing(tmp_path):
    with pytest.raises(buddy_updater.UpdateError):
        buddy_updater.apply(tmp_path / "Buddy", tmp_path / ".buddy-update" / "9.9.9", "9.9.9", tmp_path / "update.log")


@pytest.mark.skipif(os.name != "nt", reason="the swap helper is PowerShell on Windows")
def test_helper_with_nothing_to_install_touches_nothing(tmp_path):
    """A second run of the same helper (the update was already installed) must leave Buddy alone."""
    install = tmp_path / "Buddy"
    (install / "app" / "pips" / "data").mkdir(parents=True)
    (install / "app" / "pips" / "data" / "focus.db").write_text("my history", encoding="utf-8")
    (install / "app" / "buddy_host.py").write_text('BUDDY_VERSION = "2.0.0"\n', encoding="utf-8")
    (tmp_path / "Buddy.previous").mkdir()
    log = tmp_path / "update.log"
    gone = tmp_path / ".buddy-update" / "2.0.0"
    script = buddy_updater.helper_script(999999, install, gone, "2.0.0", log)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert "nothing was touched" in log.read_text(encoding="utf-8")
    assert buddy_updater.built_version(install) == "2.0.0"
    assert (install / "app" / "pips" / "data" / "focus.db").read_text(encoding="utf-8") == "my history"
    assert (tmp_path / "Buddy.previous").exists()
    assert not gone.exists()
