"""diary_obsidian.py -- mirror the productivity diary into the Obsidian vault.

Writes (and keeps up to date) one note per day plus an index note under the
configured vault folder, so the diary is browsable in Obsidian. The folder is
created automatically; existing notes are overwritten in place.

Config (``config.json`` -> ``diary``):
    "obsidian_enabled": true,
    "obsidian_dir": "C:\\Users\\you\\Documents\\Obsidian Vault\\Productivity Diary"

Nothing is raised to the caller: failures log to ``logs/diary.log`` and return a
status dict.
"""

from __future__ import annotations

import datetime as dt
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOG_FILE = HERE / "logs" / "diary.log"
DEFAULT_VAULT_DIR = r"C:\Users\you\Documents\Obsidian Vault\Productivity Diary"
INDEX_NAME = "Productivity Diary.md"


def log(msg: str) -> None:
    (HERE / "logs").mkdir(parents=True, exist_ok=True)
    line = "[%s] %s" % (dt.datetime.now().isoformat(timespec="seconds"), msg)
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _hm(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        total = 0
    h, rem = divmod(max(0, total), 3600)
    m = rem // 60
    return ("%dh %dm" % (h, m)) if h and m else ("%dh" % h if h else "%dm" % m)


def _num(n) -> str:
    try:
        return "{:,}".format(int(n or 0))
    except (TypeError, ValueError):
        return "0"


def _stars(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    return "\u2605" * n + "\u2606" * (5 - n)


def _note_frontmatter(rec: dict) -> str:
    day = rec.get("day", "")
    return "\n".join([
        "---",
        'title: "Productivity Diary \u2014 %s"' % day,
        "date: %s" % day,
        "tags: [diary, productivity]",
        "score: %s" % ("%.1f" % float(rec.get("score") or 0.0)),
        "stars: %d" % int(rec.get("stars", 0) or 0),
        "pomodoros: %d" % int(rec.get("pomodoros", 0) or 0),
        'deep_work: "%s"' % _hm(rec.get("deep_work_seconds", 0)),
        "ai_tokens: %d" % int((rec.get("tokens") or {}).get("total", 0) or 0),
        "---",
        "",
    ])


def _index_note(cfg: dict, data: dict, records: list) -> str:
    totals = data.get("totals", {}) or {}
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "---",
        "title: Productivity Diary",
        "tags: [diary, productivity, index]",
        "updated: %s" % dt.datetime.now().strftime("%Y-%m-%d"),
        "---",
        "",
        "# Productivity Diary",
        "",
        "_Auto-managed by Pips \u00b7 last %d days \u00b7 generated %s_" % (len(records), ts),
        "",
        "**Avg score:** %s \u00b7 **Pomodoros:** %s \u00b7 **AI tokens:** %s"
        % ("%.1f" % float(totals.get("score_avg", 0) or 0.0),
           _num(totals.get("pomodoros_total", 0)), _num(totals.get("tokens_total", 0))),
        "",
        "| Date | Score | Stars | Pomodoros | Deep Work | AI Tokens |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for rec in records:
        day = rec.get("day", "")
        lines.append("| [[%s]] | %s | %s | %d | %s | %s |" % (
            day, "%.1f" % float(rec.get("score") or 0.0), _stars(rec.get("stars")),
            int(rec.get("pomodoros", 0) or 0), _hm(rec.get("deep_work_seconds", 0)),
            _num((rec.get("tokens") or {}).get("total", 0))))
    lines.append("")
    return "\n".join(lines)


def sync(con, cfg: dict, repo_dir: str | None = None) -> dict:
    diary_cfg = cfg.get("diary", {}) or {}
    if not diary_cfg.get("obsidian_enabled", True):
        log("obsidian sync: disabled")
        return {"status": "disabled"}
    target = Path(diary_cfg.get("obsidian_dir") or DEFAULT_VAULT_DIR)
    days = int(diary_cfg.get("export_days") or 30)
    try:
        import diary

        data = diary.build_json(con, cfg, days=days)
        records = data.get("days", []) or []
        target.mkdir(parents=True, exist_ok=True)
        written = 0
        for rec in records:
            day = rec.get("day", "")
            if not day:
                continue
            note = _note_frontmatter(rec) + "\n" + diary.build_entry(con, cfg, day)
            (target / ("%s.md" % day)).write_text(note, encoding="utf-8")
            written += 1
        (target / INDEX_NAME).write_text(_index_note(cfg, data, records), encoding="utf-8")
        log("obsidian sync: %d notes + index -> %s" % (written, target))
        return {"status": "ok", "notes": written, "dir": str(target)}
    except Exception:
        log("obsidian sync failed\n" + traceback.format_exc())
        return {"status": "error"}


def main(argv=None) -> int:
    import sqlite3

    import tracker

    cfg = tracker.load_config()
    con = sqlite3.connect(str(HERE / "data" / "productivity.db"))
    tracker.ensure_db(con)
    result = sync(con, cfg)
    print(result)
    return 0 if result.get("status") in ("ok", "disabled") else 1


if __name__ == "__main__":
    raise SystemExit(main())
