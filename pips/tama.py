"""
tama.py - Tamagotchi-style care for Pips (v2.18).

What we took from Tamagotchi, and how it was made kinder for a work desktop:

  * Boy or girl  - every pet can be a boy or a girl (or keep it a secret); it shows on the pet
                   (lashes for girls, a little hair tuft for boys) and next to its name.
  * Growing up   - baby -> child -> teen -> adult. A Tamagotchi grows with the clock; a Pips pet
                   grows with *care*: focus sessions, opening Pips each day, games and cuddles.
                   No care mistakes, no death, no bad evolutions.
  * Sick days    - a Tamagotchi gets sick when you neglect it. A Pips pet catches a little cold
                   when *you* work 2.5 hours without a 5-minute break. A short break (or medicine)
                   makes it better - so caring for it means caring for yourself.
  * Left or Right? - the classic Tamagotchi guessing game: 5 rounds, guess 3 right to win.

Pure logic (no Tk) so everything here is easy to test; pet.py draws and talks.
"""

from __future__ import annotations

import random
import time
from datetime import date

# ---------------------------------------------------------------- gender
GENDERS = ("boy", "girl")
GENDER_SYMBOL = {"boy": "\u2642", "girl": "\u2640"}       # ♂ ♀
GENDER_COLOR = {"boy": "#3b82f6", "girl": "#ec4899"}
OWNER_GENDERS = ("m", "f")                                 # the person (profile), "" = not shown
OWNER_TO_GENDER = {"m": "boy", "f": "girl"}


def clean_gender(value) -> str:
    return value if value in GENDERS else ""


def clean_owner_gender(value) -> str:
    return value if value in OWNER_GENDERS else ""


def gender_symbol(value) -> str:
    """♂ / ♀ for "boy"/"m" and "girl"/"f", else ""."""
    value = OWNER_TO_GENDER.get(value, value)
    return GENDER_SYMBOL.get(value, "")


def gender_color(value) -> str:
    value = OWNER_TO_GENDER.get(value, value)
    return GENDER_COLOR.get(value, "#888888")


# ---------------------------------------------------------------- growing up
STAGES = ("baby", "child", "teen", "adult")
STAGE_POINTS = {"baby": 0.0, "child": 2.0, "teen": 6.0, "adult": 12.0}    # growth points needed
STAGE_SCALE = {"baby": 0.72, "child": 0.82, "teen": 0.92, "adult": 1.0}   # body size
STAGE_EYES = {"baby": 1.28, "child": 1.15, "teen": 1.06, "adult": 1.0}    # little ones have big eyes
STAGE_ICON = {"baby": "\U0001F37C", "child": "\U0001F331", "teen": "\U0001F33F", "adult": "\u2B50"}

GROWTH_FOR = {"pomodoro": 1.0, "day": 1.0, "game": 0.5, "care": 0.2}
DAILY_CAP = {"game": 1.5, "care": 1.0}     # at most this much per day from games / snacks & cuddles


def clean_stage(value) -> str:
    return value if value in STAGES else "adult"


def stage_for(points: float) -> str:
    stage = STAGES[0]
    for name in STAGES:
        if points >= STAGE_POINTS[name]:
            stage = name
    return stage


def stage_scale(stage) -> float:
    return STAGE_SCALE.get(stage, 1.0)


def stage_eyes(stage) -> float:
    return STAGE_EYES.get(stage, 1.0)


class Growth:
    """Growth points live in the pet's state dict (saved in data/pet_state.json)."""

    def __init__(self, state: dict, first_run: bool):
        self.st = state
        if "stage" not in state:
            if first_run:
                state["growth"] = 0.0
                state["stage"] = STAGES[0]
            else:
                # pets from before v2.18 are grown up already - they are asked once whether they
                # would like to grow up again (see DesktopPet._offer_regrow)
                state["growth"] = STAGE_POINTS["adult"]
                state["stage"] = "adult"
                state["regrow_offer"] = True
        state["stage"] = clean_stage(state.get("stage"))
        try:
            state["growth"] = max(0.0, float(state.get("growth", 0.0)))
        except (TypeError, ValueError):
            state["growth"] = 0.0

    @property
    def stage(self) -> str:
        return self.st["stage"]

    @property
    def points(self) -> float:
        return float(self.st.get("growth", 0.0))

    @property
    def grown(self) -> bool:
        return self.stage == STAGES[-1]

    def progress(self):
        """(next stage or None, points still needed, fraction 0..1 of the current step)."""
        index = STAGES.index(self.stage)
        if index + 1 >= len(STAGES):
            return None, 0.0, 1.0
        nxt = STAGES[index + 1]
        low = STAGE_POINTS[self.stage]
        high = STAGE_POINTS[nxt]
        fraction = (self.points - low) / (high - low) if high > low else 1.0
        return nxt, max(0.0, high - self.points), max(0.0, min(1.0, fraction))

    def add(self, reason: str, today: str | None = None) -> str | None:
        """Growth for a bit of care. Returns the new stage when the pet just grew up."""
        if self.grown:
            return None
        today = today or date.today().isoformat()
        amount = GROWTH_FOR.get(reason, 0.0)
        if reason == "day":
            if self.st.get("growth_day") == today:
                return None
            self.st["growth_day"] = today
        cap = DAILY_CAP.get(reason)
        if cap is not None:
            key = "growth_" + reason
            day, used = (self.st.get(key) or [None, 0.0])[:2]
            if day != today:
                used = 0.0
            amount = min(amount, max(0.0, cap - float(used)))
            self.st[key] = [today, float(used) + amount]
        if amount <= 0:
            return None
        before = self.stage
        self.st["growth"] = round(self.points + amount, 3)
        after = stage_for(self.points)
        if STAGES.index(after) > STAGES.index(before):
            self.st["stage"] = after
            return after
        return None

    def restart(self):
        """Grow up again from a baby (the user asked for it)."""
        self.st["growth"] = 0.0
        self.st["stage"] = STAGES[0]
        self.st.pop("regrow_offer", None)


# ---------------------------------------------------------------- sick days
SICK_AFTER_S = 150 * 60     # working this long without a 5-minute break -> a little cold
BREAK_S = 5 * 60            # this long without mouse / keyboard = a real break
REST_CURES_S = 30 * 60      # Pips closed (or the PC off) this long also cures it
SNEEZE_EVERY_S = (70, 160)


class Health:
    """Catching (and curing) a little cold. The cold itself is kept in the pet's state dict."""

    def __init__(self, state: dict):
        self.st = state
        self.work_since = None      # wall time when the current stretch of work began (not saved)
        self.recovered = ""         # how the last cold was cured: "break" / "medicine" / "rest" / "off"

    @property
    def sick(self) -> bool:
        return bool(self.st.get("sick_since"))

    def worked_minutes(self, now: float | None = None) -> float:
        if self.work_since is None:
            return 0.0
        return max(0.0, ((now or time.time()) - self.work_since) / 60.0)

    def tick(self, now: float, idle_s: float, enabled: bool = True) -> str | None:
        """Call every few seconds with the seconds since the last mouse / keyboard input.
        Returns "sick" when the pet just caught a cold, "cured_break" when a break cured it."""
        if idle_s >= BREAK_S:
            self.work_since = None
            if self.sick:
                self._cure("break")
                return "cured_break"
            return None
        if self.work_since is None:
            self.work_since = now - max(0.0, idle_s)
            return None
        if not enabled:
            if self.sick:
                self._cure("off")
            return None
        if not self.sick and now - self.work_since >= SICK_AFTER_S:
            self.st["sick_since"] = now
            self.st["colds"] = int(self.st.get("colds", 0) or 0) + 1
            return "sick"
        return None

    def rested(self, offline_s: float) -> bool:
        """Pips was closed / the PC was off for a while: a good rest cures a cold."""
        if self.sick and offline_s >= REST_CURES_S:
            self._cure("rest")
            return True
        return False

    def cure(self, how: str = "medicine", now: float | None = None) -> bool:
        if not self.sick:
            return False
        self._cure(how)
        if how == "medicine":
            self.work_since = now or time.time()     # medicine helps, but the work stretch starts over
        return True

    def _cure(self, how: str):
        self.st.pop("sick_since", None)
        self.recovered = how


# ---------------------------------------------------------------- Left or Right?
GAME_ROUNDS = 5
GAME_WIN_AT = 3
GAME_REWARDS_PER_DAY = 3
GAME_COINS = 5


class LeftRight:
    """The classic Tamagotchi guessing game: which way will the pet look?"""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.round = 0
        self.score = 0
        self.history = []

    @property
    def done(self) -> bool:
        return self.round >= GAME_ROUNDS

    @property
    def won(self) -> bool:
        return self.score >= GAME_WIN_AT

    def guess(self, side: int):
        """side: -1 = left, 1 = right. Returns (correct, the side the pet looked)."""
        if self.done:
            raise RuntimeError("the game is over")
        side = -1 if side < 0 else 1
        pet_side = self.rng.choice((-1, 1))
        correct = pet_side == side
        self.round += 1
        if correct:
            self.score += 1
        self.history.append((side, pet_side))
        return correct, pet_side


def game_reward(state: dict, won: bool, today: str | None = None) -> int:
    """Coins for a won game (the first few wins per day)."""
    if not won:
        return 0
    today = today or date.today().isoformat()
    day, count = (state.get("game_rewards") or [None, 0])[:2]
    if day != today:
        count = 0
    if count >= GAME_REWARDS_PER_DAY:
        return 0
    state["game_rewards"] = [today, int(count) + 1]
    return GAME_COINS
