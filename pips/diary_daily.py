"""diary_daily.py -- one daily pipeline for the productivity diary.

Runs, in order:
    1. GENERATE  today's diary entry + index + data.json   (auto-write)
    2. EXPORT    a human-readable PDF of the diary          (diary_export)
    3. EMAIL     the diary, threaded as one conversation    (diary_email)
    4. PUBLISH   optionally commit + push to GitHub         (publish.ps1)

Every step degrades gracefully and logs to ``logs/diary.log`` -- a failure in one
step never stops the others. This is what the daily 22:00 scheduled task runs.

CLI:
    python diary_daily.py                 # full pipeline for today
    python diary_daily.py --no-email      # generate + PDF only
    python diary_daily.py --no-generate   # email only (already generated)
    python diary_daily.py --date 2026-10-24
    python diary_daily.py --publish       # also push to GitHub this run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOG_FILE = HERE / "logs" / "diary.log"
DB_PATH = HERE / "data" / "productivity.db"

DEFAULT_DASHBOARD = "https://juw12.github.io/diary/"


def log(msg: str) -> None:
    (HERE / "logs").mkdir(parents=True, exist_ok=True)
    line = "[%s] %s" % (dt.datetime.now().isoformat(timespec="seconds"), msg)
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
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
    return "★" * n + "☆" * (5 - n)


def _body_text(record: dict, pdf_path: str, dashboard_url: str) -> str:
    day = record.get("day", "")
    weekday = record.get("weekday", "")
    tok = record.get("tokens") or {}
    apps = record.get("top_apps") or []
    app_line = ", ".join("%s %s" % (a.get("app", ""), _hm(a.get("seconds", 0))) for a in apps[:6]) or "none"
    lines = [
        "Productivity Diary — %s (%s)" % (day, weekday),
        "",
        "Score: %s/100 · Stars: %s · Pomodoros: %d · Deep work: %s"
        % (("%.1f" % float(record.get("score") or 0.0)), _stars(record.get("stars")),
           int(record.get("pomodoros", 0) or 0), _hm(record.get("deep_work_seconds", 0))),
        "AI tokens: %s (in %s / out %s · %s requests)"
        % (_num(tok.get("total", 0)), _num(tok.get("input", 0)),
           _num(tok.get("output", 0)), _num(tok.get("requests", 0))),
        "Top apps: %s" % app_line,
        "",
    ]
    coach = (record.get("coach") or "").strip()
    if coach:
        lines += ["Coach:", coach, ""]
    if pdf_path:
        lines.append("Full diary PDF attached.")
    if dashboard_url:
        lines.append("Dashboard: %s" % dashboard_url)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def _generate(con, cfg, repo_dir, day: str, days: int) -> None:
    import diary

    p1 = diary.write_entry(con, cfg, day, repo_dir)
    p2 = diary.write_index(con, cfg, repo_dir, days)
    p3 = diary.write_json(con, cfg, repo_dir, days)
    log("generate: entry=%s index=%s json=%s" % (bool(p1), bool(p2), bool(p3)))


def _export_pdf(con, cfg, repo_dir, days: int) -> str:
    import diary_export

    return diary_export.build_pdf(con, cfg, repo_dir, days=days)


def _publish(repo_dir: str) -> None:
    script = Path(repo_dir) / "publish.ps1"
    if not script.exists():
        log("publish: publish.ps1 not found at %s" % script)
        return
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, text=True, timeout=180, cwd=str(script.parent),
        )
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        log("publish: rc=%s %s" % (proc.returncode, " | ".join(tail[-3:]) if tail else ""))
    except Exception as exc:
        log("publish: failed: %s" % exc)


GIT_EXE = r"C:\Users\you\AppData\Local\Programs\Git\cmd\git.exe"


def _sync_portfolio(cfg, repo_dir: str) -> None:
    diary_cfg = cfg.get("diary", {}) or {}
    if not diary_cfg.get("portfolio_sync", True):
        log("portfolio sync: disabled")
        return
    pdir = Path(diary_cfg.get("portfolio_dir") or r"C:\Users\you\Documents\portfolio")
    sub = diary_cfg.get("portfolio_subdir") or "diary"
    if not pdir.exists():
        log("portfolio sync: portfolio dir not found at %s" % pdir)
        return
    dest = pdir / sub
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("report.html", "index.html", "data.json"):
        src = Path(repo_dir) / name
        if src.exists():
            shutil.copyfile(str(src), str(dest / name))
    git = GIT_EXE if Path(GIT_EXE).exists() else shutil.which("git")
    if not git:
        log("portfolio sync: git not found; files copied but not pushed")
        return
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    subprocess.run([git, "-C", str(pdir), "add", sub], env=env, timeout=60)
    diff = subprocess.run([git, "-C", str(pdir), "diff", "--cached", "--quiet"], env=env, timeout=60)
    if diff.returncode == 0:
        log("portfolio sync: no changes")
        return
    subprocess.run([git, "-C", str(pdir), "commit", "-m", "diary: sync diary site"], env=env, timeout=60)
    push = subprocess.run([git, "-C", str(pdir), "push"], env=env, capture_output=True, text=True, timeout=180)
    log("portfolio sync: push rc=%s %s" % (push.returncode, (push.stderr or "").strip()[-140:]))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Daily productivity diary pipeline")
    parser.add_argument("--date", default=None, help="day to report (YYYY-MM-DD); default today")
    parser.add_argument("--days", type=int, default=30, help="days in the diary export (default 30)")
    parser.add_argument("--no-generate", action="store_true", help="skip writing diary files")
    parser.add_argument("--no-pdf", action="store_true", help="skip the PDF export")
    parser.add_argument("--no-obsidian", dest="no_obsidian", action="store_true", help="skip the Obsidian vault sync")
    parser.add_argument("--no-email", action="store_true", help="skip emailing")
    parser.add_argument("--force-email", dest="force_email", action="store_true",
                        help="email even on a configured skip day (e.g. Sunday)")
    parser.add_argument("--publish", action="store_true", help="force a GitHub publish this run")
    args = parser.parse_args(argv)

    day = args.date or dt.date.today().isoformat()
    log("=== diary pipeline start (%s) ===" % day)

    try:
        import diary
        import tracker
    except Exception:
        log("FATAL could not import diary/tracker\n" + traceback.format_exc())
        return 2

    cfg = tracker.load_config()
    diary_cfg = cfg.get("diary", {}) or {}
    mail_cfg = diary_cfg.get("email", {}) or {}
    repo_dir = diary_cfg.get("repo_dir") or str(HERE.parent / "productivity-diary")

    try:
        con = sqlite3.connect(str(DB_PATH), timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        tracker.ensure_db(con)
    except Exception:
        log("FATAL database unavailable\n" + traceback.format_exc())
        return 3

    # 1. generate
    if diary_cfg.get("auto_write", True) and not args.no_generate:
        try:
            _generate(con, cfg, repo_dir, day, args.days)
        except Exception:
            log("generate failed\n" + traceback.format_exc())

    # 2. export PDF
    pdf_path = ""
    if not args.no_pdf:
        try:
            pdf_path = _export_pdf(con, cfg, repo_dir, args.days)
        except Exception:
            log("pdf export failed\n" + traceback.format_exc())

    if not args.no_obsidian:
        try:
            import diary_obsidian

            diary_obsidian.sync(con, cfg, repo_dir)
        except Exception:
            log("obsidian sync failed\n" + traceback.format_exc())

    # 3. email
    skip_days = {str(d).strip().lower() for d in (mail_cfg.get("skip_days") or [])}
    try:
        weekday = dt.date.fromisoformat(day).strftime("%A")
    except ValueError:
        weekday = ""
    skip_today = weekday.lower() in skip_days and not args.force_email
    if not args.no_email and mail_cfg.get("enabled", True) and not skip_today:
        try:
            import diary_email

            record = diary.build_json(con, cfg, days=1)["days"][0]
            body = _body_text(record, pdf_path, mail_cfg.get("dashboard_url") or DEFAULT_DASHBOARD)
            subject = mail_cfg.get("subject") or "Productivity Diary"
            result = diary_email.send_diary_email(cfg, body, pdf_path=pdf_path, subject=subject)
            log("email: %s" % json.dumps(result, ensure_ascii=False))
        except Exception:
            log("email failed\n" + traceback.format_exc())
    elif not args.no_email and skip_today:
        log("email: skipped (%s is in diary.email.skip_days)" % weekday)
    elif not args.no_email:
        log("email: disabled in config (diary.email.enabled=false)")

    # 4. publish
    if (args.publish or diary_cfg.get("auto_publish")) and not args.no_generate:
        _publish(repo_dir)

    try:
        _sync_portfolio(cfg, repo_dir)
    except Exception:
        log("portfolio sync failed\n" + traceback.format_exc())

    log("=== diary pipeline complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
