"""Buddy's pointer next to the mouse.

It runs on its own thread with its own small layered window, so it keeps following the mouse even
when the rest of Buddy (pet, focus tracker, settings window) is busy or the PC is slow:

* the window is only as big as the pointer (+ speech bubble), and following the mouse just moves it;
  nothing is repainted unless the picture changes;
* the spring is time based: a slow frame moves it further instead of slower, and after a stall
  (frame later than MAX_LAG_S) it jumps straight back to the mouse;
* the picture is drawn with Pillow (anti-aliased, per-pixel alpha); bubble text is drawn by Windows
  (GDI), which falls back to the right font for Chinese, Hindi, emoji, ...

Public API = the old Tk overlay (idle/listening/thinking/say/tour/hide/...), callable from any thread.
"""

import ctypes
import logging
import math
import threading
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)       # own handles: argtypes set here stay here
# Quick calls made every frame keep the GIL (PyDLL): giving it back and waiting to get it again costs a
# 15.6 ms timer tick each time while another thread is busy, which is what made the pointer stutter.
# Only safe on 64-bit Windows (one calling convention); 32-bit uses the normal handle.
fast_user32 = ctypes.PyDLL("user32") if ctypes.sizeof(ctypes.c_void_p) == 8 else user32
winmm = ctypes.WinDLL("winmm")
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INK = "#1f2130"
DEFAULT_COLOR = "#ff8424"
DEFAULT_ICON = "arrow"

# Settings -> Look. Name shown in the list -> colour.
COLORS = {
    "Orange": "#ff8424",
    "Blue": "#3b82f6",
    "Green": "#22c55e",
    "Pink": "#ec4899",
    "Purple": "#8b5cf6",
    "Teal": "#14b8a6",
    "Red": "#ef4444",
    "Yellow": "#facc15",
    "White": "#ffffff",
    "Black": "#1f2130",
}
# Settings -> Look. Name shown in the list -> icon id.
ICONS = {
    "Arrow": "arrow",
    "Mouse pointer": "pointer",
    "Paper plane": "plane",
    "Dot": "dot",
    "Star": "star",
    "Heart": "heart",
    "Buddy face": "face",
}
FACE_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "buddy.png"


# --------------------------------------------------------------------------- Win32 plumbing
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                       ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                                       ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.MsgWaitForMultipleObjectsEx.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                               wintypes.DWORD, wintypes.DWORD]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT),
                             wintypes.UINT]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetCurrentThread.restype = wintypes.HANDLE
kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
fast_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
fast_user32.SetWindowPos.argtypes = user32.SetWindowPos.argtypes
fast_user32.UpdateLayeredWindow.argtypes = user32.UpdateLayeredWindow.argtypes
fast_user32.PeekMessageW.argtypes = user32.PeekMessageW.argtypes
fast_user32.TranslateMessage.argtypes = user32.TranslateMessage.argtypes
fast_user32.DispatchMessageW.argtypes = user32.DispatchMessageW.argtypes
fast_user32.MonitorFromPoint.argtypes = user32.MonitorFromPoint.argtypes
fast_user32.MonitorFromPoint.restype = ctypes.c_void_p
fast_user32.GetMonitorInfoW.argtypes = user32.GetMonitorInfoW.argtypes
THREAD_PRIORITY_HIGHEST = 2
WAIT_TIMEOUT = 0x102
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 2
AC_SRC_ALPHA = 1
PM_REMOVE = 1
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
WM_NCHITTEST = 0x0084
WM_MOUSEACTIVATE = 0x0021
WM_APP_WAKE = 0x8000 + 17
HTTRANSPARENT = -1
MA_NOACTIVATE = 3
MONITOR_DEFAULTTONEAREST = 2
DT_WORDBREAK = 0x0010
DT_NOPREFIX = 0x0800
DT_EXPANDTABS = 0x0040
DT_CALCRECT = 0x0400
ANTIALIASED_QUALITY = 4


def cursor_position():
    point = wintypes.POINT()
    fast_user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def monitor_at(x, y):
    """Monitor rect (this process's coordinates) of the screen that holds (x, y)."""
    handle = fast_user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    fast_user32.GetMonitorInfoW(handle, ctypes.byref(info))
    rect = info.rcMonitor
    return {"left": rect.left, "top": rect.top, "width": rect.right - rect.left, "height": rect.bottom - rect.top}


def _inside(monitor, x, y):
    return (monitor is not None and monitor["left"] <= x < monitor["left"] + monitor["width"]
            and monitor["top"] <= y < monitor["top"] + monitor["height"])


def _rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))


def _colorref(color):
    red, green, blue = _rgb(color)
    return red | (green << 8) | (blue << 16)


def normalize_color(color):
    """'#RRGGBB' (lower case) or the default orange for anything unreadable."""
    try:
        if isinstance(color, str) and len(color.strip().lstrip("#")) == 6:
            _rgb(color.strip())
            return "#" + color.strip().lstrip("#").lower()
    except ValueError:
        pass
    return DEFAULT_COLOR


def normalize_icon(icon):
    return icon if icon in ICONS.values() else DEFAULT_ICON


# --------------------------------------------------------------------------- drawing (Pillow)
SUPERSAMPLE = 4
ICON_BOX = 64                       # px; the pointer picture, hotspot at ICON_HOT inside it
ICON_HOT = (22, 22)
_face_cache = {}


def _outline_for(color):
    """Dark outline for light colours and a light one for the dark 'Black' pointer."""
    red, green, blue = _rgb(color)
    return "#ffffff" if (0.299 * red + 0.587 * green + 0.114 * blue) < 70 else INK


def _star(cx, cy, outer, inner):
    points = []
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        radius = outer if index % 2 == 0 else inner
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def _heart(cx, cy, size):
    points = []
    for step in range(60):
        t = step / 60 * 2 * math.pi
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        points.append((cx + x * size / 32, cy - y * size / 32))
    return points


def _face(size):
    image = _face_cache.get(size)
    if image is None:
        try:
            image = Image.open(FACE_IMAGE).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            image = False
        _face_cache[size] = image
    return image or None


ANIMATION_FPS = 30
ANIMATION_FRAMES = 27                 # 0.9 s loop: the waveform repeats every 2*pi/7 s, the spinner turns 324 deg
_icon_cache = {}


def icon_frame(icon, color, mode, seconds):
    """draw_icon(), cached: listening/thinking loop over ANIMATION_FRAMES pictures, the rest is one picture."""
    if mode in ("listening", "thinking"):
        frame = int(seconds * ANIMATION_FPS) % ANIMATION_FRAMES
        key = (mode, color, frame)
        seconds = frame / ANIMATION_FPS
    else:
        key = (icon, color)
    picture = _icon_cache.get(key)
    if picture is None:
        if len(_icon_cache) > 200:
            _icon_cache.clear()
        picture = _icon_cache[key] = draw_icon(icon, color, mode, seconds)
    return picture


def draw_icon(icon, color, mode="idle", seconds=0.0):
    """The pointer picture (ICON_BOX square, RGBA) for a mode; the point it points with is ICON_HOT."""
    s = SUPERSAMPLE
    hx, hy = ICON_HOT
    image = Image.new("RGBA", (ICON_BOX * s, ICON_BOX * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    outline = _outline_for(color)

    def at(points):
        return [((hx + x) * s, (hy + y) * s) for x, y in points]

    def box(cx, cy, rx, ry):
        return [(hx + cx - rx) * s, (hy + cy - ry) * s, (hx + cx + rx) * s, (hy + cy + ry) * s]

    if mode == "listening":                       # waveform in a dark pill
        draw.ellipse(box(10, 12, 17, 15), fill=INK, outline=color, width=2 * s)
        for index in range(5):
            height = 3 + 8 * abs(math.sin(seconds * 7 + index * 0.9))
            x = 10 - 10 + index * 5
            draw.line(at([(x, 12 - height), (x, 12 + height)]), fill=color, width=3 * s)
            draw.ellipse(box(x, 12 - height, 1.5, 1.5), fill=color)
            draw.ellipse(box(x, 12 + height, 1.5, 1.5), fill=color)
    elif mode == "thinking":                      # spinner in a dark circle
        draw.ellipse(box(10, 12, 14, 14), fill=INK)
        start = (seconds / (ANIMATION_FRAMES / ANIMATION_FPS) * 360) % 360   # one turn per loop
        draw.arc(box(10, 12, 9, 9), start=start, end=start + 270, fill=color, width=3 * s)
    elif icon == "pointer":
        draw.polygon(at([(0, 0), (0, 23), (6, 18), (10, 27), (14, 25), (10, 16.5), (17, 16.5)]),
                     fill=color, outline=outline, width=2 * s)
    elif icon == "plane":
        draw.polygon(at([(0, 0), (26, 9), (13, 13), (9, 26)]), fill=color, outline=outline, width=2 * s)
        draw.line(at([(1, 1), (13, 13)]), fill=outline, width=2 * s)
    elif icon == "dot":
        draw.ellipse(box(9, 9, 9, 9), fill=color, outline=outline, width=2 * s)
    elif icon == "star":
        draw.polygon(at(_star(12, 12, 13, 5.5)), fill=color, outline=outline, width=2 * s)
    elif icon == "heart":
        draw.polygon(at(_heart(12, 12, 26)), fill=color, outline=outline, width=2 * s)
    elif icon == "face":
        face = _face(30 * s)
        if face is not None:
            draw.ellipse(box(15, 15, 17, 17), fill=color)
            image.alpha_composite(face, (int((hx) * s), int((hy) * s)))
        else:
            draw.ellipse(box(14, 14, 14, 14), fill=color, outline=outline, width=2 * s)
    else:                                         # the classic Buddy arrow
        draw.polygon(at([(0, 0), (6, 26), (12, 18), (24, 22)]), fill=color, outline=outline, width=2 * s)
    return image.resize((ICON_BOX, ICON_BOX), Image.LANCZOS)


def preview_image(color, icon, size=48):
    """Small picture of the pointer for the Settings window."""
    picture = draw_icon(normalize_icon(icon), normalize_color(color))
    left, top = ICON_HOT[0] - 6, ICON_HOT[1] - 6
    return picture.crop((left, top, left + 40, top + 40)).resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------- bubble text (GDI)
BUBBLE_TEXT_WIDTH = 340
BUBBLE_PAD = (12, 8)
BUBBLE_MAX_TEXT_HEIGHT = 520


class _TextRenderer:
    """Wrapped white text on the ink colour, drawn by Windows so every script gets a font."""

    def __init__(self):
        self.font = gdi32.CreateFontW(-15, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, ANTIALIASED_QUALITY, 0, "Segoe UI")
        self.cache = {}

    def render(self, text):
        image = self.cache.get(text)
        if image is not None:
            return image
        hdc = gdi32.CreateCompatibleDC(None)
        old_font = gdi32.SelectObject(hdc, self.font)
        try:
            flags = DT_WORDBREAK | DT_NOPREFIX | DT_EXPANDTABS
            rect = wintypes.RECT(0, 0, BUBBLE_TEXT_WIDTH, 0)
            user32.DrawTextW(hdc, text, -1, ctypes.byref(rect), flags | DT_CALCRECT)
            width = max(1, min(BUBBLE_TEXT_WIDTH, rect.right))
            height = max(1, min(BUBBLE_MAX_TEXT_HEIGHT, rect.bottom))
            bits = ctypes.c_void_p()
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            bitmap = gdi32.CreateDIBSection(hdc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            old_bitmap = gdi32.SelectObject(hdc, bitmap)
            brush = gdi32.CreateSolidBrush(_colorref(INK))
            area = wintypes.RECT(0, 0, width, height)
            user32.FillRect(hdc, ctypes.byref(area), brush)
            gdi32.DeleteObject(brush)
            gdi32.SetBkMode(hdc, 1)
            gdi32.SetTextColor(hdc, _colorref("#ffffff"))
            user32.DrawTextW(hdc, text, -1, ctypes.byref(area), flags)
            gdi32.GdiFlush()
            raw = ctypes.string_at(bits, width * height * 4)
            pixels = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
            image = Image.fromarray(np.ascontiguousarray(pixels[..., 2::-1])).convert("RGBA")
            gdi32.SelectObject(hdc, old_bitmap)
            gdi32.DeleteObject(bitmap)
        finally:
            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteDC(hdc)
        if len(self.cache) > 32:
            self.cache.clear()
        self.cache[text] = image
        return image

    def bubble(self, text, color):
        """Speech bubble (RGBA): rounded ink box with a coloured border around the text."""
        body = self.render(text)
        pad_x, pad_y = BUBBLE_PAD
        width, height = body.width + 2 * pad_x, body.height + 2 * pad_y
        s = SUPERSAMPLE
        frame = Image.new("RGBA", (width * s, height * s), (0, 0, 0, 0))
        ImageDraw.Draw(frame).rounded_rectangle([s, s, (width - 1) * s, (height - 1) * s], radius=9 * s,
                                                fill=INK, outline=color, width=2 * s)
        frame = frame.resize((width, height), Image.LANCZOS)
        frame.alpha_composite(body, (pad_x, pad_y))
        return frame


# --------------------------------------------------------------------------- the overlay
class BuddyOverlay:
    """Transparent, click-through, never-focused pointer next to the mouse (Clicky style).

    With always_on the pointer follows the mouse all the time (springy); otherwise it only shows up
    while you ask. Listening = waveform, thinking = spinner, answering = text bubble, pointing = it
    flies to the target and then springs back to the mouse. Safe to call from any thread.
    """

    CURSOR_OFFSET = (16, 18)       # sit just below-right of the real mouse pointer
    STIFFNESS = 900.0              # spring pull per px (1/s^2): ~0.2 s to catch up
    DAMPING = 38.0                 # 1/s; a little under critical (60) = a little springy
    SUBSTEP_S = 1 / 240
    MAX_LAG_S = 0.25               # frame later than this (busy PC): jump straight to the mouse
    FRAME_S = 0.016
    HIDDEN_POLL_S = 0.25
    TOPMOST_EVERY_S = 2.0
    FOLLOW_MODES = ("idle", "listening", "thinking", "talking")

    def __init__(self, root=None, always_on=False, color=DEFAULT_COLOR, icon=DEFAULT_ICON):
        self.root = root                          # kept for callers; the overlay does not use Tk
        self._lock = threading.RLock()
        self.monitor = None
        self._pos = None                          # screen position of the hotspot (floats)
        self._vel = (0.0, 0.0)
        self.mode = None                          # None (hidden), idle/listening/thinking/talking, tour, shot
        self.always_on = bool(always_on)
        self._text = None
        self._mode_started = time.perf_counter()
        self._hide_at = None
        self._tour = None
        self._pulse_started = None
        self.color = normalize_color(color)
        self.icon = normalize_icon(icon)
        self._shot_done = threading.Event()
        self._ready = threading.Event()
        self.hwnd = None
        self._thread = threading.Thread(target=self._run, name="BuddyPointer", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        if self.always_on:
            self.idle()

    # -- coordinates (callers use monitor-local positions, like the old overlay)
    def attach(self, monitor):
        with self._lock:
            self.monitor = dict(monitor)
        self._wake()

    def local(self, x, y):
        monitor = self.monitor or monitor_at(x, y)
        return x - monitor["left"], y - monitor["top"]

    @property
    def position(self):
        with self._lock:
            if self._pos is None:
                return 0.0, 0.0
            monitor = self.monitor or monitor_at(*self._pos)
            return self._pos[0] - monitor["left"], self._pos[1] - monitor["top"]

    @position.setter
    def position(self, value):
        with self._lock:
            monitor = self.monitor or monitor_at(*cursor_position())
            self._pos = (float(value[0] + monitor["left"]), float(value[1] + monitor["top"]))
            self._vel = (0.0, 0.0)
        self._wake()

    # -- look
    def set_style(self, color=None, icon=None):
        with self._lock:
            if color is not None:
                self.color = normalize_color(color)
            if icon is not None:
                self.icon = normalize_icon(icon)
        self._wake()

    # -- states (same calls as the old Tk overlay)
    def set_always_on(self, enabled):
        self.always_on = bool(enabled)
        if self.always_on and self.mode is None:
            self.idle()
        elif not self.always_on and self.mode == "idle":
            self.hide()

    def idle(self):
        if self.always_on:
            self._set_mode("idle")
        else:
            self.hide()

    def listening(self, monitor, cursor):
        self.attach(monitor)
        self._set_mode("listening")

    def thinking(self, text="Thinking\u2026"):
        self._set_mode("thinking", text)

    def say(self, text):
        with self._lock:
            if self.mode == "tour":
                self._text = text                 # shown when the tour has arrived
            elif self.mode == "talking":
                self._text = text                 # streaming: just swap the words
            else:
                self._set_mode("talking", text)
                return
        self._wake()

    def clear_for_screenshot(self):
        """Hide right now (returns once the pointer is off the screen, max ~0.5 s)."""
        with self._lock:
            self._tour = None
            self._hide_at = None
            self.mode = "shot"
            self._shot_done.clear()
        self._wake()
        if threading.current_thread() is not self._thread:
            self._shot_done.wait(0.5)

    def tour(self, screen_points, answer_text, dwell_ms=1800):
        """Fly to each (x, y, label) in order, rest there with the answer, then spring back to the mouse."""
        with self._lock:
            self._hide_at = None
            if not screen_points:
                self._text = answer_text
                self.mode = "talking"
                self._wake()
                return
            first_x, first_y = screen_points[0][:2]
            if not _inside(self.monitor, first_x, first_y):
                self.monitor = monitor_at(first_x, first_y)
            if self._pos is None:
                self._pos = tuple(float(value) for value in cursor_position())
            self.mode = "tour"
            self._text = None
            self._vel = (0.0, 0.0)
            self._tour = {"points": [(float(x), float(y), label) for x, y, label in screen_points],
                          "answer": answer_text, "dwell": dwell_ms / 1000.0,
                          "index": -1, "leg": None}
        self._wake()

    def hide_later(self, delay_ms):
        with self._lock:
            self._hide_at = time.perf_counter() + delay_ms / 1000.0
        self._wake()

    def hide(self):
        """Done talking: back to resting by the mouse (always on), or gone."""
        with self._lock:
            self._tour = None
            self._hide_at = None
            self._text = None
            if self.always_on:
                self._set_mode("idle")
                return
            self.mode = None
        self._wake()

    def _set_mode(self, mode, text=None):
        with self._lock:
            if self.mode not in self.FOLLOW_MODES and self.mode != "tour":
                self._pos = None                  # appear right at the mouse, no fly-in
            if mode != self.mode:
                self._mode_started = time.perf_counter()
            self._tour = None
            self._hide_at = None
            self.mode = mode
            self._text = text
        self._wake()

    def _wake(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_WAKE, 0, 0)

    # -- the pointer thread
    def _wnd_proc(self, hwnd, message, wparam, lparam):
        if message == WM_NCHITTEST:
            return HTTRANSPARENT
        if message == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _create_window(self):
        instance = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._wnd_proc)            # keep a reference for the window's lifetime
        class_name = f"BuddyPointer{id(self)}"
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(window_class))
        style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        self.hwnd = user32.CreateWindowExW(style, class_name, "Buddy pointer", WS_POPUP,
                                           0, 0, 1, 1, None, None, instance, None)

    def _run(self):
        try:
            self._create_window()
        except Exception:
            logger.exception("Buddy pointer window failed")
        finally:
            self._ready.set()
        if not self.hwnd:
            return
        self._screen_dc = user32.GetDC(None)
        self._memory_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        self._bitmap = None
        self._bitmap_size = None
        self._bits = None
        self._text_renderer = _TextRenderer()
        self._shown = False
        self._window_origin = None
        self._picture_key = None
        self._last_topmost = 0.0
        self._moving_until = 0.0
        self._fine_timer = False
        # Its work is tiny, so it may jump the queue when other apps keep the CPU busy
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)
        last = time.perf_counter()
        message = wintypes.MSG()
        has_input = True
        while True:
            if has_input:
                while fast_user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
                    fast_user32.TranslateMessage(ctypes.byref(message))
                    fast_user32.DispatchMessageW(ctypes.byref(message))
            now = time.perf_counter()
            dt, last = now - last, now
            try:
                shown = self._step(now, dt)
            except Exception:
                logger.exception("Buddy pointer frame failed")
                shown = False
            self._use_fine_timer(shown and now < self._moving_until)
            timeout = self.FRAME_S if shown else self.HIDDEN_POLL_S
            result = user32.MsgWaitForMultipleObjectsEx(0, None, max(1, int(timeout * 1000)), QS_ALLINPUT,
                                                        MWMO_INPUTAVAILABLE)
            has_input = result != WAIT_TIMEOUT

    def _use_fine_timer(self, wanted):
        """1 ms Windows timer while the pointer moves or animates (smooth frames, quick thread switches);
        back to normal when it rests, to save battery."""
        if wanted and not self._fine_timer:
            winmm.timeBeginPeriod(1)
        elif not wanted and self._fine_timer:
            winmm.timeEndPeriod(1)
        self._fine_timer = wanted

    def _step(self, now, dt):
        """One frame. Returns False when hidden (then it can sleep longer)."""
        with self._lock:
            if self._hide_at is not None and now >= self._hide_at:
                self._hide_at = None
                self._tour = None
                self._text = None
                if self.always_on:
                    if self.mode not in self.FOLLOW_MODES and self.mode != "tour":
                        self._pos = None
                    self.mode = "idle"
                    self._mode_started = now
                else:
                    self.mode = None
            mode = self.mode
            if mode is None or mode == "shot":
                self._set_shown(False)
                self._shot_done.set()
                return False
            if mode == "tour":
                caption, pulse = self._step_tour(now)
            else:
                caption, pulse = self._step_follow(dt), None
            text = caption if mode == "tour" else self._text
            x, y = self._pos
            color, icon = self.color, self.icon
            seconds = now - self._mode_started
            monitor = self.monitor or monitor_at(x, y)
        self._present(x, y, monitor, mode, icon, color, text, seconds, pulse)
        return True

    def _step_follow(self, dt):
        mouse_x, mouse_y = cursor_position()
        if not _inside(self.monitor, mouse_x, mouse_y):
            self.monitor = monitor_at(mouse_x, mouse_y)
        monitor = self.monitor
        offset_x, offset_y = self.CURSOR_OFFSET
        target_x = min(max(mouse_x + offset_x, monitor["left"]), monitor["left"] + monitor["width"] - 30)
        target_y = min(max(mouse_y + offset_y, monitor["top"]), monitor["top"] + monitor["height"] - 30)
        if self._pos is None or dt > self.MAX_LAG_S:
            self._pos, self._vel = (float(target_x), float(target_y)), (0.0, 0.0)
            return None
        (x, y), (vx, vy) = self._pos, self._vel
        remaining = dt
        while remaining > 1e-6:
            step = min(self.SUBSTEP_S, remaining)
            vx += ((target_x - x) * self.STIFFNESS - vx * self.DAMPING) * step
            vy += ((target_y - y) * self.STIFFNESS - vy * self.DAMPING) * step
            x += vx * step
            y += vy * step
            remaining -= step
        if abs(target_x - x) < 0.4 and abs(target_y - y) < 0.4 and abs(vx) < 5 and abs(vy) < 5:
            x, y, vx, vy = float(target_x), float(target_y), 0.0, 0.0
        self._pos, self._vel = (x, y), (vx, vy)
        return None

    def _step_tour(self, now):
        """Curved flights from target to target. Returns (caption, pulse 0..1 or None)."""
        tour = self._tour
        if tour is None:                          # finished: rest at the last spot with the answer
            pulse = None
            if self._pulse_started is not None and now - self._pulse_started < 0.45:
                pulse = (now - self._pulse_started) / 0.45
            return self._text, pulse
        points = tour["points"]
        leg = tour["leg"]
        if leg is None or (leg["arrived"] is not None and now - leg["arrived"] >= tour["dwell"]):
            tour["index"] += 1
            if tour["index"] >= len(points):      # done: show the answer, then go home later
                self._tour = None
                if self._text is None:
                    self._text = tour["answer"]
                self._hide_at = now + 9.0
                return self._text, None
            tx, ty, label = points[tour["index"]]
            sx, sy = self._pos
            dx, dy = tx - sx, ty - sy
            leg = {"start": now, "from": (sx, sy), "to": (tx, ty),
                   "control": ((sx + tx) / 2 - dy * 0.25, (sy + ty) / 2 + dx * 0.25),
                   "duration": min(0.9, 0.35 + math.hypot(dx, dy) / 2500), "arrived": None,
                   "caption": (f"{tour['index'] + 1}. {label}" if len(points) > 1 and label else label or None)}
            tour["leg"] = leg
        if leg["arrived"] is None:
            t = min(1.0, (now - leg["start"]) / leg["duration"])
            eased = 1 - (1 - t) ** 3
            (sx, sy), (cx, cy), (tx, ty) = leg["from"], leg["control"], leg["to"]
            self._pos = ((1 - eased) ** 2 * sx + 2 * (1 - eased) * eased * cx + eased ** 2 * tx,
                         (1 - eased) ** 2 * sy + 2 * (1 - eased) * eased * cy + eased ** 2 * ty)
            if t >= 1.0:
                leg["arrived"] = now
                self._pulse_started = now
            return None, None
        pulse = None
        if now - leg["arrived"] < 0.45:
            pulse = (now - leg["arrived"]) / 0.45
        return leg["caption"], pulse

    # -- drawing on screen
    def _present(self, x, y, monitor, mode, icon, color, text, seconds, pulse):
        animated = mode in ("listening", "thinking")
        frame = int(seconds * ANIMATION_FPS) % ANIMATION_FRAMES if animated else 0
        hot_x, hot_y = int(round(x)), int(round(y))
        rects = [(hot_x - ICON_HOT[0], hot_y - ICON_HOT[1], ICON_BOX, ICON_BOX)]
        bubble = None
        if text:
            bubble = self._text_renderer.bubble(text, color)
            right = monitor["left"] + monitor["width"] - 8
            bottom = monitor["top"] + monitor["height"] - 8
            bubble_x = min(hot_x + 28, right - bubble.width)
            bubble_y = min(hot_y + 24, bottom - bubble.height)
            rects.append((max(bubble_x, monitor["left"] + 4), max(bubble_y, monitor["top"] + 4),
                          bubble.width, bubble.height))
        if pulse is not None:
            radius = 30
            rects.append((hot_x - radius - 2, hot_y - radius - 2, 2 * radius + 4, 2 * radius + 4))
        left = min(rect[0] for rect in rects)
        top = min(rect[1] for rect in rects)
        right = max(rect[0] + rect[2] for rect in rects)
        bottom = max(rect[1] + rect[3] for rect in rects)
        layout = tuple((rect[0] - left, rect[1] - top) for rect in rects)
        key = (mode, icon, color, text, frame, layout, None if pulse is None else round(pulse * 20),
               right - left, bottom - top)
        if animated or pulse is not None or key != self._picture_key or (left, top) != self._window_origin:
            self._moving_until = time.perf_counter() + 1.0
        if key != self._picture_key:
            picture = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
            if pulse is not None:
                radius = 18 + 10 * pulse
                alpha = int(255 * (1 - pulse))
                center_x, center_y = hot_x - left, hot_y - top
                ImageDraw.Draw(picture).ellipse(
                    [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
                    outline=_rgb(color) + (alpha,), width=2)
            picture.alpha_composite(icon_frame(icon, color, mode, seconds), layout[0])
            if bubble is not None:
                picture.alpha_composite(bubble, layout[1])
            self._upload(picture, left, top)
            self._picture_key = key
            self._window_origin = (left, top)
        elif (left, top) != self._window_origin:
            fast_user32.SetWindowPos(self.hwnd, None, left, top, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            self._window_origin = (left, top)
        self._set_shown(True)

    def _upload(self, picture, left, top):
        width, height = picture.size
        if self._bitmap_size != (width, height):
            if self._bitmap:
                gdi32.SelectObject(self._memory_dc, self._old_bitmap)
                gdi32.DeleteObject(self._bitmap)
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            self._bits = ctypes.c_void_p()
            self._bitmap = gdi32.CreateDIBSection(self._memory_dc, ctypes.byref(info), 0,
                                                  ctypes.byref(self._bits), None, 0)
            self._old_bitmap = gdi32.SelectObject(self._memory_dc, self._bitmap)
            self._bitmap_size = (width, height)
        rgba = np.asarray(picture, dtype=np.uint16)
        alpha = rgba[..., 3:4]
        premultiplied = (rgba[..., :3] * alpha + 127) // 255
        bgra = np.empty((height, width, 4), dtype=np.uint8)
        bgra[..., 0] = premultiplied[..., 2]
        bgra[..., 1] = premultiplied[..., 1]
        bgra[..., 2] = premultiplied[..., 0]
        bgra[..., 3] = rgba[..., 3]
        ctypes.memmove(self._bits, bgra.tobytes(), width * height * 4)
        blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
        fast_user32.UpdateLayeredWindow(self.hwnd, self._screen_dc, ctypes.byref(wintypes.POINT(left, top)),
                                   ctypes.byref(wintypes.SIZE(width, height)), self._memory_dc,
                                   ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)

    def _set_shown(self, shown):
        now = time.perf_counter()
        if shown and (not self._shown or now - self._last_topmost > self.TOPMOST_EVERY_S):
            if not self._shown:
                user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self._last_topmost = now
        elif not shown and self._shown:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self._picture_key = None
        self._shown = shown
