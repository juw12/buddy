"""Zip the built app folder (dist/Buddy) into the release asset dist/Buddy-win64.zip.

build.bat calls this instead of PowerShell's Compress-Archive, which stalled on the app folder when 1.2.0 was
built. The contents of the folder go to the top of the zip, as before. The archive is written beside the target,
read back, and only then moved into place, so a failed run never leaves a broken zip under the release name.

    python tools/zip_release.py [app folder] [zip file]
"""
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("Buddy.exe", "app/buddy_host.py", "app/keyit/keyit_speech.py")      # an unfinished build lacks these
PRIVATE = ("app/pips/data", "app/pips/config.json")                             # the builder's own Pips data


def zip_folder(source, target, required=REQUIRED, private=PRIVATE, level=6):
    """Zip everything under source into target and return (files, size of the zip in bytes)."""
    source, target = Path(source), Path(target)
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise SystemExit(f"not zipped: the build in {source} is missing {', '.join(missing)}")
    found = [name for name in private if (source / name).exists()]
    if found:
        raise SystemExit(f"not zipped: {source} still holds personal data ({', '.join(found)})")
    entries = sorted(source.rglob("*"))
    files = [path for path in entries if path.is_file()]
    empty_folders = [path for path in entries if path.is_dir() and not any(path.iterdir())]
    partial = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level,
                             strict_timestamps=False) as archive:
            for path in files + empty_folders:
                archive.write(path, path.relative_to(source).as_posix())
        with zipfile.ZipFile(partial) as archive:
            damaged = archive.testzip()
            stored = [info for info in archive.infolist() if not info.is_dir()]
        if damaged or len(stored) != len(files):
            raise SystemExit(f"not zipped: the archive did not read back ({damaged or 'the file count differs'})")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return len(files), target.stat().st_size


def main(arguments):
    source = Path(arguments[0]) if arguments else ROOT / "dist" / "Buddy"
    target = Path(arguments[1]) if len(arguments) > 1 else ROOT / "dist" / "Buddy-win64.zip"
    started = time.time()
    files, size = zip_folder(source, target)
    print(f"Zipped {files} files into {target} ({size / 1e6:.1f} MB) in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1:])
