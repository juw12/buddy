"""Real human speech through Buddy's speech path, one language at a time.

Shipped path (default): keyit_speech.Speech(<choice>, <language setting>) exactly as Buddy builds it, so
the numbers are what a user gets with that Speech model + Language in Settings.
    python run_real.py --out results/real_auto.json
    python run_real.py --out results/real_v120.json --code ../../buddy-1.2.0      # an older checkout
    python run_real.py --out results/real_noisy.json --snr 10                     # + office noise

Candidate engines (--raw): a model that Buddy does not ship yet, straight from an unpacked sherpa-onnx
archive in --models, behind the same voice detector. This is how the engine for each language was chosen.
    python run_real.py --raw dolphin-small --languages id_id ms_my --out results/raw_dolphin.json

KEYIT_HOME must point at a folder whose speech/ holds the models (as Buddy would have downloaded them).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))

from real_lib import LANGUAGES, add_noise, clips, errors, load  # noqa: E402

RAW = {
    # name -> (archive folder, kind)
    "whisper-base": ("sherpa-onnx-whisper-base", "whisper"),
    "whisper-small": ("sherpa-onnx-whisper-small", "whisper"),
    "whisper-base.en": ("sherpa-onnx-whisper-base.en", "whisper"),
    "dolphin-small": ("sherpa-onnx-dolphin-small-ctc-multi-lang-int8-2025-04-02", "dolphin"),
    "dolphin-base": ("sherpa-onnx-dolphin-base-ctc-multi-lang-int8-2025-04-02", "dolphin"),
    "omnilingual": ("sherpa-onnx-omnilingual-asr-1600-languages-300M-ctc-v2-int8-2026-02-05", "omnilingual"),
    "sensevoice": ("sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09", "sensevoice"),
    "parakeet": ("sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8", "parakeet"),
}


def parse():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="result file (json)")
    parser.add_argument("--code", default=str(BENCH.parent), help="Buddy checkout whose keyit/ is measured")
    parser.add_argument("--choice", default="auto", help="Speech model setting (shipped path)")
    parser.add_argument("--raw", choices=sorted(RAW), help="measure a candidate engine instead")
    parser.add_argument("--detect", action="store_true", help="--raw whisper / sensevoice: detect the language")
    parser.add_argument("--models", default=os.environ.get("BUDDY_BENCH_MODELS", str(BENCH / "models")))
    parser.add_argument("--real", default=str(BENCH / "real"), help="folder with the FLEURS clips")
    parser.add_argument("--languages", nargs="*", default=list(LANGUAGES))
    parser.add_argument("--setting", help="use this Language setting for every clip (e.g. auto)")
    parser.add_argument("--limit", type=int, help="clips per language")
    parser.add_argument("--snr", type=float, help="add pink noise at this speech-to-noise ratio (dB)")
    parser.add_argument("--silence", default=str(BENCH / "set_silence"), help="folder with the no-speech clips")
    parser.add_argument("--no-silence", action="store_true", help="skip the silence / noise-only clips")
    return parser.parse_args()


def find(folder, *patterns):
    for pattern in patterns:
        hits = sorted(folder.glob(pattern))
        if hits:
            return str(hits[0])
    raise FileNotFoundError(f"{patterns} in {folder}")


class RawEngine:
    """A candidate model behind Buddy's own voice detector and resampler."""

    def __init__(self, name, setting, detect, models, speech):
        import sherpa_onnx

        archive, kind = RAW[name]
        folder = Path(models) / archive
        make = sherpa_onnx.OfflineRecognizer
        threads = speech.THREADS
        if kind == "whisper":
            code = "" if detect else setting.split("-")[0].lower().replace("yue", "zh")
            self.recognizer = make.from_whisper(
                encoder=find(folder, "*encoder.int8.onnx", "*encoder.onnx"),
                decoder=find(folder, "*decoder.int8.onnx", "*decoder.onnx"),
                tokens=find(folder, "*tokens.txt"),
                language="en" if name.endswith(".en") else code,
                task="transcribe",
                num_threads=threads,
            )
        elif kind == "dolphin":
            self.recognizer = make.from_dolphin_ctc(
                model=find(folder, "model.int8.onnx", "model.onnx"),
                tokens=find(folder, "tokens.txt"),
                num_threads=threads,
            )
        elif kind == "omnilingual":
            self.recognizer = make.from_omnilingual_asr_ctc(
                model=find(folder, "model.int8.onnx", "model.onnx"),
                tokens=find(folder, "tokens.txt"),
                num_threads=threads,
            )
        elif kind == "sensevoice":
            code = setting.split("-")[0].lower()
            self.recognizer = make.from_sense_voice(
                model=find(folder, "model.int8.onnx", "model.onnx"),
                tokens=find(folder, "tokens.txt"),
                use_itn=True,
                language="auto" if detect or code not in ("zh", "en", "ja", "ko", "yue") else code,
                num_threads=threads,
            )
        else:
            self.recognizer = make.from_transducer(
                encoder=find(folder, "encoder*.onnx"),
                decoder=find(folder, "decoder*.onnx"),
                joiner=find(folder, "joiner*.onnx"),
                tokens=find(folder, "tokens.txt"),
                model_type="nemo_transducer",
                num_threads=threads,
            )
        self.speech = speech
        self.vad = speech.VoiceDetector()
        self.name = name + (" (detects the language)" if kind in ("whisper", "sensevoice") and detect else "")
        self.detected = ""

    def transcribe(self, audio, rate):
        audio16k = self.speech.resample(np.asarray(audio, dtype=np.float32), rate, self.speech.RATE)
        span = self.vad.span(audio16k)
        if span is None:
            return ""
        stream = self.recognizer.create_stream()
        stream.accept_waveform(self.speech.RATE, audio16k[span[0]:span[1]])
        self.recognizer.decode_stream(stream)
        self.detected = str(getattr(stream.result, "lang", "") or "")
        return stream.result.text.strip()


def no_legacy(kind, language):
    raise RuntimeError("benchmark: only the downloadable engines are measured")


def shipped_engine(speech, choice, setting):
    engine = speech.Speech(choice, setting, legacy_factory=no_legacy)
    engine.prepare(wait=True)
    if engine.engine is None:
        raise RuntimeError(engine.error or "engine did not load")
    engine.name = engine.describe()
    return engine


def main():
    args = parse()
    sys.path.insert(0, str(Path(args.code).resolve() / "keyit"))
    import keyit_speech

    result = {
        "code": str(Path(args.code).resolve()),
        "path": f"raw {args.raw}" if args.raw else f"shipped, Speech model = {args.choice}",
        "noise": f"pink noise at {args.snr:g} dB SNR" if args.snr is not None else "none (as recorded)",
        "threads": keyit_speech.THREADS,
        "languages": {},
        "rows": [],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def save():
        # after every language, so a run that is cut short still leaves its rows behind
        out.write_bytes(json.dumps(result, ensure_ascii=False, indent=1).encode("utf-8"))

    engines = {}
    for language in args.languages:
        name, setting, unit = LANGUAGES[language]
        setting = args.setting or setting
        try:
            if args.raw:
                engine = RawEngine(args.raw, setting, args.detect, args.models, keyit_speech)
            else:
                engine = shipped_engine(keyit_speech, args.choice, setting)
        except Exception as error:
            result["languages"][language] = {"name": name, "setting": setting, "failed": str(error)[:200]}
            print(language, "FAILED", error, flush=True)
            continue
        engines[engine.name] = engine
        wrong = total = 0
        seconds, audio_seconds = [], []
        for index, (path, said) in enumerate(clips(args.real, language, args.limit)):
            audio, rate = load(path)
            if args.snr is not None:
                audio = add_noise(audio, args.snr, seed=index)
            started = time.perf_counter()
            heard = engine.transcribe(audio, rate)
            took = time.perf_counter() - started
            bad, units = errors(said, heard, language)
            wrong += bad
            total += units
            seconds.append(took)
            audio_seconds.append(len(audio) / rate)
            result["rows"].append({
                "language": language,
                "file": path.name,
                "said": said,
                "heard": heard,
                "errors": bad,
                "units": units,
                "seconds": round(took, 3),
                "audio_seconds": round(len(audio) / rate, 2),
                "detected": getattr(engine, "detected", ""),
            })
        summary = {
            "name": name,
            "setting": setting,
            "engine": engine.name,
            "unit": unit,
            "clips": len(seconds),
            "error_pct": round(100 * wrong / max(1, total), 1),
            "decode_mean_s": round(float(np.mean(seconds)), 2),
            "decode_p90_s": round(float(np.percentile(seconds, 90)), 2),
            "audio_mean_s": round(float(np.mean(audio_seconds)), 1),
        }
        result["languages"][language] = summary
        print(language, json.dumps(summary, ensure_ascii=False), flush=True)
        save()

    if not args.no_silence:
        typed = []
        quiet = sorted(Path(args.silence).glob("*.wav"))
        if not quiet:
            raise SystemExit(f"no silence clips in {args.silence}: pass --silence or --no-silence")
        for engine_name, engine in engines.items():
            for path in quiet:
                audio, rate = load(path)
                heard = engine.transcribe(audio, rate)
                if heard.strip():
                    typed.append({"engine": engine_name, "file": path.name, "heard": heard})
        result["silence"] = {"clips": len(quiet), "engines": len(engines), "typed": typed}
        print("silence:", len(quiet), "clips x", len(engines), "engines ->", len(typed), "transcripts", flush=True)

    failed = [language for language, summary in result["languages"].items() if "failed" in summary]
    result["complete"] = not failed
    save()
    print("wrote", out, flush=True)
    if failed or not result["rows"]:
        # a run that measured nothing must not look like a finished benchmark
        raise SystemExit("not measured: " + (", ".join(failed) or "no clips found"))


if __name__ == "__main__":
    main()
