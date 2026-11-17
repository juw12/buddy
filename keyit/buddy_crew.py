"""Buddy's crew: one extra pointer on screen for every AI agent that is working on this PC.

Each agent (Claude Code, Codex, OpenCode, a sub-agent of one of them, or anything that writes a line to
Buddy's crew inbox) gets its own pointer with its own name and colour. The pointer says what the agent is
doing right now ("explorer: Reading README.md"), flies to the window that work shows up in when there is
one on screen, and hops at every new step. When the agent is done the pointer says so and leaves.

Nothing here decides what an agent does: the crew only shows it. Where the events come from is in
crew_sources.py; this file turns them into pointers.

One event is a dict (see README, "Crew"):
    {"agent": "explorer", "team": "OpenCode", "say": "Reading README.md", "state": "working",
     "file": "README.md", "id": "any-stable-key", "color": "#22c55e", "icon": "star", "x": 800, "y": 400}
Only "agent" is needed.
"""

import ctypes
import logging
import math
import re
import threading
import time
from ctypes import wintypes

import buddy_overlay
from buddy_overlay import BuddyOverlay

logger = logging.getLogger(__name__)

# Colours handed out in this order; the user's own Buddy colour is skipped so the crew never looks like Buddy
CREW_COLORS = ("#3b82f6", "#22c55e", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#facc15", "#ff8424")
CREW_ICON = "pointer"
STATES = ("working", "done", "leave")
MAX_MEMBERS = 8                       # more pointers than this is noise, not information
NAME_CHARS = 24
SAY_CHARS = 48
DONE_LINGER_S = 6.0                   # "done" stays this long, then the pointer leaves
SILENT_LEAVE_S = 150.0                # no news for this long: the agent stopped or was closed
QUIET_S = 10.0                        # no news for this long: only the name stays, so a waiting crew is small
COLORS_REMEMBERED = 200               # agents whose colour is kept for when they come back
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _one_line(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def valid_color(value):
    """'#rrggbb' in lower case, or None."""
    if isinstance(value, str) and _HEX_COLOR.fullmatch(value.strip()):
        return value.strip().lower()
    return None


def normalize_event(event):
    """A clean copy of one crew event, or None when it names no agent."""
    if not isinstance(event, dict):
        return None
    agent = _one_line(event.get("agent") or event.get("name"), NAME_CHARS)
    if not agent:
        return None
    team = _one_line(event.get("team"), NAME_CHARS)
    state = event.get("state") if event.get("state") in STATES else "working"
    clean = {
        "agent": agent,
        "team": team,
        "key": _one_line(event.get("id"), 120) or f"{team}/{agent}".lower(),
        "say": _one_line(event.get("say"), SAY_CHARS),
        "state": state,
        "where": _one_line(event.get("where"), NAME_CHARS),
        "file": _one_line(event.get("file"), 260),
        "window": _one_line(event.get("window"), 80),
        "color": valid_color(event.get("color")),
        "icon": event.get("icon") if event.get("icon") in buddy_overlay.ICONS.values() else None,
        "point": None,
    }
    try:
        clean["point"] = (int(event["x"]), int(event["y"]))
    except (KeyError, TypeError, ValueError):
        pass
    return clean


# --------------------------------------------------------------------------- one pointer
class CrewPointer(BuddyOverlay):
    """A pointer that goes where it is told instead of following the mouse."""

    STIFFNESS = 150.0                  # softer than Buddy's own pointer: a glide you can follow by eye
    DAMPING = 17.0
    ACTIVE_FRAME_S = 0.016
    REST_FRAME_S = 0.15                # at rest nothing moves, so it only has to notice new orders
    HOP_SPEED = 260.0                  # px/s upwards at every new step
    PULSE_S = 0.45

    def __init__(self, color=CREW_COLORS[0], icon=CREW_ICON):
        self._target = None
        self._hop = False
        super().__init__(always_on=False, color=color, icon=icon)

    def show(self, text, target, hop=False, enter_from=None):
        """Say `text` at screen point `target`. `hop`: a new step, so jump and ripple. `enter_from`: start there."""
        with self._lock:
            if self.mode != "talking":
                self._pos = None if enter_from is None else (float(enter_from[0]), float(enter_from[1]))
                self._vel = (0.0, 0.0)
                self._mode_started = time.perf_counter()
            self.mode = "talking"
            self._text = text
            self._target = (float(target[0]), float(target[1]))
            self._hop = self._hop or hop
        self._wake()

    def leave(self):
        with self._lock:
            self.mode = None
            self._text = None
            self._target = None
        self._wake()

    def _step(self, now, dt):
        with self._lock:
            mode, target = self.mode, self._target
            if mode != "talking" or target is None:
                self._set_shown(False)
                self._shot_done.set()
                self.FRAME_S = self.ACTIVE_FRAME_S
                return False
            if self._pos is None or dt > 1.0:
                self._pos, self._vel = target, (0.0, 0.0)
            (x, y), (vx, vy) = self._pos, self._vel
            if self._hop:
                self._hop = False
                vy -= self.HOP_SPEED
                self._pulse_started = now
            remaining = min(dt, self.MAX_LAG_S)
            while remaining > 1e-6:
                step = min(self.SUBSTEP_S, remaining)
                vx += ((target[0] - x) * self.STIFFNESS - vx * self.DAMPING) * step
                vy += ((target[1] - y) * self.STIFFNESS - vy * self.DAMPING) * step
                x += vx * step
                y += vy * step
                remaining -= step
            resting = math.hypot(target[0] - x, target[1] - y) < 0.4 and math.hypot(vx, vy) < 5
            if resting:
                x, y, vx, vy = target[0], target[1], 0.0, 0.0
            self._pos, self._vel = (x, y), (vx, vy)
            pulse = None
            if self._pulse_started is not None:
                if now - self._pulse_started < self.PULSE_S:
                    pulse = (now - self._pulse_started) / self.PULSE_S
                else:
                    self._pulse_started = None
            if self.monitor is None or not buddy_overlay._inside(self.monitor, x, y):
                self.monitor = buddy_overlay.monitor_at(x, y)
            monitor, color, icon, text = self.monitor, self.color, self.icon, self._text
            seconds = now - self._mode_started
            self.FRAME_S = self.REST_FRAME_S if resting and pulse is None else self.ACTIVE_FRAME_S
        self._present(x, y, monitor, "talking", icon, color, text, seconds, pulse)
        return True


# --------------------------------------------------------------------------- where a pointer goes
user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUM_WINDOWS_PROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
GA_ROOT = 2
SPI_GETWORKAREA = 0x0030
MIN_WINDOW_SIZE = (240, 120)
DOCK_FROM_RIGHT = 400                 # room for the pointer's bubble between it and the screen edge
DOCK_FROM_TOP = 96
SLOT_HEIGHT = 50
WINDOW_SLOT_HEIGHT = 44               # on a window they stand closer, but one bubble never covers the next
MIN_FILE_NAME_CHARS = 5               # "a.py" would match half the windows on screen


def visible_windows():
    """[(hwnd, title, (left, top, right, bottom))] of the windows on screen, front to back."""
    found = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(300)
        if user32.GetWindowTextW(hwnd, buffer, 300) <= 0:
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right - rect.left >= MIN_WINDOW_SIZE[0] and rect.bottom - rect.top >= MIN_WINDOW_SIZE[1]:
            found.append((hwnd, buffer.value, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(ENUM_WINDOWS_PROC(visit), 0)
    return found


def _on_top_at(hwnd, x, y):
    """True when `hwnd` is the window you actually see at (x, y); Buddy's own pointers are click-through."""
    seen = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    return bool(seen) and user32.GetAncestor(seen, GA_ROOT) == hwnd


def work_area():
    """(left, top, right, bottom) of the main screen without the taskbar."""
    rect = wintypes.RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, 1920, 1040


def title_wanted(titles_needles):
    """Lower-case words to look for in window titles, most telling first: the file, a named window, the team."""
    file_name, window, team = titles_needles
    needles = []
    base = re.split(r"[\\/]", file_name)[-1] if file_name else ""
    if len(base) >= MIN_FILE_NAME_CHARS and "." in base:
        needles.append(base.lower())
    if window:
        needles.append(window.lower())
    if team:
        needles.append(team.lower())
    return needles


def pick_window(needles, windows):
    """The first window whose title holds the first needle that any title holds; None when none does."""
    for needle in needles:
        for hwnd, title, rect in windows:
            if needle in title.lower():
                return hwnd, rect
    return None


class Desktop:
    """Finds the screen point for a crew member: the point it was given, the window its work is in, or the dock."""

    WINDOWS_EVERY_S = 1.0

    def __init__(self, windows=visible_windows, on_top_at=_on_top_at, area=work_area, clock=time.monotonic):
        self._list_windows = windows
        self._on_top_at = on_top_at
        self._area = area
        self._clock = clock
        self._seen = None                 # (when, windows)

    def _windows(self):
        now = self._clock()
        if self._seen is None or now - self._seen[0] >= self.WINDOWS_EVERY_S:
            self._seen = (now, self._list_windows())
        return self._seen[1]

    def dock(self, slot):
        left, top, right, bottom = self._area()
        x = max(left + 40, right - DOCK_FROM_RIGHT)
        y = min(top + DOCK_FROM_TOP + slot * SLOT_HEIGHT, bottom - 60)
        return x, y

    def entrance(self, slot):
        """Where a new pointer comes in from: the right edge, level with its place in the dock."""
        _, _, right, _ = self._area()
        return right - 30, self.dock(slot)[1]

    def spot(self, slot, point=None, file_name="", window="", team=""):
        if point is not None:
            return point
        needles = title_wanted((file_name, window, team))
        match = pick_window(needles, self._windows()) if needles else None
        if match is not None:
            hwnd, (left, top, right, bottom) = match
            x = left + min((right - left) // 2, 360)
            for y in (top + 18 + slot * WINDOW_SLOT_HEIGHT, (top + bottom) // 2 + slot * WINDOW_SLOT_HEIGHT):
                if y < bottom - 10 and self._on_top_at(hwnd, x, y):
                    return x, y
        return self.dock(slot)


# --------------------------------------------------------------------------- the crew
class Member:
    def __init__(self, key, slot, pointer, now):
        self.key = key
        self.slot = slot
        self.pointer = pointer
        self.agent = ""
        self.team = ""
        self.where = ""
        self.say = ""
        self.state = "working"
        self.file = ""
        self.window = ""
        self.point = None
        self.color = CREW_COLORS[0]
        self.icon = CREW_ICON
        self.style = None                # (colour, icon) the pointer has now
        self.changed = now               # last event
        self.label = ""
        self.shown = None                # (label, target) the pointer shows now; None = not on screen
        self.entered = False             # has flown in from the edge of the screen
        self.hop = False


class Crew:
    """Keeps one pointer per working agent. post() from any thread; tick() from one thread, a few times a second."""

    def __init__(self, pointer_factory=CrewPointer, desktop=None, clock=time.monotonic, looks=None,
                 avoid_color=None):
        self._pointer_factory = pointer_factory
        self._desktop = desktop or Desktop()
        self._clock = clock
        self._lock = threading.Lock()
        self._inbox = []
        self._members = {}                # key -> Member
        self._spare = []                  # pointers of members that left (their windows are reused)
        self._colors = {}                 # key -> colour it had, so an agent that comes back looks the same
        self._hidden_until = 0.0
        self.looks = dict(looks or {})    # "explorer" or "opencode/explorer" -> {"color": ..., "icon": ...}
        self.avoid_color = (avoid_color or "").lower()

    # -- from any thread
    def post(self, event):
        clean = normalize_event(event)
        if clean is not None:
            with self._lock:
                self._inbox.append(clean)

    def clear_for_screenshot(self, seconds=1.2):
        """Take every crew pointer off the screen now (Buddy is about to look at the screen); back by itself."""
        with self._lock:
            self._hidden_until = self._clock() + seconds
            members = list(self._members.values())
            for member in members:
                member.shown = None
        waits = []
        for member in members:
            done = getattr(member.pointer, "_shot_done", None)
            if done is not None:
                done.clear()
                waits.append(done)
            member.pointer.leave()
        deadline = time.perf_counter() + 0.4
        for done in waits:
            done.wait(max(0.0, deadline - time.perf_counter()))

    def members(self):
        """[(agent, team, state, say, colour)] for the agents on screen now, in dock order."""
        with self._lock:
            ordered = sorted(self._members.values(), key=lambda member: member.slot)
            return [(member.agent, member.team, member.state, member.say, member.color) for member in ordered]

    def close(self):
        with self._lock:
            for member in list(self._members.values()):
                self._dismiss(member)
            self._inbox.clear()

    # -- the crew's own thread
    def tick(self):
        now = self._clock()
        with self._lock:
            events, self._inbox = self._inbox, []
            for event in events:
                self._apply(event, now)
            for member in list(self._members.values()):
                silent = now - member.changed
                if member.state == "leave" or (member.state == "done" and silent > DONE_LINGER_S) \
                        or silent > SILENT_LEAVE_S:
                    self._dismiss(member)
            if now < self._hidden_until:
                return
            self._name_everyone(now)
            members = list(self._members.values())
        for member in members:
            target = self._desktop.spot(member.slot, member.point, member.file, member.window, member.team)
            if member.style != (member.color, member.icon):
                member.style = (member.color, member.icon)
                member.pointer.set_style(member.color, member.icon)
            if member.hop or member.shown != (member.label, target):
                enter_from = None if member.entered else self._desktop.entrance(member.slot)
                member.pointer.show(member.label, target, hop=member.hop, enter_from=enter_from)
                member.shown, member.hop, member.entered = (member.label, target), False, True

    def _apply(self, event, now):
        member = self._members.get(event["key"])
        if member is None:
            if event["state"] != "working":
                return                            # someone we never showed is done: nothing to show
            member = self._join(event["key"], now)
            if member is None:
                return
        member.agent, member.team = event["agent"], event["team"]
        member.where = event["where"] or member.where
        look = self.looks.get(f"{member.team}/{member.agent}".lower()) or self.looks.get(member.agent.lower()) or {}
        member.color = event["color"] or valid_color(look.get("color")) or member.color
        if look.get("icon") in buddy_overlay.ICONS.values():
            member.icon = look["icon"]
        member.icon = event["icon"] or member.icon
        self._colors[member.key] = member.color
        if event["state"] == "working":
            if event["say"] and event["say"] != member.say:
                member.hop = True
            member.say = event["say"] or member.say
            member.file, member.window, member.point = event["file"], event["window"], event["point"]
        elif event["state"] == "done":
            member.say = event["say"] or "done \u2713"
            member.point = None
        member.state = event["state"]
        member.changed = now

    def _join(self, key, now):
        if len(self._members) >= MAX_MEMBERS:
            idle = [member for member in self._members.values() if member.state != "working"]
            if not idle:
                return None                       # everyone on screen is busy: the newcomer is not shown
            self._dismiss(min(idle, key=lambda member: member.changed))
        taken = {member.slot for member in self._members.values()}
        slot = next(index for index in range(MAX_MEMBERS) if index not in taken)
        pointer = self._spare.pop() if self._spare else self._pointer_factory()
        member = Member(key, slot, pointer, now)
        used = {other.color for other in self._members.values()}
        before = self._colors.get(key)
        member.color = before if before and before not in used else self._free_color(key, used)
        self._members[key] = member
        return member

    def _free_color(self, key, used):
        """A colour nobody on screen has; one that no agent who may come back had, when there is such a colour."""
        if len(self._colors) > COLORS_REMEMBERED:
            self._colors = {other: member.color for other, member in self._members.items()}
        kept = {color for other, color in self._colors.items() if other != key}
        choices = [color for color in CREW_COLORS if color != self.avoid_color]
        for taken in (used | kept, used):
            for color in choices:
                if color not in taken:
                    return color
        return choices[len(self._members) % len(choices)]

    def _dismiss(self, member):
        member.pointer.leave()
        self._spare.append(member.pointer)
        self._members.pop(member.key, None)

    def _name_everyone(self, now):
        """Bubble text per member; two agents with one name are told apart by where they work, then by number.
        What it is doing is shown for QUIET_S after its last news, then only the name."""
        by_name = {}
        for member in sorted(self._members.values(), key=lambda member: member.slot):
            by_name.setdefault(member.agent.lower(), []).append(member)
        for same in by_name.values():
            places = [member.where for member in same]
            for index, member in enumerate(same):
                name = member.agent
                if len(same) > 1:
                    unique_place = member.where and places.count(member.where) == 1
                    name = f"{name} ({member.where})" if unique_place else f"{name} {index + 1}"
                quiet = member.state == "working" and now - member.changed > QUIET_S
                member.label = f"{name}: {member.say}" if member.say and not quiet else name
