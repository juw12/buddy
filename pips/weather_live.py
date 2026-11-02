"""
weather_live.py - v2.23 "Weather on your desktop".

Where am I?
  follow : your exact spot from Windows location (when Windows allows it), checked every few
           minutes so a laptop follows you around; falls back to your internet location.
  ip     : approximate city from your internet connection.
  city   : a place you picked - search by name, or paste exact coordinates ("1.1301, 104.0529").
  off    : no location, no weather.

Live weather comes straight from Open-Meteo (free, no key) every 15 minutes, so it works even
without Pips Friends. WeatherOverlay draws it on the desktop in a transparent, click-through
window: around the pet (its own little rain cloud, an umbrella, snow piling up at its feet, sun,
moon and stars, fog, lightning, wind) or over the whole screen.

Nothing here touches Tk from a worker thread: the service posts results to a queue that the pet
polls from its own loop.
"""

from __future__ import annotations

import ctypes
import json
import logging
import math
import queue
import random
import re
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("productivity.weather")

MODES = ("follow", "ip", "city", "off")
FX_MODES = ("pet", "screen", "off")
DEFAULT_MODE = "ip"
DEFAULT_FX = "pet"

WEATHER_REFRESH_S = 15 * 60       # Open-Meteo updates its "current" values every 15 minutes
FOLLOW_CHECK_S = 5 * 60           # follow mode: where is the laptop now?
IP_CHECK_S = 3 * 60 * 60          # approximate mode: the internet location rarely changes
WINDOWS_RETRY_S = 60 * 60         # Windows location was off: try again in an hour
RETRY_S = 2 * 60                  # a request failed: try again soon
MOVE_KM = 0.5                     # moved further than this -> new place, new weather
CACHE_MAX_AGE_S = 2 * 60 * 60

USER_AGENT = "Pips desktop pet (https://juw12.github.io/projects/pips/)"
CREATE_NO_WINDOW = 0x08000000

CONDITIONS = ("clear", "partly", "cloudy", "fog", "drizzle", "rain", "heavy_rain", "snow", "storm")
WET = ("drizzle", "rain", "heavy_rain", "storm")
ICONS = {"clear": ("\u2600", "\U0001F319"), "partly": ("\u26C5", "\u2601"), "cloudy": ("\u2601", "\u2601"),
         "fog": ("\U0001F32B", "\U0001F32B"), "drizzle": ("\U0001F326", "\U0001F327"),
         "rain": ("\U0001F327", "\U0001F327"), "heavy_rain": ("\U0001F327", "\U0001F327"),
         "snow": ("\u2744", "\u2744"), "storm": ("\u26C8", "\u26C8")}


def condition(code) -> str:
    """WMO weather code -> one of CONDITIONS (same mapping as weather_fx.condition)."""
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = 0
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


def icon_for(weather: dict | None) -> str:
    if not weather:
        return "\U0001F326"
    pair = ICONS[condition(weather.get("code"))]
    return pair[0 if weather.get("is_day", 1) else 1]


def distance_km(a: dict | None, b: dict | None) -> float:
    """Great-circle distance between two places with lat / lon."""
    try:
        lat1, lon1, lat2, lon2 = (math.radians(float(v)) for v in (a["lat"], a["lon"], b["lat"], b["lon"]))
    except (TypeError, KeyError, ValueError):
        return float("inf")
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def place_label(place: dict | None, long: bool = False) -> str:
    if not place:
        return ""
    parts = [place.get("name") or ""]
    if long:
        parts += [place.get("admin") or "", place.get("country") or ""]
    return ", ".join(p for p in parts if p)


COORD_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def parse_coords(text: str):
    """'1.1301, 104.0529' -> (1.1301, 104.0529) or None."""
    match = COORD_RE.match(text or "")
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return lat, lon


# ---------------------------------------------------------------------------
# HTTP (env proxy first, then the Windows proxy settings - same idea as social.api_call)
# ---------------------------------------------------------------------------
_SYSTEM_OPENER = {"opener": None}


def _system_opener():
    if _SYSTEM_OPENER["opener"] is None:
        try:
            proxies = urllib.request.getproxies_registry()
        except Exception:  # pragma: no cover - non-Windows
            proxies = {}
        _SYSTEM_OPENER["opener"] = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return _SYSTEM_OPENER["opener"]


_ROUTE = {"first": 0}          # remembers which route (env proxy / Windows proxy) answered last time


def http_json(url: str, timeout: float = 12.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    routes = [urllib.request.urlopen, _system_opener().open]
    order = [_ROUTE["first"], 1 - _ROUTE["first"]]
    last = None
    for index in order:
        try:
            with routes[index](req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _ROUTE["first"] = index
            return data
        except urllib.error.HTTPError as exc:
            last = exc
            break                       # the server answered: another proxy won't help
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
    raise ConnectionError(str(last))


# ---------------------------------------------------------------------------
# Locating
# ---------------------------------------------------------------------------
_PS_LOCATE = r"""
$ErrorActionPreference = 'Stop'
try { Add-Type -AssemblyName System.Device } catch { 'ERR nodevice'; exit }
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High)
[void]$w.TryStart($false, [TimeSpan]::FromMilliseconds(4000))
$deadline = (Get-Date).AddSeconds(9)
while (($w.Status -ne 'Ready') -and ($w.Permission -ne 'Denied') -and ((Get-Date) -lt $deadline)) { Start-Sleep -Milliseconds 250 }
$loc = $w.Position.Location
if ($w.Permission -eq 'Denied') { 'DENIED' }
elseif ($loc.IsUnknown) { 'NODATA' }
else { [string]::Format([Globalization.CultureInfo]::InvariantCulture, 'OK {0} {1} {2}', $loc.Latitude, $loc.Longitude, $loc.HorizontalAccuracy) }
$w.Stop()
"""


def windows_location(timeout: float = 20.0):
    """Exact position from Windows location services -> ({lat, lon, accuracy}, "") or (None, reason)."""
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", _PS_LOCATE], capture_output=True, text=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("windows location failed: %s", exc)
        return None, "unavailable"
    out = (proc.stdout or "").strip().splitlines()
    line = out[-1].strip() if out else ""
    if line.startswith("OK "):
        try:
            _ok, lat, lon, acc = line.split()
            accuracy = float(acc) if acc not in ("NaN", "") else None
            return {"lat": float(lat), "lon": float(lon), "accuracy": accuracy}, ""
        except ValueError:
            return None, "unavailable"
    if line == "DENIED":
        return None, "denied"
    return None, "nodata" if line == "NODATA" else "unavailable"


def ip_location():
    """Approximate city from the internet connection -> place dict or None."""
    try:
        data = http_json("https://ipwho.is/")
        if data.get("success") and data.get("latitude") is not None:
            return {"name": data.get("city") or "", "admin": data.get("region") or "",
                    "country": data.get("country") or "", "lat": float(data["latitude"]),
                    "lon": float(data["longitude"]), "source": "ip"}
    except Exception as exc:
        logger.debug("ipwho.is failed: %s", exc)
    try:
        data = http_json("https://ipapi.co/json/")
        if data.get("latitude") is not None:
            return {"name": data.get("city") or "", "admin": data.get("region") or "",
                    "country": data.get("country_name") or "", "lat": float(data["latitude"]),
                    "lon": float(data["longitude"]), "source": "ip"}
    except Exception as exc:
        logger.debug("ipapi.co failed: %s", exc)
    return None


def reverse_geocode(lat: float, lon: float, lang: str = "en") -> dict:
    """Name of the place at (lat, lon): {name, admin, country} (empty strings if unknown)."""
    try:
        data = http_json("https://api.bigdatacloud.net/data/reverse-geocode-client?"
                         + urllib.parse.urlencode({"latitude": lat, "longitude": lon, "localityLanguage": lang}))
        name = data.get("city") or data.get("locality") or ""
        if name or data.get("countryName"):
            return {"name": name, "admin": data.get("principalSubdivision") or "",
                    "country": data.get("countryName") or ""}
    except Exception as exc:
        logger.debug("reverse geocode failed: %s", exc)
    try:
        data = http_json("https://nominatim.openstreetmap.org/reverse?"
                         + urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "json", "zoom": 12,
                                                   "accept-language": lang}))
        addr = data.get("address") or {}
        name = (addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb")
                or addr.get("county") or "")
        return {"name": name, "admin": addr.get("state") or "", "country": addr.get("country") or ""}
    except Exception as exc:
        logger.debug("nominatim failed: %s", exc)
    return {"name": "", "admin": "", "country": ""}


def search_places(query: str, lang: str = "en") -> list:
    """Places matching a name - or the exact spot for pasted coordinates."""
    coords = parse_coords(query)
    if coords:
        lat, lon = coords
        named = reverse_geocode(lat, lon, lang)
        return [{"name": named["name"] or f"{lat:.4f}, {lon:.4f}", "admin": named["admin"],
                 "country": named["country"], "lat": lat, "lon": lon, "exact": True}]
    query = (query or "").strip()
    if len(query) < 2:
        return []
    data = http_json("https://geocoding-api.open-meteo.com/v1/search?"
                     + urllib.parse.urlencode({"name": query, "count": 8, "language": lang, "format": "json"}))
    return [{"name": p.get("name") or "", "admin": p.get("admin1") or "", "country": p.get("country") or "",
             "lat": float(p["latitude"]), "lon": float(p["longitude"])}
            for p in data.get("results") or [] if p.get("latitude") is not None]


def fetch_weather(lat: float, lon: float):
    """Current weather (same keys the Pips server sends, plus precip) or None."""
    params = {"latitude": round(lat, 4), "longitude": round(lon, 4), "timezone": "auto",
              "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,"
                         "wind_speed_10m,precipitation"}
    try:
        data = http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    except Exception as exc:
        logger.debug("weather fetch failed: %s", exc)
        return None
    cur = data.get("current") or {}
    if "weather_code" not in cur:
        return None
    return {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"), "code": int(cur.get("weather_code") or 0),
            "is_day": int(cur.get("is_day", 1)), "wind": cur.get("wind_speed_10m") or 0,
            "precip": cur.get("precipitation") or 0, "utc_offset": data.get("utc_offset_seconds", 0),
            "fetched": time.time()}


# ---------------------------------------------------------------------------
# Background service
# ---------------------------------------------------------------------------
class WeatherService:
    """Finds where you are and what the weather is like there, in a background thread."""

    def __init__(self, cache_path=None, lang: str = "en"):
        self.cache_path = Path(cache_path) if cache_path else None
        self.lang = lang
        self.mode = DEFAULT_MODE
        self.city_place = None
        self.place = None
        self.weather = None
        self.status = ""                 # "", "denied", "nodata", "offline", "no_location", "no_place"
        self.updates = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._next_loc = 0.0
        self._next_wx = 0.0
        self._windows_retry = 0.0
        self.locate_windows = windows_location     # swappable in tests
        self.locate_ip = ip_location
        self.name_place = reverse_geocode
        self.get_weather = fetch_weather
        self._load_cache()

    # ---- control (Tk thread) ----
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="pips-weather", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def configure(self, mode: str, city_place: dict | None = None):
        mode = mode if mode in MODES else DEFAULT_MODE
        changed = mode != self.mode or (mode == "city" and distance_km(city_place, self.city_place) > 0.01)
        self.mode = mode
        self.city_place = dict(city_place) if city_place else None
        if changed:
            self._windows_retry = 0.0
            self.refresh_now()

    def refresh_now(self):
        self._next_loc = 0.0
        self._next_wx = 0.0
        self._wake.set()

    def poll(self):
        """Latest update posted by the worker (or None)."""
        latest = None
        while True:
            try:
                latest = self.updates.get_nowait()
            except queue.Empty:
                return latest

    # ---- worker ----
    def _run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                logger.exception("weather step failed")
                self._next_loc = self._next_wx = time.time() + RETRY_S
            delay = max(2.0, min(self._next_loc, self._next_wx) - time.time())
            self._wake.wait(timeout=min(delay, 300.0))
            self._wake.clear()

    def step(self, now: float | None = None):
        """One round: locate if due, fetch the weather if due (or if we moved). Returns the update."""
        now = time.time() if now is None else now
        mode = self.mode
        if mode == "off":
            had = self.place is not None or self.weather is not None
            self.place = self.weather = None
            self.status = ""
            self._next_loc = self._next_wx = now + 3600
            if had:
                return self._post(moved=True)
            return None
        moved = False
        if now >= self._next_loc:
            place, status = self._locate(mode, now)
            self.status = status
            if place:
                if (self.place is None or distance_km(place, self.place) > MOVE_KM
                        or place.get("source") != self.place.get("source")):
                    moved = True
                self.place = place
            self._next_loc = now + ({"follow": FOLLOW_CHECK_S, "ip": IP_CHECK_S}.get(mode, 24 * 3600)
                                    if place else RETRY_S)
        if self.place and (moved or now >= self._next_wx or self.weather is None):
            weather = self.get_weather(self.place["lat"], self.place["lon"])
            if weather:
                self.weather = weather
                self._next_wx = now + WEATHER_REFRESH_S
                self._save_cache()
            else:
                self.status = self.status or "offline"
                self._next_wx = now + RETRY_S
        elif not self.place:
            self._next_wx = now + RETRY_S
        return self._post(moved=moved)

    def _locate(self, mode: str, now: float):
        if mode == "city":
            if not self.city_place:
                return None, "no_place"
            place = dict(self.city_place)
            place["source"] = "city"
            return place, ""
        status = ""
        if mode == "follow":
            if now >= self._windows_retry:
                spot, reason = self.locate_windows()
                if spot:
                    place = dict(spot)
                    place["source"] = "windows"
                    if self.place and self.place.get("source") == "windows" and distance_km(place, self.place) < MOVE_KM:
                        for key in ("name", "admin", "country"):
                            place[key] = self.place.get(key, "")
                    else:
                        place.update(self.name_place(place["lat"], place["lon"], self.lang))
                    return place, ""
                status = reason
                self._windows_retry = now + WINDOWS_RETRY_S
            else:
                status = "denied"
        place = self.locate_ip()
        if place:
            return place, status
        return None, status or "no_location"

    def _post(self, moved=False):
        update = {"place": dict(self.place) if self.place else None,
                  "weather": dict(self.weather) if self.weather else None,
                  "status": self.status, "moved": bool(moved), "mode": self.mode}
        self.updates.put(update)
        return update

    # ---- cache (so the weather shows right away after a restart) ----
    def _load_cache(self):
        if not self.cache_path:
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        weather = data.get("weather") or None
        if weather and time.time() - float(weather.get("fetched", 0)) < CACHE_MAX_AGE_S:
            self.weather = weather
            self.place = data.get("place") or None

    def _save_cache(self):
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"place": self.place, "weather": self.weather}), encoding="utf-8")
            tmp.replace(self.cache_path)
        except OSError as exc:
            logger.debug("weather cache not saved: %s", exc)


# ---------------------------------------------------------------------------
# Desktop weather animation
# ---------------------------------------------------------------------------
def make_click_through(win) -> bool:
    """Let mouse clicks pass through a (colour-keyed) Tk toplevel; keep it out of Alt+Tab."""
    try:
        user32 = ctypes.windll.user32
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        gwl_exstyle = -20
        style = user32.GetWindowLongW(hwnd, gwl_exstyle)
        style |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000   # LAYERED|TRANSPARENT|TOOLWINDOW|NOACTIVATE
        user32.SetWindowLongW(hwnd, gwl_exstyle, style)
        return True
    except Exception as exc:  # pragma: no cover - non-Windows
        logger.debug("click-through failed: %s", exc)
        return False


RAIN = "#8fbbe6"
RAIN_DARK = "#5d8fc4"
SNOW = "#ffffff"
SNOW_EDGE = "#b9c8d8"
CLOUD = {"light": ("#eef2f6", "#c3ccd6"), "grey": ("#c2cad4", "#97a2ae"), "dark": ("#7b8491", "#565e6a")}
UMBRELLA = ("#ff6b6b", "#ffd166", "#4ecdc4", "#a78bfa")


class WeatherOverlay:
    """The weather, drawn on the desktop: around the pet or over the whole screen (click-through)."""

    FRAME_MS = 40

    def __init__(self, root, key="#010203"):
        self.root = root
        self.key = key
        self.win = None
        self.cv = None
        self.mode = DEFAULT_FX
        self.weather = None
        self.cond = None
        self.day = True
        self.wind = 0.0
        self.shown = False
        self.want = False
        self.region = (0, 0, 0, 0)
        self.pet = {"px": 0, "floor": 0, "size": 64, "head": 0, "umbrella": True}
        self.scene_key = None
        self.t0 = time.monotonic()
        self.last = self.t0
        self._job = None
        self.drops = []
        self.flakes = []
        self.splashes = []
        self.splash_i = 0
        self.leaves = []
        self.stars = []
        self.fog = []
        self.clouds = []
        self.pile = None
        self.pile_w = 0.0
        self.bolt = None
        self.flash = None
        self.flash_until = 0.0
        self.next_flash = 0.0
        self.umbrella = []
        self.umbrella_colour = random.choice(UMBRELLA)
        self.sun = []
        self.moon = []
        self.frames = 0
        self.frame_time = 0.0
        self.rng = random.Random()

    # ---- public API ----
    def set_mode(self, mode: str):
        self.mode = mode if mode in FX_MODES else DEFAULT_FX
        self.scene_key = None

    def set_weather(self, weather: dict | None):
        self.weather = dict(weather) if weather else None
        if weather:
            self.cond = condition(weather.get("code"))
            self.day = bool(weather.get("is_day", 1))
            try:
                self.wind = float(weather.get("wind") or 0)
            except (TypeError, ValueError):
                self.wind = 0.0
        else:
            self.cond = None
        self.scene_key = None

    def follow(self, px, floor, size, head, work, show=True, umbrella=True):
        """Called by the pet every frame: where it is (screen coords) and the monitor work area."""
        self.pet = {"px": float(px), "floor": float(floor), "size": float(size), "head": float(head),
                    "umbrella": bool(umbrella)}
        self.want = bool(show and self.weather and self.mode != "off" and self._has_content())
        if not self.want:
            self._hide()
            return
        if self.mode == "pet":
            s = size
            w = int(max(s * 4.6, 260))
            h = int(s * 3.8 + 40)
            x = int(px - w / 2)
            y = int(floor + s * 0.25) - h
        else:
            x, y = int(work[0]), int(work[1])
            w, h = int(work[2] - work[0]), int(work[3] - work[1])
        if not self._ensure():
            return
        if (x, y, w, h) != self.region:
            size_changed = (w, h) != self.region[2:]
            self.region = (x, y, w, h)
            try:
                self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
                if size_changed:
                    self.cv.config(width=w, height=h)
            except tk.TclError:
                return
            if size_changed:
                self.scene_key = None
        if not self.shown:
            self.shown = True
            try:
                self.win.deiconify()
                self.win.attributes("-topmost", True)
            except tk.TclError:
                pass
            make_click_through(self.win)
            self._schedule()

    def destroy(self):
        self._hide()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None

    # ---- internals ----
    def _has_content(self) -> bool:
        if self.cond is None:
            return False
        if self.mode == "screen" and self.cond == "clear" and self.day and self.wind < 28:
            return False          # a sunny screen needs nothing drawn on it
        return True

    def _ensure(self) -> bool:
        if self.win is not None:
            return True
        try:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=self.key)
            try:
                win.attributes("-transparentcolor", self.key)
            except tk.TclError:
                pass
            cv = tk.Canvas(win, bg=self.key, highlightthickness=0, bd=0)
            cv.pack(fill="both", expand=True)
            win.withdraw()
            self.win, self.cv = win, cv
            return True
        except tk.TclError:
            return False

    def _hide(self):
        if self.shown and self.win is not None:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
        self.shown = False
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _schedule(self):
        if self._job is None and self.shown:
            self._job = self.root.after(self.FRAME_MS, self._frame)

    def _frame(self):
        self._job = None
        if not self.shown or self.win is None:
            return
        started = time.perf_counter()
        try:
            self._draw()
        except tk.TclError:
            return
        except Exception:
            logger.exception("weather frame failed")
        self.frames += 1
        self.frame_time += time.perf_counter() - started
        self._schedule()

    # ---- scene ----
    def _local_pet(self):
        x, y, _w, _h = self.region
        p = self.pet
        return p["px"] - x, p["floor"] - y, p["size"], p["head"] - y

    def _build(self):
        cv = self.cv
        cv.delete("all")
        _x, _y, w, h = self.region
        cx, floor, s, head = self._local_pet()
        rng = self.rng
        cond = self.cond
        pet_mode = self.mode == "pet"
        self.drops, self.flakes, self.splashes, self.leaves = [], [], [], []
        self.stars, self.fog, self.clouds, self.umbrella, self.sun, self.moon = [], [], [], [], [], []
        self.pile = self.bolt = self.flash = None
        self.pile_w = 0.0
        self.next_flash = time.monotonic() + rng.uniform(1.5, 4.0)

        # sky pieces
        if cond == "clear" or cond == "partly":
            if self.day:
                if pet_mode:
                    self._make_sun(cx + s * 1.45, floor - s * 2.55, s * 0.32)
            else:
                count = 8 if pet_mode else max(18, int(w / 90))
                for _ in range(count):
                    if pet_mode:
                        sx, sy = rng.uniform(8, w - 8), rng.uniform(6, max(8, floor - s * 1.9))
                    else:
                        sx, sy = rng.uniform(10, w - 10), rng.uniform(6, h * 0.16)
                    r = rng.uniform(1.6, 3.2)
                    item = cv.create_polygon(self._star_pts(sx, sy, r), fill="#fff3b0", outline="")
                    self.stars.append([item, sx, sy, r, rng.uniform(0, 6.28), rng.uniform(1.5, 3.5)])
                if pet_mode:
                    self._make_moon(cx + s * 1.45, floor - s * 2.5, s * 0.28)
        if cond == "fog":
            # thin drifting wisps (Tk on Windows has no transparency, so no solid fog banks)
            wisps = 9 if pet_mode else max(14, int(w / 120))
            for i in range(wisps):
                bw = rng.uniform(s * 0.6, s * 1.6) if pet_mode else rng.uniform(80, 220)
                by = (floor - rng.uniform(0.05, 1.6) * s) if pet_mode else h * rng.uniform(0.55, 0.97)
                bx = rng.uniform(-bw, w)
                width = rng.uniform(2.0, 4.5) if pet_mode else rng.uniform(2.5, 6.0)
                item = cv.create_line(bx, by, bx + bw, by, fill=rng.choice(("#e6ebf0", "#d6dde5", "#f2f5f8")),
                                      width=width, capstyle="round")
                self.fog.append([item, bx, by, bw, width, rng.choice((-1, 1)) * rng.uniform(6, 18)])

        cloud_kind = {"partly": "light", "cloudy": "grey", "drizzle": "grey", "rain": "grey", "snow": "light",
                      "heavy_rain": "dark", "storm": "dark"}.get(cond)
        if cloud_kind:
            if pet_mode:
                ccx, ccy = cx, max(s * 0.55, head - s * 0.95)
                if cond == "partly":
                    ccx, ccy = cx + s * 1.05, floor - s * 2.35
                self.clouds.append(self._make_cloud(ccx, ccy, s * (0.62 if cond == "partly" else 1.0), cloud_kind))
            elif cond in ("heavy_rain", "storm", "snow"):
                # only the bottoms of a few clouds peek in at the top edge of the screen
                for i in range(max(3, int(w / 500))):
                    ccx = rng.uniform(0, w)
                    self.clouds.append(self._make_cloud(ccx, -12.0, rng.uniform(0.9, 1.4), cloud_kind,
                                                        speed=rng.uniform(4, 12)))

        # precipitation
        if cond in WET:
            per = {"drizzle": 0.45, "rain": 1.0, "heavy_rain": 1.6, "storm": 1.4}[cond]
            count = int((22 if pet_mode else w / 20) * per)
            width = 1 if cond == "drizzle" else 2
            colour = RAIN_DARK if cond in ("heavy_rain", "storm") else RAIN
            for _ in range(count):
                item = cv.create_line(0, 0, 0, 0, fill=colour, width=width, capstyle="round")
                drop = [item, 0.0, 0.0, 0.0, 0.0]
                self._respawn_drop(drop, initial=True)
                self.drops.append(drop)
            for _ in range(10 if pet_mode else 28):
                item = cv.create_oval(0, 0, 0, 0, outline=colour, width=1, state="hidden")
                self.splashes.append([item, 0.0, 0.0, 0.0])
        if cond == "snow":
            count = 26 if pet_mode else int(w / 26)
            for _ in range(count):
                r = rng.uniform(1.6, 3.4)
                item = cv.create_oval(0, 0, 0, 0, fill=SNOW, outline=SNOW_EDGE)
                flake = [item, 0.0, 0.0, r, rng.uniform(0, 6.28), 0.0]
                self._respawn_flake(flake, initial=True)
                self.flakes.append(flake)
            if pet_mode:
                self.pile = cv.create_oval(0, 0, 0, 0, fill=SNOW, outline=SNOW_EDGE)
        if cond == "storm":
            self.bolt = cv.create_line(0, 0, 0, 0, fill="#fff176", width=3 if pet_mode else 4, state="hidden")
        if self.wind >= 28:
            for _ in range(4 if pet_mode else 10):
                item = cv.create_oval(0, 0, 0, 0, fill=rng.choice(("#8bc34a", "#c5a25a", "#e0893b")), outline="")
                leaf = [item, rng.uniform(-w, 0), rng.uniform(h * 0.3, h * 0.9), rng.uniform(0, 6.28),
                        rng.uniform(0.8, 1.4)]
                self.leaves.append(leaf)
        if cond in WET:
            self._make_umbrella()

    def _star_pts(self, x, y, r):
        pts = []
        for i in range(8):
            rad = r if i % 2 == 0 else r * 0.38
            a = math.pi / 4 * i
            pts += [x + math.cos(a) * rad, y + math.sin(a) * rad]
        return pts

    def _make_sun(self, x, y, r):
        cv = self.cv
        rays = [cv.create_line(0, 0, 0, 0, fill="#ffb300", width=max(2, int(r / 6)), capstyle="round")
                for _ in range(8)]
        disc = cv.create_oval(x - r, y - r, x + r, y + r, fill="#ffd54f", outline="#ffb300", width=2)
        self.sun = [disc, rays, x, y, r]

    def _make_moon(self, x, y, r):
        cv = self.cv
        disc = cv.create_oval(x - r, y - r, x + r, y + r, fill="#fff3b0", outline="")
        bite = cv.create_oval(x - r * 0.45, y - r * 1.05, x + r * 1.45, y + r * 0.85, fill=self.key, outline="")
        self.moon = [disc, bite, x, y, r]

    def _make_cloud(self, x, y, scale, kind, stipple="", speed=0.0):
        fill, edge = CLOUD[kind]
        puffs = ((-0.55, 0.12, 0.42), (-0.15, -0.12, 0.55), (0.35, -0.02, 0.48), (0.7, 0.18, 0.34),
                 (0.05, 0.22, 0.45))
        unit = 40 * scale if self.mode == "screen" else scale
        # an outline layer underneath, then the puffs without outlines: one soft silhouette
        backs = [self.cv.create_oval(0, 0, 0, 0, fill=edge, outline="") for _ in puffs]
        items = [self.cv.create_oval(0, 0, 0, 0, fill=fill, outline="") for _ in puffs]
        cloud = {"items": items, "backs": backs, "x": x, "y": y, "unit": unit, "puffs": puffs, "speed": speed,
                 "fill": fill, "phase": self.rng.uniform(0, 6.28)}
        self._place_cloud(cloud, 0.0)
        return cloud

    def _place_cloud(self, cloud, bob):
        u = cloud["unit"]
        edge = max(1.2, u * 0.03)
        for item, back, (dx, dy, r) in zip(cloud["items"], cloud["backs"], cloud["puffs"]):
            x = cloud["x"] + dx * u
            y = cloud["y"] + dy * u + bob
            self.cv.coords(back, x - r * u - edge, y - r * u - edge, x + r * u + edge, y + r * u + edge)
            self.cv.coords(item, x - r * u, y - r * u, x + r * u, y + r * u)

    def _cloud_span(self):
        """x-range and bottom edge where rain / snow leave the pet's cloud."""
        if self.mode == "pet" and self.clouds:
            c = self.clouds[0]
            u = c["unit"]
            return c["x"] - 0.85 * u, c["x"] + 1.0 * u, c["y"] + 0.45 * u
        _x, _y, w, _h = self.region
        return -40.0, w + 40.0, -20.0

    def _make_umbrella(self):
        cv = self.cv
        colour = self.umbrella_colour
        canopy = cv.create_arc(0, 0, 0, 0, start=0, extent=180, style="pieslice", fill=colour,
                               outline="#5a3d2b", width=1)
        ribs = [cv.create_line(0, 0, 0, 0, fill="#5a3d2b", width=1) for _ in range(2)]
        handle = cv.create_line(0, 0, 0, 0, fill="#5a3d2b", width=2, smooth=True)
        self.umbrella = [canopy, ribs, handle]

    def _umbrella_geom(self):
        cx, _floor, s, head = self._local_pet()
        r = s * 0.78
        top = head - s * 0.18
        return cx, top, r

    def _respawn_drop(self, drop, initial=False):
        rng = self.rng
        _x, _y, w, h = self.region
        x0, x1, top = self._cloud_span()
        speed = {"drizzle": 260, "rain": 420, "heavy_rain": 540, "storm": 520}.get(self.cond, 400)
        speed *= rng.uniform(0.85, 1.15)
        if self.mode == "screen":
            speed *= 1.6
        length = speed * (0.03 if self.cond == "drizzle" else 0.045)
        drop[1] = rng.uniform(x0, x1)
        drop[2] = rng.uniform(top, h) if initial else top - rng.uniform(0, 30)
        drop[3] = speed
        drop[4] = length

    def _respawn_flake(self, flake, initial=False):
        rng = self.rng
        _x, _y, w, h = self.region
        x0, x1, top = self._cloud_span()
        flake[1] = rng.uniform(x0, x1)
        flake[2] = rng.uniform(top, h) if initial else top - rng.uniform(0, 20)
        flake[5] = rng.uniform(28, 55) * (1.5 if self.mode == "screen" else 1.0)

    def _splash(self, x, y):
        if not self.splashes:
            return
        sp = self.splashes[self.splash_i % len(self.splashes)]
        self.splash_i += 1
        sp[1], sp[2], sp[3] = x, y, 0.0
        self.cv.itemconfigure(sp[0], state="normal")

    # ---- per frame ----
    def _draw(self):
        now = time.monotonic()
        dt = min(0.1, now - self.last)
        self.last = now
        tt = now - self.t0
        key = (self.mode, self.cond, self.day, self.wind >= 28, self.region[2], self.region[3],
               int(self.pet["size"]))
        if key != self.scene_key:
            self.scene_key = key
            self._build()
        cv = self.cv
        _x, _y, w, h = self.region
        cx, floor, s, head = self._local_pet()
        pet_mode = self.mode == "pet"
        ground = floor if pet_mode else h - 2
        wind_dx = max(-0.5, min(0.5, self.wind / 90.0))

        # pet cloud follows the pet's head (it moves with it); screen clouds drift
        for cloud in self.clouds:
            if pet_mode:
                if self.cond != "partly":
                    cloud["x"] = cx
                    cloud["y"] = max(s * 0.55, head - s * 0.95)
                self._place_cloud(cloud, math.sin(tt * 1.3 + cloud["phase"]) * s * 0.04)
            else:
                cloud["x"] += cloud["speed"] * dt
                if cloud["x"] - 1.2 * cloud["unit"] > w:
                    cloud["x"] = -1.4 * cloud["unit"]
                self._place_cloud(cloud, 0.0)

        # umbrella over the pet's head
        ux = utop = ur = None
        if self.umbrella:
            show_umb = self.pet["umbrella"]
            canopy, ribs, handle = self.umbrella
            state = "normal" if show_umb else "hidden"
            if show_umb:
                ux, utop, ur = self._umbrella_geom()
                tilt = math.sin(tt * 1.7) * ur * 0.04
                cv.coords(canopy, ux - ur + tilt, utop, ux + ur + tilt, utop + ur * 1.1)
                cv.coords(ribs[0], ux + tilt, utop, ux - ur * 0.45 + tilt, utop + ur * 0.55)
                cv.coords(ribs[1], ux + tilt, utop, ux + ur * 0.45 + tilt, utop + ur * 0.55)
                hy = utop + ur * 0.55
                cv.coords(handle, ux + tilt, hy, ux + tilt * 0.5, hy + s * 0.55, ux - s * 0.06, hy + s * 0.62,
                          ux - s * 0.11, hy + s * 0.55)
            for item in [canopy, handle] + ribs:
                cv.itemconfigure(item, state=state)

        # rain
        for drop in self.drops:
            item, x, y, speed, length = drop
            y += speed * dt
            x += speed * dt * wind_dx
            hit = None
            if ux is not None and abs(x - ux) < ur * 0.98:
                dome = utop + ur * 0.55 * (1 - math.sqrt(max(0.0, 1 - ((x - ux) / ur) ** 2)))
                if dome - 2 <= y < utop + ur * 0.6:
                    hit = dome
            if y >= ground:
                hit = ground
            if hit is not None:
                self._splash(x, hit)
                self._respawn_drop(drop)
                continue
            drop[1], drop[2] = x, y
            cv.coords(item, x, y - length, x - length * wind_dx, y)
        for sp in self.splashes:
            if sp[3] >= 0.0 and cv.itemcget(sp[0], "state") == "normal":
                sp[3] += dt
                if sp[3] > 0.22:
                    cv.itemconfigure(sp[0], state="hidden")
                    sp[3] = -1.0
                    continue
                r = 2 + sp[3] * 26
                cv.coords(sp[0], sp[1] - r, sp[2] - r * 0.35, sp[1] + r, sp[2] + r * 0.2)

        # snow
        landed = 0
        for flake in self.flakes:
            item, x, y, r, phase, speed = flake
            y += speed * dt
            x += math.sin(tt * 1.4 + phase) * 18 * dt + speed * dt * wind_dx
            if ux is not None and abs(x - ux) < ur and y >= utop:
                y = 9999
            if y >= ground - r:
                landed += 1
                self._respawn_flake(flake)
                continue
            flake[1], flake[2] = x, y
            cv.coords(item, x - r, y - r, x + r, y + r)
        if self.pile is not None:
            self.pile_w = min(s * 1.7, self.pile_w + landed * s * 0.004)
            pw = self.pile_w
            cv.coords(self.pile, cx - pw / 2, floor - pw * 0.14, cx + pw / 2, floor + pw * 0.08)

        # lightning
        if self.bolt is not None:
            if now >= self.next_flash:
                self.next_flash = now + self.rng.uniform(3.0, 7.5)
                self.flash_until = now + 0.16
                x0, x1, top = self._cloud_span()
                bx = self.rng.uniform(x0, x1) if pet_mode else self.rng.uniform(w * 0.1, w * 0.9)
                if pet_mode and abs(bx - cx) < s * 0.6:
                    bx = cx + s * 0.9 * (1 if bx >= cx else -1)
                pts = [bx, max(0.0, top)]
                y = max(0.0, top)
                bottom = ground if pet_mode else h * self.rng.uniform(0.4, 0.7)
                while y < bottom:
                    y = min(bottom, y + self.rng.uniform(10, 24) * (1 if pet_mode else 3))
                    bx += self.rng.uniform(-9, 9) * (1 if pet_mode else 3)
                    pts += [bx, y]
                cv.coords(self.bolt, *pts)
                cv.itemconfigure(self.bolt, state="normal")
                if self.flash is not None:
                    cv.itemconfigure(self.flash, state="normal")
                for cloud in self.clouds:
                    for item in cloud["items"]:
                        cv.itemconfigure(item, fill="#fff6b0")
            elif self.flash_until and now >= self.flash_until:
                self.flash_until = 0.0
                cv.itemconfigure(self.bolt, state="hidden")
                if self.flash is not None:
                    cv.itemconfigure(self.flash, state="hidden")
                for cloud in self.clouds:
                    for item in cloud["items"]:
                        cv.itemconfigure(item, fill=cloud["fill"])

        # sun, moon & stars
        if self.sun:
            disc, rays, sx, sy, r = self.sun
            if pet_mode:
                sx, sy = cx + s * 1.45, max(r + 4, floor - s * 2.55)
                cv.coords(disc, sx - r, sy - r, sx + r, sy + r)
            spin = tt * 0.6
            pulse = 1.0 + 0.12 * math.sin(tt * 2.2)
            for i, ray in enumerate(rays):
                a = spin + i * math.pi / 4
                cv.coords(ray, sx + math.cos(a) * r * 1.3, sy + math.sin(a) * r * 1.3,
                          sx + math.cos(a) * r * 1.75 * pulse, sy + math.sin(a) * r * 1.75 * pulse)
        if self.moon and pet_mode:
            disc, bite, _mx, _my, r = self.moon
            mx, my = cx + s * 1.45, max(r + 4, floor - s * 2.5)
            cv.coords(disc, mx - r, my - r, mx + r, my + r)
            cv.coords(bite, mx - r * 0.45, my - r * 1.05, mx + r * 1.45, my + r * 0.85)
        for star in self.stars:
            item, sx, sy, r, phase, speed = star
            k = 0.55 + 0.45 * math.sin(tt * speed + phase)
            cv.coords(item, *self._star_pts(sx, sy, r * (0.6 + 0.7 * k)))

        # fog drifts, leaves blow
        for band in self.fog:
            item, bx, by, bw, _width, speed = band
            bx += speed * dt
            if bx > w:
                bx = -bw
            elif bx + bw < 0:
                bx = w
            band[1] = bx
            wave = math.sin(tt * 0.8 + bw) * 2
            cv.coords(item, bx, by + wave, bx + bw, by - wave)
        for leaf in self.leaves:
            item, lx, ly, phase, k = leaf
            lx += (60 + self.wind * 2) * k * dt
            if lx > w + 10:
                lx = self.rng.uniform(-80, -10)
                ly = self.rng.uniform(h * 0.3, h * 0.9)
            leaf[1], leaf[2] = lx, ly
            yy = ly + math.sin(tt * 3 + phase) * 8
            rr = 3 + 2 * abs(math.sin(tt * 4 + phase))
            cv.coords(item, lx - rr, yy - 2, lx + rr, yy + 2)


# ---------------------------------------------------------------------------
# "Pick a place" window
# ---------------------------------------------------------------------------
class PlacePicker:
    """Small window: search a city / area, or paste exact coordinates, then pick one."""

    def __init__(self, root, labels: dict, search, on_pick, colours: dict | None = None):
        c = {"bg": "#fffaf3", "card": "#ffffff", "ink": "#2b2b2b", "muted": "#8a8a8a", "accent": "#e8833a"}
        c.update(colours or {})
        self.c = c
        self.labels = labels
        self.search = search
        self.on_pick = on_pick
        self.root = root
        self.results = queue.Queue()
        self.win = tk.Toplevel(root)
        self.win.title(labels.get("title", "Pick a place"))
        self.win.configure(bg=c["bg"], padx=14, pady=12)
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        tk.Label(self.win, text=labels.get("hint", ""), bg=c["bg"], fg=c["muted"], font=("Segoe UI", 9),
                 wraplength=330, justify="left").pack(fill="x")
        row = tk.Frame(self.win, bg=c["bg"])
        row.pack(fill="x", pady=(8, 6))
        self.var = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.var, font=("Segoe UI", 10), width=26, relief="solid", bd=1)
        self.entry.pack(side="left", ipady=3)
        self.entry.bind("<Return>", lambda e: self.go())
        tk.Button(row, text=labels.get("search", "Search"), command=self.go, bg=c["accent"], fg="#ffffff",
                  relief="flat", font=("Segoe UI", 9, "bold"), padx=10, cursor="hand2").pack(side="left", padx=6)
        self.status = tk.Label(self.win, text="", bg=c["bg"], fg=c["muted"], font=("Segoe UI", 8))
        self.status.pack(fill="x")
        self.box = tk.Frame(self.win, bg=c["bg"])
        self.box.pack(fill="x")
        self.win.update_idletasks()
        try:
            x = root.winfo_pointerx() - 180
            y = root.winfo_pointery() - 220
            self.win.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except tk.TclError:
            pass
        self.entry.focus_set()

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def go(self):
        query = self.var.get().strip()
        if len(query) < 2:
            return
        self.status.config(text=self.labels.get("searching", "..."))

        def work():
            try:
                self.results.put(("ok", self.search(query)))
            except Exception as exc:
                self.results.put(("err", str(exc)))

        threading.Thread(target=work, daemon=True).start()
        self.win.after(150, self._poll)

    def _poll(self):
        if not self.alive():
            return
        try:
            kind, payload = self.results.get_nowait()
        except queue.Empty:
            self.win.after(150, self._poll)
            return
        self.show(payload if kind == "ok" else [], error=kind != "ok")

    def show(self, places, error=False):
        for child in self.box.winfo_children():
            child.destroy()
        c = self.c
        if not places:
            self.status.config(text=self.labels.get("offline" if error else "none", ""))
            return
        self.status.config(text="")
        for place in places[:7]:
            row = tk.Frame(self.box, bg=c["card"], padx=6, pady=3)
            row.pack(fill="x", pady=1)
            text = place_label(place, long=True)
            if place.get("exact"):
                text = "\U0001F4CD " + text + "  (" + self.labels.get("exact", "exact") + ")"
            tk.Label(row, text=text, bg=c["card"], fg=c["ink"], font=("Segoe UI", 9), anchor="w",
                     wraplength=250, justify="left").pack(side="left", fill="x", expand=True)
            tk.Button(row, text=self.labels.get("use", "Use"), command=lambda p=place: self.pick(p),
                      bg=c["accent"], fg="#ffffff", relief="flat", font=("Segoe UI", 8, "bold"), padx=8,
                      cursor="hand2").pack(side="right")

    def pick(self, place):
        try:
            self.on_pick(place)
        finally:
            try:
                self.win.destroy()
            except tk.TclError:
                pass


if __name__ == "__main__":   # quick check: python weather_live.py
    logging.basicConfig(level=logging.DEBUG)
    svc = WeatherService()
    svc.mode = "follow"
    print(svc.step())
