"""
social.py - levels, achievements and Friends for Pips.

* Levels / XP / streak / achievements are computed locally from the pomodoro
  history (data/productivity.db) and the pet state - they work offline.
* Friends (opt-in, off by default) syncs a tiny public profile to the Pips
  Friends server (Cloudflare Worker + D1) so friends and teams can see each
  other's level, streak and pomodoro counts. Shared: display name, pet animal +
  colour, level, XP, streak, today's / this week's pomodoros and focus minutes,
  achievements, pet mood. Never: app names, window titles, scores, files.

v2.6 adds public / private profiles (photo, bio, info), online status,
friend requests for private profiles, relationship labels and a global
leaderboard of everyone using Pips.

v2 adds chat (friends + groups), private groups ("circles") with invites,
affinity (friendship level that grows with every interaction) and pet
interactions: wave, pet, snack, high five and pet visits (the friend's pet
walks onto your desktop for a moment).

Account (id + secret + friend code) lives in data/social.json on this PC.
The window lives in social_ui.py. All user-facing text comes from i18n.py.
"""

from __future__ import annotations

import base64
import calendar
import json
import logging
import math
import os
import queue
import random

try:
    import pet_shop
except ImportError:  # pragma: no cover
    pet_shop = None
import sqlite3
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from i18n import t
except ImportError:  # pragma: no cover
    def t(key, lang=None, **kwargs):
        return key.format(**kwargs) if kwargs else key

try:
    import outreach                 # v2.15 staff update reminders (Outlook / Teams)
except ImportError:  # pragma: no cover
    outreach = None

logger = logging.getLogger("social")

API_URL = "https://pips-friends.juwenmondial.workers.dev"
# v2.15: every call tells the server which Pips version this is (staff see who still needs to update).
CLIENT_VERSION = ""
REMIND_AGAIN_S = 12 * 3600       # ask before reminding the same person again within 12 h
SYNC_EVERY_S = 10 * 60
BOARD_EVERY_S = 15 * 60
ACH_CHECK_EVERY_S = 5 * 60
FRIEND_NEWS_GAP_S = 30 * 60
# The app's creator. The "owner" role itself is granted only on the server (by friend code),
# so nobody else can claim it from the app.
OWNER_NAME = "Juwen Lie"
OWNER_EMAIL = "73946469+juw12@users.noreply.github.com"
OWNER_LEVEL = 99
OWNER_XP = 999999
PREMIUM_FOREVER = 4102444800        # server value for "Premium forever"

# Chat bots (v2.10) - mirrored from the server; replies come from /v1/bot/ask.
BOTS = [
    {"key": "helper", "code": "BOTPIP", "name": "Pip Helper", "species": "owl", "color": "#9b7bd4", "handle": "helper"},
    {"key": "coach", "code": "BOTFCS", "name": "Coach Focus", "species": "dog", "color": "#f08c3c", "handle": "coach"},
    {"key": "weather", "code": "BOTWTR", "name": "Sunny", "species": "penguin", "color": "#5aa7e6", "handle": "weather"},
    {"key": "arena", "code": "BOTARN", "name": "Arena Master", "species": "dragon", "color": "#d6453d", "handle": "arena"},
    {"key": "fun", "code": "BOTFUN", "name": "Giggles", "species": "bunny", "color": "#f783ac", "handle": "fun"},
    {"key": "guard", "code": "BOTGRD", "name": "Guardian", "species": "bear", "color": "#3b82c4", "handle": "guard"},
]
BOT_BY_KEY = {b["key"]: b for b in BOTS}
BOT_CODES = {b["code"] for b in BOTS}
BOT_HISTORY = 60
STAFF_ROLES = ("owner", "mod")

INBOX_EVERY_S = 45
IDLE_AWAY_S = 10 * 60          # idle this long -> "away" (auto status)
INBOX_FAST_S = 5          # while a chat is open
MAX_ROOM_HISTORY = 150
# v2.16: Pips Community - one Discord-style space for everyone with Friends turned on.
NEWS_ROOM = "pub:news"          # #announcements: posts from the Pips team + every new release
GENERAL_ROOM = "pub:general"    # #general: everyone can chat
COMMUNITY_ROOMS = (NEWS_ROOM, GENERAL_ROOM)
NEWS_MAX_CHARS = 1500
NEWS_FRESH_S = 7 * 24 * 3600    # first start: announcements newer than this still pop up / count as new

# affinity (friendship) levels: (points needed, i18n key)
AFFINITY_LEVELS = [(0, "aff_acq"), (20, "aff_buddy"), (60, "aff_good"), (150, "aff_close"),
                   (300, "aff_best"), (600, "aff_soul")]
INTERACTIONS = ("wave", "pet", "snack", "highfive", "visit")
INTERACTION_SYMBOL = {"wave": "\u270b", "pet": "\u2665", "snack": "\u2615", "highfive": "\u2605",
                      "visit": "\u2302", "cheer": "\u266b"}
# v2.14 nudges: poke / tickle / tease / boop / hug a friend from a distance (no pet visit)
try:
    from nudge_ui import NUDGES, NUDGE_SYMBOL
except ImportError:  # pragma: no cover
    NUDGES = ("poke", "tickle", "tease", "boop", "hug")
    NUDGE_SYMBOL = {"poke": "\U0001F449", "tickle": "\U0001F606", "tease": "\U0001F61C",
                    "boop": "\U0001F443", "hug": "\U0001F917"}
INTERACTION_SYMBOL.update(NUDGE_SYMBOL)
NUDGE_GAP_S = 3.2         # matches the server limit per friend
NUDGE_RALLY_S = 90        # poll the inbox fast for a while after a nudge (poke wars!)
NUDGE_ERRORS = {"nudges_off": "nudge_err_off", "nudge_slow": "nudge_err_slow", "nudge_limit": "nudge_err_limit"}

# v2.25 play the minigames together: party rooms for up to 8 friends (the host + 7 invites)
try:
    import minigames
except ImportError:  # pragma: no cover
    minigames = None
MP_MAX_INVITES = 7
MP_ERRORS = {"need_players": "mp_err_need_players", "game_started": "mp_err_started", "room_full": "mp_err_full",
             "expired": "mp_err_closed", "not_found": "mp_err_closed", "bad_game": "mp_err_closed"}


def affinity_level(points: int):
    """-> (level index 0.., i18n key, points of this level, points of next level or None)."""
    points = max(0, int(points or 0))
    index = 0
    for i, (need, _key) in enumerate(AFFINITY_LEVELS):
        if points >= need:
            index = i
    nxt = AFFINITY_LEVELS[index + 1][0] if index + 1 < len(AFFINITY_LEVELS) else None
    return index, AFFINITY_LEVELS[index][1], AFFINITY_LEVELS[index][0], nxt

# (id, symbol, condition) - names/descriptions are i18n keys "ach_<id>"
ACHIEVEMENTS = [
    ("first_pom", "\u2605", lambda s: s["total_poms"] >= 1),
    ("poms_10", "\u2726", lambda s: s["total_poms"] >= 10),
    ("poms_50", "\u2742", lambda s: s["total_poms"] >= 50),
    ("poms_100", "\u2654", lambda s: s["total_poms"] >= 100),
    ("poms_250", "\u265b", lambda s: s["total_poms"] >= 250),
    ("day_4", "\u2618", lambda s: s["best_day_poms"] >= 4),
    ("day_8", "\u2600", lambda s: s["best_day_poms"] >= 8),
    ("streak_3", "\u2668", lambda s: s["best_streak"] >= 3),
    ("streak_7", "\u2604", lambda s: s["best_streak"] >= 7),
    ("streak_14", "\u269d", lambda s: s["best_streak"] >= 14),
    ("early_bird", "\u263c", lambda s: s["early_bird"]),
    ("night_owl", "\u263e", lambda s: s["night_owl"]),
    ("weekend", "\u2740", lambda s: s["weekend"]),
    ("level_5", "\u2691", lambda s: s["level"] >= 5),
    ("level_10", "\u269c", lambda s: s["level"] >= 10),
    ("pet_lover", "\u2665", lambda s: s["pets_total"] >= 50),
    ("tickler", "\u266a", lambda s: s["tickles_total"] >= 100),
    ("chef", "\u2615", lambda s: s["snacks_total"] >= 20),
    ("friend_1", "\u263a", lambda s: s["friends"] >= 1),
    ("team_1", "\u2691", lambda s: s["groups"] >= 1),
    ("close_friend", "\u2766", lambda s: s.get("max_affinity", 0) >= 150),
    ("visitor", "\u2302", lambda s: s.get("visits_sent", 0) >= 1),
]
ACH_IDS = [a[0] for a in ACHIEVEMENTS]
ACH_SYMBOL = {a[0]: a[1] for a in ACHIEVEMENTS}


# =============================================================================
# Levels & stats (offline)
# =============================================================================

def xp_for_level(level: int) -> int:
    """Total XP needed to reach `level` (level 1 = 0 XP, 2 = 100, 3 = 300, 4 = 600...)."""
    return 50 * level * (level - 1)


def level_for_xp(xp: int) -> int:
    return max(1, int((1 + math.sqrt(1 + max(0, xp) / 12.5)) / 2))


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _parse(ts):
    try:
        return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def compute_stats(db_path, pet_state: dict | None = None, friends: int = 0, groups: int = 0,
                  today: date | None = None, max_affinity: int = 0, visits_sent: int = 0) -> dict:
    """Everything the level / achievement / Friends features need, from local data only."""
    today = today or date.today()
    pet_state = pet_state or {}
    per_day: dict[str, int] = {}
    minutes_day: dict[str, int] = {}
    early = night = weekend = False
    try:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT day, started_at, ended_at FROM sessions WHERE kind = 'work' AND completed = 1"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.debug("stats: no session data (%s)", exc)
        rows = []
    for day, started, ended in rows:
        day = str(day)
        per_day[day] = per_day.get(day, 0) + 1
        start_dt = _parse(started)
        end_dt = _parse(ended)
        minutes = 25
        if start_dt and end_dt:
            minutes = max(0, min(90, int((end_dt - start_dt).total_seconds() // 60)))
        minutes_day[day] = minutes_day.get(day, 0) + minutes
        stamp = end_dt or start_dt
        if stamp:
            if stamp.hour < 8:
                early = True
            if stamp.hour >= 22:
                night = True
            if stamp.weekday() >= 5:
                weekend = True

    total = sum(per_day.values())
    active_days = sorted(d for d, n in per_day.items() if n > 0)
    xp = 20 * total + 10 * len(active_days)
    level = level_for_xp(xp)

    # current streak: consecutive active days ending today (or yesterday if today is still empty)
    streak = 0
    cursor = today if per_day.get(today.isoformat()) else today - timedelta(days=1)
    while per_day.get(cursor.isoformat()):
        streak += 1
        cursor -= timedelta(days=1)
    best = run = 0
    previous = None
    for d in active_days:
        current = date.fromisoformat(d)
        run = run + 1 if previous and (current - previous).days == 1 else 1
        best = max(best, run)
        previous = current

    week = week_key(today)
    week_poms = week_minutes = 0
    for d, n in per_day.items():
        try:
            if week_key(date.fromisoformat(d)) == week:
                week_poms += n
                week_minutes += minutes_day.get(d, 0)
        except ValueError:
            continue
    stats = {
        "total_poms": total,
        "xp": xp,
        "level": level,
        "level_floor": xp_for_level(level),
        "level_next": xp_for_level(level + 1),
        "streak": streak,
        "best_streak": max(best, streak),
        "best_day_poms": max(per_day.values(), default=0),
        "day": today.isoformat(),
        "today_poms": per_day.get(today.isoformat(), 0),
        "today_minutes": minutes_day.get(today.isoformat(), 0),
        "week": week,
        "week_poms": week_poms,
        "week_minutes": week_minutes,
        "early_bird": early,
        "night_owl": night,
        "weekend": weekend,
        "pets_total": int(pet_state.get("pets_total", 0) or 0),
        "tickles_total": int(pet_state.get("tickles_total", 0) or 0),
        "snacks_total": int(pet_state.get("snacks_total", 0) or 0),
        "friends": friends,
        "groups": groups,
        "max_affinity": max_affinity,
        "visits_sent": visits_sent,
    }
    stats["achievements"] = [aid for aid, _sym, cond in ACHIEVEMENTS if cond(stats)]
    return stats


# =============================================================================
# Server client
# =============================================================================

class ApiError(Exception):
    def __init__(self, code: str, status: int = 0):
        super().__init__(code)
        self.code = code
        self.status = status


# v2.15: a local proxy helper set in HTTP(S)_PROXY (e.g. localhost:3128) that hangs used to take
# Friends offline. If a GET fails through it, Pips retries with the Windows proxy settings and keeps
# using those for a while.
SYSTEM_PROXY_STICKY_S = 10 * 60
_SYSTEM_PROXY = {"opener": None, "until": 0.0}


def _system_proxy_opener():
    """urllib opener that uses the Windows proxy settings (Internet Options) instead of HTTP(S)_PROXY."""
    if _SYSTEM_PROXY["opener"] is None:
        try:
            proxies = urllib.request.getproxies_registry()
        except Exception:  # pragma: no cover - non-Windows
            proxies = {}
        _SYSTEM_PROXY["opener"] = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return _SYSTEM_PROXY["opener"]


def _env_proxy_differs_from_system() -> bool:
    env = urllib.request.getproxies_environment()
    if not (env.get("https") or env.get("http")):
        return False
    try:
        system = urllib.request.getproxies_registry()
    except Exception:  # pragma: no cover - non-Windows
        system = {}
    return (env.get("https"), env.get("http")) != (system.get("https"), system.get("http"))


def _api_error(err) -> "ApiError":
    try:
        payload = json.loads(err.read().decode("utf-8"))
    except Exception:
        payload = {}
    return ApiError(payload.get("error", "http_%d" % err.code), err.code)


def api_call(method: str, path: str, body=None, account: dict | None = None, timeout=15) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API_URL + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", f"Pips/{CLIENT_VERSION}" if CLIENT_VERSION else "Pips")
    if CLIENT_VERSION:
        req.add_header("x-pips-version", CLIENT_VERSION)
    if account:
        req.add_header("authorization", f"Bearer {account['id']}.{account['secret']}")
    use_system = time.time() < _SYSTEM_PROXY["until"]
    opener = _system_proxy_opener().open if use_system else urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise _api_error(err) from None
    except (urllib.error.URLError, OSError, ValueError) as err:
        # Only GETs are retried (a POST may already have reached the server).
        if not use_system and method == "GET" and _env_proxy_differs_from_system():
            try:
                with _system_proxy_opener().open(req, timeout=timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err2:
                _SYSTEM_PROXY["until"] = time.time() + SYSTEM_PROXY_STICKY_S
                raise _api_error(err2) from None
            except (urllib.error.URLError, OSError, ValueError):
                pass
            else:
                _SYSTEM_PROXY["until"] = time.time() + SYSTEM_PROXY_STICKY_S
                logger.warning("social: HTTP(S)_PROXY is not answering - using the Windows proxy settings")
                return result
        raise ApiError("offline") from err


def default_display_name() -> str:
    raw = os.environ.get("USERNAME", "") or "Friend"
    first = raw.replace(".", "_").split("_")[0]
    return (first[:1].upper() + first[1:])[:24] or "Friend"


# =============================================================================
# Social controller (lives inside the DesktopPet)
# =============================================================================


def item_name(item: str) -> str:
    """Display name of any shop item (prop, move or arena gear)."""
    if pet_shop is not None:
        if item in pet_shop.PROPS:
            return t("pr_" + item)
        if item in pet_shop.GEAR:
            return t("gr_" + item)
        if item in pet_shop.MOVES:
            return t("mv_" + item).split(":")[0]
    return item


def bday_text(month: int, day: int) -> str:
    if not month:
        return ""
    return t("bday_fmt", d=int(day), month=t("month_%d" % int(month)))


def version_tuple(text) -> tuple:
    parts = str(text or "").split(".")
    if len(parts) < 3:
        return ()
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return ()


def is_newer(version: str, than: str) -> bool:
    """True when `version` is a newer x.y.z than `than` (an unknown `than` counts as older)."""
    other = version_tuple(version)
    mine = version_tuple(than)
    return bool(other) and (not mine or other > mine)


def release_post(m) -> dict | None:
    """The {v, title, date, notes} payload of a release announcement, or None."""
    if not isinstance(m, dict) or m.get("kind") != "release":
        return None
    try:
        data = json.loads(m.get("body") or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("v") else None


def release_notes_for(release: dict, lang: str) -> list:
    notes = release.get("notes") or {}
    return list(notes.get(lang) or notes.get("en") or [])


def current_lang() -> str:
    try:
        import i18n
        return i18n.get_language()
    except Exception:  # pragma: no cover
        return "en"

class Social:
    def __init__(self, pet):
        self.pet = pet
        self.path = Path(pet.base_dir) / "data" / "social.json"
        self.db_path = Path(pet.base_dir) / "data" / "productivity.db"
        self.data = self._load()
        self.results: queue.Queue = queue.Queue()
        self.window = None
        self.board = None
        self.board_at = 0.0
        self.stats = None
        self.busy = False
        self.rooms: dict[str, list] = {}        # "dm:CODE" / "c:ID" -> messages (oldest first)
        self.invites: list = []
        self.history_loaded = False
        self.chat_open_room = None              # room shown in the chat view (fast polling)
        self.visitors = []
        self.leaderboards: dict[str, dict] = {}   # sort -> leaderboard payload
        self.profiles: dict[str, dict] = {}       # code -> {"full": bool, "user": {...}, "at": t}
        self._avatar_jobs: set = set()
        now = time.monotonic()
        self._next_ach = now + 20
        self._next_sync = now + 45
        self._next_board = now + 90
        self._next_inbox = now + 8
        self._inbox_busy = False
        self._last_news = 0.0
        self.geo_results = None     # city search results (/v1/geo)
        self.bot_pending = None     # bot key we're waiting on
        self.arena = None           # /v1/arena payload
        self.parties = {}           # host code -> /v1/party payload (v2.13)
        self.party_windows = {}     # host code -> PartyWindow
        self._pending_gift = None
        self._nudge_sent = {}        # friend code -> monotonic time of my last nudge
        self._nudge_queue = []       # incoming nudges waiting for focus / fullscreen to end
        self._nudge_held = False
        self._rally_until = 0.0
        self.arena_open = False     # Arena tab visible -> fast lobby polling + "in arena" presence
        self._next_arena = 0.0
        self.duels: dict = {}       # duel id -> latest /v1/duel view
        self.duel_windows: dict = {}
        self.mp_rooms: dict = {}    # v2.25 play together: room id -> latest /v1/games room view
        self.mp_list_data = None    # my rooms (/v1/games), None until loaded
        self.mp_invites: list = []  # pending party invites (from /v1/inbox)
        self._next_mp = 0.0
        self._mp_open_next = None   # room to open in a window once it has loaded
        self.mod_data = None        # moderator queue (/v1/mod/reports)
        self.admin_data = None      # owner overview (/v1/admin/overview)
        self.new_keys = []          # premium keys generated in this session
        self.show_inactive = False  # Mod tab: also list people not seen for a while
        self.staff_note = ("", 0.0)  # (text, show-until) status line for update reminders
        self.community = {}         # Pips Community stats from /v1/inbox: {members, online}
        global CLIENT_VERSION
        try:
            get_version = (getattr(pet, "cb", None) or {}).get("version")
            CLIENT_VERSION = str(get_version() or "") if callable(get_version) else CLIENT_VERSION
        except Exception:  # pragma: no cover - defensive
            pass
        if self.data.get("role") or self.data.get("premium_until"):
            pet.root.after(0, self._apply_owner)

    # ---- persistence ----
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
            logger.warning("social: could not save %s: %s", self.path, exc)

    @property
    def enabled(self) -> bool:
        return bool(self.pet.s.get("friends", False)) and bool(self.data.get("id"))

    @property
    def is_owner(self) -> bool:
        return self.data.get("role") == "owner"

    @property
    def is_mod(self) -> bool:
        return self.data.get("role") == "mod"

    @property
    def is_staff(self) -> bool:
        return self.data.get("role") in STAFF_ROLES

    @property
    def premium_until(self) -> int:
        return int(self.data.get("premium_until") or 0)

    @property
    def is_premium(self) -> bool:
        return self.is_staff or self.premium_until > time.time()

    @property
    def muted_until(self) -> int:
        until = int(self.data.get("muted_until") or 0)
        return until if until > time.time() else 0

    def _apply_owner(self):
        """Push role / premium perks into the shop (owner: unlimited, premium + mods: premium items)."""
        shop = getattr(self.pet, "shop", None)
        if shop is not None:
            shop.unlimited = self.is_owner
            shop.premium = self.is_premium
            if shop.premium and shop.add_premium_daily() and pet_shop is not None:
                self.pet.root.after(4000, lambda: self.pet.say(
                    t("premium_daily", n=pet_shop.PREMIUM_DAILY_COINS), duration=5, priority=0))

    @property
    def account(self):
        if self.data.get("id") and self.data.get("secret"):
            return {"id": self.data["id"], "secret": self.data["secret"]}
        return None

    # ---- lookups ----
    def friends(self) -> list:
        return list((self.board or {}).get("friends", []))

    def friend(self, code: str):
        for f in self.friends():
            if f.get("code") == code:
                return f
        return None

    def circles(self) -> list:
        return list((self.board or {}).get("circles", []))

    def circle(self, circle_id: str):
        for c in self.circles():
            if c.get("id") == circle_id:
                return c
        return None

    def room_title(self, room: str) -> str:
        if room in COMMUNITY_ROOMS:
            return "#" + t("com_ch_news" if room == NEWS_ROOM else "com_ch_general")
        if room.startswith("dm:"):
            f = self.friend(room[3:])
            return f["name"] if f else room[3:]
        c = self.circle(room[2:])
        return c["name"] if c else "?"

    def unread(self, room: str) -> int:
        if room == GENERAL_ROOM and self.community_muted:
            return 0
        read = self.data.get("read", {}).get(room, 0)
        return sum(1 for m in self.rooms.get(room, []) if not m.get("mine") and m["id"] > read)

    def total_unread(self) -> int:
        return sum(self.unread(r) for r in self.rooms)

    def mark_read(self, room: str):
        msgs = self.rooms.get(room) or []
        if msgs:
            self.data.setdefault("read", {})[room] = msgs[-1]["id"]
            self.save()

    # ---- Pips Community (v2.16) ----
    @property
    def community_muted(self) -> bool:
        return bool(self.data.get("community_muted"))

    def set_community_muted(self, value: bool):
        self.data["community_muted"] = bool(value)
        if value:
            self.mark_read(GENERAL_ROOM)
        self.save()

    def can_delete(self, m) -> bool:
        """Your own community messages; staff: anyone's (mods can't remove the owner's or other mods' posts)."""
        if m.get("mine"):
            return True
        if not self.is_staff:
            return False
        role = (m.get("from") or {}).get("role", "")
        if role == "owner":
            return False
        if role == "mod":
            return self.is_owner
        return True

    def delete_community(self, msg_id: int):
        if self.enabled:
            self._run("com_deleted", self._delete_community, int(msg_id))

    def _delete_community(self, msg_id):
        api_call("POST", "/v1/community/delete", {"id": msg_id}, self.account)
        return {"id": msg_id}

    def open_update(self):
        """Show the updater's offer (or check now) - same as Help > Check for updates."""
        callbacks = getattr(self.pet, "cb", None) or {}
        action = callbacks.get("show_update") or callbacks.get("check_updates")
        if callable(action):
            action()

    def _take_community_meta(self, result):
        if not isinstance(result, dict):
            return
        if isinstance(result.get("community"), dict):
            self.community = result["community"]
        gone = {i for i in (result.get("deleted") or []) if isinstance(i, int)}
        if gone:
            for room in COMMUNITY_ROOMS:
                msgs = self.rooms.get(room)
                if msgs and any(m.get("id") in gone for m in msgs):
                    self.rooms[room] = [m for m in msgs if m.get("id") not in gone]

    def _init_community_marks(self):
        """First time the community shows up: old #general chat counts as read, recent announcements don't."""
        read = self.data.setdefault("read", {})
        changed = False
        general = self.rooms.get(GENERAL_ROOM) or []
        if GENERAL_ROOM not in read and general:
            read[GENERAL_ROOM] = general[-1]["id"]
            changed = True
        if "news_seen" not in self.data:
            old = [m["id"] for m in self.rooms.get(NEWS_ROOM) or [] if m.get("at", 0) < time.time() - NEWS_FRESH_S]
            mark = max(old) if old else 0
            self.data["news_seen"] = mark
            read.setdefault(NEWS_ROOM, mark)
            changed = True
        if changed:
            self.save()

    def _take_community(self, fresh, initial):
        news_seen = int(self.data.get("news_seen", 0))
        new_posts = [m for m in self.rooms.get(NEWS_ROOM, []) if m["id"] > news_seen]
        if new_posts:
            self.data["news_seen"] = new_posts[-1]["id"]
            self.save()
            others = [m for m in new_posts if not m.get("mine")]
            if others and self.chat_open_room != NEWS_ROOM:
                self._announce(others[-1])
        if self.community_muted:
            return
        # #general only pops up when someone mentions you (@name)
        name = str(self.data.get("name") or "").strip().lower()
        cutoff = time.time() - (10 * 60 if initial else 3600)
        mentions = [m for m in fresh
                    if m.get("room") == GENERAL_ROOM and m.get("kind") == "text" and m.get("at", 0) >= cutoff
                    and name and ("@" + name) in str(m.get("body", "")).lower()]
        for m in mentions[-3:]:
            self._react_to(m)

    def _announce(self, m):
        """Pop up a new #announcements post (release posts only when this PC doesn't have that version yet)."""
        open_news = lambda: self.open_window("chat", room=NEWS_ROOM)  # noqa: E731
        release = release_post(m)
        if release:
            if not is_newer(release.get("v", ""), CLIENT_VERSION):
                return
            self.pet.say(t("com_bubble_release", v=release.get("v", ""), title=release.get("title", "")),
                         actions=[(t("com_update_now"), self.open_update), (t("com_read"), open_news)],
                         duration=20, priority=1, on_click=open_news)
            return
        who = (m.get("from") or {}).get("name", "?")
        body = " ".join(str(m.get("body", "")).split())
        body = body if len(body) <= 90 else body[:87] + "\u2026"
        self.pet.say(t("com_bubble_news", name=who, text=body), duration=10,
                     priority=0 if self._quiet() else 1, on_click=open_news)

    # ---- coins ----
    def affinity_levels(self) -> int:
        return sum(affinity_level(f.get("affinity", 0))[0] for f in self.friends())

    def wallet(self) -> dict:
        shop = getattr(self.pet, "shop", None)
        if not shop:
            return {"balance": 0, "total": 0, "spent": 0}
        stats = self.stats or self.refresh_stats()
        return shop.wallet(stats, len(self.data.get("unlocked", {})), self.affinity_levels(),
                           int(self.data.get("duel_coins", 0)))

    def my_look(self) -> dict:
        shop = getattr(self.pet, "shop", None)
        look = {}
        if shop:
            look = dict(shop.equipped())
            look["moves"] = shop.moves()
        # v2.18: the pet is a boy / girl, its life stage, and the person's own gender (\u2642 / \u2640 by names)
        gender = self.pet.s.get("gender", "")
        if gender in ("boy", "girl"):
            look["gender"] = gender
        growth = getattr(self.pet, "growth", None)
        if growth is not None and growth.stage in ("baby", "child", "teen"):
            look["stage"] = growth.stage
        owner_gender = self.profile().get("gender", "")
        if owner_gender in ("m", "f"):
            look["owner_gender"] = owner_gender
        return look

    def look_changed(self):
        """The pet's gender / life stage (or my gender) changed: sync soon so friends see it."""
        if self.enabled:
            self._next_sync = min(self._next_sync, time.monotonic() + 8)

    def my_gear(self) -> dict:
        shop = getattr(self.pet, "shop", None)
        return shop.gear() if shop else {}

    # ---- stats / achievements ----
    def refresh_stats(self) -> dict:
        board = self.board or {}
        self.stats = compute_stats(
            self.db_path, self.pet.st,
            friends=len(board.get("friends", [])) if self.enabled else 0,
            groups=(len(self.data.get("groups", [])) + len(board.get("circles", []))) if self.enabled else 0,
            max_affinity=max([f.get("affinity", 0) for f in board.get("friends", [])] or [0]) if self.enabled else 0,
            visits_sent=int(self.data.get("visits_sent", 0)))
        if self.is_owner:
            self.stats.update(level=OWNER_LEVEL, xp=OWNER_XP, level_floor=OWNER_XP - 1, level_next=OWNER_XP,
                              achievements=list(ACH_IDS))
        return self.stats

    def check_achievements(self, announce=True) -> list:
        stats = self.refresh_stats()
        unlocked = self.data.setdefault("unlocked", {})
        first_run = not unlocked and not self.data.get("ach_seeded")
        new = [aid for aid in stats["achievements"] if aid not in unlocked]
        if new:
            stamp = date.today().isoformat()
            for aid in new:
                unlocked[aid] = stamp
            self.data["ach_seeded"] = True
            self.save()
            if announce and not first_run:
                aid = new[-1]
                more = len(new) - 1
                text = t("ach_unlocked", name=t("ach_" + aid).split(":")[0])
                if more:
                    text += t("ach_more", n=more)
                if pet_shop is not None:
                    text += t("shop_coins_plus", n=pet_shop.COINS_PER_ACHIEVEMENT * len(new))
                self.pet.set_action("love", 2.0)
                self.pet._spawn("sparkle", 5)
                self.pet._voice("happy")
                self.pet.say(text, duration=6, priority=2, on_click=self.open_window)
        elif not self.data.get("ach_seeded"):
            self.data["ach_seeded"] = True
            self.save()
        self._refresh_window()
        return new

    # ---- background work ----
    def _run(self, kind, fn, *args):
        def worker():
            try:
                result = fn(*args)
                self.results.put((kind, True, result))
            except ApiError as err:
                self.results.put((kind, False, err.code))
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("social worker failed")
                self.results.put((kind, False, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    # ---- my profile (v2.6) ----
    PROFILE_DEFAULTS = {"visibility": "public", "bio": "", "info": {}, "status": "auto", "status_text": "",
                        "gender": ""}

    def profile(self) -> dict:
        prof = dict(self.PROFILE_DEFAULTS)
        prof.update(self.data.get("profile") or {})
        if not isinstance(prof.get("info"), dict):
            prof["info"] = {}
        return prof

    def save_profile(self, changes: dict, name: str | None = None):
        prof = self.profile()
        prof.update(changes)
        self.data["profile"] = prof
        if name is not None:
            self.data["name"] = (name.strip() or default_display_name())[:24]
        self.save()
        if self.enabled:
            self.sync()

    def avatar_dir(self) -> Path:
        return Path(self.pet.base_dir) / "data" / "avatars"

    def my_avatar_path(self):
        path = self.avatar_dir() / "me.png"
        return path if self.data.get("avatar_v") and path.exists() else None

    def set_avatar_from_file(self, src: str) -> str:
        """Crop + resize a picture to a round 96px PNG; returns '' or an error code."""
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return "no_pillow"
        try:
            img = Image.open(src).convert("RGBA")
        except Exception:
            return "bad_image"
        side = min(img.size)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((96, 96), Image.LANCZOS)
        mask = Image.new("L", (96 * 4, 96 * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 96 * 4 - 1, 96 * 4 - 1], fill=255)
        img.putalpha(mask.resize((96, 96), Image.LANCZOS))
        self.avatar_dir().mkdir(parents=True, exist_ok=True)
        out = self.avatar_dir() / "me.png"
        img.save(out, "PNG", optimize=True)
        data = base64.b64encode(out.read_bytes()).decode("ascii")
        if len(data) > 78000:
            img.quantize(colors=128).save(out, "PNG", optimize=True)
            data = base64.b64encode(out.read_bytes()).decode("ascii")
        self.data["avatar_v"] = int(time.time())
        self.save()
        if self.enabled:
            self._act("avatar_set", "/v1/avatar", {"data": data})
        return ""

    def clear_avatar(self):
        self.data["avatar_v"] = 0
        self.save()
        try:
            (self.avatar_dir() / "me.png").unlink()
        except OSError:
            pass
        if self.enabled:
            self._act("avatar_set", "/v1/avatar", {"data": ""})

    def avatar_path(self, code: str, version: int):
        """Cached avatar for a user (downloads it in the background when missing)."""
        if not version or not code:
            return None
        if code == self.data.get("code"):
            return self.my_avatar_path()
        path = self.avatar_dir() / f"{code}_{int(version)}.png"
        if path.exists():
            return path
        if self.enabled and (code, version) not in self._avatar_jobs:
            self._avatar_jobs.add((code, version))
            self._run("avatar", self._fetch_avatar, code, int(version))
        return None

    def _fetch_avatar(self, code, version):
        result = api_call("GET", f"/v1/avatar?code={code}", None, self.account)
        raw = base64.b64decode(result.get("data", ""))
        if not raw.startswith(b"\x89PNG"):
            raise ApiError("bad_image")
        folder = self.avatar_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob(f"{code}_*.png"):
            try:
                old.unlink()
            except OSError:
                pass
        (folder / f"{code}_{version}.png").write_bytes(raw)
        return {"code": code}

    # ---- leaderboard / profiles / requests / labels (v2.6) ----
    def load_leaderboard(self, sort="xp"):
        if self.enabled:
            self._run("leaderboard", api_call, "GET", f"/v1/leaderboard?sort={sort}", None, self.account)

    def view_user(self, code: str):
        if self.enabled:
            self._run("user", api_call, "GET", f"/v1/user?code={code}", None, self.account)

    def respond_request(self, code: str, accept: bool):
        board = self.board or {}
        board["requests"] = [r for r in board.get("requests", []) if r.get("code") != code]
        self._act("request_answered", "/v1/friends/respond", {"code": code, "accept": bool(accept)})

    def cancel_request(self, code: str):
        self._act("request_cancelled", "/v1/friends/cancel", {"code": code})

    def set_label(self, code: str, label: str):
        self._act("label_set", "/v1/friends/label", {"code": code, "label": label})

    # ---- reports, moderation, premium (v2.7) ----
    def report_user(self, code: str, reason: str):
        if self.enabled:
            self._run("reported", api_call, "POST", "/v1/report", {"code": code, "reason": reason[:200]}, self.account)

    def load_mod(self):
        if self.enabled and self.is_staff:
            self._run("mod", self._fetch_mod, self.account)

    @staticmethod
    def _fetch_mod(account):
        data = api_call("GET", "/v1/mod/reports", None, account)
        try:
            data["versions"] = api_call("GET", "/v1/mod/versions", None, account)
        except ApiError as err:          # older server / offline: reports still show
            data["versions"] = {"ok": False, "error": err.code}
        return data

    # ---- work e-mail + update reminders (v2.15) ----
    def work_email(self) -> str:
        """My work e-mail: what I typed in the Profile tab, else the Outlook account on this PC."""
        if "work_email" in self.data:
            return str(self.data.get("work_email") or "")
        return outreach.detect_work_email() if outreach is not None else ""

    def set_work_email(self, email: str):
        email = (email or "").strip()
        detected = outreach.detect_work_email() if outreach is not None else ""
        if "work_email" not in self.data and email == detected:
            return                       # unchanged - keep following Outlook
        self.data["work_email"] = email
        self.save()

    def set_contact(self, code: str, value: str):
        """Staff: save someone's work e-mail ('' clears). A name is looked up in Outlook."""
        if self.enabled and self.is_staff:
            self._run("contact_set", self._set_contact, code, (value or "").strip(), self.account)

    @staticmethod
    def _set_contact(code, value, account):
        if value and not (outreach is not None and outreach.valid_email(value)):
            found = None
            if outreach is not None:
                try:
                    found = outreach.resolve_address(value)
                except outreach.OutreachError:
                    found = None
            if not found:
                raise ApiError("email_not_found")
            value = found[0]
        return api_call("POST", "/v1/mod/contact", {"code": code, "email": value}, account)

    def note_reminded(self, codes, via: str):
        if self.enabled and self.is_staff and codes:
            self._run("reminded", api_call, "POST", "/v1/mod/remind", {"codes": list(codes), "via": via},
                      self.account)

    def send_update_reminder(self, people: list, latest: str, bulk: bool = False) -> int:
        """E-mail an update reminder through Outlook (background). -> number of recipients."""
        if not (self.enabled and self.is_staff) or outreach is None:
            return 0
        targets = [p for p in people if outreach.valid_email(p.get("email", ""))]
        if targets:
            self._run("remind_mail", self._send_update_reminder, targets, latest, bulk)
        return len(targets)

    def _send_update_reminder(self, targets, latest, bulk):
        sender = self.data.get("name") or default_display_name()
        notes, title = self.release_notes(latest)
        subject = outreach.reminder_subject(latest)
        emails = sorted({p["email"].strip() for p in targets}, key=str.lower)
        single = targets[0] if not bulk and len(targets) == 1 else None
        name = single.get("name", "") if single else ""
        ver = single.get("app_ver", "") if single else ""
        try:
            html_body = outreach.reminder_html(name, ver, latest, sender, notes, title)
            if single:
                outreach.send_mail(emails, subject, html_body)
            else:
                me = self.work_email()
                outreach.send_mail([me] if me else [], subject, html_body, bcc=emails)
            method = "outlook"
        except outreach.OutreachError as err:
            if err.code != "no_outlook":
                raise ApiError(err.code) from None
            body = outreach.reminder_text(name, ver, latest, sender)
            if single:
                outreach.open_mail_draft(emails, subject, body)
            else:
                outreach.open_mail_draft([], subject, body, bcc=emails)
            method = "draft"
        codes = [p["code"] for p in targets if p.get("code")]
        try:
            api_call("POST", "/v1/mod/remind", {"codes": codes, "via": "email"}, self.account)
        except ApiError:
            pass
        return {"method": method, "count": len(emails), "codes": codes}

    def release_notes(self, version: str):
        """English notes + title of `version` from the local CHANGELOG.json (for reminder mails)."""
        try:
            data = json.loads((Path(self.pet.base_dir) / "CHANGELOG.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], ""
        releases = data.get("releases", []) if isinstance(data, dict) else data
        for rel in releases if isinstance(releases, list) else []:
            if isinstance(rel, dict) and rel.get("version") == version:
                notes = rel.get("notes") or {}
                return list(notes.get("en", []) if isinstance(notes, dict) else notes), rel.get("title", "")
        return [], ""

    def staff_status(self, text: str, seconds: float = 10.0):
        """Show a short note in the hub's status line (survives the next refreshes for a few seconds)."""
        self.staff_note = (text, time.time() + seconds)
        if self.window and self.window.alive():
            self.window.error = ""
            self.window.status.config(text=text, fg=self.window.MUTED)

    def mod_action(self, code: str, action: str, **extra):
        body = {"code": code, "action": action}
        body.update(extra)
        self._act("mod_done", "/v1/mod/action", body)

    def load_admin(self):
        if self.enabled and self.is_owner:
            self._run("admin", api_call, "GET", "/v1/admin/overview", None, self.account)

    def set_role(self, code: str, role: str):
        self._act("admin_done", "/v1/admin/role", {"code": code, "role": role})

    def give_premium(self, code: str, days: int):
        self._act("admin_done", "/v1/admin/premium", {"code": code, "days": int(days)})

    def make_keys(self, days: int, count: int = 1, note: str = ""):
        if self.enabled and self.is_owner:
            self._run("keys", api_call, "POST", "/v1/admin/keys",
                      {"days": int(days), "count": int(count), "note": note[:60]}, self.account)

    def redeem_key(self, key: str):
        self._act("redeemed", "/v1/premium/redeem", {"key": key.strip().upper()})

    # ---- chat bots (v2.10) ----
    def bot_chat(self, key: str) -> list:
        return list((self.data.get("bot_chats") or {}).get(key, []))

    def ask_bot(self, key: str, text: str):
        text = text.strip()[:400]
        if not text or key not in BOT_BY_KEY or not self.enabled:
            return
        chats = self.data.setdefault("bot_chats", {})
        history = chats.get(key, [])
        payload = {"bot": key, "text": text, "lang": getattr(self.pet, "lang", "en"),
                   "history": [{"mine": m["mine"], "text": m["text"]} for m in history[-8:]]}
        history.append({"mine": True, "text": text, "at": time.time()})
        chats[key] = history[-BOT_HISTORY:]
        self.bot_pending = key
        self.save()
        self._run("bot_reply", api_call, "POST", "/v1/bot/ask", payload, self.account)

    def clear_bot_chat(self, key: str):
        (self.data.get("bot_chats") or {}).pop(key, None)
        self.save()

    # ---- location & weather (v2.9) ----
    def my_weather(self):
        me = (self.board or {}).get("me") or {}
        return me.get("weather"), me.get("loc")

    def set_location(self, mode: str, place: dict | None = None, quiet: bool = False):
        """Profile city. v2.23: quiet=True for the pet's own location updates (no bubble);
        a city picked here also becomes the pet's weather place."""
        body = {"mode": mode}
        if place:
            body.update(city=place.get("name", ""), country=place.get("country", ""),
                        lat=place.get("lat"), lon=place.get("lon"))
        self._quiet_location = bool(quiet)
        self._act("location_set", "/v1/location", body)
        if not quiet and hasattr(self.pet, "set_weather_mode"):
            try:
                if mode == "city" and place:
                    self.pet.set_weather_mode("city", place, announce=False)
                elif mode == "auto" and self.pet.s.get("weather_mode") in ("off", "city"):
                    self.pet.set_weather_mode("ip", announce=False)
            except Exception as exc:
                logger.debug("pet weather sync failed: %s", exc)

    def search_city(self, query: str):
        if self.enabled and len(query.strip()) >= 2:
            from urllib.parse import quote
            lang = {"id": "id", "zh_TW": "zh", "zh_CN": "zh"}.get(getattr(self.pet, "lang", "en"), "en")
            self._run("geo", api_call, "GET", f"/v1/geo?q={quote(query.strip())}&lang={lang}", None, self.account)

    def _maybe_prompt_location(self, board):
        me = board.get("me") or {}
        if me.get("loc_set") or self.data.get("loc_prompted") or "loc_set" not in me:
            return
        self.data["loc_prompted"] = True
        self.pet.root.after(8000, lambda: self.pet.say(t("wx_prompt"), duration=10, priority=0,
                                                         on_click=lambda: self.open_window("profile")))

    # ---- birthdays, parties & gifts (v2.13) ----
    BDAY_BONUS = 100
    GIFT_COIN_CHOICES = (25, 50, 100, 200)

    def my_birthday(self):
        me = (self.board or {}).get("me") or {}
        return int(me.get("bday_m") or 0), int(me.get("bday_d") or 0), bool(me.get("bday_visible", True))

    def set_birthday(self, month: int, day: int, visible: bool = True):
        self._act("bday_set", "/v1/birthday", {"m": int(month), "d": int(day), "visible": bool(visible)})

    @staticmethod
    def days_until(bday, today=None):
        """Days until the next birthday {m, d} (0 = today), or None."""
        if not bday or not bday.get("m"):
            return None
        today = today or date.today()
        month, day = int(bday["m"]), int(bday["d"])
        for year in (today.year, today.year + 1):
            d = day
            if month == 2 and day == 29 and not calendar.isleap(year):
                d = 28
            try:
                when = date(year, month, d)
            except ValueError:
                return None
            if when >= today:
                return (when - today).days
        return None

    def upcoming_birthdays(self, within: int = 7) -> list:
        out = []
        for friend in self.friends():
            days = self.days_until(friend.get("bday"))
            if days is not None and days <= within:
                out.append((days, friend))
        out.sort(key=lambda pair: (pair[0], pair[1].get("name", "").lower()))
        return out

    def is_my_birthday(self) -> bool:
        m, d, _visible = self.my_birthday()
        return bool(m) and self.days_until({"m": m, "d": d}) == 0

    def gift_cost(self, item: str, coins: int) -> int:
        shop = getattr(self.pet, "shop", None)
        cost = int(coins or 0)
        if item and shop and shop.is_known(item):
            cost += shop.price(item)
        return cost

    def send_gift(self, code: str, item: str = "", coins: int = 0, message: str = "") -> str:
        """Returns '' when the gift is on its way, otherwise an error code."""
        if not self.enabled:
            return "offline"
        shop = getattr(self.pet, "shop", None)
        if not shop or (not item and not coins):
            return "empty"
        if item and pet_shop is not None and item in pet_shop.PREMIUM_ITEMS:
            return "premium_only"
        cost = self.gift_cost(item, coins)
        if cost > self.wallet().get("balance", 0):
            return "poor"
        self._pending_gift = {"cost": cost, "code": code, "item": item, "coins": int(coins or 0)}
        self._run("gift_sent", api_call, "POST", "/v1/gift",
                  {"to": code, "item": item, "coins": int(coins or 0), "message": message}, self.account)
        return ""

    def load_party(self, code: str):
        if self.enabled:
            self._run("party", api_call, "GET", f"/v1/party?code={code}&date={date.today().isoformat()}",
                      None, self.account)

    def party_wish(self, code: str, text: str):
        if self.enabled and text.strip():
            self._run("party_wish", api_call, "POST", "/v1/party/wish",
                      {"code": code, "date": date.today().isoformat(), "text": text.strip()}, self.account)

    def open_party(self, code: str):
        import party_ui
        win = self.party_windows.get(code)
        if win and win.alive():
            win.win.deiconify()
            win.win.lift()
            return
        self.party_windows[code] = party_ui.PartyWindow(self, code)

    def open_gift(self, code: str):
        import party_ui
        friend = self.friend(code)
        if friend:
            party_ui.GiftDialog(self, friend)

    def _take_gifts(self, gifts):
        shop = getattr(self.pet, "shop", None)
        if not gifts or not shop:
            return
        seen = self.data.setdefault("gifts_seen", [])
        fresh = [g for g in gifts if g.get("id") not in seen]
        claim = [g["id"] for g in gifts]
        for gift in fresh:
            item = gift.get("item") or ""
            coins = int(gift.get("coins") or 0)
            parts = []
            if item:
                outcome = shop.receive_item(item)
                name = item_name(item)
                parts.append(name if outcome == "new" else t("gift_dupe", item=name))
            if coins:
                shop.add_gift_coins(coins)
                parts.append(f"{pet_shop.COIN if pet_shop else ''} {coins}")
            seen.append(gift["id"])
            sender = (gift.get("from") or {}).get("name", "?")
            text = t("gift_received", name=sender, what=" + ".join(parts))
            if gift.get("message"):
                text += "\n\u201c" + gift["message"] + "\u201d"
            self.pet.set_action("cheer", 1.6)
            self.pet._spawn("heart", 6)
            self.pet._voice("happy")
            self.pet.say(text, duration=9, priority=2, on_click=lambda: self.open_window("shop"))
        self.data["gifts_seen"] = seen[-300:]
        self.save()
        if claim:
            self._run("gifts_claimed", api_call, "POST", "/v1/gift/claim", {"ids": claim}, self.account)
        if fresh:
            self._refresh_window()

    def _check_birthdays(self):
        today = date.today()
        stamp = today.isoformat()
        if self.data.get("bday_checked") == stamp:
            return
        self.data["bday_checked"] = stamp
        shop = getattr(self.pet, "shop", None)
        if self.is_my_birthday():
            if self.data.get("bday_bonus_year") != today.year and shop:
                self.data["bday_bonus_year"] = today.year
                shop.add_gift_coins(self.BDAY_BONUS)
            name = (self.board or {}).get("me", {}).get("name", "")
            code = self.data.get("code", "")
            self.pet.root.after(4000, lambda: self._celebrate_me(name, code))
        for days, friend in self.upcoming_birthdays(within=3):
            code, name = friend["code"], friend.get("name", "?")
            if days == 0:
                actions = [(t("bday_join"), lambda c=code: self.open_party(c)),
                           (t("bday_gift"), lambda c=code: self.open_gift(c))]
                self.pet.root.after(9000, lambda n=name, a=actions: self.pet.say(
                    t("bday_today_friend", name=n), actions=a, duration=25, priority=2))
            elif days in (1, 3):
                self.pet.root.after(12000, lambda n=name, d=days, c=code: self.pet.say(
                    t("bday_soon", name=n, n=d), actions=[(t("bday_gift"), lambda: self.open_gift(c))],
                    duration=12, priority=1))
        self.save()

    def _celebrate_me(self, name, code):
        self.pet.do_trick("confetti", force=True)
        self.pet.say(t("bday_mine", name=name, n=self.BDAY_BONUS),
                     actions=[(t("bday_my_party"), lambda: self.open_party(code))], duration=20, priority=2)

    # ---- arena & duels (v2.8) ----
    def load_arena(self, here=False):
        if self.enabled:
            self._run("arena", api_call, "GET", "/v1/arena" + ("?here=1" if here else ""), None, self.account)

    # v2.24 skill duels: every fighter brings 3 skills; the challenger picks best of 1 / 3 / 5 rounds.
    def my_level(self) -> int:
        stats = self.stats
        if not stats:
            try:
                stats = self.refresh_stats()
            except Exception:
                stats = {}
        try:
            return max(1, int(stats.get("level", 1) or 1))
        except (TypeError, ValueError):
            return 1

    def duel_loadout(self) -> list:
        if pet_shop is None:
            return ["strike", "heavy", "guard"]
        return pet_shop.clean_skills(self.data.get("duel_skills"), self.my_level(), self.my_gear())

    def duel_rounds(self) -> int:
        try:
            n = int(self.data.get("duel_rounds", 3) or 3)
        except (TypeError, ValueError):
            n = 3
        return n if n in (1, 3, 5) else 3

    def save_duel_setup(self, skills, rounds=None):
        self.data["duel_skills"] = [s for s in (skills or []) if isinstance(s, str)][:3]
        if rounds in (1, 3, 5):
            self.data["duel_rounds"] = rounds
        self.save()

    def challenge(self, code: str, rounds=None, skills=None):
        if self.enabled:
            body = {"code": code, "gear": self.my_gear(), "v": 2, "rounds": rounds or self.duel_rounds(),
                    "skills": list(skills or self.duel_loadout())}
            self._run("duel_challenged", api_call, "POST", "/v1/duel/challenge", body, self.account)

    def respond_duel(self, duel_id: str, accept: bool, skills=None):
        if self.enabled:
            body = {"id": duel_id, "accept": bool(accept), "gear": self.my_gear()}
            if accept:
                body.update(v=2, skills=list(skills or self.duel_loadout()))
            self._run("duel_responded", api_call, "POST", "/v1/duel/respond", body, self.account)

    def quick_duel(self, code: str, rounds=None, skills=None):
        if self.enabled:
            body = {"code": code, "gear": self.my_gear(), "v": 2, "rounds": rounds or self.duel_rounds(),
                    "skills": list(skills or self.duel_loadout())}
            self._run("duel", api_call, "POST", "/v1/duel/quick", body, self.account)

    def duel_skill(self, duel_id: str, skill: str, turn: int):
        """Skill duels: use one of my skills on my turn (`turn` must match the duel's turn number)."""
        if self.enabled:
            self._run("duel", api_call, "POST", "/v1/duel/act",
                      {"id": duel_id, "skill": skill, "turn": int(turn or 0)}, self.account)

    def load_duel(self, duel_id: str):
        if self.enabled:
            self._run("duel", api_call, "GET", f"/v1/duel?id={duel_id}", None, self.account)

    def duel_act(self, duel_id: str, round_no: int, action: str):
        if self.enabled:
            self._run("duel", api_call, "POST", "/v1/duel/act",
                      {"id": duel_id, "round": int(round_no), "action": action}, self.account)

    def forfeit_duel(self, duel_id: str):
        if self.enabled:
            self._run("duel_forfeit", api_call, "POST", "/v1/duel/forfeit", {"id": duel_id}, self.account)

    def open_duel(self, duel_id: str, spectate: bool = False):
        import arena_ui
        win = self.duel_windows.get(duel_id)
        if win and win.alive():
            win.win.deiconify()
            win.win.lift()
            return
        skill_duel = (self.duels.get(duel_id) or {}).get("v") == 2
        window_class = arena_ui.SkillDuelWindow if skill_duel else arena_ui.DuelWindow
        self.duel_windows[duel_id] = window_class(self, duel_id, spectate=spectate)

    # ---- v2.25: play the minigames together (party rooms for 2-8 friends) ----
    def mp_list(self):
        if self.enabled:
            self._run("mp_list", api_call, "GET", "/v1/games", None, self.account)

    def mp_load(self, room_id: str):
        if self.enabled:
            self._run("mp_room", api_call, "GET", f"/v1/games?id={room_id}", None, self.account)

    def mp_create(self, game: str, codes):
        if self.enabled:
            body = {"game": game, "codes": [c for c in codes if isinstance(c, str)][:MP_MAX_INVITES]}
            self._run("mp_created", api_call, "POST", "/v1/games/create", body, self.account)

    def mp_invite(self, room_id: str, codes):
        if self.enabled:
            body = {"id": room_id, "codes": [c for c in codes if isinstance(c, str)][:MP_MAX_INVITES]}
            self._run("mp_room", api_call, "POST", "/v1/games/invite", body, self.account)

    def mp_respond(self, room_id: str, accept: bool):
        self.mp_invites = [g for g in self.mp_invites if g.get("id") != room_id]
        if self.enabled:
            self._run("mp_joined" if accept else "mp_declined", api_call, "POST", "/v1/games/respond",
                      {"id": room_id, "accept": bool(accept)}, self.account)

    def mp_start(self, room_id: str, game=None):
        if self.enabled:
            body = {"id": room_id}
            if game:
                body["game"] = game
            self._run("mp_room", api_call, "POST", "/v1/games/start", body, self.account)

    def mp_score(self, room_id: str, round_no: int, score: int, done: bool = False):
        if self.enabled:
            body = {"id": room_id, "round": int(round_no or 0), "score": max(0, int(score or 0)), "done": bool(done)}
            self._run("mp_score", api_call, "POST", "/v1/games/score", body, self.account)

    def mp_leave(self, room_id: str):
        self.mp_rooms.pop(room_id, None)
        if self.mp_list_data:
            self.mp_list_data = [r for r in self.mp_list_data if r.get("id") != room_id]
        if self.enabled:
            self._run("mp_left", api_call, "POST", "/v1/games/leave", {"id": room_id}, self.account)

    def mp_open(self, room_id: str):
        """Open the party window for a room I'm in (loads it first if needed)."""
        room = self.mp_rooms.get(room_id)
        if room and minigames is not None:
            minigames.open_party(self.pet, room)
        self._mp_open_next = room_id
        self.mp_load(room_id)

    def mp_pending(self) -> int:
        """Party invites waiting for an answer (for the Hub badge)."""
        return len(self.mp_invites)

    def _take_mp_room(self, room: dict, open_window: bool = False):
        room_id = room.get("id")
        if not room_id:
            return
        self.mp_rooms[room_id] = room
        if self.mp_list_data is not None:
            rest = [r for r in self.mp_list_data if r.get("id") != room_id]
            mine = (room.get("mine") or {}).get("status")
            self.mp_list_data = ([room] + rest) if mine in ("joined", "invited") and room.get("status") != "closed" else rest
        if minigames is None:
            return
        if open_window or getattr(self, "_mp_open_next", None) == room_id:
            self._mp_open_next = None
            if (room.get("mine") or {}).get("status") == "joined" and room.get("status") != "closed":
                minigames.open_party(self.pet, room)
                return
        win = minigames.party_window(room_id, self.pet)
        if win is not None:
            win.take_room(room)

    def _notify_games(self, games: list):
        self.mp_invites = [g for g in games or [] if g.get("id")]
        notified = set(self.data.get("notified_games", []))
        for g in self.mp_invites:
            if g["id"] in notified:
                continue
            notified.add(g["id"])
            game = g.get("game", "catch")
            icon = minigames.ICONS.get(game, "") if minigames is not None else ""
            self.pet.set_action("hop", 0.55)
            self.pet._voice("greet")
            self.pet.say(t("mp_invite_bubble", name=g.get("from", {}).get("name", "?"), game=f"{icon} {t('mg_' + game)}"),
                         actions=[("▶ " + t("mp_join"), lambda i=g["id"]: self.mp_respond(i, True)),
                                  (t("mp_later"), lambda: self.open_window("games"))],
                         duration=20, priority=2)
        self.data["notified_games"] = sorted(notified)[-50:]

    def _notify_duels(self, duels: list):
        notified = set(self.data.get("notified_duels", []))
        for d in duels or []:
            if d.get("id") in notified:
                continue
            notified.add(d["id"])
            self.pet.set_action("hop", 0.55)
            self.pet._voice("greet")
            self.pet.say(t("duel_incoming", name=d.get("from", {}).get("name", "?")), duration=12, priority=2,
                         on_click=lambda: self.open_window("arena"))
        self.data["notified_duels"] = sorted(notified)[-50:]

    def _take_notices(self, notices: list):
        """React to moderator / owner notices (warnings, mutes, role and premium changes)."""
        seen = int(self.data.get("notice_seen", 0))
        fresh = [n for n in notices or [] if int(n.get("id", 0)) > seen]
        if not fresh:
            return
        self.data["notice_seen"] = max(int(n["id"]) for n in fresh)
        resync = False
        for n in fresh:
            kind = n.get("kind")
            body = n.get("body", "")
            if kind == "warn":
                self.data["last_warning"] = {"text": body, "at": n.get("created_at", 0)}
                self.pet.set_action("worried", 3.0)
                self.pet.say(t("notice_warn", text=body), duration=12, priority=2)
            elif kind == "mute":
                stamp = datetime.fromtimestamp(int(body or 0)).strftime("%d %b %H:%M") if body.isdigit() else "?"
                self.pet.set_action("worried", 3.0)
                self.pet.say(t("notice_mute", time=stamp), duration=10, priority=2)
            elif kind == "unmute":
                self.pet.say(t("notice_unmute"), duration=6, priority=1)
            elif kind == "cleared":
                prof = self.profile()
                prof.update(bio="", status_text="", info={})
                self.data["profile"] = prof
                self.data.pop("avatar_v", None)
                resync = True
                self.pet.say(t("notice_cleared"), duration=10, priority=2)
            elif kind == "renamed":
                self.data["name"] = "Pips friend"
                resync = True
                self.pet.say(t("notice_renamed"), duration=10, priority=2)
            elif kind == "mod_on":
                self.pet.set_action("cheer", 1.6)
                self.pet._spawn("sparkle", 6)
                self.pet.say(t("mod_welcome"), duration=10, priority=2, on_click=lambda: self.open_window("mod"))
            elif kind == "mod_off":
                self.pet.say(t("mod_removed"), duration=7, priority=1)
            elif kind == "premium_on":
                until = int(body) if str(body).isdigit() else 0
                self.pet.set_action("love", 2.0)
                self.pet._spawn("sparkle", 8)
                self.pet._voice("happy")
                self.pet.say(t("premium_welcome", until=self.premium_text(until)), duration=10, priority=2,
                             on_click=lambda: self.open_window("profile"))
            elif kind == "premium_off":
                self.pet.say(t("premium_ended"), duration=8, priority=1)
        self.save()
        if resync:
            self.sync()

    @staticmethod
    def premium_text(until: int) -> str:
        if until >= PREMIUM_FOREVER:
            return t("premium_forever")
        if until <= 0:
            return "-"
        return datetime.fromtimestamp(until).strftime("%d %b %Y")

    def requests(self) -> list:
        return list((self.board or {}).get("requests", []))

    def sent_requests(self) -> set:
        return set((self.board or {}).get("sent", []))

    def _notify_requests(self):
        notified = set(self.data.get("notified_requests", []))
        for req in self.requests():
            if req["code"] not in notified:
                notified.add(req["code"])
                self.pet.set_action("hop", 0.55)
                self.pet.say(t("req_new", name=req.get("name", "?")), duration=7, priority=1,
                             on_click=lambda: self.open_window("friends"))
        self.data["notified_requests"] = sorted(notified)[-100:]

    def _profile_payload(self) -> dict:
        stats = self.stats or self.refresh_stats()
        unlocked = list(self.data.get("unlocked", {}).keys())
        prof = self.profile()
        payload = {
            "visibility": prof["visibility"],
            "bio": prof["bio"],
            "info": prof["info"],
            "status": prof["status"],
            "status_text": prof["status_text"],
            "name": self.data.get("name") or default_display_name(),
            "species": self.pet.s.get("species", "cat"),
            "color": self.pet.s.get("color", "#ffc78f"),
            "level": stats["level"],
            "xp": stats["xp"],
            "streak": stats["streak"],
            "day": stats["day"],
            "today_poms": stats["today_poms"],
            "today_minutes": stats["today_minutes"],
            "week": stats["week"],
            "week_poms": stats["week_poms"],
            "week_minutes": stats["week_minutes"],
            "achievements": unlocked,
            "mood": self.pet.mood(),
            "look": self.my_look(),
            "gear": self.my_gear(),
            "allow_nudges": self.allow_nudges(),
        }
        email = self.work_email()
        if email or "work_email" in self.data:   # never wipe an address staff entered for us
            payload["email"] = email
        return payload

    def _board_now(self, account=None):
        stats = self.stats or self.refresh_stats()
        return api_call("GET", f"/v1/board?day={stats['day']}&week={stats['week']}", None,
                        account or self.account)

    def _sync_and_board(self, payload, account):
        api_call("POST", "/v1/profile", payload, account)
        return self._board_now(account)

    def sync(self):
        if not self.enabled:
            return
        self.refresh_stats()
        self._run("board", self._sync_and_board, self._profile_payload(), self.account)

    def enable(self, name: str):
        name = (name or "").strip()[:24] or default_display_name()
        self.data["name"] = name
        self.save()
        self.pet.update_setting("friends", True)
        if self.data.get("id"):
            self.sync()
            return
        self.refresh_stats()
        body = {"name": name, "species": self.pet.s.get("species", "cat"),
                "color": self.pet.s.get("color", "#ffc78f")}
        self.busy = True
        self._run("register", self._register_then_sync, body)

    def _register_then_sync(self, body):
        account = api_call("POST", "/v1/register", body)
        self.results.put(("account", True, account))
        acc = {"id": account["id"], "secret": account["secret"]}
        api_call("POST", "/v1/profile", dict(self._profile_payload()), acc)
        return self._board_now(acc)

    def disable_and_delete(self):
        account = self.account
        self.pet.update_setting("friends", False)
        self.board = None
        self.rooms = {}
        self.invites = []
        self.history_loaded = False
        if account:
            self._run("deleted", api_call, "DELETE", "/v1/me", None, account)
        self.leaderboards = {}
        self.profiles = {}
        for key in ("id", "secret", "code", "groups", "read", "seen_id", "notified_invites", "notified_requests",
                    "role", "premium_until", "muted_until", "notice_seen"):
            self.data.pop(key, None)
        self.mod_data = None
        self.admin_data = None
        self._apply_owner()
        self.save()
        self._refresh_window()

    def _then_board(self, method, path, body):
        result = api_call(method, path, body, self.account)
        board = self._board_now()
        board["_result"] = result
        return board

    def _act(self, kind, path, body):
        if self.enabled:
            self._run(kind, self._then_board, "POST", path, body)

    def add_friend(self, code: str):
        self._act("friend_added", "/v1/friends/add", {"code": code.strip().upper()})

    def remove_friend(self, code: str):
        self._act("friend_removed", "/v1/friends/remove", {"code": code})

    def join_group(self, group: str):
        self._act("group_joined", "/v1/groups/join", {"group": group})

    def leave_group(self, group: str):
        self._act("group_left", "/v1/groups/leave", {"group": group})

    def create_circle(self, name: str):
        self._act("circle_created", "/v1/circles/create", {"name": name})

    def invite_to_circle(self, circle_id: str, code: str):
        self._act("circle_invited", "/v1/circles/invite", {"circle": circle_id, "code": code})

    def respond_invite(self, circle_id: str, accept: bool):
        self.invites = [i for i in self.invites if i.get("circle") != circle_id]
        self._act("circle_joined" if accept else "circle_declined", "/v1/circles/respond",
                  {"circle": circle_id, "accept": bool(accept)})

    def leave_circle(self, circle_id: str):
        self._act("circle_left", "/v1/circles/leave", {"circle": circle_id})

    def kick_from_circle(self, circle_id: str, code: str):
        self._act("circle_kicked", "/v1/circles/kick", {"circle": circle_id, "code": code})

    # ---- chat & interactions ----
    def send(self, room: str, kind: str = "text", body: str = "", quiet: bool = False):
        """quiet: don't have your own pet announce it (e.g. the visitor card already reacted)."""
        if not self.enabled:
            return
        shop = getattr(self.pet, "shop", None)
        if kind == "visit" and shop and shop.moves() and not body:
            body = random.choice(shop.moves())       # my pet shows off a move when it arrives
        if shop and room not in COMMUNITY_ROOMS:
            shop.add_interaction_bonus()
        limit = NEWS_MAX_CHARS if room == NEWS_ROOM else 500
        payload = {"kind": kind, "body": body[:limit], "lang": getattr(self.pet, "lang", "en")}
        if room.startswith("dm:"):
            payload["to"] = room[3:]
        elif room in COMMUNITY_ROOMS:
            payload["channel"] = room[4:]
        else:
            payload["circle"] = room[2:]
        if kind == "visit":
            self.data["visits_sent"] = int(self.data.get("visits_sent", 0)) + 1
            self.save()
        self._run("sent", self._send_then_inbox, payload, room, kind, quiet)

    def _send_then_inbox(self, payload, room, kind, quiet=False):
        api_call("POST", "/v1/send", payload, self.account)
        result = api_call("GET", f"/v1/inbox?since={self._since()}&nudge=1&community=1", None, self.account)
        result["_room"] = room
        result["_kind"] = kind
        result["_quiet"] = bool(quiet)
        return result

    def _since(self) -> int:
        return max([m["id"] for msgs in self.rooms.values() for m in msgs] or [0]) if self.history_loaded else 0

    def _idle_flag(self) -> str:
        try:
            import pet as pet_mod
            return "1" if pet_mod.user_idle_seconds() > IDLE_AWAY_S else "0"
        except Exception:
            return "0"

    def poll_inbox(self):
        if not self.enabled or self._inbox_busy:
            return
        self._inbox_busy = True
        self._run("inbox", api_call, "GET",
                  f"/v1/inbox?since={self._since()}&idle={self._idle_flag()}&nudge=1&community=1", None,
                  self.account)

    def _take_inbox(self, result, initial=False):
        if isinstance(result, dict) and result.get("duels"):
            self._notify_duels(result["duels"])
        if isinstance(result, dict) and "games" in result:
            self._notify_games(result.get("games") or [])
        self._take_community_meta(result)
        seen = int(self.data.get("seen_id", 0))
        fresh = []
        for m in result.get("messages", []):
            room = m.get("room")
            if not room:
                continue
            msgs = self.rooms.setdefault(room, [])
            if msgs and msgs[-1]["id"] >= m["id"]:
                continue
            msgs.append(m)
            if len(msgs) > MAX_ROOM_HISTORY:
                del msgs[: len(msgs) - MAX_ROOM_HISTORY]
            if m["id"] > seen and not m.get("mine"):
                fresh.append(m)
        top = max([m["id"] for m in result.get("messages", [])] or [seen])
        if top > seen:
            self.data["seen_id"] = top
            self.save()
        if self.chat_open_room:
            self.mark_read(self.chat_open_room)
        self._init_community_marks()
        self.invites = result.get("invites", self.invites)
        self._notify_invites()
        community_fresh = [m for m in fresh if m.get("room") in COMMUNITY_ROOMS]
        fresh = [m for m in fresh if m.get("room") not in COMMUNITY_ROOMS]
        # first load after start: only react to things that arrived very recently
        cutoff = time.time() - (10 * 60 if initial else 24 * 3600)
        others = [m for m in fresh if m.get("kind") not in NUDGES]
        for m in others[-5:]:
            if m.get("at", 0) >= cutoff:
                self._react_to(m)
        nudge_cutoff = time.time() - (10 * 60 if initial else 3600)
        nudges = [m for m in fresh if m.get("kind") in NUDGES and m.get("at", 0) >= nudge_cutoff
                  and str(m.get("room", "")).startswith("dm:")]
        if nudges:
            self._take_nudges(nudges)
        self._take_community(community_fresh, initial)

    def _notify_invites(self):
        notified = set(self.data.get("notified_invites", []))
        for inv in self.invites:
            if inv["circle"] not in notified:
                notified.add(inv["circle"])
                self.pet.say(t("cir_invited_you", name=inv.get("by", "?"), group=inv.get("name", "?")),
                             duration=7, priority=1, on_click=lambda: self.open_window("groups"))
        self.data["notified_invites"] = sorted(notified)[-50:]
        self.save()

    def _quiet(self) -> bool:
        return bool(self.pet.focus and self.pet.s.get("quiet_focus", True))

    def _react_to(self, m):
        """Your pet reacts to an incoming message / interaction."""
        pet = self.pet
        who = (m.get("from") or {}).get("name", "?")
        kind = m.get("kind", "text")
        room = m.get("room", "")
        open_chat = lambda r=room: self.open_window("chat", room=r)
        if self.window and self.window.alive() and self.chat_open_room == room and kind == "text":
            return  # you're looking at it already
        pet_name = pet.s.get("name", "Pip")
        if kind == "text":
            body = m.get("body", "")
            body = body if len(body) <= 80 else body[:77] + "\u2026"
            where = "" if room.startswith("dm:") else " (" + self.room_title(room) + ")"
            pet.say(t("chat_bubble", name=who + where, text=body), duration=7,
                    priority=0 if self._quiet() else 1, on_click=open_chat)
            if not self._quiet():
                pet.set_action("hop", 0.55)
                pet._voice("greet")
            return
        if kind == "wave":
            pet.set_action("hop", 0.55)
            pet._voice("greet")
        elif kind == "pet":
            pet.set_action("petted", 1.8)
            pet._spawn("heart", 3)
            pet._bump("happiness", 3)
            pet._voice("pet")
        elif kind == "snack":
            pet.set_action("eat", 2.0)
            pet._spawn("crumb", 4)
            pet._bump("fullness", 10)
            pet._bump("happiness", 4)
            pet._voice("eat")
        elif kind in ("highfive", "cheer"):
            pet.set_action("cheer", 1.4)
            pet._spawn("sparkle", 4)
            pet._voice("happy")
        elif kind == "visit":
            who_info = dict(m.get("from") or {})
            if m.get("body"):
                who_info["trick"] = m["body"]
            self.start_visit(who_info)
            pet._voice("greet")
        group = "" if room.startswith("dm:") else self.room_title(room)
        pet.say(t("ix_in_" + kind, name=who, pet=pet_name, group=group), duration=6, priority=1, on_click=open_chat)

    # ---- pokes, tickles & teases (v2.14) ----
    def allow_nudges(self) -> bool:
        return bool(self.data.get("allow_nudges", True))

    def set_allow_nudges(self, value: bool):
        self.data["allow_nudges"] = bool(value)
        self.save()
        self.sync()

    def nudge_targets(self) -> list:
        """Friends you can nudge, online first."""
        order = {"online": 0, "busy": 1, "away": 2, "offline": 3}
        people = [f for f in self.friends() if f.get("code") and f.get("code") not in BOT_CODES]
        return sorted(people, key=lambda f: (order.get(f.get("presence") or "offline", 3),
                                             -f.get("affinity", 0), f.get("name", "").lower()))

    def nudge(self, code: str, kind: str):
        """Poke / tickle / tease / boop / hug a friend - it pops up on their screen."""
        if not self.enabled or kind not in NUDGES:
            return
        friend = self.friend(code)
        if not friend:
            return
        if friend.get("nudges") is False:
            self.pet.say(t("nudge_err_off_name", name=friend.get("name", "?")), duration=4, priority=1)
            return
        now = time.monotonic()
        if now - self._nudge_sent.get(code, 0.0) < NUDGE_GAP_S:
            self.pet.say(t("nudge_err_slow"), duration=3, priority=1)
            return
        self._nudge_sent[code] = now
        self._start_rally()
        self.mark_read("dm:" + code)   # answering a poke means you've seen it
        self.send("dm:" + code, kind)

    def _start_rally(self):
        now = time.monotonic()
        self._rally_until = now + NUDGE_RALLY_S
        self._next_inbox = min(self._next_inbox, now + INBOX_FAST_S)

    def _take_nudges(self, msgs):
        groups = {}
        for m in msgs:
            sender = dict(m.get("from") or {})
            code = sender.get("code") or str(m.get("room", ""))[3:]
            group = groups.setdefault(code, {"code": code, "who": sender, "count": 0, "kind": m["kind"]})
            group["count"] += 1
            group["kind"] = m["kind"]   # react to the latest one
        self._nudge_queue.extend(groups.values())
        self._start_rally()
        self._flush_nudges()

    def _nudge_blocked(self) -> bool:
        """Hold nudges while a fullscreen app runs or during a focus session - shown at the break."""
        pet = self.pet
        return bool(getattr(pet, "fs_hidden", False) or getattr(pet, "focus", False)
                    or getattr(pet, "_destroyed", False))

    def _flush_nudges(self):
        if not self._nudge_queue:
            return
        if self._nudge_blocked():
            self._nudge_held = True
            return
        queue, self._nudge_queue = self._nudge_queue, []
        merged = {}
        for group in queue:
            known = merged.get(group["code"])
            if known:
                known["count"] += group["count"]
                known["kind"] = group["kind"]
            else:
                merged[group["code"]] = dict(group)
        groups = list(merged.values())
        held, self._nudge_held = self._nudge_held, False
        if len(groups) == 1 and not held:
            self._show_nudge(groups[0])
        else:
            self._show_nudge_summary(groups)

    def _nudge_text(self, group) -> str:
        who = group["who"].get("name") or "?"
        text = NUDGE_SYMBOL.get(group["kind"], "") + " " + t("nudge_in_" + group["kind"], name=who,
                                                              pet=self.pet.s.get("name", "Pip"))
        if group["count"] > 1:
            text += " " + t("nudge_times", n=group["count"])
        return text

    def _show_nudge(self, group):
        code, kind = group["code"], group["kind"]
        actions = [
            (NUDGE_SYMBOL[kind] + " " + t("nudge_back_" + kind), lambda c=code, k=kind: self.nudge(c, k)),
            ("\U0001F4AC", lambda c=code: self.open_window("chat", room="dm:" + c)),
        ]
        text = self._nudge_text(group)
        pet = self.pet
        if pet.visible and not pet.fs_hidden:
            if getattr(pet, "sleeping", False):
                pet.wake("poke", quiet=True)
            self._nudge_react(kind)
            if pet.say(text, actions=actions, duration=10, priority=2):
                return
        self._nudge_toast(group["who"], text, actions)

    def _show_nudge_summary(self, groups):
        parts = ["%s %s%s" % (g["who"].get("name") or "?", NUDGE_SYMBOL.get(g["kind"], ""),
                              "\u00d7%d" % g["count"] if g["count"] > 1 else "") for g in groups[:4]]
        if len(groups) > 4:
            parts.append("+%d" % (len(groups) - 4))
        text = t("nudge_summary", list=" \u00b7 ".join(parts))

        def back_all(items=groups):
            for g in items:
                self.nudge(g["code"], g["kind"])

        actions = [(NUDGE_SYMBOL["poke"] + " " + t("nudge_back_all"), back_all),
                   ("\U0001F465", lambda: self.open_window("friends"))]
        pet = self.pet
        if pet.visible and not pet.fs_hidden:
            self._nudge_react(groups[-1]["kind"])
            if pet.say(text, actions=actions, duration=12, priority=2):
                return
        self._nudge_toast(groups[-1]["who"], text, actions)

    def _nudge_react(self, kind):
        pet = self.pet
        try:
            if kind == "poke":
                pet.set_action("startle", 0.9)
                pet._spawn("bang", 1)
                pet._voice("greet")
            elif kind == "tickle":
                pet.set_action("tickle", 1.6)
                pet._spawn("note", 2)
                pet._spawn("sparkle", 2)
                pet._voice("tickle")
                pet._bump("happiness", 2)
            elif kind == "tease":
                pet.set_action("angry", 1.3)
                pet._spawn("dots", 1)
                pet._voice("grumpy")
            elif kind == "boop":
                pet.set_action("dizzy", 1.0)
                pet._spawn("sparkle", 3)
                pet._voice("happy")
            elif kind == "hug":
                pet.set_action("love", 2.2)
                pet._spawn("heart", 5)
                pet._voice("pet")
                pet._bump("happiness", 3)
                pet._bump("bond", 0.5)
        except Exception:
            logger.exception("nudge reaction failed")

    def _nudge_toast(self, who, text, actions):
        try:
            import nudge_ui
            nudge_ui.NudgeToast(self.pet.root, who, text, actions)
        except Exception:
            logger.exception("nudge toast failed")

    def start_visit(self, who: dict):
        if len(self.visitors) >= 2 or not self.pet.visible or self.pet.fs_hidden:
            return
        try:
            import visit_ui                     # v2.20: name tag + interactive card
            greet = getattr(self.pet, "greet_visitor", None)
            if greet is not None:
                greet()                         # your pet comes down to the taskbar to meet it
            self.visitors.append(visit_ui.VisitorPet(self, who))
        except Exception:
            logger.exception("visit failed")

    # ---- main-thread pump ----
    def poll(self):
        while True:
            try:
                kind, ok, payload = self.results.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(kind, ok, payload)
            except Exception:
                logger.exception("social: handling %s failed", kind)

    def _handle(self, kind, ok, payload):
        if kind == "account":
            if ok:
                self.data.update({"id": payload["id"], "secret": payload["secret"], "code": payload["code"]})
                self.save()
            return
        if kind == "register":
            self.busy = False
        if kind == "inbox":
            self._inbox_busy = False
        if not ok:
            if payload == "unauthorized" and kind != "deleted":
                logger.warning("social: account no longer valid - turning Friends off")
                for key in ("id", "secret", "code", "groups"):
                    self.data.pop(key, None)
                self.save()
                self.pet.update_setting("friends", False)
            if kind.startswith("mp_"):
                self._mp_error(kind, payload)
                return
            if kind == "bot_reply":
                self.bot_pending = None
            if kind in ("party", "party_wish", "gift_sent"):
                self._pending_gift = None
                for win in list(self.party_windows.values()):
                    if win.alive():
                        win.on_error(payload)
                        return
                if kind == "gift_sent":
                    self.pet.say(t("gift_failed", err=payload), duration=5, priority=1)
                    return
            if kind == "sent" and payload in NUDGE_ERRORS:
                self.pet.say(t(NUDGE_ERRORS[payload]), duration=4, priority=1)
                if self.window and self.window.alive():
                    self.window.show_error(payload)
                return
            if payload == "no_location" and self.window and self.window.alive():
                self.window.show_error("no_location")
                return
            if kind in ("remind_mail", "contact_set"):
                self.staff_note = ("", 0.0)
            if kind not in ("inbox", "avatar", "leaderboard", "arena", "duel") and self.window and self.window.alive():
                self.window.show_error(payload)
            return
        if not self.enabled:
            return  # late reply after Friends was turned off
        if kind == "inbox" or (kind == "sent" and isinstance(payload, dict)):
            initial = not self.history_loaded
            self.history_loaded = True
            self._take_inbox(payload, initial=initial)
            if kind == "sent":
                pet = self.pet
                pet.set_action("hop", 0.55)
                if (payload.get("_kind") in INTERACTIONS or payload.get("_kind") in NUDGES) \
                        and not payload.get("_quiet"):
                    pet.say(t("ix_sent_" + payload["_kind"], name=self.room_title(payload["_room"])),
                            duration=3, priority=1)
                    if payload["_kind"] == "visit":
                        self.check_achievements()
        if isinstance(payload, dict) and "friends" in payload:
            self._take_board(payload)
        if kind == "bot_reply":
            key = payload.get("bot") or self.bot_pending
            self.bot_pending = None
            if key in BOT_BY_KEY:
                chats = self.data.setdefault("bot_chats", {})
                chats.setdefault(key, []).append({"mine": False, "text": payload.get("reply", "..."), "at": time.time()})
                chats[key] = chats[key][-BOT_HISTORY:]
                self.save()
            self._refresh_window()
            return
        if kind == "bday_set":
            result = payload.get("_result", {})
            if result.get("bday_m"):
                self.pet.set_action("hop", 0.55)
                self.pet.say(t("bday_saved", date=bday_text(result["bday_m"], result["bday_d"])), duration=5, priority=1)
            self._refresh_window()
            return
        if kind == "gift_sent":
            pending = getattr(self, "_pending_gift", None) or {}
            self._pending_gift = None
            shop = getattr(self.pet, "shop", None)
            if shop and pending.get("cost"):
                shop.spend(pending["cost"])
            friend = self.friend(pending.get("code", "")) or {}
            self.pet.set_action("cheer", 1.4)
            self.pet._spawn("heart", 5)
            self.pet.say(t("gift_sent", name=friend.get("name", "?")), duration=5, priority=1)
            for win in list(self.party_windows.values()):
                if win.alive():
                    win.on_gift_sent()
            self._refresh_window()
            return
        if kind == "party":
            code = (payload.get("host") or {}).get("code", "")
            self.parties[code] = dict(payload, _at=time.time())
            win = self.party_windows.get(code)
            if win and win.alive():
                win.on_party(payload)
            return
        if kind == "party_wish":
            for code, win in list(self.party_windows.items()):
                if win.alive():
                    self.load_party(code)
            return
        if kind == "gifts_claimed":
            return
        if kind == "geo":
            self.geo_results = payload.get("places", [])
            if self.window and self.window.alive():
                self.window.show_geo_results()
            return
        if kind == "location_set":
            result = payload.get("_result", {})
            self.geo_results = None
            quiet = getattr(self, "_quiet_location", False)
            self._quiet_location = False
            if result.get("loc") and not quiet:
                weather = result.get("weather") or {}
                self.pet.set_action("hop", 0.55)
                self.pet.say(t("wx_set", city=result["loc"].get("city", "?"),
                               temp=round(weather.get("temp", 0)) if weather else "?"), duration=5, priority=1)
            if self.window and self.window.alive():
                self.window.refresh(force=True)
        if kind in ("mp_room", "mp_created", "mp_joined", "mp_declined", "mp_score"):
            room = payload.get("room") if isinstance(payload, dict) else None
            if room:
                self._take_mp_room(room, open_window=kind in ("mp_created", "mp_joined"))
            if kind != "mp_score" and self.window and self.window.alive() and self.window.tab == "games":
                self.window.refresh(force=True)
            if kind == "mp_created" and payload.get("invited") is not None and room:
                self.pet.say(t("mp_created", n=payload.get("invited", 0)), duration=5, priority=1)
        elif kind == "mp_list":
            self.mp_list_data = payload.get("rooms", []) if isinstance(payload, dict) else []
            for room in self.mp_list_data:
                self.mp_rooms[room["id"]] = room
                win = minigames.party_window(room["id"], self.pet) if minigames is not None else None
                if win is not None:
                    win.take_room(room)
            if self.window and self.window.alive() and self.window.tab == "games":
                self.window.refresh(force=True)
        elif kind == "mp_left":
            if self.window and self.window.alive() and self.window.tab == "games":
                self.window.refresh(force=True)
        if kind == "arena":
            self.arena = dict(payload, _at=time.time())
            me = payload.get("me", {})
            self.data["duel_coins"] = int(me.get("duel_coins", self.data.get("duel_coins", 0)))
            self.data["rating"] = int(me.get("rating", 1000))
            self._notify_duels([{"id": c["id"], "from": c["from"]} for c in payload.get("incoming", [])])
            active = me.get("active")
            if active and active not in self.duel_windows:
                self.open_duel(active)
        elif kind == "duel":
            duel = payload.get("duel", {})
            if duel.get("id"):
                first = duel["id"] not in self.duels
                self.duels[duel["id"]] = dict(duel, _at=time.time())
                if duel.get("result", {}).get("coins"):
                    side = duel.get("mine")
                    if side:
                        self.load_arena()
                if first and duel.get("mode") == "quick" and duel["id"] not in self.duel_windows:
                    self.open_duel(duel["id"])
                win = self.duel_windows.get(duel["id"])
                if win and win.alive():
                    win.update_state()
        elif kind == "duel_challenged":
            self.pet.set_action("hop", 0.55)
            self.pet.say(t("duel_sent"), duration=5, priority=1)
            self.load_arena()
        elif kind == "duel_responded":
            if payload.get("status") == "active" and payload.get("id"):
                self.open_duel(payload["id"])
            self.load_arena()
        elif kind == "duel_forfeit":
            self.load_arena()
        if kind == "mod":
            self.mod_data = dict(payload, _at=time.time())
        elif kind == "admin":
            self.admin_data = dict(payload, _at=time.time())
        elif kind == "keys":
            self.new_keys = list(payload.get("keys", [])) + self.new_keys
            self.load_admin()
        elif kind == "reported":
            self.pet.say(t("report_sent"), duration=5, priority=1)
        elif kind == "mod_done":
            self.pet.set_action("hop", 0.55)
            if self.window and self.window.alive():
                self.window.status.config(text=t("mod_done"))
            self.load_mod()
            self.load_admin()
            self.profiles.pop(payload.get("_result", {}).get("user", {}).get("code", ""), None)
            self._refresh_social_views()
        elif kind == "admin_done":
            self.load_admin()
            self._refresh_social_views()
        elif kind == "contact_set":
            self.staff_status(t("upd_rem_saved"))
            self.load_mod()
        elif kind == "reminded":
            self.load_mod()
        elif kind == "remind_mail":
            self.pet.set_action("hop", 0.55)
            key = "upd_rem_draft" if payload.get("method") == "draft" else "upd_rem_sent"
            self.staff_status(t(key, n=payload.get("count", 0)))
            self.load_mod()
        elif kind == "redeemed":
            self._refresh_window()
        elif kind == "com_deleted":
            gone = payload.get("id")
            for room in COMMUNITY_ROOMS:
                if room in self.rooms:
                    self.rooms[room] = [m for m in self.rooms[room] if m.get("id") != gone]
            self.staff_status(t("com_deleted_ok"), 5)
        if kind == "leaderboard":
            self.leaderboards[payload.get("sort", "xp")] = dict(payload, _at=time.time())
        elif kind == "user":
            user = payload.get("user", {})
            self.profiles[user.get("code", "")] = {"full": payload.get("full"), "user": user, "at": time.time()}
        elif kind == "avatar":
            pass
        if kind == "friend_added":
            result = payload.get("_result", {})
            friend = result.get("friend", {})
            if result.get("status") == "requested":
                self.pet.set_action("hop", 0.55)
                self.pet.say(t("req_sent", name=friend.get("name", "?")), duration=4, priority=1)
            else:
                self.pet.set_action("cheer", 1.4)
                self.pet._voice("greet")
                self.pet.say(t("fr_added", name=friend.get("name", "?")), duration=4, priority=1)
                self.check_achievements()
            self._refresh_social_views(friend.get("code"))
        elif kind == "request_answered":
            result = payload.get("_result", {})
            if result.get("accepted"):
                self.pet.set_action("cheer", 1.4)
                self.pet._voice("greet")
                self.pet.say(t("fr_added", name=result.get("friend", {}).get("name", "?")), duration=4, priority=1)
                self.check_achievements()
        elif kind in ("label_set", "request_cancelled", "friend_removed"):
            self._refresh_social_views()
        elif kind in ("group_joined", "circle_joined", "circle_created"):
            self.check_achievements()
        elif kind == "circle_invited":
            self.pet.say(t("cir_invite_sent", name=payload.get("_result", {}).get("invited", "?")),
                         duration=3, priority=1)
        self._refresh_window()

    def _take_board(self, board):
        if not self.enabled:
            return
        old = {f["code"]: f for f in (self.board or {}).get("friends", [])}
        self.board = board
        self.board_at = time.time()
        self.data["code"] = board.get("me", {}).get("code", self.data.get("code"))
        me = board.get("me", {})
        role = me.get("role", "")
        was_premium = self.is_premium
        self.data["premium_until"] = int(me.get("premium_until") or 0)
        if "duel_coins" in me:
            self.data["duel_coins"] = int(me.get("duel_coins") or 0)
            self.data["rating"] = int(me.get("rating") or 1000)
        self.data["muted_until"] = int(me.get("muted_until") or 0)
        if role != self.data.get("role", ""):
            self.data["role"] = role
            if role == "owner":
                self.pet.set_action("cheer", 1.6)
                self.pet._spawn("sparkle", 8)
                self.pet.say(t("owner_welcome"), duration=7, priority=2)
                self.check_achievements(announce=False)
        if role != getattr(self, "_applied_role", None) or was_premium != self.is_premium:
            self._applied_role = role
            self._apply_owner()
        self._take_notices(board.get("notices", []))
        self._maybe_prompt_location(board)
        self._take_gifts(board.get("gifts", []))
        self._check_birthdays()
        self.data["groups"] = [g["code"] for g in board.get("groups", [])]
        self.invites = board.get("invites", self.invites)
        self._notify_requests()
        self.save()
        self._notify_invites()
        if old and self.pet.s.get("chatty", True) and time.monotonic() - self._last_news > FRIEND_NEWS_GAP_S:
            for friend in board.get("friends", []):
                before = old.get(friend["code"])
                if before and friend["today_poms"] > before["today_poms"]:
                    self._last_news = time.monotonic()
                    self.pet.say(t("fr_news", name=friend["name"], n=friend["today_poms"]),
                                 duration=5, priority=0, on_click=self.open_window)
                    break
                if before and affinity_level(friend.get("affinity", 0))[0] > affinity_level(before.get("affinity", 0))[0]:
                    self._last_news = time.monotonic()
                    self.pet.set_action("love", 2.0)
                    self.pet.say(t("aff_level_up", name=friend["name"],
                                   level=t(affinity_level(friend["affinity"])[1])),
                                 duration=6, priority=1, on_click=self.open_window)
                    break

    def _mp_error(self, kind, code):
        """Party-game request failed: tell the open party window (or the Hub)."""
        room_id = getattr(self, "_mp_open_next", None)
        wins = minigames.party_windows(self.pet) if minigames is not None else []
        if wins:
            for win in wins:
                if kind == "mp_score" and code in ("offline", "timeout"):
                    win._busy = False          # just try again with the next update
                else:
                    win.on_error(code)
            return
        if room_id and code == "not_found":
            self._mp_open_next = None
        if kind in ("mp_score", "mp_left", "mp_list"):
            return
        if self.window and self.window.alive():
            self.window.show_error(code)
        elif kind in ("mp_joined", "mp_created"):
            self.pet.say(t(MP_ERRORS[code]) if code in MP_ERRORS else t("fr_err_generic", code=code), duration=5, priority=1)

    def _refresh_window(self):
        if self.window and self.window.alive():
            self.window.refresh()

    def _refresh_social_views(self, code=None):
        """Re-fetch whatever the open window shows after a relationship change."""
        win = self.window
        if not (win and win.alive()):
            return
        if getattr(win, "tab", "") == "ranks":
            self.load_leaderboard(getattr(win, "rank_sort", "xp"))
        viewing = getattr(win, "viewing", None)
        if viewing:
            self.view_user(viewing)

    def tick(self, now: float):
        self.poll()
        self.visitors = [v for v in self.visitors if v.alive]
        if now >= self._next_ach:
            self._next_ach = now + ACH_CHECK_EVERY_S
            try:
                self.check_achievements()
            except Exception:
                logger.exception("achievement check failed")
        if not self.enabled:
            return
        if now >= self._next_sync:
            self._next_sync = now + SYNC_EVERY_S
            self._next_board = now + BOARD_EVERY_S
            self.sync()
        elif now >= self._next_board:
            self._next_board = now + BOARD_EVERY_S
            self.sync()
        if self.window and self.window.alive() and getattr(self.window, "tab", "") == "games" and now >= self._next_mp:
            self._next_mp = now + 5
            self.mp_list()
        if self.arena_open and self.window and self.window.alive() and now >= self._next_arena:
            self._next_arena = now + 3
            self.load_arena(here=True)
        fast = (self.window and self.window.alive() and self.chat_open_room) or now < self._rally_until
        if self._nudge_queue:
            self._flush_nudges()
        if now >= self._next_inbox:
            self._next_inbox = now + (INBOX_FAST_S if fast else INBOX_EVERY_S)
            self.poll_inbox()

    def on_pomodoro(self):
        try:
            self.check_achievements()
        except Exception:
            logger.exception("achievement check failed")
        if self.enabled:
            self._next_sync = time.monotonic() + SYNC_EVERY_S
            self.sync()

    # ---- window ----
    def open_window(self, tab=None, room=None):
        import social_ui
        if self.window and self.window.alive():
            self.window.win.deiconify()
            self.window.win.lift()
        else:
            self.window = social_ui.FriendsWindow(self)
        if tab:
            self.window.show_tab(tab, room=room)
        else:
            self.window.refresh()
        if self.enabled and time.time() - self.board_at > 30:
            self.sync()
        if self.enabled:
            self.poll_inbox()

    def close(self):
        if self.window and self.window.alive():
            self.window.close()
        for v in self.visitors:
            v.leave(now=True)
        for win in list(self.duel_windows.values()):
            if win.alive():
                win.close()


# =============================================================================
# A friend's pet visiting your desktop: moved to visit_ui.py in v2.20 ("Come over")
# =============================================================================

def __getattr__(name):
    """Keep `social.VisitorPet` working for older callers (PEP 562 lazy attribute)."""
    if name == "VisitorPet":
        import visit_ui
        return visit_ui.VisitorPet
    raise AttributeError("module 'social' has no attribute %r" % name)
