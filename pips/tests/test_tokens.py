"""tests/test_tokens.py -- stdlib-only tests for tokens.py.

Runs against a TEMPORARY database and a TEMPORARY fixture log (never the real
``data/productivity.db`` or the real OpenCode log). Exits 0 on success, 1 on any
failed assertion. No third-party deps.

Run:  python tests\\test_tokens.py
"""

import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta

# Make ``import tokens`` work when run from anywhere.
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
import tokens  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
LINE1 = (
    'timestamp=2026-10-21T10:56:04.274Z level=INFO run=4069de62 '
    'message="otel: api_request" sessionID=ses_aa model=coder-model '
    'agent="Sisyphus - ultraworker" cost_usd=0 duration_ms=4397 '
    'input_tokens=34857 output_tokens=12'
)
LINE2 = (
    'timestamp=2026-10-21T10:56:14.176Z level=INFO run=4069de62 '
    'message="otel: api_request" sessionID=ses_aa model=coder-model '
    'agent=unknown cost_usd=0 duration_ms=3720 '
    'input_tokens=34878 output_tokens=146'
)
LINE3 = (
    'timestamp=2026-10-21T11:00:00.000Z level=INFO run=abc '
    'message="otel: api_request" sessionID=ses_bb model=GPT-5 '
    'agent="code" input_tokens=100 output_tokens=5'
)
JUNK = 'timestamp=2026-10-21T10:56:04.274Z level=INFO message="just a log line"'


def local_of(raw_z):
    """Independently convert a UTC 'Z' timestamp to local (day, ts)."""
    dt = datetime.fromisoformat(raw_z.replace("Z", "+00:00")).astimezone()
    return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H:%M:%S")


def new_db(path):
    con = sqlite3.connect(path)
    tokens.ensure_db(con)
    return con


def check(label, cond):
    if not cond:
        raise AssertionError("FAILED: %s" % label)
    print("  ok - %s" % label)


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_parse_line():
    print("[parse_line]")
    rec = tokens.parse_line(LINE1)
    check("usage line parsed", rec is not None)
    day, ts = local_of("2026-10-21T10:56:04.274Z")
    check("model", rec["model"] == "coder-model")
    check("quoted agent", rec["agent"] == "Sisyphus - ultraworker")
    check("session_id", rec["session_id"] == "ses_aa")
    check("input_tokens", rec["input_tokens"] == 34857)
    check("output_tokens", rec["output_tokens"] == 12)
    check("total_tokens", rec["total_tokens"] == 34869)
    check("local day", rec["day"] == day)
    check("local ts", rec["ts"] == ts)

    rec2 = tokens.parse_line(LINE2)
    check("bare agent", rec2["agent"] == "unknown")
    check("bare agent total", rec2["total_tokens"] == 35024)

    check("junk -> None", tokens.parse_line(JUNK) is None)
    check("empty -> None", tokens.parse_line("") is None)


def test_format_tokens():
    print("[format_tokens]")
    check("1234567 -> 1.23M", tokens.format_tokens(1234567) == "1.23M")
    check("12345 -> 12.3k", tokens.format_tokens(12345) == "12.3k")
    check("950 -> 950", tokens.format_tokens(950) == "950")
    check("0 -> 0", tokens.format_tokens(0) == "0")
    check("1000 -> 1.0k", tokens.format_tokens(1000) == "1.0k")
    check("bad -> 0", tokens.format_tokens(None) == "0")


def test_refresh_incremental_and_dedupe(tmpdir):
    print("[refresh incremental / dedupe / rotation]")
    db_path = os.path.join(tmpdir, "tokens.db")
    log_path = os.path.join(tmpdir, "opencode.log")
    con = new_db(db_path)

    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(LINE1 + "\n")
        fh.write(JUNK + "\n")
        fh.write(LINE2 + "\n")

    added = tokens.refresh(con, log_path)
    check("first refresh adds 2", added == 2)
    check("row count == 2", con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0] == 2)

    # No new bytes -> nothing added.
    check("second refresh adds 0", tokens.refresh(con, log_path) == 0)

    # Append a new, distinct usage line.
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(LINE3 + "\n")
    check("append refresh adds 1", tokens.refresh(con, log_path) == 1)
    check("row count == 3", con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0] == 3)

    # Rotation: file shrinks below stored offset -> reset; LINE1 is a dup.
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(LINE1 + "\n")
    check("rotation refresh adds 0 (dup)", tokens.refresh(con, log_path) == 0)
    check("row count still 3", con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0] == 3)

    # Partial line (no trailing newline) must NOT be ingested yet.
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(LINE3)  # no newline
    check("partial line not ingested", tokens.refresh(con, log_path) == 0)

    con.close()

    # Missing file -> 0, no raise.
    con2 = new_db(os.path.join(tmpdir, "tokens2.db"))
    check("missing file -> 0", tokens.refresh(con2, os.path.join(tmpdir, "nope.log")) == 0)
    con2.close()


def test_aggregates(tmpdir):
    print("[today_summary / daily_totals / by_model / by_agent]")
    db_path = os.path.join(tmpdir, "agg.db")
    con = new_db(db_path)

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    rows = [
        (today + " 09:00:00", today, "ses_1", "modelA", "agentX", 100, 10, 110),
        (today + " 09:05:00", today, "ses_1", "modelA", "agentY", 200, 20, 220),
        (today + " 10:00:00", today, "ses_2", "modelB", "agentY", 50, 5, 55),
        (yesterday + " 10:00:00", yesterday, "ses_3", "modelA", "agentX", 999, 1, 1000),
    ]
    con.executemany(
        "INSERT INTO token_usage "
        "(ts, day, session_id, model, agent, input_tokens, output_tokens, total_tokens) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()

    summary = tokens.today_summary(con)
    check("today input", summary["input"] == 350)
    check("today output", summary["output"] == 35)
    check("today total", summary["total"] == 385)
    check("today requests", summary["requests"] == 3)
    check("today last_ts", summary["last_ts"] == today + " 10:00:00")

    totals = tokens.daily_totals(con, 7)
    check("daily_totals has 7 days", len(totals) == 7)
    check("daily_totals today", totals[today]["total"] == 385)
    check("daily_totals yesterday", totals[yesterday]["total"] == 1000)

    models = tokens.by_model(con, today)
    check("by_model count", len(models) == 2)
    check("by_model desc order", models[0]["model"] == "modelA" and models[0]["total"] == 330)
    check("by_model second", models[1]["model"] == "modelB" and models[1]["total"] == 55)

    agents = tokens.by_agent(con, today)
    check("by_agent count", len(agents) == 2)
    check("by_agent desc order", agents[0]["agent"] == "agentY" and agents[0]["total"] == 275)
    check("by_agent second", agents[1]["agent"] == "agentX" and agents[1]["total"] == 110)

    con.close()

    # Empty DB -> safe defaults.
    con3 = new_db(os.path.join(tmpdir, "empty.db"))
    empty = tokens.today_summary(con3)
    check("empty summary zeros", empty == {"input": 0, "output": 0, "total": 0, "requests": 0, "last_ts": ""})
    check("empty by_model []", tokens.by_model(con3, today) == [])
    check("empty by_agent []", tokens.by_agent(con3, today) == [])
    con3.close()


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_parse_line()
        test_format_tokens()
        test_refresh_incremental_and_dedupe(tmpdir)
        test_aggregates(tmpdir)
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
