"""Animated, card-style right-click menus for Pips.

A FancyMenu is a borderless rounded popup drawn on a single Canvas:
header with stats, a pulsing highlight banner (used for updates),
quick-action tiles, rows with icons / hints / badges, animated toggle
switches, colour swatches, segmented pickers and fly-out submenus.

Usage:
    menu = FancyMenu(master, accent="#8b1a1a")
    menu.header("Pip", "Cat - Happy", avatar="P", stats=[("\u2665", 80, "#e0557a")])
    menu.banner("Update 2.11.0 is ready!", "Click to see what's new", on_click)
    menu.tiles([("\U0001F36A", "Snack", feed)])
    menu.item("\u2699", "Settings", open_settings)
    menu.popup(x_root, y_root)
"""
from __future__ import annotations

import logging
import math
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

logger = logging.getLogger(__name__)

KEY = "#010203"          # transparent colour key (rounded corners)
FRAME_MS = 30
ROW_H = 30
TILE_H = 56
TILE_GAP = 6
PAD = 6
ICON_FONT = "Segoe UI Emoji"
TEXT_FONT = "Segoe UI"
BANNER_A = "#ff8a00"
BANNER_B = "#e52e71"

_active_root = None


# ---------------------------------------------------------------- helpers
IDLE_CLOSE_S = 8.0   # menu closes itself when the mouse has been away this long

def _rgb(color: str) -> tuple:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, amount: float) -> str:
    """Blend colour a towards b (amount 0 = a, 1 = b)."""
    amount = max(0.0, min(1.0, amount))
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return "#%02x%02x%02x" % (
        round(ra + (rb - ra) * amount),
        round(ga + (gb - ga) * amount),
        round(ba + (bb - ba) * amount),
    )


def luminance(color: str) -> float:
    r, g, b = _rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def ink_on(color: str) -> str:
    return "#1a1a1a" if luminance(color) > 0.62 else "#ffffff"


def usable_accent(color: str, dark: bool) -> str:
    """Pet colours can be very pale (white cat) - keep the accent readable."""
    try:
        lum = luminance(color)
    except (ValueError, TypeError):
        return "#8b1a1a"
    if not dark and lum > 0.72:
        return mix(color, "#000000", 0.45 if lum > 0.88 else 0.3)
    if dark and lum < 0.25:
        return mix(color, "#ffffff", 0.35)
    return color


def rounded(cv, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(points, smooth=True, splinesteps=10, **kw)


def palette(dark: bool, accent: str) -> dict:
    if dark:
        base = "#1f2127"
        return {
            "bg": base, "border": "#3b3e47", "ink": "#ecebe7", "muted": "#9a978f",
            "hover": mix(accent, base, 0.70), "tile": "#292c33", "tile_hover": mix(accent, "#292c33", 0.55),
            "sep": "#33363e", "danger": "#ff6b6b", "track": "#3f424b", "accent": accent,
            "header": mix(accent, base, 0.62), "bar_bg": "#353841", "section": "#8d8a83",
        }
    base = "#ffffff"
    return {
        "bg": base, "border": "#ddd8cf", "ink": "#1d1d1f", "muted": "#7a766f",
        "hover": mix(accent, base, 0.86), "tile": "#f5f3ef", "tile_hover": mix(accent, base, 0.76),
        "sep": "#eeeae4", "danger": "#c62828", "track": "#d6d2cb", "accent": accent,
        "header": mix(accent, base, 0.82), "bar_bg": "#ffffff", "section": "#9a958c",
    }


def work_area(widget, x: int, y: int) -> tuple:
    """Usable area (left, top, right, bottom) of the monitor containing x, y."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            monitor = ctypes.windll.user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rect = info.rcWork
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def _mouse_buttons_down() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return bool((user32.GetAsyncKeyState(0x01) & 0x8000) or (user32.GetAsyncKeyState(0x02) & 0x8000))
    except Exception:
        return False


def _safe(command, *args):
    if not command:
        return
    try:
        command(*args)
    except Exception as exc:
        logger.warning("Menu command failed: %s", exc)


def close_active():
    global _active_root
    if _active_root is not None:
        _active_root.close()
        _active_root = None


# ---------------------------------------------------------------- menu
class FancyMenu:
    def __init__(self, master, accent: str = "#8b1a1a", dark: bool | None = None,
                 width: int = 276, parent: "FancyMenu | None" = None):
        if dark is None:
            try:
                import theme as theme_mod
                dark = theme_mod.current_name() == "dark"
            except Exception:
                dark = False
        self.master = master
        self.parent = parent
        self.root_menu = parent.root_menu if parent else self
        # every submenu ever opened under this root - closed together with it (no orphans)
        self._family = []
        if parent is not None:
            self.root_menu._family.append(self)
        self._outside_since = None
        self.dark = bool(dark)
        self.raw_accent = accent
        self.accent = usable_accent(accent, self.dark)
        self.p = palette(self.dark, self.accent)
        self.width = width
        self.entries: list[dict] = []
        self.win = None
        self.cv = None
        self.child: FancyMenu | None = None
        self.child_index = None
        self.hover = None              # (entry index, sub index)
        self.pressed = None
        self.progress: dict = {}       # (entry, sub) -> 0..1 hover animation
        self.closed = False
        self.height = 0
        self.x = 0
        self.y = 0
        self._opened_at = 0.0
        self._slide = 0.0
        self._target_y = 0
        self._armed = False
        self._child_timer = None
        self._close_child_timer = None
        self.on_close = None
        self.fonts = {}

    # ------------------------------------------------------------ builders
    def _add(self, kind: str, height: int, **data) -> dict:
        entry = {"kind": kind, "h": height, **data}
        self.entries.append(entry)
        return entry

    def header(self, title, subtitle="", avatar="", stats=None, avatar_color=None, badge=None):
        """badge: optional (symbol, colour) drawn right after the title, e.g. ("\u2640", "#ec4899")."""
        height = 82 if stats else 62
        return self._add("header", height, title=title, subtitle=subtitle, avatar=avatar,
                         stats=stats or [], avatar_color=avatar_color, badge=badge)

    def banner(self, title, subtitle, command, icon="\u2B06", badge="NEW"):
        return self._add("banner", 62, title=title, subtitle=subtitle, command=command,
                         icon=icon, badge=badge, selectable=True)

    def tiles(self, items):
        """items: (icon, label, command[, badge])"""
        items = [tuple(item) + (None,) * (4 - len(item)) for item in items]
        rows = max(1, math.ceil(len(items) / 4))
        height = rows * TILE_H + (rows - 1) * TILE_GAP + 10
        return self._add("tiles", height, items=items, selectable=True)

    def section(self, label):
        return self._add("section", 22, label=label)

    def separator(self):
        return self._add("sep", 9)

    def item(self, icon, label, command=None, hint="", badge=None, danger=False,
             keep_open=False, highlight=False):
        return self._add("item", ROW_H, icon=icon, label=label, command=command, hint=hint,
                         badge=badge, danger=danger, keep_open=keep_open, highlight=highlight,
                         selectable=True)

    def choice(self, icon, label, selected, command):
        return self._add("choice", ROW_H, icon=icon, label=label, selected=bool(selected),
                         command=command, selectable=True)

    def toggle(self, icon, label, value, command, close=False):
        return self._add("toggle", ROW_H, icon=icon, label=label, value=bool(value),
                         knob=1.0 if value else 0.0, command=command, close=close, selectable=True)

    def submenu(self, icon, label, builder, hint="", badge=None, width=None):
        return self._add("submenu", ROW_H, icon=icon, label=label, builder=builder, hint=hint,
                         badge=badge, sub_width=width, selectable=True)

    def swatches(self, items, selected, command):
        """items: (colour, tooltip label)"""
        rows = max(1, math.ceil(len(items) / 8))
        return self._add("swatches", rows * 30 + 10, items=list(items), selected=selected,
                         command=command, selectable=True)

    def segmented(self, icon, label, options, selected, command):
        """options: (value, label)"""
        return self._add("segmented", 58, icon=icon, label=label, options=list(options),
                         selected=selected, command=command, selectable=True)

    # ------------------------------------------------------------ popup
    def popup(self, x: int, y: int):
        global _active_root
        if self.parent is None:
            close_active()
            _active_root = self
        self._create_window()
        self._layout()
        left, top, right, bottom = work_area(self.master, x, y)
        width, height = self.width, self.height
        if x + width > right:
            x = max(left, x - width)
        if y + height > bottom:
            y = max(top, min(y - height, bottom - height))
        self._place(x, y)
        self._start()

    def _popup_beside(self, parent_x, parent_w, row_top):
        self._create_window()
        self._layout()
        left, top, right, bottom = work_area(self.master, parent_x + parent_w, row_top)
        x = parent_x + parent_w - 4
        if x + self.width > right:
            x = parent_x - self.width + 4
        y = row_top - PAD
        if y + self.height > bottom:
            y = max(top, bottom - self.height)
        self._place(x, y)
        self._start()

    def _create_window(self):
        self.win = tk.Toplevel(self.master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=KEY)
        for attribute, value in (("-topmost", True), ("-transparentcolor", KEY), ("-alpha", 0.0)):
            try:
                self.win.attributes(attribute, value)
            except tk.TclError:
                pass
        self.fonts = {
            "title": tkfont.Font(family=TEXT_FONT, size=11, weight="bold"),
            "text": tkfont.Font(family=TEXT_FONT, size=9),
            "bold": tkfont.Font(family=TEXT_FONT, size=9, weight="bold"),
            "small": tkfont.Font(family=TEXT_FONT, size=8),
            "smallb": tkfont.Font(family=TEXT_FONT, size=7, weight="bold"),
            "icon": tkfont.Font(family=ICON_FONT, size=10),
            "tile_icon": tkfont.Font(family=ICON_FONT, size=15),
            "avatar": tkfont.Font(family=TEXT_FONT, size=15, weight="bold"),
            "chev": tkfont.Font(family=TEXT_FONT, size=12),
        }
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Motion>", self._on_motion)
        self.cv.bind("<Leave>", self._on_leave)
        self.cv.bind("<ButtonPress-1>", self._on_press)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Button-3>", lambda _e: "break")
        for key, handler in (("<Escape>", self._key_escape), ("<Up>", lambda e: self._key_move(-1)),
                             ("<Down>", lambda e: self._key_move(1)), ("<Return>", self._key_enter),
                             ("<Right>", self._key_right), ("<Left>", self._key_left)):
            self.win.bind(key, handler)

    def _layout(self):
        y = PAD
        for entry in self.entries:
            entry["y0"] = y
            y += entry["h"]
            entry["y1"] = y
        self.height = y + PAD
        self.cv.configure(width=self.width, height=self.height)

    def _place(self, x, y):
        self.x, self.y = int(x), int(y)
        self._target_y = self.y
        self._slide = 8.0
        self.win.geometry(f"{self.width}x{self.height}+{self.x}+{self.y + int(self._slide)}")

    def _start(self):
        self._opened_at = time.monotonic()
        self._draw()
        self.win.deiconify()
        self.win.lift()
        if self.parent is None:
            try:
                self.win.focus_force()
            except tk.TclError:
                pass
            self.win.bind("<FocusOut>", lambda _e: self.win.after(150, self._check_focus))
        self._tick()

    # ------------------------------------------------------------ closing
    def chain(self):
        menus = []
        menu = self.root_menu
        while menu is not None and not menu.closed:
            menus.append(menu)
            menu = menu.child
        return menus

    def close(self):
        global _active_root
        if self.closed:
            return
        self.closed = True
        if self.child:
            self.child.close()
            self.child = None
        for timer in (self._child_timer, self._close_child_timer):
            if timer:
                try:
                    self.master.after_cancel(timer)
                except Exception:
                    pass
        try:
            if self.win:
                self.win.destroy()
        except tk.TclError:
            pass
        if self.parent is None:
            for menu in list(self._family):
                if not menu.closed:
                    menu.close()
            self._family.clear()
            if _active_root is self:
                _active_root = None
            _safe(self.on_close)

    def _pointer_inside_chain(self) -> bool:
        try:
            px, py = self.master.winfo_pointerxy()
        except tk.TclError:
            return False
        for menu in self.chain():
            try:
                wx, wy = menu.win.winfo_rootx(), menu.win.winfo_rooty()
            except tk.TclError:
                continue
            if wx <= px < wx + menu.width and wy <= py < wy + menu.height:
                return True
        return False

    def _check_focus(self):
        if self.closed:
            return
        try:
            focused = self.win.focus_get()
        except (tk.TclError, KeyError):
            focused = None
        if focused is not None:
            try:
                top = focused.winfo_toplevel()
                if any(top is menu.win for menu in self.chain()):
                    return
            except tk.TclError:
                pass
        if not self._pointer_inside_chain():
            self.close()

    # ------------------------------------------------------------ animation
    def _tick(self):
        if self.closed:
            return
        changed = False
        # slide + fade in
        age = time.monotonic() - self._opened_at
        alpha = min(1.0, age / 0.14)
        try:
            self.win.attributes("-alpha", 0.25 + 0.75 * alpha if alpha < 1 else 1.0)
        except tk.TclError:
            pass
        if self._slide > 0.05:
            self._slide *= 0.55
            self.win.geometry(f"+{self.x}+{self._target_y + int(round(self._slide))}")
        # hover progress
        target_key = self.hover
        if self.child is not None and self.child_index is not None:
            target_key = (self.child_index, 0)
        keys = set(self.progress) | ({target_key} if target_key else set())
        for key in keys:
            current = self.progress.get(key, 0.0)
            goal = 1.0 if key == target_key else 0.0
            if abs(goal - current) > 0.01:
                current += (goal - current) * 0.45
                changed = True
            else:
                current = goal
            if current <= 0.0:
                self.progress.pop(key, None)
            else:
                self.progress[key] = current
        for entry in self.entries:
            if entry["kind"] == "toggle":
                goal = 1.0 if entry["value"] else 0.0
                if abs(entry["knob"] - goal) > 0.01:
                    entry["knob"] += (goal - entry["knob"]) * 0.45
                    changed = True
                else:
                    entry["knob"] = goal
            if entry.get("flash", 0) > 0.02:
                entry["flash"] *= 0.7
                changed = True
        animated = any(entry["kind"] == "banner" for entry in self.entries)
        if changed or animated or alpha < 1:
            self._draw()
        # click outside closes the whole chain (works even without focus)
        if self.parent is None:
            inside = self._pointer_inside_chain()
            buttons = _mouse_buttons_down()
            if not buttons:
                self._armed = True
            elif self._armed and not inside:
                self.close()
                return
            # left alone with the mouse elsewhere: tidy up after a few seconds
            now = time.monotonic()
            if inside or now - self._opened_at < 1.0:
                self._outside_since = None
            elif self._outside_since is None:
                self._outside_since = now
            elif now - self._outside_since > IDLE_CLOSE_S:
                self.close()
                return
        self.win.after(FRAME_MS, self._tick)

    # ------------------------------------------------------------ drawing
    def _draw(self):
        cv = self.cv
        if cv is None or self.closed:
            return
        cv.delete("all")
        p = self.p
        rounded(cv, 0, 0, self.width - 1, self.height - 1, 14, fill=p["bg"], outline=p["border"])
        for index, entry in enumerate(self.entries):
            draw = getattr(self, "_draw_" + entry["kind"], None)
            if draw:
                draw(index, entry)

    def _fit(self, text, font, max_width):
        text = str(text)
        if font.measure(text) <= max_width:
            return text
        while text and font.measure(text + "\u2026") > max_width:
            text = text[:-1]
        return text + "\u2026"

    def _prog(self, index, sub=0):
        return self.progress.get((index, sub), 0.0)

    def _draw_header(self, index, entry):
        cv, p, w = self.cv, self.p, self.width
        y0, y1 = entry["y0"], entry["y1"]
        rounded(cv, 6, y0, w - 6, y1 - 4, 11, fill=p["header"], outline="")
        avatar_color = entry.get("avatar_color") or self.raw_accent
        cx, cy, radius = 32, y0 + 27, 19
        cv.create_oval(cx - radius - 2, cy - radius - 2, cx + radius + 2, cy + radius + 2,
                       fill=p["bg"], outline="")
        cv.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=avatar_color, outline="")
        cv.create_text(cx, cy, text=entry["avatar"], fill=ink_on(avatar_color), font=self.fonts["avatar"])
        text_x = 60
        badge = entry.get("badge")
        badge_w = self.fonts["title"].measure(" " + badge[0]) if badge and badge[0] else 0
        title = self._fit(entry["title"], self.fonts["title"], w - text_x - 14 - badge_w)
        cv.create_text(text_x, y0 + 18, anchor="w", fill=p["ink"], font=self.fonts["title"], text=title)
        if badge_w:
            cv.create_text(text_x + self.fonts["title"].measure(title + " "), y0 + 18, anchor="w",
                           fill=badge[1] or p["ink"], font=self.fonts["title"], text=badge[0])
        if entry["subtitle"]:
            cv.create_text(text_x, y0 + 36, anchor="w", fill=p["muted"], font=self.fonts["small"],
                           text=self._fit(entry["subtitle"], self.fonts["small"], w - text_x - 14))
        if entry["stats"]:
            bar_y = y0 + 60
            slot = (w - 26) / max(1, len(entry["stats"]))
            for n, (icon, value, color) in enumerate(entry["stats"]):
                sx = 14 + n * slot
                cv.create_text(sx + 7, bar_y, text=icon, fill=color, font=self.fonts["icon"])
                bar_x1, bar_x2 = sx + 18, sx + slot - 10
                rounded(cv, bar_x1, bar_y - 3, bar_x2, bar_y + 3, 3, fill=p["bar_bg"], outline="")
                fill_w = (bar_x2 - bar_x1) * max(0.0, min(1.0, float(value) / 100.0))
                if fill_w > 4:
                    rounded(cv, bar_x1, bar_y - 3, bar_x1 + fill_w, bar_y + 3, 3, fill=color, outline="")

    def _draw_banner(self, index, entry):
        cv, w = self.cv, self.width
        y0, y1 = entry["y0"] + 4, entry["y1"] - 4
        now = time.monotonic()
        pulse = 0.5 + 0.5 * math.sin(now * 3.2)
        prog = self._prog(index)
        fill = mix(mix(BANNER_A, BANNER_B, pulse), "#ffffff", 0.08 * prog)
        glow = mix(BANNER_B, "#ffd166", pulse)
        rounded(cv, 7, y0 - 1, w - 7, y1 + 1, 14, fill=glow, outline="")
        rounded(cv, 9, y0 + 1, w - 9, y1 - 1, 12, fill=fill, outline="")
        # moving shine
        span = w - 60
        shine_x = 18 + ((now * 120) % (span + 160)) - 80
        if 18 < shine_x < w - 40:
            shine = mix(fill, "#ffffff", 0.28)
            cv.create_polygon(shine_x, y0 + 3, shine_x + 14, y0 + 3, shine_x + 4, y1 - 3,
                              shine_x - 10, y1 - 3, fill=shine, outline="")
        # icon bubble with a little bounce
        bounce = math.sin(now * 5.0) * 2.0
        cx, cy = 32, (y0 + y1) / 2 + bounce * 0.5
        cv.create_oval(cx - 15, cy - 15, cx + 15, cy + 15, fill="#ffffff", outline="")
        cv.create_text(cx, cy, text=entry["icon"], fill=BANNER_B, font=self.fonts["tile_icon"])
        text_x = 56
        badge_w = 0
        if entry.get("badge"):
            badge_w = self.fonts["smallb"].measure(entry["badge"]) + 14
            bx2 = w - 18
            bx1 = bx2 - badge_w
            by = (y0 + y1) / 2
            rounded(cv, bx1, by - 9, bx2, by + 9, 9, fill="#ffffff", outline="")
            cv.create_text((bx1 + bx2) / 2, by, text=entry["badge"], fill=BANNER_B, font=self.fonts["smallb"])
        max_w = w - text_x - badge_w - 26
        cv.create_text(text_x + 2 * prog, y0 + 17, anchor="w", fill="#ffffff", font=self.fonts["bold"],
                       text=self._fit(entry["title"], self.fonts["bold"], max_w))
        cv.create_text(text_x + 2 * prog, y0 + 35, anchor="w", fill="#fff4e8", font=self.fonts["small"],
                       text=self._fit(entry["subtitle"], self.fonts["small"], max_w))

    def _tile_rect(self, entry, sub):
        cols = max(1, min(4, len(entry["items"])))
        col, row = sub % cols, sub // cols
        tile_w = (self.width - 20 - (cols - 1) * TILE_GAP) / cols
        x1 = 10 + col * (tile_w + TILE_GAP)
        y1 = entry["y0"] + 5 + row * (TILE_H + TILE_GAP)
        return x1, y1, x1 + tile_w, y1 + TILE_H

    def _draw_tiles(self, index, entry):
        cv, p = self.cv, self.p
        for sub, (icon, label, _command, badge) in enumerate(entry["items"]):
            x1, y1, x2, y2 = self._tile_rect(entry, sub)
            prog = self._prog(index, sub)
            lift = 2 * prog
            pressed = self.pressed == (index, sub)
            fill = mix(p["tile"], p["tile_hover"], prog)
            if pressed:
                fill = mix(fill, self.accent, 0.25)
                lift = 0
            if prog > 0.05:
                rounded(cv, x1, y1 + 2, x2, y2 + 1, 10, fill=mix(p["bg"], p["border"], 0.8 * prog), outline="")
            rounded(cv, x1, y1 - lift, x2, y2 - lift, 10, fill=fill,
                    outline=mix(p["tile"], self.accent, 0.55 * prog))
            cx = (x1 + x2) / 2
            icon_color = mix(p["ink"], self.accent, 0.35 + 0.65 * prog)
            cv.create_text(cx, y1 + 20 - lift, text=icon, fill=icon_color, font=self.fonts["tile_icon"])
            cv.create_text(cx, y2 - 12 - lift, fill=p["ink"], font=self.fonts["small"],
                           text=self._fit(label, self.fonts["small"], x2 - x1 - 6))
            if badge:
                text = str(badge)
                bw = max(16, self.fonts["smallb"].measure(text) + 8)
                rounded(cv, x2 - bw + 2, y1 - 5 - lift, x2 + 2, y1 + 11 - lift, 8, fill="#e5383b", outline="")
                cv.create_text(x2 - bw / 2 + 2, y1 + 3 - lift, text=text, fill="#ffffff", font=self.fonts["smallb"])

    def _draw_section(self, index, entry):
        self.cv.create_text(14, entry["y0"] + 13, anchor="w", text=str(entry["label"]).upper(),
                            fill=self.p["section"], font=self.fonts["smallb"])

    def _draw_sep(self, index, entry):
        y = entry["y0"] + 4
        self.cv.create_line(14, y, self.width - 14, y, fill=self.p["sep"])

    def _row_base(self, index, entry):
        """Hover highlight + accent bar + icon + label. Returns (prog, right edge for extras)."""
        cv, p, w = self.cv, self.p, self.width
        y0, y1 = entry["y0"], entry["y1"]
        prog = self._prog(index)
        flash = entry.get("flash", 0.0)
        highlight = entry.get("highlight")
        if highlight:
            pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 3.2)
            base = mix(p["bg"], mix(BANNER_A, BANNER_B, pulse), 0.16 + 0.1 * prog)
            rounded(cv, 6, y0 + 1, w - 6, y1 - 1, 9, fill=base, outline=mix(BANNER_A, BANNER_B, pulse))
        elif prog > 0.01 or flash > 0.02:
            fill = mix(p["bg"], p["hover"], prog)
            if flash > 0.02:
                fill = mix(fill, self.accent, 0.35 * flash)
            rounded(cv, 6, y0 + 1, w - 6, y1 - 1, 9, fill=fill, outline="")
        if prog > 0.05:
            bar_h = (y1 - y0 - 12) * prog
            mid = (y0 + y1) / 2
            rounded(cv, 8, mid - bar_h / 2, 11, mid + bar_h / 2, 2, fill=self.accent, outline="")
        shift = 3 * prog
        ink = p["danger"] if entry.get("danger") else p["ink"]
        icon_color = p["danger"] if entry.get("danger") else mix(p["muted"], self.accent, prog)
        if highlight:
            icon_color = BANNER_B
        cy = (y0 + y1) / 2
        cv.create_text(24 + shift, cy, text=entry.get("icon") or "", fill=icon_color, font=self.fonts["icon"])
        font = self.fonts["bold"] if entry.get("selected") or highlight else self.fonts["text"]
        return prog, cy, shift, ink, font

    def _draw_extras(self, entry, cy, right, label_x, ink, font, prog):
        """hint + badge between label and right edge; returns nothing."""
        cv, p = self.cv, self.p
        badge = entry.get("badge")
        if badge:
            text = str(badge)
            bw = self.fonts["smallb"].measure(text) + 12
            bx2 = right
            rounded(cv, bx2 - bw, cy - 8, bx2, cy + 8, 8, fill=mix(BANNER_A, BANNER_B, 0.6), outline="")
            cv.create_text(bx2 - bw / 2, cy, text=text, fill="#ffffff", font=self.fonts["smallb"])
            right = bx2 - bw - 6
        hint = entry.get("hint")
        if hint:
            hint_text = self._fit(hint, self.fonts["small"], 90)
            cv.create_text(right, cy, anchor="e", text=hint_text, fill=p["muted"], font=self.fonts["small"])
            right -= self.fonts["small"].measure(hint_text) + 8
        cv.create_text(label_x, cy, anchor="w", fill=ink, font=font,
                       text=self._fit(entry["label"], font, max(30, right - label_x)))

    def _draw_item(self, index, entry):
        prog, cy, shift, ink, font = self._row_base(index, entry)
        self._draw_extras(entry, cy, self.width - 16, 42 + shift, ink, font, prog)

    def _draw_choice(self, index, entry):
        prog, cy, shift, ink, font = self._row_base(index, entry)
        right = self.width - 16
        if entry["selected"]:
            self.cv.create_oval(right - 16, cy - 8, right, cy + 8, fill=self.accent, outline="")
            self.cv.create_text(right - 8, cy, text="\u2713", fill=ink_on(self.accent), font=self.fonts["smallb"])
        else:
            self.cv.create_oval(right - 15, cy - 7, right - 1, cy + 7, outline=mix(self.p["track"], self.accent, prog))
        self._draw_extras(entry, cy, right - 24, 42 + shift, ink, font, prog)

    def _draw_toggle(self, index, entry):
        prog, cy, shift, ink, font = self._row_base(index, entry)
        right = self.width - 16
        knob = entry["knob"]
        track_x1 = right - 32
        track = mix(self.p["track"], self.accent, knob)
        rounded(self.cv, track_x1, cy - 9, right, cy + 9, 9, fill=track, outline="")
        kx = track_x1 + 9 + knob * 14
        self.cv.create_oval(kx - 7, cy - 7, kx + 7, cy + 7, fill="#ffffff", outline=mix(track, "#000000", 0.08))
        self._draw_extras(entry, cy, track_x1 - 8, 42 + shift, ink, font, prog)

    def _draw_submenu(self, index, entry):
        prog, cy, shift, ink, font = self._row_base(index, entry)
        right = self.width - 14
        chevron_color = mix(self.p["muted"], self.accent, prog)
        self.cv.create_text(right - 4 + 2 * prog, cy - 1, text="\u203A", fill=chevron_color, font=self.fonts["chev"])
        self._draw_extras(entry, cy, right - 16, 42 + shift, ink, font, prog)

    def _swatch_center(self, entry, sub):
        col, row = sub % 8, sub // 8
        step = (self.width - 28) / 8
        return 14 + step * col + step / 2, entry["y0"] + 20 + row * 30

    def _draw_swatches(self, index, entry):
        cv = self.cv
        for sub, (color, _label) in enumerate(entry["items"]):
            cx, cy = self._swatch_center(entry, sub)
            prog = self._prog(index, sub)
            radius = 10 + 2.5 * prog
            selected = color == entry["selected"]
            if selected:
                cv.create_oval(cx - radius - 4, cy - radius - 4, cx + radius + 4, cy + radius + 4,
                               outline=self.accent, width=2)
            cv.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=color,
                           outline=mix(color, "#000000", 0.18))
            if selected:
                cv.create_text(cx, cy, text="\u2713", fill=ink_on(color), font=self.fonts["smallb"])

    def _segment_rect(self, entry, sub):
        count = max(1, len(entry["options"]))
        x1, x2 = 14, self.width - 14
        seg_w = (x2 - x1) / count
        y1 = entry["y0"] + 28
        return x1 + sub * seg_w, y1, x1 + (sub + 1) * seg_w, y1 + 24

    def _draw_segmented(self, index, entry):
        cv, p = self.cv, self.p
        y0 = entry["y0"]
        cv.create_text(24, y0 + 14, text=entry["icon"], fill=p["muted"], font=self.fonts["icon"])
        cv.create_text(42, y0 + 14, anchor="w", text=entry["label"], fill=p["ink"], font=self.fonts["text"])
        x1, y1, _x2, y2 = self._segment_rect(entry, 0)
        _a, _b, x2_last, _c = self._segment_rect(entry, len(entry["options"]) - 1)
        rounded(cv, x1, y1, x2_last, y2, 12, fill=p["tile"], outline=p["border"])
        for sub, (value, label) in enumerate(entry["options"]):
            sx1, sy1, sx2, sy2 = self._segment_rect(entry, sub)
            prog = self._prog(index, sub)
            selected = value == entry["selected"]
            if selected:
                rounded(cv, sx1 + 2, sy1 + 2, sx2 - 2, sy2 - 2, 10, fill=self.accent, outline="")
                ink = ink_on(self.accent)
            else:
                if prog > 0.02:
                    rounded(cv, sx1 + 2, sy1 + 2, sx2 - 2, sy2 - 2, 10, fill=mix(p["tile"], p["tile_hover"], prog), outline="")
                ink = p["ink"]
            cv.create_text((sx1 + sx2) / 2, (sy1 + sy2) / 2, text=self._fit(label, self.fonts["small"], sx2 - sx1 - 6),
                           fill=ink, font=self.fonts["bold"] if selected else self.fonts["small"])

    # ------------------------------------------------------------ hit testing
    def _hit(self, x, y):
        for index, entry in enumerate(self.entries):
            if not entry.get("selectable") or not (entry["y0"] <= y < entry["y1"]):
                continue
            kind = entry["kind"]
            if kind == "tiles":
                for sub in range(len(entry["items"])):
                    x1, y1, x2, y2 = self._tile_rect(entry, sub)
                    if x1 <= x <= x2 and y1 - 3 <= y <= y2 + 3:
                        return index, sub
                return None
            if kind == "swatches":
                for sub in range(len(entry["items"])):
                    cx, cy = self._swatch_center(entry, sub)
                    if abs(x - cx) <= 13 and abs(y - cy) <= 13:
                        return index, sub
                return None
            if kind == "segmented":
                for sub in range(len(entry["options"])):
                    x1, y1, x2, y2 = self._segment_rect(entry, sub)
                    if x1 <= x <= x2 and y1 <= y <= y2:
                        return index, sub
                return None
            return index, 0
        return None

    def _selectables(self):
        result = []
        for index, entry in enumerate(self.entries):
            if not entry.get("selectable"):
                continue
            kind = entry["kind"]
            if kind == "tiles":
                result.extend((index, sub) for sub in range(len(entry["items"])))
            elif kind == "swatches":
                result.extend((index, sub) for sub in range(len(entry["items"])))
            elif kind == "segmented":
                result.extend((index, sub) for sub in range(len(entry["options"])))
            else:
                result.append((index, 0))
        return result

    # ------------------------------------------------------------ events
    def _set_hover(self, target):
        if target == self.hover:
            return
        self.hover = target
        if self._child_timer:
            self.master.after_cancel(self._child_timer)
            self._child_timer = None
        if target is not None and self.entries[target[0]]["kind"] == "submenu":
            if self.child_index != target[0]:
                self._child_timer = self.master.after(170, lambda i=target[0]: self._open_child(i))
            elif self._close_child_timer:
                self.master.after_cancel(self._close_child_timer)
                self._close_child_timer = None
        elif self.child is not None and target is not None:
            if self._close_child_timer:
                self.master.after_cancel(self._close_child_timer)
            self._close_child_timer = self.master.after(260, self._close_child)

    def _on_motion(self, event):
        self._set_hover(self._hit(event.x, event.y))

    def _on_leave(self, _event):
        if self.child is None:
            self._set_hover(None)

    def _on_press(self, event):
        self.pressed = self._hit(event.x, event.y)
        self._draw()

    def _on_release(self, event):
        target = self._hit(event.x, event.y)
        pressed, self.pressed = self.pressed, None
        if target is not None and target == pressed:
            self._activate(*target)
        else:
            self._draw()

    def _open_child(self, index):
        self._child_timer = None
        if self.closed:
            return
        if self.child is not None:
            if self.child_index == index:
                return
            # replacing a submenu: always close the old one, even if the pointer is still in its box
            self.child.close()
            self.child = None
            self.child_index = None
        entry = self.entries[index]
        child = FancyMenu(self.master, accent=self.raw_accent, dark=self.dark,
                          width=entry.get("sub_width") or 236, parent=self)
        try:
            entry["builder"](child)
        except Exception as exc:
            logger.warning("Submenu builder failed: %s", exc)
            return
        if not child.entries:
            return
        self.child = child
        self.child_index = index
        child._popup_beside(self.x, self.width, self.y + entry["y0"])

    def _close_child(self):
        self._close_child_timer = None
        if self.child is not None:
            if self.child._pointer_inside_self():
                return
            self.child.close()
            self.child = None
            self.child_index = None

    def _pointer_inside_self(self) -> bool:
        try:
            px, py = self.master.winfo_pointerxy()
            wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
        except tk.TclError:
            return False
        inside = wx <= px < wx + self.width and wy <= py < wy + self.height
        if not inside and self.child is not None:
            return self.child._pointer_inside_self()
        return inside

    def _activate(self, index, sub=0):
        entry = self.entries[index]
        kind = entry["kind"]
        entry["flash"] = 1.0
        if kind == "submenu":
            self._open_child(index)
            if self.child is not None:
                self.child.hover = self.child._selectables()[0] if self.child._selectables() else None
            return
        if kind == "toggle":
            entry["value"] = not entry["value"]
            value = entry["value"]
            if entry.get("close"):
                self.master.after(220, lambda: self._finish(entry["command"], value))
            else:
                self.master.after(180, lambda: _safe(entry["command"], value))
            return
        if kind == "swatches":
            color = entry["items"][sub][0]
            entry["selected"] = color
            _safe(entry["command"], color)
            return
        if kind == "segmented":
            value = entry["options"][sub][0]
            entry["selected"] = value
            _safe(entry["command"], value)
            return
        if kind == "tiles":
            command = entry["items"][sub][2]
        else:
            command = entry.get("command")
        if entry.get("keep_open"):
            _safe(command)
            return
        self.master.after(90, lambda: self._finish(command))

    def _finish(self, command, *args):
        self.root_menu.close()
        self.master.after(10, lambda: _safe(command, *args))

    # ------------------------------------------------------------ keyboard
    def _deepest(self):
        menu = self.root_menu
        while menu.child is not None and not menu.child.closed and menu.child.hover is not None:
            menu = menu.child
        return menu

    def _key_move(self, step):
        menu = self._deepest()
        items = menu._selectables()
        if not items:
            return "break"
        if menu.hover in items:
            position = (items.index(menu.hover) + step) % len(items)
        else:
            position = 0 if step > 0 else len(items) - 1
        menu.hover = items[position]
        if menu.child is not None and menu.child_index != menu.hover[0]:
            menu.child.close()
            menu.child = None
            menu.child_index = None
        return "break"

    def _key_enter(self, _event=None):
        menu = self._deepest()
        if menu.hover is not None:
            menu._activate(*menu.hover)
        return "break"

    def _key_right(self, _event=None):
        menu = self._deepest()
        if menu.hover is not None and menu.entries[menu.hover[0]]["kind"] == "submenu":
            menu._activate(*menu.hover)
        return "break"

    def _key_left(self, _event=None):
        menu = self._deepest()
        if menu.parent is not None:
            parent = menu.parent
            menu.close()
            parent.child = None
            parent.child_index = None
        return "break"

    def _key_escape(self, _event=None):
        menu = self._deepest()
        if menu.parent is None:
            menu.close()
        else:
            self._key_left()
        return "break"
