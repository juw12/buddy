"""
Buddy can DO small things when you ask it to: close / minimize / maximize / focus a window, open an app,
folder or website, click an on-screen button, type text, press a shortcut.

The model ends its spoken reply with tags like  [DO:close:File Explorer - 1029]  (protocol in
ACTION_RULES). Buddy only acts on requests (never on "how do I..." questions), every action is logged,
and anything that can lose work (force-closing a frozen app, Alt+F4-like shortcuts) asks first.
"""
import ctypes
import difflib
import logging
import os
import re
import subprocess
import time
from ctypes import wintypes

ACTION_PATTERN = re.compile(r"\[\s*DO\s*:\s*([a-z_]+)\s*(?::\s*([^\]]*))?\]", re.IGNORECASE)
ACTIONS = ("close", "minimize", "maximize", "focus", "open", "click", "type", "keys")
MAX_ACTIONS = 4
CLOSE_WAIT_S = 3.0
# Questions that ask for an explanation, not an action: DO tags are ignored for these
QUESTION_STARTS = ("how ", "where ", "what ", "why ", "which ", "when ", "who ", "is ", "are ", "does ",
                   "do i ", "explain", "tell me", "show me")
RISKY_KEYS = {"alt+f4", "ctrl+w", "ctrl+shift+w", "delete", "shift+delete", "ctrl+q", "ctrl+shift+esc"}

ACTION_RULES = """
You can also DO simple things on this PC, but only when the user asks you to do them (not when they ask how).
Put the action tags at the very END, after one short sentence saying what you are doing. Examples:
  "Close the file explorer"  -> Closing File Explorer. [DO:close:File Explorer]
  "Close this"               -> Closing it. [DO:close:this]
  "Minimize Outlook"         -> Minimizing Outlook. [DO:minimize:Outlook]
  "Open notepad"             -> Opening Notepad. [DO:open:notepad]
  "Open google.com"          -> Opening Google. [DO:open:https://google.com]
  "Click Insert"             -> Clicking Insert. [DO:click:Insert]
  "Type hello"               -> Typing it. [DO:type:hello]
  "Save it"                  -> Saving. [DO:keys:ctrl+s]
  "How do I close this?"     -> Click the X at the top-right corner of the window. (a how-to question: no DO tag)
Actions: close, minimize, maximize, focus (a window name from the list below, or "this"), open, click, type, keys."""

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.IsHungAppWindow.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]  # PDWORD_PTR
WM_CLOSE = 0x0010
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
FRIENDLY = {"explorer": "File Explorer", "msedge": "Microsoft Edge", "chrome": "Google Chrome",
            "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "outlook": "Outlook",
            "ms-teams": "Microsoft Teams", "notepad": "Notepad", "code": "VS Code"}
APP_ALIASES = {"file explorer": "explorer", "explorer": "explorer", "notepad": "notepad",
               "calculator": "calc", "paint": "mspaint", "task manager": "taskmgr", "settings": "ms-settings:",
               "edge": "msedge", "microsoft edge": "msedge", "chrome": "chrome", "word": "winword",
               "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook", "teams": "msteams:",
               "command prompt": "cmd", "terminal": "wt", "control panel": "control",
               "snipping tool": "snippingtool"}


# --------------------------------------------------------------------------- parsing
def parse_actions(text):
    """[(action, argument), ...] from the model's reply (unknown actions dropped)."""
    found = []
    for match in ACTION_PATTERN.finditer(text):
        action = match.group(1).lower()
        if action in ACTIONS:
            found.append((action, (match.group(2) or "").strip()))
    return found[:MAX_ACTIONS]


def is_request(question):
    """True for 'close this', 'open notepad', 'can you close it'; False for 'how do I close it?'."""
    text = question.strip().lower()
    return not text.startswith(QUESTION_STARTS)


# --------------------------------------------------------------------------- windows
def _process_name(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.splitext(os.path.basename(buffer.value))[0].lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def list_windows():
    """Visible app windows, front first (Buddy's own windows excluded)."""
    own_pid = os.getpid()
    windows = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):     # skip owned popups
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"):
            return True
        process = _process_name(pid.value)
        app = "File Explorer" if class_name.value == "CabinetWClass" else FRIENDLY.get(process, process)
        if process == "applicationframehost":
            app = title.value
        windows.append({"hwnd": hwnd, "title": title.value, "pid": pid.value, "process": process,
                        "app": app, "class": class_name.value,
                        "minimized": bool(user32.IsIconic(hwnd))})
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return windows


def describe_windows(windows, limit=15):
    """Lines for the system prompt, e.g. '- File Explorer: 1029 (the user is in this one)'."""
    lines = []
    for index, window in enumerate(windows[:limit]):
        name = window["title"] if window["app"].lower() in window["title"].lower() \
            else f'{window["app"]}: {window["title"]}'
        notes = []
        if index == 0:
            notes.append("the user is in this one")
        if window["minimized"]:
            notes.append("minimized")
        lines.append(f"- {name[:70]}" + (f" ({', '.join(notes)})" if notes else ""))
    return "\n".join(lines)


PLACEHOLDERS = {"window", "the window", "a window", "app", "the app", "program"}


THIS_WORDS = re.compile(r"\b(this|it|that|current|here)\b", re.IGNORECASE)


def windows_named_in(question, windows):
    """Windows the user named in their own words ('close notepad'), front first."""
    text = question.lower()
    named = []
    for window in windows:
        names = {window["app"].lower(), window["process"]}
        names |= {part.strip() for part in window["title"].lower().split(" - ") if len(part.strip()) > 2}
        if any(name and len(name) > 2 and name in text for name in names):
            named.append(window)
    return named


def resolve_window(argument, question, windows):
    """Which window an action means. The user's own words beat the model's guess:
    - the model named a specific window that exists -> that one
    - otherwise the one window the user named ('close notepad' even if the model wrote 'this')
    - otherwise 'this/it' in the user's words -> the window they were in
    Returns None when unsure (Buddy then says it couldn't find it instead of closing the wrong thing)."""
    wanted = argument.strip().lower()
    specific = wanted not in PLACEHOLDERS and wanted not in ("", "this", "this window", "current",
                                                             "current window", "it", "that")
    if specific:
        window = find_window(argument, windows)
        if window is not None:
            return window
    named = windows_named_in(question, windows)
    if len({w["app"] for w in named}) == 1:
        return named[0]
    if not named and THIS_WORDS.search(question) and windows:
        return windows[0]
    return None


def find_window(name, windows):
    """Best window for the model's name ('this', a title, an app name). None if nothing fits."""
    if not windows:
        return None
    wanted = name.strip().lower()
    if wanted in ("", "this", "this window", "current", "current window", "it", "that"):
        return windows[0]
    if wanted in PLACEHOLDERS:
        return None
    best, best_score = None, 0.0
    for window in windows:
        candidates = [window["title"].lower(), window["app"].lower(), window["process"],
                      f'{window["app"]}: {window["title"]}'.lower()]
        score = max(difflib.SequenceMatcher(None, wanted, c).ratio() for c in candidates)
        if any(wanted in c or (c and c in wanted) for c in candidates):
            score = max(score, 0.8)
        if score > best_score:
            best, best_score = window, score
    return best if best_score >= 0.55 else None


def label(window):
    title = window["title"]
    return title if window["app"].lower() in title.lower() else f'{window["app"]} "{title}"'


def _front_root():
    front = user32.GetForegroundWindow()
    return user32.GetAncestor(front, 2) if front else None          # GA_ROOT


def bring_to_front(hwnd):
    """Make hwnd the active window. True only if it really is in front afterwards
    (keys and text are never sent unless this succeeded)."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    for _ in range(3):
        if _front_root() == hwnd:
            return True
        user32.keybd_event(0x12, 0, 0, 0)                           # Alt tap: lets a background app
        user32.keybd_event(0x12, 0, 2, 0)                           # take the foreground
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    return _front_root() == hwnd


def _asking_user(hwnd):
    """The app answered the close with a question ('Save changes?'): a visible window it owns,
    or its main window disabled behind a modal dialog."""
    if not user32.IsWindowEnabled(hwnd):
        return True
    owned = []

    def visit(other, _):
        if user32.IsWindowVisible(other) and user32.GetWindow(other, 4) == hwnd:   # GW_OWNER
            owned.append(other)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return bool(owned)


def close_window(window):
    """Polite close. Returns "closed", "asking" (the app shows e.g. 'Save changes?') or "stuck"."""
    hwnd = window["hwnd"]
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, WM_CLOSE, 0, 0, 0x0002, 2000, ctypes.byref(result))
    deadline = time.monotonic() + CLOSE_WAIT_S
    while time.monotonic() < deadline:
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return "closed"
        if not user32.IsHungAppWindow(hwnd) and _asking_user(hwnd):
            return "asking"
        time.sleep(0.2)
    if not user32.IsHungAppWindow(hwnd) and _asking_user(hwnd):
        return "asking"
    return "stuck"


def force_close(window):
    """Last resort for a frozen window. Explorer folder windows share explorer.exe with the taskbar,
    so Explorer is restarted (taskbar comes back in a few seconds) instead of just killed."""
    if window["process"] == "explorer":
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, creationflags=0x08000000)
        time.sleep(1.0)
        subprocess.Popen(["explorer.exe"], creationflags=0x08000000)
        return "I restarted File Explorer; the taskbar is back in a few seconds."
    subprocess.run(["taskkill", "/f", "/pid", str(window["pid"])], capture_output=True, creationflags=0x08000000)
    return f"I force-closed {window['app']}."


# --------------------------------------------------------------------------- keyboard / mouse
VK = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "windows": 0x5B,
      "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
      "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21,
      "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "insert": 0x2D,
      **{f"f{n}": 0x6F + n for n in range(1, 13)}}


# Own prototype: other modules set different argtypes on the shared windll.user32 function object
_vk_key_scan = ctypes.WINFUNCTYPE(ctypes.c_short, ctypes.c_wchar)(("VkKeyScanW", ctypes.windll.user32))


def _vk(key):
    key = key.strip().lower()
    if key in VK:
        return VK[key]
    if len(key) == 1:
        return _vk_key_scan(key) & 0xFF
    raise ValueError(f"unknown key '{key}'")


def press_keys(combo):
    codes = [_vk(part) for part in re.split(r"\s*\+\s*", combo) if part]
    for code in codes:
        user32.keybd_event(code, 0, 0, 0)
    for code in reversed(codes):
        user32.keybd_event(code, 0, 2, 0)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def type_text(text):
    """Type any text (Unicode) into the focused field."""
    events = []
    for char in text:
        for flags in (0x0004, 0x0004 | 0x0002):                    # KEYEVENTF_UNICODE (+ KEYUP)
            event = _INPUT(type=1)
            event.ki = _KEYBDINPUT(0, ord(char), flags, 0, 0)
            events.append(event)
    array = (_INPUT * len(events))(*events)
    user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))


def click_at(x, y):
    """Left click at physical screen pixel (x, y)."""
    from keyit_buddy import physical_pixels

    with physical_pixels():
        user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def open_thing(target):
    """Open an app name, folder/file path or web address."""
    text = target.strip().strip('"')
    lowered = text.lower()
    if re.match(r"^(https?://|www\.)", lowered) or re.match(r"^[\w-]+\.(com|org|net|io|ai|dev|gov|edu)\b", lowered):
        os.startfile(text if lowered.startswith("http") else "https://" + text)
        return f"Opening {text}."
    expanded = os.path.expandvars(os.path.expanduser(text))
    if os.path.exists(expanded):
        os.startfile(expanded)
        return f"Opening {os.path.basename(expanded.rstrip(os.sep)) or expanded}."
    command = APP_ALIASES.get(lowered, lowered)
    if command.endswith(":"):
        os.startfile(command)
    else:
        subprocess.Popen(["cmd", "/c", "start", "", command], creationflags=0x08000000)
    return f"Opening {text}."


# --------------------------------------------------------------------------- running one action
def run(action, argument, windows, targets=None, confirm=lambda question: False, question=""):
    """Do one action. Returns a short sentence for Buddy to say. `confirm(question)` -> bool asks the user."""
    logging.info("Buddy action: %s %r", action, argument)
    if action in ("close", "minimize", "maximize", "focus"):
        window = resolve_window(argument, question, windows) if question else find_window(argument, windows)
        if window is None:
            return f"I couldn't find a window called {argument}."
        if action == "close":
            outcome = close_window(window)
            if outcome == "closed":
                return f"Closed {label(window)}."
            if outcome == "asking":
                return f"{window['app']} is asking you something first, like saving. Answer it there."
            if confirm(f"{label(window)} isn't responding.\nForce it closed?"
                       + ("\n(Restarts File Explorer; the taskbar blinks)" if window["process"] == "explorer" else "")):
                return force_close(window)
            return "OK, I left it open."
        if action == "minimize":
            user32.ShowWindow(window["hwnd"], SW_MINIMIZE)
            return f"Minimized {label(window)}."
        if action == "maximize":
            user32.ShowWindow(window["hwnd"], SW_MAXIMIZE)
            return f"Maximized {label(window)}."
        if bring_to_front(window["hwnd"]):
            return f"Switched to {label(window)}."
        return f"Windows didn't let me switch to {label(window)}."
    if action == "open":
        if not argument:
            return "Open what?"
        return open_thing(argument)
    if action == "click":
        import keyit_targets

        target = keyit_targets.find_by_label(argument, targets or [])
        if target is None:
            return f"I couldn't find {argument} on the screen."
        left, top, right, bottom = target["rect"]
        click_at((left + right) / 2, (top + bottom) / 2)
        return f"Clicked {target['name'][:40]}."
    if action in ("type", "keys"):
        named = windows_named_in(question, windows) if question else []
        window = named[0] if len({w["app"] for w in named}) == 1 else (windows[0] if windows else None)
        if window is None:
            return "I don't know which window to type in."
        if not bring_to_front(window["hwnd"]):
            return f"I couldn't switch to {label(window)}, so I didn't type anything."
        if action == "type":
            type_text(argument)
            return "Typed it."
        combo = argument.lower().replace(" ", "")
        if combo in RISKY_KEYS and not confirm(f"Press {argument}?\nThis can close things."):
            return "OK, I didn't press it."
        press_keys(argument)
        return f"Pressed {argument}."
    return f"I can't do {action} yet."
