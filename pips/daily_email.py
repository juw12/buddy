"""Generate and email the daily productivity progress report.

Usage:
    python daily_email.py                 # today's report; sends if email.enabled
    python daily_email.py --date 2026-10-21
    python daily_email.py --no-email      # generate the HTML only
    python daily_email.py --preview       # generate + open it in the browser

Sends through the Outlook desktop app via the outlook-agent skill CLI
(``email.cli`` in config.json), exactly like The Morning Paper does.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOG_FILE = HERE / "logs" / "email.log"


def log(msg: str) -> None:
    (HERE / "logs").mkdir(parents=True, exist_ok=True)
    line = "[%s] %s" % (dt.datetime.now().isoformat(timespec="seconds"), msg)
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def send_email(cfg: dict, html_path: Path, subject: str) -> bool:
    email = dict(cfg.get("email", {}) or {})
    to_addr = (email.get("to") or "").strip()
    cli = email.get("cli") or ""
    if not to_addr:
        log("email skipped: email.to is empty")
        return False
    if not cli or not Path(cli).exists():
        log("email skipped: outlook CLI not found at %r" % cli)
        return False

    cmd = [
        sys.executable, cli, "send",
        "--to", to_addr,
        "--subject", subject,
        "--body-file", str(html_path),
        "--html",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, input="y\n",
            cwd=str(Path(cli).resolve().parent),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if "'status': 'sent'" in out or '"status": "sent"' in out:
            log("email sent: %s" % subject)
            return True
        log("WARN email NOT sent (rc=%s): %s" % (proc.returncode, out.strip()[:400]))
        return False
    except Exception as exc:
        log("WARN email error: %s" % exc)
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Daily productivity report email")
    parser.add_argument("--date", default=None, help="day to report (YYYY-MM-DD); default today")
    parser.add_argument("--no-email", action="store_true", help="generate the HTML only")
    parser.add_argument("--preview", action="store_true", help="open the HTML in the browser")
    parser.add_argument("--monthly", action="store_true", help="email the monthly roll-up instead of the daily report")
    args = parser.parse_args(argv)

    if args.monthly:
        day = args.date or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    else:
        day = args.date or dt.date.today().isoformat()
    log("=== %s report start (%s) ===" % ("monthly" if args.monthly else "daily", day))

    try:
        import coach
        import tracker
    except Exception:
        log("FATAL could not import modules\n" + traceback.format_exc())
        return 2

    cfg = tracker.load_config()
    email_cfg = dict(cfg.get("email", {}) or {})

    db_path = HERE / "data" / "productivity.db"
    con = sqlite3.connect(str(db_path))
    tracker.ensure_db(con)

    prefix = email_cfg.get("subject_prefix") or "Productivity Report"
    try:
        if args.monthly:
            html_doc = coach.monthly_report(con, cfg, day)
            out_name = "monthly-report-%s.html" % day[:7]
            subject = "%s - Monthly - %s" % (prefix, day[:7])
        else:
            html_doc = coach.daily_chart_report_html(con, cfg, day)
            out_name = "daily-report-%s.html" % day
            subject = "%s - %s" % (prefix, day)
    except Exception:
        log("FATAL report generation failed\n" + traceback.format_exc())
        return 3

    out_dir = HERE / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_name
    path.write_text(html_doc, encoding="utf-8")
    log("wrote %s (%d bytes)" % (path.name, len(html_doc)))

    if args.preview:
        try:
            os.startfile(str(path))
        except Exception as exc:
            log("WARN could not open browser: %s" % exc)

    if email_cfg.get("enabled", False) and not args.no_email:
        send_email(cfg, path, subject)
    else:
        log("email disabled (enabled=%s, no_email=%s)" % (email_cfg.get("enabled"), args.no_email))

    log("=== report complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
