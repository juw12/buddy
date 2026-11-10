"""
KeyIt Buddy - a free, local, screen-aware AI helper that lives next to your cursor.

Hold Ctrl+Alt+Space, ask a question out loud, release. Buddy:
  1. transcribes your voice locally (faster-whisper),
  2. screenshots the monitor under your cursor (only at that moment),
  3. asks a local vision model (on the laptop GPU when possible, else Ollama; nothing leaves this PC),
  4. speaks the answer with the Windows voice, sentence by sentence while it streams,
  5. flies a pointer to the UI elements it mentions, in order (step-by-step tours), snapped onto
     the exact button/field through Windows UI Automation.

Pointing protocol: the model appends tags  [POINT:x,y:label]  with x, y in 0..1000
normalized screenshot coordinates (the convention Qwen-VL models are trained on).
"""
import base64
import ctypes
import http.client
import io
import json
import logging
import math
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes

import numpy as np

import buddy_island
import buddy_overlay
import keyit_actions
import keyit_core as core
import keyit_gpu
import keyit_script
import keyit_speech
import keyit_targets
import keyit_words

DEFAULTS = {
    "buddy_enabled": False,
    "ollama_url": "http://localhost:11434",
    "buddy_model": "qwen3-vl:4b-instruct",   # the plain 4b tag is the slow "thinking" variant
    "whisper_model": "auto",          # old setting, read once by keyit_speech.migrate
    "speech_model": "auto",           # Voice input -> Speech model: one of keyit_speech.CHOICES
    "dictation": True,                # Ctrl+Alt+D types what you say into the app in front
    "voice_cleanup": True,            # drop um/uh, apply spoken corrections ("no wait")
    "my_words": "",                   # Voice input -> My words: spellings + spoken shortcuts (keyit_words)
    "mic_device": "",                 # Voice input -> Microphone: "" = the Windows default
    "speech_language": "en-US",
    "buddy_voice": "",
    "buddy_speak": True,
    "screenshot_max_side": 1280,
    "buddy_cursor_always": True,      # Clicky-style: the buddy rests next to the mouse all the time
    "buddy_color": buddy_overlay.DEFAULT_COLOR,   # Settings -> Look: colour of the pointer and bubbles
    "buddy_icon": buddy_overlay.DEFAULT_ICON,     # Settings -> Look: arrow, pointer, plane, dot, star, heart, face
    "buddy_island": True,             # Buddy Island: a notch at the top of the screen (click = type, hold = talk)
    "buddy_engine": "auto",           # "free": Buddy's free cloud brain; "maas": company MAAS (smart, off-PC);
                                      # "auto": GPU when possible, else Ollama, else the free cloud brain;
                                      # "cpu": always Ollama
}
# "auto" = Whisper base.en if it can be downloaded/loaded, else the built-in Windows recognizer
WHISPER_MODELS = ["auto", "windows", "tiny.en", "base.en", "small.en", "base", "small"]
WHISPER_RATE = 16000
MAX_POINTS = 3
# Keep identical for warm-up, look-ahead and questions, or Ollama reloads the model (~15-45 s).
# Half the cores = the fast ones on hybrid laptop CPUs: measured as fast as all 8 here (84 s vs 88 s
# for a screenshot) while leaving the other half free, so the PC stays usable while Buddy thinks.
MODEL_OPTIONS = {"num_ctx": 8192, "num_thread": max(2, (os.cpu_count() or 4) // 2)}
KEEP_ALIVE = "30m"            # how long Ollama keeps the model in memory after the last question
LOOK_AHEAD_MAX_AGE = 300      # s; a look-ahead screenshot older than this is not reused
TAP_SECONDS = 0.35            # Ctrl+Alt+Space shorter than this = type, longer = talk
DICTATION_MAX_SECONDS = 300   # hands-free dictation stops by itself after 5 minutes
DICTATION_HISTORY = 20        # last dictations kept in memory (never on disk) for 'type it again'
CHIME_SKIP_SECONDS = 0.25     # drop the start of the recording so speech recognition doesn't hear the chime
SELECTION_MAX_CHARS = 4000
HISTORY_TURNS = 4

SYSTEM_PROMPT = """You are KeyIt Buddy, a friendly expert tutor that lives next to the user's mouse cursor on Windows.
You are given a screenshot of the user's current screen and their spoken question. Your reply is read aloud.

Rules:
- Reply in 1 to 3 short, natural, spoken sentences. No markdown, no lists, no emojis, no URLs.
- Reply in the language of the question, unless the user asks for another one.
- Be concrete and refer to what is actually visible on the screen.
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
# A sentence is finished at . ! ? once whitespace follows. Chinese, Japanese and Hindi put no space after their
# full stops, so there the sentence ends at the stop itself, with any closing quote, once the next character
# shows that nothing more belongs to it.
CJK_STOPS = "\u3002\uff01\uff1f\u0964"                               # 。 ！ ？ and the Hindi danda
CLOSERS = "\u300d\u300f\u201d\u2019\uff09\u300b\u3011\"')"                # closing quotes and brackets
FINISHED_SENTENCE = re.compile(
    rf"\s*(.+?(?:[.!?]+(?=\s)|[{CJK_STOPS}]+[{CLOSERS}]*(?=[^{CLOSERS}])))", re.DOTALL)


def language_rule(code):
    """One more line for the system prompt when voice input is in a language other than English: the answer
    follows the question's language (a rule in SYSTEM_PROMPT), and this names the language to use when a short
    or mixed question leaves that open. Nothing for English, Auto-detect, "Other European" and unknown codes."""
    wanted = (code or "").strip()
    if not wanted or keyit_speech.primary(wanted) in ("en", "auto", "eu"):
        return ""
    name = keyit_speech.language_name(wanted)
    if keyit_speech.primary(keyit_speech.LANGUAGES[name]) != keyit_speech.primary(wanted):
        return ""                                   # a language the picker does not have
    return (f"\n- The user's voice input language is {name}. If the question mixes languages or its language "
            "is unclear, reply in that language.")


SLOW_THINKING_MODELS = {"qwen3-vl:4b": "qwen3-vl:4b-instruct", "qwen3-vl:latest": "qwen3-vl:4b-instruct"}


def buddy_settings(settings):
    for key, value in DEFAULTS.items():
        settings.setdefault(key, value)
    # The plain qwen3-vl tags "think" silently for minutes on a laptop CPU; use the instruct twin.
    settings["buddy_model"] = SLOW_THINKING_MODELS.get(settings["buddy_model"], settings["buddy_model"])
    keyit_speech.migrate(settings)
    return settings


# --------------------------------------------------------------------------- parsing
def _thousandths(value):
    """Some models write a coordinate on a 0..1 scale ("183,0.440"): a decimal <= 1 is a fraction."""
    number = float(value)
    if "." in value and number <= 1:
        return round(number * 1000, 1)
    return number


def parse_reply(text):
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
            "keep_alive": KEEP_ALIVE,
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
        payload = {"model": model, "messages": [], "keep_alive": KEEP_ALIVE, "options": dict(MODEL_OPTIONS)}
        with self._request("/api/chat", payload, timeout=300) as response:
            response.read()

    def prefill(self, model, messages, stop):
        """Make Ollama read the screenshot now. A later chat that starts with the same messages reuses
        that work (measured: 1.2 s instead of ~85 s). Setting `stop` aborts by closing the connection.
        Returns True when Ollama finished reading."""
        address = urllib.parse.urlparse(self.base_url)
        connection = http.client.HTTPConnection(address.hostname, address.port or 80, timeout=900)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": KEEP_ALIVE,
            "options": dict(MODEL_OPTIONS, temperature=0.2, num_predict=1),
        }
        finished = threading.Event()

        def abort_when_stopped():
            while not finished.is_set():
                if stop.wait(0.5):
                    # shutdown() (not just close()) breaks the blocking read on Windows and drops the
                    # connection, so Ollama stops reading the image
                    sock = connection.sock
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    connection.close()
                    return

        threading.Thread(target=abort_when_stopped, daemon=True).start()
        try:
            connection.request("POST", "/api/chat", json.dumps(payload), {"Content-Type": "application/json"})
            response = connection.getresponse()
            response.read()
            return response.status == 200 and not stop.is_set()
        except (OSError, http.client.HTTPException):
            return False                               # aborted or Ollama unavailable: the question still works
        finally:
            finished.set()
            connection.close()


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


def dictation_spacing(text):
    """A dictation that ends a sentence gets a trailing space, so the next one doesn't stick to it."""
    if text and text[-1] in ".!?,;:" and not keyit_speech_is_cjk(text[-2:-1]):
        return text + " "
    return text


def keyit_speech_is_cjk(char):
    return bool(char) and ("\u3000" <= char <= "\u9fff" or "\uff00" <= char <= "\uffef")


def wait_for_keys_up(timeout=2.0):
    """Wait until Ctrl, Alt, Shift and D are physically released (typing with Ctrl held makes shortcuts)."""
    try:
        get_key_state = ctypes.windll.user32.GetAsyncKeyState
    except AttributeError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(get_key_state(vk) & 0x8000 for vk in (0x11, 0x12, 0x10, 0x44, 0x5B, 0x5C)):
            return
        time.sleep(0.02)


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


def legacy_engine(kind, language, whisper_model="auto"):
    """The old engines for keyit_speech.Speech: 'whisper' (faster-whisper) or 'windows' (System.Speech)."""
    if kind == "whisper":
        model = "base.en" if whisper_model in ("auto", "windows", "", None) else whisper_model
        return SpeechToText(model)
    return WindowsSpeechToText(language)


def resample(audio, source_rate, target_rate):
    return keyit_speech.resample(audio, source_rate, target_rate)


TEST_LINES = {
    "zh": "你好，我是Buddy。這就是我的聲音。",
    "es": "Hola, soy Buddy. Así es como sueno.",
    "fr-fr": "Bonjour, je suis Buddy. Voici ma voix.",
    "it": "Ciao, sono Buddy. Questa è la mia voce.",
    "pt-br": "Olá, eu sou o Buddy. Esta é a minha voz.",
    "hi": "नमस्ते, मैं बडी हूँ। मेरी आवाज़ ऐसी है।",
}


def test_line(voice_name):
    """What the Test button says, in the voice's own language."""
    import keyit_voice

    if keyit_voice.is_natural(voice_name):
        language = keyit_voice.language_of(keyit_voice.voice_id(voice_name))
        return TEST_LINES.get(language, "Hi, I'm Buddy. This is how I sound.")
    return "Hi, I'm Buddy. This is how I sound."


def default_voice():
    """No voice picked yet: the natural voice when it's on this PC, else the first Windows voice."""
    import keyit_voice

    return keyit_voice.DEFAULT if keyit_voice.is_downloaded() else ""


class Speaker:
    """Buddy's voice. Natural voices (keyit_voice, Kokoro) or a Windows SAPI voice on its own COM thread.
    say() queues, stop() interrupts."""

    SVSF_ASYNC = 1
    SVSF_PURGE = 2

    def __init__(self, voice_name="", on_status=lambda text: None):
        self.jobs = queue.SimpleQueue()
        self.on_status = on_status
        self.voice_name = voice_name or default_voice()
        self.voices = []
        self.natural = None                    # keyit_voice.NaturalVoice once a natural voice is picked
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="keyit-tts", daemon=True).start()
        self._ready.wait(30)   # first run in the exe generates COM bindings (several seconds)
        self._use(self.voice_name)

    def _use(self, name):
        import keyit_voice

        self.voice_name = name
        if not keyit_voice.is_natural(name):
            return
        if self.natural is None:
            self.natural = keyit_voice.NaturalVoice(keyit_voice.voice_id(name), on_status=self.on_status)
        else:
            self.natural.set_voice(keyit_voice.voice_id(name))

    def _natural_on(self):
        import keyit_voice

        return (self.natural is not None and keyit_voice.is_natural(self.voice_name)
                and not self.natural.failed)

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
        if not text.strip():
            return
        if self._natural_on():
            self.natural.say(text)
        else:
            self.jobs.put(("say", text))

    def stop(self):
        if self.natural is not None:
            self.natural.stop()
        self.jobs.put(("stop", None))

    def set_voice(self, name):
        self.stop()
        self._use(name)
        if not self._natural_on():
            self.jobs.put(("voice", name))

    def test(self):
        """Settings' Test button."""
        self.stop()
        if self.natural is not None and self._natural_on() and not self.natural.ready.is_set():
            self.on_status("Loading the natural voice...")
        self.say(test_line(self.voice_name))


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
# The pointer next to the mouse lives in buddy_overlay.py (own thread + window, so it keeps up on a busy PC)
BuddyOverlay = buddy_overlay.BuddyOverlay
ACCENT = {"color": buddy_overlay.DEFAULT_COLOR}   # Settings -> Look colour, also used by the ask/confirm boxes


def set_accent(color):
    ACCENT["color"] = buddy_overlay.normalize_color(color)


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
        accent = ACCENT["color"]
        top.config(bg=accent)
        inner = tk.Frame(top, bg=self.INK, padx=12, pady=9)
        inner.pack(padx=2, pady=2)
        tk.Label(inner, text="Ask Buddy", fg=accent, bg=self.INK,
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
class ConfirmBox:
    """'Force it closed?  [Yes] [No]' next to the mouse. Enter = yes, Esc = no, closes itself after 30 s."""

    def __init__(self, root, cursor, question, on_answer):
        import tkinter as tk

        self.on_answer = on_answer
        self.answered = False
        self.window = window = tk.Toplevel(root)
        window.title("Buddy asks")             # findable by screen readers and tests
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        accent = ACCENT["color"]
        frame = tk.Frame(window, bg="#fff7ef", highlightbackground=accent, highlightthickness=2)
        frame.pack()
        tk.Label(frame, text=question, bg="#fff7ef", justify="left", font=("Segoe UI", 10),
                 wraplength=320).pack(padx=12, pady=(10, 6), anchor="w")
        buttons = tk.Frame(frame, bg="#fff7ef")
        buttons.pack(padx=12, pady=(0, 10), anchor="e")
        tk.Button(buttons, text="Yes, do it", command=lambda: self.answer(True), bg=accent,
                  fg=buddy_overlay.INK if accent in ("#ffffff", "#facc15") else "white",
                  relief="flat", padx=10).pack(side="left", padx=(0, 6))
        tk.Button(buttons, text="No", command=lambda: self.answer(False), relief="flat", padx=10).pack(side="left")
        window.bind("<Return>", lambda _e: self.answer(True))
        window.bind("<Escape>", lambda _e: self.answer(False))
        window.update_idletasks()
        x, y = cursor
        window.geometry(f"+{max(0, x + 20)}+{max(0, y + 20)}")
        window.after(30000, lambda: self.answer(False))
        window.focus_force()

    def answer(self, value):
        if self.answered:
            return
        self.answered = True
        try:
            self.window.destroy()
        finally:
            self.on_answer(value)


class Buddy:
    """Push-to-talk flow. Call press()/release() from the hook; ask_text() from the GUI."""

    def __init__(self, settings, root, post, on_status=None, overlay=None, context_provider=None, sound=None):
        self.settings = buddy_settings(settings)
        self.root = root
        self.post = post                      # thread-safe "run on UI thread"
        self.on_status = on_status or (lambda text: None)
        self.island = None                             # Buddy Island at the top of the screen (set_island)
        # The pointer by the mouse (e.g. the Pips pet when hosted); the island mirrors what it shows
        self.overlay = buddy_island.IslandMirror(overlay or BuddyOverlay(root), lambda: self.island)
        set_accent(self.settings["buddy_color"])
        self.overlay.set_style(self.settings["buddy_color"], self.settings["buddy_icon"])
        self.context_provider = context_provider       # extra facts about the user (optional)
        self.sound = sound                             # play_effect(name) from the KeyIt player
        self.speaker = Speaker(self.settings["buddy_voice"], on_status=self.on_status)
        self.stt = None
        self.stt_name = None
        self.mic = None
        self.dictation = None                  # {"mic", "hands_free", "stop_on_release", ...} while dictating
        self.dictations = []                   # last few dictated texts (memory only)
        self.last_reply = None
        self.last_timing = None
        self.monitor = None
        self.history = []
        self.busy = threading.Lock()
        self.generation = 0
        self.look_ahead = None                # screenshot the model starts reading while you talk/type
        self.gpu_server = None                # llama-server on the GPU (keyit_gpu), started on demand
        self.gpu_failed = {}                  # model -> why the GPU can't run it (then: Ollama)
        self.maas_client = None               # off-PC brain: keyit_maas.MaasClient or keyit_free.FreeClient
        self.maas_failed_until = 0.0          # after an off-PC brain failure: local brain until this time
        self.local_missing = False            # "auto" and no local brain works here: use the free cloud brain
        self.windows = []                     # open windows when the question started (for actions)
        threading.Thread(target=self._prepare, daemon=True).start()

    # -- setup
    def set_island(self, enabled, on_menu=None):
        """Settings: 'Buddy Island at the top of the screen'. Click it = type, hold it = talk (like the
        hotkey), click an answer = dismiss, right click = on_menu (e.g. open the settings)."""
        if enabled and self.island is None:
            try:
                self.island = buddy_island.BuddyIsland(
                    color=self.settings["buddy_color"],
                    on_press=self.press,
                    on_release=self.release,
                    on_dismiss=self.cancel,
                    on_menu=on_menu,
                )
            except Exception:
                logging.exception("Buddy Island could not start")
                self.island = None
        elif not enabled and self.island is not None:
            island, self.island = self.island, None
            island.close()

    def _cloud_client(self, engine):
        """The off-PC brain for "maas" (company gateway) or "free" (Buddy's free cloud brain)."""
        wanted = "keyit_maas" if engine == "maas" else "keyit_free"
        if self.maas_client is None or type(self.maas_client).__module__ != wanted:
            if engine == "maas":
                import keyit_maas

                self.maas_client = keyit_maas.MaasClient()
            else:
                import keyit_free

                self.maas_client = keyit_free.FreeClient()
        return self.maas_client

    def client(self):
        """Smart brain (company MAAS) or the free cloud brain when picked and working; else the GPU brain
        when it can run the chosen model; else Ollama. "auto" with no working local brain: free cloud brain."""
        engine = self.settings.get("buddy_engine", "auto")
        cloud_ok = time.monotonic() >= self.maas_failed_until
        if engine in ("maas", "free") and cloud_ok:
            return self._cloud_client(engine)
        if engine == "auto" and self.local_missing and cloud_ok:
            return self._cloud_client("free")
        model = self.settings["buddy_model"]
        if self.settings.get("buddy_engine", "auto") == "cpu" or model in self.gpu_failed:
            if self.gpu_server is not None:
                self.gpu_server.stop()                     # switched off: free its memory
                self.gpu_server = None
            return OllamaClient(self.settings["ollama_url"])
        if self.gpu_server is None or self.gpu_server.model != model:
            reason = keyit_gpu.available(model)
            if reason:
                logging.info("GPU brain not available (%s); using Ollama", reason)
                self.gpu_failed[model] = reason
                return OllamaClient(self.settings["ollama_url"])
            if self.gpu_server is not None:
                self.gpu_server.stop()
            self.gpu_server = keyit_gpu.GpuServer(model)
        return keyit_gpu.GpuClient(self.gpu_server)

    def _maas_broke(self, error):
        """The off-PC brain failed (network, login, gateway, daily cap): answer locally for 5 minutes."""
        name = getattr(self.maas_client, "label", "Smart brain")
        logging.warning("%s failed, using the local brain for 5 min: %s", name, error)
        self.maas_failed_until = time.monotonic() + 300
        if getattr(error, "code", "") == "daily_cap":
            self.on_status(f"{name} is out of questions for today - answering on this PC.")
        else:
            self.on_status(f"{name} unreachable - answering on this PC for a few minutes.")

    def _use_free_instead(self, error):
        """"auto" and the local brain can't run here (no Ollama, no model): use the free cloud brain."""
        if self.settings.get("buddy_engine", "auto") != "auto" or self.local_missing:
            return False
        logging.info("No local brain (%s); using the free cloud brain", error)
        self.local_missing = True
        self.maas_failed_until = 0.0
        return True

    def _gpu_broke(self, error):
        """The GPU server failed: use Ollama from now on (until the model changes)."""
        logging.warning("GPU brain failed, switching to Ollama: %s", error)
        self.gpu_failed[self.settings["buddy_model"]] = str(error)
        if self.gpu_server is not None:
            self.gpu_server.stop()
            self.gpu_server = None

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
        threading.Thread(target=self._prepare_speech, name="buddy-speech-prepare", daemon=True).start()
        try:
            self.on_status("Loading model into memory…")
            client = self.client()
            if getattr(client, "smart", False):
                try:
                    client.warm_up()
                except Exception as error:
                    self._maas_broke(error)
                    client = self.client()
            try:
                try:
                    client.warm_up(self.settings["buddy_model"])
                except RuntimeError as error:
                    if not getattr(client, "gpu", False):
                        raise
                    self._gpu_broke(error)
                    client = self.client()
                    client.warm_up(self.settings["buddy_model"])
            except Exception as error:
                if getattr(client, "smart", False) or not self._use_free_instead(error):
                    raise
                client = self.client()
                client.warm_up()
            if getattr(client, "smart", False) and getattr(client, "label", "") == "Free cloud brain":
                where = "Free cloud brain, no setup needed"
            elif getattr(client, "smart", False):
                where = "Smart brain, company MAAS"
            else:
                where = "GPU, fast" if getattr(client, "gpu", False) else "CPU via Ollama"
            self.on_status(f"Ready ({where}) – Ctrl+Alt+Space: tap to type, hold to talk.")
        except Exception as error:
            self.on_status(self._explain_error(error).replace("\n", " "))

    def _ensure_stt(self):
        """The speech engine for the current settings (keyit_speech.Speech). Loads in the background, so the
        first question never waits for a download: until it is ready the Windows recognizer answers, if Windows
        has one for the language. If it has none, Speech.waiting() says what the user is waiting for."""
        wanted = (self.settings["speech_model"], self.settings["speech_language"])
        if self.stt is None or self.stt_name != wanted:
            whisper_model = self.settings.get("whisper_model", "auto")
            self.stt = keyit_speech.Speech(
                choice=wanted[0], language=wanted[1], on_status=self.on_status,
                legacy_factory=lambda kind, language: legacy_engine(kind, language, whisper_model))
            self.stt_name = wanted
            self.stt.prepare()
            if keyit_script.script(wanted[1]):              # Chinese: read the character tables now,
                threading.Thread(target=keyit_script.warm, daemon=True).start()    # not in the first sentence
        return self.stt

    def _prepare_speech(self):
        try:
            self._ensure_stt()
        except Exception:
            logging.exception("Speech engine could not start")

    def _mic(self):
        from keyit_recorder import MicCapture

        device = self.settings.get("mic_device") or None
        try:
            return MicCapture(device=device)
        except Exception:
            if device is None:
                raise
            logging.warning("Microphone %r unavailable, using the Windows default", device, exc_info=True)
            return MicCapture()

    def _words(self, text, mode):
        """A transcript as Buddy uses it: in the characters the language setting asks for (Traditional or
        Simplified Chinese), then My words and the tidy-up, by the rules of the language that was spoken."""
        language = self.stt.spoken_language() if self.stt else self.settings.get("speech_language")
        text = keyit_script.convert(text, language)
        return keyit_words.process(text, self.settings.get("my_words", ""), mode=mode,
                                   tidy_up=self.settings.get("voice_cleanup", True), language=language)

    # -- push to talk (called from the keyboard hook thread)
    def press(self):
        self.cancel()
        self.pressed_at = time.perf_counter()
        self.chimed = self.chime("buddy_wake")
        self.monitor = monitor_under_cursor()
        cursor = cursor_position()
        self._start_look_ahead()
        try:
            self.mic = self._mic()
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

    # -- dictation: Ctrl+Alt+D types what you say into the app in front (called from the hook thread)
    def dictate_press(self):
        if not self.settings.get("dictation", True):
            return
        current = self.dictation
        if current is not None:
            if current.get("hands_free"):
                current["stop_on_release"] = True        # this press ends hands-free dictation
            return
        self.speaker.stop()
        monitor, cursor = monitor_under_cursor(), cursor_position()
        try:
            mic = self._mic()
            mic.start()
        except Exception as error:
            self.post(self.overlay.thinking, f"Mic error: {error}")
            self.post(self.overlay.hide_later, 4000)
            return
        self.chime("buddy_wake")
        self.dictation = {"mic": mic, "hands_free": False, "stop_on_release": False,
                          "pressed_at": time.perf_counter(), "monitor": monitor, "selection": None}
        current = self.dictation

        def read_selection():                              # for "make this shorter" on highlighted text
            try:
                current["selection"] = selected_text_quick() or ""
            except Exception:
                current["selection"] = ""

        threading.Thread(target=read_selection, daemon=True).start()
        self.post(self.overlay.listening, monitor, cursor)
        self._ensure_stt()                                 # loads in the background if it isn't yet

    def dictate_release(self):
        current = self.dictation
        if current is None:
            return
        if current.get("stop_on_release"):
            self._finish_dictation(current)
            return
        held = time.perf_counter() - current["pressed_at"]
        if held < TAP_SECONDS and not current["hands_free"]:
            # a tap: keep listening hands-free until the next tap (or Esc, or 5 minutes)
            current["hands_free"] = True
            self.on_status("Dictating hands-free - tap Ctrl+Alt+D to type it, Esc to cancel.")
            threading.Thread(target=self._dictation_limit, args=(current,), daemon=True).start()
            return
        self._finish_dictation(current)

    def _dictation_limit(self, current):
        deadline = time.monotonic() + DICTATION_MAX_SECONDS
        while self.dictation is current and time.monotonic() < deadline:
            time.sleep(0.25)
        if self.dictation is current:
            self._finish_dictation(current)

    def _finish_dictation(self, current):
        if self.dictation is not current:
            return
        self.dictation = None
        try:
            audio, _ = current["mic"].stop()
        except Exception as error:
            self.post(self.overlay.thinking, f"Mic error: {error}")
            self.post(self.overlay.hide_later, 4000)
            return
        rate = current["mic"].sample_rate
        self.chime("buddy_got_it")
        self.post(self.overlay.thinking, "Writing\u2026")
        threading.Thread(target=self._dictate_text, args=(audio, rate, current.get("selection") or ""),
                         daemon=True).start()

    def _dictate_text(self, audio, sample_rate, selection=""):
        started = time.perf_counter()
        try:
            heard = self._ensure_stt().transcribe(audio, sample_rate)
        except Exception as error:
            logging.exception("Dictation failed")
            self.post(self.overlay.thinking, f"Speech recognition failed: {error}")
            self.post(self.overlay.hide_later, 5000)
            return
        text = self._words(heard, "dictate")
        if not text:
            waiting = self.stt.waiting()                    # no engine yet for this language: say so
            self.post(self.overlay.thinking, waiting or "I didn't hear anything - hold Ctrl+Alt+D while you talk.")
            self.post(self.overlay.hide_later, 4000 if waiting else 2500)
            return
        if selection.strip() and keyit_words.is_edit_command(text):
            rewritten = self._rewrite(text, selection)      # Command mode: rewrite the highlighted text
            if rewritten is None:
                return
            text = rewritten
        else:
            text = dictation_spacing(text)
        wait_for_keys_up()                                  # never type while Ctrl/Alt are still held
        sent = keyit_actions.type_text(text)
        self.dictations = (self.dictations + [text])[-DICTATION_HISTORY:]
        logging.info("Dictated %d chars in %.2fs (%s), %d key events", len(text), time.perf_counter() - started,
                     self.stt.describe(), sent)
        self.post(self.overlay.say, text if len(text) < 140 else text[:137] + "\u2026")
        self.post(self.overlay.hide_later, 1800)

    REWRITE_PROMPT = ("You edit text for the user. Apply their instruction to the text and reply with ONLY the "
                      "new text: no quotes, no explanation, no markdown, same language as the text unless they ask "
                      "for a translation. Keep names, numbers and part numbers exactly.")

    def _rewrite(self, instruction, selection):
        """Command mode: the brain applies a spoken instruction to the highlighted text. None = failed (shown)."""
        self.post(self.overlay.thinking, f"\u201c{instruction}\u201d\nRewriting\u2026")
        messages = [{"role": "system", "content": self.REWRITE_PROMPT},
                    {"role": "user", "content": f"Instruction: {instruction}\n\nText:\n{selection[:SELECTION_MAX_CHARS]}"}]
        try:
            reply = "".join(self._stream(messages)).strip()
        except Exception as error:
            logging.exception("Rewrite failed")
            self.post(self.overlay.thinking, self._explain_error(error))
            self.post(self.overlay.hide_later, 8000)
            return None
        reply = re.sub(r"^```\w*\n?|\n?```$", "", reply).strip().strip('"\u201c\u201d')
        if not reply:
            self.post(self.overlay.thinking, "The brain sent back nothing - try again?")
            self.post(self.overlay.hide_later, 3000)
            return None
        return reply

    def type_last_dictation(self):
        """Tray / settings: type the last dictation again (e.g. it went to the wrong window)."""
        if not self.dictations:
            self.on_status("Nothing dictated yet - hold Ctrl+Alt+D and talk.")
            return False
        wait_for_keys_up()
        keyit_actions.type_text(self.dictations[-1])
        return True

    def copy_last_dictation(self, root=None):
        """Put the last dictation on the clipboard (only when you ask for it)."""
        if not self.dictations:
            return False
        widget = root or self.root
        widget.clipboard_clear()
        widget.clipboard_append(self.dictations[-1])
        return True

    def _open_ask_box(self):
        """Read the highlighted text (if any) before the box takes focus, then open the box at the mouse."""
        selection = selected_text_quick()
        look = self.look_ahead
        if look is not None:
            look["taken"].wait(1.5)                          # keep the box out of the screenshot
        self.post(self.open_ask_box, selection, False)       # the hotkey already chimed

    def open_ask_box(self, selection="", chime=True):
        if chime:
            self.chime("buddy_wake")
        AskBox(self.root, monitor_under_cursor(), cursor_position(), selection,
               on_submit=lambda question: self.ask_text(question, selection=selection))

    def cancel(self):
        self.generation += 1
        self.speaker.stop()
        self._stop_look_ahead()
        if self.mic:
            try:
                self.mic.stop()
            except Exception:
                pass
            self.mic = None
        dictation, self.dictation = self.dictation, None
        if dictation is not None:
            try:
                dictation["mic"].stop()
            except Exception:
                pass
        self.post(self.overlay.hide)
        self.post(AskBox.close_current)

    # -- look-ahead: start reading the screen the moment the hotkey goes down
    def _start_look_ahead(self):
        self._stop_look_ahead()
        look = {
            "monitor": self.monitor,
            "history": list(self.history[-2 * HISTORY_TURNS:]),
            "at": time.monotonic(),
            "png": None,
            "system": None,
            "taken": threading.Event(),       # screenshot captured
            "targets": None,                  # visible UI elements (for snapping the arrow)
            "read": threading.Event(),        # Ollama finished reading it
            "stop": threading.Event(),        # cancelled
        }
        self.look_ahead = look
        threading.Thread(target=self._look_ahead, args=(look,), daemon=True).start()

    def _look_ahead(self, look):
        try:
            cleared = threading.Event()
            self.post(lambda: (self.overlay.clear_for_screenshot(), cleared.set()))
            cleared.wait(1.0)
            time.sleep(0.05)
            look["png"], _ = capture_monitor(look["monitor"], self.settings["screenshot_max_side"])
            look["system"] = self._system_prompt()
            look["windows"] = list(self.windows)
            look["targets"] = self._snapshot_ui(look["monitor"])
        except Exception:
            logging.exception("Look-ahead screenshot failed")
            return
        finally:
            look["taken"].set()
        messages = self._messages(look["system"], look["history"], "", look["png"])
        started = time.perf_counter()
        try:
            client = self.client()
        except Exception:
            return
        if client.prefill(self.settings["buddy_model"], messages, look["stop"]):
            look["read"].set()
            logging.info("Buddy read the screen ahead in %.1fs", time.perf_counter() - started)

    def _stop_look_ahead(self):
        look, self.look_ahead = self.look_ahead, None
        if look is not None:
            look["stop"].set()

    def _take_look_ahead(self):
        """The look-ahead for this question, if it matches the screen being asked about."""
        look, self.look_ahead = self.look_ahead, None
        if look is None:
            return None
        look["taken"].wait(2.0)
        fresh = time.monotonic() - look["at"] < LOOK_AHEAD_MAX_AGE
        if look["png"] is not None and fresh and look["monitor"] == self.monitor:
            return look
        look["stop"].set()                     # stale: free Ollama for the real question
        return None

    @staticmethod
    def _snapshot_ui(monitor):
        """Start listing the visible UI elements (same moment as the screenshot). Returns a holder
        {"done": Event, "items": [...]} so the question doesn't wait for it unless it needs it."""
        rect = monitor.get("physical") or (monitor["left"], monitor["top"],
                                           monitor["left"] + monitor["width"],
                                           monitor["top"] + monitor["height"])
        holder = {"done": threading.Event(), "items": [], "rect": rect}

        def work():
            try:
                holder["items"] = keyit_targets.snapshot(rect)
            except Exception:
                logging.debug("UI snapshot failed", exc_info=True)
            finally:
                holder["done"].set()

        threading.Thread(target=work, daemon=True).start()
        return holder

    def _snap_points(self, points, targets):
        """Move the model's points onto the exact UI elements they mean (when Windows knows them)."""
        if not points or targets is None:
            return points
        targets["done"].wait(3.0)
        snapped = []
        for point in points:
            new_point, target = keyit_targets.snap(point, targets["items"], targets["rect"])
            if target is not None:
                logging.info("Arrow snapped %r -> %r %s", point[2], target["name"][:40], target["kind"])
            snapped.append(new_point)
        return snapped

    def _stream(self, messages):
        """Answer chunks from the GPU brain; if it breaks before answering, from Ollama instead."""
        client = self.client()
        model = self.settings["buddy_model"]
        if getattr(client, "smart", False):
            started = False
            try:
                for chunk in client.chat_stream(model, messages):
                    started = True
                    yield chunk
                return
            except Exception as error:
                if started or self.local_missing:      # no local brain to fall back to: say why
                    raise
                self._maas_broke(error)
                client = self.client()                 # the local brain answers this one
        if not getattr(client, "gpu", False):
            started = False
            try:
                for chunk in client.chat_stream(model, messages):
                    started = True
                    yield chunk
            except (urllib.error.URLError, OSError) as error:
                if started or not self._use_free_instead(error):
                    raise
                yield from self.client().chat_stream(model, messages)    # no Ollama here: free brain
            return
        started = False
        try:
            for chunk in client.chat_stream(model, messages):
                started = True
                yield chunk
        except (RuntimeError, OSError, http.client.HTTPException) as error:
            if started:
                raise
            self._gpu_broke(error)
            yield from self.client().chat_stream(model, messages)

    @staticmethod
    def _messages(system, history, content, png):
        """System + recent turns + this question with the screenshot. The look-ahead uses the same
        start with empty text, so Ollama can reuse the image work for the real question."""
        messages = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user", "content": content, "images": [base64.b64encode(png).decode()]})
        return messages

    # -- question flow (worker threads)
    def _from_voice(self, audio, sample_rate):
        try:
            started = time.perf_counter()
            question = self._ensure_stt().transcribe(audio, sample_rate)
            question = self._words(question, "ask")
            logging.info("Heard in %.2fs (%s): %r", time.perf_counter() - started, self.stt.describe(), question)
        except Exception as error:
            self.post(self.overlay.thinking, f"Speech recognition failed: {error}")
            return
        if not question:
            waiting = self.stt.waiting()
            self.post(self.overlay.thinking, waiting or "I didn't catch that – try again?")
            self.post(self.overlay.hide_later, 4000 if waiting else 2500)
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
        code = getattr(error, "code", "")
        if code == "daily_cap":
            return ("The free brain is out of questions for today. It's back tomorrow.\n"
                    "For unlimited answers on this PC: install Ollama, then Settings -> Brain -> Local.")
        if code == "rate_limited":
            return "That's a lot of questions in a minute. Give me a few seconds and ask again."
        if code == "unreachable":
            return "I can't reach the free brain. Check the internet connection and ask again."
        if isinstance(error, urllib.error.HTTPError) and error.code == 404:
            return (f"I can't find my brain: Ollama has no '{model}' model.\n"
                    f"Fix: reinstall Ollama from ollama.com, then run:  ollama pull {model}")
        if isinstance(error, urllib.error.URLError):
            return "Ollama isn't running, so I can't think.\nStart Ollama from the Start menu and ask again."
        return f"Buddy error: {error}"

    def _system_prompt(self):
        language = self.stt.spoken_language() if self.stt else self.settings.get("speech_language")
        prompt = SYSTEM_PROMPT + language_rule(language)
        try:
            self.windows = keyit_actions.list_windows()
        except Exception:
            logging.debug("Could not list windows", exc_info=True)
            self.windows = []
        prompt += "\n" + keyit_actions.ACTION_RULES
        if self.windows:
            prompt += "\nOpen windows (front first):\n" + keyit_actions.describe_windows(self.windows)
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
        look = self._take_look_ahead()
        if look is not None:
            # Screenshot from the moment the hotkey went down; Ollama may already have read it
            png, system, history = look["png"], look["system"], look["history"]
            targets = look["targets"]
            self.windows = look.get("windows", self.windows)
            wait_text = "Almost there\u2026" if look["read"].is_set() else "Still reading your screen\u2026"
        else:
            # Remove the buddy from the screen before taking the screenshot
            done = threading.Event()
            self.post(lambda: (self.overlay.clear_for_screenshot(), done.set()))
            done.wait(1.0)
            time.sleep(0.05)
            png, _ = capture_monitor(self.monitor, self.settings["screenshot_max_side"])
            targets = self._snapshot_ui(self.monitor)
            system, history = self._system_prompt(), self.history[-2 * HISTORY_TURNS:]
            fast = self.settings.get("buddy_engine", "auto") != "cpu" and \
                self.settings["buddy_model"] not in self.gpu_failed
            wait_text = "Looking at your screen\u2026" if fast else "Looking at your screen\u2026 (about a minute)"
        self.post(self.overlay.thinking, f"\u201c{question}\u201d\n{wait_text}")

        content = question
        if selection:
            content += f"\n\nThe text I highlighted on screen:\n\"\"\"\n{selection[:SELECTION_MAX_CHARS]}\n\"\"\""
        messages = self._messages(system, history, content, png)

        reply, spoken_upto = "", 0
        first_token_at = None
        for chunk in self._stream(messages):
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
        points = self._snap_points(points, targets)
        screen_points = [to_screen(p, self.monitor) for p in points]
        self.history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": reply},
        ]
        total = time.perf_counter() - started
        first = (first_token_at or time.perf_counter()) - started
        self.last_timing = {"first_token_s": first, "total_s": total, "points": len(points)}
        self.on_status(f"Answered in {total:.1f}s (first words after {first:.1f}s)")
        logging.info("Answered in %.1fs: %r", total, " ".join(reply.split())[:200])
        actions = keyit_actions.parse_actions(reply)
        if actions and not keyit_actions.is_request(question):
            logging.info("Not acting: '%s' is a how-to question", question)
            if not screen_points and targets is not None:
                # "How do I close this?" -> [DO:click:Close]: point at it instead of clicking it
                targets["done"].wait(3.0)
                for action, argument in actions:
                    target = keyit_targets.find_by_label(argument, targets["items"]) if action == "click" else None
                    if target is not None:
                        screen_points.append(to_screen(keyit_targets.to_normalized(target, targets["rect"]),
                                                       self.monitor))
            actions = []
        dropped = [(a, arg) for a, arg in actions if not keyit_actions.asked_for(a, question)]
        if dropped:
            logging.info("Dropped actions the user didn't ask for: %s", dropped)
            actions = [item for item in actions if item not in dropped]
        direct = keyit_actions.direct_command(question)
        if direct and not any(a == direct[0] for a, _ in actions):
            logging.info("Using the user's own command %s (model gave %s)", direct, actions or "none")
            actions = [direct]
        self.last_reply = {"question": question, "reply": reply, "spoken": spoken, "points": screen_points,
                           "actions": actions}
        if actions:
            self._do_actions(actions, targets, spoken, generation, question)
        elif screen_points:
            self.post(self.overlay.tour, screen_points, spoken)
        else:
            self.post(self.overlay.say, spoken)
            self.post(self.overlay.hide_later, 9000)

    # -- doing things
    def _do_actions(self, actions, targets, spoken, generation, question=""):
        """Run the model's [DO:...] actions one by one and say what happened."""
        if targets is not None:
            targets["done"].wait(3.0)
        items = targets["items"] if targets is not None else []
        results = []
        for action, argument in actions:
            if generation != self.generation:
                return                                            # cancelled (Esc / new question)
            try:
                results.append(keyit_actions.run(action, argument, self.windows, items, confirm=self.confirm,
                                                  question=question))
            except Exception as error:
                logging.exception("Buddy action %s failed", action)
                results.append(f"I couldn't {action} that: {error}")
            if action in ("close", "minimize", "open"):
                time.sleep(0.4)
                try:
                    self.windows = keyit_actions.list_windows()  # later steps see the new window list
                except Exception:
                    pass
        summary = " ".join(results)
        logging.info("Action result: %s", summary)
        self.last_reply["action_results"] = results
        self.on_status(summary)
        if self.settings["buddy_speak"] and summary != spoken:
            self.speaker.say(summary)
        self.post(self.overlay.say, summary)
        self.post(self.overlay.hide_later, 6000)

    def confirm(self, question, timeout_s=30):
        """Ask yes/no in a small box next to the mouse (worker threads only). False on timeout."""
        answer = {"value": False}
        done = threading.Event()

        def show():
            ConfirmBox(self.root, cursor_position(), question, lambda value: (answer.update(value=value), done.set()))

        if self.settings["buddy_speak"]:
            self.speaker.say(question.split("\n")[0])
        self.post(show)
        done.wait(timeout_s)
        return answer["value"]
