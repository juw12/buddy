"""End-to-end benchmark through Buddy's shipped code: keyit_speech.Speech("auto") + keyit_words.

KEYIT_HOME must point at a folder whose speech/ holds the models (as Buddy would have downloaded them).
English clips use speech_language en-US (-> Whisper base.en), Chinese clips zh-CN (-> SenseVoice).
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "keyit"))

import keyit_speech  # noqa: E402
import keyit_words  # noqa: E402
from bench_lib import BENCH, LANGUAGE, all_clips, jargon_hits, key_of, load, summarize, REFERENCE  # noqa: E402

MY_WORDS = """SRM
PLM
BOM
eGreen
PureGMS
ZIRIS
Pega-Vision
Spec Green
Vendor Mapping
Notepad
Outlook
"""


def no_legacy(kind, language):
    raise RuntimeError("benchmark: only the shipped default engines are measured")


engines = {}
for language in ("en-US", "zh-CN"):
    speech = keyit_speech.Speech("auto", language, legacy_factory=no_legacy)
    started = time.perf_counter()
    speech.prepare(wait=True)
    assert speech.engine is not None, speech.error
    engines[language] = speech
    print(language, "->", speech.describe(), f"ready in {time.perf_counter() - started:.1f}s", flush=True)

raw_rows, word_rows, dictated = [], [], {}
for name, path in all_clips():
    key = key_of(path)
    language = "zh-CN" if name != "silence" and LANGUAGE.get(key) == "zh" else "en-US"
    audio, rate = load(path)
    started = time.perf_counter()
    heard = engines[language].transcribe(audio, rate)
    asked = keyit_words.process(heard, MY_WORDS, mode="ask")
    seconds = time.perf_counter() - started
    raw_rows.append({"set": name, "key": key, "file": path.name, "hyp": heard, "seconds": round(seconds, 3)})
    word_rows.append({"set": name, "key": key, "file": path.name, "hyp": asked, "seconds": round(seconds, 3)})
    if key in ("q07", "q08", "q03", "q09", "q10") and name == "clean":
        dictated[path.name] = {"said": REFERENCE[key], "heard": heard,
                               "typed": keyit_words.process(heard, MY_WORDS, mode="dictate")}
    print(name, path.name, f"{seconds:.2f}s", repr(asked), flush=True)

result = {
    "engine": "Buddy shipped: keyit_speech auto (Whisper base.en en / SenseVoice zh) + Silero VAD + My words",
    "summary": summarize(raw_rows),
    "summary_with_my_words": summarize(word_rows, intended=True),
    "dictation_examples": dictated,
    "rows": word_rows,
}
(BENCH / "results" / "shipped.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print("SUMMARY raw", result["summary"], flush=True)
print("SUMMARY words", result["summary_with_my_words"], flush=True)
for name, example in dictated.items():
    print("DICTATE", name, json.dumps(example, ensure_ascii=False), flush=True)
