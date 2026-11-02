"""scoring.py -- pure metrics for Pips.

Implements the `scoring.py` section of CONTRACT.md. No I/O happens at import
time; every function only reads the ``sqlite3`` connection passed to it (plus an
optional best-effort read of ``config.json`` for the two helpers whose contract
signatures do not receive a config).

Tolerates an empty / partially-populated database: missing tables, missing rows
and ``NULL`` columns all degrade to sane zeros rather than raising.

Score rule (per CONTRACT.md, verbatim):

    score = clamp(0..100, 100 * (productive + 0.3*neutral)
                           / max(1, productive + neutral + distracting*1.5))

then adjusted for the idle ratio and pomodoro completion. Monotonicity is kept:
with neutral/distracting fixed, adding productive time raises the numerator by
the same amount the denominator grows with, and the derivative

    d/dp [ (p + a) / (p + C) ] = (C - a) / (p + C)^2   with a = 0.3*n,
                                                        C = n + 1.5*d
    C - a = 0.7*n + 1.5*d >= 0

so more productive time never lowers the score unless distraction (or neutral)
also grows. XP = productive_minutes//25 * 10 + pomodoros_done * 20
                 + (50 if goal_met else 0).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

__all__ = [
    "categorize",
    "compute_daily",
    "score_to_stars",
    "aggregate_hours",
    "hour_matrix",
    "current_streak",
    "save_daily",
    "breakdown",
]

# Weighted coefficient applied to neutral time in the numerator is taken from
# config (default 0.3). The distraction suppression factor is fixed at 1.5 per
# CONTRACT.md.
_DISTRACTION_FACTOR = 1.5
_IDLE_PENALTY = 0.25   # max fraction the idle ratio may remove from the base
_POMO_BONUS_MAX = 5.0  # max points awarded for completing the pomodoro goal

_VALID_CATEGORIES = ("productive", "neutral", "distracting")

_CONFIG_CACHE: dict | None = None


# --------------------------------------------------------------------------- #
# config helpers (best-effort; never fatal)
# --------------------------------------------------------------------------- #
def _default_config() -> dict:
    """Lazily load config.json from the project folder; cache the result.

    Used by ``aggregate_hours`` / ``breakdown`` whose contract signatures do not
    receive ``cfg``. Failures return an empty dict so callers degrade safely.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        try:
            with open(Path(__file__).parent / "config.json", encoding="utf-8") as fh:
                data = json.load(fh)
            _CONFIG_CACHE = data if isinstance(data, dict) else {}
        except Exception:  # pragma: no cover - missing/invalid config is fine
            _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _scoring_weights(cfg: dict) -> dict:
    scoring = (cfg or {}).get("scoring") or {}
    weights = scoring.get("weights") or {}
    return {
        "productive": float(weights.get("productive", 1.0)),
        "neutral": float(weights.get("neutral", 0.3)),
        "distracting": float(weights.get("distracting", -0.8)),
    }


def _goals(cfg: dict) -> dict:
    return (cfg or {}).get("goals") or {}


# --------------------------------------------------------------------------- #
# categorisation
# --------------------------------------------------------------------------- #
def categorize(app: str, title: str, cfg: dict) -> str:
    """Return "productive" | "neutral" | "distracting" for an app/title pair.

    ``title_rules`` are consulted first, then the ``category`` app lists, finally
    falling back to "neutral".
    """
    cfg = cfg or {}
    app_l = (app or "").lower()
    title_l = (title or "").lower()

    for rule in cfg.get("title_rules") or []:
        if not isinstance(rule, dict):
            continue
        match = str(rule.get("match", "") or "").lower()
        if not match:
            continue
        where = str(rule.get("where", "title") or "title").lower()
        if where == "app":
            haystack = app_l
        elif where in ("both", "any", "all"):
            haystack = app_l + " " + title_l
        else:  # "title" (default)
            haystack = title_l
        if match in haystack:
            cat = str(rule.get("category", "") or "")
            if cat in _VALID_CATEGORIES:
                return cat

    category_lists = cfg.get("category") or {}
    for cat in _VALID_CATEGORIES:
        names = category_lists.get(cat) or []
        if any(app_l == str(name).lower() for name in names):
            return cat

    return "neutral"


def _productive_apps(cfg: dict) -> set[str]:
    names = ((cfg or {}).get("category") or {}).get("productive") or []
    return {str(name).lower() for name in names}


# --------------------------------------------------------------------------- #
# low-level readers (all defensive)
# --------------------------------------------------------------------------- #
def _app_rows(con, day: str):
    """-> [(app, title, seconds), ...] for a day; [] when the table is absent."""
    try:
        cur = con.execute(
            "SELECT app, title, seconds FROM app_usage WHERE day = ?", (day,)
        )
        return cur.fetchall()
    except sqlite3.Error:
        return []


def _meta_int(con, key: str) -> int:
    try:
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return 0
    if not row or row[0] is None:
        return 0
    try:
        return int(float(row[0]))
    except (TypeError, ValueError):
        return 0


def _summary_row(con, day: str):
    try:
        row = con.execute(
            "SELECT idle_seconds, goal_met, xp FROM daily_summary WHERE day = ?",
            (day,),
        ).fetchone()
        return row
    except sqlite3.Error:
        return None


def _pomodoro_count(con, day: str) -> int:
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM sessions "
            "WHERE day = ? AND kind = 'work' AND completed = 1",
            (day,),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 0


def _focus_seconds(con, day: str, cfg: dict):
    """Seconds spent in "focus hours" (productive ratio >= 0.5 within the hour).

    Returns ``None`` when there is no hour-level data so callers can fall back to
    total productive seconds.
    """
    try:
        rows = con.execute(
            "SELECT hour, app, seconds FROM hour_usage WHERE day = ?", (day,)
        ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None

    per_hour: dict[int, list[int]] = {}
    for hour, app, seconds in rows:
        secs = int(seconds or 0)
        cat = categorize(app or "", "", cfg)
        slot = per_hour.setdefault(int(hour), [0, 0, 0])  # p, n, d
        if cat == "productive":
            slot[0] += secs
        elif cat == "distracting":
            slot[2] += secs
        else:
            slot[1] += secs

    total = 0
    for p, n, d in per_hour.values():
        denom = p + n + d
        if denom > 0 and (p / denom) >= 0.5:
            total += p
    return total


# --------------------------------------------------------------------------- #
# daily computation
# --------------------------------------------------------------------------- #
def compute_daily(con, day: str, cfg: dict) -> dict:
    """Compute one day's metrics without writing anything (see CONTRACT.md)."""
    cfg = cfg or {}
    productive = neutral = distracting = 0
    app_totals: dict[str, int] = {}

    for app, title, seconds in _app_rows(con, day):
        secs = int(seconds or 0)
        name = app or "unknown"
        app_totals[name] = app_totals.get(name, 0) + secs
        cat = categorize(name, title or "", cfg)
        if cat == "productive":
            productive += secs
        elif cat == "distracting":
            distracting += secs
        else:
            neutral += secs

    summary = _summary_row(con, day)
    idle_seconds = int(summary[0]) if summary and summary[0] is not None else 0
    app_switches = _meta_int(con, "switch:%s" % day)
    pomodoros_done = _pomodoro_count(con, day)

    focus = _focus_seconds(con, day, cfg)
    focus_seconds = productive if focus is None else focus

    weights = _scoring_weights(cfg)
    goals = _goals(cfg)
    deep_goal_s = int(float(goals.get("deep_work_hours", 4) or 0) * 3600)
    max_distraction_s = int(float(goals.get("max_distraction_hours", 2) or 0) * 3600)
    pomo_goal = int(float(goals.get("pomodoros", 8) or 0))

    # --- score ------------------------------------------------------------- #
    numerator = weights["productive"] * productive + weights["neutral"] * neutral
    denominator = max(1.0, productive + neutral + distracting * _DISTRACTION_FACTOR)
    base = 100.0 * numerator / denominator

    tracked = productive + neutral + distracting + idle_seconds
    idle_ratio = (idle_seconds / tracked) if tracked > 0 else 0.0
    idle_factor = max(0.0, 1.0 - _IDLE_PENALTY * idle_ratio)

    pomo_bonus = 0.0
    if pomo_goal > 0:
        pomo_bonus = min(_POMO_BONUS_MAX, _POMO_BONUS_MAX * pomodoros_done / pomo_goal)

    score = base * idle_factor + pomo_bonus
    score = round(max(0.0, min(100.0, score)), 1)
    stars = score_to_stars(score, cfg)

    goal_met = int(
        productive >= deep_goal_s > 0 and distracting <= max_distraction_s
    )

    productive_minutes = productive // 60
    xp = productive_minutes // 25 * 10 + pomodoros_done * 20 + (50 if goal_met else 0)

    return {
        "day": day,
        "productive_seconds": productive,
        "neutral_seconds": neutral,
        "distracting_seconds": distracting,
        "idle_seconds": idle_seconds,
        "focus_seconds": focus_seconds,
        "app_switches": app_switches,
        "pomodoros_done": pomodoros_done,
        "score": score,
        "stars": stars,
        "goal_met": goal_met,
        "xp": xp,
        "breakdown": app_totals,
    }


def score_to_stars(score: float, cfg: dict) -> int:
    """Map a 0..100 score to 1..5 stars using ``scoring.star_thresholds``."""
    thresholds = ((cfg or {}).get("scoring") or {}).get("star_thresholds")
    if not thresholds:
        thresholds = [20, 40, 60, 80]
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    stars = 1
    for bound in thresholds:
        if score >= float(bound):
            stars += 1
    return max(1, min(5, stars))


# --------------------------------------------------------------------------- #
# history / aggregates
# --------------------------------------------------------------------------- #
def aggregate_hours(con, days: int = 30) -> dict:
    """-> {hour: total_seconds} over the last ``days`` days, productive only.

    Without a config argument we categorise using config.json (best effort). If
    no productive apps can be determined we fall back to summing all activity so
    the heatmap is never silently empty.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    if days <= 0:
        days = 30

    end = date.today()
    start = end - timedelta(days=days - 1)
    params = [start.isoformat(), end.isoformat()]

    productive = _productive_apps(_default_config())
    sql = "SELECT hour, app, seconds FROM hour_usage WHERE day >= ? AND day <= ?"
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error:
        return {}

    totals: dict[int, int] = {}
    fallback: dict[int, int] = {}
    for hour, app, seconds in rows:
        secs = int(seconds or 0)
        h = int(hour)
        fallback[h] = fallback.get(h, 0) + secs
        if not productive or (app or "").lower() in productive:
            totals[h] = totals.get(h, 0) + secs
    return totals if totals else fallback


def hour_matrix(con, days: int = 7) -> dict:
    """Return {"YYYY-MM-DD": {hour:int->seconds:int}} for the last `days` days ending today,
    summed across apps from hour_usage. Missing days/hours omitted. Tolerates empty DB."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    if days <= 0:
        days = 7

    end = date.today()
    start = end - timedelta(days=days - 1)
    params = [start.isoformat(), end.isoformat()]

    sql = "SELECT day, hour, seconds FROM hour_usage WHERE day >= ? AND day <= ?"
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error:
        return {}

    # Structure: {day_str: {hour: total_seconds}}
    matrix: dict[str, dict[int, int]] = {}
    for day, hour, seconds in rows:
        day_str = str(day)
        hour_int = int(hour)
        secs = int(seconds or 0)
        if day_str not in matrix:
            matrix[day_str] = {}
        matrix[day_str][hour_int] = matrix[day_str].get(hour_int, 0) + secs

    return matrix


def current_streak(con, cfg: dict) -> dict:
    """-> {"streak_days","total_xp","best_day","goal_days"} (see CONTRACT.md)."""
    cfg = cfg or {}
    goals = _goals(cfg)
    deep_goal_s = int(float(goals.get("deep_work_hours", 4) or 0) * 3600)

    day_productivity: dict[str, int] = {}
    try:
        rows = con.execute(
            "SELECT day, app, title, seconds FROM app_usage"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    for day, app, title, seconds in rows:
        day = str(day)
        secs = int(seconds or 0)
        if deep_goal_s <= 0 or categorize(app or "", title or "", cfg) == "productive":
            day_productivity[day] = day_productivity.get(day, 0) + secs

    summary_days: dict[str, tuple[int, int]] = {}
    try:
        for day, prod_s, goal_met, xp in con.execute(
            "SELECT day, productive_seconds, goal_met, xp FROM daily_summary"
        ).fetchall():
            summary_days[str(day)] = (int(prod_s or 0), int(goal_met or 0))
            day_productivity.setdefault(str(day), int(prod_s or 0))
    except sqlite3.Error:
        summary_days = {}

    if not day_productivity:
        return {"streak_days": 0, "total_xp": 0, "best_day": "", "goal_days": 0}

    goal_days = {
        day
        for day, prod in day_productivity.items()
        if deep_goal_s > 0 and prod >= deep_goal_s
    }
    goal_days |= {day for day, (_, met) in summary_days.items() if met}

    # best day by productive seconds (ties -> earliest)
    best_day = ""
    best_prod = -1
    for day in sorted(day_productivity):
        if day_productivity[day] > best_prod:
            best_prod = day_productivity[day]
            best_day = day

    # streak: consecutive goal days ending today (today may still be in progress)
    streak = 0
    cursor = date.today()
    if cursor.isoformat() not in goal_days:
        cursor -= timedelta(days=1)
    while cursor.isoformat() in goal_days:
        streak += 1
        cursor -= timedelta(days=1)

    # total xp: prefer the persisted daily_summary, else recompute
    total_xp = 0
    persisted = False
    try:
        for (xp,) in con.execute("SELECT xp FROM daily_summary").fetchall():
            if xp is not None:
                total_xp += int(xp)
                persisted = True
    except sqlite3.Error:
        persisted = False

    if not persisted:
        pomos: dict[str, int] = {}
        try:
            for day, cnt in con.execute(
                "SELECT day, COUNT(*) FROM sessions "
                "WHERE kind = 'work' AND completed = 1 GROUP BY day"
            ).fetchall():
                pomos[str(day)] = int(cnt or 0)
        except sqlite3.Error:
            pomos = {}
        for day, prod in day_productivity.items():
            met = day in goal_days
            total_xp += (prod // 60) // 25 * 10 + pomos.get(day, 0) * 20 + (50 if met else 0)

    return {
        "streak_days": streak,
        "total_xp": total_xp,
        "best_day": best_day,
        "goal_days": len(goal_days),
    }


def save_daily(con, day: str, data: dict) -> None:
    """Upsert a computed daily record into ``daily_summary``."""
    data = data or {}
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_summary (
              day TEXT PRIMARY KEY,
              productive_seconds INTEGER NOT NULL DEFAULT 0,
              neutral_seconds   INTEGER NOT NULL DEFAULT 0,
              distracting_seconds INTEGER NOT NULL DEFAULT 0,
              idle_seconds      INTEGER NOT NULL DEFAULT 0,
              focus_seconds     INTEGER NOT NULL DEFAULT 0,
              app_switches      INTEGER NOT NULL DEFAULT 0,
              pomodoros_done    INTEGER NOT NULL DEFAULT 0,
              score REAL,
              stars INTEGER,
              goal_met INTEGER NOT NULL DEFAULT 0,
              xp INTEGER NOT NULL DEFAULT 0,
              coach_text TEXT,
              updated_at TEXT
            )
            """
        )
    except sqlite3.Error:
        return

    row = {
        "day": day,
        "productive_seconds": int(data.get("productive_seconds", 0) or 0),
        "neutral_seconds": int(data.get("neutral_seconds", 0) or 0),
        "distracting_seconds": int(data.get("distracting_seconds", 0) or 0),
        "idle_seconds": int(data.get("idle_seconds", 0) or 0),
        "focus_seconds": int(data.get("focus_seconds", 0) or 0),
        "app_switches": int(data.get("app_switches", 0) or 0),
        "pomodoros_done": int(data.get("pomodoros_done", 0) or 0),
        "score": data.get("score"),
        "stars": data.get("stars"),
        "goal_met": int(data.get("goal_met", 0) or 0),
        "xp": int(data.get("xp", 0) or 0),
        "coach_text": data.get("coach_text"),
        "updated_at": data.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
    }

    try:
        con.execute(
            """
            INSERT INTO daily_summary
              (day, productive_seconds, neutral_seconds, distracting_seconds,
               idle_seconds, focus_seconds, app_switches, pomodoros_done,
               score, stars, goal_met, xp, coach_text, updated_at)
            VALUES
              (:day, :productive_seconds, :neutral_seconds, :distracting_seconds,
               :idle_seconds, :focus_seconds, :app_switches, :pomodoros_done,
               :score, :stars, :goal_met, :xp, :coach_text, :updated_at)
            ON CONFLICT(day) DO UPDATE SET
               productive_seconds = excluded.productive_seconds,
               neutral_seconds    = excluded.neutral_seconds,
               distracting_seconds = excluded.distracting_seconds,
               idle_seconds       = excluded.idle_seconds,
               focus_seconds      = excluded.focus_seconds,
               app_switches       = excluded.app_switches,
               pomodoros_done     = excluded.pomodoros_done,
               score              = excluded.score,
               stars              = excluded.stars,
               goal_met           = excluded.goal_met,
               xp                 = excluded.xp,
               coach_text         = excluded.coach_text,
               updated_at         = excluded.updated_at
            """,
            row,
        )
        con.commit()
    except sqlite3.Error:
        return


def breakdown(con, day: str) -> list:
    """-> [{"app","category","seconds"}, ...] sorted by seconds descending."""
    cfg = _default_config()
    items = []
    for app, title, seconds in _app_rows(con, day):
        name = app or "unknown"
        items.append(
            {
                "app": name,
                "category": categorize(name, title or "", cfg),
                "seconds": int(seconds or 0),
            }
        )
    items.sort(key=lambda item: item["seconds"], reverse=True)
    return items


# --------------------------------------------------------------------------- #
# smoke test -- temporary in-memory DB, no GUI, no network
# --------------------------------------------------------------------------- #
def _smoke() -> None:
    ddl = """
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
    """
    cfg = {
        "category": {
            "productive": ["code.exe", "windowsterminal.exe"],
            "distracting": ["steam.exe", "discord.exe"],
            "neutral": [],
        },
        "title_rules": [
            {"match": "youtube", "category": "distracting", "where": "title"},
            {"match": "github", "category": "productive", "where": "title"},
        ],
        "goals": {"deep_work_hours": 4, "pomodoros": 8, "max_distraction_hours": 2},
        "scoring": {
            "weights": {"productive": 1.0, "neutral": 0.3, "distracting": -0.8, "idle": 0.0},
            "star_thresholds": [20, 40, 60, 80],
        },
    }

    con = sqlite3.connect(":memory:")
    con.executescript(ddl)
    today = date.today().isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()

    con.executemany(
        "INSERT INTO app_usage (day, app, title, seconds) VALUES (?,?,?,?)",
        [
            (today, "code.exe", "main.py - Visual Studio Code", 12000),
            (today, "firefox.exe", "GitHub - pull request", 3000),
            (today, "chrome.exe", "YouTube - lofi", 4800),
            (today, "explorer.exe", "Downloads", 900),
            (yday, "code.exe", "old.py", 5000),
        ],
    )
    con.executemany(
        "INSERT INTO hour_usage (day, hour, app, seconds) VALUES (?,?,?,?)",
        [
            (today, 9, "code.exe", 3600),
            (today, 10, "code.exe", 3600),
            (today, 14, "code.exe", 1800),
            (today, 14, "chrome.exe", 3600),
            (today, 20, "chrome.exe", 4800),
        ],
    )
    con.execute(
        "INSERT INTO sessions (day, kind, task, started_at, ended_at, completed) "
        "VALUES (?,?,?,?,?,?)",
        (today, "work", "deep work", today + "T09:00:00", today + "T09:25:00", 1),
    )
    con.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("switch:%s" % today, "42"))
    con.execute("INSERT INTO daily_summary (day, idle_seconds, updated_at) VALUES (?,?,?)", (today, 600, today))

    # dependency-free copies of the config-driven helpers
    print("categorize code.exe          ->", categorize("code.exe", "x", cfg))
    print("categorize chrome+youtube    ->", categorize("chrome.exe", "YouTube", cfg))
    print("categorize chrome+github     ->", categorize("chrome.exe", "GitHub", cfg))
    print("categorize chrome (neutral)  ->", categorize("chrome.exe", "blank", cfg))

    daily = compute_daily(con, today, cfg)
    for key in (
        "day", "productive_seconds", "neutral_seconds", "distracting_seconds",
        "idle_seconds", "focus_seconds", "app_switches", "pomodoros_done",
        "score", "stars", "goal_met", "xp", "breakdown",
    ):
        assert key in daily, "missing key %s" % key
    print("compute_daily ->", {k: daily[k] for k in daily if k != "breakdown"})
    print("  breakdown   ->", daily["breakdown"])

    # monotonicity: adding productive time (same distraction) never lowers the score
    con.executemany(
        "INSERT INTO app_usage (day, app, title, seconds) VALUES (?,?,?,?)",
        [
            ("2020-01-01", "code.exe", "a", 3600),
            ("2020-01-01", "steam.exe", "b", 3600),
            ("2020-01-02", "code.exe", "a", 7200),
            ("2020-01-02", "steam.exe", "b", 3600),
        ],
    )
    s_low = compute_daily(con, "2020-01-01", cfg)["score"]
    s_high = compute_daily(con, "2020-01-02", cfg)["score"]
    print("monotonicity -> more productive:", s_low, "->", s_high)
    assert s_high >= s_low, "score is not monotonic in productive time"

    print("score_to_stars 50 ->", score_to_stars(50, cfg))
    print("aggregate_hours   ->", aggregate_hours(con, 30))
    print("current_streak    ->", current_streak(con, cfg))
    print("breakdown()       ->", breakdown(con, today))

    save_daily(con, today, daily)
    saved = con.execute("SELECT score, stars, xp FROM daily_summary WHERE day=?", (today,)).fetchone()
    print("save_daily        ->", saved)

    empty = sqlite3.connect(":memory:")
    empty.executescript(ddl)
    print("empty compute_daily ->", compute_daily(empty, "2000-01-01", cfg)["score"])
    print("empty streak        ->", current_streak(empty, cfg))
    print("SCORING SMOKE OK")


if __name__ == "__main__":
    _smoke()
