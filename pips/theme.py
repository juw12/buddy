"""
theme.py - Light / Dark colour themes for the Pips windows.

One palette per theme. The hub (social_ui), the pet settings window and the
focus card read their colours from here; app.py applies the palette to its
THEME dict and rebuilds the card when the user switches.
"""

from __future__ import annotations

LIGHT = {
    "name": "light",
    "bg": "#faf8f3",        # window background (cream paper)
    "card": "#ffffff",      # cards / panels
    "ink": "#1a1a1a",       # main text
    "muted": "#6b6b6b",     # secondary text
    "border": "#e2ddd4",
    "accent": "#8b1a1a",    # Pips red
    "accent_ink": "#ffffff",
    "gold": "#c8961e",
    "pink": "#e0557a",
    "soft": "#f1ece4",      # neutral buttons / tabs
    "warm": "#fbf3e6",      # highlighted rows (me, owned items, invites)
    "tile": "#f6f4f0",      # shop / badge tiles
    "tile_off": "#f4f2ee",
    "off_ink": "#9a958c",
    "off_sym": "#c9c4bb",
    "bar": "#eeeae3",
    "mine": "#fbeee6",      # my chat bubbles
    "theirs": "#f1f4f8",
    "entry": "#ffffff",
    "entry_ink": "#1a1a1a",
    "online": "#2fa84f",
    "busy": "#d93838",
    "away": "#e0a020",
    "offline": "#a8a39a",
}

DARK = {
    "name": "dark",
    "bg": "#17181c",
    "card": "#202228",
    "ink": "#ece9e3",
    "muted": "#9c988f",
    "border": "#34363e",
    "accent": "#d0484c",
    "accent_ink": "#ffffff",
    "gold": "#e2b33f",
    "pink": "#f07a98",
    "soft": "#2d3038",
    "warm": "#2f2a24",
    "tile": "#272a31",
    "tile_off": "#24262c",
    "off_ink": "#6f6c66",
    "off_sym": "#55534e",
    "bar": "#34363e",
    "mine": "#3a2a26",
    "theirs": "#2a2f38",
    "entry": "#2b2e35",
    "entry_ink": "#ece9e3",
    "online": "#43c463",
    "busy": "#ef5a5a",
    "away": "#f0b43c",
    "offline": "#6f6c66",
}

THEMES = {"light": LIGHT, "dark": DARK}
_current = "light"


def set_theme(name: str) -> str:
    global _current
    _current = name if name in THEMES else "light"
    return _current


def current_name() -> str:
    return _current


def palette() -> dict:
    return THEMES[_current]


# Colours for the focus card (app.py THEME keys) per theme.
APP_COLORS = {
    "light": {
        "paper_bg": "#faf8f3", "outer_bg": "#f0ede6", "white": "#ffffff",
        "ink_black": "#1a1a1a", "ink_light": "#4a4a4a", "muted": "#6b6b6b",
        "accent_red": "#8b1a1a", "accent_red_dark": "#6b1212",
        "border": "#d4cfc7", "border_light": "#e8e4dc",
        "shadow_1": "#d8d2c7", "shadow_2": "#e4dfd6",
        "pulse_focus": "#efdede", "pulse_break": "#dcefe2", "pulse_bg": "#fff5f5",
    },
    "dark": {
        "paper_bg": "#202228", "outer_bg": "#17181c", "white": "#2b2e35",
        "ink_black": "#ece9e3", "ink_light": "#c9c5bd", "muted": "#9c988f",
        "accent_red": "#d0484c", "accent_red_dark": "#a83236",
        "border": "#3a3c44", "border_light": "#34363e",
        "shadow_1": "#0e0f12", "shadow_2": "#131418",
        "pulse_focus": "#4a2a2c", "pulse_break": "#24402c", "pulse_bg": "#2e2224",
    },
}

PILLS = {
    "light": {
        "focus": ("#8b1a1a", "#f6e1e1"), "break": ("#1a7f37", "#e1f2e6"),
        "paused": ("#8a6508", "#f6eed7"), "idle": ("#6b6b6b", "#ece9e2"),
    },
    "dark": {
        "focus": ("#f08a8a", "#3a2426"), "break": ("#6fd18a", "#1f3326"),
        "paused": ("#e8c062", "#3a3220"), "idle": ("#b5b1a8", "#2d3038"),
    },
}


def apply_to_app(theme_dict: dict, pill_dict: dict, name: str | None = None):
    """Update app.py's THEME / PILL_COLORS dicts in place."""
    key = name if name in THEMES else _current
    theme_dict.update(APP_COLORS[key])
    pill_dict.update(PILLS[key])
