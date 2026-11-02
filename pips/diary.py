"""diary.py -- auto-generated daily productivity diary.

Implements the ``diary.py`` section of ``TOKENS_DIARY_CONTRACT.md`` (section 2).

Produces a Markdown entry per day plus a machine-readable ``data.json`` for the
web dashboard. This module is importable with **zero side effects** (stdlib
imports only; ``scoring`` / ``coach`` are imported lazily inside the functions
that use them). Every public function swallows transient failures, logs to the
``diary`` logger and returns a safe default -- nothing is ever raised to the
caller.

Files written (all relative to the passed ``repo_dir``):

* ``<repo_dir>/diary/YYYY-MM-DD.md``  -- one Markdown entry per day
* ``<repo_dir>/diary/README.md``      -- reverse-chronological index
* ``<repo_dir>/data.json``            -- dashboard data source

``deep_work_seconds`` maps to ``scoring``'s ``productive_seconds`` -- this matches
how this app's own coach and daily goals define "deep work" (see ``scoring``'s goal
check and ``coach.insights``), so the diary stays self-consistent with the widget.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

__all__ = [
    "ensure_db",
    "build_entry",
    "write_entry",
    "write_index",
    "build_json",
    "write_json",
]

_log = logging.getLogger("diary")

_TOP_APPS_LIMIT = 10
_STAR_FULL = "\u2605"   # ★
_STAR_EMPTY = "\u2606"  # ☆


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #
def _norm_day(day) -> str:
    """Best-effort normalisation of ``day`` to ``YYYY-MM-DD``."""
    try:
        return datetime.strptime(str(day), "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(day)


def _weekday(day: str) -> str:
    """Full weekday name (e.g. ``Thursday``); falls back to the raw string."""
    try:
        return datetime.strptime(_norm_day(day), "%Y-%m-%d").strftime("%A")
    except (TypeError, ValueError):
        return ""


def _recent_days(days: int) -> list[str]:
    """``days`` ISO dates ending today, **newest-first**."""
    try:
        n = int(days)
    except (TypeError, ValueError):
        n = 30
    if n <= 0:
        n = 30
    end = date.today()
    return [(end - timedelta(days=i)).isoformat() for i in range(n)]


def _fmt_hm(seconds) -> str:
    """Seconds -> ``"4h 8m"`` / ``"4h"`` / ``"8m"`` / ``"0m"``."""
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        total = 0
    hours, rem = divmod(max(0, total), 3600)
    minutes = rem // 60
    if hours and minutes:
        return "%dh %dm" % (hours, minutes)
    if hours:
        return "%dh" % hours
    return "%dm" % minutes


def _fmt_int(n) -> str:
    """1234567 -> ``"1,234,567"``."""
    try:
        return "{:,}".format(int(n or 0))
    except (TypeError, ValueError):
        return "0"


def _score_str(score) -> str:
    try:
        return "%.1f" % float(score)
    except (TypeError, ValueError):
        return "0.0"


def _stars_markup(stars) -> str:
    try:
        n = int(stars or 0)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    return _STAR_FULL * n + _STAR_EMPTY * (5 - n)


def _md_escape(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|")


# --------------------------------------------------------------------------- #
# defensive readers (scoring / coach / token_usage)
# --------------------------------------------------------------------------- #
def _zero_daily(day: str) -> dict:
    return {
        "day": day,
        "productive_seconds": 0,
        "neutral_seconds": 0,
        "distracting_seconds": 0,
        "idle_seconds": 0,
        "focus_seconds": 0,
        "app_switches": 0,
        "pomodoros_done": 0,
        "score": 0.0,
        "stars": 0,
        "goal_met": 0,
        "xp": 0,
        "breakdown": {},
    }


def _compute_daily(con, day: str, cfg: dict) -> dict:
    """``scoring.compute_daily`` with graceful degradation."""
    try:
        import scoring  # lazy: never imported at module load

        data = scoring.compute_daily(con, day, cfg)
        return data if isinstance(data, dict) else _zero_daily(day)
    except Exception:  # pragma: no cover - scoring absent or raised
        _log.exception("compute_daily failed for %s", day)
        return _zero_daily(day)


def _categorize(app: str, title: str, cfg: dict) -> str:
    try:
        import scoring

        return scoring.categorize(app or "", title or "", cfg)
    except Exception:
        return "neutral"


def _top_apps(con, day: str, cfg: dict, limit: int = _TOP_APPS_LIMIT) -> list:
    """Top apps for a day: ``[{"app","category","seconds"}]`` desc.

    Reads the ``app_usage`` table directly (defensive) and categorises with the
    **passed** ``cfg`` so results match the caller's configuration.
    """
    try:
        rows = con.execute(
            "SELECT app, title, seconds FROM app_usage WHERE day = ?", (day,)
        ).fetchall()
    except sqlite3.Error:
        return []

    totals: dict[str, list] = {}
    for app, title, seconds in rows:
        name = app or "unknown"
        try:
            secs = int(seconds or 0)
        except (TypeError, ValueError):
            secs = 0
        entry = totals.get(name)
        if entry is None:
            totals[name] = [title or "", secs]
        else:
            entry[1] += secs
            if title:
                entry[0] = title

    items = [
        {
            "app": name,
            "category": _categorize(name, title, cfg),
            "seconds": secs,
        }
        for name, (title, secs) in totals.items()
    ]
    items.sort(key=lambda item: (-item["seconds"], item["app"]))
    return items[:limit]


def _tokens_for_day(con, day: str) -> dict:
    """Aggregate ``token_usage`` for a day; zeros when the table is absent."""
    zero = {"input": 0, "output": 0, "total": 0, "requests": 0}
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
            "COALESCE(SUM(total_tokens), 0), COUNT(*) "
            "FROM token_usage WHERE day = ?",
            (day,),
        ).fetchone()
    except sqlite3.Error:
        return dict(zero)
    if not row:
        return dict(zero)
    try:
        inp = int(row[0] or 0)
        out = int(row[1] or 0)
        total = int(row[2] or 0)
        req = int(row[3] or 0)
    except (TypeError, ValueError):
        return dict(zero)
    if total <= 0 and (inp or out):
        total = inp + out
    return {"input": inp, "output": out, "total": total, "requests": req}


def _coach_text(con, day: str, cfg: dict) -> str:
    """``coach.daily_coach`` with graceful degradation to an empty string."""
    try:
        import coach  # lazy

        text = coach.daily_coach(con, day, cfg)
        return str(text).strip() if text else ""
    except Exception:  # pragma: no cover - coach absent or raised
        _log.exception("daily_coach failed for %s", day)
        return ""


def _day_record(con, cfg: dict, day: str) -> dict:
    """One ``data.json`` day object (missing data -> zeros / empty lists)."""
    day = _norm_day(day)
    daily = _compute_daily(con, day, cfg)
    tokens = _tokens_for_day(con, day)
    top_apps = _top_apps(con, day, cfg)

    score = float(daily.get("score") or 0.0)
    stars = int(daily.get("stars") or 0)
    # A genuinely empty day (no activity / tokens / pomodoros) is reported as
    # zeros per contract -- note ``scoring.score_to_stars`` clamps to a 1-star
    # minimum, so we normalise only truly-empty days (never a low-scoring one).
    empty = (
        not top_apps
        and not tokens["total"]
        and not tokens["requests"]
        and not daily.get("productive_seconds")
        and not daily.get("neutral_seconds")
        and not daily.get("distracting_seconds")
        and not daily.get("idle_seconds")
        and not daily.get("pomodoros_done")
    )
    if empty:
        score = 0.0
        stars = 0

    return {
        "day": day,
        "weekday": _weekday(day),
        "score": score,
        "stars": stars,
        "pomodoros": int(daily.get("pomodoros_done") or 0),
        "deep_work_seconds": int(daily.get("productive_seconds") or 0),
        "productive_seconds": int(daily.get("productive_seconds") or 0),
        "neutral_seconds": int(daily.get("neutral_seconds") or 0),
        "distracting_seconds": int(daily.get("distracting_seconds") or 0),
        "idle_seconds": int(daily.get("idle_seconds") or 0),
        "tokens": tokens,
        "top_apps": _top_apps(con, day, cfg),
        "coach": _coach_text(con, day, cfg),
    }


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def ensure_db(con) -> None:
    """No-op: the diary only reads existing tables. Never raises."""
    return None


def build_entry(con, cfg: dict, day: str) -> str:
    """Markdown for ONE day (header, KPI, AI tokens, top apps, coach)."""
    cfg = cfg or {}
    day = _norm_day(day)
    try:
        record = _day_record(con, cfg, day)
        return _render_markdown(record)
    except Exception:  # pragma: no cover - never raise to caller
        _log.exception("build_entry failed for %s", day)
        return "# %s\n\n_No data available._\n" % day


def _render_markdown(record: dict) -> str:
    tokens = record["tokens"]
    lines: list[str] = []
    lines.append("# %s \u2014 %s" % (record["day"], record["weekday"] or ""))
    lines.append("")
    lines.append(
        "**Score:** %s / 100 \u00b7 **Stars:** %s (%d) \u00b7 **Pomodoros:** %d "
        "\u00b7 **Deep Work:** %s"
        % (
            _score_str(record["score"]),
            _stars_markup(record["stars"]),
            record["stars"],
            record["pomodoros"],
            _fmt_hm(record["deep_work_seconds"]),
        )
    )
    lines.append("")
    lines.append("## AI Tokens")
    lines.append("")
    lines.append(
        "Input: %s \u00b7 Output: %s \u00b7 Total: %s \u00b7 Requests: %s"
        % (
            _fmt_int(tokens["input"]),
            _fmt_int(tokens["output"]),
            _fmt_int(tokens["total"]),
            _fmt_int(tokens["requests"]),
        )
    )
    lines.append("")
    lines.append("## Top Apps")
    lines.append("")
    lines.append("| App | Category | Time |")
    lines.append("| --- | --- | --- |")
    if record["top_apps"]:
        for app in record["top_apps"]:
            lines.append(
                "| %s | %s | %s |"
                % (_md_escape(app["app"]), _md_escape(app["category"]), _fmt_hm(app["seconds"]))
            )
    else:
        lines.append("| _none_ | - | - |")
    lines.append("")
    if record["coach"]:
        lines.append("## Coach")
        lines.append("")
        lines.append(record["coach"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_entry(con, cfg: dict, day: str, repo_dir: str) -> str:
    """Write ``<repo_dir>/diary/YYYY-MM-DD.md``; return the path (or "" on error)."""
    day = _norm_day(day)
    try:
        text = build_entry(con, cfg, day)
        path = Path(repo_dir) / "diary" / ("%s.md" % day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)
    except Exception:  # pragma: no cover - never raise to caller
        _log.exception("write_entry failed for %s", day)
        return ""


def write_index(con, cfg: dict, repo_dir: str, days: int = 30) -> str:
    """Write ``<repo_dir>/diary/README.md`` (reverse-chron index); return path."""
    cfg = cfg or {}
    try:
        records = [_day_record(con, cfg, d) for d in _recent_days(days)]
        lines: list[str] = []
        lines.append("# Productivity Diary")
        lines.append("")
        lines.append(
            "_Generated %s \u00b7 last %d days_"
            % (datetime.now().strftime("%Y-%m-%d %H:%M"), len(records))
        )
        lines.append("")
        lines.append("| Date | Score | Stars | Pomodoros | Deep Work | AI Tokens |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for record in records:
            lines.append(
                "| [%s](%s.md) | %s | %s | %d | %s | %s |"
                % (
                    record["day"],
                    record["day"],
                    _score_str(record["score"]),
                    _stars_markup(record["stars"]),
                    record["pomodoros"],
                    _fmt_hm(record["deep_work_seconds"]),
                    _fmt_int(record["tokens"]["total"]),
                )
            )
        lines.append("")
        path = Path(repo_dir) / "diary" / "README.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)
    except Exception:  # pragma: no cover - never raise to caller
        _log.exception("write_index failed")
        return ""


def build_json(con, cfg: dict, days: int = 30) -> dict:
    """Structured dashboard data (contract section 2 schema); days newest-first."""
    cfg = cfg or {}
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        records = [_day_record(con, cfg, d) for d in _recent_days(days)]
        scores = [r["score"] for r in records]
        score_avg = round(sum(scores) / len(scores), 1) if scores else 0.0
        totals = {
            "days": len(records),
            "score_avg": score_avg,
            "tokens_total": sum(r["tokens"]["total"] for r in records),
            "pomodoros_total": sum(r["pomodoros"] for r in records),
        }
        return {"generated_at": generated_at, "totals": totals, "days": records}
    except Exception:  # pragma: no cover - never raise to caller
        _log.exception("build_json failed")
        return {
            "generated_at": generated_at,
            "totals": {
                "days": 0,
                "score_avg": 0.0,
                "tokens_total": 0,
                "pomodoros_total": 0,
            },
            "days": [],
        }


def write_json(con, cfg: dict, repo_dir: str, days: int = 30) -> str:
    """Write ``<repo_dir>/data.json``; return the path (or "" on error)."""
    try:
        data = build_json(con, cfg, days)
        path = Path(repo_dir) / "data.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(path)
    except Exception:  # pragma: no cover - never raise to caller
        _log.exception("write_json failed")
        return ""


# --------------------------------------------------------------------------- #
# smoke helper (opt-in; no work at import time)
# --------------------------------------------------------------------------- #
def _smoke() -> None:  # pragma: no cover
    con = sqlite3.connect(":memory:")
    print("ensure_db   ->", ensure_db(con))
    print("build_entry ->\n" + build_entry(con, {}, date.today().isoformat()))
    print("build_json  ->", build_json(con, {}, days=3)["totals"]) 


if __name__ == "__main__":  # pragma: no cover
    _smoke()
