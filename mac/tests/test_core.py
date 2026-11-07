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
    assert settings["brain_url"] == core.FREE_BRAIN_URL
    assert settings["brain_model"] == core.FREE_BRAIN_MODEL
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


def test_local_brain_bypasses_http_proxy(monkeypatch):
    # A proxy that is not listening: if Buddy used it for 127.0.0.1 the error would not be "can't reach".
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    server = _serve()
    try:
        _Brain.stream = False
        brain = core.Brain(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "m")
        assert brain.ask("hi", None) == "Plain answer. [POINT:1,2:x]"
    finally:
        _Brain.stream = True
        server.shutdown()


def test_loopback_url_detection():
    assert core._is_loopback_url("http://127.0.0.1:11434/v1/chat/completions")
    assert core._is_loopback_url("http://localhost:1234/v1")
    assert core._is_loopback_url("http://[::1]:11434/v1")
    assert not core._is_loopback_url("https://example.com/v1/chat/completions")
    assert not core._is_loopback_url("http://192.168.1.20:11434/v1")


def test_parse_reply_fraction_coordinates():
    # Llama 4 Scout often writes y as a 0..1 fraction
    assert core.parse_reply("Here. [POINT:183,0.440:Save As]")[1] == [(183.0, 440.0, "Save As")]
    assert core.parse_reply("Here. [POINT:450,.44:x]")[1] == [(450.0, 440.0, "x")]
    assert core.parse_reply("Here. [POINT:0,1:corner]")[1] == [(0.0, 1.0, "corner")]


def test_brain_candidates_fallback_rules():
    free = (core.FREE_BRAIN_URL, core.FREE_BRAIN_MODEL, "")
    local = (core.LOCAL_BRAIN_URL, core.LOCAL_BRAIN_MODEL, "")
    assert core.brain_candidates(dict(core.DEFAULTS)) == [free, local]
    old_default = dict(core.DEFAULTS, brain_url=core.LOCAL_BRAIN_URL, brain_model=core.LOCAL_BRAIN_MODEL)
    assert core.brain_candidates(old_default) == [local, free]
    # A brain with a key (company / paid) never sends screenshots to the free cloud brain
    company = dict(core.DEFAULTS, brain_url="https://example.com/v1/chat/completions", brain_model="m", brain_key="k")
    assert core.brain_candidates(company) == [("https://example.com/v1/chat/completions", "m", "k"), local]
    assert core.brain_candidates(dict(core.DEFAULTS, fallback=False)) == [free]


def test_fallback_after_daily_cap(monkeypatch):
    server = _serve()
    try:
        _Brain.stream = False
        capped = _serve_error(429, {"error": {"message": "out", "code": "daily_cap"}})
        settings = dict(core.DEFAULTS, brain_url=f"http://127.0.0.1:{capped.server_port}/v1/chat/completions")
        monkeypatch.setattr(core, "FREE_BRAIN_URL", "http://127.0.0.1:9/v1/chat/completions")
        monkeypatch.setattr(core, "LOCAL_BRAIN_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
        reply, brain = core.ask_with_fallback("hi", None, settings, [])
        assert reply.startswith("Plain answer")
        assert brain.url == core.LOCAL_BRAIN_URL
        capped.shutdown()
    finally:
        _Brain.stream = True
        server.shutdown()


def test_no_fallback_on_bad_request_and_friendly_text():
    bad = _serve_error(400, {"error": {"message": "bad", "code": "bad_request"}})
    settings = dict(core.DEFAULTS, brain_url=f"http://127.0.0.1:{bad.server_port}/v1/chat/completions")
    try:
        core.ask_with_fallback("hi", None, settings, [])
        raise AssertionError("expected BrainError")
    except core.BrainError as error:
        assert error.status == 400 and error.code == "bad_request"
    finally:
        bad.shutdown()
    assert "out of questions" in core.friendly_error(core.BrainError("x", 429, "daily_cap"))
    assert "can't reach" in core.friendly_error(core.BrainError("x", None, "unreachable"))


def _serve_error(status, payload):
    class _Error(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Error)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_snap_points_moves_onto_matching_text():
    boxes = [
        ("File", 160, 170, 200, 195),
        ("Save As... Ctrl+Shift+S", 160, 420, 400, 445),
        ("Save", 160, 380, 200, 405),
        ("Do you want to save changes?", 500, 500, 800, 520),
    ]
    # Rough point near the menu, label "Save As": lands on the left part of the Save As row
    (x, y, label), = core.snap_points([(200, 300, "Save As")], boxes)
    assert 160 <= x <= 230 and 420 <= y <= 445 and label == "Save As"
    # "Save" prefers the exact "Save" line over the longer rows
    (x, y, _), = core.snap_points([(190, 400, "Save button")], boxes)
    assert (x, y) == (180.0, 392.5)
    # No matching text nearby: unchanged
    assert core.snap_points([(900, 900, "Dock")], boxes) == [(900, 900, "Dock")]
    # Matching text but too far away: unchanged
    assert core.snap_points([(950, 950, "File")], boxes) == [(950, 950, "File")]


def test_snap_points_word_inside_a_line():
    # Vision reads the menu bar as one line: "Format" sits in the middle of it
    boxes = [("Edit Format View", 200, 170, 400, 196)]
    (x, y, _), = core.snap_points([(330, 140, "Format")], boxes)
    assert 260 <= x <= 340 and 170 <= y <= 196
    (x, _, _), = core.snap_points([(480, 140, "Edit menu")], boxes)
    assert x < 250


# --------------------------------------------------------------------------- Buddy Island (notch) geometry
MACBOOK_PRO_14 = core.ScreenRect(0, 0, 1512, 982)
# NSScreen.auxiliaryTopLeftArea / auxiliaryTopRightArea on a 14" MacBook Pro (points): the notch is 185 wide
LEFT_AREA = (0, 950, 663.5, 32)
RIGHT_AREA = (848.5, 950, 663.5, 32)


def test_notch_found_between_the_menu_bar_areas():
    notch = core.notch_rect(MACBOOK_PRO_14, LEFT_AREA, RIGHT_AREA)
    assert notch == core.ScreenRect(663.5, 950, 185, 32)


def test_no_notch_on_plain_displays():
    assert core.notch_rect(MACBOOK_PRO_14, None, None) is None
    assert core.notch_rect(MACBOOK_PRO_14, (0, 950, 756, 32), (756, 950, 756, 32)) is None   # no gap


def test_island_wraps_the_notch_and_hangs_from_the_top():
    notch = core.notch_rect(MACBOOK_PRO_14, LEFT_AREA, RIGHT_AREA)
    rest = core.island_rect(MACBOOK_PRO_14, 32, notch, "idle")
    assert rest.y + rest.height == 982                               # flush with the top edge
    assert rest.x + rest.width / 2 == notch.x + notch.width / 2      # centred on the camera
    assert rest.width == notch.width + 2 * core.ISLAND_WINGS and rest.height == 32
    busy = core.island_rect(MACBOOK_PRO_14, 32, notch, "thinking")
    assert busy.width > rest.width and busy.height == rest.height
    answer = core.island_rect(MACBOOK_PRO_14, 32, notch, "talking", answer_height=60)
    assert answer.height == 32 + 60 and answer.y + answer.height == 982
    assert answer.width >= core.ISLAND_ANSWER_WIDTH


def test_island_without_notch_sits_in_the_middle_of_the_menu_bar():
    display = core.ScreenRect(-1920, 0, 1920, 1080)
    rest = core.island_rect(display, 25, None, "idle")
    assert rest.width == core.ISLAND_NO_NOTCH_WIDTH
    assert rest.x + rest.width / 2 == -960
    assert rest.y + rest.height == 1080 and rest.height == 25


def test_island_never_wider_than_the_display():
    narrow = core.ScreenRect(0, 0, 300, 600)
    assert core.island_rect(narrow, 24, None, "talking", 40).width == 300 - 16


def test_island_is_on_by_default():
    assert core.DEFAULTS["island"] is True
