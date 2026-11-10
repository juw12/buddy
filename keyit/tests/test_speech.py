"""Speech in: engine choice, settings migration, fallbacks, voice detection, resampling, dictation helpers."""
import io
import os
import sys
import tarfile
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyit_speech as speech  # noqa: E402


def test_auto_picks_engine_for_language():
    assert speech.auto_engine("en-US") == "whisper-en"
    assert speech.auto_engine("en-GB") == "whisper-en"
    assert speech.auto_engine("de-DE") == "parakeet"
    assert speech.auto_engine("eu") == "parakeet"
    assert speech.auto_engine("zh-CN") == "sensevoice"
    assert speech.auto_engine("zh-TW") == "sensevoice"
    assert speech.auto_engine("yue") == "sensevoice"
    # measured on real recordings (bench/README.md): SenseVoice writes Japanese and Korean as Chinese
    # characters (81% and 76% of the characters wrong), Dolphin gets 22% and 14% wrong
    assert speech.auto_engine("ja-JP") == "dolphin"
    assert speech.auto_engine("ko-KR") == "dolphin"
    assert speech.auto_engine("id-ID") == "omnilingual"
    for language in ("ms-MY", "th-TH", "vi-VN"):
        assert speech.auto_engine(language) == "dolphin"
    assert speech.auto_engine("auto") == "whisper-multi"
    assert speech.auto_engine("fr-FR") == "parakeet"


def test_language_helpers():
    assert speech.primary("zh-TW") == "zh" and speech.primary("yue") == "yue" and speech.primary(None) == "en"
    assert speech.whisper_language("id-ID") == "id"
    assert speech.whisper_language("yue") == "zh"
    assert speech.whisper_language("auto") == "" and speech.whisper_language("eu") == ""    # '' = detect it
    assert speech.windows_can_hear("en-US") and speech.windows_can_hear("zh-TW")
    assert not speech.windows_can_hear("id-ID") and not speech.windows_can_hear("auto")


def test_every_engine_has_what_settings_needs():
    for key, info in speech.ENGINES.items():
        assert key in speech.SHORT_LABELS
        assert info["archive"].startswith("sherpa-onnx-") and info["mb"] > 0
    assert set(speech.ROUTES.values()) <= set(speech.ENGINES)


def test_migrate_old_whisper_setting():
    assert speech.migrate({"whisper_model": "auto"})["speech_model"] == "auto"
    assert speech.migrate({"whisper_model": "windows"})["speech_model"] == "windows"
    assert speech.migrate({"whisper_model": "small.en"})["speech_model"] == "whisper"
    assert speech.migrate({})["speech_model"] == "auto"
    assert speech.migrate({"speech_model": "bogus"})["speech_model"] == "auto"
    assert speech.migrate({"speech_model": "sensevoice", "whisper_model": "windows"})["speech_model"] == "sensevoice"


def test_labels_round_trip():
    for choice in speech.CHOICES:
        assert speech.choice_for_short_label(speech.SHORT_LABELS[choice]) == choice
        assert speech.choice_for_label(speech.label(choice)) == choice


def test_models_come_from_github_not_huggingface():
    assert speech.GITHUB.startswith("https://github.com/")
    for info in speech.ENGINES.values():
        assert "huggingface" not in info["archive"]


def test_resample_keeps_pitch_and_length():
    rate = 48000
    t = np.arange(rate) / rate
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = speech.resample(tone, rate, 16000)
    assert abs(len(out) - 16000) <= 1
    spectrum = np.abs(np.fft.rfft(out))
    assert abs(np.argmax(spectrum) * 16000 / len(out) - 440) < 3
    # energy above the new Nyquist is filtered out instead of folding back as noise
    high = np.sin(2 * np.pi * 15000 * t).astype(np.float32)
    assert np.sqrt(np.mean(speech.resample(high, rate, 16000) ** 2)) < 0.05


class FakeEngine:
    def __init__(self, name):
        self.name = name
        self.calls = 0

    def transcribe(self, audio, rate):
        self.calls += 1
        return f"{self.name} heard {len(audio)}"


def test_speech_never_waits_for_download_and_uses_windows_meanwhile(monkeypatch):
    gate = threading.Event()
    made = {}

    def slow_load(self):
        gate.wait(5)
        self.engine, self.engine_name = FakeEngine("parakeet"), "Parakeet V3"

    monkeypatch.setattr(speech.Speech, "_load", slow_load)
    monkeypatch.setattr(speech, "trim_to_speech", lambda audio, rate: audio)
    factory = lambda kind, language: made.setdefault(kind, FakeEngine(kind))
    s = speech.Speech("auto", "en-US", legacy_factory=factory)
    s.prepare()
    started = time.perf_counter()
    assert s.transcribe(np.zeros(1600, dtype=np.float32), 16000).startswith("windows heard")
    assert time.perf_counter() - started < 1.0                    # no waiting for the download
    assert "getting ready" in s.describe()
    gate.set()
    s.loading.join(5)
    assert s.transcribe(np.zeros(1600, dtype=np.float32), 16000).startswith("parakeet heard")


def test_failed_engine_falls_back_to_whisper_then_windows(monkeypatch):
    def broken_download(engine, progress):
        raise OSError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(speech, "download", broken_download)
    monkeypatch.setattr(speech, "is_ready", lambda engine: False)
    statuses = []
    made = []

    def whisper_ok(kind, language):
        made.append(kind)
        return FakeEngine(kind)

    s = speech.Speech("parakeet", "en-US", on_status=statuses.append, legacy_factory=whisper_ok)
    s.prepare(wait=True)
    assert s.engine.name == "whisper" and s.error == "download blocked"
    assert any("using Whisper" in text for text in statuses)

    def nothing_works(kind, language):
        if kind == "whisper":
            raise OSError("huggingface blocked")
        return FakeEngine(kind)

    s = speech.Speech("parakeet", "en-US", on_status=statuses.append, legacy_factory=nothing_works)
    s.prepare(wait=True)
    assert s.engine is None
    monkeypatch.setattr(speech, "trim_to_speech", lambda audio, rate: audio)
    assert s.transcribe(np.zeros(800, dtype=np.float32), 16000).startswith("windows heard")


def test_fetch_resumes_after_dropped_connection(tmp_path, monkeypatch):
    payload = bytes(range(256)) * 4000                           # ~1 MB
    calls = []

    class Response:
        def __init__(self, start, cut):
            self.status = 206 if start else 200
            self.data = payload[start:cut]
            self.headers = {"Content-Length": str(len(payload) - start)}
            self.pos = 0

        def read(self, size):
            block = self.data[self.pos:self.pos + size]
            self.pos += len(block)
            return block

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, request, timeout):
            header = request.get_header("Range") or ""
            start = int(header.split("=")[1].rstrip("-")) if header else 0
            calls.append(start)
            cut = len(payload) // 3 if not header else len(payload)      # first try is cut off
            return Response(start, cut)

    monkeypatch.setattr(speech, "_opener", lambda: Opener())
    monkeypatch.setattr(speech.time, "sleep", lambda s: None)
    target = tmp_path / "model.tar.bz2"
    speech._fetch("https://github.com/x", target, lambda text: None, "test")
    assert target.read_bytes() == payload
    assert calls == [0, len(payload) // 3]                         # second request resumed, not restarted


def test_spoken_language_is_the_setting_or_what_was_detected():
    s = speech.Speech("auto", "zh-TW")
    assert s.spoken_language() == "zh-TW"
    s = speech.Speech("auto", "auto")
    assert s.spoken_language() == "auto"                           # nothing heard yet
    s.engine = types.SimpleNamespace(detected="id")
    assert s.spoken_language() == "id"
    s.engine = FakeEngine("whisper")                               # an engine that doesn't say
    assert s.spoken_language() == "auto"


def test_no_english_only_fallback_for_other_languages(monkeypatch):
    def broken_download(engine, progress):
        raise OSError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(speech, "download", broken_download)
    monkeypatch.setattr(speech, "is_ready", lambda engine: False)
    statuses = []
    made = []

    def factory(kind, language):
        made.append(kind)
        return FakeEngine(kind)

    s = speech.Speech("auto", "id-ID", on_status=statuses.append, legacy_factory=factory)
    s.prepare(wait=True)
    assert s.engine is None and made == []                         # Whisper base.en can't hear Indonesian
    assert "voice input is off" in statuses[-1] and "Windows recognizer" not in statuses[-1]
    assert s.transcribe(np.zeros(800, dtype=np.float32), 16000) == ""
    assert made == []                                              # and Windows has no Indonesian recognizer
    assert "Windows" not in s.describe()

    s = speech.Speech("auto", "de-DE", on_status=statuses.append, legacy_factory=factory)
    s.prepare(wait=True)
    assert s.engine is None and made == []                         # German: no English-only Whisper either...
    assert "using the Windows recognizer" in statuses[-1]          # ...but Windows has a German recognizer


def test_waits_honestly_while_a_model_for_another_language_downloads(monkeypatch):
    gate = threading.Event()
    made = []

    def slow_load(self):
        gate.wait(5)

    monkeypatch.setattr(speech.Speech, "_load", slow_load)
    s = speech.Speech("auto", "id-ID", legacy_factory=lambda kind, language: made.append(kind))
    s.prepare()
    assert s.transcribe(np.zeros(1600, dtype=np.float32), 16000) == ""
    assert made == []
    assert "voice input starts when it is" in s.describe() and "Windows" not in s.describe()
    gate.set()
    s.loading.join(5)


def test_failed_download_is_tried_again_later(monkeypatch):
    attempts = []

    def broken_download(engine, progress):
        attempts.append(engine)
        raise OSError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(speech, "download", broken_download)
    monkeypatch.setattr(speech, "is_ready", lambda engine: False)
    s = speech.Speech("auto", "id-ID")
    s.prepare(wait=True)
    assert attempts == ["omnilingual"]
    s.transcribe(np.zeros(800, dtype=np.float32), 16000)           # right after the failure: not again yet
    s.loading.join(5)
    assert attempts == ["omnilingual"]
    s.failed_at -= s.RETRY_AFTER                                   # a minute later
    s.transcribe(np.zeros(800, dtype=np.float32), 16000)
    s.loading.join(5)
    assert attempts == ["omnilingual", "omnilingual"]


def test_language_picker_keeps_every_saved_setting():
    for code in ("en-US", "zh-CN", "yue", "ja-JP", "ko-KR", "eu"):          # what Buddy 1.2.0 could save
        assert speech.language_code(speech.language_name(code)) == code
    assert speech.language_name("zh-TW") != speech.language_name("zh-CN")   # Traditional has its own entry
    assert speech.language_code(speech.language_name("zh-HK")) == "zh-TW"
    assert speech.language_code(speech.language_name("zh_Hant")) == "zh-TW"
    assert speech.language_code(speech.language_name("zh")) == "zh-CN"
    assert speech.language_code(speech.language_name("id")) == "id-ID"
    for unknown in ("", None, "xx-XX"):
        assert speech.language_name(unknown) == "English"
    assert speech.language_code("not in the list") == "en-US"


def test_every_language_in_the_picker_has_an_engine_and_a_note():
    codes = list(speech.LANGUAGES.values())
    assert len(set(codes)) == len(codes)
    assert {"id-ID", "zh-TW", "auto"} <= set(codes)
    for name, code in speech.LANGUAGES.items():
        engine = speech.auto_engine(code)
        assert engine in speech.ENGINES, name
        assert speech.engine_name(engine) in speech.language_note(code), name


def test_language_note_says_which_characters_and_what_to_expect():
    assert "Traditional" in speech.language_note("zh-TW")
    assert "Traditional" in speech.language_note("yue")
    assert "Simplified" in speech.language_note("zh-CN")
    assert "Chinese" not in speech.language_note("en-US")
    assert "more mistakes" in speech.language_note("id-ID")
    assert "more mistakes" not in speech.language_note("en-US")
    assert "only hears English" in speech.language_note("id-ID", "whisper-en")
    assert "Traditional" not in speech.language_note("zh-TW", "whisper-en")     # a warning promises no characters
    assert "Traditional" in speech.language_note("zh-TW", "whisper-multi")      # a model that can hear it still does
    assert "only hears English" not in speech.language_note("en-US", "whisper-en")
    assert "Windows has no recognizer" in speech.language_note("id-ID", "windows")
    assert "Windows has no recognizer" not in speech.language_note("de-DE", "windows")
    assert "Auto would use Omnilingual" in speech.language_note("id-ID", "parakeet")


def test_download_note_only_promises_windows_where_windows_can_help():
    assert "Windows recognizer helps meanwhile" in speech.download_note("whisper-en", "en-US")
    indonesian = speech.download_note("omnilingual", "id-ID")
    assert "290 MB" in indonesian and "waits until it is ready" in indonesian and "Windows" not in indonesian


def test_waiting_says_what_the_user_is_waiting_for(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(speech.Speech, "_load", lambda self: gate.wait(5))
    indonesian = speech.Speech("auto", "id-ID")
    indonesian.prepare()
    assert "Omnilingual is still getting ready" in indonesian.waiting()
    english = speech.Speech("auto", "en-US")
    english.prepare()
    assert english.waiting() == ""                  # the Windows recognizer answers meanwhile
    gate.set()
    indonesian.loading.join(5)
    english.loading.join(5)


def test_waiting_reports_a_failed_load(monkeypatch):
    def broken_download(engine, progress):
        raise OSError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(speech, "download", broken_download)
    monkeypatch.setattr(speech, "is_ready", lambda engine: False)
    s = speech.Speech("auto", "id-ID")
    s.prepare(wait=True)
    assert "couldn't load" in s.waiting() and "download blocked" in s.waiting()
    s.engine = FakeEngine("omnilingual")
    assert s.waiting() == ""


def test_progress_text_shows_size_speed_and_time_left():
    mb = speech.MEGABYTE
    assert speech.progress_text("the speech model (once)", 88 * mb, 208 * mb, 3.1 * mb) == (
        "Downloading the speech model (once)... 42% (88 of 208 MB, 3.1 MB/s, about 40 s left)")
    assert speech.progress_text("x", 34 * mb) == "Downloading x... 34 MB so far"
    assert speech.progress_text("x", 5 * mb, 208 * mb) == "Downloading x... 2% (5.0 of 208 MB)"
    assert speech.progress_text("x", 208 * mb, 208 * mb, 2 * mb) == "Downloading x... 100% (208 of 208 MB, 2.0 MB/s)"
    assert "a few seconds left" in speech.progress_text("x", 200 * mb, 208 * mb, 4 * mb)
    assert "about 5 min left" in speech.progress_text("x", 8 * mb, 308 * mb, 1 * mb)


def test_fetch_reports_megabytes(tmp_path, monkeypatch):
    payload = b"x" * 3_000_000

    class Response:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __init__(self):
            self.stream = io.BytesIO(payload)

        def read(self, size):
            return self.stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(speech, "_opener", lambda: types.SimpleNamespace(open=lambda request, timeout: Response()))
    texts = []
    speech._fetch("https://github.com/x", tmp_path / "model.tar.bz2", texts.append, "the speech model (once)")
    assert texts[0].startswith("Downloading the speech model (once)... ")
    assert texts[-1].startswith("Downloading the speech model (once)... 100% (3.0 of 3.0 MB")


def _archive(path, files, folder="model-x"):
    with tarfile.open(path, "w:bz2") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{folder}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_unpack_keeps_the_wanted_files_and_replaces_a_half_unpacked_folder(tmp_path):
    archive = tmp_path / "model-x.tar.bz2"
    _archive(archive, {"model.int8.onnx": b"small" * 1000, "model.onnx": b"large" * 1000, "tokens.txt": b"a 1\n"})
    model = tmp_path / "model-x"                                   # left behind by an unpack that was cut off
    model.mkdir()
    (model / "model.onnx").write_bytes(b"cut off")
    texts = []
    speech._unpack(archive, tmp_path, "model-x", ("int8.onnx", "tokens.txt"), texts.append)
    assert sorted(entry.name for entry in model.iterdir()) == ["model.int8.onnx", "tokens.txt"]
    assert (model / "model.int8.onnx").read_bytes() == b"small" * 1000
    assert not (tmp_path / "model-x.unpacking").exists()
    assert texts[-1] == "Unpacking the speech model... 100%"


def test_unpack_cut_short_never_looks_like_a_ready_model(tmp_path):
    archive = tmp_path / "model-x.tar.bz2"
    _archive(archive, {"tokens.txt": b"a 1\n", "model.int8.onnx": os.urandom(300_000)})
    archive.write_bytes(archive.read_bytes()[:-100_000])           # the end of the file is missing
    with pytest.raises((tarfile.TarError, EOFError)):
        speech._unpack(archive, tmp_path, "model-x")
    assert not (tmp_path / "model-x").exists()                     # nothing that is_ready() could mistake


def test_download_unpacks_once_and_fetches_a_damaged_archive_again(tmp_path, monkeypatch):
    name = speech.ENGINES["dolphin"]["archive"]
    fetched = []

    def fake_fetch(url, target, progress, what, attempts=6):
        fetched.append(target.name)
        if target.name == speech.VAD_FILE:
            target.write_bytes(b"vad")
        elif len(fetched) == 2:
            target.write_bytes(b"this is not a tar.bz2 file")     # damaged on the way
        else:
            _archive(target, {"model.int8.onnx": b"model", "tokens.txt": b"a 1\n"}, folder=name)

    monkeypatch.setattr(speech, "models_dir", lambda: tmp_path)
    monkeypatch.setattr(speech, "_fetch", fake_fetch)
    texts = []
    with pytest.raises(tarfile.TarError):
        speech.download("dolphin", texts.append)
    assert not speech.is_ready("dolphin")
    assert not (tmp_path / (name + ".tar.bz2")).exists()           # so the next try downloads it again
    speech.download("dolphin", texts.append)
    assert speech.is_ready("dolphin")
    assert fetched == [speech.VAD_FILE, name + ".tar.bz2", name + ".tar.bz2"]
    assert not (tmp_path / (name + ".tar.bz2")).exists()
    assert "Unpacking the speech model... 100%" in texts and texts[-1] == "Speech model ready."


VAD = Path(__file__).resolve().parents[2] / "bench_models" / "silero_vad.onnx"


def _vad_available():
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return speech.models_dir().joinpath(speech.VAD_FILE).is_file()


@pytest.mark.skipif(not _vad_available(), reason="sherpa-onnx or the voice detector file not on this PC")
def test_voice_detector_ignores_noise_and_finds_speech():
    detector = speech.VoiceDetector()
    rng = np.random.default_rng(1)
    silence = (rng.standard_normal(speech.RATE * 2) * 0.002).astype(np.float32)
    assert detector.span(silence) is None
