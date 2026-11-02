"""
minigames.py - quick arcade games to play with your pet (v2.24).

    Treat Catch  - move your pet left / right and catch falling treats, dodge the rocks
    Bonk the Bugs - whack the bugs that pop out of the holes (leave the flowers alone!)
    Memory Match - flip cards and find the 8 pairs with as few misses as possible
    Pet Says     - repeat the growing colour + sound pattern (Simon)
    Hop Hop      - your pet runs; jump over the obstacles

v2.25: every game can also be played with friends (2-8 players, "party" rooms on the Friends server):
everyone gets the same seeded round (same treats, bugs, cards, pattern, obstacles), starts on the same
countdown and sees the others' scores live; the best score wins the round. See PartyGameWindow.

Every game has a small, timing-independent logic class (step(dt) + input methods) so it can be
tested headless, and one GameWindow (tkinter) that draws it. Finishing a game makes the pet happier,
records your best score, and a good score gives a few coins (limited per day).
"""

from __future__ import annotations

import math
import random
import threading
import time
import tkinter as tk
from datetime import date

try:
    from i18n import t
except ImportError:  # pragma: no cover
    def t(key, lang=None, **kwargs):
        return key.format(**kwargs) if kwargs else key

try:
    import pet_shop
except ImportError:  # pragma: no cover
    pet_shop = None

GAMES = ("catch", "bonk", "memory", "simon", "hop")
ICONS = {"catch": "\U0001F36A", "bonk": "\U0001F41E", "memory": "\U0001F0CF", "simon": "\U0001F3B5",
         "hop": "\U0001F3C3"}
GOOD_SCORE = {"catch": 30, "bonk": 22, "memory": 90, "simon": 7, "hop": 60}   # "well played" thresholds
GAME_COINS = 3                     # coins for a good score...
COIN_GAMES_PER_DAY = 5             # ...the first few times per day
CW, CH = 440, 340                  # canvas size

# =============================================================================
# Game logic (no tkinter)
# =============================================================================


class CatchGame:
    """Treats fall from the sky; move the pet to catch them. Rocks cost a life (3 lives, 45 s)."""
    DURATION = 45.0
    PET_Y = CH - 44
    REACH = 40
    ITEMS = {"cookie": 1, "fish": 2, "star": 5, "rock": 0}

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.x = CW / 2
        self.items: list = []
        self.score = 0
        self.lives = 3
        self.time_left = self.DURATION
        self.elapsed = 0.0
        self._spawn_in = 0.4
        self.events: list = []        # (kind, x, y) for the window's pop effects
        self.done = False

    def move_to(self, x):
        self.x = max(30.0, min(CW - 30.0, float(x)))

    def nudge(self, dx):
        self.move_to(self.x + dx)

    def _spawn(self):
        roll = self.rng.random()
        kind = "star" if roll < 0.06 else "rock" if roll < 0.28 else "fish" if roll < 0.5 else "cookie"
        speed = 120 + self.elapsed * 3.2 + self.rng.uniform(-20, 30)
        self.items.append({"kind": kind, "x": self.rng.uniform(24, CW - 24), "y": -12.0, "vy": speed})

    def step(self, dt):
        if self.done:
            return
        self.elapsed += dt
        self.time_left = max(0.0, self.time_left - dt)
        self._spawn_in -= dt
        if self._spawn_in <= 0:
            self._spawn()
            self._spawn_in = max(0.32, 0.85 - self.elapsed * 0.012) * self.rng.uniform(0.7, 1.2)
        keep = []
        for item in self.items:
            item["y"] += item["vy"] * dt
            if self.PET_Y - 30 <= item["y"] <= self.PET_Y + 6 and abs(item["x"] - self.x) < self.REACH:
                if item["kind"] == "rock":
                    self.lives -= 1
                    self.events.append(("ouch", item["x"], item["y"]))
                else:
                    self.score += self.ITEMS[item["kind"]]
                    self.events.append((item["kind"], item["x"], item["y"]))
                continue
            if item["y"] < CH + 20:
                keep.append(item)
        self.items = keep
        if self.time_left <= 0 or self.lives <= 0:
            self.done = True


class BonkGame:
    """3x3 holes. Bonk bugs (+1), golden bees (+3); bonking a flower costs 2. 30 seconds."""
    DURATION = 30.0
    HOLES = 9

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.holes: list = [None] * self.HOLES     # {"kind", "left", "hit"}
        self.score = 0
        self.hits = 0
        self.time_left = self.DURATION
        self.elapsed = 0.0
        self._spawn_in = 0.5
        self.done = False

    def step(self, dt):
        if self.done:
            return
        self.elapsed += dt
        self.time_left = max(0.0, self.time_left - dt)
        for i, hole in enumerate(self.holes):
            if hole:
                hole["left"] -= dt
                if hole["left"] <= 0:
                    self.holes[i] = None
        self._spawn_in -= dt
        if self._spawn_in <= 0:
            # always draw the same 3 numbers per spawn, so friends playing the same seed (v2.25) get the
            # same bugs in the same holes even when someone's board is fuller than another's
            roll = self.rng.random()
            jitter = self.rng.uniform(0.85, 1.15)
            pick = self.rng.randrange(self.HOLES)
            empty = [i for i, h in enumerate(self.holes) if h is None]
            if empty:
                kind = "gold" if roll < 0.1 else "flower" if roll < 0.3 else "bug"
                stay = max(0.55, 1.25 - self.elapsed * 0.022) * jitter
                index = pick if pick in empty else empty[pick % len(empty)]
                self.holes[index] = {"kind": kind, "left": stay, "hit": False}
            self._spawn_in = max(0.28, 0.8 - self.elapsed * 0.016)
        if self.time_left <= 0:
            self.done = True

    def bonk(self, index) -> str | None:
        """Returns what was bonked ('bug', 'gold', 'flower') or None for an empty hole."""
        if self.done or not 0 <= index < self.HOLES:
            return None
        hole = self.holes[index]
        if not hole or hole["hit"]:
            return None
        hole["hit"] = True
        hole["left"] = min(hole["left"], 0.35)
        if hole["kind"] == "flower":
            self.score = max(0, self.score - 2)
        else:
            self.score += 3 if hole["kind"] == "gold" else 1
            self.hits += 1
        return hole["kind"]


class MemoryGame:
    """16 face-down cards, 8 pairs. Score: 150 minus 6 per miss and a little for the time taken."""
    SYMBOLS = ("\U0001F436", "\U0001F431", "\U0001F430", "\U0001F43B", "\U0001F438", "\U0001F98A",
               "\U0001F43C", "\U0001F427")

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.cards = list(self.SYMBOLS) * 2
        self.rng.shuffle(self.cards)
        self.matched = [False] * len(self.cards)
        self.open: list = []          # indexes face up but not matched (max 2)
        self.misses = 0
        self.flips = 0
        self.elapsed = 0.0
        self.done = False

    def step(self, dt):
        if not self.done:
            self.elapsed += dt

    def hide_open(self):
        if len(self.open) == 2:
            self.open = []

    def flip(self, index) -> str | None:
        """'first', 'match', 'miss' or None (ignored click)."""
        if self.done or not 0 <= index < len(self.cards) or self.matched[index] or index in self.open:
            return None
        if len(self.open) == 2:
            self.open = []
        self.open.append(index)
        self.flips += 1
        if len(self.open) == 1:
            return "first"
        a, b = self.open
        if self.cards[a] == self.cards[b]:
            self.matched[a] = self.matched[b] = True
            self.open = []
            if all(self.matched):
                self.done = True
            return "match"
        self.misses += 1
        return "miss"

    @property
    def score(self) -> int:
        pairs = sum(self.matched) // 2
        if not self.done:
            return pairs * 10
        return max(10, 150 - 6 * self.misses - int(self.elapsed) // 2)


class SimonGame:
    """Pet Says: watch the pattern, then repeat it. Each round adds one more step."""
    PADS = 4

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.seq: list = []
        self.pos = 0
        self.done = False
        self.add_step()

    def add_step(self):
        self.seq.append(self.rng.randrange(self.PADS))
        self.pos = 0

    def press(self, pad) -> str | None:
        """'ok' (keep going), 'round' (pattern complete, a new step was added) or 'wrong' (game over)."""
        if self.done or not 0 <= pad < self.PADS:
            return None
        if pad != self.seq[self.pos]:
            self.done = True
            return "wrong"
        self.pos += 1
        if self.pos >= len(self.seq):
            self.add_step()
            return "round"
        return "ok"

    def step(self, dt):
        pass

    @property
    def score(self) -> int:
        return len(self.seq) - 1


class HopGame:
    """The pet runs to the right; jump (space / click) over rocks and cacti. Score = distance."""
    GROUND = CH - 40
    PET_X = 90
    GRAVITY = 1900.0
    JUMP_V = 720.0

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.y = 0.0                  # height above the ground
        self.vy = 0.0
        self.speed = 230.0
        self.distance = 0.0
        self.obstacles: list = []     # {"x", "w", "h", "kind"}
        self._gap = 260.0
        self.done = False

    @property
    def on_ground(self) -> bool:
        return self.y <= 0.0 and self.vy <= 0.0

    def jump(self) -> bool:
        if self.done or not self.on_ground:
            return False
        self.vy = self.JUMP_V
        return True

    def step(self, dt):
        if self.done:
            return
        self.speed = min(560.0, 230.0 + self.distance * 0.045)
        self.distance += self.speed * dt
        self.vy -= self.GRAVITY * dt
        self.y = max(0.0, self.y + self.vy * dt)
        if self.y == 0.0 and self.vy < 0:
            self.vy = 0.0
        for ob in self.obstacles:
            ob["x"] -= self.speed * dt
        self.obstacles = [ob for ob in self.obstacles if ob["x"] + ob["w"] > -10]
        self._gap -= self.speed * dt
        if self._gap <= 0:
            kind = self.rng.choice(("rock", "cactus", "cactus", "log"))
            w, h = {"rock": (30, 24), "cactus": (20, 44), "log": (46, 20)}[kind]
            self.obstacles.append({"x": CW + 20.0, "w": w, "h": h, "kind": kind})
            self._gap = self.rng.uniform(230, 420) + self.speed * 0.35
        for ob in self.obstacles:
            if ob["x"] < self.PET_X + 16 and ob["x"] + ob["w"] > self.PET_X - 16 and self.y < ob["h"] - 4:
                self.done = True
                break

    @property
    def score(self) -> int:
        return int(self.distance / 100)


LOGIC = {"catch": CatchGame, "bonk": BonkGame, "memory": MemoryGame, "simon": SimonGame, "hop": HopGame}


# =============================================================================
# Records & rewards (stored in the pet's state)
# =============================================================================

def records(state: dict) -> dict:
    rec = state.get("minigames")
    if not isinstance(rec, dict):
        rec = {}
    rec.setdefault("best", {})
    rec.setdefault("plays", {})
    state["minigames"] = rec
    return rec


def best_score(state: dict, game: str) -> int:
    try:
        return int(records(state)["best"].get(game, 0) or 0)
    except (TypeError, ValueError):
        return 0


def record_result(state: dict, game: str, score: int, today: str | None = None) -> dict:
    """Store the result. Returns {'best': bool, 'good': bool, 'coins': int, 'happy': int, 'bond': int}."""
    rec = records(state)
    score = max(0, int(score))
    old = best_score(state, game)
    is_best = score > old
    if is_best:
        rec["best"][game] = score
    rec["plays"][game] = int(rec["plays"].get(game, 0) or 0) + 1
    good = score >= GOOD_SCORE.get(game, 10**9)
    coins = 0
    if good:
        today = today or date.today().isoformat()
        day, count = (rec.get("coin_days") or [None, 0])[:2]
        if day != today:
            count = 0
        if count < COIN_GAMES_PER_DAY:
            rec["coin_days"] = [today, int(count) + 1]
            coins = GAME_COINS
    happy = 10 if good else 5
    if is_best and score > 0:
        happy += 3
    return {"best": is_best and score > 0, "good": good, "coins": coins, "happy": happy, "bond": 2 if good else 1}


def apply_reward(pet, game: str, score: int) -> dict:
    """Record the result on the pet and let it react (happiness, bond, coins, a happy hop)."""
    result = record_result(pet.st, game, score)
    pet.st["games_played"] = int(pet.st.get("games_played", 0) or 0) + 1
    try:
        pet._bump("happiness", result["happy"])
        pet._bump("bond", result["bond"])
        pet._bump("energy", -2)
    except Exception:
        pass
    coins = result["coins"]
    if coins and getattr(pet, "shop", None) is not None:
        try:
            pet.shop.add_gift_coins(coins)
        except Exception:
            result["coins"] = 0
    elif coins:
        result["coins"] = 0
    try:
        if result["good"] or result["best"]:
            pet.set_action("love", 2.0)
            pet._spawn("heart", 4)
        else:
            pet.set_action("hop", 0.6)
        pet.save_state()
        pet._grow("game", announce_ms=4500)
    except Exception:
        pass
    return result


# =============================================================================
# Window
# =============================================================================
_window = None


def open_game(pet, game: str = "catch"):
    """Open (or switch) the game window."""
    global _window
    if game not in GAMES:
        game = "catch"
    if _window is not None and _window.alive():
        _window.load(game)
        _window.lift()
        return _window
    _window = GameWindow(pet, game)
    return _window


def _beep(freq, ms):
    def run():
        try:
            import winsound
            winsound.Beep(int(freq), int(ms))
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


class GameWindow:
    BG = "#fff8ec"
    INK = "#2b2b2b"
    MUTED = "#7a7468"
    ACCENT = "#e8590c"
    PAD_COLOURS = (("#fa5252", "#ffc9c9"), ("#40c057", "#d3f9d8"), ("#228be6", "#d0ebff"), ("#fab005", "#fff3bf"))
    PAD_TONES = (330, 392, 494, 587)

    def __init__(self, pet, game):
        import pet as pet_mod  # late import (pet imports this module)

        self.pet = pet
        self.pet_mod = pet_mod
        self._alive = True
        self.game_key = game
        self.game = None
        self.state = "intro"            # intro | play | over
        self.result = None
        self.effects: list = []         # (x, y, text, colour, t0)
        self.flash = {}                 # simon pad -> until
        self.show_queue: list = []      # simon pattern playback
        self.input_ok = True
        self.memory_hide_at = 0.0
        self.keys = set()
        self._last = time.monotonic()
        self.win = tk.Toplevel(pet.root)
        self.win.configure(bg=self.BG)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        icon = pet.cb.get("icon_path") if hasattr(pet, "cb") else None
        if icon:
            try:
                self.win.iconbitmap(icon)
            except tk.TclError:
                pass
        top = tk.Frame(self.win, bg=self.BG, padx=10, pady=6)
        top.pack(fill="x")
        self.picker = {}
        for key in GAMES:
            btn = tk.Label(top, text=ICONS[key], font=("Segoe UI Emoji", 13), padx=6, cursor="hand2", bg=self.BG)
            btn.pack(side="left")
            btn.bind("<Button-1>", lambda e, k=key: self.load(k))
            self.picker[key] = btn
        self.hud = tk.Label(top, text="", font=("Segoe UI", 10, "bold"), fg=self.INK, bg=self.BG, anchor="e")
        self.hud.pack(side="right")
        self.mid = tk.Frame(self.win, bg=self.BG)
        self.mid.pack(padx=10, fill="x")
        self.cv = tk.Canvas(self.mid, width=CW, height=CH, bg="#ffffff", highlightthickness=0)
        self.cv.pack(side="left")
        foot = tk.Frame(self.win, bg=self.BG, padx=10, pady=6)
        foot.pack(fill="x")
        self.foot = foot
        self.tip = tk.Label(foot, text="", font=("Segoe UI", 8), fg=self.MUTED, bg=self.BG, anchor="w",
                            justify="left", wraplength=CW - 120)
        self.tip.pack(side="left")
        close = tk.Label(foot, text=t("mg_close"), font=("Segoe UI", 9), fg="#ffffff", bg="#868e96", padx=10, pady=2,
                         cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        self.close_btn = close
        self._setup_extra()
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Motion>", self._motion)
        self.win.bind("<KeyPress>", self._key_down)
        self.win.bind("<KeyRelease>", self._key_up)
        self.load(game)
        self._place()
        self._pet_mood = (None, 0.0)    # (happy | sad, until) - the pet reacts inside the game
        self._take_pet()
        self.pet.root.after(16, self._tick)

    def _setup_extra(self):
        """Hook for subclasses (party mode adds its scoreboard here, before the first load)."""

    # ---- v2.25: the pet leaves the desktop and plays inside the game window ----
    def _take_pet(self):
        enter = getattr(self.pet, "enter_game", None)
        if callable(enter):
            try:
                enter(self)
            except Exception:
                pass

    def _release_pet(self):
        leave = getattr(self.pet, "leave_game", None)
        if callable(leave):
            try:
                leave(self)
            except Exception:
                pass

    def _react(self, mood, seconds=0.6):
        self._pet_mood = (mood, time.monotonic() + seconds)

    def _pet_extra(self, now):
        mood, until = getattr(self, "_pet_mood", (None, 0.0))
        if now >= until:
            return None
        if mood == "happy":
            return {"mouth": "big_open"}
        return {"eyes": "closed", "mouth": "wavy"}

    def _draw_buddy(self, now, x, floor, size, facing=-1):
        """The pet watching / cheering from a corner (games where it isn't the player)."""
        bob = 2 * math.sin(now * 4) if getattr(self, "_pet_mood", (None, 0))[0] == "happy" and now < self._pet_mood[1] else 0
        self._draw_pet(x, floor - abs(bob) * 3, size, facing=facing, extra=self._pet_extra(now))

    # ---- plumbing ----
    def alive(self) -> bool:
        return self._alive

    def lift(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def close(self):
        global _window
        self._alive = False
        self._release_pet()
        if _window is self:
            _window = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _place(self):
        try:
            self.win.update_idletasks()
            x = max(20, self.pet.root.winfo_screenwidth() // 2 - (CW + 20) // 2)
            self.win.geometry(f"+{x}+110")
            self.lift()
        except tk.TclError:
            pass

    def load(self, game):
        self.game_key = game if game in GAMES else "catch"
        self.state = "intro"
        self.game = None
        self.result = None
        self.effects = []
        self.flash = {}
        self.show_queue = []
        self.win.title(f"{ICONS[self.game_key]} {t('mg_' + self.game_key)}")
        for key, btn in self.picker.items():
            btn.config(bg="#ffd8a8" if key == self.game_key else self.BG)
        self.tip.config(text=t("mg_" + self.game_key + "_how"))

    def _new_rng(self):
        return None                   # solo: a fresh random game every time

    def start(self):
        self.game = LOGIC[self.game_key](rng=self._new_rng())
        self.state = "play"
        self.result = None
        self.effects = []
        self._last = time.monotonic()
        if self.game_key == "simon":
            self._play_pattern(delay=0.6)
        try:
            self.cv.focus_set()
        except tk.TclError:
            pass

    def finish(self):
        if self.state != "play":
            return
        self.state = "over"
        score = self.game.score
        try:
            self.result = apply_reward(self.pet, self.game_key, score)
        except Exception:
            self.result = {"best": False, "good": False, "coins": 0}
        self.result["score"] = score

    # ---- input ----
    def _key_down(self, event):
        key = event.keysym.lower()
        self.keys.add(key)
        if key in ("return", "space") and self.state != "play":
            self._overlay_action()
            return
        if self.state != "play":
            return
        if self.game_key == "hop" and key in ("space", "up", "w"):
            if self.game.jump():
                self._react("happy", 0.35)
                if self._sounds():
                    _beep(660, 40)
        elif self.game_key == "simon" and key in ("1", "2", "3", "4"):
            self._simon_press(int(key) - 1)

    def _key_up(self, event):
        self.keys.discard(event.keysym.lower())

    def _motion(self, event):
        if self.state == "play" and self.game_key == "catch":
            self.game.move_to(event.x)

    def _click(self, event):
        if self.state != "play":
            if self._button_hit(event.x, event.y):
                self._overlay_action()
            return
        g = self.game
        if self.game_key == "hop":
            g.jump()
        elif self.game_key == "bonk":
            index = self._bonk_index(event.x, event.y)
            if index is not None:
                what = g.bonk(index)
                if what:
                    self._react("sad" if what == "flower" else "happy")
                    cx, cy = self._hole_xy(index)
                    text = {"bug": "+1", "gold": "+3", "flower": "-2"}[what]
                    colour = "#e03131" if what == "flower" else "#2f9e44"
                    self.effects.append((cx, cy - 40, text, colour, time.monotonic()))
        elif self.game_key == "memory":
            index = self._card_index(event.x, event.y)
            if index is not None and not self.memory_hide_at:
                what = g.flip(index)
                if what == "miss":
                    self.memory_hide_at = time.monotonic() + 0.75
                    self._react("sad")
                elif what == "match":
                    self._react("happy", 0.9)
                    x, y = self._card_xy(index)
                    self.effects.append((x, y - 30, "\u2728", "#f59f00", time.monotonic()))
        elif self.game_key == "simon":
            pad = self._pad_index(event.x, event.y)
            if pad is not None:
                self._simon_press(pad)

    def _overlay_action(self):
        """The big button on the intro / game-over card (solo: start a new game)."""
        self.start()

    def _sounds(self) -> bool:
        try:
            return bool(self.pet.s.get("sounds", True))
        except Exception:
            return False

    # ---- simon helpers ----
    def _play_pattern(self, delay=0.5):
        now = time.monotonic() + delay
        self.show_queue = []
        for i, pad in enumerate(self.game.seq):
            self.show_queue.append((now + i * 0.6, pad))
        self.input_ok = False

    def _simon_press(self, pad):
        if not self.input_ok or self.game is None:
            return
        self.flash[pad] = time.monotonic() + 0.25
        if self._sounds():
            _beep(self.PAD_TONES[pad], 160)
        what = self.game.press(pad)
        if what == "wrong":
            self._react("sad", 1.5)
        elif what == "round":
            self._react("happy", 0.9)
        if what == "round":
            self.effects.append((CW / 2, CH / 2, "\u2714", "#2f9e44", time.monotonic()))
            self._play_pattern(delay=0.8)
        elif what == "wrong":
            if self._sounds():
                _beep(180, 350)

    # ---- geometry ----
    def _hole_xy(self, index):
        col, row = index % 3, index // 3
        return 90 + col * 130, 95 + row * 95

    def _bonk_index(self, x, y):
        for i in range(BonkGame.HOLES):
            cx, cy = self._hole_xy(i)
            if abs(x - cx) < 50 and -60 < y - cy < 30:
                return i
        return None

    def _card_xy(self, index):
        col, row = index % 4, index // 4
        return 60 + col * 92, 50 + row * 80        # leaves room on the right for the pet

    def _card_index(self, x, y):
        for i in range(16):
            cx, cy = self._card_xy(i)
            if abs(x - cx) < 42 and abs(y - cy) < 35:
                return i
        return None

    def _pad_rect(self, pad):
        col, row = pad % 2, pad // 2
        x0 = CW / 2 - 150 + col * 155
        y0 = 30 + row * 140
        return x0, y0, x0 + 145, y0 + 130

    def _pad_index(self, x, y):
        for pad in range(4):
            x0, y0, x1, y1 = self._pad_rect(pad)
            if x0 <= x <= x1 and y0 <= y <= y1:
                return pad
        return None

    def _button_hit(self, x, y):
        return abs(x - CW / 2) < 110 and abs(y - (CH - 70)) < 22

    # ---- loop ----
    def _tick(self):
        if not self._alive:
            return
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        if self.state == "play" and self.game is not None:
            g = self.game
            if self.game_key == "catch":
                if {"left", "a"} & self.keys:
                    g.nudge(-360 * dt)
                if {"right", "d"} & self.keys:
                    g.nudge(360 * dt)
            if self.game_key == "memory" and self.memory_hide_at and now >= self.memory_hide_at:
                g.hide_open()
                self.memory_hide_at = 0.0
            if self.game_key == "simon":
                while self.show_queue and self.show_queue[0][0] <= now:
                    _at, pad = self.show_queue.pop(0)
                    self.flash[pad] = now + 0.38
                    if self._sounds():
                        _beep(self.PAD_TONES[pad], 300)
                if not self.show_queue and not self.input_ok and not any(v > now for v in self.flash.values()):
                    self.input_ok = True
            g.step(dt)
            if self.game_key == "catch":
                for kind, x, y in g.events:
                    self._react("sad" if kind == "ouch" else "happy", 0.5)
                    text = {"cookie": "+1", "fish": "+2", "star": "+5", "ouch": "\U0001F4A5"}[kind]
                    self.effects.append((x, y - 20, text, "#e03131" if kind == "ouch" else "#2f9e44", now))
                g.events.clear()
            if g.done:
                self.finish()
        try:
            self._draw(now)
        except tk.TclError:
            return
        self.pet.root.after(16 if self.state == "play" else 50, self._tick)

    # ---- drawing ----
    def _hud(self):
        best = best_score(self.pet.st, self.game_key)
        g = self.game
        parts = [t("mg_best", n=best)]
        if g is not None and self.state == "play":
            parts.insert(0, t("mg_score", n=g.score))
            if hasattr(g, "time_left"):
                parts.append(f"\u23f1 {int(math.ceil(g.time_left))}s")
            if hasattr(g, "lives"):
                parts.append("\u2764" * max(0, g.lives))
            if self.game_key == "memory":
                parts.append(t("mg_misses", n=g.misses))
        self.hud.config(text="   ".join(parts))

    def _draw_pet(self, x, floor, size, facing=1, extra=None):
        pet = self.pet
        look = {}
        social = getattr(pet, "social", None)
        try:
            if social is not None and hasattr(social, "my_look"):
                look = social.my_look() or {}
        except Exception:
            look = {}
        fighter = {"species": pet.s.get("species"), "color": pet.s.get("color"), "look": look}
        try:
            if pet_shop is not None:
                pet_shop.draw_fighter(self.cv, self.pet_mod, x, floor, size, fighter, facing=facing,
                                      t=time.monotonic(), look_extra=extra or {})
                return
        except Exception:
            pass
        self.cv.create_oval(x - size / 2, floor - size, x + size / 2, floor, fill=pet.s.get("color", "#ffc78f"),
                            outline="#555555")

    def _draw(self, now):
        cv = self.cv
        cv.delete("all")
        self._hud()
        getattr(self, "_draw_" + self.game_key)(now)
        keep = []
        for (x, y, text, colour, t0) in self.effects:
            age = now - t0
            if age < 0.9:
                keep.append((x, y, text, colour, t0))
                cv.create_text(x, y - age * 40, text=text, fill=colour, font=("Segoe UI Emoji", 14, "bold"))
        self.effects = keep
        self._draw_overlay(now)

    def _draw_overlay(self, now):
        if self.state == "intro":
            self._overlay(f"{ICONS[self.game_key]} {t('mg_' + self.game_key)}", t("mg_" + self.game_key + "_how"),
                          "\u25b6 " + t("mg_start"))
        elif self.state == "over" and self.result is not None:
            r = self.result
            lines = [t("mg_final", n=r.get("score", 0))]
            if r.get("best"):
                lines.append("\U0001F3C6 " + t("mg_new_best"))
            if r.get("coins"):
                lines.append(t("mg_coins", n=r["coins"]))
            lines.append(t("mg_pet_happy", name=self.pet.s.get("name", "Pips")))
            self._overlay(t("mg_over"), "\n".join(lines), "\u21bb " + t("mg_again"))

    def _overlay(self, title, body, button, body_y=150, top=False):
        cv = self.cv
        cv.create_rectangle(30, 40, CW - 30, CH - 30, fill="#ffffff", outline="#ffa94d", width=3)
        cv.create_text(CW / 2, 74, text=title, fill=self.ACCENT, font=("Segoe UI Emoji", 16, "bold"))
        cv.create_text(CW / 2, body_y, text=body, fill=self.INK, font=("Segoe UI Emoji", 10), width=CW - 100,
                       justify="center", anchor="n" if top else "center")
        if button:
            cv.create_rectangle(CW / 2 - 110, CH - 92, CW / 2 + 110, CH - 48, fill=self.ACCENT, outline="")
            cv.create_text(CW / 2, CH - 70, text=button, fill="#ffffff", font=("Segoe UI Symbol", 11, "bold"))

    def _draw_catch(self, now):
        cv = self.cv
        cv.create_rectangle(0, 0, CW, CH, fill="#d0ebff", outline="")
        for i in range(4):
            x = (i * 130 + now * 12) % (CW + 80) - 40
            cv.create_oval(x, 30 + i * 18, x + 70, 56 + i * 18, fill="#ffffff", outline="")
        cv.create_rectangle(0, CH - 30, CW, CH, fill="#8ce99a", outline="")
        g = self.game
        x = g.x if g else CW / 2
        if g:
            for item in g.items:
                symbol = {"cookie": "\U0001F36A", "fish": "\U0001F41F", "star": "\u2B50", "rock": "\U0001FAA8"}[item["kind"]]
                if item["kind"] == "rock":
                    cv.create_oval(item["x"] - 13, item["y"] - 11, item["x"] + 13, item["y"] + 11, fill="#868e96",
                                   outline="#495057", width=2)
                else:
                    cv.create_text(item["x"], item["y"], text=symbol, font=("Segoe UI Emoji", 18))
        self._draw_pet(x, CH - 26, 56, extra=self._pet_extra(now) or ({"mouth": "big_open"} if g and g.items else None))

    def _draw_bonk(self, now):
        cv = self.cv
        cv.create_rectangle(0, 0, CW, CH, fill="#b2f2bb", outline="")
        g = self.game
        for i in range(BonkGame.HOLES):
            cx, cy = self._hole_xy(i)
            cv.create_oval(cx - 46, cy - 14, cx + 46, cy + 18, fill="#5c3d2e", outline="#3b2418", width=2)
            hole = g.holes[i] if g else None
            if hole:
                rise = min(1.0, (now * 6) % 1 + 0.6) if not hole["hit"] else 0.6
                symbol = {"bug": "\U0001F41E", "gold": "\U0001F41D", "flower": "\U0001F33C"}[hole["kind"]]
                cv.create_text(cx, cy - 18 * rise, text=symbol, font=("Segoe UI Emoji", 26))
                if hole["hit"]:
                    cv.create_text(cx, cy - 46, text="\U0001F4A5", font=("Segoe UI Emoji", 16))
            cv.create_arc(cx - 46, cy - 14, cx + 46, cy + 18, start=180, extent=180, style="arc", outline="#3b2418",
                          width=3)
        self._draw_buddy(now, CW - 32, CH - 4, 58)

    def _draw_memory(self, now):
        cv = self.cv
        cv.create_rectangle(0, 0, CW, CH, fill="#f3d9fa", outline="")
        g = self.game
        for i in range(16):
            x, y = self._card_xy(i)
            face_up = g is not None and (g.matched[i] or i in g.open)
            fill = "#ffffff" if face_up else "#9c36b5"
            if g is not None and g.matched[i]:
                fill = "#d3f9d8"
            cv.create_rectangle(x - 40, y - 33, x + 40, y + 33, fill=fill, outline="#862e9c", width=2)
            if face_up:
                cv.create_text(x, y, text=g.cards[i], font=("Segoe UI Emoji", 24))
            else:
                cv.create_text(x, y, text="?", fill="#f3d9fa", font=("Segoe UI", 20, "bold"))
        self._draw_buddy(now, CW - 34, CH - 6, 60)

    def _draw_simon(self, now):
        cv = self.cv
        cv.create_rectangle(0, 0, CW, CH, fill="#212529", outline="")
        for pad in range(4):
            x0, y0, x1, y1 = self._pad_rect(pad)
            lit = self.flash.get(pad, 0) > now
            dark, light = self.PAD_COLOURS[pad]
            cv.create_rectangle(x0, y0, x1, y1, fill=light if lit else dark, outline="#ffffff" if lit else "#343a40",
                                width=4)
            cv.create_text(x0 + 14, y0 + 14, text=str(pad + 1), fill="#ffffff", font=("Segoe UI", 10, "bold"))
        if not self.input_ok and self.state == "play":
            self._react("happy", 0.2)       # "watch me!" while it shows the pattern
        self._draw_buddy(now, CW - 34, CH - 30, 60)
        g = self.game
        if g is not None and self.state == "play":
            text = t("mg_simon_watch") if not self.input_ok else t("mg_simon_go", n=g.pos, total=len(g.seq))
            cv.create_text(CW / 2, CH - 22, text=text, fill="#ffffff", font=("Segoe UI", 10, "bold"))

    def _draw_hop(self, now):
        cv = self.cv
        cv.create_rectangle(0, 0, CW, CH, fill="#fff3bf", outline="")
        cv.create_oval(CW - 90, 20, CW - 40, 70, fill="#ffd43b", outline="")
        g = self.game
        shift = (g.distance if g else now * 100) * 0.3
        for i in range(5):
            x = (i * 120 - shift) % (CW + 120) - 60
            cv.create_polygon(x, HopGame.GROUND, x + 60, HopGame.GROUND - 70, x + 120, HopGame.GROUND,
                              fill="#ffe8cc", outline="")
        cv.create_rectangle(0, HopGame.GROUND, CW, CH, fill="#e9b872", outline="")
        if g:
            for ob in g.obstacles:
                x0, x1 = ob["x"], ob["x"] + ob["w"]
                top = HopGame.GROUND - ob["h"]
                colour = {"rock": "#868e96", "cactus": "#2f9e44", "log": "#8d5524"}[ob["kind"]]
                if ob["kind"] == "rock":
                    cv.create_oval(x0, top, x1, HopGame.GROUND + 4, fill=colour, outline="#495057", width=2)
                else:
                    cv.create_rectangle(x0, top, x1, HopGame.GROUND, fill=colour, outline="#2b2b2b", width=1)
        y = g.y if g else 0
        extra = {"eyes": "closed", "mouth": "wavy"} if g and g.done else self._pet_extra(now)
        self._draw_pet(HopGame.PET_X, HopGame.GROUND - y, 50, extra=extra)


# =============================================================================
# Play together (v2.25): party rooms for 2-8 friends
# =============================================================================
PARTY_MAX = 8                  # players per room (the host + 7 friends)
PARTY_SCORE_EVERY = 1.2        # seconds between live score updates while playing
PARTY_POLL_EVERY = 1.5         # seconds between room refreshes while waiting
PARTY_FINAL_RETRY = 2.5        # resend the final score until the server has it
PARTY_MEDALS = ("\U0001F947", "\U0001F948", "\U0001F949")
_party_windows: dict = {}


def party_standings(players: list) -> list:
    """Players who took part, best score first, each with a 'place' (ties share a place)."""
    playing = [dict(p) for p in players or [] if p.get("status") == "joined" or (p.get("status") == "left" and p.get("score"))]
    playing.sort(key=lambda p: (-int(p.get("score") or 0), -int(p.get("wins") or 0), str(p.get("name") or "")))
    place, last = 0, None
    for i, p in enumerate(playing, 1):
        score = int(p.get("score") or 0)
        if score != last:
            place, last = i, score
        p["place"] = place
    return playing


def party_place(players: list, code: str) -> tuple:
    """(place, number of players) for `code`, or (0, n) if they didn't play."""
    table = party_standings(players)
    for p in table:
        if p.get("code") == code:
            return p["place"], len(table)
    return 0, len(table)


def party_reward(pet, place: int, players: int) -> dict:
    """A party round: the pet enjoys playing with friends; winning makes it extra happy (no coins)."""
    won = place == 1 and players >= 2
    happy, bond = (8, 3) if won else (5, 2)
    try:
        pet._bump("happiness", happy)
        pet._bump("bond", bond)
        pet.st["party_rounds"] = int(pet.st.get("party_rounds", 0) or 0) + 1
        if won:
            pet.st["party_wins"] = int(pet.st.get("party_wins", 0) or 0) + 1
            pet.set_action("love", 2.0)
            pet._spawn("heart", 5)
        else:
            pet.set_action("hop", 0.6)
        pet.save_state()
    except Exception:
        pass
    return {"won": won, "happy": happy, "bond": bond}


def open_party(pet, room: dict):
    """Open (or refresh) the window for a party room. Only one party window is open at a time."""
    room_id = (room or {}).get("id")
    if not room_id:
        return None
    win = party_window(room_id, pet)
    if win is not None:
        win.take_room(room)
        win.lift()
        return win
    for key, other in list(_party_windows.items()):
        if key[0] == id(pet):
            if other.alive():
                other.close(leave=True)
            _party_windows.pop(key, None)
    win = PartyGameWindow(pet, room)
    _party_windows[(id(pet), room_id)] = win
    return win


def party_window(room_id: str, pet=None):
    """The open window for a room (of this pet, if given - tests run two pets in one process)."""
    for (pet_id, rid), win in list(_party_windows.items()):
        if rid == room_id and (pet is None or pet_id == id(pet)) and win.alive():
            return win
    return None


def party_windows(pet=None) -> list:
    return [w for (pet_id, _rid), w in list(_party_windows.items()) if w.alive() and (pet is None or pet_id == id(pet))]


class PartyGameWindow(GameWindow):
    """One of the minigames played together. States:
    lobby (room open) -> countdown -> play -> wait (I finished, friends still playing) -> over (results)
    -> countdown (host starts the next round) ...  and closed (room ended / not found)."""

    SIDE_W = 200

    def __init__(self, pet, room):
        self.room = dict(room)
        self.social = getattr(pet, "social", None)
        self.me = (self.social.data.get("code") if self.social is not None else "") or ""
        self.round_no = 0             # round being played / last played here
        self.seed = None
        self.start_at = 0.0
        self.final_score = None       # my score once I finished the round
        self.final_acked = False
        self.reacted_round = 0        # round whose result the pet already reacted to
        self.next_game = room.get("game") if room.get("game") in GAMES else "catch"
        self.error_text = ""
        self._busy = False            # a room request is on its way
        self._next_send = 0.0
        self._next_poll = 0.0
        self._last_sent = None
        self._board_sig = None
        self._left = False
        self._ready = False           # the first load() (from GameWindow.__init__) always goes through
        super().__init__(pet, self.next_game)
        self._ready = True
        self.take_room(room)

    # ---- layout ----
    def _setup_extra(self):
        side = tk.Frame(self.mid, bg="#fff1dc", width=self.SIDE_W, height=CH, padx=8, pady=6)
        side.pack(side="left", fill="y", padx=(8, 0))
        side.pack_propagate(False)
        self.side_title = tk.Label(side, text="", font=("Segoe UI", 9, "bold"), fg=self.ACCENT, bg="#fff1dc",
                                   anchor="w", justify="left", wraplength=self.SIDE_W - 16)
        self.side_title.pack(fill="x")
        self.board = tk.Label(side, text="", font=("Segoe UI Emoji", 8), fg=self.INK, bg="#fff1dc", anchor="nw",
                              justify="left", wraplength=self.SIDE_W - 16)
        self.board.pack(fill="both", expand=True, pady=(4, 0))
        self.close_btn.config(text=t("mp_leave"))

    def _place(self):
        try:
            self.win.update_idletasks()
            width = CW + self.SIDE_W + 40
            x = max(20, self.pet.root.winfo_screenwidth() // 2 - width // 2)
            self.win.geometry(f"+{x}+100")
            self.lift()
        except tk.TclError:
            pass

    # ---- helpers ----
    @property
    def is_host(self) -> bool:
        return bool(self.me) and self.room.get("host") == self.me

    def _players(self) -> list:
        return self.room.get("players") or []

    def _joined(self) -> list:
        return [p for p in self._players() if p.get("status") == "joined"]

    def _name(self, code) -> str:
        for p in self._players():
            if p.get("code") == code:
                return p.get("name") or "?"
        return "?"

    def _new_rng(self):
        return random.Random(self.seed) if self.seed is not None else None

    # ---- the game picker: only the host, only between rounds ----
    def load(self, game):
        if self.state in ("play", "countdown", "wait") and self.game is not None:
            return
        if self._ready and not self.is_host:
            return
        super().load(game)
        self.next_game = self.game_key
        self.state = self._idle_state()
        self.win.title(f"\U0001F465 {ICONS[self.game_key]} {t('mg_' + self.game_key)}")

    def _idle_state(self) -> str:
        status = (getattr(self, "room", None) or {}).get("status")
        return {"open": "lobby", "done": "over", "closed": "closed"}.get(status, "lobby")

    def _update_picker(self):
        can_pick = self.is_host and self.room.get("status") in ("open", "done")
        for key, btn in self.picker.items():
            selected = key == self.game_key
            btn.config(bg="#ffd8a8" if selected else self.BG, fg=self.INK if (can_pick or selected) else "#c9c4bb",
                       cursor="hand2" if can_pick else "arrow")

    # ---- room updates (from social.py) ----
    def take_room(self, room: dict):
        if not self._alive or not room or room.get("id") != self.room.get("id"):
            return
        self._busy = False
        self.error_text = ""
        self.room = dict(room)
        status = room.get("status")
        mine = room.get("mine") or {}
        if mine.get("done") and self.final_score is not None:
            self.final_acked = True
        if status == "playing" and int(room.get("round") or 0) != self.round_no:
            self._begin_round(room)
        elif status == "playing" and self.state in ("lobby", "over", "intro"):
            self._begin_round(room)
        elif status == "done":
            if self.state == "play" and self.game is not None:
                self.finish()              # deadline: the round ended while I was still playing
            if self.state in ("play", "wait", "countdown", "lobby", "intro") or self.reacted_round != self.round_no:
                self.state = "over"
                self._react_to_result()
            if self.is_host and self.next_game:
                self.game_key = self.next_game
        elif status == "open":
            if self.state not in ("play", "countdown"):
                self.state = "lobby"
                if not self.is_host and room.get("game") in GAMES and room.get("game") != self.game_key:
                    GameWindow.load(self, room["game"])
                    self.state = "lobby"
        elif status == "closed":
            self.state = "closed"
        self._update_picker()
        self._refresh_board(force=True)

    def _begin_round(self, room):
        self.round_no = int(room.get("round") or 1)
        self.seed = int(room.get("seed") or 0) or None
        game = room.get("game") if room.get("game") in GAMES else "catch"
        GameWindow.load(self, game)           # resets the canvas state for this game
        self.next_game = game
        self.win.title(f"\U0001F465 {ICONS[game]} {t('mg_' + game)}")
        self.start_at = time.monotonic() + max(0.0, float(room.get("starts_in") or 0))
        self.final_score = None
        self.final_acked = False
        self._last_sent = None
        self.state = "countdown"
        if (room.get("mine") or {}).get("status") != "joined":
            self.state = "over"            # I'm only watching this round

    def _react_to_result(self):
        if self.reacted_round == self.round_no or not self.round_no:
            return
        self.reacted_round = self.round_no
        place, count = party_place(self._players(), self.me)
        if not place:
            return
        reward = party_reward(self.pet, place, count)
        name = self.pet.s.get("name", "Pips")
        try:
            if reward["won"]:
                self.pet.say(t("mp_pet_won", name=name, game=t("mg_" + self.game_key)), duration=6, priority=1)
                if self._sounds():
                    _beep(784, 120)
                    _beep(988, 180)
        except Exception:
            pass

    def on_error(self, code):
        self._busy = False
        if code == "not_found":
            self.state = "closed"
        self.error_text = {"need_players": t("mp_err_need_players"), "game_started": t("mp_err_started"),
                           "room_full": t("mp_err_full"), "expired": t("mp_err_closed"),
                           "not_found": t("mp_err_closed"), "too_fast": t("fr_err_too_fast"),
                           "offline": t("fr_err_offline")}.get(code, t("fr_err_generic", code=code))
        if code == "expired":
            self.state = "closed"

    # ---- server calls ----
    def _request(self, fn, *args, **kwargs):
        if self.social is None or self._busy:
            return
        self._busy = True
        try:
            fn(*args, **kwargs)
        except Exception:
            self._busy = False

    def _overlay_action(self):
        if self.state == "closed":
            self.close(leave=False)
        elif self.is_host and self.state in ("lobby", "over"):
            if len(self._joined()) < 2:
                self.error_text = t("mp_err_need_players")
                return
            self._request(self.social.mp_start, self.room["id"], game=self.next_game)

    # ---- the round ----
    def finish(self):
        if self.state != "play":
            return
        score = self.game.score if self.game is not None else 0
        self.final_score = score
        try:
            self.result = record_result(self.pet.st, self.game_key, score)   # personal best still counts
        except Exception:
            self.result = {"best": False}
        self.result["score"] = score
        self.state = "wait"
        self._send_score(final=True)

    def _send_score(self, final=False):
        if self.social is None:
            return
        score = self.final_score if final else (self.game.score if self.game is not None else 0)
        self._busy = True
        self._last_sent = score
        self.social.mp_score(self.room["id"], self.round_no, int(score or 0), done=final)

    def _tick(self):
        if self._alive:
            now = time.monotonic()
            if self.state == "countdown" and now >= self.start_at:
                self.start()
                self._next_send = now + PARTY_SCORE_EVERY
            elif self.state == "play" and self.game is not None and now >= self._next_send and not self._busy:
                self._next_send = now + PARTY_SCORE_EVERY
                if self.game.score != self._last_sent or now >= self._next_poll:
                    self._next_poll = now + 4
                    self._send_score()
            elif self.state == "wait" and not self.final_acked and now >= self._next_send and not self._busy:
                self._next_send = now + PARTY_FINAL_RETRY
                self._send_score(final=True)
            elif self.state in ("lobby", "wait", "over", "countdown") and now >= self._next_poll and not self._busy:
                self._next_poll = now + PARTY_POLL_EVERY
                self._request(self.social.mp_load, self.room["id"])
            self._refresh_board()
        super()._tick()

    # ---- scoreboard ----
    def _refresh_board(self, force=False):
        players = [dict(p) for p in self._players() if p.get("status") in ("joined", "invited", "left")]
        live = self.state == "play" and self.game is not None
        for p in players:
            if p.get("code") == self.me and live:
                p["score"] = self.game.score      # my own score is always fresher locally
        sig = (self.state, self.round_no, tuple((p.get("code"), p.get("status"), p.get("score"), p.get("done"),
                                                 p.get("wins")) for p in players))
        if not force and sig == self._board_sig:
            return
        self._board_sig = sig
        title = t("mp_round", n=max(1, self.round_no or int(self.room.get("round") or 1)))
        host = self._name(self.room.get("host"))
        self.side_title.config(text=f"\U0001F465 {title}  \u00b7  {t('mp_host_short')}: {host}")
        joined = [p for p in players if p.get("status") == "joined"]
        joined.sort(key=lambda p: (-int(p.get("score") or 0), str(p.get("name") or "")))
        rows = []
        show_scores = self.state not in ("lobby",)
        for i, p in enumerate(joined):
            medal = PARTY_MEDALS[i] if show_scores and i < 3 and int(p.get("score") or 0) > 0 else "\u2022"
            name = (p.get("name") or "?")[:9]
            if p.get("code") == self.me:
                name = "\u25b8" + name           # that's me
            line = f"{medal} {name}"
            if show_scores:
                line += f"  {int(p.get('score') or 0)}"
                if p.get("done") and self.state in ("play", "wait"):
                    line += " \u2714"
            if int(p.get("wins") or 0):
                line += f" \U0001F451{p['wins']}"
            rows.append(line)
        waiting = [p for p in players if p.get("status") == "invited"]
        if waiting:
            rows.append("")
            rows.append(t("mp_invited_list"))
            rows.extend(f"\u2026 {(p.get('name') or '?')[:14]}" for p in waiting)
        gone = [p for p in players if p.get("status") == "left"]
        if gone:
            rows.append("")
            rows.extend(f"\u2716 {(p.get('name') or '?')[:14]} ({t('mp_left_short')})" for p in gone)
        self.board.config(text="\n".join(rows))

    # ---- drawing ----
    def _draw_overlay(self, now):
        joined = self._joined()
        game_name = f"{ICONS[self.game_key]} {t('mg_' + self.game_key)}"
        err = ("\n\u26a0 " + self.error_text) if self.error_text else ""
        host_name = self._name(self.room.get("host"))
        self._update_tip()
        if self.state == "lobby":
            names = ", ".join(p.get("name", "?") for p in joined)
            body = t("mp_lobby_body", game=t("mg_" + self.game_key), n=len(joined), names=names)
            button = None
            if self.is_host and len(joined) >= 2:
                button = "\u25b6 " + t("mp_start", n=len(joined))
            elif self.is_host:
                body += "\n\n" + t("mp_wait_join")
            else:
                body += "\n\n" + t("mp_wait_host", name=host_name)
            self._overlay("\U0001F465 " + game_name, body + err, button, body_y=104, top=True)
        elif self.state == "countdown":
            left = max(0.0, self.start_at - now)
            self._overlay("\U0001F465 " + game_name, t("mp_get_ready", n=len(joined)), None, body_y=104, top=True)
            self.cv.create_text(CW / 2, CH - 100, text=str(int(math.ceil(left))) if left > 0.05 else t("mp_go"),
                                fill=self.ACCENT, font=("Segoe UI", 34, "bold"))
        elif self.state == "wait":
            done = sum(1 for p in joined if p.get("done"))
            body = t("mg_final", n=self.final_score or 0) + "\n" + t("mp_waiting_others", done=done, n=len(joined))
            if self.result and self.result.get("best"):
                body += "\n\U0001F3C6 " + t("mg_new_best")
            self._overlay(t("mg_over"), body + err, None, body_y=104, top=True)
        elif self.state == "over":
            table = party_standings(self._players())
            place, count = party_place(self._players(), self.me)
            if place == 1 and count >= 2 and table and int(table[0].get("score") or 0) > 0:
                head = "\U0001F3C6 " + t("mp_you_won")
            elif place:
                head = t("mp_you_placed", place=place, n=count)
            else:
                head = t("mp_round_over")
            lines = []
            for p in table[:3]:
                medal = PARTY_MEDALS[p["place"] - 1] if p["place"] <= 3 and int(p.get("score") or 0) > 0 else f"{p['place']}."
                lines.append(f"{medal} {p.get('name', '?')}  {int(p.get('score') or 0)}")
            body = head + "\n" + "\n".join(lines)
            button = None
            if self.is_host and len(joined) >= 2:
                button = "\u21bb " + t("mp_next_round")
            elif self.is_host:
                body += "\n" + t("mp_err_need_players")
            else:
                body += "\n" + t("mp_wait_host", name=host_name)
            title = t("mp_results", n=max(1, self.round_no or int(self.room.get("round") or 1)))
            self._overlay(title, body + err, button, body_y=100, top=True)
        elif self.state == "closed":
            self._overlay(t("mp_title"), t("mp_err_closed") + err, t("mg_close"), body_y=104, top=True)

    def _update_tip(self):
        """Footer line: the host's 'switch game' hint between rounds, otherwise how to play."""
        text = t("mp_pick_hint") if self.is_host and self.state in ("lobby", "over") else t("mg_" + self.game_key + "_how")
        try:
            if self.tip.cget("text") != text:
                self.tip.config(text=text)
        except tk.TclError:
            pass

    # ---- closing ----
    def close(self, leave=True):
        if leave and not self._left and self.social is not None and self.room.get("status") != "closed":
            self._left = True
            try:
                self.social.mp_leave(self.room["id"])
            except Exception:
                pass
        self._alive = False
        self._release_pet()
        _party_windows.pop((id(self.pet), self.room.get("id")), None)
        try:
            self.win.destroy()
        except tk.TclError:
            pass
