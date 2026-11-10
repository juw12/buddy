"""Speech in: engine choice, settings migration, fallbacks, voice detection, resampling, dictation helpers."""
import sys
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
    assert speech.auto_engine("ja-JP") == "sensevoice"


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
