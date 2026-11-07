"""Buddy Island: Buddy docked at the top of the screen, like a notch or a phone's Dynamic Island.

A small black notch sits at the top centre of the display under the mouse. It is always there, so Buddy is
one move away even when you forget the hotkey:

* rest the mouse on it for a moment and it opens: "Ask Buddy";
* click it to type a question, press and hold it to talk (release to send), exactly like Ctrl+Alt+Space;
* while Buddy listens, thinks and answers, the island grows into a live strip (waveform, spinner, the answer
  text) and shrinks back into the notch when it is done. Click an answer to dismiss it;
* it is click-through while it is a notch (it never steals a click meant for a browser tab under it), takes no
  focus, hides while a full-screen app, game or presentation runs, and never shows up in Buddy's screenshot.

Like the pointer, it runs on its own thread with its own layered window. The thread is per-monitor DPI aware,
so the island is drawn sharp at every scaling, in physical pixels of the monitor it sits on.

The layout and state logic (island_size, place, IslandState, render) is plain Python and tested without
a window; BuddyIsland is the Win32 part.
"""

import ctypes
import logging
import math
import threading
import time
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw

import buddy_overlay as overlay

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- sizes (logical px at 100 %)
NOTCH = (112, 16)               # resting: a slim notch; click-through
HOVER = (264, 38)               # mouse resting on it: "Ask Buddy"
BUSY = (300, 38)                # listening / thinking with one line of text
ANSWER_WIDTH = 440              # answers wrap inside this width
TEXT_MAX_HEIGHT = 230           # tallest answer text before it is cut (about 10 lines)
PAD_X = 16
PAD_Y = 10
ICON_SLOT = 26                  # left column for the dot / waveform / spinner
EAR = 8                         # the concave flare where a flush notch meets the screen edge
FLOAT_GAP = 6                   # gap under a top taskbar, where the island floats as a pill
FONT_PX = 14
INK_BLACK = "#000000"
MUTED = "#a1a1aa"               # 7.8:1 on black
HOVER_DWELL_S = 0.35            # resting this long on the notch opens it (a quick pass over it does not)
SHOT_HIDE_S = 1.5               # hidden at most this long for Buddy's screenshot
HOLD_TO_TALK_S = 0.35           # = keyit_buddy.TAP_SECONDS
MORPH_RATE = 16.0               # 1/s; how fast the shape follows its target size
SUPERSAMPLE = 3

# SHQueryUserNotificationState: full-screen app / D3D game / presentation -> keep out of the way
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUIET_STATES = {QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE}

ACTIVE_MODES = ("listening", "thinking", "talking")


# --------------------------------------------------------------------------- pure layout
def scaled(size, scale):
    return int(round(size[0] * scale)), int(round(size[1] * scale))


def place(monitor, work, width, height, scale=1.0):
    """Where the island goes: (x, y, flush). Centred on the monitor; flush with the top edge (a notch) unless
    a taskbar sits at the top, then it floats just under it as a pill. Rects are dicts left/top/width/height."""
    x = monitor["left"] + (monitor["width"] - width) // 2
    flush = work["top"] <= monitor["top"]
    y = monitor["top"] if flush else work["top"] + int(round(FLOAT_GAP * scale))
    return x, y, flush


def island_size(mode, hovered, text_size=None, scale=1.0):
    """Target (width, height) in physical px of the shape (without the ears) for a state."""
    if mode == "talking" or (mode in ("listening", "thinking") and text_size is not None):
        text_w, text_h = text_size or (0, 0)
        min_w, min_h = scaled(BUSY, scale)
        width = max(min_w, text_w + int(round((2 * PAD_X + ICON_SLOT) * scale)))
        height = max(min_h, text_h + int(round(2 * PAD_Y * scale)))
        return width, height
    if mode in ACTIVE_MODES:
        return scaled(BUSY, scale)
    if hovered:
        return scaled(HOVER, scale)
    return scaled(NOTCH, scale)


def reading_seconds(text):
    """How long an answer stays open: time to read it, between 6 and 25 s."""
    return max(6.0, min(25.0, 3.0 + 0.055 * len(text or "")))


def approach(current, target, dt, rate=MORPH_RATE):
    """Frame-rate independent ease towards target (critically damped feel, no overshoot)."""
    if abs(target - current) < 0.5:
        return float(target)
    return current + (target - current) * (1.0 - math.exp(-rate * max(0.0, dt)))


class IslandState:
    """What the island shows, independent of Windows: mode + text + timers. Times are time.perf_counter()."""

    def __init__(self):
        self.mode = "idle"            # idle / listening / thinking / talking
        self.text = ""
        self.since = 0.0
        self.close_at = None
        self.shot = False             # hidden while Buddy takes its screenshot
        self.shot_until = 0.0
        self.hover_since = None
        self.hovered = False

    def set(self, mode, text="", now=None, close_after=None):
        now = time.perf_counter() if now is None else now
        if mode != self.mode:
            self.since = now
        self.mode = mode
        self.text = text or ""
        self.shot = False
        self.close_at = None if close_after is None else now + close_after

    def say(self, text, now=None):
        now = time.perf_counter() if now is None else now
        self.set("talking", text, now, close_after=reading_seconds(text))

    def idle_later(self, seconds, now=None):
        now = time.perf_counter() if now is None else now
        if self.mode != "idle":
            self.close_at = now + seconds

    def hide_for_shot(self, now=None, seconds=SHOT_HIDE_S):
        now = time.perf_counter() if now is None else now
        self.shot = True
        self.shot_until = now + seconds

    def tick(self, now, mouse_inside):
        """Advance timers; returns True when something changed."""
        changed = False
        if self.shot and now >= self.shot_until:
            self.shot = False                        # the screenshot is long done: back in the notch
            changed = True
        if mouse_inside:
            if self.hover_since is None:
                self.hover_since = now
            if not self.hovered and now - self.hover_since >= HOVER_DWELL_S:
                self.hovered = True
                changed = True
        else:
            self.hover_since = None
            if self.hovered:
                self.hovered = False
                changed = True
        if self.close_at is not None and now >= self.close_at:
            if self.mode == "talking" and self.hovered:
                self.close_at = now + 1.0            # reading it: keep it open while the mouse is on it
            else:
                self.set("idle", now=now)
                changed = True
        return changed

    @property
    def interactive(self):
        """Clickable only when open; the resting notch lets clicks through to whatever is under it."""
        return self.hovered or self.mode in ACTIVE_MODES

    def caption(self):
        if self.mode == "idle":
            return "Ask Buddy" if self.hovered else ""
        if self.mode == "listening":
            return self.text or "Listening\u2026  release to send"
        if self.mode == "thinking":
            return self.text or "Thinking\u2026"
        return self.text


# --------------------------------------------------------------------------- drawing
def _rgb(color):
    return overlay._rgb(overlay.normalize_color(color))


def shape_mask(width, height, flush, scale=1.0):
    """Alpha mask (L) of the island incl. its ears. Flush: square top, round bottom, concave ears into the
    screen edge (like a laptop notch). Floating: a pill with all corners round (like a Dynamic Island)."""
    s = SUPERSAMPLE
    ear = int(round(EAR * scale)) if flush else 0
    full_w = width + 2 * ear
    mask = Image.new("L", (full_w * s, height * s), 0)
    draw = ImageDraw.Draw(mask)
    radius = min(height / 2.0, 22 * scale) * s
    left, right = ear * s, (ear + width) * s - 1
    if flush:
        draw.rounded_rectangle([left, -radius, right, height * s - 1], radius=radius, fill=255)
        if ear:
            e = ear * s
            draw.rectangle([0, 0, e, e], fill=255)
            draw.ellipse([-e, 0, e, 2 * e], fill=0)
            draw.rectangle([full_w * s - e - 1, 0, full_w * s - 1, e], fill=255)
            draw.ellipse([full_w * s - e - 1, 0, full_w * s + e - 1, 2 * e], fill=0)
            draw.rectangle([left, 0, right, height * s // 2], fill=255)
    else:
        draw.rounded_rectangle([left, 0, right, height * s - 1], radius=radius, fill=255)
    return mask.resize((full_w, height), Image.LANCZOS), ear


def _glyph(draw, mode, color, cx, cy, seconds, scale):
    accent = _rgb(color)
    if mode == "listening":
        bar_w = max(2, int(round(2.6 * scale)))
        for index in range(4):
            level = 0.3 + 0.7 * abs(math.sin(seconds * 7.0 + index * 1.25))
            half = (3 + 7 * level) * scale
            x = cx - 7 * scale + index * 4.6 * scale
            draw.rounded_rectangle([x, cy - half, x + bar_w, cy + half], radius=bar_w / 2, fill=accent)
    elif mode == "thinking":
        radius = 7 * scale
        start = (seconds * 360.0) % 360.0
        draw.arc([cx - radius, cy - radius, cx + radius, cy + radius], start, start + 260, fill=accent,
                 width=max(2, int(round(2.4 * scale))))
    else:
        radius = 3.6 * scale * (1.25 if mode == "talking" else 1.0)
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=accent)


def render(state_mode, caption, width, height, flush, color, seconds, scale=1.0, text_image=None):
    """The island picture (RGBA): black shape, accent glyph on the left, text on the right."""
    mask, ear = shape_mask(width, height, flush, scale)
    picture = Image.new("RGBA", mask.size, _rgb(INK_BLACK) + (255,))
    picture.putalpha(mask)
    draw = ImageDraw.Draw(picture)
    resting_notch = state_mode == "idle" and not caption
    if resting_notch:
        cx = ear + width / 2.0
        cy = height / 2.0
        _glyph(draw, "idle", color, cx, cy, seconds, min(scale, height / 16.0))
        return picture
    icon_cx = ear + (PAD_X + ICON_SLOT / 2.0 - 4) * scale
    icon_cy = PAD_Y * scale + 9 * scale if text_image is not None and text_image.height > 24 * scale \
        else height / 2.0
    _glyph(draw, state_mode, color, icon_cx, icon_cy, seconds, scale)
    if text_image is not None:
        text_x = ear + int(round((PAD_X + ICON_SLOT) * scale))
        text_y = max(int(round(PAD_Y * scale)), (height - text_image.height) // 2)
        # Clip to the shape so a morphing island never shows text outside its outline
        room_w = max(0, ear + width - int(round(PAD_X * scale)) - text_x)
        room_h = max(0, height - text_y - int(round(4 * scale)))
        clipped = text_image.crop((0, 0, min(text_image.width, room_w), min(text_image.height, room_h)))
        if clipped.width and clipped.height:
            picture.alpha_composite(clipped, (text_x, text_y))
            picture.putalpha(mask)
    return picture


# --------------------------------------------------------------------------- Win32
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32")
_kernel32 = ctypes.WinDLL("kernel32")
try:
    _shcore = ctypes.WinDLL("shcore")
except OSError:                                    # Windows 7
    _shcore = None

_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = ctypes.c_void_p
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(overlay.MONITORINFO)]
_shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
if _shcore is not None:
    _shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                                         ctypes.POINTER(ctypes.c_uint)]

GWL_EXSTYLE = -20
VK_LBUTTON = 0x01
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONUP = 0x0205
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


def _monitor_info(x, y):
    handle = _user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), overlay.MONITOR_DEFAULTTONEAREST)
    info = overlay.MONITORINFO()
    info.cbSize = ctypes.sizeof(overlay.MONITORINFO)
    _user32.GetMonitorInfoW(handle, ctypes.byref(info))

    def rect(r):
        return {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}

    scale = 1.0
    if _shcore is not None:
        dpi_x, dpi_y = ctypes.c_uint(96), ctypes.c_uint(96)
        if _shcore.GetDpiForMonitor(handle, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
            scale = max(1.0, dpi_x.value / 96.0)
    return rect(info.rcMonitor), rect(info.rcWork), scale


def quiet_time():
    """True while a full-screen app, game or presentation runs (the island gets out of the way)."""
    state = ctypes.c_int(0)
    try:
        if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except OSError:
        return False
    return state.value in QUIET_STATES


def _cursor():
    point = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


class BuddyIsland:
    """The island window. Safe to call from any thread.

    on_press() / on_release(): like pressing and releasing Ctrl+Alt+Space (tap = type, hold = talk).
    on_dismiss(): the user clicked an open answer (stop talking). on_menu(): right click (Buddy's settings).
    """

    FRAME_S = 1 / 60
    REST_POLL_S = 0.1
    QUIET_POLL_S = 1.0
    TOPMOST_EVERY_S = 2.0

    def __init__(self, color=overlay.DEFAULT_COLOR, on_press=None, on_release=None, on_dismiss=None,
                 on_menu=None):
        self.color = overlay.normalize_color(color)
        self.on_press = on_press or (lambda: None)
        self.on_release = on_release or (lambda: None)
        self.on_dismiss = on_dismiss or (lambda: None)
        self.on_menu = on_menu or (lambda: None)
        self.state = IslandState()
        self._lock = threading.RLock()
        self._shot_done = threading.Event()
        self._ready = threading.Event()
        self._stop = False
        self.hwnd = None
        self.rect = None                       # (x, y, w, h) on screen, physical px, while shown
        self.shown = False
        self.quiet = False
        self.frames = 0
        self.last_error = None
        self._thread = threading.Thread(target=self._run, name="BuddyIsland", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)

    # -- public API (any thread)
    def set_color(self, color):
        with self._lock:
            self.color = overlay.normalize_color(color)
        self._wake()

    def listening(self, text=""):
        with self._lock:
            self.state.set("listening", text)
        self._wake()

    def thinking(self, text=""):
        with self._lock:
            self.state.set("thinking", text)
        self._wake()

    def say(self, text):
        with self._lock:
            if self.state.mode == "talking":
                self.state.text = text or ""            # streaming: swap the words, restart the reading time
                self.state.close_at = time.perf_counter() + reading_seconds(text)
            else:
                self.state.say(text)
        self._wake()

    def idle(self):
        with self._lock:
            self.state.set("idle")
        self._wake()

    def idle_later(self, seconds):
        with self._lock:
            self.state.idle_later(seconds)
        self._wake()

    def clear_for_screenshot(self):
        """Hide now; returns once the island is off the screen (max ~0.5 s). The next state shows it again."""
        with self._lock:
            self.state.hide_for_shot()
            self._shot_done.clear()
        self._wake()
        if threading.current_thread() is not self._thread and self.hwnd:
            self._shot_done.wait(0.5)

    def close(self):
        self._stop = True
        self._wake()
        self._thread.join(2.0)

    # -- window thread
    def _wake(self):
        if self.hwnd:
            overlay.user32.PostMessageW(self.hwnd, overlay.WM_APP_WAKE, 0, 0)

    def _wnd_proc(self, hwnd, message, wparam, lparam):
        if message == overlay.WM_MOUSEACTIVATE:
            return overlay.MA_NOACTIVATE                  # never take focus from the app you are asking about
        if message == WM_LBUTTONDOWN:
            self._clicked()
            return 0
        if message == WM_RBUTTONUP:
            self._safe(self.on_menu)
            return 0
        return overlay.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    @staticmethod
    def _safe(callback):
        try:
            callback()
        except Exception:
            logger.exception("Buddy island callback failed")

    def _clicked(self):
        with self._lock:
            mode = self.state.mode
        if mode == "talking":
            self._safe(self.on_dismiss)
            self.idle()
            return
        if mode in ("listening", "thinking"):
            return
        self._safe(self.on_press)
        threading.Thread(target=self._wait_for_release, name="BuddyIslandHold", daemon=True).start()

    def _wait_for_release(self):
        """Hold = talk: the window may hide for the screenshot meanwhile, so watch the button itself."""
        deadline = time.perf_counter() + 120
        while _user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000 and time.perf_counter() < deadline:
            time.sleep(0.015)
        self._safe(self.on_release)

    def _create_window(self):
        instance = overlay.kernel32.GetModuleHandleW(None)
        self._wndproc = overlay.WNDPROC(self._wnd_proc)
        class_name = f"BuddyIsland{id(self)}"
        window_class = overlay.WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(overlay.WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        window_class.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(32649))     # IDC_HAND
        overlay.user32.RegisterClassExW(ctypes.byref(window_class))
        style = (overlay.WS_EX_LAYERED | overlay.WS_EX_TRANSPARENT | overlay.WS_EX_TOOLWINDOW
                 | overlay.WS_EX_NOACTIVATE | overlay.WS_EX_TOPMOST)
        self.hwnd = overlay.user32.CreateWindowExW(style, class_name, "Buddy Island", overlay.WS_POPUP,
                                                   0, 0, 1, 1, None, None, instance, None)
        self._click_through = True

    def _set_click_through(self, through):
        if through == self._click_through:
            return
        style = _user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        if through:
            style |= overlay.WS_EX_TRANSPARENT
        else:
            style &= ~overlay.WS_EX_TRANSPARENT
        _user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, style)
        self._click_through = through

    def _run(self):
        try:
            # Physical pixels on every monitor: sharp text at 125-200 % and exact placement
            set_context = getattr(_user32, "SetThreadDpiAwarenessContext", None)
            if set_context is not None:
                set_context.restype = ctypes.c_void_p
                set_context.argtypes = [ctypes.c_void_p]
                set_context(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
            _user32.LoadCursorW.restype = ctypes.c_void_p
            _user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self._create_window()
        except Exception as error:
            self.last_error = error
            logger.exception("Buddy island window failed")
        finally:
            self._ready.set()
        if not self.hwnd:
            return
        self._screen_dc = overlay.user32.GetDC(None)
        self._memory_dc = overlay.gdi32.CreateCompatibleDC(self._screen_dc)
        self._bitmap = None
        self._bitmap_size = None
        self._renderers = {}
        self._size = None                    # current (w, h) of the shape, floats, while morphing
        self._last_key = None
        self._last_topmost = 0.0
        self._next_quiet_check = 0.0
        last = time.perf_counter()
        message = wintypes.MSG()
        while not self._stop:
            while overlay.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, overlay.PM_REMOVE):
                overlay.user32.TranslateMessage(ctypes.byref(message))
                overlay.user32.DispatchMessageW(ctypes.byref(message))
            now = time.perf_counter()
            dt, last = now - last, now
            try:
                busy = self._step(now, dt)
                self.last_error = None
            except Exception as error:
                if self.last_error is None:
                    logger.exception("Buddy island frame failed")
                self.last_error = error
                busy = False
            timeout = self.FRAME_S if busy else self.REST_POLL_S
            overlay.user32.MsgWaitForMultipleObjectsEx(0, None, max(1, int(timeout * 1000)), overlay.QS_ALLINPUT,
                                                       overlay.MWMO_INPUTAVAILABLE)
        self._hide()
        _user32.DestroyWindow(self.hwnd)
        self.hwnd = None

    def _renderer(self, scale):
        key = round(scale, 2)
        renderer = self._renderers.get(key)
        if renderer is None:
            renderer = overlay._TextRenderer(font_px=round(FONT_PX * scale), weight=500)
            self._renderers[key] = renderer
        return renderer

    def _step(self, now, dt):
        """One frame. Returns True while animating (then it runs at 60 fps)."""
        mouse_x, mouse_y = _cursor()
        with self._lock:
            state = self.state
            active = state.mode in ACTIVE_MODES
            if now >= self._next_quiet_check:
                self.quiet = quiet_time()
                self._next_quiet_check = now + self.QUIET_POLL_S
            inside = self.rect is not None and self.shown and (
                self.rect[0] <= mouse_x < self.rect[0] + self.rect[2]
                and self.rect[1] <= mouse_y < self.rect[1] + self.rect[3])
            state.tick(now, inside)
            if state.shot or (self.quiet and not active):
                self._hide()
                self._shot_done.set()
                return False
            mode, caption, color = state.mode, state.caption(), self.color
            seconds = now - state.since
            hovered = state.hovered
            interactive = state.interactive
        # The island lives on the monitor with the mouse (it stays put while Buddy is busy)
        if not active or self.rect is None:
            self._monitor, self._work, self._scale = _monitor_info(mouse_x, mouse_y)
        scale = self._scale
        text_image = None
        if caption:
            width_px = int(round((ANSWER_WIDTH - 2 * PAD_X - ICON_SLOT) * scale))
            color_text = MUTED if mode == "idle" else "#ffffff"
            if mode == "idle":
                caption = "Ask Buddy  \u00b7  click to type, hold to talk"
            text_image = self._renderer(scale).render(caption, width=width_px, background=INK_BLACK,
                                                     color=color_text,
                                                     max_height=int(round(TEXT_MAX_HEIGHT * scale)))
        text_size = None if text_image is None else text_image.size
        target_w, target_h = island_size(mode, hovered, text_size if mode != "idle" else None, scale)
        if mode == "idle" and hovered and text_image is not None:
            target_w = max(target_w, text_image.width + int(round((2 * PAD_X + ICON_SLOT) * scale)))
        if self._size is None:
            self._size = (float(target_w), float(target_h))
        width = approach(self._size[0], target_w, dt)
        height = approach(self._size[1], target_h, dt)
        self._size = (width, height)
        width_i, height_i = int(round(width)), int(round(height))
        x, y, flush = place(self._monitor, self._work, width_i, height_i, scale)
        morphing = (width_i, height_i) != (target_w, target_h)
        animated = mode in ("listening", "thinking")
        frame = int(seconds * 30) if animated else 0
        key = (mode, caption, color, width_i, height_i, flush, frame, round(scale, 2))
        if key != self._last_key:
            picture = render(mode, caption, width_i, height_i, flush, color, seconds, scale,
                             text_image if (caption and (mode != "idle" or hovered)) else None)
            ear = (picture.width - width_i) // 2
            self._upload(picture, x - ear, y)
            self._last_key = key
            self.rect = (x - ear, y, picture.width, picture.height)
            self.frames += 1
        self._set_click_through(not interactive)
        self._show(now)
        return morphing or animated

    def _upload(self, picture, left, top):
        width, height = picture.size
        if self._bitmap_size != (width, height):
            if self._bitmap:
                overlay.gdi32.SelectObject(self._memory_dc, self._old_bitmap)
                overlay.gdi32.DeleteObject(self._bitmap)
            info = overlay.BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(overlay.BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            self._bits = ctypes.c_void_p()
            self._bitmap = overlay.gdi32.CreateDIBSection(self._memory_dc, ctypes.byref(info), 0,
                                                          ctypes.byref(self._bits), None, 0)
            self._old_bitmap = overlay.gdi32.SelectObject(self._memory_dc, self._bitmap)
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
        blend = overlay.BLENDFUNCTION(0, 0, 255, overlay.AC_SRC_ALPHA)
        overlay.user32.UpdateLayeredWindow(self.hwnd, self._screen_dc, ctypes.byref(wintypes.POINT(left, top)),
                                           ctypes.byref(wintypes.SIZE(width, height)), self._memory_dc,
                                           ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend),
                                           overlay.ULW_ALPHA)

    def _show(self, now):
        if not self.shown or now - self._last_topmost > self.TOPMOST_EVERY_S:
            if not self.shown:
                overlay.user32.ShowWindow(self.hwnd, overlay.SW_SHOWNOACTIVATE)
            overlay.user32.SetWindowPos(self.hwnd, overlay.HWND_TOPMOST, 0, 0, 0, 0,
                                        overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE)
            self._last_topmost = now
        self.shown = True

    def _hide(self):
        if self.shown and self.hwnd:
            overlay.user32.ShowWindow(self.hwnd, overlay.SW_HIDE)
            self._last_key = None
        self.shown = False


# --------------------------------------------------------------------------- Buddy's overlay, mirrored
class IslandMirror:
    """Wraps Buddy's pointer overlay and tells the island the same story (listening, thinking, the answer).

    Everything else (attributes, properties such as position, set_always_on, ...) goes to the pointer, so
    the rest of Buddy does not know the island exists. island_getter() returns the island or None (off).
    """

    def __init__(self, pointer, island_getter):
        object.__setattr__(self, "_pointer", pointer)
        object.__setattr__(self, "_island_getter", island_getter)

    def __getattr__(self, name):
        return getattr(self._pointer, name)

    def __setattr__(self, name, value):
        setattr(self._pointer, name, value)

    def _island(self, method, *args):
        island = self._island_getter()
        if island is None:
            return
        try:
            getattr(island, method)(*args)
        except Exception:
            logger.exception("Buddy island %s failed", method)

    @property
    def pointer(self):
        return self._pointer

    def listening(self, monitor, cursor):
        self._pointer.listening(monitor, cursor)
        self._island("listening")

    def thinking(self, text="Thinking\u2026"):
        self._pointer.thinking(text)
        self._island("thinking", text)

    def say(self, text):
        self._pointer.say(text)
        self._island("say", text)

    def tour(self, screen_points, answer_text, dwell_ms=1800):
        self._pointer.tour(screen_points, answer_text, dwell_ms)
        self._island("say", answer_text)

    def hide_later(self, delay_ms):
        self._pointer.hide_later(delay_ms)
        self._island("idle_later", delay_ms / 1000.0)

    def hide(self):
        self._pointer.hide()
        self._island("idle")

    def clear_for_screenshot(self):
        self._island("clear_for_screenshot")
        self._pointer.clear_for_screenshot()

    def set_style(self, color=None, icon=None):
        self._pointer.set_style(color, icon)
        if color is not None:
            self._island("set_color", color)
