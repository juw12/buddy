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

# (label in Settings, Kokoro voice id). The id's first letter is the language, the second the gender.
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
    ("Xiaobei (Chinese woman)", "zf_xiaobei"),
    ("Xiaoni (Chinese woman)", "zf_xiaoni"),
    ("Xiaoxiao (Chinese woman)", "zf_xiaoxiao"),
    ("Xiaoyi (Chinese woman)", "zf_xiaoyi"),
    ("Yunjian (Chinese man)", "zm_yunjian"),
    ("Yunxi (Chinese man)", "zm_yunxi"),
    ("Yunyang (Chinese man)", "zm_yunyang"),
    ("Dora (Spanish woman)", "ef_dora"),
    ("Alex (Spanish man)", "em_alex"),
    ("Siwis (French woman)", "ff_siwis"),
    ("Sara (Italian woman)", "if_sara"),
    ("Nicola (Italian man)", "im_nicola"),
    ("Dora (Portuguese woman, Brazil)", "pf_dora"),
    ("Alex (Portuguese man, Brazil)", "pm_alex"),
    ("Alpha (Hindi woman)", "hf_alpha"),
    ("Beta (Hindi woman)", "hf_beta"),
    ("Omega (Hindi man)", "hm_omega"),
]
DEFAULT = PREFIX + VOICES[0][0]
LANGUAGES = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "p": "pt-br", "z": "zh"}
# when the text's script doesn't fit the chosen voice, this voice of the same gender reads it
PARTNERS = {("zh", "f"): "zf_xiaobei", ("zh", "m"): "zm_yunjian", ("hi", "f"): "hf_alpha", ("hi", "m"): "hm_omega",
            ("en-us", "f"): "af_heart", ("en-us", "m"): "am_michael"}
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
DEVANAGARI = re.compile(r"[\u0900-\u097f]")
LATIN = re.compile(r"[A-Za-z]")
FULL_WIDTH = str.maketrans({"，": ",", "。": ".", "！": "!", "？": "?", "：": ":", "；": ";", "、": ",",
                            "（": "(", "）": ")", "“": '"', "”": '"', "「": '"', "」": '"'})


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


def language_of(voice):
    return LANGUAGES.get(voice[:1], "en-us")


def voice_for(text, voice):
    """(voice, language) to read this text: the chosen voice, unless the text is in another script
    (Chinese answer + English voice -> a Chinese voice of the same gender, and the other way round)."""
    language = language_of(voice)
    gender = voice[1:2] if voice[1:2] in ("f", "m") else "f"
    if HAN.search(text):
        wanted = "zh"
    elif DEVANAGARI.search(text):
        wanted = "hi"
    elif language in ("zh", "hi") and LATIN.search(text):
        wanted = "en-us"                                   # plain English with a Chinese/Hindi voice
    else:
        return voice, language
    if wanted == language:
        return voice, language
    return PARTNERS[(wanted, gender)], wanted


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
    tiny sentences ("Sure!") join the next one, long ones are cut at commas/semicolons.
    Chinese is denser (~0.2 s per character), so its pieces are a third as long."""
    if HAN.search(text):
        max_chars, min_chars = max_chars // 3, min_chars // 3
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", " ".join(text.split())) if s.strip()]
    joined = []
    for sentence in sentences:
        if joined and len(joined[-1]) < min_chars:
            joined[-1] = f"{joined[-1]} {sentence}"
        else:
            joined.append(sentence)
    pieces = []
    for sentence in joined:
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(mark, min_chars, max_chars) for mark in (", ", "; ", ": ", " - ", "，", "、", "；"))
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
        self.zh_g2p = None                     # misaki Chinese G2P, loaded on first Chinese text
        self.failed = None
        self.ready = threading.Event()
        self.on_block = None                   # talk mode: on_block(samples, rate, play_time) before each write
        self.paused = False
        self.making = False
        self.playing = False
        threading.Thread(target=self._make_loop, name="buddy-voice-make", daemon=True).start()
        threading.Thread(target=self._play_loop, name="buddy-voice-play", daemon=True).start()

    # -- public
    def say(self, text):
        for piece in split_text(text):
            self.texts.put((self.generation, piece))

    def stop(self):
        self.generation += 1
        self.paused = False
        for pending in (self.texts, self.audio):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break

    def set_voice(self, voice):
        self.voice = voice

    def pause(self):
        """Hold your breath (talk mode: the user started to speak). resume() carries on mid-sentence."""
        self.paused = True

    def resume(self):
        self.paused = False

    def busy(self):
        """True while there is speech queued, being made or playing."""
        return self.making or self.playing or not self.texts.empty() or not self.audio.empty()

    def _speak(self, text):
        """Audio for one piece, in the language its script needs."""
        voice, language = voice_for(text, self.voice)
        if language != "zh":
            return self.kokoro.create(text, voice=voice, speed=self.speed, lang=language)
        return self.kokoro.create(self._chinese_phonemes(text), voice=voice, speed=self.speed, is_phonemes=True)

    def _chinese_phonemes(self, text):
        """Chinese runs through misaki (pinyin -> IPA, works for traditional characters too),
        English words in between through espeak, punctuation to the ASCII marks the model knows."""
        if self.zh_g2p is None:
            from misaki import zh

            logging.getLogger("jieba").setLevel(logging.WARNING)
            self.zh_g2p = zh.ZHG2P()
        chinese = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff，。！？：；、"
        parts = []
        for run in re.findall(f"[{chinese}]+|[^{chinese}]+", text):
            if HAN.search(run):
                parts.append(self.zh_g2p(run).translate(FULL_WIDTH))
            elif LATIN.search(run):
                parts.append(" " + self.kokoro.tokenizer.phonemize(run.strip(), "en-us") + " ")
            else:
                parts.append(run.translate(FULL_WIDTH))
        return " ".join("".join(parts).split())

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
            self.making = True
            while len(text) < 30:                  # "Sure!" alone: wait a moment for the next sentence
                try:
                    more_generation, more = self.texts.get(timeout=0.8)
                except queue.Empty:
                    break
                if more_generation == generation:
                    text = f"{text} {more}"
            try:
                started = time.perf_counter()
                samples, rate = self._speak(text)
                took = time.perf_counter() - started
                logging.debug("Voice: %.1fs audio in %.1fs", len(samples) / rate, took)
            except Exception:
                logging.exception("Natural voice failed on %r", text[:60])
                self.making = False
                continue
            if generation == self.generation:
                self.audio.put((generation, samples, rate))
            self.making = False

    def _play_loop(self):
        import sounddevice

        while True:
            generation, samples, rate = self.audio.get()
            if generation != self.generation:
                continue
            self.playing = True
            try:
                with sounddevice.OutputStream(samplerate=rate, channels=1, dtype="float32") as stream:
                    self._play(stream, generation, samples, rate)
            except Exception:
                logging.exception("Could not play the natural voice")
            finally:
                self.playing = False

    def _play(self, stream, generation, samples, rate):
        block = rate // 20                         # 0.05 s: stop() and pause() react this fast
        written_until = 0.0                        # when the last block written will have been played
        start = 0
        while start < len(samples):
            if generation != self.generation:
                stream.abort()
                return
            if self.paused:
                stream.abort()                     # drop what is buffered: silence now, not in 0.1 s
                while self.paused and generation == self.generation:
                    time.sleep(0.02)
                if generation != self.generation:
                    return
                start = max(0, start - int(float(stream.latency) * rate))   # say the dropped part again
                stream.start()
                continue
            piece = samples[start:start + block].astype("float32")
            listener = self.on_block
            if listener is not None:
                written_until = max(time.monotonic(), written_until)
                listener(piece, rate, written_until + float(stream.latency))
                written_until += len(piece) / rate
            stream.write(piece.reshape(-1, 1))
            start += block
