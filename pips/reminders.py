"""Reminder scheduling and rotating health tips for the Pips.

Pure logic only: the scheduler receives the current monotonic time as an
argument, so it can be tested without sleeping. `app.py` owns the popup UI,
the timers and the chime.

    BREAK_TIPS / HYDRATION_TIPS / EYE_TIPS  -> tuple[str, ...]
    DEFAULTS                                -> dict
    load_settings(cfg) -> dict
    ReminderScheduler(settings):
        begin_work(now)
        break_reminder() -> dict | None
        due(now, working: bool) -> dict | None
"""

from __future__ import annotations

BREAK_TIPS = (
    "Stand up and walk around for a couple of minutes.",
    "Stretch your arms, neck and back.",
    "Drink a full glass of water.",
    "Rest your eyes and look out of a window for 20 seconds.",
    "Roll your shoulders slowly ten times.",
    "Take ten slow, deep breaths away from the screen.",
    "Refill your water bottle.",
    "Do a quick calf and hamstring stretch.",
)

HYDRATION_TIPS = (
    "Time to drink some water.",
    "Take a sip - hydration keeps your focus sharp.",
    "Grab a glass of water and stretch your legs.",
)

EYE_TIPS = (
    "20-20-20: look 20 feet away for 20 seconds.",
    "Rest your eyes - focus on something far away for 20 seconds.",
    "Blink slowly and gaze into the distance for 20 seconds.",
)

DEFAULTS = {
    "enabled": True,
    "chime": True,
    "break_popup": True,
    "eye_enabled": True,
    "eye_every_minutes": 20,
    "hydration_enabled": True,
    "hydration_every_minutes": 40,
    "break_tips": list(BREAK_TIPS),
    "hydration_tips": list(HYDRATION_TIPS),
    "eye_tips": list(EYE_TIPS),
}


def load_settings(cfg):
    """Merge the ``reminders`` block of ``cfg`` over the built-in defaults."""
    settings = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    try:
        block = dict((cfg or {}).get("reminders", {}) or {})
    except Exception:
        block = {}
    for key, value in block.items():
        if key in settings and value is not None:
            settings[key] = value
    return settings


class ReminderScheduler:
    """Decide which health nudge is due, and rotate through the tip lists."""

    def __init__(self, settings):
        self.s = dict(settings or DEFAULTS)
        self._index = {}
        self._last = {}

    def enabled(self):
        return bool(self.s.get("enabled", True))

    def begin_work(self, now):
        """Anchor the recurring nudges to the start of a work session."""
        self._last["eye"] = now
        self._last["hydration"] = now

    def break_reminder(self):
        """Return the stand-up/water reminder for a finished work session."""
        if not self.s.get("break_popup", True):
            return None
        return {
            "kind": "break",
            "title": "Break time",
            "tip": self._next("break", self.s.get("break_tips") or list(BREAK_TIPS)),
        }

    def due(self, now, working):
        """Return an eye-rest or hydration reminder if one is due, else None."""
        if not self.enabled() or not working:
            return None
        eye = self._due_kind(now, "eye", "eye_enabled", "eye_every_minutes", 20,
                             "eye_tips", list(EYE_TIPS), "Eye rest (20-20-20)")
        if eye is not None:
            return eye
        return self._due_kind(now, "hydration", "hydration_enabled",
                              "hydration_every_minutes", 40, "hydration_tips",
                              list(HYDRATION_TIPS), "Drink water")

    def _due_kind(self, now, key, enabled_key, every_key, every_default,
                  tips_key, tips_default, title):
        if not self.s.get(enabled_key, True):
            return None
        try:
            interval = max(1, int(self.s.get(every_key, every_default))) * 60
        except Exception:
            interval = every_default * 60
        last = self._last.setdefault(key, now)
        if now - last < interval:
            return None
        self._last[key] = now
        return {
            "kind": key,
            "title": title,
            "tip": self._next(key, self.s.get(tips_key) or tips_default),
        }

    def _next(self, key, tips):
        tips = list(tips) or [""]
        i = self._index.get(key, 0) % len(tips)
        self._index[key] = i + 1
        return tips[i]


if __name__ == "__main__":
    s = ReminderScheduler(load_settings({}))
    t = 1000.0
    s.begin_work(t)
    assert s.due(t + 5, True) is None, "nothing due right after work starts"
    assert s.due(t + 20 * 60, True)["kind"] == "eye", "eye rest due at 20 min"
    assert s.due(t + 30 * 60, True) is None, "nothing due while working quietly"
    br = s.break_reminder()
    assert br and br["kind"] == "break"
    assert s.due(t + 99, False) is None, "no nudges when not working"
    tips = {s.break_reminder()["tip"] for _ in range(len(BREAK_TIPS))}
    assert len(tips) == len(BREAK_TIPS), "break tips rotate through all entries"
    print("reminders smoke ok:", br["title"], "|", br["tip"])
