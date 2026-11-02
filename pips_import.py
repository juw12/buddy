"""Copy a standalone Pips profile (config.json + data/) into Buddy. The source is only READ.

The Pips database is copied with SQLite's online-backup API, so it is consistent even while
the standalone Pips is still running (WAL mode).
"""
import shutil
import sqlite3
import time
from pathlib import Path

SKIP_SUFFIXES = ("-wal", "-shm", "-journal")


def _copy_sqlite(source: Path, target: Path) -> int:
    source_uri = source.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        status = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if status != "ok":
            raise RuntimeError(f"copied database failed integrity check: {status}")
        return dst.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]


def _has_profile(folder: Path) -> bool:
    return (folder / "config.json").exists() or (folder / "data" / "productivity.db").exists()


def import_pips(source_dir, target_dir, force=False):
    """Copy Pips' profile from source_dir into target_dir (Buddy's pips folder). Returns a report."""
    source_dir = Path(source_dir).expanduser().resolve()
    target_dir = Path(target_dir).resolve()
    if source_dir == target_dir:
        raise ValueError("source and target are the same folder")
    if not _has_profile(source_dir):
        raise FileNotFoundError(f"no Pips profile (config.json / data/productivity.db) in {source_dir}")

    report = [f"from {source_dir}", f"to   {target_dir}"]
    if _has_profile(target_dir):
        if not force:
            raise FileExistsError(f"Buddy already has a Pips profile in {target_dir} (use --force to replace it)")
        backup_dir = target_dir / "backup" / time.strftime("before-import-%Y%m%d-%H%M%S")
        backup_dir.mkdir(parents=True)
        for name in ("config.json", "data"):
            if (target_dir / name).exists():
                shutil.move(str(target_dir / name), str(backup_dir / name))
        report.append(f"previous Buddy profile moved to {backup_dir}")

    if (source_dir / "config.json").exists():
        shutil.copy2(source_dir / "config.json", target_dir / "config.json")
        report.append("config.json")

    source_data = source_dir / "data"
    target_data = target_dir / "data"
    target_data.mkdir(parents=True, exist_ok=True)
    if source_data.is_dir():
        for item in sorted(source_data.iterdir()):
            if item.name.endswith(SKIP_SUFFIXES):
                continue
            if item.is_dir():
                shutil.copytree(item, target_data / item.name, dirs_exist_ok=True)
                report.append(f"data/{item.name}/ ({sum(1 for _ in item.rglob('*'))} files)")
            elif item.suffix == ".db":
                tables = _copy_sqlite(item, target_data / item.name)
                report.append(f"data/{item.name} (sqlite backup, {tables} tables, integrity ok)")
            else:
                shutil.copy2(item, target_data / item.name)
                report.append(f"data/{item.name}")
    return report
