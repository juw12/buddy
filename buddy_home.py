"""Buddy's own tabs in the main Buddy window: Home (ask Buddy + everything at a glance) and Focus."""
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
        self._brain_text = "Checking\u2026"
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
        self.greeting.grid(row=0, column=0, columnspan=3, sticky="w")

        # Ask Buddy - the AI is simply Buddy
        ask = ttk.Frame(home)
        ask.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 2))
        ask.columnconfigure(0, weight=1)
        self.question_var = tk.StringVar()
        self.ask_entry = ttk.Entry(ask, textvariable=self.question_var, font=("Segoe UI", 11))
        self.ask_entry.grid(row=0, column=0, sticky="ew", ipady=3)
        self.ask_entry.bind("<Return>", lambda _event: self._ask())
        ttk.Button(ask, text="Ask Buddy", command=self._ask).grid(row=0, column=1, padx=(8, 0))
        self.brain_status = ttk.Label(home, foreground=MUTED)
        self.brain_status.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 14))

        self.focus_status, buttons = self._row(home, 3, "\u25CE", "Focus")
        ttk.Button(buttons, text="Focus card", command=self.app.open_focus_card).pack(side="left")
        ttk.Button(buttons, text="Dashboard", command=self.app.open_dashboard).pack(side="left", padx=(6, 0))

        self.sound_status, buttons = self._row(home, 5, "\u2328", "Keyboard sounds")
        self.pack_box = ttk.Combobox(buttons, state="readonly", width=10)
        self.pack_box.pack(side="left")
        self.pack_box.bind("<<ComboboxSelected>>",
                           lambda _event: self.app.select_sound_pack(self.pack_box.current()))
        ttk.Button(buttons, text="Test", width=5, command=self.app.test_sound).pack(side="left", padx=(6, 0))
        self.sound_button = ttk.Button(buttons, width=8, command=self.app.toggle_sounds)
        self.sound_button.pack(side="left", padx=(6, 0))

        self.pet_status, buttons = self._row(home, 7, "\u2665", "On your desktop")
        ttk.Button(buttons, text="Find", command=self.app.summon_pet).pack(side="left")
        ttk.Button(buttons, text="Pet settings", command=self.app.open_pet_settings).pack(side="left", padx=(6, 0))

        ttk.Separator(home).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(2, 10))
        self.autostart_var = tk.BooleanVar(value=self.app.autostart_enabled())
        ttk.Checkbutton(home, text="Start Buddy with Windows", variable=self.autostart_var,
                        command=self._on_autostart).grid(row=10, column=0, columnspan=2, sticky="w")
        self.hint = ttk.Label(home, foreground=MUTED)
        self.hint.grid(row=11, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _ask(self):
        question = self.question_var.get().strip()
        if question:
            self.question_var.set("")
            self.app.ask_buddy(question)

    def focus_ask_box(self):
        self.notebook.select(0)
        self.ask_entry.focus_set()

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
        ttk.Label(focus, text="Pomodoros, goals, reminders and friends: right-click Buddy on your desktop.",
                  foreground=MUTED).grid(row=3, column=0, sticky="w", pady=(14, 0))

    # ------------------------------------------------------------------ updates
    def refresh(self):
        hour = datetime.now().hour
        part = "morning" if 5 <= hour < 12 else "afternoon" if hour < 18 else "evening"
        self.greeting.config(text=f"Good {part}! What can I help with?")

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
        self.sound_status.config(text=f"{'Clicking as you type' if sounds_on else 'Muted'}  \u00b7  "
                                      f"Ctrl+Alt+M mutes  \u00b7  more in Settings")
        self.sound_button.config(text="Mute" if sounds_on else "Turn on")
        labels, current = self.app.sound_pack_choices()
        self.pack_box["values"] = labels
        if current >= 0:
            self.pack_box.current(current)

        self.brain_status.config(text=self._brain_text)
        name, visible = self.app.pet_state()
        self.pet_status.config(text=f"{name} is on your desktop" if visible else f"{name} is hidden - click Find")
        self.autostart_var.set(self.app.autostart_enabled())
        self.hint.config(text=f"Anywhere: tap Ctrl+Alt+Space to type, hold it to talk  \u00b7  {self.app.pet_hotkey()} brings "
                              f"{name} to you")

    def check_ai(self):
        settings = self.app.settings
        if not settings.get("buddy_enabled"):
            self._set_brain("My brain is switched off - turn it on in Settings")
            return
        self._set_brain("Waking up my brain (Ollama)\u2026")

        def worker():
            model = settings.get("buddy_model", "")
            try:
                models = keyit_buddy.OllamaClient(settings["ollama_url"]).list_models()
            except Exception:
                self.app.post(self._set_brain, "I can't think yet: Ollama isn't answering - start or reinstall Ollama")
                return
            installed = any(name == model or name.split(":")[0] == model for name in models)
            text = (f"I see your screen when you ask  \u00b7  brain: {model}" if installed
                    else f"My brain '{model}' is missing - run: ollama pull {model}")
            self.app.post(self._set_brain, text)

        threading.Thread(target=worker, daemon=True).start()

    def _set_brain(self, text):
        self._brain_text = text
        self.brain_status.config(text=text)
