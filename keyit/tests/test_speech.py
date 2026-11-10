"""Speech in: engine choice, settings migration, fallbacks, voice detection, resampling, dictation helpers."""
import ctypes
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


def _priority():
    """The Windows priority class of this process."""
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel.GetPriorityClass.restype = ctypes.c_ulong
    return kernel.GetPriorityClass(kernel.GetCurrentProcess())


def _needs_normal_priority():
    if sys.platform != "win32":
        pytest.skip("Windows priority classes")
    if _priority() != speech.NORMAL_PRIORITY:
        pytest.skip("the tests were not started at normal priority")


def test_urgent_raises_the_priority_and_puts_it_back():
    _needs_normal_priority()
    with speech.urgent():
        assert _priority() == speech.ABOVE_NORMAL_PRIORITY
        with speech.urgent():                               # a second recording decoded during the first
            assert _priority() == speech.ABOVE_NORMAL_PRIORITY
        assert _priority() == speech.ABOVE_NORMAL_PRIORITY  # the inner one does not end the outer one early
    assert _priority() == speech.NORMAL_PRIORITY


def test_urgent_puts_the_priority_back_after_an_error():
    _needs_normal_priority()
    with pytest.raises(RuntimeError):
        with speech.urgent():
            raise RuntimeError("the decode failed")
    assert _priority() == speech.NORMAL_PRIORITY


def test_urgent_leaves_a_priority_somebody_chose():
    _needs_normal_priority()
    below_normal = 0x00004000
    assert speech._swap_priority(speech.NORMAL_PRIORITY, below_normal)
    try:
        with speech.urgent():
            assert _priority() == below_normal
        assert _priority() == below_normal
    finally:
        assert speech._swap_priority(below_normal, speech.NORMAL_PRIORITY)


def test_transcribe_decodes_at_above_normal_priority(monkeypatch):
    _needs_normal_priority()
    seen = []

    class Engine:
        def transcribe(self, audio, sample_rate):
            seen.append(_priority())
            return "hello"

    monkeypatch.setattr(speech, "trim_to_speech", lambda audio, sample_rate: audio)
    listener = speech.Speech.__new__(speech.Speech)          # no model, no download: only the decode step
    listener.engine = Engine()
    assert listener.transcribe(np.zeros(1600, dtype=np.float32), 16000) == "hello"
    assert seen == [speech.ABOVE_NORMAL_PRIORITY]
    assert _priority() == speech.NORMAL_PRIORITY


# ---- long dictations: decoded in parts, and while the key is still held


def _at(seconds):
    return round(seconds * speech.RATE)


@pytest.mark.parametrize("rate", [44100, 48000, 8000, 16000])
def test_stream16k_adds_up_to_resample(rate):
    rng = np.random.default_rng(rate)
    audio = (rng.standard_normal(rate * 3 + 123) * 0.1).astype(np.float32)
    stream = speech.Stream16k(rate)
    pieces = []
    at = 0
    while at < len(audio):
        size = int(rng.integers(1, 5000))
        pieces.append(stream.feed(audio[at:at + size]))
        at += size
    early = sum(len(piece) for piece in pieces)
    pieces.append(stream.finish())
    made = np.concatenate(pieces)
    whole = speech.resample(audio, rate, speech.RATE)
    assert len(made) == len(whole)
    assert np.allclose(made, whole, atol=1e-6)
    assert len(whole) - early <= 0.005 * speech.RATE     # nearly all of it was there before the end


def test_cutter_keeps_a_short_dictation_in_one_part():
    cutter = speech.Cutter()
    assert cutter.add(_at(1.0), _at(4.0)) == []
    assert cutter.add(_at(9.0), _at(13.0)) == []          # a long pause, but nothing worth cutting
    assert cutter.early(_at(20.0)) == []
    assert cutter.rest(_at(21.0)) == [(_at(0.75), _at(13.25))]


def test_cutter_cuts_at_the_first_pause_after_fifteen_seconds():
    stretches = [(_at(0.5), _at(8.0)), (_at(8.4), _at(16.0)), (_at(18.0), _at(20.0))]
    expected = [(_at(0.25), _at(16.25)), (_at(16.25), _at(20.25))]   # the pause stays in the second part

    whole = speech.Cutter()                               # a finished recording: add() and rest() only
    parts = []
    for start, end in stretches:
        parts += whole.add(start, end)
    assert parts + whole.rest(_at(21.0)) == expected

    held = speech.Cutter()                                # the same while it is being recorded
    assert held.add(*stretches[0]) == []
    assert held.add(*stretches[1]) == []
    assert held.early(_at(16.5)) == []                    # half a second of silence is no pause yet
    assert held.early(_at(17.2), since=_at(16.4)) == []   # nor is speech that began after 0.4 s
    assert held.early(_at(17.2)) == [expected[0]]         # a pause: closed before more is said
    assert held.early(_at(17.5)) == []
    assert held.add(*stretches[2]) == []
    assert held.rest(_at(21.0)) == [expected[1]]

    late = speech.Cutter()                                # the pause is seen once speech has begun again
    late.add(*stretches[0])
    late.add(*stretches[1])
    assert late.early(_at(18.5), since=_at(18.0)) == [expected[0]]
    assert late.add(*stretches[2]) == []
    assert late.rest(_at(21.0)) == [expected[1]]


def test_cutter_keeps_two_seconds_of_the_pause_before_a_later_part():
    cutter = speech.Cutter()                              # a pause of 1.5 s: no audio is left out
    parts = cutter.add(_at(0.5), _at(16.0)) + cutter.add(_at(17.5), _at(20.0)) + cutter.rest(_at(21.0))
    assert parts == [(_at(0.25), _at(16.25)), (_at(16.25), _at(20.25))]

    cutter = speech.Cutter()                              # a pause of 10 s: its last two seconds stay
    parts = cutter.add(_at(0.5), _at(16.0)) + cutter.add(_at(26.0), _at(30.0)) + cutter.rest(_at(31.0))
    assert parts == [(_at(0.25), _at(16.25)), (_at(24.0), _at(30.25))]

    cutter = speech.Cutter()                              # before the first speech: the usual lead-in only
    assert cutter.add(_at(5.0), _at(8.0)) + cutter.rest(_at(9.0)) == [(_at(4.75), _at(8.25))]


def test_cutter_never_makes_a_part_longer_than_whisper_can_take():
    stretches = [(_at(start), _at(start + 3.6)) for start in range(0, 60, 4)]   # no pause of 0.7 s
    expected = [(_at(0), _at(23.8)), (_at(23.8), _at(47.8)), (_at(47.8), _at(59.85))]
    cutter = speech.Cutter()
    parts = []
    for start, end in stretches:
        parts += cutter.add(start, end)
    parts += cutter.rest(_at(60.0))
    assert parts == expected
    limit = _at(speech.Cutter.KEEP + speech.Cutter.LONGEST + speech.VoiceDetector.PAD)
    assert limit < _at(30.0)                              # what Whisper takes
    assert all(last - first <= limit for first, last in parts)

    held = speech.Cutter()
    for start, end in stretches[:6]:                      # up to 23.6 s
        assert held.add(start, end) == []
    assert held.early(_at(24.5), since=_at(24.0)) == []   # the part could still end within 25 s
    assert held.early(_at(26.0), since=_at(24.0)) == [expected[0]]
    assert held.add(_at(24.0), _at(27.6)) == []


def test_cutter_cuts_speech_that_never_pauses():
    cutter = speech.Cutter()
    parts = cutter.add(_at(1.0), _at(61.0)) + cutter.rest(_at(62.0))
    assert parts == [(_at(0.75), _at(26.0)), (_at(26.0), _at(51.0)), (_at(51.0), _at(61.25))]


def test_join_parts_puts_spaces_where_the_language_has_them():
    assert speech.join_parts(["hello there", "again"]) == "hello there again"
    assert speech.join_parts(["\u4f60\u597d\u3002", "\u518d\u89c1"]) == "\u4f60\u597d\u3002\u518d\u89c1"
    assert speech.join_parts(["\u6253\u5f00", "Outlook"]) == "\u6253\u5f00 Outlook"
    assert speech.join_parts(["", " one ", "", "two"]) == "one two"
    assert speech.join_parts([]) == ""


class FakeListening:
    """Stands in for the voice detector: loud audio is speech, 0.3 s of quiet ends a stretch."""

    BLOCK = 160
    QUIET = 4800

    def __init__(self):
        self.left = np.zeros(0, dtype=np.float32)
        self.heard = 0
        self.since = None                                 # where the stretch that is going on began
        self.last = 0                                     # where its last loud block ended

    def hear(self, audio16k):
        audio = np.concatenate([self.left, np.asarray(audio16k, dtype=np.float32)])
        whole = len(audio) - len(audio) % self.BLOCK
        found = []
        for i in range(0, whole, self.BLOCK):
            at = self.heard + i
            if np.abs(audio[i:i + self.BLOCK]).max() > 0.1:
                if self.since is None:
                    self.since = at
                self.last = at + self.BLOCK
            elif self.since is not None and at + self.BLOCK - self.last >= self.QUIET:
                found.append((self.since, self.last))
                self.since = None
        self.left = audio[whole:]
        self.heard += whole
        return found

    def finish(self):
        found = [(self.since, self.last)] if self.since is not None else []
        self.since = None
        return found


class FakeDetector:
    PAD = 0.25

    def listen(self, seconds=60):
        return FakeListening()

    def span(self, audio16k):                             # what the 1.2.0 code asked for
        listening = self.listen()
        found = listening.hear(audio16k) + listening.finish()
        if not found:
            return None
        pad = int(self.PAD * speech.RATE)
        return max(0, found[0][0] - pad), min(len(audio16k), found[-1][1] + pad)


class FakeRecognizer:
    """Says how many seconds it was given, so a test can read the parts off the text."""

    def __init__(self, languages=()):
        self.streams = []
        self.languages = list(languages)

    def create_stream(self):
        stream = types.SimpleNamespace(samples=0, result=types.SimpleNamespace(text="", lang=""))
        stream.accept_waveform = lambda rate, samples: setattr(stream, "samples", len(samples))
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream):
        stream.result.text = f"{stream.samples / speech.RATE:g}s"
        stream.result.lang = self.languages.pop(0) if self.languages else ""


def _sherpa_engine(languages=()):
    engine = speech.SherpaSpeech.__new__(speech.SherpaSpeech)   # no model: decoding only counts samples
    engine.recognizer = FakeRecognizer(languages)
    engine.vad = FakeDetector()
    engine.detected = ""
    engine.busy = threading.Lock()
    return engine


def _dictation(*seconds):
    """Silence and speech in turn, silence first: _dictation(0.5, 16.0, 2.0, 3.0, 0.5)."""
    pieces = [
        np.full(round(length * speech.RATE), 0.5 if index % 2 else 0.0, dtype=np.float32)
        for index, length in enumerate(seconds)
    ]
    return np.concatenate(pieces)


def _decoded(engine):
    return [stream.samples for stream in engine.recognizer.streams]


def test_a_dictation_is_decoded_in_parts_as_it_comes_in():
    engine = _sherpa_engine()
    held = speech.Held(engine, speech.RATE)
    for piece in np.split(_dictation(0.5, 16.0, 2.0, 3.0, 0.5), 88):      # a quarter of a second at a time
        held.hear(piece)
    assert _decoded(engine) == [_at(16.5)]                # the first part is text before the recording ends
    assert held.finish() == "16.5s 5s"
    assert _decoded(engine) == [_at(16.5), _at(5.0)]


def test_a_finished_recording_is_cut_into_the_same_parts():
    engine = _sherpa_engine()
    assert engine.transcribe(_dictation(0.5, 16.0, 2.0, 3.0, 0.5), speech.RATE) == "16.5s 5s"
    assert _decoded(engine) == [_at(16.5), _at(5.0)]


def test_silence_never_reaches_the_recognizer():
    engine = _sherpa_engine()
    assert engine.transcribe(_dictation(3.0), speech.RATE) == ""
    assert _decoded(engine) == []


def test_a_short_dictation_is_decoded_in_one_go():
    engine = _sherpa_engine()
    assert engine.transcribe(_dictation(0.5, 2.0, 0.5), speech.RATE) == "2.5s"
    assert _decoded(engine) == [_at(2.5)]


def test_nothing_longer_than_thirty_seconds_reaches_the_recognizer_and_nothing_is_left_out():
    engine = _sherpa_engine(["", "<|yue|>", "<|zh|>"])
    engine.transcribe(_dictation(0.5, 54.0, 0.5), speech.RATE)           # 54 s without a pause
    assert _decoded(engine) == [_at(25.25), _at(25.0), _at(4.25)]
    assert max(_decoded(engine)) <= 30 * speech.RATE      # Whisper drops whatever comes after 30 s
    assert sum(_decoded(engine)) == _at(54.5)
    assert engine.detected == "yue"                       # the first part whose language the model names


class FakeMic:
    sample_rate = 16000

    def __init__(self):
        self.blocks = []


def _wait_until(done, seconds=5.0):
    limit = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < limit, "timed out"
        time.sleep(0.005)


def _listener(engine):
    listener = speech.Speech.__new__(speech.Speech)       # no model, no download
    listener.engine = engine
    return listener


def test_hold_decodes_while_the_key_is_held(monkeypatch):
    monkeypatch.setattr(speech.Hold, "TICK", 0.01)
    engine = _sherpa_engine()
    mic = FakeMic()
    audio = _dictation(0.5, 16.0, 2.0, 3.0, 0.5)
    hold = _listener(engine).hold(mic)
    mic.blocks.extend(np.split(audio[:_at(18.0)], 36))
    _wait_until(lambda: len(engine.recognizer.streams) == 1)             # text before the key is let go
    mic.blocks.extend(np.split(audio[_at(18.0):], 8))
    assert hold.finish(np.concatenate(mic.blocks), speech.RATE) == "16.5s 5s"
    assert not hold.thread.is_alive()
    assert _decoded(engine) == [_at(16.5), _at(5.0)]      # no part was decoded twice


def test_hold_decodes_the_whole_recording_again_when_the_early_decode_failed(monkeypatch):
    monkeypatch.setattr(speech.Hold, "TICK", 0.01)
    engine = _sherpa_engine()
    decode = engine.decode
    sizes = []

    def stumbling(audio16k):
        sizes.append(len(audio16k))
        if len(sizes) == 1:
            raise RuntimeError("the recognizer stumbled")
        return decode(audio16k)

    engine.decode = stumbling
    mic = FakeMic()
    audio = _dictation(0.5, 16.0, 2.0, 3.0, 0.5)
    hold = _listener(engine).hold(mic)
    mic.blocks.extend(np.split(audio, 88))
    _wait_until(lambda: hold.failed)
    assert hold.finish(audio, speech.RATE) == "16.5s 5s"
    assert sizes == [_at(16.5), _at(16.5), _at(5.0)]


def test_only_the_bundled_engines_decode_while_the_key_is_held():
    assert _listener(object()).hold(FakeMic()) is None    # the Windows recognizer wants a finished recording
    assert _listener(None).hold(FakeMic()) is None        # nothing has loaded yet


def test_a_cancelled_hold_stops_listening(monkeypatch):
    monkeypatch.setattr(speech.Hold, "TICK", 0.01)
    engine = _sherpa_engine()
    mic = FakeMic()
    hold = _listener(engine).hold(mic)
    hold.cancel()
    hold.thread.join(5)
    assert not hold.thread.is_alive()
    mic.blocks.extend(np.split(_dictation(0.5, 16.0, 2.0, 3.0, 0.5), 88))
    time.sleep(0.05)
    assert _decoded(engine) == []
