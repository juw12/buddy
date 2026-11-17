"""The "AI agents" window: which agents Buddy shows as crew pointers, and the user's own AIs.

It is opened from Settings (Buddy (AI) -> Agents...) and, with `offer=True`, by Buddy itself the first time
it finds an agent on this PC: Buddy asks before it shows anything. What is decided here is saved by the
caller; the rules are in keyit_agents.py.
"""

import tkinter as tk
from tkinter import ttk

import buddy_overlay
import keyit_agents

CREW_LINE = 'Buddy.exe --crew "{name}: Reading the docs"'


class AgentsDialog:
    def __init__(self, parent, settings, on_saved, offer=False, scan=keyit_agents.scan):
        self.settings = settings
        self.on_saved = on_saved                   # called with (crew_agents, crew_custom) when the user confirms
        self.offer = offer
        self.scan = scan
        self.found = []
        self.ticks = {}                            # agent id -> BooleanVar
        self.custom = list(keyit_agents.custom_agents(settings).values())
        self.window = tk.Toplevel(parent)
        self.window.title("AI agents")
        self.window.resizable(False, False)
        if not offer:                              # Buddy's own window may be hidden when Buddy asks by itself
            self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.decline if offer else self.window.destroy)

        heading = "I found AI agents on this PC. Shall I show them next to me?" if offer else "AI agents on this PC"
        ttk.Label(self.window, text=heading, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Label(self.window, justify="left", wraplength=460, foreground="#555", text=(
            "Every agent you tick gets its own pointer while it works, in my shape, with its own name and colour. "
            "I only read what the agent wrote down on this PC. I send nothing anywhere and I never tell an agent "
            "what to do.")).pack(anchor="w", padx=14)

        self.rows = ttk.Frame(self.window)
        self.rows.pack(fill="x", padx=14, pady=(10, 0))
        self.custom_rows = ttk.Frame(self.window)
        self.custom_rows.pack(fill="x", padx=14, pady=(10, 0))
        self.note = ttk.Label(self.window, justify="left", wraplength=460, foreground="#555")
        self.note.pack(anchor="w", padx=14, pady=(6, 0))

        buttons = ttk.Frame(self.window)
        buttons.pack(fill="x", padx=14, pady=14)
        ttk.Button(buttons, text="Scan again", command=self.rescan).pack(side="left")
        ttk.Button(buttons, text="Add my own AI\u2026", command=self.add_custom).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Use them" if offer else "Save", command=self.save).pack(side="right")
        ttk.Button(buttons, text="No thanks" if offer else "Cancel",
                   command=self.decline if offer else self.window.destroy).pack(side="right", padx=(0, 6))
        self.rescan()
        self._show_custom()
        self.window.lift()

    # -- the agents Buddy knows
    def rescan(self):
        ticked = {name: variable.get() for name, variable in self.ticks.items()}
        saved = keyit_agents.answers(self.settings)
        self.found = self.scan()
        for child in self.rows.winfo_children():
            child.destroy()
        self.ticks = {}
        for row, agent in enumerate(self.found):
            wanted = ticked.get(agent["id"], saved.get(agent["id"], agent["found"]))
            self.ticks[agent["id"]] = tk.BooleanVar(self.window, value=wanted)
            ttk.Checkbutton(self.rows, text=agent["name"], variable=self.ticks[agent["id"]]).grid(
                row=row, column=0, sticky="w", pady=1)
            ttk.Label(self.rows, text=self.status(agent), foreground="#555").grid(
                row=row, column=1, sticky="w", padx=(12, 0))

    @staticmethod
    def status(agent):
        if agent["program"] and agent["history"]:
            return "on this PC"
        if agent["program"]:
            return "on this PC, has not worked here yet"
        if agent["history"]:
            return "has worked on this PC (its program is not on PATH)"
        return "not found on this PC"

    # -- the user's own AIs
    def _show_custom(self):
        for child in self.custom_rows.winfo_children():
            child.destroy()
        if self.custom:
            ttk.Label(self.custom_rows, text="Your own AI", font=("Segoe UI", 9, "bold")).grid(
                row=0, column=0, columnspan=3, sticky="w")
        for row, agent in enumerate(self.custom, start=1):
            swatch = tk.Label(self.custom_rows, width=2, bg=agent["color"] or "#cccccc")
            swatch.grid(row=row, column=0, sticky="w", pady=1)
            words = f"@{agent['name']}" + (f"  \u00b7  {agent['command']}" if agent["command"] else "")
            ttk.Label(self.custom_rows, text=words if len(words) < 58 else words[:57] + "\u2026").grid(
                row=row, column=1, sticky="w", padx=(8, 12))
            ttk.Button(self.custom_rows, text="Remove", width=8,
                       command=lambda name=agent["name"]: self.remove_custom(name)).grid(row=row, column=2, sticky="e")
        last = self.custom[-1]["name"] if self.custom else ""
        self.note.config(text=(f"Type @{last} and your question in my ask box to start it. To let it say what it is "
                               f"doing, it can run:  {CREW_LINE.format(name=last)}") if last else "")

    def remove_custom(self, name):
        self.custom = [agent for agent in self.custom if agent["name"] != name]
        self._show_custom()

    def add_custom(self):
        form = tk.Toplevel(self.window)
        form.title("Add my own AI")
        form.resizable(False, False)
        form.transient(self.window)
        name, command = tk.StringVar(form), tk.StringVar(form)
        ttk.Label(form, text="Name").grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(14, 0))
        name_box = ttk.Entry(form, textvariable=name, width=24)
        name_box.grid(row=0, column=1, sticky="w", padx=(0, 14), pady=(14, 0))
        ttk.Label(form, text="Command").grid(row=1, column=0, sticky="w", padx=(14, 8), pady=(8, 0))
        ttk.Entry(form, textvariable=command, width=52).grid(row=1, column=1, sticky="w", padx=(0, 14), pady=(8, 0))
        ttk.Label(form, text="Colour").grid(row=2, column=0, sticky="w", padx=(14, 8), pady=(8, 0))
        colors = [color for color in buddy_overlay.COLORS if color not in ("White", "Black")]
        color_box = ttk.Combobox(form, state="readonly", width=10, values=colors)
        color_box.set(colors[(len(self.custom) + 1) % len(colors)])
        color_box.grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Label(form, justify="left", wraplength=420, foreground="#555", text=(
            "Name: one word, as you will type it after the @. Command: what starts your AI with a question; "
            "put {prompt} where the question goes, for example  mybot.exe ask {prompt}  (leave it empty when "
            "your AI is started some other way and only reports to me).")).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 0))
        problem = ttk.Label(form, foreground="#b91c1c")
        problem.grid(row=4, column=0, columnspan=2, sticky="w", padx=14)

        def add():
            agent = keyit_agents.clean_custom({"name": name.get(), "command": command.get(),
                                               "color": buddy_overlay.COLORS[color_box.get()]})
            if agent is None:
                problem.config(text="The name must be one word of letters and digits, and not opencode, claude or codex.")
                return
            if any(other["name"].lower() == agent["name"].lower() for other in self.custom):
                problem.config(text="You already have an AI with that name.")
                return
            if len(self.custom) >= keyit_agents.MAX_CUSTOM:
                problem.config(text=f"That is the most I keep ({keyit_agents.MAX_CUSTOM}). Remove one first.")
                return
            self.custom.append(agent)
            self._show_custom()
            form.destroy()

        row = ttk.Frame(form)
        row.grid(row=5, column=0, columnspan=2, sticky="e", padx=14, pady=14)
        ttk.Button(row, text="Add", command=add).pack(side="right")
        ttk.Button(row, text="Cancel", command=form.destroy).pack(side="right", padx=(0, 6))
        name_box.focus_set()

    # -- the answer
    def save(self):
        ticked = {name: variable.get() for name, variable in self.ticks.items()}
        self._finish(keyit_agents.with_answers(self.settings, self.found, ticked), self.custom)

    def decline(self):
        """'No thanks' to what Buddy offered: the agents it asked about are not shown, and it does not ask again."""
        kept = keyit_agents.answers(self.settings)             # earlier answers stay; the new ones are "no"
        self._finish(keyit_agents.with_answers(self.settings, self.found, kept), self.custom)

    def _finish(self, crew_agents, crew_custom):
        self.window.destroy()
        self.on_saved(crew_agents, crew_custom)
