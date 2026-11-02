"""Buddy's own tabs in the main Buddy window: Home (everything at a glance) and Focus (the Pips part)."""
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import keyit_buddy

MUTED = "#777777"


class BuddyTabs:
    """Builds the Home and Focus tabs and keeps them up to date."""

    def __init__(self, app, notebook):
        self.app = app
        self.notebook = notebook
        self.home = ttk.Frame(notebook, padding=(14, 12, 14, 8))
        self.focus = ttk.Frame(notebook, padding=(14, 12, 14, 8))
        notebook.insert(0, self.home, text="  Home  ")
        notebook.insert(1, self.focus, text="  Focus  ")
        notebook.select(0)
        self._ai_text = "Checking\u2026"
        self._build_home()
        self._build_focus()

    # ------------------------------------------------------------------ Home
    def _row(self, parent, row, symbol, title):
        ttk.Label(parent, text=symbol, font=("Segoe UI Symbol", 16)).grid(row=row, column=0, rowspan=2, padx=(0, 10))
        ttk.Label(parent, text=title, font=("Segoe UI Semibold", 11)).grid(row=row, column=1, sticky="w")
        status = ttk.Label(parent, foreground=MUTED)
        status.grid(row=row + 1, column=1, sticky="w", pady=(0, 10))
        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=2, rowspan=2, sticky="e", pady=(0, 10))
        return status, buttons

    def _build_home(self):
        home = self.home
        home.columnconfigure(1, weight=1)
        self.greeting = ttk.Label(home, font=("Segoe UI Semibold", 13))
        self.greeting.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

        self.focus_status, buttons = self._row(home, 1, "\u25CE", "Focus")
        ttk.Button(buttons, text="Focus card", command=self.app.open_focus_card).pack(side="left")
        ttk.Button(buttons, text="Dashboard", command=self.app.open_dashboard).pack(side="left", padx=(6, 0))

        self.sound_status, buttons = self._row(home, 3, "\u2328", "Keyboard sounds")
        self.sound_button = ttk.Button(buttons, width=9, command=self.app.toggle_sounds)
        self.sound_button.pack(side="left")
        ttk.Button(buttons, text="Settings", command=lambda: self.app.open_home("sounds")).pack(side="left", padx=(6, 0))

        self.ai_status, buttons = self._row(home, 5, "\u2726", "AI helper")
        ttk.Button(buttons, text="Ask\u2026", command=self.app.open_ask).pack(side="left")
        ttk.Button(buttons, text="Check", command=self.check_ai).pack(side="left", padx=(6, 0))

        self.pet_status, buttons = self._row(home, 7, "\u2665", "Desktop pet")
        ttk.Button(buttons, text="Find", command=self.app.summon_pet).pack(side="left")
        ttk.Button(buttons, text="Pet settings", command=self.app.open_pet_settings).pack(side="left", padx=(6, 0))

        ttk.Separator(home).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(2, 10))
        self.autostart_var = tk.BooleanVar(value=self.app.autostart_enabled())
        ttk.Checkbutton(home, text="Start Buddy with Windows", variable=self.autostart_var,
                        command=self._on_autostart).grid(row=10, column=0, columnspan=2, sticky="w")
        self.hint = ttk.Label(home, foreground=MUTED)
        self.hint.grid(row=11, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _on_autostart(self):
        try:
            self.app.set_autostart(self.autostart_var.get())
        except Exception:
            self.autostart_var.set(self.app.autostart_enabled())

    # ------------------------------------------------------------------ Focus
    def _build_focus(self):
        focus = self.focus
        focus.columnconfigure(0, weight=1)
        ttk.Label(focus, text="Today", font=("Segoe UI Semibold", 13)).grid(row=0, column=0, sticky="w")
        self.focus_text = ttk.Label(focus, justify="left", wraplength=430)
        self.focus_text.grid(row=1, column=0, sticky="w", pady=(6, 12))
        buttons = ttk.Frame(focus)
        buttons.grid(row=2, column=0, sticky="w")
        ttk.Button(buttons, text="Focus card", command=self.app.open_focus_card).pack(side="left")
        ttk.Button(buttons, text="Dashboard", command=self.app.open_dashboard).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="History", command=self.app.open_history).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side="left", padx=(6, 0))
        ttk.Label(focus, text="Pomodoros, goals, reminders and friends live on the pet: right-click it.",
                  foreground=MUTED).grid(row=3, column=0, sticky="w", pady=(14, 0))

    # ------------------------------------------------------------------ updates
    def refresh(self):
        hour = datetime.now().hour
        part = "morning" if 5 <= hour < 12 else "afternoon" if hour < 18 else "evening"
        self.greeting.config(text=f"Good {part}! Here's everything Buddy runs for you.")

        summary = self.app.focus_numbers()
        if summary:
            stars = "\u2605" * int(summary["stars"]) + "\u2606" * max(0, 5 - int(summary["stars"]))
            self.focus_status.config(
                text=f"Score {summary['score']:.0f}  {stars}  \u00b7  {summary['productive_min']} min productive"
                     f"  \u00b7  {summary['pomodoros']} pomodoros")
        else:
            self.focus_status.config(text="Focus tracking is starting\u2026")
        self.focus_text.config(text=self.app.focus_summary_text() or "No focus data yet today.")

        sounds_on = self.app.sounds_enabled()
        self.sound_status.config(text=f"{'On' if sounds_on else 'Muted'}  \u00b7  {self.app.sound_pack_name()}"
                                      f"  \u00b7  Ctrl+Alt+M mutes")
        self.sound_button.config(text="Mute" if sounds_on else "Turn on")

        self.ai_status.config(text=self._ai_text)
        name, visible = self.app.pet_state()
        self.pet_status.config(text=f"{name} is on your desktop" if visible else f"{name} is hidden - click Find")
        self.autostart_var.set(self.app.autostart_enabled())
        self.hint.config(text=f"Hold Ctrl+Alt+Space to ask about your screen  \u00b7  "
                              f"{self.app.pet_hotkey()} finds the pet")

    def check_ai(self):
        settings = self.app.settings
        if not settings.get("buddy_enabled"):
            self._set_ai("Off - turn it on in the AI helper tab")
            return
        self._set_ai("Checking Ollama\u2026")

        def worker():
            model = settings.get("buddy_model", "")
            try:
                models = keyit_buddy.OllamaClient(settings["ollama_url"]).list_models()
            except Exception:
                self.app.post(self._set_ai, "Ollama isn't answering - start or reinstall Ollama")
                return
            installed = any(name == model or name.split(":")[0] == model for name in models)
            text = f"Ready  \u00b7  {model}" if installed else f"Model '{model}' missing - run: ollama pull {model}"
            self.app.post(self._set_ai, text)

        threading.Thread(target=worker, daemon=True).start()

    def _set_ai(self, text):
        self._ai_text = text
        self.ai_status.config(text=text)
