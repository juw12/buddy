"""Print the round-2 tables of bench/README.md from the files in bench/results_r2/.

    python bench/tables_r2.py

Every number in those tables comes out of this script; none is typed by hand.
"""
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results_r2"
ORDER = ["en_us", "cmn_hans_cn", "yue_hant_hk", "id_id", "ms_my", "ja_jp", "ko_kr", "th_th", "vi_vn",
         "de_de", "es_419", "fr_fr"]
BASELINE = ["v0_real_a_summary.json", "v0_real_b.json"]
BRANCH = ["shipped_r2_a.json", "shipped_r2_b.json", "shipped_r2_c.json"]
AUTO = "shipped_r2_auto.json"
LATENCY = "latency_load.json"
HOLD = "latency_hold.json"
WAYS = ["one piece", "in parts", "while held"]
CANDIDATES = ["raw_omnilingual.json", "raw_dolphin_base.json", "raw_dolphin_small.json",
              "raw_sensevoice_fixed.json", "raw_whisper_base.json", "raw_whisper_base_detect.json"]
SAFETY = [("Buddy 1.2.0", "my_words_safety_1.2.0.json"),
          ("Letters rule, tightened", "my_words_safety_before_exact_sound_rule.json"),
          ("This branch", "my_words_safety_this_branch.json")]
DETECTS = {"en_us": "en", "id_id": "id", "cmn_hans_cn": "zh"}


def read(name):
    path = RESULTS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def error(summary):
    return "%.1f%% %s" % (summary["error_pct"], "CER" if summary["unit"] == "char" else "WER")


def languages(names):
    found = {}
    for name in names:
        found.update(read(name).get("languages", {}))
    return {code: summary for code, summary in found.items() if "failed" not in summary}


def end_to_end():
    """Buddy 1.2.0 against this branch, both through keyit_speech.Speech, on the same clips."""
    before, after = languages(BASELINE), languages(BRANCH)
    rows = []
    for code in ORDER:
        old, new = before.get(code), after.get(code)
        if not (old and new):
            continue
        if old["clips"] != new["clips"]:
            raise SystemExit("%s: %d clips before, %d now" % (code, old["clips"], new["clips"]))
        rows.append([new["name"], new["clips"], old["engine"], error(old), new["engine"], error(new),
                     "%.2f s" % new["decode_mean_s"], "%.1f s" % new["audio_mean_s"]])
    return table(["Language", "Clips", "1.2.0 engine", "1.2.0 error", "Engine now", "Error now",
                  "Decode now (mean)", "Clip length (mean)"], rows)


def silence():
    lines = []
    for name in BASELINE + BRANCH + [AUTO]:
        quiet = read(name).get("silence")
        if quiet:
            lines.append("* `%s`: %d silence clips x %d engine(s): %d typed something" % (
                name, quiet["clips"], quiet["engines"], len(quiet["typed"])))
    return "\n".join(lines)


def chosen_on(code, files):
    """Error with the language chosen, counted over the given clips only; None unless every clip was found."""
    wrong = units = found = 0
    for name in BRANCH:
        for row in read(name).get("rows", []):
            if row["language"] == code and row["file"] in files:
                wrong, units, found = wrong + row["errors"], units + row["units"], found + 1
    return 100.0 * wrong / units if units and found == len(files) else None


def auto_detect():
    """The Auto-detect setting through Buddy's own code path, next to the same clips with the language chosen."""
    report, rows = read(AUTO), []
    for code, summary in languages([AUTO]).items():
        files = {row["file"] for row in report.get("rows", []) if row["language"] == code}
        same = chosen_on(code, files)
        unit = "CER" if summary["unit"] == "char" else "WER"
        rows.append([summary["name"], summary["clips"], summary["engine"], error(summary),
                     "%.1f%% %s" % (same, unit) if same is not None else "not measured",
                     "%.2f s" % summary["decode_mean_s"]])
    return table(["Language", "Clips", "Engine", "Error on Auto-detect",
                  "Error with the language chosen, same clips", "Decode (mean)"], rows)


def candidates():
    rows = []
    for name in CANDIDATES:
        for summary in languages([name]).values():
            rows.append([summary["engine"], summary["name"], summary["clips"], error(summary),
                         "%.2f s" % summary["decode_mean_s"]])
    return table(["Engine", "Language", "Clips", "Error", "Decode (mean)"], rows)


def detection():
    rows = read("raw_whisper_base_detect.json").get("rows", [])
    right = sum(row["detected"] == DETECTS[row["language"]] for row in rows)
    return "Whisper base named the right language on %d of %d clips." % (right, len(rows))


def cantonese():
    data = read("cantonese_traditional_output.json")
    rows = [[label, "%.1f%%" % data[key]["cer_pct"], "%d of %d" % (data[key]["wrong"], data["characters_in_reference"])]
            for label, key in (("as the model wrote it (Simplified)", "as_model_wrote_it_simplified"),
                               ("after Buddy's `to_traditional()`", "after_to_traditional"),
                               ("both sides folded to one script", "script_folded_both_sides"))]
    return table(["Cantonese against the Traditional reference", "CER", "Wrong characters"], rows)


def kept(report, wanted):
    cells = [entry for name, entry in report["jargon_kept_round_one"].items()
             if name.endswith(" dictate") and wanted(name) and entry["said"]]
    return "%d of %d" % (sum(entry["kept"] for entry in cells), sum(entry["said"] for entry in cells))


def swept(report, kind):
    found = report["dictionary_probe"][kind]
    return "%d of %d" % (found["rewritten"], found["tested"])


def safety():
    reports = [read(name) for _, name in SAFETY]
    cells = [
        ("Texts / runs", lambda r: "%d / %d" % (r["texts"], r["runs"])),
        ("Runs My words changed", lambda r: r["runs_changed_by_my_words"]),
        ("Wrong replacements", lambda r: r["wrong_replacements"]),
        ("of those: a sentence that already had the term was changed", lambda r: len(r["endings_changed"])),
        ('"Bizaira\'s power test" becomes ZIRIS', lambda r: "yes" if r["positive"]["ok"] else "no"),
        ('"Zaira\'s answer was wrong." comes out as', lambda r: r["known_risk"][0]["output"]),
        ("Jargon kept, Buddy's round-1 transcripts, clean", lambda r: kept(r, lambda n: n.startswith("shipped.json clean"))),
        ("Jargon kept, Buddy's round-1 transcripts, noisy", lambda r: kept(r, lambda n: n.startswith("shipped.json noisy"))),
        ("Jargon kept, all round-1 result files", lambda r: kept(r, lambda n: True)),
        ("Dictionary sweep: single words rewritten", lambda r: swept(r, "single")),
        ("Dictionary sweep: word pairs rewritten", lambda r: swept(r, "pair")),
    ]
    rows = [[title] + [cell(report) for report in reports] for title, cell in cells]
    return table([""] + [label for label, _ in SAFETY], rows)


def latency():
    report = read(LATENCY)
    if not report:
        return "(not measured)"
    rows = []
    for name, result in report["conditions"].items():
        rows.append([
            name.capitalize(), result["clips"],
            "%.2f s (%.2f s)" % (result["plain_mean_s"], result["plain_median_s"]),
            "%.2f s (%.2f s)" % (result["raised_mean_s"], result["raised_median_s"]),
            "%d of %d" % (result["raised_faster_on"], result["clips"]),
            "%d of %d" % (result["same_text_on"], result["clips"])])
    setup = "%s on %s clips, %d decode threads on %d logical processors, %d busy loops for the loaded row." % (
        report["engine"], report["language"], report["threads"], report["processors"], report["burners"])
    return table(["The PC", "Clips", "Decoded as 1.2.0 did, mean (median)", "Decoded as shipped, mean (median)",
                  "Shipped faster on", "Same text on"], rows) + "\n\n" + setup


def hold():
    report = read(HOLD)
    if not report:
        return "(not measured)"
    rows = []
    for folder in ORDER:
        entry = report["languages"].get(folder)
        for name, result in (entry or {}).get("conditions", {}).items():
            recordings = entry["rows"][name]
            held = [recording["while held"] for recording in recordings]
            rows.append([
                "%s, %s" % (entry["language"], entry["engine"]), name.capitalize(), result["recordings"],
                "%.0f s" % (sum(recording["audio_seconds"] for recording in recordings) / len(recordings)),
                "%d %ss" % (result["units"], "character" if entry["unit"] == "char" else "word"),
                "%.0f%%" % (100 * sum(part["pc_busy"] for part in held) / len(held)),
            ] + ["%.1f s, %d wrong" % (result[way]["mean_s"], result[way]["wrong"]) for way in WAYS] + [
                "%d of %d" % (sum(part["parts_before_release"] for part in held),
                              sum(part["parts"] for part in held))])
    setup = "%d clips in a recording, %d decode threads on %d logical processors, %d busy loops for the loaded rows."
    setup %= (report["clips_per_recording"], report["threads"], report["processors"], report["burners"])
    return table(["Language, engine", "The PC", "Recordings", "Length (mean)", "Said", "Processor busy",
                  "One piece, as 1.2.0 did", "In parts, after release", "In parts, while held (shipped)",
                  "Parts that were text at release"], rows) + "\n\n" + setup


def main():
    for title, text in (
        ("Buddy 1.2.0 against this branch, end to end", end_to_end()),
        ("Silence clips", silence()),
        ("Auto-detect, end to end", auto_detect()),
        ("Engines called directly", candidates()),
        ("Language detection", detection()),
        ("Cantonese script", cantonese()),
        ("My words safety", safety()),
        ("Release to text on a busy PC", latency()),
        ("Long dictations: release to text", hold()),
    ):
        print("### %s\n\n%s\n" % (title, text))


if __name__ == "__main__":
    main()
