"""Buddy Island for macOS: Buddy lives in the MacBook notch, like the Dynamic Island on an iPhone.

On a Mac with a notch the island wraps around the camera housing: at rest only two small black wings show
on either side of the notch (with Buddy's coloured dot), so it looks like part of the hardware. On a Mac
without a notch (or an external display as main screen) the same shape hangs from the middle of the menu bar.

* Click it to type a question; press and hold it to talk (release to send), like Control+Option+Space.
* While Buddy listens or thinks, the wings grow (waveform / spinner); the answer drops down under the notch.
  Click the answer to dismiss it.
* It floats above full-screen apps and on every Space, never takes focus, and only redraws while it moves.

The geometry (where the notch is, how big the island gets) lives in core.notch_rect / core.island_rect and is
unit-tested on any OS.
"""
from __future__ import annotations

import math
import time

import objc
from AppKit import (
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSPanel,
    NSScreen,
    NSStringDrawingUsesLineFragmentOrigin,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject, NSTimer

from . import core

try:
    from AppKit import NSScreenSaverWindowLevel as ISLAND_LEVEL
except ImportError:                                   # very old PyObjC
    ISLAND_LEVEL = 1000

FPS = 60
EAR = 6.0                     # concave flare where the island meets the top edge of the screen
PAD = 12.0
TEXT_SIZE = 13.0
MORPH_RATE = 14.0             # 1/s
GREY = (0.63, 0.63, 0.67)     # #a1a1aa, 7.8:1 on black


def _color(rgb, alpha=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


def _approach(current, target, dt):
    if abs(target - current) < 0.5:
        return target
    return current + (target - current) * (1.0 - math.exp(-MORPH_RATE * dt))


def _text(string, size=TEXT_SIZE, rgb=(1.0, 1.0, 1.0), weight=0.23):
    attributes = {
        NSFontAttributeName: NSFont.systemFontOfSize_weight_(size, weight),
        NSForegroundColorAttributeName: _color(rgb),
    }
    return NSAttributedString.alloc().initWithString_attributes_(string, attributes)


def _rect_tuple(rect):
    return (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)


def screen_geometry(screen=None):
    """(display frame, menu bar height, notch or None) for the display with the menu bar."""
    screen = screen or NSScreen.screens()[0]
    frame = screen.frame()
    rect = core.ScreenRect(frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)
    visible = screen.visibleFrame()
    menu_bar = (frame.origin.y + frame.size.height) - (visible.origin.y + visible.size.height)
    notch = None
    if screen.respondsToSelector_("auxiliaryTopLeftArea"):           # macOS 12+
        left = screen.auxiliaryTopLeftArea()
        right = screen.auxiliaryTopRightArea()
        if left.size.width > 0 and right.size.width > 0:
            notch = core.notch_rect(rect, _rect_tuple(left), _rect_tuple(right))
    if notch is not None and screen.respondsToSelector_("safeAreaInsets"):
        menu_bar = max(menu_bar, screen.safeAreaInsets().top)
    return rect, menu_bar, notch


class _Ticker(NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(_Ticker, self).init()
        if self is None:
            return None
        self.callback = callback
        return self

    def tick_(self, timer):
        self.callback()


class _IslandView(NSView):
    owner = None

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True                                   # a click works even while another app is active

    def drawRect_(self, rect):
        if self.owner is not None:
            self.owner._paint(self.bounds())

    def mouseDown_(self, event):
        if self.owner is not None:
            self.owner._mouse_down()

    def mouseUp_(self, event):
        if self.owner is not None:
            self.owner._mouse_up()

    def mouseEntered_(self, event):
        if self.owner is not None:
            self.owner._hover(True)

    def mouseExited_(self, event):
        if self.owner is not None:
            self.owner._hover(False)


class Island:
    """The notch island. All calls on the main thread (like the Overlay)."""

    def __init__(self, color_hex=core.COLORS["Orange"], on_press=None, on_release=None, on_dismiss=None):
        self.color = core.hex_to_rgb(color_hex)
        self.on_press = on_press or (lambda: None)
        self.on_release = on_release or (lambda: None)
        self.on_dismiss = on_dismiss or (lambda: None)
        self.mode = "idle"
        self.text = ""
        self.hovered = False
        self.visible = True
        self.pressing = False
        self.since = time.perf_counter()
        self.last_tick = self.since
        self.answer_layout = None
        self.frame = None                       # ScreenRect the window shows now (animated)
        self.target = None                      # ScreenRect it is moving to
        self.notch = None
        self.bar = core.ISLAND_MIN_HEIGHT
        self._make_window()
        self._relayout(animate=False)
        self._ticker = _Ticker.alloc().initWithCallback_(self._tick)
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FPS, self._ticker, "tick:", None, True
        )

    # ------------------------------------------------------------------ window
    def _make_window(self):
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 200, 32), style, NSBackingStoreBuffered, False
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setIgnoresMouseEvents_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setLevel_(ISLAND_LEVEL)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        view = _IslandView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 32))
        view.owner = self
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            NSMakeRect(0, 0, 0, 0),
            NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect,
            view,
            None,
        )
        view.addTrackingArea_(area)
        panel.setContentView_(view)
        panel.orderFrontRegardless()
        self.window = panel
        self.view = view

    # ------------------------------------------------------------------ public API
    def set_color(self, color_hex):
        self.color = core.hex_to_rgb(color_hex)
        self.view.setNeedsDisplay_(True)

    def set_state(self, state, text=""):
        if state != self.mode:
            self.since = time.perf_counter()
        self.mode = state if state in ("idle", "listening", "thinking", "talking") else "idle"
        self.text = text or ""
        self._relayout()

    def say(self, text):
        self.set_state("talking", text)

    def idle(self):
        self.set_state("idle", "")

    def show(self, visible):
        self.visible = visible
        if visible:
            self._relayout(animate=False)
            self.window.orderFrontRegardless()
        else:
            self.window.orderOut_(None)

    def render_png(self, path):
        """Draw the current island into a PNG (self-test; needs no screen permission)."""
        try:
            from AppKit import NSBitmapImageFileTypePNG as png_type
        except ImportError:
            from AppKit import NSPNGFileType as png_type
        bounds = self.view.bounds()
        rep = self.view.bitmapImageRepForCachingDisplayInRect_(bounds)
        self.view.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        data = rep.representationUsingType_properties_(png_type, {})
        data.writeToFile_atomically_(str(path), True)
        return True

    # ------------------------------------------------------------------ mouse
    def _mouse_down(self):
        if self.mode == "talking":
            self.on_dismiss()
            self.idle()
            return
        if self.mode in ("listening", "thinking"):
            return
        self.pressing = True
        self.on_press()

    def _mouse_up(self):
        if self.pressing:
            self.pressing = False
            self.on_release()

    def _hover(self, inside):
        self.hovered = inside
        self._relayout()

    # ------------------------------------------------------------------ layout + motion
    def _answer(self):
        if self.mode != "talking" or not self.text:
            return None
        string = _text(self.text)
        width = core.ISLAND_ANSWER_WIDTH - 2 * PAD - 22
        bounds = string.boundingRectWithSize_options_(NSMakeSize(width, 10000), NSStringDrawingUsesLineFragmentOrigin)
        return string, width, min(260.0, math.ceil(bounds.size.height))

    def _relayout(self, animate=True):
        frame, menu_bar, notch = screen_geometry()
        self.notch = notch
        self.bar = max(core.ISLAND_MIN_HEIGHT, menu_bar, notch.height if notch is not None else 0.0)
        self.answer_layout = self._answer()
        answer_height = self.answer_layout[2] + 2 * PAD if self.answer_layout else 0.0
        mode = self.mode
        if mode == "idle" and self.hovered:
            mode = "thinking"                              # hover: open up and say "Ask Buddy"
        self.target = core.island_rect(frame, menu_bar, notch, mode, answer_height)
        if not animate or self.frame is None:
            self.frame = self.target
            self._place()
        self.view.setNeedsDisplay_(True)

    def _place(self):
        f = self.frame
        rect = NSMakeRect(f.x - EAR, f.y, f.width + 2 * EAR, f.height)
        self.window.setFrame_display_(rect, True)
        self.view.setFrame_(NSMakeRect(0, 0, rect.size.width, rect.size.height))

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now
        if not self.visible or self.target is None:
            return
        f, t = self.frame, self.target
        if (f.x, f.y, f.width, f.height) != (t.x, t.y, t.width, t.height):
            top = t.y + t.height
            width = _approach(f.width, t.width, dt)
            height = _approach(f.height, t.height, dt)
            centre = _approach(f.x + f.width / 2.0, t.x + t.width / 2.0, dt)
            self.frame = core.ScreenRect(centre - width / 2.0, top - height, width, height)
            self._place()
        elif self.mode in ("listening", "thinking"):
            self.view.setNeedsDisplay_(True)

    # ------------------------------------------------------------------ drawing (flipped: y grows down)
    def _shape(self, width, height):
        radius = min(height / 2.0, 18.0 if self.mode == "talking" else 10.0)
        body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(EAR, -radius, width, height + radius), radius, radius
        )
        k = 0.552 * EAR
        for edge, sign in ((EAR, -1), (EAR + width, 1)):
            ear = NSBezierPath.bezierPath()
            outer = edge + sign * EAR
            ear.moveToPoint_((outer, 0))
            ear.lineToPoint_((edge, 0))
            ear.lineToPoint_((edge, EAR))
            ear.curveToPoint_controlPoint1_controlPoint2_((outer, 0), (edge, EAR - k), (outer - sign * k, 0))
            ear.closePath()
            body.appendBezierPath_(ear)
        return body

    def _paint(self, bounds):
        if not self.visible:
            return
        width = bounds.size.width - 2 * EAR
        height = bounds.size.height
        _color((0, 0, 0)).setFill()
        self._shape(width, height).fill()
        now = time.perf_counter()
        seconds = now - self.since
        bar = min(height, self.bar)
        accent = self.color
        notch_w = self.notch.width if self.notch is not None else 0.0
        wing = (width - notch_w) / 2.0                     # visible part on each side of the camera
        cy = bar / 2.0
        gx = EAR + max(10.0, min(wing / 2.0, 22.0))        # glyph sits in the left wing
        if self.mode == "listening":
            _color(accent).setFill()
            for index in range(4):
                level = 0.3 + 0.7 * abs(math.sin(seconds * 7.0 + index * 1.25))
                half = 2.5 + 5.5 * level
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(gx - 7 + index * 4.2, cy - half, 2.4, 2 * half), 1.2, 1.2).fill()
        elif self.mode == "thinking":
            arc = NSBezierPath.bezierPath()
            start = (seconds * 360.0) % 360.0
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (gx, cy), 6.0, start, start + 260.0, False)
            _color(accent).setStroke()
            arc.setLineWidth_(2.2)
            arc.setLineCapStyle_(1)
            arc.stroke()
        else:
            radius = 3.6 if self.mode == "idle" else 4.2
            _color(accent).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(gx - radius, cy - radius, 2 * radius, 2 * radius)).fill()

        # A word or two in the right wing (never under the camera)
        label = {"listening": "Listening", "thinking": "Thinking"}.get(self.mode, "")
        if self.mode == "idle" and self.hovered:
            label = "Ask Buddy"
        if label and wing > 60:
            string = _text(label, 12.0, GREY if self.mode == "idle" else (1.0, 1.0, 1.0), 0.3)
            size = string.size()
            right_wing_left = EAR + wing + notch_w
            x = right_wing_left + max(6.0, (wing - size.width) / 2.0)
            string.drawAtPoint_((x, cy - size.height / 2.0))

        if self.answer_layout is not None:
            string, text_w, text_h = self.answer_layout
            string.drawWithRect_options_(NSMakeRect(EAR + PAD + 22, bar + PAD * 0.5, text_w, text_h),
                                         NSStringDrawingUsesLineFragmentOrigin)
            radius = 4.2
            _color(accent).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(EAR + PAD + 6 - radius, bar + PAD * 0.5 + 8 - radius, 2 * radius, 2 * radius)).fill()


class IslandMirror:
    """Wraps the pointer Overlay; the island shows the same states. Everything else goes to the pointer."""

    def __init__(self, overlay, island_getter):
        object.__setattr__(self, "_overlay", overlay)
        object.__setattr__(self, "_island_getter", island_getter)

    def __getattr__(self, name):
        return getattr(self._overlay, name)

    def __setattr__(self, name, value):
        setattr(self._overlay, name, value)

    def _island(self, method, *args):
        island = self._island_getter()
        if island is not None:
            try:
                getattr(island, method)(*args)
            except Exception:                                   # noqa: BLE001
                import logging

                logging.exception("island %s failed", method)

    def set_state(self, state, text=""):
        self._overlay.set_state(state, text)
        self._island("set_state", state, text)

    def say(self, text):
        self._overlay.say(text)
        self._island("say", text)

    def home(self):
        self._overlay.home()
        self._island("idle")

    def set_color(self, color_hex):
        self._overlay.set_color(color_hex)
        self._island("set_color", color_hex)
