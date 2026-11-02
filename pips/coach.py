"""coach.py -- narrative insights and the weekly report.

Implements the `coach.py` section of CONTRACT.md. Rule-based insights always
work offline; an optional OpenAI-compatible LLM is used by ``daily_coach`` only
when explicitly enabled in the config *and* an API key is present, and it always
falls back to the rule-based output on any error.

No network or GUI work happens at import time.
"""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import scoring

__all__ = [
    "insights",
    "daily_coach",
    "weekly_report",
    "monthly_report",
    "top_distraction",
    "best_focus_hour",
]

_LLM_TIMEOUT = 20
_CFG_CACHE: dict | None = None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _default_config() -> dict:
    """Best-effort config.json read for the cfg-less contract signatures."""
    global _CFG_CACHE
    if _CFG_CACHE is None:
        try:
            with open(Path(__file__).parent / "config.json", encoding="utf-8") as fh:
                data = json.load(fh)
            _CFG_CACHE = data if isinstance(data, dict) else {}
        except Exception:  # pragma: no cover
            _CFG_CACHE = {}
    return _CFG_CACHE


def _fmt(seconds) -> str:
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours and minutes:
        return "%dh %dm" % (hours, minutes)
    if hours:
        return "%dh" % hours
    if minutes:
        return "%dm" % minutes
    return "%ds" % seconds


def _day_str(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b %Y")
    except (TypeError, ValueError):
        return str(day)


def _shift(day: str, delta: int) -> str:
    try:
        base = datetime.strptime(day, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        base = date.today()
    return (base + timedelta(days=delta)).isoformat()


def _empty_day(daily: dict) -> bool:
    return not any(
        daily.get(key, 0)
        for key in (
            "productive_seconds",
            "neutral_seconds",
            "distracting_seconds",
            "idle_seconds",
        )
    )


def _top_app(con, day: str, cfg: dict) -> tuple[str | None, int]:
    best_app, best_secs = None, 0
    for app, title, seconds in scoring._app_rows(con, day):
        secs = int(seconds or 0)
        if secs > best_secs:
            best_app, best_secs = app, secs
    return best_app, best_secs


def _avg_productive(con, end_day: str, days: int, cfg: dict) -> float:
    total = 0
    for offset in range(days):
        total += scoring.compute_daily(con, _shift(end_day, -offset), cfg)["productive_seconds"]
    return total / days if days else 0.0


# --------------------------------------------------------------------------- #
# chart helpers (email-safe, table-based) - module level
# --------------------------------------------------------------------------- #
# Palette constants
_PAL = {
    "P": "#1a7f37",      # productive
    "N": "#b8860b",      # neutral / gold
    "D": "#b3261e",      # distracting
    "ACCENT": "#8b1a1a", # accent red
    "INK": "#1a1a1a",    # primary text
    "MUTED": "#6b6b6b",  # muted text
    "BORDER": "#d4cfc7", # border
    "TRACK": "#e8e4dc",  # track/background
    "WHITE": "#ffffff",  # white
    "GOLD": "#b8860b",   # gold (stars)
    "PAPER": "#faf8f3",  # paper background
    "OUTER": "#f0ede6",  # outer background
}
_SERIF = "Georgia,'Times New Roman',serif"
_SANS = "'Segoe UI',Arial,sans-serif"
_TB = "border-collapse:collapse;mso-table-lspace:0pt;mso-table-rspace:0pt;"
_W = 600


def _ch_esc(x):
    """HTML escape helper."""
    return html.escape(str(x))


def _ch_stars(n: int) -> str:
    """Return 5 stars as BMP entities."""
    return "".join("&#9733;" if i < n else "&#9734;" for i in range(5))


def _ch_wd(s: str) -> str:
    """Return short weekday (Mon, Tue, etc.)."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%a")
    except Exception:
        return s[-2:]


def _ch_h2(t: str) -> str:
    """Section heading with bottom border."""
    return (
        f'<div style="font-family:{_SERIF};font-size:15px;font-weight:bold;color:{_PAL["INK"]};'
        f'border-bottom:1px solid {_PAL["BORDER"]};padding-bottom:4px;margin:24px 0 10px;">{_ch_esc(t)}</div>'
    )


def _ch_hbar(label: str, value, vmax, vtext: str, color: str, width: int = 600) -> str:
    """Horizontal bar chart row (label | track | value)."""
    pct = 0.0 if vmax <= 0 else max(0.0, min(100.0, 100.0 * value / vmax))
    fill = max(pct, 1.4) if value > 0 else 0.0
    rest = 100.0 - fill
    lw, tw, vw = 120, 410, 70
    return (
        f'<tr>'
        f'<td width="{lw}" valign="middle" style="font-family:{_SANS};font-size:13px;color:{_PAL["INK"]};padding:3px 10px 3px 0;">{_ch_esc(label)}</td>'
        f'<td width="{tw}" valign="middle" bgcolor="{_PAL["TRACK"]}" height="16" style="width:{tw}px;height:16px;background-color:{_PAL["TRACK"]};padding:0;">'
        f'<table role="presentation" width="{tw}" cellpadding="0" cellspacing="0" border="0" style="{_TB}"><tr>'
        f'<td width="{fill:.2f}%" height="16" bgcolor="{color}" style="width:{fill:.2f}%;height:16px;background-color:{color};">&nbsp;</td>'
        f'<td width="{rest:.2f}%" height="16" style="width:{rest:.2f}%;height:16px;">&nbsp;</td>'
        f'</tr></table></td>'
        f'<td width="{vw}" align="right" valign="middle" style="font-family:{_SANS};font-size:13px;color:{_PAL["MUTED"]};padding:3px 0 3px 10px;text-align:right;">{_ch_esc(vtext)}</td>'
        f'</tr>'
    )


def _ch_vchart(values, colors, labels, chart_h: int = 110, top=None, width: int = 600) -> str:
    """Vertical bar chart (one <td> per column, inner table for bar height)."""
    n = max(1, len(values))
    col = int(width / n)
    vmax = max(values) if values and max(values) > 0 else 1
    bars, labs, tops = [], [], []
    for i, v in enumerate(values):
        if v > 0:
            h = max(6, int(round(chart_h * (v / vmax))))
            inner = (
                f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="{_TB}">'
                f'<tr><td width="{col - 10}" height="{h}" bgcolor="{colors[i]}" '
                f'style="width:{col - 10}px;height:{h}px;background-color:{colors[i]};">&nbsp;</td></tr></table>'
            )
        else:
            inner = "&nbsp;"
        bars.append(
            f'<td width="{col}" valign="bottom" height="{chart_h}" style="vertical-align:bottom;height:{chart_h}px;text-align:center;">{inner}</td>'
        )
        labs.append(
            f'<td width="{col}" align="center" style="font-family:{_SANS};font-size:10px;color:{_PAL["MUTED"]};text-align:center;">{_ch_esc(labels[i])}</td>'
        )
        if top is not None:
            tops.append(
                f'<td width="{col}" align="center" style="font-family:{_SANS};font-size:10px;color:{_PAL["INK"]};text-align:center;">{_ch_esc(top[i])}</td>'
            )
    top_row = f'<tr>{"".join(tops)}</tr>' if top is not None else ""
    return (
        f'<table role="presentation" width="{width}" cellpadding="0" cellspacing="0" border="0" style="{_TB}">'
        f'{top_row}<tr>{"".join(bars)}</tr><tr>{"".join(labs)}</tr></table>'
    )


def _ch_stacked(segs, h: int = 18, width: int = 600) -> str:
    """Stacked horizontal bar (percentages sum to 100)."""
    tds = ""
    for pct, color in segs:
        if pct <= 0:
            continue
        tds += (
            f'<td width="{pct:.2f}%" height="{h}" bgcolor="{color}" '
            f'style="width:{pct:.2f}%;height:{h}px;background-color:{color};">&nbsp;</td>'
        )
    return (
        f'<table role="presentation" width="{width}" cellpadding="0" cellspacing="0" border="0" style="{_TB}">'
        f'<tr>{tds}</tr></table>'
    )


def _ch_kpi(items, width: int = 600) -> str:
    """KPI tiles row."""
    n = max(1, len(items))
    w = int(width / n) - 8
    tds = ""
    for k, v in items:
        tds += (
            f'<td width="{w}" valign="top" style="padding:0 8px 0 0;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="{_TB}">'
            f'<tr><td bgcolor="{_PAL["WHITE"]}" style="background-color:{_PAL["WHITE"]};border:1px solid {_PAL["BORDER"]};padding:9px 11px;">'
            f'<div style="font-family:{_SANS};font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:{_PAL["MUTED"]};">{_ch_esc(k)}</div>'
            f'<div style="font-family:{_SANS};font-size:16px;font-weight:600;color:{_PAL["INK"]};padding-top:3px;">{_ch_esc(v)}</div>'
            f'</td></tr></table></td>'
        )
    return (
        f'<table role="presentation" width="{width}" cellpadding="0" cellspacing="0" border="0" style="{_TB}">'
        f'<tr>{tds}</tr></table>'
    )


def _ch_legend(items, width: int = 600) -> str:
    """Legend with colored dots."""
    return "".join(
        f'<span style="font-family:{_SANS};font-size:11px;color:{_PAL["MUTED"]};margin-right:14px;white-space:nowrap;">'
        f'<span style="display:inline-block;width:9px;height:9px;background-color:{c};margin-right:5px;vertical-align:middle;">&nbsp;</span>{_ch_esc(t)}</span>'
        for c, t in items
    )


def _ch_delta(cur_total, prev_total, higher_is_better, fmt, label: str = "vs last wk") -> str:
    """Delta badge with BMP arrows.
    
    Returns inline HTML for the badge.
    - delta = cur_total - prev_total
    - improved = (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better)
    - arrow: ▲ (&#9650;) if delta>0, ▼ (&#9660;) if delta<0, &ndash; if zero
    - color: #1a7f37 if improved, #b3261e if worsened, #6b6b6b if unchanged/neutral
    - text: "<arrow> <fmt(abs(delta))> <label>" or "no change <label>"
    """
    delta = cur_total - prev_total
    if higher_is_better is None:
        # Neutral - always grey
        color = _PAL["MUTED"]
        arrow = "&ndash;"
        text = f"no change {label}" if delta == 0 else f"{fmt(abs(delta))} {label}"
    elif delta == 0:
        color = _PAL["MUTED"]
        arrow = "&ndash;"
        text = f"no change {label}"
    else:
        improved = (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better)
        if delta > 0:
            arrow = "&#9650;"  # ▲
        elif delta < 0:
            arrow = "&#9660;"  # ▼
        else:
            arrow = "&ndash;"
        if improved:
            color = _PAL["P"]
        else:
            color = _PAL["D"]
        text = f"{arrow} {fmt(abs(delta))} {label}"
    return (
        f'<span style="font-family:{_SANS};font-size:11px;color:{color};white-space:nowrap;">'
        f'{arrow} {fmt(abs(delta)) if delta != 0 else "no change"} {label}</span>'
    )


def _ch_factor(title: str, values, labels, fmt, color: str, delta=None, width: int = 600) -> str:
    """Factor section with small bar chart and optional delta badge.
    
    Args:
        title: Factor name (e.g., "Deep work")
        values: List of values for the bar chart
        labels: List of labels (e.g., weekdays)
        fmt: Format function for top labels
        color: Bar color
        delta: Optional tuple (cur_total, prev_total, higher_is_better[, label]) for delta badge
        width: Chart width
    """
    tops = [(fmt(v) if v else "") for v in values]
    delta_html = ""
    if delta is not None:
        if len(delta) == 4:
            cur_total, prev_total, higher_is_better, label = delta
        else:
            cur_total, prev_total, higher_is_better = delta
            label = "vs last wk"
        delta_html = (
            f' <span style="float:right;">{_ch_delta(cur_total, prev_total, higher_is_better, fmt, label)}</span>'
        )
    return (
        f'<div style="font-family:{_SANS};font-size:12px;font-weight:600;color:{_PAL["INK"]};margin:14px 0 4px;">'
        f'{_ch_esc(title)}{delta_html}</div>'
        + _ch_vchart(values, [color] * len(values), labels, chart_h=46, top=tops, width=width)
    )


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def top_distraction(con, day: str, cfg: dict) -> str | None:
    """App with the most distracting seconds on ``day``, or None."""
    cfg = cfg or {}
    best_app, best_secs = None, 0
    for app, title, seconds in scoring._app_rows(con, day):
        if scoring.categorize(app or "", title or "", cfg) != "distracting":
            continue
        secs = int(seconds or 0)
        if secs > best_secs:
            best_app, best_secs = app, secs
    return best_app


def best_focus_hour(con, days: int = 14) -> int | None:
    """Hour (0..23) with the most productive seconds over the last ``days`` days."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 14
    if days <= 0:
        days = 14

    end = date.today()
    start = end - timedelta(days=days - 1)
    cfg = _default_config()
    productive = scoring._productive_apps(cfg)
    try:
        rows = con.execute(
            "SELECT hour, app, seconds FROM hour_usage WHERE day >= ? AND day <= ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    except sqlite3.Error:
        return None

    totals: dict[int, int] = {}
    for hour, app, seconds in rows:
        if productive:
            if (app or "").lower() not in productive and scoring.categorize(
                app or "", "", cfg
            ) != "productive":
                continue
        secs = int(seconds or 0)
        h = int(hour)
        totals[h] = totals.get(h, 0) + secs

    if not totals:
        return None
    best_hour = max(totals, key=lambda h: (totals[h], -h))
    return best_hour if totals[best_hour] > 0 else None


def insights(con, day: str, cfg: dict) -> list[str]:
    """Rule-based, natural-language insights citing real numbers from the DB."""
    cfg = cfg or {}
    daily = scoring.compute_daily(con, day, cfg)

    if _empty_day(daily):
        line = "No activity recorded yet for %s." % _day_str(day)
        streak = scoring.current_streak(con, cfg)
        if streak["streak_days"]:
            line += " You are on a %d-day goal streak (total XP %d)." % (
                streak["streak_days"],
                streak["total_xp"],
            )
        return [line]

    goals = cfg.get("goals") or {}
    deep_goal_s = int(float(goals.get("deep_work_hours", 4) or 0) * 3600)
    dist_goal_s = int(float(goals.get("max_distraction_hours", 2) or 0) * 3600)
    pomo_goal = int(float(goals.get("pomodoros", 8) or 0))

    lines: list[str] = []

    productive_s = daily["productive_seconds"]
    lines.append(
        "You logged %s deep work today (score %s/100, %d star%s)."
        % (
            _fmt(productive_s),
            daily["score"],
            daily["stars"],
            "" if daily["stars"] == 1 else "s",
        )
    )

    if deep_goal_s > 0:
        pct = int(round(100 * productive_s / deep_goal_s))
        remaining = max(0, deep_goal_s - productive_s)
        if daily["goal_met"]:
            lines.append("Deep-work goal met: %d%% of your %sh target." % (pct, deep_goal_s // 3600))
        else:
            lines.append(
                "%d%% of your %sh deep-work goal - %s to go."
                % (pct, deep_goal_s // 3600, _fmt(remaining))
            )

    yesterday = scoring.compute_daily(con, _shift(day, -1), cfg)
    diff = productive_s - yesterday["productive_seconds"]
    if not _empty_day(yesterday):
        if diff > 0:
            lines.append("%s ahead of yesterday." % _fmt(diff))
        elif diff < 0:
            lines.append("%s behind yesterday." % _fmt(-diff))
        else:
            lines.append("Right on par with yesterday.")
    else:
        lines.append("No data for yesterday to compare against.")

    week_avg = _avg_productive(con, day, 7, cfg)
    if week_avg > 0:
        delta = productive_s - week_avg
        if abs(delta) >= 60:
            direction = "above" if delta > 0 else "below"
            lines.append(
                "%s %s your 7-day average of %s." % (_fmt(abs(delta)), direction, _fmt(week_avg))
            )

    dist = top_distraction(con, day, cfg)
    if dist:
        seconds = next(
            (int(s or 0) for a, _t, s in scoring._app_rows(con, day) if a == dist), 0
        )
        lines.append("Biggest distraction: %s (%s)." % (dist, _fmt(seconds)))
        if dist_goal_s > 0 and daily["distracting_seconds"] > dist_goal_s:
            over = daily["distracting_seconds"] - dist_goal_s
            lines.append(
                "You are %s over your %sh distraction budget."
                % (_fmt(over), dist_goal_s // 3600)
            )
    else:
        lines.append("No distracting apps logged - nice and clean.")

    hour = best_focus_hour(con)
    if hour is not None:
        lines.append("Your sharpest hour recently was %02d:00-%02d:00." % (hour, (hour + 1) % 24))

    if pomo_goal > 0:
        lines.append(
            "Pomodoros: %d of %d completed." % (daily["pomodoros_done"], pomo_goal)
        )

    if daily["app_switches"]:
        lines.append("You switched apps %d times today." % daily["app_switches"])

    streak = scoring.current_streak(con, cfg)
    if streak["streak_days"]:
        lines.append(
            "Goal streak: %d day%s (total XP %d)."
            % (streak["streak_days"], "" if streak["streak_days"] == 1 else "s", streak["total_xp"])
        )

    return lines


def daily_coach(con, day: str, cfg: dict) -> str:
    """A short coaching paragraph: LLM when enabled, else rule-based insights."""
    cfg = cfg or {}
    rule_based = " ".join(insights(con, day, cfg))

    coach_cfg = cfg.get("coach") or {}
    if not coach_cfg.get("enabled", True):
        return rule_based

    llm = coach_cfg.get("llm") or {}
    if not llm.get("enabled") or not llm.get("api_key") or not llm.get("endpoint"):
        return rule_based

    try:
        return _llm_coach(con, day, cfg, rule_based) or rule_based
    except Exception:
        return rule_based


def _llm_coach(con, day: str, cfg: dict, rule_based: str) -> str:
    import requests

    daily = scoring.compute_daily(con, day, cfg)
    streak = scoring.current_streak(con, cfg)
    context = {
        "day": day,
        "productive": _fmt(daily["productive_seconds"]),
        "neutral": _fmt(daily["neutral_seconds"]),
        "distracting": _fmt(daily["distracting_seconds"]),
        "idle": _fmt(daily["idle_seconds"]),
        "score": daily["score"],
        "stars": daily["stars"],
        "goal_met": bool(daily["goal_met"]),
        "pomodoros_done": daily["pomodoros_done"],
        "app_switches": daily["app_switches"],
        "top_distraction": top_distraction(con, day, cfg),
        "best_focus_hour": best_focus_hour(con),
        "streak_days": streak["streak_days"],
    }
    llm = (cfg.get("coach") or {}).get("llm") or {}
    payload = {
        "model": llm.get("model") or "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a concise productivity coach. Using only the supplied "
                    "numbers, write 2-3 short sentences: one win, one concrete "
                    "suggestion. No lists, no markdown."
                ),
            },
            {"role": "user", "content": json.dumps(context, separators=(",", ":"))},
        ],
        "temperature": 0.4,
    }
    response = requests.post(
        llm["endpoint"],
        headers={
            "Authorization": "Bearer %s" % llm["api_key"],
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_LLM_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"]["content"]
    return str(text).strip() if text else ""


def weekly_report(con, cfg: dict, end_day: str) -> str:
    """A fully self-contained HTML report for the 7 days ending at ``end_day``.
    
    Email-safe: table/`<td>` charts only (no SVG/images/flexbox/grid).
    Newspaper style matching daily_chart_report_html palette.
    """
    cfg = cfg or {}
    days = [_shift(end_day, -offset) for offset in range(6, -1, -1)]
    prev_days = [_shift(end_day, -offset) for offset in range(13, 7, -1)]

    per_day = []
    total = {"productive_seconds": 0, "neutral_seconds": 0, "distracting_seconds": 0,
             "idle_seconds": 0, "focus_seconds": 0, "pomodoros_done": 0, "app_switches": 0, "xp": 0}
    for day in days:
        d = scoring.compute_daily(con, day, cfg)
        per_day.append(d)
        for key in total:
            total[key] += d.get(key, 0)

    prev_total = {"productive_seconds": 0, "neutral_seconds": 0, "distracting_seconds": 0,
                  "idle_seconds": 0, "focus_seconds": 0, "pomodoros_done": 0, "app_switches": 0, "xp": 0}
    for day in prev_days:
        d = scoring.compute_daily(con, day, cfg)
        for key in prev_total:
            prev_total[key] += d.get(key, 0)

    scores = [d["score"] for d in per_day]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    avg_stars = scoring.score_to_stars(avg_score, cfg)
    prev_scores = [scoring.compute_daily(con, day, cfg)["score"] for day in prev_days]
    prev_avg_score = round(sum(prev_scores) / len(prev_scores), 1) if prev_scores else 0.0
    
    streak = scoring.current_streak(con, cfg)
    focus_hour = best_focus_hour(con, days=7)
    distraction = top_distraction(con, end_day, cfg)
    best_day = max(per_day, key=lambda d: d["score"]) if per_day else None

    P, N, D = _PAL["P"], _PAL["N"], _PAL["D"]
    ACCENT, MUTED = _PAL["ACCENT"], _PAL["MUTED"]
    TRACK, GOLD = _PAL["TRACK"], _PAL["GOLD"]

    def dur(v):
        return _fmt(int(v))

    def cnt(v):
        return str(int(v))

    active = total["productive_seconds"] + total["neutral_seconds"] + total["distracting_seconds"]
    active = active or 1
    p_pct = 100.0 * total["productive_seconds"] / active
    n_pct = 100.0 * total["neutral_seconds"] / active
    d_pct = 100.0 * total["distracting_seconds"] / active

    score_delta = _ch_delta(avg_score, prev_avg_score, True, lambda x: f"{x:.1f}", "vs last wk")

    app_totals = {}
    for day in days:
        for app, title, seconds in scoring._app_rows(con, day):
            app_totals[app] = app_totals.get(app, 0) + int(seconds or 0)
    top_apps = sorted(app_totals.items(), key=lambda x: x[1], reverse=True)[:7]
    app_max = max([s for _, s in top_apps], default=0) or 1
    app_bars = "".join(
        _ch_hbar(app, secs, app_max, _fmt(secs),
                 P if scoring.categorize(app, "", cfg) == "productive" else (D if scoring.categorize(app, "", cfg) == "distracting" else N))
        for app, secs in top_apps
    ) or f'<tr><td style="font-family:{_SANS};font-size:12px;color:{MUTED};">No app activity recorded.</td></tr>'

    factor_days = [_ch_wd(td) for td in days]
    deep_vals = [scoring.compute_daily(con, td, cfg)["productive_seconds"] for td in days]
    dist_vals = [scoring.compute_daily(con, td, cfg)["distracting_seconds"] for td in days]
    neutral_vals = [scoring.compute_daily(con, td, cfg)["neutral_seconds"] for td in days]
    idle_vals = [scoring.compute_daily(con, td, cfg)["idle_seconds"] for td in days]
    switch_vals = [scoring.compute_daily(con, td, cfg)["app_switches"] for td in days]
    focus_vals = [scoring.compute_daily(con, td, cfg)["focus_seconds"] for td in days]

    deep_delta = (total["productive_seconds"], prev_total["productive_seconds"], True, "vs last wk")
    dist_delta = (total["distracting_seconds"], prev_total["distracting_seconds"], False, "vs last wk")
    neutral_delta = (total["neutral_seconds"], prev_total["neutral_seconds"], None, "vs last wk")
    idle_delta = (total["idle_seconds"], prev_total["idle_seconds"], False, "vs last wk")
    switch_delta = (total["app_switches"], prev_total["app_switches"], False, "vs last wk")
    focus_delta = (total["focus_seconds"], prev_total["focus_seconds"], True, "vs last wk")

    style = (
        "body{font-family:'Segoe UI',Arial,sans-serif;background:#f0ede6;color:#1a1a1a;margin:0;padding:24px;}"
        "ul{margin:8px 0 0 18px;padding:0;font-size:13px;line-height:1.65;}"
    )

    best_day_line = (
        "%s (%s, score %s)" % (_day_str(best_day["day"]), _fmt(best_day["productive_seconds"]), best_day["score"])
        if best_day
        else "no data"
    )
    focus_line = "%02d:00-%02d:00" % (focus_hour, (focus_hour + 1) % 24) if focus_hour is not None else "n/a"

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Weekly Productivity Report</title>'
        '<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->'
        f'<style>{style}</style></head>'
        '<body style="margin:0;padding:24px;background:#f0ede6;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center">'
        f'<table role="presentation" width="660" cellpadding="0" cellspacing="0" border="0" style="width:660px;{_TB}">'
        '<tr><td style="background:#faf8f3;border:1px solid #d4cfc7;padding:26px 30px;">'
        '<div style="font-family:Georgia,serif;font-size:16px;letter-spacing:.14em;text-transform:uppercase;color:#8b1a1a;border-bottom:3px solid #8b1a1a;padding-bottom:6px;">The Productivity Report</div>'
        '<div style="border-bottom:1px solid #8b1a1a;margin-bottom:16px;"></div>'
        f'<div style="font-family:{_SERIF};font-size:20px;font-weight:bold;color:{_PAL["INK"]};margin:10px 0 2px;">WEEKLY EDITION</div>'
        f'<div style="font-family:{_SANS};font-size:13px;color:{MUTED};margin-bottom:14px;">{_ch_esc(_day_str(days[0]))} &middot; to &middot; {_ch_esc(_day_str(days[-1]))}</div>'
        f'<div style="font-family:{_SERIF};font-size:42px;font-weight:bold;color:{ACCENT};line-height:1;">{avg_score}<span style="font-size:16px;color:{MUTED};font-weight:normal;"> / 100</span></div>'
        f'<div style="font-family:{_SANS};font-size:22px;color:{GOLD};letter-spacing:3px;margin:6px 0 6px;">{_ch_stars(avg_stars)}</div>'
        f'<div style="font-family:{_SANS};font-size:11px;color:{MUTED};margin-bottom:10px;">{score_delta}</div>'
        f'<table role="presentation" width="{_W}" cellpadding="0" cellspacing="0" border="0" style="{_TB}"><tr>'
        f'<td width="{avg_score:.2f}%" height="14" bgcolor="{ACCENT}" style="width:{avg_score:.2f}%;height:14px;background-color:{ACCENT};">&nbsp;</td>'
        f'<td width="{100-avg_score:.2f}%" height="14" bgcolor="{TRACK}" style="width:{100-avg_score:.2f}%;height:14px;background-color:{TRACK};">&nbsp;</td>'
        f'</tr></table>'
        '<div style="height:18px;"></div>'
        + _ch_kpi([("Deep work", _fmt(total["productive_seconds"])), ("Distraction", _fmt(total["distracting_seconds"])),
                   ("Idle", _fmt(total["idle_seconds"])), ("Pomodoros", total["pomodoros_done"]), ("XP earned", total["xp"])])
        + _ch_h2("Time split (week)")
        + _ch_stacked([(p_pct, P), (n_pct, N), (d_pct, D)])
        + f'<div style="margin-top:7px;">{_ch_legend([(P, "productive " + _fmt(total["productive_seconds"])), (N, "neutral " + _fmt(total["neutral_seconds"])), (D, "distracting " + _fmt(total["distracting_seconds"]))])}</div>'
        + _ch_h2("Daily scores - last 7 days")
        + _ch_vchart(scores, [ACCENT] * 7, factor_days, chart_h=110,
                     top=[(str(int(v)) if v else "") for v in scores])
        + _ch_h2("Top apps (week)")
        + f'<table role="presentation" width="{_W}" cellpadding="0" cellspacing="0" border="0" style="{_TB}">{app_bars}</table>'
        + _ch_h2("Daily factors - last 7 days")
        + _ch_factor("Deep work", deep_vals, factor_days, dur, P, delta=deep_delta)
        + _ch_factor("Distraction", dist_vals, factor_days, dur, D, delta=dist_delta)
        + _ch_factor("Neutral", neutral_vals, factor_days, dur, N, delta=neutral_delta)
        + _ch_factor("Idle", idle_vals, factor_days, dur, MUTED, delta=idle_delta)
        + _ch_factor("App switches", switch_vals, factor_days, cnt, ACCENT, delta=switch_delta)
        + _ch_factor("Focus", focus_vals, factor_days, dur, P, delta=focus_delta)
        + f'<div style="font-family:{_SANS};font-size:11px;color:{MUTED};margin-top:24px;border-top:1px solid {_PAL["BORDER"]};padding-top:10px;">'
        + f'Best day: {_ch_esc(best_day_line)} &middot; Best focus hour {focus_line} &middot; Top distraction {_ch_esc(distraction or "none")} &middot; Streak {streak["streak_days"]} days &middot; All data stays local.</div>'
        '</td></tr></table></td></tr></table></body></html>'
    )


def monthly_report(con, cfg: dict, end_day: str) -> str:
    """A fully self-contained HTML monthly report.
    
    Email-safe: table/`<td>` charts only (no SVG/images/flexbox/grid).
    Newspaper style matching weekly_report / daily_chart_report_html palette.
    
    Period: calendar month containing end_day (day 1 → last day of that month,
    clipped to today if end_day is in the current month).
    Deltas: month-over-month (this month vs previous calendar month).
    """
    cfg = cfg or {}
    
    try:
        end = datetime.strptime(end_day, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        end = date.today()
    
    # Calendar month: 1st to last day, clipped to today if in current month
    month_start = end.replace(day=1)
    if end.year == date.today().year and end.month == date.today().month:
        month_end = date.today()
    else:
        # Last day of month: first day of next month minus 1 day
        if end.month == 12:
            next_month = end.replace(year=end.year + 1, month=1, day=1)
        else:
            next_month = end.replace(month=end.month + 1, day=1)
        month_end = next_month - timedelta(days=1)
    
    # Build list of days in the month
    days_in_month = []
    cur = month_start
    while cur <= month_end:
        days_in_month.append(cur.isoformat())
        cur += timedelta(days=1)
    
    # Previous calendar month
    if month_start.month == 1:
        prev_month_start = month_start.replace(year=month_start.year - 1, month=12, day=1)
    else:
        prev_month_start = month_start.replace(month=month_start.month - 1, day=1)
    prev_month_end = month_start - timedelta(days=1)
    
    prev_days_in_month = []
    cur = prev_month_start
    while cur <= prev_month_end:
        prev_days_in_month.append(cur.isoformat())
        cur += timedelta(days=1)
    
    # Compute totals for this month
    per_day = []
    total = {
        "productive_seconds": 0, "neutral_seconds": 0, "distracting_seconds": 0,
        "idle_seconds": 0, "focus_seconds": 0, "pomodoros_done": 0,
        "app_switches": 0, "xp": 0
    }
    for day in days_in_month:
        try:
            d = scoring.compute_daily(con, day, cfg)
            per_day.append(d)
            for key in total:
                total[key] += d.get(key, 0)
        except Exception:
            per_day.append({
                "productive_seconds": 0, "neutral_seconds": 0, "distracting_seconds": 0,
                "idle_seconds": 0, "focus_seconds": 0, "pomodoros_done": 0,
                "app_switches": 0, "xp": 0, "score": 0, "stars": 0
            })
    
    # Compute totals for previous month
    prev_total = {
        "productive_seconds": 0, "neutral_seconds": 0, "distracting_seconds": 0,
        "idle_seconds": 0, "focus_seconds": 0, "pomodoros_done": 0,
        "app_switches": 0, "xp": 0
    }
    for day in prev_days_in_month:
        try:
            d = scoring.compute_daily(con, day, cfg)
            for key in prev_total:
                prev_total[key] += d.get(key, 0)
        except Exception:
            pass
    
    # Average score for the month
    scores = [d["score"] for d in per_day if d.get("score", 0) > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    avg_stars = scoring.score_to_stars(avg_score, cfg)
    
    # Previous month average score
    prev_scores = []
    for day in prev_days_in_month:
        try:
            d = scoring.compute_daily(con, day, cfg)
            if d.get("score", 0) > 0:
                prev_scores.append(d["score"])
        except Exception:
            pass
    prev_avg_score = round(sum(prev_scores) / len(prev_scores), 1) if prev_scores else 0.0
    
    # Streak, focus hour, distraction, best day
    try:
        streak = scoring.current_streak(con, cfg)
    except Exception:
        streak = {"streak_days": 0, "total_xp": 0}
    
    try:
        focus_hour = best_focus_hour(con, days=len(days_in_month))
    except Exception:
        focus_hour = None
    
    try:
        distraction = top_distraction(con, end_day, cfg)
    except Exception:
        distraction = None
    
    best_day = max(per_day, key=lambda d: d["score"]) if per_day and any(d.get("score", 0) > 0 for d in per_day) else None
    
    # Palette
    P, N, D = _PAL["P"], _PAL["N"], _PAL["D"]
    ACCENT, MUTED = _PAL["ACCENT"], _PAL["MUTED"]
    TRACK, GOLD = _PAL["TRACK"], _PAL["GOLD"]
    
    def dur(v):
        return _fmt(int(v))
    
    def cnt(v):
        return str(int(v))
    
    # Time split percentages
    active = total["productive_seconds"] + total["neutral_seconds"] + total["distracting_seconds"]
    active = active or 1
    p_pct = 100.0 * total["productive_seconds"] / active
    n_pct = 100.0 * total["neutral_seconds"] / active
    d_pct = 100.0 * total["distracting_seconds"] / active
    
    # Score delta (month-over-month)
    score_delta = _ch_delta(avg_score, prev_avg_score, True, lambda x: f"{x:.1f}", "vs last month")
    
    # Top apps for the month
    app_totals = {}
    for day in days_in_month:
        try:
            for app, title, seconds in scoring._app_rows(con, day):
                app_totals[app] = app_totals.get(app, 0) + int(seconds or 0)
        except Exception:
            pass
    top_apps = sorted(app_totals.items(), key=lambda x: x[1], reverse=True)[:7]
    app_max = max([s for _, s in top_apps], default=0) or 1
    app_bars = "".join(
        _ch_hbar(app, secs, app_max, _fmt(secs),
                 P if scoring.categorize(app, "", cfg) == "productive" else (D if scoring.categorize(app, "", cfg) == "distracting" else N))
        for app, secs in top_apps
    ) or f'<tr><td style="font-family:{_SANS};font-size:12px;color:{MUTED};">No app activity recorded.</td></tr>'
    
    # Daily factors for the month (one value per day)
    factor_days = [_ch_wd(td) for td in days_in_month]
    deep_vals = [scoring.compute_daily(con, td, cfg).get("productive_seconds", 0) for td in days_in_month]
    dist_vals = [scoring.compute_daily(con, td, cfg).get("distracting_seconds", 0) for td in days_in_month]
    neutral_vals = [scoring.compute_daily(con, td, cfg).get("neutral_seconds", 0) for td in days_in_month]
    idle_vals = [scoring.compute_daily(con, td, cfg).get("idle_seconds", 0) for td in days_in_month]
    switch_vals = [scoring.compute_daily(con, td, cfg).get("app_switches", 0) for td in days_in_month]
    focus_vals = [scoring.compute_daily(con, td, cfg).get("focus_seconds", 0) for td in days_in_month]
    
    # Month-over-month deltas for factors
    deep_delta = (total["productive_seconds"], prev_total["productive_seconds"], True, "vs last month")
    dist_delta = (total["distracting_seconds"], prev_total["distracting_seconds"], False, "vs last month")
    neutral_delta = (total["neutral_seconds"], prev_total["neutral_seconds"], None, "vs last month")
    idle_delta = (total["idle_seconds"], prev_total["idle_seconds"], False, "vs last month")
    switch_delta = (total["app_switches"], prev_total["app_switches"], False, "vs last month")
    focus_delta = (total["focus_seconds"], prev_total["focus_seconds"], True, "vs last month")
    
    # Daily scores for the month
    day_scores = [d["score"] for d in per_day]
    day_labels = [str(int(td.split("-")[2])) for td in days_in_month]  # Day number only
    
    style = (
        "body{font-family:'Segoe UI',Arial,sans-serif;background:#f0ede6;color:#1a1a1a;margin:0;padding:24px;}"
        "ul{margin:8px 0 0 18px;padding:0;font-size:13px;line-height:1.65;}"
    )
    
    # Month label
    month_label = end.strftime("%B %Y")
    month_range = "%s – %s" % (_day_str(month_start.isoformat()), _day_str(month_end.isoformat()))
    
    best_day_line = (
        "%s (%s, score %s)" % (_day_str(days_in_month[per_day.index(best_day)]), _fmt(best_day["productive_seconds"]), best_day["score"])
        if best_day
        else "no data"
    )
    focus_line = "%02d:00-%02d:00" % (focus_hour, (focus_hour + 1) % 24) if focus_hour is not None else "n/a"
    
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Monthly Productivity Report</title>'
        '<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->'
        f'<style>{style}</style></head>'
        '<body style="margin:0;padding:24px;background:#f0ede6;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center">'
        f'<table role="presentation" width="660" cellpadding="0" cellspacing="0" border="0" style="width:660px;{_TB}">'
        '<tr><td style="background:#faf8f3;border:1px solid #d4cfc7;padding:26px 30px;">'
        '<div style="font-family:Georgia,serif;font-size:16px;letter-spacing:.14em;text-transform:uppercase;color:#8b1a1a;border-bottom:3px solid #8b1a1a;padding-bottom:6px;">The Productivity Report</div>'
        '<div style="border-bottom:1px solid #8b1a1a;margin-bottom:16px;"></div>'
        f'<div style="font-family:{_SERIF};font-size:20px;font-weight:bold;color:{_PAL["INK"]};margin:10px 0 2px;">MONTHLY EDITION</div>'
        f'<div style="font-family:{_SANS};font-size:13px;color:{MUTED};margin-bottom:14px;">{_ch_esc(month_label)} &middot; {_ch_esc(month_range)}</div>'
        f'<div style="font-family:{_SERIF};font-size:42px;font-weight:bold;color:{ACCENT};line-height:1;">{avg_score}<span style="font-size:16px;color:{MUTED};font-weight:normal;"> / 100</span></div>'
        f'<div style="font-family:{_SANS};font-size:22px;color:{GOLD};letter-spacing:3px;margin:6px 0 6px;">{_ch_stars(avg_stars)}</div>'
        f'<div style="font-family:{_SANS};font-size:11px;color:{MUTED};margin-bottom:10px;">{score_delta}</div>'
        f'<table role="presentation" width="{_W}" cellpadding="0" cellspacing="0" border="0" style="{_TB}"><tr>'
        f'<td width="{avg_score:.2f}%" height="14" bgcolor="{ACCENT}" style="width:{avg_score:.2f}%;height:14px;background-color:{ACCENT};">&nbsp;</td>'
        f'<td width="{100-avg_score:.2f}%" height="14" bgcolor="{TRACK}" style="width:{100-avg_score:.2f}%;height:14px;background-color:{TRACK};">&nbsp;</td>'
        f'</tr></table>'
        '<div style="height:18px;"></div>'
        + _ch_kpi([("Deep work", _fmt(total["productive_seconds"])), ("Distraction", _fmt(total["distracting_seconds"])),
                   ("Idle", _fmt(total["idle_seconds"])), ("Pomodoros", total["pomodoros_done"]), ("XP earned", total["xp"])])
        + _ch_h2("Time split (month)")
        + _ch_stacked([(p_pct, P), (n_pct, N), (d_pct, D)])
        + f'<div style="margin-top:7px;">{_ch_legend([(P, "productive " + _fmt(total["productive_seconds"])), (N, "neutral " + _fmt(total["neutral_seconds"])), (D, "distracting " + _fmt(total["distracting_seconds"]))])}</div>'
        + _ch_h2("Daily scores - this month")
        + _ch_vchart(day_scores, [ACCENT] * len(day_scores), day_labels, chart_h=110,
                     top=[(str(int(v)) if v else "") for v in day_scores])
        + _ch_h2("Top apps (month)")
        + f'<table role="presentation" width="{_W}" cellpadding="0" cellspacing="0" border="0" style="{_TB}">{app_bars}</table>'
        + _ch_h2("Daily factors - this month")
        + _ch_factor("Deep work", deep_vals, factor_days, dur, P, delta=deep_delta)
        + _ch_factor("Distraction", dist_vals, factor_days, dur, D, delta=dist_delta)
        + _ch_factor("Neutral", neutral_vals, factor_days, dur, N, delta=neutral_delta)
        + _ch_factor("Idle", idle_vals, factor_days, dur, MUTED, delta=idle_delta)
        + _ch_factor("App switches", switch_vals, factor_days, cnt, ACCENT, delta=switch_delta)
        + _ch_factor("Focus", focus_vals, factor_days, dur, P, delta=focus_delta)
        + f'<div style="font-family:{_SANS};font-size:11px;color:{MUTED};margin-top:24px;border-top:1px solid {_PAL["BORDER"]};padding-top:10px;">'
        + f'Best day: {_ch_esc(best_day_line)} &middot; Best focus hour {focus_line} &middot; Top distraction {_ch_esc(distraction or "none")} &middot; Streak {streak["streak_days"]} days &middot; All data stays local.</div>'
        '</td></tr></table></td></tr></table></body></html>'
    )


def daily_report_html(con, cfg: dict, day: str) -> str:
    """A self-contained "today's progress" HTML report in the newspaper style."""
    cfg = cfg or {}
    d = scoring.compute_daily(con, day, cfg)
    rows = scoring.breakdown(con, day)
    streak = scoring.current_streak(con, cfg)
    lines = insights(con, day, cfg)

    def stars_markup(n: int) -> str:
        return "".join("&#9733;" if i < n else "&#9734;" for i in range(5))

    active = d["productive_seconds"] + d["neutral_seconds"] + d["distracting_seconds"]
    active = active or 1
    p_pct = 100.0 * d["productive_seconds"] / active
    n_pct = 100.0 * d["neutral_seconds"] / active
    d_pct = 100.0 * d["distracting_seconds"] / active

    style = """
    body{font-family:'Segoe UI',Arial,sans-serif;background:#f0ede6;color:#1a1a1a;margin:0;padding:24px;}
    .wrap{max-width:680px;margin:0 auto;background:#faf8f3;border:1px solid #d4cfc7;padding:26px 30px;}
    .mast{font-family:Georgia,'Times New Roman',serif;text-transform:uppercase;letter-spacing:.14em;
          font-size:13px;color:#8b1a1a;border-bottom:3px solid #8b1a1a;padding-bottom:6px;}
    .mast2{border-bottom:1px solid #8b1a1a;margin-bottom:16px;}
    h1{font-family:Georgia,'Times New Roman',serif;font-size:24px;margin:10px 0 2px;}
    .date{color:#6b6b6b;font-size:13px;margin-bottom:14px;}
    .score{font-family:Georgia,serif;font-size:42px;font-weight:700;color:#8b1a1a;line-height:1;}
    .score small{font-size:16px;color:#6b6b6b;font-weight:400;}
    .stars{color:#b8860b;font-size:22px;letter-spacing:3px;margin:6px 0 4px;}
    .cards{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0;}
    .card{background:#ffffff;border:1px solid #e8e4dc;border-radius:8px;padding:10px 14px;min-width:108px;flex:1;}
    .card .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6b6b6b;}
    .card .v{font-size:18px;font-weight:600;margin-top:3px;}
    h2{font-family:Georgia,'Times New Roman',serif;font-size:15px;border-bottom:1px solid #d4cfc7;
       padding-bottom:4px;margin:24px 0 10px;}
    table{width:100%;border-collapse:collapse;font-size:13px;}
    th,td{padding:6px 8px;text-align:left;border-bottom:1px solid #e8e4dc;}
    th{color:#6b6b6b;text-transform:uppercase;font-size:11px;font-weight:600;}
    td.num{text-align:right;}
    .bar{display:flex;height:12px;border-radius:6px;overflow:hidden;background:#e8e4dc;}
    .bar i{display:block;height:100%;}
    .p{background:#1a7f37;}.n{background:#b8860b;}.d{background:#b3261e;}
    .legend{font-size:11px;color:#6b6b6b;margin-top:7px;}
    .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle;}
    .cat-productive{color:#1a7f37;font-weight:600;}.cat-neutral{color:#b8860b;font-weight:600;}
    .cat-distracting{color:#b3261e;font-weight:600;}
    ul{margin:8px 0 0 18px;padding:0;font-size:13px;line-height:1.65;}
    .foot{color:#6b6b6b;font-size:11px;margin-top:24px;border-top:1px solid #d4cfc7;padding-top:10px;}
    """

    def card(label, value):
        return '<div class="card"><div class="k">%s</div><div class="v">%s</div></div>' % (
            html.escape(label), html.escape(str(value)),
        )

    app_rows = "".join(
        "<tr><td>%s</td><td class=\"cat-%s\">%s</td><td class=\"num\">%s</td></tr>" % (
            html.escape(r["app"]), html.escape(r["category"]), html.escape(r["category"]),
            html.escape(_fmt(r["seconds"])),
        )
        for r in rows[:8]
    ) or "<tr><td colspan=\"3\">No app activity recorded.</td></tr>"

    insight_items = "".join("<li>%s</li>" % html.escape(x) for x in lines) or \
        "<li>No activity recorded for this day yet.</li>"

    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Daily Productivity Report</title><style>%s</style></head>"
        "<body><div class=\"wrap\">"
        "<div class=\"mast\">The Productivity Report</div><div class=\"mast2\"></div>"
        "<h1>%s</h1>"
        "<div class=\"date\">Daily progress &middot; %s</div>"
        "<div class=\"score\">%s<small> / 100</small></div>"
        "<div class=\"stars\">%s</div>"
        "<div class=\"cards\">%s</div>"
        "<h2>Time split</h2>"
        "<div class=\"bar\">"
        "<i class=\"p\" style=\"width:%.1f%%\"></i>"
        "<i class=\"n\" style=\"width:%.1f%%\"></i>"
        "<i class=\"d\" style=\"width:%.1f%%\"></i></div>"
        "<div class=\"legend\">"
        "<span><i class=\"dot p\"></i>productive %s</span>&nbsp;&nbsp;"
        "<span><i class=\"dot n\"></i>neutral %s</span>&nbsp;&nbsp;"
        "<span><i class=\"dot d\"></i>distracting %s</span></div>"
        "<h2>Top apps</h2><table><thead><tr><th>App</th><th>Category</th><th>Time</th></tr>"
        "</thead><tbody>%s</tbody></table>"
        "<h2>Coach</h2><ul>%s</ul>"
        "<div class=\"foot\">Generated %s &middot; goal met: %s &middot; streak %d days &middot; "
        "XP %d &middot; all data stays local on this PC.</div>"
        "</div></body></html>"
        % (
            style,
            html.escape(_day_str(day)),
            html.escape(day),
            d["score"],
            stars_markup(d["stars"]),
            card("Deep work", _fmt(d["productive_seconds"]))
            + card("Distraction", _fmt(d["distracting_seconds"]))
            + card("Idle", _fmt(d["idle_seconds"]))
            + card("Pomodoros", d["pomodoros_done"])
            + card("App switches", d["app_switches"]),
            p_pct, n_pct, d_pct,
            html.escape(_fmt(d["productive_seconds"])),
            html.escape(_fmt(d["neutral_seconds"])),
            html.escape(_fmt(d["distracting_seconds"])),
            app_rows,
            insight_items,
            html.escape(datetime.now().strftime("%Y-%m-%d %H:%M")),
            "yes" if d.get("goal_met") else "not yet",
            streak["streak_days"],
            streak["total_xp"],
        )
    )


def daily_chart_report_html(con, cfg: dict, day: str) -> str:
    """A chart-based daily report. Email-safe: table/`<td>` charts only (no SVG/images)."""
    cfg = cfg or {}
    d = scoring.compute_daily(con, day, cfg)
    rows = scoring.breakdown(con, day)
    streak = scoring.current_streak(con, cfg)
    lines = insights(con, day, cfg)

    P, N, D = _PAL["P"], _PAL["N"], _PAL["D"]
    ACCENT, MUTED = _PAL["ACCENT"], _PAL["MUTED"]
    TRACK, GOLD = _PAL["TRACK"], _PAL["GOLD"]

    def dur(v):
        return _fmt(int(v))

    def cnt(v):
        return str(int(v))

    active = (d["productive_seconds"] + d["neutral_seconds"] + d["distracting_seconds"]) or 1
    p_pct = 100.0 * d["productive_seconds"] / active
    n_pct = 100.0 * d["neutral_seconds"] / active
    d_pct = 100.0 * d["distracting_seconds"] / active

    trend_days = [_shift(day, -k) for k in range(6, -1, -1)]
    prev_days = [_shift(day, -k) for k in range(13, 7, -1)]
    tdaily = [(td, scoring.compute_daily(con, td, cfg)) for td in trend_days]
    trend_vals = [x[1]["score"] for x in tdaily]
    trend_stars = [x[1]["stars"] for x in tdaily]

    # Compute previous 7-day totals for week-over-week deltas
    prev_totals = {}
    for key in ["productive_seconds", "distracting_seconds", "neutral_seconds", "idle_seconds", "app_switches", "focus_seconds"]:
        prev_totals[key] = sum(scoring.compute_daily(con, pd, cfg).get(key, 0) for pd in prev_days)
    cur_totals = {key: sum(x[1].get(key, 0) for x in tdaily) for key in prev_totals}

    pomo_vals = [0] * 7
    try:
        cur = con.cursor()
        for i, td in enumerate(trend_days):
            cur.execute("SELECT pomodoros_done FROM daily_summary WHERE day=?", (td,))
            row = cur.fetchone()
            if row and row[0] is not None:
                pomo_vals[i] = int(row[0])
            else:
                cur.execute("SELECT COUNT(*) FROM sessions WHERE day=? AND kind='work' AND completed=1", (td,))
                pomo_vals[i] = int((cur.fetchone() or [0])[0] or 0)
    except Exception:
        pass

    hour_vals = [0] * 24
    hour_colors = [TRACK] * 24
    try:
        hm = (scoring.hour_matrix(con, 1) or {}).get(day, {})
        cur = con.cursor()
        for h in range(24):
            hour_vals[h] = int(hm.get(h, 0))
            if hour_vals[h] > 0:
                cur.execute("SELECT app FROM hour_usage WHERE day=? AND hour=? ORDER BY seconds DESC LIMIT 1", (day, h))
                row = cur.fetchone()
                cat = scoring.categorize(row[0], "", cfg) if row else "neutral"
                hour_colors[h] = P if cat == "productive" else (D if cat == "distracting" else N)
    except Exception:
        pass

    hour_labels = [(f"{h:02d}" if h % 3 == 0 else "") for h in range(24)]

    top = rows[:7]
    amax = max([r["seconds"] for r in top], default=0) or 1
    app_bars = "".join(
        _ch_hbar(r["app"], r["seconds"], amax, _fmt(r["seconds"]),
                 P if r["category"] == "productive" else (D if r["category"] == "distracting" else N))
        for r in top
    ) or f'<tr><td style="font-family:{_SANS};font-size:12px;color:{MUTED};">No app activity recorded.</td></tr>'

    insight_items = "".join(f"<li>{_ch_esc(x)}</li>" for x in lines) or "<li>No activity recorded for this day yet.</li>"

    style = (
        "body{font-family:'Segoe UI',Arial,sans-serif;background:#f0ede6;color:#1a1a1a;margin:0;padding:24px;}"
        "ul{margin:8px 0 0 18px;padding:0;font-size:13px;line-height:1.65;}"
    )

    g_on = max(0.0, min(100.0, float(d["score"])))
    g_off = 100.0 - g_on
    goal_txt = "yes" if d.get("goal_met") else "not yet"
    star_list = " / ".join(str(s) for s in trend_stars)

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Daily Productivity Report</title>'
        '<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->'
        f'<style>{style}</style></head>'
        '<body style="margin:0;padding:24px;background:#f0ede6;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center">'
        f'<table role="presentation" width="660" cellpadding="0" cellspacing="0" border="0" style="width:660px;{_TB}">'
        '<tr><td style="background:#faf8f3;border:1px solid #d4cfc7;padding:26px 30px;">'
        '<div style="font-family:Georgia,serif;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#8b1a1a;border-bottom:3px solid #8b1a1a;padding-bottom:6px;">The Productivity Report</div>'
        '<div style="border-bottom:1px solid #8b1a1a;margin-bottom:16px;"></div>'
        f'<div style="font-family:{_SERIF};font-size:24px;color:{_PAL["INK"]};margin:10px 0 2px;">{_ch_esc(_day_str(day))}</div>'
        f'<div style="font-family:{_SANS};font-size:13px;color:{MUTED};margin-bottom:14px;">Daily progress &middot; {_ch_esc(day)}</div>'
        f'<div style="font-family:{_SERIF};font-size:42px;font-weight:bold;color:{ACCENT};line-height:1;">{d["score"]}<span style="font-size:16px;color:{MUTED};font-weight:normal;"> / 100</span></div>'
        f'<div style="font-family:{_SANS};font-size:22px;color:{GOLD};letter-spacing:3px;margin:6px 0 10px;">{_ch_stars(d["stars"])}</div>'
        f'<table role="presentation" width="{_W}" cellpadding="0" cellspacing="0" border="0" style="{_TB}"><tr>'
        f'<td width="{g_on:.2f}%" height="14" bgcolor="{ACCENT}" style="width:{g_on:.2f}%;height:14px;background-color:{ACCENT};">&nbsp;</td>'
        f'<td width="{g_off:.2f}%" height="14" bgcolor="{TRACK}" style="width:{g_off:.2f}%;height:14px;background-color:{TRACK};">&nbsp;</td>'
        f'</tr></table>'
        '<div style="height:18px;"></div>'
        + _ch_kpi([("Deep work", _fmt(d["productive_seconds"])), ("Distraction", _fmt(d["distracting_seconds"])),
                   ("Idle", _fmt(d["idle_seconds"])), ("Pomodoros", d["pomodoros_done"]), ("App switches", d["app_switches"])])
        + _ch_h2("Time split")
        + _ch_stacked([(p_pct, P), (n_pct, N), (d_pct, D)])
        + f'<div style="margin-top:7px;">{_ch_legend([(P, "productive " + _fmt(d["productive_seconds"])), (N, "neutral " + _fmt(d["neutral_seconds"])), (D, "distracting " + _fmt(d["distracting_seconds"]))])}</div>'
        + _ch_h2("Focus timeline (today)")
        + _ch_vchart(hour_vals, hour_colors, hour_labels, chart_h=66)
        + f'<div style="margin-top:6px;">{_ch_legend([(P, "productive"), (N, "neutral"), (D, "distracting"), (TRACK, "no data")])}</div>'
        + _ch_h2("Score - last 7 days")
        + _ch_vchart(trend_vals, [ACCENT] * 7, [_ch_wd(td) for td in trend_days], chart_h=110,
                     top=[(str(int(v)) if v else "") for v in trend_vals])
        + f'<div style="font-family:{_SANS};font-size:10px;color:{MUTED};margin-top:4px;">stars: {star_list}</div>'
        + _ch_h2("Top apps today")
        + f'<table role="presentation" width="{_W}" cellpadding="0" cellspacing="0" border="0" style="{_TB}">{app_bars}</table>'
        + _ch_h2("Pomodoros - last 7 days")
        + _ch_vchart(pomo_vals, [ACCENT] * 7, [_ch_wd(td) for td in trend_days], chart_h=70,
                     top=[(str(v) if v else "") for v in pomo_vals])
        + _ch_h2("Daily factors - last 7 days")
        + _ch_factor("Deep work", [x[1]["productive_seconds"] for x in tdaily], [_ch_wd(td) for td in trend_days], dur, P,
                     delta=(cur_totals["productive_seconds"], prev_totals["productive_seconds"], True))
        + _ch_factor("Distraction", [x[1]["distracting_seconds"] for x in tdaily], [_ch_wd(td) for td in trend_days], dur, D,
                     delta=(cur_totals["distracting_seconds"], prev_totals["distracting_seconds"], False))
        + _ch_factor("Neutral", [x[1]["neutral_seconds"] for x in tdaily], [_ch_wd(td) for td in trend_days], dur, N,
                     delta=(cur_totals["neutral_seconds"], prev_totals["neutral_seconds"], None))
        + _ch_factor("Idle", [x[1]["idle_seconds"] for x in tdaily], [_ch_wd(td) for td in trend_days], dur, MUTED,
                     delta=(cur_totals["idle_seconds"], prev_totals["idle_seconds"], False))
        + _ch_factor("App switches", [x[1]["app_switches"] for x in tdaily], [_ch_wd(td) for td in trend_days], cnt, ACCENT,
                     delta=(cur_totals["app_switches"], prev_totals["app_switches"], False))
        + _ch_factor("Focus", [x[1]["focus_seconds"] for x in tdaily], [_ch_wd(td) for td in trend_days], dur, P,
                     delta=(cur_totals["focus_seconds"], prev_totals["focus_seconds"], True))
        + _ch_h2("Coach")
        + f'<ul>{insight_items}</ul>'
        + f'<div style="font-family:{_SANS};font-size:11px;color:{MUTED};margin-top:24px;border-top:1px solid {_PAL["BORDER"]};padding-top:10px;">'
        + f'Generated {_ch_esc(datetime.now().strftime("%Y-%m-%d %H:%M"))} &middot; goal met: {goal_txt} &middot; streak {streak["streak_days"]} days &middot; XP {streak["total_xp"]} &middot; all data stays local on this PC.</div>'
        '</td></tr></table></td></tr></table></body></html>'
    )


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

    con = sqlite3.connect(":memory:")
    con.executescript(ddl)

    today = date.today()
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
        "coach": {"enabled": True, "llm": {"enabled": False, "endpoint": "", "api_key": "", "model": ""}},
    }

    for offset in range(7):
        day = (today - timedelta(days=offset)).isoformat()
        con.execute(
            "INSERT INTO app_usage VALUES (?,?,?,?)",
            (day, "code.exe", "project - Code", 5400 + offset * 600),
        )
        con.execute(
            "INSERT INTO app_usage VALUES (?,?,?,?)",
            (day, "discord.exe", "chat", 1200 + offset * 120),
        )
        con.execute(
            "INSERT INTO hour_usage VALUES (?,?,?,?)", (day, 10, "code.exe", 3600)
        )
        con.execute("INSERT INTO meta VALUES (?,?)", ("switch:%s" % day, str(20 + offset)))

    print("top_distraction  ->", top_distraction(con, today.isoformat(), cfg))
    print("best_focus_hour  ->", best_focus_hour(con, 14))
    for line in insights(con, today.isoformat(), cfg):
        print(" insight:", line)
    print("daily_coach (offline) ->", daily_coach(con, today.isoformat(), cfg))

    report = weekly_report(con, cfg, today.isoformat())
    assert report.startswith("<!doctype html>")
    assert "<style>" in report and "http://" not in report and "https://" not in report
    print("weekly_report bytes ->", len(report))

    empty = sqlite3.connect(":memory:")
    empty.executescript(ddl)
    print("empty insights ->", insights(empty, "2000-01-01", cfg))
    print("empty best_focus_hour ->", best_focus_hour(empty))
    print("COACH SMOKE OK")


if __name__ == "__main__":
    _smoke()
