"""Unit tests for the platform-free part of Buddy Lite for Mac (run anywhere: python -m pytest mac/tests)."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buddy_mac import core  # noqa: E402


def test_parse_reply_points_and_text():
    spoken, points = core.parse_reply(
        "Open **File**, then Save As. [POINT:60,30:File menu] [POINT:110, 240 :Save As]"
    )
    assert spoken == "Open File, then Save As."
    assert points == [(60.0, 30.0, "File menu"), (110.0, 240.0, "Save As")]


def test_parse_reply_drops_out_of_range_and_half_tags():
    spoken, points = core.parse_reply("Here. [POINT:1200,30:nope] [POINT:5")
    assert spoken == "Here."
    assert points == []


def test_parse_reply_caps_points():
    reply = "x " + " ".join(f"[POINT:{i},{i}:p{i}]" for i in range(1, 6))
    assert len(core.parse_reply(reply)[1]) == core.MAX_POINTS


def test_point_to_cocoa_flips_y():
    screen = core.ScreenRect(0, 0, 1440, 900)
    assert core.point_to_cocoa((0, 0, "a"), screen) == (0, 900, "a")
    assert core.point_to_cocoa((500, 500, "b"), screen) == (720, 450, "b")
    assert core.point_to_cocoa((1000, 1000, "c"), screen) == (1440, 0, "c")


def test_point_to_cocoa_second_display():
    # A 1920x1080 display to the right of a 1440x900 main display, bottoms aligned
    screen = core.ScreenRect(1440, 0, 1920, 1080)
    x, y, _ = core.point_to_cocoa((250, 100, ""), screen)
    assert (x, y) == (1440 + 480, 1080 - 108)


def test_capture_rect_is_top_left_based():
    main_height = 900
    assert core.capture_rect(core.ScreenRect(0, 0, 1440, 900), main_height) == (0, 0, 1440, 900)
    # A display above the main one: Cocoa y=900, so its top is at -1080 in top-left coordinates
    assert core.capture_rect(core.ScreenRect(0, 900, 1920, 1080), main_height) == (0, -1080, 1920, 1080)


def test_settings_roundtrip_and_bad_color(tmp_path):
    path = tmp_path / "settings.json"
    settings = core.load_settings(path)
    assert settings["brain_url"].endswith("/v1/chat/completions")
    settings["color"] = "Purple"
    core.save_settings(settings, path)
    assert core.load_settings(path)["color"] == "Purple"
    path.write_text(json.dumps({"color": "Chartreuse"}))
    assert core.load_settings(path)["color"] == "Orange"


def test_hex_to_rgb():
    assert core.hex_to_rgb("#ff8424") == (1.0, 0x84 / 255, 0x24 / 255)


class _Brain(BaseHTTPRequestHandler):
    seen = []
    stream = True

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Brain.seen.append((body, self.headers.get("Authorization")))
        if not _Brain.stream:
            data = json.dumps({"choices": [{"message": {"content": "Plain answer. [POINT:1,2:x]"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ["It's in ", "the File menu. ", "[POINT:10,20:File]"]:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Brain)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_brain_streams_sends_image_and_key():
    server = _serve()
    try:
        _Brain.stream = True
        _Brain.seen.clear()
        brain = core.Brain(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "m", key="secret")
        partials = []
        reply = brain.ask("where is file?", b"\xff\xd8jpeg", on_text=partials.append)
        assert reply == "It's in the File menu. [POINT:10,20:File]"
        assert partials[-1] == reply and len(partials) == 3
        body, auth = _Brain.seen[-1]
        assert auth == "Bearer secret"
        assert body["stream"] is True and body["model"] == "m"
        user_parts = body["messages"][-1]["content"]
        assert user_parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert body["messages"][0]["role"] == "system"
        # History is kept for follow-ups, as text only
        assert brain.history[-1] == {"role": "assistant", "content": reply}
    finally:
        server.shutdown()


def test_brain_plain_json_reply():
    server = _serve()
    try:
        _Brain.stream = False
        brain = core.Brain(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "m")
        assert brain.ask("hi", None) == "Plain answer. [POINT:1,2:x]"
        assert _Brain.seen[-1][1] is None
    finally:
        _Brain.stream = True
        server.shutdown()


def test_brain_unreachable_message():
    brain = core.Brain("http://127.0.0.1:9/v1/chat/completions", "m", timeout=3)
    try:
        brain.ask("hi", None)
    except RuntimeError as error:
        assert "can't reach the brain" in str(error)
    else:
        raise AssertionError("expected an error")
