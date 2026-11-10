"""Shared helpers for the Buddy voice benchmark: corpus, audio loading, scoring."""
import json
import re
import unicodedata
import wave
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent
CORPUS = json.loads((BENCH / "corpus.json").read_text(encoding="utf-8"))
REFERENCE = {key: text for items in CORPUS.values() for key, text in items}
LANGUAGE = {key: language for language, items in CORPUS.items() for key, _ in items}
# words that matter for Buddy's users: work-app jargon the brain must receive intact
JARGON = ["save as", "notepad", "srm", "egreen", "puregms", "vendor mapping", "bom", "plm", "outlook",
          "pdf", "ziris", "pega-vision", "spec green", "file explorer", "insert"]
FILLERS = {"um", "uh", "er", "erm", "hmm", "uhm"}
# what the speaker meant once a spoken correction is applied (scored only for cleaned-up output)
INTENDED = {"q08": "Send the report to the team at four o'clock."}
NUMBERS = dict(enumerate("zero one two three four five six seven eight nine ten eleven twelve".split()))


def load(path):
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0, rate


def normalize(text, language="en"):
    text = unicodedata.normalize("NFKC", text).lower()
    if language == "zh":
        return [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
    text = text.replace("o'clock", "oclock").replace("o clock", "oclock")
    text = re.sub(r"[^a-z0-9' ]+", " ", text.replace("-", " "))
    # "4" and "four" are the same word to a reader (standard WER text normalisation)
    return [NUMBERS.get(int(word), word) if word.isdigit() else word for word in text.split() if word]


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, token_a in enumerate(a, 1):
        current = [i]
        for j, token_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (token_a != token_b)))
        previous = current
    return previous[-1]


def error_rate(reference, hypothesis, language="en", ignore_fillers=True):
    ref = normalize(reference, language)
    hyp = normalize(hypothesis, language)
    if ignore_fillers and language == "en":
        ref = [w for w in ref if w not in FILLERS]
        hyp = [w for w in hyp if w not in FILLERS]
    return edit_distance(ref, hyp), max(1, len(ref))


def jargon_hits(reference, hypothesis):
    """(hits, total) for jargon terms in the reference, matched without spaces/dashes."""
    squash = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    ref, hyp = squash(reference), squash(hypothesis)
    terms = [squash(term) for term in JARGON if squash(term) in ref]
    return sum(term in hyp for term in terms), len(terms)


def key_of(path):
    return Path(path).stem.split("_")[0]


def summarize(rows, intended=False):
    """rows: dicts with set, key, hyp, seconds, audio_seconds. Returns a summary dict.
    intended=True scores against what the speaker meant (spoken corrections applied) - for cleaned-up text."""
    reference = {**REFERENCE, **INTENDED} if intended else REFERENCE
    out = {}
    for name in ("clean", "noisy"):
        chosen = [r for r in rows if r["set"] == name]
        for language in ("en", "zh"):
            part = [r for r in chosen if LANGUAGE[r["key"]] == language]
            if not part:
                continue
            errors = total = 0
            for r in part:
                e, n = error_rate(reference[r["key"]], r["hyp"], language)
                errors += e
                total += n
            out[f"{name}_{language}_{'wer' if language == 'en' else 'cer'}"] = round(100 * errors / total, 1)
        en = [r for r in chosen if LANGUAGE[r["key"]] == "en"]
        hits = sum(jargon_hits(REFERENCE[r["key"]], r["hyp"])[0] for r in en)
        terms = sum(jargon_hits(REFERENCE[r["key"]], r["hyp"])[1] for r in en)
        if terms:
            out[f"{name}_jargon_pct"] = round(100 * hits / terms, 1)
    silence = [r for r in rows if r["set"] == "silence"]
    if silence:
        out["silence_hallucinations"] = sum(bool(r["hyp"].strip()) for r in silence)
    speech = [r for r in rows if r["set"] in ("clean", "noisy")]
    if speech:
        out["latency_mean_s"] = round(float(np.mean([r["seconds"] for r in speech])), 2)
        out["latency_p90_s"] = round(float(np.percentile([r["seconds"] for r in speech], 90)), 2)
    return out


def all_clips():
    for name in ("clean", "noisy", "silence"):
        for path in sorted((BENCH / f"set_{name}").glob("*.wav")):
            yield name, path
