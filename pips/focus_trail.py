"""
focus_trail.py - Strava-style "Focus Trail" stickers for Pips (v2.26; productivity-shaped trail v2.26.1).

Turns the weekday work-hours screen time that tracker.py already records
(hour_usage, idle excluded) into a route-like "footprint" and exports it as a
transparent PNG sticker in a style the user picks. Stickers can be saved,
copied to the clipboard and shared to social sites.

Data used (read-only, see CONTRACT.md):
  hour_usage     -> screen time per hour/app inside the work window
  sessions       -> completed focus sessions (pomodoros) inside the work window
  daily_summary  -> Pips score / stars
  scoring.categorize -> productive / neutral / distracting split

Optional config.json block (unknown keys are ignored elsewhere):
  "focus_trail": {"start_hour": 8, "end_hour": 17, "weekdays": [0, 1, 2, 3, 4]}

Importable without side effects (no GUI / threads / network at import).
Standard library + pywin32 + Pillow only.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import sqlite3
import urllib.parse
import webbrowser
from collections import Counter
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

try:
    import i18n
    from i18n import t as T
except Exception:  # pragma: no cover - i18n always ships with Pips
    i18n = None

    def T(key, **kwargs):
        return key

try:
    import scoring
except Exception:  # pragma: no cover
    scoring = None

try:
    import theme as theme_mod
except Exception:  # pragma: no cover
    theme_mod = None

logger = logging.getLogger("widget")

BASE_DIR = Path(__file__).parent
STICKER_DIR = BASE_DIR / "output" / "stickers"

TEMPLATES = ["Strava", "Badge", "Minimal", "Bars", "Neon"]
COLORS = [
    ("Pips red", "#D0484C"),
    ("Strava orange", "#FC4C02"),
    ("Lime", "#B6FF3B"),
    ("Cyan", "#22D3EE"),
    ("Pink", "#FF4FA3"),
    ("Violet", "#8B5CF6"),
    ("White", "#FFFFFF"),
]
APP_NAMES = {
    "outlook": "Outlook", "olk": "Outlook", "excel": "Excel", "winword": "Word",
    "powerpnt": "PowerPoint", "onenote": "OneNote", "ms-teams": "Teams",
    "teams": "Teams", "msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox",
    "explorer": "Explorer", "sldworks": "SOLIDWORKS", "sldworks_fs": "SOLIDWORKS",
    "fusion360": "Fusion", "acad": "AutoCAD", "code": "VS Code",
    "windowsterminal": "Terminal", "notepad": "Notepad", "slack": "Slack",
    "zoom": "Zoom", "line": "LINE", "opencode": "OpenCode",
}
SHARE_TARGETS = [
    ("X", "https://twitter.com/intent/tweet?text={text}"),
    ("LinkedIn", "https://www.linkedin.com/feed/?shareActive=true&text={text}"),
    ("Facebook", "https://www.facebook.com/"),
    ("Instagram", "https://www.instagram.com/"),
]


# --------------------------------------------------------------------------- #
# settings + small helpers
# --------------------------------------------------------------------------- #
def work_window(cfg: dict) -> tuple[int, int, set[int]]:
    """-> (start_hour, end_hour, weekdays) from cfg['focus_trail'] or 8-17 Mon-Fri."""
    block = (cfg or {}).get("focus_trail") or {}
    try:
        start = int(block.get("start_hour", 8))
        end = int(block.get("end_hour", 17))
    except (TypeError, ValueError):
        start, end = 8, 17
    start = max(0, min(23, start))
    end = max(start + 1, min(24, end))
    days = block.get("weekdays", [0, 1, 2, 3, 4])
    try:
        weekdays = {int(d) for d in days if 0 <= int(d) <= 6} or {0, 1, 2, 3, 4}
    except (TypeError, ValueError):
        weekdays = {0, 1, 2, 3, 4}
    return start, end, weekdays


def pretty_app(name: str) -> str:
    stem = (name or "unknown").lower()
    if stem.endswith(".exe"):
        stem = stem[:-4]
    return APP_NAMES.get(stem, stem.replace("_", " ").replace("-", " ").title())


def fmt_duration(seconds: float) -> str:
    minutes = int(round((seconds or 0) / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _lang() -> str:
    try:
        return i18n.get_language() if i18n else "en"
    except Exception:
        return "en"


def fmt_day(day: date) -> str:
    if _lang() in ("en", "id"):
        return f"{day:%a} {day.day} {day:%b}"
    return f"{day.month}/{day.day}"


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


# --------------------------------------------------------------------------- #
# statistics (read-only)
# --------------------------------------------------------------------------- #
def _hour_rows(con, day: date, start: int, end: int) -> list:
    try:
        return con.execute(
            "SELECT hour, app, seconds FROM hour_usage "
            "WHERE day = ? AND hour >= ? AND hour < ? ORDER BY hour",
            (day.isoformat(), start, end),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"focus_trail hour rows: {exc}")
        return []


def _sessions(con, day: date, start: int, end: int) -> tuple[int, int]:
    """-> (completed focus sessions, focus seconds) started inside the window."""
    try:
        rows = con.execute(
            "SELECT started_at, ended_at FROM sessions "
            "WHERE day = ? AND kind = 'work' AND completed = 1",
            (day.isoformat(),),
        ).fetchall()
    except sqlite3.Error:
        return 0, 0
    count = 0
    seconds = 0
    for started_at, ended_at in rows:
        try:
            began = datetime.fromisoformat(started_at)
        except (TypeError, ValueError):
            continue
        if not start <= began.hour < end:
            continue
        count += 1
        try:
            seconds += max(0, int((datetime.fromisoformat(ended_at) - began).total_seconds()))
        except (TypeError, ValueError):
            pass
    return count, seconds


def _score(con, day: date, cfg: dict):
    try:
        row = con.execute(
            "SELECT score FROM daily_summary WHERE day = ?", (day.isoformat(),)
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except sqlite3.Error:
        pass
    if scoring is not None and day == date.today():
        try:
            return float(scoring.compute_daily(con, day.isoformat(), cfg).get("score") or 0)
        except Exception:
            return None
    return None


def _stars(score, cfg: dict) -> int:
    if score is None:
        return 0
    if scoring is not None:
        try:
            return int(scoring.score_to_stars(score, cfg))
        except Exception:
            pass
    return max(1, min(5, int(score // 20) + 1))


def _category(app: str, cfg: dict) -> str:
    if scoring is None:
        return "neutral"
    try:
        return scoring.categorize(app or "", "", cfg)
    except Exception:
        return "neutral"


def window_seconds(day: date, cfg: dict) -> int:
    start, end, weekdays = work_window(cfg)
    full = (end - start) * 3600
    today = date.today()
    if day.weekday() not in weekdays or day > today:
        return 0
    if day < today:
        return full
    now = datetime.now()
    elapsed = (now.hour - start) * 3600 + now.minute * 60 + now.second
    return max(0, min(full, elapsed))


def app_heading(app: str) -> float:
    return hashlib.md5((app or "?").encode()).digest()[0] / 255 * 360


def chaikin(points: list, iterations: int = 3) -> list:
    for _ in range(iterations):
        if len(points) < 3:
            break
        smooth = [points[0]]
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            smooth.append((0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by))
            smooth.append((0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by))
        smooth.append(points[-1])
        points = smooth
    return points


# How each kind of screen time moves the trail (v2.26.1):
#   productive  -> long, smooth strides along the course (this is the "distance")
#   neutral     -> shorter steps (half speed)
#   distracting -> loops on the spot: you go in circles and barely move forward
TRAIL_STYLE = {
    "productive": {"length": 1.0},
    "neutral": {"length": 0.55},
    "distracting": {"length": 0.0},
}
# direction = time of day: heading sweeps clockwise across the work window
CLOCK_START = -90.0         # start of the window points "up" on the sticker
CLOCK_SWEEP = 180.0         # ...and the end of the window points "down" (half a clock turn)
# kinks = app switches: turn = MIN + RANGE * (how split that hour was across apps)
SWITCH_TURN_MIN = 18.0
SWITCH_TURN_RANGE = 62.0
SWITCH_DECAY = 0.55         # how quickly a kink straightens out (per step)
# distracting time = going in circles: one loop per step, coiling slowly forward
LOOP_SUBSTEPS = 10          # points per loop
LOOP_SIZE = 0.9             # loop circumference per sub-step (in productive-step units)
LOOP_DRIFT = 0.35           # how far each loop creeps forward
TRAIL_CATEGORIES = ("productive", "neutral", "distracting")


def chaikin_tagged(points: list, tags: list, iterations: int = 3) -> tuple[list, list]:
    """Chaikin smoothing that keeps a category per vertex.

    ``tags[k]`` is the category of the segment that ends at ``points[k]``
    (``tags[0]`` is unused). New points inherit the tag of their segment."""
    for _ in range(iterations):
        if len(points) < 3:
            break
        smooth = [points[0]]
        smooth_tags = [tags[0]]
        for index, ((ax, ay), (bx, by)) in enumerate(zip(points, points[1:])):
            tag = tags[index + 1]
            smooth.append((0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by))
            smooth.append((0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by))
            smooth_tags.extend((tag, tag))
        smooth.append(points[-1])
        smooth_tags.append(tags[-1])
        points, tags = smooth, smooth_tags
    return points, tags


def build_trail(hours: list, step_seconds: int, start_hour: int = 8, end_hour: int = 17,
                markers: list | None = None) -> tuple[list, list]:
    """Route from hourly screen time where every bend means something (v2.27).

    * step length  = time, scaled by productivity (see TRAIL_STYLE)
    * direction    = time of day: the trail turns slowly clockwise from the start
                     of the work window to the end, like a clock hand (half a turn)
    * kinks        = switching apps: each change of app within an hour bends the
                     trail, sharper the more that hour was split across apps;
                     an hour spent in one app runs straight
    * loops        = distracting time (going in circles)

    ``hours`` = [(hour, [(app, seconds, category), ...], label_or_None), ...] in time order
    (the label is optional). Labelled blocks drop a marker - (x, y, label) - into
    ``markers`` where they start, e.g. "10:00" or "Tue", so readers can see the time.
    Returns (points, tags); tags[k] is the category of the segment ending at points[k]."""
    x = y = 0.0
    points = [(x, y)]
    tags = ["productive"]
    span = max(1, end_hour - start_hour)
    zig = 1
    for block in hours:
        hour, apps = block[0], block[1]
        label = block[2] if len(block) > 2 else None
        if label and markers is not None:
            markers.append((x, y, label))
        hour_total = sum(seconds for _app, seconds, _cat in apps) or 1
        top_share = max(seconds for _app, seconds, _cat in apps) / hour_total if apps else 1.0
        split = 1.0 - top_share                       # 0 = one app all hour, ->1 = scattered
        kink = SWITCH_TURN_MIN + SWITCH_TURN_RANGE * split
        elapsed = 0
        offset = 0.0
        for index, (_app, seconds, category) in enumerate(apps):
            if category not in TRAIL_STYLE:
                category = "neutral"
            if index > 0 and seconds >= 30:
                zig = -zig
                offset = zig * kink                   # an app switch: bend left / right
            remaining = seconds
            while remaining >= 30:
                chunk = min(step_seconds, remaining)
                remaining -= chunk
                share = chunk / step_seconds
                clock = (hour - start_hour + (elapsed + chunk / 2) / 3600) / span
                elapsed += chunk
                heading = CLOCK_START + CLOCK_SWEEP * max(0.0, min(1.0, clock)) + offset
                offset *= SWITCH_DECAY               # the kink straightens out again
                if category == "distracting":
                    turn = 360.0 * share / LOOP_SUBSTEPS
                    loop_heading = heading - 90
                    for _ in range(LOOP_SUBSTEPS):
                        loop_heading += turn
                        x += LOOP_SIZE * share * math.cos(math.radians(loop_heading))
                        y += LOOP_SIZE * share * math.sin(math.radians(loop_heading))
                        points.append((x, y))
                        tags.append(category)
                    x += LOOP_DRIFT * share * math.cos(math.radians(heading))
                    y += LOOP_DRIFT * share * math.sin(math.radians(heading))
                    points.append((x, y))
                    tags.append(category)
                    continue
                length = TRAIL_STYLE[category]["length"] * share
                x += length * math.cos(math.radians(heading))
                y += length * math.sin(math.radians(heading))
                points.append((x, y))
                tags.append(category)
    if len(points) < 2:
        return [], []
    return chaikin_tagged(points, tags, iterations=2)


def period_stats(con, cfg: dict, kind: str, anchor: date) -> dict:
    """Stats for one weekday ('day') or the Mon-Fri week containing ``anchor``."""
    start, end, weekdays = work_window(cfg)
    if kind == "week":
        monday = week_start(anchor)
        days = [monday + timedelta(days=i) for i in range(7)
                if (monday + timedelta(days=i)).weekday() in weekdays]
    else:
        days = [anchor]

    screen = productive = focus_secs = pomos = window = 0
    apps = Counter()
    app_category = {}
    trail_hours = []
    per_day = []
    per_hour = {h: [0, 0] for h in range(start, end)}
    scores = []
    for day in days:
        rows = _hour_rows(con, day, start, end)
        by_hour: dict[int, list] = {}
        day_screen = day_productive = 0
        for hour, app, seconds in rows:
            seconds = int(seconds or 0)
            category = _category(app, cfg)
            app_category[app] = category
            by_hour.setdefault(int(hour), []).append((app, seconds, category))
            apps[app] += seconds
            day_screen += seconds
            is_productive = category == "productive"
            if is_productive:
                day_productive += seconds
            per_hour[int(hour)][0] += seconds
            per_hour[int(hour)][1] += seconds if is_productive else 0
        for position, hour in enumerate(sorted(by_hour)):
            if kind == "week":
                label = f"{day:%a}" if position == 0 else None      # one marker per day
            else:
                label = f"{hour:02d}:00" if hour % 2 == 0 else None  # every two hours
            trail_hours.append((hour, sorted(by_hour[hour], key=lambda item: -item[1]), label))
        count, seconds = _sessions(con, day, start, end)
        pomos += count
        focus_secs += seconds
        screen += day_screen
        productive += day_productive
        window += window_seconds(day, cfg)
        per_day.append((f"{day:%a}", day_screen, day_productive, (end - start) * 3600))
        score = _score(con, day, cfg)
        if score is not None and day_screen:
            scores.append(score)

    if kind == "week":
        bars = per_day
        first, last = days[0], days[-1]
        title = f"{fmt_day(first)} - {fmt_day(last)}"
        subtitle = T("ft_sub_week", start=f"{start:02d}", end=f"{end:02d}")
    else:
        bars = [(str(h), per_hour[h][0], per_hour[h][1], 3600) for h in range(start, end)]
        title = fmt_day(anchor)
        subtitle = f"{start:02d}:00-{end:02d}:00"
    score = sum(scores) / len(scores) if scores else None
    trail_marks = []
    trail, trail_tags = build_trail(trail_hours, 300 if kind == "day" else 900, start, end,
                                    markers=trail_marks)
    neutral_seconds = sum(sec for app, sec in apps.items() if app_category.get(app) == "neutral")
    neutral_apps = [(app, pretty_app(app), sec) for app, sec in apps.most_common()
                    if app_category.get(app) == "neutral"][:3]
    return {
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "screen": screen,
        "productive": productive,
        "productive_pct": round(100 * productive / screen) if screen else 0,
        "pomos": pomos,
        "focus_seconds": focus_secs,
        "active_pct": min(100, round(100 * screen / window)) if window else 0,
        "score": score,
        "stars": _stars(score, cfg),
        "top_apps": [(pretty_app(a), s) for a, s in apps.most_common(5)],
        "bars": bars,
        "trail": trail,
        "trail_tags": trail_tags,
        "trail_marks": trail_marks,
        "neutral_pct": round(100 * neutral_seconds / screen) if screen else 0,
        "neutral_apps": neutral_apps,
    }


def share_caption(stats: dict) -> str:
    when = T("ft_today") if stats["kind"] == "day" else T("ft_this_week")
    return T("ft_caption", screen=fmt_duration(stats["screen"]),
             productive=fmt_duration(stats["productive"]), pomos=stats["pomos"], when=when)


# --------------------------------------------------------------------------- #
# sticker rendering
# --------------------------------------------------------------------------- #
WHITE = (255, 255, 255, 255)
_LATIN_FONTS = {
    "black": ["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"],
    "bold": ["segoeuib.ttf", "arialbd.ttf"],
    "semibold": ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"],
    "regular": ["segoeui.ttf", "arial.ttf"],
}
_CJK_BOLD = ["msjhbd.ttc", "msyhbd.ttc", "msjh.ttc", "msyh.ttc"]
_CJK_FONTS = {"black": _CJK_BOLD, "bold": _CJK_BOLD, "semibold": _CJK_BOLD,
              "regular": ["msjh.ttc", "msyh.ttc"]}


@lru_cache(maxsize=96)
def get_font(pixels: int, weight: str, cjk: bool = False):
    names = (_CJK_FONTS if cjk else _LATIN_FONTS)[weight]
    if cjk and _lang() == "zh_CN":
        names = sorted(names, key=lambda n: not n.startswith("msyh"))
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in names:
        if (font_dir / name).exists():
            try:
                return ImageFont.truetype(str(font_dir / name), pixels)
            except OSError:
                continue
    return ImageFont.load_default(size=pixels)


def _needs_cjk(text: str) -> bool:
    return any(ord(ch) >= 0x2E80 for ch in text)


def hex_rgb(value: str) -> tuple:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


class Painter:
    """Layered canvas: soft shadows, cards, neon glow and crisp foreground."""

    def __init__(self, width: int, height: int, scale: float):
        self.width = width
        self.height = height
        self.s = scale
        size = (round(width * scale), round(height * scale))
        self.layers = {name: Image.new("RGBA", size, (0, 0, 0, 0))
                       for name in ("shadow", "base", "glow", "top")}
        self.draw = {name: ImageDraw.Draw(img) for name, img in self.layers.items()}

    def box(self, *coords):
        return [value * self.s for value in coords]

    def text(self, x, y, text, size, weight="bold", fill=WHITE, anchor="mm", shadow=True):
        font = get_font(int(size * self.s), weight, _needs_cjk(text))
        if shadow:
            self.draw["shadow"].text((x * self.s, (y + 3) * self.s), text, font=font,
                                     fill=(0, 0, 0, 190), anchor=anchor)
        self.draw["top"].text((x * self.s, y * self.s), text, font=font, fill=fill, anchor=anchor)

    def compose(self, shadow_blur=7, glow_blur=14) -> Image.Image:
        output = Image.new("RGBA", self.layers["top"].size, (0, 0, 0, 0))
        if self.layers["shadow"].getbbox():
            output.alpha_composite(
                self.layers["shadow"].filter(ImageFilter.GaussianBlur(shadow_blur * self.s)))
        output.alpha_composite(self.layers["base"])
        if self.layers["glow"].getbbox():
            glow = self.layers["glow"].filter(ImageFilter.GaussianBlur(glow_blur * self.s))
            output.alpha_composite(glow)
            output.alpha_composite(glow)
        output.alpha_composite(self.layers["top"])
        if self.s != 1:
            output = output.resize((self.width, self.height), Image.LANCZOS)
        return output


def _thin(points, spacing):
    kept = [points[0]]
    for x, y in points[1:-1]:
        if math.hypot(x - kept[-1][0], y - kept[-1][1]) >= spacing:
            kept.append((x, y))
    kept.append(points[-1])
    return kept


def _thin_tagged(points, tags, spacing):
    """Drop vertices closer than ``spacing`` but never across a category change."""
    kept = [points[0]]
    kept_tags = [tags[0]]
    for (x, y), tag in zip(points[1:-1], tags[1:-1]):
        if tag != kept_tags[-1] or math.hypot(x - kept[-1][0], y - kept[-1][1]) >= spacing:
            kept.append((x, y))
            kept_tags.append(tag)
    kept.append(points[-1])
    kept_tags.append(tags[-1])
    return kept, kept_tags


def trail_colors(accent) -> dict:
    """Category -> RGBA. Productive = your colour, neutral = a lighter tint, distracting = grey."""
    tint = tuple(round(c + (255 - c) * 0.55) for c in accent)
    return {
        "productive": accent + (255,),
        "neutral": tint + (235,),
        "distracting": (150, 150, 160, 225),
    }


def _runs(mapped, tags):
    """Split a polyline into [(category, [points...]), ...] runs of one category."""
    runs = []
    for index in range(1, len(mapped)):
        tag = tags[index]
        if runs and runs[-1][0] == tag:
            runs[-1][1].append(mapped[index])
        else:
            runs.append((tag, [mapped[index - 1], mapped[index]]))
    return runs


def draw_trail(p: Painter, points, box, accent, width, glow=False, shadow=True, tags=None, marks=None):
    x0, y0, x1, y1 = box
    if len(points) < 2:
        p.text((x0 + x1) / 2, (y0 + y1) / 2, T("ft_no_data"), 44, "semibold",
               fill=(255, 255, 255, 210), shadow=shadow)
        return
    if not tags or len(tags) != len(points):
        tags = ["productive"] * len(points)
    xs = [pt[0] for pt in points]
    ys = [pt[1] for pt in points]
    span_x = max(xs) - min(xs) or 1e-6
    span_y = max(ys) - min(ys) or 1e-6
    k = min((x1 - x0) / span_x, (y1 - y0) / span_y)
    off_x = (x0 + x1) / 2 - k * (min(xs) + max(xs)) / 2
    off_y = (y0 + y1) / 2 - k * (min(ys) + max(ys)) / 2
    s = p.s
    line_w = max(1, int(width * s))
    mapped, tags = _thin_tagged([((off_x + k * a) * s, (off_y + k * b) * s) for a, b in points],
                                tags, line_w * 0.6)
    colors = trail_colors(accent)
    color = colors["productive"]
    if shadow:
        p.draw["shadow"].line(mapped, fill=(0, 0, 0, 170), width=int(line_w * 1.5), joint="curve")
    top = p.draw["top"]
    r = line_w / 2
    for category, run in _runs(mapped, tags):
        fill = colors.get(category, colors["neutral"])
        width_share = {"productive": 1.0, "neutral": 0.8, "distracting": 0.55}.get(category, 0.8)
        run_w = max(1, int(line_w * width_share))
        if glow and category == "productive":
            p.draw["glow"].line(run, fill=fill, width=int(line_w * 2.4), joint="curve")
        top.line(run, fill=fill, width=run_w, joint="curve")
        rr = run_w / 2
        for cx, cy in (run[0], run[-1]):
            top.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=fill)
    if marks:
        _draw_marks(p, marks, mapped, k, off_x, off_y, line_w, shadow)
    sx, sy = mapped[0]
    r = line_w * 0.95
    top.ellipse([sx - r, sy - r, sx + r, sy + r], fill=WHITE,
                outline=(25, 25, 30, 255), width=max(1, int(line_w * 0.25)))
    ex, ey = mapped[-1]
    r = line_w * 1.25
    top.ellipse([ex - r, ey - r, ex + r, ey + r], fill=color,
                outline=WHITE, width=max(1, int(line_w * 0.35)))


def _draw_marks(p: Painter, marks, mapped, k, off_x, off_y, line_w, shadow):
    """Time markers: a small white dot on the trail and its label ("10:00" / "Tue")
    placed on the outside of the curve, away from the trail's centre."""
    s = p.s
    cx = sum(pt[0] for pt in mapped) / len(mapped)
    cy = sum(pt[1] for pt in mapped) / len(mapped)
    size = max(18, int(line_w / s * 1.5))
    font = get_font(int(size * s), "bold", _needs_cjk("".join(m[2] for m in marks)))
    top = p.draw["top"]
    r = max(2.0, line_w * 0.42)
    for mx, my, label in marks:
        px = (off_x + k * mx) * s
        py = (off_y + k * my) * s
        dx, dy = px - cx, py - cy
        dist = math.hypot(dx, dy) or 1.0
        lx = px + dx / dist * (line_w * 1.4 + size * s * 1.1)
        ly = py + dy / dist * (line_w * 1.4 + size * s * 0.8)
        top.ellipse([px - r, py - r, px + r, py + r], fill=WHITE, outline=(20, 20, 26, 255),
                    width=max(1, int(r * 0.45)))
        if shadow:
            p.draw["shadow"].text((lx, ly + 2 * s), label, font=font, fill=(0, 0, 0, 200), anchor="mm")
        top.text((lx, ly), label, font=font, fill=(255, 255, 255, 235), anchor="mm")


def draw_shape_key(p: Painter, cx, cy, tags, size=24, shadow=True):
    """One plain-language line that travels with every shared sticker."""
    parts = [T("ft_key_straight"), T("ft_key_kinks")]
    if tags and "distracting" in tags:
        parts.append(T("ft_key_loops"))
    p.text(cx, cy, "  \u00b7  ".join(parts), size, "semibold", fill=(255, 255, 255, 200), shadow=shadow)


def draw_trail_legend(p: Painter, cx, cy, accent, tags, size=26, shadow=True):
    """Small key under the trail: only the categories that appear on it."""
    present = [c for c in TRAIL_CATEGORIES if tags and c in tags]
    if len(present) < 2:
        return
    colors = trail_colors(accent)
    font = get_font(int(size * p.s), "semibold", _needs_cjk(T("ft_cat_productive")))
    swatch = size * 1.4
    gap = size * 0.5
    spacing = size * 1.3
    items = []
    total = 0.0
    for category in present:
        label = T("ft_cat_" + category)
        text_w = p.draw["top"].textlength(label, font=font) / p.s
        items.append((category, label, text_w))
        total += swatch + gap + text_w
    total += spacing * (len(items) - 1)
    x = cx - total / 2
    for category, label, text_w in items:
        p.draw["top"].line(p.box(x, cy, x + swatch, cy), fill=colors[category],
                           width=max(1, int(size * 0.32 * p.s)))
        p.text(x + swatch + gap, cy, label, size, "semibold", fill=(255, 255, 255, 220),
               anchor="lm", shadow=shadow)
        x += swatch + gap + text_w + spacing


def draw_stars(p: Painter, cx, cy, count, size, accent, layer="top"):
    gap = size * 1.3
    for index in range(5):
        x = cx + (index - 2) * gap
        points = []
        for corner in range(10):
            radius = size / 2 if corner % 2 == 0 else size / 4.6
            angle = math.radians(-90 + corner * 36)
            points.append(((x + radius * math.cos(angle)) * p.s,
                           (cy + radius * math.sin(angle)) * p.s))
        fill = accent + (255,) if index < count else (255, 255, 255, 60)
        p.draw[layer].polygon(points, fill=fill)


def _footer(stats, show_top_app):
    parts = [stats["title"], stats["subtitle"]]
    if show_top_app and stats["top_apps"]:
        parts.append(T("ft_top", app=stats["top_apps"][0][0]))
    return "  ·  ".join(parts)


def _score_text(stats):
    return f"{round(stats['score'])}" if stats["score"] is not None else "-"


def tpl_strava(st, accent, show_top_app, scale):
    p = Painter(1080, 1350, scale)
    columns = [
        (T("ft_screen"), fmt_duration(st["screen"])),
        (T("ft_productive"), fmt_duration(st["productive"])),
        (T("ft_pomos"), str(st["pomos"])),
    ]
    for index, (label, value) in enumerate(columns):
        x = 180 + index * 360
        p.text(x, 95, label, 34, "regular", fill=(255, 255, 255, 240))
        p.text(x, 168, value, 74, "black")
    draw_trail(p, st["trail"], (130, 280, 950, 1040), accent, 16, tags=st.get("trail_tags"),
               marks=st.get("trail_marks"))
    draw_trail_legend(p, 540, 1105, accent, st.get("trail_tags"), 28)
    draw_shape_key(p, 540, 1150, st.get("trail_tags"), 26)
    p.text(540, 1218, "PIPS · FOCUS TRAIL", 44, "black", fill=accent + (255,))
    p.text(540, 1274, _footer(st, show_top_app), 32, "semibold")
    return p.compose()


def tpl_badge(st, accent, show_top_app, scale):
    p = Painter(1080, 1080, scale)
    c, r = 540, 500
    p.draw["shadow"].ellipse(p.box(c - r, c - r + 12, c + r, c + r + 12), fill=(0, 0, 0, 170))
    p.draw["base"].ellipse(p.box(c - r, c - r, c + r, c + r), fill=(17, 17, 22, 242),
                           outline=accent + (255,), width=int(16 * scale))
    ri = r - 36
    p.draw["base"].ellipse(p.box(c - ri, c - ri, c + ri, c + ri),
                           outline=(255, 255, 255, 45), width=max(1, int(3 * scale)))
    p.text(540, 160, "FOCUS TRAIL", 44, "black", fill=accent + (255,), shadow=False)
    p.text(540, 212, st["title"].upper(), 28, "semibold", fill=(255, 255, 255, 170), shadow=False)
    draw_trail(p, st["trail"], (330, 262, 750, 530), accent, 12, shadow=False, tags=st.get("trail_tags"),
               marks=st.get("trail_marks"))
    draw_trail_legend(p, 540, 572, accent, st.get("trail_tags"), 22, shadow=False)
    draw_shape_key(p, 540, 604, st.get("trail_tags"), 19, shadow=False)
    p.text(540, 690, fmt_duration(st["screen"]), 116, "black", shadow=False)
    p.text(540, 772, T("ft_screen").upper(), 30, "bold", fill=(255, 255, 255, 150), shadow=False)
    p.text(540, 838, T("ft_badge_line", pct=st["productive_pct"], n=st["pomos"]),
           34, "semibold", shadow=False)
    draw_stars(p, 540, 905, st["stars"], 44, accent)
    if show_top_app and st["top_apps"]:
        p.text(540, 960, T("ft_top", app=st["top_apps"][0][0]), 26, "regular",
               fill=(255, 255, 255, 170), shadow=False)
    return p.compose()


def tpl_minimal(st, accent, show_top_app, scale):
    p = Painter(1080, 1080, scale)
    draw_trail(p, st["trail"], (150, 80, 930, 690), accent, 22, tags=st.get("trail_tags"),
               marks=st.get("trail_marks"))
    draw_trail_legend(p, 540, 752, accent, st.get("trail_tags"), 28)
    draw_shape_key(p, 540, 794, st.get("trail_tags"), 26)
    p.text(540, 895, fmt_duration(st["screen"]), 128, "black")
    p.text(540, 995, T("ft_screen").lower() + "  ·  " + _footer(st, show_top_app), 32, "semibold")
    return p.compose()


def tpl_bars(st, accent, show_top_app, scale):
    p = Painter(1080, 1080, scale)
    p.text(540, 92, st["title"], 38, "semibold")
    p.text(540, 180, fmt_duration(st["screen"]), 104, "black")
    bars = st["bars"]
    left, right, top, bottom = 120, 960, 300, 820
    gap = 22 if len(bars) > 6 else 34
    bar_w = (right - left - gap * (len(bars) - 1)) / len(bars)
    for index, (label, total, productive, maximum) in enumerate(bars):
        bx = left + index * (bar_w + gap)
        radius = bar_w / 2
        p.draw["shadow"].rounded_rectangle(p.box(bx, top + 4, bx + bar_w, bottom + 4),
                                           radius=radius * scale, fill=(0, 0, 0, 120))
        p.draw["base"].rounded_rectangle(p.box(bx, top, bx + bar_w, bottom),
                                         radius=radius * scale, fill=(255, 255, 255, 60))
        for value, alpha in ((total, 120), (productive, 255)):
            height = (bottom - top) * min(1.0, value / maximum) if maximum else 0
            if height >= 4:
                p.draw["top"].rounded_rectangle(
                    p.box(bx, bottom - height, bx + bar_w, bottom),
                    radius=min(radius, height / 2) * scale, fill=accent + (alpha,))
        p.text(bx + bar_w / 2, 868, label, 32, "semibold")
    p.text(540, 950, T("ft_bars_legend"), 28, "semibold", fill=(255, 255, 255, 220))
    p.text(540, 1020, "PIPS · FOCUS TRAIL", 40, "black", fill=accent + (255,))
    return p.compose()


def tpl_neon(st, accent, show_top_app, scale):
    p = Painter(1080, 1350, scale)
    p.draw["shadow"].rounded_rectangle(p.box(40, 52, 1040, 1322), radius=64 * scale,
                                       fill=(0, 0, 0, 180))
    p.draw["base"].rounded_rectangle(p.box(40, 40, 1040, 1310), radius=64 * scale,
                                     fill=(11, 11, 18, 245), outline=accent + (120,),
                                     width=max(1, int(3 * scale)))
    p.text(100, 122, "FOCUS TRAIL", 44, "black", fill=accent + (255,), anchor="lm", shadow=False)
    p.text(980, 122, st["title"], 32, "semibold", fill=(255, 255, 255, 190), anchor="rm",
           shadow=False)
    draw_trail(p, st["trail"], (160, 215, 920, 790), accent, 11, glow=True, shadow=False,
               tags=st.get("trail_tags"), marks=st.get("trail_marks"))
    draw_trail_legend(p, 540, 840, accent, st.get("trail_tags"), 24, shadow=False)
    draw_shape_key(p, 540, 878, st.get("trail_tags"), 22, shadow=False)
    cells = [
        (T("ft_screen").upper(), fmt_duration(st["screen"])),
        (T("ft_productive").upper(), fmt_duration(st["productive"])),
        (T("ft_pomos").upper(), str(st["pomos"])),
        (T("ft_score").upper(), _score_text(st)),
    ]
    line_w = max(1, int(2 * scale))
    p.draw["base"].line(p.box(100, 915, 980, 915), fill=(255, 255, 255, 40), width=line_w)
    p.draw["base"].line(p.box(540, 945, 540, 1205), fill=(255, 255, 255, 40), width=line_w)
    for index, (label, value) in enumerate(cells):
        x = 300 if index % 2 == 0 else 780
        y = 975 if index < 2 else 1115
        p.text(x, y, label, 26, "bold", fill=(255, 255, 255, 140), shadow=False)
        p.text(x, y + 62, value, 68, "black", shadow=False)
    bottom = "PIPS  ·  " + st["subtitle"]
    if show_top_app and st["top_apps"]:
        bottom += "  ·  " + T("ft_top", app=st["top_apps"][0][0])
    p.text(540, 1262, bottom, 28, "semibold", fill=(255, 255, 255, 150), shadow=False)
    return p.compose()


RENDERERS = {
    "Strava": tpl_strava,
    "Badge": tpl_badge,
    "Minimal": tpl_minimal,
    "Bars": tpl_bars,
    "Neon": tpl_neon,
}


def render_sticker(stats: dict, template: str, color_hex: str,
                   show_top_app: bool = False, scale: float = 2) -> Image.Image:
    renderer = RENDERERS.get(template, tpl_strava)
    return renderer(stats, hex_rgb(color_hex), show_top_app, scale)


# --------------------------------------------------------------------------- #
# clipboard / files
# --------------------------------------------------------------------------- #
def copy_image_to_clipboard(image: Image.Image) -> bool:
    """PNG (keeps transparency) + DIB fallback. Returns False on failure."""
    try:
        import win32clipboard
    except Exception as exc:
        logger.warning(f"focus_trail clipboard unavailable: {exc}")
        return False
    png = io.BytesIO()
    image.save(png, "PNG")
    flat = Image.new("RGB", image.size, "white")
    flat.paste(image, mask=image.split()[3])
    bmp = io.BytesIO()
    flat.save(bmp, "BMP")
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, bmp.getvalue()[14:])
            win32clipboard.SetClipboardData(
                win32clipboard.RegisterClipboardFormat("PNG"), png.getvalue())
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as exc:
        logger.warning(f"focus_trail clipboard copy failed: {exc}")
        return False


def sticker_path(kind: str, anchor: date, template: str) -> Path:
    key = week_start(anchor).isoformat() if kind == "week" else anchor.isoformat()
    return STICKER_DIR / f"focus-trail-{kind}-{key}-{template.lower()}.png"


# --------------------------------------------------------------------------- #
# window
# --------------------------------------------------------------------------- #
def _palette() -> dict:
    fallback = {"bg": "#faf8f3", "card": "#ffffff", "ink": "#1a1a1a", "muted": "#6b6b6b",
                "accent": "#8b1a1a", "accent_ink": "#ffffff", "soft": "#f1ece4",
                "border": "#e2ddd4"}
    try:
        return {**fallback, **theme_mod.palette()} if theme_mod else fallback
    except Exception:
        return fallback


def _checkerboard(size, dark):
    base, alt = ((52, 55, 62), (66, 70, 78)) if dark else ((225, 222, 215), (240, 237, 231))
    image = Image.new("RGBA", size, base + (255,))
    draw = ImageDraw.Draw(image)
    cell = 18
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=alt + (255,))
    return image


class FocusTrailWindow:
    """Toplevel with period picker, style/colour choice, live preview and share."""

    def __init__(self, master, con, cfg: dict, notify=None):
        import tkinter as tk

        self.tk = tk
        self.con = con
        self.cfg = cfg or {}
        self.notify = notify
        self.p = _palette()
        start, end, weekdays = work_window(self.cfg)
        self.weekdays = weekdays
        self.kind = "day"
        self.anchor = self._latest_workday(date.today())
        self.template = TEMPLATES[0]
        self.color = COLORS[0][1]
        self.show_top = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="")
        self.stats = None
        self.preview_photo = None
        self._redraw_job = None

        p = self.p
        self.top = tk.Toplevel(master)
        self.top.title("Pips · " + T("ft_title"))
        self.top.configure(bg=p["bg"])
        self.top.geometry("1060x720")
        self.top.minsize(900, 640)
        icon = BASE_DIR / "widget.ico"
        if icon.exists():
            try:
                self.top.iconbitmap(str(icon))
            except Exception:
                pass
        self.top.protocol("WM_DELETE_WINDOW", self.top.withdraw)

        left = tk.Frame(self.top, bg=p["bg"], padx=18, pady=14)
        left.pack(side="left", fill="y")
        right = tk.Frame(self.top, bg=p["bg"])
        right.pack(side="right", fill="both", expand=True)

        tk.Label(left, text=T("ft_title"), bg=p["bg"], fg=p["accent"],
                 font=("Segoe UI Semibold", 17)).pack(anchor="w")
        tk.Label(left, text=T("ft_subtitle_short", start=f"{start:02d}", end=f"{end:02d}"),
                 bg=p["bg"], fg=p["muted"], font=("Segoe UI", 9),
                 wraplength=320, justify="left").pack(anchor="w")

        # period
        row = tk.Frame(left, bg=p["bg"])
        row.pack(anchor="w", pady=(12, 4))
        self.kind_buttons = {}
        for kind, label in (("day", T("ft_day")), ("week", T("ft_week"))):
            button = self._button(row, label, lambda k=kind: self.set_kind(k))
            button.pack(side="left", padx=(0, 4))
            self.kind_buttons[kind] = button
        nav = tk.Frame(left, bg=p["bg"])
        nav.pack(anchor="w", pady=(2, 6))
        self._button(nav, "\u25C0", lambda: self.step(-1)).pack(side="left")
        self.period_label = tk.Label(nav, text="", bg=p["bg"], fg=p["ink"], width=20,
                                     font=("Segoe UI Semibold", 10))
        self.period_label.pack(side="left", padx=4)
        self.next_button = self._button(nav, "\u25B6", lambda: self.step(1))
        self.next_button.pack(side="left")
        self._button(nav, T("ft_today_btn"), self.go_today).pack(side="left", padx=(8, 0))

        # stats card
        card = tk.Frame(left, bg=p["card"], padx=12, pady=10,
                        highlightthickness=1, highlightbackground=p["border"])
        card.pack(anchor="w", fill="x", pady=(4, 8))
        self.big = tk.Label(card, text="0m", bg=p["card"], fg=p["ink"],
                            font=("Segoe UI Black", 24))
        self.big.pack(anchor="w")
        self.details = tk.Label(card, text="", bg=p["card"], fg=p["ink"], justify="left",
                                font=("Segoe UI", 9))
        self.details.pack(anchor="w")
        self.apps = tk.Label(card, text="", bg=p["card"], fg=p["muted"], justify="left",
                             font=("Consolas", 9))
        self.apps.pack(anchor="w", pady=(4, 0))

        # style + colour
        tk.Label(left, text=T("ft_style"), bg=p["bg"], fg=p["muted"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(4, 2))
        grid = tk.Frame(left, bg=p["bg"])
        grid.pack(anchor="w")
        self.style_buttons = {}
        for index, name in enumerate(TEMPLATES):
            button = self._button(grid, name, lambda n=name: self.set_template(n), width=8)
            button.grid(row=index // 3, column=index % 3, padx=(0, 4), pady=2, sticky="w")
            self.style_buttons[name] = button
        swatches = tk.Frame(left, bg=p["bg"])
        swatches.pack(anchor="w", pady=(8, 0))
        self.swatches = {}
        for name, color in COLORS:
            swatch = tk.Label(swatches, bg=color, width=3, cursor="hand2",
                              highlightthickness=3, highlightbackground=p["bg"])
            swatch.pack(side="left", padx=2)
            swatch.bind("<Button-1>", lambda e, c=color: self.set_color(c))
            self.swatches[color] = swatch
        tk.Checkbutton(left, text=T("ft_show_top"), variable=self.show_top,
                       command=self.schedule_redraw, bg=p["bg"], fg=p["ink"],
                       activebackground=p["bg"], activeforeground=p["ink"],
                       selectcolor=p["card"], font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))

        # actions
        row = tk.Frame(left, bg=p["bg"])
        row.pack(anchor="w", pady=(10, 4))
        self._button(row, T("ft_save"), self.save, primary=True).pack(side="left")
        self._button(row, T("ft_copy"), self.copy).pack(side="left", padx=4)
        self._button(row, T("ft_open"), self.open_folder).pack(side="left")
        tk.Label(left, text=T("ft_share"), bg=p["bg"], fg=p["muted"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w", pady=(6, 2))
        row = tk.Frame(left, bg=p["bg"])
        row.pack(anchor="w")
        for site, url in SHARE_TARGETS:
            self._button(row, site, lambda s=site, u=url: self.share(s, u)).pack(
                side="left", padx=(0, 4))
        tk.Label(left, textvariable=self.status, bg=p["bg"], fg=p["muted"],
                 font=("Segoe UI", 9), wraplength=320, justify="left").pack(anchor="w",
                                                                             pady=(8, 0))

        self._build_how_to_read(right)
        # v2.27: "your trail is mostly neutral" nudge with one-click app categories
        self.nudge = tk.Frame(right, bg=p["bg"])
        self.nudge.pack(fill="x", padx=14)
        self.canvas = tk.Canvas(right, bg=p["bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        self.canvas.bind("<Configure>", lambda e: self.schedule_redraw())

        self._refresh_choices()
        self.refresh()

    # ---- v2.27: help new users read the trail -----------------------------
    def _build_how_to_read(self, parent):
        """The trail's visual dictionary: five tiny drawings with a few words each,
        laid out as a compact 3 x 2 strip above the preview."""
        tk = self.tk
        p = self.p
        strip = tk.Frame(parent, bg=p["bg"])
        strip.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(strip, text=T("ft_how_title"), bg=p["bg"], fg=p["ink"],
                 font=("Segoe UI Semibold", 9)).pack(anchor="w")
        items = [("stride", T("ft_read_distance")), ("straight", T("ft_read_straight")),
                 ("kinks", T("ft_read_kinks")), ("loops", T("ft_read_loops")),
                 ("clock", T("ft_read_clock"))]
        col_w, row_h, cols = 236, 30, 3
        canvas = tk.Canvas(strip, width=col_w * cols, height=row_h * 2, bg=p["bg"], highlightthickness=0)
        canvas.pack(anchor="w")
        accent = self.color
        tint = "#%02x%02x%02x" % tuple(round(c + (255 - c) * 0.55) for c in hex_rgb(accent))
        for index, (kind, text) in enumerate(items):
            x0 = (index % cols) * col_w
            y = (index // cols) * row_h + row_h / 2
            if kind == "stride":
                canvas.create_line(x0 + 4, y, x0 + 16, y, fill=tint, width=3, capstyle="round")
                canvas.create_line(x0 + 16, y, x0 + 44, y, fill=accent, width=5, capstyle="round")
            elif kind == "straight":
                canvas.create_line(x0 + 4, y + 5, x0 + 44, y - 5, fill=accent, width=4, capstyle="round")
            elif kind == "kinks":
                canvas.create_line(x0 + 4, y, x0 + 12, y - 6, x0 + 20, y + 6, x0 + 28, y - 6,
                                   x0 + 36, y + 5, x0 + 44, y, fill=tint, width=3,
                                   joinstyle="round", capstyle="round")
            elif kind == "loops":
                canvas.create_line(x0 + 4, y + 4, x0 + 14, y + 4, fill=tint, width=3)
                canvas.create_oval(x0 + 14, y - 8, x0 + 27, y + 5, outline="#9696a0", width=2)
                canvas.create_oval(x0 + 21, y - 8, x0 + 34, y + 5, outline="#9696a0", width=2)
                canvas.create_line(x0 + 34, y + 4, x0 + 44, y + 4, fill=accent, width=3)
            else:
                canvas.create_arc(x0 + 2, y - 6, x0 + 46, y + 34, start=35, extent=110, style="arc",
                                  outline=accent, width=3)
                canvas.create_oval(x0 + 21, y - 9, x0 + 27, y - 3, fill="white", outline="#202026")
                canvas.create_text(x0 + 24, y + 8, text="10:00", fill=p["ink"], font=("Segoe UI", 6, "bold"))
            canvas.create_text(x0 + 52, y, text=text, anchor="w", fill=p["muted"], font=("Segoe UI", 8),
                               width=col_w - 58)

    def _render_nudge(self):
        """Most of the time counts as neutral -> the trail is pale. Offer one-click fixes."""
        tk = self.tk
        p = self.p
        for child in self.nudge.winfo_children():
            child.destroy()
        st = self.stats or {}
        if st.get("screen", 0) < 1800 or st.get("neutral_pct", 0) < 70 or not st.get("neutral_apps"):
            return
        box = tk.Frame(self.nudge, bg=p["card"], padx=10, pady=8,
                       highlightthickness=1, highlightbackground=p["accent"])
        box.pack(anchor="w", fill="x", pady=(8, 0))
        tk.Label(box, text="\U0001F4A1 " + T("ft_nudge", pct=st["neutral_pct"]), bg=p["card"],
                 fg=p["ink"], font=("Segoe UI", 9), wraplength=640, justify="left").pack(anchor="w")
        apps_row = tk.Frame(box, bg=p["card"])
        apps_row.pack(anchor="w", fill="x")
        for app, name, seconds in st["neutral_apps"]:
            row = tk.Frame(apps_row, bg=p["card"], padx=6)
            row.pack(side="left", pady=(6, 0))
            tk.Label(row, text=f"{name[:16]}  {fmt_duration(seconds)}", bg=p["card"], fg=p["ink"],
                     font=("Segoe UI Semibold", 9)).pack(anchor="w")
            buttons = tk.Frame(row, bg=p["card"])
            buttons.pack(anchor="w", pady=(2, 0))
            self._button(buttons, T("ft_mark_prod"), lambda a=app: self.mark_app(a, "productive"),
                         primary=True).pack(side="left")
            self._button(buttons, T("ft_mark_dist"), lambda a=app: self.mark_app(a, "distracting")).pack(
                side="left", padx=(4, 0))

    def mark_app(self, app: str, category: str):
        """Put ``app`` in a category list - live (the widget shares this cfg) and in config.json."""
        lists = self.cfg.setdefault("category", {})
        for key in ("productive", "neutral", "distracting"):
            names = lists.setdefault(key, [])
            lists[key] = [n for n in names if str(n).lower() != app.lower()]
        lists[category].append(app.lower())
        path = BASE_DIR / "config.json"
        try:
            disk = {}
            if path.exists():
                disk = json.loads(path.read_text(encoding="utf-8-sig"))
            disk["category"] = lists
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(disk, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except Exception as exc:
            logger.warning(f"focus_trail could not save the app category: {exc}")
        self.status.set(T("ft_marked", app=pretty_app(app), cat=T("ft_cat_" + category)))
        self.refresh()

    # ---- widgets ------------------------------------------------------------
    def _button(self, parent, text, command, primary=False, width=None):
        p = self.p
        button = self.tk.Button(
            parent, text=text, command=command, relief="flat", bd=0, cursor="hand2",
            bg=p["accent"] if primary else p["soft"],
            fg=p["accent_ink"] if primary else p["ink"],
            activebackground=p["accent"], activeforeground=p["accent_ink"],
            font=("Segoe UI Semibold" if primary else "Segoe UI", 9), padx=10, pady=4)
        if width:
            button.config(width=width)
        return button

    def _set_selected(self, button, selected):
        p = self.p
        button.config(bg=p["accent"] if selected else p["soft"],
                      fg=p["accent_ink"] if selected else p["ink"])

    def _refresh_choices(self):
        for kind, button in self.kind_buttons.items():
            self._set_selected(button, kind == self.kind)
        for name, button in self.style_buttons.items():
            self._set_selected(button, name == self.template)
        for color, swatch in self.swatches.items():
            swatch.config(highlightbackground=self.p["ink"] if color == self.color else self.p["bg"])

    # ---- navigation -----------------------------------------------------------
    def _latest_workday(self, day: date) -> date:
        for _ in range(7):
            if day.weekday() in self.weekdays:
                return day
            day -= timedelta(days=1)
        return day

    def set_kind(self, kind):
        self.kind = kind
        self._refresh_choices()
        self.refresh()

    def step(self, direction):
        if self.kind == "week":
            candidate = self.anchor + timedelta(days=7 * direction)
        else:
            candidate = self.anchor
            for _ in range(7):
                candidate += timedelta(days=direction)
                if candidate.weekday() in self.weekdays:
                    break
        if candidate > date.today():
            return
        self.anchor = candidate
        self.refresh()

    def go_today(self):
        self.anchor = self._latest_workday(date.today())
        self.refresh()

    def set_template(self, name):
        self.template = name
        self._refresh_choices()
        self.schedule_redraw()

    def set_color(self, color):
        self.color = color
        self._refresh_choices()
        self.schedule_redraw()

    # ---- data + preview -------------------------------------------------------
    def refresh(self):
        try:
            self.stats = period_stats(self.con, self.cfg, self.kind, self.anchor)
        except Exception as exc:
            logger.error(f"focus_trail stats failed: {exc}")
            return
        st = self.stats
        self.period_label.config(text=st["title"])
        if self.kind == "week":
            at_end = week_start(self.anchor) + timedelta(days=7) > date.today()
        else:
            at_end = self.anchor >= self._latest_workday(date.today())
        self.next_button.config(state="disabled" if at_end else "normal")
        self.big.config(text=fmt_duration(st["screen"]))
        self.details.config(text=T(
            "ft_details", productive=fmt_duration(st["productive"]), pct=st["productive_pct"],
            n=st["pomos"], focus=fmt_duration(st["focus_seconds"]), active=st["active_pct"],
            score=_score_text(st)))
        apps = "\n".join(f"{name[:14]:<14} {fmt_duration(sec):>8}" for name, sec in st["top_apps"])
        self.apps.config(text=apps or T("ft_no_data"))
        self._render_nudge()
        self.schedule_redraw()

    def schedule_redraw(self):
        if self._redraw_job:
            try:
                self.top.after_cancel(self._redraw_job)
            except Exception:
                pass
        self._redraw_job = self.top.after(120, self.redraw)

    def current_sticker(self, scale=2) -> Image.Image:
        return render_sticker(self.stats, self.template, self.color, self.show_top.get(), scale)

    def redraw(self):
        self._redraw_job = None
        if not self.stats:
            return
        width = max(200, self.canvas.winfo_width())
        height = max(200, self.canvas.winfo_height())
        try:
            sticker = self.current_sticker(scale=1)
        except Exception as exc:
            logger.error(f"focus_trail render failed: {exc}")
            return
        k = min(width / sticker.width, height / sticker.height) * 0.95
        size = (max(1, int(sticker.width * k)), max(1, int(sticker.height * k)))
        sticker = sticker.resize(size, Image.LANCZOS)
        # stickers are white-on-transparent for photos, so always preview on dark
        background = _checkerboard(size, True)
        background.alpha_composite(sticker)
        self.preview_photo = ImageTk.PhotoImage(background)
        self.canvas.delete("all")
        self.canvas.create_image(width // 2, height // 2, image=self.preview_photo)

    # ---- actions ----------------------------------------------------------------
    def save(self, quiet=False):
        try:
            STICKER_DIR.mkdir(parents=True, exist_ok=True)
            path = sticker_path(self.kind, self.anchor, self.template)
            image = self.current_sticker()
            image.save(path)
        except Exception as exc:
            logger.error(f"focus_trail save failed: {exc}")
            self.status.set(str(exc))
            return None, None
        if not quiet:
            self.status.set(T("ft_saved", name=path.name))
        logger.info(f"Focus Trail sticker saved: {path}")
        return image, path

    def copy(self):
        image, path = self.save(quiet=True)
        if image is None:
            return
        if copy_image_to_clipboard(image):
            self.status.set(T("ft_copied", name=path.name))
        else:
            self.status.set(T("ft_saved", name=path.name))

    def open_folder(self):
        try:
            STICKER_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(STICKER_DIR))
        except Exception as exc:
            logger.warning(f"focus_trail open folder failed: {exc}")

    def share(self, site, url):
        image, path = self.save(quiet=True)
        if image is None:
            return
        copy_image_to_clipboard(image)
        try:
            webbrowser.open(url.format(text=urllib.parse.quote(share_caption(self.stats))))
        except Exception as exc:
            logger.warning(f"focus_trail share open failed: {exc}")
        message = T("ft_shared", site=site, name=path.name)
        self.status.set(message)
        if self.notify:
            try:
                self.notify(T("ft_shared_short", site=site))
            except Exception:
                pass

    # ---- lifecycle --------------------------------------------------------------
    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except Exception:
            return False

    def show(self):
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        self.refresh()


def open_window(master, con, cfg: dict, notify=None) -> FocusTrailWindow:
    window = FocusTrailWindow(master, con, cfg, notify=notify)
    window.show()
    return window


def _smoke(db_path: str | None = None, out_dir: str | None = None) -> list:
    """Render every style for the latest workday + week (no GUI). Returns file paths."""
    db = db_path or str(BASE_DIR / "data" / "productivity.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cfg = {}
    try:
        import tracker
        cfg = tracker.load_config()
    except Exception:
        pass
    out = Path(out_dir or (BASE_DIR / "output" / "stickers" / "_smoke"))
    out.mkdir(parents=True, exist_ok=True)
    _, _, weekdays = work_window(cfg)
    anchor = date.today()
    while anchor.weekday() not in weekdays:
        anchor -= timedelta(days=1)
    paths = []
    for kind in ("day", "week"):
        stats = period_stats(con, cfg, kind, anchor)
        for index, template in enumerate(TEMPLATES):
            image = render_sticker(stats, template, COLORS[index % len(COLORS)][1], True)
            path = out / f"{kind}-{template.lower()}.png"
            image.save(path)
            paths.append(str(path))
    con.close()
    return paths
