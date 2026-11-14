"""
KeyIt core: paths, settings, sound packs, audio player, keyboard hook, Windows helpers.

Privacy: key codes are only used to choose a sound. Nothing is logged, stored or sent.
"""
import ctypes
import json
import os
import queue
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402
from pynput import keyboard  # noqa: E402

import keyit_sfx  # noqa: E402

APP_NAME = "KeyIt"
VERSION = "0.1.0"

# --------------------------------------------------------------------------- paths
FROZEN = getattr(sys, "frozen", False)                       # running as PyInstaller exe
_MODULE_DIR = Path(__file__).resolve().parent
BUNDLE_DIR = _MODULE_DIR if (_MODULE_DIR / "packs").is_dir() else Path(getattr(sys, "_MEIPASS", _MODULE_DIR))
BUILTIN_PACKS_DIR = BUNDLE_DIR / "packs"                     # read-only, shipped with app
ASSETS_DIR = BUNDLE_DIR / "assets"
USER_DIR = Path(os.environ.get("KEYIT_HOME") or Path(os.environ.get("APPDATA", Path.home())) / APP_NAME)
USER_PACKS_DIR = USER_DIR / "packs"                          # recorded/downloaded packs
SETTINGS_FILE = USER_DIR / "settings.json"
LOG_FILE = USER_DIR / "keyit.log"

# --------------------------------------------------------------------------- audio tuning
MIXER_RATE = 44100
MIXER_BUFFER = 256            # samples -> about 5.8 ms output buffering
MIXER_VOICES = 48
PITCH_VARIANTS = (0.96, 0.98, 1.0, 1.02, 1.04)
VOLUME_JITTER = (0.85, 1.0)
STEREO_WIDTH = 0.35           # 0 = mono, 1 = hard left/right
MISSED_KEYUP_TIMEOUT_S = 1.1  # longer than the max Windows key-repeat delay (1 s)
MOUSE_VOLUME = 0.6            # mouse clicks sit a bit under the keyboard
CHIME_VOLUME = 0.6            # Buddy's wake-up / got-it chimes (own switch, not the keyboard mute)

# --------------------------------------------------------------------------- mouse codes
# Negative so they can never clash with keyboard scan codes in the shared sound queue
MOUSE_BUTTONS = {-1: "left", -2: "right", -3: "middle", -4: "side"}

# --------------------------------------------------------------------------- key codes
# Mechvibes/iohook codes = Windows scan code set 1; extended keys = 0xE000 | scan
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_EXTENDED = 0x01
FAKE_SHIFT_CODES = {0xE02A, 0xE036}
CTRL_CODES = {29, 3613}
ALT_CODES = {56, 3640}
M_CODE = 50
SPACE_CODE = 57
D_CODE = 32                  # Ctrl+Alt+D: Buddy dictation (hold = talk, tap = hands-free)
T_CODE = 20                     # Ctrl+Alt+T: Buddy talk mode
ESCAPE_CODE = 1
VK_MENU_MASK = 0xE8           # unassigned virtual key: tapped so a lone Alt release can't open a menu
LETTER_CODES = list(range(16, 26)) + list(range(30, 39)) + list(range(44, 51))
DEMO_TYPING = [35, 18, 38, 38, 24, 57, 17, 24, 19, 38, 32, 28, 14, 14, 37, 18, 21, 23, 20, 57]

KEYBOARD_ROWS = [
    [1, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88],
    [41, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
    [15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 43],
    [58, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 28],
    [42, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54],
]
RIGHT_SIDE_CODES = [3666, 3655, 3657, 3667, 3663, 3665, 57416, 57419, 57421, 57424, 3612]


def build_pan_table():
    pan = {}
    for row in KEYBOARD_ROWS:
        for index, code in enumerate(row):
            pan[code] = -1.0 + 2.0 * index / (len(row) - 1)
    pan.update({57: 0.0, 29: -1.0, 3675: -0.8, 56: -0.6, 3640: 0.6, 3676: 0.8, 3613: 1.0})
    pan.update({code: 1.0 for code in RIGHT_SIDE_CODES})
    return pan


PAN_BY_CODE = build_pan_table()


# --------------------------------------------------------------------------- settings
def ensure_user_dirs():
    USER_PACKS_DIR.mkdir(parents=True, exist_ok=True)


def load_settings():
    settings = {"pack": "synth-thock", "volume": 0.75, "enabled": True, "mouse_sounds": True, "buddy_sounds": True}
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return settings


def save_settings(settings):
    ensure_user_dirs()
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def list_packs():
    """Return {folder_name: path}. User packs override built-in packs with the same name."""
    packs = {}
    for root in (BUILTIN_PACKS_DIR, USER_PACKS_DIR):
        if root.is_dir():
            for folder in sorted(root.iterdir()):
                if (folder / "config.json").is_file():
                    packs[folder.name] = folder
    return packs


def pack_display_name(folder):
    try:
        return json.loads((Path(folder) / "config.json").read_text(encoding="utf-8")).get("name") or Path(folder).name
    except (OSError, json.JSONDecodeError):
        return Path(folder).name


# --------------------------------------------------------------------------- sound packs
def sound_to_array(sound):
    array = pygame.sndarray.array(sound)
    return array if array.ndim == 2 else array[:, None]


def array_to_sound(array):
    _, _, mixer_channels = pygame.mixer.get_init()
    if mixer_channels == 1:
        array = array[:, :1]
    elif array.shape[1] == 1:
        array = np.repeat(array, mixer_channels, axis=1)
    if mixer_channels == 1:
        array = array[:, 0]
    return pygame.sndarray.make_sound(np.ascontiguousarray(array, dtype=np.int16))


def float_to_sound(signal):
    """-1..1 mono float signal (keyit_sfx) -> pygame Sound."""
    samples = np.clip(signal, -1.0, 1.0) * 32767
    return array_to_sound(samples[:, None])


def pitch_variants(array):
    """Resample to a few slightly different pitches so repeated keys sound natural."""
    variants = []
    frame_count = array.shape[0]
    source_positions = np.arange(frame_count)
    for factor in PITCH_VARIANTS:
        new_count = max(1, int(frame_count / factor))
        positions = np.linspace(0, frame_count - 1, new_count)
        resampled = np.stack(
            [np.interp(positions, source_positions, array[:, ch]) for ch in range(array.shape[1])],
            axis=1,
        )
        variants.append(array_to_sound(resampled))
    return variants


class SoundPack:
    """Mechvibes-style pack: config.json + audio ('single' sprite or 'multi' files)."""

    def __init__(self, folder):
        self.folder = Path(folder)
        config = json.loads((self.folder / "config.json").read_text(encoding="utf-8"))
        self.name = config.get("name", self.folder.name)
        self.define_type = config.get("key_define_type", "single")
        self._file_cache = {}
        self._variant_cache = {}
        self.down = self._load_defines(config.get("defines") or {}, config.get("sound"))
        self.up = self._load_defines(config.get("defines_up") or {}, config.get("sound_up") or config.get("sound"))
        self.down_fallback = self._fallback_pool(self.down)
        self.up_fallback = self._fallback_pool(self.up)
        if not self.down:
            raise ValueError(f"pack '{self.folder.name}' defines no playable sounds")

    def _file_array(self, filename):
        if filename not in self._file_cache:
            self._file_cache[filename] = sound_to_array(pygame.mixer.Sound(str(self.folder / filename)))
        return self._file_cache[filename]

    def _variants_for(self, value, sprite_file):
        key = json.dumps(value)
        if key not in self._variant_cache:
            if self.define_type == "multi":
                array = self._file_array(value)
            else:
                start_ms, duration_ms = value
                start = int(start_ms * MIXER_RATE / 1000)
                stop = start + int(duration_ms * MIXER_RATE / 1000)
                array = self._file_array(sprite_file)[start:stop]
            self._variant_cache[key] = pitch_variants(array) if array.shape[0] else []
        return self._variant_cache[key]

    def _load_defines(self, defines, sprite_file):
        mapping = {}
        for code_text, value in defines.items():
            if value in (None, "", []):
                continue
            try:
                variants = self._variants_for(value, sprite_file)
            except (FileNotFoundError, pygame.error, ValueError, TypeError) as error:
                print(f"[{self.folder.name}] skipping key {code_text}: {error}", file=sys.stderr)
                continue
            if variants:
                mapping[int(code_text)] = variants
        return mapping

    @staticmethod
    def _fallback_pool(mapping):
        letters = [mapping[c] for c in LETTER_CODES if c in mapping]
        return letters or list(mapping.values())

    def lookup(self, code, is_down):
        mapping, pool = (self.down, self.down_fallback) if is_down else (self.up, self.up_fallback)
        if code in mapping:
            return mapping[code]
        return random.choice(pool) if pool else None


# --------------------------------------------------------------------------- player
class Player:
    """Owns the mixer; plays sounds from a queue fed by the keyboard hook."""

    def __init__(self, settings, debug=False):
        pygame.mixer.pre_init(MIXER_RATE, -16, 2, MIXER_BUFFER)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(MIXER_VOICES)
        self.settings = settings
        self.debug = debug
        self.suppressed = False       # True while recording, so the mic doesn't hear playback
        self.pack = None
        self.events = queue.SimpleQueue()
        self._lock = threading.Lock()
        self.effects = {name: [float_to_sound(signal) for signal in signals]
                        for name, signals in keyit_sfx.all_effects().items()}
        self.load_pack(settings["pack"])
        threading.Thread(target=self._worker, name="keyit-audio", daemon=True).start()

    def load_pack(self, pack_name):
        packs = list_packs()
        if not packs:
            raise SystemExit("No sound packs found. Run generate_packs.py first.")
        if pack_name not in packs:
            pack_name = "synth-thock" if "synth-thock" in packs else next(iter(packs))
        started = time.perf_counter()
        new_pack = SoundPack(packs[pack_name])
        with self._lock:
            self.pack = new_pack
        self.settings["pack"] = pack_name
        print(f"Loaded pack '{new_pack.name}' ({len(new_pack.down)} keys, "
              f"{'with' if new_pack.up else 'no'} key-up sounds) in {time.perf_counter() - started:.2f}s")
        return new_pack

    def submit(self, code, is_down):
        self.events.put((code, is_down, time.perf_counter()))

    def _worker(self):
        while True:
            code, is_down, queued_at = self.events.get()
            if not self.settings["enabled"] or self.suppressed:
                continue
            if code in MOUSE_BUTTONS:
                if self.settings.get("mouse_sounds", True):
                    name = f"mouse_{MOUSE_BUTTONS[code]}_{'down' if is_down else 'up'}"
                    self.play_effect(name, self.settings["volume"] * MOUSE_VOLUME)
            else:
                self.play(code, is_down, queued_at)

    def play_effect(self, name, volume=None):
        """Play a synthesized effect (mouse click, Buddy chime). Safe to call from any thread."""
        if self.suppressed or name not in self.effects:
            return
        channel = random.choice(self.effects[name]).play()
        if channel is not None:
            level = CHIME_VOLUME if volume is None else volume
            level *= random.uniform(*VOLUME_JITTER) if name.startswith("mouse") else 1.0
            channel.set_volume(level, level)

    def play(self, code, is_down, queued_at=None):
        with self._lock:
            variants = self.pack.lookup(code, is_down)
        if not variants:
            return
        channel = random.choice(variants).play()
        if channel is None:
            return
        volume = self.settings["volume"] * random.uniform(*VOLUME_JITTER)
        pan = PAN_BY_CODE.get(code, 0.0) * STEREO_WIDTH
        channel.set_volume(volume * (1 - max(pan, 0)), volume * (1 + min(pan, 0)))
        if self.debug:
            delay_ms = 1000 * (time.perf_counter() - queued_at) if queued_at else 0
            print(f"{'down' if is_down else 'up  '} code={code:<6} pan={pan:+.2f} dispatch={delay_ms:.2f} ms")

    def play_demo(self):
        """Simulated typing burst through the normal playback path (ignores mute)."""
        for code in DEMO_TYPING:
            self.play(code, True)
            time.sleep(random.uniform(0.06, 0.12))
            self.play(code, False)
            time.sleep(random.uniform(0.02, 0.05))


# --------------------------------------------------------------------------- keyboard hook
def tap_menu_mask_key():
    """Ctrl+Alt+Space swallows the Space, so the app in front only sees Ctrl/Alt go down and up.
    A lone Alt release opens its menu / system menu; tapping an unused key in between prevents that."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.keybd_event(VK_MENU_MASK, 0, 0, 0)
        user32.keybd_event(VK_MENU_MASK, 0, 2, 0)     # KEYEVENTF_KEYUP
    except Exception:
        pass


# --------------------------------------------------------------------------- mouse clicks
class MouseClicks:
    """Hears mouse button presses through Windows Raw Input and feeds them to the Player.

    Raw Input is posted to our own hidden window, so - unlike a low-level mouse hook - Windows never
    waits for Python before moving the cursor. Only button flags are read; positions are ignored.
    """

    WM_INPUT = 0x00FF
    WM_QUIT = 0x0012
    RID_INPUT = 0x10000003
    RIM_TYPEMOUSE = 0
    RIDEV_INPUTSINK = 0x00000100      # also while another app is in front
    RIDEV_REMOVE = 0x00000001
    HWND_MESSAGE = -3
    # RI_MOUSE_* button flags -> (mouse code, is_down)
    BUTTON_FLAGS = (
        (0x0001, -1, True), (0x0002, -1, False),       # left
        (0x0004, -2, True), (0x0008, -2, False),       # right
        (0x0010, -3, True), (0x0020, -3, False),       # middle
        (0x0040, -4, True), (0x0080, -4, False),       # side button 4 (back)
        (0x0100, -4, True), (0x0200, -4, False),       # side button 5 (forward)
    )

    def __init__(self, player):
        self.player = player
        self.thread = None
        self.thread_id = None
        self.ready = threading.Event()
        self.error = None

    def start(self):
        if os.name != "nt" or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="keyit-mouse", daemon=True)
        self.thread.start()
        self.ready.wait(2.0)

    def stop(self):
        if self.thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)
        self.thread = None

    def _run(self):
        from ctypes import wintypes

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                        ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]

        class RAWINPUTHEADER(ctypes.Structure):
            _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                        ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]

        class BUTTONS(ctypes.Structure):
            _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]

        class BUTTONS_UNION(ctypes.Union):
            _fields_ = [("ulButtons", wintypes.ULONG), ("buttons", BUTTONS)]

        class RAWMOUSE(ctypes.Structure):
            _fields_ = [("usFlags", wintypes.USHORT), ("u", BUTTONS_UNION), ("ulRawButtons", wintypes.ULONG),
                        ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG),
                        ("ulExtraInformation", wintypes.ULONG)]

        class RAWINPUT(ctypes.Structure):
            _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
        user32.GetRawInputData.restype = wintypes.UINT
        user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                           ctypes.POINTER(wintypes.UINT), wintypes.UINT]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DestroyWindow.argtypes = [wintypes.HWND]

        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        hwnd = user32.CreateWindowExW(0, "STATIC", "KeyIt mouse clicks", 0, 0, 0, 0, 0,
                                      wintypes.HWND(self.HWND_MESSAGE), None, None, None)
        device = RAWINPUTDEVICE(0x01, 0x02, self.RIDEV_INPUTSINK, hwnd)     # generic desktop / mouse
        if not hwnd or not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
            self.error = f"mouse clicks unavailable (Windows error {ctypes.get_last_error()})"
            print(self.error, file=sys.stderr)
            self.ready.set()
            return
        self.ready.set()

        data = RAWINPUT()
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == self.WM_INPUT:
                size = wintypes.UINT(ctypes.sizeof(data))
                got = user32.GetRawInputData(wintypes.HANDLE(message.lParam), self.RID_INPUT,
                                             ctypes.byref(data), ctypes.byref(size), header_size)
                if got not in (0, 0xFFFFFFFF) and data.header.dwType == self.RIM_TYPEMOUSE:
                    flags = data.mouse.u.buttons.usButtonFlags
                    if flags & 0x03FF:
                        for bit, code, is_down in self.BUTTON_FLAGS:
                            if flags & bit:
                                self.player.submit(code, is_down)
            user32.DispatchMessageW(ctypes.byref(message))   # lets Windows clean up the raw input

        device.dwFlags = self.RIDEV_REMOVE
        device.hwndTarget = None
        user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
        user32.DestroyWindow(hwnd)
        self.thread_id = None


class KeyboardHook:
    """Global low-level keyboard hook; ignores auto-repeat; Ctrl+Alt+M toggles mute.

    observers: callables(code, is_down, perf_counter_time) - used by the recorder.
    """

    def __init__(self, player, on_toggle=None):
        self.player = player
        self.on_toggle = on_toggle
        self.observers = []
        self.held = {}
        self.buddy = None             # object with press()/release()/cancel(); set by the GUI
        self.buddy_active = False
        self.dictate_active = False
        self.listener = keyboard.Listener(win32_event_filter=self._filter)
        self.mouse = MouseClicks(player)

    def start(self):
        self.listener.start()
        self.listener.wait()
        self.mouse.start()

    def stop(self):
        self.mouse.stop()
        self.listener.stop()

    def _filter(self, msg, data):
        event_time = time.perf_counter()
        code = data.scanCode | (0xE000 if data.flags & LLKHF_EXTENDED else 0)
        if code in FAKE_SHIFT_CODES or data.vkCode == VK_MENU_MASK:
            return False
        now = time.monotonic()
        modifiers_down = self.held.keys() & CTRL_CODES and self.held.keys() & ALT_CODES

        # Buddy push-to-talk: hold Ctrl+Alt+Space. Space is swallowed so nothing gets typed.
        if code == SPACE_CODE and self.buddy is not None:
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN) and (self.buddy_active or modifiers_down):
                if not self.buddy_active:
                    self.buddy_active = True
                    threading.Thread(target=tap_menu_mask_key, daemon=True).start()
                    threading.Thread(target=self.buddy.press, daemon=True).start()
                    threading.Thread(target=self._watch_space_release, daemon=True).start()
                self.listener.suppress_event()
            if msg in (WM_KEYUP, WM_SYSKEYUP) and self.buddy_active:
                self.buddy_active = False
                threading.Thread(target=self.buddy.release, daemon=True).start()
                self.listener.suppress_event()
        # Buddy dictation: Ctrl+Alt+D types what you say into the app in front. D is swallowed.
        if code == D_CODE and self.buddy is not None and hasattr(self.buddy, "dictate_press"):
            dictating = getattr(self, "dictate_active", False)
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN) and (dictating or modifiers_down):
                if not dictating:
                    self.dictate_active = True
                    threading.Thread(target=tap_menu_mask_key, daemon=True).start()
                    threading.Thread(target=self.buddy.dictate_press, daemon=True).start()
                    threading.Thread(target=self._watch_d_release, daemon=True).start()
                self.listener.suppress_event()
            if msg in (WM_KEYUP, WM_SYSKEYUP) and dictating:
                self.dictate_active = False
                threading.Thread(target=self.buddy.dictate_release, daemon=True).start()
                self.listener.suppress_event()
        # Buddy talk mode: Ctrl+Alt+T starts or ends a conversation. T is swallowed, but only with the
        # modifiers down, so a missed key-up can never eat a plain T.
        if code == T_CODE and self.buddy is not None and getattr(self.buddy, "talk_enabled", lambda: False)():
            talk_key_down = getattr(self, "talk_key_down", False)
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self.talk_key_down = bool(modifiers_down)
                if modifiers_down:
                    if not talk_key_down:                        # not the keyboard's auto-repeat
                        threading.Thread(target=tap_menu_mask_key, daemon=True).start()
                        threading.Thread(target=self.buddy.talk_toggle, daemon=True).start()
                    self.listener.suppress_event()
            elif talk_key_down:
                self.talk_key_down = False
                self.listener.suppress_event()
        if code == ESCAPE_CODE and self.buddy is not None and msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            threading.Thread(target=self.buddy.cancel, daemon=True).start()

        if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            last_seen = self.held.get(code)
            self.held[code] = now
            if last_seen is not None and now - last_seen < MISSED_KEYUP_TIMEOUT_S:
                return False  # auto-repeat while holding the key
            if code == M_CODE and modifiers_down:
                self.toggle()
                return False
            self._emit(code, True, event_time)
        elif msg in (WM_KEYUP, WM_SYSKEYUP):
            self.held.pop(code, None)
            self._emit(code, False, event_time)
        return False  # skip pynput callbacks; the key still reaches other apps normally

    def _watch_space_release(self):
        """Safety net: if the Space key-up never reaches the hook, end push-to-talk anyway."""
        import ctypes

        get_key_state = ctypes.windll.user32.GetAsyncKeyState
        time.sleep(0.3)
        while self.buddy_active:
            if not get_key_state(0x20) & 0x8000:          # VK_SPACE is physically up
                time.sleep(0.15)
                if self.buddy_active and not get_key_state(0x20) & 0x8000:
                    self.buddy_active = False
                    if self.buddy is not None:
                        self.buddy.release()
                    return
            time.sleep(0.1)

    def _watch_d_release(self):
        """Safety net for dictation: if the D key-up never reaches the hook, end the hold anyway."""
        import ctypes

        get_key_state = ctypes.windll.user32.GetAsyncKeyState
        time.sleep(0.3)
        while getattr(self, "dictate_active", False):
            if not get_key_state(0x44) & 0x8000:          # VK 'D' is physically up
                time.sleep(0.15)
                if getattr(self, "dictate_active", False) and not get_key_state(0x44) & 0x8000:
                    self.dictate_active = False
                    if self.buddy is not None:
                        self.buddy.dictate_release()
                    return
            time.sleep(0.1)

    def _emit(self, code, is_down, event_time):
        self.player.submit(code, is_down)
        for observer in list(self.observers):
            observer(code, is_down, event_time)

    def toggle(self):
        settings = self.player.settings
        settings["enabled"] = not settings["enabled"]
        save_settings(settings)
        print("Sound", "ON" if settings["enabled"] else "MUTED")
        if self.on_toggle:
            self.on_toggle()


# --------------------------------------------------------------------------- Windows helpers
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _kernel32:
    _kernel32.CreateMutexW.restype = ctypes.c_void_p
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    _kernel32.CreateEventW.restype = ctypes.c_void_p
    _kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
    _kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    _kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _kernel32.WaitForSingleObject.restype = ctypes.c_uint32


class SingleInstance:
    """Named mutex + 'show' event: a second launch asks the running KeyIt to open its window."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name=APP_NAME):
        self.mutex = _kernel32.CreateMutexW(None, False, f"Local\\{name}Mutex")
        self.already_running = ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS
        self.show_event = _kernel32.CreateEventW(None, False, False, f"Local\\{name}Show")

    def signal_existing(self):
        _kernel32.SetEvent(self.show_event)

    def on_show_request(self, callback):
        def wait_loop():
            while True:
                if _kernel32.WaitForSingleObject(self.show_event, 0xFFFFFFFF) == 0:
                    callback()
        threading.Thread(target=wait_loop, name="keyit-show", daemon=True).start()


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if FROZEN:
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{BUNDLE_DIR / "keyit.py"}" --minimized'


def is_autostart_enabled(value_name=APP_NAME):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, value_name)
            return True
    except FileNotFoundError:
        return False


def set_autostart(enabled, value_name=APP_NAME):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, value_name)
            except FileNotFoundError:
                pass
