"""G5: onnxruntime (Kokoro voices) and sherpa-onnx (speech in) in one process, in both load orders.

usage: coexist.py ort-first | sherpa-first      (needs bench/models/silero_vad.onnx or BUDDY_BENCH_MODELS)
"""
import os
import sys
from pathlib import Path

import numpy as np

MODELS = Path(os.environ.get("BUDDY_BENCH_MODELS", Path(__file__).resolve().parent / "models"))
VAD = str(MODELS / "silero_vad.onnx")


def ort_run():
    import onnxruntime

    session = onnxruntime.InferenceSession(VAD, providers=["CPUExecutionProvider"])
    return "onnxruntime " + onnxruntime.__version__ + " inputs=" + ",".join(i.name for i in session.get_inputs())


def sherpa_run():
    import sherpa_onnx

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = VAD
    config.sample_rate = 16000
    detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=10)
    t = np.arange(16000 * 2) / 16000
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for i in range(0, len(tone), 512):
        detector.accept_waveform(tone[i:i + 512])
    return "sherpa-onnx " + sherpa_onnx.__version__ + " ok"


order = sys.argv[1].replace("_", "-")
steps = [ort_run, sherpa_run] if order == "ort-first" else [sherpa_run, ort_run]
for step in steps:
    print(step(), flush=True)
print(ort_run(), "|", sherpa_run(), flush=True)        # both again, after both are loaded
print("COEXIST OK", order)
