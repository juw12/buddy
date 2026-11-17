"""Two minds, after Daniel Kahneman's "Thinking, Fast and Slow".

System 1 (this file) reads a request in well under a millisecond and does it at once when it is sure
enough. System 2 (the brain: a vision model that looks at the screen) takes seconds and gets everything
else. Three things decide who answers:

1. Strain. System 1 stands down when a request is not routine: a question, two steps, a "don't", an "if",
   a window it cannot see. In the book, System 2 is called in when System 1 has no ready answer.
2. Stakes. A wrong action costs more than a slow answer, so the more an action can lose, the surer
   System 1 has to be: switching windows needs 0.80, closing one 0.95.
3. Practice with feedback. What the brain does for the same short phrase again and again becomes a habit
   that System 1 does itself. "No" right after a fast action counts against the rule that fired.

How sure System 1 is of a reading comes from minds_model.json: a small model trained on simulated use
(bench/simulate.py, bench/sim_users.py) and checked on requests the simulator did not write
(bench/fast_calibration.py). It says how often a reading worded like this one turned out right, so "0.95"
can be counted. The file also lists the wordings System 1 knows; without the file Buddy falls back to the
counts of Buddy 1.5.0. A habit's confidence is still its own count, (right + 1) / (seen + 2).

Nothing is clicked or typed from here, and System 1 can only do what the slow path could already do:
every action still goes through keyit_actions.run with its own checks and confirm boxes.
"""
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from typing import NamedTuple, Optional

import keyit_actions
import keyit_talk

FAST, SLOW = "fast", "slow"

# How sure System 1 must be before it acts alone, by what a mistake would cost.
STAKES = {"focus": 0.80, "minimize": 0.80, "maximize": 0.80, "open": 0.85, "keys": 0.90, "close": 0.95}
WINDOW_ACTIONS = ("close", "minimize", "maximize", "focus")

# (right, seen) on the DEV list of bench/fast_slow.py, per rule. Few examples = less confidence.
# Used when there is no trained model to ask (Buddy 1.5.0 worked this way).
PRIORS = {"window": (22, 22), "this": (6, 6), "app": (14, 14), "site": (8, 8)}
MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "minds_model.json")
SHIPPED = object()               # Minds(model=SHIPPED): the model file that came with Buddy
NEVER_CERTAIN = 0.995            # no reading is ever stated as surer than this
TRUST_AFTER = 20                 # a first "no" counts as one miss in this many fast actions

# Signs that a request is not routine. Any one hands it to System 2.
STRAIN = (
    ("two steps", re.compile(r"\b(and|then|also|plus)\b|[,;]", re.I)),
    ("a negation", re.compile(r"\b(don'?t|do not|not|never|no|without|except|but)\b", re.I)),
    ("a condition", re.compile(r"\b(if|when|after|before|unless|until|once|later|while|tomorrow)\b", re.I)),
    ("more than one thing", re.compile(r"\b(all|every|everything|both|each|any|other|others|else|or)\b", re.I)),
    ("a doubt", re.compile(r"\b(maybe|might|should|could|would|can|wonder|guess)\b|\?", re.I)),
)
MAX_WORDS = 8
THIS = {"this", "this window", "this app", "current window", "the current window"}
WINDOW_ONLY = {"window", "the window", "active window", "the active window"}     # "close window": the one in front
FILLER = {"the", "my", "window", "app", "please", "now"}
MORE_FILLER = FILLER | {"application", "program"}
SPELLING = {"minimise": "minimize", "maximise": "maximize"}
INSIDE_TOO = {"settings"}        # an app of its own, and also a page inside nearly every other app
# Words around a command that do not change it. System 1 takes them off before it reads; the model still
# gets told they were there, because "can you close this?" is not quite as sure a thing as "close this".
OPENERS = ("hey buddy", "ok buddy", "okay buddy", "buddy", "hey", "hi", "ok", "okay", "yo", "um", "uh", "so", "well",
           "alright", "now", "just", "please", "pls", "kindly")
FRAMES = ("can you", "could you", "would you", "will you", "can u", "could u", "i want you to", "i need you to",
          "i want to", "i'd like to", "i would like to", "i need to", "i wanna", "let's", "lets", "go ahead and")
CLOSERS = ("please", "pls", "plz", "for me", "right now", "now", "thanks", "thank you", "thx", "real quick", "quickly",
           "for now")
HEAD = re.compile(r"^(?:(?P<frame>%s)|(?P<opener>%s))(?![\w'])[\s,]*" % (
    "|".join(re.escape(words) for words in FRAMES), "|".join(re.escape(words) for words in OPENERS)), re.I)
TAIL = re.compile(r"[\s,]*(?<![\w'])(?:%s)$" % "|".join(re.escape(words) for words in CLOSERS), re.I)
SITE = re.compile(r"^(https?://|www\.)\S+$|^[\w-]+(\.[\w-]+)*\.(com|org|net|io|ai|dev|gov|edu)$", re.I)
CORRECTIONS = re.compile(r"^(no|nope|not that|wrong|undo|that'?s wrong|that is wrong|bukan|salah|不是|不對|不对)\b", re.I)
FEEDBACK_SECONDS = 30.0          # a "no" this soon after an action is about that action
MAX_HABITS = 200
MAX_HABIT_WORDS = 6


class Decision(NamedTuple):
    mind: str                        # FAST: System 1 does it now. SLOW: ask the brain.
    why: str                         # for the log and the status line
    action: Optional[tuple] = None   # (action, argument) when FAST
    confidence: float = 0.0          # System 1's probability that this reading is right
    rule: str = ""                   # which rule or habit fired (the key its feedback is counted under)
    window: Optional[dict] = None    # the window it means, for window actions
    noticed: tuple = ()              # what the trained model was told about this reading


GAP = " ... "


def wording(words):
    return re.escape(words).replace(r"\ ", r"\s+")


class Model:
    """What training left behind: the wordings System 1 knows, and a weight for everything it notices
    about a reading. probability() is a logistic regression small enough to do by hand."""

    def __init__(self, data):
        self.verbs = {str(verb): str(action) for verb, action in data["verbs"].items()}
        self.weights = {str(name): float(weight) for name, weight in data["weights"].items()}
        self.typical = {str(rule): float(value) for rule, value in data["typical"].items()}
        if not self.verbs or "bias" not in self.weights or set(self.verbs.values()) - set(STAKES):
            raise ValueError("not a model for System 1")
        spelled = sorted((verb for verb in self.verbs if GAP not in verb), key=len, reverse=True)
        spelled += [other for other, usual in SPELLING.items() if usual in self.verbs]
        self.reads = re.compile(r"^(%s)\s+(\S.*)$" % "|".join(wording(verb) for verb in spelled), re.I)
        # "make ... full screen": the target sits in the middle
        self.around = [(verb, re.compile(r"^%s\s+(\S.*?)\s+%s$" % tuple(wording(part) for part in verb.split(GAP)), re.I))
                       for verb in sorted(self.verbs, key=len, reverse=True) if GAP in verb]

    def read(self, command):
        """(verb, what it is said about) for a wording on the list, or None."""
        for verb, pattern in self.around:
            match = pattern.match(command)
            if match:
                return verb, " ".join(match.group(1).split())
        match = self.reads.match(command)
        if not match:
            return None
        verb = " ".join(match.group(1).lower().split())
        return SPELLING.get(verb, verb), " ".join(match.group(2).split())

    def knows(self, noticed):
        """Has training seen this kind of reading at all? What it never saw, System 1 does not guess at."""
        return all(name in self.weights for name in noticed[:2])

    def probability(self, noticed):
        total = self.weights["bias"] + sum(self.weights.get(name, 0.0) for name in noticed)
        return min(NEVER_CERTAIN, 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, total)))))


def load_model(path=MODEL_FILE):
    """The trained model, or None: then System 1 works from the counts in PRIORS, as Buddy 1.5.0 did."""
    try:
        with open(path, encoding="utf-8") as handle:
            return Model(json.load(handle))
    except Exception:
        logging.warning("No usable %s; System 1 falls back to its built-in counts", path, exc_info=True)
        return None


def plain(question):
    """(the command without the words around it, which kinds of words were taken off).
    "Hey Buddy, could you close Notepad for me?" -> ("close Notepad", {"opener", "frame", "asked", "closer"}).
    A "?" only comes off after a "can you": "close excel?" stays a doubt."""
    text, found = question.strip(), set()
    while True:
        match = HEAD.match(text)
        if not match or match.end() >= len(text):
            break
        found.add("frame" if match.group("frame") else "opener")
        text = text[match.end():]
    while True:
        text = text.rstrip(" .!")
        if text.endswith("?") and "frame" in found:
            found.add("asked")
            text = text[:-1]
            continue
        match = TAIL.search(text)
        if not match or match.start() == 0:
            break
        found.add("closer")
        text = text[:match.start()]
    return text.rstrip(" ,"), found


def confidence(right, seen):
    """Share of times right, pulled towards 0.5 while there are few examples (Laplace)."""
    return (right + 1) / (seen + 2)


def strain(question, selection=""):
    """Why this request needs System 2, or "" when it reads as routine."""
    if selection:
        return "text is highlighted"
    if not keyit_actions.is_request(question):
        return "a question"
    for name, pattern in STRAIN:
        if pattern.search(question):
            return name
    if len(keyit_talk.words(question)) > MAX_WORDS:
        return "a long request"
    return ""


def named_window(argument, windows, filler=FILLER):
    """The one open window whose app the words name, or None. Titles are not trusted: "close word" must
    not close the browser because one of its tabs is called Word."""
    wanted = " ".join(word for word in argument.lower().split() if word not in filler)
    alias = keyit_actions.APP_ALIASES.get(wanted)
    named = [w for w in windows if wanted in (w["app"].lower(), w["process"]) or (alias and w["process"] == alias)]
    return named[0] if len(named) == 1 else None


def short_named_window(argument, windows):
    """The one open window whose app name ends in these words: "teams" for Microsoft Teams, "chrome" for
    Google Chrome. Only tried when no app has exactly this name."""
    wanted = [word for word in argument.lower().split() if word not in MORE_FILLER]
    named = [w for w in windows if wanted and w["app"].lower().split()[-len(wanted):] == wanted]
    return named[0] if len(named) == 1 else None


_shipped = []


def shipped_model():
    """The model file next to this module, read once."""
    if not _shipped:
        _shipped.append(load_model())
    return _shipped[0]


def phrase_key(question):
    """A habit is filed under a scrambled form of the phrase: what you said is not kept on disk."""
    said = " ".join(keyit_talk.words(question))
    return "habit:" + hashlib.sha256(said.encode("utf-8")).hexdigest()[:16]


class Minds:
    """System 1 for requests, with the counts that say how far it can be trusted."""

    def __init__(self, path=None, clock=time.monotonic, model=SHIPPED):
        self.path = path
        self.clock = clock
        self.model = shipped_model() if model is SHIPPED else model       # None: the counts of Buddy 1.5.0
        self.counts = {}             # rule or habit key -> {"right", "wrong"[, "action", "argument"]}
        self.pending = None          # (key, action, argument, at): the last action, waiting for a "no"
        self.lock = threading.RLock()        # typed requests and talk mode's "no" come from different threads
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    self.counts = dict(json.load(handle)["counts"])
            except Exception:
                logging.warning("Could not read %s; starting without habits", path, exc_info=True)

    # -- how sure
    def sure(self, rule):
        """How sure a built-in rule or a habit is, before looking at one request's wording."""
        if self.model and rule in self.model.typical:
            return self.model.typical[rule] * self.trust(rule)
        right, seen = PRIORS.get(rule, (0, 0))
        mine = self.counts.get(rule, {})
        return confidence(right + mine.get("right", 0), seen + mine.get("right", 0) + mine.get("wrong", 0))

    def trust(self, rule):
        """What this user's own "no"s leave of the trained confidence: 1.0 until the first one, never more."""
        mine = self.counts.get(rule, {})
        went_well = TRUST_AFTER + mine.get("right", 0)
        return went_well / (went_well + mine.get("wrong", 0))

    # -- System 1
    def decide(self, question, windows, selection=""):
        """FAST with the action System 1 will take, or SLOW with the reason it stands down."""
        habit = self.counts.get(phrase_key(question))
        if self.model is None or (habit and habit.get("action") in STAKES):
            return self._decide_by_counts(question, windows, selection)
        command, found = plain(question)
        why = strain(command, selection)
        if why:
            return Decision(SLOW, why)
        rule, action, window, noticed = self._read_trained(command, found, windows)
        if rule is None:
            return Decision(SLOW, action or "nothing I know by heart")
        sure, bar = self.model.probability(noticed) * self.trust(rule), STAKES[action[0]]
        if sure < bar:
            return Decision(SLOW, f"not sure enough to {action[0]} alone ({sure:.2f} < {bar:.2f})", action, sure, rule,
                            window, tuple(noticed))
        return Decision(FAST, rule, action, sure, rule, window, tuple(noticed))

    def _read_trained(self, command, found, windows):
        """(rule, (action, argument), window, what the model should notice), or (None, why not, None, None)."""
        read = self.model.read(command)
        if not read:
            return None, "", None, None
        verb, argument = read
        action = self.model.verbs[verb]
        target, window, extra = argument.lower(), None, []
        if action in WINDOW_ACTIONS:
            if target in THIS or target in WINDOW_ONLY:        # not "it" or "that": those need a look
                if not windows:
                    return None, "no window open", None, None
                rule, action, window = "this", (action, "this"), windows[0]
                extra.append("says window" if "window" in target else "says app" if "app" in target else "says this")
            else:
                window = named_window(argument, windows)
                if window is None:
                    window = short_named_window(argument, windows)
                    extra.append("short name")
                if window is None and action == "focus" and SITE.match(target):
                    rule, action, extra = "site", ("open", target), []       # "go to github.com"
                elif window is None:
                    return None, "I can't tell which window", None, None
                else:
                    rule, action = "window", (action, argument)
                    if set(target.split()) & MORE_FILLER:
                        extra.append("filler")
        else:
            words = [word for word in target.split() if word not in ("the", "my")]
            if len(words) > 1 and words[-1] in ("app", "application", "program"):
                words = words[:-1]
            name = " ".join(words)
            if name != target:
                extra.append("filler")
            if name in keyit_actions.APP_ALIASES:
                rule = "app"
                if named_window(name, windows) or short_named_window(name, windows):
                    extra.append("already open")
                if name in INSIDE_TOO:
                    extra.append("inside apps too")
            elif SITE.match(name):
                rule = "site"
            else:
                return None, "I don't know that app by heart", None, None
            action = (action, name)
        noticed = [f"{rule}/{action[0]}", f"verb={verb}"] + extra + sorted(found)
        if not self.model.knows(noticed):
            return None, "", None, None
        return rule, action, window, noticed

    def _decide_by_counts(self, question, windows, selection=""):
        """System 1 as it was in Buddy 1.5.0: used for habits, and for everything when there is no model."""
        why = strain(question, selection)
        if why:
            return Decision(SLOW, why)
        rule, action, window = self._read(question, windows)
        if rule is None:
            return Decision(SLOW, action or "nothing I know by heart")
        sure, bar = self.sure(rule), STAKES[action[0]]
        if sure < bar:
            return Decision(SLOW, f"not sure enough to {action[0]} alone ({sure:.2f} < {bar:.2f})", action, sure, rule, window)
        return Decision(FAST, rule.split(":")[0], action, sure, rule, window)

    def _read(self, question, windows):
        """(rule, (action, argument), window), or (None, why not, None)."""
        habit = self.counts.get(phrase_key(question))
        direct = keyit_actions.direct_command(question)
        if habit and habit.get("action") in STAKES:
            rule, action, argument = phrase_key(question), habit["action"], habit["argument"]
        elif direct and direct[0] in STAKES:
            rule, (action, argument) = None, direct
        else:
            return None, "", None
        if action in WINDOW_ACTIONS:
            if argument.lower() in THIS:
                return (rule or "this", (action, "this"), windows[0]) if windows else (None, "no window open", None)
            window = named_window(argument, windows)
            if window is None:
                return None, "I can't tell which window", None
            return rule or "window", (action, argument), window
        if action == "open" and rule is None:
            target = argument.lower()
            if target in keyit_actions.APP_ALIASES:
                return "app", (action, target), None
            if SITE.match(target):
                return "site", (action, target), None
            return None, "I don't know that app by heart", None
        return rule, (action, argument), None

    # -- practice and feedback
    def acted(self, decision):
        """System 1 just did this: the next thing the user says tells whether it was right."""
        with self.lock:
            self.pending = (decision.rule, None, None, self.clock())

    def learn(self, question, actions):
        """System 2 just did `actions` for this request. One routine step may become a habit."""
        if len(actions) != 1 or strain(question) or len(keyit_talk.words(question)) > MAX_HABIT_WORDS:
            return
        action, argument = actions[0]
        argument = " ".join(str(argument).lower().split())
        if action not in STAKES or (action == "keys" and argument.replace(" ", "") in keyit_actions.RISKY_KEYS):
            return
        if action != "keys" and argument not in THIS and not set(keyit_talk.words(argument)) <= set(
                keyit_talk.words(question)):
            return                     # the brain read the target off the screen: not something to repeat blind
        with self.lock:
            self.pending = (phrase_key(question), action, argument, self.clock())

    def heard(self, question):
        """The next request arrived: settle the last action as right, or wrong if this is a "no"."""
        with self.lock:
            self._settle(question)

    def _settle(self, question):
        pending, self.pending = self.pending, None
        if pending is None:
            return
        key, action, argument, at = pending
        wrong = bool(CORRECTIONS.match(question.strip())) and self.clock() - at <= FEEDBACK_SECONDS
        entry = self.counts.get(key)
        if action is not None and entry is None:        # the brain did this for the phrase: a new habit
            if wrong:
                return
            entry = self.counts[key] = {"right": 0, "wrong": 0, "action": action, "argument": argument}
        elif action is not None and (entry.get("action"), entry.get("argument")) != (action, argument):
            wrong = True                                # the brain did something else this time
        elif entry is None:
            entry = self.counts[key] = {"right": 0, "wrong": 0}
        entry["wrong" if wrong else "right"] += 1
        self._save()

    def _save(self):
        if not self.path:
            return
        habits = [key for key in self.counts if key.startswith("habit:")]
        for key in sorted(habits, key=lambda k: self.counts[k]["right"])[:max(0, len(habits) - MAX_HABITS)]:
            del self.counts[key]
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "counts": self.counts}, handle)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            logging.warning("Could not save %s", self.path, exc_info=True)
