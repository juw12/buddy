"""Benchmark one sherpa-onnx speech engine on the Buddy voice corpus.

usage: run_sherpa.py <engine> [--vad]
engines: whisper-base.en, moonshine-base, sensevoice, parakeet-v3
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import sherpa_onnx

from bench_lib import BENCH, LANGUAGE, all_clips, key_of, load, summarize

MODELS = Path(os.environ.get("BUDDY_BENCH_MODELS", BENCH / "models"))   # unpacked sherpa-onnx archives + silero_vad.onnx
THREADS = max(2, (os.cpu_count() or 4) // 2)


def find(folder, *patterns):
    for pattern in patterns:
        hits = sorted(folder.glob(pattern))
        if hits:
            return str(hits[0])
    raise FileNotFoundError(f"{patterns} in {folder}")


def make(engine):
    if engine == "whisper-base.en":
        d = MODELS / "sherpa-onnx-whisper-base.en"
        return sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=find(d, "*encoder.int8.onnx", "*encoder.onnx"),
            decoder=find(d, "*decoder.int8.onnx", "*decoder.onnx"),
            tokens=find(d, "*tokens.txt"), language="en", task="transcribe", num_threads=THREADS)
    if engine == "moonshine-base":
        d = MODELS / "sherpa-onnx-moonshine-base-en-int8"
        return sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=find(d, "preprocess*.onnx"), encoder=find(d, "encode*.onnx"),
            uncached_decoder=find(d, "uncached_decode*.onnx"), cached_decoder=find(d, "cached_decode*.onnx"),
            tokens=find(d, "tokens.txt"), num_threads=THREADS)
    if engine == "sensevoice":
        d = MODELS / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=find(d, "model.int8.onnx", "model.onnx"), tokens=find(d, "tokens.txt"),
            use_itn=True, language="auto", num_threads=THREADS)
    if engine == "parakeet-v3":
        d = MODELS / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=find(d, "encoder*.onnx"), decoder=find(d, "decoder*.onnx"), joiner=find(d, "joiner*.onnx"),
            tokens=find(d, "tokens.txt"), model_type="nemo_transducer", num_threads=THREADS)
    raise SystemExit(f"unknown engine {engine}")


def make_vad():
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(MODELS / "silero_vad.onnx")
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = 0.3
    config.silero_vad.min_speech_duration = 0.15
    config.sample_rate = 16000
    config.num_threads = 1
    return config


def speech_span(config, audio, rate=16000):
    """(start, end) samples of all speech found by Silero, or None when there is no speech."""
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
    window = config.silero_vad.window_size
    starts, ends = [], []
    for i in range(0, len(audio), window):
        vad.accept_waveform(audio[i:i + window])
        while not vad.empty():
            segment = vad.front
            starts.append(segment.start)
            ends.append(segment.start + len(segment.samples))
            vad.pop()
    vad.flush()
    while not vad.empty():
        segment = vad.front
        starts.append(segment.start)
        ends.append(segment.start + len(segment.samples))
        vad.pop()
    if not starts:
        return None
    pad = int(0.25 * rate)
    return max(0, min(starts) - pad), min(len(audio), max(ends) + pad)


def main():
    engine = sys.argv[1]
    use_vad = "--vad" in sys.argv
    started = time.perf_counter()
    recognizer = make(engine)
    vad_config = make_vad() if use_vad else None
    load_seconds = time.perf_counter() - started
    # warm-up so the first clip's time is not the model's first-run cost
    warm, _ = load(next(p for _, p in all_clips()))
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, warm)
    recognizer.decode_stream(stream)

    rows = []
    for name, path in all_clips():
        audio, rate = load(path)
        t = time.perf_counter()
        if vad_config is not None:
            span = speech_span(vad_config, audio, rate)
            if span is None:
                hyp = ""
            else:
                stream = recognizer.create_stream()
                stream.accept_waveform(rate, audio[span[0]:span[1]])
                recognizer.decode_stream(stream)
                hyp = stream.result.text.strip()
        else:
            stream = recognizer.create_stream()
            stream.accept_waveform(rate, audio)
            recognizer.decode_stream(stream)
            hyp = stream.result.text.strip()
        seconds = time.perf_counter() - t
        rows.append({"set": name, "key": key_of(path), "file": path.name, "hyp": hyp,
                     "seconds": round(seconds, 3), "audio_seconds": round(len(audio) / rate, 2)})
        print(name, path.name, round(seconds, 2), hyp, flush=True)

    label = engine + (" + Silero VAD" if use_vad else "")
    summary = summarize(rows)
    summary["load_s"] = round(load_seconds, 1)
    out = BENCH / "results" / f"{engine}{'_vad' if use_vad else ''}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"engine": label, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("SUMMARY", label, summary, flush=True)


if __name__ == "__main__":
    main()
