"""Release -> text for a long dictation: what decoding while the key is held is worth.

    python bench/latency_hold.py --code . --real bench/real --out bench/results_r2/latency_hold.json

KEYIT_HOME must point at a folder whose speech/ holds the models, as for run_real.py.

A recording is six FLEURS clips of one language joined with one-second gaps, about a minute of speech. The
clips come from different speakers and microphones, and the voice detector takes a quiet speaker right after
a loud one for background noise, so every clip is first scaled to the same speech level. The recording then
reaches Buddy the way the microphone delivers it: in 44.1 kHz blocks, in real time. Three ways of turning it
into text are timed from the moment the last block is in, which is when the dictation key is let go:

  * one piece:  as Buddy 1.2.0 did. Everything is decoded in one go, at normal priority.
  * in parts:   this code without a head start. The recording is cut at pauses and decoded part by part.
  * while held: as shipped. The parts that were complete were decoded while the key was still down.

Each text is scored against what was said, which shows what a model does with more audio than it can take.
Which way goes first rotates from recording to recording, so slow drift on a shared PC cancels out. The
conditions are the PC as it was during the run, and the same PC with one busy loop per processor core.
"""
import argparse
import contextlib
import ctypes
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))

from latency_load import busy, no_legacy  # noqa: E402
from real_lib import LANGUAGES, clips, errors, load  # noqa: E402

GAP = 1.0                                                      # seconds of silence between two clips
SPEECH_LEVEL = 0.05                                            # what every clip's speech is scaled to
MICROPHONE = 44100                                             # the rate Buddy records at
BLOCK = 1024                                                   # samples the microphone delivers at a time
WAYS = ("one piece", "in parts", "while held")


def parse():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", default=str(BENCH.parent), help="the Buddy source tree to measure")
    parser.add_argument("--real", default=str(BENCH / "real"), help="folder with the FLEURS clips")
    parser.add_argument("--languages", nargs="+", default=["en_us", "cmn_hans_cn"], choices=sorted(LANGUAGES))
    parser.add_argument("--clips", type=int, default=6, help="clips joined into one recording")
    parser.add_argument("--recordings", type=int, default=2, help="recordings per language on the PC as it was")
    parser.add_argument("--busy-recordings", type=int, default=1,
                        help="recordings per language with every core busy (0 skips that condition)")
    parser.add_argument("--burners", type=int, default=os.cpu_count() or 4,
                        help="busy loops in the loaded condition (default: one per processor core)")
    parser.add_argument("--burner-seconds", type=int, default=900, help="the busy loops end after this long")
    parser.add_argument("--out", required=True)
    return parser.parse_args()


def speech_level(audio, rate):
    """How loud the speech in a clip is: the level that its loudest tenth of 30 ms frames reaches."""
    frame = int(0.03 * rate)
    frames = audio[:len(audio) // frame * frame].reshape(-1, frame)
    return float(np.percentile(np.sqrt(np.mean(frames ** 2, axis=1)), 90))


def recording(real, folder, number, count):
    """Recording `number` of a language: (16 kHz audio, what was said, the clips' file names), or None."""
    chosen = clips(real, folder, (number + 1) * count)[number * count:]
    if len(chosen) < count:
        return None
    pieces, said, names = [], [], []
    for path, sentence in chosen:
        audio, rate = load(path)
        audio = np.asarray(audio, dtype=np.float32)
        audio = np.clip(audio * (SPEECH_LEVEL / max(speech_level(audio, rate), 1e-6)), -1.0, 1.0)
        pieces += [audio.astype(np.float32), np.zeros(int(GAP * rate), dtype=np.float32)]
        said.append(sentence)
        names.append(Path(path).name)
    return np.concatenate(pieces), " ".join(said), names


class Microphone:
    """What Speech.hold() sees of Buddy's microphone: its sample rate and the blocks recorded so far."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.blocks = []


def play(microphone, audio):
    """Deliver the recording in real time, catching up on the blocks that are due when this thread was late."""
    started = time.perf_counter()
    for first in range(0, len(audio), BLOCK):
        wait = started + (first + BLOCK) / microphone.sample_rate - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        microphone.blocks.append(audio[first:first + BLOCK])


def processor_times():
    """(idle, total) processor time of the whole PC so far, in 100 ns units."""
    idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    ctypes.WinDLL("kernel32").GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return idle.value, kernel.value + user.value               # the kernel time includes the idle time


def one_piece(speech, listener, audio, rate):
    started = time.perf_counter()
    audio16k = speech.resample(audio, rate, speech.RATE)
    span = listener.engine.vad.span(audio16k)
    text = listener.engine.decode(audio16k[span[0]:span[1]])[0] if span else ""
    return {"seconds": time.perf_counter() - started, "text": text}


def in_parts(speech, listener, audio, rate):
    started = time.perf_counter()
    text = listener.transcribe(audio, rate)
    return {"seconds": time.perf_counter() - started, "text": text}


def while_held(speech, listener, audio, rate):
    microphone = Microphone(rate)
    hold = listener.hold(microphone)
    if hold is None:
        raise SystemExit("this engine is not decoded while the key is held: nothing to measure")
    before = processor_times()
    play(microphone, audio)
    early = len(hold.held.texts) if hold.held is not None else 0
    started = time.perf_counter()
    text = hold.finish(np.concatenate(microphone.blocks), rate)
    seconds = time.perf_counter() - started
    after = processor_times()
    if hold.failed:
        raise SystemExit("decoding while the key was held failed: the fallback was measured, not the hold")
    return {"seconds": seconds, "text": text, "parts": len(hold.held.texts), "parts_before_release": early,
            "pc_busy": round(1 - (after[0] - before[0]) / max(after[1] - before[1], 1), 3)}


def measure(speech, listener, folder, number, audio, said, order):
    row = {"recording": number, "audio_seconds": round(len(audio) / MICROPHONE, 1), "first": order[0]}
    ways = {"one piece": one_piece, "in parts": in_parts, "while held": while_held}
    for way in order:
        result = ways[way](speech, listener, audio, MICROPHONE)
        wrong, units = errors(said, result.pop("text"), folder)
        result["seconds"] = round(result["seconds"], 3)
        result["wrong"] = wrong
        row["units"] = units
        row[way] = result
    return row


def summary(rows):
    result = {"recordings": len(rows), "units": sum(row["units"] for row in rows)}
    for way in WAYS:
        seconds = [row[way]["seconds"] for row in rows]
        result[way] = {"mean_s": round(statistics.mean(seconds), 3), "median_s": round(statistics.median(seconds), 3),
                       "wrong": sum(row[way]["wrong"] for row in rows)}
    return result


def main():
    args = parse()
    sys.path.insert(0, str(Path(args.code).resolve() / "keyit"))
    import keyit_speech

    if sys.platform != "win32":
        raise SystemExit("this benchmark reads the Windows processor times")
    result = {"threads": keyit_speech.THREADS, "processors": os.cpu_count(), "burners": args.burners,
              "clips_per_recording": args.clips, "languages": {}}
    measured = 0
    for folder in args.languages:
        name, setting, unit = LANGUAGES[folder]
        listener = keyit_speech.Speech("auto", setting, legacy_factory=no_legacy)
        listener.prepare(wait=True)
        if listener.engine is None:
            raise SystemExit(f"the speech model for {name} did not load: {listener.error}")
        wanted = args.recordings + args.busy_recordings
        recordings = [recording(args.real, folder, number, args.clips) for number in range(wanted)]
        recordings = [(number, *found) for number, found in enumerate(recordings) if found]
        if not recordings:
            continue
        listener.transcribe(recordings[0][1][:5 * keyit_speech.RATE], keyit_speech.RATE)   # warm up
        entry = {"language": name, "engine": listener.describe(), "unit": unit, "rows": {}, "conditions": {}}
        plan = (
            ("as it was", recordings[:args.recordings], contextlib.nullcontext()),
            ("every core busy", recordings[args.recordings:], busy(args.burners, args.burner_seconds)),
        )
        for condition, chosen, load_it in plan:
            if not chosen:
                continue
            with load_it:
                rows = []
                for number, audio, said, names in chosen:
                    order = WAYS[measured % 3:] + WAYS[:measured % 3]
                    heard = keyit_speech.resample(audio, keyit_speech.RATE, MICROPHONE)
                    rows.append(measure(keyit_speech, listener, folder, number, heard, said, order))
                    rows[-1]["clips"] = names
                    measured += 1
            entry["rows"][condition] = rows
            entry["conditions"][condition] = summary(rows)
            print(name, "|", condition, "|", json.dumps(entry["conditions"][condition]))
            sys.stdout.flush()
        result["languages"][folder] = entry
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(json.dumps(result, ensure_ascii=False, indent=1).encode("utf-8"))
    if not measured:
        raise SystemExit("no recordings: nothing was measured")
    print("wrote", Path(args.out).name)


if __name__ == "__main__":
    main()
