"""
pet.py - the Pips desktop pet.

A tiny animated companion that lives on your desktop and taskbar.

  * 23 animals in 3 groups - Land (walks), Water (swims / floats, bubbles),
    Air (hovers & flies with flapping wings). All drawn with tk Canvas
    primitives, so every colour is customisable.
  * Drag it anywhere (multi-monitor aware). Drop it on / near the taskbar and it
    lives there and wanders along it on its own.
  * Click  -> tickle animation (+ optionally opens the Pips card; configurable).
  * Hover-rub back and forth over it -> petting (hearts!).
  * Shake it while dragging -> it gets dizzy (and a bit upset).
  * Right-click -> menu (feed, sleep / wake, animal, colour, size, language...).
  * Moods: very happy / happy / okay / sad / very sad / tired / hungry / grumpy /
    sleeping - driven by how you treat it and how your day goes.
  * Sleeps when tired or when you are away; wakes up when you come back.
  * Cute chat bubbles for reminders and notifications.
  * Every animal has its own voice (pet_sounds.py) that changes with its mood.

Settings live in config.json -> "pet"; the pet's state in data/pet_state.json.
All user-facing text comes from i18n.py.
"""

from __future__ import annotations

import ctypes
import json
import logging
import math
import random
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser

try:
    import i18n
    from i18n import t, lines
except ImportError:  # pragma: no cover - i18n ships with Pips
    i18n = None

    def t(key, lang=None, **kwargs):
        return key.format(**kwargs) if kwargs else key

    def lines(key):
        return [key]

try:
    import pet_sounds
except ImportError:  # pragma: no cover - sounds are optional
    pet_sounds = None

try:
    import pet_songs       # v2.22: songs for sing / dance / other tricks
except Exception:  # pragma: no cover - music is optional
    pet_songs = None

try:
    import weather_live    # v2.23: where am I + live weather on the desktop
except Exception:  # pragma: no cover - weather is optional
    weather_live = None

try:
    import social
except ImportError:  # pragma: no cover - friends / achievements are optional
    social = None

try:
    import pet_shop
except ImportError:  # pragma: no cover - shop is optional
    pet_shop = None

try:
    import tama                  # v2.18: boy / girl, growing up, sick days, Left or Right?
except ImportError:  # pragma: no cover
    tama = None

try:
    import minigames             # v2.24: Treat Catch, Bonk the Bugs, Memory Match, Pet Says, Hop Hop
except ImportError:  # pragma: no cover
    minigames = None

try:
    import roam                  # v2.19: land walks on windows, water leaps & splashes, air flies
except ImportError:  # pragma: no cover
    roam = None

logger = logging.getLogger("pet")

# Transparent colour key: pixels of exactly this colour are see-through and
# click-through. It is never used for drawing.
KEY = "#010203"

DEFAULT_PET_SETTINGS = {
    "enabled": True,
    "auto_update": True,         # check for new Pips versions in the background
    "auto_install": True,        # v2.28: install found updates by itself (only when no focus session runs)
    "name": "Pip",
    "species": "cat",
    "color": "#ffc78f",
    "cheek_color": "#ff8fa3",
    "size": "M",                 # S | M | L
    "click_action": "tickle_only",  # tickle_only (double-click opens the hub) | tickle_hub | tickle_open | tickle_never
    "bubble_reminders": True,    # show health/break reminders as chat bubbles
    "walk_on_taskbar": True,     # move around by myself: walk (land), swim & leap (water), fly (air)
    "sleep_when_away": True,
    "away_minutes": 5,
    "open_near_pet": True,       # open the Pips card next to the pet
    "start_with_card": False,    # show the Pips card at launch
    "chatty": True,              # occasional little messages
    "hide_in_fullscreen": True,  # get out of the way of games / videos
    "sounds": True,              # little animal voices (meow, ribbit, hoot...)
    "sound_volume": 60,          # 0..100
    "quiet_focus": True,         # no pet sounds during a focus session
    "trick_song": "own",         # v2.22: music for sing / dance tricks (pet_songs.CHOICES or "sp:<species>")
    "weather_mode": "ip",        # v2.23: follow (exact, Windows location) | ip (approximate) | city | off
    "weather_place": None,       # v2.23: the picked place for "city" {name, admin, country, lat, lon}
    "weather_fx": "pet",         # v2.23: weather animation: pet (around me) | screen | off
    "weather_share": True,       # v2.23: keep my Friends profile city in sync (when Friends is on)
    "friends": False,            # opt-in: share level / pomodoro counts with friends (social.py)
    "gender": "",                # v2.18: "boy" | "girl" | "" (secret)
    "sick_days": True,           # v2.18: catches a little cold when I skip breaks for 2.5 hours
}

COLOR_PRESETS = [
    ("Peach", "#ffc78f"),
    ("Bubblegum", "#ffb3d1"),
    ("Mint", "#a8e6cf"),
    ("Sky", "#a0d2ff"),
    ("Lavender", "#cdb4ff"),
    ("Lemon", "#fff1a8"),
    ("Snow", "#f4f4f4"),
    ("Cocoa", "#c69c7b"),
    ("Charcoal", "#5b5f73"),
    ("Pips Red", "#e0645f"),
]

CHEEK_PRESETS = [
    ("Pink", "#ff8fa3"),
    ("Coral", "#ff9e7a"),
    ("Rose", "#f06292"),
    ("Lilac", "#c39bff"),
    ("Peach", "#ffb38a"),
]

SIZES = {"S": 48, "M": 64, "L": 84}

CATEGORIES = ["land", "water", "air"]

# Species catalogue. Keys:
#   cat      land | water | air  (decides how it moves)
#   color    default body colour
#   ears     head feature      tail   tail style      feet  feet style
#   extras   decorations / body parts
#   body     (rx, ry) multipliers      shape  "ellipse" | "dome"
#   lift     raise body (room for tentacles)   float  idle hover height (water)
#   hop      moves by hopping (bunny, frog)
#   eye_sep / eye_dy / eye_scale / mouth_dy   face layout tweaks
#   eye_col  force eye colour       face_light  eyes sit on a light patch
SPECIES = {
    # ---------------- land ----------------
    "cat": dict(cat="land", color="#ffc78f", ears="cat", tail="cat", feet="paws", extras=("whiskers",)),
    "dog": dict(cat="land", color="#e9c49a", ears="dog", tail="dog", feet="paws", extras=("muzzle", "nose")),
    "bunny": dict(cat="land", color="#f6f0f0", ears="bunny", tail="puff", feet="paws", extras=("nose_pink",),
                  hop=True),
    "bear": dict(cat="land", color="#c69c7b", ears="bear", tail="stub", feet="paws", extras=("muzzle", "nose")),
    "panda": dict(cat="land", color="#f7f7f5", ears="panda", tail="stub_dark", feet="paws_dark",
                  extras=("eye_patches", "nose"), eye_ring=True),
    "fox": dict(cat="land", color="#f4a259", ears="fox", tail="fox", feet="paws_dark",
                extras=("fox_mask", "whiskers", "nose")),
    "hamster": dict(cat="land", color="#f3c98b", ears="hamster", tail=None, feet="paws",
                    extras=("muzzle", "nose_pink"), body=(1.06, 0.95), cheek_scale=1.45),
    "pig": dict(cat="land", color="#ffc4d0", ears="pig", tail="curly", feet="paws", extras=("snout",),
                mouth_dy=2.6),
    "sprout": dict(cat="land", color="#a8e6cf", ears="sprout", tail=None, feet="paws"),
    # ---------------- water ----------------
    "fish": dict(cat="water", color="#7ec8e3", ears=None, tail="fish", feet=None,
                 extras=("dorsal", "fins", "scales"), body=(1.12, 0.84), float=4),
    "whale": dict(cat="water", color="#6fa8dc", ears=None, tail="whale", feet=None,
                  extras=("whale_belly", "fins"), body=(1.22, 0.86), float=3),
    "octopus": dict(cat="water", color="#f497b6", ears=None, tail=None, feet="tentacles", extras=("spots",),
                    body=(0.95, 1.0), lift=7, float=1),
    "axolotl": dict(cat="water", color="#ffc2d9", ears="gills", tail="axolotl", feet="paws", extras=(),
                    eye_sep=10, body=(1.08, 0.92)),
    "penguin": dict(cat="water", color="#3d4a5c", ears=None, tail=None, feet="bird",
                    extras=("penguin_belly", "beak", "flippers"), body=(0.92, 1.1), face_light=True),
    "turtle": dict(cat="water", color="#b5e48c", ears=None, tail="turtle", feet="paws", extras=("shell",),
                   eye_dy=4.5, mouth_dy=-0.5),
    "frog": dict(cat="water", color="#8fd694", ears="frog", tail=None, feet="webbed", extras=(),
                 eye_sep=11, eye_dy=-12.5, mouth_dy=6.5, hop=True, body=(1.12, 0.9), eye_ring=True),
    "jellyfish": dict(cat="water", color="#cdb4ff", ears=None, tail=None, feet="jelly", shape="dome",
                      float=6, lift=6),
    # ---------------- air ----------------
    "bird": dict(cat="air", color="#ffe066", ears="tuft", tail="bird", feet="bird",
                 extras=("beak", "wings_bird")),
    "owl": dict(cat="air", color="#b08968", ears="owl", tail=None, feet="bird",
                extras=("owl_face", "beak", "wings_bird"), body=(1.0, 1.08), eye_scale=1.25, face_light=True),
    "bat": dict(cat="air", color="#6d597a", ears="bat", tail=None, feet=None, extras=("wings_bat", "fangs")),
    "bee": dict(cat="air", color="#ffd23f", ears="antennae", tail=None, feet=None,
                extras=("stripes", "wings_bug", "stinger"), flap_hz=30),
    "butterfly": dict(cat="air", color="#ffafcc", ears="antennae", tail=None, feet=None,
                      extras=("wings_butterfly",), body=(0.82, 0.95), flap_hz=7),
    "dragon": dict(cat="air", color="#80ed99", ears="horns", tail="dragon", feet="paws",
                   extras=("wings_dragon", "belly_scales"), flap_hz=7),

    # ---------------- v2.12 land ----------------
    "lion": dict(cat="land", color="#f4c06a", ears="bear", tail="cat", feet="paws",
                 extras=("mane", "muzzle", "nose", "whiskers")),
    "tiger": dict(cat="land", color="#ff9f1c", ears="cat", tail="cat", feet="paws",
                  extras=("tiger_stripes", "whiskers", "nose")),
    "unicorn": dict(cat="land", color="#fdf6ff", ears="cat", tail="puff", feet="paws",
                    extras=("horn", "mane_pastel", "nose_pink")),
    "deer": dict(cat="land", color="#c08552", ears="deer", tail="puff", feet="paws_dark",
                 extras=("antlers", "nose", "spots")),
    "sheep": dict(cat="land", color="#f8f4ec", ears="dog", tail="puff", feet="paws_dark",
                  extras=("wool", "nose_pink")),
    "cow": dict(cat="land", color="#fbfbfb", ears="horns", tail="cat", feet="paws_dark",
                extras=("cow_spots", "snout")),
    "raccoon": dict(cat="land", color="#9a9a9a", ears="cat", tail="ringed", feet="paws_dark",
                    extras=("bandit_mask", "whiskers", "nose"), eye_ring=True),
    "red_panda": dict(cat="land", color="#d9622b", ears="cat", tail="ringed", feet="paws_dark",
                      extras=("fox_mask", "whiskers", "nose")),
    "monkey": dict(cat="land", color="#a0522d", ears="monkey", tail="cat", feet="paws",
                   extras=("monkey_face", "nose"), face_light=True),
    "hedgehog": dict(cat="land", color="#c9a27e", ears="hamster", tail=None, feet="paws",
                     extras=("quills", "nose"), body=(1.08, 0.94)),
    "dino": dict(cat="land", color="#7bd389", ears=None, tail="dragon", feet="paws",
                 extras=("back_spikes", "belly_scales")),
    "squirrel": dict(cat="land", color="#c96f3b", ears="cat", tail="squirrel", feet="paws",
                     extras=("nose_pink", "whiskers"), cheek_scale=1.3),
    "mouse": dict(cat="land", color="#c9c9cf", ears="mouse", tail="cat", feet="paws",
                  extras=("whiskers", "nose_pink"), body=(0.96, 0.95)),
    "koala": dict(cat="land", color="#a8a9ad", ears="koala", tail=None, feet="paws",
                  extras=("big_nose",), body=(1.04, 1.0)),
    # ---------------- v2.12 water ----------------
    "shark": dict(cat="water", color="#8d99ae", ears=None, tail="fish", feet=None,
                  extras=("dorsal", "fins", "whale_belly", "fangs"), body=(1.15, 0.86), float=4),
    "dolphin": dict(cat="water", color="#8ecae6", ears=None, tail="whale", feet=None,
                    extras=("dorsal", "fins", "whale_belly"), body=(1.18, 0.86), float=3),
    "seal": dict(cat="water", color="#b8c0c8", ears=None, tail="turtle", feet=None,
                 extras=("flippers", "whiskers", "nose"), body=(1.15, 0.9)),
    "otter": dict(cat="water", color="#8b5e3c", ears="hamster", tail="dog", feet="webbed",
                  extras=("muzzle", "nose", "whiskers")),
    "puffer": dict(cat="water", color="#ffd166", ears=None, tail="fish", feet=None,
                   extras=("quills", "fins"), body=(1.05, 1.05), float=4),
    "duck": dict(cat="water", color="#fff3b0", ears="tuft", tail="bird", feet="webbed",
                 extras=("beak", "wings_bird")),
    "crab": dict(cat="water", color="#ef6351", ears=None, tail=None, feet="crab",
                 extras=("claws",), body=(1.15, 0.8)),
    # ---------------- v2.12 air ----------------
    "parrot": dict(cat="air", color="#e63946", ears="tuft", tail="bird", feet="bird",
                   extras=("beak", "wings_bird")),
    "chick": dict(cat="air", color="#ffe45e", ears="tuft", tail=None, feet="bird",
                  extras=("beak", "wings_bird"), body=(0.95, 1.0)),
    "ladybug": dict(cat="air", color="#e63946", ears="antennae", tail=None, feet=None,
                    extras=("wings_bug", "cow_spots"), flap_hz=24),
    "phoenix": dict(cat="air", color="#ff7b00", ears="tuft", tail="flame", feet="bird",
                    extras=("beak", "wings_bird"), flap_hz=6),
    "pegasus": dict(cat="air", color="#f1f3f5", ears="cat", tail="puff", feet="paws",
                    extras=("mane_pastel", "wings_bird", "nose_pink")),
    "ghost": dict(cat="air", color="#f1f3f5", ears=None, tail=None, feet=None, shape="dome",
                  float=7, lift=4),
    "flamingo": dict(cat="air", color="#ff8fab", ears=None, tail="bird", feet="stilts",
                     extras=("beak", "wings_bird"), lift=8, body=(0.95, 1.0)),
}

SPECIES_BY_CATEGORY = {c: [k for k, v in SPECIES.items() if v["cat"] == c] for c in CATEGORIES}

# legacy "ears" setting -> species
_EARS_TO_SPECIES = {"cat": "cat", "bunny": "bunny", "bear": "bear", "blob": "sprout"}


def species_spec(name: str) -> dict:
    return SPECIES.get(name) or SPECIES["cat"]


def species_label(name: str) -> str:
    return t("sp_" + name)


# =============================================================================
# Colour helpers
# =============================================================================

def _rgb(color: str) -> tuple:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def mix(a: str, b: str, t_: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex([ra[i] + (rb[i] - ra[i]) * t_ for i in range(3)])


def darken(color: str, amount: float) -> str:
    return mix(color, "#000000", amount)


def lighten(color: str, amount: float) -> str:
    return mix(color, "#ffffff", amount)


def luminance(color: str) -> float:
    r, g, b = _rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def safe_color(color, fallback: str) -> str:
    try:
        if isinstance(color, str) and color.startswith("#") and len(color) in (4, 7):
            _rgb(color)
            if color.lower() == KEY:
                return "#020304"
            return color.lower()
    except ValueError:
        pass
    return fallback


# =============================================================================
# Win32 helpers (all guarded - the pet still works without them)
# =============================================================================

_IS_WIN = sys.platform == "win32"
_u32 = None
_k32 = None

if _IS_WIN:
    try:
        from ctypes import wintypes as wt

        _u32 = ctypes.WinDLL("user32", use_last_error=True)
        _k32 = ctypes.WinDLL("kernel32")

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD),
            ]

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

        _u32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
        _u32.MonitorFromPoint.restype = ctypes.c_void_p
        _u32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
        _u32.MonitorFromWindow.restype = ctypes.c_void_p
        _u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
        _u32.GetMonitorInfoW.restype = wt.BOOL
        _u32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        _u32.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
        _u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wt.UINT]
        _u32.GetForegroundWindow.restype = wt.HWND
        _u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        _u32.GetClassNameW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
        _u32.GetParent.argtypes = [wt.HWND]
        _u32.GetParent.restype = wt.HWND
        _u32.GetSystemMetrics.argtypes = [ctypes.c_int]
        _k32.GetTickCount.restype = wt.DWORD
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Win32 helpers unavailable: %s", exc)
        _u32 = None


def monitor_rects(x: float, y: float, tkroot=None):
    """(monitor_rect, work_rect) as (left, top, right, bottom) for the monitor nearest (x, y)."""
    if _u32 is not None:
        try:
            handle = _u32.MonitorFromPoint(wt.POINT(int(x), int(y)), 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if handle and _u32.GetMonitorInfoW(handle, ctypes.byref(info)):
                m, w = info.rcMonitor, info.rcWork
                return (m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)
        except Exception:
            pass
    sw = tkroot.winfo_screenwidth() if tkroot else 1920
    sh = tkroot.winfo_screenheight() if tkroot else 1080
    return (0, 0, sw, sh), (0, 0, sw, sh - 40)


def virtual_screen(tkroot=None):
    """Bounding box (left, top, right, bottom) of all monitors."""
    if _u32 is not None:
        try:
            left = _u32.GetSystemMetrics(76)
            top = _u32.GetSystemMetrics(77)
            width = _u32.GetSystemMetrics(78)
            height = _u32.GetSystemMetrics(79)
            if width > 0 and height > 0:
                return left, top, left + width, top + height
        except Exception:
            pass
    sw = tkroot.winfo_screenwidth() if tkroot else 1920
    sh = tkroot.winfo_screenheight() if tkroot else 1080
    return 0, 0, sw, sh


def cursor_pos(tkroot):
    if _u32 is not None:
        try:
            pt = wt.POINT()
            if _u32.GetCursorPos(ctypes.byref(pt)):
                return pt.x, pt.y
        except Exception:
            pass
    return tkroot.winfo_pointerxy()


def user_idle_seconds() -> float:
    if _u32 is None:
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if _u32.GetLastInputInfo(ctypes.byref(info)):
            return ((_k32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        pass
    return 0.0


def hwnd_of(win):
    try:
        return int(win.wm_frame(), 16)
    except Exception:
        pass
    if _u32 is not None:
        try:
            return _u32.GetParent(win.winfo_id())
        except Exception:
            pass
    return None


def set_topmost(hwnd):
    if _u32 is None or not hwnd:
        return
    try:
        # HWND_TOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        _u32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    except Exception:
        pass


def point_on_a_monitor(x: float, y: float) -> bool:
    """False when (x, y) is on no monitor at all (e.g. a screen that was unplugged)."""
    if _u32 is None:
        return True
    try:
        return bool(_u32.MonitorFromPoint(wt.POINT(int(x), int(y)), 0))   # MONITOR_DEFAULTTONULL
    except Exception:
        return True


def foreground_window():
    if _u32 is None:
        return None
    try:
        return _u32.GetForegroundWindow()
    except Exception:
        return None


def foreground_is_fullscreen(own_hwnds=()) -> bool:
    """True when the foreground window covers its whole monitor (game, video, F11)."""
    if _u32 is None:
        return False
    try:
        hwnd = _u32.GetForegroundWindow()
        if not hwnd or hwnd in own_hwnds:
            return False
        buf = ctypes.create_unicode_buffer(64)
        _u32.GetClassNameW(hwnd, buf, 64)
        if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        rect = wt.RECT()
        if not _u32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        handle = _u32.MonitorFromWindow(hwnd, 2)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _u32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return (rect.left <= m.left and rect.top <= m.top
                and rect.right >= m.right and rect.bottom >= m.bottom)
    except Exception:
        return False


# =============================================================================
# Drawing
# =============================================================================

def ellipse_points(cx, cy, rx, ry, n=24, rot=0.0):
    pts = []
    cr, sr = math.cos(rot), math.sin(rot)
    for i in range(n):
        a = 2 * math.pi * i / n
        x, y = rx * math.cos(a), ry * math.sin(a)
        pts.extend([cx + x * cr - y * sr, cy + x * sr + y * cr])
    return pts


def rounded_rect_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def heart_points(cx, cy, s):
    pts = []
    for i in range(20):
        a = 2 * math.pi * i / 20
        x = 16 * math.sin(a) ** 3
        y = 13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a)
        pts.extend([cx + x * s / 16.0, cy - y * s / 16.0])
    return pts


def default_look(settings: dict) -> dict:
    species = settings.get("species", "cat")
    if species not in SPECIES:
        species = _EARS_TO_SPECIES.get(settings.get("ears", ""), "cat")
    return {
        "species": species,
        "color": settings.get("color", species_spec(species)["color"]),
        "cheek": settings.get("cheek_color", DEFAULT_PET_SETTINGS["cheek_color"]),
        "facing": 1,
        "squash": 0.0,
        "hop": 0.0,
        "hover": 0.0,
        "lean": 0.0,
        "eyes": "open",
        "mouth": "cat",
        "brows": None,
        "blush": False,
        "droop": 0.0,
        "twitch": 0.0,
        "eye_dx": 0.0,
        "eye_dy": 0.0,
        "foot_l": 0.0,
        "foot_r": 0.0,
        "wag": 0.0,
        "flap": 0.0,
        "headband": False,
        "tear": False,
        "sweat": False,
        "dangle": False,
        "gender": settings.get("gender") if settings.get("gender") in ("boy", "girl") else "",
        "stage": settings.get("stage") if settings.get("stage") in ("baby", "child", "teen") else "adult",
        "sick": False,
        "t": 0.0,
    }


def _band_points(cx, cy, rx, ry, y1, y2, skew, steps=6):
    """Polygon for a horizontal band of the body ellipse between y1 and y2."""
    left, right = [], []
    for i in range(steps + 1):
        y = y1 + (y2 - y1) * i / steps
        k = max(0.0, 1 - ((y - cy) / ry) ** 2)
        hw = rx * math.sqrt(k) * 0.97
        left.extend([skew(cx - hw, y), y])
        right.extend([skew(cx + hw, y), y])
    pts = left
    for i in range(len(right) - 2, -1, -2):
        pts.extend([right[i], right[i + 1]])
    return pts


def body_geometry(cx: float, floor: float, size: float, look: dict) -> dict:
    """Where the body will be drawn (same maths as draw_pet) - used to place props behind the pet."""
    spec = species_spec(look.get("species", "cat"))
    u = size / 64.0
    sq = look.get("squash", 0.0)
    bx_k, by_k = spec.get("body", (1.0, 1.0))
    base = floor - (look.get("hop", 0.0) + look.get("hover", 0.0)) * u
    rx = 24 * u * bx_k * (1 + sq * 0.8)
    ry = 19 * u * by_k * (1 - sq)
    if look.get("dangle"):
        base -= 3 * u
    bottom = base - 3 * u - spec.get("lift", 0) * u
    if spec.get("feet") is None:
        bottom = base - 1 * u - spec.get("lift", 0) * u
    cy = bottom - ry
    lean = look.get("lean", 0.0) * u
    return {"cx": cx, "cy": cy, "rx": rx, "ry": ry, "u": u, "facing": 1 if look.get("facing", 1) >= 0 else -1,
            "lean": lean, "feature_dx": lean * 0.62, "bottom": bottom,
            "eye_y": cy - 1.5 * u + spec.get("eye_dy", 0) * u, "eye_sep": spec.get("eye_sep", 11) * u}


def draw_pet(cv: tk.Canvas, cx: float, floor: float, size: float, look: dict):
    """Draw the pet with its feet resting on `floor` (canvas y), centred on `cx`. Returns its body geometry."""
    spec = species_spec(look.get("species", "cat"))
    extras = spec.get("extras", ())
    u = size / 64.0
    t_ = look["t"]
    body = look["color"]
    if look.get("sick"):
        body = mix(body, "#c4d6b0", 0.28)      # a little pale and green around the gills
    cheek = look["cheek"]
    gender = look.get("gender", "")
    outline = darken(body, 0.45)
    dark_body = luminance(body) < 0.32 and not spec.get("face_light")
    eye_col = spec.get("eye_col") or ("#fdfdfd" if dark_body else "#2a2430")
    shine = "#2a2430" if eye_col in ("#ffffff", "#fdfdfd") else "#ffffff"
    mouth_col = "#fdfdfd" if dark_body else "#5a2a34"
    lw = max(1.0, 1.6 * u)
    flap = look.get("flap", 0.0)

    sq = look["squash"]
    bx_k, by_k = spec.get("body", (1.0, 1.0))
    base = floor - (look["hop"] + look.get("hover", 0.0)) * u
    rx = 24 * u * bx_k * (1 + sq * 0.8)
    ry = 19 * u * by_k * (1 - sq)
    if look["dangle"]:
        base -= 3 * u
    bottom = base - 3 * u - spec.get("lift", 0) * u
    if spec.get("feet") is None:
        bottom = base - 1 * u - spec.get("lift", 0) * u
    cy = bottom - ry
    lean = look["lean"] * u
    facing = 1 if look["facing"] >= 0 else -1
    side = -facing
    wag = look["wag"]
    ears = spec.get("ears")
    tail = spec.get("tail")
    feet = spec.get("feet")

    def skew_x(x, y):
        return x + lean * (bottom - y) / (2 * ry)

    def ell(theta_deg, k=1.0):
        th = math.radians(theta_deg)
        x = cx + rx * k * math.cos(th)
        y = cy - ry * k * math.sin(th)
        return skew_x(x, y), y

    def blob(x, y, rx_, ry_, fill, rot=0.0, out=None, width=None, n=20):
        cv.create_polygon(*ellipse_points(x, y, rx_, ry_, n, rot), smooth=True, fill=fill,
                          outline=out or "", width=width or (lw if out else 0))

    feature_dx = lean * 0.62
    droop = max(0.0, min(1.0, look["droop"]))
    twitch = look["twitch"]
    inner = mix(cheek, body, 0.25)

    # ================= behind the body =================
    # --- wings (back) ---
    if "wings_bat" in extras or "wings_dragon" in extras:
        dragon = "wings_dragon" in extras
        wcol = darken(body, 0.15 if dragon else 0.28)
        span = (13 if dragon else 17) * u
        for s in (-1, 1):
            sx, sy = cx + s * rx * 0.55, cy - ry * 0.25
            up = flap * 6 * u
            tip = (cx + s * (rx + span), cy - ry * 0.75 - up)
            pts = [sx, sy, tip[0], tip[1]]
            if dragon:
                pts += [cx + s * (rx + span * 0.75), cy + 1 * u - up * 0.4,
                        cx + s * (rx + span * 0.35), cy - 1 * u - up * 0.2,
                        cx + s * rx * 0.7, cy + ry * 0.15]
            else:
                pts += [cx + s * (rx + span * 0.8), cy + 3 * u - up * 0.5,
                        cx + s * (rx + span * 0.55), cy - 0.5 * u - up * 0.3,
                        cx + s * (rx + span * 0.3), cy + 4 * u - up * 0.2,
                        cx + s * rx * 0.7, cy + ry * 0.2]
            cv.create_polygon(*pts, fill=wcol, outline=outline, width=lw, joinstyle=tk.ROUND)
    if "wings_bug" in extras:
        k = 0.45 + 0.55 * abs(flap)
        for s in (-1, 1):
            blob(cx + s * rx * 0.62, cy - ry * 0.95, 6.5 * u * k + 1.5 * u, 9 * u, "#eaf6ff",
                 rot=s * 0.55, out="#8fbfe0", width=max(1, u))
            blob(cx + s * rx * 0.85, cy - ry * 0.45, 4.5 * u * k + 1 * u, 6 * u, "#eaf6ff",
                 rot=s * 1.0, out="#8fbfe0", width=max(1, u))
    if "wings_butterfly" in extras:
        k = 0.4 + 0.6 * (0.5 + 0.5 * flap)
        wing = mix(cheek, body, 0.35)
        wing2 = lighten(wing, 0.35)
        for s in (-1, 1):
            ux = cx + s * (rx * 0.55 + 9 * u * k)
            blob(ux, cy - ry * 0.45, 11 * u * k + 1.5 * u, 13 * u, wing, rot=s * 0.35, out=darken(wing, 0.35))
            blob(ux, cy - ry * 0.55, 5 * u * k + 1 * u, 5 * u, wing2, rot=s * 0.35)
            lx = cx + s * (rx * 0.45 + 6 * u * k)
            blob(lx, cy + ry * 0.45, 7.5 * u * k + 1.2 * u, 8.5 * u, wing, rot=-s * 0.4, out=darken(wing, 0.35))
            r = 1.6 * u
            cv.create_oval(ux - r, cy - ry * 0.9 - r, ux + r, cy - ry * 0.9 + r, fill="#ffffff", outline="")

    # --- antennae (behind head) ---
    if ears == "antennae":
        for s in (-1, 1):
            x0, y0 = ell(90 - s * 22, 0.92)
            tipx = x0 + s * 6 * u + math.sin(t_ * 3 + s) * u
            tipy = y0 - 11 * u
            cv.create_line(x0, y0, x0 + s * 2 * u, y0 - 6 * u, tipx, tipy, smooth=True,
                           width=max(1, 1.4 * u), fill=darken(body, 0.6))
            r = 2.1 * u
            cv.create_oval(tipx - r, tipy - r, tipx + r, tipy + r, fill=darken(body, 0.55), outline="")

    # --- gills (axolotl) ---
    if ears == "gills":
        gill = mix(cheek, "#ff4f8b", 0.35)
        for s in (-1, 1):
            for k in range(3):
                x0 = cx + s * rx * 0.78
                y0 = cy - ry * 0.45 + k * 4.2 * u
                x1 = cx + s * (rx + 7.5 * u) + math.sin(t_ * 3 + k) * 0.8 * u
                y1 = cy - ry * 0.75 + k * 5.5 * u
                cv.create_line(x0, y0, x1, y1, width=2.6 * u, fill=gill, capstyle=tk.ROUND)
                r = 1.7 * u
                cv.create_oval(x1 - r, y1 - r, x1 + r, y1 + r, fill=gill, outline="")

    # --- tail ---
    if tail == "cat":
        sx, sy = cx + side * rx * 0.82, bottom - 5 * u
        pts = [sx, sy, cx + side * (rx + 7 * u), bottom - 5 * u,
               cx + side * (rx + 10 * u) + wag * u, bottom - 15 * u,
               cx + side * (rx + 5 * u) + wag * 1.6 * u, bottom - 21 * u]
        cv.create_line(*pts, smooth=True, width=5.4 * u, fill=outline, capstyle=tk.ROUND)
        cv.create_line(*pts, smooth=True, width=3.0 * u, fill=body, capstyle=tk.ROUND)
    elif tail == "fox":
        tx = cx + side * (rx + 5 * u) + wag * 0.6 * u
        ty = bottom - 10 * u
        rot = side * -0.95 + wag * 0.04
        blob(tx, ty, 11 * u, 6.2 * u, body, rot=rot, out=outline)
        ex = tx + math.cos(rot) * 8 * u * side * side
        ey = ty + math.sin(rot) * 8 * u
        blob(tx + (ex - tx) * 1.0, ty + (ey - ty) * 1.0, 4 * u, 3.6 * u, "#fff8f0", rot=rot)
    elif tail == "dog":
        pts = [cx + side * rx * 0.85, bottom - 7 * u, cx + side * (rx + 5 * u) + wag * u, bottom - 15 * u]
        cv.create_line(*pts, width=5.2 * u, fill=outline, capstyle=tk.ROUND)
        cv.create_line(*pts, width=3.2 * u, fill=body, capstyle=tk.ROUND)
    elif tail == "puff":
        r = 4.6 * u
        x, y = cx + side * (rx - 1 * u), bottom - 6 * u
        cv.create_oval(x - r, y - r, x + r, y + r, fill=lighten(body, 0.55), outline=outline, width=lw)
    elif tail in ("stub", "stub_dark"):
        r = 3.6 * u
        x, y = cx + side * (rx - 0.5 * u), bottom - 6 * u
        cv.create_oval(x - r, y - r, x + r, y + r, fill=body if tail == "stub" else "#2b2b2b",
                       outline=outline, width=lw)
    elif tail == "curly":
        ccx, ccy = cx + side * (rx + 3 * u), bottom - 10 * u
        pts = []
        for k in range(16):
            a = k * 0.55 + wag * 0.1
            r = 3.4 * u * (1 - k / 22)
            pts.extend([ccx + side * math.cos(a) * r, ccy + math.sin(a) * r])
        cv.create_line(cx + side * rx * 0.9, bottom - 9 * u, *pts, smooth=True, width=max(1, 1.7 * u),
                       fill=darken(body, 0.28), capstyle=tk.ROUND)
    elif tail == "fish":
        w = wag * 0.7 * u
        x0 = cx + side * rx * 0.85
        pts = [x0, cy - 3 * u, cx + side * (rx + 11 * u), cy - 9 * u + w,
               cx + side * (rx + 7 * u), cy + w * 0.5, cx + side * (rx + 11 * u), cy + 9 * u + w,
               x0, cy + 3 * u]
        cv.create_polygon(*pts, smooth=True, fill=darken(body, 0.08), outline=outline, width=lw)
    elif tail == "whale":
        w = wag * 0.6 * u
        x0 = cx + side * rx * 0.8
        pts = [x0, cy - 2 * u, cx + side * (rx + 6 * u), cy - 5 * u + w,
               cx + side * (rx + 13 * u), cy - 13 * u + w, cx + side * (rx + 10 * u), cy - 6 * u + w,
               cx + side * (rx + 15 * u), cy - 4 * u + w, cx + side * (rx + 6 * u), cy + w * 0.3,
               x0, cy + 5 * u]
        cv.create_polygon(*pts, smooth=True, fill=body, outline=outline, width=lw)
    elif tail == "axolotl":
        w = wag * 0.8 * u
        pts = [cx + side * rx * 0.8, bottom - 12 * u, cx + side * (rx + 13 * u), bottom - 7 * u + w,
               cx + side * rx * 0.8, bottom - 2 * u]
        cv.create_polygon(*pts, smooth=True, fill=lighten(body, 0.2), outline=outline, width=lw)
    elif tail == "dragon":
        w = wag * 0.8 * u
        tipx, tipy = cx + side * (rx + 12 * u), bottom - 12 * u + w
        pts = [cx + side * rx * 0.8, bottom - 4 * u, cx + side * (rx + 8 * u), bottom - 3 * u + w * 0.5,
               tipx, tipy]
        cv.create_line(*pts, smooth=True, width=5 * u, fill=outline, capstyle=tk.ROUND)
        cv.create_line(*pts, smooth=True, width=3 * u, fill=body, capstyle=tk.ROUND)
        cv.create_polygon(tipx - 3 * u, tipy, tipx + side * 2 * u, tipy - 5 * u, tipx + 3 * u, tipy,
                          tipx + side * 1 * u, tipy + 3 * u, fill=darken(body, 0.2), outline=outline,
                          width=max(1, u))
    elif tail == "bird":
        for k, a in enumerate((-0.5, 0.0, 0.5)):
            blob(cx + side * (rx + 2 * u), bottom - 10 * u + k * 2.5 * u, 5.5 * u, 2.2 * u,
                 darken(body, 0.12), rot=side * (-0.35 + a * 0.5), out=outline, width=max(1, u))
    elif tail == "turtle":
        x0 = cx + side * rx * 0.9
        cv.create_polygon(x0, bottom - 7 * u, cx + side * (rx + 6 * u), bottom - 4 * u + wag * 0.3 * u,
                          x0, bottom - 2 * u, fill=body, outline=outline, width=lw)

    # --- ears behind the body ---
    for s in (-1, 1):
        tw = twitch if s == 1 else 0.0
        if ears in ("cat", "fox", "bat", "pig", "owl", "horns"):
            length = {"cat": 11, "fox": 14, "bat": 16, "pig": 7, "owl": 7, "horns": 7}[ears]
            half = {"cat": 17, "fox": 16, "bat": 21, "pig": 15, "owl": 10, "horns": 8}[ears]
            centre = {"cat": 36, "fox": 34, "bat": 38, "pig": 40, "owl": 52, "horns": 30}[ears]
            theta_c = 90 - s * (centre + 30 * droop) + math.degrees(tw) * 0.6
            b1 = ell(theta_c - half, 0.96)
            b2 = ell(theta_c + half, 0.96)
            bend = {"pig": -30, "owl": 20}.get(ears, 8)
            theta_t = math.radians(theta_c + s * (bend - 25 * droop))
            ex, ey = ell(theta_c, 1.0)
            tip = (ex + math.cos(theta_t) * length * u, ey - math.sin(theta_t) * length * u)
            fill = {"horns": "#fff1b8", "owl": darken(body, 0.2)}.get(ears, body)
            cv.create_polygon(*b1, *tip, *b2, fill=fill, outline=outline, width=lw, joinstyle=tk.ROUND)
            if ears in ("cat", "fox", "bat", "pig"):
                gx = (b1[0] + b2[0] + tip[0]) / 3
                gy = (b1[1] + b2[1] + tip[1]) / 3
                inner_pts = []
                for px, py in (b1, tip, b2):
                    inner_pts.extend([gx + (px - gx) * 0.5, gy + (py - gy) * 0.5 - 1.0 * u])
                cv.create_polygon(*inner_pts, fill="#fff8f0" if ears == "fox" else inner, outline="")
        elif ears == "bunny":
            theta_c = 90 - s * (20 + 45 * droop) + math.degrees(tw)
            th = math.radians(theta_c)
            bx, by = ell(theta_c, 0.9)
            ecx = bx + math.cos(th) * 12 * u
            ecy = by - math.sin(th) * 12 * u
            rot = math.atan2(-math.sin(th), math.cos(th))
            blob(ecx, ecy, 13 * u, 5.4 * u, body, rot=rot, out=outline, n=22)
            blob(ecx + math.cos(th) * u, ecy - math.sin(th) * u, 8.5 * u, 2.6 * u, inner, rot=rot, n=18)
        elif ears in ("bear", "panda", "hamster"):
            theta_c = 90 - s * ({"hamster": 50}.get(ears, 44) + 12 * droop)
            ex, ey = ell(theta_c, 0.93)
            r = (5 if ears == "hamster" else 7) * u
            fill = "#2b2b2b" if ears == "panda" else body
            cv.create_oval(ex - r, ey - r, ex + r, ey + r, fill=fill, outline=outline, width=lw)
            if ears != "panda":
                r2 = r * 0.55
                cv.create_oval(ex - r2, ey - r2 + 0.5 * u, ex + r2, ey + r2 + 0.5 * u, fill=inner, outline="")
        elif ears == "frog":
            ex, ey = ell(90 - s * 25, 0.92)
            ex = cx + s * spec.get("eye_sep", 11) * u + lean * 0.8
            r = 7.0 * u
            cv.create_oval(ex - r, ey - r, ex + r, ey + r, fill=body, outline=outline, width=lw)

    # --- v2.12 parts behind the body ---
    if "mane" in extras or "mane_pastel" in extras:
        pastel = "mane_pastel" in extras
        palette_mane = ("#ffadad", "#ffd6a5", "#caffbf", "#9bf6ff", "#bdb2ff", "#ffc6ff")
        n_tufts = 13
        for k in range(n_tufts):
            a = math.radians(-20 + 220 * k / (n_tufts - 1))
            mx_ = cx + math.cos(a) * rx * 1.02
            my_ = cy - math.sin(a) * ry * 1.05
            col = palette_mane[k % len(palette_mane)] if pastel else darken(body, 0.28)
            blob(mx_, my_, 6.2 * u, 5.4 * u, col, out=darken(col, 0.25), width=max(1, 0.8 * u), n=14)
    if "antlers" in extras:
        ant = "#8a5a2b"
        for s in (-1, 1):
            x0, y0 = ell(90 - s * 28, 0.95)
            x1, y1 = x0 + s * 5 * u, y0 - 12 * u
            cv.create_line(x0, y0, x1, y1, width=max(2, 2.4 * u), fill=ant, capstyle=tk.ROUND)
            cv.create_line(x0 + s * 2 * u, y0 - 6 * u, x0 + s * 8 * u, y0 - 9 * u, width=max(2, 2 * u),
                           fill=ant, capstyle=tk.ROUND)
            cv.create_line(x1, y1, x1 - s * 3 * u, y1 - 5 * u, width=max(2, 2 * u), fill=ant, capstyle=tk.ROUND)
            cv.create_line(x1, y1, x1 + s * 4 * u, y1 - 4 * u, width=max(2, 2 * u), fill=ant, capstyle=tk.ROUND)
    if "quills" in extras:
        quill = darken(body, 0.35)
        for k in range(11):
            a = math.radians(5 + 170 * k / 10)
            bx_, by_ = cx + math.cos(a) * rx * 0.92, cy - math.sin(a) * ry * 0.92
            tx_, ty_ = cx + math.cos(a) * (rx + 7 * u), cy - math.sin(a) * (ry + 7 * u)
            px_, py_ = -math.sin(a) * 3 * u, -math.cos(a) * 3 * u
            cv.create_polygon(bx_ - px_, by_ - py_, tx_, ty_, bx_ + px_, by_ + py_, fill=quill,
                              outline=darken(quill, 0.3), width=max(1, 0.6 * u))
    if "back_spikes" in extras:
        plate = mix(body, "#f4a259", 0.55)
        for k in range(5):
            a = math.radians(55 + 70 * k / 4 + (-side) * 0)
            bx_, by_ = cx + math.cos(a) * rx * 0.95, cy - math.sin(a) * ry * 0.95
            tx_, ty_ = cx + math.cos(a) * (rx + 6 * u), cy - math.sin(a) * (ry + 6 * u)
            px_, py_ = -math.sin(a) * 3.2 * u, -math.cos(a) * 3.2 * u
            cv.create_polygon(bx_ - px_, by_ - py_, tx_, ty_, bx_ + px_, by_ + py_, fill=plate,
                              outline=outline, width=max(1, 0.8 * u))
    if tail == "ringed":
        tx = cx + side * (rx + 5 * u) + wag * 0.6 * u
        ty = bottom - 11 * u
        rot = side * -0.95 + wag * 0.04
        blob(tx, ty, 11 * u, 5.6 * u, body, rot=rot, out=outline)
        for k in (-0.45, 0.0, 0.45):
            ox = tx + math.cos(rot) * 11 * u * k * side * side
            oy = ty + math.sin(rot) * 11 * u * k
            blob(ox, oy, 1.8 * u, 5.0 * u, darken(body, 0.5), rot=rot)
    elif tail == "squirrel":
        tx = cx + side * (rx + 2 * u) + wag * 0.4 * u
        blob(tx, cy - ry * 0.35, 8.5 * u, 15 * u, darken(body, 0.05), rot=side * -0.25 + wag * 0.03, out=outline)
        blob(tx + side * 4 * u, cy - ry * 1.05, 6 * u, 5 * u, lighten(body, 0.15), rot=side * 0.6, out=outline)
    elif tail == "flame":
        flick = math.sin(t_ * 9)
        for k, col in enumerate(("#ff3d00", "#ff9100", "#ffd600")):
            length = (16 - k * 4) * u
            ang = side * (0.35 + k * 0.25) + flick * 0.08
            fx_ = cx + side * rx * 0.75 + math.cos(ang) * 0 + side * length * 0.6
            fy_ = bottom - 8 * u - k * 3 * u
            blob(fx_, fy_, length * 0.75, (4.5 - k) * u, col, rot=side * (0.25 + 0.15 * k) + flick * 0.1)
    for s in (-1, 1):
        if ears == "mouse":
            ex, ey = ell(90 - s * 42, 0.95)
            r = 9.5 * u
            cv.create_oval(ex - r, ey - r, ex + r, ey + r, fill=body, outline=outline, width=lw)
            r2 = r * 0.62
            cv.create_oval(ex - r2, ey - r2, ex + r2, ey + r2, fill=mix(cheek, "#ffffff", 0.3), outline="")
        elif ears == "koala":
            ex, ey = ell(90 - s * 55, 0.95)
            r = 9 * u
            cv.create_oval(ex - r, ey - r, ex + r, ey + r, fill=body, outline=outline, width=lw)
            r2 = r * 0.6
            cv.create_oval(ex - r2, ey - r2, ex + r2, ey + r2, fill="#f1f1f1", outline="")
        elif ears == "monkey":
            ex, ey = cx + s * (rx + 1 * u), cy - ry * 0.15
            r = 5.8 * u
            cv.create_oval(ex - r, ey - r, ex + r, ey + r, fill=body, outline=outline, width=lw)
            r2 = r * 0.58
            cv.create_oval(ex - r2, ey - r2, ex + r2, ey + r2, fill=lighten(body, 0.45), outline="")
        elif ears == "deer":
            ex, ey = ell(90 - s * 50, 0.95)
            blob(ex + s * 4 * u, ey - 1 * u, 7.5 * u, 3.6 * u, body, rot=s * -0.45 - s * droop * 0.4, out=outline)
            blob(ex + s * 4 * u, ey - 1 * u, 4.6 * u, 1.8 * u, inner, rot=s * -0.45 - s * droop * 0.4)

    # dorsal fin (fish) sits behind the body
    if "dorsal" in extras:
        top_x, top_y = ell(95, 0.98)
        cv.create_polygon(top_x - 7 * u, top_y + 2 * u, top_x + side * 2 * u, top_y - 8 * u,
                          top_x + 7 * u, top_y + 2 * u, smooth=True, fill=darken(body, 0.08),
                          outline=outline, width=lw)

    # ================= body =================
    if spec.get("shape") == "dome":
        dcy = cy + ry * 0.35
        pts = []
        for i in range(15):
            a = math.pi * i / 14
            x = cx + rx * math.cos(a)
            y = dcy - ry * 1.25 * math.sin(a)
            pts.extend([skew_x(x, y), y])
        for i in range(9):
            x = cx - rx + 2 * rx * i / 8
            y = dcy + (2.2 * u if i % 2 else 0)
            pts.extend([skew_x(x, y), y])
        cv.create_polygon(*pts, smooth=True, fill=body, outline=outline, width=lw)
    else:
        pts = ellipse_points(cx, cy, rx, ry, 28)
        for i in range(0, len(pts), 2):
            pts[i] = skew_x(pts[i], pts[i + 1])
        cv.create_polygon(*pts, smooth=True, fill=body, outline=outline, width=lw)

    # --- body patterns ---
    if "penguin_belly" in extras:
        blob(cx + lean * 0.4, cy + ry * 0.16, rx * 0.74, ry * 0.8, "#fbfbfb", n=24)
    elif "whale_belly" in extras:
        blob(cx + lean * 0.2, bottom - ry * 0.32, rx * 0.78, ry * 0.36, lighten(body, 0.6), n=24)
    elif "owl_face" in extras:
        blob(cx + lean * 0.3, bottom - ry * 0.32, rx * 0.55, ry * 0.32, lighten(body, 0.45))
    elif spec.get("shape") != "dome" and "shell" not in extras:
        bcy = bottom - ry * 0.36
        cv.create_oval(cx + lean * 0.2 - rx * 0.5, bcy - ry * 0.3, cx + lean * 0.2 + rx * 0.5, bcy + ry * 0.3,
                       fill=lighten(body, 0.38), outline="")
    if "belly_scales" in extras:
        bcy = bottom - ry * 0.36
        for k in range(3):
            y = bcy - ry * 0.18 + k * ry * 0.17
            cv.create_line(cx - rx * 0.32, y, cx, y + 1.2 * u, cx + rx * 0.32, y, smooth=True,
                           width=max(1, u), fill=darken(lighten(body, 0.38), 0.18))
    if "shell" in extras:
        shell = mix(body, "#7f5539", 0.62)
        line_y = cy - 0.5 * u
        pts = []
        for i in range(17):
            a = math.radians(8 + 164 * i / 16)
            x = cx + rx * 1.02 * math.cos(a)
            y = cy - ry * 1.03 * math.sin(a)
            pts.extend([skew_x(x, y), min(y, line_y)])
        cv.create_polygon(*pts, smooth=True, fill=shell, outline=darken(shell, 0.35), width=lw)
        hx, hy = skew_x(cx, cy - ry * 0.55), cy - ry * 0.55
        hex_r = 5 * u
        hex_pts = []
        for k in range(6):
            a = math.pi / 6 + k * math.pi / 3
            hex_pts.extend([hx + math.cos(a) * hex_r, hy + math.sin(a) * hex_r * 0.8])
        cv.create_polygon(*hex_pts, fill=lighten(shell, 0.15), outline=darken(shell, 0.35), width=max(1, u))
        for s in (-1, 1):
            cv.create_line(hx + s * hex_r * 0.87, hy, hx + s * rx * 0.8, hy + 2 * u, width=max(1, u),
                           fill=darken(shell, 0.35))
    if "stripes" in extras:
        for y1, y2 in ((0.18, 0.40), (0.58, 0.80)):
            cv.create_polygon(*_band_points(cx, cy, rx, ry, cy + ry * y1, cy + ry * y2, skew_x), smooth=True,
                              fill="#3b2f2f", outline="")
    if "spots" in extras:
        for dx, dy, r in ((-0.55, -0.55, 2.4), (0.6, -0.45, 1.8), (0.45, -0.75, 1.3)):
            x, y = skew_x(cx + rx * dx, cy + ry * dy), cy + ry * dy
            cv.create_oval(x - r * u, y - r * u, x + r * u, y + r * u, fill=lighten(body, 0.35), outline="")
    if "scales" in extras:
        for k in range(3):
            x = cx + side * rx * (0.25 + k * 0.18)
            y = cy + (k % 2) * 3 * u
            cv.create_arc(x - 3 * u, y - 3 * u, x + 3 * u, y + 3 * u, start=90 + side * 90 - 60, extent=120,
                          style=tk.ARC, outline=darken(body, 0.22), width=max(1, u))
    if "wool" in extras:
        wool = "#ffffff" if luminance(body) > 0.85 else lighten(body, 0.45)
        for k in range(12):
            a = math.radians(-10 + 200 * k / 11)
            wx_, wy_ = cx + math.cos(a) * rx * 0.88, cy - math.sin(a) * ry * 0.88
            cv.create_oval(wx_ - 5.2 * u, wy_ - 5.2 * u, wx_ + 5.2 * u, wy_ + 5.2 * u, fill=wool,
                           outline=darken(wool, 0.12), width=max(1, 0.7 * u))
        blob(cx + lean * 0.3, cy + ry * 0.05, rx * 0.62, ry * 0.58, mix(body, "#f3d9c7", 0.5), n=20)
    if "cow_spots" in extras:
        spot = "#2b2b2b" if luminance(body) > 0.5 else "#111111"
        for dx, dy, r_x, r_y in ((-0.55, -0.45, 5.2, 4.2), (0.62, -0.2, 4.2, 3.6), (0.15, -0.82, 3.0, 2.4),
                                 (-0.2, 0.45, 3.4, 2.6), (0.5, 0.5, 2.6, 2.2)):
            x_, y_ = skew_x(cx + rx * dx, cy + ry * dy), cy + ry * dy
            blob(x_, y_, r_x * u, r_y * u, spot, rot=dx, n=14)
    if "tiger_stripes" in extras:
        stripe = "#3b2a20"
        for s in (-1, 1):
            for k in range(3):
                y_ = cy - ry * 0.35 + k * ry * 0.33
                x_ = cx + s * rx * 0.98
                cv.create_line(x_, y_, x_ - s * 7 * u, y_ + 1.5 * u, x_ - s * 9.5 * u, y_ + 0.5 * u, smooth=True,
                               width=max(2, 2.4 * u), fill=stripe, capstyle=tk.ROUND)
        top_x, top_y = ell(90, 0.95)
        for dx in (-3, 0, 3):
            cv.create_line(top_x + dx * u, top_y + 1 * u, top_x + dx * 0.6 * u, top_y + 5 * u,
                           width=max(2, 2 * u), fill=stripe, capstyle=tk.ROUND)
    if "horn" in extras:
        hx_, hy_ = ell(90, 0.98)
        hx_ += feature_dx * 0.4
        cv.create_polygon(hx_ - 3.2 * u, hy_ + 1 * u, hx_ + 1 * u, hy_ - 15 * u, hx_ + 3.2 * u, hy_ + 1 * u,
                          fill="#ffd43b", outline="#c99a00", width=max(1, u), joinstyle=tk.ROUND)
        for k in range(3):
            yy = hy_ - 2 * u - k * 4 * u
            half = 2.6 * u * (1 - k * 0.28)
            cv.create_line(hx_ - half, yy + 1 * u, hx_ + half, yy - 1 * u, fill="#c99a00", width=max(1, 0.8 * u))
    if ears == "sprout":
        top_x, top_y = ell(90, 1.0)
        tip_x = top_x + (3 + math.degrees(twitch) * 0.08) * u - droop * 4 * u
        tip_y = top_y - (9 - droop * 4) * u
        cv.create_line(top_x, top_y + 1 * u, top_x + 1.5 * u, top_y - 4 * u, tip_x, tip_y,
                       smooth=True, width=max(1, 1.8 * u), fill="#4f8f45")
        blob(tip_x + 3.5 * u, tip_y - 0.5 * u, 4.5 * u, 2.4 * u, "#8fd27f", rot=-0.5 - droop * 0.8,
             out="#4f8f45", width=max(1, u), n=16)
    if ears == "dog":
        ear_col = darken(body, 0.22)
        for s in (-1, 1):
            tw = twitch if s == 1 else 0.0
            ex = skew_x(cx + s * rx * 0.8, cy - ry * 0.45)
            blob(ex, cy - ry * 0.22, 4.8 * u, 9.6 * u, ear_col, rot=s * (0.38 + droop * 0.4) + tw, out=outline)
    if ears == "tuft":
        top_x, top_y = ell(92, 1.0)
        cv.create_line(top_x, top_y + 1 * u, top_x - 2 * u, top_y - 5 * u, top_x + 2.5 * u, top_y - 7 * u,
                       top_x + 1.5 * u, top_y - 3.5 * u, smooth=True, width=max(1, 1.7 * u), fill=outline)
    if gender == "boy" and not look.get("head_prop"):
        # boys: a little three-strand hair tuft (cowlick) on top of the head (v2.18)
        top_x, top_y = ell(66 if ears in ("tuft", "sprout") else 90, 1.0)
        tw = max(1.3, 1.9 * u)
        for dx1, dx2, dy2, dx3, dy3 in ((-0.7, -2.8, -4.2, -5.6, -4.9), (0.0, 0.5, -5.6, 3.0, -7.6),
                                        (0.7, 3.4, -3.0, 6.2, -3.2)):
            cv.create_line(top_x + dx1 * u, top_y + 1.4 * u, top_x + dx2 * u, top_y + dy2 * u,
                           top_x + dx3 * u, top_y + dy3 * u, smooth=True, width=tw, fill=outline,
                           capstyle=tk.ROUND)
    if gender == "girl" and not look.get("head_prop"):
        # girls: a little pink bow on the head, over one ear like a certain famous cat (v2.18)
        bx, by = ell(68 if facing >= 0 else 112, 0.98)
        bow, bow_dark = "#ff5fa2", "#c93b7b"
        for s in (-1, 1):
            blob(bx + s * 3.1 * u, by - 0.4 * u, 3.3 * u, 2.4 * u, bow, rot=s * 0.35, out=bow_dark,
                 width=max(1, 0.9 * u), n=14)
        blob(bx, by - 0.2 * u, 1.5 * u, 1.5 * u, lighten(bow, 0.25), out=bow_dark, width=max(1, 0.9 * u), n=10)

    # ================= feet / lower body =================
    foot_col = darken(body, 0.1)
    if feet == "paws_dark":
        foot_col = "#2b2b2b" if luminance(body) > 0.8 else darken(body, 0.45)
    if feet in ("paws", "paws_dark", "webbed", "bird"):
        for s, lift in ((-1, look["foot_l"]), (1, look["foot_r"])):
            fx = cx + s * 9.5 * u
            fy2 = base + 2.5 * u if look["dangle"] else base - lift * u
            if look["dangle"]:
                fx = cx + s * 8 * u
            if feet == "bird":
                col = "#ff9f43"
                for k in (-1, 0, 1):
                    cv.create_line(fx, fy2 - 3.5 * u, fx + k * 2.4 * u, fy2, width=max(1, 1.5 * u), fill=col,
                                   capstyle=tk.ROUND)
            elif feet == "webbed":
                col = "#ff9f43" if (spec["cat"] == "air" or "beak" in extras) else darken(body, 0.18)
                blob(fx, fy2 - 2 * u, 5.6 * u, 2.4 * u, col, out=outline, width=max(1, u))
            else:
                cv.create_oval(fx - 4.8 * u, fy2 - 4.4 * u, fx + 4.8 * u, fy2, fill=foot_col, outline=outline,
                               width=lw)
    elif feet == "tentacles":
        for i in range(5):
            x0 = cx + (i - 2) * 6.2 * u
            wav = math.sin(t_ * 4 + i * 1.3) * 2 * u
            y_end = base + (2.5 * u if look["dangle"] else 0)
            pts = [x0, bottom - 4 * u, x0 + wav * 0.5, bottom + 3 * u, x0 + wav + (i - 2) * 1.2 * u, y_end - 1 * u]
            cv.create_line(*pts, smooth=True, width=5.4 * u, fill=outline, capstyle=tk.ROUND)
            cv.create_line(*pts, smooth=True, width=3.4 * u, fill=body, capstyle=tk.ROUND)
    elif feet == "jelly":
        dcy = cy + ry * 0.35
        for i in range(5):
            x0 = cx + (i - 2) * 5.5 * u
            pts = []
            for k in range(6):
                y = dcy + 2 * u + k * (base - dcy - 3 * u) / 5
                pts.extend([x0 + math.sin(t_ * 3 + i + k * 0.9) * 1.6 * u, y])
            cv.create_line(*pts, smooth=True, width=max(1, 1.6 * u), fill=darken(body, 0.15))

    elif feet == "crab":
        leg = darken(body, 0.2)
        for s in (-1, 1):
            for k in range(3):
                x0 = cx + s * rx * (0.45 + k * 0.17)
                y0 = bottom - 3 * u
                kick = math.sin(t_ * 6 + k + s) * 1.2 * u
                cv.create_line(x0, y0, x0 + s * 4 * u, y0 + 3 * u + kick, x0 + s * 6 * u, base,
                               width=max(1, 1.6 * u), fill=leg, capstyle=tk.ROUND)
    elif feet == "stilts":
        leg = mix(body, "#e5383b", 0.35)
        for s, lift_ in ((-1, look["foot_l"]), (1, look["foot_r"])):
            fx = cx + s * 5 * u
            fy2 = base - lift_ * u
            cv.create_line(fx, bottom - 1 * u, fx + s * 0.5 * u, fy2 - 1 * u, width=max(1, 1.5 * u), fill=leg)
            cv.create_line(fx + s * 0.5 * u, fy2 - 1 * u, fx + s * 4 * u, fy2, width=max(1, 1.5 * u), fill=leg,
                           capstyle=tk.ROUND)

    # ================= front decorations =================
    if "fins" in extras:
        for s in (-1, 1):
            blob(cx + s * rx * 0.55, cy + ry * 0.35, 4.5 * u, 2.6 * u, darken(body, 0.1),
                 rot=s * (0.5 + 0.35 * flap), out=outline, width=max(1, u))
    if "claws" in extras:
        snap = abs(math.sin(t_ * 3)) * 0.35
        for s in (-1, 1):
            ax, ay = cx + s * (rx + 6 * u), cy - ry * 0.55
            cv.create_line(cx + s * rx * 0.85, cy, ax, ay + 4 * u, width=max(2, 2.4 * u), fill=darken(body, 0.15),
                           capstyle=tk.ROUND)
            blob(ax, ay, 6 * u, 4.6 * u, body, rot=s * 0.5, out=outline)
            cv.create_polygon(ax + s * 1 * u, ay - 1 * u, ax + s * 7 * u, ay - (4 + snap * 6) * u,
                              ax + s * 6 * u, ay + 1 * u, fill=lighten(body, 0.2), outline=outline, width=max(1, u))
    if "flippers" in extras or "wings_bird" in extras:
        wcol = darken(body, 0.12)
        for s in (-1, 1):
            rot = s * (0.9 + 0.55 * flap) if "wings_bird" in extras else s * (1.25 + 0.25 * flap)
            blob(cx + s * rx * 0.93, cy + ry * 0.12, 8 * u, 4.2 * u, wcol, rot=rot, out=outline)

    if look["headband"]:
        hy = cy - ry * 0.6
        hw = rx * 0.8
        hcx = cx + lean * 0.8
        cv.create_line(hcx - hw, hy + 1.2 * u, hcx, hy - 1.6 * u, hcx + hw, hy + 1.2 * u,
                       smooth=True, width=3.2 * u, fill="#d64545", capstyle=tk.ROUND)
        kx = hcx - facing * hw
        cv.create_line(kx, hy + 1 * u, kx - facing * 5 * u, hy + 4 * u, width=2.2 * u, fill="#d64545",
                       capstyle=tk.ROUND)
        cv.create_line(kx, hy + 1 * u, kx - facing * 6 * u, hy - 0.5 * u, width=2.2 * u, fill="#d64545",
                       capstyle=tk.ROUND)

    # ================= face =================
    edx, edy = look["eye_dx"] * u, look["eye_dy"] * u
    ey = cy - 1.5 * u + edy + spec.get("eye_dy", 0) * u
    sep = spec.get("eye_sep", 8.5) * u
    r = 3.3 * u * spec.get("eye_scale", 1.0) * (tama.stage_eyes(look.get("stage")) if tama else 1.0)
    fcx = cx + feature_dx + edx
    mx = cx + feature_dx + edx * 0.6
    my = cy - 1.5 * u + edy + (5.2 + spec.get("mouth_dy", 0)) * u
    if spec.get("eye_dy", 0) > 0:
        my += spec["eye_dy"] * u

    if "owl_face" in extras:
        for s in (-1, 1):
            blob(fcx + s * sep, ey, r * 1.9, r * 1.9, lighten(body, 0.55))
    if "bandit_mask" in extras:
        blob(fcx, ey + 0.5 * u, sep + 6.5 * u, 4.6 * u, "#3a3a3a", n=22)
    if "monkey_face" in extras:
        face_col = mix(body, "#ffe0c2", 0.75)
        for s in (-1, 1):
            blob(fcx + s * sep * 0.85, ey, 6.2 * u, 6.0 * u, face_col)
        blob(mx, my - 0.5 * u, 8.5 * u, 5.4 * u, face_col)
    if "big_nose" in extras:
        blob(mx, my - 2.6 * u, 3.8 * u, 3.0 * u, "#3b3b3b", n=16)
    if "fox_mask" in extras:
        for s in (-1, 1):
            blob(fcx + s * 6.5 * u, ey + 4.6 * u, 6.8 * u, 4.6 * u, "#fff7ee")
    if "eye_patches" in extras:
        for s in (-1, 1):
            blob(fcx + s * sep, ey + 0.6 * u, 4.4 * u, 5.4 * u, "#2b2b2b", rot=s * 0.5)
    if "muzzle" in extras:
        blob(mx, my + 0.4 * u, 5.4 * u, 3.8 * u, lighten(body, 0.5))

    # cheeks
    cheek_fill = cheek if (look["blush"] or gender == "girl") else mix(cheek, body, 0.55)
    cs = spec.get("cheek_scale", 1.0)
    for s in (-1, 1):
        ccx = cx + feature_dx + edx * 0.4 + s * (14 if spec.get("eye_sep", 8.5) < 10 else 16) * u
        ccy = ey + 4.2 * u - (spec.get("eye_dy", 0) * u if spec.get("eye_dy", 0) < 0 else 0)
        cv.create_oval(ccx - 3.6 * u * cs, ccy - 2.2 * u * cs, ccx + 3.6 * u * cs, ccy + 2.2 * u * cs,
                       fill=cheek_fill, outline="")

    eyes = look["eyes"]
    eye_w = max(1.0, 1.6 * u)
    if spec.get("eye_ring") and eyes in ("open", "big", "half"):
        for s in (-1, 1):
            rr = r * (1.35 if eyes == "big" else 1.0) + 1.3 * u
            cv.create_oval(fcx + s * sep - rr, ey - rr * 1.08, fcx + s * sep + rr, ey + rr * 1.08,
                           fill="#ffffff", outline="")
    if spec.get("eye_ring"):
        eye_col = "#2a2430"
        shine = "#ffffff"
    for s in (-1, 1):
        ex = fcx + s * sep
        if eyes in ("open", "big"):
            rr = r * (1.35 if eyes == "big" else 1.0)
            cv.create_oval(ex - rr, ey - rr * 1.12, ex + rr, ey + rr * 1.12, fill=eye_col, outline="")
            hr = rr * 0.38
            hx, hy = ex + rr * 0.32, ey - rr * 0.42
            cv.create_oval(hx - hr, hy - hr, hx + hr, hy + hr, fill=shine, outline="")
        elif eyes == "half":
            cv.create_oval(ex - r, ey - r * 0.2, ex + r, ey + r * 1.05, fill=eye_col, outline="")
            cv.create_line(ex - r * 1.2, ey - r * 0.15, ex + r * 1.2, ey - r * 0.15, width=eye_w,
                           fill=outline if eye_col not in ("#ffffff",) else "#2b2b2b")
        elif eyes == "happy":
            line_col = "#ffffff" if "eye_patches" in extras else eye_col
            cv.create_line(ex - r, ey + r * 0.4, ex, ey - r * 0.8, ex + r, ey + r * 0.4, smooth=True,
                           width=eye_w, fill=line_col, capstyle=tk.ROUND)
        elif eyes == "closed":
            line_col = "#ffffff" if "eye_patches" in extras else eye_col
            cv.create_line(ex - r, ey, ex, ey + r * 0.75, ex + r, ey, smooth=True, width=eye_w,
                           fill=line_col, capstyle=tk.ROUND)
        elif eyes == "blink":
            line_col = "#ffffff" if "eye_patches" in extras else eye_col
            cv.create_line(ex - r, ey + 0.3 * u, ex + r, ey + 0.3 * u, width=eye_w, fill=line_col,
                           capstyle=tk.ROUND)
        elif eyes == "laugh":
            d = -s
            line_col = "#ffffff" if "eye_patches" in extras else eye_col
            cv.create_line(ex - d * r, ey - r, ex + d * r * 0.7, ey, ex - d * r, ey + r, width=eye_w,
                           fill=line_col, capstyle=tk.ROUND, joinstyle=tk.ROUND)
        elif eyes == "dizzy":
            ang = t_ * 9 * s
            pts_sp = []
            for k in range(14):
                a = ang + k * 0.75
                rad = r * 0.15 + r * 0.95 * k / 13
                pts_sp.extend([ex + math.cos(a) * rad, ey + math.sin(a) * rad])
            cv.create_line(*pts_sp, smooth=True, width=max(1, 1.2 * u), fill=eye_col)
        elif eyes == "heart":
            cv.create_polygon(*heart_points(ex, ey, r * 1.35), smooth=True, fill="#ff4d6d", outline="")

    if gender == "girl" and eyes in ("open", "big", "half", "happy", "closed", "blink"):
        # girls: eyelashes (v2.18)
        lash = eye_col if eye_col not in ("#ffffff", "#fdfdfd") else "#2b2b2b"
        lash_w = max(1.0, 1.3 * u)
        for s in (-1, 1):
            ex = fcx + s * sep
            if eyes in ("open", "big"):
                rr = r * (1.35 if eyes == "big" else 1.0)
                x1, y1 = ex + s * rr * 0.68, ey - rr * 0.86
                cv.create_line(x1, y1, x1 + s * 2.3 * u, y1 - 2.5 * u, width=lash_w, fill=lash, capstyle=tk.ROUND)
                x2, y2 = ex + s * rr * 0.98, ey - rr * 0.38
                cv.create_line(x2, y2, x2 + s * 2.9 * u, y2 - 1.3 * u, width=lash_w, fill=lash, capstyle=tk.ROUND)
            else:
                x1 = ex + s * r * (1.2 if eyes == "half" else 1.0)
                y1 = ey - r * 0.15 if eyes == "half" else ey + (0.4 * r if eyes == "happy" else 0.0)
                cv.create_line(x1, y1, x1 + s * 2.3 * u, y1 - 1.7 * u, width=lash_w, fill=lash, capstyle=tk.ROUND)

    brows = look["brows"]
    if brows:
        for s in (-1, 1):
            ex = fcx + s * sep
            by = ey - r * 1.9
            inner_x = ex - s * r * 1.0
            outer_x = ex + s * r * 1.1
            if brows == "sad":
                inner_y, outer_y = by - r * 0.35, by + r * 0.45
            else:
                inner_y, outer_y = by + r * 0.75, by - r * 0.25
            cv.create_line(inner_x, inner_y, outer_x, outer_y, width=eye_w,
                           fill=eye_col if eye_col != "#ffffff" else "#2b2b2b", capstyle=tk.ROUND)

    # whiskers
    if "whiskers" in extras:
        for s in (-1, 1):
            x0 = cx + feature_dx + s * 12 * u
            for dy in (-1.2, 1.6):
                cv.create_line(x0, ey + 4.5 * u, x0 + s * 7 * u, ey + 4.5 * u + dy * u, width=max(1, 0.9 * u),
                               fill=outline)

    # nose / snout / beak
    mouth = look["mouth"]
    if mouth == "nom":
        mouth = "open" if int(t_ * 7) % 2 == 0 else "cat"
    beak = "beak" in extras
    if "snout" in extras:
        sc = mix(body, "#ff6f91", 0.35)
        blob(mx, my - 2.6 * u, 4.6 * u, 3.2 * u, sc, out=darken(sc, 0.25), width=max(1, u))
        for s in (-1, 1):
            r2 = 0.8 * u
            cv.create_oval(mx + s * 1.6 * u - r2, my - 2.6 * u - r2, mx + s * 1.6 * u + r2, my - 2.6 * u + r2,
                           fill=darken(sc, 0.45), outline="")
    if "nose" in extras:
        cv.create_oval(mx - 2.1 * u, my - 2.2 * u, mx + 2.1 * u, my - 0.6 * u, fill="#3b2a2a", outline="")
        my += 1.2 * u
    if "nose_pink" in extras:
        cv.create_oval(mx - 1.4 * u, my - 1.8 * u, mx + 1.4 * u, my - 0.5 * u, fill="#ff8fa3", outline="")
        my += 0.6 * u

    mw = max(1.0, 1.3 * u)
    if beak:
        bcol = "#ffa94d"
        bout = darken(bcol, 0.3)
        if mouth in ("open", "big_open", "o"):
            cv.create_oval(mx - 2 * u, my - 0.2 * u, mx + 2 * u, my + 3 * u, fill="#7a2e3a", outline="")
            cv.create_polygon(mx - 3 * u, my - 1.4 * u, mx + 3 * u, my - 1.4 * u, mx, my + 0.8 * u,
                              fill=bcol, outline=bout, width=max(1, u))
            cv.create_polygon(mx - 2.4 * u, my + 2.2 * u, mx + 2.4 * u, my + 2.2 * u, mx, my + 4.4 * u,
                              fill=bcol, outline=bout, width=max(1, u))
        else:
            cv.create_polygon(mx - 3 * u, my - 1 * u, mx + 3 * u, my - 1 * u, mx, my + 2.8 * u,
                              fill=bcol, outline=bout, width=max(1, u), joinstyle=tk.ROUND)
    elif mouth == "cat":
        cv.create_line(mx - 3 * u, my, mx - 1.5 * u, my + 1.6 * u, mx, my, mx + 1.5 * u, my + 1.6 * u,
                       mx + 3 * u, my, smooth=True, width=mw, fill=mouth_col, capstyle=tk.ROUND)
    elif mouth == "smile":
        cv.create_line(mx - 3.2 * u, my, mx, my + 2.8 * u, mx + 3.2 * u, my, smooth=True, width=mw,
                       fill=mouth_col, capstyle=tk.ROUND)
    elif mouth in ("open", "big_open"):
        w2 = 2.3 * u if mouth == "open" else 3.2 * u
        h2 = 3.6 * u if mouth == "open" else 5.0 * u
        cv.create_oval(mx - w2, my - 0.6 * u, mx + w2, my - 0.6 * u + h2, fill="#7a2e3a", outline="")
        cv.create_oval(mx - w2 * 0.6, my - 0.6 * u + h2 * 0.5, mx + w2 * 0.6, my - 0.6 * u + h2 * 0.95,
                       fill="#ff8fa3", outline="")
    elif mouth == "frown":
        cv.create_line(mx - 2.8 * u, my + 2.2 * u, mx, my + 0.2 * u, mx + 2.8 * u, my + 2.2 * u,
                       smooth=True, width=mw, fill=mouth_col, capstyle=tk.ROUND)
    elif mouth == "flat":
        cv.create_line(mx - 2.4 * u, my + 1 * u, mx + 2.4 * u, my + 1 * u, width=mw, fill=mouth_col,
                       capstyle=tk.ROUND)
    elif mouth == "wavy":
        cv.create_line(mx - 3.5 * u, my + 1 * u, mx - 1.7 * u, my, mx, my + 1.4 * u, mx + 1.7 * u, my,
                       mx + 3.5 * u, my + 1 * u, smooth=True, width=mw, fill=mouth_col)
    elif mouth == "o":
        cv.create_oval(mx - 1.6 * u, my - 0.2 * u, mx + 1.6 * u, my + 2.8 * u, fill="#7a2e3a", outline="")
    if "fangs" in extras and not beak:
        for s in (-1, 1):
            fx = mx + s * 1.6 * u
            fy = my + (1.4 * u if mouth in ("cat", "smile", "flat") else 0.4 * u)
            cv.create_polygon(fx - 0.9 * u, fy, fx + 0.9 * u, fy, fx, fy + 1.8 * u, fill="#ffffff", outline="")
    if "stinger" in extras:
        sx = cx + side * rx * 0.75
        sy = bottom - ry * 0.25
        cv.create_polygon(sx, sy - 2 * u, sx + side * 5 * u, sy + 1 * u, sx, sy + 2 * u, fill="#3b2f2f",
                          outline="")

    if look.get("sick") and look.get("thermo", True) and not beak:
        # a thermometer in the mouth (v2.18 sick days)
        side_k = 1 if facing >= 0 else -1
        x0, y0 = mx + side_k * 1.2 * u, my + 1.0 * u
        x1, y1 = mx + side_k * 8.5 * u, my + 4.2 * u
        cv.create_line(x0, y0, x1, y1, width=max(2, 2.6 * u), fill="#9aa5b1", capstyle=tk.ROUND)
        cv.create_line(x0, y0, x1, y1, width=max(1, 1.6 * u), fill="#ffffff", capstyle=tk.ROUND)
        cv.create_line(x0, y0, x0 + (x1 - x0) * 0.55, y0 + (y1 - y0) * 0.55, width=max(1, 0.8 * u),
                       fill="#e5484d")
        cv.create_oval(x0 - 1.2 * u, y0 - 1.2 * u, x0 + 1.2 * u, y0 + 1.2 * u, fill="#e5484d", outline="")
    if look["tear"]:
        tx = fcx - sep - 1 * u
        ty = ey + r * 1.6 + (t_ * 6 % 4) * u
        cv.create_oval(tx - 1.3 * u, ty - 1.8 * u, tx + 1.3 * u, ty + 1.8 * u, fill="#7ec8ff", outline="")
    if look["sweat"]:
        sx = cx + feature_dx + rx * 0.62
        sy = cy - ry * 0.55
        cv.create_polygon(sx, sy - 3 * u, sx - 2 * u, sy + 1 * u, sx + 2 * u, sy + 1 * u, smooth=True,
                          fill="#9fd8ff", outline="#5aa9e6", width=max(1, 0.8 * u))
    return {"cx": cx, "cy": cy, "rx": rx, "ry": ry, "u": u, "facing": facing, "lean": lean,
            "feature_dx": feature_dx, "bottom": bottom,
            "eye_y": cy - 1.5 * u + spec.get("eye_dy", 0) * u, "eye_sep": spec.get("eye_sep", 11) * u}


# =============================================================================
# Chat bubble
# =============================================================================

class SpeechBubble:
    WRAP = 210
    PAD = 9
    TAIL = 9

    def __init__(self, pet: "DesktopPet"):
        self.pet = pet
        self.root = pet.root
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack()
        self.win.withdraw()
        self.font = tkfont.Font(family="Segoe UI", size=9)
        self.font_bold = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        self.visible = False
        self.priority = -1
        self.hide_at = 0.0
        self.on_click = None
        self.text = ""
        self.actions = []
        self.size = (0, 0)
        self.tail_x = 0
        self.tail_down = True
        self.pop = 0.0
        self.hover = False
        self._button_hit = False
        self.sticky = False
        self.on_close = None
        self.cv.bind("<ButtonRelease-1>", self._on_click)
        self.cv.bind("<Enter>", lambda e: setattr(self, "hover", True))
        self.cv.bind("<Leave>", lambda e: setattr(self, "hover", False))

    @property
    def hwnd(self):
        return hwnd_of(self.win)

    def show(self, text, actions=None, duration=6.0, priority=1, on_click=None, sticky=False,
             on_close=None) -> bool:
        now = time.monotonic()
        if self.visible and priority < self.priority and now < self.hide_at:
            return False
        self.sticky = sticky
        self.on_close = on_close
        self._pips_update = False   # app.py marks its update offer so it can keep it on screen
        self.text = text
        self.actions = list(actions or [])
        self.on_click = on_click
        self.priority = priority
        self.hide_at = now + max(duration, 2.0 + len(text) / 16.0)
        self.tail_x = 0
        self.pop = 1.0
        self._draw()
        self.visible = True
        self.win.deiconify()
        self.follow()
        self.win.lift()
        return True

    def hide(self):
        self.visible = False
        self.priority = -1
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def _layout(self):
        tmp = self.cv.create_text(0, 0, text=self.text, anchor="nw", width=self.WRAP, font=self.font)
        x1, y1, x2, y2 = self.cv.bbox(tmp)
        self.cv.delete(tmp)
        text_w, text_h = x2 - x1, y2 - y1
        name_w = self.font_bold.measure(self.pet.s["name"]) + 22
        btn_ws = [self.font_bold.measure(label) + 18 for label, _ in self.actions]
        btns_w = sum(btn_ws) + 6 * max(0, len(btn_ws) - 1)
        content_w = max(text_w, name_w, btns_w, 60)
        header_h = 15
        h = self.PAD + header_h + text_h + (26 if self.actions else 0) + self.PAD - 2
        w = content_w + 2 * self.PAD + 2
        return w, h, header_h, btn_ws

    def _draw(self):
        cv = self.cv
        cv.delete("all")
        w, h, header_h, btn_ws = self._layout()
        tail = self.TAIL
        total_h = h + tail
        self.size = (int(w), int(total_h))
        cv.config(width=self.size[0], height=self.size[1])
        body = self.pet.color
        border = darken(body, 0.32)
        fill = "#fffdf8"
        top = 1 if self.tail_down else tail + 1
        bot = top + h - 2
        tx = self.tail_x or w / 2
        tx = max(16, min(w - 16, tx))
        cv.create_polygon(*rounded_rect_points(1, top, w - 1, bot, 11), smooth=True, fill=fill,
                          outline=border, width=2)
        if self.tail_down:
            cv.create_polygon(tx - 7, bot - 2, tx + 7, bot - 2, tx - 1, bot + tail - 1, fill=fill,
                              outline=border, width=2)
            cv.create_line(tx - 5.5, bot - 2, tx + 5.5, bot - 2, width=4, fill=fill)
        else:
            cv.create_polygon(tx - 7, top + 2, tx + 7, top + 2, tx - 1, top - tail + 1, fill=fill,
                              outline=border, width=2)
            cv.create_line(tx - 5.5, top + 2, tx + 5.5, top + 2, width=4, fill=fill)
        x0 = self.PAD + 1
        y0 = top + self.PAD - 3
        cv.create_oval(x0, y0 + 3, x0 + 7, y0 + 10, fill=body, outline=border)
        cv.create_text(x0 + 11, y0, text=self.pet.s["name"], anchor="nw", font=self.font_bold,
                       fill=darken(body, 0.55))
        cv.create_text(w - self.PAD - 2, y0 - 1, text="\u00d7", anchor="ne", font=self.font_bold,
                       fill="#9a8f86", tags=("close",))
        cv.tag_bind("close", "<ButtonRelease-1>", self._on_close_click)
        cv.create_text(x0, y0 + header_h, text=self.text, anchor="nw", width=self.WRAP, font=self.font,
                       fill="#2b2622")
        if self.actions:
            bx = x0
            by = bot - self.PAD - 18
            for i, ((label, callback), bw) in enumerate(zip(self.actions, btn_ws)):
                tag = "btn%d" % i
                cv.create_polygon(*rounded_rect_points(bx, by, bx + bw, by + 19, 8), smooth=True,
                                  fill=lighten(body, 0.2) if i == 0 else "#f1ece4",
                                  outline=border if i == 0 else "#d8d0c4", width=1, tags=(tag,))
                cv.create_text(bx + bw / 2, by + 9.5, text=label, font=self.font_bold,
                               fill=darken(body, 0.6) if i == 0 else "#4a4038", tags=(tag,))
                cv.tag_bind(tag, "<ButtonRelease-1>", lambda e, cb=callback: self._on_button(cb))
                bx += bw + 6

    def _on_button(self, callback):
        self._button_hit = True
        self.hide()
        if callback:
            try:
                callback()
            except Exception as exc:
                logger.warning("Bubble action failed: %s", exc)

    def _on_close_click(self, _event):
        self._button_hit = True
        callback = self.on_close
        self.hide()
        if callback:
            try:
                callback()
            except Exception as exc:
                logger.warning("Bubble close failed: %s", exc)
        if self.pet.tour:
            self.pet.tour.finish(skipped=True)

    def _on_click(self, _event):
        if self._button_hit:
            self._button_hit = False
            return
        if self.sticky:
            return
        callback = self.on_click
        self.hide()
        if callback:
            try:
                callback()
            except Exception as exc:
                logger.warning("Bubble click failed: %s", exc)

    def follow(self):
        if not self.visible:
            return
        if time.monotonic() > self.hide_at and not self.hover:
            self.hide()
            return
        pet = self.pet
        wx, wy, pw, ph = pet.window_rect()
        pcx = wx + pw / 2
        head_top = wy + ph - pet.size * 1.08 - pet.current_hover * pet.u
        bw, bh = self.size
        mon, _work = monitor_rects(pcx, wy + ph - 4, self.root)
        tail_down = head_top - bh >= mon[1] + 2
        bx = pcx - bw / 2
        bx = max(mon[0] + 2, min(mon[2] - bw - 2, bx))
        tail_x = int(pcx - bx)
        if tail_down != self.tail_down or abs(tail_x - (self.tail_x or bw / 2)) >= 3:
            self.tail_down = tail_down
            self.tail_x = tail_x
            self._draw()
            bw, bh = self.size
        by = head_top - bh if self.tail_down else wy + ph + 2
        y_off = int(7 * self.pop) * (1 if self.tail_down else -1)
        self.pop *= 0.72
        try:
            self.win.geometry("%dx%d+%d+%d" % (bw, bh, int(bx), int(by + y_off)))
        except tk.TclError:
            pass

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass



# =============================================================================
# Splashes on the water (v2.19)
# =============================================================================

class SplashFx:
    """A splash + ripples where a water animal leaves or hits the water (the taskbar).

    The pet's own window flies off with the pet, so each splash gets a small see-through window
    of its own that stays where the water was hit and hides itself when the drops have fallen."""

    LIFE = 1.2

    def __init__(self, root):
        self.root = root
        self.win = None
        self.cv = None
        self.job = None
        self.active = False
        self.started = 0.0
        self.drops = []
        self.crown = []
        self.big = False
        self.size = 64
        self.w = self.h = 0
        self.water_y = 0

    def _ensure(self) -> bool:
        if self.win is not None:
            return True
        try:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=KEY)
            try:
                win.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass
            cv = tk.Canvas(win, bg=KEY, highlightthickness=0, bd=0)
            cv.pack(fill="both", expand=True)
            win.withdraw()
            self.win, self.cv = win, cv
            return True
        except tk.TclError:
            return False

    def play(self, x, y, size, big=False):
        """Splash at screen point (x, y) = where the pet meets the water."""
        if not self._ensure():
            return
        u = size / 64.0
        self.size = size
        self.big = bool(big)
        self.w = int(size * (4.2 if big else 3.4))
        self.h = int(size * (2.8 if big else 2.2))
        self.water_y = self.h - int(size * 0.35)
        power = 1.35 if big else 1.0
        self.drops = []
        for _ in range(16 if big else 10):
            self.drops.append({
                "x": random.uniform(-6, 6) * u,
                "y": 0.0,
                "vx": random.uniform(-110, 110) * u * power,
                "vy": -random.uniform(130, 250) * u * power,
                "r": random.uniform(1.6, 3.0) * u,
            })
        # a little crown of water thrown up where the pet goes in / comes out
        count = 7 if big else 5
        self.crown = [(math.radians(-55 + 110 * i / (count - 1)) + random.uniform(-0.1, 0.1),
                       size * random.uniform(0.32, 0.55) * power) for i in range(count)]
        self.started = time.monotonic()
        self.active = True
        try:
            self.cv.config(width=self.w, height=self.h)
            self.win.geometry("%dx%d+%d+%d" % (self.w, self.h, int(x - self.w / 2), int(y - self.water_y)))
            self.win.deiconify()
            self.win.attributes("-topmost", True)
        except tk.TclError:
            self.active = False
            return
        if self.job is None:
            self._tick()

    def _tick(self):
        self.job = None
        if not self.active or self.cv is None:
            return
        age = time.monotonic() - self.started
        if age >= self.LIFE:
            self.hide()
            return
        cv = self.cv
        u = self.size / 64.0
        cx = self.w / 2.0
        wy = self.water_y
        p = age / self.LIFE
        try:
            cv.delete("all")
            # ripples spreading over the water
            for delay in (0.0, 0.22):
                q = (p - delay) / (1.0 - delay)
                if 0.0 < q < 1.0:
                    rx = self.size * (0.3 + (1.7 if self.big else 1.35) * q)
                    ry = rx * 0.2
                    width = max(1.0, (3.0 if self.big else 2.4) * (1.0 - q) * u)
                    cv.create_oval(cx - rx, wy - ry, cx + rx, wy + ry, outline="#5bb8f5", width=width,
                                   dash=(4, 3) if q > 0.6 else None)
            # foam where the water was hit
            if p < 0.3:
                k = 1.0 - p / 0.3
                fr = self.size * (0.36 if self.big else 0.28) * (0.6 + 0.4 * k)
                cv.create_oval(cx - fr, wy - fr * 0.3, cx + fr, wy + fr * 0.3, fill="#e6f6ff", outline="")
            # the crown: streaks of water shooting up, then falling apart
            if p < 0.45:
                k = p / 0.45
                for angle, length in self.crown:
                    reach = length * math.sin(min(1.0, k * 1.8) * math.pi / 2)
                    x0 = cx + math.sin(angle) * self.size * 0.14
                    x1 = x0 + math.sin(angle) * reach * 0.7
                    y1 = wy - math.cos(angle) * reach
                    cv.create_line(x0, wy, x1, y1, fill="#9fd8ff", width=max(1.0, 2.8 * u * (1.0 - k)),
                                   capstyle="round")
            # flying drops
            g = 520 * u
            for d in self.drops:
                dx = d["x"] + d["vx"] * age
                dy = d["y"] + d["vy"] * age + 0.5 * g * age * age
                if dy > 2:
                    continue            # back in the water
                r = d["r"]
                x = cx + dx
                y = wy + dy
                cv.create_oval(x - r, y - r * 1.25, x + r, y + r, fill="#7cc6f2", outline="")
                cv.create_oval(x - r * 0.35, y - r * 0.8, x + r * 0.15, y - r * 0.3, fill="#ffffff", outline="")
        except tk.TclError:
            self.active = False
            return
        self.job = self.root.after(30, self._tick)

    def hide(self):
        self.active = False
        if self.job is not None:
            try:
                self.root.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.win is not None:
            try:
                self.cv.delete("all")
                self.win.withdraw()
            except tk.TclError:
                pass

    def destroy(self):
        self.hide()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None
            self.cv = None


# =============================================================================
# Interactive first-run guide
# =============================================================================

class Tour:
    """Step-by-step guide told by the pet. Steps that need an action wait for it."""

    STEPS = [
        ("tour_welcome", None),
        ("tour_click", "click"),
        ("tour_card", None),
        ("tour_drag", "drop"),
        ("tour_pet", "pet"),
        ("tour_menu", "menu"),
        ("tour_moods", None),
        ("tour_finish", None),
    ]

    def __init__(self, pet: "DesktopPet"):
        self.pet = pet
        self.index = -1
        self.active = False
        self._advance_job = None

    def start(self):
        self.active = True
        self.index = -1
        self.pet.walking = False
        self.next()

    @property
    def waiting_for(self):
        if not self.active or self.index < 0:
            return None
        return self.STEPS[self.index][1]

    def next(self):
        self.index += 1
        if self.index >= len(self.STEPS):
            self.finish()
            return
        self.show()

    def show(self):
        key, wait = self.STEPS[self.index]
        pet = self.pet
        text_key = key
        if key == "tour_drag":          # v2.19: land walks on windows, water splashes, air flies
            text_key = {"water": "tour_drag_water", "air": "tour_drag_air"}.get(pet.category, key)
        text = t(text_key, name=pet.s["name"])
        if key == "tour_welcome":
            actions = [(t("tour_go"), self.next), (t("tour_skip"), lambda: self.finish(skipped=True))]
            pet.set_action("cheer", 1.4)
        elif key == "tour_finish":
            actions = [(t("tour_done_btn"), self.finish)]
            pet.set_action("love", 2.0)
            pet._spawn("heart", 5)
        elif wait == "click":
            actions = [(t("tour_skip"), lambda: self.finish(skipped=True))]
        elif wait:
            actions = [(t("tour_next"), self.next), (t("tour_skip"), lambda: self.finish(skipped=True))]
        else:
            actions = [(t("tour_next"), self.next), (t("tour_skip"), lambda: self.finish(skipped=True))]
        if key == "tour_moods":
            pet._spawn("heart", 2)
        # keep the bubble up by re-showing it after a button press hides it
        pet.bubble.show(text, actions=actions, duration=3600, priority=3, sticky=True)

    def event(self, kind):
        if self.waiting_for != kind:
            return
        if self._advance_job:
            return
        delay = {"click": 1100, "drop": 700, "pet": 1400, "menu": 300}.get(kind, 500)

        def advance():
            self._advance_job = None
            if self.active and self.waiting_for == kind:
                self.next()

        self._advance_job = self.pet.root.after(delay, advance)

    def finish(self, skipped=False):
        if not self.active:
            return
        self.active = False
        if self._advance_job:
            try:
                self.pet.root.after_cancel(self._advance_job)
            except tk.TclError:
                pass
            self._advance_job = None
        self.pet.st["tour_done"] = True
        self.pet.save_state()
        self.pet.bubble.hide()
        if skipped:
            self.pet.say(t("tour_finish"), duration=6, priority=1)
        self.pet.next_walk_at = time.monotonic() + 6


# =============================================================================
# The pet
# =============================================================================

DEFAULT_STATE = {
    "happiness": 70.0,
    "energy": 80.0,
    "fullness": 70.0,
    "bond": 20.0,
    "mode": "taskbar",
    "floor_kind": "top",
    "px": None,
    "floor_y": None,
    "last_seen": None,
    "born": None,
    "snacks": [],
    "tickles_total": 0,
    "pets_total": 0,
    "snacks_total": 0,
}


class DesktopPet:
    FRAME_MS = 40

    def __init__(self, root: tk.Tk, app_cfg: dict, base_dir, callbacks: dict | None = None):
        self.root = root
        self.app_cfg = app_cfg
        self.base_dir = Path(base_dir)
        self.cb = callbacks or {}
        user_settings = dict(app_cfg.get("pet") or {})
        self.s = {**DEFAULT_PET_SETTINGS, **user_settings}
        if user_settings.get("species") not in SPECIES:
            # migrate the old "ears" option (cat / bunny / bear / blob) to a species
            self.s["species"] = _EARS_TO_SPECIES.get(user_settings.get("ears", ""), "cat")
        self.s.pop("ears", None)
        self.s["color"] = safe_color(self.s.get("color"), species_spec(self.s["species"])["color"])
        self.s["cheek_color"] = safe_color(self.s.get("cheek_color"), DEFAULT_PET_SETTINGS["cheek_color"])
        if self.s.get("size") not in SIZES:
            self.s["size"] = "M"
        if not self.s.get("hub_migrated"):
            # 2.5: the pet is a social hub now - the focus timer moved to the right-click menu
            if self.s.get("click_action") in (None, "tickle_open"):
                self.s["click_action"] = "tickle_hub"
            self.s["hub_migrated"] = True
        if not self.s.get("click_v224"):
            # 2.24.1: a single click only tickles - windows open on a double-click (or from the menus)
            if self.s.get("click_action") in (None, "tickle_hub", "tickle_open"):
                self.s["click_action"] = "tickle_only"
            self.s["click_v224"] = True
        if not self.s.get("auto_update_v2252"):
            # 2.25.2: automatic update checks are on for everyone (they can still be switched off)
            self.s["auto_update"] = True
            self.s["auto_update_v2252"] = True
        if not self.s.get("auto_install_v228"):
            # 2.28: updates install themselves (switch off in Pet status & settings)
            self.s["auto_install"] = True
            self.s["auto_install_v228"] = True
        self.state_path = self.base_dir / "data" / "pet_state.json"
        self.st = self._load_state()
        # v2.18 Tamagotchi-style care: growing up (baby -> adult), sick days and a guessing game (tama.py)
        self.growth = tama.Growth(self.st, self._first_run) if tama else None
        self.health = tama.Health(self.st) if tama else None
        self.game = None                # Left or Right? in progress (tama.LeftRight)
        self._game_until = 0.0
        self._cured_note = ""           # "break" / "rest": say "I feel better!" once the user is back
        self._sick_note_due = bool(self.health and self.health.sick)
        self._sick_nag_at = 0.0
        self._next_sneeze = 0.0

        now = time.monotonic()
        self.size = self._body_size()
        self.W = self.H = 0
        self.mode = self.st.get("mode", "taskbar")
        self.floor_kind = self.st.get("floor_kind", "top")
        self.px = self.st.get("px")
        self.floor_y = self.st.get("floor_y")
        self.facing = 1
        self.walking = False
        self.walk_target = 0.0
        self.walk_phase = 0.0
        self.next_walk_at = now + 4.0
        self.sleeping = False
        self.sleep_reason = ""
        self.sleep_started = 0.0
        self.action = None
        self.action_t0 = 0.0
        self.action_t1 = 0.0
        self.particles = []
        self.blink_until = 0.0
        self.next_blink = now + 2.0
        self.next_idle_action = now + random.uniform(6, 12)
        self.next_zzz = 0.0
        self.next_bubble_fx = now + 2.0
        self.current_hover = 0.0
        self.dragging = False
        self.press = None
        self.drag_hist = []
        self.drag_vx = 0.0
        self.pending_dizzy = False
        self.dizzy_cooldown = 0.0
        self.falling = False
        self.vy = 0.0
        self.fall_start = 0.0
        # v2.19 land, sea & sky (roam.py)
        self.leap = None                # a jump / dolphin leap in progress
        self.flight = None              # where a flying animal is heading
        self.platform = None            # the window edge the pet stands on (mode "platform")
        self.fall_vx = 0.0
        self._fall_voluntary = False
        self._fall_prev = 0.0
        self._fall_platforms = []
        self._fall_exclude = None
        self._plat_cache = None
        self._plat_cache_t = 0.0
        self._plat_scan_at = 0.0
        self._pending_sleep = None      # fall asleep once the jump / flight has landed
        self._said_platform = False
        self._fx = []                   # SplashFx windows (water animals)
        self.hovering = False
        self._hover_real = False
        self._hover_xy = None
        self.hover_hist = []
        self.pet_cooldown = 0.0
        self.tickles = []
        self.pout_until = 0.0
        self.grumpy_until = 0.0
        self.focus = False
        self.last_click_t = 0.0
        self.last_interaction = time.time()
        self.visible = bool(self.s.get("enabled", True))
        self.fs_hidden = False
        self._game_windows = set()      # v2.25: minigame windows the pet has jumped into (hidden from the desktop)
        self._fs_override_hwnd = None   # summoned over this fullscreen window: stay visible while it's in front
        self._offscreen_since = 0.0     # off every monitor since (monotonic) -> walks back by itself
        self.summon_hint = ""           # e.g. "Ctrl+Alt+P" (set by the app once the hotkey works)
        self.settings_win = None
        self._last_geom = None
        self._t_last = now
        self._last_slow = now
        self._next_slow = now + 2.0
        self._next_save = now + 60.0
        self._idle_s = 0.0
        self._prompt_next = time.time() + 20 * 60
        self._said_taskbar = False
        self._missed_you = False
        self._errors = 0
        self._after_id = None
        self._open_job = None
        self.tour = None
        self._next_vocal = now + random.uniform(6 * 60, 15 * 60)
        self._next_trick = now + random.uniform(10 * 60, 20 * 60)
        self.trick_dx = 0.0
        self.trick_lift = 0.0
        self.trick_extra_h = 0
        self.shop = None
        if pet_shop is not None:
            try:
                self.shop = pet_shop.Shop(self.base_dir)
            except Exception as exc:
                logger.warning("Shop unavailable: %s", exc)
        self.voice = None
        if pet_sounds is not None:
            try:
                self.voice = pet_sounds.PetVoice()
            except Exception as exc:
                logger.warning("Pet sounds unavailable: %s", exc)
        self.songs = None
        if pet_songs is not None:
            try:
                self.songs = pet_songs.SongPlayer()
            except Exception as exc:
                logger.warning("Trick songs unavailable: %s", exc)

        self._build_window()
        self._apply_offline_time()
        self._init_weather()
        if self.songs:
            self.root.after(25000, self._prewarm_songs)     # v2.22: songs ready before the first dance
        self.social = None
        if social is not None:
            try:
                self.social = social.Social(self)
            except Exception as exc:
                logger.warning("Friends / achievements unavailable: %s", exc)
        self.bubble = SpeechBubble(self)
        self._place_initial()
        if not self.visible:
            self.win.withdraw()
        self._after_id = self.root.after(self.FRAME_MS, self._loop)
        self._greet_job = self.root.after(1500, self._greet)

    # ------------------------------------------------------------------ setup
    @property
    def color(self) -> str:
        return self.s["color"]

    @property
    def u(self) -> float:
        return self.size / 64.0

    @property
    def spec(self) -> dict:
        return species_spec(self.s["species"])

    @property
    def category(self) -> str:
        return self.spec["cat"]

    def _build_window(self):
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack()
        self._resize_canvas()
        self.cv.bind("<ButtonPress-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag_motion)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Button-3>", self._show_menu)
        self.cv.bind("<Motion>", self._on_hover_motion)
        self.cv.bind("<Enter>", self._on_enter)
        self.cv.bind("<Leave>", self._on_leave)

    def _body_size(self) -> int:
        """Pixel size of the pet: the S/M/L setting, smaller while it is still growing up (v2.18)."""
        base = SIZES.get(self.s.get("size"), 64)
        growth = getattr(self, "growth", None)
        if growth is not None and tama is not None:
            return max(24, int(round(base * tama.stage_scale(growth.stage))))
        return base

    def _resize_canvas(self):
        self.size = self._body_size()
        self.W = int(self.size * 2.0)     # room for wings / tails
        self.H = int(self.size * 1.7)     # room for hovering + emotes
        self.cv.config(width=self.W, height=self.H)
        self._last_geom = None

    def _load_state(self) -> dict:
        state = dict(DEFAULT_STATE)
        try:
            if self.state_path.exists():
                state.update(json.loads(self.state_path.read_text(encoding="utf-8-sig")))
        except Exception as exc:
            logger.warning("Could not read pet state: %s", exc)
        if not state.get("born"):
            state["born"] = datetime.now().strftime("%Y-%m-%d")
            self._first_run = True
        else:
            self._first_run = False
        return state

    def save_state(self):
        try:
            self.st.update({
                "mode": self.mode if self.mode in ("fly", "taskbar") else "taskbar",
                "floor_kind": self.floor_kind,
                "px": round(self.px, 1) if self.px is not None else None,
                "floor_y": round(self.floor_y, 1) if self.floor_y is not None else None,
                "last_seen": time.time(),
            })
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.st, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception as exc:
            logger.warning("Could not save pet state: %s", exc)

    def _apply_offline_time(self):
        last = self.st.get("last_seen")
        if not last:
            return
        minutes = max(0.0, min(24 * 60.0, (time.time() - float(last)) / 60.0))
        if minutes < 15:
            return
        st = self.st
        st["energy"] = min(100.0, st["energy"] + minutes * 1.2)
        st["fullness"] = max(5.0, st["fullness"] - minutes * 0.04)
        baseline = 50 + st["bond"] * 0.25
        k = min(1.0, minutes / 600.0)
        st["happiness"] += (baseline - st["happiness"]) * k
        self._missed_you = minutes > 8 * 60
        if self.health is not None and self.health.rested(minutes * 60):
            self._cured_note = "rest"       # a good long rest cured the little cold

    def _place_initial(self):
        # v2.19: window edges are gone after a restart, older versions let pets float ("free")
        if self.mode == "free" and self.category == "air":
            self.mode = "fly"
        if self.mode not in ("taskbar", "fly") or (self.mode == "fly" and self.category != "air"):
            self.mode = "taskbar"
        if self.px is None or self.floor_y is None:
            _mon, work = monitor_rects(0, 0, self.root)
            self.px = work[2] - 380
            self.mode = "taskbar"
            self.floor_kind = "top"
            self.floor_y = work[3]
        vs = virtual_screen(self.root)
        if not (vs[0] <= self.px <= vs[2] and vs[1] <= self.floor_y <= vs[3] + 2):
            _mon, work = monitor_rects(0, 0, self.root)
            self.px = work[2] - 380
            self.mode = "taskbar"
            self.floor_y = work[3]
        if self.mode == "taskbar":
            self.floor_y = self._floor_for(self.px)
        self._apply_position(force=True)

    # --------------------------------------------------------------- geometry
    def window_rect(self):
        wx = int(round(self.px - self.W / 2 + self.trick_dx))
        wy = int(round(self.floor_y)) - self.H
        return wx, wy, self.W, self.H

    def screen_bbox(self):
        """Visible pet body box in screen coords (x, y, w, h)."""
        _wx, wy, _w, h = self.window_rect()
        body_h = int(self.size * 0.95 + self.current_hover * self.u)
        body_w = int(self.size * 0.95)
        return int(self.px - body_w / 2), wy + h - body_h, body_w, body_h

    def _apply_position(self, force=False):
        wx, wy, w, h = self.window_rect()
        geom = (w, h, wx, wy)
        if force or geom != self._last_geom:
            self._last_geom = geom
            try:
                self.win.geometry("%dx%d+%d+%d" % geom)
            except tk.TclError:
                pass

    def _floor_for(self, px):
        probe_y = (self.floor_y or 0) - 10
        mon, work = monitor_rects(px, probe_y, self.root)
        if work[3] < mon[3]:
            return work[3] if self.floor_kind == "top" else mon[3] - 1
        return work[3]

    # ------------------------------------------------------------------- loop
    def _loop(self):
        try:
            now = time.monotonic()
            dt = min(0.1, now - self._t_last)
            self._t_last = now
            if self.visible and not self.fs_hidden:
                self._update(dt, now)
                self._render(now)
                self.bubble.follow()
            self._follow_weather()
            if now >= self._next_slow:
                self._slow_tasks(now)
        except Exception:
            self._errors += 1
            if self._errors <= 5:
                logger.exception("pet loop error")
        finally:
            try:
                self._after_id = self.root.after(self.FRAME_MS, self._loop)
            except tk.TclError:
                pass

    def _update(self, dt, now):
        if self.action and pet_shop is not None and self.action in pet_shop.MOVES:
            span = max(0.01, self.action_t1 - self.action_t0)
            p = max(0.0, min(1.0, (now - self.action_t0) / span))
            self.trick_dx, self.trick_lift = pet_shop.trick_motion(self.action, p, now, self.facing)
        if self.action and now >= self.action_t1:
            finished = self.action
            self.action = None
            self._end_trick(stop_song=False)    # the song's last note rings out by itself
            if finished == "yawn_to_sleep":
                self.sleeping = True
                self.sleep_started = now
        if now >= self.next_blink:
            self.blink_until = now + 0.13
            self.next_blink = now + (0.25 if random.random() < 0.15 else random.uniform(2.5, 6.0))

        if self.dragging:
            pass
        elif self.leap is not None:
            self._leap_step(now)
        elif self.falling:
            self._fall_step(dt)
        elif self.mode == "taskbar":
            self.floor_y = self._floor_for(self.px)
            self._walk(dt, now)
        elif self.mode == "platform":
            self._platform_step(dt, now)
        elif self.mode == "fly":
            self._fly_step(dt, now)
        else:
            self._settle_free()

        if (not self.dragging and not self.falling and not self.walking and not self.sleeping
                and self.action is None and self.leap is None and self.flight is None
                and now >= self.next_idle_action):
            self._idle_action(now)

        if self.sleeping and now >= self.next_zzz:
            self._spawn("z", 1)
            self.next_zzz = now + 1.3

        if self.category == "water" and now >= self.next_bubble_fx and not self.dragging:
            self._spawn("bubble", random.choice((1, 1, 2)))
            self.next_bubble_fx = now + random.uniform(1.6, 3.5) * (2 if self.sleeping else 1)
            if self.s["species"] == "whale" and random.random() < 0.25 and not self.sleeping:
                self._spawn("drop", 4)

        if not self.sleeping and self.mood() == "happy" and random.random() < dt * 0.04:
            self._spawn("heart", 1)

        for p in self.particles:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            if p["kind"] == "drop":
                p["vy"] += 60 * self.u * dt
            elif p["kind"] == "spray":
                p["vy"] += 230 * self.u * dt
            p["life"] -= dt
        self.particles = [p for p in self.particles if p["life"] > 0]

        self._apply_position()

    def _walk(self, dt, now):
        """Walk (land), swim (water) or hop along (perched air) on the taskbar or a window edge."""
        if not self._may_roam():
            self.walking = False
            return
        lo, hi = self._walk_bounds()
        if self.walking:
            energy = self.st["energy"]
            happy = self.st["happiness"]
            speed = 34 if happy > 70 and energy > 40 else (26 if energy > 25 else 15)
            speed *= {"air": 1.3, "water": 1.0}.get(self.category, 1.0)
            speed *= self.u * (0.6 if self.focus else 1.0)
            if self.health is not None and self.health.sick:
                speed *= 0.6            # a cold makes for slow little steps
            self.px += self.facing * speed * dt
            self.walk_phase += dt * (10 if speed > 22 * self.u else 6.5)
            arrived = (self.facing > 0 and self.px >= self.walk_target) or (
                self.facing < 0 and self.px <= self.walk_target)
            at_edge = self.px <= lo or self.px >= hi
            if arrived or at_edge:
                self.px = max(lo, min(hi, self.px))
                self.walking = False
                pause = random.uniform(4, 12)
                if self.st["energy"] < 30:
                    pause *= 2
                if self.focus:
                    pause *= 2.5
                self.next_walk_at = now + pause
                if (at_edge and self.mode == "platform" and self.category == "land" and not self._calm()
                        and random.random() < 0.45):
                    self._hop_off(self.facing)          # walk off the end of the window and drop down
        elif now >= self.next_walk_at:
            if self._roam_move(now):
                return
            mon, _work = monitor_rects(self.px, self.floor_y - 10, self.root)
            if random.random() < 0.25:
                cx, cy = cursor_pos(self.root)
                if self.floor_y - 250 < cy < self.floor_y + 120 and mon[0] <= cx <= mon[2]:
                    target = cx
                else:
                    target = self.px + random.choice((-1, 1)) * random.uniform(80, 400)
            else:
                target = self.px + random.choice((-1, 1)) * random.uniform(60, 420)
            target = max(lo, min(hi, target))
            if abs(target - self.px) < 20:
                self.next_walk_at = now + random.uniform(2, 5)
                return
            self.walk_target = target
            self.facing = 1 if target > self.px else -1
            self.walking = True

    # ------------------------------------------------- v2.19 land, sea & sky (roam.py)
    def _has_visitors(self) -> bool:
        return bool(self.social is not None and getattr(self.social, "visitors", None))

    def _may_roam(self) -> bool:
        """May the pet start a new walk, jump or flight right now?"""
        return bool(self.s.get("walk_on_taskbar", True) and not self.sleeping and self.action is None
                    and not self.hovering and self.visible and not (self.tour and self.tour.active)
                    and self.game is None and not self.dragging and not self._has_visitors())

    def _calm(self) -> bool:
        """Focus session, a cold or low energy: fewer big moves."""
        return bool(self.focus or (self.health is not None and self.health.sick) or self.st["energy"] < 25)

    def _monitor_of(self, x, y):
        if not point_on_a_monitor(x, y):
            return None
        return monitor_rects(x, y, self.root)

    def _platforms(self, max_age=0.8):
        """Free window edges the pet could stand on (cached briefly; [] without Win32)."""
        if roam is None:
            return []
        now = time.monotonic()
        if self._plat_cache is not None and now - self._plat_cache_t < max_age:
            return self._plat_cache
        try:
            plats = roam.platforms_from_windows(roam.scan_windows(), self._monitor_of,
                                                bounds=virtual_screen(self.root), headroom=self.size * 1.1,
                                                min_segment=max(roam.MIN_SEGMENT, self.size * 1.1))
        except Exception as exc:
            logger.debug("window platform scan failed: %s", exc)
            plats = []
        self._plat_cache = plats
        self._plat_cache_t = now
        return plats

    def _walk_bounds(self):
        if self.mode == "platform" and self.platform and roam is not None:
            seg = roam.segment_at(self.platform, self.px, tolerance=self.size)
            if seg:
                margin = self.size * 0.4
                if seg[1] - seg[0] > 2 * margin:
                    return seg[0] + margin, seg[1] - margin
                mid = (seg[0] + seg[1]) / 2.0
                return mid, mid
        mon, _work = monitor_rects(self.px, self.floor_y - 10, self.root)
        return mon[0] + self.W / 2 + 2, mon[2] - self.W / 2 - 2

    def _roam_move(self, now) -> bool:
        """Instead of a plain walk: jump onto a window (land), leap out of the water, take off (air)."""
        if roam is None:
            return False
        calm = self._calm()
        r = random.random()
        category = self.category
        if category == "land":
            return r < (0.1 if calm else 0.25) and self.jump_somewhere()
        if category == "water":
            return self.mode == "taskbar" and r < (0.05 if calm else 0.22) and self.splash_leap()
        if category == "air":
            return r < (0.2 if calm else 0.45) and self.take_off()
        return False

    # --- jumps & leaps
    def jump_somewhere(self, windows_only=False, max_rise=None, max_dx=520.0) -> bool:
        """Land animals: jump onto a window edge - or down to the taskbar from one."""
        if roam is None or self.category != "land" or self.leap is not None or self.falling or self.dragging:
            return False
        if self.mode not in ("taskbar", "platform"):
            return False
        mon, _work = monitor_rects(self.px, self.floor_y - 10, self.root)
        current = self.platform if self.mode == "platform" else None
        targets = roam.jump_targets(self._platforms(), self.px, self.floor_y, self.size, mon,
                                    self._floor_for(self.px), current=current, max_rise=max_rise, max_dx=max_dx)
        if windows_only:
            targets = [target for target in targets if target[2] is not None]
        if not targets:
            return False
        x, y, plat = random.choice(targets)
        self._start_leap(x, y, roam.jump_apex(self.px, self.floor_y, x, y), plat, kind="jump", delay=0.22)
        return True

    def splash_leap(self) -> bool:
        """Water animals: leap out of the water (the taskbar) in a big arc and dive back in with a splash."""
        if (roam is None or self.category != "water" or self.mode != "taskbar" or self.leap is not None
                or self.falling or self.dragging):
            return False
        mon, work = monitor_rects(self.px, self.floor_y - 10, self.root)
        x1, apex = roam.water_leap(self.px, self.floor_y, self.facing, mon, work, self.size)
        if self.floor_y - apex < 60:
            return False                # no room above the water (tiny screen)
        self._start_leap(x1, self.floor_y, apex, None, kind="splash", delay=0.25)
        return True

    def _start_leap(self, x1, y1, apex_y, plat=None, kind="jump", delay=0.0):
        path = roam.leap_path(self.px, self.floor_y, x1, y1, apex_y)
        self.walking = False
        if abs(x1 - self.px) > 4:
            self.facing = 1 if x1 > self.px else -1
        self.leap = {"path": path, "t0": time.monotonic() + delay, "kind": kind,
                     "platform": dict(plat) if plat else None, "launched": False}
        self.mode = "leap"
        self.platform = None
        if delay > 0:
            self.set_action("crouch", delay)

    def _leap_step(self, now):
        leap = self.leap
        t_rel = now - leap["t0"]
        if t_rel < 0:
            return                      # still crouching
        path = leap["path"]
        if not leap["launched"]:
            leap["launched"] = True
            if leap["kind"] == "splash":
                self._splash(path["x0"], path["y0"], big=False)
                self._spawn("spray", 4)
        x, y, _vy = roam.leap_pos(path, t_rel)
        self.px, self.floor_y = x, y
        if t_rel >= path["T"]:
            self._end_leap()

    def _end_leap(self):
        leap = self.leap
        self.leap = None
        path = leap["path"]
        self.px, self.floor_y = path["x1"], path["y1"]
        plat = leap.get("platform")
        if plat is not None:
            frame = roam.window_frame(plat["hwnd"])
            if frame is None or not frame[0] - 4 <= self.px <= frame[2] + 4:
                self._start_fall()      # the window went away mid-jump
                return
            plat.update(left=frame[0], top=frame[1], right=frame[2])
            self.floor_y = frame[1]
            self._set_platform(plat)
        else:
            self.mode = "taskbar"
            self.floor_y = self._floor_for(self.px)
        if leap["kind"] == "splash":
            self._splash(self.px, self.floor_y, big=True)
            self._spawn("spray", 7)
            if random.random() < 0.3:
                self.say(random.choice(lines("splash")), duration=2.0, priority=0)
        self.set_action("land", 0.35)
        self.next_walk_at = time.monotonic() + random.uniform(2, 5)
        if plat is not None:
            self._maybe_platform_hello()
        self._after_move_landed()

    def _hop_off(self, direction):
        """Walk off the end of a window edge: a little hop outwards, then gravity."""
        hwnd = self.platform.get("hwnd") if self.platform else None
        self._start_fall(vx=direction * 200.0, vy=-300.0, exclude=hwnd, voluntary=True)

    # --- standing on a window
    def _set_platform(self, plat):
        self.mode = "platform"
        self.platform = plat
        self._plat_scan_at = time.monotonic() + 1.0

    def _platform_step(self, dt, now):
        """Ride along when the window moves; drop off when it closes, minimises or gets covered."""
        plat = self.platform
        if plat is None or roam is None:
            self._lose_platform()
            return
        frame = roam.window_frame(plat["hwnd"])
        if frame is None:
            self._lose_platform()
            return
        left, top, right, _bottom = frame
        if (left, top, right) != (plat["left"], plat["top"], plat["right"]):
            dx = left - plat["left"]
            if right - left == plat["right"] - plat["left"]:
                if dx:
                    self.px += dx
                    plat["segs"] = [(a + dx, b + dx) for a, b in plat["segs"]]
            else:
                plat["segs"] = [(max(a, left), min(b, right)) for a, b in plat["segs"] if min(b, right) > max(a, left)]
            plat.update(left=left, top=top, right=right)
            self._plat_scan_at = min(self._plat_scan_at, now + 0.35)    # what covers it now?
        self.floor_y = plat["top"]
        if not left - 4 <= self.px <= right + 4:
            self._lose_platform()
            return
        if now >= self._plat_scan_at:
            self._plat_scan_at = now + 1.0
            fresh = roam.find_platform(self._platforms(max_age=0.3), plat["hwnd"])
            if fresh is None or roam.segment_at(fresh, self.px, tolerance=self.size * 0.3) is None:
                self._lose_platform()   # covered by another window / moved too high
                return
            plat["segs"] = fresh["segs"]
            plat.update(left=fresh["left"], top=fresh["top"], right=fresh["right"])
            self.floor_y = plat["top"]
        self._walk(dt, now)

    def _lose_platform(self):
        """The window under the pet closed, moved away or got covered."""
        self.platform = None
        self.walking = False
        if self.category == "air":
            if self.sleeping:
                self.wake("grab", quiet=True)
            self.mode = "fly"           # birds just take off
            self.flight = None
            self.next_walk_at = time.monotonic() + random.uniform(1, 3)
            return
        if self.sleeping:
            self.wake("grab", quiet=True)
        self._start_fall()

    # --- falling (land & water have gravity)
    def _start_fall(self, vx=0.0, vy=0.0, exclude=None, to_taskbar=False, voluntary=False):
        self.mode = "falling"
        self._fall_voluntary = voluntary            # hopped down by itself: no "oof"
        self.falling = True
        self.leap = None
        self.flight = None
        self.platform = None
        self.walking = False
        self.vy = float(vy)
        self.fall_vx = float(vx)
        self.fall_start = self.floor_y
        self._fall_prev = self.floor_y - 8
        self._fall_exclude = exclude
        use_windows = self.category == "land" and not to_taskbar and roam is not None
        self._fall_platforms = self._platforms(max_age=0.25) if use_windows else []

    def _fall_step(self, dt):
        self.vy += (roam.GRAVITY if roam is not None else 2600.0) * dt
        self.floor_y += self.vy * dt
        if self.fall_vx:
            self.px += self.fall_vx * dt
            self.fall_vx *= max(0.0, 1.0 - 1.2 * dt)
            mon, _work = monitor_rects(self.px, self.floor_y - 10, self.root)
            lo, hi = mon[0] + self.W / 2 + 2, mon[2] - self.W / 2 - 2
            if not lo <= self.px <= hi:
                self.px = max(lo, min(hi, self.px))
                self.fall_vx = -self.fall_vx * 0.4      # bonk off the edge of the screen
        if self.vy <= 0:
            self._fall_prev = self.floor_y              # still going up
            return
        taskbar_y = self._floor_for(self.px)
        if roam is not None:
            hit = roam.floor_crossed(self._fall_platforms, self.px, self._fall_prev, self.floor_y, taskbar_y,
                                     exclude=self._fall_exclude)
        else:
            hit = (taskbar_y, None) if self.floor_y >= taskbar_y else None
        if hit is not None:
            self._land_from_fall(hit[0], hit[1])
            return
        self._fall_prev = self.floor_y

    def _land_from_fall(self, target_y, plat):
        self.floor_y = target_y
        self.falling = False
        self.fall_vx = 0.0
        self._fall_platforms = []
        self._fall_exclude = None
        drop = target_y - self.fall_start
        if plat is not None:
            self._set_platform(dict(plat))
        else:
            self.mode = "taskbar"
        into_water = self.category == "water" and plat is None
        if into_water:
            self._splash(self.px, target_y, big=drop > 160)
            self._spawn("spray", 6)
        if self.pending_dizzy:
            self._start_dizzy()
        elif drop > 450 and self.category == "land" and not self._fall_voluntary:
            self.set_action("oof", 1.6)
            self._bump("happiness", -2)
            self.say(random.choice(lines("oof")), duration=2.5, priority=0)
        else:
            self.set_action("land", 0.35)
            if into_water and drop > 160 and random.random() < 0.5:
                self.say(random.choice(lines("splash")), duration=2.0, priority=0)
        if plat is None:
            self._maybe_taskbar_hello()
        else:
            self._maybe_platform_hello()
        self.next_walk_at = time.monotonic() + random.uniform(2, 5)
        self._after_move_landed()

    def _settle_free(self):
        """Pets left floating by older versions (or in an odd state): flyers fly, the others drop down."""
        if self.category == "air":
            self.mode = "fly"
            self.flight = None
        else:
            self._start_fall(to_taskbar=self.category == "water")

    # --- flying (air)
    def take_off(self) -> bool:
        """Air animals: fly off somewhere on the screen."""
        if roam is None or self.category != "air" or self.leap is not None or self.falling or self.dragging:
            return False
        _mon, work = monitor_rects(self.px, self.floor_y - 10, self.root)
        start_y = self.floor_y - (120 if self.mode != "fly" else 0)
        x, y = roam.flight_target(self.px, start_y, work, self.W, self.H)
        if self.mode != "fly" and y > self.floor_y - 60:
            y = max(roam.fly_bounds(work, self.W, self.H)[1], self.floor_y - random.uniform(120, 320))
        self.mode = "fly"
        self.platform = None
        self._fly_to(x, y)
        return True

    def _fly_to(self, x, y, perch=None):
        speed = roam.FLY_SPEED * (0.6 if self._calm() else 1.0) * (1.3 if self.mood() == "happy" else 1.0)
        self.flight = {"path": roam.flight_path(self.px, self.floor_y, x, y, speed), "t": 0.0, "perch": perch}
        if abs(x - self.px) > 4:
            self.facing = 1 if x > self.px else -1
        self.walking = True

    def _fly_step(self, dt, now):
        flight = self.flight
        if flight is not None and roam is not None:
            # wait while you point at it (not when it merely flies under a resting mouse) or while it acts
            paused = (self.hovering and self._hover_real) or (
                self.action is not None and self.action not in ("look", "twitch"))
            if not paused:
                flight["t"] += dt
                self.walk_phase += dt * 9
            x, y, progress = roam.flight_pos(flight["path"], flight["t"])
            self.px, self.floor_y = x, y
            self.walking = not paused
            if progress >= 1.0:
                self.flight = None
                self.walking = False
                self._arrive(flight.get("perch"), now)
            return
        self.flight = None
        self.walking = False
        _mon, work = monitor_rects(self.px, self.floor_y - 10, self.root)
        if roam is not None:
            lo_x, lo_y, hi_x, hi_y = roam.fly_bounds(work, self.W, self.H)
            self.px = max(lo_x, min(hi_x, self.px))
            self.floor_y = max(lo_y, min(hi_y, self.floor_y))
        if now >= self.next_walk_at and self._may_roam() and roam is not None:
            self._next_flight(now, work)

    def _next_flight(self, now, work):
        r = random.random()
        if self.st["energy"] < 25 or r < 0.18:
            self._perch_flight()
            return
        cursor = cursor_pos(self.root) if (r < 0.33 and not self.focus) else None   # come say hi sometimes
        x, y = roam.flight_target(self.px, self.floor_y, work, self.W, self.H, cursor=cursor)
        if math.hypot(x - self.px, y - self.floor_y) < 30:
            self.next_walk_at = now + random.uniform(2, 5)
            return
        self._fly_to(x, y)

    def _perch_flight(self, prefer_taskbar=False) -> bool:
        """Air animals: glide down onto a free window edge or the taskbar for a rest."""
        if roam is None:
            self.mode = "taskbar"
            self.floor_y = self._floor_for(self.px)
            return True
        mon, _work = monitor_rects(self.px, self.floor_y - 10, self.root)
        margin = self.size * 0.45
        if not prefer_taskbar:
            spots = []
            for plat in self._platforms():
                for a, b in plat["segs"]:
                    lo, hi = a + margin, b - margin
                    if hi < lo:
                        continue
                    x = min(max(self.px + random.uniform(-120, 120), lo), hi)
                    if abs(x - self.px) <= 600 and mon[0] <= x <= mon[2]:
                        spots.append((x, plat))
            if spots and random.random() < 0.6:
                x, plat = random.choice(spots)
                self._fly_to(x, plat["top"], perch=dict(plat))
                return True
        lo, hi = mon[0] + self.W / 2 + 2, mon[2] - self.W / 2 - 2
        x = max(lo, min(hi, self.px + random.uniform(-150, 150)))
        self._fly_to(x, self._floor_for(x), perch="taskbar")
        return True

    def _arrive(self, perch, now):
        if perch == "taskbar":
            self.mode = "taskbar"
            self.floor_y = self._floor_for(self.px)
            self.set_action("land", 0.3)
            self._maybe_taskbar_hello()
        elif isinstance(perch, dict):
            frame = roam.window_frame(perch["hwnd"])
            if frame is None or not frame[0] <= self.px <= frame[2]:
                self.mode = "fly"       # that window went away - keep flying
            else:
                perch.update(left=frame[0], top=frame[1], right=frame[2])
                self.floor_y = frame[1]
                self._set_platform(perch)
                self.set_action("land", 0.3)
                self._maybe_platform_hello()
        pause = random.uniform(4, 12)
        if self.st["energy"] < 30:
            pause *= 2
        if self.focus:
            pause *= 2.5
        self.next_walk_at = now + pause
        self._after_move_landed()

    # --- splashes, greetings, pending naps
    def _splash(self, x, y, big=False):
        if not self.visible or self.fs_hidden:
            return
        if not self._fx:
            self._fx = [SplashFx(self.root), SplashFx(self.root)]
        fx = min(self._fx, key=lambda f: (f.active, f.started))     # an idle one, else the oldest
        fx.play(x, y, self.size, big=big)

    def _maybe_platform_hello(self):
        if not self._said_platform:
            self._said_platform = True
            self.say(random.choice(lines("platform")), duration=2.5, priority=0)

    def _after_move_landed(self):
        reason = self._pending_sleep
        if reason and not self.sleeping:
            self._pending_sleep = None
            self.go_to_sleep(reason)

    def _move_menu_entry(self):
        """(icon, label, callback) for the 'how I get around' menu item, or None."""
        category = self.category
        if category == "air":
            if self.mode == "fly":
                return "\u2B07", t("p_perch"), self.go_to_taskbar
            return "\U0001F54A", t("p_fly"), self.float_free
        if self.mode == "platform":
            return "\u2B07", t("p_taskbar"), self.go_to_taskbar
        if self.mode != "taskbar" or roam is None:
            return None
        if category == "water":
            return "\U0001F4A6", t("p_splash"), self.float_free
        return "\U0001F998", t("p_jump_window"), self.float_free

    def _idle_action(self, now):
        self.next_idle_action = now + random.uniform(6, 16)
        mood = self.mood()
        energy = self.st["energy"]
        options = [("look", 3), ("twitch", 3), ("stretch", 1), ("spin", 1)]
        if mood in ("happy", "content") and energy > 35:
            options += [("hop", 3), ("sparkle", 2)]
        if energy < 40:
            options += [("yawn", 4)]
        if mood in ("sad", "very_sad"):
            options += [("sigh", 4)]
        if mood == "tired":
            options += [("yawn", 3)]
        total = sum(w for _, w in options)
        pick = random.uniform(0, total)
        name = options[0][0]
        for name, weight in options:
            pick -= weight
            if pick <= 0:
                break
        durations = {"look": 1.6, "twitch": 0.6, "stretch": 1.3, "spin": 0.5, "hop": 0.55,
                     "sparkle": 1.2, "yawn": 1.5, "sigh": 1.4}
        if name == "spin":
            self.facing = -self.facing
        if name == "sparkle":
            self._spawn("sparkle", 3)
        if name == "sigh":
            self._spawn("dots", 1)
        self.set_action(name, durations[name])

    def _slow_tasks(self, now):
        elapsed = now - self._last_slow
        self._last_slow = now
        self._next_slow = now + 2.0
        self._weather_tick()
        minutes = elapsed / 60.0
        st = self.st
        idle = user_idle_seconds()
        self._idle_s = idle
        away = idle > 120
        hour = datetime.now().hour
        if self.sleeping:
            st["energy"] += 1.6 * minutes
            st["fullness"] -= 0.03 * minutes
        else:
            drain = 0.35 if self.focus else 0.22
            if hour < 6:
                drain *= 1.6
            st["energy"] -= drain * minutes
            st["fullness"] -= 0.10 * minutes
        baseline = 50 + st["bond"] * 0.25
        st["happiness"] += (baseline - st["happiness"]) * 0.01 * minutes
        if st["fullness"] < 20:
            st["happiness"] -= 0.15 * minutes
        if st["energy"] < 15 and not self.sleeping:
            st["happiness"] -= 0.08 * minutes
        lonely = (not away) and (time.time() - self.last_interaction > 2 * 3600)
        if lonely:
            st["happiness"] -= 0.05 * minutes
        self._clamp_stats()

        if not self.sleeping and self.action != "yawn_to_sleep":
            if self.s.get("sleep_when_away", True) and idle > float(self.s.get("away_minutes", 5)) * 60:
                self.go_to_sleep("away")
            elif st["energy"] < 8:
                self.go_to_sleep("tired")
        elif self.sleeping:
            slept = now - self.sleep_started
            if self.sleep_reason == "away" and idle < 5:
                self.wake("back")
            elif self.sleep_reason == "tired" and st["energy"] >= 92 and idle < 60:
                self.wake("rested")
            elif self.sleep_reason == "manual" and st["energy"] >= 99 and idle < 60 and slept > 1800:
                self.wake("rested")

        if self.health is not None:
            self._health_tick(now, idle, away)
        if self.growth is not None:
            self._grow("day")
        if self.game is not None and now > self._game_until and not self.bubble.visible:
            self.game = None            # the guessing game was left unfinished

        if (self.visible and self.s.get("chatty", True) and not self.sleeping and not away
                and time.time() >= self._prompt_next and not self.bubble.visible):
            if st["fullness"] < 20:
                self.say(t("hungry_prompt"), actions=[(t("feed_me"), self.feed)], duration=10, priority=0)
                self._prompt_next = time.time() + 30 * 60
            elif lonely:
                self.say(random.choice(lines("lonely")), duration=6, priority=0)
                self._prompt_next = time.time() + 45 * 60
            elif st["energy"] < 20 and not self.focus:
                self.say(random.choice(lines("tired_hint")), duration=6, priority=0)
                self._prompt_next = time.time() + 40 * 60

        if self.social:
            try:
                self.social.tick(now)
            except Exception:
                logger.exception("social tick failed")

        if now >= self._next_trick:
            self._next_trick = now + random.uniform(12 * 60, 25 * 60)
            owned = self.shop.moves() if self.shop else []
            if (owned and not away and not self.focus and self.action is None and not self.dragging
                    and self.leap is None and not self.falling and self.flight is None
                    and self.mood() in ("happy", "content")):
                self.do_trick(random.choice(owned), music=False)   # surprise tricks stay quiet

        if now >= self._next_vocal:
            # every now and then the pet "talks" in its current mood
            self._next_vocal = now + random.uniform(8 * 60, 20 * 60)
            if not away and not self.bubble.visible and self.action is None:
                self._voice(self.mood())

        if self.visible:
            self._watch_offscreen(now)
            fs = self.in_game or self._fullscreen_hides()
            if fs and not self.fs_hidden:
                self.fs_hidden = True
                self._stop_song()
                self.win.withdraw()
                self.bubble.hide()
            elif not fs and self.fs_hidden:
                self.fs_hidden = False
                self.win.deiconify()
                self._apply_position(force=True)
            if not self.fs_hidden:
                set_topmost(hwnd_of(self.win))
                if self.bubble.visible:
                    set_topmost(self.bubble.hwnd)

        if now >= self._next_save:
            self._next_save = now + 60.0
            self.save_state()

    # ------------------------------------------------------------- rendering
    def mood(self) -> str:
        if self.sleeping:
            return "sleeping"
        if time.monotonic() < self.grumpy_until:
            return "grumpy"
        if self.health is not None and self.health.sick:
            return "sick"
        st = self.st
        if st["energy"] < 20:
            return "tired"
        if st["fullness"] < 18:
            return "hungry"
        h = st["happiness"]
        if h >= 80:
            return "happy"
        if h >= 60:
            return "content"
        if h >= 40:
            return "okay"
        if h >= 20:
            return "sad"
        return "very_sad"

    def mood_label(self) -> str:
        return t("mood_" + self.mood())

    def compute_look(self, now, track_cursor=True) -> dict:
        L = default_look(self.s)
        if self.growth is not None:
            L["stage"] = self.growth.stage
        L["sick"] = bool(self.health is not None and self.health.sick)
        spec = self.spec
        category = spec["cat"]
        t_ = now
        L["t"] = t_
        L["facing"] = self.facing
        L["headband"] = self.focus and not self.sleeping
        L["squash"] = math.sin(t_ * 2.2) * 0.025

        if track_cursor:
            wx, wy, w, h = self.window_rect()
            mx, my = cursor_pos(self.root)
            dx = mx - (wx + w / 2)
            dy = my - (wy + h - self.size * 0.55 - self.current_hover * self.u)
            dist = math.hypot(dx, dy) or 1.0
            k = min(1.0, dist / 160.0)
            L["eye_dx"] = dx / dist * 1.8 * k
            L["eye_dy"] = dy / dist * 1.3 * k

        mood = self.mood()
        if mood == "happy":
            L["mouth"] = "smile"
            L["blush"] = True
            L["wag"] = math.sin(t_ * 8) * 3
            if (t_ % 5.0) < 1.1:
                L["eyes"] = "happy"
        elif mood == "content":
            L["wag"] = math.sin(t_ * 4) * 2
        elif mood == "okay":
            L["mouth"] = "cat" if (t_ % 9) < 6 else "flat"
            L["wag"] = math.sin(t_ * 2) * 1
        elif mood == "sad":
            L["brows"] = "sad"
            L["mouth"] = "frown"
            L["droop"] = 0.6
        elif mood == "very_sad":
            L["brows"] = "sad"
            L["mouth"] = "frown"
            L["droop"] = 1.0
            L["tear"] = True
        elif mood == "tired":
            L["eyes"] = "half"
            L["mouth"] = "flat"
            L["droop"] = 0.5
            L["sweat"] = (t_ % 12) < 3
        elif mood == "hungry":
            L["brows"] = "sad"
            L["mouth"] = "o"
            L["droop"] = 0.3
        elif mood == "grumpy":
            L["brows"] = "angry"
            L["mouth"] = "frown"
        elif mood == "sick":
            L["eyes"] = "half"
            L["brows"] = "sad"
            L["mouth"] = "flat"
            L["droop"] = 0.5
            L["sweat"] = (t_ % 9) < 2.5

        if self.hovering and not self.walking and not self.sleeping:
            L["blush"] = True

        # ---- how this animal moves ----
        flap_hz = spec.get("flap_hz", 12)
        if category == "air":
            L["flap"] = math.sin(t_ * flap_hz)
            L["hover"] = 9 + math.sin(t_ * 2.6) * 2.5
        elif category == "water":
            L["flap"] = math.sin(t_ * 4)
            L["hover"] = spec.get("float", 0) + math.sin(t_ * 2.2) * 1.6
            L["wag"] = L["wag"] + math.sin(t_ * 5) * 2.5
            L["lean"] = math.sin(t_ * 1.7) * 0.8

        if self.walking:
            wp = self.walk_phase
            L["eye_dx"] = self.facing * 1.6
            L["eye_dy"] = 0.3
            if category == "air":
                L["hover"] += abs(math.sin(wp * 0.5)) * 3
                L["lean"] = self.facing * 2.5
                L["flap"] = math.sin(t_ * flap_hz * 1.3)
            elif category == "water" and not spec.get("hop"):
                L["hover"] += math.sin(wp * 0.6) * 2
                L["lean"] = self.facing * 2
                L["wag"] = math.sin(wp * 1.5) * 4
                if spec.get("feet") in ("paws", "webbed", "bird"):
                    L["foot_l"] = max(0.0, math.sin(wp)) * 2
                    L["foot_r"] = max(0.0, -math.sin(wp)) * 2
            elif spec.get("hop"):
                L["hop"] = abs(math.sin(wp * 0.55)) * 7
                L["squash"] = -0.06 * abs(math.sin(wp * 0.55)) + 0.04
                L["lean"] = self.facing * 1.0
            else:
                L["foot_l"] = max(0.0, math.sin(wp)) * 2.6
                L["foot_r"] = max(0.0, -math.sin(wp)) * 2.6
                L["hop"] = abs(math.sin(wp)) * 1.3
                L["lean"] = self.facing * 1.5

        if self.leap is not None and not self.dragging and roam is not None:
            # v2.19: mid-jump (land) / mid-leap out of the water
            t_rel = now - self.leap["t0"]
            if t_rel >= 0:
                path = self.leap["path"]
                _x, _y, vy = roam.leap_pos(path, t_rel)
                k = max(-1.0, min(1.0, vy / max(1.0, path["vy0"])))      # -1 take-off .. +1 landing
                L["squash"] = -0.04 - 0.1 * abs(k)
                L["lean"] = self.facing * (3.2 if category == "water" else 2.2)
                L["eye_dx"] = self.facing * 1.6
                L["eye_dy"] = -0.8 if k < 0 else 0.8
                L["eyes"] = "happy" if k < 0.35 else "big"
                L["mouth"] = "big_open" if category == "water" else ("smile" if k < 0 else "o")
                L["brows"] = None
                L["hop"] = 0.0
                L["foot_l"] = L["foot_r"] = 0.0
                L["wag"] = math.sin(t_ * 14) * 4
                if category != "air":
                    L["hover"] = 0.0
                else:
                    L["flap"] = math.sin(t_ * flap_hz * 1.5)

        if now < self.blink_until and L["eyes"] in ("open", "big"):
            L["eyes"] = "blink"

        if self.sleeping:
            L["eyes"] = "closed"
            L["mouth"] = "cat"
            L["brows"] = None
            L["tear"] = False
            L["sweat"] = False
            L["squash"] = 0.16 + math.sin(t_ * 1.2) * 0.04
            L["droop"] = 0.45
            L["eye_dx"] = 0
            L["eye_dy"] = 0.8
            L["wag"] = 0
            L["flap"] = -0.4 if category == "air" else 0.0
            L["hover"] = (spec.get("float", 0) * 0.5 + math.sin(t_ * 1.1) * 0.8) if category == "water" else 0.0

        if self.dragging:
            L["dangle"] = category == "land"
            L["squash"] = -0.14 + math.sin(t_ * 12) * 0.02
            L["lean"] = max(-5.0, min(5.0, -self.drag_vx / 160.0))
            if category == "air":
                L["flap"] = math.sin(t_ * flap_hz * 1.5)
            if self.st["happiness"] > 65:
                L["eyes"] = "happy"
                L["mouth"] = "big_open"
            else:
                L["eyes"] = "big"
                L["mouth"] = "o"
            L["brows"] = None
        elif self.falling:
            if category == "water":
                L["eyes"] = "happy"         # wheee - back into the water!
                L["mouth"] = "big_open"
                L["hover"] = 0.0
            else:
                L["eyes"] = "big"
                L["mouth"] = "o"
            L["squash"] = -0.1

        act = self.action
        if act:
            span = max(0.01, self.action_t1 - self.action_t0)
            p = max(0.0, min(1.0, (now - self.action_t0) / span))
            bump = math.sin(p * math.pi)
            if act == "tickle":
                L.update(eyes="laugh", mouth="big_open", blush=True, brows=None,
                         lean=math.sin(t_ * 38) * 3.5, squash=math.sin(t_ * 25) * 0.07,
                         hop=abs(math.sin(t_ * 14)) * 2.2)
            elif act == "petted":
                L.update(eyes="happy", mouth="cat", blush=True, brows=None,
                         squash=0.06 + math.sin(t_ * 6) * 0.03, lean=math.sin(t_ * 5) * 1.5,
                         wag=math.sin(t_ * 12) * 3, droop=0.0)
            elif act == "eat":
                L.update(eyes="happy", mouth="nom", blush=True, brows=None, squash=math.sin(t_ * 14) * 0.03)
            elif act == "refuse":
                L.update(eyes="closed", mouth="frown", lean=math.sin(t_ * 16) * 2.2)
            elif act in ("yawn", "yawn_to_sleep"):
                if p < 0.7:
                    L.update(eyes="closed", mouth="big_open", squash=-0.07 * math.sin(p / 0.7 * math.pi))
                else:
                    L.update(eyes="half", mouth="flat")
            elif act in ("stretch", "wake"):
                L.update(eyes="closed", mouth="cat" if act == "stretch" else "big_open",
                         squash=-0.13 * bump, brows=None)
            elif act == "dizzy":
                L.update(eyes="dizzy", mouth="wavy", brows=None, lean=math.sin(t_ * 7) * 3,
                         squash=math.sin(t_ * 9) * 0.05)
            elif act == "startle":
                L.update(eyes="big", mouth="o", brows=None, hop=bump * 7)
            elif act == "angry":
                L.update(eyes="open", brows="angry", mouth="frown", lean=math.sin(t_ * 22) * 1.2, blush=False)
            elif act == "hop":
                L.update(eyes="happy", mouth="smile", hop=bump * 8, squash=-0.08 * bump)
            elif act == "land":
                L.update(squash=0.22 * (1 - p))
            elif act == "oof":
                if p < 0.25:
                    L.update(squash=0.32 * (1 - p * 4), eyes="closed", mouth="o")
                else:
                    L.update(eyes="dizzy", mouth="wavy", squash=math.sin(t_ * 9) * 0.04)
            elif act == "look":
                L.update(eye_dx=-2.0 if p < 0.5 else 2.0, eye_dy=0.0)
            elif act == "twitch":
                L["twitch"] = math.sin(p * math.pi * 4) * 0.35
            elif act == "sparkle":
                L.update(eyes="happy", mouth="smile", blush=True)
            elif act == "sigh":
                L.update(eyes="half", mouth="flat", squash=0.05 * bump)
            elif act == "love":
                L.update(eyes="heart", mouth="big_open" if p < 0.5 else "smile", blush=True, brows=None,
                         hop=abs(math.sin(t_ * 9)) * 4, droop=0.0, tear=False)
            elif act == "cheer":
                L.update(eyes="happy", mouth="big_open", blush=True, brows=None,
                         hop=abs(math.sin(t_ * 10)) * 5, droop=0.0)
            elif act == "worried":
                L.update(eyes="open", brows="sad", mouth="wavy", droop=0.4)
            elif act == "sip":
                L.update(eyes="closed", mouth="o", blush=True, squash=math.sin(t_ * 6) * 0.03)
            elif act == "sneeze":
                # v2.18 sick days: aaa... CHOO!
                if p < 0.6:
                    L.update(eyes="half" if p < 0.35 else "closed", mouth="o", brows="sad", thermo=False,
                             squash=-0.07 * math.sin(p / 0.6 * math.pi / 2), lean=-self.facing * 1.5 * p)
                else:
                    L.update(eyes="closed", mouth="big_open", brows=None, thermo=False,
                             squash=0.14 * (1 - (p - 0.6) / 0.4), lean=self.facing * 2.5)
            elif act == "peek":
                # v2.18 Left or Right?: the pet turns its head to one side
                L.update(eyes="big" if p < 0.25 else "happy", mouth="big_open" if p < 0.25 else "smile",
                         blush=True, brows=None, eye_dx=self.facing * 2.6, eye_dy=0.0, lean=self.facing * 3.0,
                         hop=bump * 4)
            elif act == "think":
                # v2.18 Left or Right?: waiting for your guess
                L.update(eyes="open", mouth="cat", brows=None, eye_dx=math.sin(t_ * 2.4) * 1.6, eye_dy=-0.4,
                         lean=math.sin(t_ * 2.4) * 0.8)
            elif act == "crouch":
                # v2.19: getting ready to jump / leap
                L.update(squash=0.2 * min(1.0, p * 1.6), eyes="happy", mouth="smile", brows=None,
                         eye_dx=self.facing * 1.8, hop=0.0)
            elif pet_shop is not None and act in pet_shop.MOVES:
                pet_shop.trick_pose(L, act, p, t_)
                if self.trick_lift:
                    L["hover"] = L.get("hover", 0.0) + self.trick_lift / self.u
        return L

    def _render(self, now):
        cv = self.cv
        cv.delete("all")
        look = self.compute_look(now)
        self.current_hover = look.get("hover", 0.0) + look.get("hop", 0.0)
        props = self.shop.equipped() if self.shop else {}
        look["head_prop"] = bool(props.get("head"))      # a hat hides the bow / hair tuft (v2.18)
        if props and pet_shop is not None:
            pet_shop.draw_back_props(cv, body_geometry(self.W / 2, self.H - 1, self.size, look), props, now)
        geo = draw_pet(cv, self.W / 2, self.H - 1, self.size, look)
        if props and pet_shop is not None:
            pet_shop.draw_front_props(cv, geo, props, now)
        if pet_shop is not None and self.action in pet_shop.MOVES:
            span = max(0.01, self.action_t1 - self.action_t0)
            p = max(0.0, min(1.0, (now - self.action_t0) / span))
            pet_shop.draw_trick_fx(cv, self.action, p, now, geo, self.W, self.trick_dx)
        fsize = max(7, int(6 + self.size / 14))
        head_y = self.H - 1 - self.size * 0.95 - self.current_hover * self.u
        for p in self.particles:
            frac = max(0.0, p["life"] / p["max"])
            x = self.W / 2 + p["x"]
            y = head_y + p["y"]
            kind = p["kind"]
            if kind == "heart":
                s = self.u * (3.2 + 1.5 * frac)
                cv.create_polygon(*heart_points(x, y, s), smooth=True, fill="#ff5d8f", outline="")
            elif kind == "z":
                size = int(fsize * (0.7 + 0.6 * (1 - frac)))
                cv.create_text(x, y, text="z" if frac > 0.5 else "Z", font=("Segoe UI", size, "bold"),
                               fill="#7d8cff")
            elif kind == "note":
                cv.create_text(x, y, text="\u266a", font=("Segoe UI Symbol", fsize, "bold"), fill="#8b5cf6")
            elif kind == "sparkle":
                cv.create_text(x, y, text="\u2726", font=("Segoe UI Symbol", fsize), fill="#f5b700")
            elif kind == "bang":
                cv.create_text(x, y, text="!", font=("Segoe UI", fsize + 2, "bold"), fill="#e5484d")
            elif kind == "dots":
                cv.create_text(x, y, text="\u2026", font=("Segoe UI", fsize + 1, "bold"), fill="#6b6b6b")
            elif kind == "crumb":
                r = self.u * 1.3
                cv.create_oval(x - r, y - r, x + r, y + r, fill="#c98b4a", outline="")
            elif kind == "bubble":
                r = self.u * (1.6 + 1.6 * (1 - frac))
                cv.create_oval(x - r, y - r, x + r, y + r, fill="", outline="#7cc6f2", width=max(1, self.u))
                cv.create_oval(x - r * 0.2, y - r * 0.7, x + r * 0.25, y - r * 0.25, fill="#ffffff", outline="")
            elif kind == "drop":
                r = self.u * 1.4
                cv.create_oval(x - r, y - r * 1.3, x + r, y + r, fill="#8fd3ff", outline="")
            elif kind == "spray":
                r = self.u * (1.0 + 0.6 * frac)
                cv.create_oval(x - r, y - r * 1.3, x + r, y + r, fill="#7cc6f2", outline="")

    def _spawn(self, kind, count=1):
        for _ in range(count):
            if kind == "z":
                p = dict(x=self.size * 0.25, y=-self.size * 0.05, vx=self.u * 6, vy=-self.u * 9, life=2.4)
            elif kind == "crumb":
                p = dict(x=random.uniform(-4, 4) * self.u, y=self.size * 0.55,
                         vx=random.uniform(-12, 12) * self.u, vy=random.uniform(4, 14) * self.u, life=0.7)
            elif kind == "bang":
                p = dict(x=self.size * 0.3, y=-self.size * 0.08, vx=0, vy=-self.u * 4, life=1.0)
            elif kind == "bubble":
                p = dict(x=random.uniform(-6, 14) * self.u * self.facing, y=self.size * 0.35,
                         vx=random.uniform(-3, 3) * self.u, vy=-random.uniform(10, 16) * self.u, life=2.2)
            elif kind == "drop":
                p = dict(x=random.uniform(-2, 2) * self.u, y=-self.size * 0.02,
                         vx=random.uniform(-9, 9) * self.u, vy=-random.uniform(22, 30) * self.u, life=1.0)
            elif kind == "spray":
                # v2.19: water thrown up around a water animal leaving / hitting the water
                p = dict(x=random.uniform(-10, 10) * self.u, y=self.size * 0.88,
                         vx=random.uniform(-55, 55) * self.u, vy=-random.uniform(40, 80) * self.u, life=0.75)
            else:
                p = dict(x=random.uniform(-18, 18) * self.u, y=random.uniform(-2, 8) * self.u,
                         vx=random.uniform(-6, 6) * self.u, vy=-random.uniform(12, 20) * self.u, life=1.6)
            p["kind"] = kind
            p["max"] = p["life"]
            self.particles.append(p)
        self.particles = self.particles[-28:]

    # ---------------------------------------------------------- interactions
    def set_action(self, name, duration):
        now = time.monotonic()
        if pet_shop is not None and self.action in pet_shop.MOVES and name != self.action:
            self._end_trick()
        self.action = name
        self.action_t0 = now
        self.action_t1 = now + duration
        if name not in ("look", "twitch"):
            self.walking = False

    def _bump(self, stat, amount):
        self.st[stat] = self.st.get(stat, 0) + amount
        self._clamp_stats()

    def _clamp_stats(self):
        for key in ("happiness", "energy", "fullness", "bond"):
            self.st[key] = max(0.0, min(100.0, float(self.st.get(key, 50))))

    def _touch(self):
        self.last_interaction = time.time()
        self._pending_sleep = None      # you're here: no nap after the current jump / flight

    def _on_press(self, event):
        self.press = (event.x_root, event.y_root, time.monotonic(), self.px, self.floor_y)
        self.dragging = False
        self.drag_hist = [(time.monotonic(), event.x_root)]

    def _on_drag_motion(self, event):
        if not self.press:
            return
        x0, y0, _t0, px0, fy0 = self.press
        if not self.dragging and abs(event.x_root - x0) + abs(event.y_root - y0) > 5:
            self._start_drag()
        if not self.dragging:
            return
        self.px = px0 + (event.x_root - x0)
        self.floor_y = fy0 + (event.y_root - y0)
        now = time.monotonic()
        self.drag_hist.append((now, event.x_root))
        self.drag_hist = [(t0, x) for t0, x in self.drag_hist if now - t0 < 1.0]
        if len(self.drag_hist) >= 2:
            (ta, xa), (tb, xb) = self.drag_hist[-2], self.drag_hist[-1]
            if tb > ta:
                self.drag_vx = 0.6 * self.drag_vx + 0.4 * (xb - xa) / (tb - ta)
        self._check_shake(now)
        self._apply_position()
        self.bubble.follow()

    def _start_drag(self):
        self._touch()
        if self.sleeping:
            self.wake("grab", quiet=True)
        self.dragging = True
        self.walking = False
        self.falling = False
        self.leap = None
        self.flight = None
        self.platform = None
        self.action = None
        if random.random() < 0.35:
            self.say(random.choice(lines("drag")), duration=1.8, priority=0)

    def _check_shake(self, now):
        if now < self.dizzy_cooldown or self.pending_dizzy:
            return
        reversals = 0
        direction = 0
        anchor = self.drag_hist[0][1] if self.drag_hist else 0
        for _t, x in self.drag_hist[1:]:
            delta = x - anchor
            if abs(delta) >= 14:
                d = 1 if delta > 0 else -1
                if direction and d != direction:
                    reversals += 1
                direction = d
                anchor = x
        if reversals >= 5:
            self.pending_dizzy = True
            self.say(random.choice(lines("dizzy")), duration=2.5, priority=1)

    def _on_release(self, event):
        if self.dragging:
            self.dragging = False
            self._drop()
        elif self.press:
            self._on_click()
        self.press = None

    def _drop(self):
        mon, work = monitor_rects(self.px, self.floor_y - 5, self.root)
        bottom_taskbar = work[3] < mon[3]
        fy = self.floor_y
        self.platform = None
        self.flight = None
        self.leap = None
        throw = max(-600.0, min(600.0, self.drag_vx * 0.35))
        if bottom_taskbar and fy >= work[3] - 16:
            self.floor_kind = "inside" if fy > work[3] + 10 else "top"
            self.mode = "taskbar"
            target = self._floor_for(self.px)
            if fy >= target:
                self.floor_y = target
                self._after_landing()
                if self.category == "water":
                    self._splash(self.px, self.floor_y)
            else:
                self._start_fall(to_taskbar=True)
        elif not bottom_taskbar and fy >= work[3] - 30:
            self.floor_kind = "top"
            self.mode = "taskbar"
            self.floor_y = work[3]
            self._after_landing()
            if self.category == "water":
                self._splash(self.px, self.floor_y)
        elif self.category == "air":
            # flyers stay where you leave them - or perch on the window edge you put them on
            perch = roam.perch_at(self._platforms(max_age=0.3), self.px, fy) if roam is not None else None
            if perch is not None:
                self.floor_y = perch["top"]
                self._set_platform(dict(perch))
            else:
                self.mode = "fly"
                if roam is not None:
                    lo_x, lo_y, hi_x, hi_y = roam.fly_bounds(work, self.W, self.H)
                    self.px = max(lo_x, min(hi_x, self.px))
                    self.floor_y = max(lo_y, min(hi_y, self.floor_y))
            self._after_landing(taskbar=False)
        else:
            # v2.19: land and water animals have gravity - back onto a window edge / into the water
            self.floor_kind = "top"
            self._start_fall(vx=throw if self.category == "land" else throw * 0.5,
                             to_taskbar=self.category == "water")
        self.drag_vx = 0.0
        self.save_state()
        self._tour_event("drop")

    def _after_landing(self, taskbar=True):
        if self.pending_dizzy:
            self._start_dizzy()
        else:
            self.set_action("land", 0.35)
        if taskbar:
            self._maybe_taskbar_hello()
        self.next_walk_at = time.monotonic() + random.uniform(2, 5)
        self._after_move_landed()

    def _maybe_taskbar_hello(self):
        if not self._said_taskbar and self.s.get("walk_on_taskbar", True):
            self._said_taskbar = True
            key = {"water": "taskbar_water", "air": "taskbar_air"}.get(self.category, "taskbar")
            self.say(random.choice(lines(key)), duration=2.5, priority=0)

    def _start_dizzy(self):
        self.pending_dizzy = False
        self.dizzy_cooldown = time.monotonic() + 6
        self.set_action("dizzy", 2.6)
        self._voice("dizzy")
        self._bump("happiness", -5)
        self._bump("bond", -1)

    def _on_click(self):
        self._touch()
        now = time.monotonic()
        double = now - self.last_click_t < 0.38
        self.last_click_t = now
        click_action = self.s.get("click_action", "tickle_hub")
        if self.sleeping:
            self.wake("poke")
            if click_action == "tickle_open":
                self._schedule_open(700)
            elif click_action == "tickle_hub":
                self._schedule_hub(700)
            return
        if double and click_action == "tickle_only":
            self._cancel_open()
            if self.social is not None and getattr(self.social, "enabled", False):
                self.open_hub()
            else:
                self._call("toggle_window")
            return
        self.tickle()
        touring = bool(self.tour and self.tour.waiting_for == "click")
        if click_action == "tickle_open" and not double:
            self._schedule_open(450)
        elif (click_action == "tickle_hub" and not double) or touring:
            self._schedule_hub(450)
        self._tour_event("click")

    def _schedule_hub(self, delay_ms):
        if not self.social:
            self._schedule_open(delay_ms)
            return
        window = self.social.window
        if window and window.alive():
            return  # already open - just a tickle
        self._cancel_open()
        self._open_job = self.root.after(delay_ms, self.open_hub)

    def _schedule_open(self, delay_ms):
        self._cancel_open()
        self._open_job = self.root.after(delay_ms, lambda: self._call("toggle_window"))

    def _cancel_open(self):
        if self._open_job:
            try:
                self.root.after_cancel(self._open_job)
            except tk.TclError:
                pass
            self._open_job = None

    def do_trick(self, move: str, force=False, music=True) -> bool:
        """Perform a move from the shop (if owned).

        v2.22: sing / dance (and other show moves) play the chosen song unless ``music`` is False."""
        if pet_shop is None or move not in pet_shop.MOVES:
            return False
        if not force and not (self.shop and self.shop.owns(move)):
            return False
        if self.sleeping:
            self.wake("poke", quiet=True)
        self._end_trick()
        extra = pet_shop.TALL_MOVES.get(move, 0)
        if extra:
            self.trick_extra_h = extra
            self.H += extra
            self.cv.config(height=self.H)
            self._last_geom = None
        self.walking = False
        song = self._trick_song_for(move) if music else None
        seconds = pet_shop.MOVES[move][1]
        if song:
            seconds = pet_songs.seconds_for(move, seconds)
        self.set_action(move, seconds)
        if not (song and self._play_song(song, pet_songs.style_for(move), seconds)):
            self._voice("happy")
        self._touch()
        return True

    # ------------------------------------------------------------ v2.22 songs
    # ------------------------------------------------------- v2.23 weather
    def _init_weather(self):
        self.wx_service = None
        self.wx_fx = None
        self.wx_last_cond = None
        self.wx_told_denied = False
        self.wx_last_push = None
        self.wx_picker = None
        self.wx_pending_report = False
        self.wx_started = time.monotonic()
        if weather_live is None:
            return
        try:
            self.wx_service = weather_live.WeatherService(Path(self.base_dir) / "data" / "weather.json",
                                                          self._wx_lang())
            mode = self.s.get("weather_mode", "ip")
            self.wx_service.mode = mode if mode in weather_live.MODES else weather_live.DEFAULT_MODE
            self.wx_service.city_place = self.s.get("weather_place") or None
            self.wx_service.start()
            self.wx_fx = weather_live.WeatherOverlay(self.root, KEY)
            fx = self.s.get("weather_fx", "pet")
            self.wx_fx.set_mode(fx if fx in weather_live.FX_MODES else weather_live.DEFAULT_FX)
            if self.wx_service.weather:
                self.wx_fx.set_weather(self.wx_service.weather)
                self.wx_last_cond = weather_live.condition(self.wx_service.weather.get("code"))
        except Exception as exc:
            logger.warning("Weather unavailable: %s", exc)
            self.wx_service = None

    @staticmethod
    def _wx_lang():
        try:
            from i18n import get_language
            return {"id": "id", "zh_TW": "zh", "zh_CN": "zh"}.get(get_language(), "en")
        except Exception:
            return "en"

    def current_weather(self):
        """(weather, place) - from the live service, else from my Friends profile."""
        if self.wx_service and self.wx_service.weather and self.s.get("weather_mode") != "off":
            return self.wx_service.weather, self.wx_service.place
        if self.social and getattr(self.social, "enabled", False) and self.s.get("weather_mode") != "off":
            try:
                weather, loc = self.social.my_weather()
            except Exception:
                weather, loc = None, None
            if weather:
                return weather, {"name": (loc or {}).get("city", ""), "country": (loc or {}).get("country", "")}
        return None, None

    def _follow_weather(self):
        fx = getattr(self, "wx_fx", None)
        if fx is None:
            return
        try:
            show = (self.visible and not self.fs_hidden and self.px is not None and self.floor_y is not None
                    and self.s.get("weather_fx", "pet") != "off")
            if not show:
                fx.follow(0, 0, self.size, 0, (0, 0, 1, 1), show=False)
                return
            _bx, by, _bw, _bh = self.screen_bbox()
            _mon, work = monitor_rects(self.px, self.floor_y - 10, self.root)
            umbrella = not (self.dragging or self.falling or self.leap is not None)
            fx.follow(self.px + self.trick_dx, self.floor_y, self.size, by, work, show=True, umbrella=umbrella)
        except Exception as exc:
            logger.debug("weather follow failed: %s", exc)

    def _weather_tick(self):
        """Pick up what the weather service found (called from the slow tasks)."""
        if not self.wx_service:
            return
        update = self.wx_service.poll()
        if update is None:
            if self.wx_fx is not None and self.wx_fx.weather is None:
                weather, _place = self.current_weather()      # Friends profile weather as a fallback
                if weather:
                    self.wx_fx.set_weather(weather)
            return
        weather, place = update.get("weather"), update.get("place")
        if self.wx_fx is not None:
            self.wx_fx.set_weather(weather)
        if update.get("status") in ("denied", "nodata", "unavailable") and self.s.get("weather_mode") == "follow" \
                and not self.wx_told_denied and place:
            self.wx_told_denied = True
            self.say(t("wl_denied", city=weather_live.place_label(place)), duration=10, priority=1)
        if not weather or not place:
            return
        cond = weather_live.condition(weather.get("code"))
        city = weather_live.place_label(place) or "?"
        if not self.s.get("weather_intro") and self.visible and time.monotonic() - self.wx_started > 20:
            self.update_setting("weather_intro", True)      # first time: introduce the new feature
            self.wx_pending_report = False
            self.say(t("wl_intro", report=self.weather_report(), name=self.s["name"]), duration=10, priority=1)
        elif self.wx_pending_report:
            self.wx_pending_report = False
            self.say(self.weather_report(), duration=6, priority=1)
        elif self.wx_last_cond is not None and cond != self.wx_last_cond and self.s.get("chatty", True) \
                and not self.sleeping and not (self.focus and self.s.get("quiet_focus", True)):
            key = self._weather_change_key(cond, bool(weather.get("is_day", 1)))
            if key:
                self.say(t(key, city=city), duration=6, priority=0)
        self.wx_last_cond = cond
        if update.get("moved"):
            self._share_weather_place(place)

    @staticmethod
    def _weather_change_key(cond, day):
        if cond in ("drizzle", "rain", "heavy_rain"):
            return "wl_now_rain"
        if cond == "storm":
            return "wl_now_storm"
        if cond == "snow":
            return "wl_now_snow"
        if cond == "fog":
            return "wl_now_fog"
        if cond == "clear":
            return "wl_now_clear" if day else "wl_now_night"
        return "wl_now_cloudy"

    def weather_report(self) -> str:
        weather, place = self.current_weather()
        if not weather:
            return t("wl_loading")
        cond = weather_live.condition(weather.get("code"))
        return t("wl_report", icon=weather_live.icon_for(weather), cond=t("wx_" + cond),
                 city=weather_live.place_label(place) or "?", temp=round(weather.get("temp") or 0),
                 feels=round(weather.get("feels") or weather.get("temp") or 0))

    def weather_label(self) -> str:
        weather, place = self.current_weather()
        if self.s.get("weather_mode") == "off":
            return t("wl_off")
        if not weather:
            return t("wl_loading")
        return "%s %d\u00b0 %s" % (weather_live.icon_for(weather), round(weather.get("temp") or 0),
                                   weather_live.place_label(place))

    def _share_weather_place(self, place):
        """Keep my Friends profile city in step with where I really am (never un-hides a hidden city)."""
        social_ctl = self.social
        if not (social_ctl and getattr(social_ctl, "enabled", False) and self.s.get("weather_share", True)):
            return
        if not place or not place.get("name"):
            return
        me = (getattr(social_ctl, "board", None) or {}).get("me") or {}
        if me.get("loc") and not me.get("loc_visible", True):
            return
        last = self.wx_last_push
        if last and weather_live.distance_km(last, place) < 1.0 and last.get("name") == place.get("name"):
            return
        self.wx_last_push = dict(place)
        try:
            social_ctl.set_location("city", place, quiet=True)
        except TypeError:
            social_ctl.set_location("city", place)
        except Exception as exc:
            logger.debug("weather share failed: %s", exc)

    def set_weather_mode(self, mode, place=None, announce=True):
        if weather_live is None or mode not in weather_live.MODES:
            return
        if mode == "city":
            place = place or self.s.get("weather_place")
            if not place:
                self.open_place_picker()
                return
            place = {k: place.get(k) for k in ("name", "admin", "country", "lat", "lon") if place.get(k) is not None}
            self.update_setting("weather_place", place)
        self.update_setting("weather_mode", mode)
        self.wx_told_denied = False
        if self.wx_service:
            self.wx_service.configure(mode, self.s.get("weather_place"))
        if mode == "off":
            if self.wx_fx is not None:
                self.wx_fx.set_weather(None)
            if announce:
                self.say(t("wl_now_off"), duration=3, priority=1)
            return
        if announce:
            self.wx_pending_report = True
            key = {"follow": "wl_set_follow", "ip": "wl_set_ip", "city": "wl_set_city"}[mode]
            self.say(t(key, city=weather_live.place_label(place) if place else ""), duration=4, priority=1)

    def set_weather_fx(self, fx):
        if weather_live is None or fx not in weather_live.FX_MODES:
            return
        self.update_setting("weather_fx", fx)
        if self.wx_fx is not None:
            self.wx_fx.set_mode(fx)

    def refresh_weather(self):
        if self.wx_service:
            self.wx_pending_report = True
            self.wx_service.refresh_now()
            self.say(t("wl_loading"), duration=2, priority=0)

    def open_place_picker(self):
        if weather_live is None:
            return
        if self.wx_picker is not None and self.wx_picker.alive():
            try:
                self.wx_picker.win.lift()
                return
            except tk.TclError:
                pass
        labels = {"title": t("wl_pick_title"), "hint": t("wl_pick_hint"), "search": t("wx_search"),
                  "use": t("wx_pick"), "none": t("wx_no_results"), "searching": t("wl_searching"),
                  "offline": t("wl_offline"), "exact": t("wl_exact")}
        lang = self._wx_lang()
        self.wx_picker = weather_live.PlacePicker(
            self.root, labels, lambda q: weather_live.search_places(q, lang),
            lambda place: self.set_weather_mode("city", place))

    def _build_weather_menu(self, sub):
        mode = self.s.get("weather_mode", "ip")
        fx = self.s.get("weather_fx", "pet")
        sub.item(weather_live.icon_for(self.current_weather()[0]), self.weather_label(), self.refresh_weather,
                 hint=t("wl_refresh"))
        sub.section(t("wl_where"))
        sub.choice("\U0001F4CD", t("wl_follow"), mode == "follow", lambda: self.set_weather_mode("follow"))
        sub.choice("\U0001F310", t("wl_ip"), mode == "ip", lambda: self.set_weather_mode("ip"))
        place = self.s.get("weather_place") or {}
        city_label = t("wl_city") + ("  \u00b7  " + weather_live.place_label(place) if place else "")
        sub.choice("\U0001F3D9", city_label, mode == "city", lambda: self.set_weather_mode("city"))
        sub.item("\U0001F50D", t("wl_pick_other"), self.open_place_picker)
        sub.choice("\U0001F6AB", t("wl_off"), mode == "off", lambda: self.set_weather_mode("off"))
        sub.section(t("wl_fx"))
        sub.choice("\U0001F43E", t("wl_fx_pet", name=self.s["name"]), fx == "pet", lambda: self.set_weather_fx("pet"))
        sub.choice("\U0001F5A5", t("wl_fx_screen"), fx == "screen", lambda: self.set_weather_fx("screen"))
        sub.choice("\u2716", t("wl_fx_off"), fx == "off", lambda: self.set_weather_fx("off"))
        if self.social and getattr(self.social, "enabled", False):
            sub.section(t("wl_friends"))
            share = bool(self.s.get("weather_share", True))
            sub.choice("\U0001F465", t("wl_share"), share,
                       lambda: self.update_setting("weather_share", not share))

    def _song_allowed(self, force=False) -> bool:
        if not self.songs or pet_songs is None or getattr(self, "_destroyed", False):
            return False
        if force:
            return True
        if not self.s.get("sounds", True) or not self.visible or self.fs_hidden:
            return False
        return not (self.focus and self.s.get("quiet_focus", True))

    def trick_song(self) -> str:
        choice = self.s.get("trick_song", "own")
        if pet_songs is None or not pet_songs.is_choice(choice):
            return "own"
        return choice

    def _trick_song_for(self, move):
        """The song id for this move with the chosen music (None = this move has no music / music off)."""
        if pet_songs is None or not pet_songs.style_for(move) or not self._song_allowed():
            return None
        return pet_songs.resolve(self.trick_song(), self.s["species"], self.category)

    def _song_volume(self) -> float:
        try:
            return max(0, min(100, int(self.s.get("sound_volume", 60)))) / 100.0
        except (TypeError, ValueError):
            return 0.6

    def _play_song(self, song_id, style, seconds, force=False) -> bool:
        if not song_id or not style or not self._song_allowed(force):
            return False
        try:
            return self.songs.play(song_id, self.s["species"], style, seconds, self._song_volume())
        except Exception as exc:
            logger.debug("trick song failed: %s", exc)
            return False

    def _stop_song(self):
        if getattr(self, "songs", None):
            try:
                self.songs.stop()
            except Exception:
                pass

    def song_label(self, choice=None) -> str:
        choice = self.trick_song() if choice is None else choice
        if isinstance(choice, str) and choice.startswith("sp:"):
            return t("song_of", animal=species_label(choice[3:]))
        if choice == "own":
            return t("song_own", animal=species_label(self.s["species"]))
        return t("song_" + str(choice))

    def set_trick_song(self, choice):
        """Pick the music for sing / dance tricks, then show it off with a little preview."""
        if pet_songs is None or not pet_songs.is_choice(choice):
            return
        self.update_setting("trick_song", choice)
        self.say(t("song_now", song=self.song_label(choice)), duration=3, priority=1)
        if choice == "off":
            self._stop_song()
            return
        owned = self.shop.moves() if self.shop else []
        musical = [m for m in ("sing", "dance") if m in owned]
        musical += [m for m in owned if pet_songs.style_for(m) and m not in musical]
        if musical:
            self.do_trick(musical[0])
        else:
            song = pet_songs.resolve(choice, self.s["species"], self.category)
            self.set_action("cheer", 1.4)
            self._spawn("sparkle", 3)
            self._play_song(song, "dance", 6.0)
        self._prewarm_songs()

    def _prewarm_songs(self):
        """Make the songs for my musical tricks ahead of time so they start right on the beat."""
        if not self.songs or pet_songs is None or not self.shop or getattr(self, "_destroyed", False):
            return
        choice = self.trick_song()
        if choice in ("off", "random"):
            return
        species = self.s["species"]
        song = pet_songs.resolve(choice, species, self.category)
        jobs = []
        for move in self.shop.moves():
            style = pet_songs.style_for(move)
            if style:
                jobs.append((song, species, style, pet_songs.seconds_for(move, pet_shop.MOVES[move][1])))
        try:
            self.songs.prewarm(jobs, self._song_volume())
        except Exception as exc:
            logger.debug("song prewarm failed: %s", exc)

    def _end_trick(self, stop_song=True):
        if stop_song:
            self._stop_song()
        self.trick_dx = 0.0
        self.trick_lift = 0.0
        if self.trick_extra_h:
            self.H -= self.trick_extra_h
            self.trick_extra_h = 0
            try:
                self.cv.config(height=self.H)
            except tk.TclError:
                pass
            self._last_geom = None

    def on_theme_changed(self):
        """Re-open the windows that are drawn with theme colours."""
        if self.settings_win and self.settings_win.alive():
            self.settings_win.close()
            self.open_settings()
        if self.social and self.social.window and self.social.window.alive():
            tab = self.social.window.tab
            self.social.window.close()
            self.social.window = None
            self.social.open_window(tab)

    def open_hub(self, tab=None):
        if self.social:
            self.social.open_window(tab)

    def open_community(self):
        """Pips Community (v2.16): unread announcements first, otherwise #general."""
        if not self.social or social is None or not hasattr(social, "NEWS_ROOM"):
            return
        room = social.NEWS_ROOM if self.social.unread(social.NEWS_ROOM) else social.GENERAL_ROOM
        self.social.open_window("chat", room=room)

    def _voice(self, kind, force=False, species=None) -> bool:
        """Play this animal's sound for a mood / event (respects the sound settings).

        v2.21: ``species`` lets a visiting friend's pet speak with its own voice."""
        if not self.voice or getattr(self, "_destroyed", False):
            return False
        if not force:
            if not self.s.get("sounds", True) or not self.visible or self.fs_hidden:
                return False
            if self.focus and self.s.get("quiet_focus", True):
                return False
        try:
            volume = max(0, min(100, int(self.s.get("sound_volume", 60)))) / 100.0
        except (TypeError, ValueError):
            volume = 0.6
        try:
            return self.voice.play(species or self.s["species"], kind, volume)
        except Exception as exc:
            logger.debug("pet voice failed: %s", exc)
            return False

    def tickle(self):
        now = time.monotonic()
        if now < self.pout_until:
            self.set_action("angry", 0.8)
            self._voice("grumpy")
            self.say(t("hmph"), duration=1.5, priority=0)
            return
        self.tickles = [x for x in self.tickles if now - x < 15] + [now]
        if len(self.tickles) >= 7:
            self.tickles.clear()
            self.set_action("angry", 2.2)
            self._spawn("bang", 1)
            self.grumpy_until = now + 25
            self.pout_until = now + 5
            self._bump("happiness", -6)
            self._bump("bond", -1)
            self._voice("angry")
            self.say(random.choice(lines("angry")), duration=3, priority=1)
            return
        self.set_action("tickle", 1.3)
        self._voice("tickle")
        self._bump("happiness", 3 if len(self.tickles) <= 3 else 1)
        self._bump("bond", 0.3)
        self.st["tickles_total"] = self.st.get("tickles_total", 0) + 1
        self._spawn(random.choice(("note", "sparkle")), 2)
        if self.category == "water":
            self._spawn("bubble", 2)
        if random.random() < 0.6:
            self.say(random.choice(lines("tickle")), duration=2.2, priority=0)

    def _on_hover_motion(self, event):
        if self.dragging or (event.state & 0x0100):
            return
        if (event.x_root, event.y_root) != self._hover_xy:
            self._hover_real = True         # the mouse moved (not just the pet flying under it)
        now = time.monotonic()
        self.hover_hist.append((now, event.x_root))
        self.hover_hist = [(t0, x) for t0, x in self.hover_hist if now - t0 < 1.4]
        if now < self.pet_cooldown or len(self.hover_hist) < 4:
            return
        reversals = 0
        direction = 0
        anchor = self.hover_hist[0][1]
        for _t, x in self.hover_hist[1:]:
            delta = x - anchor
            if abs(delta) >= 6:
                d = 1 if delta > 0 else -1
                if direction and d != direction:
                    reversals += 1
                direction = d
                anchor = x
        if reversals >= 3:
            self.hover_hist.clear()
            self.pet()

    def _on_enter(self, event):
        self.hovering = True
        self._hover_real = False            # v2.19: set once the mouse really moves over the pet
        self._hover_xy = (event.x_root, event.y_root)

    def _on_leave(self, _event):
        self.hovering = False
        self._hover_real = False
        self.hover_hist.clear()

    def pet(self):
        self._touch()
        now = time.monotonic()
        self.pet_cooldown = now + 3.0
        self.st["pets_total"] = self.st.get("pets_total", 0) + 1
        if self.sleeping:
            self._spawn("heart", 1)
            self._bump("happiness", 2)
            return
        self.set_action("petted", 1.8)
        self._voice("pet")
        self._spawn("heart", 3)
        self._tour_event("pet")
        self._bump("happiness", 5)
        self._bump("bond", 0.5)
        self._grow("care")
        self.grumpy_until = min(self.grumpy_until, now + 3)
        if random.random() < 0.4:
            self.say(random.choice(lines("pet")), duration=2.2, priority=0)

    def feed(self):
        self._touch()
        if self.sleeping:
            self.wake("poke", quiet=True)
        now = time.time()
        recent = [x for x in self.st.get("snacks", []) if now - x < 3600]
        if self.st["fullness"] > 90 or len(recent) >= 4:
            self.set_action("refuse", 1.2)
            self._voice("grumpy")
            self._bump("happiness", -1)
            self.say(t("too_full"), duration=2.5, priority=1)
            return
        line = random.choice(lines("snack"))
        recent.append(now)
        self.st["snacks"] = recent
        self.st["snacks_total"] = self.st.get("snacks_total", 0) + 1
        self._bump("fullness", 25)
        self._bump("happiness", 8)
        self._bump("energy", 5)
        self._bump("bond", 1)
        self._grow("care")
        self.set_action("eat", 2.0)
        self._voice("eat")
        self._spawn("crumb", 5)
        self.say(line, duration=2.5, priority=1)

    def go_to_sleep(self, reason="manual"):
        if self.sleeping:
            return
        if self.leap is not None or self.falling or self.dragging:
            self._pending_sleep = reason            # nap once we've landed
            return
        if self.category == "air" and self.mode == "fly":
            self._pending_sleep = reason            # flyers glide down to a perch first
            if self.flight is None or not self.flight.get("perch"):
                self._perch_flight()
            return
        self._pending_sleep = None
        self.walking = False
        self.sleep_reason = reason
        self.set_action("yawn_to_sleep", 1.4)
        if reason != "away":
            self._voice("tired")
        if reason == "tired":
            self.say(random.choice(lines("tired_sleep")), duration=2.5, priority=0)
        elif reason == "manual":
            self.say(t("goodnight"), duration=2.0, priority=0)

    def wake(self, reason="poke", quiet=False):
        if not self.sleeping and self.action != "yawn_to_sleep":
            return
        self.sleeping = False
        self.action = None
        now = time.monotonic()
        self.next_walk_at = now + random.uniform(3, 6)
        if reason == "back":
            self.set_action("cheer", 1.4)
            self._voice("greet")
            self._bump("happiness", 3)
            self.say(random.choice(lines("back")), duration=3, priority=1)
        elif reason == "rested":
            self.set_action("wake", 1.3)
            self._voice("wake")
            self.say(random.choice(lines("rested")), duration=2.5, priority=0)
        elif reason == "poke" and self.st["energy"] < 30:
            self.set_action("startle", 0.6)
            self._spawn("bang", 1)
            self.grumpy_until = now + 15
            self._bump("happiness", -3)
            if not quiet:
                self._voice("grumpy")
                self.say(random.choice(lines("grumpy_wake")), duration=2.5, priority=0)
        else:
            self.set_action("wake", 1.2)
            if not quiet:
                self._voice("wake")
                self.say(random.choice(lines("wake")), duration=2.2, priority=0)

    # ------------------------------------------------- v2.18 Tamagotchi-style care (tama.py)
    def gender_symbol(self) -> str:
        return tama.gender_symbol(self.s.get("gender")) if tama else ""

    def stage_text(self) -> str:
        """e.g. "\U0001f331 Child" ("" without tama)."""
        if self.growth is None:
            return ""
        stage = self.growth.stage
        return f"{tama.STAGE_ICON.get(stage, '')} {t('stage_' + stage)}"

    def growth_text(self) -> str:
        """e.g. "\U0001f331 Child \u00b7 2.5 more to grow into a Teen"."""
        if self.growth is None:
            return ""
        nxt, need, _fraction = self.growth.progress()
        text = self.stage_text()
        if nxt:
            amount = ("%.1f" % need).rstrip("0").rstrip(".")
            text += " \u00b7 " + t("tama_to_next", n=amount, stage=t("stage_" + nxt))
        return text

    def _look_changed(self):
        """Gender / life stage changed: let friends see it soon."""
        social_hub = getattr(self, "social", None)
        if social_hub is not None and hasattr(social_hub, "look_changed"):
            try:
                social_hub.look_changed()
            except Exception:
                logger.exception("look sync failed")

    def _grow(self, reason, announce_ms=2800):
        """A bit of care -> growth points; announces a new life stage."""
        if self.growth is None:
            return None
        try:
            new_stage = self.growth.add(reason)
        except Exception:
            logger.exception("growth failed")
            return None
        if new_stage:
            self._grew_up(new_stage, announce_ms)
        return new_stage

    def _apply_stage_size(self):
        floor = self.floor_y
        self._resize_canvas()
        self.floor_y = floor
        self._apply_position(force=True)

    def _grew_up(self, stage, announce_ms=2800):
        logger.info("Pet grew up: %s", stage)
        self._apply_stage_size()
        self.set_action("cheer", 2.2)
        self._spawn("sparkle", 8)
        self._spawn("heart", 3)
        self._voice("happy")
        self.st.setdefault("grew", {})[stage] = datetime.now().strftime("%Y-%m-%d")
        self.save_state()
        self._look_changed()
        text = t("tama_grew_" + stage, name=self.s["name"])
        self.root.after(announce_ms, lambda: self.say(text, duration=9, priority=2))
        if self.settings_win:
            self.settings_win.refresh_controls()

    def _offer_regrow(self):
        """Pets from before v2.18 are grown up already: ask (a few times at most) whether to raise them again."""
        self._regrow_job = None
        if self.growth is None or not self.st.get("regrow_offer") or getattr(self, "_destroyed", False):
            return
        busy = (not self.visible or self.fs_hidden or self.sleeping or self.focus or self.dragging
                or (self.tour and self.tour.active) or self.bubble.visible or self.game is not None)
        if busy:
            self._regrow_job = self.root.after(10 * 60 * 1000, self._offer_regrow)
            return
        asked = int(self.st.get("regrow_asked", 0) or 0) + 1
        self.st["regrow_asked"] = asked
        if asked >= 3:
            self.st.pop("regrow_offer", None)       # asked three times: don't nag any more
        self.save_state()
        self.set_action("hop", 0.6)
        self.say(t("tama_regrow_q", name=self.s["name"]),
                 actions=[(t("tama_regrow_yes"), self.regrow), (t("tama_regrow_no"), self._regrow_no)],
                 duration=45, priority=2)

    def regrow(self):
        """Raise the pet again from a baby (the user asked for it). Coins, items and stats stay."""
        if self.growth is None:
            return
        self.growth.restart()
        self.st.pop("regrow_asked", None)
        self._apply_stage_size()
        self.set_action("love", 2.0)
        self._spawn("sparkle", 8)
        self._voice("happy")
        self.save_state()
        self._look_changed()
        self.say(t("tama_regrow_done", name=self.s["name"]), duration=10, priority=2)
        if self.settings_win:
            self.settings_win.refresh_controls()

    def _regrow_no(self):
        self.st.pop("regrow_offer", None)
        self.st.pop("regrow_asked", None)
        self.save_state()
        self.say(t("tama_regrow_kept"), duration=6, priority=1)

    def _health_tick(self, now, idle, away):
        """Sick days: a little cold after 2.5 hours without a 5-minute break (tama.Health)."""
        health = self.health
        event = health.tick(time.time(), idle, enabled=bool(self.s.get("sick_days", True)))
        if event == "sick":
            logger.info("Pet caught a little cold (no break for %d minutes)", tama.SICK_AFTER_S // 60)
            self._sick_note_due = True
            self._sick_nag_at = 0.0
            self._next_sneeze = now + random.uniform(8, 20)
            self.save_state()
        elif event == "cured_break":
            logger.info("Pet's cold was cured by a break")
            self._cured_note = "break"
            self._sick_note_due = False
            self._sick_nag_at = 0.0
            self.save_state()
        showing = self.visible and not self.fs_hidden and not away
        if not health.sick:
            if self._cured_note == "break" and idle < 5 and showing and not self.sleeping:
                self._cured_note = ""
                self.set_action("cheer", 1.6)
                self._spawn("sparkle", 6)
                self._bump("happiness", 6)
                self.say(t("tama_cured_break", name=self.s["name"]), duration=7, priority=2)
            return
        if not showing or self.sleeping or self.game is not None:
            return
        quiet = self.focus or self.bubble.visible or self.dragging
        if self._sick_note_due and not quiet and self.action is None:
            self._sick_note_due = False
            self._sick_nag_at = now + 30 * 60
            self._next_sneeze = now + random.uniform(*tama.SNEEZE_EVERY_S)
            self.set_action("sneeze", 1.3)
            self._spawn("drop", 3)
            self.say(t("tama_sick", name=self.s["name"]), actions=self._sick_actions(), duration=25, priority=2)
            return
        if self._sick_nag_at and now >= self._sick_nag_at and not quiet:
            self._sick_nag_at = now + 30 * 60
            self.say(t("tama_sick_again"), actions=self._sick_actions(), duration=15, priority=1)
            return
        if now >= self._next_sneeze and self.action is None and not self.dragging:
            self._next_sneeze = now + random.uniform(*tama.SNEEZE_EVERY_S)
            self.set_action("sneeze", 1.3)
            self._spawn("drop", 2)
            if not quiet and random.random() < 0.5:
                self.say(random.choice(lines("sneeze")), duration=1.8, priority=0)

    def _sick_actions(self):
        return [("\U0001f48a " + t("tama_medicine"), self.give_medicine),
                ("\u2615 " + t("tama_break_btn"), self._take_break)]

    def give_medicine(self):
        """Medicine cures the cold right away (but the 2.5-hour work stretch starts over)."""
        self._touch()
        if self.health is None:
            return
        if self.sleeping:
            self.wake("poke", quiet=True)
        if not self.health.cure("medicine"):
            self.set_action("hop", 0.55)
            self.say(t("tama_not_sick"), duration=3, priority=1)
            return
        self._cured_note = ""
        self._sick_note_due = False
        self._sick_nag_at = 0.0
        self.st["medicine_total"] = int(self.st.get("medicine_total", 0) or 0) + 1
        self.set_action("sip", 1.6)
        self._spawn("sparkle", 5)
        self._bump("happiness", 3)
        self.save_state()
        self.say(t("tama_medicine_done"), duration=7, priority=2)

    def _take_break(self):
        self._touch()
        self.set_action("stretch", 1.3)
        self.say(t("tama_break_ok", name=self.s["name"]), duration=8, priority=2)

    def play_game(self):
        """Left or Right? - the classic Tamagotchi guessing game: 5 rounds, guess 3 right to win."""
        if tama is None:
            return
        self._touch()
        if self.sleeping:
            self.wake("poke", quiet=True)
        if self.health is not None and self.health.sick:
            self.say(t("tama_game_sick"), actions=self._sick_actions(), duration=10, priority=2)
            return
        if self.st.get("energy", 50) < 12:
            self.say(t("tama_game_tired"), duration=4, priority=1)
            return
        self.game = tama.LeftRight()
        self.walking = False
        self._game_round()

    def play_minigame(self, game="catch"):
        """v2.24: open one of the arcade minigames (same health / energy checks as Left or Right?)."""
        if minigames is None:
            return None
        self._touch()
        if self.sleeping:
            self.wake("poke", quiet=True)
        if self.health is not None and self.health.sick:
            self.say(t("tama_game_sick"), actions=self._sick_actions(), duration=10, priority=2)
            return None
        if self.st.get("energy", 50) < 8:
            self.say(t("tama_game_tired"), duration=4, priority=1)
            return None
        self.set_action("hop", 0.5)
        return minigames.open_game(self, game)

    def _game_round(self):
        game = self.game
        if game is None:
            return
        if game.done:
            self._game_over()
            return
        self._game_until = time.monotonic() + 40
        text = t("tama_game_q", n=game.round + 1, total=tama.GAME_ROUNDS, score=game.score)
        actions = [("\u25c0 " + t("tama_left"), lambda: self._game_guess(-1)),
                   (t("tama_right") + " \u25b6", lambda: self._game_guess(1))]
        if self.say(text, actions=actions, duration=40, priority=2):
            self.set_action("think", 40)
        else:
            self.game = None

    def _game_guess(self, side):
        game = self.game
        if game is None or game.done:
            return
        correct, pet_side = game.guess(side)
        self.facing = pet_side
        self.set_action("peek", 1.3)
        self._game_until = time.monotonic() + 40
        where = t("tama_side_left") if pet_side < 0 else t("tama_side_right")
        if correct:
            self._spawn("sparkle", 2)
        key = "tama_game_right" if correct else "tama_game_wrong"
        self.say(t(key, side=where, score=game.score, n=game.round), duration=2.0, priority=2)
        self.root.after(1500, self._game_round)

    def _game_over(self):
        game, self.game = self.game, None
        if game is None:
            return
        self.st["games_played"] = int(self.st.get("games_played", 0) or 0) + 1
        self._bump("energy", -3)
        if game.won:
            self.st["games_won"] = int(self.st.get("games_won", 0) or 0) + 1
            coins = tama.game_reward(self.st, True)
            if coins and self.shop is not None:
                try:
                    self.shop.add_gift_coins(coins)
                except Exception:
                    logger.exception("game coins failed")
                    coins = 0
            elif coins:
                coins = 0
            self._bump("happiness", 12)
            self._bump("bond", 2)
            self.set_action("love", 2.2)
            self._spawn("heart", 5)
            self._voice("happy")
            text = t("tama_game_won", score=game.score, total=tama.GAME_ROUNDS)
            if coins:
                text += " " + t("tama_game_coins", n=coins)
        else:
            self._bump("happiness", 4)
            self.set_action("hop", 0.6)
            text = t("tama_game_lost", score=game.score, total=tama.GAME_ROUNDS)
        self.save_state()
        self.say(text, actions=[(t("tama_again"), self.play_game)], duration=9, priority=2)
        self._grow("game", announce_ms=4500)

    def _call(self, name, *args):
        fn = self.cb.get(name)
        if fn:
            try:
                return fn(*args)
            except Exception as exc:
                logger.warning("Pet callback %s failed: %s", name, exc)
        return None

    # ------------------------------------------------------------ public API
    def say(self, text, actions=None, duration=5.0, priority=1, on_click=None, sticky=False,
            on_close=None) -> bool:
        if not self.visible or self.fs_hidden or getattr(self, "_destroyed", False):
            return False
        if self.tour and self.tour.active and priority < 3:
            return False
        try:
            return self.bubble.show(text, actions=actions, duration=duration, priority=priority,
                                    on_click=on_click, sticky=sticky, on_close=on_close)
        except tk.TclError:
            return False

    notify = say

    def set_focus(self, focused: bool):
        self.focus = bool(focused)

    def react(self, event: str, **kw):
        if event == "focus_start":
            if self.sleeping:
                self.wake("poke", quiet=True)
            self.set_action("hop", 0.55)
            self.say(random.choice(lines("focus")), duration=2.5, priority=1)
        elif event == "pomodoro_done":
            self.set_action("love", 2.0)
            self._bump("happiness", 8)
            self._bump("bond", 1.5)
            self._spawn("heart", 4)
            if kw.get("announce"):
                n = kw.get("count")
                extra = t("pomo_today", n=n) if n else ""
                self.say(t("pomo_done", extra=extra), duration=6, priority=2)
            if self.social:
                # give the app a moment to record the finished session first
                self.root.after(2500, self.social.on_pomodoro)
            self._grow("pomodoro", announce_ms=7000)
        elif event == "distracted":
            self.set_action("worried", 2.5)
            self._bump("happiness", -3)
            self.say(t("distracted", app=kw.get("app") or "?"), duration=6, priority=1)
        elif event == "goal":
            self.set_action("love", 3.0)
            self._spawn("heart", 6)
            self._bump("happiness", 15)
            self._bump("bond", 3)
            self.say(t("goal_reached"), duration=8, priority=2)
        elif event == "card_hidden":
            self.say(t("card_hidden"), duration=3.5, priority=0)
        elif event == "language_changed":
            self.set_action("hop", 0.55)
            self.say(t("lang_changed"), duration=3, priority=1)

    def show_reminder(self, payload: dict, on_done=None, on_snooze=None) -> bool:
        if not self.visible or self.fs_hidden:
            return False
        if self.sleeping:
            self.wake("poke", quiet=True)
        kind = payload.get("kind", "break")
        title = payload.get("title", "")
        tip = payload.get("tip", "")
        text = "%s\n%s" % (title, tip) if tip else title
        if kind == "break":
            actions = [(t("okay_btn"), on_done)]
            if on_snooze:
                actions.append((t("snooze_btn"), on_snooze))
            self.set_action("stretch", 1.3)
            duration = 30
        elif kind == "hydration":
            actions = [(t("done_btn"), on_done)]
            self.set_action("sip", 1.6)
            duration = 20
        else:
            actions = [(t("done_btn"), on_done)]
            self.set_action("look", 1.6)
            duration = 20
        return self.bubble.show(text, actions=actions, duration=duration, priority=2)

    def is_visible(self) -> bool:
        return self.visible and not self.fs_hidden

    # ---- v2.25: while a minigame is open the pet leaves the desktop and plays inside the game ----
    @property
    def in_game(self) -> bool:
        return bool(getattr(self, "_game_windows", None))

    def enter_game(self, window):
        """A minigame window opened: hide the desktop pet (it is drawn inside the game instead)."""
        self._game_windows.add(window)
        if self.fs_hidden:
            return
        self.fs_hidden = True
        self._stop_song()
        try:
            self.bubble.hide()
            self.win.withdraw()
        except tk.TclError:
            pass

    def leave_game(self, window):
        """The minigame window closed: the pet comes back to the desktop (unless something else hides it)."""
        self._game_windows.discard(window)
        if self.in_game or not self.fs_hidden or not self.visible:
            return
        if self._fullscreen_hides():
            return                      # a fullscreen app is in front: the normal check brings it back later
        self.fs_hidden = False
        try:
            self.win.deiconify()
            self._apply_position(force=True)
            self.set_action("hop", 0.6)
        except tk.TclError:
            pass

    # ---- find me (v2.17): tray icon, Ctrl+Alt+P and opening Pips again all end up here ----
    def summon(self, text=None):
        """Bring the pet back no matter what: un-hide it, wake it up, show it even over a fullscreen
        window (while that window stays in front) and walk it onto the screen the mouse is on."""
        self._fs_override_hwnd = foreground_window()
        self._game_windows.clear()
        if not self.visible:
            self.set_visible(True)
        if self.fs_hidden:
            self.fs_hidden = False
            try:
                self.win.deiconify()
            except tk.TclError:
                pass
        if self.sleeping:
            self.wake("poke", quiet=True)
        self.come_to_cursor_screen()
        set_topmost(hwnd_of(self.win))
        self.set_action("hop", 0.6)
        self._spawn("sparkle", 6)
        if text:
            self.say(text, duration=5, priority=2)

    def come_to_cursor_screen(self) -> bool:
        """Park on the taskbar of the monitor the mouse is on - unless already visible there."""
        cx, cy = cursor_pos(self.root)
        mon, work = monitor_rects(cx, cy, self.root)
        body_y = (self.floor_y or work[3]) - self.H / 2
        if self.px is not None and point_on_a_monitor(self.px, body_y):
            if monitor_rects(self.px, body_y, self.root)[0] == mon:
                return False
        self._park_on(work, cx)
        return True

    def _park_on(self, work, x):
        """Put the pet on the taskbar edge of a monitor's work area (x kept inside it)."""
        margin = max(40, self.W // 2 + 10)
        self.px = float(min(max(x, work[0] + margin), work[2] - margin))
        self.falling = False
        self.walking = False
        self.leap = None
        self.flight = None
        self.platform = None
        self.mode = "taskbar"
        self.floor_kind = "top"
        self.floor_y = work[3]
        self.floor_y = self._floor_for(self.px)
        self._apply_position(force=True)

    def _watch_offscreen(self, now):
        """Walk back by itself if the pet ends up on no monitor (unplugged screen, resolution change)."""
        if self.dragging or self.px is None or self.floor_y is None:
            return
        body_y = self.floor_y - self.H / 2
        if point_on_a_monitor(self.px, body_y):
            self._offscreen_since = 0.0
            return
        if not self._offscreen_since:
            self._offscreen_since = now
            return
        if now - self._offscreen_since < 5.0:
            return
        self._offscreen_since = 0.0
        _mon, work = monitor_rects(self.px, body_y, self.root)
        self._park_on(work, self.px)
        self.set_action("hop", 0.6)
        self.say(t("offscreen_back"), duration=5, priority=1)

    def _fullscreen_hides(self) -> bool:
        if not self.s.get("hide_in_fullscreen", True):
            return False
        if self._fs_override_hwnd:
            if foreground_window() == self._fs_override_hwnd:
                return False
            self._fs_override_hwnd = None
        return foreground_is_fullscreen((hwnd_of(self.win), self.bubble.hwnd))

    def start_tour(self):
        if not self.visible:
            self.set_visible(True)
        if self.sleeping:
            self.wake("poke", quiet=True)
        if self.tour and self.tour.active:
            self.tour.show()
            return
        self.tour = Tour(self)
        self.tour.start()

    def _tour_event(self, kind):
        if self.tour and self.tour.active:
            self.tour.event(kind)

    def dismiss_chatter(self):
        """Hide a low-priority bubble (e.g. when the Pips card opens over it)."""
        if self.bubble.visible and self.bubble.priority < 2:
            self.bubble.hide()

    def set_visible(self, visible: bool):
        self.visible = bool(visible)
        self.update_setting("enabled", self.visible)

    def set_species(self, species: str, recolor: bool = True):
        if species not in SPECIES:
            return
        old_category = self.category
        self.s["species"] = species
        if recolor:
            self.s["color"] = SPECIES[species]["color"]
        if self.category != old_category:
            self._category_changed()
        self.set_action("cheer", 1.2)
        self._spawn("sparkle", 4)
        self.update_setting("species", species)
        self._voice("happy")
        self._prewarm_songs()

    def update_setting(self, key, value):
        self.s[key] = value
        if key == "size":
            floor = self.floor_y
            self._resize_canvas()
            self.floor_y = floor
            self._apply_position(force=True)
        elif key == "enabled":
            self.visible = bool(value)
            if self.visible:
                self.fs_hidden = False
                self.win.deiconify()
                self._apply_position(force=True)
            else:
                self._stop_song()
                self.win.withdraw()
                self.bubble.hide()
        elif key == "sounds" and not value:
            self._stop_song()
        elif key == "walk_on_taskbar" and not value:
            self.walking = False
            if self.flight is not None and not self.flight.get("perch"):
                self.flight = None          # stop flying around (a glide down to a perch still finishes)
        elif key == "sounds" and value:
            self._voice("happy")
        elif key == "sick_days" and not value and self.health is not None:
            self.health.cure("off")
        elif key == "gender":
            self.set_action("cheer", 1.2)
            self._spawn("sparkle", 4)
            self._look_changed()
        self._call("save_settings", dict(self.s))
        if self.settings_win:
            self.settings_win.refresh_controls()

    def refresh_language(self):
        """Called after the UI language changes: rebuild translated windows."""
        if self.settings_win and self.settings_win.alive():
            self.settings_win.close()
            self.open_settings()

    def go_to_taskbar(self):
        """Menu: back down to the taskbar (jump off a window / glide down / fall)."""
        self.floor_kind = "top"
        if self.dragging or self.leap is not None or self.falling:
            return
        self._touch()
        if self.sleeping:
            self.wake("poke", quiet=True)
        if self.mode == "fly":
            self._perch_flight(prefer_taskbar=True)
        elif self.mode == "platform" and self.category == "air":
            self.mode = "fly"
            self.platform = None
            self._perch_flight(prefer_taskbar=True)
        elif self.mode == "platform" and roam is not None:
            mon, _work = monitor_rects(self.px, self.floor_y - 10, self.root)
            lo, hi = mon[0] + self.W / 2 + 2, mon[2] - self.W / 2 - 2
            x = max(lo, min(hi, self.px + self.facing * random.uniform(40, 140)))
            y = self._floor_for(x)
            self._start_leap(x, y, roam.jump_apex(self.px, self.floor_y, x, y), None, kind="jump", delay=0.2)
        elif self.mode != "taskbar":
            self._start_fall(to_taskbar=True)

    def greet_visitor(self):
        """v2.20: a friend's pet is coming over - come down to the taskbar to meet it."""
        if self.dragging or self.mode not in ("fly", "platform"):
            return
        try:
            self.go_to_taskbar()
        except Exception:
            logger.debug("greet_visitor: could not come down", exc_info=True)

    def float_free(self):
        """Menu: this animal's special move - fly around (air), splash leap (water), jump onto a window (land)."""
        if self.dragging or self.leap is not None or self.falling:
            return
        self._touch()
        if self.sleeping:
            self.wake("poke", quiet=True)
        category = self.category
        if category == "air":
            self.take_off()
        elif category == "water":
            if not self.splash_leap() and self.mode != "taskbar":
                self.go_to_taskbar()
        elif not self.jump_somewhere(windows_only=True, max_rise=100000.0, max_dx=100000.0):
            self.say(random.choice(lines("no_platform")), duration=3.5, priority=1)

    def _category_changed(self):
        """Switched to an animal that moves differently (v2.19)."""
        self.flight = None
        self.leap = None
        category = self.category
        if category == "air":
            if self.falling or self.mode not in ("taskbar", "platform"):
                self.falling = False
                self.mode = "fly"
        elif category == "water":
            if self.mode != "taskbar" and not self.falling:
                self._start_fall(to_taskbar=True)
            elif self.falling:
                self._fall_platforms = []       # water animals always dive back into the water
        elif self.mode in ("fly", "free", "leap"):
            self._start_fall()

    def open_settings(self):
        if self.settings_win and self.settings_win.alive():
            self.settings_win.win.deiconify()
            self.settings_win.win.lift()
            return
        self.settings_win = PetSettingsWindow(self)

    def _greet(self):
        if not self.st.get("tour_done"):
            self.start_tour()
            return
        hour = datetime.now().hour
        if 5 <= hour < 12:
            hello = t("greet_morning")
        elif 12 <= hour < 18:
            hello = t("greet_afternoon")
        elif 18 <= hour < 23:
            hello = t("greet_evening")
        else:
            hello = t("greet_late")
        if self._missed_you:
            hello = t("missed_you") + hello
            self.set_action("cheer", 1.4)
        else:
            self.set_action("hop", 0.55)
        self._voice("greet")
        if self._first_run:
            hello = t("first_hi", name=self.s["name"])
        elif not self._call("is_window_open"):
            hello += t("click_to_open")
        if self._cured_note == "rest":
            self._cured_note = ""
            hello = t("tama_cured_rest") + "\n" + hello
        self.say(hello, duration=6, priority=1, on_click=lambda: self._call("toggle_window"))
        if self.growth is not None and self.st.get("regrow_offer"):
            self._regrow_job = self.root.after(12000, self._offer_regrow)

    def destroy(self):
        self._stop_song()
        if getattr(self, "wx_service", None):
            self.wx_service.stop()
        if getattr(self, "wx_fx", None):
            self.wx_fx.destroy()
        self._destroyed = True
        for job in (getattr(self, "_greet_job", None), getattr(self, "_regrow_job", None)):
            if job:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._cancel_open()
        self.save_state()
        if getattr(self, "social", None):
            self.social.close()
        if self.settings_win:
            self.settings_win.close()
        self.bubble.destroy()
        for fx in self._fx:
            fx.destroy()
        self._fx = []
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ menu
    def _show_menu(self, event):
        self._touch()
        try:
            import fancy_menu
            self._show_fancy_menu(event, fancy_menu)
            self._tour_event("menu")
            return
        except Exception as exc:
            logger.warning("Fancy menu failed, using the classic menu: %s", exc)
        self._show_native_menu(event)

    def _show_fancy_menu(self, event, fm):
        name = self.s["name"]
        open_now = bool(self._call("is_window_open"))
        update = self._call("update_info") if self.cb.get("update_info") else None
        menu = fm.FancyMenu(self.win, accent=self.color)
        st = self.st
        subtitle = species_label(self.s["species"])
        if self.growth is not None:
            subtitle += " \u00b7 " + self.stage_text()
        subtitle += " \u00b7 " + self.mood_label()
        gender = self.s.get("gender", "") if tama else ""
        menu.header(
            name,
            subtitle,
            avatar=(name[:1] or "P").upper(),
            stats=[
                ("\u2665", st.get("happiness", 0), "#e0557a"),
                ("\u26A1", st.get("energy", 0), "#e2a72e"),
                ("\U0001F356", st.get("fullness", 0), "#e07b39"),
            ],
            badge=(tama.gender_symbol(gender), tama.gender_color(gender)) if gender else None,
        )
        if update:
            menu.banner(
                t("fm_upd_title", v=update.get("version", "")),
                t("fm_upd_sub"),
                lambda: self._call("show_update"),
                icon="\U0001F381",
                badge=t("fm_new"),
            )

        # v2.27.2: one row of everyday actions; everything else is grouped below.
        if self.health is not None and self.health.sick:
            menu.banner(
                t("fm_sick_title", name=name),
                t("fm_sick_sub"),
                self.give_medicine,
                icon="\U0001F48A",
                badge=t("fm_medicine"),
            )
        tiles = [("\u23F1", t("fm_focus"), lambda: self._call("toggle_window"))]
        tiles.append(("\U0001F36A", t("fm_snack"), self.feed))
        tiles.append(("\U0001F49E", t("fm_pat"), self.pet))
        if minigames is not None:
            tiles.append(("\U0001F3AE", t("fm_play"), lambda: self.play_minigame()))
        elif tama is not None:
            tiles.append(("\U0001F3AE", t("fm_play"), self.play_game))
        menu.tiles(tiles)
        if self.cb.get("open_token_board"):
            board_label = self.cb["token_board_label"]() if self.cb.get("token_board_label") else "Token Board"
            board_hint = self.cb["token_board_hint"]() if self.cb.get("token_board_hint") else ""
            menu.item("\u26A1", board_label, lambda: self._call("open_token_board"), hint=board_hint)

        def build_progress(sub):
            sub.item("\U0001F4CA", t("fm_stats"), lambda: self._call("open_dashboard"))
            if self.cb.get("open_focus_trail"):
                sub.item("\U0001F3C3", t("mg_trail"), lambda: self._call("open_focus_trail"))
        menu.submenu("\U0001F4C8", t("fm_progress"), build_progress,
                     hint=t("fm_progress_hint") if self.cb.get("open_focus_trail") else "", width=230)

        social_ok = self.social is not None and social is not None and getattr(self.social, "enabled", False)
        community_unread = 0
        if social_ok and hasattr(social, "COMMUNITY_ROOMS"):
            community_unread = sum(self.social.unread(room) for room in social.COMMUNITY_ROOMS)
        targets = self.social.nudge_targets() if social_ok else []
        if self.social:
            def build_nudge_friends(sub, people=targets[:14]):
                for friend in people:
                    code = friend.get("code", "")
                    presence = friend.get("presence") or "offline"
                    dot = "\u25CF" if presence == "online" else "\u25CB"
                    if friend.get("nudges") is False:
                        sub.item("\U0001F515", friend.get("name", "?"),
                                 lambda f=friend: self.say(t("nudge_err_off_name", name=f.get("name", "?")),
                                                           duration=3, priority=1),
                                 hint=t("nudge_off_short"))
                        continue

                    def build_kinds(kinds_menu, c=code):
                        for kind in social.NUDGES:
                            kinds_menu.item(social.NUDGE_SYMBOL[kind], t("nudge_kind_" + kind),
                                            lambda k=kind, cc=c: self.social.nudge(cc, k),
                                            hint=t("nudge_hint_" + kind))
                    sub.submenu(dot, friend.get("name", "?"), build_kinds, hint=t("st_" + presence), width=250)

            def build_friends(sub):
                sub.item("\U0001F465", t("fm_friends"), lambda: self.open_hub())
                if social_ok and hasattr(social, "COMMUNITY_ROOMS"):
                    sub.item("\U0001F310", t("fm_community"), self.open_community, hint=t("fm_community_hint"),
                             badge=str(community_unread) if community_unread else None)
                if self.shop:
                    sub.item("\U0001F6CD", t("fm_shop"), lambda: self.open_hub("shop"))
                if targets:
                    online = sum(1 for f in targets if f.get("presence") == "online")
                    sub.submenu(social.NUDGE_SYMBOL["poke"], t("nudge_menu"), build_nudge_friends,
                                hint=t("nudge_online", n=online) if online else "", width=240)
            menu.submenu("\U0001F465", t("fm_friends"), build_friends,
                         badge=str(community_unread) if community_unread else None, width=250)

        if minigames is not None:
            def build_games(sub):
                if social_ok:
                    sub.item("\U0001F465", t("mp_menu_friends"), lambda: self.open_hub("games"),
                             hint=str(len(self.social.mp_invites)) if getattr(self.social, "mp_invites", None) else "")
                    sub.item("\u2694", t("mg_duel"), lambda: self.open_hub("arena"))
                for key in minigames.GAMES:
                    best = minigames.best_score(self.st, key)
                    sub.item(minigames.ICONS[key], t("mg_" + key), lambda k=key: self.play_minigame(k),
                             hint=("\u2605 " + str(best)) if best else "")
                if tama is not None:
                    sub.item("\u2194", t("mg_leftright"), self.play_game)
            menu.submenu("\U0001F3B2", t("p_games"), build_games,
                         hint=str(len(minigames.GAMES) + (1 if tama else 0) + (2 if social_ok else 0)), width=240)

        moves = self.shop.moves() if self.shop else []
        if moves:
            def build_tricks(sub):
                for move in moves:
                    note = " \u266A" if pet_songs is not None and pet_songs.style_for(move) else ""
                    sub.item("\u2728", t("mv_" + move).split(":")[0] + note, lambda m=move: self.do_trick(m))
                if pet_songs is not None and self.songs:
                    sub.section(t("song_section"))
                    sub.submenu("\U0001F3B5", t("song_menu"), self._build_song_menu,
                                hint=self.song_label(), width=260)
            menu.submenu("\U0001F3AA", t("p_tricks"), build_tricks, hint=str(len(moves)))
        move = self._move_menu_entry()
        if move:
            menu.item(*move)
        if self.sleeping:
            menu.item("\u2600", t("fm_wake"), lambda: self.wake("poke"))
        else:
            menu.item("\U0001F319", t("fm_sleep"), lambda: self.go_to_sleep("manual"))

        menu.section(t("fm_customize"))

        def build_animals(sub):
            for category in CATEGORIES:
                def build_category(cat_menu, category=category):
                    for sp in SPECIES_BY_CATEGORY[category]:
                        cat_menu.choice("\U0001F43E", species_label(sp), sp == self.s["species"],
                                        lambda v=sp: self.set_species(v))
                current_in = self.s["species"] in SPECIES_BY_CATEGORY[category]
                sub.submenu("\u25CF" if current_in else "\u25CB", t("cat_" + category), build_category,
                            hint=str(len(SPECIES_BY_CATEGORY[category])))
        menu.submenu("\U0001F43E", t("p_animal"), build_animals, hint=species_label(self.s["species"]))
        if weather_live is not None and getattr(self, "wx_service", None):
            menu.submenu("\U0001F326", t("wl_menu"), self._build_weather_menu, hint=self.weather_label(), width=300)

        def build_colors(sub):
            sub.swatches([(value, t("col_" + label)) for label, value in COLOR_PRESETS],
                         self.s["color"], lambda v: self.update_setting("color", v))
            sub.separator()
            sub.item("\U0001F3A8", t("fm_custom_colour"), self._pick_custom_color)
        menu.submenu("\U0001F3A8", t("p_colour"), build_colors, width=268)

        menu.segmented("\u2195", t("p_size"), [(v, t("size_" + v)) for v in ("S", "M", "L")],
                       self.s["size"], lambda v: self.update_setting("size", v))

        click_options = (("tickle_hub", "p_click_hub"), ("tickle_open", "p_click_open"),
                         ("tickle_only", "p_click_only"), ("tickle_never", "p_click_never"))

        def build_click(sub):
            for value, key in click_options:
                sub.choice("\U0001F446", t(key), value == self.s["click_action"],
                           lambda v=value: self.update_setting("click_action", v))
        current_click = next((t(key) for value, key in click_options if value == self.s["click_action"]), "")
        menu.submenu("\U0001F446", t("p_click"), build_click, hint=current_click, width=250)

        sounds_on = bool(self.s.get("sounds", True))
        menu.toggle("\U0001F50A", t("p_sounds"), sounds_on, lambda v: self.update_setting("sounds", v))
        if self.cb.get("set_theme"):
            try:
                import theme as theme_mod
                dark = theme_mod.current_name() == "dark"
            except ImportError:
                dark = False
            menu.toggle("\U0001F319", t("p_dark_mode"), dark,
                        lambda v: self._call("set_theme", "dark" if v else "light"), close=True)

        menu.section(t("fm_more"))
        if i18n and self.cb.get("set_language"):
            def build_languages(sub):
                for code, lang_name, _hi in i18n.LANGUAGES:
                    sub.choice("\U0001F310", lang_name, code == i18n.get_language(),
                               lambda c=code: self._call("set_language", c))
            current_lang = next((n for c, n, _h in i18n.LANGUAGES if c == i18n.get_language()), "")
            menu.submenu("\U0001F310", t("m_language"), build_languages, hint=current_lang)

        def build_help(sub):
            if update:
                sub.item("\U0001F381", t("fm_upd_item", v=update.get("version", "")),
                         lambda: self._call("show_update"), badge=t("fm_new"), highlight=True)
            sub.item("\U0001F9ED", t("m_guide"), self.start_tour)
            if self.cb.get("open_user_guide"):
                sub.item("\U0001F4D6", t("m_user_guide"), lambda: self._call("open_user_guide"))
            if self.cb.get("check_updates"):
                sub.item("\U0001F504", t("m_check_updates"), lambda: self._call("check_updates"))
            if self.cb.get("version"):
                version = self._call("version") or ""
                owner = (self._call("owner") if self.cb.get("owner") else "") or ""
                sub.item("\u2139", t("m_about", v=version),
                         lambda v=version, o=owner: self.say(
                             t("about_text", v=v, owner=o, email=getattr(social, "OWNER_EMAIL", "")),
                             duration=8, priority=1))
        menu.submenu("\u2753", t("m_help"), build_help, badge=t("fm_new") if update else None, width=250)
        menu.item("\u2699", t("p_settings"), self.open_settings)
        menu.item("\U0001F648", t("p_hide", name=name), lambda: self._call("hide_pet"),
                  hint=t("p_hide_hint", hotkey=self.summon_hint) if self.summon_hint else "")
        menu.separator()
        menu.item("\u23FB", t("p_quit"), lambda: self._call("quit"), danger=True)
        menu.popup(event.x_root, event.y_root)
        self._fancy_menu = menu

    def _build_song_menu(self, sub):
        """v2.22: choose the music for sing / dance — the pet's own song, its type's song, or anything else."""
        current = self.trick_song()
        species = self.s["species"]
        mine = pet_songs.type_song(self.category)
        icons = pet_songs.ICONS

        def pick(choice):
            return lambda: self.set_trick_song(choice)

        sub.section(t("song_for", name=self.s["name"]))
        sub.choice(icons["own"], self.song_label("own"), current == "own", pick("own"))
        sub.choice(icons[mine], self.song_label(mine), current == mine, pick(mine))
        sub.section(t("song_more"))
        for choice in pet_songs.TYPE_SONGS + pet_songs.GENERIC_SONGS:
            if choice != mine:
                sub.choice(icons[choice], self.song_label(choice), current == choice, pick(choice))

        def build_animal_songs(animals):
            for category in CATEGORIES:
                def build_category(cat_menu, category=category):
                    for sp in SPECIES_BY_CATEGORY[category]:
                        if sp != species:
                            cat_menu.choice(icons["animal"], species_label(sp), current == "sp:" + sp,
                                            pick("sp:" + sp))
                inside = current.startswith("sp:") and current[3:] in SPECIES_BY_CATEGORY[category]
                animals.submenu("\u25CF" if inside else "\u25CB", t("cat_" + category), build_category,
                                hint=str(len(SPECIES_BY_CATEGORY[category])))
        other = current.startswith("sp:") and current[3:] != species
        sub.submenu(icons["animal"], t("song_animals"), build_animal_songs,
                    hint=species_label(current[3:]) if other else "")
        sub.separator()
        sub.choice(icons["random"], self.song_label("random"), current == "random", pick("random"))
        sub.choice(icons["off"], self.song_label("off"), current == "off", pick("off"))

    def _show_native_menu(self, event):
        bg, fg = "#ffffff", "#1a1a1a"

        def submenu(parent):
            return tk.Menu(parent, tearoff=0, bg=bg, fg=fg, activebackground=lighten(self.color, 0.1),
                           activeforeground="#1a1a1a")

        menu = submenu(self.win)
        name = self.s["name"]
        open_now = bool(self._call("is_window_open"))
        if self.social:
            menu.add_command(label=t("p_friends"), command=lambda: self.open_hub())
            if getattr(self.social, "enabled", False):
                menu.add_command(label=t("p_community"), command=self.open_community)
            if self.shop:
                menu.add_command(label=t("p_shop"), command=lambda: self.open_hub("shop"))
        if self.shop and self.shop.moves():
            trick_menu = submenu(menu)
            for move in self.shop.moves():
                trick_menu.add_command(label=t("mv_" + move).split(":")[0], command=lambda m=move: self.do_trick(m))
            if pet_songs is not None and self.songs:
                current_song = self.trick_song()
                song_menu = submenu(trick_menu)
                mine = pet_songs.type_song(self.category)
                order = ["own", mine] + [c for c in pet_songs.TYPE_SONGS + pet_songs.GENERIC_SONGS if c != mine]
                for choice in order + ["random", "off"]:
                    mark = "\u25cf " if choice == current_song else "   "
                    song_menu.add_command(label=mark + self.song_label(choice),
                                          command=lambda c=choice: self.set_trick_song(c))
                animals_menu = submenu(song_menu)
                for category in CATEGORIES:
                    cat_songs = submenu(animals_menu)
                    for sp in SPECIES_BY_CATEGORY[category]:
                        mark = "\u25cf " if current_song == "sp:" + sp else "   "
                        cat_songs.add_command(label=mark + species_label(sp),
                                              command=lambda c="sp:" + sp: self.set_trick_song(c))
                    animals_menu.add_cascade(label=t("cat_" + category), menu=cat_songs)
                song_menu.add_cascade(label=t("song_animals"), menu=animals_menu)
                trick_menu.add_separator()
                trick_menu.add_cascade(label=t("song_menu"), menu=song_menu)
            menu.add_cascade(label=t("p_tricks"), menu=trick_menu)
        menu.add_separator()
        menu.add_command(label=t("p_snack"), command=self.feed)
        menu.add_command(label=t("p_pet", name=name), command=self.pet)
        if minigames is not None:
            games_menu = submenu(menu)
            if self.social is not None and getattr(self.social, "enabled", False):
                games_menu.add_command(label="\U0001F465 " + t("mp_menu_friends"), command=lambda: self.open_hub("games"))
                games_menu.add_command(label="\u2694 " + t("mg_duel"), command=lambda: self.open_hub("arena"))
                games_menu.add_separator()
            if tama is not None:
                games_menu.add_command(label="\u2194 " + t("mg_leftright"), command=self.play_game)
                games_menu.add_separator()
            for key in minigames.GAMES:
                best = minigames.best_score(self.st, key)
                label = f"{minigames.ICONS[key]} {t('mg_' + key)}" + (f"   \u2605 {best}" if best else "")
                games_menu.add_command(label=label, command=lambda k=key: self.play_minigame(k))
            menu.add_cascade(label=t("p_games"), menu=games_menu)
        elif tama is not None:
            menu.add_command(label=t("p_play"), command=self.play_game)
        if tama is not None:
            if self.health is not None and self.health.sick:
                menu.add_command(label=t("p_medicine"), command=self.give_medicine)
        if self.sleeping:
            menu.add_command(label=t("p_wake"), command=lambda: self.wake("poke"))
        else:
            menu.add_command(label=t("p_sleep"), command=lambda: self.go_to_sleep("manual"))
        move = self._move_menu_entry()
        if move:
            menu.add_command(label=move[1], command=move[2])
        focus_menu = submenu(menu)
        focus_menu.add_command(label=t("p_hide_window") if open_now else t("p_open"),
                               command=lambda: self._call("toggle_window"))
        focus_menu.add_command(label=t("p_dashboard"), command=lambda: self._call("open_dashboard"))
        if self.cb.get("open_focus_trail"):
            focus_menu.add_command(label=t("mg_trail"), command=lambda: self._call("open_focus_trail"))
        if self.cb.get("open_token_board"):
            label = self.cb["token_board_label"]() if self.cb.get("token_board_label") else "Token Board"
            focus_menu.add_command(label=label, command=lambda: self._call("open_token_board"))
        menu.add_cascade(label=t("p_focus"), menu=focus_menu)
        menu.add_separator()

        animal_menu = submenu(menu)
        for category in CATEGORIES:
            cat_menu = submenu(animal_menu)
            for sp in SPECIES_BY_CATEGORY[category]:
                mark = "\u25cf " if sp == self.s["species"] else "   "
                cat_menu.add_command(label=mark + species_label(sp), command=lambda v=sp: self.set_species(v))
            animal_menu.add_cascade(label=t("cat_" + category), menu=cat_menu)
        menu.add_cascade(label=t("p_animal"), menu=animal_menu)
        if weather_live is not None and getattr(self, "wx_service", None):
            wx_menu = submenu(menu)
            wx_menu.add_command(label=self.weather_label(), command=self.refresh_weather)
            wx_menu.add_separator()
            current_mode = self.s.get("weather_mode", "ip")
            for value, key in (("follow", "wl_follow"), ("ip", "wl_ip"), ("city", "wl_city"), ("off", "wl_off")):
                mark = "\u25cf " if value == current_mode else "   "
                wx_menu.add_command(label=mark + t(key), command=lambda v=value: self.set_weather_mode(v))
            wx_menu.add_command(label="   " + t("wl_pick_other"), command=self.open_place_picker)
            wx_menu.add_separator()
            current_fx = self.s.get("weather_fx", "pet")
            for value, label in (("pet", t("wl_fx_pet", name=self.s["name"])), ("screen", t("wl_fx_screen")),
                                 ("off", t("wl_fx_off"))):
                mark = "\u25cf " if value == current_fx else "   "
                wx_menu.add_command(label=mark + label, command=lambda v=value: self.set_weather_fx(v))
            menu.add_cascade(label=t("wl_menu"), menu=wx_menu)

        color_menu = submenu(menu)
        for label, value in COLOR_PRESETS:
            mark = "\u25cf " if value == self.s["color"] else "   "
            color_menu.add_command(label=mark + t("col_" + label), command=lambda v=value: self.update_setting("color", v))
        color_menu.add_separator()
        color_menu.add_command(label=t("p_custom"), command=self._pick_custom_color)
        menu.add_cascade(label=t("p_colour"), menu=color_menu)

        size_menu = submenu(menu)
        for value in ("S", "M", "L"):
            mark = "\u25cf " if value == self.s["size"] else "   "
            size_menu.add_command(label=mark + t("size_" + value), command=lambda v=value: self.update_setting("size", v))
        menu.add_cascade(label=t("p_size"), menu=size_menu)

        click_menu = submenu(menu)
        for value, key in (("tickle_hub", "p_click_hub"), ("tickle_open", "p_click_open"),
                           ("tickle_only", "p_click_only"), ("tickle_never", "p_click_never")):
            mark = "\u25cf " if value == self.s["click_action"] else "   "
            click_menu.add_command(label=mark + t(key), command=lambda v=value: self.update_setting("click_action", v))
        menu.add_cascade(label=t("p_click"), menu=click_menu)
        sounds_on = bool(self.s.get("sounds", True))
        menu.add_command(label=("\u2713 " if sounds_on else "   ") + t("p_sounds"),
                         command=lambda: self.update_setting("sounds", not sounds_on))
        if self.cb.get("set_theme"):
            try:
                import theme as theme_mod
                dark = theme_mod.current_name() == "dark"
            except ImportError:
                dark = False
            menu.add_command(label=("\u2713 " if dark else "   ") + t("p_dark_mode"),
                             command=lambda: self._call("set_theme", "light" if dark else "dark"))

        if i18n and self.cb.get("set_language"):
            lang_menu = submenu(menu)
            for code, lang_name, _hi in i18n.LANGUAGES:
                mark = "\u25cf " if code == i18n.get_language() else "   "
                lang_menu.add_command(label=mark + lang_name, command=lambda c=code: self._call("set_language", c))
            menu.add_cascade(label=t("m_language"), menu=lang_menu)
        help_menu = submenu(menu)
        help_menu.add_command(label=t("m_guide"), command=self.start_tour)
        if self.cb.get("open_user_guide"):
            help_menu.add_command(label=t("m_user_guide"), command=lambda: self._call("open_user_guide"))
        if self.cb.get("check_updates"):
            help_menu.add_command(label=t("m_check_updates"), command=lambda: self._call("check_updates"))
        if self.cb.get("version"):
            version = self._call("version") or ""
            owner = (self._call("owner") if self.cb.get("owner") else "") or ""
            help_menu.add_command(label=t("m_about", v=version),
                                  command=lambda v=version, o=owner: self.say(t("about_text", v=v, owner=o, email=getattr(social, "OWNER_EMAIL", "")),
                                                                              duration=8, priority=1))
        menu.add_cascade(label=t("m_help"), menu=help_menu)
        menu.add_separator()
        menu.add_command(label=t("p_settings"), command=self.open_settings)
        menu.add_command(label=t("p_hide", name=name), command=lambda: self._call("hide_pet"))
        menu.add_separator()
        menu.add_command(label=t("p_quit"), command=lambda: self._call("quit"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        self._tour_event("menu")

    def _pick_custom_color(self):
        result = colorchooser.askcolor(color=self.s["color"], title=t("p_pick_colour", name=self.s["name"]))
        if result and result[1]:
            self.update_setting("color", safe_color(result[1], self.s["color"]))


# =============================================================================
# Settings / status window
# =============================================================================

class PetSettingsWindow:
    BG = "#faf8f3"
    CARD = "#ffffff"
    INK = "#1a1a1a"
    MUTED = "#6b6b6b"
    BORDER = "#e2ddd4"
    ACCENT = "#8b1a1a"
    TILE = 52

    def __init__(self, pet: DesktopPet):
        self.pet = pet
        try:
            import theme as theme_mod
            p = theme_mod.palette()
            self.BG, self.CARD, self.INK = p["bg"], p["card"], p["ink"]
            self.MUTED, self.BORDER, self.ACCENT = p["muted"], p["border"], p["accent"]
            self.SOFT, self.TRACK = p["soft"], p["bar"]
        except Exception:
            self.SOFT, self.TRACK = self.SOFT, self.TRACK
        self.win = tk.Toplevel(pet.root)
        self.win.title(t("s_title", name=pet.s["name"]))
        self.win.configure(bg=self.BG)
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        icon = pet.cb.get("icon_path")
        if icon:
            try:
                self.win.iconbitmap(icon)
            except tk.TclError:
                pass
        self._alive = True
        self._swatches = []
        self._cheek_swatches = []
        self._tiles = []
        self._tab_buttons = {}
        self.category = pet.category
        self._build()
        self._position()
        self._tick()

    def alive(self) -> bool:
        return self._alive

    def close(self):
        self._alive = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        if self.pet.settings_win is self:
            self.pet.settings_win = None

    def _fit_scroll(self, _event=None):
        """v2.18: the window never gets taller than the screen - the content scrolls instead."""
        body = getattr(self, "_body", None)
        if body is None:
            return
        try:
            body.update_idletasks()
            w, h = body.winfo_reqwidth(), body.winfo_reqheight()
            bx, by, _bw, _bh = self.pet.screen_bbox()
            _mon, work = monitor_rects(bx, by, self.pet.root)
            max_h = max(300, work[3] - work[1] - 60)
            view_h = min(h, max_h)
            self._scroll_canvas.config(width=w, height=view_h, scrollregion=(0, 0, w, h))
            needs_bar = h > view_h
            if needs_bar and not self._scrollbar.winfo_ismapped():
                self._scrollbar.pack(side="right", fill="y", before=self._scroll_canvas)
            elif not needs_bar and self._scrollbar.winfo_ismapped():
                self._scrollbar.pack_forget()
                self._scroll_canvas.yview_moveto(0)
        except tk.TclError:
            pass

    def _on_wheel(self, event):
        if self._scrollbar.winfo_ismapped():
            self._scroll_canvas.yview_scroll(int(-event.delta / 120) * 3, "units")

    def _position(self):
        self._fit_scroll()
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        bx, by, bw, _bh = self.pet.screen_bbox()
        _mon, work = monitor_rects(bx, by, self.pet.root)
        x = max(work[0] + 8, min(work[2] - w - 8, bx + bw // 2 - w // 2))
        y = max(work[1] + 8, min(work[3] - h - 8, by - h - 16))
        self.win.geometry("+%d+%d" % (x, y))

    def _section(self, parent, title):
        tk.Label(parent, text=title.upper(), font=("Segoe UI", 8, "bold"), fg=self.ACCENT,
                 bg=self.BG, anchor="w").pack(fill="x", pady=(10, 3))
        frame = tk.Frame(parent, bg=self.CARD, highlightthickness=1, highlightbackground=self.BORDER)
        frame.pack(fill="x")
        inner = tk.Frame(frame, bg=self.CARD)
        inner.pack(fill="x", padx=10, pady=8)
        return inner

    def _button(self, parent, text, command, primary=False):
        bg = self.pet.color if primary else self.SOFT
        fg = darken(self.pet.color, 0.65) if primary else self.INK
        btn = tk.Button(parent, text=text, command=command, font=("Segoe UI", 9, "bold" if primary else "normal"),
                        bg=bg, fg=fg, activebackground=darken(bg, 0.08), relief=tk.FLAT, bd=0,
                        padx=10, pady=3, cursor="hand2")
        btn.bind("<Enter>", lambda e: btn.config(bg=darken(bg, 0.06)))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg))
        return btn

    def _build(self):
        pet = self.pet
        # v2.18: scrollable body - on small / scaled screens the settings no longer run off the bottom
        self._scroll_canvas = tk.Canvas(self.win, bg=self.BG, highlightthickness=0, bd=0)
        self._scrollbar = tk.Scrollbar(self.win, orient="vertical", command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=self._scrollbar.set, yscrollincrement=12)
        self._scroll_canvas.pack(side="left", fill="both", expand=True)
        self._body = tk.Frame(self._scroll_canvas, bg=self.BG)
        self._scroll_canvas.create_window(0, 0, window=self._body, anchor="nw")
        self._body.bind("<Configure>", self._fit_scroll)
        self.win.bind("<MouseWheel>", self._on_wheel)
        outer = tk.Frame(self._body, bg=self.BG)
        outer.pack(fill="both", expand=True, padx=14, pady=12)

        # --- status card ---
        top = tk.Frame(outer, bg=self.CARD, highlightthickness=1, highlightbackground=self.BORDER)
        top.pack(fill="x")
        self.preview = tk.Canvas(top, width=130, height=112, bg=lighten(pet.color, 0.75),
                                 highlightthickness=0, cursor="hand2")
        self.preview.pack(side="left", padx=10, pady=10)
        self.preview.bind("<Button-1>", lambda e: pet.tickle())
        stats = tk.Frame(top, bg=self.CARD)
        stats.pack(side="left", fill="both", expand=True, padx=(0, 12), pady=10)
        self.mood_lbl = tk.Label(stats, text="", font=("Georgia", 12, "bold"), fg=self.INK, bg=self.CARD,
                                 anchor="w")
        self.mood_lbl.pack(fill="x")
        self.age_lbl = tk.Label(stats, text="", font=("Segoe UI", 8), fg=self.MUTED, bg=self.CARD, anchor="w")
        self.age_lbl.pack(fill="x", pady=(0, 4))
        self.bars = {}
        bar_rows = [("happiness", "bar_happy", "#ff6b8b"), ("energy", "bar_energy", "#f5b700"),
                    ("fullness", "bar_food", "#5bbf72"), ("bond", "bar_bond", "#8b5cf6")]
        if pet.growth is not None:
            bar_rows.append(("growth", "bar_grow", "#2bb5a0"))      # v2.18: progress to the next life stage
        for key, label_key, color in bar_rows:
            row = tk.Frame(stats, bg=self.CARD)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=t(label_key), width=7, anchor="w", font=("Segoe UI", 8), fg=self.MUTED,
                     bg=self.CARD).pack(side="left")
            bar = tk.Canvas(row, width=130, height=9, bg=self.CARD, highlightthickness=0)
            bar.pack(side="left", padx=(2, 4))
            value = tk.Label(row, text="", width=3, anchor="e", font=("Segoe UI", 8), fg=self.MUTED,
                             bg=self.CARD)
            value.pack(side="left")
            self.bars[key] = (bar, value, color)

        actions = tk.Frame(outer, bg=self.BG)
        actions.pack(fill="x", pady=(8, 0))
        self._button(actions, t("b_snack"), pet.feed, primary=True).pack(side="left")
        self._button(actions, t("b_pet"), pet.pet).pack(side="left", padx=6)
        self.sleep_btn = self._button(actions, t("b_sleep"), self._toggle_sleep)
        self.sleep_btn.pack(side="left")
        self._button(actions, t("b_tickle"), pet.tickle).pack(side="left", padx=6)
        if tama is not None:
            self._button(actions, t("b_play"), pet.play_game).pack(side="left")
            self.medicine_btn = self._button(actions, t("b_medicine"), pet.give_medicine)

        # --- animal picker ---
        animal = self._section(outer, t("p_animal"))
        tabs = tk.Frame(animal, bg=self.CARD)
        tabs.pack(fill="x")
        for category in CATEGORIES:
            btn = tk.Label(tabs, text=t("cat_" + category), font=("Segoe UI", 9, "bold"), padx=12, pady=3,
                           cursor="hand2")
            btn.pack(side="left", padx=(0, 4))
            btn.bind("<Button-1>", lambda e, c=category: self._show_category(c))
            self._tab_buttons[category] = btn
        self.grid_frame = tk.Frame(animal, bg=self.CARD)
        self.grid_frame.pack(fill="x", pady=(6, 0))
        self.species_lbl = tk.Label(animal, text="", font=("Segoe UI", 8), fg=self.MUTED, bg=self.CARD, anchor="w")
        self.species_lbl.pack(fill="x", pady=(4, 0))
        self._show_category(self.category)

        # --- look ---
        look = self._section(outer, t("sec_look"))
        row = tk.Frame(look, bg=self.CARD)
        row.pack(fill="x")
        tk.Label(row, text=t("l_name"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD, width=8,
                 anchor="w").pack(side="left")
        self.name_var = tk.StringVar(value=pet.s["name"])
        entry = tk.Entry(row, textvariable=self.name_var, font=("Segoe UI", 9), relief=tk.FLAT,
                         bg="#f6f3ee", width=18)
        entry.pack(side="left", ipady=2)
        entry.bind("<Return>", lambda e: self._save_name())
        entry.bind("<FocusOut>", lambda e: self._save_name())

        if tama is not None:
            # v2.18: boy / girl / secret - shows on the pet (hair tuft, bow & lashes) and next to its name
            row = tk.Frame(look, bg=self.CARD)
            row.pack(fill="x", pady=(6, 0))
            tk.Label(row, text=t("l_gender"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD, width=8,
                     anchor="w").pack(side="left")
            self.gender_var = tk.StringVar(value=pet.s.get("gender", ""))
            for value, key in (("boy", "gender_boy"), ("girl", "gender_girl"), ("", "gender_secret")):
                tk.Radiobutton(row, text=t(key), value=value, variable=self.gender_var,
                               command=lambda: pet.update_setting("gender", self.gender_var.get()),
                               font=("Segoe UI", 9, "bold" if value else "normal"),
                               fg=tama.gender_color(value) if value else self.INK, bg=self.CARD,
                               activebackground=self.CARD, cursor="hand2").pack(side="left")
        if pet.growth is not None:
            row = tk.Frame(look, bg=self.CARD)
            row.pack(fill="x", pady=(6, 0))
            tk.Label(row, text=t("l_stage"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD, width=8,
                     anchor="w").pack(side="left")
            self.stage_lbl = tk.Label(row, text=pet.growth_text(), font=("Segoe UI", 9), fg=self.INK,
                                      bg=self.CARD, anchor="w")
            self.stage_lbl.pack(side="left")
            self._button(row, t("b_regrow"), self._regrow).pack(side="right")

        row = tk.Frame(look, bg=self.CARD)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text=t("l_colour"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD, width=8,
                 anchor="nw").pack(side="left", anchor="n")
        grid = tk.Frame(row, bg=self.CARD)
        grid.pack(side="left")
        for i, (_label, value) in enumerate(COLOR_PRESETS):
            sw = self._swatch(grid, value, lambda v=value: pet.update_setting("color", v), 22)
            sw.grid(row=i // 5, column=i % 5, padx=2, pady=2)
            self._swatches.append((sw, value))
        self._button(grid, t("p_custom"), self._custom_color).grid(row=0, column=5, rowspan=2, padx=(6, 0))

        row = tk.Frame(look, bg=self.CARD)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text=t("l_cheeks"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD, width=8,
                 anchor="w").pack(side="left")
        for _label, value in CHEEK_PRESETS:
            sw = self._swatch(row, value, lambda v=value: pet.update_setting("cheek_color", v), 18)
            sw.pack(side="left", padx=2)
            self._cheek_swatches.append((sw, value))

        self.size_var = tk.StringVar(value=pet.s["size"])
        row = tk.Frame(look, bg=self.CARD)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=t("l_size"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD, width=8,
                 anchor="w").pack(side="left")
        for value in ("S", "M", "L"):
            tk.Radiobutton(row, text=t("size_" + value), value=value, variable=self.size_var,
                           command=lambda: pet.update_setting("size", self.size_var.get()),
                           font=("Segoe UI", 9), bg=self.CARD, activebackground=self.CARD,
                           cursor="hand2").pack(side="left")

        # --- behaviour ---
        beh = self._section(outer, t("sec_behaviour"))
        self.click_var = tk.StringVar(value=pet.s.get("click_action", "tickle_hub"))
        tk.Label(beh, text=t("click_q"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD,
                 anchor="w").pack(fill="x")
        for value, key in (("tickle_hub", "click_hub_long"), ("tickle_open", "click_open_long"),
                           ("tickle_only", "click_only_long"), ("tickle_never", "click_never_long")):
            tk.Radiobutton(beh, text=t(key), value=value, variable=self.click_var,
                           command=lambda: pet.update_setting("click_action", self.click_var.get()),
                           font=("Segoe UI", 9), bg=self.CARD, activebackground=self.CARD, anchor="w",
                           cursor="hand2").pack(fill="x", padx=(8, 0))
        self.flag_vars = {}
        for key in ("bubble_reminders", "walk_on_taskbar", "sleep_when_away", "open_near_pet",
                    "start_with_card", "chatty", "hide_in_fullscreen", "auto_update", "auto_install", "sounds",
                    "quiet_focus", "sick_days"):
            var = tk.BooleanVar(value=bool(pet.s.get(key)))
            self.flag_vars[key] = var
            tk.Checkbutton(beh, text=t("f_" + key), variable=var, anchor="w", font=("Segoe UI", 9), bg=self.CARD,
                           activebackground=self.CARD, cursor="hand2",
                           command=lambda k=key, v=var: pet.update_setting(k, bool(v.get()))
                           ).pack(fill="x", pady=(2 if key == "bubble_reminders" else 0, 0))

        row = tk.Frame(beh, bg=self.CARD)
        row.pack(fill="x", pady=(2, 0))
        tk.Label(row, text=t("l_volume"), font=("Segoe UI", 9), fg=self.INK, bg=self.CARD,
                 anchor="w").pack(side="left", padx=(4, 6))
        self.volume_var = tk.IntVar(value=int(pet.s.get("sound_volume", 60)))
        volume = tk.Scale(row, from_=0, to=100, orient="horizontal", variable=self.volume_var, showvalue=False,
                          length=130, sliderlength=14, width=10, bg=self.CARD, highlightthickness=0,
                          troughcolor=self.TRACK, activebackground=self.ACCENT, bd=0)
        volume.pack(side="left")
        volume.bind("<ButtonRelease-1>", lambda e: self._volume_changed())
        self._button(row, t("b_hear"), lambda: pet._voice(pet.mood(), force=True)).pack(side="left", padx=(8, 0))

        # --- language ---
        if i18n and pet.cb.get("set_language"):
            lang = self._section(outer, t("sec_language"))
            self.lang_var = tk.StringVar(value=i18n.get_language())
            row = tk.Frame(lang, bg=self.CARD)
            row.pack(fill="x")
            for i, (code, lang_name, _hi) in enumerate(i18n.LANGUAGES):
                tk.Radiobutton(row, text=lang_name, value=code, variable=self.lang_var,
                               command=lambda: pet._call("set_language", self.lang_var.get()),
                               font=("Segoe UI", 9), bg=self.CARD, activebackground=self.CARD,
                               cursor="hand2").grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 18))

        bottom = tk.Frame(outer, bg=self.BG)
        bottom.pack(fill="x", pady=(10, 0))
        tk.Label(bottom, text=t("s_tip", name=pet.s["name"]), font=("Segoe UI", 8), fg=self.MUTED,
                 bg=self.BG, wraplength=330, justify="left").pack(side="left")
        self._button(bottom, t("close"), self.close).pack(side="right")

    # --- animal grid ---
    def _show_category(self, category):
        self.category = category
        for c, btn in self._tab_buttons.items():
            active = c == category
            btn.config(bg=self.ACCENT if active else self.SOFT, fg="#ffffff" if active else self.INK)
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._tiles = []
        for i, sp in enumerate(SPECIES_BY_CATEGORY[category]):
            tile = tk.Canvas(self.grid_frame, width=self.TILE, height=self.TILE, bg="#fbf9f5",
                             highlightthickness=2, highlightbackground="#fbf9f5", cursor="hand2")
            tile.grid(row=i // 6, column=i % 6, padx=2, pady=2)
            tile.bind("<Button-1>", lambda e, s=sp: self.pet.set_species(s))
            tile.bind("<Enter>", lambda e, s=sp: self.species_lbl.config(text=species_label(s)))
            tile.bind("<Leave>", lambda e: self._update_species_label())
            self._tiles.append((tile, sp))
        self._draw_tiles()
        self._update_species_label()

    def _update_species_label(self):
        sp = self.pet.s["species"]
        self.species_lbl.config(text="\u25cf %s \u00b7 %s" % (species_label(sp), t("cat_" + species_spec(sp)["cat"])))

    def _draw_tiles(self):
        current = self.pet.s["species"]
        now = time.monotonic()
        for tile, sp in self._tiles:
            tile.delete("all")
            colour = self.pet.s["color"] if sp == current else SPECIES[sp]["color"]
            look = default_look({"species": sp, "color": colour, "cheek_color": self.pet.s["cheek_color"],
                                 "gender": self.pet.s.get("gender", "")})
            look["t"] = now
            look["mouth"] = "smile" if sp == current else "cat"
            spec = SPECIES[sp]
            if spec["cat"] == "air":
                look["flap"] = math.sin(now * spec.get("flap_hz", 12) * 0.5)
            draw_pet(tile, self.TILE / 2, self.TILE - 3, 30, look)
            tile.config(highlightbackground=self.ACCENT if sp == current else "#fbf9f5")

    def _swatch(self, parent, color, command, size):
        cv = tk.Canvas(parent, width=size, height=size, bg=self.CARD, highlightthickness=0, cursor="hand2")
        cv.bind("<Button-1>", lambda e: command())
        cv._color = color
        cv._size = size
        return cv

    def _draw_swatches(self):
        for group, current in ((self._swatches, self.pet.s["color"]),
                               (self._cheek_swatches, self.pet.s["cheek_color"])):
            for cv, color in group:
                s = cv._size
                cv.delete("all")
                if color.lower() == current.lower():
                    cv.create_oval(1, 1, s - 1, s - 1, outline="#1a1a1a", width=2)
                cv.create_oval(4, 4, s - 4, s - 4, fill=color, outline=darken(color, 0.3))

    def refresh_controls(self):
        if not self._alive:
            return
        s = self.pet.s
        self.size_var.set(s["size"])
        self.click_var.set(s["click_action"])
        if hasattr(self, "gender_var"):
            self.gender_var.set(s.get("gender", ""))
        for key, var in self.flag_vars.items():
            var.set(bool(s.get(key)))
        self.volume_var.set(int(s.get("sound_volume", 60)))
        self.preview.config(bg=lighten(s["color"], 0.75))
        if species_spec(s["species"])["cat"] != self.category:
            self._show_category(species_spec(s["species"])["cat"])
        self._draw_tiles()
        self._update_species_label()
        self._draw_swatches()

    def _save_name(self):
        name = self.name_var.get().strip()[:16] or "Pip"
        if name != self.pet.s["name"]:
            self.pet.update_setting("name", name)
            self.win.title(t("s_title", name=name))

    def _custom_color(self):
        result = colorchooser.askcolor(color=self.pet.s["color"], parent=self.win, title=t("pick_colour"))
        if result and result[1]:
            self.pet.update_setting("color", safe_color(result[1], self.pet.s["color"]))

    def _volume_changed(self):
        value = int(self.volume_var.get())
        if value != int(self.pet.s.get("sound_volume", 60)):
            self.pet.update_setting("sound_volume", value)
        self.pet._voice(self.pet.mood(), force=True)

    def _toggle_sleep(self):
        if self.pet.sleeping:
            self.pet.wake("poke")
        else:
            self.pet.go_to_sleep("manual")

    def _regrow(self):
        from tkinter import messagebox
        name = self.pet.s["name"]
        if messagebox.askyesno(t("regrow_title"), t("regrow_confirm", name=name), parent=self.win):
            self.pet.regrow()

    def _growth_percent(self) -> float:
        growth = self.pet.growth
        if growth is None:
            return 0.0
        _next_stage, _need, fraction = growth.progress()
        return 100.0 * fraction

    def _tick(self):
        if not self._alive:
            return
        try:
            pet = self.pet
            now = time.monotonic()
            look = pet.compute_look(now, track_cursor=False)
            look["facing"] = 1
            look["dangle"] = False
            look["hop"] = min(look["hop"], 6)
            look["hover"] = min(look.get("hover", 0), 8)
            look["foot_l"] = look["foot_r"] = 0
            look["lean"] = max(-3, min(3, look["lean"]))
            self.preview.delete("all")
            preview_size = 60 * (tama.stage_scale(look.get("stage")) if tama else 1.0)
            draw_pet(self.preview, 65, 106, preview_size, look)
            if pet.mood() == "sleeping":
                phrase = t("sleeping_phrase", name=pet.s["name"])
            else:
                phrase = t("mood_phrase", name=pet.s["name"], mood=pet.mood_label())
            self.mood_lbl.config(text=phrase)
            try:
                born = datetime.strptime(pet.st.get("born", ""), "%Y-%m-%d")
                days = (datetime.now() - born).days
            except ValueError:
                days = 0
            self.age_lbl.config(text=t("s_age", d=days, p=pet.st.get("pets_total", 0),
                                       s=pet.st.get("snacks_total", 0)))
            for key, (bar, value, color) in self.bars.items():
                v = self._growth_percent() if key == "growth" else float(pet.st.get(key, 0))
                bar.delete("all")
                bar.create_rectangle(0, 0, 130, 9, fill=self.TRACK, outline="")
                bar.create_rectangle(0, 0, max(2, 130 * v / 100.0), 9, fill=color, outline="")
                value.config(text="%d" % round(v))
            self.sleep_btn.config(text=t("b_wake") if pet.sleeping else t("b_sleep"))
            if getattr(self, "stage_lbl", None) is not None:
                self.stage_lbl.config(text=pet.growth_text())
            medicine_btn = getattr(self, "medicine_btn", None)
            if medicine_btn is not None:
                sick = bool(pet.health is not None and pet.health.sick)
                if sick and not medicine_btn.winfo_ismapped():
                    medicine_btn.pack(side="left", padx=6)
                elif not sick and medicine_btn.winfo_ismapped():
                    medicine_btn.pack_forget()
            if not getattr(self, "_swatches_drawn", False):
                self._draw_swatches()
                self._swatches_drawn = True
            self._tick_n = getattr(self, "_tick_n", 0) + 1
            if self._tick_n % 3 == 0:
                self._draw_tiles()
        except tk.TclError:
            return
        self.win.after(80, self._tick)
