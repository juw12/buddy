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

BUDDY_NAME = "Buddy"
BUDDY_VERSION = "0.1.0"
CONTEXT_REFRESH_MS = 60_000
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
        f"Today so far: focus score {float(data.get('score', 0)):.0f}/100 ({data.get('stars', 0)} stars), "
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
    """Buddy's pointer, but the Pips pet does the talking and beams at the targets.

    Falls back to the plain orange pointer whenever the pet is hidden or busy.
    """

    BEAM_GROW_S = 0.35
    SAY_THROTTLE_S = 0.4

    def __init__(self, root, pet_getter):
        super().__init__(root)
        self.pet_getter = pet_getter
        self._last_said = ("", 0.0)

    # -- pet helpers
    def _pet(self):
        pet = self.pet_getter()
        if pet is None or not getattr(pet, "visible", False) or getattr(pet, "fs_hidden", False):
            return None
        if getattr(pet, "_destroyed", False):
            return None
        return pet

    def _pet_say(self, text, duration, force=False):
        pet = self._pet()
        if pet is None:
            return False
        now = time.monotonic()
        last_text, last_time = self._last_said
        if not force and (text == last_text or now - last_time < self.SAY_THROTTLE_S):
            return True
        self._last_said = (text, now)
        try:
            return bool(pet.say(text, duration=duration, priority=3))
        except Exception:
            logger.exception("pet.say failed")
            return False

    def _pet_anchor(self):
        pet = self._pet()
        if pet is None:
            return None
        x, y, width, height = pet.window_rect()
        return x + width / 2, y + height * 0.3

    # -- states (same interface as BuddyOverlay)
    def listening(self, monitor, cursor):
        self.monitor = monitor
        if self._pet_say("\U0001F399 Listening\u2026 let go of Ctrl+Alt+Space when you're done.", 60, force=True):
            self._cancel_jobs()
            self.canvas.delete("all")
            self._hide_window()
            return
        super().listening(monitor, cursor)

    def thinking(self, text="Thinking\u2026"):
        if self._pet_say(text.replace("\n", "  "), 90, force=True):
            self._cancel_jobs()
            return
        super().thinking(text)

    def say(self, text):
        if self._pet_say(text, 20):
            return
        super().say(text)

    def clear_for_screenshot(self):
        super().clear_for_screenshot()
        pet = self._pet()
        if pet is not None:
            try:
                pet.bubble.hide()          # keep "Listening..." out of the screenshot
            except Exception:
                pass

    def tour(self, screen_points, answer_text, dwell_ms=1800):
        anchor = self._pet_anchor()
        if anchor is None or self.monitor is None:
            super().tour(screen_points, answer_text, dwell_ms)
            return
        self._cancel_jobs()
        total_s = len(screen_points) * (self.BEAM_GROW_S + dwell_ms / 1000) + 6
        self._pet_say(answer_text, max(10, total_s), force=True)
        self.attach(self.monitor)
        start_x, start_y = self._clamp(*self.local(*anchor))
        targets = [(self.local(x, y), label) for x, y, label in screen_points]

        def beam(index):
            if index >= len(targets):
                self.hide_later(3500)
                return
            (tx, ty), label = targets[index]
            caption = f"{index + 1}. {label}" if len(targets) > 1 and label else label
            started = time.perf_counter()

            def step():
                t = min(1.0, (time.perf_counter() - started) / self.BEAM_GROW_S)
                eased = 1 - (1 - t) ** 3
                ex = start_x + (tx - start_x) * eased
                ey = start_y + (ty - start_y) * eased
                self._draw_beam(start_x, start_y, ex, ey, tx, ty, caption if t >= 1.0 else None, pulse=0.0)
                if t < 1.0:
                    self._animation = self.root.after(16, step)
                else:
                    pulse_until = time.perf_counter() + dwell_ms / 1000

                    def pulse():
                        remaining = pulse_until - time.perf_counter()
                        phase = (math.sin(time.perf_counter() * 7) + 1) / 2
                        self._draw_beam(start_x, start_y, tx, ty, tx, ty, caption, pulse=phase)
                        if remaining > 0:
                            self._animation = self.root.after(33, pulse)
                        else:
                            beam(index + 1)

                    pulse()

            step()

        beam(0)

    # -- drawing
    def _clamp(self, x, y):
        width, height = self.monitor["width"], self.monitor["height"]
        return min(max(x, 4), width - 4), min(max(y, 4), height - 4)

    def _draw_beam(self, sx, sy, ex, ey, tx, ty, caption, pulse):
        canvas = self.canvas
        canvas.delete("all")
        canvas.create_line(sx, sy, ex, ey, fill=self.ORANGE, width=3, dash=(10, 6), capstyle="round")
        canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill=self.ORANGE, outline=self.INK, width=2)
        if (ex, ey) == (tx, ty):
            radius = 16 + 8 * pulse
            canvas.create_oval(tx - radius, ty - radius, tx + radius, ty + radius,
                               outline=self.ORANGE, width=3)
            canvas.create_oval(tx - 5, ty - 5, tx + 5, ty + 5, fill=self.ORANGE, outline=self.INK, width=2)
            if caption:
                self._bubble(tx + 14, ty + 12, caption)


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


# --------------------------------------------------------------------------- host adapter for KeyIt's window
class BuddyHost:
    """What KeyIt's settings window needs from Buddy: it becomes Buddy's main window."""

    window_title = BUDDY_NAME
    tagline = f"v{BUDDY_VERSION}  \u00b7  focus \u00b7 keyboard sounds \u00b7 AI helper"
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
                    title, subtitle, avatar = BUDDY_NAME, f"v{BUDDY_VERSION} \u00b7 focus \u00b7 sounds \u00b7 AI", "B"
                return base.header(self, title, subtitle, avatar, **kwargs)

            def _add_buddy_section(self):
                if self._buddy_added:
                    return
                self._buddy_added = True
                base.section(self, BUDDY_NAME)
                base.item(self, "\U0001F3E0", "Open Buddy", app.open_home)
                base.item(self, "\U0001F399", "Ask about my screen", app.open_ask, hint="or hold Ctrl+Alt+Space")
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


# --------------------------------------------------------------------------- the app
TRAY_MENU = ("summon", "ask", "sounds", None, "pet", "focus", None, "quit")   # "summon" = left click = Open Buddy


class BuddyApp:
    """The boss: one process, one tray icon, one main window. Pips, KeyIt and the AI are its features."""

    def __init__(self, cfg, con, no_track=False, instance=None, selftest=False):
        self.instance = instance
        self.selftest = selftest
        self.tabs = None
        self.focus = None
        self.tray = None
        self._quitting = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(BUDDY_NAME)
        if ICON_PATH.exists():
            self.root.iconbitmap(default=str(ICON_PATH))

        # Feature: Focus (Pips pet + tracker)
        self.focus = FocusFeature(self, cfg, con, no_track=no_track)
        self.focus.build()

        # Feature: keyboard sounds + AI helper; KeyIt's window becomes Buddy's main window
        self.host = BuddyHost(self)
        keyit_core.ensure_user_dirs()
        self.settings = keyit_core.load_settings()
        self.player = keyit_core.Player(self.settings)
        self.hook = keyit_core.KeyboardHook(self.player)
        self.hook.start()
        self.window = keyit_gui.KeyItApp(self.player, self.hook, start_hidden=True, host=self.host)

        install_pet_menu(self)
        self.host.refresh_context()
        if not selftest:
            self.root.after(800, self._start_tray)
            if instance is not None:
                self.root.after(1000, self._poll_instance)
            self.root.after(1500, self.tabs.check_ai)
            self.root.after(4000, self._first_run)
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
        index = {"home": 0, "focus": 1, "sounds": 2, "ai": 3}.get(tab, 0)
        self.refresh()
        self.window.show()
        self.window.notebook.select(index)

    def open_ask(self):
        self.open_home("ai")

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
            self.focus._tray = None                    # Pips never sees a tray of its own
            self.root.after(150, self._poll_tray)
        except Exception:
            logger.exception("Tray icon unavailable")
            self.tray = None

    def _tray_labels(self):
        """Called on the tray thread: only read plain data here."""
        sound = "Mute keyboard sounds" if self.settings.get("enabled") else "Turn keyboard sounds on"
        return {
            "summon": "Open Buddy",
            "ask": "Ask about my screen   (Ctrl+Alt+Space)",
            "sounds": f"{sound}   (Ctrl+Alt+M)",
            "pet": f"Find my pet   ({self.pet_hotkey()})",
            "focus": "Focus card",
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
            "quit": self.quit,
        }
        action = actions.get(command)
        if action is not None:
            action()

    def _poll_instance(self):
        if self._quitting:
            return
        try:
            if self.instance.show_requested():          # Buddy was started again: show it
                self.open_home()
        except Exception:
            logger.exception("Show request failed")
        self.root.after(700, self._poll_instance)

    def _first_run(self):
        if self.settings.get("buddy_home_shown"):
            return
        self.settings["buddy_home_shown"] = True
        keyit_core.save_settings(self.settings)
        self.open_home()
        pet = self.focus.pet
        if pet is not None:
            pet.say("Buddy's in charge now! Click the orange Buddy icon by the clock for focus, "
                    "keyboard sounds and the AI helper.", duration=12, priority=2)

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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="buddy", description="Buddy: focus pet, keyboard sounds and AI helper")
    parser.add_argument("--selftest", action="store_true", help="start everything offscreen, check, exit 0")
    parser.add_argument("--no-track", action="store_true", help="don't track app usage")
    args = parser.parse_args(argv)

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
            assert tabs[:2] == ["Home", "Focus"], tabs
        except Exception:
            logger.exception("Self-test failed")
            app.quit()
            return 1
        app.quit()
        logger.info("=== BUDDY SELF-TEST PASSED ===")
        return 0
    app.run()
    return 0
