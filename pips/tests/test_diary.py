"""tests/test_diary.py -- hermetic test for diary.py.

Seeds a temp SQLite DB (daily_summary + app_usage + hour_usage + sessions +
meta + token_usage), exercises every public function of ``diary`` and writes
only into a temp directory. Exits 0 on success, 1 on failure.

Run:  python tests\\test_diary.py   (from the pips folder)
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

WIDGET_DIR = Path(__file__).resolve().parent.parent
if str(WIDGET_DIR) not in sys.path:
    sys.path.insert(0, str(WIDGET_DIR))

import diary  # noqa: E402
import scoring  # noqa: E402
import coach  # noqa: E402

DDL = """
CREATE TABLE app_usage (
  day TEXT NOT NULL, app TEXT NOT NULL, title TEXT,
  seconds INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, app));
CREATE TABLE hour_usage (
  day TEXT NOT NULL, hour INTEGER NOT NULL, app TEXT NOT NULL,
  seconds INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, hour, app));
CREATE TABLE sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, kind TEXT NOT NULL,
  task TEXT, started_at TEXT NOT NULL, ended_at TEXT, completed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE daily_summary (
  day TEXT PRIMARY KEY, productive_seconds INTEGER NOT NULL DEFAULT 0,
  neutral_seconds INTEGER NOT NULL DEFAULT 0, distracting_seconds INTEGER NOT NULL DEFAULT 0,
  idle_seconds INTEGER NOT NULL DEFAULT 0, focus_seconds INTEGER NOT NULL DEFAULT 0,
  app_switches INTEGER NOT NULL DEFAULT 0, pomodoros_done INTEGER NOT NULL DEFAULT 0,
  score REAL, stars INTEGER, goal_met INTEGER NOT NULL DEFAULT 0, xp INTEGER NOT NULL DEFAULT 0,
  coach_text TEXT, updated_at TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE token_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
  session_id TEXT, model TEXT, agent TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0);
CREATE INDEX idx_token_usage_day ON token_usage(day);
"""

CFG = {
    "category": {
        "productive": ["code.exe", "windowsterminal.exe", "devenv.exe"],
        "distracting": ["steam.exe", "discord.exe"],
        "neutral": [],
    },
    "title_rules": [
        {"match": "youtube", "category": "distracting", "where": "title"},
    ],
    "goals": {"deep_work_hours": 4, "pomodoros": 8, "max_distraction_hours": 2},
    "scoring": {
        "weights": {"productive": 1.0, "neutral": 0.3, "distracting": -0.8, "idle": 0.0},
        "star_thresholds": [20, 40, 60, 80],
    },
    "coach": {"enabled": True, "llm": {"enabled": False}},
}

TODAY = date.today().isoformat()
YDAY = (date.today() - timedelta(days=1)).isoformat()
OLD = (date.today() - timedelta(days=10)).isoformat()  # no data at all


def seed(con) -> None:
    con.executescript(DDL)

    con.executemany(
        "INSERT INTO app_usage (day, app, title, seconds) VALUES (?,?,?,?)",
        [
            (TODAY, "code.exe", "main.py - Visual Studio Code", 12000),
            (TODAY, "chrome.exe", "YouTube - lofi beats", 4800),
            (TODAY, "explorer.exe", "Downloads", 900),
            (YDAY, "code.exe", "old.py - Visual Studio Code", 5000),
        ],
    )
    con.executemany(
        "INSERT INTO hour_usage (day, hour, app, seconds) VALUES (?,?,?,?)",
        [
            (TODAY, 9, "code.exe", 3600),      # focus hour
            (TODAY, 10, "code.exe", 3600),     # focus hour
            (TODAY, 14, "chrome.exe", 3600),   # all distracting -> not focus
        ],
    )
    con.executemany(
        "INSERT INTO sessions (day, kind, task, started_at, ended_at, completed) "
        "VALUES (?,?,?,?,?,?)",
        [
            (TODAY, "work", "deep", TODAY + "T09:00:00", TODAY + "T09:25:00", 1),
            (TODAY, "work", "deep", TODAY + "T10:00:00", TODAY + "T10:25:00", 1),
        ],
    )
    con.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("switch:%s" % TODAY, "42"))
    con.execute(
        "INSERT INTO daily_summary (day, idle_seconds, updated_at) VALUES (?,?,?)",
        (TODAY, 600, TODAY),
    )
    con.executemany(
        "INSERT INTO token_usage (ts, day, session_id, model, agent, "
        "input_tokens, output_tokens, total_tokens) VALUES (?,?,?,?,?,?,?,?)",
        [
            (TODAY + " 10:00:00", TODAY, "ses_a", "coder-model", "Sisyphus", 800000, 5000, 805000),
            (TODAY + " 11:00:00", TODAY, "ses_b", "coder-model", "Sisyphus", 12345, 1200, 13545),
            (YDAY + " 10:00:00", YDAY, "ses_c", "coder-model", "Sisyphus", 100, 10, 110),
        ],
    )
    con.commit()


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def ok(self, cond, msg: str) -> None:
        if cond:
            self.passed += 1
        else:
            self.failures.append(msg)

    def eq(self, got, want, msg: str) -> None:
        self.ok(got == want, "%s (got %r, want %r)" % (msg, got, want))

    def in_(self, needle, haystack, msg: str) -> None:
        self.ok(needle in haystack, "%s (missing %r)" % (msg, needle))


def run() -> int:
    checks = Checks()
    tmp = Path(tempfile.mkdtemp(prefix="diary_test_"))
    repo_dir = tmp / "diary-repo"
    con = sqlite3.connect(":memory:")
    seed(con)

    expected = scoring.compute_daily(con, TODAY, CFG)
    expected_coach = coach.daily_coach(con, TODAY, CFG)

    # -- ensure_db is a no-op ------------------------------------------------ #
    checks.eq(diary.ensure_db(con), None, "ensure_db returns None")

    # -- build_entry --------------------------------------------------------- #
    md = diary.build_entry(con, CFG, TODAY)
    checks.in_("# %s" % TODAY, md, "markdown H1 has date")
    checks.in_(datetime.strptime(TODAY, "%Y-%m-%d").strftime("%A"), md, "markdown has weekday")
    checks.in_("%.1f" % expected["score"], md, "markdown has score")
    checks.in_("Pomodoros:** %d" % expected["pomodoros_done"], md, "markdown has pomodoros")
    checks.eq(expected["pomodoros_done"], 2, "expected pomodoros == 2")
    checks.eq(expected["focus_seconds"], 7200, "scoring focus_seconds sanity == 7200s")
    checks.in_("## AI Tokens", md, "markdown has AI Tokens section")
    checks.in_("812,345", md, "markdown has token input")
    checks.in_("6,200", md, "markdown has token output")
    checks.in_("818,545", md, "markdown has token total")
    checks.in_("Requests: 2", md, "markdown has token requests")
    checks.in_("## Top Apps", md, "markdown has Top Apps section")
    checks.in_("code.exe", md, "markdown top apps has code.exe")
    checks.in_("| App | Category | Time |", md, "markdown has table header")
    checks.in_("## Coach", md, "markdown has coach section")
    if expected_coach:
        checks.in_(expected_coach[:15], md, "markdown embeds coach paragraph")

    # -- write_entry --------------------------------------------------------- #
    entry_path = diary.write_entry(con, CFG, TODAY, str(repo_dir))
    checks.eq(entry_path, str(repo_dir / "diary" / ("%s.md" % TODAY)), "write_entry path")
    p = Path(entry_path)
    checks.ok(p.is_file(), "entry file created")
    checks.eq(p.read_text(encoding="utf-8"), md, "entry file content matches build_entry")

    # -- write_index --------------------------------------------------------- #
    index_path = diary.write_index(con, CFG, str(repo_dir), days=30)
    checks.eq(index_path, str(repo_dir / "diary" / "README.md"), "write_index path")
    idx = Path(index_path)
    checks.ok(idx.is_file(), "index file created")
    idx_text = idx.read_text(encoding="utf-8")
    checks.in_("[%s](%s.md)" % (TODAY, TODAY), idx_text, "index links today's entry")

    # -- build_json ---------------------------------------------------------- #
    data = diary.build_json(con, CFG, days=30)
    checks.eq(set(data.keys()), {"generated_at", "totals", "days"}, "data.json top keys")
    checks.eq(data["totals"]["days"], 30, "totals.days == 30")
    checks.eq(
        data["totals"]["tokens_total"], 818545 + 110, "totals.tokens_total sums all days"
    )
    checks.eq(data["totals"]["pomodoros_total"], 2, "totals.pomodoros_total")
    checks.ok(isinstance(data["totals"]["score_avg"], float), "totals.score_avg is float")

    days = data["days"]
    checks.eq(len(days), 30, "days length == 30")
    checks.eq(days[0]["day"], TODAY, "days newest-first (today first)")
    checks.eq(days[1]["day"], YDAY, "days newest-first (yesterday second)")
    checks.ok(
        all(
            datetime.strptime(days[i]["day"], "%Y-%m-%d")
            > datetime.strptime(days[i + 1]["day"], "%Y-%m-%d")
            for i in range(len(days) - 1)
        ),
        "days strictly descending",
    )

    today_rec = days[0]
    checks.eq(
        set(today_rec.keys()),
        {
            "day", "weekday", "score", "stars", "pomodoros", "deep_work_seconds",
            "productive_seconds", "neutral_seconds", "distracting_seconds",
            "idle_seconds", "tokens", "top_apps", "coach",
        },
        "day record keys match schema",
    )
    checks.eq(today_rec["score"], expected["score"], "record score matches scoring")
    checks.eq(today_rec["stars"], expected["stars"], "record stars matches scoring")
    checks.eq(today_rec["pomodoros"], 2, "record pomodoros")
    checks.eq(today_rec["deep_work_seconds"], expected["productive_seconds"], "record deep_work_seconds == productive")
    checks.eq(today_rec["deep_work_seconds"], 12000, "record deep_work_seconds == 12000")
    checks.eq(today_rec["productive_seconds"], expected["productive_seconds"], "record productive")
    checks.eq(today_rec["idle_seconds"], 600, "record idle")
    checks.eq(today_rec["tokens"], {"input": 812345, "output": 6200, "total": 818545, "requests": 2}, "record tokens")
    checks.eq(today_rec["top_apps"][0], {"app": "code.exe", "category": "productive", "seconds": 12000}, "top app #1")
    checks.eq(today_rec["top_apps"][1], {"app": "chrome.exe", "category": "distracting", "seconds": 4800}, "top app #2")
    checks.eq(today_rec["top_apps"][2], {"app": "explorer.exe", "category": "neutral", "seconds": 900}, "top app #3")

    # missing-data day -> zeros / empty lists, entry never dropped
    old_rec = next(d for d in days if d["day"] == OLD)
    checks.eq(old_rec["score"], 0.0, "empty day score == 0.0")
    checks.eq(old_rec["stars"], 0, "empty day stars == 0")
    checks.eq(old_rec["tokens"], {"input": 0, "output": 0, "total": 0, "requests": 0}, "empty day tokens zeros")
    checks.eq(old_rec["top_apps"], [], "empty day top_apps empty list")
    checks.eq(old_rec["productive_seconds"], 0, "empty day productive == 0")

    # -- write_json ---------------------------------------------------------- #
    json_path = diary.write_json(con, CFG, str(repo_dir), days=30)
    checks.eq(json_path, str(repo_dir / "data.json"), "write_json path")
    jp = Path(json_path)
    checks.ok(jp.is_file(), "data.json created")
    loaded = json.loads(jp.read_text(encoding="utf-8"))
    checks.eq(loaded["days"][0]["day"], TODAY, "data.json newest-first")
    checks.eq(loaded["days"][0]["tokens"]["total"], 818545, "data.json today tokens")

    con.close()
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)

    # -- report -------------------------------------------------------------- #
    if checks.failures:
        print("FAILED (%d checks failed, %d passed):" % (len(checks.failures), checks.passed))
        for failure in checks.failures:
            print("  - " + failure)
        return 1
    print("OK (%d checks passed)" % checks.passed)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
