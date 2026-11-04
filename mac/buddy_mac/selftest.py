"""End-to-end self-test for Buddy Lite on a real Mac (run by CI on GitHub's macOS machines).

Starts the real app with a fake brain on localhost, asks "Where's Save As?", and records what happens:
offscreen renders of the buddy (always work), real screen captures (need Screen Recording permission),
whether the pointer landed on each target, and whether the hotkey, speech in and speech out are available.
Writes report.json; exits 0 only if the checks pass.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from AppKit import NSApp, NSScreen
from PyObjCTools import AppHelper

from . import core
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

    # -------------------------------------------------------------- steps
    def _state(self, argument):
        self.overlay.set_state(*argument)

    def _shot(self, name):
        offscreen = self.out_dir / f"{name}_buddy.png"
        try:
            self.overlay.render_png(offscreen)
        except Exception as error:                        # noqa: BLE001
            self.report["steps"].append({"shot": name, "render_error": str(error)})
        screen_path = self.out_dir / f"{name}_screen.png"
        result = subprocess.run(["screencapture", "-x", str(screen_path)], capture_output=True, timeout=20)
        self.report["steps"].append({
            "shot": name,
            "state": self.overlay.state,
            "text": self.overlay.text,
            "buddy_pos": [round(v, 1) for v in self.overlay.pos],
            "render_ok": offscreen.exists() and offscreen.stat().st_size > 0,
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
        required = ["answer_parsed", "two_points", "pointer_landed", "renders_ok", "error_bubble",
                    "brain_called", "hotkey_registered", "menu_bar_item"]
        self.report["passed"] = reason == "done" and all(checks.get(name) for name in required)
        (self.out_dir / "report.json").write_text(json.dumps(self.report, indent=1), encoding="utf-8")
        print(json.dumps({"passed": self.report["passed"], "checks": checks}, indent=1), flush=True)
        self.server.shutdown()
        AppHelper.stopEventLoop()
        NSApp.terminate_(None)


def make_controller(out_dir: Path) -> SelfTestController:
    return SelfTestController(Path(out_dir))
