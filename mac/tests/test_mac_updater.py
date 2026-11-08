"""Buddy Lite for Mac updater: the parts that run anywhere (no PyObjC, no ditto)."""
import hashlib
import json
import plistlib
import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buddy_mac import updater  # noqa: E402


def test_versions():
    assert updater.parse_version("v1.2") == (1, 2, 0)
    assert updater.parse_version("1.10.3-beta") == (1, 10, 3)
    assert updater.is_newer("1.1.1", "1.1.0")
    assert updater.is_newer("1.10.0", "1.9.9")
    assert not updater.is_newer("1.1.0", "1.1.0")
    assert not updater.is_newer("", "0.0.1")


def _manifest(tmp_path: Path, version: str, with_mac: bool = True) -> str:
    data = {"version": version}
    if with_mac:
        data["mac"] = {"version": version, "url": "https://example.invalid/Buddy-mac.zip"}
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path.as_uri()


def test_check_reads_manifest(tmp_path):
    url = _manifest(tmp_path, "9.0.0")
    assert updater.check("1.1.0", url)["mac"]["version"] == "9.0.0"
    assert updater.check("9.0.0", url) is None


def test_manifest_without_mac_is_rejected(tmp_path):
    with pytest.raises(updater.UpdateError):
        updater.fetch_manifest(_manifest(tmp_path, "9.0.0", with_mac=False))


def test_download_checks_size_and_hash(tmp_path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"buddy" * 1000)
    good = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "out" / "Buddy-mac.zip"
    updater.download(source.as_uri(), target, good, 5000)
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(updater.UpdateError):
        updater.download(source.as_uri(), target, "0" * 64)
    assert not target.exists()
    with pytest.raises(updater.UpdateError):
        updater.download(source.as_uri(), target, good, 4999)


def test_app_bundle_and_version(tmp_path):
    app = tmp_path / "Applications" / "Buddy.app"
    exe = app / "Contents" / "MacOS" / "Buddy"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    with open(app / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleShortVersionString": "1.1.0"}, handle)
    assert updater.app_bundle(str(exe)) == app.resolve()
    assert updater.bundle_version(app) == "1.1.0"
    assert updater.app_bundle(str(tmp_path / "python")) is None


def test_helper_script_quotes_paths_and_rolls_back():
    current = PurePosixPath("/Applications/My Apps/Buddy.app")
    new = PurePosixPath("/tmp/buddy update/new/Buddy.app")
    script = updater.helper_script(4242, current, new, PurePosixPath("/tmp/it's.log"))
    assert script.startswith("#!/bin/sh")
    assert "kill -0 4242" in script
    assert "'/Applications/My Apps/Buddy.previous.app'" in script
    assert "'/tmp/buddy update/new/Buddy.app'" in script
    assert "putting the old version back" in script
    assert script.rstrip().endswith("open '/Applications/My Apps/Buddy.app'")
