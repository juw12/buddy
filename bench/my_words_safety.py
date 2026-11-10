"""Does My words ever write a term that nobody said?

Runs the words layer of one Buddy tree over every transcript the benchmarks hold:

  * the FLEURS reference sentences (what a perfect engine would hand over),
  * what each engine really heard for those clips (bench/results_r2/*.json),
  * the round-1 clips (bench/results/*.json) with their references,
  * hand-written sentences made of real words that sound close to the terms,
  * sentences that say a term with an ending ("ZIRIS's results").

A replacement counts as wrong when a term appears in the output more often than it
was said and more often than the engine had already written it:

    wrong(term) = max(0, with_my_words - max(without_my_words, said))

That count cannot see a dropped ending, so the sentences with an ending must come
out exactly as they went in; each one My words changes counts as one more.

The dictionary probe then asks a wider question: which English dictionary words, and
which pairs of them, does My words turn into a term? Those are reported on their own
(not added to the count), because a pair that sounds like the term - "speck green" -
is rewritten on purpose.

The same script measures an older tree (--keyit), whose process() takes no language.

    python bench/my_words_safety.py --out bench/results_r2/my_words_safety.json
"""
import argparse
import glob
import importlib
import importlib.util
import inspect
import json
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# The dictionary every Buddy benchmark uses (same list as run_shipped.py).
MY_WORDS = [
    "SRM", "PLM", "BOM", "eGreen", "PureGMS", "ZIRIS", "Pega-Vision",
    "Spec Green", "Vendor Mapping", "Notepad", "Outlook",
]

# FLEURS folder -> Buddy language setting.
SETTINGS = {
    "en_us": "en-US", "id_id": "id-ID", "cmn_hans_cn": "zh-CN", "yue_hant_hk": "yue",
    "ms_my": "ms-MY", "ja_jp": "ja-JP", "ko_kr": "ko-KR", "th_th": "th-TH",
    "vi_vn": "vi-VN", "de_de": "de-DE", "es_419": "es-ES", "fr_fr": "fr-FR",
}

# Real words and names that sound close to a term. None of them is a term.
NEAR_MISSES = [
    "Sirius is the brightest star in the sky.",
    "Cyrus sent the whole series of reports.",
    "I am serious about this.",
    "Paris is zealous about its museums.",
    "Osiris and Iris are names from old stories.",
    "Zara's shop was closed, so we went to the bizarre bazaar.",
    "Suarez and Soros were both mentioned.",
    "The bomb squad left and he answered with aplomb.",
    "Put the plum on your palm.",
    "We agreed that the evergreen tree stays.",
    "A green light means go, and a speck of green paint is fine.",
    "She sells pure gems and plays pure games.",
    "Pegasus had a big vision for the mega division.",
    "The vendor is mapping the render, and the vendor map is old.",
    "He noted the pad was wet, so there was no pad to write on.",
    "Look out, the outlet is loose and the lookout is asleep.",
    "The sermon gave him esteem.",
    # found by the dictionary probe: good English that the letters rule of 1.2.0 took for a term
    "We use pure gas and pure gum here.",
    "The pure gyms chain opened a new branch.",
    "Open two notepads please.",
    "The outlooks are different.",
    "He bought new zoris in Japan.",
    "The vendor napping in the lobby was tired.",
    "The vendors mapping the site left early.",
    "They painted the space green.",
    # found by the dictionary sweep of word pairs: everyday phrases a sound or two away from a term
    "The invoice is not paid yet.",
    "We could not pad the schedule.",
    "I sip green tea every morning.",
    "Sir is waiting in the lobby.",
    "The vendor moving the boxes is new.",
    "The vendor mopping the floor was quick.",
    "He has pure gums and healthy teeth.",
    "She reached peak vision after the surgery.",
    "Check out logo ideas for the team.",
    "Please note Pat is away today.",
    "Please note bad weather is expected.",
    "The PM revision is due on Friday.",
    "Rev P revision notes are attached.",
    "I green lit the project.",
    # real words one sound away from a term: they stay unless My words has a line such as "peg vision -> Pega-Vision"
    "Open bender mapping first.",
    "Check the peg-vision camera.",
]

# A term said with an ending. My words must hand these back untouched.
MUST_STAY = [
    "ZIRIS's results are in.",
    "Notepad's window is open.",
    "Outlook's calendar is full.",
]

# The sentence the rule was written for: the engine heard ZIRIS as "Bizaira's".
POSITIVE = ("Type Hello team, Bizaira's power test passed.", "ZIRIS")

# Stated limit, reported on its own line and never hidden inside the count:
# the possessive of a name no dictionary knows, with the sound shape of a term.
KNOWN_RISK = ["Zaira's answer was wrong."]

EDGE = "[A-Za-z0-9]"


def chunks(term):
    parts = []
    for piece in re.split(r"[\s\-]+", term):
        parts.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", piece))
    return parts


def written(term):
    """The term exactly as My words spells it."""
    return re.compile("(?<!%s)%s(?!%s)" % (EDGE, re.escape(term), EDGE))


def said(term):
    """The term in a reference, in any case, with or without spaces or hyphens."""
    body = r"[\s\-]*".join(re.escape(chunk) for chunk in chunks(term))
    return re.compile("(?<!%s)%s(?!%s)" % (EDGE, body, EDGE), re.IGNORECASE)


WRITTEN = {term: written(term) for term in MY_WORDS}
SAID = {term: said(term) for term in MY_WORDS}


def read_json(path):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def collect(data, results):
    """Every (source, setting, text, reference); the same text is kept once per setting."""
    texts = []
    for folder in sorted(glob.glob(os.path.join(data, "real", "*"))):
        setting = SETTINGS.get(os.path.basename(folder))
        manifest = os.path.join(folder, "manifest.json")
        if not setting or not os.path.exists(manifest):
            continue
        for clip in read_json(manifest):
            texts.append(("fleurs reference", setting, clip["text"], clip["text"]))
    for path in sorted(glob.glob(os.path.join(results, "*.json"))):
        rows = read_json(path)
        rows = rows.get("rows", []) if isinstance(rows, dict) else []
        for row in rows:
            setting = SETTINGS.get(row.get("language"))
            if setting and row.get("heard"):
                texts.append(("heard: " + os.path.basename(path), setting, row["heard"], row.get("said", "")))
    corpus = {}
    corpus_path = os.path.join(data, "corpus.json")
    if os.path.exists(corpus_path):
        for group, setting in (("en", "en-US"), ("zh", "zh-CN")):
            for key, text in read_json(corpus_path).get(group, []):
                corpus[key] = (setting, text)
                texts.append(("round-1 reference", setting, text, text))
    round_one = []
    for path in sorted(glob.glob(os.path.join(data, "results", "*.json"))):
        rows = read_json(path)
        rows = rows.get("rows", []) if isinstance(rows, dict) else rows
        for row in rows:
            setting, reference = corpus.get(row.get("key"), ("en-US", ""))
            if row.get("hyp"):
                source = "round-1 heard: " + os.path.basename(path)
                texts.append((source, setting, row["hyp"], reference))
                round_one.append((os.path.basename(path), row.get("set", ""), setting, row["hyp"], reference))
    for text in NEAR_MISSES:
        texts.append(("near miss", "en-US", text, text))
    seen, unique = set(), []
    for source, setting, text, reference in texts:
        if (setting, text, reference) not in seen:
            seen.add((setting, text, reference))
            unique.append((source, setting, text, reference))
    return unique, round_one


def lexicon():
    """The English word list that ships with Buddy's voice (misaki), read here so an older tree gets the same probe."""
    spec = importlib.util.find_spec("misaki")
    if spec is None or not spec.submodule_search_locations:
        return []
    found = set()
    for name in ("us_gold.json", "us_silver.json"):
        path = os.path.join(list(spec.submodule_search_locations)[0], "data", name)
        if os.path.exists(path):
            found.update(word.lower() for word in read_json(path) if re.fullmatch("[A-Za-z]+", word))
    return sorted(found)


def outside(word, term):
    """How many letters of `word` are left over after its longest common subsequence with `term`."""
    row = [0] * (len(term) + 1)
    for letter in word:
        diagonal = 0
        for index, other in enumerate(term, 1):
            above = row[index]
            row[index] = diagonal + 1 if letter == other else max(above, row[index - 1])
            diagonal = above
    return len(word) - row[-1]


def probe(words, process, my_words, sample=60):
    """Every dictionary word, and every pair of dictionary words, that My words turns into a term.

    It runs the tree's own process(), so it measures any version. The search is complete for one word and for
    two words without trying every pair: the letters rule needs a difflib ratio of 0.79 (0.8 for a long term)
    between a chunk of n letters and a term of m letters, difflib never matches more letters than the longest
    common subsequence, so the letters of a pair that lie outside the term are at most

        n - ceil(ratio * (n + m) / 2)

    Words are grouped by that leftover count and only groups that can still pass are combined. The sound rule
    cannot reach these chunks: it only fires when a piece is not a dictionary word.
    Not covered: three-word chunks of a two-word term, and words the list does not hold (names, other languages).
    """
    vocabulary = lexicon()
    report = {"lexicon_words": len(vocabulary), "single": {"tested": 0, "rewritten": 0, "by_term": {}, "sample": []},
              "pair": {"tested": 0, "rewritten": 0, "by_term": {}, "sample": []}}
    if not vocabulary:
        return report

    def check(kind, chunk, term):
        entry = report[kind]
        entry["tested"] += 1
        if WRITTEN[term].search(process("We saw %s there." % chunk, my_words, "dictate", "en-US")):
            entry["rewritten"] += 1
            entry["by_term"][term] = entry["by_term"].get(term, 0) + 1
            if entry["by_term"][term] <= sample:
                entry["sample"].append([chunk, term])

    for term in MY_WORDS:
        letters = words._squash(term)
        if len(letters) < 5:
            continue
        started = time.time()
        ratio = 0.8 if len(letters) >= 7 else 0.79
        spare = {size: size - math.ceil(ratio * (size + len(letters)) / 2 - 1e-9)
                 for size in (len(letters) - 1, len(letters), len(letters) + 1)}
        groups = {}
        for word in vocabulary:
            if len(word) <= len(letters) + 1:
                left = outside(word, letters)
                if left <= max(spare.values()):
                    groups.setdefault((len(word), left), []).append(word)
        for size, allowed in spare.items():
            for left in range(allowed + 1):
                for word in groups.get((size, left), ()):
                    if word != letters and words._near_miss(word, term):
                        check("single", word, term)
            for (length, left), firsts in groups.items():
                if length >= size or left > allowed:
                    continue
                firsts = [word for word in firsts if words._sounds_alike(word[0], letters[0])]
                for other in range(allowed - left + 1):
                    seconds = groups.get((size - length, other), ())
                    for first in firsts:
                        for second in seconds:
                            if first + second != letters and words._near_miss(first + second, term):
                                check("pair", first + " " + second, term)
        print("probe %-14s %5.0f s  single %d/%d  pair %d/%d" % (
            term, time.time() - started, report["single"]["rewritten"], report["single"]["tested"],
            report["pair"]["rewritten"], report["pair"]["tested"]), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keyit", default=os.path.join(HERE, "..", "keyit"), help="keyit folder of the tree to test")
    parser.add_argument("--data", default=HERE, help="folder with real/, corpus.json and results/")
    parser.add_argument("--results", default=None, help="folder with the multilingual result files")
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-probe", action="store_true", help="skip the dictionary probe (it takes minutes)")
    options = parser.parse_args()
    results = options.results or os.path.join(options.data, "results_r2")

    sys.path.insert(0, os.path.abspath(options.keyit))
    words = importlib.import_module("keyit_words")
    takes_language = "language" in inspect.signature(words.process).parameters
    my_words = "\n".join(MY_WORDS) + "\n"

    def process(text, dictionary, mode, setting):
        if takes_language:
            return words.process(text, dictionary, mode=mode, language=setting)
        return words.process(text, dictionary, mode=mode)

    def judge(text, reference, mode, setting):
        with_words = process(text, my_words, mode, setting)
        plain = process(text, "", mode, setting)
        wrong = {}
        for term in MY_WORDS:
            extra = len(WRITTEN[term].findall(with_words)) - max(
                len(WRITTEN[term].findall(plain)), len(SAID[term].findall(reference)))
            if extra > 0:
                wrong[term] = extra
        return with_words, plain, wrong

    texts, round_one = collect(os.path.abspath(options.data), os.path.abspath(results))
    runs = changed = wrong_total = 0
    events, by_source = [], {}
    for source, setting, text, reference in texts:
        group = source.split(":")[0]
        by_source[group] = by_source.get(group, 0) + 1
        for mode in ("dictate", "ask"):
            for used in ((setting, "auto") if takes_language else (None,)):
                with_words, plain, wrong = judge(text, reference, mode, used)
                runs += 1
                changed += with_words != plain
                for term, extra in wrong.items():
                    wrong_total += extra
                    events.append({
                        "source": source, "setting": used, "mode": mode, "term": term, "extra": extra,
                        "text": text, "reference": reference, "output": with_words,
                    })

    endings = []
    for text in MUST_STAY:
        for mode in ("dictate", "ask"):
            for used in (("en-US", "auto") if takes_language else (None,)):
                with_words, plain = process(text, my_words, mode, used), process(text, "", mode, used)
                runs += 1
                changed += with_words != plain
                if with_words != plain:
                    wrong_total += 1
                    endings.append({"setting": used, "mode": mode, "text": text, "output": with_words})

    kept = {}
    for name, group, setting, text, reference in round_one:
        for mode in ("dictate", "ask"):
            with_words = process(text, my_words, mode, setting)
            entry = kept.setdefault("%s %s %s" % (name, group, mode), {"said": 0, "kept": 0})
            for term in MY_WORDS:
                count = len(SAID[term].findall(reference))
                entry["said"] += count
                entry["kept"] += min(count, len(WRITTEN[term].findall(with_words)))
    for entry in kept.values():
        entry["rate"] = round(entry["kept"] / entry["said"], 4) if entry["said"] else None

    positive = process(POSITIVE[0], my_words, "dictate", "en-US")
    risk = [{"text": text, "output": process(text, my_words, "dictate", "en-US")} for text in KNOWN_RISK]
    vocabulary = getattr(words, "_vocabulary", None)
    lexicon = len(vocabulary() or ()) if vocabulary else 0

    report = {
        "keyit": os.path.abspath(options.keyit),
        "takes_language": takes_language,
        "lexicon_words": lexicon,
        "my_words": MY_WORDS,
        "texts": len(texts),
        "texts_by_source": by_source,
        "english_words": sum(len(text.split()) for _, setting, text, _ in texts if setting == "en-US"),
        "runs": runs,
        "runs_changed_by_my_words": changed,
        "wrong_replacements": wrong_total,
        "events": events,
        "endings_changed": endings,
        "positive": {"text": POSITIVE[0], "output": positive, "ok": POSITIVE[1] in positive},
        "known_risk": risk,
        "jargon_kept_round_one": kept,
    }
    if not options.no_probe:
        report["dictionary_probe"] = probe(words, process, my_words)
    with open(options.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print("texts %d  runs %d  changed by My words %d  wrong replacements %d  lexicon %d words" % (
        len(texts), runs, changed, wrong_total, lexicon))
    print("positive:", "ok" if report["positive"]["ok"] else "MISSED", "-", positive)
    for item in risk:
        print("known risk:", item["text"], "->", item["output"])
    for event in events[:20]:
        print("WRONG", event["term"], "|", event["setting"], event["mode"], "|", event["text"], "->", event["output"])
    for item in endings[:20]:
        print("WRONG ending |", item["setting"], item["mode"], "|", item["text"], "->", item["output"])
    for kind in ("single", "pair"):
        found = report.get("dictionary_probe", {}).get(kind)
        if found:
            print("dictionary %s: %d of %d near misses rewritten, e.g. %s" % (
                kind, found["rewritten"], found["tested"], ", ".join(chunk for chunk, _ in found["sample"][:12])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
