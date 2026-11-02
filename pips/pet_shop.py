"""
pet_shop.py - coins, the shop catalog, props your pet can wear and moves (tricks).

Coins are *derived* from what you did (pomodoros, levels, achievements, days you
used Pips, friendships, interactions with friends) plus a welcome gift, minus
what you spent - so nothing is lost if a file gets out of sync and earning
can't be double counted. Purchases and the equipped props live in
data/shop.json.

Drawing helpers here are pure tkinter-canvas code shared by the desktop pet,
the visiting friend's pet and the avatars in the Pips hub.
All user-facing text comes from i18n.py.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date
from pathlib import Path

logger = logging.getLogger("pet_shop")

WELCOME_GIFT = 100
COINS_PER_POMODORO = 5
COINS_PER_LEVEL = 40
COINS_PER_ACHIEVEMENT = 25
COINS_PER_DAY = 10
COINS_PER_AFFINITY_LEVEL = 15
COINS_PER_INTERACTION = 2
INTERACTION_DAILY_CAP = 20
COIN = "\u25c9"   # ◉

SLOTS = ("head", "face", "neck", "back")

# id -> (slot, price)
PROPS = {
    "flower": ("head", 40),
    "party_hat": ("head", 60),
    "cap": ("head", 80),
    "beanie": ("head", 90),
    "headphones": ("head", 140),
    "wizard": ("head", 220),
    "halo": ("head", 260),
    "crown": ("head", 300),
    "shades": ("face", 110),
    "heart_glasses": ("face", 130),
    "monocle": ("face", 160),
    "bowtie": ("neck", 50),
    "bell": ("neck", 60),
    "scarf": ("neck", 80),
    "medal": ("neck", 200),
    "cape": ("back", 240),
    "wings": ("back", 380),
    # Premium-only (v2.7)
    "tiara": ("head", 250),
    "star_glasses": ("face", 180),
    "diamond": ("neck", 220),
    "fairy_wings": ("back", 320),
    # v2.12 - head
    "bow": ("head", 45),
    "beret": ("head", 70),
    "propeller_cap": ("head", 95),
    "flower_crown": ("head", 110),
    "chef_hat": ("head", 120),
    "cowboy_hat": ("head", 150),
    "santa_hat": ("head", 150),
    "grad_cap": ("head", 170),
    "pirate_hat": ("head", 190),
    "top_hat": ("head", 210),
    "viking_helmet": ("head", 240),
    # v2.12 - face
    "clown_nose": ("face", 40),
    "mustache": ("face", 70),
    "nerd_glasses": ("face", 90),
    "eyepatch": ("face", 100),
    "goggles": ("face", 140),
    "masquerade": ("face", 170),
    # v2.12 - neck
    "bandana": ("neck", 55),
    "tie": ("neck", 75),
    "lei": ("neck", 90),
    "pearls": ("neck", 160),
    "gold_chain": ("neck", 230),
    # v2.12 - back
    "balloon": ("back", 60),
    "backpack": ("back", 130),
    "guitar": ("back", 220),
    "shield": ("back", 260),
    "butterfly_wings": ("back", 300),
    "devil_wings": ("back", 300),
    # v2.12 - Premium
    "galaxy_crown": ("head", 280),
    "galaxy_cape": ("back", 300),
    "dragon_wings": ("back", 360),
}

# Shop sections (v2.12)
SHOP_CATEGORIES = ("head", "face", "neck", "back", "moves", "gear")

# id -> (price, seconds)
MOVES = {
    "spin": (60, 2.0),
    "dance": (90, 4.0),
    "moonwalk": (140, 4.0),
    "backflip": (180, 1.8),
    "kungfu": (260, 3.4),
    "spider": (420, 5.5),
    "fireworks": (300, 3.6),      # Premium-only
    # v2.12
    "wave": (30, 2.4),
    "jump": (50, 2.6),
    "hearts": (70, 3.2),
    "sing": (80, 3.6),
    "yoga": (100, 4.0),
    "zoomies": (120, 3.2),
    "cartwheel": (150, 2.4),
    "juggle": (170, 4.0),
    "rain_dance": (190, 4.2),
    "breakdance": (220, 3.6),
    "magic": (240, 3.0),
    "confetti": (110, 3.0),
    "rocket": (340, 4.2),         # Premium-only
}
# Arena gear (v2.8): id -> (slot, price, stats). Stats are mirrored from the server (which is authoritative).
GEAR_SLOTS = ("weapon", "armor", "tool")
GEAR = {
    "water_pistol": ("weapon", 80, {"atk": 4, "kind": "gun"}),
    "slingshot": ("weapon", 140, {"atk": 6, "crit": 3, "kind": "gun"}),
    "foam_sword": ("weapon", 220, {"atk": 8, "kind": "melee"}),
    "bubble_blaster": ("weapon", 320, {"atk": 10, "spd": 2, "kind": "gun"}),
    "laser_blaster": ("weapon", 480, {"atk": 13, "crit": 5, "kind": "gun"}),
    "rocket_launcher": ("weapon", 700, {"atk": 18, "spd": -2, "kind": "launcher"}),
    "plasma_cannon": ("weapon", 900, {"atk": 20, "crit": 8, "kind": "gun"}),
    "cardboard_armor": ("armor", 60, {"def": 2, "hp": 10}),
    "leather_armor": ("armor", 150, {"def": 4, "hp": 20}),
    "knight_armor": ("armor", 320, {"def": 7, "hp": 35, "spd": -1}),
    "ninja_suit": ("armor", 380, {"def": 5, "hp": 20, "spd": 4}),
    "mech_suit": ("armor", 750, {"def": 11, "hp": 60}),
    "medkit": ("tool", 120, {"special": "heal"}),
    "water_bomb": ("tool", 200, {"special": "bomb"}),
    "jetpack": ("tool", 300, {"special": "dodge", "spd": 3}),
    "lucky_charm": ("tool", 180, {"special": "power", "crit": 10}),
    "force_field": ("tool", 420, {"special": "shield"}),
}

PREMIUM_ITEMS = {"tiara", "star_glasses", "diamond", "fairy_wings", "fireworks",
                 "galaxy_crown", "galaxy_cape", "dragon_wings", "rocket",
                 "plasma_cannon", "mech_suit", "force_field"}


def fighter_stats(level: int, gear: dict) -> dict:
    """Same maths as the server's fighterStats (for showing your fighter card)."""
    lvl = max(1, min(99, int(level or 1)))
    hp = 120 + 2.5 * lvl
    atk = 18 + 0.4 * lvl
    df = 0.2 * lvl
    crit = 8.0
    spd = 10 + 0.1 * lvl
    special = "power"
    kind = "paw"
    for slot in GEAR_SLOTS:
        item = (gear or {}).get(slot)
        if item not in GEAR or GEAR[item][0] != slot:
            continue
        stats = GEAR[item][2]
        hp += stats.get("hp", 0)
        atk += stats.get("atk", 0)
        df += stats.get("def", 0)
        crit += stats.get("crit", 0)
        spd += stats.get("spd", 0)
        special = stats.get("special", special)
        kind = stats.get("kind", kind)
    crit = min(40.0, crit)
    return {"hp": round(hp), "atk": round(atk, 1), "def": round(df, 1), "crit": round(crit, 1),
            "spd": round(spd, 1), "special": special, "weapon_kind": kind, "level": lvl,
            "power": round(hp / 4 + atk * 3 + df * 3 + crit + spd)}
# =============================================================================
# Skill duels (v2.24) - mirrored from the server's SKILLS table (the server is authoritative)
# =============================================================================
# id -> (icon, unlock, stats). unlock: level number, ("weapon", kinds) or ("tool", special).
SKILLS = {
    "strike": ("\u2694", 1, {"power": 1.0, "acc": 95, "cd": 0}),
    "heavy": ("\U0001F4A5", 1, {"power": 1.7, "acc": 70, "cd": 1}),
    "guard": ("\U0001F6E1", 1, {"guard": 0.6, "heal": 0.04, "cd": 2}),
    "fury": ("\U0001F43E", 4, {"power": 0.55, "hits": 3, "acc": 80, "cd": 2}),
    "drain": ("\U0001F9DB", 7, {"power": 0.9, "acc": 90, "drain": 0.4, "cd": 2}),
    "focus": ("\U0001F3AF", 10, {"focus": True, "cd": 3}),
    "pierce": ("\U0001F3F9", 13, {"power": 1.25, "acc": 92, "pierce": True, "cd": 2}),
    "stun": ("\U0001F4AB", 16, {"power": 0.7, "acc": 85, "stun": 0.4, "cd": 3}),
    "snack": ("\U0001F36A", 20, {"heal": 0.22, "cd": 4}),
    "blast": ("\U0001F52B", ("weapon", ("gun", "launcher")), {"power": 1.4, "acc": 85, "cd": 1}),
    "slash": ("\U0001F5E1", ("weapon", ("melee",)), {"power": 1.3, "acc": 92, "crit": 15, "cd": 1}),
    "medkit": ("\u271a", ("tool", "heal"), {"heal": 0.3, "cd": 4}),
    "bomb": ("\U0001F4A7", ("tool", "bomb"), {"power": 2.2, "acc": 80, "pierce": True, "cd": 4}),
    "jet": ("\U0001F680", ("tool", "dodge"), {"power": 1.1, "acc": 95, "evade": True, "cd": 3}),
    "lucky": ("\U0001F340", ("tool", "power"), {"power": 1.6, "acc": 95, "sure_crit": True, "cd": 4}),
    "field": ("\u25ce", ("tool", "shield"), {"power": 0.8, "acc": 95, "block": True, "cd": 4}),
}
DEFAULT_SKILLS = ("strike", "heavy", "guard")
DUEL_ROUND_CHOICES = (1, 3, 5)


def skill_unlocked(skill: str, level: int, gear: dict) -> bool:
    if skill not in SKILLS:
        return False
    unlock = SKILLS[skill][1]
    stats = fighter_stats(level, gear)
    if isinstance(unlock, int):
        return stats["level"] >= unlock
    kind, value = unlock
    if kind == "weapon":
        return stats["weapon_kind"] in value
    return bool((gear or {}).get("tool")) and stats["special"] == value


def unlocked_skills(level: int, gear: dict) -> list:
    return [s for s in SKILLS if skill_unlocked(s, level, gear)]


def clean_skills(skills, level: int, gear: dict) -> list:
    """Exactly 3 usable skills (same rules as the server): your picks first, then the basics."""
    out = []
    for skill in skills or []:
        if skill not in out and skill_unlocked(skill, level, gear):
            out.append(skill)
        if len(out) == 3:
            break
    for skill in DEFAULT_SKILLS:
        if len(out) == 3:
            break
        if skill not in out:
            out.append(skill)
    return out


def stat_breakdown(level: int, gear: dict) -> dict:
    """{stat: (from level, from equipment)} - shows where each number comes from."""
    base = fighter_stats(level, {})
    full = fighter_stats(level, gear)
    return {k: (base[k], round(full[k] - base[k], 1)) for k in ("hp", "atk", "def", "crit", "spd")}


PREMIUM_DAILY_COINS = 30          # Premium: bonus coins every day you use Pips
PREMIUM_INTERACTION_CAP = 40      # Premium: daily cap for friend-interaction coins (normal: 20)
TALL_MOVES = {"spider": 230, "fireworks": 70, "rocket": 240, "juggle": 50, "magic": 40, "confetti": 60, "rain_dance": 50, "hearts": 40}      # extra canvas height (px) the move needs above the pet


# =============================================================================
# Wallet & inventory
# =============================================================================

def earned_coins(stats: dict, achievements: int, login_days: int, affinity_levels: int, bonus: int,
                 premium: int = 0, arena: int = 0) -> dict:
    """Breakdown of every coin you have earned so far."""
    parts = {
        "premium": int(premium),
        "arena": int(arena),
        "gift": WELCOME_GIFT,
        "pomodoros": COINS_PER_POMODORO * int(stats.get("total_poms", 0)),
        "levels": COINS_PER_LEVEL * max(0, int(stats.get("level", 1)) - 1),
        "achievements": COINS_PER_ACHIEVEMENT * int(achievements),
        "days": COINS_PER_DAY * int(login_days),
        "friends": COINS_PER_AFFINITY_LEVEL * int(affinity_levels),
        "social": int(bonus),
    }
    parts["total"] = sum(parts.values())
    return parts


OWNER_COINS = 999999   # shown as ∞ for the app owner


class Shop:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / "data" / "shop.json"
        self.data = self._load()
        self.data.setdefault("owned", [])
        self.data.setdefault("equipped", {})
        self.data.setdefault("spent", 0)
        self.data.setdefault("login_days", [])
        self.data.setdefault("bonus", 0)
        self.data.setdefault("bonus_day", "")
        self.data.setdefault("bonus_today", 0)
        self.data.setdefault("premium_bonus", 0)
        self.data.setdefault("premium_day", "")
        self.unlimited = False          # app owner: everything unlocked, endless coins
        self.premium = False            # Premium / moderators: premium items, daily bonus, higher caps
        self.mark_today()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("shop: could not save %s: %s", self.path, exc)

    def mark_today(self):
        today = date.today().isoformat()
        days = self.data["login_days"]
        if today not in days:
            days.append(today)
            self.data["login_days"] = days[-3650:]
            self.save()

    def add_interaction_bonus(self):
        today = date.today().isoformat()
        if self.data.get("bonus_day") != today:
            self.data["bonus_day"] = today
            self.data["bonus_today"] = 0
        cap = PREMIUM_INTERACTION_CAP if (self.premium or self.unlimited) else INTERACTION_DAILY_CAP
        if self.data["bonus_today"] < cap:
            self.data["bonus_today"] += COINS_PER_INTERACTION
            self.data["bonus"] += COINS_PER_INTERACTION
            self.save()

    def add_premium_daily(self) -> bool:
        """Premium members get PREMIUM_DAILY_COINS once per day (kept even if Premium ends)."""
        today = date.today().isoformat()
        if not self.premium or self.data.get("premium_day") == today:
            return False
        self.data["premium_day"] = today
        self.data["premium_bonus"] = int(self.data.get("premium_bonus", 0)) + PREMIUM_DAILY_COINS
        self.save()
        return True

    def wallet(self, stats: dict, achievements: int, affinity_levels: int, arena: int = 0) -> dict:
        parts = earned_coins(stats, achievements, len(self.data["login_days"]), affinity_levels,
                             self.data.get("bonus", 0), self.data.get("premium_bonus", 0), arena)
        parts["spent"] = int(self.data.get("spent", 0))
        parts["balance"] = parts["total"] - parts["spent"]
        if self.unlimited:
            parts["balance"] = OWNER_COINS
            parts["unlimited"] = True
        return parts

    def owns(self, item: str) -> bool:
        if self.unlimited:
            return True
        if item in PREMIUM_ITEMS and not self.premium:
            return False      # premium items rest while Premium is off (they come back when it's renewed)
        return item in self.data["owned"]

    def can_buy(self, item: str) -> bool:
        return self.unlimited or self.premium or item not in PREMIUM_ITEMS

    def price(self, item: str) -> int:
        if item in GEAR:
            return GEAR[item][1]
        if item in PROPS:
            return PROPS[item][1]
        if item in MOVES:
            return MOVES[item][0]
        raise KeyError(item)

    def buy(self, item: str, balance: int) -> bool:
        if self.owns(item):
            if item in PROPS:
                self.equip(item)
            if item in GEAR:
                self.equip_gear(item)
            return True
        price = self.price(item)
        if price > balance or not self.can_buy(item):
            return False
        self.data["owned"].append(item)
        self.data["spent"] = int(self.data.get("spent", 0)) + price
        if item in PROPS:
            self.equip(item)
        if item in GEAR:
            self.equip_gear(item)
        self.save()
        return True

    def equip(self, item: str):
        if item in PROPS and self.owns(item):
            self.data["equipped"][PROPS[item][0]] = item
            self.save()

    def unequip(self, slot: str):
        self.data["equipped"].pop(slot, None)
        self.save()

    def equipped(self) -> dict:
        return {slot: item for slot, item in self.data["equipped"].items() if item in PROPS and self.owns(item)}

    def moves(self) -> list:
        return [m for m in MOVES if self.owns(m)]

    # ---- arena gear ----
    def equip_gear(self, item: str):
        if item in GEAR and self.owns(item):
            self.data.setdefault("gear", {})[GEAR[item][0]] = item
            self.save()

    def unequip_gear(self, slot: str):
        self.data.setdefault("gear", {}).pop(slot, None)
        self.save()

    def gear(self) -> dict:
        return {slot: item for slot, item in (self.data.get("gear") or {}).items()
                if item in GEAR and GEAR[item][0] == slot and self.owns(item)}

    # ---- gifts (v2.13) ----
    def is_known(self, item: str) -> bool:
        return item in PROPS or item in MOVES or item in GEAR

    def spend(self, amount: int):
        """Pay for something that isn't an item for yourself (e.g. a gift for a friend)."""
        if self.unlimited or amount <= 0:
            return
        self.data["spent"] = int(self.data.get("spent", 0)) + int(amount)
        self.save()

    def add_gift_coins(self, amount: int):
        if amount > 0:
            self.data["bonus"] = int(self.data.get("bonus", 0)) + int(amount)
            self.save()

    def receive_item(self, item: str) -> str:
        """Add a gifted item. Returns 'new', or 'coins' if it was already owned / unknown (refunded as coins)."""
        if self.is_known(item) and item not in self.data["owned"]:
            self.data["owned"].append(item)
            self.save()
            return "new"
        if self.is_known(item):
            self.add_gift_coins(self.price(item))
        return "coins"


def clean_look(look) -> dict:
    """Validate a look dict coming from the server (friends' props)."""
    if not isinstance(look, dict):
        return {}
    out = {}
    for slot in SLOTS:
        item = look.get(slot)
        if isinstance(item, str) and item in PROPS and PROPS[item][0] == slot:
            out[slot] = item
    moves = look.get("moves")
    if isinstance(moves, list):
        out["moves"] = [m for m in moves if m in MOVES][:10]
    return out


def clean_traits(look) -> dict:
    """v2.18: the pet's gender / life stage and the owner's gender from a friend's look dict."""
    if not isinstance(look, dict):
        return {}
    out = {}
    if look.get("gender") in ("boy", "girl"):
        out["gender"] = look["gender"]
    if look.get("stage") in ("baby", "child", "teen"):
        out["stage"] = look["stage"]
    if look.get("owner_gender") in ("m", "f"):
        out["owner_gender"] = look["owner_gender"]
    return out


# =============================================================================
# Drawing props
# =============================================================================

def _poly(cv, pts, fill, outline="", width=1, smooth=False):
    cv.create_polygon(*pts, fill=fill, outline=outline, width=width, smooth=smooth, joinstyle="round")


def _oval(cv, x, y, rx, ry, fill, outline="", width=1):
    cv.create_oval(x - rx, y - ry, x + rx, y + ry, fill=fill, outline=outline, width=width)


def draw_back_props(cv, geo: dict, props: dict, t: float = 0.0):
    """Props drawn *behind* the pet (cape, wings). Call before draw_pet."""
    if not geo or not props:
        return
    item = props.get("back")
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    if item == "cape":
        sway = math.sin(t * 2.4) * 3 * u
        side = -geo["facing"]
        pts = [cx - rx * 0.55, cy - ry * 0.35, cx + rx * 0.55, cy - ry * 0.35,
               cx + rx * 0.95 + side * 4 * u + sway, cy + ry * 1.05,
               cx + side * 6 * u + sway, cy + ry * 0.92,
               cx - rx * 0.95 + side * 4 * u + sway, cy + ry * 1.05]
        _poly(cv, pts, "#c0262d", "#7a1418", max(1, 1.2 * u), smooth=True)
    elif item == "wings":
        flap = math.sin(t * 5) * 4 * u
        for s in (-1, 1):
            base_x = cx + s * rx * 0.55
            pts = [base_x, cy - ry * 0.2,
                   cx + s * (rx + 15 * u), cy - ry * 0.95 - flap,
                   cx + s * (rx + 19 * u), cy - ry * 0.25 - flap * 0.5,
                   cx + s * (rx + 13 * u), cy + ry * 0.2,
                   base_x, cy + ry * 0.25]
            _poly(cv, pts, "#ffffff", "#c9d6e8", max(1, 1.2 * u), smooth=True)
            for k in (0.45, 0.7):
                cv.create_line(base_x, cy, cx + s * (rx + 15 * u * k + 4 * u), cy - ry * 0.6 * k - flap * k,
                               fill="#dfe7f2", width=max(1, u))
    elif item == "fairy_wings":
        flap = math.sin(t * 7) * 3 * u
        for s in (-1, 1):
            base_x = cx + s * rx * 0.45
            upper = [base_x, cy - ry * 0.25,
                     cx + s * (rx + 10 * u), cy - ry * 1.15 - flap,
                     cx + s * (rx + 20 * u), cy - ry * 0.75 - flap,
                     cx + s * (rx + 14 * u), cy - ry * 0.05]
            lower = [base_x, cy,
                     cx + s * (rx + 15 * u), cy + ry * 0.15 + flap * 0.5,
                     cx + s * (rx + 10 * u), cy + ry * 0.75 + flap * 0.5,
                     base_x + s * 2 * u, cy + ry * 0.35]
            _poly(cv, upper, "#e5dbff", "#9775fa", max(1, 1.1 * u), smooth=True)
            _poly(cv, lower, "#fcc2d7", "#f06595", max(1, 1.1 * u), smooth=True)
            sx = cx + s * (rx + 12 * u)
            sy = cy - ry * 0.7 - flap
            twinkle = 0.6 + 0.4 * abs(math.sin(t * 4 + s))
            cv.create_text(sx, sy, text="\u2726", fill="#ffd43b",
                           font=("Segoe UI Symbol", max(5, int(4 * u * twinkle))))


    _draw_back_props_v212(cv, geo, props, t)


def _draw_back_props_v212(cv, geo, props, t):
    item = props.get("back")
    if not item:
        return
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    facing = geo["facing"]
    side = -facing
    lw = max(1, 1.2 * u)
    if item == "backpack":
        bx = cx + side * rx * 0.72
        cv.create_rectangle(bx - 7 * u, cy - ry * 0.55, bx + 7 * u, cy + ry * 0.55, fill="#4dabf7",
                            outline="#1864ab", width=lw)
        cv.create_rectangle(bx - 5 * u, cy + ry * 0.05, bx + 5 * u, cy + ry * 0.4, fill="#74c0fc",
                            outline="#1864ab", width=max(1, u))
        cv.create_line(bx - 7 * u, cy - ry * 0.5, bx - 5 * u, cy - ry * 0.75, bx + 5 * u, cy - ry * 0.75,
                       bx + 7 * u, cy - ry * 0.5, fill="#1864ab", width=lw, smooth=True)
    elif item == "balloon":
        bob = math.sin(t * 2.0) * 3 * u
        bx, by = cx + side * (rx + 8 * u), cy - ry - 24 * u + bob
        cv.create_line(cx + side * rx * 0.6, cy, bx - side * 1 * u, by + 10 * u + 2 * u, smooth=True,
                       fill="#868e96", width=max(1, 0.8 * u))
        _oval(cv, bx, by, 8 * u, 10 * u, "#ff6b6b", "#c92a2a", lw)
        _oval(cv, bx - 2.5 * u, by - 4 * u, 1.8 * u, 2.6 * u, "#ffc9c9", "")
        _poly(cv, [bx - 1.5 * u, by + 10 * u, bx + 1.5 * u, by + 10 * u, bx, by + 12 * u], "#c92a2a", "")
    elif item == "guitar":
        gx, gy = cx + side * rx * 0.55, cy + ry * 0.1
        cv.create_line(gx - side * 4 * u, gy + 6 * u, gx + side * 14 * u, gy - ry * 1.15, fill="#5c3d1e",
                       width=max(2, 3 * u))
        _oval(cv, gx - side * 5 * u, gy + 7 * u, 8 * u, 7 * u, "#e8590c", "#7c2d12", lw)
        _oval(cv, gx - side * 1 * u, gy + 1 * u, 6 * u, 5 * u, "#e8590c", "#7c2d12", lw)
        _oval(cv, gx - side * 3.5 * u, gy + 5 * u, 2 * u, 2 * u, "#3b2a20", "")
    elif item == "shield":
        sx, sy = cx + side * rx * 0.85, cy
        pts = [sx - 8 * u, sy - 9 * u, sx + 8 * u, sy - 9 * u, sx + 8 * u, sy + 1 * u, sx, sy + 11 * u,
               sx - 8 * u, sy + 1 * u]
        _poly(cv, pts, "#adb5bd", "#495057", lw)
        _poly(cv, [sx - 5 * u, sy - 6 * u, sx + 5 * u, sy - 6 * u, sx + 5 * u, sy, sx, sy + 7 * u, sx - 5 * u, sy],
              "#e03131", "", 0)
        cv.create_text(sx, sy - 1 * u, text="\u2605", fill="#ffd43b", font=("Segoe UI Symbol", max(5, int(4 * u))))
    elif item in ("butterfly_wings", "devil_wings", "dragon_wings"):
        flap = math.sin(t * (7 if item == "butterfly_wings" else 4)) * 3 * u
        for s in (-1, 1):
            base_x = cx + s * rx * 0.45
            if item == "butterfly_wings":
                _oval(cv, cx + s * (rx + 8 * u), cy - ry * 0.55 - flap, 11 * u, 9 * u, "#ffd43b", "#e67700", lw)
                _oval(cv, cx + s * (rx + 6 * u), cy + ry * 0.3 + flap * 0.5, 8 * u, 7 * u, "#ff922b", "#e67700", lw)
                _oval(cv, cx + s * (rx + 9 * u), cy - ry * 0.6 - flap, 3 * u, 3 * u, "#ffffff", "")
            else:
                col, edge = ("#c92a2a", "#5c0a0a") if item == "devil_wings" else ("#2f9e44", "#0b4d1d")
                span = 16 if item == "devil_wings" else 21
                pts = [base_x, cy - ry * 0.25, cx + s * (rx + span * u), cy - ry * 1.05 - flap,
                       cx + s * (rx + (span - 4) * u), cy - ry * 0.3 - flap * 0.4,
                       cx + s * (rx + (span - 2) * u), cy + ry * 0.15,
                       cx + s * (rx + (span - 8) * u), cy - ry * 0.05,
                       cx + s * (rx + (span - 10) * u), cy + ry * 0.35, base_x, cy + ry * 0.2]
                _poly(cv, pts, col, edge, lw)
    elif item == "galaxy_cape":
        sway = math.sin(t * 2.4) * 3 * u
        pts = [cx - rx * 0.55, cy - ry * 0.35, cx + rx * 0.55, cy - ry * 0.35,
               cx + rx * 0.95 + side * 4 * u + sway, cy + ry * 1.05, cx + side * 6 * u + sway, cy + ry * 0.92,
               cx - rx * 0.95 + side * 4 * u + sway, cy + ry * 1.05]
        _poly(cv, pts, "#2b2d6e", "#7048e8", max(1, 1.4 * u), smooth=True)
        for k in range(7):
            sx = cx + math.sin(k * 2.7) * rx * 0.75 + sway * 0.5
            sy = cy + ry * (0.0 + 0.14 * k)
            tw = 0.5 + 0.5 * abs(math.sin(t * 3 + k))
            _oval(cv, sx, sy, 0.9 * u * tw + 0.4 * u, 0.9 * u * tw + 0.4 * u, "#fff3bf", "")


def draw_front_props(cv, geo: dict, props: dict, t: float = 0.0):
    """Props drawn *on top of* the pet (hats, glasses, neckwear). Call after draw_pet."""
    if not geo or not props:
        return
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    fx = geo.get("feature_dx", 0.0)
    facing = geo["facing"]
    top = cy - ry
    lw = max(1, 1.2 * u)
    eye_y = geo.get("eye_y", cy - 1.5 * u)
    sep = geo.get("eye_sep", 11 * u)
    hx = cx + fx * 0.6

    neck = props.get("neck")
    neck_y = cy + ry * 0.48
    if neck == "bowtie":
        _poly(cv, [hx, neck_y, hx - 7 * u, neck_y - 4.5 * u, hx - 7 * u, neck_y + 4.5 * u], "#d6336c", "#8f1d45", lw)
        _poly(cv, [hx, neck_y, hx + 7 * u, neck_y - 4.5 * u, hx + 7 * u, neck_y + 4.5 * u], "#d6336c", "#8f1d45", lw)
        _oval(cv, hx, neck_y, 2 * u, 2 * u, "#f06595", "#8f1d45", lw)
    elif neck == "bell":
        cv.create_line(hx - rx * 0.62, neck_y - 2 * u, hx + rx * 0.62, neck_y - 2 * u, fill="#d6336c",
                       width=max(2, 2.6 * u), capstyle="round")
        _oval(cv, hx, neck_y + 2.5 * u, 3.6 * u, 3.6 * u, "#f5c518", "#a37f00", lw)
        cv.create_line(hx, neck_y + 3 * u, hx, neck_y + 5.5 * u, fill="#a37f00", width=lw)
    elif neck == "scarf":
        cv.create_line(hx - rx * 0.7, neck_y - 2 * u, hx + rx * 0.7, neck_y - 2 * u, fill="#2f9e44",
                       width=max(3, 5 * u), capstyle="round")
        side = -facing
        _poly(cv, [hx + side * rx * 0.25, neck_y - 1 * u, hx + side * rx * 0.48, neck_y - 1 * u,
                   hx + side * rx * 0.55, neck_y + 12 * u, hx + side * rx * 0.3, neck_y + 11 * u],
              "#37b24d", "#1f6f30", lw)
        for k in (-0.4, 0.0, 0.4):
            cv.create_line(hx + rx * k, neck_y - 4 * u, hx + rx * k, neck_y, fill="#b2f2bb", width=max(1, u))
    elif neck == "medal":
        _poly(cv, [hx - 6 * u, neck_y - 5 * u, hx - 1 * u, neck_y + 5 * u, hx + 1 * u, neck_y + 5 * u,
                   hx + 6 * u, neck_y - 5 * u, hx + 3 * u, neck_y - 5 * u, hx, neck_y + 1 * u,
                   hx - 3 * u, neck_y - 5 * u], "#1c7ed6", "#0b4f8c", lw)
        _oval(cv, hx, neck_y + 8 * u, 4.4 * u, 4.4 * u, "#fab005", "#a36a00", lw)
        cv.create_text(hx, neck_y + 8 * u, text="1", font=("Segoe UI", max(5, int(4.5 * u)), "bold"),
                       fill="#7a4f00")
    elif neck == "diamond":
        chain = []
        for k in range(9):
            a = math.pi * k / 8
            chain += [hx - math.cos(a) * rx * 0.6, neck_y - 3 * u + math.sin(a) * 3 * u]
        cv.create_line(*chain, fill="#ced4da", width=max(1, 1.2 * u), smooth=True)
        gy = neck_y + 2.5 * u
        _poly(cv, [hx - 4 * u, gy, hx - 2 * u, gy - 2.6 * u, hx + 2 * u, gy - 2.6 * u, hx + 4 * u, gy,
                   hx, gy + 5 * u], "#a5d8ff", "#1971c2", lw)
        cv.create_line(hx - 4 * u, gy, hx + 4 * u, gy, fill="#1971c2", width=max(1, 0.8 * u))
        cv.create_line(hx - 1.5 * u, gy - 2 * u, hx - 0.5 * u, gy - 0.6 * u, fill="#ffffff", width=max(1, 0.8 * u))

    face = props.get("face")
    if face == "shades":
        for s in (-1, 1):
            ex = cx + fx + s * sep
            cv.create_rectangle(ex - 5.5 * u, eye_y - 3.2 * u, ex + 5.5 * u, eye_y + 3.2 * u,
                                fill="#1a1a1a", outline="#000000", width=lw)
            cv.create_line(ex - 3.5 * u, eye_y - 1.8 * u, ex - 1 * u, eye_y - 1.8 * u, fill="#6b6b6b",
                           width=max(1, u))
        cv.create_line(cx + fx - sep + 5.5 * u, eye_y - 1 * u, cx + fx + sep - 5.5 * u, eye_y - 1 * u,
                       fill="#000000", width=lw)
    elif face == "heart_glasses":
        for s in (-1, 1):
            ex = cx + fx + s * sep
            r = 4.2 * u
            pts = [ex, eye_y + r * 1.1, ex - r * 1.3, eye_y - r * 0.1, ex - r * 0.7, eye_y - r * 1.0,
                   ex, eye_y - r * 0.45, ex + r * 0.7, eye_y - r * 1.0, ex + r * 1.3, eye_y - r * 0.1]
            _poly(cv, pts, "#ff6b9a", "#c2255c", lw, smooth=True)
        cv.create_line(cx + fx - sep + 4 * u, eye_y - 1.5 * u, cx + fx + sep - 4 * u, eye_y - 1.5 * u,
                       fill="#c2255c", width=lw)
    elif face == "monocle":
        ex = cx + fx + facing * sep
        _oval(cv, ex, eye_y, 4.8 * u, 4.8 * u, "", "#c99a2e", max(1.5, 1.6 * u))
        cv.create_line(ex + facing * 3 * u, eye_y + 4 * u, ex + facing * 6 * u, eye_y + 14 * u,
                       fill="#c99a2e", width=max(1, 0.9 * u))
    elif face == "star_glasses":
        for s in (-1, 1):
            ex = cx + fx + s * sep
            r_out, r_in = 5.4 * u, 2.4 * u
            pts = []
            for k in range(10):
                a = -math.pi / 2 + k * math.pi / 5
                r = r_out if k % 2 == 0 else r_in
                pts += [ex + math.cos(a) * r, eye_y + math.sin(a) * r]
            _poly(cv, pts, "#ffd43b", "#e67700", lw)
            _oval(cv, ex, eye_y, 1.6 * u, 1.6 * u, "#7048e8", "")
        cv.create_line(cx + fx - sep + 4 * u, eye_y - 1 * u, cx + fx + sep - 4 * u, eye_y - 1 * u,
                       fill="#e67700", width=lw)

    head = props.get("head")
    if head == "party_hat":
        apex_x = hx + 2 * u
        _poly(cv, [hx - 8 * u, top + 3 * u, hx + 8 * u, top + 3 * u, apex_x, top - 15 * u],
              "#845ef7", "#5f3dc4", lw)
        for k in (0.3, 0.6):
            y = top + 3 * u - 18 * u * k
            half = 8 * u * (1 - k)
            cv.create_line(hx - half + (apex_x - hx) * k, y, hx + half + (apex_x - hx) * k, y, fill="#ffd43b",
                           width=max(1, 1.6 * u))
        _oval(cv, apex_x, top - 15 * u, 2.6 * u, 2.6 * u, "#ff6b6b", "#c92a2a", lw)
    elif head == "flower":
        fxp = hx + facing * rx * 0.5
        fyp = top + 3 * u
        for k in range(5):
            a = k * 2 * math.pi / 5 + t * 0.5
            _oval(cv, fxp + math.cos(a) * 3.2 * u, fyp + math.sin(a) * 3.2 * u, 2.6 * u, 2.6 * u,
                  "#ff8fab", "#d6336c", max(1, 0.8 * u))
        _oval(cv, fxp, fyp, 2.2 * u, 2.2 * u, "#ffd43b", "#e8a200", max(1, 0.8 * u))
    elif head == "cap":
        pts = []
        for k in range(13):
            a = math.pi * k / 12
            pts += [hx - math.cos(a) * rx * 0.72, top + 4 * u - math.sin(a) * 9 * u]
        _poly(cv, pts, "#1c7ed6", "#0b4f8c", lw, smooth=True)
        visor_x = hx + facing * rx * 0.72
        _poly(cv, [hx + facing * rx * 0.3, top + 4 * u, visor_x + facing * 9 * u, top + 5.5 * u,
                   visor_x + facing * 8 * u, top + 3 * u, hx + facing * rx * 0.4, top + 2 * u],
              "#1864ab", "#0b4f8c", lw)
        _oval(cv, hx, top - 5 * u, 1.6 * u, 1.6 * u, "#ffffff", "")
    elif head == "beanie":
        pts = []
        for k in range(13):
            a = math.pi * k / 12
            pts += [hx - math.cos(a) * rx * 0.78, top + 5 * u - math.sin(a) * 11 * u]
        _poly(cv, pts, "#f76707", "#b34700", lw, smooth=True)
        cv.create_rectangle(hx - rx * 0.8, top + 2.5 * u, hx + rx * 0.8, top + 6.5 * u, fill="#ffa94d",
                            outline="#b34700", width=lw)
        _oval(cv, hx, top - 7 * u, 3.4 * u, 3.4 * u, "#fff4e6", "#b34700", lw)
    elif head == "headphones":
        cv.create_arc(hx - rx * 0.95, top - 6 * u, hx + rx * 0.95, cy + ry * 0.2, start=15, extent=150,
                      style="arc", outline="#343a40", width=max(2, 2.6 * u))
        for s in (-1, 1):
            ex = hx + s * rx * 0.93
            _oval(cv, ex, cy - ry * 0.35, 3.6 * u, 5.4 * u, "#e64980", "#343a40", lw)
    elif head == "wizard":
        _oval(cv, hx, top + 3 * u, rx * 0.9, 3.4 * u, "#3b5bdb", "#1c2f8a", lw)
        _poly(cv, [hx - rx * 0.5, top + 2.5 * u, hx + rx * 0.5, top + 2.5 * u, hx + 7 * u, top - 22 * u],
              "#4263eb", "#1c2f8a", lw)
        for sx, sy in ((-0.15, -6), (0.15, -13), (0.0, 0)):
            cv.create_text(hx + rx * sx, top + sy * u, text="\u2605", fill="#ffd43b",
                           font=("Segoe UI Symbol", max(5, int(4.5 * u))))
    elif head == "halo":
        bob = math.sin(t * 2.5) * 1.5 * u
        _oval(cv, hx, top - 7 * u + bob, rx * 0.55, 2.6 * u, "", "#fcc419", max(2, 2.4 * u))
    elif head == "crown":
        y0 = top + 3 * u
        w = rx * 0.62
        pts = [hx - w, y0, hx - w, y0 - 9 * u, hx - w * 0.5, y0 - 4 * u, hx, y0 - 11 * u,
               hx + w * 0.5, y0 - 4 * u, hx + w, y0 - 9 * u, hx + w, y0]
        _poly(cv, pts, "#fcc419", "#b8860b", lw)
        for gx, col in ((-0.5, "#e03131"), (0.0, "#1c7ed6"), (0.5, "#2f9e44")):
            _oval(cv, hx + w * gx, y0 - 2.5 * u, 1.5 * u, 1.5 * u, col, "")
    elif head == "tiara":
        y0 = top + 3.5 * u
        w = rx * 0.55
        arc = []
        for k in range(11):
            a = math.pi * k / 10
            arc += [hx - math.cos(a) * w, y0 - math.sin(a) * 4 * u]
        cv.create_line(*arc, fill="#dee2e6", width=max(2, 2 * u), smooth=True)
        for gx, h in ((-0.6, 4.5), (-0.3, 6.5), (0.0, 9.0), (0.3, 6.5), (0.6, 4.5)):
            px = hx + w * gx
            base = y0 - math.sqrt(max(0.0, 1 - gx * gx)) * 4 * u
            _poly(cv, [px - 1.6 * u, base, px, base - h * u, px + 1.6 * u, base], "#f1f3f5", "#adb5bd",
                  max(1, 0.8 * u))
        sparkle = 1.2 + 0.5 * abs(math.sin(t * 3))
        _oval(cv, hx, y0 - 6.5 * u, 1.9 * u, 1.9 * u, "#f783ac", "#c2255c", max(1, 0.8 * u))
        _oval(cv, hx - w * 0.3, y0 - 5.2 * u, sparkle * u * 0.8, sparkle * u * 0.8, "#99e9f2", "")
        _oval(cv, hx + w * 0.3, y0 - 5.2 * u, sparkle * u * 0.8, sparkle * u * 0.8, "#99e9f2", "")


    _draw_front_props_v212(cv, geo, props, t)


def _draw_front_props_v212(cv, geo, props, t):
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    fx = geo.get("feature_dx", 0.0)
    facing = geo["facing"]
    top = cy - ry
    lw = max(1, 1.2 * u)
    eye_y = geo.get("eye_y", cy - 1.5 * u)
    sep = geo.get("eye_sep", 11 * u)
    hx = cx + fx * 0.6
    neck_y = cy + ry * 0.48
    mouth_y = eye_y + 5.5 * u

    neck = props.get("neck")
    if neck == "bandana":
        _poly(cv, [hx - rx * 0.62, neck_y - 3 * u, hx + rx * 0.62, neck_y - 3 * u, hx, neck_y + 9 * u],
              "#e03131", "#8f1d1d", lw)
        for k in (-0.3, 0.0, 0.3):
            _oval(cv, hx + rx * k * 0.8, neck_y + 0.5 * u, 0.9 * u, 0.9 * u, "#ffffff", "")
    elif neck == "tie":
        _poly(cv, [hx - 2.5 * u, neck_y - 3 * u, hx + 2.5 * u, neck_y - 3 * u, hx + 1.5 * u, neck_y],
              "#1c7ed6", "#0b4f8c", lw)
        _poly(cv, [hx - 1.5 * u, neck_y, hx + 1.5 * u, neck_y, hx + 3.5 * u, neck_y + 11 * u, hx,
                   neck_y + 14 * u, hx - 3.5 * u, neck_y + 11 * u], "#1c7ed6", "#0b4f8c", lw)
        cv.create_line(hx - 2.5 * u, neck_y + 4 * u, hx + 2.8 * u, neck_y + 7 * u, fill="#74c0fc", width=max(1, u))
    elif neck == "lei":
        colors = ("#ff8fab", "#ffd43b", "#ff6b6b", "#b197fc")
        for k in range(9):
            a = math.pi * k / 8
            px = hx - math.cos(a) * rx * 0.66
            py = neck_y - 2 * u + math.sin(a) * 4.5 * u
            _oval(cv, px, py, 2.6 * u, 2.6 * u, colors[k % 4], "", 0)
            _oval(cv, px, py, 0.8 * u, 0.8 * u, "#ffffff", "")
    elif neck == "pearls":
        for k in range(11):
            a = math.pi * k / 10
            _oval(cv, hx - math.cos(a) * rx * 0.62, neck_y - 2.5 * u + math.sin(a) * 4 * u, 1.6 * u, 1.6 * u,
                  "#f8f9fa", "#ced4da", max(1, 0.6 * u))
    elif neck == "gold_chain":
        chain = []
        for k in range(13):
            a = math.pi * k / 12
            chain += [hx - math.cos(a) * rx * 0.62, neck_y - 2 * u + math.sin(a) * 5 * u]
        cv.create_line(*chain, fill="#fab005", width=max(2, 2.2 * u), smooth=True)
        cv.create_line(*chain, fill="#ffe066", width=max(1, 0.8 * u), smooth=True, dash=(2, 2))
        _oval(cv, hx, neck_y + 5 * u, 3.6 * u, 3.6 * u, "#fab005", "#a36a00", lw)
        cv.create_text(hx, neck_y + 5 * u, text="$", fill="#7a4f00", font=("Segoe UI", max(5, int(4 * u)), "bold"))

    face = props.get("face")
    if face == "nerd_glasses":
        for s in (-1, 1):
            ex = cx + fx + s * sep
            cv.create_rectangle(ex - 4.6 * u, eye_y - 3.6 * u, ex + 4.6 * u, eye_y + 3.6 * u, outline="#212529",
                                width=max(2, 1.8 * u))
        cv.create_line(cx + fx - sep + 4.6 * u, eye_y - 1 * u, cx + fx + sep - 4.6 * u, eye_y - 1 * u,
                       fill="#212529", width=max(2, 1.6 * u))
        cv.create_rectangle(cx + fx - 1.2 * u, eye_y - 2 * u, cx + fx + 1.2 * u, eye_y, fill="#ffffff", outline="")
    elif face == "eyepatch":
        ex = cx + fx + facing * sep
        cv.create_line(cx - rx * 0.9, eye_y - 5 * u, cx + rx * 0.9, eye_y - 1 * u, fill="#212529", width=max(1, u))
        _oval(cv, ex, eye_y, 4.4 * u, 4.0 * u, "#212529", "#000000", lw)
    elif face == "mustache":
        my = mouth_y - 1.5 * u
        for s in (-1, 1):
            _poly(cv, [hx + fx * 0.4, my, hx + fx * 0.4 + s * 3 * u, my - 2 * u, hx + fx * 0.4 + s * 8 * u,
                       my - 1 * u, hx + fx * 0.4 + s * 9.5 * u, my - 3.5 * u, hx + fx * 0.4 + s * 9 * u, my + 1 * u,
                       hx + fx * 0.4 + s * 4 * u, my + 1.5 * u], "#3b2a20", "", 0, smooth=True)
    elif face == "goggles":
        cv.create_line(cx - rx * 0.95, eye_y, cx + rx * 0.95, eye_y, fill="#495057", width=max(2, 2.4 * u))
        for s in (-1, 1):
            ex = cx + fx + s * sep
            _oval(cv, ex, eye_y, 5.4 * u, 5 * u, "#a5d8ff", "#495057", max(2, 1.8 * u))
            _oval(cv, ex - 1.6 * u, eye_y - 1.6 * u, 1.2 * u, 1.2 * u, "#ffffff", "")
    elif face == "clown_nose":
        _oval(cv, hx + fx * 0.4, mouth_y - 2.5 * u, 3.2 * u, 3.2 * u, "#ff2b2b", "#a61e1e", lw)
        _oval(cv, hx + fx * 0.4 - 1 * u, mouth_y - 3.5 * u, 0.9 * u, 0.9 * u, "#ffffff", "")
    elif face == "masquerade":
        pts = [cx + fx - sep - 7 * u, eye_y - 3 * u, cx + fx - sep, eye_y - 4.5 * u, cx + fx, eye_y - 2 * u,
               cx + fx + sep, eye_y - 4.5 * u, cx + fx + sep + 7 * u, eye_y - 3 * u, cx + fx + sep + 5 * u,
               eye_y + 3.5 * u, cx + fx + sep, eye_y + 4 * u, cx + fx, eye_y + 1.5 * u, cx + fx - sep,
               eye_y + 4 * u, cx + fx - sep - 5 * u, eye_y + 3.5 * u]
        _poly(cv, pts, "#7048e8", "#3b1f8c", lw, smooth=True)
        for s in (-1, 1):
            _oval(cv, cx + fx + s * sep, eye_y, 2.6 * u, 2.2 * u, "#1a1a1a", "")
        cv.create_line(cx + fx + sep + 6 * u, eye_y - 3 * u, cx + fx + sep + 10 * u, eye_y - 10 * u,
                       fill="#ffd43b", width=max(1, 1.2 * u))

    head = props.get("head")
    if head == "bow":
        bx, by = hx + facing * rx * 0.45, top + 2 * u
        _poly(cv, [bx, by, bx - 7 * u, by - 5 * u, bx - 7 * u, by + 4 * u], "#f06595", "#a61e4d", lw)
        _poly(cv, [bx, by, bx + 7 * u, by - 5 * u, bx + 7 * u, by + 4 * u], "#f06595", "#a61e4d", lw)
        _oval(cv, bx, by, 2.2 * u, 2.2 * u, "#ff8fab", "#a61e4d", lw)
    elif head == "beret":
        _oval(cv, hx - facing * 2 * u, top + 1 * u, rx * 0.72, 5 * u, "#c92a2a", "#7a1414", lw)
        cv.create_line(hx - facing * 2 * u, top - 4 * u, hx - facing * 1 * u, top - 7 * u, fill="#7a1414",
                       width=max(1, 1.4 * u))
    elif head == "propeller_cap":
        pts = []
        for k in range(13):
            a = math.pi * k / 12
            pts += [hx - math.cos(a) * rx * 0.66, top + 4 * u - math.sin(a) * 8 * u]
        _poly(cv, pts, "#fcc419", "#b8860b", lw, smooth=True)
        cv.create_line(hx - rx * 0.66, top + 4 * u, hx, top - 4 * u, fill="#e03131", width=max(1, 1.5 * u))
        cv.create_line(hx + rx * 0.66, top + 4 * u, hx, top - 4 * u, fill="#1c7ed6", width=max(1, 1.5 * u))
        cv.create_line(hx, top - 4 * u, hx, top - 7 * u, fill="#495057", width=max(1, u))
        spin = math.cos(t * 14)
        cv.create_line(hx - 8 * u * spin, top - 7.5 * u, hx + 8 * u * spin, top - 7.5 * u, fill="#e03131",
                       width=max(2, 2.2 * u), capstyle="round")
    elif head == "flower_crown":
        colors = ("#ff8fab", "#ffd43b", "#b197fc", "#69db7c", "#ff922b")
        for k in range(7):
            a = math.pi * (0.12 + 0.76 * k / 6)
            px, py = hx - math.cos(a) * rx * 0.66, top + 4 * u - math.sin(a) * 5 * u
            _oval(cv, px, py, 2.8 * u, 2.8 * u, colors[k % 5], darken_hex(colors[k % 5]), max(1, 0.7 * u))
            _oval(cv, px, py, 1 * u, 1 * u, "#fff3bf", "")
    elif head == "chef_hat":
        cv.create_rectangle(hx - rx * 0.5, top - 2 * u, hx + rx * 0.5, top + 4 * u, fill="#ffffff",
                            outline="#adb5bd", width=lw)
        for dx, r in ((-0.35, 6.5), (0.0, 8), (0.35, 6.5)):
            _oval(cv, hx + rx * dx, top - 8 * u, r * u, r * u, "#ffffff", "#adb5bd", lw)
        cv.create_rectangle(hx - rx * 0.48, top - 5 * u, hx + rx * 0.48, top + 3 * u, fill="#ffffff", outline="")
    elif head == "cowboy_hat":
        _oval(cv, hx, top + 2 * u, rx * 1.0, 3.8 * u, "#a0522d", "#5c2e14", lw)
        pts = [hx - rx * 0.45, top + 2 * u, hx - rx * 0.4, top - 10 * u, hx, top - 7 * u, hx + rx * 0.4,
               top - 10 * u, hx + rx * 0.45, top + 2 * u]
        _poly(cv, pts, "#b5651d", "#5c2e14", lw, smooth=True)
        cv.create_line(hx - rx * 0.44, top - 1 * u, hx + rx * 0.44, top - 1 * u, fill="#3b2a20", width=max(2, 2 * u))
    elif head == "santa_hat":
        tip_x = hx - facing * rx * 0.9
        _poly(cv, [hx - rx * 0.62, top + 3 * u, hx + rx * 0.62, top + 3 * u, hx + rx * 0.1, top - 13 * u,
                   tip_x, top - 6 * u], "#e03131", "#8f1d1d", lw, smooth=True)
        cv.create_rectangle(hx - rx * 0.66, top + 0.5 * u, hx + rx * 0.66, top + 5.5 * u, fill="#ffffff",
                            outline="#dee2e6", width=lw)
        _oval(cv, tip_x, top - 6 * u, 3 * u, 3 * u, "#ffffff", "#dee2e6", lw)
    elif head == "grad_cap":
        _poly(cv, [hx - rx * 0.4, top + 4 * u, hx + rx * 0.4, top + 4 * u, hx + rx * 0.4, top - 2 * u,
                   hx - rx * 0.4, top - 2 * u], "#212529", "#000000", lw)
        _poly(cv, [hx - rx * 0.85, top - 3 * u, hx, top - 8 * u, hx + rx * 0.85, top - 3 * u, hx, top + 1.5 * u],
              "#343a40", "#000000", lw)
        swing = math.sin(t * 2.5) * 1.5 * u
        cv.create_line(hx, top - 3 * u, hx + facing * rx * 0.75, top - 1 * u, hx + facing * rx * 0.78 + swing,
                       top + 7 * u, fill="#fcc419", width=max(1, 1.2 * u))
    elif head == "pirate_hat":
        pts = [hx - rx * 0.9, top + 3 * u, hx - rx * 0.6, top - 7 * u, hx, top - 11 * u, hx + rx * 0.6,
               top - 7 * u, hx + rx * 0.9, top + 3 * u, hx, top - 1 * u]
        _poly(cv, pts, "#212529", "#000000", lw, smooth=True)
        cv.create_text(hx, top - 5 * u, text="\u2620", fill="#f8f9fa", font=("Segoe UI Symbol", max(5, int(5 * u))))
    elif head == "top_hat":
        _oval(cv, hx, top + 2.5 * u, rx * 0.85, 3 * u, "#212529", "#000000", lw)
        cv.create_rectangle(hx - rx * 0.48, top - 15 * u, hx + rx * 0.48, top + 2 * u, fill="#212529",
                            outline="#000000", width=lw)
        cv.create_rectangle(hx - rx * 0.48, top - 3 * u, hx + rx * 0.48, top, fill="#c92a2a", outline="")
    elif head == "viking_helmet":
        pts = []
        for k in range(13):
            a = math.pi * k / 12
            pts += [hx - math.cos(a) * rx * 0.74, top + 5 * u - math.sin(a) * 10 * u]
        _poly(cv, pts, "#adb5bd", "#495057", lw, smooth=True)
        cv.create_rectangle(hx - rx * 0.76, top + 2 * u, hx + rx * 0.76, top + 6 * u, fill="#c99a2e",
                            outline="#7a5c12", width=lw)
        for s in (-1, 1):
            bx = hx + s * rx * 0.7
            _poly(cv, [bx, top + 1 * u, bx + s * 9 * u, top - 4 * u, bx + s * 11 * u, top - 14 * u,
                       bx + s * 5 * u, top - 4 * u, bx - s * 1 * u, top - 3 * u], "#fff4e6", "#a08060", lw,
                  smooth=True)
    elif head == "galaxy_crown":
        y0 = top + 3 * u
        w = rx * 0.62
        pts = [hx - w, y0, hx - w, y0 - 9 * u, hx - w * 0.5, y0 - 4 * u, hx, y0 - 12 * u,
               hx + w * 0.5, y0 - 4 * u, hx + w, y0 - 9 * u, hx + w, y0]
        _poly(cv, pts, "#5f3dc4", "#e599f7", lw)
        for k, gx in enumerate((-0.6, -0.2, 0.2, 0.6)):
            tw = 0.6 + 0.4 * abs(math.sin(t * 3 + k))
            _oval(cv, hx + w * gx, y0 - 3 * u, 1.3 * u * tw, 1.3 * u * tw, "#fff3bf", "")
        cv.create_text(hx, y0 - 15 * u, text="\u2726", fill="#ffd43b",
                       font=("Segoe UI Symbol", max(5, int((4 + abs(math.sin(t * 2)) * 2) * u))))


def darken_hex(color: str, amount: float = 0.3) -> str:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r * (1 - amount)), int(g * (1 - amount)), int(b * (1 - amount)))


def draw_props_preview(cv, pet_mod, size, species, color, props, cx=None, floor=None, t=0.0, traits=None):
    """Small helper for the shop / avatars: pet + props on a canvas (traits: clean_traits() of a look)."""
    traits = traits or {}
    look = pet_mod.default_look({"species": species if species in pet_mod.SPECIES else "cat",
                                 "color": color or "#ffc78f", "cheek_color": "#ff8fa3",
                                 "gender": traits.get("gender", ""), "stage": traits.get("stage", "adult")})
    look["t"] = t
    look["mouth"] = "smile"
    look["head_prop"] = bool((props or {}).get("head"))
    tama_mod = getattr(pet_mod, "tama", None)
    if tama_mod is not None:
        size = size * tama_mod.stage_scale(look.get("stage"))
    cx = cx if cx is not None else int(cv["width"]) / 2
    floor = floor if floor is not None else int(cv["height"]) - 2
    geo = pet_mod.body_geometry(cx, floor, size, look)
    draw_back_props(cv, geo, props, t)
    geo = pet_mod.draw_pet(cv, cx, floor, size, look) or geo
    draw_front_props(cv, geo, props, t)


# =============================================================================
# Drawing arena gear (weapons, armor, gadgets) - used by the arena and the shop
# =============================================================================

ARMOR_COLORS = {
    "cardboard_armor": ("#c8a46e", "#7d5f30"),
    "leather_armor": ("#8a5a3b", "#4f2f1b"),
    "knight_armor": ("#c9d1d9", "#6c7680"),
    "ninja_suit": ("#2b2d42", "#0d0e1a"),
    "mech_suit": ("#4c6ef5", "#1c2f8a"),
}


def draw_gear_back(cv, geo: dict, gear: dict, t: float = 0.0):
    """Gear drawn behind the pet (jetpack)."""
    if not geo or not gear:
        return
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    back = -geo["facing"]
    if gear.get("tool") == "jetpack":
        for k in (0, 1):
            x = cx + back * (rx * 0.75 + k * 6 * u)
            cv.create_rectangle(x - 3 * u, cy - ry * 0.6, x + 3 * u, cy + ry * 0.3, fill="#adb5bd",
                                outline="#495057", width=max(1, u))
            flame = 4 + 3 * abs(math.sin(t * 14 + k))
            _poly(cv, [x - 2.5 * u, cy + ry * 0.3, x + 2.5 * u, cy + ry * 0.3, x, cy + ry * 0.3 + flame * u],
                  "#ff922b", "")


def draw_gear_front(cv, geo: dict, gear: dict, t: float = 0.0, aim: float = 0.0):
    """Armor plate, the held weapon and the gadget. `aim` (0..1) raises the weapon for an attack."""
    if not geo or not gear:
        return
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    face = geo["facing"]
    lw = max(1, 1.2 * u)
    armor = gear.get("armor")
    if armor in ARMOR_COLORS:
        fill, line = ARMOR_COLORS[armor]
        pts = []
        for k in range(13):
            a = math.pi * k / 12
            pts += [cx - math.cos(a) * rx * 0.78, cy + ry * 0.05 + math.sin(a) * ry * 0.82]
        _poly(cv, pts, fill, line, lw, smooth=True)
        if armor == "knight_armor":
            cv.create_line(cx, cy + ry * 0.1, cx, cy + ry * 0.8, fill=line, width=lw)
            cv.create_line(cx - rx * 0.4, cy + ry * 0.35, cx + rx * 0.4, cy + ry * 0.35, fill=line, width=lw)
        elif armor == "mech_suit":
            _oval(cv, cx, cy + ry * 0.4, 4 * u, 4 * u, "#99e9f2", "#1c7ed6", lw)
        elif armor == "ninja_suit":
            cv.create_line(cx - rx * 0.7, cy + ry * 0.3, cx + rx * 0.7, cy + ry * 0.3, fill="#e03131",
                           width=max(2, 2.2 * u))
        elif armor == "cardboard_armor":
            cv.create_text(cx, cy + ry * 0.45, text="\u2191", fill=line, font=("Segoe UI", max(5, int(4 * u)), "bold"))
    weapon = gear.get("weapon")
    hx = cx + face * rx * 0.92
    hy = cy + ry * 0.2 - aim * ry * 0.35
    if weapon:
        _oval(cv, hx, hy, 3 * u, 3 * u, "#ffe8cc", "#a35b00", max(1, 0.8 * u))
    if weapon in ("water_pistol", "bubble_blaster", "laser_blaster", "plasma_cannon"):
        spec = {"water_pistol": ("#4dabf7", "#1864ab", 9, 3), "bubble_blaster": ("#f783ac", "#a61e4d", 11, 4),
                "laser_blaster": ("#868e96", "#212529", 13, 3.5), "plasma_cannon": ("#9775fa", "#5f3dc4", 15, 5)}[weapon]
        body, line, length, thick = spec
        x0, x1 = hx - face * 2 * u, hx + face * length * u
        cv.create_rectangle(min(x0, x1), hy - thick * u, max(x0, x1), hy + 0.6 * u, fill=body, outline=line, width=lw)
        cv.create_rectangle(hx - 1.5 * u, hy, hx + 1.5 * u, hy + 5 * u, fill=line, outline=line)
        if weapon == "laser_blaster":
            _oval(cv, x1, hy - thick * u / 2, 1.4 * u, 1.4 * u, "#ff6b6b", "")
        if weapon == "plasma_cannon":
            glow = 1.2 + abs(math.sin(t * 6))
            _oval(cv, x1, hy - thick * u / 2, glow * u, glow * u, "#e5dbff", "#9775fa")
        if weapon == "bubble_blaster":
            _oval(cv, hx - face * 1 * u, hy - thick * u - 2.5 * u, 2.5 * u, 2.5 * u, "#ffdeeb", "#a61e4d", max(1, 0.8 * u))
    elif weapon == "slingshot":
        cv.create_line(hx, hy + 5 * u, hx, hy, fill="#8a5a3b", width=max(2, 1.8 * u))
        cv.create_line(hx, hy, hx - 3 * u, hy - 6 * u, fill="#8a5a3b", width=max(2, 1.6 * u))
        cv.create_line(hx, hy, hx + 3 * u, hy - 6 * u, fill="#8a5a3b", width=max(2, 1.6 * u))
        cv.create_line(hx - 3 * u, hy - 6 * u, hx + 3 * u, hy - 6 * u, fill="#e03131", width=max(1, u))
    elif weapon == "foam_sword":
        tip_x = hx + face * (5 + 12 * (1 - aim * 0.3)) * u
        tip_y = hy - (14 - aim * 10) * u
        cv.create_line(hx, hy, tip_x, tip_y, fill="#ffd43b", width=max(3, 3.2 * u), capstyle="round")
        cv.create_line(hx - 3 * u, hy - 1 * u, hx + 3 * u, hy + 1 * u, fill="#1c7ed6", width=max(2, 2 * u))
    elif weapon == "rocket_launcher":
        x0, x1 = hx - face * 10 * u, hx + face * 10 * u
        sy = hy - 5 * u
        cv.create_rectangle(min(x0, x1), sy - 3.5 * u, max(x0, x1), sy + 3.5 * u, fill="#5c940d", outline="#2b5000", width=lw)
        _oval(cv, x1, sy, 2 * u, 3.5 * u, "#2b5000", "")
    tool = gear.get("tool")
    tx = cx - face * rx * 0.55
    ty = cy + ry * 0.75
    if tool == "medkit":
        cv.create_rectangle(tx - 4 * u, ty - 3 * u, tx + 4 * u, ty + 3 * u, fill="#ffffff", outline="#c92a2a", width=lw)
        cv.create_line(tx - 2 * u, ty, tx + 2 * u, ty, fill="#e03131", width=max(1, 1.4 * u))
        cv.create_line(tx, ty - 2 * u, tx, ty + 2 * u, fill="#e03131", width=max(1, 1.4 * u))
    elif tool == "water_bomb":
        _oval(cv, tx, ty, 3.6 * u, 4 * u, "#74c0fc", "#1864ab", lw)
        cv.create_line(tx, ty - 4 * u, tx + 1.5 * u, ty - 6 * u, fill="#1864ab", width=max(1, u))
    elif tool == "lucky_charm":
        for a in range(4):
            ang = a * math.pi / 2 + math.pi / 4
            _oval(cv, tx + math.cos(ang) * 1.8 * u, ty + math.sin(ang) * 1.8 * u, 1.8 * u, 1.8 * u, "#51cf66", "#2b8a3e",
                  max(1, 0.6 * u))
    elif tool == "force_field":
        _oval(cv, tx, ty, 3 * u, 3 * u, "#d0bfff", "#7048e8", lw)


def draw_fighter(cv, pet_mod, cx, floor, size, fighter: dict, facing: int = 1, t: float = 0.0, aim: float = 0.0,
                 hit: float = 0.0, look_extra: dict | None = None):
    """A pet with its cosmetic props + arena gear, facing left (-1) or right (1)."""
    species = fighter.get("species") or "cat"
    look = pet_mod.default_look({"species": species if species in pet_mod.SPECIES else "cat",
                                 "color": fighter.get("color") or "#ffc78f", "cheek_color": "#ff8fa3"})
    look.update(t=t, facing=facing, mouth="smile", eyes="open")
    if hit > 0:
        look.update(eyes="closed", mouth="o", squash=0.12 * hit, lean=-facing * 4 * hit)
    if look_extra:
        look.update(look_extra)
    props = clean_look(fighter.get("look") or {})
    props.pop("moves", None)
    gear = {k: v for k, v in (fighter.get("gear") or {}).items() if v in GEAR}
    geo = pet_mod.body_geometry(cx, floor, size, look)
    draw_gear_back(cv, geo, gear, t)
    draw_back_props(cv, geo, props, t)
    geo = pet_mod.draw_pet(cv, cx, floor, size, look) or geo
    draw_front_props(cv, geo, props, t)
    draw_gear_front(cv, geo, gear, t, aim)
    return geo


def draw_gear_preview(cv, pet_mod, size, species, color, item, cx=None, floor=None, t=0.0):
    """Shop tile: your pet facing right holding / wearing one gear item."""
    cx = cx if cx is not None else int(cv["width"]) / 2
    floor = floor if floor is not None else int(cv["height"]) - 2
    fighter = {"species": species, "color": color, "gear": {GEAR[item][0]: item} if item in GEAR else {}}
    draw_fighter(cv, pet_mod, cx - size * 0.12, floor, size, fighter, facing=1, t=t)


# =============================================================================
# Moves (tricks)
# =============================================================================

def trick_pose(L: dict, trick: str, p: float, t: float) -> dict:
    """Adjust the look for a trick; p = progress 0..1, t = time (s)."""
    bump = math.sin(p * math.pi)
    if trick == "spin":
        L.update(eyes="happy", mouth="big_open", blush=True, brows=None,
                 facing=1 if int(t * 9) % 2 == 0 else -1, hop=bump * 6 + abs(math.sin(t * 9)) * 2,
                 squash=math.sin(t * 18) * 0.05)
    elif trick == "dance":
        beat = math.sin(t * 8)
        L.update(eyes="happy" if int(t * 2) % 2 else "open", mouth="big_open", blush=True, brows=None,
                 lean=beat * 4.5, hop=abs(beat) * 6, squash=-abs(beat) * 0.06,
                 wag=beat * 4, facing=1 if math.sin(t * 2) > 0 else -1,
                 foot_l=max(0.0, beat) * 4, foot_r=max(0.0, -beat) * 4)
    elif trick == "moonwalk":
        step = math.sin(t * 10)
        L.update(eyes="half", mouth="smile", brows=None, lean=-2.5 * L.get("facing", 1),
                 foot_l=step * 3.5, foot_r=-step * 3.5, hop=abs(step) * 1.2)
    elif trick == "backflip":
        L.update(eyes="closed" if 0.2 < p < 0.8 else "happy", mouth="o" if p < 0.8 else "big_open",
                 brows=None, hop=bump * 30, squash=(0.25 * (1 - p * 6)) if p < 0.15 else (-0.12 * bump),
                 facing=1 if (p < 0.35 or p > 0.65) else -1)
    elif trick == "kungfu":
        hit = math.sin(t * 16)
        kick = 0.55 < p < 0.8
        L.update(eyes="open", brows="angry", mouth="o" if hit > 0 else "big_open", blush=False,
                 lean=hit * 5, squash=(-0.1 if kick else 0.06 * abs(hit)),
                 hop=(math.sin((p - 0.55) / 0.25 * math.pi) * 14 if kick else 0),
                 facing=1 if int(t * 3) % 2 == 0 else -1)
        if p > 0.85:
            L.update(eyes="happy", brows=None, mouth="smile", lean=0, hop=0)
    elif trick == "spider":
        L.update(eyes="big" if p < 0.15 else "happy", mouth="big_open", blush=True, brows=None,
                 dangle=True, squash=-0.05)
    elif trick == "fireworks":
        beat = abs(math.sin(t * 5))
        L.update(eyes="big" if p < 0.2 else "happy", mouth="big_open", blush=True, brows=None,
                 hop=beat * 7 * (1 - p * 0.4), squash=-beat * 0.05, wag=math.sin(t * 9) * 4)
    elif trick == "wave":
        L.update(eyes="happy", mouth="smile", blush=True, brows=None, lean=math.sin(t * 10) * 2.5,
                 foot_r=3 + math.sin(t * 12) * 2.5)
    elif trick == "jump":
        jumps = abs(math.sin(p * math.pi * 3))
        L.update(eyes="happy" if jumps > 0.3 else "open", mouth="big_open", blush=True, brows=None,
                 hop=jumps * 20, squash=(0.18 if jumps < 0.12 else -0.08 * jumps))
    elif trick == "hearts":
        L.update(eyes="heart", mouth="smile", blush=True, brows=None, hop=abs(math.sin(t * 4)) * 3,
                 lean=math.sin(t * 3) * 2)
    elif trick == "sing":
        L.update(eyes="closed" if int(t * 2) % 2 else "happy", mouth="big_open" if math.sin(t * 7) > 0 else "o",
                 blush=True, brows=None, lean=math.sin(t * 2.5) * 3.5)
    elif trick == "yoga":
        breathe = math.sin(t * 2)
        L.update(eyes="closed", mouth="smile", brows=None, blush=False, squash=-0.12 - breathe * 0.06,
                 hop=2 + breathe * 2, foot_l=4, foot_r=0)
    elif trick == "zoomies":
        L.update(eyes="big", mouth="big_open", blush=True, brows=None,
                 lean=6 * (1 if math.cos(p * math.pi * 4) > 0 else -1), hop=abs(math.sin(t * 22)) * 4,
                 foot_l=abs(math.sin(t * 22)) * 4, foot_r=abs(math.cos(t * 22)) * 4)
    elif trick == "cartwheel":
        L.update(eyes="dizzy" if p > 0.8 else "happy", mouth="big_open", brows=None, blush=True,
                 hop=bump * 18, facing=1 if int(p * 8) % 2 == 0 else -1, squash=math.sin(t * 20) * 0.08)
    elif trick == "juggle":
        L.update(eyes="open", mouth="o" if int(t * 3) % 2 else "smile", brows=None,
                 lean=math.sin(t * 6) * 2, hop=abs(math.sin(t * 6)) * 2)
    elif trick == "rain_dance":
        beat = math.sin(t * 6)
        L.update(eyes="happy", mouth="big_open", blush=True, brows=None, lean=beat * 5, hop=abs(beat) * 8,
                 foot_l=max(0.0, beat) * 4, foot_r=max(0.0, -beat) * 4)
    elif trick == "breakdance":
        L.update(eyes="half" if p < 0.6 else "happy", mouth="smile", brows=None, blush=True,
                 squash=0.22 + math.sin(t * 16) * 0.06, facing=1 if int(t * 7) % 2 == 0 else -1,
                 hop=abs(math.sin(t * 8)) * 3, lean=math.sin(t * 8) * 6)
    elif trick == "magic":
        L.update(eyes="big" if p > 0.6 else "closed", mouth="o" if p > 0.6 else "smile", brows=None,
                 blush=p > 0.6, hop=bump * 4)
    elif trick == "confetti":
        beat = abs(math.sin(t * 6))
        L.update(eyes="happy", mouth="big_open", blush=True, brows=None, hop=beat * 10, squash=-beat * 0.06,
                 wag=math.sin(t * 9) * 4)
    elif trick == "rocket":
        L.update(eyes="big" if p < 0.25 else "happy", mouth="big_open", brows=None, blush=True,
                 squash=-0.08, dangle=False)
    return L


def trick_motion(trick: str, p: float, t: float, facing: int):
    """Window offset (dx px) and lift (px) for moves that travel."""
    if trick == "moonwalk":
        return -facing * math.sin(p * math.pi) * 110, 0.0
    if trick == "spider":
        env = math.sin(min(1.0, p * 1.15) * math.pi) if p < 0.87 else 0.0
        swing = math.sin(p * math.pi * 3.0) * 130 * env
        lift = (70 + 45 * abs(math.cos(p * math.pi * 3.0))) * env
        return swing, lift
    if trick == "zoomies":
        return math.sin(p * math.pi * 4) * 120 * math.sin(p * math.pi), 0.0
    if trick == "cartwheel":
        return facing * math.sin(p * math.pi) * 90, 0.0
    if trick == "rocket":
        if p < 0.2:
            return math.sin(p * 120) * 2, 0.0
        climb = math.sin(min(1.0, (p - 0.2) / 0.8) * math.pi)
        return 0.0, climb * 200
    return 0.0, 0.0


def draw_trick_fx(cv, trick: str, p: float, t: float, geo: dict, canvas_w: float, dx: float = 0.0):
    """Extra effects (web line, fists, motion lines, notes...)."""
    if not geo:
        return
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    font_px = max(7, int(5 * u))
    if trick == "spider":
        top_y = cy - ry
        anchor_x = canvas_w / 2 - dx * 0.55
        cv.create_line(anchor_x, 0, cx, top_y + 2 * u, fill="#ffffff", width=max(2, 1.6 * u))
        cv.create_line(anchor_x, 0, cx, top_y + 2 * u, fill="#c8d2dc", width=1, dash=(3, 3))
        if p < 0.2:
            cv.create_text(cx + 18 * u, top_y - 6 * u, text="thwip!", fill="#e03131",
                           font=("Segoe UI", font_px, "bold italic"))
    elif trick == "kungfu":
        hit = math.sin(t * 16)
        side = 1 if hit > 0 else -1
        reach = (8 + 9 * abs(hit)) * u
        fist_x = cx + side * (rx + reach * 0.4)
        fist_y = cy + ry * 0.05
        _oval(cv, fist_x, fist_y, 3.6 * u, 3.6 * u, "#ffe8cc", "#a35b00", max(1, u))
        for k in range(3):
            y = fist_y - 3 * u + k * 3 * u
            cv.create_line(fist_x - side * 5 * u, y, fist_x - side * (11 + 2 * k) * u, y, fill="#868e96",
                           width=max(1, 0.8 * u))
        if p > 0.82:
            cv.create_text(cx, cy - ry - 9 * u, text="Hi-ya!", fill="#e03131",
                           font=("Segoe UI", font_px + 1, "bold italic"))
    elif trick == "backflip":
        r = rx * 1.25
        start = (p * 360 * 1.0) % 360
        cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=110, style="arc",
                      outline="#adb5bd", width=max(1, 1.2 * u))
        if p > 0.8:
            cv.create_text(cx, cy - ry - 8 * u, text="ta-da!", fill="#7048e8",
                           font=("Segoe UI", font_px, "bold italic"))
    elif trick in ("dance", "moonwalk"):
        for k in range(2):
            a = t * 2.2 + k * math.pi
            cv.create_text(cx + math.cos(a) * (rx + 8 * u), cy - ry - 4 * u + math.sin(a * 1.3) * 4 * u,
                           text="\u266a" if k else "\u266b", fill="#7048e8",
                           font=("Segoe UI Symbol", font_px + 1, "bold"))
    elif trick == "fireworks":
        colors = ("#ff6b6b", "#ffd43b", "#69db7c", "#4dabf7", "#da77f2", "#ff922b")
        for k in range(4):
            phase = (p * 2.6 + k * 0.27) % 1.0
            bx = cx + (-1.35 + k * 0.9) * rx + math.sin(k * 2.3) * 6 * u
            by = cy - ry - (14 + 7 * (k % 2)) * u
            col = colors[k % len(colors)]
            if phase < 0.35:
                rise = phase / 0.35
                cv.create_line(bx, cy + ry * 0.4, bx, cy + ry * 0.4 - (cy + ry * 0.4 - by) * rise,
                               fill="#ffe8a3", width=max(1, u), dash=(2, 2))
            else:
                burst = (phase - 0.35) / 0.65
                r = (4 + 11 * burst) * u
                for j in range(8):
                    a = j * math.pi / 4 + k
                    cv.create_line(bx + math.cos(a) * r * 0.45, by + math.sin(a) * r * 0.45,
                                   bx + math.cos(a) * r, by + math.sin(a) * r,
                                   fill=col, width=max(1, 1.3 * u * (1 - burst * 0.6)))
                cv.create_text(bx, by, text="\u2726", fill="#ffffff", font=("Segoe UI Symbol", max(5, int(3.5 * u))))
        if p > 0.8:
            cv.create_text(cx, cy - ry - 30 * u, text="woo-hoo!", fill="#e64980",
                           font=("Segoe UI", font_px + 1, "bold italic"))
    elif trick == "spin":
        for k in range(3):
            a = t * 9 + k * 2.1
            cv.create_text(cx + math.cos(a) * (rx + 6 * u), cy + math.sin(a) * ry * 0.8, text="\u2726",
                           fill="#f5b700", font=("Segoe UI Symbol", font_px))

    _draw_trick_fx_v212(cv, trick, p, t, geo, canvas_w, dx)


def _draw_trick_fx_v212(cv, trick, p, t, geo, canvas_w, dx=0.0):
    u = geo["u"]
    cx, cy, rx, ry = geo["cx"], geo["cy"], geo["rx"], geo["ry"]
    font_px = max(7, int(5 * u))
    top = cy - ry
    if trick == "wave":
        side = geo["facing"]
        hand_x = cx + side * (rx + 4 * u)
        hand_y = cy - ry * 0.6 + math.sin(t * 12) * 3 * u
        _oval(cv, hand_x, hand_y, 3.4 * u, 3.4 * u, "#ffe8cc", "#a35b00", max(1, u))
        cv.create_text(hand_x + side * 9 * u, hand_y - 6 * u, text="hi!", fill="#1c7ed6",
                       font=("Segoe UI", font_px, "bold italic"))
    elif trick == "hearts":
        for k in range(5):
            phase = (p * 1.6 + k * 0.21) % 1.0
            hx_ = cx + math.sin(k * 2.1 + t * 2) * (rx + 4 * u)
            hy_ = cy - phase * (ry + 30 * u)
            size = max(6, int((4 + 3 * (1 - phase)) * u))
            cv.create_text(hx_, hy_, text="\u2665", fill=("#ff4d6d", "#ff8fab", "#f06595")[k % 3],
                           font=("Segoe UI Symbol", size))
    elif trick == "sing":
        for k in range(3):
            a = t * 2.4 + k * 2.1
            cv.create_text(cx + math.cos(a) * (rx + 9 * u), top - 6 * u + math.sin(a * 1.4) * 5 * u,
                           text=("\u266a", "\u266b", "\u266c")[k], fill=("#7048e8", "#e64980", "#1c7ed6")[k],
                           font=("Segoe UI Symbol", font_px + 2, "bold"))
    elif trick == "yoga":
        cv.create_text(cx, top - 10 * u, text="o m m m . . .", fill="#868e96", font=("Segoe UI", font_px, "italic"))
        r = rx * (1.35 + 0.05 * math.sin(t * 2))
        cv.create_oval(cx - r, cy - r * 0.95, cx + r, cy + r * 0.95, outline="#d0bfff", width=max(1, u), dash=(3, 4))
    elif trick == "zoomies":
        for k in range(3):
            y_ = cy - ry * 0.3 + k * ry * 0.35
            cv.create_line(cx - (rx + 6 * u) * (1 if dx < 0 else -1), y_,
                           cx - (rx + 18 * u) * (1 if dx < 0 else -1), y_, fill="#adb5bd", width=max(1, u))
        if p > 0.85:
            cv.create_text(cx, top - 8 * u, text="zoom!", fill="#f76707", font=("Segoe UI", font_px, "bold italic"))
    elif trick == "cartwheel":
        r = rx * 1.3
        cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=(p * 720) % 360, extent=140, style="arc",
                      outline="#ffa94d", width=max(1, 1.4 * u))
    elif trick == "juggle":
        colors = ("#ff6b6b", "#4dabf7", "#ffd43b")
        for k in range(3):
            a = t * 5 + k * 2 * math.pi / 3
            bx_ = cx + math.cos(a) * rx * 0.8
            by_ = top - 14 * u - abs(math.sin(a)) * 18 * u
            _oval(cv, bx_, by_, 3.2 * u, 3.2 * u, colors[k], "#495057", max(1, 0.8 * u))
    elif trick == "rain_dance":
        cloud_y = top - 26 * u
        for dxk, r in ((-8, 7), (0, 9), (8, 7)):
            _oval(cv, cx + dxk * u, cloud_y, r * u, r * 0.75 * u, "#ced4da", "#868e96", max(1, 0.8 * u))
        for k in range(7):
            phase = (t * 1.8 + k * 0.37) % 1.0
            x_ = cx + (k - 3) * 4 * u
            y_ = cloud_y + 6 * u + phase * (cy - cloud_y)
            cv.create_line(x_, y_, x_ - 1 * u, y_ + 4 * u, fill="#4dabf7", width=max(1, u))
    elif trick == "breakdance":
        for k in range(4):
            a = t * 10 + k * math.pi / 2
            cv.create_text(cx + math.cos(a) * (rx + 8 * u), cy + math.sin(a) * ry * 0.5 + ry * 0.4, text="\u2726",
                           fill="#20c997", font=("Segoe UI Symbol", font_px))
        if p > 0.8:
            cv.create_text(cx, top - 8 * u, text="freeze!", fill="#20c997", font=("Segoe UI", font_px, "bold italic"))
    elif trick == "magic":
        wand_x, wand_y = cx + geo["facing"] * (rx + 6 * u), cy - ry * 0.4
        cv.create_line(wand_x - geo["facing"] * 6 * u, wand_y + 6 * u, wand_x, wand_y, fill="#343a40", width=max(2, 2 * u))
        cv.create_text(wand_x, wand_y - 2 * u, text="\u2605", fill="#ffd43b", font=("Segoe UI Symbol", font_px + 1))
        if p < 0.6:
            for k in range(6):
                a = t * 6 + k
                r = (6 + (p / 0.6) * 18) * u
                cv.create_text(wand_x + math.cos(a) * r, wand_y + math.sin(a) * r, text="\u2727", fill="#b197fc",
                               font=("Segoe UI Symbol", max(5, font_px - 1)))
            cv.create_text(cx, top - 10 * u, text="abracadabra~", fill="#7048e8", font=("Segoe UI", font_px, "italic"))
        else:
            puff = (p - 0.6) / 0.4
            for k in range(6):
                a = k * math.pi / 3
                _oval(cv, cx + math.cos(a) * (rx * 0.6 + puff * 14 * u), cy + math.sin(a) * (ry * 0.6 + puff * 10 * u),
                      (5 - puff * 3) * u, (5 - puff * 3) * u, "#e9ecef", "#ced4da", max(1, 0.6 * u))
            cv.create_text(cx, top - 10 * u, text="ta-da!", fill="#7048e8", font=("Segoe UI", font_px + 1, "bold italic"))
    elif trick == "confetti":
        colors = ("#ff6b6b", "#ffd43b", "#69db7c", "#4dabf7", "#da77f2", "#ff922b")
        for k in range(18):
            phase = (p * 1.4 + k * 0.083) % 1.0
            x_ = cx + math.sin(k * 1.7) * (rx + 14 * u) + math.sin(t * 3 + k) * 3 * u
            y_ = top - 34 * u + phase * (ry * 2 + 34 * u)
            w_ = 2.2 * u
            cv.create_rectangle(x_ - w_, y_ - w_ * 0.5, x_ + w_, y_ + w_ * 0.5, fill=colors[k % 6], outline="")
        if p < 0.3:
            cv.create_text(cx, top - 14 * u, text="\U0001F389 yay!", fill="#e64980", font=("Segoe UI Emoji", font_px + 1, "bold"))
    elif trick == "rocket":
        flame = 0.7 + 0.3 * abs(math.sin(t * 30))
        for s in (-1, 1):
            px_ = cx + s * rx * 0.55
            cv.create_rectangle(px_ - 3 * u, cy - ry * 0.2, px_ + 3 * u, cy + ry * 0.6, fill="#adb5bd",
                                outline="#495057", width=max(1, u))
            _poly(cv, [px_ - 3 * u, cy + ry * 0.6, px_ + 3 * u, cy + ry * 0.6, px_, cy + ry * 0.6 + 12 * u * flame],
                  "#ff922b", "#ffd43b", max(1, u))
        if p < 0.22:
            cv.create_text(cx, top - 8 * u, text=str(3 - int(p / 0.22 * 3)) if p < 0.21 else "liftoff!",
                           fill="#e03131", font=("Segoe UI", font_px + 2, "bold"))
