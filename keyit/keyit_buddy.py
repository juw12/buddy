"""
KeyIt Buddy - a free, local, screen-aware AI helper that lives next to your cursor.

Hold Ctrl+Alt+Space, ask a question out loud, release. Buddy:
  1. transcribes your voice locally (faster-whisper),
  2. screenshots the monitor under your cursor (only at that moment),
  3. asks a local vision model through Ollama (nothing leaves this PC),
  4. speaks the answer with the Windows voice, sentence by sentence while it streams,
  5. flies a pointer to the UI elements it mentions, in order (step-by-step tours).

Pointing protocol: the model appends tags  [POINT:x,y:label]  with x, y in 0..1000
normalized screenshot coordinates (the convention Qwen-VL models are trained on).
"""
import base64
import ctypes
import io
import json
import logging
import math
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes

import numpy as np

import keyit_core as core

DEFAULTS = {
    "buddy_enabled": False,
    "ollama_url": "http://localhost:11434",
    "buddy_model": "qwen3-vl:4b-instruct",   # the plain 4b tag is the slow "thinking" variant
    "whisper_model": "auto",
    "speech_language": "en-US",
    "buddy_voice": "",
    "buddy_speak": True,
    "screenshot_max_side": 1280,
    "buddy_cursor_always": True,      # Clicky-style: the buddy rests next to the mouse all the time
}
# "auto" = Whisper base.en if it can be downloaded/loaded, else the built-in Windows recognizer
WHISPER_MODELS = ["auto", "windows", "tiny.en", "base.en", "small.en", "base", "small"]
WHISPER_RATE = 16000
MAX_POINTS = 3
MODEL_OPTIONS = {"num_ctx": 8192}   # keep identical for warm-up and questions
TAP_SECONDS = 0.35            # Ctrl+Alt+Space shorter than this = type, longer = talk
CHIME_SKIP_SECONDS = 0.25     # drop the start of the recording so speech recognition doesn't hear the chime
SELECTION_MAX_CHARS = 4000
HISTORY_TURNS = 4

SYSTEM_PROMPT = """You are KeyIt Buddy, a friendly expert tutor that lives next to the user's mouse cursor on Windows.
You are given a screenshot of the user's current screen and their spoken question. Your reply is read aloud.

Rules:
- Reply in 1 to 3 short, natural, spoken sentences. No markdown, no lists, no emojis, no URLs.
- Be concrete and refer to what is actually visible on the screen.
- When pointing at something on screen helps (a button, menu, tab, field, icon, text), put one tag per
  element at the very END of your reply, in the order the user should use them, at most 3 tags:
  [POINT:x,y:short label]
  x and y are the CENTER of the element in normalized coordinates from 0 to 1000,
  where 0,0 is the top-left and 1000,1000 is the bottom-right of the screenshot.
- If nothing needs pointing at, do not add any tag."""

POINT_PATTERN = re.compile(
    r"\[\s*POINT\s*:\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*(?::\s*([^\]]*))?\]",
    re.IGNORECASE,
)
FINISHED_SENTENCE = re.compile(r"\s*(.+?[.!?]+)(?=\s)", re.DOTALL)


SLOW_THINKING_MODELS = {"qwen3-vl:4b": "qwen3-vl:4b-instruct", "qwen3-vl:latest": "qwen3-vl:4b-instruct"}


def buddy_settings(settings):
    for key, value in DEFAULTS.items():
        settings.setdefault(key, value)
    # The plain qwen3-vl tags "think" silently for minutes on a laptop CPU; use the instruct twin.
    settings["buddy_model"] = SLOW_THINKING_MODELS.get(settings["buddy_model"], settings["buddy_model"])
    return settings


# --------------------------------------------------------------------------- parsing
def parse_reply(text):
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


def to_screen(point, monitor):
    """Normalized 0..1000 point -> absolute screen pixel."""
    x, y, label = point
    return (
        int(monitor["left"] + x / 1000.0 * monitor["width"]),
        int(monitor["top"] + y / 1000.0 * monitor["height"]),
        label,
    )


# --------------------------------------------------------------------------- Ollama
class OllamaClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        # Never send local Ollama traffic through a corporate HTTP(S)_PROXY
        if host in ("localhost", "127.0.0.1", "::1"):
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        else:
            self.opener = urllib.request.build_opener()

    def _request(self, path, payload=None, timeout=10):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data else "GET",
        )
        return self.opener.open(request, timeout=timeout)

    def list_models(self):
        with self._request("/api/tags") as response:
            return [m["name"] for m in json.load(response).get("models", [])]

    def has_vision(self, model):
        try:
            with self._request("/api/show", {"model": model}) as response:
                info = json.load(response)
        except (urllib.error.URLError, OSError):
            return False
        if "vision" in info.get("capabilities", []):
            return True
        return bool(info.get("projector_info"))

    def chat_stream(self, model, messages, timeout=180):
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": False,                 # answer right away; silent reasoning took minutes on this laptop
            "keep_alive": "15m",
            "options": dict(MODEL_OPTIONS, temperature=0.2),
        }
        with self._request("/api/chat", payload, timeout=timeout) as response:
            for line in response:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
                if chunk.get("done"):
                    break

    def warm_up(self, model):
        """Load the model into memory so the first real question is fast."""
        # Same options as real questions, or Ollama reloads the model on the first question (~45 s here)
        payload = {"model": model, "messages": [], "keep_alive": "15m", "options": dict(MODEL_OPTIONS)}
        with self._request("/api/chat", payload, timeout=300) as response:
            response.read()


# --------------------------------------------------------------------------- speech in/out
class SpeechToText:
    def __init__(self, model_name):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(core.USER_DIR / "models"),
        )

    def transcribe(self, audio, sample_rate):
        audio = resample(audio, sample_rate, WHISPER_RATE)
        if audio.size < WHISPER_RATE * 0.3:
            return ""
        segments, _ = self.model.transcribe(audio, beam_size=1, vad_filter=False)
        return " ".join(segment.text.strip() for segment in segments).strip()


class WindowsSpeechToText:
    """Offline fallback: the speech recognizer built into Windows (System.Speech, no download)."""

    # One Recognize() pass per push-to-talk utterance. (Looping is unsafe: with wave-file input
    # System.Speech never returns null at end of stream and repeats the last phrase.)
    SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$culture = New-Object System.Globalization.CultureInfo('{culture}')
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$engine.SetInputToWaveFile('{path}')
$engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(3)
$engine.BabbleTimeout = [TimeSpan]::FromSeconds(0)
$engine.EndSilenceTimeout = [TimeSpan]::FromSeconds(1.5)
$engine.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromSeconds(1.5)
$result = $engine.Recognize()
$engine.Dispose()
if ($result -ne $null) {{ Write-Output $result.Text }}
"""

    def __init__(self, culture="en-US"):
        self.culture = culture

    @staticmethod
    def installed_languages():
        import subprocess

        script = ("Add-Type -AssemblyName System.Speech; "
                  "[System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | "
                  "ForEach-Object { $_.Culture.Name }")
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                capture_output=True, text=True, timeout=20, creationflags=0x08000000)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def transcribe(self, audio, sample_rate):
        import subprocess
        import tempfile
        import wave
        from pathlib import Path

        audio = resample(audio, sample_rate, WHISPER_RATE)
        if audio.size < WHISPER_RATE * 0.3:
            return ""
        pcm = np.clip(audio * 32767, -32768, 32767).astype("<i2")
        handle, wav_name = tempfile.mkstemp(suffix=".wav", prefix="keyit_")
        os_close(handle)
        try:
            with wave.open(wav_name, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(WHISPER_RATE)
                wav_file.writeframes(pcm.tobytes())
            script = self.SCRIPT.format(culture=self.culture, path=wav_name.replace("'", "''"))
            result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                    capture_output=True, timeout=30, creationflags=0x08000000)
            return result.stdout.decode("utf-8", "replace").strip()
        finally:
            Path(wav_name).unlink(missing_ok=True)       # the recording is never kept


def os_close(handle):
    import os

    os.close(handle)


def make_speech_engine(choice, language, on_status=lambda text: None):
    """Return (engine, description). 'auto' tries Whisper then falls back to Windows."""
    if choice == "windows":
        return WindowsSpeechToText(language), f"Windows speech recognizer ({language})"
    model = "base.en" if choice == "auto" else choice
    try:
        on_status(f"Loading Whisper {model} (first use downloads ~150 MB)…")
        return SpeechToText(model), f"Whisper {model}"
    except Exception as error:
        if choice != "auto":
            raise
        logging.warning("Whisper unavailable (%s); using Windows speech recognizer", error)
        return WindowsSpeechToText(language), f"Windows speech recognizer ({language}) – Whisper download blocked"


def resample(audio, source_rate, target_rate):
    audio = np.asarray(audio, dtype=np.float32)
    if source_rate == target_rate or audio.size == 0:
        return audio
    width = max(1, int(round(source_rate / target_rate)))
    smoothed = np.convolve(audio, np.ones(width) / width, mode="same")   # crude anti-alias
    positions = np.arange(0, audio.size, source_rate / target_rate)
    return np.interp(positions, np.arange(audio.size), smoothed).astype(np.float32)


class Speaker:
    """Windows SAPI voice on its own COM thread. say() queues, stop() interrupts."""

    SVSF_ASYNC = 1
    SVSF_PURGE = 2

    def __init__(self, voice_name=""):
        self.jobs = queue.SimpleQueue()
        self.voice_name = voice_name
        self.voices = []
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="keyit-tts", daemon=True).start()
        self._ready.wait(30)   # first run in the exe generates COM bindings (several seconds)

    def _run(self):
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self.voice = comtypes.client.CreateObject("SAPI.SpVoice")
        tokens = self.voice.GetVoices()
        self.voices = [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
        self._select_voice(self.voice_name)
        self._ready.set()
        while True:
            kind, value = self.jobs.get()
            if kind == "say":
                self.voice.Speak(value, self.SVSF_ASYNC)
            elif kind == "stop":
                self.voice.Speak("", self.SVSF_ASYNC | self.SVSF_PURGE)
            elif kind == "voice":
                self._select_voice(value)

    def _select_voice(self, name):
        if not name:
            return
        tokens = self.voice.GetVoices()
        for i in range(tokens.Count):
            if name.lower() in tokens.Item(i).GetDescription().lower():
                self.voice.Voice = tokens.Item(i)
                return

    def say(self, text):
        if text.strip():
            self.jobs.put(("say", text))

    def stop(self):
        self.jobs.put(("stop", None))

    def set_voice(self, name):
        self.jobs.put(("voice", name))


# --------------------------------------------------------------------------- screen
class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def cursor_position():
    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


MONITOR_DEFAULTTONEAREST = 2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


class physical_pixels:
    """Run Win32 calls on this thread in physical pixels.

    Never switch the whole process to DPI awareness (mss does that): a DPI-unaware app
    like Buddy would see all its windows shrink the moment it happens.
    """

    def __enter__(self):
        self.previous = None
        try:
            set_context = ctypes.windll.user32.SetThreadDpiAwarenessContext
        except AttributeError:
            return self
        set_context.restype = ctypes.c_void_p
        set_context.argtypes = [ctypes.c_void_p]
        self.set_context = set_context
        self.previous = set_context(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
        return self

    def __exit__(self, *exc):
        if self.previous:
            self.set_context(ctypes.c_void_p(self.previous))
        return False


def _monitor_rect(x, y):
    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    handle = user32.MonitorFromPoint(POINT(x, y), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(handle, ctypes.byref(info))
    rect = info.rcMonitor
    return rect.left, rect.top, rect.right, rect.bottom


def monitor_at(x, y):
    left, top, right, bottom = _monitor_rect(int(x), int(y))
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def monitor_under_cursor():
    """Monitor rect in this process's own coordinates (what Tk and GetCursorPos use),
    plus its physical-pixel rect for screenshots."""
    left, top, right, bottom = _monitor_rect(*cursor_position())
    with physical_pixels():
        physical = _monitor_rect(*cursor_position())
    return {"left": left, "top": top, "width": right - left, "height": bottom - top,
            "physical": physical}


def capture_monitor(monitor, max_side):
    """Screenshot one monitor at full resolution -> (PNG bytes for the model, PIL image)."""
    from PIL import Image, ImageGrab

    bbox = monitor.get("physical") or (monitor["left"], monitor["top"],
                                       monitor["left"] + monitor["width"],
                                       monitor["top"] + monitor["height"])
    image = ImageGrab.grab(bbox=tuple(bbox), all_screens=True).convert("RGB")
    scale = min(1.0, max_side / max(image.size))
    if scale < 1.0:
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue(), image


# --------------------------------------------------------------------------- overlay
class BuddyOverlay:
    """Transparent, click-through, never-focused window that draws the buddy next to the mouse.

    Like Clicky: with always_on the little orange pointer follows the mouse all the time (springy);
    otherwise it only shows up while you ask. Listening = waveform, thinking = spinner,
    answering = text bubble, pointing = it flies to the target and then springs back to the mouse.
    """

    KEY_COLOR = "#010203"
    ORANGE = "#ff8424"
    INK = "#1f2130"

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOPMOST = 0x00000008
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4

    CURSOR_OFFSET = (16, 18)       # sit just below-right of the real mouse pointer
    SPRING = 0.22                  # pull towards the mouse per frame
    DAMPING = 0.62                 # velocity kept per frame (a little overshoot = springy)
    FRAME_MS = 16
    IDLE_POLL_MS = 50
    FOLLOW_MODES = ("idle", "listening", "thinking", "talking")

    def __init__(self, root, always_on=False):
        import tkinter as tk

        self.root = root
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.config(bg=self.KEY_COLOR)
        self.top.attributes("-transparentcolor", self.KEY_COLOR)
        self.canvas = tk.Canvas(self.top, bg=self.KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.top.update_idletasks()
        self.hwnd = int(self.top.wm_frame(), 16)
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(self.hwnd, self.GWL_EXSTYLE)
        style |= self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT | self.WS_EX_TOOLWINDOW
        style |= self.WS_EX_NOACTIVATE | self.WS_EX_TOPMOST
        user32.SetWindowLongW(self.hwnd, self.GWL_EXSTYLE, style)
        self.monitor = None
        self.position = (0.0, 0.0)
        self.velocity = (0.0, 0.0)
        self.mode = None               # None (hidden), idle/listening/thinking/talking (follow), tour, shot
        self.always_on = always_on
        self._text = None
        self._mode_started = time.perf_counter()
        self._dirty = False
        self._animation = None
        self._hide_job = None
        self._frame_job = None
        self._visible = False
        self._hide_window()
        if always_on:
            self.root.after(300, self.idle)

    # -- window
    def _show_window(self):
        if not self._visible:
            ctypes.windll.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
            self._visible = True

    def _hide_window(self):
        ctypes.windll.user32.ShowWindow(self.hwnd, self.SW_HIDE)
        self._visible = False

    def attach(self, monitor):
        if monitor != self.monitor:
            if self.monitor is not None:       # keep the buddy where it is on screen
                x, y = self.position
                self.position = (x + self.monitor["left"] - monitor["left"],
                                 y + self.monitor["top"] - monitor["top"])
            self.monitor = monitor
            self.top.geometry(f"{monitor['width']}x{monitor['height']}+{monitor['left']}+{monitor['top']}")
            self.top.update_idletasks()
        self._show_window()

    def local(self, x, y):
        return x - self.monitor["left"], y - self.monitor["top"]

    def _follow_monitor(self):
        """Attach to the monitor under the mouse; returns the mouse position."""
        x, y = cursor_position()
        monitor = self.monitor
        if monitor is None or not (monitor["left"] <= x < monitor["left"] + monitor["width"]
                                   and monitor["top"] <= y < monitor["top"] + monitor["height"]):
            self.attach(monitor_under_cursor())
        return x, y

    def _cursor_spot(self):
        x, y = self.local(*self._follow_monitor())
        dx, dy = self.CURSOR_OFFSET
        width, height = self.monitor["width"], self.monitor["height"]
        return min(max(x + dx, 0), width - 30), min(max(y + dy, 0), height - 30)

    # -- drawing
    def _draw_arrow(self, x, y):
        arrow = [x, y, x + 6, y + 26, x + 12, y + 18, x + 24, y + 22]
        self.canvas.create_polygon(arrow, fill=self.ORANGE, outline=self.INK, width=2, joinstyle="round")

    def _draw_wave(self, x, y, seconds):
        cx, cy = x + 10, y + 12
        self.canvas.create_oval(cx - 17, cy - 15, cx + 17, cy + 15, fill=self.INK, outline=self.ORANGE, width=2)
        for index in range(5):
            height = 3 + 8 * abs(math.sin(seconds * 7 + index * 0.9))
            bar_x = cx - 10 + index * 5
            self.canvas.create_line(bar_x, cy - height, bar_x, cy + height,
                                    fill=self.ORANGE, width=3, capstyle="round")

    def _draw_spinner(self, x, y, seconds):
        cx, cy = x + 10, y + 12
        self.canvas.create_oval(cx - 14, cy - 14, cx + 14, cy + 14, fill=self.INK, outline="")
        self.canvas.create_arc(cx - 9, cy - 9, cx + 9, cy + 9, start=-seconds * 360 % 360, extent=270,
                               style="arc", outline=self.ORANGE, width=3)

    def _draw(self, x, y, text=None, pulse=0.0):
        canvas = self.canvas
        canvas.delete("all")
        if pulse:
            radius = 18 + 10 * pulse
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline=self.ORANGE, width=2)
        self._draw_arrow(x, y)
        if text:
            self._bubble(x + 28, y + 24, text)

    def _render(self):
        self.canvas.delete("all")
        x, y = self.position
        seconds = time.perf_counter() - self._mode_started
        if self.mode == "listening":
            self._draw_wave(x, y, seconds)
        elif self.mode == "thinking":
            self._draw_spinner(x, y, seconds)
        else:
            self._draw_arrow(x, y)
        if self._text:
            self._bubble(x + 28, y + 24, self._text)

    def _bubble(self, x, y, text):
        canvas = self.canvas
        item = canvas.create_text(x + 12, y + 8, text=text, anchor="nw", width=340,
                                  fill="white", font=("Segoe UI", 11))
        left, top, right, bottom = canvas.bbox(item)
        width_limit = self.monitor["width"]
        height_limit = self.monitor["height"]
        shift_x = min(0, width_limit - 8 - (right + 12))
        shift_y = min(0, height_limit - 8 - (bottom + 8))
        canvas.move(item, shift_x, shift_y)
        left, top, right, bottom = canvas.bbox(item)
        background = canvas.create_rectangle(left - 12, top - 8, right + 12, bottom + 8,
                                             fill=self.INK, outline=self.ORANGE, width=2)
        canvas.tag_lower(background, item)

    # -- the follow loop (spring towards the mouse)
    def _frame(self):
        self._frame_job = None
        if self.mode not in self.FOLLOW_MODES:
            return
        target_x, target_y = self._cursor_spot()
        x, y = self.position
        vx, vy = self.velocity
        vx = vx * self.DAMPING + (target_x - x) * self.SPRING
        vy = vy * self.DAMPING + (target_y - y) * self.SPRING
        x, y = x + vx, y + vy
        settled = (abs(target_x - x) < 0.5 and abs(target_y - y) < 0.5
                   and abs(vx) < 0.3 and abs(vy) < 0.3)
        if settled:
            x, y, vx, vy = target_x, target_y, 0.0, 0.0
        moved = (x, y) != self.position
        self.position, self.velocity = (x, y), (vx, vy)
        animated = self.mode in ("listening", "thinking")
        if animated or moved or self._dirty:
            self._render()
            self._dirty = False
        busy = animated or not settled
        self._frame_job = self.root.after(self.FRAME_MS if busy else self.IDLE_POLL_MS, self._frame)

    def _set_mode(self, mode, text=None):
        self._cancel_jobs()
        if self.mode not in self.FOLLOW_MODES and self.mode != "tour":
            self._follow_monitor()                     # appear right at the mouse, no fly-in
            self.position = self._cursor_spot()
            self.velocity = (0.0, 0.0)
        if mode != self.mode:
            self._mode_started = time.perf_counter()
        self.mode = mode
        self._text = text
        self._dirty = True
        self._show_window()
        self._frame()

    def _cancel_jobs(self):
        for job in (self._animation, self._hide_job, self._frame_job):
            if job:
                self.root.after_cancel(job)
        self._animation = self._hide_job = self._frame_job = None

    # -- states
    def set_always_on(self, enabled):
        self.always_on = bool(enabled)
        if self.always_on and self.mode is None:
            self.idle()
        elif not self.always_on and self.mode == "idle":
            self.hide()

    def idle(self):
        if self.always_on:
            self._set_mode("idle")
        else:
            self.hide()

    def listening(self, monitor, cursor):
        self.attach(monitor)
        self._set_mode("listening")

    def thinking(self, text="Thinking\u2026"):
        self._set_mode("thinking", text)

    def clear_for_screenshot(self):
        self._cancel_jobs()
        self.mode = "shot"
        self.canvas.delete("all")
        self.canvas.update_idletasks()

    def say(self, text):
        if self.mode in self.FOLLOW_MODES or self.mode in (None, "shot"):
            if self.mode == "talking":
                self._text = text                      # streaming: just swap the words
                self._dirty = True
            else:
                self._set_mode("talking", text)
        elif self.monitor:
            self._draw(*self.position, text)

    def tour(self, screen_points, answer_text, dwell_ms=1800):
        """Fly to each (x, y, label) in order, rest there with the answer, then spring back to the mouse."""
        self._cancel_jobs()
        if screen_points:
            first_x, first_y = screen_points[0][:2]
            monitor = self.monitor
            if monitor is None or not (monitor["left"] <= first_x < monitor["left"] + monitor["width"]
                                       and monitor["top"] <= first_y < monitor["top"] + monitor["height"]):
                self.attach(monitor_at(first_x, first_y))
        self._show_window()
        self.mode = "tour"
        self.velocity = (0.0, 0.0)
        targets = [(self.local(x, y), label) for x, y, label in screen_points]

        def fly(index):
            if index >= len(targets):
                self._draw(*self.position, answer_text)
                self.hide_later(9000)
                return
            (tx, ty), label = targets[index]
            sx, sy = self.position
            # Curved path: control point lifted perpendicular to the straight line
            mx, my = (sx + tx) / 2, (sy + ty) / 2
            dx, dy = tx - sx, ty - sy
            cx, cy = mx - dy * 0.25, my + dx * 0.25
            duration = min(0.9, 0.35 + math.hypot(dx, dy) / 2500)
            start = time.perf_counter()
            caption = f"{index + 1}. {label}" if len(targets) > 1 and label else label or None

            def step():
                t = min(1.0, (time.perf_counter() - start) / duration)
                eased = 1 - (1 - t) ** 3
                x = (1 - eased) ** 2 * sx + 2 * (1 - eased) * eased * cx + eased ** 2 * tx
                y = (1 - eased) ** 2 * sy + 2 * (1 - eased) * eased * cy + eased ** 2 * ty
                self.position = (x, y)
                self._draw(x, y, caption if t >= 1.0 else None)
                if t < 1.0:
                    self._animation = self.root.after(16, step)
                else:
                    self._animation = self.root.after(dwell_ms, lambda: fly(index + 1))

            step()

        fly(0)

    def hide_later(self, delay_ms):
        if self._hide_job:
            self.root.after_cancel(self._hide_job)
        self._hide_job = self.root.after(delay_ms, self.hide)

    def hide(self):
        """Done talking: back to resting by the mouse (always on), or gone."""
        self._cancel_jobs()
        self._text = None
        if self.always_on:
            self._set_mode("idle")
            return
        self.mode = None
        self.canvas.delete("all")
        self._hide_window()


# --------------------------------------------------------------------------- typing instead of talking
def _uia():
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as uia

    return uia, comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)


def uia_selection(element, uia, max_chars):
    """Selected text of one UI Automation element ('' if it has no text pattern or no selection)."""
    if not element:
        return ""
    pattern = element.GetCurrentPattern(uia.UIA_TextPatternId)
    if not pattern:
        return ""
    ranges = pattern.QueryInterface(uia.IUIAutomationTextPattern).GetSelection()
    parts = []
    for index in range(ranges.Length):
        text = ranges.GetElement(index).GetText(max_chars)
        if text and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)[:max_chars]


def selected_text(max_chars=None):
    """Text highlighted in the app you're using, read through Windows UI Automation.

    No Ctrl+C and no clipboard: Ctrl+C would stop whatever runs in a terminal and overwrite your clipboard.
    Works where apps expose their text (browsers, Office, WPF/UWP apps...); returns "" otherwise.
    """
    max_chars = max_chars or SELECTION_MAX_CHARS
    try:
        uia, automation = _uia()
        return uia_selection(automation.GetFocusedElement(), uia, max_chars)
    except Exception:
        logging.debug("Could not read the selected text", exc_info=True)
        return ""


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", RECT)]


def edit_control_selection(hwnd, max_chars=None):
    """Selected text of a classic text box (Win32 / WinForms edit control), asked from the control itself."""
    max_chars = max_chars or SELECTION_MAX_CHARS
    WM_GETTEXT, WM_GETTEXTLENGTH, EM_GETSEL = 0x000D, 0x000E, 0x00B0
    SMTO_ABORTIFHUNG = 0x0002
    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    class_name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, class_name, 64)
    if "EDIT" not in class_name.value.upper():
        return ""

    def send(message, wparam=0, lparam=0):
        result = ctypes.c_size_t()
        if not user32.SendMessageTimeoutW(hwnd, message, wparam, lparam, SMTO_ABORTIFHUNG, 300,
                                          ctypes.byref(result)):
            return None
        return result.value

    selection = send(EM_GETSEL)
    length = send(WM_GETTEXTLENGTH)
    if not selection or not length:
        return ""
    start, end = selection & 0xFFFF, (selection >> 16) & 0xFFFF
    if end <= start:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    if send(WM_GETTEXT, length + 1, ctypes.addressof(buffer)) is None:
        return ""
    return buffer.value[start:end].strip()[:max_chars]


def selected_text_from_edit_control(max_chars=None):
    """Fallback for classic text boxes: the focused control of the app in front."""
    try:
        user32 = ctypes.windll.user32
        thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not user32.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.hwndFocus:
            return ""
        return edit_control_selection(info.hwndFocus, max_chars)
    except Exception:
        logging.debug("Could not read the edit control's selection", exc_info=True)
        return ""


def selected_text_quick(timeout_s=0.8):
    """selected_text(), but never wait long on a busy app."""
    result = {}

    def read():
        result["text"] = selected_text() or selected_text_from_edit_control()

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(timeout_s)
    return result.get("text", "")


class AskBox:
    """Small 'Ask Buddy' box that pops up at the mouse when you tap Ctrl+Alt+Space."""

    current = None
    ORANGE = "#ff8424"
    INK = "#1f2130"
    FIELD = "#2b2e40"
    MUTED = "#a9acbd"

    def __init__(self, root, monitor, cursor, selection, on_submit):
        import tkinter as tk

        AskBox.close_current()
        AskBox.current = self
        self.root = root
        self.selection = selection
        self.on_submit = on_submit
        self.top = top = tk.Toplevel(root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.config(bg=self.ORANGE)
        inner = tk.Frame(top, bg=self.INK, padx=12, pady=9)
        inner.pack(padx=2, pady=2)
        tk.Label(inner, text="Ask Buddy", fg=self.ORANGE, bg=self.INK,
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        if selection:
            preview = " ".join(selection.split())
            if len(preview) > 70:
                preview = preview[:70] + "\u2026"
            tk.Label(inner, text=f"About the text you highlighted: \u201c{preview}\u201d", fg=self.MUTED,
                     bg=self.INK, font=("Segoe UI", 9), wraplength=400, justify="left").pack(anchor="w", pady=(2, 0))
        self.var = tk.StringVar()
        self.entry = tk.Entry(inner, textvariable=self.var, width=44, font=("Segoe UI", 11), bg=self.FIELD,
                              fg="white", insertbackground="white", relief="flat")
        self.entry.pack(fill="x", pady=(6, 0), ipady=4)
        hint = "Enter to ask  \u00b7  Esc to close"
        if selection:
            hint += "  \u00b7  just Enter = explain it"
        tk.Label(inner, text=hint, fg=self.MUTED, bg=self.INK, font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))
        self.entry.bind("<Return>", self.submit)
        self.entry.bind("<Escape>", lambda _event: self.close())
        self.entry.bind("<FocusOut>", lambda _event: self.root.after(150, self._close_if_unfocused))

        top.update_idletasks()
        width, height = top.winfo_reqwidth(), top.winfo_reqheight()
        right = monitor["left"] + monitor["width"]
        bottom = monitor["top"] + monitor["height"]
        x = min(max(cursor[0] + 18, monitor["left"] + 4), right - width - 8)
        y = cursor[1] + 26
        if y + height > bottom - 8:
            y = cursor[1] - height - 12             # no room below the mouse: open above it
        top.geometry(f"+{int(x)}+{int(max(y, monitor['top'] + 4))}")
        self._take_focus()

    def _take_focus(self):
        """Windows only lets the app in front hand out keyboard focus, so borrow its input queue briefly."""
        self.top.update_idletasks()
        hwnd = int(self.top.wm_frame(), 16)
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        foreground = user32.GetForegroundWindow()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        this_thread = kernel32.GetCurrentThreadId()
        attached = bool(foreground_thread and foreground_thread != this_thread
                        and user32.AttachThreadInput(foreground_thread, this_thread, True))
        try:
            # A key event sent by this app makes Windows accept it as "the app the user is talking to"
            # (an unused key code, ignored by Buddy's keyboard hook and by every app).
            user32.keybd_event(core.VK_MENU_MASK, 0, 0, 0)
            user32.keybd_event(core.VK_MENU_MASK, 0, 2, 0)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(foreground_thread, this_thread, False)
        self.top.focus_force()
        self.entry.focus_force()
        if user32.GetForegroundWindow() != hwnd and not getattr(self, "_retried", False):
            self._retried = True
            self.root.after(120, self._take_focus)          # Windows sometimes needs a second try

    def _close_if_unfocused(self):
        try:
            if self.top.winfo_exists() and self.top.focus_get() is None:
                self.close()
        except Exception:
            self.close()

    def submit(self, _event=None):
        question = self.var.get().strip()
        if not question and self.selection:
            question = "Explain this."
        if not question:
            return
        self.close()
        self.on_submit(question)

    def close(self):
        if AskBox.current is self:
            AskBox.current = None
        try:
            self.top.destroy()
        except Exception:
            pass

    @classmethod
    def close_current(cls):
        if cls.current is not None:
            cls.current.close()


# --------------------------------------------------------------------------- controller
class Buddy:
    """Push-to-talk flow. Call press()/release() from the hook; ask_text() from the GUI."""

    def __init__(self, settings, root, post, on_status=None, overlay=None, context_provider=None, sound=None):
        self.settings = buddy_settings(settings)
        self.root = root
        self.post = post                      # thread-safe "run on UI thread"
        self.on_status = on_status or (lambda text: None)
        self.overlay = overlay or BuddyOverlay(root)   # e.g. the Pips pet when hosted
        self.context_provider = context_provider       # extra facts about the user (optional)
        self.sound = sound                             # play_effect(name) from the KeyIt player
        self.speaker = Speaker(self.settings["buddy_voice"])
        self.stt = None
        self.stt_name = None
        self.mic = None
        self.last_reply = None
        self.last_timing = None
        self.monitor = None
        self.history = []
        self.busy = threading.Lock()
        self.generation = 0
        threading.Thread(target=self._prepare, daemon=True).start()

    # -- setup
    def client(self):
        return OllamaClient(self.settings["ollama_url"])

    def chime(self, name):
        """Buddy's wake-up / got-it sounds (Settings: 'Chime when Buddy wakes up')."""
        if self.sound is None or not self.settings.get("buddy_sounds", True):
            return False
        try:
            self.sound(name)
        except Exception:
            return False
        return True

    def _prepare(self):
        try:
            self.on_status("Loading model into memory…")
            self.client().warm_up(self.settings["buddy_model"])
            self.on_status("Ready – Ctrl+Alt+Space: tap to type, hold to talk.")
        except Exception as error:
            self.on_status(self._explain_error(error).replace("\n", " "))

    def _ensure_stt(self):
        wanted = (self.settings["whisper_model"], self.settings["speech_language"])
        if self.stt is None or self.stt_name != wanted:
            self.stt, description = make_speech_engine(*wanted, on_status=self.on_status)
            self.stt_name = wanted
            self.on_status(f"Speech: {description}")

    # -- push to talk (called from the keyboard hook thread)
    def press(self):
        from keyit_recorder import MicCapture

        self.cancel()
        self.pressed_at = time.perf_counter()
        self.chimed = self.chime("buddy_wake")
        self.monitor = monitor_under_cursor()
        cursor = cursor_position()
        try:
            self.mic = MicCapture()
            self.mic.start()
        except Exception as error:
            self.mic = None
            self.mic_error = error
            return
        self.mic_error = None
        mic = self.mic

        def show_listening():
            if self.mic is mic:                       # still held: it's a voice question
                self.overlay.listening(self.monitor, cursor)

        self.post(lambda: self.root.after(int(TAP_SECONDS * 1000), show_listening))

    def release(self):
        """End of Ctrl+Alt+Space: a quick tap opens the type box, a hold sends what you said."""
        held = time.perf_counter() - getattr(self, "pressed_at", 0.0)
        mic, self.mic = self.mic, None
        if held < TAP_SECONDS:
            if mic is not None:
                try:
                    mic.stop()
                except Exception:
                    pass
            threading.Thread(target=self._open_ask_box, daemon=True).start()
            return
        if mic is None:
            error = getattr(self, "mic_error", None) or "no microphone"
            self.post(self.overlay.thinking, f"Mic error: {error}\nTap Ctrl+Alt+Space to type instead.")
            self.post(self.overlay.hide_later, 5000)
            return
        audio, _ = mic.stop()
        if getattr(self, "chimed", False):
            skip = int(CHIME_SKIP_SECONDS * mic.sample_rate)
            if audio.size > 2 * skip:
                audio = audio[skip:]
        self.chime("buddy_got_it")
        self.post(self.overlay.thinking, "Transcribing\u2026")
        threading.Thread(target=self._from_voice, args=(audio, mic.sample_rate), daemon=True).start()

    def _open_ask_box(self):
        """Read the highlighted text (if any) before the box takes focus, then open the box at the mouse."""
        selection = selected_text_quick()
        self.post(self.open_ask_box, selection, False)       # the hotkey already chimed

    def open_ask_box(self, selection="", chime=True):
        if chime:
            self.chime("buddy_wake")
        AskBox(self.root, monitor_under_cursor(), cursor_position(), selection,
               on_submit=lambda question: self.ask_text(question, selection=selection))

    def cancel(self):
        self.generation += 1
        self.speaker.stop()
        if self.mic:
            try:
                self.mic.stop()
            except Exception:
                pass
            self.mic = None
        self.post(self.overlay.hide)
        self.post(AskBox.close_current)

    # -- question flow (worker threads)
    def _from_voice(self, audio, sample_rate):
        try:
            self._ensure_stt()
            question = self.stt.transcribe(audio, sample_rate)
        except Exception as error:
            self.post(self.overlay.thinking, f"Speech recognition failed: {error}")
            return
        if not question:
            self.post(self.overlay.thinking, "I didn't catch that – try again?")
            self.post(self.overlay.hide_later, 2500)
            return
        self._ask(question)

    def ask_text(self, question, monitor=None, selection=""):
        self.monitor = monitor or monitor_under_cursor()
        cursor = cursor_position()

        def start():
            self.overlay.attach(self.monitor)
            self.overlay.position = self.overlay.local(*cursor)
            threading.Thread(target=self._ask, args=(question, selection), daemon=True).start()

        self.post(start)

    def _ask(self, question, selection=""):
        if not self.busy.acquire(blocking=False):
            return
        generation = self.generation
        try:
            self._ask_locked(question, generation, selection)
        except Exception as error:
            logging.exception("Buddy error")
            self.post(self.overlay.thinking, self._explain_error(error))
            self.post(self.overlay.hide_later, 12000)
        finally:
            self.busy.release()

    def _explain_error(self, error):
        """Plain-language reason Buddy can't answer (instead of 'HTTP Error 404')."""
        model = self.settings["buddy_model"]
        if isinstance(error, urllib.error.HTTPError) and error.code == 404:
            return (f"I can't find my brain: Ollama has no '{model}' model.\n"
                    f"Fix: reinstall Ollama from ollama.com, then run:  ollama pull {model}")
        if isinstance(error, urllib.error.URLError):
            return "Ollama isn't running, so I can't think.\nStart Ollama from the Start menu and ask again."
        return f"Buddy error: {error}"

    def _system_prompt(self):
        prompt = SYSTEM_PROMPT
        if self.context_provider:
            try:
                context = self.context_provider()
            except Exception:
                logging.exception("Buddy context provider failed")
                context = ""
            if context:
                prompt += ("\n\nWhat you know about the user right now (from their focus tracker). "
                           "Use it only when the question is about their day, focus or habits:\n" + context)
        return prompt

    def _ask_locked(self, question, generation, selection=""):
        started = time.perf_counter()
        # Remove the buddy from the screen before taking the screenshot
        done = threading.Event()
        self.post(lambda: (self.overlay.clear_for_screenshot(), done.set()))
        done.wait(1.0)
        time.sleep(0.05)
        png, image = capture_monitor(self.monitor, self.settings["screenshot_max_side"])
        self.post(self.overlay.thinking, f"“{question}”\nLooking at your screen… (about a minute)")

        messages = [{"role": "system", "content": self._system_prompt()}]
        messages += self.history[-2 * HISTORY_TURNS:]
        content = question
        if selection:
            content += f"\n\nThe text I highlighted on screen:\n\"\"\"\n{selection[:SELECTION_MAX_CHARS]}\n\"\"\""
        messages.append({
            "role": "user",
            "content": content,
            "images": [base64.b64encode(png).decode()],
        })

        reply, spoken_upto = "", 0
        first_token_at = None
        for chunk in self.client().chat_stream(self.settings["buddy_model"], messages):
            if generation != self.generation:
                return                                            # cancelled
            if first_token_at is None:
                first_token_at = time.perf_counter()
            reply += chunk
            visible = reply.split("[")[0]                         # never speak tags
            # Speak each sentence as soon as it is complete (followed by whitespace)
            while True:
                match = FINISHED_SENTENCE.match(visible, spoken_upto)
                if not match:
                    break
                if self.settings["buddy_speak"]:
                    self.speaker.say(parse_reply(match.group(1))[0])
                spoken_upto = match.end()
            self.post(self.overlay.say, parse_reply(reply)[0] or "…")

        spoken, points = parse_reply(reply)
        remainder = parse_reply(reply.split("[")[0][spoken_upto:])[0]
        if remainder and self.settings["buddy_speak"]:
            self.speaker.say(remainder)
        screen_points = [to_screen(p, self.monitor) for p in points]
        self.history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": reply},
        ]
        total = time.perf_counter() - started
        first = (first_token_at or time.perf_counter()) - started
        self.last_timing = {"first_token_s": first, "total_s": total, "points": len(points)}
        self.on_status(f"Answered in {total:.1f}s (first words after {first:.1f}s)")
        if screen_points:
            self.post(self.overlay.tour, screen_points, spoken)
        else:
            self.post(self.overlay.say, spoken)
            self.post(self.overlay.hide_later, 9000)
        self.last_reply = {"question": question, "reply": reply, "spoken": spoken, "points": screen_points}
