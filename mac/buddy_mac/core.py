"""Buddy Lite for Mac: the parts that don't touch AppKit (settings, prompt, reply parsing, brain, screen look).

Kept free of PyObjC so it can be unit-tested anywhere. The screen look uses the built-in macOS tools
`screencapture` and `sips`, so no image library is needed.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Buddy"
VERSION = "1.3.2"

SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
SETTINGS_PATH = SUPPORT_DIR / "settings.json"
LOG_PATH = Path.home() / "Library" / "Logs" / APP_NAME / "buddy.log"

# Same palette as the Windows app's Settings -> Look
COLORS = {
    "Orange": "#ff8424",
    "Red": "#ef4444",
    "Pink": "#ec4899",
    "Purple": "#8b5cf6",
    "Blue": "#3b82f6",
    "Teal": "#14b8a6",
    "Green": "#22c55e",
    "Yellow": "#eab308",
    "Brown": "#a16207",
    "Graphite": "#475569",
}

# Buddy's free brain: a small Cloudflare Worker (cloud/brain in the repo), no account or key needed.
# Shared by everyone, so it has a daily cap; a local Ollama is the unlimited, private alternative.
FREE_BRAIN_URL = "https://buddy-brain.juwenmondial.workers.dev/v1/chat/completions"
FREE_BRAIN_MODEL = "buddy-free"
LOCAL_BRAIN_URL = "http://localhost:11434/v1/chat/completions"
LOCAL_BRAIN_MODEL = "qwen3-vl:4b-instruct"
FEEDBACK_URL = "https://github.com/juw12/buddy/issues/new?template=feedback.yml"

DEFAULTS = {
    # Any OpenAI-compatible chat endpoint that accepts images. The default needs no setup.
    "brain_url": FREE_BRAIN_URL,
    "brain_model": FREE_BRAIN_MODEL,
    "brain_key": "",
    # When the brain can't answer (offline, busy, out of free questions), try the other free one
    "fallback": True,
    "speak": True,
    "follow_mouse": True,
    "color": "Orange",
    # Buddy Island: Buddy lives in the MacBook notch (or a notch-shaped strip on Macs without one).
    # Click it to type, press and hold it to talk; it grows while Buddy listens, thinks and answers.
    "island": True,
    # The language Buddy listens for when you hold the hotkey. "" = the Mac's own language.
    "voice_language": "",
}

# Languages for hold-to-talk (Apple's speech recognition). The menu only lists the ones this Mac supports.
VOICE_LANGUAGES = [
    ("", "Same as this Mac"),
    ("en-US", "English (US)"),
    ("en-GB", "English (UK)"),
    ("id-ID", "Indonesian"),
    ("ms-MY", "Malay"),
    ("zh-TW", "Chinese, Traditional (Taiwan)"),
    ("zh-CN", "Chinese, Simplified"),
    ("yue-CN", "Cantonese"),
    ("ja-JP", "Japanese"),
    ("ko-KR", "Korean"),
    ("th-TH", "Thai"),
    ("vi-VN", "Vietnamese"),
    ("hi-IN", "Hindi"),
    ("de-DE", "German"),
    ("fr-FR", "French"),
    ("es-ES", "Spanish"),
    ("pt-BR", "Portuguese (Brazil)"),
]

MAX_POINTS = 3
SCREENSHOT_MAX_SIDE = 1280
HISTORY_TURNS = 4

SYSTEM_PROMPT = """You are Buddy, a friendly expert tutor that lives next to the user's mouse cursor on a Mac.
You are given a screenshot of the user's current screen and their question. Your reply is read aloud.

Rules:
- Reply in 1 to 3 short, natural, spoken sentences. No markdown, no lists, no emojis, no URLs.
- Reply in the language of the question, unless the user asks for another one.
- Be concrete and refer to what is actually visible on the screen. Use Mac names (Command, Option, Finder, menu bar).
- Whenever the question is about where something is or how to do something on screen, you MUST point:
  put one tag per element (button, menu, tab, field, icon, text) at the very END of your reply,
  in the order the user should use them, at most 3 tags:
  [POINT:x,y:short label]
  x and y are the CENTER of the element in normalized coordinates from 0 to 1000,
  where 0,0 is the top-left and 1000,1000 is the bottom-right of the screenshot.
- Both numbers are whole numbers from 0 to 1000, for example [POINT:512,88:Save button].
- If nothing needs pointing at, do not add any tag."""

POINT_PATTERN = re.compile(
    r"\[\s*POINT\s*:\s*(\.?\d+(?:\.\d+)?)\s*,\s*(\.?\d+(?:\.\d+)?)\s*(?::\s*([^\]]*))?\]",
    re.IGNORECASE,
)


def primary(code: str) -> str:
    """"zh-TW" -> "zh", "id_ID" -> "id"."""
    return (code or "").replace("_", "-").split("-")[0].lower()


def voice_language_name(code: str) -> str:
    for known, name in VOICE_LANGUAGES:
        if known and known.lower() == (code or "").replace("_", "-").lower():
            return name
    return ""


def language_rule(code: str) -> str:
    """One more system-prompt line when hold-to-talk listens for a language other than English: the answer
    follows the question's language, and this names the language to use when a short or mixed question
    leaves that open. Nothing for English, the Mac's own language ("") and codes the menu doesn't have."""
    name = voice_language_name(code)
    if not name or primary(code) == "en":
        return ""
    return (f"\n- The user's voice input language is {name}. If the question mixes languages or its language "
            "is unclear, reply in that language.")


def pick_voice(language: str, voices, preferred: str = "") -> str | None:
    """The identifier of the system voice to read an answer in `language` with, or None for no match.

    language: what the text is written in, as macOS reports it ("id", "en", "zh-Hant", "zh-Hans").
    voices: [(identifier, language_code, quality), ...] such as ("com.apple.voice.compact.id-ID.Damayanti",
    "id-ID", 1). preferred: the hold-to-talk language ("zh-TW"), used to choose a region when it shares
    the text's language. Better quality wins within the same region preference.
    """
    wanted = primary(language)
    if not wanted or wanted == "und":
        return None
    script_regions = {"hant": ("zh-TW", "zh-HK"), "hans": ("zh-CN",)}
    regions = []
    if preferred and primary(preferred) == wanted:
        regions.append(preferred.replace("_", "-").lower())
    for part in (language or "").replace("_", "-").lower().split("-")[1:]:
        regions += [region.lower() for region in script_regions.get(part, (f"{wanted}-{part}",))]
    best = None
    for identifier, code, quality in voices:
        code = (code or "").replace("_", "-").lower()
        if primary(code) != wanted:
            continue
        rank = (regions.index(code) if code in regions else len(regions), -int(quality or 0))
        if best is None or rank < best[0]:
            best = (rank, identifier)
    return best[1] if best else None


# --------------------------------------------------------------------------- settings
def load_settings(path: Path = SETTINGS_PATH) -> dict:
    settings = dict(DEFAULTS)
    try:
        settings.update(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if settings.get("color") not in COLORS:
        settings["color"] = DEFAULTS["color"]
    return settings


def save_settings(settings: dict, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# --------------------------------------------------------------------------- replies
def _thousandths(value: str) -> float:
    """Some models write one coordinate on a 0..1 scale ("183,0.440"): treat a decimal <= 1 as a fraction."""
    number = float(value)
    if "." in value and number <= 1:
        return round(number * 1000, 1)
    return number


def parse_reply(text: str) -> tuple[str, list[tuple[float, float, str]]]:
    """Split a model reply into (spoken_text, [(x_norm, y_norm, label), ...])."""
    points = []
    for match in POINT_PATTERN.finditer(text):
        x, y = _thousandths(match.group(1)), _thousandths(match.group(2))
        if 0 <= x <= 1000 and 0 <= y <= 1000:
            points.append((x, y, (match.group(3) or "").strip()))
    spoken = POINT_PATTERN.sub("", text)
    spoken = spoken.split("[")[0]                         # drop any malformed trailing tag
    spoken = re.sub(r"[*_#`>]+", "", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken, points[:MAX_POINTS]


def visible_text(partial: str) -> str:
    """What to show in the bubble while a reply is still streaming in (hide half-written tags)."""
    return parse_reply(partial)[0]


# --------------------------------------------------------------------------- snapping to on-screen text
# Small vision models usually name the right thing ("Save As") but land a little off. If the screen shows
# that text, move the point onto it. Text boxes come from on-device text recognition (ocr.py on a Mac).
_FILLER_WORDS = {"the", "button", "menu", "icon", "tab", "key", "option", "item", "link", "field", "box"}
SNAP_MAX_DISTANCE = 320       # in 0..1000 units: only snap to text near where the model pointed
SNAP_MIN_SCORE = 0.78


def _words(text: str) -> str:
    text = text.lower().replace("\u2026", " ").replace("...", " ")
    text = re.sub(r"[^\w\s']", " ", text)
    return " ".join(word for word in text.split() if word not in _FILLER_WORDS)


def _match_score(label: str, text: str) -> tuple[float, float]:
    """(score 0..1, where the label's centre sits across the text box, 0..1) for a label vs. one line of text.

    Text recognition often reads a row of controls as one line ("Edit Format View", "Save As... Ctrl+Shift+S"),
    so a label may be the start or the middle of a line; the point then goes to that part of the line.
    """
    from difflib import SequenceMatcher

    wanted, seen = _words(label), _words(text)
    if not wanted or not seen:
        return 0.0, 0.5
    if wanted == seen:
        return 1.0, 0.5
    padded = f" {seen} "
    at = padded.find(f" {wanted} ")
    if at != -1 and len(seen.split()) <= 6:
        centre = (at + len(wanted) / 2) / len(seen)
        extra = (len(seen) - len(wanted)) / 40
        return (0.9 if at == 0 else 0.86) - 0.12 * min(1.0, extra), min(1.0, max(0.0, centre))
    return SequenceMatcher(None, wanted, seen).ratio(), 0.5


def snap_points(points, text_boxes):
    """Move each (x, y, label) onto the screen text that matches its label, if there is one close by.

    text_boxes: [(text, x0, y0, x1, y1), ...] in 0..1000 screenshot coordinates.

    A point with a coordinate of exactly 0 is on the very edge of the screen. Models replying in a language
    other than English often write a menu path that way ([POINT:70,0:File] [POINT:70,0:Save As]), so for
    those the position is a guess and the label is what counts: they snap to matching text anywhere.
    """
    snapped = []
    for x, y, label in points:
        on_edge = x == 0 or y == 0
        best = None
        for text, x0, y0, x1, y1 in text_boxes:
            score, centre = _match_score(label, text)
            if score < SNAP_MIN_SCORE:
                continue
            target_x = x0 + (x1 - x0) * centre
            target_y = (y0 + y1) / 2
            distance = ((target_x - x) ** 2 + (target_y - y) ** 2) ** 0.5
            if distance > SNAP_MAX_DISTANCE and not on_edge:
                continue
            rank = (round(score, 2), -distance)
            if best is None or rank > best[0]:
                best = (rank, target_x, target_y)
        if best is None:
            snapped.append((x, y, label))
        else:
            snapped.append((round(best[1], 1), round(best[2], 1), label))
    return snapped


@dataclass
class ScreenRect:
    """A display in Cocoa coordinates: origin at the bottom-left of the main display, in points."""
    x: float
    y: float
    width: float
    height: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


# --------------------------------------------------------------------------- Buddy Island geometry
ISLAND_WINGS = 30.0          # resting: how far the island shows on each side of the notch (room for the dot)
ISLAND_BUSY_WINGS = 92.0     # listening / thinking: wider wings for the waveform and a word or two
ISLAND_NO_NOTCH_WIDTH = 132.0
ISLAND_ANSWER_WIDTH = 420.0
ISLAND_MIN_HEIGHT = 24.0     # menu bar height when macOS doesn't say (no notch, menu bar hidden)


def notch_rect(frame: ScreenRect, left_area, right_area) -> ScreenRect | None:
    """The camera notch of a display, from NSScreen.auxiliaryTopLeftArea / auxiliaryTopRightArea (macOS 12+):
    the gap between the two menu-bar areas. None on displays without a notch (or older macOS)."""
    if not left_area or not right_area:
        return None
    lx, ly, lw, lh = left_area
    rx, ry, rw, rh = right_area
    gap_left = lx + lw
    gap_right = rx
    height = max(lh, rh)
    if gap_right - gap_left < 20 or height <= 0:
        return None
    return ScreenRect(gap_left, frame.y + frame.height - height, gap_right - gap_left, height)


def island_rect(frame: ScreenRect, menu_bar_height: float, notch: ScreenRect | None, mode: str,
                answer_height: float = 0.0) -> ScreenRect:
    """Where the island is drawn (Cocoa points). It always hangs from the top edge of the display, centred on
    the notch (or the display), as tall as the menu bar; an answer grows it downwards."""
    top = frame.y + frame.height
    bar = max(ISLAND_MIN_HEIGHT, menu_bar_height, notch.height if notch else 0.0)
    if notch is not None:
        centre = notch.x + notch.width / 2.0
        core_width = notch.width
    else:
        centre = frame.x + frame.width / 2.0
        core_width = ISLAND_NO_NOTCH_WIDTH - 2 * ISLAND_WINGS
    if mode == "talking":
        width = max(ISLAND_ANSWER_WIDTH, core_width + 2 * ISLAND_WINGS)
        height = bar + max(0.0, answer_height)
    elif mode in ("listening", "thinking"):
        width = core_width + 2 * ISLAND_BUSY_WINGS
        height = bar
    else:
        width = core_width + 2 * ISLAND_WINGS
        height = bar
    width = min(width, frame.width - 16)
    return ScreenRect(centre - width / 2.0, top - height, width, height)


def point_to_cocoa(point: tuple[float, float, str], screen: ScreenRect) -> tuple[float, float, str]:
    """Normalized 0..1000 point on the screenshot -> Cocoa screen point (y grows upwards)."""
    nx, ny, label = point
    return (
        screen.x + nx / 1000.0 * screen.width,
        screen.y + screen.height - ny / 1000.0 * screen.height,
        label,
    )


def capture_rect(screen: ScreenRect, main_height: float) -> tuple[int, int, int, int]:
    """Cocoa rect -> the top-left based rect that `screencapture -R` expects."""
    top = main_height - (screen.y + screen.height)
    return int(round(screen.x)), int(round(top)), int(round(screen.width)), int(round(screen.height))


# --------------------------------------------------------------------------- screen look
class ScreenLookError(RuntimeError):
    pass


def take_screenshot(screen: ScreenRect, main_height: float) -> bytes:
    """One look at the display under the mouse, as a JPEG no bigger than SCREENSHOT_MAX_SIDE.

    macOS asks for the Screen Recording permission the first time. Without it the capture comes back
    as just the wallpaper, which the caller can't tell apart, so the menu shows how to allow it.
    """
    x, y, width, height = capture_rect(screen, main_height)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "look.jpg"
        result = subprocess.run(
            ["screencapture", "-x", "-t", "jpg", "-R", f"{x},{y},{width},{height}", str(path)],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0 or not path.exists():
            raise ScreenLookError(result.stderr.decode(errors="replace").strip() or "screencapture failed")
        subprocess.run(
            ["sips", "-Z", str(SCREENSHOT_MAX_SIDE), "-s", "formatOptions", "70", str(path)],
            capture_output=True,
            timeout=15,
        )
        return path.read_bytes()


# --------------------------------------------------------------------------- brain
class BrainError(RuntimeError):
    """A brain that didn't answer. status is the HTTP status (None = couldn't connect), code the reason."""

    def __init__(self, message: str, status: int | None = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def worth_retrying_elsewhere(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500


def _is_loopback_url(url: str) -> bool:
    """True for brains on this machine (Ollama, LM Studio): http://127.0.0.1, localhost, [::1]."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# A local brain must never go through an HTTP proxy: corporate proxies answer "502 Bad Gateway"
# for 127.0.0.1 instead of letting the connection reach Ollama (Python only bypasses proxies for
# hosts listed in no_proxy, and many no_proxy lists omit 127.0.0.1).
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(request: urllib.request.Request, timeout: float):
    if _is_loopback_url(request.full_url):
        return _DIRECT_OPENER.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


@dataclass
class Brain:
    """Any OpenAI-compatible /chat/completions endpoint that reads images (Ollama, LM Studio, a gateway...)."""
    url: str
    model: str
    key: str = ""
    timeout: float = 90
    history: list = field(default_factory=list)
    system: str = SYSTEM_PROMPT

    def _messages(self, question: str, jpeg: bytes | None) -> list:
        content = [{"type": "text", "text": question}]
        if jpeg:
            encoded = base64.b64encode(jpeg).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        return [{"role": "system", "content": self.system}, *self.history, {"role": "user", "content": content}]

    def ask(self, question: str, jpeg: bytes | None, on_text=None) -> str:
        """Send one question (+ screenshot) and return the full reply, calling on_text(partial) while it streams."""
        body = {
            "model": self.model,
            "messages": self._messages(question, jpeg),
            "temperature": 0.2,
            "max_tokens": 400,
            "stream": True,
        }
        # A real User-Agent: Cloudflare (which hosts the free brain) blocks the default "Python-urllib" one
        headers = {"Content-Type": "application/json", "User-Agent": f"Buddy/{VERSION} (macOS)"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        started = time.perf_counter()
        try:
            response = _open(request, self.timeout)
        except urllib.error.HTTPError as error:
            detail = error.read(600).decode(errors="replace")
            code, message = "", detail[:300]
            try:
                info = json.loads(detail).get("error") or {}
                code, message = info.get("code") or "", info.get("message") or message
            except (ValueError, AttributeError):
                pass
            raise BrainError(f"brain error {error.code}: {message}", error.code, code) from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            reason = getattr(error, "reason", error)
            raise BrainError(f"can't reach the brain at {self.url} ({reason})", None, "unreachable") from error
        with response:
            reply = "".join(self._read(response, on_text))
        logging.info("brain %s answered in %.1fs", self.model, time.perf_counter() - started)
        self.history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": reply},
        ]
        self.history = self.history[-2 * HISTORY_TURNS:]
        return reply

    @staticmethod
    def _read(response, on_text):
        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" not in content_type:
            data = json.loads(response.read().decode("utf-8", "replace"))
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if on_text:
                on_text(text)
            yield text
            return
        so_far = ""
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            piece = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
            if piece is not None and not isinstance(piece, str):
                piece = str(piece)
            if piece:
                so_far += piece
                if on_text:
                    on_text(so_far)
                yield piece


def brain_candidates(settings: dict) -> list[tuple[str, str, str]]:
    """(url, model, key) to try in order: the chosen brain, then the other free one if fallback is on.

    A brain with an API key (a company or paid model) never falls back to the free cloud brain, so
    screenshots meant for it are not sent anywhere else. Falling back to a local Ollama is always private.
    """
    url = settings.get("brain_url") or FREE_BRAIN_URL
    key = settings.get("brain_key", "")
    chosen = (url, settings.get("brain_model") or FREE_BRAIN_MODEL, key)
    candidates = [chosen]
    if settings.get("fallback", True):
        if url != FREE_BRAIN_URL and not key:
            candidates.append((FREE_BRAIN_URL, FREE_BRAIN_MODEL, ""))
        if url != LOCAL_BRAIN_URL:
            candidates.append((LOCAL_BRAIN_URL, LOCAL_BRAIN_MODEL, ""))
    return candidates


def ask_with_fallback(question: str, jpeg: bytes | None, settings: dict, history: list, on_text=None):
    """Ask the chosen brain; if it can't answer (offline, busy, out of free questions) try the next one.

    Returns (reply, brain_that_answered). Raises the chosen brain's BrainError if none could answer.
    """
    first_error = None
    system = SYSTEM_PROMPT + language_rule(settings.get("voice_language", ""))
    for url, model, key in brain_candidates(settings):
        brain = Brain(url, model, key, history=list(history), system=system)
        try:
            return brain.ask(question, jpeg, on_text), brain
        except BrainError as error:
            logging.warning("brain %s failed: %s", model, error)
            first_error = first_error or error
            if not error.worth_retrying_elsewhere:
                break
    raise first_error


def friendly_error(error: Exception) -> str:
    """What Buddy says when no brain could answer."""
    code = getattr(error, "code", "")
    if code == "daily_cap":
        return ("The free brain is out of questions for today. It's back tomorrow, or install Ollama "
                "for unlimited private answers: menu bar, then Brain.")
    if code == "rate_limited":
        return "That's a lot of questions in a minute. Give me a few seconds and ask again."
    if code == "unreachable" or "can't reach the brain" in str(error):
        return "I can't reach my brain. Check the internet, or set one up: menu bar, then Brain."
    return f"Something went wrong: {str(error)[:140]}"


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
