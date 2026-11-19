"""End-to-end self-test for Buddy Lite on a real Mac (run by CI on GitHub's macOS machines).

Starts the real app with a fake brain on localhost, asks "Where's Save As?", and records what happens:
offscreen renders of the buddy (always work), real screen captures (need Screen Recording permission),
whether the pointer landed on each target, and whether the hotkey, speech in and speech out are available.
Writes report.json; exits 0 only if the checks pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from AppKit import NSApp, NSScreen
from PyObjCTools import AppHelper

from . import core, ocr
from .app import Controller

CANNED_REPLY = (
    "Save As is in the File menu. Open File, then choose Save As. "
    "[POINT:60,30:File menu] [POINT:110,240:Save As]"
)


class _FakeBrain(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        user = body.get("messages", [{}])[-1].get("content", [])
        has_image = any(isinstance(part, dict) and part.get("type") == "image_url" for part in user)
        _FakeBrain.requests.append({"model": body.get("model"), "stream": body.get("stream"), "image": has_image})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        words = CANNED_REPLY.split(" ")
        for start in range(0, len(words), 4):
            piece = " ".join(words[start:start + 4]) + " "
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.05)
        self.wfile.write(b"data: [DONE]\n\n")


def _start_fake_brain():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeBrain)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class SelfTestController(Controller):
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.server = _start_fake_brain()
        settings = dict(core.DEFAULTS)
        settings.update(
            brain_url=f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions",
            brain_model="fake-vision",
            fallback=False,                     # the unreachable step must fail, not ask the real free brain
            speak=False,
        )
        super().__init__(settings=settings, persist=False)
        self.report = {"version": core.VERSION, "steps": [], "checks": {}}
        self.targets = []

    def start(self):
        super().start()
        screens = [tuple(map(float, (s.frame().origin.x, s.frame().origin.y, s.frame().size.width,
                                      s.frame().size.height))) for s in NSScreen.screens()]
        self.report["screens"] = screens
        self.report["checks"]["hotkey_registered"] = self.hotkey.ok
        self.report["hotkey_error"] = self.hotkey.error
        self.report["checks"]["speech_in_available"] = self.listener.ok
        self.report["checks"]["speech_out_available"] = self.speaker.ok
        self.report["checks"]["menu_bar_item"] = self.status_item is not None
        self._check_island()
        self._check_text_recognition()
        self._check_voice_languages()
        self._check_agent_pointer()
        plan = [
            (0.8, self._shot, "01_idle"),
            (1.0, self._state, ("listening", "“where's save as”")),
            (1.8, self._shot, "02_listening"),
            (2.0, self._state, ("thinking", "")),
            (2.8, self._shot, "03_thinking"),
            (3.0, self._ask, None),
            (40.0, self._finish, "timeout"),
        ]
        for delay, action, argument in plan:
            AppHelper.callLater(delay, action, argument)

    def _check_voice_languages(self):
        """Hold-to-talk in other languages: the menu's languages exist in Apple's speech recognition, the
        listener switches to Indonesian and back, and answers are recognised by language for the voice."""
        from .voice import text_language

        checks = self.report["checks"]
        supported = {code.lower() for code in self.listener.supported_languages()}
        wanted = ["en-US", "id-ID", "zh-TW", "zh-CN", "ja-JP", "ko-KR"]
        self.report["voice_languages"] = {code: code.lower() in supported for code in wanted}
        self.report["voice_menu"] = sorted(self.voice_language_items)
        checks["voice_languages"] = all(self.report["voice_languages"].values())
        switched = self.listener.set_language("id-ID")
        locale = str(self.listener.recognizer.locale().localeIdentifier()) if self.listener.recognizer else ""
        back = self.listener.set_language("")
        self.report["voice_switch"] = {"to_id": switched, "locale": locale, "back": back}
        checks["voice_language_switch"] = switched and locale.replace("_", "-").startswith("id") and back
        samples = {
            "id": "Klik menu File di kiri atas, lalu pilih Simpan Sebagai untuk menyimpan dokumen ini.",
            "zh-Hant": "請按左上角的檔案選單，然後選擇另存新檔。",
            "en": "Open the File menu at the top left, then choose Save As.",
        }
        detected = {expected: text_language(text) for expected, text in samples.items()}
        self.report["language_detect"] = detected
        detected["id with id-ID picked"] = text_language(samples["id"], "id-ID")
        self.report["language_detect"] = detected
        checks["language_detect"] = (all(detected[expected] == expected for expected in samples)
                                     and detected["id with id-ID picked"] == "id")
        voices = {}
        for expected, text in samples.items():
            voice = self.speaker.voice_for(text, "")
            voices[expected] = str(voice.language()) if voice is not None else "default"
        self.report["answer_voices"] = voices        # which voice reads each answer (not required)

    def _check_agent_pointer(self):
        """The agent pointer: a click a program makes is flown to and named, your own click is left alone,
        and the pointer goes away again. The press is handed to the app directly, so this needs no permission."""
        import Quartz
        from AppKit import NSEvent

        checks = self.report["checks"]
        result = self.report["agent_pointer"] = {}
        checks["agent_monitor"] = self.agent_monitor is not None
        # A real mouse press event is read back correctly (where, which button)
        try:
            press = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventRightMouseDown, (300.0, 200.0),
                                                   Quartz.kCGMouseButtonRight)
            read = self._read_press(NSEvent.eventWithCGEvent_(press))
            result["read"] = list(read)
            checks["agent_press_read"] = read[1:4] == (300.0, 200.0, "right")
        except Exception as error:                                # noqa: BLE001
            result["read_error"] = repr(error)
            checks["agent_press_read"] = False
        # Your own hand (no posting process) and Buddy itself are never followed; another process is
        other = os.getppid()
        result["own_hand_followed"] = self.program_clicked(0, 200.0, 150.0)
        result["buddy_followed"] = self.program_clicked(os.getpid(), 200.0, 150.0)
        result["program_followed"] = self.program_clicked(other, 200.0, 150.0)
        result["program"] = other
        main_height = NSScreen.screens()[0].frame().size.height
        AppHelper.callLater(1.0, self._check_agent_landed, (200.0, main_height - 150.0))
        AppHelper.callLater(core.AGENT_HOLD_SECONDS + 1.0, self._check_agent_rested, None)
        if os.environ.get("BUDDY_SELFTEST_LIVE_CLICK") == "1":
            AppHelper.callLater(core.AGENT_HOLD_SECONDS + 1.2, self._live_click, None)

    def _check_agent_landed(self, target):
        result = self.report["agent_pointer"]
        agent = self.agent
        landed = False
        if agent is not None:
            error = ((agent.pos[0] - target[0]) ** 2 + (agent.pos[1] - target[1]) ** 2) ** 0.5
            picture = self.out_dir / "00_agent_pointer.png"
            try:
                agent.render_png(picture)
            except Exception as error_drawing:                    # noqa: BLE001
                result["render_error"] = repr(error_drawing)
            result.update(landed_error_pt=round(error, 2), text=agent.text,
                          visible=bool(agent.window.isVisible()),
                          render_ok=picture.exists() and picture.stat().st_size > 0)
            landed = (error < 3 and agent.text.endswith("clicked here") and result["visible"]
                      and result["render_ok"])
        self.report["checks"]["agent_pointer_flies"] = (landed and result["program_followed"]
                                                        and not result["own_hand_followed"]
                                                        and not result["buddy_followed"])

    def _check_agent_rested(self, _):
        self.report["checks"]["agent_pointer_rests"] = self.agent is None

    def _live_click(self, _):
        """Only when CI asks for it: post one real click and see whether the monitor hears it with the
        posting process on it. Reported, not required: a machine may not let a program post clicks."""
        import Quartz

        live = self.report["live_click"] = {"buddy": os.getpid()}
        try:
            allowed = getattr(Quartz, "CGPreflightPostEventAccess", None)
            live["post_allowed"] = bool(allowed()) if allowed else None
            frame = NSScreen.screens()[0].frame()
            where = (frame.size.width / 2.0, frame.size.height / 2.0)
            self.agent_heard = []
            for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                press = Quartz.CGEventCreateMouseEvent(None, kind, where, Quartz.kCGMouseButtonLeft)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, press)
            live["posted_at"] = list(where)
        except Exception as error:                                # noqa: BLE001
            live["error"] = repr(error)
        AppHelper.callLater(1.5, self._live_click_heard, None)

    def _live_click_heard(self, _):
        live = self.report["live_click"]
        heard = self.agent_heard or []
        self.agent_heard = None
        live["heard"] = [list(press) for press in heard]
        # Buddy posted it, so the press must come back carrying Buddy's own process
        self.report["checks"]["live_click_seen"] = any(press[0] == os.getpid() for press in heard)

    def _check_text_recognition(self):
        """The arrow snaps onto text read by Apple's Vision framework: prove it is bundled and works."""
        from AppKit import (NSBitmapImageRep, NSColor, NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
                            NSImage, NSJPEGFileType, NSMakeRect, NSRectFill, NSString)

        try:
            picture = NSImage.alloc().initWithSize_((640, 200))
            picture.lockFocus()
            NSColor.whiteColor().set()
            NSRectFill(NSMakeRect(0, 0, 640, 200))
            attributes = {NSFontAttributeName: NSFont.systemFontOfSize_(48),
                          NSForegroundColorAttributeName: NSColor.blackColor()}
            NSString.stringWithString_("Save As").drawAtPoint_withAttributes_((60, 70), attributes)
            picture.unlockFocus()
            bitmap = NSBitmapImageRep.imageRepWithData_(picture.TIFFRepresentation())
            jpeg = bytes(bitmap.representationUsingType_properties_(NSJPEGFileType, {}))
            lines = ocr.read_text(jpeg)
            self.report["text_recognition"] = [list(line) for line in lines]
            # A rough point near the words must move onto them
            start = (320.0, 420.0)
            snapped = core.snap_points([(*start, "Save As")], lines)
            self.report["text_snap"] = list(snapped[0])
            self.report["checks"]["text_recognition"] = any("save" in text.lower() for text, *_ in lines) \
                and snapped[0][:2] != start
        except Exception as error:                                # noqa: BLE001
            self.report["text_recognition_error"] = repr(error)
            self.report["checks"]["text_recognition"] = False

    def _check_island(self):
        """Buddy Island: hangs from the top edge of the menu-bar display, centred on the notch or display."""
        from .island import screen_geometry

        island = self.island
        checks = self.report["checks"]
        if island is None:
            checks["island_at_top"] = False
            return
        frame, menu_bar, notch = screen_geometry()
        f = island.frame
        self.report["island"] = {
            "display": [frame.x, frame.y, frame.width, frame.height],
            "menu_bar": menu_bar,
            "notch": None if notch is None else [notch.x, notch.y, notch.width, notch.height],
            "frame": [round(f.x, 1), round(f.y, 1), round(f.width, 1), round(f.height, 1)],
        }
        centre = (notch.x + notch.width / 2.0) if notch is not None else (frame.x + frame.width / 2.0)
        checks["island_at_top"] = (abs((f.y + f.height) - (frame.y + frame.height)) < 0.5
                                   and abs((f.x + f.width / 2.0) - centre) < 0.5
                                   and island.window.isVisible())
        checks["island_clickable"] = not island.window.ignoresMouseEvents()

    # -------------------------------------------------------------- steps
    def _state(self, argument):
        self.overlay.set_state(*argument)

    def _shot(self, name):
        offscreen = self.out_dir / f"{name}_buddy.png"
        try:
            self.overlay.render_png(offscreen)
        except Exception as error:                        # noqa: BLE001
            self.report["steps"].append({"shot": name, "render_error": str(error)})
        island_path = self.out_dir / f"{name}_island.png"
        island_ok = False
        if self.island is not None:
            try:
                island_ok = bool(self.island.render_png(island_path)) and island_path.stat().st_size > 0
            except Exception as error:                    # noqa: BLE001
                self.report["steps"].append({"shot": name, "island_error": str(error)})
        screen_path = self.out_dir / f"{name}_screen.png"
        result = subprocess.run(["screencapture", "-x", str(screen_path)], capture_output=True, timeout=20)
        self.report["steps"].append({
            "shot": name,
            "state": self.overlay.state,
            "text": self.overlay.text,
            "buddy_pos": [round(v, 1) for v in self.overlay.pos],
            "render_ok": offscreen.exists() and offscreen.stat().st_size > 0,
            "island_mode": self.island.mode if self.island is not None else None,
            "island_ok": island_ok,
            "screen_ok": result.returncode == 0 and screen_path.exists(),
        })

    def _ask(self, _):
        self.on_answered = self._answered_hook
        self.ask("Where's Save As?")

    def _answered_hook(self, spoken, targets):
        self.on_answered = None
        self.targets = targets
        self.report["answer"] = spoken
        self.report["targets"] = [[round(x, 1), round(y, 1), label] for x, y, label in targets]
        for index in range(len(targets)):
            # Each step starts at index * STEP_SECONDS; the flight takes ~0.55 s
            AppHelper.callLater(index * 2.4 + 1.0, self._check_point, index)
        AppHelper.callLater(len(targets) * 2.4 + 0.5, self._ask_unreachable, None)

    def _check_point(self, index):
        x, y, label = self.targets[index]
        error = ((self.overlay.pos[0] - x) ** 2 + (self.overlay.pos[1] - y) ** 2) ** 0.5
        self.report["steps"].append({"point": index, "label": label, "landed_error_pt": round(error, 2)})
        self._shot(f"0{4 + index}_point{index + 1}")

    def _ask_unreachable(self, _):
        self.settings["brain_url"] = "http://127.0.0.1:9/v1/chat/completions"     # nothing listens there
        self.on_answered = lambda spoken, targets: AppHelper.callLater(0.6, self._error_shot, None)
        self.ask("Are you there?")

    def _error_shot(self, _):
        self._shot("07_error")
        AppHelper.callLater(0.3, self._finish, "done")

    def _finish(self, reason):
        if self.report.get("finished"):
            return
        self.report["finished"] = reason
        self.report["events"] = [[what, detail if isinstance(detail, (str, int)) else repr(detail)]
                                 for what, detail in self.events]
        self.report["fake_brain_requests"] = _FakeBrain.requests
        landed = [step for step in self.report["steps"] if "landed_error_pt" in step]
        checks = self.report["checks"]
        checks["answer_parsed"] = bool(self.report.get("answer")) and "[POINT" not in self.report.get("answer", "")
        checks["two_points"] = len(self.report.get("targets", [])) == 2
        checks["pointer_landed"] = bool(landed) and all(step["landed_error_pt"] < 3 for step in landed)
        checks["renders_ok"] = all(step.get("render_ok", True) for step in self.report["steps"] if "shot" in step)
        checks["error_bubble"] = any(what == "error" for what, _ in self.events)
        checks["brain_called"] = len(_FakeBrain.requests) >= 1
        shots = [step for step in self.report["steps"] if "island_mode" in step]
        checks["island_renders"] = bool(shots) and all(step["island_ok"] for step in shots)
        # The island followed the pointer's story: listening, thinking, then the answer
        modes = [step["island_mode"] for step in shots]
        checks["island_mirrors"] = "listening" in modes and "thinking" in modes and "talking" in modes
        required = ["answer_parsed", "two_points", "pointer_landed", "renders_ok", "error_bubble",
                    "brain_called", "hotkey_registered", "menu_bar_item", "text_recognition",
                    "island_at_top", "island_clickable", "island_renders", "island_mirrors",
                    "voice_languages", "voice_language_switch", "language_detect",
                    "agent_monitor", "agent_press_read", "agent_pointer_flies", "agent_pointer_rests"]
        self.report["passed"] = reason == "done" and all(checks.get(name) for name in required)
        (self.out_dir / "report.json").write_text(json.dumps(self.report, indent=1), encoding="utf-8")
        print(json.dumps({"passed": self.report["passed"], "checks": checks,
                          "text_recognition": self.report.get("text_recognition"),
                          "text_snap": self.report.get("text_snap"),
                          "voice_languages": self.report.get("voice_languages"),
                          "voice_switch": self.report.get("voice_switch"),
                          "language_detect": self.report.get("language_detect"),
                          "answer_voices": self.report.get("answer_voices"),
                          "agent_pointer": self.report.get("agent_pointer"),
                          "live_click": self.report.get("live_click"),
                          "text_recognition_error": self.report.get("text_recognition_error")}, indent=1), flush=True)
        self.server.shutdown()
        AppHelper.stopEventLoop()
        NSApp.terminate_(None)


def make_controller(out_dir: Path) -> SelfTestController:
    return SelfTestController(Path(out_dir))
