"""Simulated use for System 1 (keyit/keyit_minds.py): PCs with windows open, and people asking for things.

Everything here is made up by a program, on purpose: for each request the right answer is known because
the request was written *from* the wish, not guessed afterwards. That gives thousands of labelled
requests in seconds, where waiting for real use would take months. What simulation cannot give is proof
that real people talk like this, so the trained mind is always scored on requests this file did not write
(bench/fast_calibration.py).

    python bench/simulate.py --requests 5 --seed 1        # print a few simulated requests
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "keyit"))

import keyit_actions  # noqa: E402

# name people use, app and process as keyit_actions.list_windows() reports them, window titles
APPS = [
    ("Excel", "Excel", "excel", ["BOM_rev3.xlsx - Excel", "Budget 2026.xlsx - Excel", "Book1 - Excel"]),
    ("Word", "Word", "winword", ["Report.docx - Word", "Document1 - Word", "Minutes.docx - Word"]),
    ("PowerPoint", "PowerPoint", "powerpnt", ["Review.pptx - PowerPoint", "Presentation1 - PowerPoint"]),
    ("Outlook", "Outlook", "outlook", ["Inbox - Outlook", "Calendar - Outlook"]),
    ("Notepad", "Notepad", "notepad", ["Untitled - Notepad", "todo.txt - Notepad", "notes.txt - Notepad"]),
    ("Edge", "Microsoft Edge", "msedge", ["News - Microsoft Edge", "SRM Portal - Microsoft Edge"]),
    ("Chrome", "Google Chrome", "chrome", ["Gmail - Google Chrome", "YouTube - Google Chrome"]),
    ("File Explorer", "File Explorer", "explorer", ["Downloads - File Explorer", "Documents - File Explorer"]),
    ("Teams", "Microsoft Teams", "ms-teams", ["Chat | Microsoft Teams", "Calendar | Microsoft Teams"]),
    ("VS Code", "VS Code", "code", ["main.py - Visual Studio Code", "README.md - Visual Studio Code"]),
    ("Calculator", "Calculator", "applicationframehost", ["Calculator"]),
    ("Settings", "Settings", "applicationframehost", ["Settings"]),
    ("Paint", "mspaint", "mspaint", ["Untitled - Paint", "sketch.png - Paint"]),
    ("Spotify", "spotify", "spotify", ["Spotify Premium", "Spotify Free"]),
    ("Slack", "slack", "slack", ["Slack", "general - Slack"]),
    ("Zoom", "zoom", "zoom", ["Zoom Meeting", "Zoom Workplace"]),
]
BROWSERS = ("Microsoft Edge", "Google Chrome")
DOCUMENT_APPS = ("Excel", "Word", "PowerPoint", "Notepad", "VS Code", "mspaint")
KNOWN_TO_OPEN = ["Notepad", "Calculator", "Paint", "Task Manager", "Settings", "Edge", "Microsoft Edge", "Chrome",
                 "Word", "Excel", "PowerPoint", "Outlook", "Teams", "Command Prompt", "Terminal", "Control Panel",
                 "Snipping Tool", "File Explorer"]
NOT_KNOWN_TO_OPEN = ["Spotify", "Photoshop", "Zoom", "Slack", "Steam", "Discord", "Firefox", "OneNote", "WhatsApp"]
SITES = ["google.com", "youtube.com", "github.com", "wikipedia.org", "bbc.com", "amazon.com", "weather.com",
         "nytimes.com", "reddit.com", "openai.com", "python.org", "stackoverflow.com", "linkedin.com",
         "maps.google.com", "www.microsoft.com", "docs.python.org", "example.net", "huggingface.co"]
FILES = ["report.docx", "budget.xlsx", "notes.txt", "the invoice from yesterday", "my resume", "the photo I just saved",
         "the downloads folder", "the project folder", "readme.md", "slides for monday"]


def name_of(app):
    """The name people use for an app, from the name the window list gives it."""
    return next(spoken for spoken, listed, _process, _titles in APPS if listed == app)


def world(rng, twins=0.15, empty=0.03):
    """Open windows, front first: 1 to 6 apps, now and then two windows of one app, rarely none."""
    if rng.random() < empty:
        return []
    chosen = rng.sample(APPS, rng.randint(1, 6))
    windows = []
    for _spoken, listed, process, titles in chosen:
        windows.append((listed, process, rng.choice(titles)))
    with_spares = [app for app in chosen if len(app[3]) > 1]
    if with_spares and rng.random() < twins:
        _spoken, listed, process, titles = rng.choice(with_spares)
        windows.append((listed, process, rng.choice(titles)))
    rng.shuffle(windows)
    return [{"app": app, "process": process, "title": title, "hwnd": index + 1, "pid": index + 1, "class": "",
             "minimized": False} for index, (app, process, title) in enumerate(windows)]


def unique_apps(windows):
    apps = [window["app"] for window in windows]
    return [app for app in dict.fromkeys(apps) if apps.count(app) == 1]


def twin_apps(windows):
    apps = [window["app"] for window in windows]
    return [app for app in dict.fromkeys(apps) if apps.count(app) > 1]


def site_key(text):
    return re.sub(r"^(https?://)?(www\.)?", "", text.lower()).rstrip("/")


def same(did, expected):
    """Is what System 1 would do the thing that was wanted? Both are (action, target) or None."""
    if did is None or expected is None:
        return did is None and expected is None
    if did[0] != expected[0]:
        return False
    got, wanted = str(did[1]).lower(), str(expected[1]).lower()
    if did[0] != "open":
        return got == wanted
    aliases = keyit_actions.APP_ALIASES
    return aliases.get(got, site_key(got)) == aliases.get(wanted, site_key(wanted))


def fine(did, row):
    """Is doing `did` right for this request? Asking to switch to an app that is not open has one more right
    answer: opening it, when the person named it and said "open"."""
    wanted = tuple(row["expected"]) if row.get("expected") else None
    if same(did, wanted):
        return True
    closed_app = re.match(r"wants to switch to (.+?), and has not noticed", row.get("wish") or "")
    return bool(did and closed_app and row.get("kind") == "not open" and same(did, ("open", closed_app.group(1))))


# --------------------------------------------------------------------------- the grammar simulator
# How often each wording is used is an assumption of this simulator, not a measurement of real people.
# Each list is (weight, wording). {x} is the app or site, {this} a way to say "the window I am in".
CLOSE_VERBS = [(60, "close"), (8, "quit"), (8, "exit"), (4, "shut"), (4, "shut down"), (3, "kill"), (4, "get rid of"),
               (3, "close out"), (2, "close down"), (3, "exit out of"), (1, "stop")]
MINIMIZE_VERBS = [(64, "minimize"), (10, "minimise"), (14, "hide"), (4, "shrink"), (3, "put away"), (5, "put {x} away")]
MAXIMIZE_VERBS = [(60, "maximize"), (10, "maximise"), (8, "full screen"), (6, "enlarge"), (12, "make {x} full screen"),
                  (4, "make {x} fullscreen")]
FOCUS_VERBS = [(28, "switch to"), (18, "go to"), (8, "bring up"), (6, "show"), (6, "pull up"), (4, "switch over to"),
               (6, "go back to"), (4, "jump to"), (4, "focus on"), (4, "open"), (5, "bring {x} to the front"),
               (4, "bring {x} to front"), (3, "bring {x} up")]
OPEN_VERBS = [(55, "open"), (12, "launch"), (12, "start"), (8, "open up"), (4, "fire up"), (3, "start up"), (4, "run"),
              (2, "load")]
VERBS = {"close": CLOSE_VERBS, "minimize": MINIMIZE_VERBS, "maximize": MAXIMIZE_VERBS, "focus": FOCUS_VERBS,
         "open": OPEN_VERBS}
THIS_WORDS = [(34, "this"), (30, "this window"), (10, "it"), (5, "this app"), (5, "the current window"), (5, "window"),
              (5, "the window"), (3, "this one"), (2, "that"), (1, "the active window")]
# what people say for "this" when they mean a tab, a pop-up or a file rather than the window (an assumption)
TRAP_THIS = [(50, "this"), (20, "it"), (10, "that"), (10, "this one"), (10, "this window")]
NAMED = [(60, "{x}"), (12, "the {x}"), (10, "{x} window"), (8, "the {x} window"), (5, "the {x} app"), (5, "my {x}")]
OTHER_NAMES = {"Microsoft Edge": ["edge", "microsoft edge", "the browser"], "Google Chrome": ["chrome", "google chrome"],
               "Microsoft Teams": ["teams", "microsoft teams", "ms teams"], "VS Code": ["vs code", "code", "vscode",
               "visual studio code"], "File Explorer": ["file explorer", "explorer", "files", "the file manager"],
               "mspaint": ["paint"], "PowerPoint": ["powerpoint", "ppt"], "Calculator": ["calculator", "calc"]}
OPENERS = [(86, ""), (3, "hey buddy "), (2, "ok "), (2, "buddy, "), (2, "hey, "), (2, "um "), (2, "just "), (1, "now ")]
FRAMES = [(66, ""), (8, "can you "), (6, "could you "), (5, "please "), (3, "would you "), (3, "i want to "),
          (3, "i need to "), (2, "can you please "), (2, "i'd like to "), (1, "let's "), (1, "go ahead and ")]
CLOSERS = [(78, ""), (9, " please"), (5, " for me"), (3, " now"), (2, " thanks"), (2, " real quick"), (1, " pls")]

# Wishes that need a look or a think first, and how people word them. Some wordings are also plain commands
# for something else ("close this" for a tab): those are the traps System 1 must learn to be careful with.
THINK = {
    "close tab": [(40, "close this tab"), (18, "close the tab"), (8, "close tab"), (8, "close this page"),
                  (9, "close {this}"), (6, "close the current tab"), (6, "exit this tab"), (5, "close this website")],
    "close dialog": [(22, "close this popup"), (14, "dismiss this"), (14, "close the dialog"), (12, "close this message"),
                     (10, "close {this}"), (10, "get rid of this popup"), (8, "close the popup in {x}"),
                     (10, "click ok")],
    "close file": [(26, "close this file"), (20, "close the document"), (10, "close {this}"), (16, "close this document"),
                   (12, "close the file in {x}"), (8, "close the {x} file"), (8, "close {x}")],
    "open file": [(40, "open {file}"), (20, "open the {file}"), (15, "open my {file}"), (15, "can you open {file}"),
                  (10, "pull up {file}")],
    "app settings": [(22, "open settings"), (20, "open {x} settings"), (12, "go to settings"), (12, "open the settings"),
                     (10, "open options"), (8, "settings"), (8, "open preferences"), (8, "show settings")],
    "how to": [(25, "how do i close this"), (20, "how to minimize a window"), (20, "how can i open {x}"),
               (15, "tell me how to close {x}"), (10, "what is the way to maximize {x}"), (10, "how do you quit {x}")],
    "two steps": [(30, "close {x} and open {y}"), (20, "close {x} then start {y}"), (15, "open {y}, close {x}"),
                  (15, "shut {x} and launch {y}"), (10, "close {x} & open {y}"), (10, "quit {x} open {y}")],
    "later": [(22, "close {x} when it's done saving"), (20, "close {x} after it saves"), (16, "close {x} once saved"),
              (14, "close {x} in 5 minutes"), (14, "close {x} later"), (14, "minimize {x} when the call ends")],
    "everything": [(22, "close everything"), (20, "minimize all windows"), (12, "close all"), (12, "minimize everything"),
                   (10, "hide all windows"), (12, "close all windows"), (6, "show desktop"), (6, "close the windows")],
    "do not": [(30, "don't close {x}"), (20, "do not close {x}"), (12, "never close {x}"), (14, "leave {x} open"),
               (12, "keep {x} open"), (12, "dont close {x}")],
    "inside an app": [(16, "go to the next page"), (14, "go to the next slide"), (12, "switch to the next tab"),
                      (10, "go to inbox"), (10, "go to downloads"), (10, "switch to the other tab"), (8, "go back"),
                      (8, "open the next tab"), (6, "go to sheet 2"), (6, "go to settings")],
    "start a thing": [(18, "start a timer"), (16, "start recording"), (14, "start a meeting"), (14, "open a new document"),
                      (10, "start a new tab"), (10, "new document"), (10, "start the meeting"), (8, "run the report")],
    "not open": [(40, "switch to {y}"), (25, "go to {y}"), (20, "close {y}"), (15, "minimize {y}")],
    "which one": [(50, "close {x}"), (25, "close the {x} window"), (25, "minimize {x}")],
}
TYPO = 0.04           # share of requests with one slip of the fingers
ALREADY_OPEN = 0.5    # how often "open X", said while X is open, means "bring me the X that is open"


def pick(rng, weighted):
    return rng.choices([item for _weight, item in weighted], [weight for weight, _item in weighted])[0]


def spoken(rng, app):
    names = OTHER_NAMES.get(app, [name_of(app).lower()])
    return rng.choice(names) if rng.random() < 0.6 else name_of(app).lower()


def slip(rng, text):
    """One typing slip: two letters swapped, or one dropped."""
    places = [index for index in range(len(text) - 1) if text[index].isalpha() and text[index + 1].isalpha()]
    if not places:
        return text
    at = rng.choice(places)
    if rng.random() < 0.5:
        return text[:at] + text[at + 1] + text[at] + text[at + 2:]
    return text[:at] + text[at + 1:]


def dress(rng, core):
    """The same command the way people actually put it: a "can you", a "please", a capital, a full stop."""
    opener, frame, closer = pick(rng, OPENERS), pick(rng, FRAMES), pick(rng, CLOSERS)
    text = opener + frame + core + closer
    if frame and frame != "please " and frame.split()[0] in ("can", "could", "would") and rng.random() < 0.5:
        text += "?"
    elif rng.random() < 0.2:
        text += "."
    if rng.random() < 0.45:
        text = text[0].upper() + text[1:]
    if rng.random() < TYPO:
        text = slip(rng, text)
    return text


def command(rng, action, target):
    verb = pick(rng, VERBS[action])
    return verb.replace("{x}", target) if "{x}" in verb else f"{verb} {target}"


def request(rng):
    """One simulated request: {"text", "windows", "expected", "kind"}. `expected` is (action, target) when a
    careful assistant would just do it, None when it should look or think first."""
    windows = world(rng)
    unique, twins = unique_apps(windows), twin_apps(windows)
    closed = [name for name in KNOWN_TO_OPEN + NOT_KNOWN_TO_OPEN
              if not any(name.lower() in window["app"].lower() for window in windows)]
    kinds = [(18, "open app"), (8, "open site")]
    if unique:
        kinds += [(30, "named window"), (4, "open what is open")]
    if windows:
        kinds += [(14, "this window")]
        kinds += [(3, kind) for kind in THINK if kind != "which one"]
    else:
        kinds += [(3, kind) for kind in ("open file", "how to", "start a thing", "everything")]
    if twins:
        kinds += [(6, "which one")]
    kind = pick(rng, kinds)
    expected = None
    if kind == "named window":
        action, app = rng.choice(("close", "minimize", "maximize", "focus")), rng.choice(unique)
        core = command(rng, action, pick(rng, NAMED).replace("{x}", spoken(rng, app)))
        expected = (action, app)
    elif kind == "this window":
        action = rng.choice(("close", "minimize", "maximize"))
        core = command(rng, action, pick(rng, THIS_WORDS))
        expected = (action, windows[0]["app"])
    elif kind == "open app":
        name = rng.choice(closed).lower()
        core = command(rng, "open", rng.choice([name, name, name, f"the {name}", f"{name} app", f"the {name} app"]))
        expected = ("open", name)
    elif kind == "open site":
        site = rng.choice(SITES)
        core = command(rng, "open", site) if rng.random() < 0.8 else f"go to {site}"
        expected = ("open", site)
    elif kind == "open what is open":
        app = rng.choice(unique)
        core = f"{pick(rng, OPEN_VERBS)} {spoken(rng, app)}"
        expected = ("focus", app) if rng.random() < ALREADY_OPEN else ("open", name_of(app).lower())
    else:
        apps = twins if kind == "which one" else [window["app"] for window in windows] or ["Notepad"]
        core = pick(rng, THINK[kind]).replace("{x}", spoken(rng, rng.choice(apps)))
        core = core.replace("{y}", rng.choice(closed).lower()).replace("{file}", rng.choice(FILES))
        core = core.replace("{this}", pick(rng, TRAP_THIS))
    return {"text": dress(rng, core), "windows": windows, "expected": list(expected) if expected else None,
            "kind": kind}


def requests(count, seed):
    rng = random.Random(seed)
    return [request(rng) for _ in range(count)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    for row in requests(args.requests, args.seed):
        print(json.dumps({"text": row["text"], "expected": row["expected"], "kind": row["kind"],
                          "windows": [window["app"] for window in row["windows"]]}))


if __name__ == "__main__":
    main()
