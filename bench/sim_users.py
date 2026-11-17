"""Simulated users, written by language models: what would this person say to get this done?

    python bench/sim_users.py --out bench/data/sim_users.json

Each call gives a model a person, the windows open on their PC and six wishes, and asks for three things
that person might say for each wish. The right answer for every line is known from the wish it was written
for. Two different models write: Llama 4 Scout writes the people used for training and calibration,
Mistral Small 3.1 writes four other people who are only used for the final test.

These are not real users. They are a second and third author for the requests, who never saw Buddy's rules.
The models run on Buddy's own free brain (cloud/brain), so each call counts against its shared daily cap.
"""
import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import simulate  # noqa: E402

URL = "https://buddy-brain.juwenmondial.workers.dev/v1/chat/completions"
PEOPLE = {
    "train": ("buddy-scout", [
        "an office worker in a hurry who types short lowercase commands with no punctuation",
        "a polite older person who speaks to the assistant in full, courteous sentences",
        "a software engineer who is precise and uses the exact names of apps",
        "a student who talks casually, with slang and filler words",
    ]),
    "calibration": ("buddy-scout", [
        "a non-native English speaker who uses simple grammar and sometimes an unusual word order",
        "a manager dictating by voice between meetings, in natural spoken English",
    ]),
    "test": ("buddy-mistral", [
        "a new computer user who is unsure of the right words for things on the screen",
        "a designer who speaks in a relaxed, friendly way",
        "an accountant who gives brief, direct instructions",
        "a parent multitasking at home, speaking quickly and informally",
    ]),
}
WORLDS_PER_PERSON = {"train": 7, "calibration": 7, "test": 6}
TOP_UP = "Write all 18 lines: three for each of the six numbers."
SYSTEM = ("You write test data for a desktop assistant that listens to its user. You play one user and write "
          "exactly what that user would type or say to the assistant. Output only the lines asked for.")


def wishes(rng, windows):
    """Six wishes for this PC: three that are one plain step, three that need a look or a think first.
    Each is (kind, what the user wants in words, the right answer)."""
    front = windows[0]["app"]
    unique = simulate.unique_apps(windows)
    spoken = simulate.name_of
    plain, think = [], []
    for app in unique:
        plain += [
            ("close window", f"wants the {spoken(app)} window closed", ("close", app)),
            ("minimize window", f"wants the {spoken(app)} window minimized (out of the way, not closed)", ("minimize", app)),
            ("maximize window", f"wants the {spoken(app)} window to fill the whole screen", ("maximize", app)),
            ("switch window", f"wants the {spoken(app)} window, which is already open, brought to the front", ("focus", app)),
        ]
    for action, words in (("close", "closed"), ("minimize", "minimized"), ("maximize", "made full screen")):
        plain.append((f"{action} this", f"wants the window they are working in right now {words}, and refers to it "
                                        f"without saying its name", (action, front)))
    closed = [name for name in simulate.KNOWN_TO_OPEN + simulate.NOT_KNOWN_TO_OPEN
              if not any(name.lower() in window["app"].lower() for window in windows)]
    for name in rng.sample(closed, 3):
        plain.append(("open app", f"wants to start the {name} app, which is not open yet", ("open", name.lower())))
    for site in rng.sample(simulate.SITES, 2):
        plain.append(("open site", f"wants the website {site} opened", ("open", site)))

    some_app = spoken(rng.choice(windows)["app"])
    other_app = spoken(rng.choice(windows)["app"])
    think += [
        ("close tab", "wants only the current browser tab closed, not the whole browser window", None),
        ("close dialog", f"wants a pop-up message that just appeared in {some_app} dismissed", None),
        ("close file", f"wants the document that is open in {some_app} closed while {some_app} itself stays open", None),
        ("open file", f"wants to open {rng.choice(simulate.FILES)}", None),
        ("app settings", f"wants the options or settings page inside {some_app} (not Windows Settings)", None),
        ("how to", f"wants to be told how to {rng.choice(['close', 'minimize', 'maximize', 'open'])} something "
                   f"themselves, not have it done", None),
        ("two steps", f"wants two things in one go: {some_app} closed and {rng.choice(closed)} started", None),
        ("later", f"wants {some_app} closed, but only after it has finished saving", None),
        ("everything", "wants every open window minimized or closed at once", None),
        ("do not", f"wants to make sure the assistant does NOT close {other_app}", None),
        ("inside an app", f"wants to move inside {some_app}: to the next page, another tab or another folder", None),
        ("start a thing", "wants to start something that is not an app: a timer, a recording, a meeting or a new "
                          "document", None),
        ("not open", f"wants to switch to {rng.choice(closed)}, and has not noticed that it is not open", None),
    ]
    for app in simulate.twin_apps(windows):
        think.append(("which one", f"wants one of the {spoken(app)} windows closed, without saying which", None))
    return rng.sample(plain, 3) + rng.sample(think, 3)


def ask(model, person, windows, wanted, temperature=0.9, extra=""):
    listed = ", ".join(f"{simulate.name_of(window['app'])} ({window['title']})" for window in windows)
    lines = "\n".join(f"{number}. The user {wish}." for number, (_kind, wish, _answer) in enumerate(wanted, 1))
    prompt = (f"The user is {person}.\nWindows open on their PC, front first: {listed}.\n\n"
              f"For each wish below, write 3 different things this user might say to the assistant to get it. "
              f"Each is one short utterance in the user's own style. Do not explain, and do not mention these "
              f"instructions.{' ' + extra if extra else ''}\nFormat, one per line: number| utterance\n\n{lines}")
    body = {"model": model, "temperature": temperature, "max_tokens": 500, "stream": False,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
    request = urllib.request.Request(URL, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "User-Agent": "Buddy/1.6 (bench)"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def lines_of(reply, count):
    found = []
    for line in str(reply).splitlines():
        match = re.match(r"^\s*(\d+)\s*[|.:)\-]+\s*(.+?)\s*$", line)
        if match and 1 <= int(match.group(1)) <= count:
            text = match.group(2).strip().strip('"\u201c\u201d\'').strip()
            if text:
                found.append((int(match.group(1)) - 1, text))
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--pause", type=float, default=4.0, help="seconds between calls (the worker allows 20 a minute)")
    parser.add_argument("--splits", default=",".join(PEOPLE), help="which splits to write, comma separated")
    parser.add_argument("--worlds", type=int, default=0, help="PCs per person (default: WORLDS_PER_PERSON)")
    parser.add_argument("--add", action="store_true", help="add to the file instead of starting it again")
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, calls, batches = [], [], []
    if args.add:
        before = json.loads(out.read_text(encoding="utf-8"))
        rows, calls = before["rows"], before["calls"]
        batches = before.get("batches") or [{"seed": before["seed"], "splits": list(PEOPLE), "extra": ""}]
    # the first batch often came back with one line per wish, so later batches say how many lines are wanted
    extra = TOP_UP if args.add else ""
    batches.append({"seed": args.seed, "splits": args.splits.split(","), "extra": extra})
    for split, (model, people) in PEOPLE.items():
        if split not in args.splits.split(","):
            continue
        for person_number, person in enumerate(people):
            rng = random.Random(f"{args.seed}/{split}/{person_number}")
            for world_number in range(args.worlds or WORLDS_PER_PERSON[split]):
                windows = []
                while not windows:
                    windows = simulate.world(rng, empty=0)
                wanted = wishes(rng, windows)
                reply, error = "", ""
                for attempt in range(3):
                    try:
                        reply, error = ask(model, person, windows, wanted, extra=extra), ""
                        break
                    except urllib.error.HTTPError as problem:
                        error = f"{problem.code} {problem.read(200).decode('utf-8', 'replace')}"
                        if "daily_cap" in error:
                            break
                        time.sleep(20)
                    except Exception as problem:
                        error = str(problem)
                        time.sleep(10)
                calls.append({"split": split, "model": model, "person": person, "error": error, "reply": reply})
                for index, text in lines_of(reply, len(wanted)):
                    kind, wish, answer = wanted[index]
                    rows.append({"split": split, "writer": model, "person": person,
                                 "world": f"{args.seed}/{world_number}" if args.add else world_number,
                                 "windows": windows, "kind": kind, "wish": wish,
                                 "expected": list(answer) if answer else None, "text": text})
                print(split, person_number, world_number, len(lines_of(reply, len(wanted))), error[:80], flush=True)
                out.write_text(json.dumps({"seed": batches[0]["seed"], "system": SYSTEM, "batches": batches,
                                           "rows": rows, "calls": calls},
                                          indent=1, ensure_ascii=False), encoding="utf-8")
                if "daily_cap" in error:
                    print("the free brain is out of questions for today; stopping")
                    return
                time.sleep(args.pause)
    print({split: sum(row["split"] == split for row in rows) for split in PEOPLE})


if __name__ == "__main__":
    main()
