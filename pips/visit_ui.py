"""A friend's pet coming over to your desktop (v2.20 "Come over").

The visitor walks up to your pet wearing a name tag. Point at it (or click it) and the tag opens a
little card: whose pet it is, how close you two are, and buttons to wave, pet it, give it a snack,
visit back or chat. It reacts to everything you do - and playing with it makes it stay longer.
"""
from __future__ import annotations

import math
import random
import time
import tkinter as tk
import tkinter.font as tkfont

try:
    import pet_shop
except ImportError:  # pragma: no cover
    pet_shop = None

try:
    import tama
except ImportError:  # pragma: no cover
    tama = None

try:
    from i18n import lines, t
except ImportError:  # pragma: no cover
    def t(key, lang=None, **kwargs):
        return key.format(**kwargs) if kwargs else key

    def lines(key):
        return [key]

ACTIONS = ("wave", "pet", "snack", "visit", "chat")
ACTION_ICON = {"wave": "\U0001F44B", "pet": "\u2665", "snack": "\U0001F36A", "visit": "\U0001F3E0",
               "chat": "\U0001F4AC"}
ACTION_COOLDOWN_S = {"wave": 4.0, "pet": 4.0, "snack": 4.0, "visit": 120.0, "chat": 1.0}  # visit: server allows 1 / 2 min
SEND_GAP_S = 20.0           # wave / pet / snack reach your friend at most this often (the visitor still reacts)
# v2.21: what the visitor says in its own species' voice (pet_sounds kinds)
VISIT_SOUNDS = {"hi": "greet", "wave": "happy", "pet": "pet", "snack": "eat", "visit": "happy"}
HINT_VISITS = 3             # on your first visits the visitor tells you to point at it
NEEDS_FRIEND = ("visit", "chat")            # the others also work for pets of people you don't know yet
PRESENCE_COLOR = {"online": "#2fb36d", "busy": "#e5484d", "away": "#f5a524", "offline": "#a8a29e"}
INK = "#2b2622"
MUTED = "#857a70"
CARD_BG = "#fffdf9"
CARD_LINE = "#e3dacd"
SHADOW = "#d2cabe"
REACTION_S = {"wave": 1.3, "pet": 1.8, "snack": 1.8, "visit": 1.5, "hi": 1.2, "bye": 1.0, "click": 0.8}


class VisitorPet:
    """A friend's pet visiting for a little while, next to your own pet."""

    DURATION_S = 25.0       # how long a visit lasts if you don't play with it
    MAX_STAY_S = 80.0       # playing makes it stay longer, up to this
    HIDE_DELAY_MS = 450

    def __init__(self, social, who: dict):
        import pet as pet_mod  # late import: pet imports social

        self.pet_mod = pet_mod
        self.social = social
        self.host = social.pet
        self.who = dict(who or {})
        self.alive = True
        host = self.host
        look = self.who.get("look") if isinstance(self.who.get("look"), dict) else {}
        self.stage = look.get("stage") if look.get("stage") in ("baby", "child", "teen") else "adult"
        stage_scale = {"baby": 0.72, "child": 0.82, "teen": 0.92}.get(self.stage, 1.0)
        base_size = pet_mod.SIZES.get(host.s.get("size"), host.size) if hasattr(pet_mod, "SIZES") else host.size
        self.size = int(base_size * 0.9 * stage_scale)
        self.u = self.size / 64.0
        species = self.who.get("species") if self.who.get("species") in pet_mod.SPECIES else "cat"
        self.species = species
        self.category = pet_mod.SPECIES[species].get("cat", "land")
        self.accent = pet_mod.safe_color(self.who.get("color"), pet_mod.SPECIES[species]["color"])
        self.props = pet_shop.clean_look(self.who.get("look")) if pet_shop is not None else {}
        self.trick = self.who.get("trick") if pet_shop is not None and self.who.get("trick") in pet_shop.MOVES else None
        self.trick_t0 = None
        self.look_base = pet_mod.default_look({"species": species, "color": self.accent,
                                               "gender": look.get("gender", ""), "stage": self.stage})
        self.look_base["head_prop"] = bool(self.props.get("head"))

        # who it belongs to
        self.code = str(self.who.get("code") or "")
        self.real_name = str(self.who.get("name") or "?")[:24]
        info = self.friend_info()
        label = str((info or {}).get("label") or "").strip()
        self.name = (label or self.real_name)[:18]
        owner_gender = look.get("owner_gender", "")
        self.badge = ""
        self.badge_color = MUTED
        if tama is not None and owner_gender in getattr(tama, "OWNER_GENDERS", ()):
            self.badge = tama.gender_symbol(owner_gender)
            self.badge_color = tama.gender_color(owner_gender)

        self.font_name = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.font_badge = tkfont.Font(family="Segoe UI Symbol", size=9, weight="bold")
        self.font_say = tkfont.Font(family="Segoe UI", size=9)
        say_texts = [text for key in ("vis_hi", "vis_wave", "vis_pet", "vis_snack", "vis_visit", "vis_bye")
                     for text in lines(key)] + [t("vis_hint")]
        say_w = max([self.font_say.measure(text) for text in say_texts] or [80])
        self.W = int(max(self.size * 2.0, self._tag_width() + 24, say_w + 34))
        self.H = int(self.size * 2.0 + 34)

        self.win = tk.Toplevel(host.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=pet_mod.KEY)
        try:
            self.win.attributes("-transparentcolor", pet_mod.KEY)
        except tk.TclError:
            pass
        self.cv = tk.Canvas(self.win, width=self.W, height=self.H, bg=pet_mod.KEY, highlightthickness=0,
                            cursor="hand2")
        self.cv.pack()
        self.cv.bind("<ButtonRelease-1>", self._on_click)
        self.cv.bind("<Enter>", self._on_enter)
        self.cv.bind("<Leave>", self._on_leave)
        self.cv.bind("<Motion>", self._on_motion)
        self._alpha = None
        self._set_alpha(0.0)                # fades in as it walks up

        now = time.monotonic()
        self.t0 = now
        self.stay_until = now + self.DURATION_S
        self.max_until = now + self.MAX_STAY_S
        self.leaving = False
        self.arrived = False
        self.arrived_at = 0.0
        others = [v for v in getattr(social, "visitors", []) if v is not self and getattr(v, "alive", False)]
        self.side = self._pick_side(others)
        self.floor = self._host_floor()
        self.cx = self._target_cx() + self.side * 170
        self.facing = -self.side
        self.fx = []                    # hearts / crumbs / sparkles: dicts
        self.say_text = ""
        self.say_until = 0.0
        self.reaction = ("", 0.0)
        self.cooldowns = {}
        self.sent_at = {}               # kind -> monotonic time it was last sent to your friend
        self.hover_pet = False
        self.hover_card = False
        self.pinned = False
        self.card = None
        self._card_job = None
        self._rub = []                  # (time, x) for rub-to-pet
        self._last_rub_pet = 0.0
        self._next_idle_heart = now + 5.0
        self._hint_at = None            # when to say "Point at me!" (first few visits only)
        self.tag_box = (0, 0, 0, 0)     # canvas bbox of the name tag (for the card's arrow)
        self._frame()

    # ------------------------------------------------------------------ info
    def friend_info(self):
        try:
            return self.social.friend(self.code) if self.code else None
        except Exception:
            return None

    def is_friend(self) -> bool:
        return bool(self.friend_info()) and bool(getattr(self.social, "enabled", False))

    def action_ready(self, kind) -> bool:
        if kind in NEEDS_FRIEND and not self.is_friend():
            return False
        return time.monotonic() >= self.cooldowns.get(kind, 0.0)

    def seconds_left(self) -> float:
        return max(0.0, self.stay_until - time.monotonic())

    # -------------------------------------------------------------- placing
    def _host_floor(self):
        host = self.host
        try:
            return host._floor_for(host.px)
        except Exception:
            wx, wy, w, h = host.window_rect()
            return wy + h

    def _monitor(self):
        try:
            return self.pet_mod.monitor_rects(self.host.px, self.floor - 10, self.host.root)[0]
        except Exception:
            return 0, 0, self.host.root.winfo_screenwidth(), self.host.root.winfo_screenheight()

    def _gap(self):
        return self.host.size * 0.85 + self.size * 0.6

    def _pick_side(self, others):
        host = self.host
        try:
            mon = self.pet_mod.monitor_rects(host.px, self._host_floor() - 10, host.root)[0]
        except Exception:
            mon = (0, 0, host.root.winfo_screenwidth(), 0)
        room_right = mon[2] - (host.px + self._gap() + self.W / 2)
        room_left = (host.px - self._gap() - self.W / 2) - mon[0]
        taken = {getattr(v, "side", 0) for v in others}
        for side in ((1, -1) if room_right >= room_left else (-1, 1)):
            room = room_right if side > 0 else room_left
            if side not in taken and room > 10:
                return side
        return 1 if room_right >= room_left else -1

    def _target_cx(self):
        mon = self._monitor()
        cx = self.host.px + self.side * self._gap()
        return max(mon[0] + self.W / 2, min(mon[2] - self.W / 2, cx))

    # ---------------------------------------------------------- interaction
    def extend(self, seconds):
        now = time.monotonic()
        self.stay_until = min(self.max_until, max(self.stay_until, now + seconds))

    def say(self, text, seconds=1.8):
        self.say_text = str(text)
        self.say_until = time.monotonic() + seconds

    def react(self, kind):
        now = time.monotonic()
        self.reaction = (kind, now)
        if kind == "pet":
            for _ in range(4):
                self._add_fx("heart")
        elif kind == "snack":
            for _ in range(6):
                self._add_fx("crumb")
        elif kind in ("visit", "wave", "hi"):
            for _ in range(3):
                self._add_fx("sparkle")
        elif kind == "click":
            self._add_fx("heart")
        key = {"hi": "vis_hi", "wave": "vis_wave", "pet": "vis_pet", "snack": "vis_snack", "visit": "vis_visit",
               "bye": "vis_bye"}.get(kind)
        if key:
            self.say(random.choice(lines(key)), seconds=1.9)
        self._speak(VISIT_SOUNDS.get(kind))

    def _speak(self, sound):
        """v2.21: the visitor answers in its own species' voice (same sound settings as your pet)."""
        if not sound:
            return False
        try:
            return bool(self.host._voice(sound, species=self.species))
        except Exception:
            return False

    def do_action(self, kind):
        """A button on the card: wave / pet / snack / visit back / chat."""
        if kind not in ACTIONS or not self.alive or self.leaving or not self.action_ready(kind):
            return False
        now = time.monotonic()
        self.cooldowns[kind] = now + ACTION_COOLDOWN_S.get(kind, 4.0)
        self.extend(12.0)
        host = self.host
        friend = self.is_friend()
        room = "dm:" + self.code
        if kind == "chat":
            try:
                self.social.open_window("chat", room=room)
            except Exception:
                pass
            self.pinned = False
            self._hide_card()
            return True
        self.react(kind)
        try:
            if kind == "wave":
                host.set_action("hop", 0.55)
            elif kind == "pet":
                host._spawn("heart", 2)
            elif kind == "visit":
                host.set_action("cheer", 1.2)
        except Exception:
            pass
        if friend and (kind == "visit" or now - self.sent_at.get(kind, -1e9) >= SEND_GAP_S):
            self.sent_at[kind] = now
            try:
                # your friend sees it too (their pet reacts); the visitor already answered, so your
                # own pet keeps quiet - except for "visit", where your pet tells you it's off
                self.social.send(room, kind, quiet=(kind != "visit"))
            except Exception:
                pass
        if self.card is not None:
            self.card.refresh()
        return True

    def _set_alpha(self, alpha):
        alpha = round(max(0.0, min(1.0, alpha)), 2)
        if alpha != self._alpha:
            self._alpha = alpha
            try:
                self.win.attributes("-alpha", alpha)
            except tk.TclError:
                pass

    def _maybe_hint(self, now):
        """First few visits: "Point at me!" - so you find the name tag's card."""
        if self._hint_at is None or now < self._hint_at:
            return
        self._hint_at = None
        if self.hover_pet or self.hover_card or self.pinned or self.leaving:
            return                          # found it already
        try:
            data = self.social.data
            data["visit_hints"] = int(data.get("visit_hints", 0)) + 1
            self.social.save()
        except Exception:
            pass
        self.say(t("vis_hint"), seconds=2.8)

    def _on_click(self, event):
        if self.leaving:
            return
        self.react("click")
        self.extend(6.0)
        tag = self.tag_box
        on_tag = tag[0] <= event.x <= tag[2] and tag[1] <= event.y <= tag[3]
        self.pinned = True if on_tag or self.card is None else not self.pinned
        if self.pinned:
            self._show_card()
        else:
            self._sync_card()

    def _on_enter(self, _event):
        self.hover_pet = True
        self._sync_card()

    def _on_leave(self, _event):
        self.hover_pet = False
        self._rub.clear()
        self._sync_card()

    def _on_motion(self, event):
        """Rub the mouse back and forth over the visitor to pet it."""
        now = time.monotonic()
        self._rub.append((now, event.x_root))
        self._rub = [(t0, x) for t0, x in self._rub if now - t0 < 1.3]
        if now - self._last_rub_pet < 2.5 or len(self._rub) < 4:
            return
        reversals = 0
        direction = 0
        anchor = self._rub[0][1]
        for _t, x in self._rub[1:]:
            delta = x - anchor
            if abs(delta) >= 6:
                d = 1 if delta > 0 else -1
                if direction and d != direction:
                    reversals += 1
                direction = d
                anchor = x
        if reversals >= 3:
            self._rub.clear()
            self._last_rub_pet = now
            self.react("pet")
            self.extend(6.0)

    def card_hover(self, inside: bool):
        self.hover_card = bool(inside)
        self._sync_card()

    def _sync_card(self):
        wanted = (self.hover_pet or self.hover_card or self.pinned) and not self.leaving
        if self._card_job is not None:
            try:
                self.host.root.after_cancel(self._card_job)
            except tk.TclError:
                pass
            self._card_job = None
        if wanted and self.card is None:
            self._card_job = self.host.root.after(140, self._show_card)
        elif not wanted and self.card is not None:
            self._card_job = self.host.root.after(self.HIDE_DELAY_MS, self._hide_card)

    def _show_card(self):
        self._card_job = None
        if not self.alive or self.leaving:
            return
        if self.card is None:
            try:
                self.card = VisitorCard(self)
            except Exception:
                self.card = None
                return
        self._place_card()

    def _hide_card(self):
        self._card_job = None
        if self.card is not None:
            self.card.destroy()
            self.card = None

    def _place_card(self):
        if self.card is None:
            return
        wx = int(self.cx - self.W / 2)
        wy = int(self.floor - self.H)
        tag = self.tag_box
        self.card.place(wx + (tag[0] + tag[2]) / 2.0, wy + tag[1] - 2, self._monitor())

    def leave(self, now=False):
        if now:
            self.alive = False
            self._hide_card()
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        elif not self.leaving:
            self.leaving = True
            self.pinned = False
            self._hide_card()
            self.react("bye")

    # ------------------------------------------------------------- drawing
    def _add_fx(self, kind):
        u = self.u
        if kind == "crumb":                 # crumbs pop out of the mouth and fall
            x = self.W / 2 + self.facing * 7 * u + random.uniform(-5, 5) * u
            y = self.H - 4 - self.size * 0.42
            vx = random.uniform(-22, 22) * u
            vy = -random.uniform(4, 16) * u
        else:                               # hearts / sparkles float up from the head
            x = self.W / 2 + random.uniform(-14, 14) * u
            y = self.H - 4 - self.size * 0.9 + random.uniform(-4, 10) * u
            vx = random.uniform(-14, 14) * u
            vy = -random.uniform(18, 30) * u
        self.fx.append({
            "kind": kind,
            "x": x,
            "y": y,
            "vx": vx,
            "vy": vy,
            "born": time.monotonic(),
            "life": {"heart": 1.6, "crumb": 0.9, "sparkle": 1.1}.get(kind, 1.2),
        })
        self.fx = self.fx[-24:]

    def _tag_width(self):
        width = 16 + 10 + self.font_name.measure(self.name)
        if self.badge:
            width += 4 + self.font_badge.measure(self.badge)
        return width

    def _draw_tag(self, cv, cx, bottom, now):
        """Pill-shaped name tag with a little arrow, coloured like the visiting pet."""
        pm = self.pet_mod
        w = self._tag_width()
        h = 20
        x0, x1 = cx - w / 2.0, cx + w / 2.0
        y1 = bottom - 6
        y0 = y1 - h
        border = pm.darken(self.accent, 0.28)
        since = now - self.arrived_at if self.arrived else 0.0
        if self.arrived and since < 3.0:              # a soft pulse right after arriving: "I'm here, say hi!"
            k = 0.5 + 0.5 * math.sin(since * 6.0)
            glow = pm.mix(pm.lighten(self.accent, 0.35), "#ffffff", 0.4 + 0.4 * k)
            cv.create_polygon(*pm.rounded_rect_points(x0 - 3, y0 - 3, x1 + 3, y1 + 3, (h + 6) / 2), smooth=True,
                              fill="", outline=glow, width=2)
        cv.create_polygon(*pm.rounded_rect_points(x0 + 1, y0 + 2, x1 + 1, y1 + 2, h / 2), smooth=True,
                          fill=SHADOW, outline="")
        cv.create_polygon(cx - 5, y1 - 1, cx + 5, y1 - 1, cx, y1 + 5, fill="#ffffff", outline=border, width=1)
        cv.create_polygon(*pm.rounded_rect_points(x0, y0, x1, y1, h / 2), smooth=True, fill="#ffffff",
                          outline=border, width=1.2)
        cv.create_line(cx - 4, y1, cx + 4, y1, fill="#ffffff", width=2)
        dot_x = x0 + 10
        dot_r = 3.4
        presence = (self.friend_info() or {}).get("presence")
        dot = PRESENCE_COLOR.get(presence, self.accent)
        cv.create_oval(dot_x - dot_r, (y0 + y1) / 2 - dot_r, dot_x + dot_r, (y0 + y1) / 2 + dot_r, fill=dot,
                       outline=pm.darken(dot, 0.2))
        tx = dot_x + 7
        cv.create_text(tx, (y0 + y1) / 2, text=self.name, anchor="w", font=self.font_name, fill=INK)
        if self.badge:
            bx = tx + self.font_name.measure(self.name) + 4
            cv.create_text(bx, (y0 + y1) / 2, text=self.badge, anchor="w", font=self.font_badge,
                           fill=self.badge_color)
        self.tag_box = (x0 - 2, y0 - 2, x1 + 2, y1 + 6)

    def _draw_say(self, cv, cx, bottom):
        pm = self.pet_mod
        w = self.font_say.measure(self.say_text) + 22
        h = 22
        x0 = max(2, min(self.W - w - 2, cx - w / 2.0))
        x1 = x0 + w
        y1 = bottom - 7
        y0 = y1 - h
        border = pm.darken(self.accent, 0.28)
        cv.create_polygon(*pm.rounded_rect_points(x0 + 1, y0 + 2, x1 + 1, y1 + 2, 10), smooth=True,
                          fill=SHADOW, outline="")
        cv.create_polygon(cx - 6, y1 - 1, cx + 3, y1 - 1, cx - 4, y1 + 6, fill="#ffffff", outline=border, width=1)
        cv.create_polygon(*pm.rounded_rect_points(x0, y0, x1, y1, 10), smooth=True, fill="#ffffff",
                          outline=border, width=1.2)
        cv.create_line(cx - 5, y1, cx + 2, y1, fill="#ffffff", width=2)
        cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=self.say_text, font=self.font_say, fill=INK)
        self.tag_box = (x0, y0, x1, y1 + 6)

    def _draw_fx(self, cv, now):
        pm = self.pet_mod
        keep = []
        for p in self.fx:
            age = now - p["born"]
            if age >= p["life"]:
                continue
            keep.append(p)
            x = p["x"] + p["vx"] * age
            y = p["y"] + p["vy"] * age + (90 * self.u * age * age if p["kind"] == "crumb" else 0)
            frac = 1.0 - age / p["life"]
            if p["kind"] == "heart":
                s = self.u * (3.0 + 2.0 * frac)
                cv.create_polygon(*pm.heart_points(x, y, s), smooth=True, fill="#ff5d8f", outline="")
            elif p["kind"] == "crumb":
                r = self.u * 1.4
                cv.create_oval(x - r, y - r, x + r, y + r, fill="#c98b4a", outline="")
            else:
                cv.create_text(x, y, text="\u2726", font=("Segoe UI Symbol", max(7, int(6 + self.size / 14))),
                               fill="#f5b700")
        self.fx = keep

    def _look(self, now, walking, goal):
        look = dict(self.look_base)
        look["t"] = now
        look["facing"] = (1 if goal > self.cx else -1) if walking else self.facing
        phase = now * 9
        if walking:
            look["foot_l"] = math.sin(phase) * 3
            look["foot_r"] = -math.sin(phase) * 3
            look["hop"] = abs(math.sin(phase)) * 2
        look["mouth"] = "smile"
        if self.category == "air":
            look["flap"] = math.sin(now * 14)
            look["hover"] = 6 + math.sin(now * 2) * 3
        elif self.category == "water":
            look["hover"] = math.sin(now * 2.2) * 1.6
        if self.hover_pet or self.hover_card:
            # looks at your mouse and blushes a little
            try:
                mx, my = self.pet_mod.cursor_pos(self.host.root)
            except Exception:
                mx, my = self.cx, self.floor - self.size
            dx = mx - self.cx
            dy = my - (self.floor - self.size * 0.6)
            dist = math.hypot(dx, dy) or 1.0
            k = min(1.0, dist / 160.0)
            look["eye_dx"] = dx / dist * 1.8 * k
            look["eye_dy"] = dy / dist * 1.3 * k
            look["blush"] = True
        elif not walking and int((now - self.t0) * 2) % 9 == 0:
            look["eyes"] = "happy"
        kind, t0 = self.reaction
        span = REACTION_S.get(kind, 1.2)
        p = (now - t0) / span if kind else 2.0
        if 0.0 <= p <= 1.0:
            bump = math.sin(p * math.pi)
            if kind in ("wave", "hi", "visit", "click"):
                look.update(eyes="happy", mouth="big_open", hop=abs(math.sin(p * math.pi * 3)) * 9,
                            lean=math.sin(now * 10) * 2, blush=True)
            elif kind == "pet":
                look.update(eyes="heart", mouth="cat", blush=True, squash=0.06 + math.sin(now * 6) * 0.03,
                            wag=math.sin(now * 12) * 3)
            elif kind == "snack":
                look.update(eyes="happy", mouth="nom", blush=True, squash=math.sin(now * 14) * 0.03)
            elif kind == "bye":
                look.update(eyes="happy", mouth="smile", hop=bump * 5)
        elif self.trick and not walking and not self.leaving and now - self.t0 > 2.5:
            if self.trick_t0 is None:
                self.trick_t0 = now
            trick_span = min(pet_shop.MOVES[self.trick][1], 4.0)
            q = (now - self.trick_t0) / trick_span
            if q <= 1.0:
                pet_shop.trick_pose(look, self.trick if self.trick not in ("spider", "moonwalk") else "dance", q, now)
        return look

    def _frame(self):
        if not self.alive:
            return
        now = time.monotonic()
        host = self.host
        if not getattr(host, "visible", True) or getattr(host, "fs_hidden", False):
            self.leave(now=True)            # your pet got hidden (or a full-screen app): pop out with it
            return
        if (self.hover_pet or self.hover_card or self.pinned) and not self.leaving:
            # while you're looking at it, it doesn't run off the moment you look away
            self.stay_until = max(self.stay_until, min(self.max_until, now + 5.0))
        elif not self.leaving and now > self.stay_until:
            self.leave()
        self.floor = self._host_floor()
        if self.card is not None and self.arrived and not self.leaving:
            goal = self.cx                  # hold still while you're using its card
        else:
            goal = self._target_cx() + (self.side * 260 if self.leaving else 0)
        walking = abs(goal - self.cx) > 2
        if self.leaving:
            self._set_alpha(abs(goal - self.cx) / 90.0)     # fades out at the end of the walk home
        else:
            self._set_alpha((now - self.t0) / 0.5)
        if walking:
            step = 3.2 if self.leaving else 2.6
            self.cx += step if goal > self.cx else -step
        elif self.leaving and now > self.say_until:
            self.leave(now=True)
            return
        else:
            self.facing = -self.side            # face your pet
            if not self.arrived:
                self.arrived = True
                self.arrived_at = now
                self.react("hi")
                try:
                    hints_shown = int(self.social.data.get("visit_hints", 0))
                except Exception:
                    hints_shown = HINT_VISITS
                if hints_shown < HINT_VISITS:
                    self._hint_at = now + 2.3
        self._maybe_hint(now)
        if self.arrived and not walking and now >= self._next_idle_heart:
            self._next_idle_heart = now + random.uniform(5, 8)
            self._add_fx("heart")
        look = self._look(now, walking, goal)
        u = self.u
        try:
            cv = self.cv
            cv.delete("all")
            floor_y = self.H - 4
            if self.props:
                pet_shop.draw_back_props(cv, self.pet_mod.body_geometry(self.W / 2, floor_y, self.size, look),
                                         self.props, now)
            geo = self.pet_mod.draw_pet(cv, self.W / 2, floor_y, self.size, look)
            if self.props:
                pet_shop.draw_front_props(cv, geo, self.props, now)
            self._draw_fx(cv, now)              # under the tag / bubble, so the text stays readable
            head_top = floor_y - self.size * 1.12 - (look.get("hop", 0.0) + look.get("hover", 0.0)) * u
            if self.say_text and now < self.say_until:
                self._draw_say(cv, self.W / 2, head_top)
            else:
                self.say_text = ""
                self._draw_tag(cv, self.W / 2, head_top, now)
            self.win.geometry("%dx%d+%d+%d" % (self.W, self.H, int(self.cx - self.W / 2), int(self.floor - self.H)))
        except tk.TclError:
            self.alive = False
            return
        if self.card is not None:
            self._place_card()
            self.card.tick()
        self.host.root.after(40, self._frame)


class VisitorCard:
    """The little card above a visitor: who it is + what you can do with it."""

    W = 268
    H = 160
    TAIL = 9

    def __init__(self, visitor: VisitorPet):
        self.v = visitor
        pm = visitor.pet_mod
        self.pm = pm
        self.win = tk.Toplevel(visitor.host.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=pm.KEY)
        try:
            self.win.attributes("-transparentcolor", pm.KEY)
        except tk.TclError:
            pass
        self.cv = tk.Canvas(self.win, width=self.W, height=self.H, bg=pm.KEY, highlightthickness=0)
        self.cv.pack()
        self.cv.bind("<Enter>", lambda e: visitor.card_hover(True))
        self.cv.bind("<Leave>", lambda e: visitor.card_hover(False))
        self.f_name = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.f_badge = tkfont.Font(family="Segoe UI Symbol", size=10, weight="bold")
        self.f_small = tkfont.Font(family="Segoe UI", size=8)
        self.f_chip = tkfont.Font(family="Segoe UI", size=8, weight="bold")
        self.f_icon = tkfont.Font(family="Segoe UI Emoji", size=13)
        self.f_label = tkfont.Font(family="Segoe UI", size=7)
        self.hover_btn = None
        self.tail_x = self.W / 2
        self.closed = False
        self._state = None
        self.born = time.monotonic()
        self._alpha = 0.0
        try:
            self.win.attributes("-alpha", 0.0)      # quick fade-in (see tick)
        except tk.TclError:
            self._alpha = 1.0
        self.buttons = {}
        for kind in ACTIONS:
            tag = "btn_" + kind
            self.cv.tag_bind(tag, "<Enter>", lambda e, k=kind: self._hover(k))
            self.cv.tag_bind(tag, "<Leave>", lambda e, k=kind: self._hover(None))
            self.cv.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=kind: self._press(k))
        self.cv.tag_bind("close", "<ButtonRelease-1>", lambda e: self._close())
        self.refresh()

    # ----------------------------------------------------------- behaviour
    def _hover(self, kind):
        self.hover_btn = kind
        self._paint_buttons()

    def _press(self, kind):
        if self.v.do_action(kind) and not self.closed:
            self.refresh()

    def _close(self):
        self.v.pinned = False
        self.v.hover_card = False
        self.v._hide_card()

    def place(self, cx, tag_top, mon):
        x = int(max(mon[0] + 4, min(mon[2] - self.W - 4, cx - self.W / 2)))
        y = int(tag_top - self.H)
        if y < mon[1] + 4:
            y = int(mon[1] + 4)
        tail_x = max(26, min(self.W - 26, cx - x))
        if abs(tail_x - self.tail_x) >= 2:
            self.tail_x = tail_x
            self.refresh()
        try:
            self.win.geometry("%dx%d+%d+%d" % (self.W, self.H, x, y))
        except tk.TclError:
            pass

    def tick(self):
        """Cheap per-frame update: fade-in, the visit-time bar and buttons coming off cooldown."""
        if self._alpha < 1.0:
            self._alpha = min(1.0, (time.monotonic() - self.born) / 0.16)
            try:
                self.win.attributes("-alpha", self._alpha)
            except tk.TclError:
                pass
        state = tuple(self.v.action_ready(k) for k in ACTIONS)
        if state != self._state:
            self.refresh()
            return
        self._paint_bar()

    def destroy(self):
        self.closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------- drawing
    def _paint_bar(self):
        v = self.v
        total = max(1.0, v.max_until - v.t0)
        frac = max(0.0, min(1.0, v.seconds_left() / min(total, v.DURATION_S + 20)))
        x0, x1 = 18, self.W - 18
        try:
            self.cv.coords("bar_fill", x0, self.H - self.TAIL - 15, x0 + (x1 - x0) * frac, self.H - self.TAIL - 11)
            self.cv.itemconfigure("bar_text", text=t("vis_stays", n=int(round(v.seconds_left()))))
        except tk.TclError:
            pass

    def _paint_buttons(self):
        pm = self.pm
        for kind, (bg_item, icon_item, label_item, ready) in self.buttons.items():
            hot = ready and kind == self.hover_btn
            fill = pm.lighten(self.v.accent, 0.62) if hot else ("#f6f1ea" if ready else "#f8f6f2")
            line = pm.darken(self.v.accent, 0.15) if hot else ("#e6ddd1" if ready else "#eeeae4")
            try:
                self.cv.itemconfigure(bg_item, fill=fill, outline=line)
                self.cv.configure(cursor="hand2" if hot else "")
            except tk.TclError:
                pass

    def refresh(self):
        if self.closed:
            return
        v = self.v
        pm = self.pm
        cv = self.cv
        W, H, tail = self.W, self.H, self.TAIL
        accent = v.accent
        info = v.friend_info() or {}
        friend = v.is_friend()
        self._state = tuple(v.action_ready(k) for k in ACTIONS)
        cv.delete("all")
        body_bottom = H - tail
        # shadow, card, arrow
        cv.create_polygon(*pm.rounded_rect_points(6, 5, W - 3, body_bottom + 2, 14), smooth=True, fill=SHADOW,
                          outline="")
        cv.create_polygon(*pm.rounded_rect_points(3, 2, W - 6, body_bottom - 1, 14), smooth=True, fill=CARD_BG,
                          outline=CARD_LINE, width=1)
        tx = self.tail_x
        cv.create_polygon(tx - 8, body_bottom - 2, tx + 8, body_bottom - 2, tx, H - 1, fill=CARD_BG,
                          outline=CARD_LINE, width=1)
        cv.create_line(tx - 7, body_bottom - 1.5, tx + 7, body_bottom - 1.5, fill=CARD_BG, width=2)
        # header band in the visitor's colour
        band = pm.lighten(accent, 0.78)
        cv.create_polygon(*pm.rounded_rect_points(4, 3, W - 7, 58, 13), smooth=True, fill=band, outline="")
        cv.create_rectangle(4, 40, W - 7, 58, fill=band, outline="")
        # mini portrait of the visiting pet
        cv.create_oval(14, 9, 58, 53, fill="#ffffff", outline=pm.darken(accent, 0.2), width=1.5)
        mini = dict(v.look_base)
        mini.update(t=time.monotonic(), eyes="happy", mouth="smile", blush=True, facing=1)
        try:
            pm.draw_pet(cv, 36, 47, 26, mini)
        except Exception:
            pass
        # name + gender, whose pet, level chip
        name_x = 66
        cv.create_text(name_x, 21, text=v.name, anchor="w", font=self.f_name, fill=INK)
        if v.badge:
            cv.create_text(name_x + self.f_name.measure(v.name) + 4, 21, text=v.badge, anchor="w",
                           font=self.f_badge, fill=v.badge_color)
        species_txt = pm.species_label(v.species)
        if v.stage != "adult" and tama is not None:
            species_txt += " \u00b7 " + t("stage_" + v.stage)
        if v.name != v.real_name:
            species_txt = v.real_name + " \u00b7 " + species_txt
        cv.create_text(name_x, 40, text=species_txt, anchor="w", font=self.f_small, fill=MUTED)
        level = info.get("level")
        if level:
            chip = "Lv %s" % level
            cw = self.f_chip.measure(chip) + 14
            cx1 = W - 32
            cv.create_polygon(*pm.rounded_rect_points(cx1 - cw, 13, cx1, 29, 8), smooth=True,
                              fill=pm.darken(accent, 0.35), outline="")
            cv.create_text(cx1 - cw / 2, 21, text=chip, font=self.f_chip, fill="#ffffff")
        # close
        cv.create_text(W - 18, 14, text="\u00d7", font=("Segoe UI", 11, "bold"), fill="#9a8f86", tags=("close",))
        # friendship + presence
        if friend:
            try:
                import social as social_mod
                _index, key, _lo, _hi = social_mod.affinity_level(info.get("affinity", 0))
                bond = "\u2665 " + t(key)
            except Exception:
                bond = "\u2665"
            cv.create_text(16, 72, text=bond, anchor="w", font=self.f_chip, fill=pm.darken(accent, 0.4))
            presence = info.get("presence") or "offline"
            dot = PRESENCE_COLOR.get(presence, PRESENCE_COLOR["offline"])
            label = t("st_" + presence)
            lw = self.f_small.measure(label)
            cv.create_text(W - 18, 72, text=label, anchor="e", font=self.f_small, fill=MUTED)
            cv.create_oval(W - 18 - lw - 12, 68, W - 18 - lw - 4, 76, fill=dot, outline="")
        else:
            cv.create_text(16, 72, text=t("vis_not_friend"), anchor="w", font=self.f_small, fill=MUTED)
        # action buttons
        self.buttons = {}
        n = len(ACTIONS)
        gap = 6
        left, right = 14, W - 17
        bw = (right - left - gap * (n - 1)) / n
        top, bottom = 84, 124
        for i, kind in enumerate(ACTIONS):
            x0 = left + i * (bw + gap)
            x1 = x0 + bw
            ready = v.action_ready(kind)
            tag = ("btn_" + kind,)
            bg = cv.create_polygon(*pm.rounded_rect_points(x0, top, x1, bottom, 9), smooth=True, fill="#f6f1ea",
                                   outline="#e6ddd1", width=1, tags=tag)
            cooling = not ready and not (kind in NEEDS_FRIEND and not friend)
            icon = "\u2713" if cooling else ACTION_ICON[kind]
            icon_item = cv.create_text((x0 + x1) / 2, top + 14, text=icon, font=self.f_icon,
                                       fill=(INK if ready else "#c4bcb2"), tags=tag)
            label_item = cv.create_text((x0 + x1) / 2, bottom - 8, text=t("vis_" + kind), font=self.f_label,
                                        fill=(MUTED if ready else "#c4bcb2"), tags=tag)
            self.buttons[kind] = (bg, icon_item, label_item, ready)
        # visit time left
        cv.create_polygon(*pm.rounded_rect_points(18, H - tail - 15, W - 18, H - tail - 11, 2), smooth=True,
                          fill="#efe8de", outline="")
        cv.create_rectangle(18, H - tail - 15, 18, H - tail - 11, fill=pm.darken(accent, 0.05), outline="",
                            tags=("bar_fill",))
        cv.create_text(W - 18, H - tail - 22, text="", anchor="e", font=self.f_label, fill=MUTED,
                       tags=("bar_text",))
        self._paint_buttons()
        self._paint_bar()
