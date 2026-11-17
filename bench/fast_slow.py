"""Thinking fast and slow: when may System 1 (keyit/keyit_minds.py) act alone, and is its confidence honest?

    python bench/fast_slow.py --out bench/results_fast                 # lists, calibration, decision time
    python bench/fast_slow.py --out bench/results_fast --real 5        # + real actions on the bench's own window
    python bench/fast_slow.py --out bench/results_fast --brain 1       # + the same commands through the free brain

Two lists of requests, each labelled with what a careful assistant would do at once without looking at the
screen: (action, target), or None for "think first". DEV is the list the rules were written and counted
on (keyit_minds.PRIORS comes from it). TEST was written before the first run and is only scored.
Both lists are text, in English, by the author of the rules: they are not a sample of real requests.
"""
import argparse
import base64
import json
import statistics
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keyit"))
sys.path.insert(0, str(ROOT / "bench"))

import keyit_actions  # noqa: E402
import keyit_minds as minds_module  # noqa: E402


def window(app, process, title, hwnd):
    return {"app": app, "process": process, "title": title, "hwnd": hwnd, "pid": hwnd, "class": "", "minimized": False}


# The open windows the requests are read against, front first.
WINDOWS = [
    window("Excel", "excel", "BOM_rev3.xlsx - Excel", 1),
    window("Notepad", "notepad", "Untitled - Notepad", 2),
    window("Outlook", "outlook", "Inbox - Outlook", 3),
    window("Microsoft Edge", "msedge", "SRM Portal - Microsoft Edge", 4),
    window("File Explorer", "explorer", "Downloads - File Explorer", 5),
]
TWO_NOTEPADS = WINDOWS + [window("Notepad", "notepad", "todo.txt - Notepad", 6)]

DEV = [
    ("close notepad", ("close", "Notepad")), ("Close Notepad.", ("close", "Notepad")),
    ("please close outlook", ("close", "Outlook")), ("close the notepad window", ("close", "Notepad")),
    ("quit excel", ("close", "Excel")), ("exit outlook", ("close", "Outlook")),
    ("minimize outlook", ("minimize", "Outlook")), ("minimise excel", ("minimize", "Excel")),
    ("Minimize Notepad.", ("minimize", "Notepad")), ("maximize notepad", ("maximize", "Notepad")),
    ("maximize the excel window", ("maximize", "Excel")), ("switch to outlook", ("focus", "Outlook")),
    ("go to excel", ("focus", "Excel")), ("switch to notepad", ("focus", "Notepad")),
    ("switch to file explorer", ("focus", "File Explorer")), ("close file explorer", ("close", "File Explorer")),
    ("minimize explorer", ("minimize", "File Explorer")), ("switch to edge", ("focus", "Microsoft Edge")),
    ("maximize microsoft edge", ("maximize", "Microsoft Edge")), ("close edge", ("close", "Microsoft Edge")),
    ("go to the notepad window", ("focus", "Notepad")), ("please minimize notepad", ("minimize", "Notepad")),
    ("minimize this", ("minimize", "Excel")), ("maximize this window", ("maximize", "Excel")),
    ("close this", ("close", "Excel")), ("minimize this window", ("minimize", "Excel")),
    ("close this window", ("close", "Excel")), ("maximize this", ("maximize", "Excel")),
    ("open notepad", ("open", "notepad")), ("open calculator", ("open", "calculator")),
    ("launch paint", ("open", "paint")), ("start task manager", ("open", "task manager")),
    ("open chrome", ("open", "chrome")), ("Open Excel.", ("open", "excel")), ("open word", ("open", "word")),
    ("open powerpoint", ("open", "powerpoint")), ("launch file explorer", ("open", "file explorer")),
    ("open settings", ("open", "settings")), ("start outlook", ("open", "outlook")),
    ("open terminal", ("open", "terminal")), ("please open teams", ("open", "teams")),
    ("open control panel", ("open", "control panel")),
    ("open google.com", ("open", "google.com")), ("open github.com", ("open", "github.com")),
    ("open www.wikipedia.org", ("open", "www.wikipedia.org")), ("launch youtube.com", ("open", "youtube.com")),
    ("open outlook.com", ("open", "outlook.com")), ("open python.org", ("open", "python.org")),
    ("start maps.google.com", ("open", "maps.google.com")), ("open example.net", ("open", "example.net")),
    ("open readme.md", None), ("open report.docx", None),
    ("how do I close notepad", None), ("what does this button do", None),
    ("close notepad and open excel", None), ("open notepad then type hello", None), ("don't close notepad", None),
    ("close notepad if it's saved", None), ("close everything", None), ("minimize all windows", None),
    ("close the popup", None), ("close it", None), ("close that", None), ("close this tab", None),
    ("open the file I was working on", None), ("open my email", None), ("open sesame", None), ("start over", None),
    ("start recording", None), ("go to sleep", None), ("go to the next page", None), ("switch to teams", None),
    ("close word", None), ("close notepad plus plus", None), ("exit full screen", None), ("quit talking", None),
    ("can you close notepad", ("close", "Notepad")), ("close excel?", None), ("open a new tab", None),
    ("open the downloads folder", None), ("close the other window", None), ("maybe close outlook", None),
    ("close outlook later", None), ("where is the save button", None), ("save it", ("keys", "ctrl+s")),
    ("click insert", None), ("type hello", ("type", "hello")), ("explain this error", None),
    ("open notepad or wordpad", None), ("minimize", None), ("close", None), ("open excel file bom", None),
    ("switch to the other one", None), ("close both excel windows", None), ("is notepad open", None),
    ("tell me how to open settings", None),
]

TEST = [
    ("close outlook", ("close", "Outlook")), ("Close Excel.", ("close", "Excel")),
    ("please close notepad", ("close", "Notepad")), ("quit notepad", ("close", "Notepad")),
    ("exit excel", ("close", "Excel")), ("close the outlook window", ("close", "Outlook")),
    ("minimize excel", ("minimize", "Excel")), ("minimise notepad", ("minimize", "Notepad")),
    ("please minimize outlook", ("minimize", "Outlook")), ("minimize the excel window", ("minimize", "Excel")),
    ("maximize outlook", ("maximize", "Outlook")), ("maximise excel", ("maximize", "Excel")),
    ("maximize edge", ("maximize", "Microsoft Edge")), ("switch to excel", ("focus", "Excel")),
    ("go to notepad", ("focus", "Notepad")), ("go to outlook", ("focus", "Outlook")),
    ("switch to microsoft edge", ("focus", "Microsoft Edge")), ("switch to the excel window", ("focus", "Excel")),
    ("go to file explorer", ("focus", "File Explorer")), ("quit edge", ("close", "Microsoft Edge")),
    ("maximize file explorer", ("maximize", "File Explorer")), ("close explorer", ("close", "File Explorer")),
    ("maximise this", ("maximize", "Excel")), ("minimize this app", ("minimize", "Excel")),
    ("close this app", ("close", "Excel")), ("minimise this window", ("minimize", "Excel")),
    ("close the current window", ("close", "Excel")), ("maximize this window please", ("maximize", "Excel")),
    ("open paint", ("open", "paint")), ("launch notepad", ("open", "notepad")),
    ("start calculator", ("open", "calculator")), ("open edge", ("open", "edge")),
    ("open microsoft edge", ("open", "microsoft edge")), ("launch excel", ("open", "excel")),
    ("open outlook", ("open", "outlook")), ("start word", ("open", "word")),
    ("open task manager", ("open", "task manager")), ("launch chrome", ("open", "chrome")),
    ("open command prompt", ("open", "command prompt")), ("open snipping tool", ("open", "snipping tool")),
    ("start teams", ("open", "teams")), ("open explorer", ("open", "explorer")),
    ("open bing.com", ("open", "bing.com")), ("open www.github.com", ("open", "www.github.com")),
    ("launch openai.com", ("open", "openai.com")), ("open docs.python.org", ("open", "docs.python.org")),
    ("how do I minimize this", None), ("what is this window for", None),
    ("minimize outlook and close excel", None), ("close excel then open it again", None),
    ("do not close outlook", None), ("never minimize excel", None), ("close outlook when the mail is sent", None),
    ("close all windows", None), ("minimize everything", None), ("close the dialog", None), ("minimize it", None),
    ("maximize that", None), ("close this file", None), ("open the report from yesterday", None),
    ("open my calendar", None), ("open up", None), ("start again", None), ("start the test", None),
    ("go to bed", None), ("go to the last slide", None), ("switch to chrome", None), ("close powerpoint", None),
    ("close outlook express", None), ("exit presentation mode", None), ("quit complaining", None),
    ("could you minimize excel", ("minimize", "Excel")), ("minimize outlook?", None),
    ("open a blank document", None), ("open the bom folder", None), ("close every other window", None),
    ("should I close outlook", None), ("close excel after saving", None), ("where do I click to save", None),
    ("copy this", ("keys", "ctrl+c")), ("click send", None), ("type best regards", ("type", "best regards")),
    ("summarize this page", None), ("open paint or photos", None), ("maximize", None), ("quit", None),
    ("open notepad file notes", None), ("switch to the previous one", None), ("close both notepads", None),
    ("are there any unread mails", None), ("show me how to open task manager", None),
    ("close notepad", None, TWO_NOTEPADS), ("switch to notepad", None, TWO_NOTEPADS), ("minimize this", None, []),
]


def what(decision):
    """(action, target) the way the lists write it."""
    if decision.action is None:
        return None
    action, argument = decision.action
    return (action, decision.window["app"]) if decision.window else (action, argument)


def score(items, minds):
    """Every request of a list through System 1."""
    rows = []
    for item in items:
        text, expected = item[0], item[1]
        windows = item[2] if len(item) > 2 else WINDOWS
        decision = minds.decide(text, windows)
        fast = decision.mind == minds_module.FAST
        rows.append({
            "text": text, "expected": expected, "mind": decision.mind, "why": decision.why, "rule": decision.rule,
            "did": what(decision), "confidence": round(decision.confidence, 4),
            "read_right": bool(decision.rule) and what(decision) == expected,
            "wrong_fast": fast and what(decision) != expected,
            "missed": not fast and expected is not None,
        })
    return rows


def by_rule(rows):
    """{rule: (right, seen)} over every reading a rule made, acted on or not."""
    counts = {}
    for row in rows:
        if row["rule"]:
            right, seen = counts.get(row["rule"], (0, 0))
            counts[row["rule"]] = (right + row["read_right"], seen + 1)
    return counts


def summary(rows, minds):
    one_step = [row for row in rows if row["expected"] is not None]
    fast = [row for row in rows if row["mind"] == minds_module.FAST]
    rules = {}
    total_gap, readings = 0.0, 0
    for rule, (right, seen) in sorted(by_rule(rows).items()):
        stated = minds.sure(rule)
        rules[rule] = {"stated": round(stated, 3), "right": right, "seen": seen, "observed": round(right / seen, 3),
                       "overconfident_by": round(stated - right / seen, 3)}
        total_gap += abs(stated - right / seen) * seen
        readings += seen
    return {
        "requests": len(rows), "one_step_commands": len(one_step), "must_think": len(rows) - len(one_step),
        "done_fast": len(fast), "wrong_fast": sum(row["wrong_fast"] for row in rows),
        "one_step_done_fast": sum(row["mind"] == minds_module.FAST for row in one_step),
        "missed": [row["text"] for row in rows if row["missed"]],
        "rules": rules, "ece": round(total_gap / readings, 4) if readings else None,
    }


def practice_needed():
    """How many times the brain must do a phrase, unchallenged, before System 1 does it alone."""
    table = {}
    for action, bar in minds_module.STAKES.items():
        times = 0
        while minds_module.confidence(times, times) < bar:
            times += 1
        table[action] = {"bar": bar, "times": times}
    return table


def cpu_load():
    try:
        import psutil
        return psutil.cpu_percent(interval=1.0)
    except ImportError:
        return None


def decision_time(minds, repeats=200):
    """Seconds for one decide(), over both lists."""
    items = [(item[0], item[2] if len(item) > 2 else WINDOWS) for item in DEV + TEST]
    took = []
    for _ in range(repeats):
        for text, windows in items:
            started = time.perf_counter()
            minds.decide(text, windows)
            took.append(time.perf_counter() - started)
    took.sort()
    return {"decisions": len(took), "median_ms": round(statistics.median(took) * 1000, 4),
            "p95_ms": round(took[int(len(took) * 0.95)] * 1000, 4), "max_ms": round(took[-1] * 1000, 4)}


BENCH_WINDOW = """
import tkinter
root = tkinter.Tk()
root.title("Buddy bench window")
root.geometry("420x200+80+80")
root.after(120000, root.destroy)
root.mainloop()
"""


def real_actions(rounds):
    """Words to done, for real: System 1 minimizes, maximizes and switches to a window this bench opened.
    The window lives in a second process, because Buddy never lists (or acts on) its own windows."""
    import ctypes
    import subprocess

    import keyit_buddy

    child = subprocess.Popen([sys.executable, "-c", BENCH_WINDOW], creationflags=0x08000000)   # no console
    try:
        mine, listed = [], []
        for _ in range(100):
            started = time.perf_counter()
            windows = keyit_actions.list_windows()
            listed.append(time.perf_counter() - started)
            mine = [w for w in windows if w["title"] == "Buddy bench window"]
            if mine:
                break
            time.sleep(0.1)
        if len(mine) != 1:
            return {"error": "the bench window was not listed exactly once"}
        name, hwnd = mine[0]["process"], mine[0]["hwnd"]
        said = []
        buddy = types.SimpleNamespace(
            minds=minds_module.Minds(None), settings={"fast_thinking": True, "buddy_speak": False}, windows=[],
            history=[], last_reply=None, last_timing=None, confirm=lambda question: False, speaker=None,
            _stop_look_ahead=lambda: None, on_status=said.append, post=lambda *args: None,
            overlay=types.SimpleNamespace(say=None, hide_later=None))
        user32 = ctypes.windll.user32
        checks = {"minimize": lambda: bool(user32.IsIconic(hwnd)), "maximize": lambda: bool(user32.IsZoomed(hwnd)),
                  "switch to": lambda: user32.GetForegroundWindow() == hwnd}
        rows = []
        for _ in range(rounds):
            for verb, check in checks.items():
                started = time.perf_counter()
                done = keyit_buddy.think_fast(buddy, f"{verb} {name}")
                took = time.perf_counter() - started
                changed = False
                for _ in range(20):                     # the window answers in its own time: look for up to 1 s
                    changed = check()
                    if changed:
                        break
                    time.sleep(0.05)
                rows.append({"said": f"{verb} {name}", "done_by_system_1": done, "seconds": round(took, 4),
                             "window_really_changed": changed, "status": said[-1] if said else ""})
    finally:
        child.kill()
    took = sorted(row["seconds"] for row in rows)
    listed.sort()
    return {"rows": rows, "median_s": statistics.median(took), "max_s": took[-1],
            "all_done": all(row["done_by_system_1"] and row["window_really_changed"] for row in rows),
            "list_windows_median_ms": round(statistics.median(listed) * 1000, 3),
            "note": "from the words to the action returning; speech and the on-screen bubble are not in it"}


BRAIN_COMMANDS = [("close outlook", ("close", "outlook")), ("minimize excel", ("minimize", "excel")),
                  ("switch to notepad", ("focus", "notepad")), ("open paint", ("open", "paint")),
                  ("maximize outlook", ("maximize", "outlook")), ("go to file explorer", ("focus", "file explorer"))]


def same_action(got, expected):
    """The brain names a window by its title, so compare the window it means, not the words it used."""
    if got[0] != expected[0]:
        return False
    if got[0] == "open":
        return keyit_actions.APP_ALIASES.get(got[1], got[1]) == keyit_actions.APP_ALIASES.get(expected[1], expected[1])
    window = keyit_actions.find_window(got[1], WINDOWS)
    return window is not None and window["app"].lower() == expected[1]


def through_the_brain(rounds):
    """The same one-step commands the slow way: Buddy's prompt and a drawn screenshot to the free cloud brain.
    Timed to the end of the reply, because the action tag is the last thing in it."""
    import keyit_buddy
    import keyit_free
    import reply_language

    png = base64.b64encode(reply_language.draw_screen()).decode()
    system = (keyit_buddy.SYSTEM_PROMPT + "\n" + keyit_actions.ACTION_RULES + "\nOpen windows (front first):\n"
              + keyit_actions.describe_windows(WINDOWS))
    client = keyit_free.FreeClient()
    client.warm_up()
    rows = []
    for _ in range(rounds):
        for command, expected in BRAIN_COMMANDS:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": command, "images": [png]}]
            started = time.perf_counter()
            try:
                reply, error = "".join(client.chat_stream(keyit_free.MODEL, messages, timeout=90)), ""
            except Exception as problem:
                reply, error = "", str(problem)
            took = time.perf_counter() - started
            actions = [(action, argument.lower()) for action, argument in keyit_actions.parse_actions(reply)]
            rows.append({"said": command, "seconds": round(took, 3), "reply": " ".join(reply.split())[:160],
                         "error": error, "same_action": bool(actions) and same_action(actions[0], expected)})
    answered = sorted(row["seconds"] for row in rows if not row["error"])
    return {"brain": "free cloud brain (" + keyit_free.CHAT_URL + ")", "rows": rows,
            "median_s": statistics.median(answered) if answered else None,
            "min_s": answered[0] if answered else None, "max_s": answered[-1] if answered else None,
            "errors": sum(bool(row["error"]) for row in rows)}


def markdown(result):
    lines = ["# Thinking fast and slow: System 1 on two lists", ""]
    lines.append(f"CPU load while measuring: {result['cpu_load_percent']}%. Lists are English text by the rules' author.")
    lines += ["", "| | DEV (rules counted on it) | TEST (held out) |", "|---|---|---|"]
    dev, test = result["dev"], result["test"]
    for label, key in (("Requests", "requests"), ("One-step commands", "one_step_commands"),
                       ("Must think first", "must_think"), ("Done by System 1", "done_fast"),
                       ("Wrong fast actions", "wrong_fast"), ("One-step commands done fast", "one_step_done_fast"),
                       ("Calibration error (ECE)", "ece")):
        lines.append(f"| {label} | {dev[key]} | {test[key]} |")
    lines += ["", "| Rule | Stated confidence | Right on TEST | Stated minus observed |", "|---|---|---|---|"]
    for rule, row in test["rules"].items():
        lines.append(f"| {rule} | {row['stated']} | {row['right']} of {row['seen']} | {row['overconfident_by']:+} |")
    lines += ["", "Left to the brain although one step: " + ", ".join(f'"{text}"' for text in test["missed"]), ""]
    timing = result["decision_time"]
    lines.append(f"One decision: {timing['median_ms']} ms median, {timing['p95_ms']} ms p95, {timing['max_ms']} ms "
                 f"slowest, over {timing['decisions']} decisions.")
    lines += ["", "| Action | Bar | Times the brain must do it first |", "|---|---|---|"]
    for action, row in result["practice_needed"].items():
        lines.append(f"| {action} | {row['bar']} | {row['times']} |")
    if result.get("real"):
        real = result["real"]
        lines += ["", f"Real actions on the bench's own window: {real.get('median_s')} s median, "
                      f"{real.get('max_s')} s slowest, all done and checked: {real.get('all_done')}."]
    if result.get("brain"):
        brain = result["brain"]
        lines += ["", f"The same kind of command through the {brain['brain']}: {brain['median_s']} s median "
                      f"({brain['min_s']} to {brain['max_s']} s), {brain['errors']} errors in {len(brain['rows'])} asks."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--real", type=int, default=0, help="rounds of real actions on the bench's own window")
    parser.add_argument("--brain", type=int, default=0, help="rounds of the same commands through the free brain")
    parser.add_argument("--priors", action="store_true", help="print the DEV counts for keyit_minds.PRIORS and stop")
    args = parser.parse_args()
    minds = minds_module.Minds(None)
    dev_rows = score(DEV, minds)
    if args.priors:
        print(by_rule(dev_rows))
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    previous = json.loads((out / "fast_slow.json").read_text(encoding="utf-8")) if (out / "fast_slow.json").exists() else {}
    test_rows = score(TEST, minds)
    result = {
        "cpu_load_percent": cpu_load(), "priors": minds_module.PRIORS, "priors_match_dev": by_rule(dev_rows) == {
            rule: tuple(counts) for rule, counts in minds_module.PRIORS.items()},
        "dev": summary(dev_rows, minds), "test": summary(test_rows, minds),
        "decision_time": decision_time(minds), "practice_needed": practice_needed(),
        "real": real_actions(args.real) if args.real else previous.get("real"),
        "brain": through_the_brain(args.brain) if args.brain else previous.get("brain"),
        "rows": {"dev": dev_rows, "test": test_rows},
    }
    (out / "fast_slow.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out / "fast_slow.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))


if __name__ == "__main__":
    main()
