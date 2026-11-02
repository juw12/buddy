"""How the desktop pet gets around (v2.19 "Land, sea & sky").

* land  - walks on the taskbar and on the top edges of open windows ("platforms") and jumps
          between them. Land animals always have gravity: let go of one in mid-air and it falls
          onto the first window edge below it, or onto the taskbar.
* water - swims along the taskbar (the "water") and every now and then leaps out of it in a big
          arc and dives back in with a splash.
* air   - flies anywhere on the screen, hovers, and sometimes perches on the taskbar or a window.

Everything here is plain maths so it can be tested without Tk. The Win32 window scan is guarded:
without it there simply are no window platforms and land animals stay on the taskbar.
Screen coordinates: x grows to the right, y grows downwards; a "floor" is the y the feet stand on.
"""
from __future__ import annotations

import ctypes
import math
import os
import random
import sys

GRAVITY = 2600.0          # px/s^2 - the same feel as the old "fall to the taskbar"
TASKBAR_GAP = 28          # window tops this close above the taskbar don't count as platforms
MIN_SEGMENT = 70          # a platform needs at least this much free (uncovered) edge to stand on
FLY_SPEED = 95.0          # px/s cruising speed of flying animals (before mood / focus factors)

# Shell windows that are never something to stand on (they still don't hide anything either).
_SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow", "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow",
    "Windows.UI.Input.InputSite.WindowClass", "ForegroundStaging", "MultitaskingViewFrame",
    "TaskListThumbnailWnd", "EdgeUiInputTopWndClass", "EdgeUiInputWndClass",
}


# =============================================================================
# Intervals & platforms (pure)
# =============================================================================

def subtract(segs, lo, hi):
    """Remove [lo, hi] from a list of (a, b) intervals."""
    out = []
    for a, b in segs:
        if hi <= a or lo >= b:
            out.append((a, b))
            continue
        if lo > a:
            out.append((a, lo))
        if hi < b:
            out.append((hi, b))
    return out


def platforms_from_windows(windows, monitor_of, bounds=None, headroom=60, min_segment=MIN_SEGMENT):
    """Turn a top-to-bottom z-ordered window list into platforms a pet can stand on.

    windows     dicts {hwnd, rect: (left, top, right, bottom), title, zoomed, platform_ok}
    monitor_of  callable (x, y) -> (monitor_rect, work_rect), or None when the point is on no monitor
    bounds      (left, top, right, bottom) of the whole desktop - edges are clipped to it
    headroom    room needed above an edge for the pet to stay on screen
    Returns dicts {hwnd, left, top, right, segs: [(a, b), ...], title} - segs are the parts of the
    window's top edge that no window above it covers.
    """
    result = []
    above = []
    for win in windows:
        left, top, right, bottom = win["rect"]
        if right - left > 0 and bottom - top > 0:
            if (win.get("platform_ok", True) and win.get("title") and not win.get("zoomed")
                    and right - left >= min_segment):
                lo, hi = left, right
                if bounds is not None:
                    lo, hi = max(lo, bounds[0]), min(hi, bounds[2])
                where = monitor_of((lo + hi) / 2.0, top + 1) if hi - lo >= min_segment else None
                if where is not None:
                    mon, work = where
                    if mon[1] + headroom <= top <= work[3] - TASKBAR_GAP:
                        segs = [(lo, hi)]
                        probe = top + 2          # the row just inside the window's top edge
                        for a_left, a_top, a_right, a_bottom in above:
                            if a_top <= probe < a_bottom:
                                segs = subtract(segs, a_left, a_right)
                                if not segs:
                                    break
                        segs = [(a, b) for a, b in segs if b - a >= min_segment]
                        if segs:
                            result.append({"hwnd": win["hwnd"], "left": left, "top": top, "right": right,
                                           "segs": segs, "title": win.get("title", "")})
            above.append((left, top, right, bottom))
    return result


def segment_at(platform, x, tolerance=0.0):
    """The (a, b) segment of `platform` under x (None when x is over a covered / missing part)."""
    if not platform:
        return None
    for a, b in platform.get("segs", ()):
        if a - tolerance <= x <= b + tolerance:
            return a, b
    return None


def find_platform(platforms, hwnd):
    for plat in platforms or ():
        if plat["hwnd"] == hwnd:
            return plat
    return None


def floor_crossed(platforms, x, y_prev, y_new, taskbar_y, exclude=None):
    """While falling from y_prev to y_new at x: the floor that was reached, as (y, platform_or_None).

    A window edge counts when it lies between the two heights and x is over a free part of it.
    None when nothing was reached yet.
    """
    best = None
    for plat in platforms or ():
        if exclude is not None and plat["hwnd"] == exclude:
            continue
        top = plat["top"]
        if y_prev <= top <= y_new and segment_at(plat, x) is not None and top < taskbar_y:
            if best is None or top < best[0]:
                best = (top, plat)
    if best is not None:
        return best
    if y_new >= taskbar_y:
        return taskbar_y, None
    return None


def floor_below(platforms, x, y, taskbar_y, exclude=None):
    """The first floor at or below y at x: (y, platform_or_None)."""
    hit = floor_crossed(platforms, x, y, taskbar_y, taskbar_y, exclude=exclude)
    return hit if hit is not None else (taskbar_y, None)


def perch_at(platforms, x, y, tolerance=14):
    """A platform whose free edge is right at (x, y) - for dropping a bird onto a window."""
    best = None
    for plat in platforms or ():
        if abs(plat["top"] - y) <= tolerance and segment_at(plat, x) is not None:
            if best is None or abs(plat["top"] - y) < abs(best["top"] - y):
                best = plat
    return best


def jump_targets(platforms, px, floor_y, size, mon, taskbar_y, current=None, max_rise=None,
                 max_dx=520.0, rng=random):
    """Places a land animal standing at (px, floor_y) could jump to.

    Returns a list of (x, y, platform_or_None) - None means the taskbar.
    """
    margin = size * 0.45
    if max_rise is None:
        max_rise = max(260.0, (taskbar_y - mon[1]) * 0.62)
    out = []
    here = segment_at(current, px, tolerance=2) if current else None
    for plat in platforms or ():
        top = plat["top"]
        rise = floor_y - top
        if rise > max_rise:
            continue
        for a, b in plat["segs"]:
            if current is not None and plat["hwnd"] == current.get("hwnd") and here == (a, b):
                continue                     # that's where we are standing
            lo, hi = a + margin, b - margin
            if hi < lo or hi < mon[0] or lo > mon[2]:
                continue
            x = min(max(px + rng.uniform(-140, 140), lo), hi)
            if abs(x - px) > max_dx:
                x = min(max(px, lo), hi)     # the nearest end
                if abs(x - px) > max_dx:
                    continue
            if abs(rise) < 12 and abs(x - px) < 40:
                continue
            out.append((x, top, plat))
    if floor_y < taskbar_y - 12:
        side = rng.choice((-1, 1))
        x = px + side * rng.uniform(40, 200)
        x = min(max(x, mon[0] + size), mon[2] - size)
        out.append((x, taskbar_y, None))
    return out


# =============================================================================
# Jumps, leaps & flights (pure)
# =============================================================================

def leap_path(x0, y0, x1, y1, apex_y, g=GRAVITY):
    """A real projectile from (x0, y0) to (x1, y1) whose highest point is apex_y (y grows downwards)."""
    apex_y = min(apex_y, y0 - 1.0, y1 - 1.0)
    up = y0 - apex_y
    down = y1 - apex_y
    t_up = math.sqrt(2.0 * up / g)
    t_down = math.sqrt(2.0 * down / g)
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "apex": float(apex_y),
            "g": float(g), "vy0": g * t_up, "t_up": t_up, "T": t_up + t_down}


def leap_pos(path, t):
    """(x, y, vy) after t seconds - vy < 0 while going up."""
    T = path["T"]
    t = max(0.0, min(T, t))
    k = t / T if T > 0 else 1.0
    x = path["x0"] + (path["x1"] - path["x0"]) * k
    y = path["y0"] - path["vy0"] * t + 0.5 * path["g"] * t * t
    vy = -path["vy0"] + path["g"] * t
    return x, y, vy


def jump_apex(x0, y0, x1, y1):
    """A cartoon jump: a bit higher than the higher end, more for longer jumps."""
    arc = max(45.0, min(150.0, 40.0 + 0.22 * abs(x1 - x0)))
    return min(y0, y1) - arc


def water_leap(px, floor_y, facing, mon, work, size, rng=random):
    """A dolphin leap out of the water (the taskbar): (x1, apex_y) - lands back on the same floor."""
    height_room = floor_y - (work[1] + size * 1.3)
    span = max(120.0, work[3] - work[1])
    height = rng.uniform(0.25, 0.55) * span
    height = max(min(110.0, height_room), min(height, height_room))
    lo = mon[0] + size * 1.1
    hi = mon[2] - size * 1.1
    dist = rng.uniform(150, 450)
    x1 = px + facing * dist
    if not lo <= x1 <= hi:
        x1 = px - facing * dist          # not enough room that way: leap the other way
    x1 = min(max(x1, lo), hi)
    return x1, floor_y - height


def flight_path(x0, y0, x1, y1, speed=FLY_SPEED):
    dist = math.hypot(x1 - x0, y1 - y0)
    duration = max(0.8, dist / max(20.0, speed))
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "dur": duration,
            "arc": min(55.0, dist * 0.12)}


def flight_pos(path, t):
    """(x, y, progress 0..1) along an eased flight with a gentle upward arc."""
    p = max(0.0, min(1.0, t / path["dur"]))
    s = 0.5 - 0.5 * math.cos(math.pi * p)
    x = path["x0"] + (path["x1"] - path["x0"]) * s
    y = path["y0"] + (path["y1"] - path["y0"]) * s - path["arc"] * math.sin(math.pi * p)
    return x, y, p


def fly_bounds(work, W, H):
    """Where the pet window's anchor (centre x, floor y) may be while flying on this work area."""
    return (work[0] + W / 2.0 + 4, work[1] + H + 4, work[2] - W / 2.0 - 4, work[3] - 10)


def flight_target(px, py, work, W, H, rng=random, cursor=None):
    """Somewhere new to fly to on this work area (near the cursor sometimes)."""
    lo_x, lo_y, hi_x, hi_y = fly_bounds(work, W, H)
    if hi_x <= lo_x or hi_y <= lo_y:
        return px, py
    if cursor is not None:
        cx, cy = cursor
        if work[0] <= cx <= work[2] and work[1] <= cy <= work[3]:
            side = 1 if px >= cx else -1
            x = cx + side * rng.uniform(70, 130)
            y = cy + rng.uniform(10, 50)
            return min(max(x, lo_x), hi_x), min(max(y, lo_y), hi_y)
    for _ in range(10):
        angle = rng.uniform(0, 2 * math.pi)
        dist = rng.uniform(160, 600)
        x = px + math.cos(angle) * dist
        y = py + math.sin(angle) * dist * 0.6
        if lo_x <= x <= hi_x and lo_y <= y <= hi_y:
            return x, y
    return rng.uniform(lo_x, hi_x), rng.uniform(lo_y, hi_y)


# =============================================================================
# Win32: which windows are open, and where (guarded)
# =============================================================================

_u32 = None
_dwm = None
_ENUMPROC = None

if sys.platform == "win32":
    try:
        from ctypes import wintypes as _wt

        _u32 = ctypes.WinDLL("user32", use_last_error=True)
        _dwm = ctypes.WinDLL("dwmapi")
        _ENUMPROC = ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
        _u32.EnumWindows.argtypes = [_ENUMPROC, _wt.LPARAM]
        _u32.EnumWindows.restype = _wt.BOOL
        for _name in ("IsWindow", "IsWindowVisible", "IsIconic", "IsZoomed"):
            getattr(_u32, _name).argtypes = [_wt.HWND]
            getattr(_u32, _name).restype = _wt.BOOL
        _u32.GetWindowRect.argtypes = [_wt.HWND, ctypes.POINTER(_wt.RECT)]
        _u32.GetWindowRect.restype = _wt.BOOL
        _u32.GetWindowTextLengthW.argtypes = [_wt.HWND]
        _u32.GetWindowTextW.argtypes = [_wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
        _u32.GetClassNameW.argtypes = [_wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
        _u32.GetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int]
        _u32.GetWindowLongW.restype = ctypes.c_long
        _u32.GetWindowThreadProcessId.argtypes = [_wt.HWND, ctypes.POINTER(_wt.DWORD)]
        _u32.GetWindowThreadProcessId.restype = _wt.DWORD
        _u32.GetSystemMetrics.argtypes = [ctypes.c_int]
        _dwm.DwmGetWindowAttribute.argtypes = [_wt.HWND, _wt.DWORD, ctypes.c_void_p, _wt.DWORD]
        _dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    except Exception:  # pragma: no cover - defensive
        _u32 = None
        _dwm = None

_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_WS_THICKFRAME = 0x00040000


def available() -> bool:
    return _u32 is not None


def _cloaked(hwnd) -> bool:
    if _dwm is None:
        return False
    value = ctypes.c_int(0)
    try:
        if _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value)) == 0:
            return value.value != 0
    except Exception:
        pass
    return False


def _frame_rect(hwnd):
    """The visible frame of a window (left, top, right, bottom) without the invisible resize borders."""
    r = _wt.RECT()
    if not _u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    rect = (r.left, r.top, r.right, r.bottom)
    if _dwm is not None:
        e = _wt.RECT()
        try:
            ok = _dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(e), ctypes.sizeof(e)) == 0
        except Exception:
            ok = False
        if ok:
            ext = (e.left, e.top, e.right, e.bottom)
            # DWM bounds are in physical pixels; use them only when they're in our (possibly
            # DPI-virtualised) coordinate space, i.e. inside the window rect
            if (ext[2] > ext[0] and ext[3] > ext[1] and ext[0] >= rect[0] - 2 and ext[1] >= rect[1] - 2
                    and ext[2] <= rect[2] + 2 and ext[3] <= rect[3] + 2):
                return ext
    style = _u32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
    if style & _WS_THICKFRAME:
        inset = max(0, _u32.GetSystemMetrics(32) + _u32.GetSystemMetrics(92) - 1)
        return rect[0] + inset, rect[1], rect[2] - inset, rect[3] - inset
    return rect


def _window_info(hwnd, own_pid):
    if not _u32.IsWindowVisible(hwnd) or _u32.IsIconic(hwnd):
        return None
    pid = _wt.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == own_pid:
        return None                      # the pet itself, its bubbles, the Pips card...
    ex_style = _u32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
    if ex_style & (_WS_EX_TOOLWINDOW | _WS_EX_TRANSPARENT):
        return None
    if _cloaked(hwnd):
        return None                      # on another virtual desktop / suspended app
    cls = ctypes.create_unicode_buffer(96)
    _u32.GetClassNameW(hwnd, cls, 96)
    if cls.value in _SHELL_CLASSES:
        return None
    rect = _frame_rect(hwnd)
    if rect is None or rect[2] - rect[0] < 40 or rect[3] - rect[1] < 24:
        return None
    title = ""
    length = _u32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buf = ctypes.create_unicode_buffer(min(length, 80) + 1)
        _u32.GetWindowTextW(hwnd, buf, len(buf))
        title = buf.value or " "
    return {"hwnd": hwnd, "rect": rect, "title": title, "zoomed": bool(_u32.IsZoomed(hwnd)),
            "platform_ok": not (ex_style & _WS_EX_NOACTIVATE), "cls": cls.value}


def scan_windows(own_pid=None):
    """Visible top-level windows of other programs, top-to-bottom z-order ([] without Win32)."""
    if _u32 is None or _ENUMPROC is None:
        return []
    own = os.getpid() if own_pid is None else own_pid
    found = []

    def callback(hwnd, _lparam):
        try:
            info = _window_info(hwnd, own)
            if info is not None:
                found.append(info)
        except Exception:
            pass
        return True

    try:
        _u32.EnumWindows(_ENUMPROC(callback), 0)
    except Exception:
        return []
    return found


def window_frame(hwnd):
    """Where a platform window is now (left, top, right, bottom) - None once it can't be stood on
    any more (closed, hidden, minimised, maximised or moved to another virtual desktop)."""
    if _u32 is None or not hwnd:
        return None
    try:
        if (not _u32.IsWindow(hwnd) or not _u32.IsWindowVisible(hwnd) or _u32.IsIconic(hwnd)
                or _u32.IsZoomed(hwnd) or _cloaked(hwnd)):
            return None
        return _frame_rect(hwnd)
    except Exception:
        return None
