"""The buddy next to your mouse, for macOS.

One small transparent, click-through panel that moves with the buddy (like the Windows version's own little
layered window). It floats above full-screen apps and on every Space, and never takes focus.
The view is flipped, so drawing goes top-down: the pointer tip sits at (tip_x, tip_y) inside the view.
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
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSPanel,
    NSScreen,
    NSStringDrawingUsesLineFragmentOrigin,
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
    from AppKit import NSScreenSaverWindowLevel as OVERLAY_LEVEL
except ImportError:                                   # very old PyObjC
    OVERLAY_LEVEL = 1000

INK = (0.122, 0.129, 0.188)          # #1f2130, the outline and bubble colour used on the website
FPS = 60
PAD = 14                              # room around the drawing so outlines and the pulse ring aren't clipped
BUBBLE_MAX_WIDTH = 270
BUBBLE_GAP = 22                       # from the pointer tip to the bubble corner
REST_OFFSET = (16.0, -16.0)           # where the buddy rests relative to the real mouse (Cocoa: y grows up)
FOLLOW_RATE = 18.0                    # spring speed: higher = tighter follow
SNAP_DISTANCE = 600.0                 # after a stall, jump straight back instead of crawling
FLY_SECONDS = 0.55

# The classic arrow, tip at 0,0, pointing up-left (flipped coordinates: y grows down)
ARROW = [(0, 0), (0, 21), (5.5, 16.5), (9.5, 25), (13, 23.5), (9, 15.5), (16, 15.5)]


def _color(rgb, alpha=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


def _ease_in_out(t):
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


class _Ticker(NSObject):
    """NSTimer target that calls a plain Python function."""

    def initWithCallback_(self, callback):
        self = objc.super(_Ticker, self).init()
        if self is None:
            return None
        self.callback = callback
        return self

    def tick_(self, timer):
        self.callback()


class _BuddyView(NSView):
    painter = None

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        if self.painter is not None:
            self.painter(self.bounds())


class Overlay:
    """State machine + drawing: idle / listening / thinking / talking, plus flights to points."""

    def __init__(self, color_hex=core.COLORS["Orange"], follow_mouse=True):
        self.color = core.hex_to_rgb(color_hex)
        self.follow_mouse = follow_mouse
        self.state = "idle"
        self.text = ""
        self.visible = True
        x, y = NSEvent.mouseLocation()
        self.pos = [x + REST_OFFSET[0], y + REST_OFFSET[1]]
        self.flight = None            # (start_x, start_y, end_x, end_y, started_at)
        self.parked = False           # True while pointing at something: don't follow the mouse
        self.pulse_started = 0.0
        self.last_tick = time.perf_counter()
        self.layout = None
        self.tip = (PAD + 18, PAD + 18)
        self.bubble_origin = (0, 0)
        self._make_window()
        self._ticker = _Ticker.alloc().initWithCallback_(self._tick)
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FPS, self._ticker, "tick:", None, True
        )

    # ------------------------------------------------------------------ window
    def _make_window(self):
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 80, 80), style, NSBackingStoreBuffered, False
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setLevel_(OVERLAY_LEVEL)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        view = _BuddyView.alloc().initWithFrame_(NSMakeRect(0, 0, 80, 80))
        view.painter = self._paint
        panel.setContentView_(view)
        panel.orderFrontRegardless()
        self.window = panel
        self.view = view

    # ------------------------------------------------------------------ public API
    def set_color(self, color_hex):
        self.color = core.hex_to_rgb(color_hex)
        self.view.setNeedsDisplay_(True)

    def set_state(self, state, text=""):
        self.state = state
        self.text = text
        self._relayout()

    def say(self, text):
        self.state = "talking"
        self.text = text
        self._relayout()

    def fly_to(self, x, y):
        """Fly the pointer tip to a Cocoa screen point and stay there until home()."""
        self.parked = True
        self.flight = (self.pos[0], self.pos[1], x, y, time.perf_counter())

    def home(self):
        self.parked = False
        self.flight = None
        self.state = "idle"
        self.text = ""
        self._relayout()

    def show(self, visible):
        self.visible = visible
        if visible:
            self.window.orderFrontRegardless()
        else:
            self.window.orderOut_(None)

    def render_png(self, path):
        """Draw the current frame into a PNG (used by the self-test; needs no screen permission)."""
        from AppKit import NSBitmapImageRep

        try:
            from AppKit import NSBitmapImageFileTypePNG as png_type
        except ImportError:
            from AppKit import NSPNGFileType as png_type
        bounds = self.view.bounds()
        rep = self.view.bitmapImageRepForCachingDisplayInRect_(bounds)
        self.view.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        data = rep.representationUsingType_properties_(png_type, {})
        data.writeToFile_atomically_(str(path), True)
        return isinstance(rep, NSBitmapImageRep)

    # ------------------------------------------------------------------ motion
    def _tick(self):
        now = time.perf_counter()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now
        if self.flight is not None:
            sx, sy, ex, ey, started = self.flight
            t = min(1.0, (now - started) / FLY_SECONDS)
            k = _ease_in_out(t)
            self.pos = [sx + (ex - sx) * k, sy + (ey - sy) * k]
            if t >= 1.0:
                self.flight = None
                self.pulse_started = now
        elif not self.parked and self.follow_mouse:
            mx, my = NSEvent.mouseLocation()
            tx, ty = mx + REST_OFFSET[0], my + REST_OFFSET[1]
            dx, dy = tx - self.pos[0], ty - self.pos[1]
            if math.hypot(dx, dy) > SNAP_DISTANCE:
                self.pos = [tx, ty]
            else:
                rate = min(1.0, dt * FOLLOW_RATE)
                self.pos = [self.pos[0] + dx * rate, self.pos[1] + dy * rate]
        self._place_window()
        if self.state in ("listening", "thinking") or now - self.pulse_started < 0.9:
            self.view.setNeedsDisplay_(True)

    def _screen_frame(self):
        for screen in NSScreen.screens():
            frame = screen.frame()
            if (frame.origin.x <= self.pos[0] < frame.origin.x + frame.size.width
                    and frame.origin.y <= self.pos[1] < frame.origin.y + frame.size.height):
                return frame
        return NSScreen.mainScreen().frame()

    # ------------------------------------------------------------------ layout
    def _bubble(self):
        if not self.text:
            return None
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(13.5, 0.23),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        string = NSAttributedString.alloc().initWithString_attributes_(self.text, attributes)
        bounds = string.boundingRectWithSize_options_(
            NSMakeSize(BUBBLE_MAX_WIDTH, 10000), NSStringDrawingUsesLineFragmentOrigin
        )
        width = math.ceil(bounds.size.width) + 24
        height = math.ceil(bounds.size.height) + 16
        return string, width, height

    def _relayout(self):
        self.layout = self._bubble()
        self._place_window()
        self.view.setNeedsDisplay_(True)

    def _place_window(self):
        """Size and move the panel so it holds the pointer and (if any) the bubble, flipping near edges."""
        bubble = self.layout
        pointer_w, pointer_h = 64, 64            # room for the arrow, spinner and pulse ring
        width, height = pointer_w, pointer_h
        tip_x, tip_y = PAD + 18, PAD + 18
        if bubble is not None:
            _, bubble_w, bubble_h = bubble
            frame = self._screen_frame()
            right_side = self.pos[0] + BUBBLE_GAP + bubble_w + PAD < frame.origin.x + frame.size.width
            below = self.pos[1] - BUBBLE_GAP - bubble_h - PAD > frame.origin.y
            width = max(pointer_w, BUBBLE_GAP + bubble_w + 2 * PAD + 18)
            height = max(pointer_h, BUBBLE_GAP + bubble_h + 2 * PAD + 18)
            tip_x = PAD + 18 if right_side else width - PAD - 18
            tip_y = PAD + 18 if below else height - PAD - 18
            self.bubble_origin = (
                tip_x + BUBBLE_GAP if right_side else tip_x - BUBBLE_GAP - bubble_w,
                tip_y + BUBBLE_GAP if below else tip_y - BUBBLE_GAP - bubble_h,
            )
        self.tip = (tip_x, tip_y)
        # View is flipped; the window origin is its bottom-left corner in Cocoa screen coordinates
        origin_x = self.pos[0] - tip_x
        origin_y = self.pos[1] + tip_y - height
        self.window.setFrame_display_(NSMakeRect(origin_x, origin_y, width, height), False)
        self.view.setFrame_(NSMakeRect(0, 0, width, height))

    # ------------------------------------------------------------------ drawing
    def _paint(self, bounds):
        if not self.visible:
            return
        now = time.perf_counter()
        tip_x, tip_y = self.tip
        accent = self.color

        # Pulse ring where it just landed
        age = now - self.pulse_started
        if age < 0.9:
            radius = 6 + age * 26
            ring = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(tip_x - radius, tip_y - radius, 2 * radius, 2 * radius)
            )
            _color(accent, max(0.0, 0.8 - age)).setStroke()
            ring.setLineWidth_(3)
            ring.stroke()

        # The arrow
        arrow = NSBezierPath.bezierPath()
        arrow.moveToPoint_((tip_x + ARROW[0][0], tip_y + ARROW[0][1]))
        for ax, ay in ARROW[1:]:
            arrow.lineToPoint_((tip_x + ax, tip_y + ay))
        arrow.closePath()
        _color(accent).setFill()
        arrow.fill()
        _color(INK).setStroke()
        arrow.setLineWidth_(1.6)
        arrow.setLineJoinStyle_(1)            # round
        arrow.stroke()

        # Listening: a little dark disc with bouncing bars; thinking: a spinning arc
        badge_x, badge_y = tip_x + 18, tip_y + 20
        if self.state == "listening":
            disc = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(badge_x - 11, badge_y - 11, 22, 22))
            _color(INK).setFill()
            disc.fill()
            _color(accent).setStroke()
            disc.setLineWidth_(2)
            disc.stroke()
            _color(accent).setFill()
            for index in range(3):
                level = 0.35 + 0.65 * abs(math.sin(now * 7 + index * 1.3))
                bar_h = 4 + 9 * level
                NSBezierPath.fillRect_(NSMakeRect(badge_x - 6 + index * 4.5, badge_y - bar_h / 2, 2.6, bar_h))
        elif self.state == "thinking":
            disc = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(badge_x - 11, badge_y - 11, 22, 22))
            _color(INK).setFill()
            disc.fill()
            arc = NSBezierPath.bezierPath()
            start = (now * 360) % 360
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (badge_x, badge_y), 6.5, start, start + 260, False
            )
            _color(accent).setStroke()
            arc.setLineWidth_(2.4)
            arc.setLineCapStyle_(1)
            arc.stroke()

        # Speech bubble
        if self.layout is not None:
            string, bubble_w, bubble_h = self.layout
            bx, by = self.bubble_origin
            box = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(bx, by, bubble_w, bubble_h), 9, 9
            )
            _color(INK, 0.96).setFill()
            box.fill()
            _color(accent).setStroke()
            box.setLineWidth_(2)
            box.stroke()
            string.drawWithRect_options_(
                NSMakeRect(bx + 12, by + 8, bubble_w - 24, bubble_h - 16), NSStringDrawingUsesLineFragmentOrigin
            )
