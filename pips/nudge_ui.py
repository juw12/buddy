"""nudge_ui.py - remote pokes, tickles & teases (v2.14).

* open_nudge_menu(): the little "Poke / Tickle / Tease / Boop / Hug" picker for one friend.
* NudgeToast: an always-on-top card in the screen corner. Used only when your own pet
  is hidden - normally your pet shows the nudge in its speech bubble and reacts.
"""
from __future__ import annotations

import logging
import tkinter as tk

try:
    from i18n import t
except ImportError:  # pragma: no cover
    def t(key, lang=None, **kwargs):
        return key

try:
    import fancy_menu as fm
except ImportError:  # pragma: no cover
    fm = None

try:
    import theme as theme_mod
except ImportError:  # pragma: no cover
    theme_mod = None

logger = logging.getLogger("nudge_ui")

NUDGES = ("poke", "tickle", "tease", "boop", "hug")
NUDGE_SYMBOL = {
    "poke": "\U0001F449",
    "tickle": "\U0001F606",
    "tease": "\U0001F61C",
    "boop": "\U0001F443",
    "hug": "\U0001F917",
}


def open_nudge_menu(master, social, code: str, x: int, y: int, accent: str = "#8b1a1a"):
    """Pop up the nudge picker for friend `code` with its top-left corner at (x, y)."""
    if fm is None:
        return None
    friend = social.friend(code) or {}
    menu = fm.FancyMenu(master, accent=accent)
    menu.section(t("nudge_menu_for", name=friend.get("name", "?")))
    for kind in NUDGES:
        menu.item(NUDGE_SYMBOL[kind], t("nudge_kind_" + kind), lambda k=kind: social.nudge(code, k),
                  hint=t("nudge_hint_" + kind))
    menu.popup(x, y)
    return menu


def _palette():
    dark = bool(theme_mod and theme_mod.current_name() == "dark")
    if dark:
        return {"bg": "#2b2622", "ink": "#f3ece2", "muted": "#b8ab9b", "line": "#e0557a",
                "btn": "#3a332d", "btn_ink": "#f3ece2", "accent": "#e0557a"}
    return {"bg": "#fffaf2", "ink": "#2b2118", "muted": "#7a6a58", "line": "#e0557a",
            "btn": "#f3e7d6", "btn_ink": "#2b2118", "accent": "#8b1a1a"}


class NudgeToast:
    """Corner notification: '<friend> is teasing you!' with a back-button."""

    W = 312
    H = 96
    GAP = 10
    active: list = []

    def __init__(self, root, who: dict, text: str, actions=(), duration: float = 10.0):
        self.root = root
        self.p = _palette()
        self.alive = True
        self.alpha = 0.0
        self.remaining_ms = int(duration * 1000)
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.win.configure(bg=self.p["line"])
        card = tk.Frame(self.win, bg=self.p["bg"], padx=8, pady=8)
        card.pack(fill="both", expand=True, padx=2, pady=2)
        avatar = tk.Canvas(card, width=58, height=58, bg=self.p["bg"], highlightthickness=0)
        avatar.pack(side="left", anchor="n")
        self._draw_avatar(avatar, who)
        box = tk.Frame(card, bg=self.p["bg"])
        box.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(box, text=text, font=("Segoe UI", 10, "bold"), bg=self.p["bg"], fg=self.p["ink"],
                 anchor="w", justify="left", wraplength=self.W - 100).pack(fill="x")
        row = tk.Frame(box, bg=self.p["bg"])
        row.pack(fill="x", pady=(6, 0))
        for label, command in actions:
            button = tk.Label(row, text=label, font=("Segoe UI", 9, "bold"), bg=self.p["btn"],
                              fg=self.p["btn_ink"], padx=8, pady=2, cursor="hand2")
            button.pack(side="left", padx=(0, 6))
            button.bind("<Button-1>", lambda e, c=command: self._run(c))
        close = tk.Label(card, text="\u2715", font=("Segoe UI", 9), bg=self.p["bg"], fg=self.p["muted"],
                         cursor="hand2")
        close.place(relx=1.0, x=-2, y=-4, anchor="ne")
        close.bind("<Button-1>", lambda e: self.close())
        NudgeToast.active.append(self)
        self._place()
        self.win.after(16, self._fade_in)
        self.win.after(250, self._countdown)

    def _draw_avatar(self, canvas, who):
        try:
            from party_ui import draw_mini_pet
            draw_mini_pet(canvas, 29, 54, 42, who or {})
        except Exception:
            canvas.create_text(29, 29, text="\U0001F43E", font=("Segoe UI Emoji", 22), fill=self.p["ink"])

    def _place(self):
        self.win.update_idletasks()
        try:
            screen_w = self.win.winfo_screenwidth()
            screen_h = self.win.winfo_screenheight()
            if fm is not None:
                left, top, right, bottom = fm.work_area(self.win, screen_w - 2, screen_h - 2)
            else:
                left, top, right, bottom = 0, 0, screen_w, screen_h - 48
        except Exception:
            left, top, right, bottom = 0, 0, 1280, 720
        index = NudgeToast.active.index(self) if self in NudgeToast.active else 0
        x = right - self.W - 14
        y = bottom - (self.H + self.GAP) * (index + 1) - 4
        self.win.geometry(f"{self.W}x{self.H}+{int(x)}+{int(max(top, y))}")

    def _fade_in(self):
        if not self.alive:
            return
        self.alpha = min(0.97, self.alpha + 0.14)
        try:
            self.win.attributes("-alpha", self.alpha)
        except tk.TclError:
            return
        if self.alpha < 0.97:
            self.win.after(16, self._fade_in)

    def _pointer_inside(self) -> bool:
        try:
            x, y = self.win.winfo_pointerxy()
            left, top = self.win.winfo_rootx(), self.win.winfo_rooty()
            return left <= x <= left + self.W and top <= y <= top + self.H
        except tk.TclError:
            return False

    def _countdown(self):
        if not self.alive:
            return
        if not self._pointer_inside():
            self.remaining_ms -= 250
        if self.remaining_ms <= 0:
            self.close()
            return
        self.win.after(250, self._countdown)

    def _run(self, command):
        try:
            if command:
                command()
        except Exception:
            logger.exception("nudge toast action failed")
        self.close()

    def close(self):
        if not self.alive:
            return
        self.alive = False
        if self in NudgeToast.active:
            NudgeToast.active.remove(self)
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        for toast in list(NudgeToast.active):
            try:
                toast._place()
            except tk.TclError:
                pass
