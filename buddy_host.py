"""
Buddy = the app. It owns the process, the tray icon, the main window, autostart and quitting.

Its features:
  * Focus     - the Pips desktop pet + focus tracker, running INSIDE Buddy (Pips' window is a
                child of Buddy's root; Pips' own tray, updater and single-instance are switched off)
  * Sounds    - KeyIt mechanical keyboard sounds
  * AI helper - local screen helper (Ollama); the pet talks and beams at what it explains

The vendored Pips source in pips/ stays byte-identical; Buddy adapts it from the outside.
"""
import argparse
import logging
import math
import re
import threading
import tkinter as tk
import os
import sys
import time
import types
from datetime import datetime
from pathlib import Path

BUNDLE_DIR = Path(__file__).resolve().parent          # app root: source checkout or <exe dir>/app
PIPS_DIR = BUNDLE_DIR / "pips"
ICON_PATH = BUNDLE_DIR / "assets" / "buddy.ico"
KEYIT_DIR = BUNDLE_DIR / "keyit"
for folder in (KEYIT_DIR, PIPS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

# Buddy keeps its sound/AI settings apart from a standalone KeyIt install
os.environ.setdefault("KEYIT_HOME", str(Path(os.environ.get("APPDATA", Path.home())) / "Buddy"))

import app as pips_app  # noqa: E402  (Pips main module)
import keyit_buddy  # noqa: E402
import keyit_core  # noqa: E402
import keyit_gui  # noqa: E402
import buddy_home  # noqa: E402
import buddy_updater  # noqa: E402

BUDDY_NAME = "Buddy"
BUDDY_VERSION = "1.3.2"
CONTEXT_REFRESH_MS = 60_000
UPDATE_FIRST_CHECK_MS = 60_000              # first update check a minute after start...
UPDATE_EVERY_MS = 3 * 3600 * 1000           # ...then every 3 hours
UPDATE_IDLE_S = 90                          # a ready update installs after 90 s without keyboard/mouse use
UPDATE_IDLE_POLL_MS = 15_000
PREVIOUS_KEEP_MS = 10 * 60 * 1000           # the old version is kept 10 min after an update
logger = logging.getLogger("buddy")


# --------------------------------------------------------------------------- focus context
def pips_focus_summary(widget):
    """Short plain-text summary of today's Pips data for the AI (computed on the Tk thread)."""
    scoring = getattr(pips_app, "scoring", None)
    if scoring is None or widget.con is None:
        return ""
    day = datetime.now().strftime("%Y-%m-%d")
    data = scoring.compute_daily(widget.con, day, widget.cfg)
    minutes = lambda key: int(data.get(key, 0) or 0) // 60
    lines = [
        f"Today so far: focus score {float(data.get('score', 0)):.0f}/100 ({data.get('stars', 0)}/5 stars), "
        f"productive {minutes('productive_seconds')} min, distracting {minutes('distracting_seconds')} min, "
        f"idle {minutes('idle_seconds')} min, {data.get('pomodoros_done', 0)} pomodoros, "
        f"{data.get('app_switches', 0)} app switches."
    ]
    apps = scoring.breakdown(widget.con, day) if hasattr(scoring, "breakdown") else []
    top_apps = [item for item in apps if isinstance(item, dict) and item.get("seconds", 0) >= 60][:5]
    if top_apps:
        lines.append("Top apps: " + ", ".join(
            f"{item['app']} {item['seconds'] // 60} min ({item.get('category', 'neutral')})" for item in top_apps))
    goals = widget.cfg.get("goals") or {}
    if goals:
        lines.append(f"Daily goals: {goals.get('deep_work_hours', '?')} h deep work, "
                     f"{goals.get('pomodoros', '?')} pomodoros, "
                     f"max {goals.get('max_distraction_hours', '?')} h distraction. "
                     f"Goal met: {'yes' if data.get('goal_met') else 'not yet'}.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- pet as the AI
class PetGuideOverlay(keyit_buddy.BuddyOverlay):
    """Buddy's AI lives next to the mouse pointer (Clicky style): listening, thinking, the answer
    and the pointing tour all happen at the cursor. The desktop pet just reacts with a word or two."""

    def __init__(self, root, pet_getter):
        super().__init__(root)
        self.pet_getter = pet_getter

    def _pet(self):
        pet = self.pet_getter()
        if pet is None or not getattr(pet, "visible", False) or getattr(pet, "fs_hidden", False):
            return None
        if getattr(pet, "_destroyed", False):
            return None
        return pet

    def _pet_react(self, text, duration):
        pet = self._pet()
        if pet is None:
            return
        try:
            pet.say(text, duration=duration, priority=3)
        except Exception:
            logger.exception("pet.say failed")

    def _pet_quiet(self):
        pet = self._pet()
        if pet is not None:
            try:
                pet.bubble.hide()
            except Exception:
                pass

    def listening(self, monitor, cursor):
        super().listening(monitor, cursor)
        self._pet_react("\U0001F442", 3)

    def clear_for_screenshot(self):
        super().clear_for_screenshot()
        self._pet_quiet()                      # keep bubbles out of the screenshot

    def tour(self, screen_points, answer_text, dwell_ms=1800):
        super().tour(screen_points, answer_text, dwell_ms)
        self._pet_react("\U0001F449 look!", 3)


# --------------------------------------------------------------------------- autostart
STARTUP_SHORTCUT = Path(os.environ.get("APPDATA", Path.home())) / r"Microsoft\Windows\Start Menu\Programs\Startup" / "Buddy.lnk"


def autostart_enabled():
    return STARTUP_SHORTCUT.exists()


def set_autostart(enabled):
    """Startup-folder shortcut 'Buddy.lnk' (never touches a standalone Pips.lnk)."""
    if not enabled:
        STARTUP_SHORTCUT.unlink(missing_ok=True)
        return
    from win32com.client import Dispatch

    if getattr(sys, "frozen", False):
        target, arguments, workdir = Path(sys.executable), "", Path(sys.executable).parent
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        target = pythonw if pythonw.exists() else Path(sys.executable)
        arguments = f'"{BUNDLE_DIR / "buddy.py"}"'
        workdir = BUNDLE_DIR
    STARTUP_SHORTCUT.parent.mkdir(parents=True, exist_ok=True)
    shortcut = Dispatch("WScript.Shell").CreateShortCut(str(STARTUP_SHORTCUT))
    shortcut.TargetPath = str(target)
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = str(workdir)
    shortcut.IconLocation = str(ICON_PATH)
    shortcut.save()


# --------------------------------------------------------------------------- Pips as a feature
def rebrand_pips_texts():
    """Pips' texts say 'Pips' (e.g. 'Quit Pips'); inside Buddy they say 'Buddy'. New pets are called Buddy."""
    i18n = getattr(pips_app, "i18n", None)
    pattern = re.compile(r"\bPips\b(?!-Guide)")

    def swap(value):
        if isinstance(value, str):
            return pattern.sub(BUDDY_NAME, value)
        if isinstance(value, list):
            return [swap(item) for item in value]
        return value

    if i18n is not None:
        for table in (i18n.S, i18n.L):
            for key, entry in table.items():
                table[key] = type(entry)(swap(value) for value in entry)
    pet_module = getattr(pips_app, "pet_mod", None)
    if pet_module is not None:
        pet_module.DEFAULT_PET_SETTINGS["name"] = BUDDY_NAME


class _TkUnderBuddy(types.ModuleType):
    """tkinter look-alike whose Tk() returns a child window of Buddy's root (used while Pips builds)."""

    def __init__(self, real_tk, master):
        super().__init__("tkinter_under_buddy")
        self._real_tk = real_tk
        self._master = master

    def __getattr__(self, name):
        return getattr(self._real_tk, name)

    def Tk(self, *args, **kwargs):
        return self._real_tk.Toplevel(self._master)


class FocusFeature(pips_app.ProductivityWidget):
    """Pips (pet + focus tracker) as one feature of Buddy. Buddy decides about tray, updates and quitting."""

    def __init__(self, buddy, cfg, con, no_track=False):
        super().__init__(cfg, con, no_track=no_track)
        self.buddy = buddy

    def build(self):
        real_tk = pips_app.tk
        pips_app.tk = _TkUnderBuddy(real_tk, self.buddy.root)
        try:
            super().build()
        finally:
            pips_app.tk = real_tk
        self.root.title(f"{BUDDY_NAME} \u00b7 Focus")

    # Buddy has the one tray icon (with the Ctrl+Alt+P hotkey)
    def _start_tray(self):
        return None

    def _toggle_autostart(self):
        try:
            set_autostart(not autostart_enabled())
            state = "on" if autostart_enabled() else "off"
            if self.pet is not None:
                self.pet.say(f"Start Buddy with Windows: {state}", duration=4, priority=2)
            self.buddy.refresh()
        except Exception:
            logger.exception("Autostart toggle failed")

    # Updates come with Buddy, never from the Pips updater
    def _auto_update_tick(self):
        return None

    def _update_watchdog(self):
        return None

    def _announce_installed_update(self):
        return None

    def _check_updates(self, manual: bool = False):
        if manual and self.pet is not None:
            self.pet.say("Updates come with Buddy.", duration=6, priority=2)
        return False

    def _on_close(self):
        self.buddy.quit()               # "Quit" anywhere in the pet/focus menus quits Buddy

    def shutdown(self):
        pips_app.ProductivityWidget._on_close(self)


class _BuddyTrayForPips:
    """What Pips sees as 'its' tray: Buddy's tray. Pips only pops its card up when it thinks there is
    no tray (nothing else to bring the pet back), so this must always be present and truthy."""

    def __init__(self, app):
        self.app = app

    @property
    def hotkey_label(self):
        return self.app.tray.hotkey_label if self.app.tray is not None else ""

    def notify(self, title, text):
        tray = self.app.tray
        return bool(tray is not None and tray.notify(title, text))

    def set_tooltip(self, _text):
        return True                      # Buddy owns the tooltip

    def drain(self):
        return []

    def stop(self):
        return None                      # Buddy stops its own tray on quit


# --------------------------------------------------------------------------- host adapter for KeyIt's window
class BuddyHost:
    """What KeyIt's settings window needs from Buddy: it becomes Buddy's main window."""

    window_title = BUDDY_NAME
    tagline = f"v{BUDDY_VERSION}  \u00b7  your desktop buddy"
    icon_path = ICON_PATH

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._context = ""

    def make_overlay(self):
        return PetGuideOverlay(self.root, lambda: self.app.focus.pet if self.app.focus else None)

    def build_tabs(self, notebook):
        self.app.tabs = buddy_home.BuddyTabs(self.app, notebook)

    def on_refresh(self):
        self.app.refresh()

    def quit(self):
        self.app.quit()

    def autostart_enabled(self):
        return autostart_enabled()

    def set_autostart(self, enabled):
        set_autostart(enabled)
        self.app.refresh()

    def buddy_context(self):
        return self._context                       # cached; refreshed on the Tk thread (sqlite!)

    def refresh_context(self):
        try:
            self._context = pips_focus_summary(self.app.focus)
        except Exception:
            logger.exception("Could not read focus data")
        self.root.after(CONTEXT_REFRESH_MS, self.refresh_context)


# --------------------------------------------------------------------------- pet menu
def install_pet_menu(app):
    """Buddy goes first in the pet's right-click menu (header + a Buddy section), without editing pet.py."""
    pet_module = getattr(pips_app, "pet_mod", None)
    if pet_module is None or getattr(pet_module.DesktopPet, "_buddy_menu_installed", False):
        return
    original = pet_module.DesktopPet._show_fancy_menu

    def show_fancy_menu_with_buddy(pet, event, fancy_menu_module):
        base = fancy_menu_module.FancyMenu

        class BuddyMenu(base):
            _buddy_added = False

            def header(self, title, subtitle="", avatar="", **kwargs):
                if title == "Pips":
                    title, subtitle, avatar = BUDDY_NAME, f"v{BUDDY_VERSION} \u00b7 your desktop buddy", "B"
                return base.header(self, title, subtitle, avatar, **kwargs)

            def _add_buddy_section(self):
                if self._buddy_added:
                    return
                self._buddy_added = True
                base.section(self, BUDDY_NAME)
                base.item(self, "\U0001F3E0", "Open Buddy", app.open_home)
                base.item(self, "\U0001F399", "Ask Buddy", app.open_ask, hint="tap Ctrl+Alt+Space")
                base.toggle(self, "\u2328", "Keyboard sounds", app.sounds_enabled(), app.toggle_sounds)

            def section(self, label):
                self._add_buddy_section()
                return base.section(self, label)

            def item(self, *args, **kwargs):
                self._add_buddy_section()
                return base.item(self, *args, **kwargs)

            def toggle(self, *args, **kwargs):
                self._add_buddy_section()
                return base.toggle(self, *args, **kwargs)

            def popup(self, x, y):
                self._add_buddy_section()
                return base.popup(self, x, y)

        proxy = types.SimpleNamespace(**{name: getattr(fancy_menu_module, name)
                                         for name in dir(fancy_menu_module) if not name.startswith("__")})
        proxy.FancyMenu = BuddyMenu
        return original(pet, event, proxy)

    pet_module.DesktopPet._show_fancy_menu = show_fancy_menu_with_buddy
    pet_module.DesktopPet._buddy_menu_installed = True


# --------------------------------------------------------------------------- scrollable pet menus
def install_menu_scrolling():
    """Pips' pet menus are one fixed popup; on a short screen the bottom was cut off. Make every
    FancyMenu (the pet menu and its submenus) fit the screen and scroll with the mouse wheel."""
    import fancy_menu

    menu_class = fancy_menu.FancyMenu
    if getattr(menu_class, "_buddy_scrolling", False):
        return
    original_create = menu_class._create_window
    original_layout = menu_class._layout
    original_draw = menu_class._draw
    original_open_child = menu_class._open_child
    original_key_move = menu_class._key_move
    margin = 12
    wheel_step = 3 * fancy_menu.ROW_H

    def offset(menu):
        return int(menu.cv.canvasy(0)) if getattr(menu, "_scrollable", False) and menu.cv else 0

    def create_window(menu):
        original_create(menu)
        menu._scrollable = False
        menu.win.bind("<MouseWheel>", lambda event: on_wheel(menu, event))
        menu.cv.bind("<MouseWheel>", lambda event: on_wheel(menu, event))

    def layout(menu):
        original_layout(menu)                         # height = full content height
        menu.content_h = menu.height
        try:
            pointer_x, pointer_y = menu.master.winfo_pointerxy()
            _left, top, _right, bottom = fancy_menu.work_area(menu.master, pointer_x, pointer_y)
        except Exception:
            top, bottom = 0, menu.master.winfo_screenheight()
        room = max(160, bottom - top - 2 * margin)
        menu._scrollable = menu.content_h > room
        if menu._scrollable:
            menu.height = room                        # the window is only as tall as the screen allows
            menu.cv.configure(height=room, scrollregion=(0, 0, menu.width, menu.content_h),
                              yscrollincrement=1)

    def draw(menu):
        if not getattr(menu, "_scrollable", False):
            original_draw(menu)
            return
        view_h = menu.height
        menu.height = menu.content_h                  # paint the whole menu; the canvas shows a window of it
        try:
            original_draw(menu)
        finally:
            menu.height = view_h
        if menu.cv is None or menu.closed:
            return
        top = offset(menu)
        track = view_h - 24
        thumb = max(28, int(track * view_h / menu.content_h))
        position = top / max(1, menu.content_h - view_h)
        y0 = top + 12 + int((track - thumb) * position)
        x = menu.width - 6
        menu.cv.create_line(x, y0, x, y0 + thumb, width=4, capstyle="round", fill=menu.p.get("muted", "#999999"))
        if top + view_h < menu.content_h - 2:         # "more below" hint on its own strip
            strip_top = top + view_h - 20
            menu.cv.create_rectangle(8, strip_top, menu.width - 14, top + view_h - 3,
                                     fill=menu.p.get("bg", "#ffffff"), outline="")
            menu.cv.create_text(menu.width // 2, strip_top + 9, text="\u25BE  scroll for more",
                                fill=menu.p.get("muted", "#999999"), font=menu.fonts.get("small"))

    def scroll_to(menu, y):
        if not getattr(menu, "_scrollable", False):
            return
        y = min(max(0, y), menu.content_h - menu.height)
        menu.cv.yview_moveto(y / menu.content_h)
        menu._draw()

    def on_wheel(menu, event):
        # the wheel goes to the focused menu; scroll whichever menu of the chain is under the pointer
        target = menu
        for candidate in menu.root_menu.chain():
            try:
                px, py = candidate.master.winfo_pointerxy()
                wx, wy = candidate.win.winfo_rootx(), candidate.win.winfo_rooty()
            except Exception:
                continue
            if wx <= px < wx + candidate.width and wy <= py < wy + candidate.height:
                target = candidate
        if getattr(target, "_scrollable", False):
            steps = -1 if event.delta > 0 else 1
            scroll_to(target, offset(target) + steps * wheel_step)
            target._set_hover(None)
        return "break"

    def shifted(handler):
        def wrapper(menu, event):
            if getattr(menu, "_scrollable", False):
                event = types.SimpleNamespace(x=event.x, y=event.y + offset(menu),
                                              x_root=event.x_root, y_root=event.y_root)
            return handler(menu, event)
        return wrapper

    def open_child(menu, index):
        shift = offset(menu)                          # submenus open next to the row where it is now
        menu.y -= shift
        try:
            return original_open_child(menu, index)
        finally:
            menu.y += shift

    def key_move(menu, step):
        result = original_key_move(menu, step)
        deepest = menu._deepest()
        if getattr(deepest, "_scrollable", False) and deepest.hover is not None:
            entry = deepest.entries[deepest.hover[0]]
            top = offset(deepest)
            if entry["y0"] < top + 8:
                scroll_to(deepest, entry["y0"] - 8)
            elif entry["y1"] > top + deepest.height - 24:
                scroll_to(deepest, entry["y1"] - deepest.height + 24)
        return result

    menu_class._create_window = create_window
    menu_class._layout = layout
    menu_class._draw = draw
    menu_class._on_motion = shifted(menu_class._on_motion)
    menu_class._on_press = shifted(menu_class._on_press)
    menu_class._on_release = shifted(menu_class._on_release)
    menu_class._open_child = open_child
    menu_class._key_move = key_move
    menu_class._buddy_scrolling = True


# --------------------------------------------------------------------------- the app
TRAY_MENU = ("summon", "ask", "sounds", None, "pet", "focus", None, "update", "quit")   # "summon" = left click


class BuddyApp:
    """The boss: one process, one tray icon, one main window. Pips, KeyIt and the AI are its features."""

    def __init__(self, cfg, con, no_track=False, instance=None, selftest=False):
        self.instance = instance
        self.selftest = selftest
        self.tabs = None
        self.focus = None
        self.tray = None
        self._quitting = False

        self.quit_event = None
        self.ask_event = None
        self.update_event = None
        self._update_state = "idle"          # idle / checking / preparing / ready / installing
        self._update_ready = None            # (manifest, folder) once a verified update waits to be installed
        self._update_manual = False
        if instance is not None:
            try:
                import win32event

                self.quit_event = win32event.CreateEvent(None, True, False, QUIT_EVENT_NAME)
                self.ask_event = win32event.CreateEvent(None, False, False, ASK_EVENT_NAME)   # auto-reset
                self.update_event = win32event.CreateEvent(None, False, False, UPDATE_EVENT_NAME)
            except Exception:
                logger.warning("Quit event unavailable", exc_info=True)

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(BUDDY_NAME)
        if ICON_PATH.exists():
            self.root.iconbitmap(default=str(ICON_PATH))

        # Feature: Focus (Pips pet + tracker)
        self.focus = FocusFeature(self, cfg, con, no_track=no_track)
        self.focus.build()
        self.focus._tray = _BuddyTrayForPips(self)

        # Feature: keyboard sounds + AI helper; KeyIt's window becomes Buddy's main window
        self.host = BuddyHost(self)
        keyit_core.ensure_user_dirs()
        self.settings = keyit_core.load_settings()
        self._make_it_all_buddy()
        self.player = keyit_core.Player(self.settings)
        self.hook = keyit_core.KeyboardHook(self.player)
        self.hook.start()
        self.window = keyit_gui.KeyItApp(self.player, self.hook, start_hidden=True, host=self.host)

        install_pet_menu(self)
        install_menu_scrolling()
        self.host.refresh_context()
        if not selftest:
            self.root.after(800, self._start_tray)
            if instance is not None:
                self.root.after(1000, self._poll_instance)
            self.root.after(1500, self.tabs.check_ai)
            self.root.after(4000, self._first_run)
            self.root.after(6000, self._announce_update_result)
            self.root.after(UPDATE_FIRST_CHECK_MS, self._update_tick)
        self.root.after(30_000, self._periodic_refresh)
        logger.info("Buddy started: focus=%s, sounds=%s, AI=%s", self.focus.pet is not None,
                    self.settings["enabled"], self.settings.get("buddy_enabled"))

    # ------------------------------------------------------------------ helpers for the UI
    def post(self, function, *args):
        self.window.post(function, *args)

    def refresh(self):
        if self.tabs is not None and not self._quitting:
            try:
                self.tabs.refresh()
            except Exception:
                logger.exception("Home refresh failed")

    def _periodic_refresh(self):
        if self._quitting:
            return
        self.refresh()
        self.root.after(30_000, self._periodic_refresh)

    def focus_numbers(self):
        scoring = getattr(pips_app, "scoring", None)
        if scoring is None or self.focus is None or self.focus.con is None:
            return None
        data = scoring.compute_daily(self.focus.con, datetime.now().strftime("%Y-%m-%d"), self.focus.cfg)
        return {
            "score": float(data.get("score", 0) or 0),
            "stars": int(data.get("stars", 0) or 0),
            "productive_min": int(data.get("productive_seconds", 0) or 0) // 60,
            "pomodoros": int(data.get("pomodoros_done", 0) or 0),
        }

    def focus_summary_text(self):
        try:
            return pips_focus_summary(self.focus)
        except Exception:
            logger.exception("Focus summary failed")
            return ""

    def sounds_enabled(self):
        return bool(self.settings["enabled"])

    def sound_pack_name(self):
        return self.player.pack.name if self.player.pack else "no pack"

    def toggle_sounds(self, *_args):
        self.hook.toggle()

    def sound_pack_choices(self):
        """(labels, current index) of the installed keyboard sound packs, e.g. Thock / Clicky."""
        window = getattr(self, "window", None)
        if window is None:                      # still starting up
            return [], -1
        labels = [label.replace("KeyIt Synth ", "").replace(" (synthetic)", "")
                  for label in window.pack_box["values"]]
        names = list(getattr(window, "pack_names", []))
        current = names.index(self.settings["pack"]) if self.settings["pack"] in names else -1
        return labels, current

    def select_sound_pack(self, index):
        names = list(getattr(self.window, "pack_names", []))
        if 0 <= index < len(names):
            self.window.select_pack(names[index])

    def test_sound(self):
        self.window.test_sound()

    def pet_state(self):
        pet = self.focus.pet if self.focus else None
        if pet is None:
            return BUDDY_NAME, False
        return pet.s.get("name") or BUDDY_NAME, bool(getattr(pet, "visible", False))

    def autostart_enabled(self):
        return autostart_enabled()

    def set_autostart(self, enabled):
        set_autostart(enabled)
        self.refresh()

    def pet_hotkey(self):
        """Ctrl+Alt+P, or the fallback if another program (e.g. a standalone Pips) already has it."""
        return (self.tray.hotkey_label if self.tray is not None else "") or "Ctrl+Alt+P"

    # ------------------------------------------------------------------ actions
    def open_home(self, tab="home"):
        index = {"home": 0, "focus": 1, "settings": 2}.get(tab, 0)
        self.refresh()
        self.window.show()
        self.window.notebook.select(index)

    def open_ask(self):
        """Tray / pet menu 'Ask Buddy': the little ask box at the mouse (or Home if the brain is off)."""
        buddy = getattr(self.window, "buddy", None)
        if buddy is not None:
            self.root.after(150, buddy.open_ask_box)     # let the menu close first
            return
        self.open_home("home")
        if self.tabs is not None:
            self.tabs.focus_ask_box()

    def ask_buddy(self, question):
        """Typed question from the Home tab: Buddy hides its window, looks at the screen and answers."""
        self.window.question_var.set(question)
        self.window.ask_typed()

    def open_focus_card(self):
        self.focus._show_card(near_pet=True)

    def open_dashboard(self):
        self.focus._open_dashboard()

    def open_history(self):
        self.focus._open_history()

    def summon_pet(self, source="buddy"):
        self.focus._summon_pet(source=source)
        self.root.after(300, self.refresh)

    def open_pet_settings(self):
        if self.focus.pet is not None:
            self.focus.pet.open_settings()

    # ------------------------------------------------------------------ tray + single instance
    def _start_tray(self):
        tray_module = getattr(pips_app, "tray", None)
        if tray_module is None or os.name != "nt":
            return
        try:
            tray_module.MENU = TRAY_MENU
            self.tray = tray_module.TrayIcon(ICON_PATH if ICON_PATH.exists() else None, BUDDY_NAME, self._tray_labels)
            if not self.tray.start():
                self.tray = None
                return
            key = self.tray.hotkey_label
            self.tray.set_tooltip(f"{BUDDY_NAME} - click to open" + (f"  \u00b7  {key} finds the pet" if key else ""))
            if self.focus.pet is not None:
                self.focus.pet.summon_hint = key
            self.root.after(150, self._poll_tray)
        except Exception:
            logger.exception("Tray icon unavailable")
            self.tray = None

    def _tray_labels(self):
        """Called on the tray thread: only read plain data here."""
        sound = "Mute keyboard sounds" if self.settings.get("enabled") else "Turn keyboard sounds on"
        return {
            "summon": "Open Buddy",
            "ask": "Ask Buddy   (tap Ctrl+Alt+Space, hold to talk)",
            "sounds": f"{sound}   (Ctrl+Alt+M)",
            "pet": f"Where are you, Buddy?   ({self.pet_hotkey()})",
            "focus": "Focus card",
            "update": f"Check for updates   (v{BUDDY_VERSION})",
            "quit": "Quit Buddy",
        }

    def _poll_tray(self):
        if self.tray is None or self._quitting:
            return
        for command in self.tray.drain():
            try:
                self._tray_command(command)
            except Exception:
                logger.exception("Tray command %s failed", command)
        if not self._quitting:
            self.root.after(150, self._poll_tray)

    def _tray_command(self, command):
        actions = {
            "summon": self.open_home,
            "ask": self.open_ask,
            "sounds": self.toggle_sounds,
            "pet": self.summon_pet,
            "hotkey": lambda: self.summon_pet(source="hotkey"),
            "focus": self.open_focus_card,
            "update": lambda: self.check_for_updates(manual=True),
            "quit": self.quit,
        }
        action = actions.get(command)
        if action is not None:
            action()

    def _poll_instance(self):
        if self._quitting:
            return
        try:
            if self.quit_event is not None:
                import win32event

                if win32event.WaitForSingleObject(self.quit_event, 0) == win32event.WAIT_OBJECT_0:
                    logger.info("Quit requested from outside (--quit)")
                    self.quit()
                    return
            if self.ask_event is not None and \
                    win32event.WaitForSingleObject(self.ask_event, 0) == win32event.WAIT_OBJECT_0:
                self._ask_from_outside()
            if self.update_event is not None and \
                    win32event.WaitForSingleObject(self.update_event, 0) == win32event.WAIT_OBJECT_0:
                logger.info("Update requested from outside (--update)")
                self.check_for_updates(manual=True)
            if self.instance.show_requested():          # Buddy was started again: show it
                self.open_home()
        except Exception:
            logger.exception("Show request failed")
        self.root.after(700, self._poll_instance)

    # ------------------------------------------------------------------ automatic updates
    def tell(self, text, seconds=8):
        """A short status line in the island at the top of the screen and in the pet's speech bubble."""
        logger.info("Buddy says: %s", text)
        buddy = getattr(self.window, "buddy", None)
        if buddy is not None:
            try:
                buddy.overlay.say(text)
                buddy.overlay.hide_later(int(seconds * 1000))
            except Exception:
                logger.debug("Island message failed", exc_info=True)
        pet = self.focus.pet if self.focus else None
        if pet is not None:
            try:
                pet.say(text, duration=seconds, priority=2)
            except Exception:
                logger.debug("Pet message failed", exc_info=True)

    def check_for_updates(self, manual=False):
        """Tray 'Check for updates' / Buddy.exe --update: check now and install as soon as it is ready."""
        if self._update_ready is not None:
            self._install_update()
            return
        self._update_manual = manual or self._update_manual
        self._update_tick(scheduled=False)

    def _update_tick(self, scheduled=True):
        if self._quitting:
            return
        if scheduled:
            self.root.after(UPDATE_EVERY_MS, self._update_tick)
        install = buddy_updater.install_dir()
        if install is None:
            if self._update_manual:
                self.tell("This Buddy runs from its source code - update it with git pull.")
                self._update_manual = False
            return
        if not self.settings.get("auto_update", True) and not self._update_manual:
            return
        if self._update_state != "idle":
            return
        self._update_state = "checking"
        if self._update_manual:
            self.tell("Checking for a new Buddy\u2026", seconds=4)
        threading.Thread(target=self._update_worker, args=(install,), name="buddy-update", daemon=True).start()

    def _update_worker(self, install):
        try:
            manifest = buddy_updater.check(BUDDY_VERSION)
            if manifest is None:
                self.post(self._update_finished, None, None, None)
                return
            self.post(self._update_progress, f"Buddy {manifest['version']} is out - getting it ready in the background\u2026")

            shown = {"step": -1}

            def progress(stage, done, total):
                if stage == "download" and total:
                    step = int(done * 100 / total) // 25          # 0, 25, 50, 75, 100 %: a few updates, no spam
                    if step != shown["step"]:
                        shown["step"] = step
                        self.post(self._update_progress,
                                  f"Downloading Buddy {manifest['version']}\u2026 {min(step * 25, 100)}%")
                elif stage == "selftest":
                    self.post(self._update_progress, f"Testing Buddy {manifest['version']} before installing it\u2026")

            folder = buddy_updater.prepare(manifest, install, progress=progress)
            self.post(self._update_finished, manifest, folder, None)
        except Exception as error:
            logger.warning("Update failed: %s", error, exc_info=True)
            self.post(self._update_finished, None, None, str(error))

    def _update_progress(self, text):
        self._update_state = "preparing"
        self.tell(text, seconds=6)

    def _update_finished(self, manifest, folder, error):
        manual, self._update_manual = self._update_manual, False
        self._update_state = "idle"
        if error:
            if manual:
                self.tell(f"I couldn't update just now ({error}). I'll try again later.", seconds=10)
            return
        if manifest is None:
            if manual:
                self.tell(f"You have the newest Buddy (v{BUDDY_VERSION}).", seconds=6)
            return
        self._update_ready = (manifest, folder)
        self._update_state = "ready"
        if manual:
            self._install_update()
        else:
            self.tell(f"Buddy {manifest['version']} is ready - I'll restart into it when you take a short break.",
                      seconds=10)
            self.root.after(UPDATE_IDLE_POLL_MS, self._install_when_idle)

    def _install_when_idle(self):
        if self._quitting or self._update_ready is None or self._update_state != "ready":
            return
        buddy = getattr(self.window, "buddy", None)
        busy = buddy is not None and buddy.busy.locked()
        if not busy and buddy_updater.idle_seconds() >= UPDATE_IDLE_S:
            self._install_update()
        else:
            self.root.after(UPDATE_IDLE_POLL_MS, self._install_when_idle)

    def _install_update(self):
        if self._update_ready is None or self._update_state == "installing":
            return
        manifest, folder = self._update_ready
        version = manifest["version"]
        self._update_state = "installing"
        self.settings["update_pending"] = version
        self.settings["update_from"] = BUDDY_VERSION
        self.settings["update_notes"] = list(manifest.get("notes") or [])[:4]
        keyit_core.save_settings(self.settings)
        self.tell(f"Updating to Buddy {version} - back in a few seconds!", seconds=6)

        def go():
            try:
                buddy_updater.apply(buddy_updater.install_dir(), folder, version)
            except Exception:
                logger.exception("Could not start the update helper")
                self._update_state = "idle"
                self.settings.pop("update_pending", None)
                keyit_core.save_settings(self.settings)
                self.tell("The update couldn't start - I'll keep running this version.", seconds=8)
                return
            self.quit()

        self.root.after(2500, go)

    def _announce_update_result(self):
        """First start after an update: say what happened (and what's new)."""
        install = buddy_updater.install_dir()
        if install is not None:                       # half-finished downloads from a crash or an old run
            threading.Thread(target=buddy_updater.cleanup, args=(install,), daemon=True).start()
        pending = self.settings.pop("update_pending", None)
        if not pending:
            return
        previous = self.settings.pop("update_from", "?")
        notes = self.settings.pop("update_notes", []) or []
        keyit_core.save_settings(self.settings)
        if buddy_updater.parse_version(pending) == buddy_updater.parse_version(BUDDY_VERSION):
            extra = (" New: " + "; ".join(notes[:2])) if notes else ""
            self.tell(f"Updated to Buddy {BUDDY_VERSION} \u2713 (was {previous}).{extra}", seconds=12)
            if install is not None:                   # still fine after 10 min: the old copy isn't needed
                self.root.after(PREVIOUS_KEEP_MS, lambda: threading.Thread(
                    target=buddy_updater.drop_previous, args=(install,), daemon=True).start())
        else:
            self.tell(f"The update to {pending} didn't work, so I'm still on {BUDDY_VERSION}. "
                      "I'll try again later.", seconds=12)

    def _make_it_all_buddy(self):
        """One-time: the AI, the pet and the app are all Buddy (brain on, pet called Buddy unless renamed)."""
        if self.settings.get("buddy_unified"):
            return
        self.settings["buddy_unified"] = True
        self.settings["buddy_enabled"] = True                  # Buddy's brain is part of Buddy, not an extra
        pet = self.focus.pet if self.focus else None
        if pet is not None and (pet.s.get("name") or "Pip") == "Pip":
            pet.s["name"] = BUDDY_NAME
            try:
                self.focus._save_pet_settings(pet.s)
            except Exception:
                logger.exception("Could not save the pet's new name")
        keyit_core.save_settings(self.settings)

    def _first_run(self):
        if self.settings.get("buddy_hello_v2"):
            return
        self.settings["buddy_hello_v2"] = True
        keyit_core.save_settings(self.settings)
        self.open_home()
        pet = self.focus.pet
        if pet is not None:
            pet.say("Hi, I'm Buddy! Ask me anything - tap Ctrl+Alt+Space to type, or hold it and talk. "
                    "I also keep your focus score and click your keys.", duration=14, priority=2)

    def _ask_from_outside(self):
        """Buddy.exe --ask "...": same as typing it into the Ctrl+Alt+Space box."""
        try:
            path = _ask_file()
            question = path.read_text(encoding="utf-8").strip()
            path.unlink(missing_ok=True)
        except OSError:
            return
        buddy = getattr(self.window, "buddy", None)
        if not question or buddy is None:
            logger.info("Ignored --ask (%s)", "empty" if not question else "AI is off")
            return
        logger.info("Question from --ask: %s", question)
        buddy.ask_text(question)

    # ------------------------------------------------------------------ quit
    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        logger.info("Buddy quitting")
        steps = [
            lambda: self.tray and self.tray.stop(),
            lambda: self.window.buddy and self.window.buddy.cancel(),
            self.hook.stop,
            lambda: self.focus and self.focus.shutdown(),
            self.root.destroy,
        ]
        for step in steps:
            try:
                step()
            except Exception:
                logger.exception("Shutdown step failed")

    def run(self):
        self.root.mainloop()


# --------------------------------------------------------------------------- start-up
def _prepare_pips(cfg):
    """What Pips' own main() does before it builds its window - minus its language picker."""
    pips_app.import_modules()
    try:
        import theme as theme_mod

        theme_mod.set_theme((cfg.get("ui") or {}).get("theme", "light"))
        theme_mod.apply_to_app(pips_app.THEME, pips_app.PILL_COLORS)
    except Exception:
        logger.warning("Theme not applied", exc_info=True)
    i18n = getattr(pips_app, "i18n", None)
    if i18n is not None:
        i18n.set_language(cfg.get("language") or "en")
    rebrand_pips_texts()
    con = pips_app.get_db_connection()
    pips_app.ensure_db_schema(con)
    tracker = getattr(pips_app, "tracker", None)
    if tracker is not None and hasattr(tracker, "ensure_db"):
        tracker.ensure_db(con)
    return con


QUIT_EVENT_NAME = "Local\\Buddy.App.QuitRequest"
ASK_EVENT_NAME = "Local\\Buddy.App.AskRequest"
UPDATE_EVENT_NAME = "Local\\Buddy.App.UpdateRequest"


def _ask_file():
    return keyit_core.USER_DIR / "ask_request.txt"


def request_ask(question):
    """Hand a typed question to the running Buddy (Buddy.exe --ask "open notepad"), like the Ctrl+Alt+Space box."""
    try:
        import win32event

        event = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, ASK_EVENT_NAME)
    except Exception:
        logger.info("Buddy isn't running - start it first")
        return 1
    keyit_core.ensure_user_dirs()
    _ask_file().write_text(question, encoding="utf-8")
    win32event.SetEvent(event)
    logger.info("Asked Buddy: %s", question)
    return 0


def request_quit():
    """Signal a running Buddy to quit (used by build.bat and by you: Buddy.exe --quit)."""
    try:
        import win32event

        event = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, QUIT_EVENT_NAME)
    except Exception:
        logger.info("No running Buddy to quit")
        return 0
    win32event.SetEvent(event)
    logger.info("Asked the running Buddy to quit")
    return 0


def request_update():
    """Buddy.exe --update: the running Buddy checks for a new version now and installs it right away."""
    try:
        import win32event

        event = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, UPDATE_EVENT_NAME)
    except Exception:
        logger.info("Buddy isn't running - start it first")
        return 1
    win32event.SetEvent(event)
    logger.info("Asked the running Buddy to update")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="buddy", description="Buddy: focus pet, keyboard sounds and AI helper")
    parser.add_argument("--selftest", action="store_true", help="start everything offscreen, check, exit 0")
    parser.add_argument("--no-track", action="store_true", help="don't track app usage")
    parser.add_argument("--quit", action="store_true", help="ask a running Buddy to quit cleanly, then exit")
    parser.add_argument("--ask", metavar="TEXT", help='give the running Buddy a typed question, e.g. --ask "open notepad"')
    parser.add_argument("--update", action="store_true", help="ask the running Buddy to update itself now")
    args = parser.parse_args(argv)
    if args.quit:
        return request_quit()
    if args.ask:
        return request_ask(args.ask)
    if args.update:
        return request_update()

    pips_app.SingleInstance.MUTEX = "Local\\Buddy.App.Instance"
    pips_app.SingleInstance.EVENT = "Local\\Buddy.App.ShowRequest"
    instance = None
    if not args.selftest:
        instance = pips_app.SingleInstance()
        if not instance.acquire():
            logger.info("Buddy is already running - asked it to show itself")
            return 0

    cfg = pips_app.load_config()
    try:
        con = _prepare_pips(cfg)
    except Exception:
        logger.exception("Focus data could not be opened")
        return 1

    app = BuddyApp(cfg, con, no_track=args.no_track, instance=instance, selftest=args.selftest)
    if args.selftest:
        logger.info("=== BUDDY SELF-TEST ===")
        try:
            app.focus._update_card()
            app.focus._update_dashboard()
            app.focus._update_history()
            app.refresh()
            app.window.notebook.select(0)
            tabs = [app.window.notebook.tab(tab_id, "text").strip() for tab_id in app.window.notebook.tabs()]
            numbers = app.focus_numbers()
            logger.info("Buddy owns root: %s; focus window parent: %s; tabs: %s",
                        app.root.winfo_class(), app.focus.root.winfo_parent() or "?", tabs)
            logger.info("Today's focus: %s", numbers)
            assert app.focus.root.master is app.root, "Pips must live inside Buddy's root"
            assert tabs == ["Home", "Focus", "Settings", "Voice input"], tabs
            assert app.pet_state()[0] != "Pip", "the pet should be Buddy"
            uia, automation = keyit_buddy._uia()               # selection reading for the ask box
            assert automation.GetRootElement(), "UI Automation unavailable"
            logger.info("UI Automation ready for highlighted-text questions")
            import keyit_voice                                  # natural voices: libraries are bundled
            import kokoro_onnx  # noqa: F401
            import sounddevice  # noqa: F401

            if keyit_voice.is_downloaded():
                voice = keyit_voice.NaturalVoice("af_heart")
                assert voice.ready.wait(60) and not voice.failed, f"natural voice: {voice.failed}"
                samples, rate = voice.kokoro.create("Hi.", voice="af_heart", speed=1.0, lang="en-us")
                assert len(samples) > rate // 10, "natural voice made no audio"
                logger.info("Natural voice works (%.1fs of audio for 'Hi.')", len(samples) / rate)
                for text in ("你好，我是Buddy。", "Hola, soy Buddy."):           # Chinese (misaki) and espeak languages
                    voice.set_voice("zf_xiaobei" if "你" in text else "ef_dora")
                    samples, rate = voice._speak(text)
                    assert len(samples) > rate // 2, f"natural voice made no audio for {text!r}"
                    logger.info("Natural voice works for %a (%.1fs)", text, len(samples) / rate)
            else:
                logger.info("Natural voice libraries load; voice files not downloaded yet")
        except Exception:
            logger.exception("Self-test failed")
            app.quit()
            return 1
        app.quit()
        logger.info("=== BUDDY SELF-TEST PASSED ===")
        return 0
    app.run()
    return 0
