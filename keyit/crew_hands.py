"""Crew hands: an AI agent really clicks and types on this PC, and several agents do it at the same time.

An agent (any tool that can run a command) asks with one line:
    Buddy.exe --crew "{\"agent\": \"tester\", \"do\": \"look\", \"window\": \"Notepad\"}"
    Buddy.exe --crew "{\"agent\": \"tester\", \"do\": \"click\", \"target\": \"Save\", \"window\": \"Notepad\"}"
    Buddy.exe --crew "{\"agent\": \"tester\", \"do\": \"type\", \"target\": \"File name\", \"text\": \"notes\",
                      \"window\": \"Save As\"}"
    Buddy.exe --crew "{\"agent\": \"tester\", \"do\": \"click\", \"x\": 900, \"y\": 500}"
The command waits, prints what happened as one JSON line ({"ok": true, "did": "Clicked Save", ...}) and ends
with 0 when it was done, 3 when it was not.

Many at once: every agent has its own pointer (buddy_crew.py) and its own thread here. A button, tick box, tab,
list item or text box is pressed through Windows UI Automation, which needs no mouse: several agents press
things in several windows in the same moment, and your own mouse stays where it is. Only where a program
offers no such way, and for a bare x/y point, the real mouse is used: one click at a time, then it is put back.

The rules, the same for every agent:
* off until you said yes (Settings -> Buddy (AI), or the question Buddy asks the first time an agent tries);
* only what you can see: something covered by another window is not clicked, so every click has a pointer;
* never Buddy's own windows, never a window's close/minimize button;
* a name that can delete or send something ("Delete", "Send", "Don't save"...) is asked about first;
* what an agent types is never shown in its bubble: the screen may be shared.
"""

import ctypes
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import buddy_crew
import crew_sources
import keyit_core
import keyit_targets

logger = logging.getLogger(__name__)

ACTIONS = ("look", "click", "type")
RESULTS_DIR = keyit_core.USER_DIR / "crew" / "results"
RESULT_WAIT_S = 45.0                  # how long `Buddy.exe --crew` waits (a question to you takes up to 30 s)
RESULT_KEEP_S = 600.0                 # an answer nobody fetched is thrown away after this
REQUEST_MAX_AGE_S = 60.0              # a request older than this is not done any more: its agent gave up
ARRIVE_S = 0.7                        # the pointer flies to the spot first: you see the click before it happens
QUEUE_PER_AGENT = 20
MAX_AGENTS = buddy_crew.MAX_MEMBERS   # agents with work in hand at one time
IDLE_EXIT_S = 20.0                    # an agent's thread ends after this long without work
LOOK_MAX = 150                        # elements one "look" lists
MAX_ELEMENTS = 3000
TEXT_CHARS = 2000
NAME_CHARS = 40
UIA_E_TIMEOUT = -2146233083           # 0x80131505: the program is still busy with what was pressed
# The same words Buddy's own clicks are careful with (keyit_actions.py)
WINDOW_BUTTONS = re.compile(r"^(close|close window|close tab|minimi[sz]e|maximi[sz]e|restore( down)?|exit)$",
                            re.IGNORECASE)
RISKY_CLICKS = re.compile(r"\b(delete|remove|discard|don'?t save|do not save|erase|reset|uninstall|format|send|"
                          r"sign out|log ?out|empty|clear all|overwrite|replace all|purchase|buy|pay)\b",
                          re.IGNORECASE)
NOT_ALLOWED = "Agents may not click or type on this PC. Buddy -> Settings -> Buddy (AI) switches it on."
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _no(why):
    return {"ok": False, "did": why}


def normalize_request(event):
    """A clean copy of one request to do something, or None when the event is not one."""
    if not isinstance(event, dict) or event.get("do") not in ACTIONS:
        return None
    shown = buddy_crew.normalize_event(event)
    if shown is None:
        return None
    request_id = event.get("request")
    try:
        asked_at = float(event["at"])
    except (KeyError, TypeError, ValueError):
        asked_at = None
    return {
        "do": event["do"],
        "agent": shown["agent"],
        "key": shown["key"],
        # who it is, for the crew: the pointer that clicks is the agent's own pointer
        "who": {name: event[name] for name in ("agent", "name", "team", "id", "color", "icon", "where")
                if name in event},
        "window": shown["window"],
        "target": buddy_crew._one_line(event.get("target"), 80),
        "text": str(event.get("text") or "")[:TEXT_CHARS],
        "point": shown["point"],
        "request": request_id if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id) else "",
        "at": asked_at,
    }


# --------------------------------------------------------------------------- the answer an agent gets
def write_result(request_id, result, folder=None):
    """Leave the answer where `Buddy.exe --crew` waits for it. A request without an id gets no answer."""
    if not request_id:
        return
    folder = Path(folder or RESULTS_DIR)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        draft = folder / f"{request_id}.tmp"
        draft.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(draft, folder / f"{request_id}.json")
    except OSError:
        logger.exception("Could not write the answer for %s", request_id)


def tidy_results(folder=None, now=None):
    """Throw away answers nobody fetched (their agent stopped waiting)."""
    now = time.time() if now is None else now
    try:
        for path in Path(folder or RESULTS_DIR).iterdir():
            if now - path.stat().st_mtime > RESULT_KEEP_S:
                path.unlink()
    except OSError:
        pass


def ask(event, wait_s=RESULT_WAIT_S, post=crew_sources.post_event, folder=None, sleep=time.sleep,
        clock=time.monotonic):
    """What `Buddy.exe --crew` does with a "do" line: hand it to the running Buddy and wait for the answer."""
    folder = Path(folder or RESULTS_DIR)
    request_id = uuid.uuid4().hex
    post(dict(event, request=request_id, at=time.time()))
    answer = folder / f"{request_id}.json"
    deadline = clock() + wait_s
    while clock() < deadline:
        try:
            result = json.loads(answer.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sleep(0.05)
            continue
        try:
            answer.unlink()
        except OSError:
            pass
        return result if isinstance(result, dict) else _no("Buddy gave no usable answer.")
    return _no("Buddy did not answer. Is it running, with the other AI agents switched on in its settings?")


# --------------------------------------------------------------------------- the real desktop
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.EnumWindows.argtypes = [buddy_crew.ENUM_WINDOWS_PROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
MONITOR_DEFAULTTONEAREST = 2
GA_ROOT = 2
DWMWA_CLOAKED = 14
# UI Automation pattern ids, in the order they are tried for "press this"
INVOKE, TOGGLE, SELECTION_ITEM, EXPAND_COLLAPSE, LEGACY, VALUE = 10000, 10015, 10010, 10005, 10018, 10002


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]


def _center(rect):
    left, top, right, bottom = rect
    return (left + right) / 2, (top + bottom) / 2


def scaled(point, from_rect, to_rect):
    """The same spot on a monitor whose rectangle is `from_rect` in one kind of pixels and `to_rect` in another."""
    x, y = point
    from_width, from_height = from_rect[2] - from_rect[0], from_rect[3] - from_rect[1]
    if from_width <= 0 or from_height <= 0:
        return int(round(x)), int(round(y))
    return (int(round(to_rect[0] + (x - from_rect[0]) * (to_rect[2] - to_rect[0]) / from_width)),
            int(round(to_rect[1] + (y - from_rect[1]) * (to_rect[3] - to_rect[1]) / from_height)))


class Screen:
    """The real desktop, through Win32 and UI Automation. Element rectangles are physical pixels; the crew's
    points are this process's own pixels (Buddy is not DPI aware), so to_own() and to_physical() translate.
    Every thread that uses it gets its own UI Automation client."""

    _input = threading.Lock()             # there is one mouse and one keyboard

    def __init__(self):
        self._local = threading.local()
        self._own_pid = os.getpid()

    # -- windows
    def _is_buddy(self, hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == self._own_pid

    def find_window(self, needle):
        """(hwnd, title) of the front-most window on screen with `needle` in its title; None when there is none."""
        needle = needle.lower()
        cloaked_of = ctypes.windll.dwmapi.DwmGetWindowAttribute
        found = []

        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            buffer = ctypes.create_unicode_buffer(300)
            if user32.GetWindowTextW(hwnd, buffer, 300) <= 0 or needle not in buffer.value.lower():
                return True
            cloaked = wintypes.DWORD()
            cloaked_of(wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if not cloaked.value and not self._is_buddy(hwnd):
                found.append((hwnd, buffer.value))
                return False                                # EnumWindows goes front to back: the first is it
            return True

        user32.EnumWindows(buddy_crew.ENUM_WINDOWS_PROC(visit), 0)
        return found[0] if found else None

    # -- what is in a window
    def _uia(self):
        if not hasattr(self._local, "uia"):
            self._local.uia = keyit_targets._automation_with_timeouts()
        return self._local.uia

    def elements(self, hwnd):
        """The named things in one window: dicts with name, kind, rect (physical pixels), visible (not covered
        by another window) and the live element."""
        from keyit_buddy import physical_pixels

        uia, automation = self._uia()
        items = []
        with physical_pixels():
            request = automation.CreateCacheRequest()
            for prop in (uia.UIA_NamePropertyId, uia.UIA_ControlTypePropertyId, uia.UIA_BoundingRectanglePropertyId):
                request.AddProperty(prop)
            on_screen = automation.CreatePropertyCondition(uia.UIA_IsOffscreenPropertyId, False)
            condition = automation.CreateAndCondition(on_screen, automation.ControlViewCondition)
            request.TreeFilter = condition
            window = automation.ElementFromHandle(hwnd)
            found = window.FindAllBuildCache(uia.TreeScope_Descendants, condition, request)
            for index in range(min(found.Length if found else 0, MAX_ELEMENTS)):
                element = found.GetElement(index)
                kind = keyit_targets.KINDS.get(element.CachedControlType)
                name = " ".join((element.CachedName or "").split())
                if kind is None or not name:
                    continue
                rect = element.CachedBoundingRectangle
                rect = (rect.left, rect.top, rect.right, rect.bottom)
                if rect[2] - rect[0] < 3 or rect[3] - rect[1] < 3:
                    continue
                items.append({"name": name, "kind": kind, "rect": rect, "hwnd": int(hwnd), "foreground": False,
                              "visible": keyit_targets._visible_at(hwnd, *_center(rect)), "element": element})
        return items

    def still_visible(self, item):
        """Is it still there, and still not covered? (The screen may have changed since the look.)"""
        from keyit_buddy import physical_pixels

        try:
            with physical_pixels():
                rect = item["element"].CurrentBoundingRectangle
                return bool(keyit_targets._visible_at(item["hwnd"], *_center((rect.left, rect.top, rect.right,
                                                                              rect.bottom))))
        except Exception:
            return False

    def name_at(self, x, y):
        """The name of what is at a physical point ("" when the program does not say)."""
        from keyit_buddy import physical_pixels

        try:
            _, automation = self._uia()
            with physical_pixels():
                element = automation.ElementFromPoint(wintypes.POINT(int(x), int(y)))
                return " ".join((element.CurrentName or "").split())
        except Exception:
            return ""

    def is_buddy_at(self, x, y):
        from keyit_buddy import physical_pixels

        with physical_pixels():
            hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
            root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
        return bool(root) and self._is_buddy(root)

    # -- pressing and typing without the mouse
    def _pattern(self, item, pattern_id, interface_name):
        uia, _ = self._uia()
        try:
            raw = item["element"].GetCurrentPattern(pattern_id)
            return raw.QueryInterface(getattr(uia, interface_name)) if raw else None
        except Exception:
            return None

    @staticmethod
    def _done(call):
        """Run one UI Automation action. A program that opens a box and waits in it answers late: the press
        happened all the same."""
        try:
            call()
        except Exception as error:
            if getattr(error, "hresult", None) != UIA_E_TIMEOUT:
                raise

    def press(self, item):
        """Press it the way the program offers, without the mouse. False when it offers no way."""
        from keyit_buddy import physical_pixels

        with physical_pixels():
            for pattern_id, interface_name, action in (
                    (INVOKE, "IUIAutomationInvokePattern", "Invoke"),
                    (TOGGLE, "IUIAutomationTogglePattern", "Toggle"),
                    (SELECTION_ITEM, "IUIAutomationSelectionItemPattern", "Select"),
                    (EXPAND_COLLAPSE, "IUIAutomationExpandCollapsePattern", None),
                    (LEGACY, "IUIAutomationLegacyIAccessiblePattern", "DoDefaultAction")):
                pattern = self._pattern(item, pattern_id, interface_name)
                if pattern is None:
                    continue
                try:
                    if pattern_id == LEGACY and not pattern.CurrentDefaultAction:
                        continue                            # it has nothing to do when pressed: no press at all
                    if action is None:
                        opened = pattern.CurrentExpandCollapseState != 0
                        self._done(pattern.Collapse if opened else pattern.Expand)
                    else:
                        self._done(getattr(pattern, action))
                    return True
                except Exception:
                    logger.debug("%s did not take %s", item["name"], interface_name, exc_info=True)
        return False

    def set_text(self, item, text):
        """Put text in a text box without the keyboard. False when the program offers no way."""
        from keyit_buddy import physical_pixels

        with physical_pixels():
            pattern = self._pattern(item, VALUE, "IUIAutomationValuePattern")
            try:
                if pattern is None or pattern.CurrentIsReadOnly:
                    return False
                self._done(lambda: pattern.SetValue(text))
                return True
            except Exception:
                logger.debug("%s did not take the text", item["name"], exc_info=True)
                return False

    # -- the real mouse and keyboard: one agent at a time, and the mouse goes back to where you had it
    def mouse_click(self, x, y, then_type=None):
        import keyit_actions
        from keyit_buddy import physical_pixels

        with self._input:
            before = wintypes.POINT()
            with physical_pixels():
                user32.GetCursorPos(ctypes.byref(before))
            try:
                keyit_actions.click_at(x, y)
                time.sleep(0.05)
                if then_type:
                    time.sleep(0.15)
                    keyit_actions.type_text(then_type)
            finally:
                with physical_pixels():
                    user32.SetCursorPos(before.x, before.y)

    # -- two kinds of pixels
    def _monitor_rects(self, x, y, physical):
        """(own, physical) rectangles of the monitor that holds a point given in one of the two."""
        from keyit_buddy import physical_pixels

        def rect_of(handle):
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            user32.GetMonitorInfoW(handle, ctypes.byref(info))
            box = info.rcMonitor
            return box.left, box.top, box.right, box.bottom

        point = wintypes.POINT(int(x), int(y))
        if physical:
            with physical_pixels():
                handle = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        else:
            handle = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        own = rect_of(handle)
        with physical_pixels():
            return own, rect_of(handle)

    def to_own(self, x, y):
        own, physical = self._monitor_rects(x, y, physical=True)
        return scaled((x, y), physical, own)

    def to_physical(self, x, y):
        own, physical = self._monitor_rects(x, y, physical=False)
        return scaled((x, y), own, physical)


# --------------------------------------------------------------------------- the hands
def _pick(label, items):
    """The element an agent means by `label`. Its exact name first (a button can be called just "Open", which
    Buddy's own matching reads as a filler word), then the strict matching Buddy's own clicks use."""
    wanted = keyit_targets._plain(label)
    same = [item for item in items if wanted and keyit_targets._plain(item["name"]) == wanted]
    if same:
        return next((item for item in same if item["kind"] in keyit_targets.CLICKABLE), same[0])
    return keyit_targets.find_by_label(label, items)


class Hands:
    """Does what agents ask for. submit() from any thread; every agent's requests are done in order on its own
    thread, and different agents work at the same time.

    `show(event)` is Crew.post: it moves the agent's pointer. `allowed(request)` -> bool is the user's yes.
    `confirm(question)` -> bool asks the user about one risky click. `reply(request_id, result)` answers."""

    def __init__(self, show, screen=None, allowed=lambda request: True, confirm=None, reply=write_result,
                 clock=time.time, sleep=time.sleep):
        self._show_event = show
        self._screen = screen or Screen()
        self._allowed = allowed
        self._confirm = confirm
        self._reply = reply
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._lines = {}                  # agent key -> queue of its requests (it has a thread while this exists)
        self._closed = False

    # -- from any thread
    def submit(self, event):
        """Take one event. True when it was a request to do something (done later, or refused)."""
        request = normalize_request(event)
        if request is None:
            return False
        refusal = None
        with self._lock:
            line = self._lines.get(request["key"])
            if self._closed:
                refusal = "Buddy is closing."
            elif line is None and len(self._lines) >= MAX_AGENTS:
                refusal = f"{MAX_AGENTS} agents are at work already. Try again in a moment."
            elif line is not None and line.qsize() >= QUEUE_PER_AGENT:
                refusal = "Too much at once: wait for the answers first."
            else:
                if line is None:
                    line = self._lines[request["key"]] = queue.Queue()
                    threading.Thread(target=self._work, args=(request["key"], line), name="BuddyCrewHands",
                                     daemon=True).start()
                line.put(request)
        if refusal:
            self._reply(request["request"], _no(refusal))
        return True

    def close(self):
        with self._lock:
            self._closed = True
            for line in self._lines.values():
                line.put(None)

    def busy(self):
        """How many agents have work in hand."""
        with self._lock:
            return len(self._lines)

    # -- one agent's thread
    def _work(self, key, line):
        while True:
            try:
                request = line.get(timeout=IDLE_EXIT_S)
            except queue.Empty:
                with self._lock:
                    if line.empty():
                        self._lines.pop(key, None)
                        return
                continue
            if request is None:
                with self._lock:
                    self._lines.pop(key, None)
                return
            try:
                result = self.do(request)
            except Exception:
                logger.exception("Crew hands: %s of %s failed", request["do"], request["agent"])
                result = _no("That did not work: the window did not answer.")
            logger.info("Crew hands: %s %s -> %s", request["agent"], request["do"], result.get("did"))
            self._reply(request["request"], result)

    def do(self, request):
        """Do one request now, on this thread. Returns the answer for the agent."""
        if request["at"] is not None and self._clock() - request["at"] > REQUEST_MAX_AGE_S:
            return _no("That was asked too long ago, so it was not done.")
        if not self._allowed(request):
            return _no(NOT_ALLOWED)
        if request["do"] == "look":
            return self._look(request)
        if request["do"] == "click":
            return self._click(request)
        return self._type(request)

    def _show(self, request, say, spot=None, window="", state="working"):
        event = dict(request["who"], say=say, state=state)
        if spot is not None:
            event["x"], event["y"] = spot
        if window:
            event["window"] = window
        self._show_event(event)

    def _window(self, request):
        """((hwnd, title), None), or (None, the answer that says why not)."""
        if not request["window"]:
            return None, _no('Say which window: "window": "a part of its title".')
        found = self._screen.find_window(request["window"])
        if found is None:
            return None, _no(f"No window called {request['window']} is on screen.")
        return found, None

    def _find(self, request):
        """(the element the request means, None), or (None, the answer that says why not)."""
        found, problem = self._window(request)
        if problem:
            return None, problem
        hwnd, _ = found
        items = self._screen.elements(hwnd)
        item = _pick(request["target"], [item for item in items if item["visible"]])
        if item is not None:
            return item, None
        if _pick(request["target"], items) is not None:
            return None, _no(f"{request['target']} is covered by another window, so it was left alone.")
        return None, _no(f"There is no {request['target']} in {request['window']}.")

    def _look(self, request):
        found, problem = self._window(request)
        if problem:
            return problem
        hwnd, title = found
        self._show(request, f"Looking at {buddy_crew._one_line(title, 30)}", window=request["window"])
        seen = [item for item in self._screen.elements(hwnd) if item["visible"]]
        listed = []
        for item in seen[:LOOK_MAX]:
            x, y = self._screen.to_own(*_center(item["rect"]))
            listed.append({"name": item["name"], "kind": item["kind"], "x": x, "y": y})
        result = {"ok": True, "did": f"Looked at {title}", "window": title, "elements": listed}
        if len(seen) > LOOK_MAX:
            result["more"] = len(seen) - LOOK_MAX
        return result

    def _may(self, request, name, spot):
        """The rules for one click on something called `name`. None when it may go on, else the answer."""
        if WINDOW_BUTTONS.match(name.strip()):
            return _no("A window's close and minimize buttons are not for agents.")
        if not RISKY_CLICKS.search(name):
            return None
        short = name[:NAME_CHARS]
        self._show(request, f'May I click "{short}"?', spot, state="waiting")
        question = f'{request["agent"]} wants to click "{short}".\nThis could delete or send something.'
        if self._confirm is not None and self._confirm(question):
            return None
        self._show(request, f"Left {short} alone", spot)
        return _no(f'You did not say yes to "{short}", so it was not clicked.')

    def _click(self, request):
        if not request["target"]:
            if request["point"] is None:
                return _no('click needs "target" (the name on the button), or "x" and "y".')
            return self._click_point(request)
        item, problem = self._find(request)
        if problem:
            return problem
        name = item["name"][:NAME_CHARS]
        center = _center(item["rect"])
        spot = self._screen.to_own(*center)
        problem = self._may(request, item["name"], spot)
        if problem:
            return problem
        self._show(request, f"Clicking {name}", spot)
        self._sleep(ARRIVE_S)
        if not self._screen.still_visible(item):
            return _no(f"{name} moved or got covered, so it was not clicked.")
        how = "direct"
        if not self._screen.press(item):
            self._screen.mouse_click(*center)
            how = "mouse"
        self._show(request, f"Clicked {name} \u2713", spot)
        return {"ok": True, "did": f"Clicked {name}", "how": how}

    def _click_point(self, request):
        spot = request["point"]
        x, y = self._screen.to_physical(*spot)
        if self._screen.is_buddy_at(x, y):
            return _no("That is Buddy's own window.")
        name = self._screen.name_at(x, y)
        problem = self._may(request, name, spot)
        if problem:
            return problem
        self._show(request, f"Clicking {name[:NAME_CHARS]}" if name else "Clicking here", spot)
        self._sleep(ARRIVE_S)
        self._screen.mouse_click(x, y)
        self._show(request, "Clicked \u2713", spot)
        return {"ok": True, "did": f"Clicked at {spot[0]}, {spot[1]}", "how": "mouse"}

    def _type(self, request):
        if not request["target"] or not request["text"]:
            return _no('type needs "target" (the name of the text box) and "text".')
        item, problem = self._find(request)
        if problem:
            return problem
        name = item["name"][:NAME_CHARS]
        center = _center(item["rect"])
        spot = self._screen.to_own(*center)
        self._show(request, f"Typing in {name}", spot)
        self._sleep(ARRIVE_S)
        if not self._screen.still_visible(item):
            return _no(f"{name} moved or got covered, so nothing was typed.")
        how = "direct"
        if not self._screen.set_text(item, request["text"]):
            self._screen.mouse_click(*center, then_type=request["text"])
            how = "keyboard"
        self._show(request, f"Typed in {name} \u2713", spot)
        return {"ok": True, "did": f"Typed in {name}", "how": how}
