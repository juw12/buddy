"""Where a window is pressed through the screen, for the crew's task watch (crew_tasks.py).

A program can work a window without touching your mouse: it asks Windows for the things in it (UI
Automation) and presses them there. A script that reads Gmail in your own Chrome works like this, and so does
one that presses a button in Notepad, in a settings window or in a tool of your company. Such a press never
moves your mouse, and crew_browser.py cannot see it (it needs a browser's debugging port). But the program
that is pressed tells Windows "this thing was pressed", with the place the thing had. This listens for exactly
that, in the windows a followed task works in, and hands the place to the task watch, which lets the crew
pointer fly there.

Only the place and the kind of thing are read (a link, a button, a box). Never a name, a text or a value.

Two kinds of windows:

* a browser's, and every app that is built on Chrome (Edge, Obsidian, VS Code: "a page"). Buddy never makes
  such an app do extra work: a page only tells Windows about its things once some program has asked for them.
  So the window is first only listened to in the way that asks nothing (a WinEvent hook, out of context). The
  first news about a thing inside the page shows that somebody works the page through the screen, and only
  then the listeners for presses are put on that one window. (Measured with Chrome 157: a page that changes
  by itself said nothing for 12 s, with and without Buddy's listeners on its window; the news began in the
  instant another program asked for the page's things.)
* every other window ("plain"): listened to at once, it costs the program nothing. Measured on this PC, with
  a window off the screen pressed from another process: a classic Windows program tells a pressed button, a
  ticked box, a chosen entry of a list and a new value of a box; so does a WPF program; an old Windows Forms
  program tells the entry, the tab and the value, and for its buttons and tick boxes only "my state changed"
  (a WinEvent), which is heard through the same kind of hook and taken as the press.

What cannot be seen this way:

* a press on a thing that is gone in the same instant (a menu entry that closes its menu, a "back" button
  that takes its own view away): the program tells nothing about it, so the pointer stays where it was. A
  thing that is gone a moment later (a row of mail that opens its mail quickly) is sometimes told without
  its place: that press is in the trace, and the pointer says it at the window. (Asking where the keyboard
  went does not help: Windows then only has the thing's number, the thing itself is gone. Tried.)
* typing is only known as "this box has another value", so it is shown for the box that has the keyboard;
* a round box (one of several choices) of an old Windows Forms program that is chosen through the screen
  tells nothing at all (measured), and neither does a box that is filled in without the keyboard in it;
* a program that draws everything itself and tells Windows nothing (a game, a drawing area): nothing is told.
  A click such a program gets from another program's mouse is followed all the same (crew_tasks.mouse_action);
* Firefox gives no such sign, and a command window has nothing to press: they are not followed.

ScreenWatch(on_action, log): the task watch calls follow() at every look; a thread of its own does the rest.
"""
import ctypes
import threading
import time
from ctypes import wintypes

import crew_browser                               # the page's place in a window, what is on top, whose typing it is

PAGE_FRAME = "Chrome_WidgetWin_"                  # a window of Chrome, Edge or an app built on them (its class begins so)
PAGE_CLASS = "Chrome_RenderWidgetHostHWND"        # the part of such a window that shows the page
NOT_FOLLOWED = ("MozillaWindowClass", "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow")
FORMS_BUTTON = "WindowsForms10.BUTTON"            # a button, tick box or round box of an old Windows Forms program
INVOKED, VALUE, TOGGLED = 20009, 30045, 30086     # UI Automation: Invoke.Invoked, Value.Value, Toggle.ToggleState
SELECTED = 20012                                  # SelectionItem.ElementSelected: an entry of a list, a tab
PLACE, KIND, HAS_KEYBOARD = 30001, 30003, 30008   # BoundingRectangle, ControlType, HasKeyboardFocus
BUTTON = 50000
KINDS = {BUTTON: "button", 50031: "button", 50005: "link", 50002: "tick box", 50013: "tick box", 50004: "box",
         50003: "list", 50007: "list", 50011: "list", 50024: "list", 50019: "tab"}
TYPED_IN = (50004, 50003)                         # Edit, ComboBox
# Every accessibility event but "moved" (0x800B: far too many of those, and none is needed)
HOOKED = ((0x8000, 0x800A), (0x800C, 0x8015))
STATE_CHANGED, OBJID_CLIENT = 0x800A, -4          # "my state changed", said by a thing that is a window itself
BUTTONS_HOOKED = ((STATE_CHANGED, STATE_CHANGED),)
MOST_PLAIN = 12                 # so many plain windows are listened to at once (an agent seldom has three)
SAME_PRESS_S = 0.5              # a second report about the same thing this soon is the same press
TYPING_EVERY_S = 1.5            # typing in one window is told this often at most (as crew_browser does)
OWN_CLICK_S = 1.0               # your mouse on the thing, used this short a time ago: your own click
OWN_MARGIN_PX = 4
IN_FRONT_S = 0.5                # your own typing: the window was in front for this long already
LOOK_S = 0.25                   # while a window is waited for, the watch's wishes are read this often
QS_ALLINPUT = 0x04FF

user32 = ctypes.WinDLL("user32")                  # its own copy: the argument types below reach no other module
kernel32 = ctypes.WinDLL("kernel32")
HOOK_PROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
                               wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, HOOK_PROC, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD]
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                             wintypes.DWORD]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


def spots(box, first=None):
    """Points of a thing, to find one of them on screen: its middle (or `first`), then towards its edges."""
    left, top, right, bottom = box
    middle_x, middle_y = (left + right) // 2, (top + bottom) // 2
    quarter_x, quarter_y = (right - left) // 4, (bottom - top) // 4
    found = [first] if first else []
    found += [(middle_x, middle_y), (left + quarter_x, middle_y), (right - quarter_x, middle_y),
              (middle_x, top + quarter_y), (middle_x, bottom - quarter_y)]
    return found


def idle_seconds():
    """How long ago you last used the keyboard or the mouse."""
    last = crew_browser.LASTINPUTINFO(ctypes.sizeof(crew_browser.LASTINPUTINFO), 0)
    user32.GetLastInputInfo(ctypes.byref(last))
    return ((kernel32.GetTickCount() - last.dwTime) & 0xFFFFFFFF) / 1000


def mouse():
    cursor = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cursor))
    return cursor.x, cursor.y


def class_of(hwnd):
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, name, 64)
    return name.value


def page_of(hwnd):
    """The top-level window whose web page this window is, or 0 when it is not a browser's page."""
    return int(user32.GetAncestor(hwnd, crew_browser.GA_ROOT) or 0) if class_of(hwnd) == PAGE_CLASS else 0


def kind_of(hwnd):
    """How a top-level window is followed: "page" (a browser, or an app built on one: only once somebody works
    its page), "plain" (at once), or None (never)."""
    name = class_of(hwnd)
    if name.startswith(PAGE_FRAME):
        return "page"
    return None if name in NOT_FOLLOWED else "plain"


def forms_button(hwnd):
    """(the top-level window, the place on screen) of a Windows Forms button, or None for any other window."""
    if not class_of(hwnd).startswith(FORMS_BUTTON):
        return None
    place = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(place)):
        return None
    return (int(user32.GetAncestor(hwnd, crew_browser.GA_ROOT) or 0),
            (place.left, place.top, place.right, place.bottom))


class Screen:
    """Windows itself: the hook that asks nothing, and UI Automation's listeners. Made and used by one thread."""

    def __init__(self, on_sign, on_button=None):
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)  # the listeners are called on Windows' own threads
        except OSError:
            # This thread is the first of the program to load comtypes, and loading it made the thread a
            # single-threaded one ("cannot change thread mode after it is set"): start over
            comtypes.CoUninitialize()
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as api

        self.api = api
        try:
            self.automation = comtypes.client.CreateObject(api.CUIAutomation8, interface=api.IUIAutomation2)
            self.automation.ConnectionTimeout = 1000                # a browser that hangs is given up on
            self.automation.TransactionTimeout = 1500
        except Exception:
            self.automation = comtypes.client.CreateObject(api.CUIAutomation, interface=api.IUIAutomation)
        self.cache = self.automation.CreateCacheRequest()           # what a report brings along, read in the browser
        for wanted in (PLACE, KIND, HAS_KEYBOARD):
            self.cache.AddProperty(wanted)

        class Listener(comtypes.COMObject):
            _com_interfaces_ = [api.IUIAutomationEventHandler, api.IUIAutomationPropertyChangedEventHandler]

            def __init__(self, tell):
                super().__init__()
                self.tell = tell

            def report(self, what, thing):
                try:
                    box = thing.CachedBoundingRectangle
                    told = (thing.CachedControlType, (box.left, box.top, box.right, box.bottom),
                            bool(thing.GetCachedPropertyValue(HAS_KEYBOARD)))
                except Exception:                                   # the thing was gone before its place was read
                    told = (0, None, False)
                try:
                    self.tell(what, *told)
                except Exception:                                   # nothing may fly back into Windows
                    pass

            def IUIAutomationEventHandler_HandleAutomationEvent(self, sender, event_id):
                self.report(event_id, sender)
                return 0

            def IUIAutomationPropertyChangedEventHandler_HandlePropertyChangedEvent(self, sender, property_id, new_value):
                self.report(property_id, sender)
                return 0

        self.Listener = Listener

        def heard(_, event, hwnd, id_object, id_child, thread, at):
            if id_child < 0 and hwnd:                               # news about a thing inside a window
                on_sign(hwnd)
            elif on_button and event == STATE_CHANGED and id_object == OBJID_CLIENT and hwnd:
                on_button(hwnd)                                     # a thing that is a window itself: a button?

        self.heard = HOOK_PROC(heard)                               # kept: Windows calls it for as long as a hook lives

    def hook(self, pid, events=HOOKED):
        """Listen to one process the way that asks it nothing. pump() must be called for the news to come."""
        return [handle for handle in (user32.SetWinEventHook(first, last, None, self.heard, pid, 0, 0)
                                      for first, last in events) if handle]

    def unhook(self, handles):
        for handle in handles:
            user32.UnhookWinEvent(handle)

    def pump(self, seconds):
        user32.MsgWaitForMultipleObjects(0, None, False, int(seconds * 1000), QS_ALLINPUT)
        message = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def listen(self, hwnd, tell, selections=False):
        """`tell(what, kind, box, has the keyboard)` for every press, tick and new value in this window, and
        with `selections` for every entry that is chosen (a plain window has no other word for that)."""
        window = self.automation.ElementFromHandle(hwnd)
        listener = self.Listener(tell)
        events = (INVOKED, SELECTED) if selections else (INVOKED,)
        for event in events:
            self.automation.AddAutomationEventHandler(event, window, self.api.TreeScope_Subtree, self.cache, listener)
        changes = (ctypes.c_int * 2)(VALUE, TOGGLED)
        self.automation.AddPropertyChangedEventHandlerNativeArray(window, self.api.TreeScope_Subtree, self.cache,
                                                                 listener, changes, len(changes))
        return window, listener, events

    def unlisten(self, token):
        window, listener, events = token
        removals = [lambda event=event: self.automation.RemoveAutomationEventHandler(event, window, listener)
                    for event in events]
        for remove in removals + [lambda: self.automation.RemovePropertyChangedEventHandler(window, listener)]:
            try:
                remove()
            except Exception:                                       # the window is gone already
                pass

    def close(self):
        try:
            self.automation.RemoveAllEventHandlers()                # only this client's own
        except Exception:
            pass


class ScreenWatch:
    """Follows the presses in the windows it is told to follow. follow() from the task watch's window loop,
    stop() for good."""

    def __init__(self, on_action, log, screen=Screen):
        # (window, process, "click" or "type", kind of thing, x, y); x None: on no spot you can see, and with
        # gone=True: the thing was gone before its place could be read
        self.on_action = on_action
        self.log = log
        self.make_screen = screen
        self.wanted = {}                # window -> its process, as the task watch last said
        self.front = (0, 0.0)           # the window in front, and since when
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        # the thread's own
        self.screen = None
        self.kinds = {}                 # window -> "page", "plain" or None (kind_of), asked once
        self.waiting = {}               # window -> process: a page nobody was seen to work yet
        self.plain = {}                 # window -> process: the plain windows that are listened to
        self.hooks = {}                 # process -> (which news, its hooks): a page is waited for, or its
                                        # plain window is listened to (a Windows Forms button only says it so)
        self.spoke = set()              # windows whose page gave a sign since the last look
        self.listening = {}             # window -> what unlisten() needs
        self.failed = set()             # windows that could not be listened to (told once)
        # the listeners' own (Windows calls them on its threads)
        self.lock = threading.Lock()
        self.pressed = {}               # window -> (when, box) of the last press told
        self.typed = {}                 # window -> when typing was last told

    # --- from the task watch
    def follow(self, windows, front=0):
        """At every look: the windows to follow ({window: its process}) and the window in front.
        Never waits."""
        if front != self.front[0]:
            self.front = (front, time.monotonic())
        if windows == self.wanted:
            return
        self.wanted = dict(windows)      # a new dict every time: the thread reads it, nobody changes it
        if windows and self.thread is None and not self.stopped.is_set():
            self.start()
        self.wake.set()

    def start(self):
        self.thread = threading.Thread(target=self.run, name="BuddyTaskScreen", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()

    # --- the thread
    def run(self):
        try:
            self.screen = self.make_screen(self.page_spoke, self.button_spoke)
        except Exception as error:
            self.log(f"presses through the screen are not followed: {type(error).__name__}: {error}")
            return
        try:
            while not self.stopped.is_set():
                self.wake.clear()
                self.settle()
                if self.hooks:
                    self.screen.pump(LOOK_S)         # the hooks' news comes in here
                else:
                    self.wake.wait()
        except Exception as error:                   # a watch must not die silently
            self.log(f"error (presses through the screen): {type(error).__name__}: {error}")
        finally:
            self.wanted = {}
            try:
                self.settle()
                self.screen.close()
            except Exception:
                pass

    def page_spoke(self, hwnd):
        """A hook's news (in the thread, inside pump): is it about a thing in the page of a waited-for window?"""
        window = page_of(hwnd)
        if window in self.waiting:
            self.spoke.add(window)

    def button_spoke(self, hwnd):
        """A hook's news (in the thread, inside pump): a thing that is a window itself says "my state changed".
        A button, tick box or round box of a Windows Forms program says only that when it is pressed (and when
        its program ticks it by itself)."""
        if not self.plain:
            return
        found = forms_button(hwnd)
        if found and found[0] in self.plain:
            self.saw(found[0], self.plain[found[0]], INVOKED, BUTTON, found[1], False)

    def listen(self, hwnd, pid, plain=False):
        name = "window" if plain else "browser window"
        try:
            def tell(what, kind, box, has_keyboard):
                self.saw(hwnd, pid, what, kind, box, has_keyboard)

            self.listening[hwnd] = self.screen.listen(hwnd, tell, selections=True) if plain else self.screen.listen(hwnd, tell)
            if plain:
                self.plain[hwnd] = pid
            self.log(f"presses in the {name} {hwnd:#x} (pid {pid}) are followed"
                     + ("" if plain else ": it is worked through the screen"))
        except Exception as error:
            self.failed.add(hwnd)
            self.log(f"presses in the {name} {hwnd:#x} (pid {pid}) are not followed: {type(error).__name__}: {error}")

    def settle(self):
        """Make what is hooked and listened to match what is wanted."""
        wanted = self.wanted
        for hwnd in [hwnd for hwnd in self.listening if hwnd not in wanted]:
            self.screen.unlisten(self.listening.pop(hwnd))
            self.plain.pop(hwnd, None)
            with self.lock:
                self.pressed.pop(hwnd, None)
                self.typed.pop(hwnd, None)
        self.waiting = {hwnd: pid for hwnd, pid in self.waiting.items() if wanted.get(hwnd) == pid}
        self.failed &= set(wanted)
        self.kinds = {hwnd: kind for hwnd, kind in self.kinds.items() if hwnd in wanted}
        for hwnd, pid in wanted.items():
            if hwnd in self.listening or hwnd in self.failed or hwnd in self.waiting:
                continue
            if hwnd not in self.kinds:
                self.kinds[hwnd] = kind_of(hwnd)
            if self.kinds[hwnd] == "page":
                self.waiting[hwnd] = pid
            elif self.kinds[hwnd] == "plain" and len(self.plain) < MOST_PLAIN:
                self.listen(hwnd, pid, plain=True)
        spoke, self.spoke = self.spoke, set()
        for hwnd in spoke:
            pid = self.waiting.pop(hwnd, None)
            if pid is not None:
                self.listen(hwnd, pid)
        hooked = {pid: BUTTONS_HOOKED for pid in self.plain.values()}
        hooked.update({pid: HOOKED for pid in self.waiting.values()})
        for pid in [pid for pid, (events, _) in self.hooks.items() if hooked.get(pid) != events]:
            self.screen.unhook(self.hooks.pop(pid)[1])
        for pid, events in hooked.items():
            if pid not in self.hooks:
                self.hooks[pid] = (events, self.screen.hook(pid, events))

    # --- the listeners (Windows' own threads)
    def saw(self, hwnd, pid, what, kind, box, has_keyboard):
        """One report of the window: a thing was pressed (INVOKED), ticked (TOGGLED), chosen (SELECTED) or
        got another value (VALUE). `box` is the thing's place on screen (left, top, right, bottom); None: the
        thing was gone before its place could be read."""
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))      # real pixels on a scaled screen
        except (AttributeError, OSError):
            pass
        if box is None:
            if what == INVOKED:                                      # a press all the same: told without a place
                with self.lock:
                    if time.monotonic() - self.pressed.get(hwnd, (-SAME_PRESS_S, None))[0] < SAME_PRESS_S:
                        return
                    self.pressed[hwnd] = (time.monotonic(), None)
                if not self.yours("click", hwnd, None):
                    self.on_action(hwnd, pid, "click", "", None, None, gone=True)
            return
        left, top, right, bottom = box
        if right - left < 2 or bottom - top < 2:
            return                                                   # it has no place
        now = time.monotonic()
        first = None
        if what == VALUE:
            # A box gets another value from the page itself too (the address bar on every new page, a search
            # box the page fills in): only a box in the page that has the keyboard is somebody typing.
            if kind not in TYPED_IN or not has_keyboard:
                return
            middle = (left + right) // 2, (top + bottom) // 2
            if hwnd not in self.plain:                               # a browser: only a box inside the page
                area = next((area[2] for area in crew_browser.page_areas(pid) if area[0] == hwnd), None)
                if area is None or not (area[0] <= middle[0] < area[2] and area[1] <= middle[1] < area[3]):
                    return
            with self.lock:
                if now - self.typed.get(hwnd, -TYPING_EVERY_S) < TYPING_EVERY_S:
                    return
                self.typed[hwnd] = now
            action, thing = "type", "box"
            first = (left + min((right - left) // 2, 80), middle[1])
        else:
            with self.lock:
                when, before = self.pressed.get(hwnd, (-SAME_PRESS_S, None))
                # one press can be told twice (a tick box: pressed, and ticked), and one press of the page's
                # own "all" box ticks every box of a list: ticks right after a press are that press. So are
                # the entries a list chooses right after it (a program that fills its list chooses many)
                if now - when < SAME_PRESS_S and (what in (TOGGLED, SELECTED) or before == box):
                    return
                self.pressed[hwnd] = (now, box)
            action, thing = "click", KINDS.get(kind, "")
        point = next((spot for spot in spots(box, first) if crew_browser.on_top(hwnd, spot)), None)
        if point is None:
            self.on_action(hwnd, pid, action, thing, None, None)     # covered, or the window is off the screen
        elif not self.yours(action, hwnd, box):
            self.on_action(hwnd, pid, action, thing, point[0], point[1])

    def yours(self, action, hwnd, box):
        """Your own click or typing in that window is not the agent's work."""
        if action == "click":
            x, y = mouse()
            if box is None:                                          # no place known: your mouse is in that window
                on_it = crew_browser.on_top(hwnd, (x, y))
            else:
                on_it = box[0] - OWN_MARGIN_PX <= x < box[2] + OWN_MARGIN_PX and box[1] - OWN_MARGIN_PX <= y < box[3] + OWN_MARGIN_PX
            return on_it and idle_seconds() < OWN_CLICK_S
        # A program that presses through the screen brings the browser to the front for a moment, also while
        # you type somewhere else: typing is only yours in a window that was in front already.
        front, since = self.front
        return front == hwnd and time.monotonic() - since >= IN_FRONT_S and idle_seconds() < crew_browser.OWN_KEYS_S
