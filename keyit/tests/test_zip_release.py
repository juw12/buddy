"""tools/zip_release.py: the release zip that build.bat makes."""
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("zip_release", ROOT / "tools" / "zip_release.py")
zip_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zip_release)


def _build(folder):
    """A small stand-in for dist/Buddy: {name in the zip: bytes}."""
    files = {
        "Buddy.exe": b"MZ" + bytes(2000),
        "app/buddy_host.py": b"VERSION = '0'\n",
        "app/keyit/keyit_speech.py": b"# speech\n",
        "_internal/\u540d\u524d with space.txt": "h\u00e9llo".encode("utf-8"),
    }
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "app" / "assets" / "empty").mkdir(parents=True)
    return files


def test_zip_holds_the_folder_contents_at_the_top(tmp_path):
    source, target = tmp_path / "Buddy", tmp_path / "out" / "Buddy-win64.zip"
    files = _build(source)
    target.parent.mkdir()
    assert zip_release.zip_folder(source, target) == (len(files), target.stat().st_size)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert {name: archive.read(name) for name in files} == files       # no "Buddy/" in front, forward slashes
        assert "app/assets/empty/" in archive.namelist()                    # an empty folder is kept
    assert not list(target.parent.glob("*.part"))


def test_incomplete_build_is_refused_and_the_old_zip_stays(tmp_path):
    source, target = tmp_path / "Buddy", tmp_path / "Buddy-win64.zip"
    _build(source)
    (source / "app" / "keyit" / "keyit_speech.py").unlink()
    target.write_bytes(b"previous release")
    with pytest.raises(SystemExit, match="keyit_speech.py"):
        zip_release.zip_folder(source, target)
    assert target.read_bytes() == b"previous release"
    assert not list(tmp_path.glob("*.part"))


def test_personal_pips_data_is_never_zipped(tmp_path):
    source, target = tmp_path / "Buddy", tmp_path / "Buddy-win64.zip"
    _build(source)
    (source / "app" / "pips" / "data").mkdir(parents=True)
    with pytest.raises(SystemExit, match="personal data"):
        zip_release.zip_folder(source, target)
    assert not target.exists()


def test_archive_that_does_not_read_back_is_not_published(tmp_path, monkeypatch):
    source, target = tmp_path / "Buddy", tmp_path / "Buddy-win64.zip"
    _build(source)
    target.write_bytes(b"previous release")
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "Buddy.exe")
    with pytest.raises(SystemExit, match="did not read back"):
        zip_release.zip_folder(source, target)
    assert target.read_bytes() == b"previous release"
    assert not list(tmp_path.glob("*.part"))


def test_command_line_takes_the_folder_and_the_zip(tmp_path, capsys):
    source, target = tmp_path / "Buddy", tmp_path / "Buddy-win64.zip"
    files = _build(source)
    zip_release.main([str(source), str(target)])
    assert f"Zipped {len(files)} files" in capsys.readouterr().out
    assert zipfile.is_zipfile(target)


def test_build_bat_makes_the_zip_with_this_tool():
    lines = [line.strip() for line in (ROOT / "build.bat").read_text(encoding="utf-8").splitlines()]
    commands = [line for line in lines if line and not line.lower().startswith("rem ")]
    assert '"%PYTHON%" tools\\zip_release.py || goto :fail' in commands
    assert not [line for line in commands if "Compress-Archive" in line]
