"""The pointers' button: one click in the taskbar's corner switches every agent pointer off or on.

An arrow icon in the notification area, next to the clock (Settings -> Buddy (AI) puts it there). Green arrow =
the crew's pointers are live; grey arrow with a red bar = they are off. One click flips the same switch as
"Show my other AI agents as extra pointers" in Settings and `Buddy.exe --pointers toggle`, so the three always
agree. With the task watch on (crew_tasks.py) its trace is written either way, and the button's menu opens it.

The button only shows and asks: `is_on()` reads the switch, `flip()` is what a click does (Buddy does the
work on its own thread).
"""

import ctypes
import logging
import threading

logger = logging.getLogger(__name__)

NAME = "buddy-pointers"
FOLLOW_EVERY_S = 1.0            # a change made elsewhere (Settings, --pointers) shows on the button this soon
KEEP_EVERY = 5                  # every so many looks the icon is checked: is it still in the taskbar?


def tip(on):
    return "Agent pointers: ON  (click to turn off)" if on else "Agent pointers: OFF  (click to turn on)"


def button_image(on):
    """Buddy's arrow: green when the pointers are live, grey with a red bar when they are off."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    arrow = [(12, 6), (12, 50), (24, 39), (33, 58), (41, 54), (32, 36), (48, 36)]
    draw.polygon(arrow, fill="#22c55e" if on else "#9ca3af", outline="#0f172a")
    draw.line(arrow + [arrow[0]], fill="#0f172a", width=3)
    if not on:
        draw.line([(8, 58), (58, 8)], fill="#ef4444", width=7)
    return image


class Button:
    """The icon. start() shows it, stop() takes it away."""

    def __init__(self, is_on, flip, open_trace=None):
        self.is_on = is_on
        self.flip = flip
        self.open_trace = open_trace          # the menu's second line, when there is a trace to open
        self.icon = None
        self.shown = None                     # the answer the icon shows now
        self.icon_was_missing = False
        self.stopped = threading.Event()

    def start(self):
        threading.Thread(target=self._show_icon, name="BuddyPointersButton", daemon=True).start()
        threading.Thread(target=self._follow, name="BuddyPointersButtonFollow", daemon=True).start()

    def stop(self):
        self.stopped.set()
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                logger.debug("The pointers' button did not stop cleanly", exc_info=True)

    def show(self):
        """Make the icon say what the switch says now."""
        icon, on = self.icon, bool(self.is_on())
        if icon is None or on == self.shown:
            return
        icon.icon = button_image(on)
        icon.title = tip(on)
        icon.update_menu()
        self.shown = on

    def _clicked(self, *_):
        try:
            self.flip()
        except Exception:
            logger.exception("The pointers' button could not flip the switch")

    def _menu(self, pystray):
        lines = [pystray.MenuItem(lambda _: tip(bool(self.is_on())), self._clicked, default=True)]
        if self.open_trace is not None:
            lines.append(pystray.MenuItem("Open the trace (what the agents and tasks did)",
                                          lambda *_: self.open_trace()))
        return pystray.Menu(*lines)

    def _show_icon(self):
        """Its own thread. Right after you log on the taskbar may not be there yet, and then Windows refuses
        the icon: it is tried again until it is there. (After that pystray itself puts the icon back when
        Explorer restarts.)"""
        try:
            import pystray
        except Exception:                               # no button is no reason to stop anything else
            logger.exception("No taskbar button for the pointers")
            return
        failures = 0
        while not self.stopped.is_set():
            try:
                on = bool(self.is_on())
                icon = pystray.Icon(NAME, button_image(on), tip(on), self._menu(pystray))
                self.icon, self.shown = icon, on
                if self.stopped.is_set():               # stopped while it was made
                    self.icon = None
                    return
                icon.run()
                return                                  # stopped on purpose
            except Exception as error:
                self.icon = None
                failures += 1
                if failures in (1, 10) or failures % 100 == 0:
                    logger.warning("No taskbar button for the pointers yet (try %d): %s: %s", failures,
                                   type(error).__name__, error)
                self.stopped.wait(min(3 * failures, 30))

    def keep_icon(self):
        """Windows can refuse a new icon while it is busy right after you log on, and says so only in an answer
        pystray does not read. So the icon is asked for now and then, and added again when it is not there."""
        icon = self.icon
        if icon is None or not getattr(icon, "_running", False) or not icon.visible:
            return
        from pystray._util import win32

        there = win32.Shell_NotifyIcon(win32.NIM_MODIFY, win32.NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW), hWnd=icon._hwnd, uFlags=0))
        if not there:
            icon._show()
            if not self.icon_was_missing:
                logger.info("The pointers' button was not in the taskbar: added again")
            self.icon_was_missing = True
        else:
            self.icon_was_missing = False

    def _follow(self):
        """Its own thread: the button shows a change made elsewhere within a second."""
        looks = 0
        while not self.stopped.wait(FOLLOW_EVERY_S):
            try:
                self.show()
                looks += 1
                if looks % KEEP_EVERY == 0:
                    self.keep_icon()
            except Exception:
                logger.exception("The pointers' button could not be kept up to date")
                self.stopped.wait(10)
