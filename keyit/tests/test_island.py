"""Buddy Island (the notch at the top of the screen): layout, states, drawing and the mirror of Buddy's pointer."""
import ctypes
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import buddy_island as island  # noqa: E402

MONITOR = {"left": 0, "top": 0, "width": 1920, "height": 1080}


class physical_pixels:
    """The island works in physical pixels (per-monitor DPI aware thread); measure it the same way."""

    def __enter__(self):
        self.previous = None
        set_context = getattr(ctypes.windll.user32, "SetThreadDpiAwarenessContext", None)
        if set_context is not None:
            set_context.restype = ctypes.c_void_p
            set_context.argtypes = [ctypes.c_void_p]
            self.set_context = set_context
            self.previous = set_context(ctypes.c_void_p(-4))
        return self

    def __exit__(self, *exc):
        if self.previous:
            self.set_context(ctypes.c_void_p(self.previous))
        return False


# --------------------------------------------------------------------------- placement
def test_place_centred_and_flush_with_the_top_edge():
    x, y, flush = island.place(MONITOR, {"left": 0, "top": 0, "width": 1920, "height": 1040}, 112, 16)
    assert (x, y, flush) == ((1920 - 112) // 2, 0, True)


def test_place_floats_under_a_top_taskbar():
    work = {"left": 0, "top": 48, "width": 1920, "height": 1032}
    x, y, flush = island.place(MONITOR, work, 300, 38, scale=1.5)
    assert flush is False
    assert y == 48 + round(island.FLOAT_GAP * 1.5)
    assert x == (1920 - 300) // 2


def test_place_on_a_second_monitor_left_of_the_main_one():
    monitor = {"left": -2560, "top": -200, "width": 2560, "height": 1440}
    x, y, flush = island.place(monitor, dict(monitor), 168, 24)
    assert flush is True
    assert y == -200
    assert x == -2560 + (2560 - 168) // 2


# --------------------------------------------------------------------------- sizes
def test_sizes_grow_from_notch_to_hover_to_busy():
    notch = island.island_size("idle", False)
    hover = island.island_size("idle", True)
    busy = island.island_size("thinking", False)
    assert notch == island.NOTCH
    assert hover[0] > notch[0] and hover[1] > notch[1]
    assert busy == island.BUSY


def test_sizes_scale_with_dpi():
    assert island.island_size("idle", False, scale=2.0) == (2 * island.NOTCH[0], 2 * island.NOTCH[1])


def test_answer_fits_its_text():
    width, height = island.island_size("talking", False, text_size=(380, 120))
    assert width == 380 + 2 * island.PAD_X + island.ICON_SLOT
    assert height == 120 + 2 * island.PAD_Y
    # never smaller than the busy strip
    assert island.island_size("talking", False, text_size=(10, 10)) == island.BUSY


def test_reading_time_is_bounded():
    assert island.reading_seconds("") == 6.0
    assert island.reading_seconds("x" * 10000) == 25.0
    assert 6.0 < island.reading_seconds("x" * 200) < 25.0


def test_approach_moves_towards_target_without_overshoot():
    value = 100.0
    for _ in range(200):
        value = island.approach(value, 300.0, 1 / 60)
        assert value <= 300.0
    assert value == 300.0


# --------------------------------------------------------------------------- states
def test_hover_needs_a_short_dwell():
    state = island.IslandState()
    assert state.tick(0.0, True) is False
    assert state.hovered is False and state.interactive is False     # a quick pass over the notch does nothing
    assert state.tick(island.HOVER_DWELL_S + 0.01, True) is True
    assert state.hovered is True and state.interactive is True
    assert state.caption() == "Ask Buddy"
    assert state.tick(1.0, False) is True
    assert state.hovered is False


def test_answer_closes_after_reading_time_but_not_while_hovered():
    state = island.IslandState()
    state.say("Save As is in the File menu.", now=0.0)
    close = state.close_at
    assert state.mode == "talking" and state.interactive
    state.tick(close - 0.1, False)
    assert state.mode == "talking"
    state.tick(close - 0.05, True)                  # mouse arrives
    state.tick(close + island.HOVER_DWELL_S, True)  # reading it: stays open
    assert state.mode == "talking"
    state.tick(close + 5.0, False)
    state.tick(close + 7.0, False)
    assert state.mode == "idle"


def test_idle_later_and_listening_caption():
    state = island.IslandState()
    state.set("listening", now=0.0)
    assert "Listening" in state.caption()
    state.idle_later(2.0, now=1.0)
    state.tick(2.9, False)
    assert state.mode == "listening"
    state.tick(3.1, False)
    assert state.mode == "idle"


def test_screenshot_hide_expires_by_itself():
    state = island.IslandState()
    state.hide_for_shot(now=10.0)
    assert state.shot is True
    state.tick(10.5, False)
    assert state.shot is True
    state.tick(10.0 + island.SHOT_HIDE_S + 0.01, False)
    assert state.shot is False


def test_new_state_cancels_screenshot_hide():
    state = island.IslandState()
    state.hide_for_shot(now=0.0)
    state.set("thinking", "Looking at your screen", now=0.1)
    assert state.shot is False


# --------------------------------------------------------------------------- drawing
def test_flush_notch_has_square_top_round_bottom_and_ears():
    mask, ear = island.shape_mask(112, 16, flush=True)
    assert ear == island.EAR
    assert mask.size == (112 + 2 * island.EAR, 16)
    pixels = mask.load()
    assert pixels[ear, 0] > 200                      # top-left corner of the body: square (merges with the edge)
    assert pixels[ear + 1, 15] < 60                  # bottom-left corner: rounded away
    assert pixels[3, 0] > 60                         # the ear flares into the screen edge
    assert pixels[1, 6] < 40                         # ... and curves away below it


def test_floating_pill_is_round_everywhere():
    mask, ear = island.shape_mask(300, 38, flush=False)
    assert ear == 0
    pixels = mask.load()
    assert pixels[0, 0] < 40 and pixels[299, 0] < 40 and pixels[0, 37] < 40
    assert pixels[150, 19] == 255


def test_render_states_use_the_accent_colour():
    for mode in ("idle", "listening", "thinking", "talking"):
        picture = island.render(mode, "" if mode == "idle" else "x", 300 if mode != "idle" else 112,
                                38 if mode != "idle" else 16, True, "#3b82f6", 0.3)
        assert picture.mode == "RGBA"
        pixels = np.asarray(picture).reshape(-1, 4)
        colors = {tuple(int(c) for c in pixel[:3]) for pixel in pixels if pixel[3] > 250}
        assert (0, 0, 0) in colors
        assert any(b > 200 and r < 120 for r, g, b in colors), mode   # the blue accent is drawn


@pytest.mark.skipif(sys.platform != "win32", reason="GDI text")
def test_answer_text_renders_white_on_black():
    import buddy_overlay

    renderer = buddy_overlay._TextRenderer(font_px=14, weight=500)
    text = renderer.render("Open File, then Save As.", width=360, background="#000000")
    assert text.getpixel((0, 0))[:3] == (0, 0, 0)
    assert int(np.asarray(text)[..., 0].max()) > 200
    picture = island.render("talking", "Open File, then Save As.", 420, 40, True, "#ff8424", 0.0, text_image=text)
    assert picture.size == (420 + 2 * island.EAR, 40)


# --------------------------------------------------------------------------- mirror
class FakePointer:
    def __init__(self):
        self.calls = []
        self.position = (0, 0)
        self.always_on = False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args):
            self.calls.append((name, args))
        return record


class FakeIsland:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
        return record


def test_mirror_tells_pointer_and_island_the_same_story():
    pointer, fake_island = FakePointer(), FakeIsland()
    mirror = island.IslandMirror(pointer, lambda: fake_island)
    mirror.listening({"left": 0}, (5, 5))
    mirror.thinking("Looking at your screen")
    mirror.say("Here")
    mirror.tour([(1, 2, "File")], "Open File")
    mirror.hide_later(3000)
    mirror.hide()
    mirror.clear_for_screenshot()
    mirror.set_style("#22c55e", "dot")
    assert [name for name, _ in pointer.calls] == ["listening", "thinking", "say", "tour", "hide_later", "hide",
                                                     "clear_for_screenshot", "set_style"]
    assert fake_island.calls == [("listening", ()), ("thinking", ("Looking at your screen",)), ("say", ("Here",)),
                                 ("say", ("Open File",)), ("idle_later", (3.0,)), ("idle", ()),
                                 ("clear_for_screenshot", ()), ("set_color", ("#22c55e",))]


def test_mirror_forwards_everything_else_and_works_without_island():
    pointer = FakePointer()
    mirror = island.IslandMirror(pointer, lambda: None)
    mirror.position = (40, 50)                       # Buddy sets the pointer position through the overlay
    assert pointer.position == (40, 50)
    assert mirror.always_on is False
    mirror.set_always_on(True)
    mirror.say("works without an island")
    assert ("set_always_on", (True,)) in pointer.calls
    assert mirror.pointer is pointer


def test_broken_island_never_breaks_the_pointer():
    class Broken:
        def say(self, text):
            raise RuntimeError("boom")

    pointer = FakePointer()
    mirror = island.IslandMirror(pointer, lambda: Broken())
    mirror.say("still answers")
    assert ("say", ("still answers",)) in pointer.calls


# --------------------------------------------------------------------------- the real window (Windows)
@pytest.mark.skipif(sys.platform != "win32", reason="Win32 window")
def test_real_island_window_sits_in_the_top_centre_and_changes_state():
    pressed = []
    bubble = island.BuddyIsland(color="#ff8424", on_press=lambda: pressed.append(1))
    try:
        assert bubble.hwnd
        deadline = time.time() + 3
        while bubble.rect is None and time.time() < deadline:
            time.sleep(0.05)
        x, y, width, height = bubble.rect
        with physical_pixels():
            cursor = island._cursor()
            monitor, work, scale = island._monitor_info(*cursor)
        assert abs((x + width / 2) - (monitor["left"] + monitor["width"] / 2)) <= 2
        assert y in (monitor["top"], work["top"] + round(island.FLOAT_GAP * scale))
        assert width < 200 * scale and height <= island.NOTCH[1] * scale + 1

        rect = island.overlay.wintypes.RECT()
        with physical_pixels():
            ctypes.windll.user32.GetWindowRect(bubble.hwnd, ctypes.byref(rect))
        if bubble.shown:
            assert (rect.left, rect.top) == (x, y)
            ex_style = ctypes.windll.user32.GetWindowLongW(bubble.hwnd, -20)
            assert ex_style & island.overlay.WS_EX_TOPMOST
            assert ex_style & island.overlay.WS_EX_NOACTIVATE
            assert ex_style & island.overlay.WS_EX_TRANSPARENT          # resting notch: click-through

        bubble.thinking("Looking at your screen")
        time.sleep(0.6)
        assert bubble.state.mode == "thinking"
        if bubble.shown:
            assert bubble.rect[2] >= island.BUSY[0] * scale - 2
            ex_style = ctypes.windll.user32.GetWindowLongW(bubble.hwnd, -20)
            assert not ex_style & island.overlay.WS_EX_TRANSPARENT      # open: clickable

        bubble.clear_for_screenshot()
        assert bubble.shown is False
        bubble.say("Save As is in the File menu.")
        time.sleep(0.5)
        assert bubble.state.mode == "talking"
        assert bubble.last_error is None
    finally:
        bubble.close()
    assert bubble.hwnd is None
