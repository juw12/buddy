"""Buddy Lite for Mac: the parts that don't touch AppKit (settings, prompt, reply parsing, brain, screen look).

Kept free of PyObjC so it can be unit-tested anywhere. The screen look uses the built-in macOS tools
`screencapture` and `sips`, so no image library is needed.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Buddy"
VERSION = "0.1.0"

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

DEFAULTS = {
    # Any OpenAI-compatible chat endpoint that accepts images. The default is a local Ollama.
    "brain_url": "http://localhost:11434/v1/chat/completions",
    "brain_model": "qwen3-vl:4b-instruct",
    "brain_key": "",
    "speak": True,
    "follow_mouse": True,
    "color": "Orange",
}

MAX_POINTS = 3
SCREENSHOT_MAX_SIDE = 1280
HISTORY_TURNS = 4

SYSTEM_PROMPT = """You are Buddy, a friendly expert tutor that lives next to the user's mouse cursor on a Mac.
You are given a screenshot of the user's current screen and their question. Your reply is read aloud.

Rules:
- Reply in 1 to 3 short, natural, spoken sentences. No markdown, no lists, no emojis, no URLs.
- Be concrete and refer to what is actually visible on the screen. Use Mac names (Command, Option, Finder, menu bar).
- Whenever the question is about where something is or how to do something on screen, you MUST point:
  put one tag per element (button, menu, tab, field, icon, text) at the very END of your reply,
  in the order the user should use them, at most 3 tags:
  [POINT:x,y:short label]
  x and y are the CENTER of the element in normalized coordinates from 0 to 1000,
  where 0,0 is the top-left and 1000,1000 is the bottom-right of the screenshot.
- If nothing needs pointing at, do not add any tag."""

POINT_PATTERN = re.compile(
    r"\[\s*POINT\s*:\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*(?::\s*([^\]]*))?\]",
    re.IGNORECASE,
)


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
def parse_reply(text: str) -> tuple[str, list[tuple[float, float, str]]]:
    """Split a model reply into (spoken_text, [(x_norm, y_norm, label), ...])."""
    points = []
    for match in POINT_PATTERN.finditer(text):
        x, y = float(match.group(1)), float(match.group(2))
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


@dataclass
class ScreenRect:
    """A display in Cocoa coordinates: origin at the bottom-left of the main display, in points."""
    x: float
    y: float
    width: float
    height: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


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
@dataclass
class Brain:
    """Any OpenAI-compatible /chat/completions endpoint that reads images (Ollama, LM Studio, a gateway...)."""
    url: str
    model: str
    key: str = ""
    timeout: float = 90
    history: list = field(default_factory=list)

    def _messages(self, question: str, jpeg: bytes | None) -> list:
        content = [{"type": "text", "text": question}]
        if jpeg:
            encoded = base64.b64encode(jpeg).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        return [{"role": "system", "content": SYSTEM_PROMPT}, *self.history, {"role": "user", "content": content}]

    def ask(self, question: str, jpeg: bytes | None, on_text=None) -> str:
        """Send one question (+ screenshot) and return the full reply, calling on_text(partial) while it streams."""
        body = {
            "model": self.model,
            "messages": self._messages(question, jpeg),
            "temperature": 0.2,
            "max_tokens": 400,
            "stream": True,
        }
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        started = time.perf_counter()
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            detail = error.read(300).decode(errors="replace")
            raise RuntimeError(f"brain error {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"can't reach the brain at {self.url} ({error.reason})") from error
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
            if piece:
                so_far += piece
                if on_text:
                    on_text(so_far)
                yield piece


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
