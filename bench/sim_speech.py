"""Simulated speech: requests from the grammar simulator, said by Buddy's own Kokoro voices and heard by
the speech engine Buddy ships (whisper-en). What comes out is what System 1 would be handed in talk mode.

    set KEYIT_HOME=%APPDATA%\\Buddy
    python bench/sim_speech.py --out bench/data/sim_speech.json

Synthetic voices in a silent room are the easy case for a recognizer: this measures whether a command
survives the trip through the recognizer's spelling and punctuation, not how well a real microphone in a
real office is heard. bench/fast_calibration.py scores the result; nothing is trained on it.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keyit"))
sys.path.insert(0, str(ROOT / "bench"))

import simulate  # noqa: E402

SEED = 20261119                     # not a seed anything was trained or calibrated on
VOICES = ("am_michael", "af_heart")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--requests", type=int, default=100)
    args = parser.parse_args()
    import keyit_speech
    import keyit_voice

    simulate.TYPO = 0.0             # nobody mistypes out loud
    wanted = simulate.requests(args.requests, SEED)
    voice = keyit_voice.NaturalVoice()
    voice.ready.wait(600)
    if voice.failed:
        raise SystemExit(f"Kokoro did not load: {voice.failed}")
    stt = keyit_speech.SherpaSpeech("whisper-en")
    stt.warm_up()
    rows, started = [], time.perf_counter()
    for speaker in VOICES:
        voice.set_voice(speaker)
        for number, row in enumerate(wanted):
            samples, rate = voice._speak(row["text"])
            audio = np.asarray(keyit_speech.resample(np.asarray(samples, dtype=np.float32), rate, keyit_speech.RATE),
                               dtype=np.float32)
            heard = stt.decode(audio)
            heard = heard[0] if isinstance(heard, tuple) else heard
            rows.append({"voice": speaker, "said": row["text"], "text": str(heard).strip(), "windows": row["windows"],
                         "expected": row["expected"], "kind": row["kind"]})
            if number % 20 == 0:
                print(speaker, number, repr(row["text"]), "->", repr(rows[-1]["text"]), flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"seed": SEED, "voices": list(VOICES), "engine": "whisper-en",
                               "seconds": round(time.perf_counter() - started, 1), "rows": rows},
                              indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(len(rows), "utterances in", round(time.perf_counter() - started), "s")


if __name__ == "__main__":
    main()
