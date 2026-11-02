"""
weather_fx.py - live weather banners for Pips profiles (v2.9).

The server sends {"temp", "feels", "humidity", "code", "is_day", "wind", "utc_offset"}
(Open-Meteo, WMO weather codes). WeatherBanner draws an animated scene on a tk
Canvas: rain, drizzle, thunderstorms with lightning, snow, fog, drifting clouds,
sunshine or a starry night, plus the temperature, condition and local time.
All user-facing text comes from i18n.py.
"""

from __future__ import annotations

import math
import random
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone

try:
    from i18n import t
except ImportError:  # pragma: no cover
    def t(key, lang=None, **kwargs):
        return key.format(**kwargs) if kwargs else key


def condition(code: int) -> str:
    """WMO weather code -> one of our condition keys."""
    code = int(code or 0)
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if code in (65, 82):
        return "heavy_rain"
    if 61 <= code <= 67 or 80 <= code <= 81:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloudy"


ICONS = {"clear": ("\u2600", "\u263e"), "partly": ("\u26c5", "\u2601"), "cloudy": ("\u2601", "\u2601"),
         "fog": ("\u2248", "\u2248"), "drizzle": ("\u2602", "\u2602"), "rain": ("\u2602", "\u2602"),
         "heavy_rain": ("\u2602", "\u2602"), "snow": ("\u2744", "\u2744"), "storm": ("\u26a1", "\u26a1")}

# sky gradients (top, bottom) per condition, day / night
SKY = {
    "clear": (("#4aa3f0", "#bfe3ff"), ("#0b1638", "#26386e")),
    "partly": (("#5aa7e6", "#cfe6fa"), ("#111c3d", "#2c3a66")),
    "cloudy": (("#8c9aab", "#c9d1db"), ("#262b36", "#3b4250")),
    "fog": (("#aab3bd", "#d9dee3"), ("#3a3f47", "#555b64")),
    "drizzle": (("#7d8fa3", "#b8c4d1"), ("#1f2633", "#353e4e")),
    "rain": (("#5f7187", "#9aa9ba"), ("#161c27", "#2a3342")),
    "heavy_rain": (("#4a5a6e", "#7f8ea0"), ("#10151e", "#232b38")),
    "snow": (("#b9c9da", "#eef3f8"), ("#2a3446", "#4a566b")),
    "storm": (("#3c4656", "#6b7686"), ("#0c0f16", "#1f2430")),
}


def icon(weather: dict) -> str:
    cond = condition(weather.get("code", 0))
    return ICONS[cond][0 if weather.get("is_day", 1) else 1]


def short_text(weather: dict, loc: dict | None = None) -> str:
    """e.g. '☔ 27° Batam' for friend rows."""
    if not weather:
        return ""
    text = f"{icon(weather)} {round(weather.get('temp', 0))}\u00b0"
    if loc and loc.get("city"):
        text += f" {loc['city']}"
    return text


def local_time(weather: dict) -> str:
    try:
        tz = timezone(timedelta(seconds=int(weather.get("utc_offset", 0))))
        return datetime.now(tz).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _mix(c1: str, c2: str, k: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(a, b))


class WeatherBanner:
    """Animated weather strip. `compact` = one-line banner for the hub header."""

    FRAME_MS = 60

    def __init__(self, parent, weather: dict, loc: dict | None, width: int, height: int = 92, compact: bool = False):
        self.weather = weather or {}
        self.loc = loc or {}
        self.w = width
        self.h = height
        self.compact = compact
        self.cond = condition(self.weather.get("code", 0))
        self.day = bool(self.weather.get("is_day", 1))
        self.cv = tk.Canvas(parent, width=width, height=height, highlightthickness=0, bd=0)
        rng = random.Random(hash((self.cond, width, height)))
        n_drops = {"drizzle": 35, "rain": 70, "heavy_rain": 120, "storm": 110}.get(self.cond, 0)
        self.drops = [[rng.uniform(0, width), rng.uniform(0, height), rng.uniform(0.7, 1.3)] for _ in range(n_drops)]
        n_flakes = 55 if self.cond == "snow" else 0
        self.flakes = [[rng.uniform(0, width), rng.uniform(0, height), rng.uniform(1.2, 3.0), rng.uniform(0, 6.28)]
                       for _ in range(n_flakes)]
        n_clouds = {"partly": 3, "cloudy": 5, "drizzle": 4, "rain": 5, "heavy_rain": 6, "storm": 6, "snow": 4,
                    "fog": 2}.get(self.cond, 0)
        self.clouds = [[rng.uniform(-60, width), rng.uniform(6, height * 0.45), rng.uniform(0.7, 1.3),
                        rng.uniform(4, 12)] for _ in range(n_clouds)]
        self.stars = [(rng.uniform(0, width), rng.uniform(0, height * 0.7), rng.uniform(0, 6.28)) for _ in range(40)]
        self.flash_until = 0.0
        self.next_flash = time.monotonic() + rng.uniform(1.5, 4)
        self.bolt = []
        self.t0 = time.monotonic()
        self.last = self.t0
        self._draw_static_sky()
        self._tick()

    # ---- drawing ----
    def _draw_static_sky(self):
        top, bottom = SKY[self.cond][0 if self.day else 1]
        steps = 12
        for i in range(steps):
            self.cv.create_rectangle(0, i * self.h / steps, self.w, (i + 1) * self.h / steps + 1,
                                     fill=_mix(top, bottom, i / (steps - 1)), outline="", tags="sky")

    def _cloud(self, x, y, s, colour):
        for dx, dy, r in ((0, 0, 14), (16, -6, 17), (34, 0, 13), (18, 6, 14)):
            self.cv.create_oval(x + dx * s - r * s, y + dy * s - r * s, x + dx * s + r * s, y + dy * s + r * s,
                                fill=colour, outline="", tags="fx")

    def _tick(self):
        try:
            if not self.cv.winfo_exists():
                return
        except tk.TclError:
            return
        now = time.monotonic()
        dt = min(0.2, now - self.last)
        self.last = now
        tt = now - self.t0
        cv = self.cv
        cv.delete("fx")
        dark = self.cond in ("storm", "heavy_rain") or not self.day
        # sun / moon / stars
        if self.cond in ("clear", "partly"):
            if self.day:
                cx, cy, r = self.w - 70, self.h * 0.42, min(24, self.h * 0.28)
                for k in range(12):
                    a = k * math.pi / 6 + tt * 0.4
                    cv.create_line(cx + math.cos(a) * r * 1.25, cy + math.sin(a) * r * 1.25,
                                   cx + math.cos(a) * r * 1.8, cy + math.sin(a) * r * 1.8,
                                   fill="#ffe066", width=2, tags="fx")
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ffd43b", outline="#fab005", width=2, tags="fx")
            else:
                for (sx, sy, ph) in self.stars:
                    if 0.5 + 0.5 * math.sin(tt * 2 + ph) > 0.35:
                        cv.create_text(sx, sy, text="\u00b7", fill="#ffffff", font=("Segoe UI", 10), tags="fx")
                cx, cy, r = self.w - 70, self.h * 0.4, min(18, self.h * 0.22)
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#f1f3f5", outline="", tags="fx")
                cv.create_oval(cx - r + 8, cy - r - 3, cx + r + 8, cy + r - 3, fill=SKY[self.cond][1][0], outline="",
                               tags="fx")
        # clouds
        cloud_col = {"storm": "#4b5563", "heavy_rain": "#5c6878", "rain": "#7b8794", "drizzle": "#9aa5b1",
                     "cloudy": "#e3e8ee" if self.day else "#4a5160", "snow": "#f1f3f5" if self.day else "#5b6577",
                     "fog": "#e9ecef", "partly": "#ffffff" if self.day else "#3d4660"}.get(self.cond, "#ffffff")
        for c in self.clouds:
            c[0] += c[3] * dt
            if c[0] > self.w + 30:
                c[0] = -90
            self._cloud(c[0], c[1], c[2], cloud_col)
        # lightning
        if self.cond == "storm":
            if now >= self.next_flash:
                self.flash_until = now + 0.18
                self.next_flash = now + random.uniform(2.0, 5.0)
                x = random.uniform(self.w * 0.15, self.w * 0.7)
                pts = [x, 0]
                y = 0
                while y < self.h * 0.85:
                    y += random.uniform(8, 16)
                    x += random.uniform(-10, 10)
                    pts += [x, y]
                self.bolt = pts
            if now < self.flash_until:
                cv.create_rectangle(0, 0, self.w, self.h, fill="#ffffff", stipple="gray25", outline="", tags="fx")
                if len(self.bolt) >= 4:
                    cv.create_line(*self.bolt, fill="#fff3bf", width=3, tags="fx")
                    cv.create_line(*self.bolt, fill="#ffffff", width=1, tags="fx")
        # rain
        if self.drops:
            speed = {"drizzle": 140, "rain": 260, "heavy_rain": 340, "storm": 360}[self.cond]
            length = {"drizzle": 5, "rain": 10, "heavy_rain": 14, "storm": 14}[self.cond]
            colour = "#dbe4ff" if dark else "#e7f5ff"
            for d in self.drops:
                d[1] += speed * d[2] * dt
                d[0] -= speed * 0.18 * d[2] * dt
                if d[1] > self.h:
                    d[1] = random.uniform(-20, 0)
                    d[0] = random.uniform(0, self.w + 30)
                cv.create_line(d[0], d[1], d[0] + length * 0.2, d[1] - length * d[2], fill=colour, width=1, tags="fx")
                if d[1] > self.h - 4 and self.cond != "drizzle":
                    cv.create_oval(d[0] - 2, self.h - 3, d[0] + 2, self.h - 1, outline=colour, tags="fx")
        # snow
        for f in self.flakes:
            f[1] += (14 + f[2] * 8) * dt
            f[0] += math.sin(tt * 1.5 + f[3]) * 12 * dt
            if f[1] > self.h:
                f[1] = random.uniform(-10, 0)
                f[0] = random.uniform(0, self.w)
            cv.create_oval(f[0] - f[2], f[1] - f[2], f[0] + f[2], f[1] + f[2], fill="#ffffff", outline="", tags="fx")
        # fog
        if self.cond == "fog":
            for k in range(4):
                y = self.h * (0.25 + k * 0.2)
                x = (tt * (8 + k * 3)) % (self.w + 200) - 200
                cv.create_rectangle(x, y - 6, x + self.w * 0.8, y + 6, fill="#f8f9fa", stipple="gray50", outline="",
                                    tags="fx")
        self._draw_text(dark)
        cv.after(self.FRAME_MS, self._tick)

    def _draw_text(self, dark):
        w = self.weather
        cv = self.cv
        ink = "#ffffff" if dark or self.cond in ("rain", "heavy_rain", "storm", "drizzle") else "#102a43"
        shadow = "#000000" if ink == "#ffffff" else "#ffffff"
        temp = f"{round(w.get('temp', 0))}\u00b0C"
        cond_text = t("wx_" + self.cond)
        city = self.loc.get("city", "")
        if self.loc.get("country"):
            city += ", " + self.loc["country"]
        lt = local_time(w)

        def text(x, y, s, size, bold=False, anchor="w"):
            font = ("Segoe UI Symbol", size, "bold" if bold else "normal")
            cv.create_text(x + 1, y + 1, text=s, fill=shadow, font=font, anchor=anchor, tags="fx")
            cv.create_text(x, y, text=s, fill=ink, font=font, anchor=anchor, tags="fx")

        if self.compact:
            line = f"\U0001F4CD {city}   {icon(w)} {temp} {cond_text}" + (f"   \u23f0 {lt}" if lt else "")
            text(10, self.h / 2, line, 9, True)
            return
        text(14, 26, temp, 24, True)
        text(14, 54, f"{icon(w)} {cond_text}", 11, True)
        details = []
        if w.get("feels") is not None:
            details.append(t("wx_feels", t=round(w["feels"])))
        if w.get("humidity") is not None:
            details.append(f"\U0001F4A7 {w['humidity']}%")
        if w.get("wind") is not None:
            details.append(f"\u2248 {round(w['wind'])} km/h")
        text(14, 76, "   ".join(details), 8)
        text(self.w - 12, self.h - 14, f"\U0001F4CD {city}" + (f"  \u00b7  {lt}" if lt else ""), 9, True, anchor="e")
