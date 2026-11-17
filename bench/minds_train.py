"""Train and calibrate System 1's confidence on simulated use, and write keyit/minds_model.json.

    python bench/minds_train.py                 # train, calibrate, write the model, print what it learned
    python bench/minds_train.py --check         # retrain and compare with the model file (used by the tests)
    python bench/minds_train.py --rehearse      # leave one simulated person out at a time (no test data)

What is learned: for a reading System 1 makes ("close notepad" -> close the Notepad window), the
probability that it is what the person wanted, from what System 1 notices about it: the kind of target,
the action, the verb, a "can you" in front, a "please" behind, filler words, an app that is already open.

Training data, all simulated and all with the right answer known from how it was made:
  * bench/simulate.py: a grammar of wishes, wordings, windows and typing slips (seeded, so repeatable)
  * bench/data/sim_users.json, split "train": people played by Llama 4 Scout
Calibration (Platt scaling: one stretch and one shift of the scores) uses split "calibration": two other
people played by the same model. Split "test" is never read here.

A verb or a kind of reading seen fewer than MIN_SEEN times in training is left out of the model, and what
is not in the model System 1 does not read at all.
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keyit"))
sys.path.insert(0, str(ROOT / "bench"))

import keyit_minds as minds_module  # noqa: E402
import simulate  # noqa: E402

MODEL = ROOT / "keyit" / "minds_model.json"
USERS = ROOT / "bench" / "data" / "sim_users.json"
VERBS = {
    "close": "close", "quit": "close", "exit": "close", "shut": "close", "shut down": "close", "kill": "close",
    "get rid of": "close", "close out": "close", "close down": "close", "exit out of": "close",
    "minimize": "minimize", "hide": "minimize", "shrink": "minimize", "put away": "minimize",
    "put ... away": "minimize",
    "maximize": "maximize", "full screen": "maximize", "enlarge": "maximize", "make ... full screen": "maximize",
    "make ... fullscreen": "maximize",
    "switch to": "focus", "go to": "focus", "bring up": "focus", "show": "focus", "pull up": "focus",
    "switch over to": "focus", "go back to": "focus", "jump to": "focus", "focus on": "focus",
    "bring ... to the front": "focus", "bring ... to front": "focus", "bring ... up": "focus",
    "open": "open", "launch": "open", "start": "open", "open up": "open", "fire up": "open", "start up": "open",
    "run": "open", "load": "open",
}
GRAMMAR_TRAIN = (30000, 20261117)       # (requests, seed)
GRAMMAR_CALIBRATION = (10000, 20261118)
MIN_SEEN = 40
POLITE = ("opener", "frame", "closer", "asked")        # "hey", "can you", "please", "?"
LEFT_OUT = POLITE     # things System 1 notices that get no weight: --rehearse found them no help
USER_SHARE = 0.5      # the language-model users weigh as much as the grammar in total; --rehearse (leave one
                      # person out) gave log loss 0.0611 at 0.1, 0.0592 at 0.25 and 0.0566 at 0.5
RIDGE = 1.0
BINS = 10


class OpenMinded(minds_module.Model):
    """Reads every wording on the list and is sure of none, so every reading can be collected."""

    def __init__(self, verbs):
        super().__init__({"verbs": verbs, "weights": {"bias": 0.0}, "typical": {}})

    def knows(self, noticed):
        return True


def what(decision):
    if decision.action is None:
        return None
    return (decision.action[0], decision.window["app"]) if decision.window else decision.action


def readings(rows, model):
    """[(what was noticed, was the reading right, the request)] for every request System 1 reads."""
    minds = minds_module.Minds(None, model=model)
    found = []
    for row in rows:
        decision = minds.decide(row["text"], row["windows"])
        if decision.rule and decision.noticed:
            found.append((decision.noticed, simulate.fine(what(decision), row), row))
    return found


def users(split):
    return [row for row in json.loads(USERS.read_text(encoding="utf-8"))["rows"] if row["split"] == split]


def fit(examples, weights, names, ridge=RIDGE, start=None):
    """Logistic regression by Newton's method. examples: [(noticed, right)], weights: one per example."""
    index = {name: column for column, name in enumerate(names)}
    table = np.zeros((len(examples), len(names)))
    for line, (noticed, _right) in enumerate(examples):
        table[line, 0] = 1.0
        for name in noticed:
            if name in index:
                table[line, index[name]] = 1.0
    right = np.array([float(right) for _noticed, right in examples])
    weight = np.asarray(weights, dtype=float)
    penalty = np.full(len(names), ridge)
    penalty[0] = 0.0
    beta = np.zeros(len(names)) if start is None else np.array(start, dtype=float)
    for _ in range(60):
        chance = 1.0 / (1.0 + np.exp(-table @ beta))
        slope = table.T @ (weight * (chance - right)) + penalty * beta
        curve = (table * (weight * chance * (1 - chance))[:, None]).T @ table + np.diag(penalty + 1e-9)
        step = np.linalg.solve(curve, slope)
        beta -= step
        if np.abs(step).max() < 1e-10:
            break
    return beta


def score(noticed, names, beta):
    index = {name: column for column, name in enumerate(names)}
    return beta[0] + sum(beta[index[name]] for name in noticed if name in index)


def platt(scores, right, strength=2.0):
    """(stretch, shift) so that sigmoid(stretch * score + shift) fits; pulled gently towards (1, 0)."""
    scores, right = np.asarray(scores, dtype=float), np.asarray(right, dtype=float)
    stretch, shift = 1.0, 0.0
    for _ in range(100):
        chance = 1.0 / (1.0 + np.exp(-(stretch * scores + shift)))
        slope = np.array([np.sum((chance - right) * scores) + strength * (stretch - 1.0),
                          np.sum(chance - right) + strength * shift])
        spread = chance * (1 - chance)
        curve = np.array([[np.sum(spread * scores * scores) + strength, np.sum(spread * scores)],
                          [np.sum(spread * scores), np.sum(spread) + strength]])
        step = np.linalg.solve(curve, slope)
        stretch, shift = stretch - step[0], shift - step[1]
        if np.abs(step).max() < 1e-12:
            break
    return float(stretch), float(shift)


def calibration_error(stated, right, bins=BINS):
    """(ECE, rows): stated confidence against the share that was right, in equal-width bins."""
    rows, total = [], 0.0
    for number in range(bins):
        low, high = number / bins, (number + 1) / bins
        inside = [(said, was) for said, was in zip(stated, right) if low <= said < high or (number == bins - 1 and said == 1.0)]
        if not inside:
            continue
        said = sum(item[0] for item in inside) / len(inside)
        was = sum(item[1] for item in inside) / len(inside)
        rows.append({"bin": f"{low:.1f}-{high:.1f}", "readings": len(inside), "stated": round(said, 4),
                     "right": round(was, 4), "overconfident_by": round(said - was, 4)})
        total += abs(said - was) * len(inside)
    return (round(total / len(stated), 4) if stated else None), rows


def known(examples, min_seen=MIN_SEEN, left_out=()):
    """Feature names seen often enough, and the examples whose kind and verb are both among them."""
    seen = {}
    for noticed, _right, _row in examples:
        for name in noticed:
            seen[name] = seen.get(name, 0) + 1
    names = ["bias"] + sorted(name for name, count in seen.items() if count >= min_seen and name not in left_out)
    kept = [example for example in examples if all(name in names for name in example[0][:2])]
    return names, kept


def train(grammar_rows, user_rows, calibration_rows, user_share=USER_SHARE, left_out=LEFT_OUT):
    open_minded = OpenMinded(VERBS)
    from_grammar, from_users = readings(grammar_rows, open_minded), readings(user_rows, open_minded)
    names, _ = known(from_grammar + from_users, left_out=left_out)
    from_grammar = [example for example in from_grammar if all(name in names for name in example[0][:2])]
    from_users = [example for example in from_users if all(name in names for name in example[0][:2])]
    examples = [(noticed, right) for noticed, right, _row in from_grammar + from_users]
    # the language-model users get a fixed share of the weight, however few readings they have
    per_user = user_share / (1.0 - user_share) * len(from_grammar) / max(1, len(from_users))
    beta = fit(examples, [1.0] * len(from_grammar) + [per_user] * len(from_users), names)
    held = [example for example in readings(calibration_rows, open_minded) if all(name in names for name in example[0][:2])]
    scores = [score(noticed, names, beta) for noticed, _right, _row in held]
    right = [float(was) for _noticed, was, _row in held]
    before, _ = calibration_error([1 / (1 + math.exp(-value)) for value in scores], right)
    stretch, shift = platt(scores, right)
    beta = beta * stretch
    beta[0] += shift
    after, bins = calibration_error([1 / (1 + math.exp(-score(noticed, names, beta))) for noticed, _r, _row in held], right)
    weights = {name: round(float(value), 6) for name, value in zip(names, beta)}
    verbs = {verb: action for verb, action in VERBS.items() if f"verb={verb}" in weights}
    model = minds_module.Model({"verbs": verbs, "weights": weights, "typical": {}})
    typical = {}
    for rule in ("window", "this", "app", "site"):
        stated = [model.probability(noticed) for noticed, _r, _row in from_grammar if noticed[0].startswith(rule + "/")]
        typical[rule] = round(sum(stated) / len(stated), 4) if stated else 0.5
    return {
        "version": 1,
        "about": "System 1's trained confidence. Made by bench/minds_train.py from simulated use; do not edit by hand.",
        "verbs": verbs, "weights": weights, "typical": typical,
        "training": {
            "grammar_requests": len(grammar_rows), "grammar_readings": len(from_grammar),
            "grammar_readings_right": sum(right for _n, right, _row in from_grammar),
            "user_requests": len(user_rows), "user_readings": len(from_users),
            "user_readings_right": sum(right for _n, right, _row in from_users),
            "calibration_requests": len(calibration_rows), "calibration_readings": len(held),
            "calibration_readings_right": int(sum(right)), "platt": [round(stretch, 6), round(shift, 6)],
            "calibration_ece_before": before, "calibration_ece_after": after, "calibration_bins": bins,
            "min_seen": MIN_SEEN, "ridge": RIDGE, "user_share": user_share, "seeds": [GRAMMAR_TRAIN[1], GRAMMAR_CALIBRATION[1]],
        },
    }


def shipped_training_data():
    return simulate.requests(*GRAMMAR_TRAIN), users("train"), users("calibration")


def rehearse(user_share=USER_SHARE, left_out=LEFT_OUT):
    """Leave one simulated person out, train on the rest, and see how honest the confidence is on them."""
    everyone = users("train") + users("calibration")
    people = sorted({row["person"] for row in everyone})
    grammar = simulate.requests(*GRAMMAR_TRAIN)
    stated, right, wrong_fast, fast = [], [], [], 0
    for person in people:
        rest = [row for row in everyone if row["person"] != person]
        half = [row for row in rest if row["split"] == "train"] or rest
        other = [row for row in rest if row["split"] == "calibration"] or rest
        model = minds_module.Model(train(grammar, half, other, user_share, left_out))
        minds = minds_module.Minds(None, model=model)
        for row in everyone:
            if row["person"] != person:
                continue
            decision = minds.decide(row["text"], row["windows"])
            if not decision.rule:
                continue
            was = simulate.fine(what(decision), row)
            stated.append(decision.confidence)
            right.append(float(was))
            if decision.mind == minds_module.FAST:
                fast += 1
                if not was:
                    wrong_fast.append((row["text"], row["kind"], decision.action, round(decision.confidence, 3)))
    error, bins = calibration_error(stated, right)
    surprise = -sum(math.log(said if was else 1.0 - said) for said, was in zip(stated, right)) / max(1, len(stated))
    return {"user_share": user_share, "left_out": list(left_out), "people": len(people), "readings": len(stated), "right": int(sum(right)),
            "log_loss": round(surprise, 4), "ece": error, "fast": fast, "wrong_fast": wrong_fast, "bins": bins}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rehearse", action="store_true")
    args = parser.parse_args()
    if args.rehearse:
        for share in (0.0, 0.1, 0.25, 0.5):
            print(json.dumps(rehearse(share)))
        print(json.dumps(rehearse(USER_SHARE, POLITE)))
        return
    model = train(*shipped_training_data())
    text = json.dumps(model, indent=1, sort_keys=True) + "\n"
    if args.check:
        same = MODEL.exists() and json.loads(MODEL.read_text(encoding="utf-8")) == json.loads(text)
        print("the model file is what training gives" if same else "the model file differs from a fresh training run")
        sys.exit(0 if same else 1)
    MODEL.write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps({key: model[key] for key in ("verbs", "weights", "typical", "training")}, indent=1))


if __name__ == "__main__":
    main()
