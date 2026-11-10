"""Speech in for Buddy: what you said -> text, offline on this PC.

Engines (Voice input tab -> Speech model):
  * Whisper base.en (OpenAI, English): the most accurate for English in Buddy's benchmark, ~210 MB once.
  * Parakeet V3 (NVIDIA, 25 European languages): for German, French, Spanish and the rest, ~490 MB once.
  * SenseVoice (Chinese, English, Cantonese, Japanese, Korean): fast, punctuated, ~170 MB once.
  * Omnilingual (Meta): Indonesian, ~290 MB once.
  * Dolphin (DataoceanAI): Malay, Japanese, Korean, Thai, Vietnamese, ~80 MB once.
  * Whisper base (OpenAI, multilingual): finds out by itself which language is spoken, ~210 MB once.
  * Moonshine (English only): the quickest, ~250 MB once.
  * Whisper (faster-whisper): the old engine; its download (HuggingFace) is blocked on many office networks.
  * Windows: the recognizer built into Windows. Always there, but much less accurate.

All but the last two come from GitHub release files (k2-fsa/sherpa-onnx) and run with sherpa-onnx on the CPU.
GitHub is reachable from offices where HuggingFace is not, which is why "auto" no longer silently ends up
on the Windows recognizer there.

Every engine gets Silero voice activity detection first (0.6 MB): the recording is trimmed to the part where
someone speaks, and a recording without speech (a key click, a fan, a breath) gives no text at all, so
nothing made up ("Thank you.") is ever typed or asked.

A long dictation is decoded in parts (Cutter): it is cut at the first pause after 15 seconds and after 25
seconds at the latest, because Whisper drops whatever comes after 30 seconds. A part keeps up to two seconds
of the pause before it, so a pause of ordinary length loses no audio. While the dictation key is held, the
parts that are complete are decoded already (Hold), so letting go only waits for the last one.
"""
import collections
import contextlib
import ctypes
import logging
import os
import shutil
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

import keyit_script

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
    "omnilingual": {
        "label": "Omnilingual - Indonesian (and many more languages)",
        "archive": "sherpa-onnx-omnilingual-asr-1600-languages-300M-ctc-v2-int8-2026-02-05",
        "mb": 290,
        "languages": "id",
    },
    "dolphin": {
        "label": "Dolphin - Malay, Japanese, Korean, Thai, Vietnamese",
        "archive": "sherpa-onnx-dolphin-base-ctc-multi-lang-int8-2025-04-02",
        "mb": 80,
        "languages": "ms ja ko th vi",
    },
    "whisper-multi": {
        "label": "Whisper base - detects the language (less accurate than choosing it)",
        "archive": "sherpa-onnx-whisper-base",
        "mb": 210,
        "languages": "auto",
        "keep": ("int8.onnx", "tokens.txt"),
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
    "omnilingual": "Omnilingual - Indonesian",
    "dolphin": "Dolphin - Malay, Japanese, Korean, Thai",
    "whisper-multi": "Whisper base - detects the language",
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


# speech language -> engine, each one the best measured on real recordings (bench/README.md)
ROUTES = {
    "en": "whisper-en",
    "zh": "sensevoice", "cmn": "sensevoice", "yue": "sensevoice",
    "id": "omnilingual",
    "ms": "dolphin", "ja": "dolphin", "ko": "dolphin", "th": "dolphin", "vi": "dolphin",
    "auto": "whisper-multi",
}
WHISPER_LANGUAGES = {"en", "id", "ms", "zh", "ja", "ko", "th", "vi", "de", "es", "fr"}
WINDOWS_LANGUAGES = {"en", "de", "fr", "es", "ja", "zh"}      # what the Windows recognizer exists for


def primary(language):
    """'zh-TW' -> 'zh', 'yue' -> 'yue', 'auto' -> 'auto', nothing -> 'en'."""
    return (language or "en").replace("_", "-").split("-")[0].lower()


def auto_engine(language):
    """'auto' -> the engine for the speech language: Whisper for English, SenseVoice for Chinese and Cantonese,
    Omnilingual for Indonesian, Dolphin for Malay/Japanese/Korean/Thai/Vietnamese, multilingual Whisper when
    the language itself is to be detected, Parakeet for the other European languages."""
    return ROUTES.get(primary(language), "parakeet")


def whisper_language(language):
    """What multilingual Whisper is told: the chosen language, or '' so that it detects the language."""
    code = primary(language)
    code = {"yue": "zh", "cmn": "zh"}.get(code, code)
    return code if code in WHISPER_LANGUAGES else ""


def windows_can_hear(language):
    """The recognizer built into Windows exists for a handful of languages only (not Indonesian, Korean...)."""
    return primary(language) in WINDOWS_LANGUAGES


# what the Voice input tab offers as Language, in the order shown: name -> the code saved as speech_language
LANGUAGES = {
    "English": "en-US",
    "Indonesian (Bahasa Indonesia)": "id-ID",
    "Chinese, Simplified (\u7b80\u4f53)": "zh-CN",
    "Chinese, Traditional (\u7e41\u9ad4)": "zh-TW",
    "Cantonese (\u5ee3\u6771\u8a71)": "yue",
    "Japanese (\u65e5\u672c\u8a9e)": "ja-JP",
    "Korean (\ud55c\uad6d\uc5b4)": "ko-KR",
    "Malay (Bahasa Melayu)": "ms-MY",
    "Thai (\u0e44\u0e17\u0e22)": "th-TH",
    "Vietnamese (Ti\u1ebfng Vi\u1ec7t)": "vi-VN",
    "German (Deutsch)": "de-DE",
    "Spanish (Espa\u00f1ol)": "es-ES",
    "French (Fran\u00e7ais)": "fr-FR",
    "Other European": "eu",
    "Auto-detect": "auto",
}
NEWER = {"id", "ms", "ja", "ko", "th", "vi"}        # engines new in 1.3: more mistakes than English or Chinese
ENGLISH_ONLY = {"whisper-en", "moonshine", "whisper"}


def language_name(code):
    """The picker entry for a saved speech_language: the exact code, else the same language (zh-HK -> the
    Traditional entry), else English."""
    wanted = (code or "").replace("_", "-").lower()
    for name, known in LANGUAGES.items():
        if known.lower() == wanted:
            return name
    same = [name for name, known in LANGUAGES.items() if primary(known) == primary(wanted)]
    for name in same:
        if keyit_script.script(LANGUAGES[name]) == keyit_script.script(wanted):
            return name
    return same[0] if same else next(iter(LANGUAGES))


def language_code(name):
    return LANGUAGES.get(name, "en-US")


def engine_name(choice):
    """'Whisper base.en', 'Parakeet V3', 'Windows speech recognizer'...: the short name used in messages."""
    return label(choice).split(" - ")[0].split(" (")[0]


def language_note(language, choice="auto"):
    """One line under the language picker: which engine hears this language and what to expect from it."""
    best = auto_engine(language)
    code = primary(language)
    if choice in ENGLISH_ONLY and code != "en":      # a warning promises no characters: nothing useful gets typed
        return f"{engine_name(choice)} only hears English. Choose Auto to use {engine_name(best)} for this language."
    if choice == "windows" and not windows_can_hear(language):
        return f"Windows has no recognizer for this language. Choose Auto to use {engine_name(best)}."
    if choice not in ("auto", best):
        note = f"You picked the speech model yourself. Auto would use {engine_name(best)} for this language."
    elif code == "auto":
        note = (f"{engine_name(best)} works out the language of each recording. "
                "Picking your language is more accurate.")
    elif code == "eu":
        note = (f"Heard by {engine_name(best)}, which knows 25 European languages; "
                "only German, Spanish and French were measured.")
    elif code in NEWER:
        note = (f"Heard by {engine_name(best)}, new in this version: expect more mistakes than in English "
                "or Chinese - check names and numbers.")
    else:
        note = f"Heard by {engine_name(best)}."
    written = keyit_script.script(language)
    if written:
        note += f" Typed in {written.capitalize()} Chinese characters."
    elif code == "auto":
        note += " Chinese is typed in the characters the model writes."
    return note


def download_note(engine, language):
    """What to expect while a model that is not on this PC yet downloads."""
    size = ENGINES[engine]["mb"]
    if windows_can_hear(language):
        return f"downloading ~{size} MB once, the Windows recognizer helps meanwhile"
    return f"downloading ~{size} MB once, voice input waits until it is ready"


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


MEGABYTE = 1_000_000
SPEED_WINDOW = 5.0            # seconds of download the speed is averaged over
TELL_EVERY = 0.5              # seconds between two progress lines


def _megabytes(count):
    megabytes = count / MEGABYTE
    return f"{megabytes:.1f}" if megabytes < 10 else f"{megabytes:.0f}"


def _time_left(seconds):
    if seconds < 5:
        return "a few seconds left"
    if seconds < 90:
        return f"about {max(5, round(seconds / 5) * 5)} s left"
    return f"about {round(seconds / 60)} min left"


def progress_text(what, done, total=0, speed=0.0):
    """The status line of a download: how far, of how much, how fast and how long it still takes.
    'Downloading the speech model (once)... 42% (88 of 208 MB, 3.1 MB/s, about 40 s left)'"""
    if not total:
        return f"Downloading {what}... {_megabytes(done)} MB so far"
    text = f"Downloading {what}... {done * 100 // total}% ({_megabytes(done)} of {_megabytes(total)} MB"
    if speed > 0:
        text += f", {speed / MEGABYTE:.1f} MB/s"
        if done < total:
            text += f", {_time_left((total - done) / speed)}"
    return text + ")"


def _fetch(url, target, progress, what, attempts=6):
    """Download url -> target. Office proxies often cut long downloads; this resumes where it stopped
    (HTTP Range) and retries with a short pause, so a 490 MB model still arrives in one go for the user."""
    partial = target.with_name(target.name + ".part")
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
                marks = collections.deque([(time.monotonic(), done)])      # (when, bytes so far): the speed
                told = 0.0
                with open(partial, "ab" if done else "wb") as handle:
                    while True:
                        block = response.read(1 << 18)
                        if not block:
                            break
                        handle.write(block)
                        done += len(block)
                        now = time.monotonic()
                        marks.append((now, done))
                        while len(marks) > 2 and now - marks[0][0] > SPEED_WINDOW:
                            marks.popleft()
                        if now - told >= TELL_EVERY or done == total:
                            told = now
                            elapsed = now - marks[0][0]
                            speed = (done - marks[0][1]) / elapsed if elapsed >= 1 else 0.0
                            progress(progress_text(what, done, total, speed))
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
            _fetch(GITHUB + archive.name, archive, progress, "the speech model (once)")
        try:
            _unpack(archive, folder, info["archive"], info.get("keep"), progress)
        except (tarfile.TarError, EOFError):
            archive.unlink(missing_ok=True)                     # a damaged file: fetch it again next time
            raise
        archive.unlink(missing_ok=True)
        progress("Speech model ready.")


class _Watched:
    """The archive while it is being read: tells how far the unpacking is, in percent."""

    def __init__(self, handle, size, progress):
        self.handle, self.size, self.progress = handle, size, progress
        self.done = 0
        self.shown = -1

    def read(self, count=-1):
        block = self.handle.read(count)
        self.done += len(block)
        percent = min(100, self.done * 100 // self.size) if self.size else 100
        if percent != self.shown:
            self.shown = percent
            self.progress(f"Unpacking the speech model... {percent}%")
        return block


def _unpack(archive, folder, name, keep=None, progress=lambda text: None):
    """Unpack archive (.tar.bz2) so that folder/name only appears once it is complete.

    One pass over the compressed stream: listing the files first, as before, decompressed everything twice.
    The files go to a staging folder that is renamed at the end, so an unpack cut short by a shutdown or a
    full disk can never look like a model that is ready."""
    staging = folder / (name + ".unpacking")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with open(archive, "rb") as handle:
        watched = _Watched(handle, archive.stat().st_size, progress)
        with tarfile.open(fileobj=watched, mode="r|bz2") as tar:
            for member in tar:
                if member.name.startswith(("/", "..")) or ".." in member.name:
                    continue
                if keep is not None and not (member.isdir() or member.name.endswith(keep)):
                    continue
                try:
                    tar.extract(member, staging, filter="data")
                except TypeError:                               # Python < 3.12
                    tar.extract(member, staging)
    unpacked = staging / name
    if not unpacked.is_dir():                                   # an archive whose folder has another name
        inside = [entry for entry in staging.iterdir() if entry.is_dir()]
        if len(inside) != 1:
            raise OSError(f"{archive.name} holds no {name} folder")
        unpacked = inside[0]
    final = folder / name
    for attempt in range(3):
        try:
            shutil.rmtree(final, ignore_errors=True)            # a half-unpacked folder from an older version
            unpacked.replace(final)
            break
        except OSError:                                         # a virus scanner still reading the new files
            if attempt == 2:
                raise
            time.sleep(1)
    shutil.rmtree(staging, ignore_errors=True)


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
    SPLIT = 10.0          # s without a pause after which a stretch of speech ends at the slightest dip. With
                          # the 20 s of sherpa-onnx, speech without pauses came in stretches of over 25 s,
                          # which Cutter could only cut in the middle of a word.

    def __init__(self, threshold=0.5):
        import sherpa_onnx

        self.sherpa = sherpa_onnx
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(models_dir() / VAD_FILE)
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = 0.3
        config.silero_vad.min_speech_duration = 0.15
        config.silero_vad.max_speech_duration = self.SPLIT
        config.sample_rate = RATE
        config.num_threads = 1
        self.config = config

    def span(self, audio16k):
        """(start, end) in samples around all speech, or None when nobody spoke."""
        listening = self.listen(max(30, len(audio16k) // RATE + 5))
        found = listening.hear(audio16k) + listening.finish()
        if not found:
            return None
        pad = int(self.PAD * RATE)
        first = min(start for start, _ in found)
        last = max(end for _, end in found)
        return max(0, first - pad), min(len(audio16k), last + pad)

    def listen(self, seconds=60):
        """A recording that is heard piece by piece (see Listening); `seconds` is the longest stretch of
        speech it has to hold."""
        return Listening(self, seconds)


class Listening:
    """The voice detector on one recording that arrives in pieces. `hear` returns the stretches of speech
    that have ended since the last call, as (start, end) in samples from the start of the recording;
    `finish` returns the ones that were still going on when the recording ended."""

    def __init__(self, detector, seconds=60):
        self.vad = detector.sherpa.VoiceActivityDetector(detector.config, buffer_size_in_seconds=seconds)
        self.window = detector.config.silero_vad.window_size
        self.left = np.zeros(0, dtype=np.float32)           # less than one window, waiting for the rest
        self.heard = 0                                      # samples the detector has had

    @property
    def since(self):
        """Where the speech that is going on right now began; None when nobody is speaking."""
        if not self.vad.is_speech_detected():
            return None
        return self.vad.current_segment.start

    def hear(self, audio16k):
        audio = np.concatenate([self.left, np.asarray(audio16k, dtype=np.float32)])
        whole = len(audio) - len(audio) % self.window
        found = []
        for i in range(0, whole, self.window):
            self.vad.accept_waveform(audio[i:i + self.window])
            found += self._ended()
        self.left = audio[whole:]
        self.heard += whole
        return found

    def finish(self):
        if len(self.left):
            self.vad.accept_waveform(self.left)
            self.heard += len(self.left)
            self.left = self.left[:0]
        found = self._ended()
        self.vad.flush()
        return found + self._ended()

    def _ended(self):
        found = []
        while not self.vad.empty():
            segment = self.vad.front
            found.append((segment.start, segment.start + len(segment.samples)))
            self.vad.pop()
        return found


# --------------------------------------------------------------------------- engines
class SherpaSpeech:
    """Whisper / Parakeet / SenseVoice / Omnilingual / Dolphin / Moonshine through sherpa-onnx, with Silero
    VAD in front. `detected` is the language the engine heard in the last recording ('' when it doesn't say)."""

    def __init__(self, engine, language=None):
        import sherpa_onnx

        self.engine = engine
        self.detected = ""
        self.busy = threading.Lock()                        # one decode at a time: Hold decodes in a thread
        folder = models_dir() / ENGINES[engine]["archive"]
        if engine in ("whisper-en", "whisper-multi"):
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=_find(folder, "*encoder.int8.onnx", "*encoder.onnx"),
                decoder=_find(folder, "*decoder.int8.onnx", "*decoder.onnx"),
                tokens=_find(folder, "*tokens.txt"),
                language="en" if engine == "whisper-en" else whisper_language(language),
                task="transcribe", num_threads=THREADS)
        elif engine == "dolphin":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_dolphin_ctc(
                model=_find(folder, "model.int8.onnx", "model.onnx"), tokens=_find(folder, "tokens.txt"),
                num_threads=THREADS)
        elif engine == "omnilingual":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_omnilingual_asr_ctc(
                model=_find(folder, "model.int8.onnx", "model.onnx"), tokens=_find(folder, "tokens.txt"),
                num_threads=THREADS)
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
        self.decode(np.zeros(RATE // 2, dtype=np.float32))

    def transcribe(self, audio, sample_rate):
        held = Held(self, sample_rate)                      # a finished recording: one that came in one piece
        held.hear(audio)
        return held.finish()

    def decode(self, audio16k):
        """The text of one stretch of speech (16 kHz), and the language the model heard ('': it doesn't say)."""
        with self.busy:
            stream = self.recognizer.create_stream()
            stream.accept_waveform(RATE, audio16k)
            self.recognizer.decode_stream(stream)
            result = stream.result
        language = str(getattr(result, "lang", "") or "").strip().strip("<|>").lower()             # '<|yue|>'
        return result.text.strip(), language


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


def _low_pass(ratio):
    """The windowed-sinc filter that takes out what the lower sample rate cannot carry."""
    cutoff = ratio * 0.95
    reach = 16 * int(np.ceil(1 / ratio))
    taps = np.arange(-reach, reach + 1)
    kernel = cutoff * np.sinc(cutoff * taps) * np.hanning(len(taps))
    return kernel / kernel.sum()


def resample(audio, source_rate, target_rate):
    """Windowed-sinc resampling (clean enough for speech recognition), numpy only."""
    audio = np.asarray(audio, dtype=np.float32)
    if source_rate == target_rate or audio.size == 0:
        return audio
    ratio = target_rate / source_rate
    if ratio < 1:                                           # low-pass at the new Nyquist before decimating
        audio = np.convolve(audio, _low_pass(ratio), mode="same").astype(np.float32)
    positions = np.arange(0, audio.size, source_rate / target_rate)
    return np.interp(positions, np.arange(audio.size), audio).astype(np.float32)


class Stream16k:
    """resample() to 16 kHz for a recording that arrives in pieces: what `feed` and `finish` return adds up
    to what resample() makes of the whole recording."""

    def __init__(self, source_rate):
        self.rate = int(source_rate)
        self.step = self.rate / RATE                        # source samples per 16 kHz sample
        self.kernel = _low_pass(RATE / self.rate) if self.rate > RATE else None
        self.reach = len(self.kernel) // 2 if self.kernel is not None else 0
        self.audio = np.zeros(0, dtype=np.float32)          # source audio that the next samples still need
        self.first = 0                                      # where self.audio starts in the recording
        self.made = 0                                       # 16 kHz samples returned so far

    def feed(self, audio):
        audio = np.asarray(audio, dtype=np.float32)
        if self.rate == RATE:
            return audio
        self.audio = np.concatenate([self.audio, audio])
        last = self.first + len(self.audio) - self.reach - 3    # the filter and the interpolation look ahead
        return self._make(last * RATE // self.rate + 1 if last >= 0 else 0)

    def finish(self):
        """The last samples: the ones that had to wait for audio that never came."""
        if self.rate == RATE:
            return self.audio
        return self._make(int(np.ceil((self.first + len(self.audio)) / self.step)))

    def _make(self, upto):
        if upto <= self.made:
            return np.zeros(0, dtype=np.float32)
        clean = self.audio
        if self.kernel is not None:
            clean = np.convolve(clean, self.kernel)[self.reach:self.reach + len(clean)].astype(np.float32)
        positions = np.arange(self.made, upto) * self.step
        made = np.interp(positions, self.first + np.arange(len(clean)), clean).astype(np.float32)
        self.made = upto
        keep = max(self.first, upto * self.rate // RATE - self.reach - 2)
        self.audio = self.audio[keep - self.first:]
        self.first = keep
        return made


class Cutter:
    """Where a long dictation is cut into parts that are decoded one by one.

    A part ends at the first pause after EARLY seconds, so that most of a long dictation is text before the
    key is let go, and it holds LONGEST seconds at most: Whisper drops whatever comes after 30 seconds, and
    the other models were trained on less. What is shorter than EARLY is never cut, so an ordinary dictation
    is decoded in one piece, with everything that was said as context.

    A part after the first keeps up to KEEP seconds of the pause before it. A pause of KEEP + pad seconds or
    less therefore loses no audio: what the detector took for silence there still reaches the recognizer.
    Before the first speech only the pad is kept, as for a dictation that is not cut at all.

    Everything is counted in 16 kHz samples from the start of the recording."""

    PAUSE = 0.7                                             # seconds of silence that end a part ...
    EARLY = 15.0                                            # ... once the part holds this many seconds
    LONGEST = 25.0                                          # seconds in one part, its pauses included
    LEAD = 0.3                                              # speech is noticed within this many seconds
    KEEP = 2.0                                              # seconds of the pause before a later part

    def __init__(self, pad=VoiceDetector.PAD):
        self.pad = int(pad * RATE)
        self.start = self.end = None                        # the speech in the part that is still open
        self.floor = 0                                      # the audio before this is in an earlier part

    def add(self, start, end):
        """Speech from `start` to `end` has ended. Returns the parts this closes, as (first, last) audio."""
        closed = []
        longest = int(self.LONGEST * RATE)
        if self.start is not None and (self._paused(start) or end - self.start > longest):
            closed.append(self._close((self.end + start) // 2))
        while end - start > longest:                        # nobody paused for LONGEST seconds: cut anyway
            self.start, self.end = start, start + longest
            start += longest
            closed.append(self._close(start))
        if self.start is None:
            self.start = start
        self.end = end
        return closed

    def early(self, now, since=None):
        """What add() is going to decide about the open part, as soon as that is certain: its decoding can
        then start while the next speech is still going on. `now` is how far the recording has been heard,
        `since` where the speech that is going on began (None when nobody is speaking)."""
        if self.start is None:
            return []
        if since is None:                                   # a pause, and nothing in it that could be speech
            return [self._close(now)] if self._paused(now - int(self.LEAD * RATE)) else []
        if self._paused(since) or now - self.start > self.LONGEST * RATE:
            return [self._close((self.end + since) // 2)]
        return []

    def rest(self, total):
        """The recording ended after `total` samples: the part that is still open."""
        return [self._close(total)] if self.start is not None else []

    def _paused(self, start):
        """Would speech that begins at `start` come after a pause that ends the open part?"""
        return start - self.end >= self.PAUSE * RATE and self.end - self.start >= self.EARLY * RATE

    def _close(self, limit):
        lead = int(self.KEEP * RATE) if self.floor else self.pad
        first = max(self.start - lead, self.floor)
        last = min(self.end + self.pad, limit)
        self.floor = last
        self.start = self.end = None
        return first, last


def _unspaced(char):
    """Chinese and Japanese (and their punctuation) are written without spaces between the words."""
    return "\u3000" <= char <= "\u9fff" or "\uff00" <= char <= "\uffef"


def join_parts(parts):
    """The texts of the parts of one dictation as one text."""
    text = ""
    for part in parts:
        part = part.strip()
        if text and part and not (_unspaced(text[-1]) and _unspaced(part[0])):
            text += " "
        text += part
    return text


class Held:
    """One recording on its way through a SherpaSpeech engine: `hear` takes the audio as it comes in and
    decodes every part of it that is complete (see Cutter), `finish` decodes the rest and returns the text."""

    def __init__(self, engine, sample_rate):
        self.engine = engine
        self.stream = Stream16k(sample_rate)
        self.listening = engine.vad.listen()
        self.cutter = Cutter(engine.vad.PAD)
        self.audio = np.zeros(0, dtype=np.float32)          # 16 kHz audio that is not text yet
        self.first = 0                                      # where self.audio starts in the recording
        self.texts = []
        self.language = ""                                  # of the first part whose language the model names

    def hear(self, audio):
        fresh = self.stream.feed(audio)
        self.audio = np.concatenate([self.audio, fresh])
        self._ended(self.listening.hear(fresh))
        self._decode(self.cutter.early(self.listening.heard, self.listening.since))

    def finish(self):
        fresh = self.stream.finish()
        self.audio = np.concatenate([self.audio, fresh])
        self._ended(self.listening.hear(fresh) + self.listening.finish())
        self._decode(self.cutter.rest(self.first + len(self.audio)))
        self.engine.detected = self.language
        return join_parts(self.texts)

    def _ended(self, stretches):
        for start, end in stretches:
            self._decode(self.cutter.add(start, end))

    def _decode(self, parts):
        for first, last in parts:
            with urgent():                                  # somebody is waiting for this text, or soon will be
                text, language = self.engine.decode(self.audio[first - self.first:last - self.first])
            self.texts.append(text)
            self.language = self.language or language
            self.audio = self.audio[last - self.first:]     # what has become text needs no memory
            self.first = last


class Hold:
    """A dictation that is decoded while its key is still held.

    Every TICK seconds a thread hands the microphone audio that has come in to a Held, which decodes every
    part of the dictation that is complete. Letting go of the key (`finish`) then only waits for the last
    part, however long the dictation was. bench/latency_hold.py measures that wait."""

    TICK = 0.25

    def __init__(self, speech, engine, mic):
        self.speech = speech
        self.engine = engine
        self.mic = mic
        self.held = None                                    # made by the thread: a key press never waits
        self.blocks = 0                                     # microphone blocks that self.held has had ...
        self.samples = 0                                    # ... and the samples in them
        self.failed = False
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.held = Held(self.engine, self.mic.sample_rate)
            while not self.stop.wait(self.TICK):
                self._hear()
        except Exception:
            logging.warning("decoding while the key is held failed", exc_info=True)
            self.failed = True

    def _hear(self):
        blocks = self.mic.blocks[self.blocks:]
        if blocks:
            audio = np.concatenate(blocks)
            self.blocks += len(blocks)
            self.samples += len(audio)
            self.held.hear(audio)

    def finish(self, audio, sample_rate):
        """The key is let go: the text of the whole recording."""
        self.cancel()
        self.thread.join()
        if not self.failed and self.samples <= len(audio):
            try:
                with urgent():
                    self.held.hear(audio[self.samples:])
                    return self.held.finish()
            except Exception:
                logging.warning("decoding the end of the dictation failed", exc_info=True)
        return self.speech.transcribe(audio, sample_rate)   # all of it once more, in one go

    def cancel(self):
        """The recording is thrown away."""
        self.stop.set()


NORMAL_PRIORITY = 0x00000020                               # Windows priority classes
ABOVE_NORMAL_PRIORITY = 0x00008000


def _swap_priority(expected, wanted):
    """Move this process from one Windows priority class to another. False when it was not in the first
    one (somebody chose a priority for Buddy: leave it alone), and when this is not Windows."""
    if sys.platform != "win32":
        return False
    try:
        kernel = ctypes.WinDLL("kernel32")                 # our own handle: the prototypes stay private
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
        kernel.GetPriorityClass.restype = ctypes.c_ulong
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        process = kernel.GetCurrentProcess()
        if kernel.GetPriorityClass(process) != expected:
            return False
        return bool(kernel.SetPriorityClass(process, wanted))
    except Exception:
        return False


@contextlib.contextmanager
def urgent():
    """Run the block at above-normal priority, then put the process back.

    Between letting go of the key and seeing the text the user is waiting, so the decode should not queue
    up behind whatever else keeps the processor busy. bench/latency_load.py measures what this is worth."""
    raised = _swap_priority(NORMAL_PRIORITY, ABOVE_NORMAL_PRIORITY)
    try:
        yield
    finally:
        if raised:
            _swap_priority(ABOVE_NORMAL_PRIORITY, NORMAL_PRIORITY)


class Speech:
    """What Buddy uses: picks the engine, downloads it in the background, falls back while it isn't there.

    transcribe(audio, rate) never waits for a download: until the chosen engine is ready, the Windows
    recognizer answers where Windows has one for the language (and the status line says so). For the other
    languages (Indonesian, Korean, Thai...) voice input starts when the model is there, and a download that
    failed is tried again on a later use."""

    RETRY_AFTER = 60.0                                # seconds before a failed download is tried again

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
        self.failed_at = 0.0
        self.progress = ""                            # the latest line of a download that is under way
        self.lock = threading.Lock()

    @property
    def wanted(self):
        return auto_engine(self.language) if self.choice == "auto" else self.choice

    def spoken_language(self):
        """The language of the last recording, for the cleanup and spelling that follow: the setting, or with
        auto-detect what the engine heard ('auto' when it didn't say)."""
        if primary(self.language) != "auto":
            return self.language
        return getattr(self.engine, "detected", "") or "auto"

    def describe(self):
        if self.engine is not None:
            return self.engine_name
        windows = windows_can_hear(self.language)
        if self.loading is not None and self.loading.is_alive():
            meanwhile = "Windows recognizer meanwhile" if windows else "voice input starts when it is"
            return f"{label(self.wanted).split(' - ')[0]} getting ready ({meanwhile})"
        state = "Windows speech recognizer" if windows else "Voice input waits for its speech model"
        return state + (f" ({self.error})" if self.error else "")

    def waiting(self):
        """Why nothing can be heard right now, '' when something can: the model for a language Windows has no
        recognizer for is still on its way (with how far its download is), or could not be downloaded."""
        if self.engine is not None or windows_can_hear(self.language):
            return ""
        name = engine_name(self.wanted)
        if self.error and not (self.loading is not None and self.loading.is_alive()):
            return f"{name} couldn't load ({self.error}). Buddy tries again when you next use your voice."
        progress = f" {self.progress}" if self.progress else ""
        return f"{name} is still getting ready - try again in a moment.{progress}"

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

    def _downloading(self, text):
        """A line from the download: the status line shows it, and waiting() repeats the latest one."""
        self.progress = text
        self.on_status(text)

    def _load(self):
        wanted = self.wanted
        started = time.perf_counter()
        try:
            if wanted in ENGINES:
                if not is_ready(wanted):
                    try:
                        download(wanted, self._downloading)
                    finally:
                        self.progress = ""
                engine = SherpaSpeech(wanted, self.language)
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
            self.failed_at = time.monotonic()
            name = label(wanted).split(" - ")[0]
            english = primary(self.language) == "en"       # the old Whisper here is base.en: English only
            if wanted in ENGINES and self.legacy_factory is not None and english:
                try:                                       # next best: Whisper, if its files can be had here
                    self.engine = self.legacy_factory("whisper", self.language)
                    self.engine_name = "Whisper base.en"
                    self.on_status(f"Speech: {name} couldn't load ({self.error}); using Whisper.")
                    return
                except Exception:
                    logging.info("Whisper fallback unavailable too", exc_info=True)
            if windows_can_hear(self.language):
                self.on_status(f"Speech: {name} couldn't load ({self.error}); using the Windows recognizer.")
            else:
                self.on_status(f"Speech: {name} couldn't load ({self.error}); voice input is off until its "
                               f"model can be downloaded. Buddy tries again when you next use your voice.")

    def transcribe(self, audio, sample_rate):
        engine = self.engine
        if engine is None:
            if self.loading is None or (not self.loading.is_alive()
                                        and time.monotonic() - self.failed_at >= self.RETRY_AFTER):
                self.prepare()                             # first use, or a failed download worth another try
            if not windows_can_hear(self.language):
                return ""                                  # Windows has no recognizer for this language
            engine = self._fallback()
        with urgent():                                     # the user is waiting for this text
            if not isinstance(engine, SherpaSpeech):
                trimmed = trim_to_speech(audio, sample_rate)
                if trimmed is None:
                    return ""
                audio = trimmed
            return engine.transcribe(audio, sample_rate)

    def hold(self, mic):
        """Start decoding a dictation while it is still being recorded (see Hold). None when the engine
        cannot do that: the finished recording then goes to transcribe(), as it always did."""
        try:
            if isinstance(self.engine, SherpaSpeech):
                return Hold(self, self.engine, mic)
        except Exception:
            logging.warning("no decoding while the key is held", exc_info=True)
        return None

    def _fallback(self):
        if self.fallback is None:
            self.fallback = self.legacy_factory("windows", self.language)
        return self.fallback


def _short(error):
    text = str(error) or type(error).__name__
    if "403" in text or "407" in text or "502" in text or "urlopen" in text.lower() or "timed out" in text:
        return "download blocked"
    return text[:80]
