"""Buddy Lite for Mac: a menu-bar app with a buddy next to your mouse.

Hold Control+Option+Space and talk, or tap it to type. Buddy takes one look at the display under your mouse,
asks the brain, then points at what it means and reads the answer aloud.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvent,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSScreen,
    NSSecureTextField,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
    NSView,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)
from Foundation import NSMakeRect, NSObject
from PyObjCTools import AppHelper

from . import core, ocr, updater
from .hotkey import HotKey
from .island import Island, IslandMirror
from .overlay import Overlay
from .voice import Listener, Speaker

HOLD_SECONDS = 0.35          # held longer than this = talk, shorter = type
STEP_SECONDS = 2.4           # time on each point of a multi-step tour
ALERT_FIRST_BUTTON = 1000    # NSAlertFirstButtonReturn
UPDATE_FIRST_CHECK_S = 60    # first update check a minute after start, then every 3 hours
UPDATE_EVERY_S = 3 * 3600
UPDATE_IDLE_S = 90           # a ready update installs after 90 s without keyboard/mouse use

PRIVACY_SCREEN = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"


def _status_icon():
    """A small template arrow for the menu bar (macOS tints it for light/dark menu bars)."""
    image = NSImage.alloc().initWithSize_((18, 18))
    image.lockFocus()
    path = NSBezierPath.bezierPath()
    points = [(4, 16), (4, 3.5), (7.2, 6.4), (9.6, 1.2), (11.6, 2.2), (9.3, 7.2), (13.6, 7.2)]
    path.moveToPoint_(points[0])
    for point in points[1:]:
        path.lineToPoint_(point)
    path.closePath()
    NSColor.blackColor().setFill()
    path.fill()
    image.unlockFocus()
    image.setTemplate_(True)
    return image


def _screen_under_mouse():
    """(ScreenRect of the display under the mouse, height of the main display) in Cocoa coordinates."""
    mouse_x, mouse_y = NSEvent.mouseLocation()
    screens = NSScreen.screens()
    main_height = screens[0].frame().size.height
    for screen in screens:
        frame = screen.frame()
        rect = core.ScreenRect(frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)
        if rect.contains(mouse_x, mouse_y):
            return rect, main_height
    frame = screens[0].frame()
    return core.ScreenRect(frame.origin.x, frame.origin.y, frame.size.width, frame.size.height), main_height


class _Delegate(NSObject):
    """Receives menu, text-field and app callbacks and hands them to the Python controller."""

    controller = None

    def applicationDidFinishLaunching_(self, notification):
        self.controller.start()

    def askClicked_(self, sender):
        self.controller.open_ask_box()

    def askSubmitted_(self, sender):
        self.controller.ask_box_submitted(sender.stringValue())

    def brainClicked_(self, sender):
        self.controller.edit_brain()

    def feedbackClicked_(self, sender):
        subprocess.run(["open", core.FEEDBACK_URL], check=False)

    def speakClicked_(self, sender):
        self.controller.toggle("speak", sender)

    def followClicked_(self, sender):
        self.controller.toggle("follow_mouse", sender)

    def islandClicked_(self, sender):
        self.controller.toggle("island", sender)

    def colorClicked_(self, sender):
        self.controller.set_color(sender.representedObject())

    def screenAccessClicked_(self, sender):
        self.controller.open_screen_access()

    def logClicked_(self, sender):
        subprocess.run(["open", "-R", str(core.LOG_PATH)], check=False)

    def aboutClicked_(self, sender):
        self.controller.about()

    def updateClicked_(self, sender):
        self.controller.check_for_updates(manual=True)

    def quitClicked_(self, sender):
        NSApp.terminate_(None)


class Controller:
    def __init__(self, settings=None, persist=True):
        self.persist = persist
        self.first_run = not core.SETTINGS_PATH.exists()
        self.settings = settings or core.load_settings()
        self.history = []
        self.ask_token = 0
        self.press_token = 0
        self.pressed = False
        self.listening = False
        self.ask_panel = None
        self.island = None
        self.events = []                    # (what, detail) for the self-test report
        self.on_answered = None             # self-test hook
        self.update_ready = None            # new Buddy.app waiting to be installed
        self.update_busy = False

    # ------------------------------------------------------------------ startup
    def start(self):
        pointer = Overlay(core.COLORS[self.settings["color"]], self.settings["follow_mouse"])
        # The island in the notch shows the same states as the pointer (listening, thinking, the answer)
        self.overlay = IslandMirror(pointer, lambda: self.island)
        self._set_island(self.settings.get("island", True))
        self.speaker = Speaker()
        self.listener = Listener(self._heard_partial, self._heard, self._voice_error)
        self.hotkey = HotKey(self._key_down, self._key_up)
        self._build_menu()
        if self.persist:
            self._ask_screen_permission()
            self.listener.request_permission()
        if self.first_run and self.persist:
            core.save_settings(self.settings)
            self.overlay.say("Hi, I'm Buddy! Hold Control+Option+Space and ask me, or tap it to type.")
            AppHelper.callLater(7, self._home_if, self.ask_token)
        logging.info("Buddy Lite %s started (hotkey ok: %s, voice in: %s, voice out: %s)",
                     core.VERSION, self.hotkey.ok, self.listener.ok, self.speaker.ok)
        if self.persist and updater.app_bundle() is not None:
            AppHelper.callLater(UPDATE_FIRST_CHECK_S, self._update_tick)

    # ------------------------------------------------------------------ automatic updates
    def _update_tick(self):
        AppHelper.callLater(UPDATE_EVERY_S, self._update_tick)
        self.check_for_updates(manual=False)

    def check_for_updates(self, manual=False):
        if self.update_ready is not None:
            self._install_update()
            return
        if self.update_busy:
            return
        if updater.app_bundle() is None:
            if manual:
                self._say_briefly("This Buddy runs from source - update it with git pull.")
            return
        self.update_busy = True
        if manual:
            self._say_briefly("Checking for a new Buddy\u2026", 3)

        def work():
            try:
                manifest = updater.check(core.VERSION)
                if manifest is None:
                    AppHelper.callAfter(self._update_done, None, None, manual)
                    return
                AppHelper.callAfter(self._say_briefly, f"Buddy {manifest['version']} is out - getting it ready\u2026")
                AppHelper.callAfter(self._update_done, manifest, updater.prepare(manifest), manual)
            except Exception as error:                  # noqa: BLE001
                logging.warning("Update failed: %s", error)
                AppHelper.callAfter(self._update_done, None, str(error), manual)

        threading.Thread(target=work, daemon=True).start()

    def _update_done(self, manifest, result, manual):
        self.update_busy = False
        if manifest is None:
            if manual:
                self._say_briefly(f"Couldn't update: {result}" if result else
                                  f"You have the newest Buddy ({core.VERSION}).", 6)
            return
        self.update_ready = (manifest["version"], result)
        if manual:
            self._install_update()
        else:
            self._say_briefly(f"Buddy {manifest['version']} is ready - I'll restart into it when you take a break.", 8)
            AppHelper.callLater(15, self._install_when_idle)

    def _install_when_idle(self):
        if self.update_ready is None:
            return
        if updater.idle_seconds() >= UPDATE_IDLE_S and not self.pressed:
            self._install_update()
        else:
            AppHelper.callLater(15, self._install_when_idle)

    def _install_update(self):
        version, new_app = self.update_ready
        current = updater.app_bundle()
        self._say_briefly(f"Updating to Buddy {version} - back in a few seconds!", 4)
        try:
            updater.apply(current, new_app, core.LOG_PATH.with_name("update.log"))
        except Exception:                               # noqa: BLE001
            logging.exception("Could not start the update helper")
            return
        AppHelper.callLater(2.5, NSApp.terminate_, None)

    def _ask_screen_permission(self):
        try:
            import Quartz

            preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
            request = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
            if preflight and request and not preflight():
                request()
        except Exception:                                   # noqa: BLE001
            logging.exception("screen permission check failed")

    def _build_menu(self):
        delegate = NSApp.delegate()
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.status_item.button().setImage_(_status_icon())
        self.status_item.button().setToolTip_("Buddy")
        menu = NSMenu.alloc().init()

        def add(title, action, key=""):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            item.setTarget_(delegate)
            menu.addItem_(item)
            return item

        add("Ask Buddy…   ⌃⌥Space", "askClicked:")
        if not self.hotkey.ok:
            warning = add(f"Hotkey off: {self.hotkey.error}", None)
            warning.setEnabled_(False)
        menu.addItem_(NSMenuItem.separatorItem())
        add("Brain…", "brainClicked:")
        add("Send feedback…", "feedbackClicked:")
        speak = add("Read answers aloud", "speakClicked:")
        speak.setState_(1 if self.settings["speak"] else 0)
        follow = add("Keep Buddy next to my mouse", "followClicked:")
        follow.setState_(1 if self.settings["follow_mouse"] else 0)
        island = add("Buddy Island in the notch", "islandClicked:")
        island.setState_(1 if self.settings.get("island", True) else 0)

        colors = NSMenu.alloc().init()
        self.color_items = {}
        for name in core.COLORS:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(name, "colorClicked:", "")
            item.setTarget_(delegate)
            item.setRepresentedObject_(name)
            item.setState_(1 if name == self.settings["color"] else 0)
            colors.addItem_(item)
            self.color_items[name] = item
        color_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Colour", None, "")
        color_parent.setSubmenu_(colors)
        menu.addItem_(color_parent)

        menu.addItem_(NSMenuItem.separatorItem())
        add("Allow screen access…", "screenAccessClicked:")
        add("Open log", "logClicked:")
        add(f"Check for updates   ({core.VERSION})", "updateClicked:")
        add("About Buddy Lite", "aboutClicked:")
        add("Quit Buddy", "quitClicked:", "q")
        self.status_item.setMenu_(menu)

    # ------------------------------------------------------------------ settings
    def _save(self):
        if self.persist:
            core.save_settings(self.settings)

    def toggle(self, key, item):
        self.settings[key] = not self.settings[key]
        item.setState_(1 if self.settings[key] else 0)
        if key == "follow_mouse":
            self.overlay.follow_mouse = self.settings[key]
        if key == "island":
            self._set_island(self.settings[key])
        if key == "speak" and not self.settings[key]:
            self.speaker.stop()
        self._save()

    def set_color(self, name):
        self.settings["color"] = name
        for other, item in self.color_items.items():
            item.setState_(1 if other == name else 0)
        self.overlay.set_color(core.COLORS[name])
        self._save()

    def _set_island(self, enabled):
        """Buddy Island: click = type, hold = talk (same as the hotkey), click an answer = dismiss."""
        if enabled and self.island is None:
            try:
                self.island = Island(core.COLORS[self.settings["color"]], on_press=self._key_down,
                                     on_release=self._key_up, on_dismiss=self.dismiss)
            except Exception:                                   # noqa: BLE001
                logging.exception("Buddy Island could not start")
                self.island = None
        elif self.island is not None:
            self.island.show(enabled)
        self.events.append(("island", bool(enabled)))

    def dismiss(self):
        """Clicked the answer in the island: stop talking and fold back into the notch."""
        self.ask_token += 1
        self.speaker.stop()
        self.overlay.home()

    def edit_brain(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Buddy's brain")
        alert.setInformativeText_(
            "Out of the box Buddy uses its free cloud brain: no setup, shared daily limit, screenshots are "
            "answered by Cloudflare Workers AI and not stored.\n\n"
            "For unlimited private answers use a local Ollama: endpoint "
            f"{core.LOCAL_BRAIN_URL}, model {core.LOCAL_BRAIN_MODEL} (ollama pull it first). "
            "Any OpenAI-compatible endpoint that reads images works, with an API key if it needs one.\n\n"
            f"Free brain: {core.FREE_BRAIN_URL}, model {core.FREE_BRAIN_MODEL}"
        )
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 380, 96))
        fields = {}
        rows = [("brain_url", "Endpoint"), ("brain_model", "Model"), ("brain_key", "API key")]
        for index, (key, label) in enumerate(rows):
            y = 66 - index * 32
            caption = NSTextField.labelWithString_(label)
            caption.setFrame_(NSMakeRect(0, y + 3, 70, 20))
            view.addSubview_(caption)
            field_class = NSSecureTextField if key == "brain_key" else NSTextField
            field = field_class.alloc().initWithFrame_(NSMakeRect(74, y, 306, 24))
            field.setStringValue_(self.settings.get(key, ""))
            view.addSubview_(field)
            fields[key] = field
        alert.setAccessoryView_(view)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        NSApp.activateIgnoringOtherApps_(True)
        if alert.runModal() == ALERT_FIRST_BUTTON:
            for key, field in fields.items():
                self.settings[key] = field.stringValue().strip()
            self.history = []
            self._save()

    def open_screen_access(self):
        self._ask_screen_permission()
        subprocess.run(["open", PRIVACY_SCREEN], check=False)

    def about(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Buddy Lite for Mac {core.VERSION}")
        alert.setInformativeText_(
            "Hold Control+Option+Space and ask, or tap it to type. Buddy looks at the display under your mouse, "
            "points at what it means and reads the answer aloud.\n\n"
            "Lite means: pointing and answers only. Clicking and typing for you, the Pips pet and keyboard "
            "sounds are on Windows for now.\n\nhttps://juw12.github.io/buddy/"
        )
        NSApp.activateIgnoringOtherApps_(True)
        alert.runModal()

    # ------------------------------------------------------------------ hotkey: hold = talk, tap = type
    def _key_down(self):
        if self.pressed:
            return
        self.pressed = True
        self.press_token += 1
        AppHelper.callLater(HOLD_SECONDS, self._hold_check, self.press_token)

    def _hold_check(self, token):
        if self.pressed and token == self.press_token and not self.listening:
            self.speaker.stop()
            self.ask_token += 1
            if self.listener.start():
                self.listening = True
                self.overlay.set_state("listening", "")

    def _key_up(self):
        self.pressed = False
        if self.listening:
            self.listening = False
            self.listener.stop()
            self.overlay.set_state("thinking", "")
        else:
            self.open_ask_box()

    def _heard_partial(self, text):
        if self.listening and text:
            self.overlay.set_state("listening", f"“{text}”")

    def _heard(self, text):
        if text:
            self.ask(text)
        else:
            self._say_briefly("I didn't catch that. Hold the keys while you talk, or tap them to type.")

    def _voice_error(self, message):
        self.listening = False
        self._say_briefly(message)

    # ------------------------------------------------------------------ ask box
    def open_ask_box(self):
        if self.ask_panel is None:
            style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskUtilityWindow
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 360, 44), style, NSBackingStoreBuffered, False
            )
            panel.setTitle_("Ask Buddy")
            panel.setFloatingPanel_(True)
            panel.setHidesOnDeactivate_(False)
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(10, 10, 340, 24))
            field.setPlaceholderString_("Ask about your screen, then press Return")
            field.setTarget_(NSApp.delegate())
            field.setAction_("askSubmitted:")
            panel.contentView().addSubview_(field)
            self.ask_panel = panel
            self.ask_field = field
        mouse_x, mouse_y = NSEvent.mouseLocation()
        self.ask_panel.setFrameTopLeftPoint_((mouse_x + 24, mouse_y - 24))
        self.ask_field.setStringValue_("")
        NSApp.activateIgnoringOtherApps_(True)
        self.ask_panel.makeKeyAndOrderFront_(None)
        self.ask_panel.makeFirstResponder_(self.ask_field)

    def ask_box_submitted(self, text):
        text = (text or "").strip()
        if self.ask_panel is not None:
            self.ask_panel.orderOut_(None)
        if text:
            # Let the panel disappear before Buddy looks at the screen
            AppHelper.callLater(0.15, self.ask, text)

    # ------------------------------------------------------------------ ask -> look -> answer -> point
    def ask(self, question):
        self.ask_token += 1
        token = self.ask_token
        self.speaker.stop()
        self.overlay.home()
        self.overlay.set_state("thinking", "")
        screen, main_height = _screen_under_mouse()
        settings = dict(self.settings)
        history = list(self.history)
        self.events.append(("ask", question))
        logging.info("ask: %s", question)

        def work():
            jpeg = None
            text_boxes = []
            reader = None
            try:
                jpeg = core.take_screenshot(screen, main_height)
                self.events.append(("screenshot", len(jpeg)))
                # Read the screen's text while the brain thinks, to snap the arrow onto what it names
                reader = threading.Thread(target=lambda: text_boxes.extend(ocr.read_text(jpeg)), daemon=True)
                reader.start()
            except Exception as error:                        # noqa: BLE001
                self.events.append(("screenshot_failed", str(error)))
                logging.warning("screen look failed: %s", error)
            try:
                reply, brain = core.ask_with_fallback(
                    question,
                    jpeg,
                    settings,
                    history,
                    on_text=lambda text: AppHelper.callAfter(self._partial, token, text),
                )
                self.history = brain.history
                self.events.append(("brain", brain.model))
                if reader is not None:
                    reader.join(timeout=2.0)
                AppHelper.callAfter(self._answered, token, reply, screen, list(text_boxes))
            except Exception as error:                        # noqa: BLE001
                logging.warning("brain failed: %s", error)
                AppHelper.callAfter(self._failed, token, error)

        threading.Thread(target=work, daemon=True).start()

    def _partial(self, token, text):
        if token == self.ask_token:
            shown = core.visible_text(text)
            if shown:
                self.overlay.say(shown)

    def _answered(self, token, reply, screen, text_boxes=()):
        if token != self.ask_token:
            return
        spoken, points = core.parse_reply(reply)
        if text_boxes:
            snapped = core.snap_points(points, text_boxes)
            if snapped != points:
                logging.info("snapped %s -> %s using %d text lines", points, snapped, len(text_boxes))
            points = snapped
        self.events.append(("answer", spoken))
        self.events.append(("points", points))
        if self.settings["speak"]:
            self.speaker.speak(spoken)
        targets = [core.point_to_cocoa(point, screen) for point in points]
        if not targets:
            self.overlay.say(spoken or "Hmm, I'm not sure.")
            AppHelper.callLater(max(4.0, len(spoken) * 0.065), self._home_if, token)
        else:
            for index, (x, y, label) in enumerate(targets):
                text = spoken if index == 0 else f"{index + 1}. {label}"
                AppHelper.callLater(index * STEP_SECONDS, self._point_step, token, x, y, text)
            reading = max(len(targets) * STEP_SECONDS, len(spoken) * 0.065) + 2.0
            AppHelper.callLater(reading, self._home_if, token)
        if self.on_answered:
            self.on_answered(spoken, targets)

    def _point_step(self, token, x, y, text):
        if token == self.ask_token:
            self.overlay.fly_to(x, y)
            self.overlay.say(text)

    def _failed(self, token, error):
        if token != self.ask_token:
            return
        self.events.append(("error", str(error)))
        self._say_briefly(core.friendly_error(error), seconds=7)
        if self.on_answered:
            self.on_answered(None, [])

    def _say_briefly(self, text, seconds=4.5):
        self.ask_token += 1
        token = self.ask_token
        self.overlay.say(text)
        AppHelper.callLater(seconds, self._home_if, token)

    def _home_if(self, token):
        if token == self.ask_token:
            self.overlay.home()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    core.setup_logging()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = _Delegate.alloc().init()
    if "--selftest" in argv:
        from . import selftest

        out_dir = Path(argv[argv.index("--selftest") + 1]) if len(argv) > argv.index("--selftest") + 1 else Path("selftest_out")
        controller = selftest.make_controller(out_dir)
    else:
        controller = Controller()
    delegate.controller = controller
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
