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

A confidence here is a share of times the rule was right, (right + 1) / (seen + 2), so it can be checked
against held-out requests: bench/fast_slow.py does that.

Nothing is clicked or typed from here, and System 1 can only do what the slow path could already do:
every action still goes through keyit_actions.run with its own checks and confirm boxes.
"""
import hashlib
import json
import logging
import os
import re
import time
from typing import NamedTuple, Optional

import keyit_actions
import keyit_talk

FAST, SLOW = "fast", "slow"

# How sure System 1 must be before it acts alone, by what a mistake would cost.
STAKES = {"focus": 0.80, "minimize": 0.80, "maximize": 0.80, "open": 0.85, "keys": 0.90, "close": 0.95}
WINDOW_ACTIONS = ("close", "minimize", "maximize", "focus")

# (right, seen) on the DEV list of bench/fast_slow.py, per rule. Few examples = less confidence.
PRIORS = {"window": (22, 22), "this": (6, 6), "app": (14, 14), "site": (8, 8)}

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
FILLER = {"the", "my", "window", "app", "please", "now"}
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


def named_window(argument, windows):
    """The one open window whose app the words name, or None. Titles are not trusted: "close word" must
    not close the browser because one of its tabs is called Word."""
    wanted = " ".join(word for word in argument.lower().split() if word not in FILLER)
    alias = keyit_actions.APP_ALIASES.get(wanted)
    named = [w for w in windows if wanted in (w["app"].lower(), w["process"]) or (alias and w["process"] == alias)]
    return named[0] if len(named) == 1 else None


def phrase_key(question):
    """A habit is filed under a scrambled form of the phrase: what you said is not kept on disk."""
    said = " ".join(keyit_talk.words(question))
    return "habit:" + hashlib.sha256(said.encode("utf-8")).hexdigest()[:16]


class Minds:
    """System 1 for requests, with the counts that say how far it can be trusted."""

    def __init__(self, path=None, clock=time.monotonic):
        self.path = path
        self.clock = clock
        self.counts = {}             # rule or habit key -> {"right", "wrong"[, "action", "argument"]}
        self.pending = None          # (key, action, argument, at): the last action, waiting for a "no"
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    self.counts = dict(json.load(handle)["counts"])
            except Exception:
                logging.warning("Could not read %s; starting without habits", path, exc_info=True)

    # -- how sure
    def sure(self, rule):
        right, seen = PRIORS.get(rule, (0, 0))
        mine = self.counts.get(rule, {})
        return confidence(right + mine.get("right", 0), seen + mine.get("right", 0) + mine.get("wrong", 0))

    # -- System 1
    def decide(self, question, windows, selection=""):
        """FAST with the action System 1 will take, or SLOW with the reason it stands down."""
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
        self.pending = (phrase_key(question), action, argument, self.clock())

    def heard(self, question):
        """The next request arrived: settle the last action as right, or wrong if this is a "no"."""
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
