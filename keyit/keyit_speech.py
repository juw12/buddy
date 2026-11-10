"""Speech in for Buddy: what you said -> text, offline on this PC.

Engines (Settings -> Speech model):
  * Whisper base.en (OpenAI, English): the most accurate for English in Buddy's benchmark, ~210 MB once.
  * Parakeet V3 (NVIDIA, 25 European languages): for German, French, Spanish and the rest, ~490 MB once.
  * SenseVoice (Chinese, English, Cantonese, Japanese, Korean): fast, punctuated, ~170 MB once.
  * Moonshine (English only): the quickest, ~250 MB once.
  * Whisper (faster-whisper): the old engine; its download (HuggingFace) is blocked on many office networks.
  * Windows: the recognizer built into Windows. Always there, but much less accurate.

The first four come from GitHub release files (k2-fsa/sherpa-onnx) and run with sherpa-onnx on the CPU.
GitHub is reachable from offices where HuggingFace is not, which is why "auto" no longer silently ends up
on the Windows recognizer there.

Every engine gets Silero voice activity detection first (0.6 MB): the recording is trimmed to the part where
someone speaks, and a recording without speech (a key click, a fan, a breath) gives no text at all, so
nothing made up ("Thank you.") is ever typed or asked.
"""
import logging
import os
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

RATE = 16000
GITHUB = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
VAD_FILE = "silero_vad.onnx"
THREADS = max(2, (os.cpu_count() or 4) // 2)

# key -> what Settings shows and where the files are
ENGINES = {
    "whisper-en": {
        "label": "Whisper base.en - English, most accurate in our tests",
        "archive": "sherpa-onnx-whisper-base.en",
        "mb": 210,
        "languages": "en",
        "keep": ("int8.onnx", "tokens.txt"),          # the archive also holds the 2x larger float models
    },
    "parakeet": {
        "label": "Parakeet V3 - 25 European languages",
        "archive": "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "mb": 490,
        "languages": "en de fr es it pt nl pl ru uk sv da fi no cs sk sl hr bg ro hu el et lv lt mt",
    },
    "sensevoice": {
        "label": "SenseVoice - Chinese + English (also Cantonese, Japanese, Korean)",
        "archive": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        "mb": 170,
        "languages": "zh en yue ja ko",
    },
    "moonshine": {
        "label": "Moonshine - English, small",
        "archive": "sherpa-onnx-moonshine-base-en-int8",
        "mb": 250,
        "languages": "en",
    },
}
LEGACY = {
    "whisper": "Whisper base.en (old engine; download often blocked at work)",
    "windows": "Windows speech recognizer (no download, least accurate)",
}
CHOICES = ["auto"] + list(ENGINES) + list(LEGACY)
LABELS = {"auto": "Auto - best for your language (recommended)",
          **{key: value["label"] for key, value in ENGINES.items()}, **LEGACY}


SHORT_LABELS = {
    "auto": "Auto (recommended)",
    "whisper-en": "Whisper base.en - English",
    "parakeet": "Parakeet V3 - European languages",
    "sensevoice": "SenseVoice - Chinese + English",
    "moonshine": "Moonshine - English, small",
    "whisper": "Whisper (old engine)",
    "windows": "Windows recognizer (no download)",
}


def choice_for_short_label(text):
    for key, value in SHORT_LABELS.items():
        if value == text:
            return key
    return "auto"


def label(choice):
    return LABELS.get(choice, choice)


def choice_for_label(text):
    for key, value in LABELS.items():
        if value == text:
            return key
    return text if text in LABELS else "auto"


def auto_engine(language):
    """'auto' -> the engine for the speech language: Whisper for English, SenseVoice for Chinese/Japanese/Korean,
    Parakeet for the other European languages (choices measured with bench/ - see README)."""
    language = (language or "en").lower()
    if language.startswith(("zh", "yue", "ja", "ko")):
        return "sensevoice"
    if language.startswith("en"):
        return "whisper-en"
    return "parakeet"


def migrate(settings):
    """Old settings used 'whisper_model'. Keep a deliberate old choice, move everyone else to the new engines."""
    if "speech_model" not in settings:
        old = settings.get("whisper_model", "auto")
        settings["speech_model"] = "windows" if old == "windows" else (
            "auto" if old in ("auto", "", None) else "whisper")
    if settings["speech_model"] not in CHOICES:
        settings["speech_model"] = "auto"
    return settings


# --------------------------------------------------------------------------- files
def models_dir():
    import keyit_core

    return Path(keyit_core.USER_DIR) / "speech"


def is_ready(engine):
    if engine not in ENGINES:
        return True
    folder = models_dir() / ENGINES[engine]["archive"]
    return folder.is_dir() and any(folder.glob("*tokens.txt")) and (models_dir() / VAD_FILE).is_file()


def _opener():
    # the Windows/system proxy settings, like the natural voices (keyit_voice.download)
    return urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies()))


def _fetch(url, target, progress, what, attempts=6):
    """Download url -> target. Office proxies often cut long downloads; this resumes where it stopped
    (HTTP Range) and retries with a short pause, so a 490 MB model still arrives in one go for the user."""
    partial = target.with_name(target.name + ".part")
    shown = -1
    for attempt in range(1, attempts + 1):
        done = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "Buddy"}
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with _opener().open(request, timeout=60) as response:
                if done and getattr(response, "status", 200) != 206:
                    done = 0                               # server ignored the range: start over
                length = int(response.headers.get("Content-Length") or 0)
                total = done + length if length else 0
                with open(partial, "ab" if done else "wb") as handle:
                    while True:
                        block = response.read(1 << 20)
                        if not block:
                            break
                        handle.write(block)
                        done += len(block)
                        if total and done * 100 // total != shown:
                            shown = done * 100 // total
                            progress(f"Downloading {what}... {shown}%")
            if total and done < total:
                raise OSError(f"connection closed at {done} of {total} bytes")
            partial.replace(target)
            return
        except (OSError, urllib.error.URLError) as error:
            if attempt == attempts or (isinstance(error, urllib.error.HTTPError) and error.code in (403, 404, 407)):
                raise
            logging.info("Download of %s interrupted (%s), resuming (try %d)", target.name, error, attempt + 1)
            progress(f"Downloading {what}... connection dropped, resuming")
            time.sleep(min(2 * attempt, 10))


_download_lock = threading.Lock()


def download(engine, progress=lambda text: None):
    """Fetch the voice detector and the engine's model once (~0.6 MB + the model). Raises on failure."""
    with _download_lock:
        folder = models_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / VAD_FILE).is_file():
            _fetch(GITHUB + VAD_FILE, folder / VAD_FILE, progress, "the voice detector")
        if engine not in ENGINES or is_ready(engine):
            return
        info = ENGINES[engine]
        archive = folder / (info["archive"] + ".tar.bz2")
        if not archive.is_file():
            _fetch(GITHUB + archive.name, archive, progress, f"the speech model (~{info['mb']} MB, once)")
        progress("Unpacking the speech model...")
        with tarfile.open(archive) as tar:
            keep = info.get("keep")
            members = [m for m in tar.getmembers() if not m.name.startswith(("/", "..")) and ".." not in m.name
                       and (keep is None or m.isdir() or m.name.endswith(keep))]
            try:
                tar.extractall(folder, members=members, filter="data")
            except TypeError:                                   # Python < 3.12
                tar.extractall(folder, members=members)
        archive.unlink(missing_ok=True)
        progress("Speech model ready.")


def _find(folder, *patterns):
    for pattern in patterns:
        hits = sorted(folder.glob(pattern))
        if hits:
            return str(hits[0])
    raise FileNotFoundError(f"{patterns[0]} missing in {folder}")


# --------------------------------------------------------------------------- voice activity
class VoiceDetector:
    """Silero VAD: where in a recording someone speaks."""

    PAD = 0.25            # s kept before and after speech, so first and last sounds are not clipped

    def __init__(self, threshold=0.5):
        import sherpa_onnx

        self.sherpa = sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(models_dir() / VAD_FILE)
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = 0.3
        config.silero_vad.min_speech_duration = 0.15
        config.sample_rate = RATE
        config.num_threads = 1
        self.config = config

    def span(self, audio16k):
        """(start, end) in samples around all speech, or None when nobody spoke."""
        vad = self.sherpa.VoiceActivityDetector(self.config, buffer_size_in_seconds=max(30, len(audio16k) // RATE + 5))
        window = self.config.silero_vad.window_size
        starts, ends = [], []

        def drain():
            while not vad.empty():
                segment = vad.front
                starts.append(segment.start)
                ends.append(segment.start + len(segment.samples))
                vad.pop()

        for i in range(0, len(audio16k), window):
            vad.accept_waveform(audio16k[i:i + window])
            drain()
        vad.flush()
        drain()
        if not starts:
            return None
        pad = int(self.PAD * RATE)
        return max(0, min(starts) - pad), min(len(audio16k), max(ends) + pad)


# --------------------------------------------------------------------------- engines
class SherpaSpeech:
    """Whisper / Parakeet / SenseVoice / Moonshine through sherpa-onnx, with Silero VAD in front."""

    def __init__(self, engine):
        import sherpa_onnx

        self.engine = engine
        folder = models_dir() / ENGINES[engine]["archive"]
        if engine == "whisper-en":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=_find(folder, "*encoder.int8.onnx", "*encoder.onnx"),
                decoder=_find(folder, "*decoder.int8.onnx", "*decoder.onnx"),
                tokens=_find(folder, "*tokens.txt"), language="en", task="transcribe", num_threads=THREADS)
        elif engine == "parakeet":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=_find(folder, "encoder*.onnx"), decoder=_find(folder, "decoder*.onnx"),
                joiner=_find(folder, "joiner*.onnx"), tokens=_find(folder, "tokens.txt"),
                model_type="nemo_transducer", num_threads=THREADS)
        elif engine == "sensevoice":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=_find(folder, "model.int8.onnx", "model.onnx"), tokens=_find(folder, "tokens.txt"),
                use_itn=True, language="auto", num_threads=THREADS)
        elif engine == "moonshine":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
                preprocessor=_find(folder, "preprocess*.onnx"), encoder=_find(folder, "encode*.onnx"),
                uncached_decoder=_find(folder, "uncached_decode*.onnx"),
                cached_decoder=_find(folder, "cached_decode*.onnx"), tokens=_find(folder, "tokens.txt"),
                num_threads=THREADS)
        else:
            raise ValueError(engine)
        self.vad = VoiceDetector()
        self.warm_up()

    def warm_up(self):
        """One tiny decode so the first real question doesn't pay the first-run cost."""
        stream = self.recognizer.create_stream()
        stream.accept_waveform(RATE, np.zeros(RATE // 2, dtype=np.float32))
        self.recognizer.decode_stream(stream)

    def transcribe(self, audio, sample_rate):
        audio = np.asarray(audio, dtype=np.float32)
        audio16k = resample(audio, sample_rate, RATE)
        span = self.vad.span(audio16k)
        if span is None:
            return ""
        stream = self.recognizer.create_stream()
        stream.accept_waveform(RATE, audio16k[span[0]:span[1]])
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip()


def trim_to_speech(audio, sample_rate):
    """For the old engines: the speech part of a recording, or None for no speech (VAD when available)."""
    try:
        detector = VoiceDetector()
    except Exception:                                       # no sherpa-onnx / no VAD file: keep everything
        return audio
    audio16k = resample(np.asarray(audio, dtype=np.float32), sample_rate, RATE)
    span = detector.span(audio16k)
    if span is None:
        return None
    scale = sample_rate / RATE
    return audio[int(span[0] * scale):int(span[1] * scale)]


def resample(audio, source_rate, target_rate):
    """Windowed-sinc resampling (clean enough for speech recognition), numpy only."""
    audio = np.asarray(audio, dtype=np.float32)
    if source_rate == target_rate or audio.size == 0:
        return audio
    ratio = target_rate / source_rate
    if ratio < 1:                                           # low-pass at the new Nyquist before decimating
        cutoff = ratio * 0.95
        half = 16
        taps = np.arange(-half * int(np.ceil(1 / ratio)), half * int(np.ceil(1 / ratio)) + 1)
        kernel = cutoff * np.sinc(cutoff * taps) * np.hanning(len(taps))
        kernel /= kernel.sum()
        audio = np.convolve(audio, kernel, mode="same").astype(np.float32)
    positions = np.arange(0, audio.size, source_rate / target_rate)
    return np.interp(positions, np.arange(audio.size), audio).astype(np.float32)


class Speech:
    """What Buddy uses: picks the engine, downloads it in the background, falls back while it isn't there.

    transcribe(audio, rate) never waits for a download: until the chosen engine is ready, the Windows
    recognizer answers (and says so in the status line)."""

    def __init__(self, choice="auto", language="en-US", on_status=lambda text: None, legacy_factory=None):
        self.choice = choice
        self.language = language
        self.on_status = on_status
        self.legacy_factory = legacy_factory          # (kind, language) -> engine with .transcribe (whisper/windows)
        self.engine = None
        self.engine_name = ""
        self.fallback = None
        self.loading = None
        self.error = None
        self.lock = threading.Lock()

    @property
    def wanted(self):
        return auto_engine(self.language) if self.choice == "auto" else self.choice

    def describe(self):
        if self.engine is not None:
            return self.engine_name
        if self.loading is not None and self.loading.is_alive():
            return f"{label(self.wanted).split(' - ')[0]} getting ready (Windows recognizer meanwhile)"
        return "Windows speech recognizer" + (f" ({self.error})" if self.error else "")

    def prepare(self, wait=False):
        """Start downloading/loading the chosen engine in the background (call at startup)."""
        with self.lock:
            if self.engine is not None or (self.loading is not None and self.loading.is_alive()):
                pass
            else:
                self.loading = threading.Thread(target=self._load, name="buddy-speech-load", daemon=True)
                self.loading.start()
        if wait and self.loading is not None:
            self.loading.join()

    def _load(self):
        wanted = self.wanted
        started = time.perf_counter()
        try:
            if wanted in ENGINES:
                if not is_ready(wanted):
                    download(wanted, self.on_status)
                engine = SherpaSpeech(wanted)
                name = label(wanted).split(" - ")[0]
            else:
                try:                                       # VAD for the old engines too, when it can be had
                    download("vad-only", lambda _text: None)
                except Exception:
                    logging.info("Voice detector download failed; old engine without it", exc_info=True)
                engine = self.legacy_factory(wanted, self.language)
                name = label(wanted).split(" (")[0]
            self.engine, self.engine_name, self.error = engine, name, None
            logging.info("Speech engine %s ready in %.1fs", name, time.perf_counter() - started)
            self.on_status(f"Speech: {name}, on this PC.")
        except Exception as error:                          # network blocked, disk full, missing package...
            logging.warning("Speech engine %s unavailable: %s", wanted, error, exc_info=True)
            self.error = _short(error)
            if wanted in ENGINES and self.legacy_factory is not None:
                try:                                       # next best: Whisper, if its files can be had here
                    self.engine = self.legacy_factory("whisper", self.language)
                    self.engine_name = "Whisper base.en"
                    self.on_status(f"Speech: {label(wanted).split(' - ')[0]} couldn't load ({self.error}); "
                                   f"using Whisper.")
                    return
                except Exception:
                    logging.info("Whisper fallback unavailable too", exc_info=True)
            self.on_status(f"Speech: {label(wanted).split(' - ')[0]} couldn't load ({self.error}); "
                           f"using the Windows recognizer.")

    def transcribe(self, audio, sample_rate):
        engine = self.engine
        if engine is None:
            if self.loading is None:
                self.prepare()
            engine = self._fallback()
            if not isinstance(engine, SherpaSpeech):
                trimmed = trim_to_speech(audio, sample_rate)
                if trimmed is None:
                    return ""
                audio = trimmed
        elif not isinstance(engine, SherpaSpeech):
            trimmed = trim_to_speech(audio, sample_rate)
            if trimmed is None:
                return ""
            audio = trimmed
        return engine.transcribe(audio, sample_rate)

    def _fallback(self):
        if self.fallback is None:
            self.fallback = self.legacy_factory("windows", self.language)
        return self.fallback


def _short(error):
    text = str(error) or type(error).__name__
    if "403" in text or "407" in text or "502" in text or "urlopen" in text.lower() or "timed out" in text:
        return "download blocked"
    return text[:80]
