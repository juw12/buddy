"""
arena_ui.py - the Pips Arena battle window (live duels, quick battles and spectating).

The server resolves every round (see cloud/src/index.js, v7). This window only
polls /v1/duel, sends your action (attack / guard / special) and animates the
round events: shots and lunges, damage numbers, blocks, dodges, heals and the
final result. All user-facing text comes from i18n.py.
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import messagebox

import pet_shop

try:
    from i18n import t
except ImportError:  # pragma: no cover
    def t(key, lang=None, **kwargs):
        return key.format(**kwargs) if kwargs else key

W, H = 640, 330
FLOOR = 292
SIZE = 104
LEFT_X, RIGHT_X = 175, 465
EVENT_S = 0.95          # seconds per animated round event
POLL_MS = 1000

def _untopmost(win):
    try:
        win.attributes("-topmost", False)
    except tk.TclError:
        pass


# Timers run on the app's root window (not on these short-lived windows): Tk keeps a pending timer
# after its window is destroyed, and tkinter would free the callback's command name for reuse.
SPECIAL_SYMBOL = {"power": "\u2726", "heal": "\u271a", "bomb": "\u25cf", "dodge": "\u27b6", "shield": "\u25ce"}


class DuelWindow:
    def __init__(self, soc, duel_id: str, spectate: bool = False):
        import pet as pet_mod  # late import: pet imports social

        self.soc = soc
        self.pet_mod = pet_mod
        self.duel_id = duel_id
        self.spectate = spectate
        self._alive = True
        self.state = None
        self.offset = 0.0              # server clock - local clock
        self.shown = 0                 # log events already animated
        self.queue: list = []
        self.anim = None               # {"ev": ..., "t0": ...}
        self.disp_hp = {"a": None, "b": None}
        self.floaters: list = []       # (x, y, text, colour, t0, big)
        self.acted_round = 0
        self._poll_busy = False
        self.win = tk.Toplevel(soc.pet.root)
        self.win.title(t("arena_title"))
        self.win.configure(bg="#1d1b2e")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        icon = soc.pet.cb.get("icon_path") if hasattr(soc.pet, "cb") else None
        if icon:
            try:
                self.win.iconbitmap(icon)
            except tk.TclError:
                pass
        self.cv = tk.Canvas(self.win, width=W, height=H, bg="#1d1b2e", highlightthickness=0)
        self.cv.pack()
        bar = tk.Frame(self.win, bg="#1d1b2e", padx=10, pady=6)
        bar.pack(fill="x")
        self.info = tk.Label(bar, text="", font=("Segoe UI", 10, "bold"), fg="#ffd43b", bg="#1d1b2e", anchor="w")
        self.info.pack(side="left")
        self.btns = {}
        self._build_controls(bar)
        bottom = tk.Frame(self.win, bg="#1d1b2e", padx=10)
        bottom.pack(fill="x", pady=(0, 8))
        self.log_lbl = tk.Label(bottom, text="", font=("Segoe UI", 8), fg="#c9c4e8", bg="#1d1b2e", anchor="w",
                                justify="left")
        self.log_lbl.pack(side="left", fill="x", expand=True)
        self.extra = tk.Frame(bottom, bg="#1d1b2e")
        self.extra.pack(side="right")
        self._place()
        cached = soc.duels.get(duel_id)
        if cached:
            self._take(cached, initial=True)
        soc.load_duel(duel_id)
        self.soc.pet.root.after(33, self._frame)
        self.soc.pet.root.after(POLL_MS, self._poll)

    def _build_controls(self, bar):
        for key in ("special", "guard", "attack"):
            btn = tk.Label(bar, text="", font=("Segoe UI Symbol", 10, "bold"), padx=12, pady=5, cursor="hand2")
            btn.pack(side="right", padx=(6, 0))
            btn.bind("<Button-1>", lambda e, k=key: self._act(k))
            self.btns[key] = btn

    def _ev_dur(self, ev) -> float:
        return EVENT_S

    # ---- window plumbing ----
    def alive(self) -> bool:
        return self._alive

    def close(self):
        self._alive = False
        self.soc.duel_windows.pop(self.duel_id, None)
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _place(self):
        try:
            self.win.update_idletasks()
            x = max(20, self.soc.pet.root.winfo_screenwidth() // 2 - W // 2)
            self.win.geometry(f"+{x}+120")
            self.win.lift()
            self.win.attributes("-topmost", True)
            self.soc.pet.root.after(600, lambda w=self.win: _untopmost(w))
        except tk.TclError:
            pass

    # ---- data ----
    def update_state(self):
        duel = self.soc.duels.get(self.duel_id)
        self._poll_busy = False
        if duel:
            self._take(duel)

    def _take(self, duel, initial=False):
        if duel.get("v") == 2 and not isinstance(self, SkillDuelWindow):
            # v2.24: this is a skill duel - swap to the skill battle window.
            self.soc.pet.root.after(10, lambda: self._swap_to_skills())
            return
        first = self.state is None
        self.state = duel
        self.offset = duel.get("now", time.time()) - time.time()
        log = duel.get("log", [])
        if first:
            catch_up = duel.get("mode") != "quick" and len(log) > 2
            if catch_up:
                self.shown = len(log)
                self.disp_hp = {"a": duel["a"].get("hp_now"), "b": duel["b"].get("hp_now")}
            else:
                self.disp_hp = {"a": duel["a"].get("hp"), "b": duel["b"].get("hp")}
        if len(log) > self.shown:
            self.queue.extend(log[self.shown:])
            self.shown = len(log)
        self._refresh_controls()

    def _swap_to_skills(self):
        if not self._alive:
            return
        spectate = self.spectate
        self.close()
        self.soc.open_duel(self.duel_id, spectate=spectate)

    def _poll(self):
        if not self._alive:
            return
        state = self.state or {}
        if state.get("status") in ("active", "pending") or not state:
            if not self._poll_busy:
                self._poll_busy = True
                self.soc.load_duel(self.duel_id)
                self.soc.pet.root.after(4000, self._unstick)
        self.soc.pet.root.after(POLL_MS, self._poll)

    def _unstick(self):
        self._poll_busy = False

    @property
    def mine(self):
        return None if self.spectate else (self.state or {}).get("mine")

    # ---- actions ----
    def _act(self, action):
        st = self.state or {}
        side = self.mine
        if not side or st.get("status") != "active" or self.acted_round == st.get("round"):
            return
        me = st.get(side, {})
        if me.get("acted"):
            return
        if action == "special" and me.get("charge", 0) < st.get("special_cost", 3):
            return
        self.acted_round = st.get("round")
        self.soc.duel_act(self.duel_id, st.get("round"), action)
        self.soc.pet.set_action("hop", 0.4)
        self._refresh_controls()

    def _forfeit(self):
        if messagebox.askyesno(t("arena_title"), t("duel_forfeit_q"), parent=self.win):
            self.soc.forfeit_duel(self.duel_id)
            self.soc.pet.root.after(800, lambda: self.soc.load_duel(self.duel_id))

    def _rematch(self):
        st = self.state or {}
        side = self.mine or "a"
        other = st.get("b" if side == "a" else "a", {})
        code = other.get("code")
        if not code:
            return
        if st.get("mode") == "quick":
            self.soc.quick_duel(code)
        else:
            self.soc.challenge(code)
        self.close()

    def _button_style(self, btn, text, enabled, colour):
        btn.config(text=text, bg=colour if enabled else "#3a3752", fg="#ffffff" if enabled else "#77729a",
                   cursor="hand2" if enabled else "arrow")

    def _refresh_controls(self):
        st = self.state or {}
        side = self.mine
        status = st.get("status")
        live_turn = side and status == "active"
        me = st.get(side, {}) if side else {}
        waiting = live_turn and (me.get("acted") or self.acted_round == st.get("round"))
        special = me.get("special") or "power"
        can_special = live_turn and not waiting and me.get("charge", 0) >= st.get("special_cost", 3)
        self._button_style(self.btns["attack"], "\u2694 " + t("duel_attack"), live_turn and not waiting, "#e03131")
        self._button_style(self.btns["guard"], "\u26e8 " + t("duel_guard"), live_turn and not waiting, "#1c7ed6")
        self._button_style(self.btns["special"], SPECIAL_SYMBOL.get(special, "\u2726") + " " + t("duel_sp_" + special),
                           can_special, "#9c36b5")
        for btn in self.btns.values():
            if not side or status == "done":
                btn.pack_forget()
        for child in self.extra.winfo_children():
            child.destroy()
        if status == "done" and not self.queue and not self.anim:
            if side:
                self._small_button(self.extra, "\u21bb " + t("duel_rematch"), self._rematch, "#2f9e44")
            self._small_button(self.extra, t("duel_close"), self.close, "#495057")
        elif live_turn:
            self._small_button(self.extra, t("duel_forfeit"), self._forfeit, "#495057")

    def _small_button(self, parent, text, command, colour):
        btn = tk.Label(parent, text=text, font=("Segoe UI", 8, "bold"), fg="#ffffff", bg=colour, padx=8, pady=3,
                       cursor="hand2")
        btn.pack(side="left", padx=(4, 0))
        btn.bind("<Button-1>", lambda e: command())

    # ---- animation ----
    def _frame(self):
        if not self._alive:
            return
        now = time.monotonic()
        if self.anim and now - self.anim["t0"] >= self._ev_dur(self.anim["ev"]):
            self._finish_event(self.anim["ev"])
            self.anim = None
            if not self.queue:
                self._refresh_controls()
        if not self.anim and self.queue:
            ev = self.queue.pop(0)
            self.anim = {"ev": ev, "t0": now}
            self._start_event(ev)
        try:
            self._draw(now)
            self._update_info()
        except tk.TclError:
            return
        self.soc.pet.root.after(33, self._frame)

    def _pos(self, side):
        return LEFT_X if side == "a" else RIGHT_X

    def _float(self, side, text, colour, big=False, dy=0):
        now = time.monotonic()
        x = self._pos(side)
        base_x = self._pos(side)
        stacked = sum(1 for f in self.floaters if abs(f[0] - base_x) < 120 and now - f[4] < 1.0)
        toward_centre = 1 if side == "a" else -1
        x = base_x + toward_centre * 70 * stacked
        self.floaters.append((x, FLOOR - SIZE * 1.2 + dy + stacked * 16, text, colour, now, big))

    def _start_event(self, ev):
        s = ev.get("s")
        act = ev.get("act")
        if act in ("afk", "forfeit"):
            self._float(s, t("duel_afk") if act == "afk" else t("duel_gave_up"), "#adb5bd", True)
        elif act == "guard":
            self._float(s, "\u26e8 " + t("duel_guard"), "#74c0fc")
        elif ev.get("special"):
            self._float(s, SPECIAL_SYMBOL.get(ev["special"], "\u2726") + " " + t("duel_sp_" + ev["special"]), "#e599f7",
                        False, -18)

    def _finish_event(self, ev):
        s = ev.get("s")
        o = "b" if s == "a" else "a"
        if ev.get("heal"):
            self._float(s, f"+{ev['heal']}", "#69db7c", True)
            self.disp_hp[s] = min((self.state or {}).get(s, {}).get("hp", 9999), (self.disp_hp[s] or 0) + ev["heal"])
        if ev.get("act") in ("attack", "special"):
            if ev.get("shield"):
                self._float(o, "\u25ce " + t("duel_shielded"), "#d0bfff")
            elif ev.get("dodge"):
                self._float(o, t("duel_miss"), "#ced4da")
            elif ev.get("dmg") is not None:
                if ev.get("crit"):
                    self._float(o, t("duel_crit") + f" -{ev['dmg']}", "#ffd43b", True)
                elif ev.get("blocked"):
                    self._float(o, t("duel_block") + f" -{ev['dmg']}", "#74c0fc")
                else:
                    self._float(o, f"-{ev['dmg']}", "#ff6b6b", True)
        if "hp_a" in ev:
            self.disp_hp["a"] = ev["hp_a"]
            self.disp_hp["b"] = ev["hp_b"]
        self.soc.pet.set_action("hop", 0.3) if ev.get("crit") else None

    def _update_info(self):
        st = self.state or {}
        status = st.get("status")
        side = self.mine
        if not st:
            text = t("fr_connecting")
        elif status == "pending":
            text = t("duel_waiting_accept")
        elif status in ("expired", "declined"):
            text = t("duel_expired")
        elif status == "done" and (self.queue or self.anim):
            current = (self.anim or {}).get("ev", {}).get("r", 1)
            text = t("duel_round", n=current, max=st.get("max_rounds", 12)) + "  \u00b7  \u25b6"
        elif status == "done":
            text = t("duel_round", n=st.get("round", 0), max=st.get("max_rounds", 12)) + "  \u00b7  " + t("duel_over")
        else:
            remaining = max(0, int(st.get("deadline", 0) - (time.time() + self.offset)))
            text = t("duel_round", n=st.get("round", 1), max=st.get("max_rounds", 12)) + f"  \u00b7  \u23f1 {remaining}s"
            if side:
                me = st.get(side, {})
                waiting = me.get("acted") or self.acted_round == st.get("round")
                text += "  \u00b7  " + (t("duel_wait_opp") if waiting else t("duel_choose"))
            else:
                text += "  \u00b7  \U0001F441 " + t("duel_spectating")
        self.info.config(text=text)
        lines = []
        for ev in (st.get("log") or [])[max(0, self.shown - len(self.queue) - 3):self.shown - len(self.queue)]:
            lines.append(self._log_line(ev))
        self.log_lbl.config(text="\n".join(lines[-3:]))

    def _log_line(self, ev):
        st = self.state or {}
        who = st.get(ev.get("s"), {}).get("name", "?")
        act = ev.get("act")
        if act == "guard":
            return t("log_duel_guard", who=who, r=ev.get("r"))
        if act in ("afk", "forfeit"):
            return t("log_duel_" + act, who=who, r=ev.get("r"))
        if ev.get("dodge"):
            return t("log_duel_miss", who=who, r=ev.get("r"))
        text = t("log_duel_hit", who=who, r=ev.get("r"), dmg=ev.get("dmg", 0))
        if ev.get("special"):
            text = t("duel_sp_" + ev["special"]) + ": " + text
        if ev.get("heal"):
            text += f" (+{ev['heal']})"
        if ev.get("crit"):
            text += " \u2605"
        return text

    # ---- drawing ----
    def _draw(self, now):
        cv = self.cv
        cv.delete("all")
        st = self.state or {}
        # sky + crowd + floor
        bands = ("#2b2849", "#322e57", "#3a3565", "#433d73")
        for i, col in enumerate(bands):
            cv.create_rectangle(0, i * 40, W, (i + 1) * 40, fill=col, outline="")
        for i in range(34):
            x = 10 + i * 19
            bob = math.sin(now * 3 + i) * 2
            col = ("#ff8787", "#ffd43b", "#69db7c", "#74c0fc", "#e599f7", "#ffa94d")[i % 6]
            cv.create_oval(x - 6, 150 + bob - 6, x + 6, 150 + bob + 6, fill=col, outline="")
            cv.create_rectangle(x - 7, 156 + bob, x + 7, 172, fill=col, outline="")
        cv.create_rectangle(0, 170, W, H, fill="#c9a66b", outline="")
        cv.create_oval(60, FLOOR - 28, W - 60, FLOOR + 40, fill="#d9b77e", outline="#a8844a", width=3)
        cv.create_text(W / 2, 92, text="\u2694  PIPS ARENA  \u2694", fill="#ffe066", font=("Segoe UI", 13, "bold"))
        if not st.get("a"):
            cv.create_text(W / 2, H / 2, text=t("fr_connecting"), fill="#ffffff", font=("Segoe UI", 12))
            return
        anim = self.anim
        p = min(1.0, (now - anim["t0"]) / self._ev_dur(anim["ev"])) if anim else 0.0
        ev = anim["ev"] if anim else {}
        offsets = {"a": 0.0, "b": 0.0}
        aims = {"a": 0.0, "b": 0.0}
        hits = {"a": 0.0, "b": 0.0}
        attacker = ev.get("s")
        defender = "b" if attacker == "a" else "a"
        kind = st.get(attacker, {}).get("weapon_kind", "paw") if attacker else "paw"
        striking = anim and ev.get("act") in ("attack", "special") and not ev.get("heal") or (ev.get("heal") and ev.get("dmg"))
        if anim and striking:
            direction = 1 if attacker == "a" else -1
            if kind in ("melee", "paw"):
                lunge = math.sin(min(1.0, p / 0.7) * math.pi)
                offsets[attacker] = direction * lunge * (RIGHT_X - LEFT_X) * 0.42
            else:
                aims[attacker] = min(1.0, p * 4) if p < 0.75 else max(0.0, 1 - (p - 0.75) * 4)
                offsets[attacker] = -direction * 6 * math.sin(min(1.0, max(0.0, (p - 0.3) / 0.2)) * math.pi)
            if p > 0.55 and ev.get("dmg") and not ev.get("dodge"):
                hits[defender] = math.sin(min(1.0, (p - 0.55) / 0.45) * math.pi)
                offsets[defender] += (1 if defender == "b" else -1) * 8 * hits[defender]
            if ev.get("dodge") and p > 0.4:
                offsets[defender] += (1 if defender == "b" else -1) * 26 * math.sin(min(1.0, (p - 0.4) / 0.6) * math.pi)
        for side, facing in (("a", 1), ("b", -1)):
            fighter = st.get(side, {})
            x = self._pos(side) + offsets[side]
            extra = {}
            if st.get("status") == "done" and not self.queue and not anim:
                if st.get("winner") == side:
                    extra = {"eyes": "happy", "mouth": "big_open", "hop": abs(math.sin(now * 5)) * 6}
                elif st.get("winner") not in (side, "draw"):
                    extra = {"eyes": "closed", "mouth": "wavy", "droop": 0.5}
            if anim and ev.get("act") == "guard" and ev.get("s") == side:
                cv.create_oval(x - 52, FLOOR - 110, x + 52, FLOOR + 6, outline="#74c0fc", width=3)
            pet_shop.draw_fighter(cv, self.pet_mod, x, FLOOR, SIZE, fighter, facing=facing, t=now, aim=aims[side],
                                  hit=hits[side], look_extra=extra)
            if anim and ev.get("shield") and side == defender and p > 0.45:
                cv.create_oval(x - 56, FLOOR - 116, x + 56, FLOOR + 8, outline="#b197fc", width=4, dash=(6, 3))
        # projectile
        if anim and striking and kind not in ("melee", "paw") and 0.25 < p < 0.62:
            q = (p - 0.25) / 0.37
            x0 = self._pos(attacker) + (1 if attacker == "a" else -1) * 55
            x1 = self._pos(defender) - (1 if attacker == "a" else -1) * 30
            x = x0 + (x1 - x0) * q
            y = FLOOR - 60 - (math.sin(q * math.pi) * 40 if kind == "launcher" or ev.get("special") == "bomb" else 0)
            weapon = st.get(attacker, {}).get("gear", {}).get("weapon", "")
            if ev.get("special") == "bomb":
                cv.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#74c0fc", outline="#1864ab", width=2)
            elif weapon in ("laser_blaster", "plasma_cannon"):
                col = "#ff6b6b" if weapon == "laser_blaster" else "#b197fc"
                cv.create_line(x - 22, y, x + 22, y, fill=col, width=5, capstyle="round")
                cv.create_line(x - 18, y, x + 18, y, fill="#ffffff", width=2)
            elif weapon == "rocket_launcher":
                cv.create_polygon(x - 14, y - 5, x + 10, y - 5, x + 18, y, x + 10, y + 5, x - 14, y + 5, fill="#5c940d",
                                  outline="#2b5000")
                cv.create_oval(x - 22, y - 4, x - 14, y + 4, fill="#ff922b", outline="")
            elif weapon == "bubble_blaster":
                for k in range(3):
                    cv.create_oval(x - 6 - k * 12, y - 6 + k * 3, x + 6 - k * 12, y + 6 + k * 3, outline="#f783ac", width=2)
            elif weapon == "slingshot":
                cv.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#868e96", outline="#343a40")
            else:
                cv.create_oval(x - 6, y - 4, x + 6, y + 4, fill="#4dabf7", outline="#1864ab")
                cv.create_oval(x - 16, y - 2, x - 9, y + 3, fill="#a5d8ff", outline="")
        if anim and ev.get("special") == "bomb" and 0.6 < p < 0.95:
            r = 20 + (p - 0.6) * 160
            x = self._pos(defender)
            cv.create_oval(x - r, FLOOR - 60 - r, x + r, FLOOR - 60 + r, outline="#4dabf7", width=4)
        if anim and ev.get("heal"):
            x = self._pos(attacker)
            for k in range(5):
                yy = FLOOR - 30 - ((now * 60 + k * 25) % 100)
                cv.create_text(x - 40 + k * 20, yy, text="\u271a", fill="#69db7c", font=("Segoe UI Symbol", 12, "bold"))
        self._draw_bars(st)
        self._draw_floaters(now)
        if st.get("status") == "done" and not self.queue and not anim:
            self._draw_result(st)

    def _draw_bars(self, st):
        cv = self.cv
        for side, x0, anchor in (("a", 14, "w"), ("b", W - 14, "e")):
            f = st.get(side, {})
            max_hp = max(1, f.get("hp", 1))
            hp = self.disp_hp.get(side)
            hp = max_hp if hp is None else hp
            frac = max(0.0, min(1.0, hp / max_hp))
            bw = 250
            left = x0 if side == "a" else x0 - bw
            cv.create_rectangle(left, 14, left + bw, 70, fill="#14122a", outline="#4b4680", width=2)
            role = {"owner": " \u265b", "mod": " \U0001F6E1"}.get(f.get("role", ""), " \u2605" if f.get("premium") else "")
            cv.create_text(left + 8, 26, text=f"{f.get('name', '?')}{role}", anchor="w", fill="#ffffff",
                           font=("Segoe UI Symbol", 10, "bold"))
            cv.create_text(left + bw - 8, 26, text=t("arena_lv_power", level=f.get("level", 1), power=f.get("power", 0)),
                           anchor="e", fill="#ffd43b", font=("Segoe UI", 8, "bold"))
            colour = "#51cf66" if frac > 0.5 else "#fcc419" if frac > 0.25 else "#ff6b6b"
            cv.create_rectangle(left + 8, 36, left + bw - 8, 50, fill="#3b3760", outline="")
            fill_left = left + 8 if side == "a" else left + bw - 8 - (bw - 16) * frac
            cv.create_rectangle(fill_left, 36, fill_left + (bw - 16) * frac, 50, fill=colour, outline="")
            cv.create_text(left + bw / 2, 43, text=f"{int(hp)} / {max_hp}", fill="#ffffff", font=("Segoe UI", 8, "bold"))
            cost = st.get("special_cost", 3)
            charge = f.get("charge", 0)
            pips = "".join("\u25cf" if i < min(charge, cost) else "\u25cb" for i in range(cost))
            stats = f"\u2694{f.get('atk', 0)}  \u26e8{f.get('def', 0)}  \u2605{f.get('crit', 0)}%  " + pips
            cv.create_text(left + 8, 61, text=stats, anchor="w", fill="#c9c4e8", font=("Segoe UI Symbol", 8))

    def _draw_floaters(self, now):
        keep = []
        for (x, y, text, colour, t0, big) in self.floaters:
            age = now - t0
            if age > 1.4:
                continue
            keep.append((x, y, text, colour, t0, big))
            self.cv.create_text(x + 1, y - age * 26 + 1, text=text, fill="#000000",
                                font=("Segoe UI Symbol", 15 if big else 11, "bold"))
            self.cv.create_text(x, y - age * 26, text=text, fill=colour, font=("Segoe UI Symbol", 15 if big else 11, "bold"))
        self.floaters = keep

    def _draw_result(self, st):
        cv = self.cv
        side = self.mine
        winner = st.get("winner")
        result = st.get("result") or {}
        if side:
            key = "duel_draw" if winner == "draw" else "duel_win" if winner == side else "duel_lose"
            title = t(key)
            coins = (result.get("coins") or {}).get(side, 0)
            rating = (result.get("rating") or {}).get(side, 0)
            sub = t("duel_reward", coins=coins, rating=("+" if rating >= 0 else "") + str(rating))
        else:
            name = st.get(winner, {}).get("name", "?") if winner in ("a", "b") else ""
            title = t("duel_winner", name=name) if name else t("duel_draw")
            sub = ""
        colour = "#ffd43b" if (not side or winner == side) else "#ced4da" if winner == "draw" else "#ff8787"
        cv.create_rectangle(W / 2 - 170, 82, W / 2 + 170, 150 if sub else 128, fill="#14122a", outline=colour, width=3)
        cv.create_text(W / 2, 104, text=title, fill=colour, font=("Segoe UI", 18, "bold"))
        if sub:
            cv.create_text(W / 2, 132, text=sub, fill="#ffffff", font=("Segoe UI", 10, "bold"))



# =============================================================================
# v2.24 skill duels: bring 3 skills, take turns, best-of rounds (server v14)
# =============================================================================
SK_EVENT_S = {"round": 1.9, "round_start": 1.3, "stunned": 0.8}
AUTO_DELAY_S = 0.8          # auto battle: short pause so you can see whose turn it is
FX_ICONS = (("guard", "\u26e8", "#74c0fc"), ("focus", "\U0001F3AF", "#ffd43b"), ("stun", "\U0001F4AB", "#ffe066"),
            ("evade", "\U0001F680", "#a5d8ff"), ("block", "\u25ce", "#d0bfff"))
STAT_ROWS = (("level", "Lv"), ("hp", "\u2764 HP"), ("atk", "\u2694 ATK"), ("def", "\u26e8 DEF"),
             ("spd", "\u27b6 SPD"), ("crit", "\u2605 CRIT"))


def skill_name(skill: str) -> str:
    icon = pet_shop.SKILLS.get(skill, ("\u2694",))[0]
    return f"{icon} {t('sk_' + skill)}"


def skill_numbers(skill: str) -> str:
    """Short stat line for a skill tile, e.g. 'x1.7 · 70% · ⏳1' (damage, hit chance, cooldown)."""
    info = pet_shop.SKILLS.get(skill)
    if not info:
        return ""
    st = info[2]
    parts = []
    if st.get("power"):
        hits = st.get("hits", 1)
        parts.append((f"{hits}\u00d7" if hits > 1 else "\u00d7") + f"{st['power']:g}")
    if st.get("acc"):
        parts.append(f"{st['acc']}%")
    if st.get("heal"):
        parts.append(f"+{round(st['heal'] * 100)}%\u2764")
    if st.get("guard"):
        parts.append(f"\u26e8{round(st['guard'] * 100)}%")
    if st.get("focus"):
        parts.append("\U0001F3AF")
    if st.get("cd"):
        parts.append(f"\u23f3{st['cd']}")
    return " \u00b7 ".join(parts)


def skill_colour(skill: str) -> str:
    info = pet_shop.SKILLS.get(skill)
    if not info:
        return "#e03131"
    st = info[2]
    if not isinstance(info[1], int):
        return "#9c36b5"            # gear skills
    if st.get("heal") and not st.get("power"):
        return "#2f9e44" if not st.get("guard") else "#1c7ed6"
    if st.get("guard") or st.get("focus"):
        return "#1c7ed6"
    return "#e03131"


def skill_lock_text(skill: str) -> str:
    unlock = pet_shop.SKILLS[skill][1]
    if isinstance(unlock, int):
        return t("sk_lock_lv", n=unlock)
    kind, value = unlock
    if kind == "weapon":
        return t("sk_lock_melee") if "melee" in value else t("sk_lock_gun")
    return t("sk_lock_tool", name=t("duel_sp_" + value))


def auto_pick(state: dict, side: str):
    """Auto battle: choose one of my ready skills like a sensible player would.
    Returns the index into state["skills"][side] (or None if nothing is usable)."""
    skills = list(((state or {}).get("skills") or {}).get(side) or [])
    ready = [(i, s.get("id")) for i, s in enumerate(skills) if int(s.get("cd", 0) or 0) == 0 and s.get("id") in pet_shop.SKILLS]
    if not ready:
        return None
    other = "b" if side == "a" else "a"
    me = (state or {}).get(side) or {}
    op = (state or {}).get(other) or {}
    my_max = max(1, me.get("hp", 1) or 1)
    my_hp = me.get("hp_now", my_max)
    op_hp = op.get("hp_now", op.get("hp", 1)) or 1
    focused = bool((((state or {}).get("fx") or {}).get(side) or {}).get("focus"))

    def expected(skill):
        st = pet_shop.SKILLS[skill][2]
        if not st.get("power"):
            return 0.0
        dmg = (me.get("atk", 10) or 10) * st["power"] * 1.6 * st.get("hits", 1) * st.get("acc", 90) / 100.0
        if not st.get("pierce"):
            dmg *= 100.0 / (100.0 + (op.get("def", 0) or 0) * 4)
        if st.get("sure_crit"):
            dmg *= 1.7
        if focused:
            dmg *= 1.5
        return dmg

    heals = [(i, s) for i, s in ready if pet_shop.SKILLS[s][2].get("heal") and not pet_shop.SKILLS[s][2].get("power")
             and not pet_shop.SKILLS[s][2].get("guard")]
    attacks = sorted(((expected(s), i, s) for i, s in ready if expected(s) > 0), reverse=True)
    # 1) finish them off with the most reliable hit that is enough
    finishers = [(pet_shop.SKILLS[s][2].get("acc", 90), i) for dmg, i, s in attacks if dmg >= op_hp]
    if finishers:
        return max(finishers)[1]
    # 2) low on HP: heal, else guard
    if my_hp / my_max < 0.35:
        if heals:
            return heals[0][0]
        guards = [i for i, s in ready if pet_shop.SKILLS[s][2].get("guard")]
        if guards and my_hp / my_max < 0.2:
            return guards[0]
    # 3) charge up with Focus when the fight is still long
    focus = [i for i, s in ready if pet_shop.SKILLS[s][2].get("focus")]
    if focus and not focused and attacks and op_hp > attacks[0][0] * 2.5:
        return focus[0]
    # 4) otherwise the hardest expected hit
    if attacks:
        return attacks[0][1]
    return ready[0][0]


class BattleSetup:
    """Before a skill duel: compare both fighters' stats, pick exactly 3 skills and (challenger) the rounds."""
    BG = "#1d1b2e"
    CARD = "#2b2849"
    INK = "#ffffff"
    MUTED = "#a9a3d1"
    GOOD = "#69db7c"
    BAD = "#ff8787"
    COLS = 4

    def __init__(self, soc, opponent: dict, mode: str = "challenge", on_confirm=None, rounds=None, parent=None):
        self.soc = soc
        self.opponent = dict(opponent or {})
        self.mode = mode                      # challenge | quick | accept
        self.on_confirm = on_confirm
        self.level = soc.my_level()
        self.gear = soc.my_gear()
        self.unlocked = set(pet_shop.unlocked_skills(self.level, self.gear))
        self.picked = [s for s in soc.duel_loadout() if s in self.unlocked][:3]
        self.rounds = rounds if rounds in pet_shop.DUEL_ROUND_CHOICES else soc.duel_rounds()
        self.tiles = {}
        self.round_btns = {}
        self.confirmed = False
        self.win = tk.Toplevel(parent or soc.pet.root)
        self.win.title(t("sk_setup_title"))
        self.win.configure(bg=self.BG)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        icon = soc.pet.cb.get("icon_path") if hasattr(soc.pet, "cb") else None
        if icon:
            try:
                self.win.iconbitmap(icon)
            except tk.TclError:
                pass
        self._build()
        self._refresh()
        self._place(parent)

    # ---- helpers ----
    def _label(self, parent, text, size=9, bold=False, fg=None, bg=None, **kw):
        return tk.Label(parent, text=text, font=("Segoe UI Symbol", size, "bold" if bold else "normal"),
                        fg=fg or self.INK, bg=bg or self.BG, **kw)

    def _place(self, parent):
        try:
            self.win.update_idletasks()
            x = max(20, self.soc.pet.root.winfo_screenwidth() // 2 - self.win.winfo_width() // 2)
            self.win.geometry(f"+{x}+80")
            self.win.lift()
            self.win.attributes("-topmost", True)
            self.soc.pet.root.after(600, lambda w=self.win: _untopmost(w))
            self.win.focus_force()
        except tk.TclError:
            pass

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def opponent_stats(self) -> dict:
        opp = self.opponent
        stats = opp.get("stats")
        if isinstance(stats, dict) and stats.get("hp"):
            return stats
        if opp.get("hp") and opp.get("atk") is not None:
            return {k: opp.get(k) for k in ("hp", "atk", "def", "crit", "spd", "level", "special", "weapon_kind")}
        if opp.get("level"):
            return pet_shop.fighter_stats(int(opp.get("level") or 1), opp.get("gear") or {})
        return {}

    # ---- layout ----
    def _build(self):
        opp_name = self.opponent.get("name", "?")
        head = {"quick": "\u26a1 " + t("sk_setup_quick", name=opp_name),
                "accept": "\u2694 " + t("sk_setup_accept", name=opp_name)}.get(
            self.mode, "\u2694 " + t("sk_setup_challenge", name=opp_name))
        outer = tk.Frame(self.win, bg=self.BG, padx=14, pady=12)
        outer.pack(fill="both", expand=True)
        self._label(outer, head, size=12, bold=True, fg="#ffe066").pack(anchor="w")
        # stats comparison
        box = tk.Frame(outer, bg=self.CARD, padx=10, pady=8)
        box.pack(fill="x", pady=(8, 0))
        mine = pet_shop.fighter_stats(self.level, self.gear)
        breakdown = pet_shop.stat_breakdown(self.level, self.gear)
        theirs = self.opponent_stats()
        self._label(box, "", bg=self.CARD).grid(row=0, column=0, sticky="w")
        self._label(box, t("sk_you"), bold=True, bg=self.CARD, fg="#ffd43b").grid(row=0, column=1, sticky="w", padx=8)
        self._label(box, opp_name, bold=True, bg=self.CARD, fg="#ffd43b").grid(row=0, column=2, sticky="w", padx=8)
        for r, (key, title) in enumerate(STAT_ROWS, start=1):
            self._label(box, title, size=9, bg=self.CARD, fg=self.MUTED).grid(row=r, column=0, sticky="w")
            mv = mine.get(key, 0)
            ov = theirs.get(key) if theirs else None
            colour = self.INK
            if isinstance(ov, (int, float)) and isinstance(mv, (int, float)) and mv != ov:
                colour = self.GOOD if mv > ov else self.BAD
            unit = "%" if key == "crit" else ""
            text = f"{mv:g}{unit}"
            if key in breakdown:
                lvl_part, gear_part = breakdown[key]
                if gear_part:
                    text += "   " + t("sk_breakdown", lv=f"{lvl_part:g}", gear=f"{gear_part:+g}")
            self._label(box, text, size=9, bold=True, bg=self.CARD, fg=colour).grid(row=r, column=1, sticky="w", padx=8)
            other = "?" if ov is None else f"{ov:g}{unit}"
            self._label(box, other, size=9, bold=True, bg=self.CARD).grid(row=r, column=2, sticky="w", padx=8)
        gear_names = [t("gr_" + self.gear[slot]) for slot in pet_shop.GEAR_SLOTS if self.gear.get(slot)]
        self._label(box, (t("arena_gear", items=", ".join(gear_names)) if gear_names else t("arena_no_gear")),
                    size=8, bg=self.CARD, fg=self.MUTED, wraplength=520, justify="left").grid(
            row=len(STAT_ROWS) + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._label(box, t("sk_stats_note"), size=8, bg=self.CARD, fg=self.MUTED, wraplength=520,
                    justify="left").grid(row=len(STAT_ROWS) + 2, column=0, columnspan=3, sticky="w")
        # skills
        top = tk.Frame(outer, bg=self.BG)
        top.pack(fill="x", pady=(10, 4))
        self.count_lbl = self._label(top, "", size=10, bold=True)
        self.count_lbl.pack(side="left")
        self._label(top, t("sk_legend"), size=8, fg=self.MUTED).pack(side="right")
        grid = tk.Frame(outer, bg=self.BG)
        grid.pack(fill="x")
        for i, skill in enumerate(pet_shop.SKILLS):
            tile = tk.Label(grid, text="", font=("Segoe UI Symbol", 8, "bold"), width=19, height=2, padx=4, pady=3,
                            justify="center", cursor="hand2")
            tile.grid(row=i // self.COLS, column=i % self.COLS, padx=2, pady=2, sticky="nsew")
            tile.bind("<Button-1>", lambda e, s=skill: self.toggle(s))
            tile.bind("<Enter>", lambda e, s=skill: self._describe(s))
            self.tiles[skill] = tile
        self.desc = self._label(outer, t("sk_pick_hint"), size=8, fg="#c9c4e8", wraplength=540, justify="left",
                                anchor="w")
        self.desc.pack(fill="x", pady=(4, 0))
        # rounds
        rrow = tk.Frame(outer, bg=self.BG)
        rrow.pack(fill="x", pady=(10, 0))
        self._label(rrow, t("sk_rounds_label"), size=10, bold=True).pack(side="left")
        if self.mode == "accept":
            self._label(rrow, "  " + t("sk_best_of", n=self.rounds) + "  \u00b7  " + t("sk_rounds_theirs"),
                        size=9, fg=self.MUTED).pack(side="left")
        else:
            for n in pet_shop.DUEL_ROUND_CHOICES:
                btn = tk.Label(rrow, text=t("sk_best_of", n=n), font=("Segoe UI", 9, "bold"), padx=10, pady=3,
                               cursor="hand2")
                btn.pack(side="left", padx=(6, 0))
                btn.bind("<Button-1>", lambda e, k=n: self.set_rounds(k))
                self.round_btns[n] = btn
        # auto battle
        self.auto_var = tk.BooleanVar(value=bool(self.soc.data.get("duel_auto")))
        tk.Checkbutton(outer, text="\U0001F916 " + t("sk_auto_setup"), variable=self.auto_var,
                       font=("Segoe UI Symbol", 9), fg="#e9e6ff", bg=self.BG, selectcolor="#3a3752",
                       activebackground=self.BG, activeforeground="#ffffff", anchor="w",
                       cursor="hand2").pack(fill="x", pady=(8, 0))
        # footer
        foot = tk.Frame(outer, bg=self.BG)
        foot.pack(fill="x", pady=(12, 0))
        self.go_btn = tk.Label(foot, text="", font=("Segoe UI", 10, "bold"), padx=16, pady=5, cursor="hand2")
        self.go_btn.pack(side="right")
        self.go_btn.bind("<Button-1>", lambda e: self.confirm())
        cancel = tk.Label(foot, text=t("sk_cancel"), font=("Segoe UI", 9), fg="#ffffff", bg="#495057", padx=12,
                          pady=5, cursor="hand2")
        cancel.pack(side="right", padx=(0, 6))
        cancel.bind("<Button-1>", lambda e: self.close())
        self.win.bind("<Return>", lambda e: self.confirm())
        self.win.bind("<Escape>", lambda e: self.close())

    def _describe(self, skill):
        text = skill_name(skill) + " \u2014 " + t("sk_" + skill + "_d")
        if skill not in self.unlocked:
            text += "  (\U0001F512 " + skill_lock_text(skill) + ")"
        try:
            self.desc.config(text=text)
        except tk.TclError:
            pass

    def _refresh(self):
        for skill, tile in self.tiles.items():
            name = skill_name(skill)
            if skill not in self.unlocked:
                tile.config(text=f"\U0001F512 {t('sk_' + skill)}\n{skill_lock_text(skill)}", bg="#2a2840",
                            fg="#6f6a92", cursor="arrow", relief="flat")
            elif skill in self.picked:
                n = self.picked.index(skill) + 1
                tile.config(text=f"{n}. {name}\n{skill_numbers(skill)}", bg=skill_colour(skill), fg="#ffffff",
                            cursor="hand2", relief="solid")
            else:
                tile.config(text=f"{name}\n{skill_numbers(skill)}", bg="#3a3752", fg="#e9e6ff", cursor="hand2",
                            relief="flat")
        self.count_lbl.config(text=t("sk_pick_count", n=len(self.picked)))
        for n, btn in self.round_btns.items():
            active = n == self.rounds
            btn.config(bg="#f59f00" if active else "#3a3752", fg="#1d1b2e" if active else "#e9e6ff")
        ready = len(self.picked) == 3
        self.go_btn.config(text="\u2694 " + t("sk_fight"), bg="#e03131" if ready else "#3a3752",
                           fg="#ffffff" if ready else "#77729a", cursor="hand2" if ready else "arrow")

    # ---- actions ----
    def toggle(self, skill):
        if skill not in self.unlocked:
            self._describe(skill)
            return
        if skill in self.picked:
            self.picked.remove(skill)
        else:
            if len(self.picked) >= 3:
                self.picked.pop(0)
            self.picked.append(skill)
        self._describe(skill)
        self._refresh()

    def set_rounds(self, n):
        if n in pet_shop.DUEL_ROUND_CHOICES and self.mode != "accept":
            self.rounds = n
            self._refresh()

    def confirm(self):
        if len(self.picked) != 3 or self.confirmed:
            return
        self.confirmed = True
        skills = pet_shop.clean_skills(self.picked, self.level, self.gear)
        self.soc.data["duel_auto"] = bool(self.auto_var.get())
        self.soc.save_duel_setup(skills, None if self.mode == "accept" else self.rounds)
        callback = self.on_confirm
        self.close()
        if callback:
            callback(skills, self.rounds)


class SkillDuelWindow(DuelWindow):
    """Turn-based skill duel: on your turn pick one of your 3 skills; a round ends at a knock-out and the
    match is best of 1, 3 or 5 rounds."""

    def __init__(self, soc, duel_id: str, spectate: bool = False):
        self.tiles = []
        self.sent_turn = None
        self.sent_at = 0.0
        self.banner = None
        self.disp_wins = {"a": 0, "b": 0}
        self.auto = bool(soc.data.get("duel_auto"))
        self._auto_due = 0.0
        super().__init__(soc, duel_id, spectate)
        self.win.bind("<a>", lambda e: self.toggle_auto())
        self.win.title(t("arena_title") + " \u00b7 " + t("sk_title"))
        for i in range(3):
            self.win.bind(str(i + 1), lambda e, k=i: self._use_skill(k))

    def _build_controls(self, bar):
        row = tk.Frame(self.win, bg="#1d1b2e", padx=10)
        row.pack(fill="x")
        self.skill_row = row
        for i in range(3):
            tile = tk.Label(row, text="", font=("Segoe UI Symbol", 9, "bold"), width=24, height=2, pady=4,
                            justify="center", bg="#1d1b2e", cursor="arrow")
            tile.pack(side="left", padx=(0 if i == 0 else 6, 0), expand=True, fill="x")
            tile.bind("<Button-1>", lambda e, k=i: self._use_skill(k))
            self.tiles.append(tile)

    def _ev_dur(self, ev) -> float:
        return SK_EVENT_S.get(ev.get("act"), EVENT_S)

    # ---- data ----
    def _take(self, duel, initial=False):
        first = self.state is None
        super()._take(duel, initial)
        if first:
            log = duel.get("log", [])
            caught_up = bool(log) and self.shown == len(log) and not self.queue
            self.disp_wins = dict(duel.get("wins") or {"a": 0, "b": 0}) if caught_up else {"a": 0, "b": 0}

    def _my_skills(self) -> list:
        side = self.mine
        if not side:
            return []
        return list(((self.state or {}).get("skills") or {}).get(side) or [])

    def can_act(self) -> bool:
        st = self.state or {}
        side = self.mine
        if not side or st.get("status") != "active" or st.get("turn") != side:
            return False
        if self.queue or self.anim:
            return False
        if self.sent_turn == st.get("round") and time.monotonic() - self.sent_at < 6:
            return False
        return True

    def _use_skill(self, index):
        if not self.can_act():
            return
        skills = self._my_skills()
        if index >= len(skills) or int(skills[index].get("cd", 0)) > 0:
            return
        st = self.state or {}
        self.sent_turn = st.get("round")
        self.sent_at = time.monotonic()
        self.soc.duel_skill(self.duel_id, skills[index]["id"], st.get("round"))
        self.soc.pet.set_action("hop", 0.4)
        self._refresh_controls()

    def toggle_auto(self):
        if not self.mine:
            return
        self.auto = not self.auto
        self.soc.data["duel_auto"] = self.auto
        try:
            self.soc.save()
        except Exception:
            pass
        self._auto_due = 0.0
        self._refresh_controls()

    def _auto_tick(self):
        """Auto battle: on my turn (after the animations) pick a skill by myself."""
        if not self.auto or not self.can_act():
            self._auto_due = 0.0
            return
        now = time.monotonic()
        if not self._auto_due:
            self._auto_due = now + AUTO_DELAY_S
            return
        if now >= self._auto_due:
            self._auto_due = 0.0
            index = auto_pick(self.state or {}, self.mine)
            if index is not None:
                self._use_skill(index)

    def _rematch(self):
        st = self.state or {}
        side = self.mine or "a"
        other = dict(st.get("b" if side == "a" else "a", {}))
        code = other.get("code")
        if not code:
            return
        mode = "quick" if st.get("mode") == "quick" else "challenge"
        soc = self.soc

        def go(skills, rounds):
            if mode == "quick":
                soc.quick_duel(code, rounds=rounds, skills=skills)
            else:
                soc.challenge(code, rounds=rounds, skills=skills)

        BattleSetup(soc, other, mode=mode, on_confirm=go, rounds=st.get("rounds"))
        self.close()

    def _refresh_controls(self):
        st = self.state or {}
        side = self.mine
        status = st.get("status")
        skills = self._my_skills()
        can = self.can_act()
        show = bool(side) and status == "active"
        for i, tile in enumerate(self.tiles):
            if not show or i >= len(skills):
                tile.config(text="", bg="#1d1b2e", cursor="arrow")
                continue
            skill = skills[i]["id"]
            cd = int(skills[i].get("cd", 0) or 0)
            sub = ("\u23f3 " + t("sk_cooldown", n=cd)) if cd > 0 else skill_numbers(skill)
            enabled = can and cd == 0
            tile.config(text=f"{i + 1}. {skill_name(skill)}\n{sub}",
                        bg=skill_colour(skill) if enabled else "#3a3752",
                        fg="#ffffff" if enabled else "#8a85ad", cursor="hand2" if enabled else "arrow")
        for child in self.extra.winfo_children():
            child.destroy()
        if status == "done" and not self.queue and not self.anim:
            if side:
                self._small_button(self.extra, "\u21bb " + t("duel_rematch"), self._rematch, "#2f9e44")
            self._small_button(self.extra, t("duel_close"), self.close, "#495057")
        elif side and status == "active":
            self._small_button(self.extra, "\U0001F916 " + (t("sk_auto_on") if self.auto else t("sk_auto_off")),
                               self.toggle_auto, "#7048e8" if self.auto else "#495057")
            self._small_button(self.extra, t("duel_forfeit"), self._forfeit, "#495057")

    # ---- events ----
    def _start_event(self, ev):
        st = self.state or {}
        act = ev.get("act")
        s = ev.get("s")
        if act == "round":
            winner = ev.get("winner")
            name = st.get(winner, {}).get("name", "?") if winner in ("a", "b") else ""
            text = t("sk_round_won", n=ev.get("rnd", 1), name=name) if name else t("sk_round_draw", n=ev.get("rnd", 1))
            self.banner = (text, "#ffd43b" if winner == self.mine or not self.mine else "#ff8787")
            if winner and winner == self.mine:
                self.soc.pet.set_action("hop", 0.6)
        elif act == "round_start":
            self.banner = (t("sk_round_fight", n=ev.get("rnd", 1)), "#ffffff")
        elif act == "stunned":
            self._float(s, "\U0001F4AB " + t("sk_stunned"), "#ffe066", True)
        elif act in ("afk", "forfeit"):
            super()._start_event(ev)
        elif s in ("a", "b"):
            self._float(s, skill_name(ev.get("skill") or "strike"), "#e599f7", False, -18)

    def _finish_event(self, ev):
        s = ev.get("s")
        o = "b" if s == "a" else "a"
        act = ev.get("act")
        if act == "attack":
            if ev.get("shield"):
                self._float(o, "\u25ce " + t("duel_shielded"), "#d0bfff")
            elif ev.get("dodge"):
                self._float(o, t("sk_dodged"), "#ced4da")
            elif ev.get("miss"):
                self._float(o, t("duel_miss"), "#ced4da")
            else:
                dmg = ev.get("dmg", 0)
                landed = [h for h in (ev.get("hits") or []) if h]
                prefix = f"{len(landed)}\u00d7 " if len(landed) > 1 else ""
                if ev.get("crit"):
                    self._float(o, t("duel_crit") + f" {prefix}-{dmg}", "#ffd43b", True)
                elif ev.get("blocked"):
                    self._float(o, t("duel_block") + f" -{dmg}", "#74c0fc")
                else:
                    self._float(o, f"{prefix}-{dmg}", "#ff6b6b", True)
            if ev.get("stun"):
                self._float(o, "\U0001F4AB " + t("sk_stunned"), "#ffe066")
        elif act == "guard":
            self._float(s, "\u26e8 " + t("duel_guard"), "#74c0fc")
        elif act == "focus":
            self._float(s, "\U0001F3AF " + t("sk_focused"), "#ffd43b")
        if ev.get("heal"):
            self._float(s, f"+{ev['heal']}", "#69db7c", True)
        if act == "round" and isinstance(ev.get("wins"), dict):
            self.disp_wins = dict(ev["wins"])
        if act in ("round", "round_start"):
            self.banner = None
        if "hp_a" in ev:
            self.disp_hp["a"] = ev["hp_a"]
            self.disp_hp["b"] = ev["hp_b"]
        if ev.get("crit"):
            self.soc.pet.set_action("hop", 0.3)

    def _update_info(self):
        self._auto_tick()
        st = self.state or {}
        status = st.get("status")
        side = self.mine
        rounds = st.get("rounds", 3)
        if not st:
            text = t("fr_connecting")
        elif status == "pending":
            text = t("duel_waiting_accept") + "  \u00b7  " + t("sk_best_of", n=rounds)
        elif status in ("expired", "declined"):
            text = t("duel_expired")
        elif self.queue or self.anim:
            ev = (self.anim or {}).get("ev", {})
            text = t("sk_round", n=ev.get("rnd") or st.get("rnd", 1), total=rounds) + "  \u00b7  \u25b6"
        elif status == "done":
            wins = st.get("wins") or {}
            text = t("sk_score", a=wins.get("a", 0), b=wins.get("b", 0)) + "  \u00b7  " + t("duel_over")
        else:
            remaining = max(0, int(st.get("deadline", 0) - (time.time() + self.offset)))
            text = t("sk_round", n=st.get("rnd", 1), total=rounds) + f"  \u00b7  \u23f1 {remaining}s  \u00b7  "
            turn = st.get("turn")
            if side and turn == side and self.auto:
                text += "\U0001F916 " + t("sk_auto_playing")
            elif side and turn == side:
                text += t("sk_your_turn")
            else:
                text += t("sk_their_turn", name=st.get(turn, {}).get("name", "?"))
                if not side:
                    text += "  \u00b7  \U0001F441"
        self.info.config(text=text)
        lines = []
        for ev in (st.get("log") or [])[max(0, self.shown - len(self.queue) - 3):self.shown - len(self.queue)]:
            lines.append(self._log_line(ev))
        self.log_lbl.config(text="\n".join(lines[-3:]))

    def _log_line(self, ev):
        st = self.state or {}
        act = ev.get("act")
        who = st.get(ev.get("s"), {}).get("name", "?")
        if act == "round":
            winner = ev.get("winner")
            if winner in ("a", "b"):
                return t("sk_round_won", n=ev.get("rnd", 1), name=st.get(winner, {}).get("name", "?"))
            return t("sk_round_draw", n=ev.get("rnd", 1))
        if act == "round_start":
            return t("sk_round_fight", n=ev.get("rnd", 1))
        if act in ("afk", "forfeit"):
            return t("log_duel_" + act, who=who, r=ev.get("r"))
        if act == "stunned":
            return t("sk_log_stunned", who=who)
        skill = t("sk_" + (ev.get("skill") or "strike"))
        if act == "guard":
            text = t("sk_log_guard", who=who, skill=skill)
        elif act == "focus":
            text = t("sk_log_focus", who=who, skill=skill)
        elif act == "heal":
            text = t("sk_log_heal", who=who, skill=skill, n=ev.get("heal", 0))
            return text
        elif ev.get("shield"):
            text = t("sk_log_shield", who=who, skill=skill)
        elif ev.get("dodge"):
            text = t("sk_log_dodge", who=who, skill=skill)
        elif ev.get("miss"):
            text = t("sk_log_miss", who=who, skill=skill)
        else:
            text = t("sk_log_hit", who=who, skill=skill, dmg=ev.get("dmg", 0))
            if ev.get("crit"):
                text += " \u2605"
            if ev.get("stun"):
                text += " \U0001F4AB"
        if ev.get("heal"):
            text += f" (+{ev['heal']})"
        return text

    # ---- drawing ----
    def _draw(self, now):
        super()._draw(now)
        st = self.state or {}
        if not st.get("a"):
            return
        cv = self.cv
        anim = self.anim
        ev = anim["ev"] if anim else {}
        rounds = st.get("rounds", 3)
        rnd = ev.get("rnd") or st.get("rnd", 1)
        cv.create_text(W / 2, 114, text=t("sk_round", n=rnd, total=rounds), fill="#ffffff",
                       font=("Segoe UI", 9, "bold"))
        # whose turn
        if st.get("status") == "active" and not anim and not self.queue and st.get("turn") in ("a", "b"):
            x = self._pos(st["turn"])
            y = FLOOR - SIZE * 1.32 + math.sin(now * 5) * 4
            cv.create_polygon(x - 10, y - 12, x + 10, y - 12, x, y + 2, fill="#ffd43b", outline="#e67700", width=2)
        # status effects (guard, focus, stun, jetpack, shield)
        fx = st.get("fx") or {}
        if not anim and not self.queue:
            for side in ("a", "b"):
                icons = [(sym, col) for key, sym, col in FX_ICONS if (fx.get(side) or {}).get(key)]
                for k, (sym, col) in enumerate(icons):
                    x = self._pos(side) + (-70 if side == "a" else 70)
                    y = FLOOR - 92 + k * 22
                    cv.create_oval(x - 11, y - 11, x + 11, y + 11, fill="#14122a", outline=col, width=2)
                    cv.create_text(x, y, text=sym, fill=col, font=("Segoe UI Symbol", 9, "bold"))
        # round banners
        if anim and ev.get("act") in ("round", "round_start") and self.banner:
            text, colour = self.banner
            cv.create_rectangle(W / 2 - 200, 124, W / 2 + 200, 168, fill="#14122a", outline=colour, width=3)
            cv.create_text(W / 2, 146, text=text, fill=colour, font=("Segoe UI Symbol", 13, "bold"))

    def _draw_bars(self, st):
        cv = self.cv
        need = max(1, int(st.get("need", 2) or 2))
        for side, x0 in (("a", 14), ("b", W - 14)):
            f = st.get(side, {})
            max_hp = max(1, f.get("hp", 1))
            hp = self.disp_hp.get(side)
            hp = max_hp if hp is None else hp
            frac = max(0.0, min(1.0, hp / max_hp))
            bw = 250
            left = x0 if side == "a" else x0 - bw
            cv.create_rectangle(left, 14, left + bw, 70, fill="#14122a", outline="#4b4680", width=2)
            role = {"owner": " \u265b", "mod": " \U0001F6E1"}.get(f.get("role", ""), " \u2605" if f.get("premium") else "")
            cv.create_text(left + 8, 26, text=f"{f.get('name', '?')}{role}", anchor="w", fill="#ffffff",
                           font=("Segoe UI Symbol", 10, "bold"))
            wins = int(self.disp_wins.get(side, 0) or 0)
            trophies = "\u25cf" * min(wins, need) + "\u25cb" * max(0, need - wins)
            cv.create_text(left + bw - 8, 26, text=f"Lv {f.get('level', 1)}  {trophies}", anchor="e", fill="#ffd43b",
                           font=("Segoe UI Symbol", 9, "bold"))
            colour = "#51cf66" if frac > 0.5 else "#fcc419" if frac > 0.25 else "#ff6b6b"
            cv.create_rectangle(left + 8, 36, left + bw - 8, 50, fill="#3b3760", outline="")
            fill_left = left + 8 if side == "a" else left + bw - 8 - (bw - 16) * frac
            cv.create_rectangle(fill_left, 36, fill_left + (bw - 16) * frac, 50, fill=colour, outline="")
            cv.create_text(left + bw / 2, 43, text=f"{int(hp)} / {max_hp}", fill="#ffffff", font=("Segoe UI", 8, "bold"))
            stats = (f"\u2694{f.get('atk', 0)}  \u26e8{f.get('def', 0)}  \u27b6{f.get('spd', 0)}  "
                     f"\u2605{f.get('crit', 0)}%")
            cv.create_text(left + 8, 61, text=stats, anchor="w", fill="#c9c4e8", font=("Segoe UI Symbol", 8))

    def _draw_result(self, st):
        super()._draw_result(st)
        wins = st.get("wins") or {}
        text = "\U0001F3C6 " + t("sk_score", a=wins.get("a", 0), b=wins.get("b", 0))
        self.cv.create_rectangle(W / 2 - 80, 156, W / 2 + 80, 178, fill="#14122a", outline="#ffd43b", width=2)
        self.cv.create_text(W / 2, 167, text=text, fill="#ffd43b", font=("Segoe UI Symbol", 10, "bold"))
