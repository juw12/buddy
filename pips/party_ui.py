"""Birthday party room and gift picker (Pips v2.13).

PartyWindow  - animated party scene (host pet in a party hat, guests' pets, cake,
               balloons, confetti), a guestbook of wishes and celebrate / gift buttons.
GiftDialog   - pick a shop item (shown on the friend's own pet) and/or coins,
               add a message and send it.
"""
from __future__ import annotations

import math
import random
import time
import tkinter as tk
from datetime import date

from i18n import t

try:
    import pet as pet_mod
except Exception:  # pragma: no cover
    pet_mod = None
try:
    import pet_shop
except Exception:  # pragma: no cover
    pet_shop = None
try:
    import theme as theme_mod
except Exception:  # pragma: no cover
    theme_mod = None

CONFETTI = ("#ff6b6b", "#ffd43b", "#69db7c", "#4dabf7", "#da77f2", "#ff922b", "#f783ac")
POLL_MS = 10_000
FRAME_MS = 40


def _palette() -> dict:
    if theme_mod is not None:
        try:
            return dict(theme_mod.palette())
        except Exception:
            pass
    return {"bg": "#faf8f3", "card": "#ffffff", "ink": "#1a1a1a", "muted": "#6b6b6b", "border": "#e2ddd4",
            "accent": "#8b1a1a", "accent_ink": "#ffffff", "gold": "#c8961e", "pink": "#e0557a", "soft": "#f1ece4",
            "warm": "#fbf3e6", "tile": "#f6f4f0", "entry": "#ffffff", "entry_ink": "#1a1a1a", "dark": False}


def draw_mini_pet(cv, cx, floor, size, person, props=None, t_=0.0, hop=0.0, facing=1):
    """Draw someone's pet (species/colour/look) on a canvas."""
    if pet_mod is None:
        return None
    settings = dict(pet_mod.DEFAULT_PET_SETTINGS)
    species = person.get("species") or "cat"
    person_look = person.get("look") if isinstance(person.get("look"), dict) else {}
    settings.update(species=species, color=person.get("color") or pet_mod.species_spec(species)["color"],
                    gender=person_look.get("gender", ""), stage=person_look.get("stage", "adult"))
    look = pet_mod.default_look(settings)
    tama_mod = getattr(pet_mod, "tama", None)
    if tama_mod is not None:
        size = size * tama_mod.stage_scale(look.get("stage"))     # v2.18: little ones are little
    look.update(t=t_, hop=hop, facing=facing, eyes="happy" if hop > 2 else "open", mouth="big_open" if hop > 2 else "smile",
                blush=True)
    all_props = {}
    for slot in ("head", "face", "neck", "back"):
        item = (person.get("look") or {}).get(slot)
        if item:
            all_props[slot] = item
    all_props.update(props or {})
    look["head_prop"] = bool(all_props.get("head"))
    try:
        if pet_shop is not None and all_props.get("back"):
            # back props sit behind the pet: draw once to learn the geometry, remove it, then draw in order
            existing = cv.find_all()
            last_id = existing[-1] if existing else 0
            geo = pet_mod.draw_pet(cv, cx, floor, size, look)
            for item_id in cv.find_all():
                if item_id > last_id:
                    cv.delete(item_id)
            pet_shop.draw_back_props(cv, geo, all_props, t_)
        geo = pet_mod.draw_pet(cv, cx, floor, size, look)
        if pet_shop is not None and all_props:
            pet_shop.draw_front_props(cv, geo, all_props, t_)
        return geo
    except Exception:
        return None

class PartyWindow:
    W = 520
    SCENE_H = 250

    def __init__(self, social, code: str):
        self.social = social
        self.code = code
        self.p = _palette()
        self.data = social.parties.get(code)
        self.error = ""
        self.confetti = []
        self.burst_until = 0.0
        self._alive = True
        self._poll_id = None
        self._anim_id = None
        root = social.pet.root
        self.win = tk.Toplevel(root)
        self.win.title("\U0001F389 " + t("party_title_short"))
        self.win.configure(bg=self.p["bg"])
        self.win.resizable(False, True)
        self.win.minsize(self.W, 520)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        icon = getattr(social.pet, "icon_path", None)
        if icon:
            try:
                self.win.iconbitmap(icon)
            except tk.TclError:
                pass
        self.scene = tk.Canvas(self.win, width=self.W, height=self.SCENE_H, highlightthickness=0, bg="#2b2d6e")
        self.scene.pack(fill="x")
        self.info = tk.Label(self.win, text=t("party_loading"), font=("Segoe UI", 9), bg=self.p["bg"],
                             fg=self.p["muted"])
        self.info.pack(fill="x", pady=(6, 2))
        self.actions = tk.Frame(self.win, bg=self.p["bg"])
        self.actions.pack(fill="x", padx=12, pady=4)
        composer = tk.Frame(self.win, bg=self.p["bg"])
        composer.pack(fill="x", padx=12, pady=(4, 6))
        self.wish_var = tk.StringVar()
        self.entry = tk.Entry(composer, textvariable=self.wish_var, font=("Segoe UI", 10), relief="flat",
                              bg=self.p["entry"], fg=self.p["entry_ink"], insertbackground=self.p["entry_ink"])
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self.entry.bind("<Return>", lambda e: self._send_wish())
        self._button(composer, "\U0001F48C " + t("party_send_wish"), self._send_wish, primary=True).pack(side="right")
        self.err_lbl = tk.Label(self.win, text="", font=("Segoe UI", 8), bg=self.p["bg"], fg="#c92a2a",
                                wraplength=self.W - 30, justify="left")
        self.err_lbl.pack(fill="x", padx=12)
        tk.Label(self.win, text="\U0001F4D6 " + t("party_guestbook"), font=("Segoe UI", 10, "bold"),
                 bg=self.p["bg"], fg=self.p["ink"], anchor="w").pack(fill="x", padx=12, pady=(4, 2))
        outer = tk.Frame(self.win, bg=self.p["bg"])
        outer.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.book_cv = tk.Canvas(outer, bg=self.p["bg"], highlightthickness=0, height=200)
        bar = tk.Scrollbar(outer, orient="vertical", command=self.book_cv.yview)
        self.book = tk.Frame(self.book_cv, bg=self.p["bg"])
        self.book.bind("<Configure>", lambda e: self.book_cv.configure(scrollregion=self.book_cv.bbox("all")))
        self.book_cv.create_window((0, 0), window=self.book, anchor="nw", width=self.W - 40)
        self.book_cv.configure(yscrollcommand=bar.set)
        self.book_cv.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.book_cv.bind("<MouseWheel>", lambda e: self.book_cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.win.geometry(f"{self.W}x660")
        for _ in range(40):
            self._add_confetti(initial=True)
        if self.data:
            self.on_party(self.data)
        self._poll()
        self._animate()

    # ---------------------------------------------------------- helpers
    def alive(self) -> bool:
        try:
            return self._alive and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        self._alive = False
        for job in (self._poll_id, self._anim_id):
            if job:
                try:
                    self.win.after_cancel(job)
                except tk.TclError:
                    pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.social.party_windows.pop(self.code, None)

    def _button(self, parent, text, command, primary=False):
        btn = tk.Label(parent, text=text, font=("Segoe UI", 9, "bold" if primary else "normal"),
                       fg=self.p["accent_ink"] if primary else self.p["ink"],
                       bg=self.p["accent"] if primary else self.p["soft"], padx=10, pady=4, cursor="hand2")
        btn.bind("<Button-1>", lambda e: command())
        return btn

    def _poll(self):
        if not self.alive():
            return
        self.social.load_party(self.code)
        self._poll_id = self.win.after(POLL_MS, self._poll)

    # ---------------------------------------------------------- data
    def on_party(self, data):
        if not self.alive():
            return
        self.data = data
        self.error = ""
        self.err_lbl.config(text="")
        host = data.get("host") or {}
        self.win.title("\U0001F389 " + t("party_title", name=host.get("name", "?")))
        guests = data.get("guests", [])
        here = sum(1 for g in guests if g.get("here"))
        self.info.config(text=t("party_info", guests=len(guests), here=here, wishes=len(data.get("wishes", [])),
                                gifts=data.get("gifts", 0)))
        for child in self.actions.winfo_children():
            child.destroy()
        self._button(self.actions, "\U0001F389 " + t("party_celebrate"), self._celebrate, primary=True).pack(side="left")
        if not data.get("is_host"):
            self._button(self.actions, "\U0001F381 " + t("bday_gift"),
                         lambda: self.social.open_gift(self.code)).pack(side="left", padx=6)
        self._button(self.actions, "\u21bb", lambda: self.social.load_party(self.code)).pack(side="right")
        self._render_book(data.get("wishes", []))

    def on_error(self, code):
        if not self.alive():
            return
        if code == "not_birthday":
            friend = self.social.friend(self.code) or {}
            bday = friend.get("bday") or {}
            import social as social_mod
            when = social_mod.bday_text(bday.get("m", 0), bday.get("d", 0)) if bday else "?"
            text = t("party_not_today", date=when)
            self.info.config(text=text)
        else:
            text = {"wish_limit": t("party_err_wish_limit"), "gift_limit": t("gift_err_limit"),
                    "poor": t("shop_poor"), "muted": t("fr_err_muted"),
                    "not_friends": t("fr_err_not_friends")}.get(code, t("fr_err_generic", code=code))
        self.err_lbl.config(text=text)

    def on_gift_sent(self):
        self._burst()
        self.social.load_party(self.code)

    def _render_book(self, wishes):
        for child in self.book.winfo_children():
            child.destroy()
        if not wishes:
            tk.Label(self.book, text=t("party_no_wishes"), font=("Segoe UI", 9), bg=self.p["bg"],
                     fg=self.p["muted"], anchor="w").pack(fill="x", pady=4)
            return
        for wish in wishes:
            card = tk.Frame(self.book, bg=self.p["warm"], padx=6, pady=4)
            card.pack(fill="x", pady=2)
            av = tk.Canvas(card, width=34, height=32, bg=self.p["warm"], highlightthickness=0)
            av.pack(side="left", padx=(0, 6))
            draw_mini_pet(av, 17, 30, 24, wish.get("from") or {})
            box = tk.Frame(card, bg=self.p["warm"])
            box.pack(side="left", fill="x", expand=True)
            tk.Label(box, text=(wish.get("from") or {}).get("name", "?"), font=("Segoe UI", 9, "bold"),
                     bg=self.p["warm"], fg=self.p["ink"], anchor="w").pack(fill="x")
            tk.Label(box, text=wish.get("text", ""), font=("Segoe UI", 9), bg=self.p["warm"], fg=self.p["ink"],
                     anchor="w", justify="left", wraplength=self.W - 110).pack(fill="x")

    def _send_wish(self):
        text = self.wish_var.get().strip()
        if not text:
            return
        self.wish_var.set("")
        self.social.party_wish(self.code, text)
        self._burst(small=True)

    def _celebrate(self):
        self._burst()
        self.social.pet.do_trick("confetti", force=True)

    # ---------------------------------------------------------- animation
    def _add_confetti(self, initial=False, x=None):
        self.confetti.append({
            "x": random.uniform(0, self.W) if x is None else x + random.uniform(-40, 40),
            "y": random.uniform(-self.SCENE_H, self.SCENE_H) if initial else random.uniform(-30, 0),
            "vy": random.uniform(30, 70), "vx": random.uniform(-15, 15), "rot": random.uniform(0, 6),
            "col": random.choice(CONFETTI), "w": random.uniform(3, 6),
        })

    def _burst(self, small=False):
        self.burst_until = time.monotonic() + (1.0 if small else 2.5)
        for _ in range(15 if small else 60):
            self._add_confetti(x=self.W / 2)

    def _animate(self):
        if not self.alive():
            return
        try:
            self._draw_scene()
        except tk.TclError:
            return
        self._anim_id = self.win.after(FRAME_MS, self._animate)

    def _draw_scene(self):
        cv = self.scene
        cv.delete("all")
        W, H = self.W, self.SCENE_H
        now = time.monotonic()
        # sky + floor
        for i in range(10):
            shade = 0.1 * i
            top = H * i / 10
            cv.create_rectangle(0, top, W, top + H / 10 + 1, fill=_mix("#3b2f8f", "#e64980", shade * 0.6), outline="")
        cv.create_rectangle(0, H - 46, W, H, fill="#f3d9a4", outline="")
        for x in range(0, W, 26):
            cv.create_line(x, H - 46, x + 13, H, fill="#e8c789")
        # garland
        flags = 16
        for i in range(flags):
            x0 = W * i / flags
            sag = 14 * math.sin(math.pi * i / flags)
            col = CONFETTI[i % len(CONFETTI)]
            cv.create_polygon(x0 + 4, 8 + sag, x0 + W / flags - 2, 8 + sag + 2, x0 + W / flags / 2 + 1, 26 + sag,
                              fill=col, outline="")
        cv.create_line(*[v for i in range(flags + 1) for v in (W * i / flags, 8 + 14 * math.sin(math.pi * i / flags))],
                       fill="#ffffff", smooth=True)
        # balloons
        for i, (bx, col) in enumerate(((40, "#ff6b6b"), (75, "#ffd43b"), (W - 45, "#4dabf7"), (W - 80, "#da77f2"))):
            by = 70 + math.sin(now * 1.5 + i) * 6
            cv.create_line(bx, by + 18, bx + math.sin(now + i) * 4, H - 50, fill="#dddddd")
            cv.create_oval(bx - 14, by - 18, bx + 14, by + 18, fill=col, outline="")
            cv.create_oval(bx - 7, by - 11, bx - 2, by - 4, fill="#ffffff", outline="")
        data = self.data or {}
        host = data.get("host") or self.social.friend(self.code) or {}
        title = t("party_banner", name=host.get("name", "?"))
        cv.create_text(W / 2 + 1, 46, text=title, font=("Segoe UI", 15, "bold"), fill="#1a1a1a")
        cv.create_text(W / 2, 45, text=title, font=("Segoe UI", 15, "bold"), fill="#ffffff")
        # cake
        cx, cy = W / 2 + 70, H - 60
        cv.create_rectangle(cx - 26, cy - 22, cx + 26, cy + 8, fill="#ffc9de", outline="#d6336c")
        cv.create_rectangle(cx - 26, cy - 22, cx + 26, cy - 14, fill="#ffffff", outline="")
        for k in range(3):
            fx = cx - 14 + k * 14
            cv.create_rectangle(fx - 1.5, cy - 34, fx + 1.5, cy - 22, fill="#74c0fc", outline="")
            flick = 2 + math.sin(now * 12 + k) * 1.2
            cv.create_oval(fx - 3, cy - 40 - flick, fx + 3, cy - 33, fill="#ffd43b", outline="#ff922b")
        # pets: host in the middle, guests around
        floor = H - 18
        hop_host = abs(math.sin(now * 3.0)) * 6
        draw_mini_pet(cv, W / 2 - 20, floor - hop_host, 64, host, props={"head": "party_hat"}, t_=now, hop=hop_host)
        guests = [g for g in data.get("guests", []) if g.get("code") != host.get("code")][:8]
        slots = [W / 2 - 115, W / 2 + 130, W / 2 - 185, W / 2 + 195, W / 2 - 250, W / 2 + 250, W / 2 - 60, W / 2 + 15]
        for i, guest in enumerate(guests):
            x = slots[i]
            phase = now * 3.2 + i * 0.9
            hop = abs(math.sin(phase)) * (7 if guest.get("here") else 2)
            facing = 1 if x < W / 2 - 20 else -1
            draw_mini_pet(cv, x, floor - hop, 40, guest, props={"head": "party_hat"}, t_=now + i, hop=hop,
                          facing=facing)
            cv.create_text(x, floor + 9, text=guest.get("name", "")[:10], font=("Segoe UI", 7, "bold"),
                           fill="#5c3d1e")
            if guest.get("here"):
                cv.create_oval(x + 14, floor - 44, x + 20, floor - 38, fill="#40c057", outline="#ffffff")
        # confetti
        dt = FRAME_MS / 1000.0
        alive = []
        for c in self.confetti:
            c["y"] += c["vy"] * dt
            c["x"] += c["vx"] * dt + math.sin(now * 2 + c["rot"]) * 0.6
            c["rot"] += 0.2
            if c["y"] < H:
                alive.append(c)
                w = c["w"] * abs(math.cos(c["rot"])) + 1
                cv.create_rectangle(c["x"] - w, c["y"] - 2, c["x"] + w, c["y"] + 2, fill=c["col"], outline="")
        self.confetti = alive
        target = 70 if now < self.burst_until else 40
        while len(self.confetti) < target:
            self._add_confetti()


class GiftDialog:
    W = 440

    def __init__(self, social, friend: dict):
        self.social = social
        self.friend = friend
        self.p = _palette()
        self.cat = "head"
        self.item = ""
        self.coins = 0
        root = social.pet.root
        self.win = tk.Toplevel(root)
        self.win.title("\U0001F381 " + t("gift_title", name=friend.get("name", "?")))
        self.win.configure(bg=self.p["bg"])
        self.win.geometry(f"{self.W}x640")
        self.win.minsize(self.W, 520)
        self.win.attributes("-topmost", True)
        self.win.after(400, lambda: self.win.winfo_exists() and self.win.attributes("-topmost", False))
        self.win.lift()
        self.win.focus_force()
        head = tk.Frame(self.win, bg=self.p["warm"], padx=10, pady=8)
        head.pack(fill="x")
        av = tk.Canvas(head, width=56, height=50, bg=self.p["warm"], highlightthickness=0)
        av.pack(side="left")
        draw_mini_pet(av, 28, 46, 38, friend)
        box = tk.Frame(head, bg=self.p["warm"])
        box.pack(side="left", fill="x", expand=True, padx=8)
        tk.Label(box, text=t("gift_title", name=friend.get("name", "?")), font=("Segoe UI", 12, "bold"),
                 bg=self.p["warm"], fg=self.p["ink"], anchor="w").pack(fill="x")
        self.balance_lbl = tk.Label(box, text="", font=("Segoe UI", 9), bg=self.p["warm"], fg=self.p["gold"], anchor="w")
        self.balance_lbl.pack(fill="x")
        tk.Label(box, text=t("gift_hint"), font=("Segoe UI", 8), bg=self.p["warm"], fg=self.p["muted"], anchor="w",
                 wraplength=300, justify="left").pack(fill="x")
        self.chips = tk.Frame(self.win, bg=self.p["bg"])
        self.chips.pack(fill="x", padx=10, pady=(8, 4))
        outer = tk.Frame(self.win, bg=self.p["bg"])
        outer.pack(fill="both", expand=True, padx=10)
        self.grid_cv = tk.Canvas(outer, bg=self.p["bg"], highlightthickness=0)
        bar = tk.Scrollbar(outer, orient="vertical", command=self.grid_cv.yview)
        self.grid = tk.Frame(self.grid_cv, bg=self.p["bg"])
        self.grid.bind("<Configure>", lambda e: self.grid_cv.configure(scrollregion=self.grid_cv.bbox("all")))
        self.grid_cv.create_window((0, 0), window=self.grid, anchor="nw", width=self.W - 40)
        self.grid_cv.configure(yscrollcommand=bar.set)
        self.grid_cv.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.grid_cv.bind_all("<MouseWheel>", self._wheel)
        bottom = tk.Frame(self.win, bg=self.p["bg"])
        bottom.pack(fill="x", padx=10, pady=8)
        tk.Label(bottom, text=t("gift_message"), font=("Segoe UI", 9), bg=self.p["bg"], fg=self.p["ink"],
                 anchor="w").pack(fill="x")
        self.msg_var = tk.StringVar(value=t("gift_default_msg") if social.days_until(friend.get("bday")) == 0 else "")
        tk.Entry(bottom, textvariable=self.msg_var, font=("Segoe UI", 10), relief="flat", bg=self.p["entry"],
                 fg=self.p["entry_ink"], insertbackground=self.p["entry_ink"]).pack(fill="x", ipady=4, pady=(2, 6))
        row = tk.Frame(bottom, bg=self.p["bg"])
        row.pack(fill="x")
        self.total_lbl = tk.Label(row, text="", font=("Segoe UI", 10, "bold"), bg=self.p["bg"], fg=self.p["ink"])
        self.total_lbl.pack(side="left")
        self.send_btn = tk.Label(row, text="\U0001F381 " + t("gift_send"), font=("Segoe UI", 10, "bold"),
                                 fg=self.p["accent_ink"], bg=self.p["accent"], padx=14, pady=5, cursor="hand2")
        self.send_btn.pack(side="right")
        self.send_btn.bind("<Button-1>", lambda e: self._send())
        self.err_lbl = tk.Label(bottom, text="", font=("Segoe UI", 8), bg=self.p["bg"], fg="#c92a2a", anchor="w")
        self.err_lbl.pack(fill="x")
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._render()

    def close(self):
        try:
            self.grid_cv.unbind_all("<MouseWheel>")
            self.win.destroy()
        except tk.TclError:
            pass

    def _wheel(self, event):
        try:
            self.grid_cv.yview_scroll(-1 if event.delta > 0 else 1, "units")
        except tk.TclError:
            pass

    def _items(self, cat):
        if pet_shop is None:
            return []
        if cat == "moves":
            return [(m, price) for m, (price, _s) in pet_shop.MOVES.items() if m not in pet_shop.PREMIUM_ITEMS]
        return [(k, price) for k, (slot, price) in pet_shop.PROPS.items()
                if slot == cat and k not in pet_shop.PREMIUM_ITEMS]

    def _render(self):
        balance = self.social.wallet().get("balance", 0)
        coin = pet_shop.COIN if pet_shop else ""
        self.balance_lbl.config(text=f"{coin} " + t("shop_balance", n=balance))
        for child in self.chips.winfo_children():
            child.destroy()
        cats = ("head", "face", "neck", "back", "moves", "coins")
        for n, key in enumerate(cats):
            sel = key == self.cat
            label = t("gift_cat_coins") if key == "coins" else t("shop_cat_" + key)
            chip = tk.Label(self.chips, text=label, font=("Segoe UI", 8, "bold" if sel else "normal"),
                            fg=self.p["accent_ink"] if sel else self.p["ink"],
                            bg=self.p["accent"] if sel else self.p["soft"], padx=6, pady=3, cursor="hand2")
            chip.grid(row=0, column=n, sticky="ew", padx=1)
            chip.bind("<Button-1>", lambda e, k=key: self._set_cat(k))
            self.chips.grid_columnconfigure(n, weight=1, uniform="g")
        for child in self.grid.winfo_children():
            child.destroy()
        if self.cat == "coins":
            for i, amount in enumerate(self.social.GIFT_COIN_CHOICES):
                sel = amount == self.coins
                cell = tk.Label(self.grid, text=f"{coin} {amount}", font=("Segoe UI", 12, "bold"),
                                bg=self.p["warm"] if sel else self.p["tile"], fg=self.p["gold"], pady=16,
                                cursor="hand2", relief="solid" if sel else "flat", bd=1)
                cell.grid(row=i // 2, column=i % 2, sticky="nsew", padx=3, pady=3)
                cell.bind("<Button-1>", lambda e, a=amount: self._set_coins(a))
            for c in range(2):
                self.grid.grid_columnconfigure(c, weight=1, uniform="c")
        else:
            species = self.friend.get("species") or "cat"
            color = self.friend.get("color") or "#ffc78f"
            for i, (item, price) in enumerate(self._items(self.cat)):
                sel = item == self.item
                bg = self.p["warm"] if sel else self.p["tile"]
                cell = tk.Frame(self.grid, bg=bg, padx=3, pady=3, cursor="hand2",
                                highlightthickness=2, highlightbackground=self.p["accent"] if sel else bg)
                cell.grid(row=i // 4, column=i % 4, sticky="nsew", padx=2, pady=2)
                cv = tk.Canvas(cell, width=80, height=58, bg=bg, highlightthickness=0, cursor="hand2")
                cv.pack()
                if self.cat == "moves":
                    cv.create_text(40, 30, text="\u2728", font=("Segoe UI Emoji", 20), fill=self.p["accent"])
                    name = t("mv_" + item).split(":")[0]
                else:
                    try:
                        pet_shop.draw_props_preview(cv, pet_mod, 28, species, color, {self.cat: item}, cx=40, floor=55,
                                                    t=time.monotonic())
                    except Exception:
                        pass
                    name = t("pr_" + item)
                tk.Label(cell, text=name, font=("Segoe UI", 7, "bold"), bg=bg, fg=self.p["ink"],
                         wraplength=86).pack()
                tk.Label(cell, text=f"{coin} {price}", font=("Segoe UI", 7), bg=bg, fg=self.p["gold"]).pack()
                for widget in (cell, cv) + tuple(cell.winfo_children()):
                    widget.bind("<Button-1>", lambda e, it=item: self._set_item(it))
            for c in range(4):
                self.grid.grid_columnconfigure(c, weight=1, uniform="i")
        cost = self.social.gift_cost(self.item, self.coins)
        parts = []
        if self.item:
            import social as social_mod
            parts.append(social_mod.item_name(self.item))
        if self.coins:
            parts.append(f"{coin} {self.coins}")
        what = " + ".join(parts) if parts else t("gift_pick")
        self.total_lbl.config(text=f"{what}   \u2192  {coin} {cost}",
                              fg="#c92a2a" if cost > balance else self.p["ink"])

    def _set_cat(self, key):
        self.cat = key
        self.grid_cv.yview_moveto(0)
        self._render()

    def _set_item(self, item):
        self.item = "" if self.item == item else item
        self._render()

    def _set_coins(self, amount):
        self.coins = 0 if self.coins == amount else amount
        self._render()

    def _send(self):
        err = self.social.send_gift(self.friend["code"], self.item, self.coins, self.msg_var.get().strip())
        if err:
            self.err_lbl.config(text={"poor": t("shop_poor"), "empty": t("gift_pick"),
                                      "premium_only": t("fr_err_premium_only")}.get(err, t("fr_err_generic", code=err)))
            return
        self.close()


def _mix(a: str, b: str, k: float) -> str:
    a = a.lstrip("#")
    b = b.lstrip("#")
    ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    return "#%02x%02x%02x" % (int(ra + (rb - ra) * k), int(ga + (gb - ga) * k), int(ba + (bb - ba) * k))
