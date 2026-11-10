"""Release -> text on a busy PC: what the priority raise in keyit_speech.urgent() is worth.

    python bench/latency_load.py --code . --real bench/real --out bench/results_r2/latency_load.json

KEYIT_HOME must point at a folder whose speech/ holds the models, as for run_real.py.

Every clip is decoded through keyit_speech.Speech the way Buddy does it, twice in each condition: once with the
raise switched off (how Buddy 1.2.0 decoded) and once as shipped. Which of the two goes first alternates from
clip to clip, so slow drift on a shared PC cancels out. The two conditions are the PC as it was during the run,
and the same PC with one busy loop per processor core running at normal priority (a build, a render, another
program working in the background).
"""
import argparse
import contextlib
import ctypes
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))

from real_lib import LANGUAGES, clips, load  # noqa: E402

# a busy loop that ends by itself, so a run that is cut short never leaves the processor burning
BURNER = "import time\nend = time.time() + {seconds}\nwhile time.time() < end:\n    pass\n"
QUIET = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", default=str(BENCH.parent), help="the Buddy source tree to measure")
    parser.add_argument("--real", default=str(BENCH / "real"), help="folder with the FLEURS clips")
    parser.add_argument("--language", default="en_us", choices=sorted(LANGUAGES))
    parser.add_argument("--limit", type=int, default=8, help="how many clips")
    parser.add_argument("--burners", type=int, default=os.cpu_count() or 4,
                        help="busy loops in the loaded condition (default: one per processor core)")
    parser.add_argument("--burner-seconds", type=int, default=900, help="the busy loops end after this long")
    parser.add_argument("--out", required=True)
    return parser.parse_args()


def priority():
    """The Windows priority class of this process."""
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel.GetPriorityClass.restype = ctypes.c_ulong
    return kernel.GetPriorityClass(kernel.GetCurrentProcess())


def no_legacy(kind, language):
    raise RuntimeError("benchmark: only the downloadable engines are measured")


@contextlib.contextmanager
def busy(count, seconds):
    """Keep `count` processor cores busy with other programs at normal priority."""
    code = BURNER.format(seconds=seconds)
    burners = [subprocess.Popen([sys.executable, "-c", code], creationflags=QUIET) for _ in range(count)]
    try:
        time.sleep(3)                                          # let them reach full speed
        yield
    finally:
        for burner in burners:
            burner.kill()
        for burner in burners:
            burner.wait()


def measure(speech, engine, recordings):
    """One row per clip: seconds without and with the priority raise, and whether the text was the same."""
    shipped = speech.urgent
    rows = []
    for index, (name, audio, rate) in enumerate(recordings):
        order = ("plain", "raised") if index % 2 == 0 else ("raised", "plain")
        seconds, texts = {}, {}
        for way in order:
            speech.urgent = contextlib.nullcontext if way == "plain" else shipped
            try:
                started = time.perf_counter()
                texts[way] = engine.transcribe(audio, rate)
                seconds[way] = time.perf_counter() - started
            finally:
                speech.urgent = shipped
        rows.append({
            "file": name,
            "audio_seconds": round(len(audio) / rate, 2),
            "first": order[0],
            "plain": round(seconds["plain"], 3),
            "raised": round(seconds["raised"], 3),
            "same_text": texts["plain"] == texts["raised"],
        })
    return rows


def summary(rows):
    plain = [row["plain"] for row in rows]
    raised = [row["raised"] for row in rows]
    return {
        "clips": len(rows),
        "plain_mean_s": round(statistics.mean(plain), 3),
        "raised_mean_s": round(statistics.mean(raised), 3),
        "plain_median_s": round(statistics.median(plain), 3),
        "raised_median_s": round(statistics.median(raised), 3),
        "raised_faster_on": sum(row["raised"] < row["plain"] for row in rows),
        "same_text_on": sum(row["same_text"] for row in rows),
    }


def main():
    args = parse()
    sys.path.insert(0, str(Path(args.code).resolve() / "keyit"))
    import keyit_speech

    if sys.platform != "win32":
        raise SystemExit("the priority raise is Windows only")
    name, setting, _ = LANGUAGES[args.language]
    engine = keyit_speech.Speech("auto", setting, legacy_factory=no_legacy)
    engine.prepare(wait=True)
    if engine.engine is None:
        raise SystemExit(f"the speech model did not load: {engine.error}")
    recordings = [(path.name, *load(path)) for path, _ in clips(args.real, args.language, args.limit)]
    if not recordings:
        raise SystemExit("no clips: nothing was measured")
    with keyit_speech.urgent():
        works = priority() == keyit_speech.ABOVE_NORMAL_PRIORITY
    if not works or priority() != keyit_speech.NORMAL_PRIORITY:
        raise SystemExit("the priority raise does nothing here: start this script at normal priority")
    engine.transcribe(*recordings[0][1:])                      # warm up: the first decode pays for loading

    result = {
        "language": name,
        "engine": engine.describe(),
        "threads": keyit_speech.THREADS,
        "processors": os.cpu_count(),
        "burners": args.burners,
        "conditions": {},
        "rows": {},
    }
    loads = (
        ("as it was", contextlib.nullcontext()),
        ("every core busy", busy(args.burners, args.burner_seconds)),
    )
    for condition, load_it in loads:
        with load_it:
            rows = measure(keyit_speech, engine, recordings)
        result["rows"][condition] = rows
        result["conditions"][condition] = summary(rows)
        print(condition, json.dumps(result["conditions"][condition]))
        sys.stdout.flush()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(json.dumps(result, ensure_ascii=False, indent=1).encode("utf-8"))
    print("wrote", out.name)


if __name__ == "__main__":
    main()
