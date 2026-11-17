"""Is System 1's trained confidence honest on requests the simulator did not write, and is it safer and
more useful than the hand-counted System 1 of Buddy 1.5.0?

    python bench/fast_calibration.py                                            # rehearsal: no held-out data
    python bench/fast_calibration.py --final --out bench/results_calibration    # the held-out sets, scored once

The trained mind (keyit/minds_model.json, made by bench/minds_train.py) and the mind of Buddy 1.5.0
(Minds(model=None)) read the same requests. A request is a row {"text", "windows", "expected"}: `expected`
is (action, target) when a careful assistant would do it at once, None when it should look or think first.

Held-out sets (none of them was used to write the rules, to train or to calibrate):
  users      requests written by Mistral Small 3.1 playing four people (bench/sim_users.py, split "test").
             Simulated users, by a different model and with different personas than the training data.
  vendor     the app and window commands of Windows Voice Access, from Microsoft's own command list
  old test   the TEST list of bench/fast_slow.py, written before Buddy 1.5.0 was first run
  CLINC150   requests people wrote for other assistants (Larson et al. 2019, CC BY 3.0), splits "test" and
             "oos_test". Almost none is a window command: this is the "do no harm" set.
  speech     simulated requests said by two Kokoro voices and heard by Buddy's own recognizer
             (bench/sim_speech.py). Synthetic speech in a silent room.
Reported apart, because the simulator that trained the mind also wrote it:
  grammar    bench/simulate.py with a seed that was not trained on

Also here: habits in simulated use (--final runs it too): is a habit's confidence honest when the brain is
sometimes wrong and the person does not always say "no"?
"""
import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keyit"))
sys.path.insert(0, str(ROOT / "bench"))

import fast_slow  # noqa: E402
import keyit_minds as minds_module  # noqa: E402
import minds_train  # noqa: E402
import simulate  # noqa: E402

CLINC = ROOT / "bench" / "data" / "clinc150_data_full.json"
CLINC_URL = "https://raw.githubusercontent.com/clinc/oos-eval/master/data/data_full.json"
SPEECH = ROOT / "bench" / "data" / "sim_speech.json"
GRAMMAR_TEST = (20000, 20261120)         # (requests, seed): not the training or the calibration seed
VOICE_ACCESS = "https://support.microsoft.com/en-us/accessibility/windows/voice-access/voice-access-command-list"
BINS = 10
# CLINC150 has no labels for "what would a careful assistant do with this PC". Every request there counts
# as "think first", so every fast action on it counts as wrong, unless it is listed here after being read by
# hand: {request: [action, target]}. Only requests System 1 acted on are ever looked at.
CLINC_REVIEW = {}


# --------------------------------------------------------------------------- the sets
def vendor_rows():
    """Windows Voice Access, "Voice access command list" (Microsoft Support, read 2026-11-17), the rows
    about apps and windows, each said for a few apps. Windows as in bench/fast_slow.py: Excel in front."""
    windows = fast_slow.WINDOWS
    rows = []

    def add(text, expected):
        rows.append({"text": text, "windows": windows, "expected": list(expected) if expected else None,
                     "kind": "vendor"})

    for verb in ("Open", "Start", "Show"):                       # "Open a new app"
        for app in ("Calculator", "Paint", "Word", "Task Manager"):
            add(f"{verb} {app}", ("open", app.lower()))
    for verb in ("Close", "Exit", "Quit"):                       # "Close an open app"
        for app in ("Notepad", "Outlook", "Excel"):
            add(f"{verb} {app}", ("close", app))
    add("Close window", ("close", "Excel"))
    for verb in ("Switch to", "Go to"):                          # "Switch to an existing app"
        for app in ("Notepad", "Outlook", "File Explorer"):
            add(f"{verb} {app}", ("focus", app))
    for action in ("Minimize", "Maximize"):
        add(f"{action} window", (action.lower(), "Excel"))
        for app in ("Notepad", "Outlook", "Excel"):
            add(f"{action} {app}", (action.lower(), app))
    add("Restore window", ("restore", "Excel"))                  # one step, but not one System 1 can take
    add("Restore Notepad", ("restore", "Notepad"))
    for text in ("Show task switcher", "List all windows", "Show all windows", "Go to desktop", "Go home",
                 "Minimize all windows"):
        add(text, None)
    return rows


def old_test_rows():
    return [{"text": item[0], "windows": item[2] if len(item) > 2 else fast_slow.WINDOWS,
             "expected": list(item[1]) if item[1] else None, "kind": "old test"} for item in fast_slow.TEST]


def clinc_rows(splits):
    if not CLINC.exists():
        CLINC.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(CLINC_URL, headers={"User-Agent": "Buddy bench"}),
                                    timeout=120) as response:
            CLINC.write_bytes(response.read())
    data = json.loads(CLINC.read_text(encoding="utf-8"))
    return [{"text": text, "windows": fast_slow.WINDOWS, "expected": CLINC_REVIEW.get(text), "kind": intent}
            for split in splits for text, intent in data[split]]


def speech_rows():
    return json.loads(SPEECH.read_text(encoding="utf-8"))["rows"] if SPEECH.exists() else []


def held_out():
    sets = {
        "users": ("simulated users written by Mistral Small 3.1 (four people)", minds_train.users("test")),
        "vendor": ("Windows Voice Access command list, app and window commands", vendor_rows()),
        "old test": ("the TEST list of Buddy 1.5.0", old_test_rows()),
        "CLINC150": ("requests for other assistants, test + oos_test", clinc_rows(("test", "oos_test"))),
    }
    spoken = speech_rows()
    for voice in sorted({row["voice"] for row in spoken}):
        sets[f"speech ({voice})"] = ("simulated requests said by Kokoro, heard by whisper-en",
                                     [row for row in spoken if row["voice"] == voice])
    return sets


def rehearsal():
    return {
        "users (calibration)": ("simulated users written by Llama 4 Scout: the calibration split, not held out",
                                minds_train.users("calibration")),
        "grammar (calibration seed)": ("bench/simulate.py, a seed that was not trained on",
                                       simulate.requests(*minds_train.GRAMMAR_CALIBRATION)),
        "CLINC150 (train + val)": ("requests for other assistants, the splits kept for development",
                                   clinc_rows(("train", "val", "oos_train", "oos_val"))),
    }


# --------------------------------------------------------------------------- scoring
def upper_bound(wrong, total, level=0.95):
    """One-sided Clopper-Pearson: the largest error rate that would still give this few wrong, 1 time in 20."""
    if total == 0:
        return None
    if wrong >= total:
        return 1.0

    def at_most(rate):                 # P(X <= wrong) for X ~ Binomial(total, rate)
        log_rate, log_rest = math.log(rate), math.log1p(-rate)
        return sum(math.exp(math.lgamma(total + 1) - math.lgamma(k + 1) - math.lgamma(total - k + 1)
                            + k * log_rate + (total - k) * log_rest) for k in range(wrong + 1))

    low, high = wrong / total, 1.0
    for _ in range(60):
        middle = (low + high) / 2
        if middle in (0.0, 1.0):
            break
        if at_most(middle) > 1 - level:
            low = middle
        else:
            high = middle
    return high


def score(rows, minds):
    """Every request through one System 1."""
    scored = []
    for row in rows:
        started = time.perf_counter()
        decision = minds.decide(row["text"], row["windows"])
        took = time.perf_counter() - started
        did = minds_train.what(decision)
        read = bool(decision.rule)
        right = read and simulate.fine(did, row)
        fast = decision.mind == minds_module.FAST
        scored.append({"text": row["text"], "said": row.get("said"), "kind": row.get("kind"), "expected": row["expected"],
                       "mind": decision.mind, "why": decision.why, "did": list(did) if did else None,
                       "confidence": round(decision.confidence, 4), "read": read, "right": right, "fast": fast,
                       "wrong_fast": fast and not right, "command": row["expected"] is not None, "took": took})
    return scored


def reliability(scored):
    """(ECE, bins) over every reading: stated confidence against the share that was right."""
    readings = [row for row in scored if row["read"]]
    return minds_train.calibration_error([row["confidence"] for row in readings],
                                         [float(row["right"]) for row in readings], BINS)


def summary(scored):
    fast = [row for row in scored if row["fast"]]
    readings = [row for row in scored if row["read"]]
    wrong = sum(row["wrong_fast"] for row in scored)
    error, _bins = reliability(scored)
    bound = upper_bound(wrong, len(fast))
    return {
        "requests": len(scored), "one_step_commands": sum(row["command"] for row in scored),
        "done_fast": len(fast), "wrong_fast": wrong,
        "wrong_fast_upper_95": round(bound, 4) if bound is not None else None,
        "commands_done_fast": sum(row["command"] and row["fast"] and row["right"] for row in scored),
        "readings": len(readings), "readings_right": sum(row["right"] for row in readings), "ece": error,
        "wrong_fast_rows": [{key: row[key] for key in ("text", "said", "kind", "expected", "did", "confidence")}
                            for row in scored if row["wrong_fast"]],
    }


def stakes(scored):
    """For each action: of the readings that passed its bar, how many were right?"""
    table = {}
    for action, bar in minds_module.STAKES.items():
        passed = [row for row in scored if row["fast"] and row["did"] and row["did"][0] == action]
        if passed:
            right = sum(row["right"] for row in passed)
            table[action] = {"bar": bar, "passed": len(passed), "right": right,
                             "share_right": round(right / len(passed), 4), "meets_bar": right / len(passed) >= bar}
    return table


def speed(scored):
    took = sorted(row["took"] for row in scored)
    return {"decisions": len(took), "median_ms": round(statistics.median(took) * 1000, 4),
            "p95_ms": round(took[int(len(took) * 0.95)] * 1000, 4), "max_ms": round(took[-1] * 1000, 4)}


def compare(sets):
    """{set: {"about", "trained": summary, "1.5.0": summary}}, and the trained mind's scored rows per set."""
    trained, before = minds_module.Minds(None), minds_module.Minds(None, model=None)
    if trained.model is None:
        raise SystemExit("keyit/minds_model.json is missing: run bench/minds_train.py first")
    table, rows = {}, {}
    for name, (about, requests) in sets.items():
        rows[name] = score(requests, trained)
        table[name] = {"about": about, "trained": summary(rows[name]), "1.5.0": summary(score(requests, before))}
    return table, rows


# --------------------------------------------------------------------------- habits in simulated use
# Things a person might say that System 1 has no rule for, and what they mean by them.
HABIT_PHRASES = [
    ("outlook front", ("focus", "Outlook")), ("notepad away", ("minimize", "Notepad")),
    ("excel big", ("maximize", "Excel")), ("bye notepad", ("close", "Notepad")), ("edge up", ("focus", "Microsoft Edge")),
    ("excel down", ("minimize", "Excel")), ("done with outlook", ("close", "Outlook")),
    ("calculator", ("open", "calculator")), ("word time", ("open", "word")), ("explorer front", ("focus", "File Explorer")),
    ("outlook big", ("maximize", "Outlook")), ("edge away", ("minimize", "Microsoft Edge")),
]
HABIT_WORLDS = [                  # (how often the brain does the wrong thing, how often the person means something
    (0.00, 0.00, 1.0),            #  else by the same words, how often a wrong action gets a "no")
    (0.05, 0.02, 1.0),
    (0.05, 0.02, 0.5),
    (0.10, 0.10, 0.5),
    (0.05, 0.02, 0.0),
]


def another(rng, meant):
    """A different thing to do with the same app."""
    action, target = meant
    if action == "open":
        return ("focus", target)
    return (rng.choice([other for other in ("close", "minimize", "maximize", "focus") if other != action]), target)


def spoken_target(phrase, meant):
    """The word for the app as it is in the phrase: the brain's action names it that way."""
    return next(word for word in phrase.split() if word in meant[1].lower().split())


def habits_in_use(brain_error, drift, corrects, people=200, requests=250, seed=20261121):
    """Simulated people who each say a dozen phrases of their own again and again. The brain does them first;
    System 1 takes a phrase over once its habit is sure enough."""
    rng = random.Random(f"{seed}/{brain_error}/{drift}/{corrects}")
    fast = wrong_fast = brain = asked = 0
    stated, right = [], []
    weights = [1.0 / rank for rank in range(1, len(HABIT_PHRASES) + 1)]
    for _person in range(people):
        now = [0.0]
        minds = minds_module.Minds(None, clock=lambda: now[0])
        for _request in range(requests):
            phrase, meant = rng.choices(HABIT_PHRASES, weights)[0]
            wanted = another(rng, meant) if rng.random() < drift else meant
            now[0] += 60.0
            minds.heard(phrase)                                   # a new request: the last action stands
            asked += 1
            habit = minds.counts.get(minds_module.phrase_key(phrase))
            if habit:                                             # every time a habit could speak, is it right?
                stated.append(minds.sure(minds_module.phrase_key(phrase)))
                right.append(float((habit["action"], habit["argument"]) == (wanted[0], spoken_target(phrase, wanted))))
            decision = minds.decide(phrase, fast_slow.WINDOWS)
            if decision.mind == minds_module.FAST:
                fast += 1
                good = simulate.same(minds_train.what(decision), wanted)
                wrong_fast += not good
                minds.acted(decision)
            else:
                brain += 1
                did = another(rng, wanted) if rng.random() < brain_error else wanted
                good = did == wanted
                minds.learn(phrase, [(did[0], spoken_target(phrase, did))])
            if not good and rng.random() < corrects:
                now[0] += 3.0
                minds.heard("no")
    error, bins = minds_train.calibration_error(stated, right, BINS)
    return {"brain_error": brain_error, "means_something_else": drift, "says_no_when_wrong": corrects,
            "people": people, "requests": asked, "done_by_brain": brain, "done_fast_by_habit": fast,
            "wrong_fast": wrong_fast, "wrong_fast_per_1000_requests": round(1000 * wrong_fast / asked, 2),
            "wrong_share_of_fast": round(wrong_fast / fast, 4) if fast else None,
            "habit_readings": len(stated), "stated_mean": round(sum(stated) / len(stated), 4) if stated else None,
            "right_share": round(sum(right) / len(right), 4) if right else None, "ece": error, "bins": bins}


# --------------------------------------------------------------------------- report
def gates(table, rows, in_simulator):
    """The gates this run froze before any code was written (run log), from the held-out sets only."""
    together = [row for name in table for row in rows[name]]
    error, bins = reliability(together)
    by_action = stakes(together)
    new = sum(table[name]["trained"]["commands_done_fast"] for name in table)
    old = sum(table[name]["1.5.0"]["commands_done_fast"] for name in table)
    timing = speed(together)
    worst = max((row["overconfident_by"] for row in bins if row["readings"] >= 30), default=None)
    return {
        "held_out_together": {"readings": sum(row["read"] for row in together),
                              "readings_right": sum(row["right"] for row in together), "ece": error, "bins": bins,
                              "by_action": by_action, "commands_done_fast": {"trained": new, "1.5.0": old},
                              "decision_time": timing},
        "G2 no more wrong fast than 1.5.0 on any set": all(
            table[name]["trained"]["wrong_fast"] <= table[name]["1.5.0"]["wrong_fast"] for name in table),
        "G2 old test list still 0 wrong fast": table["old test"]["trained"]["wrong_fast"] == 0 if "old test" in table else None,
        "G3 ECE <= 0.03": error is not None and error <= 0.03,
        "G3 no big bin overconfident by more than 0.05": worst is None or worst <= 0.05,
        "G4 every action as right as its bar": all(entry["meets_bar"] for entry in by_action.values()),
        "G5 more commands done fast than 1.5.0": new > old,
        "G6 a decision under 1 ms at p95": timing["p95_ms"] < 1.0,
        "in_simulator": in_simulator,
    }


def pooled(sets, names):
    """Fast actions and wrong ones over several sets, with the bound on the wrong share."""
    fast = sum(sets[name]["trained"]["done_fast"] for name in names)
    wrong = sum(sets[name]["trained"]["wrong_fast"] for name in names)
    return fast, wrong, upper_bound(wrong, fast)


def percent(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def markdown(result):
    title = "held-out results" if result["final"] else "REHEARSAL on development data (not the held-out sets)"
    lines = [f"# System 1, trained and calibrated by simulation: {title}", "",
             f"Model `keyit/minds_model.json`, sha256 `{result['model_sha256'][:16]}`. Scored {result['scored_at']}.",
             "\"1.5.0\" is the same code with no model file: the hand counts of Buddy 1.5.0.", "",
             "| set | requests | one-step commands | done fast, right (1.5.0 -> trained) | fast actions (1.5.0 -> trained) | "
             "wrong fast (1.5.0 -> trained) | 95% upper bound on wrong share of trained fast actions | readings right | ECE |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, entry in list(result["sets"].items()) + list(result["in_simulator"].items()):
        new, old = entry["trained"], entry["1.5.0"]
        lines.append(f"| {name}: {entry['about']} | {new['requests']} | {new['one_step_commands']} | "
                     f"{old['commands_done_fast']} -> {new['commands_done_fast']} | {old['done_fast']} -> {new['done_fast']} | "
                     f"{old['wrong_fast']} -> {new['wrong_fast']} | {percent(new['wrong_fast_upper_95'])} | "
                     f"{new['readings_right']}/{new['readings']} | {new['ece']} |")
    together = result["gates"]["held_out_together"]
    written = [name for name in result["sets"] if not name.startswith("speech")]
    if result["final"]:
        fast, wrong, bound = pooled(result["sets"], written)
        lines += ["", f"Together, the sets whose wording the simulator did not write ({', '.join(written)}): {fast} fast "
                      f"actions, {wrong} wrong; with 95% confidence the wrong share is under {percent(bound)}."]
        fast, wrong, bound = pooled(result["sets"], list(result["sets"]))
        lines += [f"With the two speech sets added: {fast} fast actions, {wrong} wrong, under {percent(bound)}. The speech "
                  "sets are the same 100 grammar-simulator requests said by two voices, so they are neither independent "
                  "of each other nor free of the simulator's wording."]
    lines += ["", "The last rows named \"grammar\" are in-simulator: the program that trained the mind wrote them too.",
              "", "## Reliability on these sets together (the grammar rows are not in it)", "",
              f"{together['readings']} readings, {together['readings_right']} right, ECE {together['ece']}.", "",
              "| stated confidence | readings | stated (mean) | right | overconfident by |", "|---|---|---|---|---|"]
    for row in together["bins"]:
        lines.append(f"| {row['bin']} | {row['readings']} | {row['stated']} | {row['right']} | {row['overconfident_by']:+.4f} |")
    lines += ["", "## Stakes: of the readings that passed an action's bar, how many were right", "",
              "| action | bar | passed | right | share | meets the bar |", "|---|---|---|---|---|---|"]
    for action, entry in together["by_action"].items():
        lines.append(f"| {action} | {entry['bar']} | {entry['passed']} | {entry['right']} | {entry['share_right']} | "
                     f"{'yes' if entry['meets_bar'] else 'NO'} |")
    timing = together["decision_time"]
    lines += ["", f"Decision time over {timing['decisions']} held-out requests: median {timing['median_ms']} ms, "
                  f"p95 {timing['p95_ms']} ms, max {timing['max_ms']} ms.", "", "## Gates", ""]
    lines += [f"- {'n/a (set not scored)' if passed is None else 'PASS' if passed else 'FAIL'}: {name}"
              for name, passed in result["gates"].items() if name.startswith("G")]
    if result.get("in_simulator_reliability"):
        inside = result["in_simulator_reliability"]
        lines += ["", "## Reliability in the simulator (grammar, test seed)", "",
                  f"ECE {inside['ece']}. The two simulators disagree about some requests (\"open settings\", \"launch X\" "
                  "when X is already open): the grammar assumes the person often means something else, the language-model "
                  "users nearly always mean the app. The trained confidence sits between the two, under every bar, so System 1 "
                  "leaves these to System 2; against the grammar alone those bins read as overconfident.", "",
                  "| stated confidence | readings | stated (mean) | right | overconfident by |", "|---|---|---|---|---|"]
        for row in inside["bins"]:
            lines.append(f"| {row['bin']} | {row['readings']} | {row['stated']} | {row['right']} | "
                         f"{row['overconfident_by']:+.4f} |")
    lines += ["", "## Wrong fast actions of the trained mind, every one", ""]
    for name, entry in list(result["sets"].items()) + list(result["in_simulator"].items()):
        shown = entry["trained"]["wrong_fast_rows"][:12]
        for row in shown:
            heard = f" (said: \"{row['said']}\")" if row.get("said") else ""
            lines.append(f"- {name}: \"{row['text']}\"{heard} -> {row['did']} at {row['confidence']}; "
                         f"wanted {row['expected'] or 'a look first'} ({row['kind']})")
        if len(entry["trained"]["wrong_fast_rows"]) > len(shown):
            lines.append(f"- {name}: ... and {len(entry['trained']['wrong_fast_rows']) - len(shown)} more (in the JSON)")
    if result.get("habits"):
        lines += ["", "## Habits in simulated use", "",
                  "Simulated people say a dozen phrases of their own (\"outlook front\", \"bye notepad\") again and again. "
                  "The brain does a phrase until its habit is sure enough for System 1.", "",
                  "| brain wrong | means something else | says \"no\" when wrong | requests | fast by habit | wrong fast | "
                  "per 1,000 requests | habit confidence stated | habit right | ECE |", "|---|---|---|---|---|---|---|---|---|---|"]
        for row in result["habits"]:
            lines.append(f"| {percent(row['brain_error'])} | {percent(row['means_something_else'])} | "
                         f"{percent(row['says_no_when_wrong'])} | {row['requests']} | {row['done_fast_by_habit']} | "
                         f"{row['wrong_fast']} | {row['wrong_fast_per_1000_requests']} | {row['stated_mean']} | "
                         f"{row['right_share']} | {row['ece']} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--final", action="store_true", help="score the held-out sets (do this once, after the freeze)")
    parser.add_argument("--out", default="")
    parser.add_argument("--report", action="store_true", help="write the .md again from the saved .json: nothing is scored")
    args = parser.parse_args()
    if args.report:
        saved = json.loads((Path(args.out) / "fast_calibration.json").read_text(encoding="utf-8"))
        (Path(args.out) / "fast_calibration.md").write_text(markdown(saved), encoding="utf-8", newline="\n")
        return
    table, rows = compare(held_out() if args.final else rehearsal())
    grammar = {"grammar (test seed)": ("bench/simulate.py with a seed that was not trained on: in-simulator",
                                       simulate.requests(*GRAMMAR_TEST))}
    in_simulator, inside_rows = compare(grammar) if args.final else ({}, {})
    model = (ROOT / "keyit" / "minds_model.json").read_bytes().replace(b"\r\n", b"\n")
    result = {"scored_at": time.strftime("%Y-%m-%d %H:%M"), "final": args.final,
              "model_sha256": hashlib.sha256(model).hexdigest(), "voice_access_source": VOICE_ACCESS,
              "sets": table, "in_simulator": in_simulator, "gates": gates(table, rows, bool(in_simulator)),
              "habits": [habits_in_use(*world) for world in HABIT_WORLDS]}
    if args.final:
        inside_error, inside_bins = reliability(inside_rows["grammar (test seed)"])
        result["in_simulator_reliability"] = {"ece": inside_error, "bins": inside_bins}
        result["users_rows"] = [{key: row[key] for key in ("text", "kind", "expected", "mind", "why", "did", "confidence",
                                                           "right")} for row in rows["users"]]
        result["fast_on_clinc"] = [{key: row[key] for key in ("text", "kind", "did", "confidence", "right")}
                                   for row in rows["CLINC150"] if row["fast"]]
    report = markdown(result)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "fast_calibration.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8",
                                                   newline="\n")
        (out / "fast_calibration.md").write_text(report, encoding="utf-8", newline="\n")
    print(report)


if __name__ == "__main__":
    main()
