"""
Buddy = Pips (desktop pet + focus tracker) + KeyIt (keyboard sounds) + Buddy AI (local screen helper).

Everything runs in ONE process around Pips' own Tk main loop. The vendored Pips source in
pips/ stays byte-identical: Buddy plugs in from the outside by

  * subclassing Pips' ProductivityWidget (attach KeyIt + AI after Pips has built its UI),
  * giving Buddy its own single-instance names (so it never just wakes a standalone Pips),
  * turning off Pips' self-updater (a Pips release would overwrite the merged app),
  * adding a "Buddy" section to the pet's right-click menu,
  * letting the pet BE the AI: answers appear in its speech bubble, and a beam goes from
    the pet to each button the AI points at.
"""
import logging
import math
import os
import sys
import time
import types
from datetime import datetime
from pathlib import Path

BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PIPS_DIR = BUNDLE_DIR / "pips"
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


# --------------------------------------------------------------------------- host adapter
class BuddyHost:
    """What KeyIt's settings window needs from its host (Pips)."""

    window_title = BUDDY_NAME
    tagline = f"v{BUDDY_VERSION}  ·  pet · keyboard sounds · AI helper"

    def __init__(self, widget):
        self.widget = widget
        self.root = widget.root
        self._context = ""

    def make_overlay(self):
        return PetGuideOverlay(self.root, lambda: self.widget.pet)

    def buddy_context(self):
        return self._context                       # cached; refreshed on the Tk thread (sqlite!)

    def refresh_context(self):
        try:
            self._context = pips_focus_summary(self.widget)
        except Exception:
            logger.exception("Could not read Pips focus data")
        self.root.after(CONTEXT_REFRESH_MS, self.refresh_context)


class BuddySuite:
    """KeyIt sounds + Buddy AI attached to a running Pips widget."""

    def __init__(self, widget):
        self.widget = widget
        self.host = BuddyHost(widget)
        keyit_core.ensure_user_dirs()
        settings = keyit_core.load_settings()
        self.player = keyit_core.Player(settings)
        self.hook = keyit_core.KeyboardHook(self.player)
        self.hook.start()
        self.window = keyit_gui.KeyItApp(self.player, self.hook, start_hidden=True, host=self.host)
        install_pet_menu(self)
        self.host.refresh_context()
        widget.root.after(6000, self._greet_once)
        logger.info("Buddy attached: sounds=%s, AI enabled=%s", settings["enabled"], settings.get("buddy_enabled"))

    # -- actions used by the pet menu
    def open_settings(self, tab=0):
        self.window.show()
        self.window.notebook.select(tab)

    def open_ask(self):
        self.open_settings(tab=1)

    def sounds_enabled(self):
        return bool(self.player.settings["enabled"])

    def toggle_sounds(self, *_args):
        self.hook.toggle()

    def _greet_once(self):
        settings = self.player.settings
        if settings.get("buddy_greeted"):
            return
        pet = self.widget.pet
        if pet is not None and pet.say(
                "I'm Buddy now! \u2328 Keyboard sounds are on, and with Ollama I can help with "
                "anything on your screen - right-click me \u2192 Buddy.", duration=12, priority=2):
            settings["buddy_greeted"] = True
            keyit_core.save_settings(settings)

    def shutdown(self):
        try:
            if getattr(self.window, "buddy", None):
                self.window.buddy.cancel()
        finally:
            self.hook.stop()


def install_pet_menu(suite):
    """Add a 'Buddy' section to the pet's fancy right-click menu (without editing pet.py)."""
    pet_module = getattr(pips_app, "pet_mod", None)
    if pet_module is None or getattr(pet_module.DesktopPet, "_buddy_menu_installed", False):
        return
    original = pet_module.DesktopPet._show_fancy_menu

    def show_fancy_menu_with_buddy(pet, event, fancy_menu_module):
        base = fancy_menu_module.FancyMenu
        more_label = pet_module.t("fm_more")

        class BuddyMenu(base):
            _buddy_added = False

            def _add_buddy_section(self):
                if self._buddy_added:
                    return
                self._buddy_added = True
                base.section(self, BUDDY_NAME)
                base.item(self, "\U0001F399", "Ask Buddy about my screen", suite.open_ask,
                          hint="or hold Ctrl+Alt+Space")
                base.toggle(self, "\u2328", "Keyboard sounds", suite.sounds_enabled(), suite.toggle_sounds)
                base.item(self, "\u2699", "Sounds & AI settings", suite.open_settings)

            def section(self, label):
                if label == more_label:
                    self._add_buddy_section()
                return base.section(self, label)

            def popup(self, x, y):
                self._add_buddy_section()
                return base.popup(self, x, y)

        proxy = types.SimpleNamespace(**{name: getattr(fancy_menu_module, name)
                                         for name in dir(fancy_menu_module) if not name.startswith("__")})
        proxy.FancyMenu = BuddyMenu
        return original(pet, event, proxy)

    pet_module.DesktopPet._show_fancy_menu = show_fancy_menu_with_buddy
    pet_module.DesktopPet._buddy_menu_installed = True


# --------------------------------------------------------------------------- Pips host
class BuddyWidget(pips_app.ProductivityWidget):
    """Pips' main widget with Buddy attached. Pips keeps working even if a Buddy part fails."""

    def build(self):
        super().build()
        self.root.title(BUDDY_NAME)
        self.buddy_suite = None
        try:
            self.buddy_suite = BuddySuite(self)
        except Exception:
            logger.exception("Buddy add-ons failed to start; Pips continues without them")

    # Pips is shipped inside Buddy now: its own updater must never replace the merged files
    def _auto_update_tick(self):
        return None

    def _update_watchdog(self):
        return None

    def _announce_installed_update(self):
        return None

    def _check_updates(self, manual: bool = False):
        if manual and self.pet is not None:
            self.pet.say("Pips is part of Buddy now - updates come with Buddy.", duration=6, priority=2)
        return False

    def _on_close(self):
        suite = getattr(self, "buddy_suite", None)
        if suite is not None:
            try:
                suite.shutdown()
            except Exception:
                logger.exception("Buddy shutdown failed")
        super()._on_close()


def main(argv=None):
    pips_app.SingleInstance.MUTEX = "Local\\Buddy.DesktopPet.Instance"
    pips_app.SingleInstance.EVENT = "Local\\Buddy.DesktopPet.ShowRequest"
    pips_app.ProductivityWidget = BuddyWidget
    return pips_app.main(argv)
