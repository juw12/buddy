"""tools/release_manifest.py: what the auto-updaters are told to download."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("release_manifest", ROOT / "tools" / "release_manifest.py")
release_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_manifest)


def _asset(name, digest="sha256:" + "ab" * 32):
    return {"name": name, "browser_download_url": f"https://example.invalid/{name}", "size": 10, "digest": digest}


def _release(tag, *names):
    return {"tag_name": tag, "body": "- a fix", "assets": [_asset(name) for name in names]}


def test_full_release_lists_every_platform():
    manifest = release_manifest.build_manifest(_release("v1.1.0", "Buddy-win64.zip", "Buddy-mac.zip", "Buddy-iOS.ipa"))
    assert manifest["version"] == "1.1.0"
    assert {manifest[p]["version"] for p in ("windows", "mac", "ios")} == {"1.1.0"}


def test_windows_only_fix_keeps_mac_and_iphone_from_the_last_release():
    full = _release("v1.1.0", "Buddy-win64.zip", "Buddy-mac.zip", "Buddy-iOS.ipa")
    fix = _release("v1.1.1", "Buddy-win64.zip")
    manifest = release_manifest.build_manifest(fix, [full])
    assert manifest["version"] == "1.1.1"
    assert manifest["windows"]["version"] == "1.1.1"
    assert manifest["mac"]["version"] == "1.1.0"
    assert manifest["ios"]["version"] == "1.1.0"
    assert manifest["mac"]["url"].endswith("Buddy-mac.zip")


def test_unverified_files_are_left_out():
    release = {"tag_name": "v2.0.0", "body": "", "assets": [_asset("Buddy-win64.zip", digest="")]}
    assert "windows" not in release_manifest.build_manifest(release)
