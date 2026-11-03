"""
KeyIt settings window, tray icon and "Record my keyboard" dialog (tkinter + pystray).

Threading: tkinter owns the main thread. The tray icon runs detached on its own thread
and the keyboard hook on pynput's thread; both talk to the UI only through post().
"""
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import keyit_core as core
from keyit_recorder import IMPORTANT_KEYS, RecordingSession, build_pack, slice_keystrokes

MIN_KEYS_TO_BUILD = 10



# Settings -> Brain. "Smart" sends the screenshot + question to your company's MAAS gateway (fast, much smarter);
# the local ones never leave this PC.
BRAINS = {"Smart (MAAS)": "maas", "Local GPU": "auto", "Local CPU": "cpu"}
BRAIN_NOTES = {
    "maas": "Smart brain: company MAAS (vision-model), answers in ~1-2 s. Your screenshot and question go to "
            "the company AI gateway. Falls back to this PC if it can't be reached.",
    "auto": "Local GPU brain: everything stays on this PC. Answers in ~5-15 s.",
    "cpu": "Local CPU brain (Ollama): everything stays on this PC, but slow (about a minute).",
}

class _NullTray:
    """Stand-in when a host app (Pips) owns the tray icon."""

    icon = None
    title = ""

    def update_menu(self):
        pass

    def notify(self, *args, **kwargs):
        pass

    def stop(self):
        pass


def enable_dpi_awareness():
    """Crisp text on high-DPI screens instead of blurry bitmap scaling."""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor: matches screenshots + cursor
    except (AttributeError, OSError):
        pass


def make_tray_image(enabled=True):
    from PIL import Image, ImageDraw, ImageOps

    icon_path = core.ASSETS_DIR / "keyit.png"
    if icon_path.exists():
        image = Image.open(icon_path).convert("RGBA").resize((64, 64), Image.LANCZOS)
    else:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((4, 8, 60, 60), radius=10, fill=(43, 45, 58))
        draw.rounded_rectangle((12, 10, 52, 46), radius=8, fill=(255, 138, 42))
    if not enabled:
        gray = ImageOps.grayscale(image).convert("RGBA")
        gray.putalpha(image.getchannel("A"))
        image = gray
    return image


class KeyItApp:
    def __init__(self, player, hook, start_hidden=False, host=None):
        self.host = host                  # hosting app (Pips): provides root, pet overlay, user context
        self.player = player
        self.hook = hook
        self.settings = player.settings
        self.ui_queue = queue.SimpleQueue()
        self.recorder = None
        self._save_job = None
        self._told_about_tray = False

        if host is None:
            enable_dpi_awareness()
            self.root = tk.Tk()
            self.root.title(core.APP_NAME)
        else:
            self.root = tk.Toplevel(host.root)      # child window; the host owns the main loop
            self.root.title(host.window_title)
        self.root.resizable(False, False)
        icon_file = core.ASSETS_DIR / "keyit.ico"
        if host is not None and getattr(host, "icon_path", None):
            icon_file = Path(host.icon_path)
        if icon_file.exists():
            self.root.iconbitmap(default=str(icon_file))
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._build_window()

        self.hook.on_toggle = lambda: self.post(self.refresh)
        if host is None:
            self.tray = self._build_tray()
            self.tray.run_detached()
        else:
            self.tray = _NullTray()

        self.refresh()
        self.root.after(50, self._drain_queue)
        if start_hidden:
            self.root.withdraw()
        else:
            self._center_on_screen()

    # ------------------------------------------------------------------ plumbing
    def post(self, function, *args):
        """Thread-safe: run function(*args) on the tkinter thread."""
        self.ui_queue.put((function, args))

    def _drain_queue(self):
        while True:
            try:
                function, args = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            function(*args)
        self.root.after(50, self._drain_queue)

    def run(self):
        self.root.mainloop()

    # ------------------------------------------------------------------ window
    def _build_window(self):
        frame = ttk.Frame(self.root, padding=(18, 14, 18, 14))
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        title = self.host.window_title if self.host else core.APP_NAME
        tagline = self.host.tagline if self.host else f"v{core.VERSION}  ·  mechanical sounds for any keyboard"
        ttk.Label(header, text=title, font=("Segoe UI Semibold", 18)).pack(side="left")
        ttk.Label(header, text=f"  {tagline}", foreground="#777").pack(side="left", pady=(8, 0))

        notebook = ttk.Notebook(frame)
        notebook.grid(row=1, column=0, columnspan=3, sticky="nsew")
        hosted = self.host is not None
        if hosted:
            # Inside Buddy everything is Buddy: one Settings tab with two sections
            settings_tab = ttk.Frame(notebook, padding=(10, 8, 10, 6))
            settings_tab.columnconfigure(0, weight=1)
            sounds = ttk.LabelFrame(settings_tab, text=" Keyboard & mouse sounds ", padding=(10, 4, 10, 8))
            buddy_tab = ttk.LabelFrame(settings_tab, text=" Buddy's brain (local AI) ", padding=(10, 4, 10, 8))
            sounds.grid(row=0, column=0, sticky="ew")
            buddy_tab.grid(row=1, column=0, sticky="ew", pady=(10, 0))
            notebook.add(settings_tab, text="  Settings  ")
        else:
            sounds = ttk.Frame(notebook, padding=(10, 10, 10, 6))
            buddy_tab = ttk.Frame(notebook, padding=(10, 10, 10, 6))
            notebook.add(sounds, text="  Sounds  ")
            notebook.add(buddy_tab, text="  Buddy (AI)  ")
        sounds.columnconfigure(1, weight=1)
        buddy_tab.columnconfigure(1, weight=1)
        self.notebook = notebook
        if hosted and hasattr(self.host, "build_tabs"):
            self.host.build_tabs(notebook)          # the host's own tabs (Home, Focus) go first

        self.enabled_var = tk.BooleanVar(value=self.settings["enabled"])
        ttk.Checkbutton(sounds, text="Sound on        (Ctrl+Alt+M)", variable=self.enabled_var,
                        command=self._on_enabled_clicked).grid(row=1, column=0, columnspan=3, sticky="w")
        self.mouse_sounds_var = tk.BooleanVar(value=self.settings.get("mouse_sounds", True))
        ttk.Checkbutton(sounds, text="Mouse clicks too", variable=self.mouse_sounds_var,
                        command=self._on_mouse_sounds_toggled).grid(row=1, column=1, columnspan=2, sticky="e")

        ttk.Label(sounds, text="Volume").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.volume_var = tk.DoubleVar(value=self.settings["volume"] * 100)
        ttk.Scale(sounds, from_=0, to=100, variable=self.volume_var, length=260,
                  command=self._on_volume_changed).grid(row=2, column=1, sticky="ew", padx=8, pady=(10, 0))
        self.volume_label = ttk.Label(sounds, width=5)
        self.volume_label.grid(row=2, column=2, sticky="e", pady=(10, 0))

        ttk.Label(sounds, text="Sound pack").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.pack_box = ttk.Combobox(sounds, state="readonly", width=34)
        self.pack_box.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(10, 0))
        self.pack_box.bind("<<ComboboxSelected>>", self._on_pack_selected)

        pack_buttons = ttk.Frame(sounds)
        pack_buttons.grid(row=4, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Button(pack_buttons, text="Test sound", command=self.test_sound).pack(side="left")
        ttk.Button(pack_buttons, text="Rescan", command=self.rescan_packs).pack(side="left", padx=6)
        ttk.Button(pack_buttons, text="Open packs folder", command=self.open_packs_folder).pack(side="left")

        ttk.Separator(sounds).grid(row=5, column=0, columnspan=3, sticky="ew", pady=12)

        ttk.Button(sounds, text="Record my keyboard…", command=self.open_recorder).grid(
            row=6, column=0, columnspan=2, sticky="w")
        self.autostart_var = tk.BooleanVar(value=self._safe_autostart_state())
        if self.host is None:                        # inside Buddy it's on the Home tab ("Start Buddy with Windows")
            ttk.Checkbutton(sounds, text="Start with Windows", variable=self.autostart_var,
                            command=self._on_autostart_clicked).grid(row=6, column=1, columnspan=2, sticky="e")

        self._build_buddy_tab(buddy_tab)

        self.status_label = ttk.Label(frame, foreground="#777")
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))

        bottom = ttk.Frame(frame)
        bottom.grid(row=3, column=0, columnspan=3, sticky="e", pady=(8, 0))
        if self.host is None:
            ttk.Button(bottom, text="Hide to tray", command=self.hide).pack(side="left")
            ttk.Button(bottom, text="Quit KeyIt", command=self.quit).pack(side="left", padx=(6, 0))
        else:
            ttk.Button(bottom, text="Close", command=lambda: self.hide(notify=False)).pack(side="left")
            if hasattr(self.host, "quit"):
                ttk.Button(bottom, text=f"Quit {self.host.window_title}",
                           command=self.host.quit).pack(side="left", padx=(6, 0))

        self._populate_packs()
        self._update_volume_label()

    def _center_on_screen(self):
        self.root.update_idletasks()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 3
        self.root.geometry(f"+{x}+{y}")

    def _populate_packs(self):
        packs = core.list_packs()
        self.pack_names = list(packs.keys())
        labels = [core.pack_display_name(path) for path in packs.values()]
        duplicates = {label for label in labels if labels.count(label) > 1}
        self.pack_box["values"] = [
            f"{label} ({name})" if label in duplicates else label
            for label, name in zip(labels, self.pack_names)
        ]

    def refresh(self):
        self.enabled_var.set(self.settings["enabled"])
        if self.settings["pack"] in self.pack_names:
            self.pack_box.current(self.pack_names.index(self.settings["pack"]))
        pack = self.player.pack
        state = "on" if self.settings["enabled"] else "muted"
        self.status_label.config(
            text=f"Sound {state} · {pack.name} · {len(pack.down)} keys"
                 f"{' + release sounds' if pack.up else ''}")
        if self.host is None:
            self.tray.icon = make_tray_image(self.settings["enabled"])
        self.tray.title = f"{core.APP_NAME} – {state} – {pack.name}"
        self.tray.update_menu()
        if self.host is not None and hasattr(self.host, "on_refresh"):
            self.host.on_refresh()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self, notify=True):
        self.root.withdraw()
        if notify and not self._told_about_tray:
            self._told_about_tray = True
            try:
                self.tray.notify("KeyIt is still running in the tray. Right-click the icon to quit.", core.APP_NAME)
            except Exception:
                pass

    def quit(self):
        if self.host is not None:            # the host app decides when to quit
            self.hide(notify=False)
            return
        if self.recorder:
            self.recorder.close()
        if self.buddy:
            self.buddy.cancel()
        self.tray.stop()
        self.hook.stop()
        self.root.destroy()

    # ------------------------------------------------------------------ actions
    def _on_enabled_clicked(self):
        if self.enabled_var.get() != self.settings["enabled"]:
            self.hook.toggle()

    def toggle_enabled(self):
        self.hook.toggle()

    def _on_mouse_sounds_toggled(self):
        self.settings["mouse_sounds"] = self.mouse_sounds_var.get()
        core.save_settings(self.settings)

    def _on_volume_changed(self, _value=None):
        self.settings["volume"] = round(self.volume_var.get() / 100, 3)
        self._update_volume_label()
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, lambda: core.save_settings(self.settings))

    def _update_volume_label(self):
        self.volume_label.config(text=f"{round(self.volume_var.get())}%")

    def _on_pack_selected(self, _event=None):
        index = self.pack_box.current()
        if index >= 0:
            self.select_pack(self.pack_names[index])

    def select_pack(self, pack_name):
        self.status_label.config(text="Loading pack…")

        def load():
            try:
                self.player.load_pack(pack_name)
                core.save_settings(self.settings)
                self.post(self.refresh)
            except Exception as error:
                self.post(messagebox.showerror, core.APP_NAME, f"Could not load pack:\n{error}")
                self.post(self.refresh)

        threading.Thread(target=load, daemon=True).start()

    def test_sound(self):
        threading.Thread(target=self.player.play_demo, daemon=True).start()

    def rescan_packs(self):
        self._populate_packs()
        self.refresh()

    def open_packs_folder(self):
        import os

        core.ensure_user_dirs()
        os.startfile(core.USER_PACKS_DIR)

    def _safe_autostart_state(self):
        try:
            if self.host is not None:
                return self.host.autostart_enabled()
            return core.is_autostart_enabled()
        except OSError:
            return False

    def _on_autostart_clicked(self):
        try:
            if self.host is not None:
                self.host.set_autostart(self.autostart_var.get())
                return
            core.set_autostart(self.autostart_var.get())
        except OSError as error:
            self.autostart_var.set(self._safe_autostart_state())
            messagebox.showerror(core.APP_NAME, f"Could not change startup setting:\n{error}")

    def open_recorder(self):
        if self.recorder:
            self.recorder.top.lift()
            return
        self.recorder = RecorderDialog(self)


    # ------------------------------------------------------------------ buddy tab
    def _build_buddy_tab(self, tab):
        import keyit_buddy as buddy_module

        buddy_module.buddy_settings(self.settings)
        self.buddy = None
        self.buddy_enabled_var = tk.BooleanVar(value=self.settings["buddy_enabled"])
        enable_text = ("Buddy can see your screen and answer  (Ctrl+Alt+Space: tap to type, hold to talk)" if self.host
                       else "Enable Buddy – hold Ctrl+Alt+Space and ask about your screen")
        ttk.Checkbutton(tab, text=enable_text,
                        variable=self.buddy_enabled_var, command=self._on_buddy_toggled).grid(
            row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(tab, text="Vision model").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.model_box = ttk.Combobox(tab, width=30)
        self.model_box.set(self.settings["buddy_model"])
        self.model_box.grid(row=1, column=1, sticky="ew", padx=8, pady=(10, 0))
        self.model_box.bind("<<ComboboxSelected>>", self._on_buddy_option)
        self.model_box.bind("<FocusOut>", self._on_buddy_option)
        ttk.Button(tab, text="Refresh", command=self.refresh_models).grid(row=1, column=2, pady=(10, 0))
        self.model_status = ttk.Label(tab, foreground="#777")
        self.model_status.grid(row=2, column=1, columnspan=2, sticky="w", padx=8)

        ttk.Label(tab, text="Speech model").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.whisper_box = ttk.Combobox(tab, state="readonly", width=12, values=buddy_module.WHISPER_MODELS)
        self.whisper_box.set(self.settings["whisper_model"])
        self.whisper_box.grid(row=3, column=1, sticky="w", padx=8, pady=(8, 0))
        self.whisper_box.bind("<<ComboboxSelected>>", self._on_buddy_option)
        self.brain_box = ttk.Combobox(tab, state="readonly", width=16, values=list(BRAINS))
        engine = self.settings.get("buddy_engine", "auto")
        self.brain_box.set(next((label for label, value in BRAINS.items() if value == engine), "Local GPU"))
        self.brain_box.grid(row=3, column=2, sticky="w", pady=(8, 0))
        self.brain_box.bind("<<ComboboxSelected>>", self._on_brain_changed)

        ttk.Label(tab, text="Voice").grid(row=4, column=0, sticky="w", pady=(8, 0))
        voice_row = ttk.Frame(tab)
        voice_row.grid(row=4, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.voice_box = ttk.Combobox(voice_row, state="readonly", width=30, height=20)
        self.voice_box.pack(side="left", fill="x", expand=True)
        self.voice_box.bind("<<ComboboxSelected>>", self._on_buddy_option)
        ttk.Button(voice_row, text="Test", width=6, command=self._test_voice).pack(side="left", padx=(6, 0))
        self.speak_var = tk.BooleanVar(value=self.settings["buddy_speak"])
        ttk.Checkbutton(tab, text="Speak", variable=self.speak_var,
                        command=self._on_buddy_option).grid(row=4, column=2, sticky="w", pady=(8, 0))

        self.cursor_always_var = tk.BooleanVar(value=self.settings["buddy_cursor_always"])
        ttk.Checkbutton(tab, text="Keep Buddy next to my mouse all the time",
                        variable=self.cursor_always_var, command=self._on_cursor_always_toggled).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.buddy_sounds_var = tk.BooleanVar(value=self.settings.get("buddy_sounds", True))
        ttk.Checkbutton(tab, text="Wake-up chime", variable=self.buddy_sounds_var,
                        command=self._on_buddy_sounds_toggled).grid(row=5, column=2, sticky="w", pady=(8, 0))

        self.question_var = tk.StringVar()
        if self.host is None:                            # inside Buddy the ask box is on the Home tab
            ttk.Label(tab, text="Or type").grid(row=6, column=0, sticky="w", pady=(12, 0))
            entry = ttk.Entry(tab, textvariable=self.question_var)
            entry.grid(row=6, column=1, sticky="ew", padx=8, pady=(12, 0))
            entry.bind("<Return>", lambda _e: self.ask_typed())
            ttk.Button(tab, text="Ask", command=self.ask_typed).grid(row=6, column=2, pady=(12, 0))

        self.buddy_status = ttk.Label(tab, foreground="#777", wraplength=430, justify="left",
                                      text="Runs 100% on this PC with Ollama. A screenshot is taken only when you ask.")
        self.buddy_status.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.refresh_models()
        self._load_voices()
        if self.settings["buddy_enabled"]:
            self.root.after(200, self._start_buddy)

    def _set_buddy_status(self, text):
        self.buddy_status.config(text=text)

    def refresh_models(self):
        import keyit_buddy as buddy_module

        def work():
            client = buddy_module.OllamaClient(self.settings["ollama_url"])
            try:
                models = client.list_models()
            except Exception:
                self.post(self.model_status.config, {"text": "Ollama is not running – start it from the Start menu."})
                return
            chosen = self.settings["buddy_model"]
            vision = client.has_vision(chosen) if chosen in models else False
            if chosen not in models:
                note = f"'{chosen}' not installed – run:  ollama pull {chosen}"
            elif vision:
                note = "✓ can see images"
            else:
                note = "✗ this model can't see images – pick a vision model"
            self.post(self.model_box.config, {"values": models})
            self.post(self.model_status.config, {"text": note})

        threading.Thread(target=work, daemon=True).start()

    def _load_voices(self):
        """Natural voices first (they sound human), then the Windows voices."""
        import keyit_buddy as buddy_module
        import keyit_voice

        def work():
            try:
                import comtypes
                import comtypes.client

                comtypes.CoInitialize()
                tokens = comtypes.client.CreateObject("SAPI.SpVoice").GetVoices()
                windows_voices = [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
            except Exception:
                windows_voices = []
            voices = keyit_voice.names() + windows_voices
            current = (self.settings["buddy_voice"] or buddy_module.default_voice()
                       or (windows_voices[0] if windows_voices else ""))
            self.post(self.voice_box.config, {"values": voices})
            self.post(self.voice_box.set, current)

        threading.Thread(target=work, daemon=True).start()

    def _test_voice(self):
        """Say a short line with the voice picked in the list."""
        import keyit_voice

        self._on_buddy_option()
        if keyit_voice.is_natural(self.voice_box.get()) and not keyit_voice.is_downloaded():
            self._set_buddy_status(f"Downloading the natural voices once (~{keyit_voice.DOWNLOAD_MB} MB)...")
        chosen = self.voice_box.get()

        def work():
            if self.buddy:
                speaker = self.buddy.speaker
            else:                                        # brain off: a speaker just for the test
                import keyit_buddy as buddy_module

                if getattr(self, "_test_speaker", None) is None:
                    self._test_speaker = buddy_module.Speaker(
                        chosen, on_status=lambda text: self.post(self._set_buddy_status, text))
                speaker = self._test_speaker
                speaker.set_voice(chosen)
            speaker.test()

        threading.Thread(target=work, daemon=True).start()

    def _on_buddy_option(self, _event=None):
        changed_model = self.model_box.get().strip() != self.settings["buddy_model"]
        self.settings["buddy_model"] = self.model_box.get().strip() or self.settings["buddy_model"]
        self.settings["whisper_model"] = self.whisper_box.get()
        self.settings["buddy_speak"] = self.speak_var.get()
        if self.voice_box.get() and self.voice_box.get() != self.settings["buddy_voice"]:
            self.settings["buddy_voice"] = self.voice_box.get()
            if self.buddy:
                self.buddy.speaker.set_voice(self.settings["buddy_voice"])
        core.save_settings(self.settings)
        if changed_model:
            self.refresh_models()
            if self.buddy:
                threading.Thread(target=self.buddy._prepare, daemon=True).start()

    def _on_brain_changed(self, _event=None):
        engine = BRAINS.get(self.brain_box.get(), "auto")
        self.settings["buddy_engine"] = engine
        core.save_settings(self.settings)
        self._set_buddy_status(BRAIN_NOTES[engine])
        if self.buddy:
            self.buddy.maas_failed_until = 0.0
            threading.Thread(target=self.buddy._prepare, daemon=True).start()

    def _on_cursor_always_toggled(self):
        self.settings["buddy_cursor_always"] = self.cursor_always_var.get()
        core.save_settings(self.settings)
        if self.buddy:
            self.buddy.overlay.set_always_on(self.settings["buddy_cursor_always"])

    def _on_buddy_sounds_toggled(self):
        self.settings["buddy_sounds"] = self.buddy_sounds_var.get()
        core.save_settings(self.settings)
        if self.settings["buddy_sounds"]:
            self.player.play_effect("buddy_wake")       # let them hear what they switched on

    def _on_buddy_toggled(self):
        self.settings["buddy_enabled"] = self.buddy_enabled_var.get()
        core.save_settings(self.settings)
        if self.settings["buddy_enabled"]:
            self._start_buddy()
        else:
            self._stop_buddy()
        self.tray.update_menu()

    def _start_buddy(self):
        import keyit_buddy as buddy_module

        if self.buddy is None:
            host_root = self.host.root if self.host else self.root
            self.buddy = buddy_module.Buddy(
                self.settings, host_root, self.post,
                on_status=lambda text: self.post(self._set_buddy_status, text),
                overlay=self.host.make_overlay() if self.host else None,
                context_provider=self.host.buddy_context if self.host else None,
                sound=self.player.play_effect,
            )
        self.hook.buddy = self.buddy
        self.buddy.overlay.set_always_on(self.settings["buddy_cursor_always"])

    def _stop_buddy(self):
        self.hook.buddy = None
        if self.buddy:
            self.buddy.overlay.set_always_on(False)     # brain off = no buddy by the mouse
            self.buddy.cancel()
        self._set_buddy_status("Buddy is off.")

    def toggle_buddy(self):
        self.buddy_enabled_var.set(not self.buddy_enabled_var.get())
        self._on_buddy_toggled()

    def ask_typed(self):
        import keyit_buddy as buddy_module

        question = self.question_var.get().strip()
        if not question:
            return
        if not self.buddy:
            self.buddy_enabled_var.set(True)
            self._on_buddy_toggled()
        self.question_var.set("")
        self.hide(notify=False)                          # get out of the way of the screenshot
        self.root.after(350, lambda: self.buddy.ask_text(question, buddy_module.monitor_under_cursor()))

    # ------------------------------------------------------------------ tray
    def _build_tray(self):
        import pystray

        def pack_items():
            for name, path in core.list_packs().items():
                yield pystray.MenuItem(
                    core.pack_display_name(path),
                    lambda n=name: self.post(self.select_pack, n),
                    checked=lambda _item, n=name: self.settings["pack"] == n,
                    radio=True,
                )

        menu = pystray.Menu(
            pystray.MenuItem("Open KeyIt", lambda: self.post(self.show), default=True),
            pystray.MenuItem("Sound on", lambda: self.post(self.toggle_enabled),
                             checked=lambda _item: self.settings["enabled"]),
            pystray.MenuItem("Sound pack", pystray.Menu(pack_items)),
            pystray.MenuItem("Buddy (Ctrl+Alt+Space)", lambda: self.post(self.toggle_buddy),
                             checked=lambda _item: self.settings.get("buddy_enabled", False)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit KeyIt", lambda: self.post(self.quit)),
        )
        return pystray.Icon(core.APP_NAME, make_tray_image(self.settings["enabled"]), core.APP_NAME, menu)


class RecorderDialog:
    """Guided recording of the user's own keyboard into a new pack."""

    INSTRUCTIONS = (
        "1.  Put your microphone close to the keyboard; keep the room quiet.\n"
        "2.  Press Start, then type normally in the box below for 30–60 seconds.\n"
        "3.  Hit Space, Enter, Backspace and Shift several times each.\n"
        "4.  Leave a short pause now and then – it helps KeyIt line up the sounds.\n"
        "5.  Press Stop & build. KeyIt's own sounds are muted while recording."
    )

    def __init__(self, app):
        self.app = app
        self.session = None
        self.top = tk.Toplevel(app.root)
        self.top.title("Record my keyboard – KeyIt")
        self.top.resizable(False, False)
        self.top.transient(app.root)
        self.top.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(self.top, padding=16)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Pack name").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value="My Keyboard")
        ttk.Entry(frame, textvariable=self.name_var, width=36).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(frame, text=self.INSTRUCTIONS, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=10)

        ttk.Label(frame, text="Mic level").grid(row=2, column=0, sticky="w")
        self.level_bar = ttk.Progressbar(frame, maximum=1.0, length=280)
        self.level_bar.grid(row=2, column=1, sticky="w", padx=(8, 0))
        self.stats_label = ttk.Label(frame, text="Not recording")
        self.stats_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.missing_label = ttk.Label(frame, foreground="#b35c00")
        self.missing_label.grid(row=4, column=0, columnspan=2, sticky="w")

        self.text = tk.Text(frame, height=5, width=56, wrap="word", font=("Segoe UI", 10))
        self.text.grid(row=5, column=0, columnspan=2, pady=8)
        self.text.insert("1.0", "Type here while recording…")
        self.text.config(state="disabled")

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e")
        self.start_button = ttk.Button(buttons, text="Start recording", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop & build", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.close).pack(side="left")

    def start(self):
        try:
            self.session = RecordingSession(self.app.hook, self.app.player)
            self.session.start()
        except Exception as error:
            self.session = None
            messagebox.showerror("Microphone", f"Could not open the microphone:\n{error}", parent=self.top)
            return
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.focus_set()
        self._tick()

    def _tick(self):
        if not self.session:
            return
        self.level_bar["value"] = min(1.0, self.session.mic.level ** 0.5)
        elapsed = time.perf_counter() - self.session.started_at
        self.stats_label.config(text=f"Recording…  {self.session.key_count()} keystrokes · {elapsed:.0f} s")
        recorded = self.session.recorded_codes()
        missing = [label for code, label in IMPORTANT_KEYS.items() if code not in recorded]
        self.missing_label.config(text=f"Still to press: {', '.join(missing)}" if missing else "All key types captured ✓")
        self.top.after(100, self._tick)

    def stop(self):
        if not self.session:
            return
        audio, audio_start, events = self.session.stop()
        key_count = self.session.key_count()
        self.session = None
        self.level_bar["value"] = 0
        self.text.config(state="disabled")
        self.stop_button.config(state="disabled")
        self.start_button.config(state="normal")
        if key_count < MIN_KEYS_TO_BUILD:
            self.stats_label.config(text=f"Only {key_count} keystrokes – type a bit more and try again.")
            return
        self.stats_label.config(text="Building pack…")
        name = self.name_var.get().strip() or "My Keyboard"
        threading.Thread(target=self._build, args=(audio, audio_start, events, name), daemon=True).start()

    def _build(self, audio, audio_start, events, name):
        try:
            clips, info = slice_keystrokes(audio, events, audio_start)
            pack_dir, report = build_pack(clips, name, core.USER_PACKS_DIR,
                                          reserved_names=set(core.list_packs()))
            self.app.post(self._finished, pack_dir, report, info)
        except Exception as error:
            self.app.post(self._failed, error)

    def _finished(self, pack_dir, report, info):
        self.app.rescan_packs()
        self.app.select_pack(pack_dir.name)
        message = (f"Saved “{self.name_var.get()}”: {report['recorded_down_keys']} keys "
                   f"+ {report['recorded_up_keys']} release sounds (mic latency ≈ {info['latency_ms']:.0f} ms).")
        if report["missing_important"]:
            message += f"\nNot captured: {', '.join(report['missing_important'])} – they use letter sounds."
        self.stats_label.config(text=message)
        self.missing_label.config(text="")

    def _failed(self, error):
        self.stats_label.config(text="Build failed.")
        messagebox.showerror("Record my keyboard", str(error), parent=self.top)

    def close(self):
        if self.session:
            self.session.stop()
            self.session = None
        self.app.recorder = None
        self.top.destroy()
