"""Natural-sounding voices for Buddy (Kokoro, runs offline on this PC).

The Windows voices (SAPI) sound robotic. Kokoro is a small neural voice model (~120 MB, downloaded once from
GitHub into <user dir>/voices). It runs on the CPU with onnxruntime; the GPU can't run it (DirectML and
OpenVINO both reject some of its layers on Intel Arc).

To keep speech smooth on a busy CPU:
  * text is cut into short pieces (sentences, long ones at commas),
  * the next piece is made while the current one plays,
  * stop() cuts the audio within ~0.1 s and drops everything queued.
"""
import logging
import os
import queue
import re
import threading
import time
import urllib.request
from pathlib import Path

MODEL_FILE = "kokoro-v1.0.int8.onnx"
VOICES_FILE = "voices-v1.0.bin"
DOWNLOAD_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
DOWNLOAD_MB = 120
SAMPLE_RATE = 24000
PREFIX = "Natural: "

# (label in Settings, Kokoro voice id) - the best-rated English voices first
VOICES = [
    ("Heart (US woman)", "af_heart"),
    ("Bella (US woman)", "af_bella"),
    ("Nicole (US woman, soft)", "af_nicole"),
    ("Sarah (US woman)", "af_sarah"),
    ("Michael (US man)", "am_michael"),
    ("Fenrir (US man)", "am_fenrir"),
    ("Puck (US man)", "am_puck"),
    ("Emma (UK woman)", "bf_emma"),
    ("Isabella (UK woman)", "bf_isabella"),
    ("George (UK man)", "bm_george"),
    ("Fable (UK man)", "bm_fable"),
]
DEFAULT = PREFIX + VOICES[0][0]


def names():
    return [PREFIX + label for label, _ in VOICES]


def is_natural(name):
    return bool(name) and name.startswith(PREFIX)


def voice_id(name):
    label = name[len(PREFIX):] if is_natural(name) else name
    for known_label, known_id in VOICES:
        if label == known_label:
            return known_id
    return VOICES[0][1]


def voices_dir():
    import keyit_core

    return Path(keyit_core.USER_DIR) / "voices"


def is_downloaded():
    folder = voices_dir()
    return (folder / MODEL_FILE).is_file() and (folder / VOICES_FILE).is_file()


def download(progress=lambda text: None):
    """Fetch the model files once (uses the Windows/system proxy settings). Raises on failure."""
    folder = voices_dir()
    folder.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies()))
    for name in (VOICES_FILE, MODEL_FILE):
        target = folder / name
        if target.is_file():
            continue
        partial = target.with_name(name + ".part")
        request = urllib.request.Request(DOWNLOAD_BASE + name, headers={"User-Agent": "Buddy"})
        with opener.open(request, timeout=60) as response, open(partial, "wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if total:
                    progress(f"Downloading natural voices... {done * 100 // total}%")
        partial.replace(target)
    progress("Natural voices ready.")


def split_text(text, max_chars=90, min_chars=30):
    """Pieces of about one breath, so audio for the next piece is ready before the current one ends:
    tiny sentences ("Sure!") join the next one, long ones are cut at commas/semicolons."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s]
    joined = []
    for sentence in sentences:
        if joined and len(joined[-1]) < min_chars:
            joined[-1] = f"{joined[-1]} {sentence}"
        else:
            joined.append(sentence)
    pieces = []
    for sentence in joined:
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(mark, min_chars, max_chars) for mark in (", ", "; ", ": ", " - "))
            if cut < 0:
                cut = sentence.rfind(" ", min_chars, max_chars)
            if cut < 0:
                break
            pieces.append(sentence[:cut + 1].strip())
            sentence = sentence[cut + 1:].strip()
        if sentence:
            pieces.append(sentence)
    return pieces


class NaturalVoice:
    """say() queues text, stop() interrupts. Two threads: one makes audio, one plays it."""

    def __init__(self, voice="af_heart", speed=1.0, on_status=lambda text: None):
        self.voice = voice
        self.speed = speed
        self.on_status = on_status
        self.texts = queue.Queue()
        self.audio = queue.Queue(maxsize=3)
        self.generation = 0
        self.kokoro = None
        self.failed = None
        self.ready = threading.Event()
        threading.Thread(target=self._make_loop, name="buddy-voice-make", daemon=True).start()
        threading.Thread(target=self._play_loop, name="buddy-voice-play", daemon=True).start()

    # -- public
    def say(self, text):
        for piece in split_text(text):
            self.texts.put((self.generation, piece))

    def stop(self):
        self.generation += 1
        for pending in (self.texts, self.audio):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break

    def set_voice(self, voice):
        self.voice = voice

    def speak_test(self):
        self.stop()
        self.say("Hi, I'm Buddy. This is how I sound.")

    # -- threads
    def _load(self):
        logging.getLogger("phonemizer").setLevel(logging.ERROR)     # harmless "words count mismatch" noise
        if not is_downloaded():
            download(self.on_status)
        import onnxruntime
        from kokoro_onnx import Kokoro

        started = time.perf_counter()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = max(2, min(6, (os.cpu_count() or 4) - 2))  # leave room for the rest
        session = onnxruntime.InferenceSession(str(voices_dir() / MODEL_FILE), options,
                                               providers=["CPUExecutionProvider"])
        self.kokoro = Kokoro.from_session(session, str(voices_dir() / VOICES_FILE))
        logging.info("Natural voice ready in %.1fs", time.perf_counter() - started)

    def _make_loop(self):
        try:
            self._load()
        except Exception as error:
            self.failed = f"{type(error).__name__}: {error}"
            logging.exception("Natural voice could not start")
        finally:
            self.ready.set()
        while True:
            generation, text = self.texts.get()
            if self.failed or generation != self.generation:
                continue
            while len(text) < 30:                  # "Sure!" alone: wait a moment for the next sentence
                try:
                    more_generation, more = self.texts.get(timeout=0.8)
                except queue.Empty:
                    break
                if more_generation == generation:
                    text = f"{text} {more}"
            try:
                started = time.perf_counter()
                samples, rate = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-us")
                took = time.perf_counter() - started
                logging.debug("Voice: %.1fs audio in %.1fs", len(samples) / rate, took)
            except Exception:
                logging.exception("Natural voice failed on %r", text[:60])
                continue
            if generation == self.generation:
                self.audio.put((generation, samples, rate))

    def _play_loop(self):
        import sounddevice

        while True:
            generation, samples, rate = self.audio.get()
            if generation != self.generation:
                continue
            try:
                with sounddevice.OutputStream(samplerate=rate, channels=1, dtype="float32") as stream:
                    block = rate // 10                                   # 0.1 s: stop() reacts this fast
                    for start in range(0, len(samples), block):
                        if generation != self.generation:
                            stream.abort()
                            break
                        stream.write(samples[start:start + block].astype("float32").reshape(-1, 1))
            except Exception:
                logging.exception("Could not play the natural voice")
