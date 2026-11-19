"""The crew's task watch: no more ghost windows.  (Settings -> Buddy (AI) -> "Also show scheduled tasks ...")

A scheduled task runs hidden, but it can open a window you see: a web page in your browser, a command window.
An AI app (AGENT_HOSTS) works through programs it starts itself, and those open windows too. The crew's other
sources follow the sessions of OpenCode, Claude and Codex, so such a window popped up with nobody pointing at
it. This watch closes that gap, without touching any task. It is off until you switch it on, and everything
it learns stays on this PC:

* it reads the non-Microsoft scheduled tasks (name + action) every 10 minutes;
* it sees every new process (4 looks a second) and tags it with the task it belongs to: by its command line,
  by its parent (wscript -> powershell -> python keeps the tag), or for `opencode run --title X` by its title;
* when a tagged process tree shows a window, a crew pointer named after the task flies to that window, says
  "Working in Gmail" and stays followed until the task ends;
* a window that a tagged process hands to an already running program (the browser opens the URL in your own
  browser) is still attributed: a new window of the same program within a few seconds of the hand-off;
* an AI app's own programs are followed the same way, and in a browser such a program steers
  (--remote-debugging-port) the pointer flies to every click and typed-in box, with a hop and a ripple on the
  click (crew_browser.py);
* a window a followed program works through the screen instead (UI Automation, no debugging port: a script
  that reads Gmail in your own Chrome, or one that presses a button in Notepad or in any other program) is
  followed too: the pointer flies to the thing that is pressed (crew_screen.py). After a click the pointer
  stays there while the page changes, instead of going back to the top of the window with every new title;
* a click a program makes with the mouse itself is followed in every window, also in one that is not the
  task's own (an app you had open already): Windows gives such a press no device, and Buddy's own listener
  for the mouse tells it (keyit_core.MouseClicks; your own mouse and touchpad are never taken for it). The
  click is the task's whose window it is in; in any other window nobody can say which program clicked, so it
  is taken for the followed task that is most likely at work (an AI agent before a scheduled task), and the
  trace says that it was taken so. With no followed task at all only the trace has it;
* typing a program does with the keyboard itself is followed the same way, in every window, also in a command
  window and in a program that tells Windows nothing: Windows marks such a key, and Buddy's own listener for
  the keyboard tells "a program pressed a key" (keyit_core.KeyboardHook; never which key, and never Buddy's
  own keys or those of a helper that answers your own key within a second). The pointer flies to where the
  typing goes (the text cursor, else the thing that has the keyboard, else the window);
* a press without the mouse in an app you had open already (not the task's own window) is followed too, while
  a followed task works through the screen: one of its programs that shows no window has loaded Windows' UI
  Automation (look_for_screen_work; any program of an AI agent, of a scheduled task only a script host). For
  that long the other windows on screen are listened to like the task's own (crew_screen), and what they
  tell is taken for that task, unless your own hand was at the keys or the mouse in the last 5 seconds
  (crew_screen.AT_WORK_S);
* the pointer is for that one window, by its number: never for another window with the same words in its
  title (your own Gmail next to the agent's). A new window you cannot see (opened behind your windows, or
  off the screen) and a press in such a window are said at the right edge of the screen instead, "... (in a
  window you cannot see)": no ghost;
* everything is written to %APPDATA%\\Buddy\\crew\\trace.log, so there is always a trace: which task, which
  program, which window, when it started and ended. The trace is written with the pointers off too.

TaskWatch is one more source of the crew (crew_sources.CrewWatcher asks poll() for its news), with threads
of its own that live as long as the setting is on, also while the pointers are off.

Never late. On a PC with a full CPU one question to Windows for the list of all processes can take 1 to 100 s.
So:

* windows are watched 4 times a second in a loop that never asks for the list of all processes;
* the list is asked for once per look, in its own thread, and each new process is then asked for its own
  command line only;
* a window of a process the list has not shown yet is looked up at once, through its own parents;
* the scheduled tasks are read in a third thread (PowerShell needs 5 to 15 s for that);
* these threads get their turn before ordinary ones (they need almost no CPU), and a look that still came
  late is written in the trace ("slow: ...").
"""

import collections
import ctypes
import datetime
import json
import os
import queue
import re
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

import crew_browser                               # where a steered browser is clicked
import crew_screen                                # where a browser is pressed through the screen
import keyit_core

TRACE = keyit_core.USER_DIR / "crew" / "trace.log"

WINDOWS_EVERY_S = 0.25          # windows are looked at this often
PROCESSES_EVERY_S = 0.25        # the list of all processes is asked for this often
TASKS_EVERY_S = 600             # the task list is read again this often
ALIVE_EVERY_S = 10              # the crew lets a silent agent go after 30 s
HANDOFF_S = 6                   # a window of the same program this soon after a hand-off is the task's
LATE_S = 20                     # a new window nobody owns is asked about again this long (the list can be late)
REPOINT_S = 15                  # a window that comes to the front again is pointed at after this long
SLOW_S = 3                      # a process list that takes longer than this is written in the trace
LATE_POINTER_S = 1              # a look at the windows that takes (or comes) later than this is written in the trace
TRACE_MAX = 1_000_000
TITLE_CHARS = 512               # of a window's title this much is read
OWN_PID = os.getpid()
NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW: the watch itself never flashes a console
FIRST_IN_LINE = 15              # THREAD_PRIORITY_TIME_CRITICAL

# Programs that run a script: the script names the task, never the program
GENERIC = {"python.exe", "pythonw.exe", "py.exe", "wscript.exe", "cscript.exe", "powershell.exe",
           "pwsh.exe", "cmd.exe"}
# A process started by one of these got its task from them, so a match on the task's folder is trusted
LAUNCHERS = {"svchost.exe", "wscript.exe", "cscript.exe", "taskeng.exe"}
CONSOLE_HOSTS = {"conhost.exe", "openconsole.exe", "windowsterminal.exe"}
CONSOLE_APPS = {"cmd.exe", "python.exe", "powershell.exe", "pwsh.exe", "py.exe", "node.exe", "opencode.exe"}
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe"}
# never followed through the screen, whatever their window is; a command window and Firefox are also known by
# the kind of window (crew_screen.NOT_FOLLOWED): PowerShell that shows a real window of its own is followed
NOT_PRESSED = CONSOLE_HOSTS | {"firefox.exe"}
# Apps whose AI works on this PC through programs it starts itself (its Python, PowerShell, the browser it
# opens). What those open gets a pointer too. The app's own windows are never pointed at.
AGENT_HOSTS = {"vector.exe": "Vector"}
AGENT_TEAM = "AI agent"
DRIVEN_BROWSER = "Browser agent"   # a browser a program steers, when nobody is left to say which program
AGENT_QUIET_S = 60                # an agent's app stays open all day: its pointer goes after this long without news
CLICK_LANDS_S = 0.4               # the pointer needs this long to fly to a click: then it hops and ripples there
CLICK_HOLDS_S = 10                # news about a window this soon after a click in it leaves the pointer at the click
SAME_CLICK_S = 0.7                # a press a window tells this soon after a mouse click in it is that click
SAME_CLICK_PX = 4                 # and a second mouse click this soon and this near is the same one (a double click)
SCREEN_DLL = "uiautomationcore.dll"   # a program that presses things in other windows through the screen has loaded this
NO_SCREEN_WORK = CONSOLE_HOSTS | BROWSERS   # these load it for their own windows: never asked
# In a script host, the script asked for it. A compiled program of a scheduled task can have it without ever
# pressing anything (Zoom's updater, run by its task without a window, has): of a scheduled task only these count
SCRIPT_HOSTS = CONSOLE_APPS | {"pythonw.exe", "wscript.exe", "cscript.exe"}
SCREEN_WORK_S = 60                # a task works through the screen for this long after such a program of it was last seen
ASK_FIRST_S, ASK_LAST_S = 0.25, 30   # a program is asked for it again after this long, twice as long every time
MOST_OTHERS = 20                  # so many windows that are no task's own are listened to then (those in front first)
CONSOLE_CLASS = "ConsoleWindowClass"   # Windows says a command window is its program's: the program shows no window all the same
KEYS_EVERY_S = 0.25               # a program's typing is looked at this often at most
NOBODY_KEYS_S = 10                # and with no followed task it is written in the trace this often at most
OWN_HANDS_S = 1.0                 # a program's key this soon after your own key or click answers you (a helper of yours)
FOCUS_MAX_PX = 120                # the thing that has the keyboard is the place of typing when it is at most this high
GA_ROOT = 2
GWL_STYLE = -16
WS_CAPTION = 0x00C00000               # a window with a title bar
NEW_WINDOW_S = 10                 # a window is new for this long: its news is told even where you cannot see it
PATH_RE = re.compile(r"[A-Za-z]:\\[^\"'|<>]*?\.(?:py|pyw|ps1|vbs|bat|cmd|exe)\b|[\w.-]+\.(?:py|pyw|ps1|vbs|bat)\b",
                     re.IGNORECASE)
TITLE_RE = re.compile(r"--title\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))")

user32 = ctypes.WinDLL("user32")                  # their own copies: what is set on them reaches no other module
dwmapi = ctypes.WinDLL("dwmapi")
kernel32 = ctypes.WinDLL("kernel32")
ntdll = ctypes.WinDLL("ntdll")
shell32 = ctypes.WinDLL("shell32")
# One look at the windows asks Windows a few quick questions about every window there is, shown or not
# (several hundred). Asked the normal way, each question gives the GIL back and waits to get it again, up to
# 5 ms each time while another thread of Buddy is busy: a look then took 1 to 9 s and the pointer was late
# (measured: 407 windows, 0.9 s with one busy thread, 1 ms with the GIL kept). So these questions keep the GIL
# (PyDLL). None of them waits for another program. Only safe on 64-bit Windows (one calling convention);
# 32-bit uses the normal handles.
if ctypes.sizeof(ctypes.c_void_p) == 8:
    fast_user32, fast_dwmapi = ctypes.PyDLL("user32"), ctypes.PyDLL("dwmapi")
else:
    fast_user32, fast_dwmapi = user32, dwmapi
EACH_WINDOW = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
fast_user32.EnumWindows.argtypes = [EACH_WINDOW, wintypes.LPARAM]
fast_user32.IsWindowVisible.argtypes = [wintypes.HWND]
fast_user32.IsIconic.argtypes = [wintypes.HWND]
fast_user32.InternalGetWindowText.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
fast_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
fast_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
fast_user32.GetWindowLongW.restype = wintypes.LONG
fast_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
fast_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.GetCurrentThread.restype = wintypes.HANDLE
kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_longlong)] * 4
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
ntdll.NtQueryInformationProcess.restype = wintypes.LONG
ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                            ctypes.POINTER(wintypes.ULONG)]
ntdll.NtQuerySystemInformation.restype = wintypes.LONG
ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                           ctypes.POINTER(wintypes.ULONG)]
shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
psapi = ctypes.WinDLL("psapi")
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


# ----------------------------------------------------------------------------------------- trace and news
_trace_lock = threading.Lock()
_news = collections.deque(maxlen=500)             # crew events nobody has asked for yet (TaskWatch.poll)


def log(line):
    """One line in the trace. Several threads write it."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _trace_lock:
        try:
            TRACE.parent.mkdir(parents=True, exist_ok=True)
            if TRACE.exists() and TRACE.stat().st_size > TRACE_MAX:
                TRACE.replace(TRACE.with_suffix(".log.old"))
            with open(TRACE, "a", encoding="utf-8") as handle:
                handle.write(f"{stamp}  {line}\n")
        except OSError:
            pass                                  # a full disk must not stop the pointers


def post(event):
    """One crew event: the crew's watcher takes it at its next look (4 a second)."""
    _news.append(event)


def first_in_line():
    """The thread that calls this gets its turn before ordinary ones, also on a PC with a full CPU. Only for a
    thread that needs almost no CPU."""
    try:
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), FIRST_IN_LINE)
    except (AttributeError, OSError):
        pass


# ----------------------------------------------------------------------------------------- tasks
def read_tasks(ignored=()):
    """[(task name, [match keys, most specific first])] for every enabled non-Microsoft task whose name is not
    in `ignored`. None when Windows could not be asked."""
    script = (
        "Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' -and $_.State -ne 'Disabled' } | "
        "ForEach-Object { [pscustomobject]@{ Name = ($_.TaskPath + $_.TaskName).TrimStart('\\'); "
        "Actions = @($_.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments) |dir=$($_.WorkingDirectory)\" }) } } | "
        "ConvertTo-Json -Depth 3 -Compress"
    )
    try:
        output = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW).stdout
        records = json.loads(output or "[]")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        log(f"could not read the scheduled tasks: {error}")
        return None
    if isinstance(records, dict):
        records = [records]
    found = []
    for record in records:
        if record.get("Name") in ignored:
            continue
        scripts = []                                 # (script, its folder or "")
        for action in record.get("Actions") or []:
            text, _, folder = str(action).partition(" |dir=")
            for script in PATH_RE.findall(text):
                script = script.strip().lower()
                if Path(script).name in GENERIC:
                    continue
                if not os.path.isabs(script) and folder:
                    script = os.path.join(folder.lower(), script)
                # folder key: a helper a launcher started
                scripts.append((script, os.path.dirname(script) + "\\" if os.path.isabs(script) else ""))
        if scripts:
            found.append((record["Name"], scripts))
    # A windowless launcher several tasks share (run_hidden.vbs my_script.ps1) names none of them: a task keeps
    # the scripts only it has. (A task that has nothing of its own keeps what it shares.)
    held = collections.Counter(script for _, scripts in found for script in {script for script, _ in scripts})
    tasks = []
    for name, scripts in found:
        own = [pair for pair in scripts if held[pair[0]] == 1] or scripts
        keys = {key for pair in own for key in pair if key}
        tasks.append((name, sorted(keys, key=len, reverse=True)))
    return tasks

def match_task(cmdline, tasks, folders=True):
    """The task whose script (or, with folders=True, folder) is in this command line; the longest key wins.
    A folder key only counts for a process a launcher started: otherwise every Python you run yourself in a
    task's folder would be called by that task's name."""
    text = cmdline.lower().replace("/", "\\")
    best, best_length = None, 0
    for name, keys in tasks:
        for key in keys:
            if key.endswith("\\") and not folders:
                continue
            if len(key) > best_length and key in text:
                best, best_length = name, len(key)
    return best


# ----------------------------------------------------------------------------------------- processes
class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


class SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    """One row of Windows' own process list (winternl.h; psutil reads the same rows)."""
    _fields_ = [("NextEntryOffset", wintypes.ULONG), ("NumberOfThreads", wintypes.ULONG),
                ("Reserved1", ctypes.c_byte * 24), ("CreateTime", ctypes.c_longlong),
                ("UserTime", ctypes.c_longlong), ("KernelTime", ctypes.c_longlong),
                ("ImageName", UNICODE_STRING), ("BasePriority", wintypes.LONG),
                ("UniqueProcessId", ctypes.c_void_p), ("InheritedFromUniqueProcessId", ctypes.c_void_p)]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("ExitStatus", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_void_p), ("BasePriority", ctypes.c_void_p),
                ("UniqueProcessId", ctypes.c_void_p), ("InheritedFromUniqueProcessId", ctypes.c_void_p)]


TOO_SMALL = ctypes.c_long(0xC0000004).value          # STATUS_INFO_LENGTH_MISMATCH
_table_buffer = ctypes.create_string_buffer(2 * 1024 * 1024)


def process_table():
    """{pid: (parent pid, exe name, created)} for every process, from ONE question to Windows; None when it
    failed. `created` is Windows' own start time of the process: with the pid it names one process for good
    (Windows gives the pid of an ended process to a new one)."""
    global _table_buffer
    needed = wintypes.ULONG()
    for _ in range(4):
        status = ntdll.NtQuerySystemInformation(5, _table_buffer, len(_table_buffer), ctypes.byref(needed))
        if status != TOO_SMALL:
            break
        _table_buffer = ctypes.create_string_buffer(needed.value + 256 * 1024)
    if status != 0:
        return None
    table = {}
    offset = 0
    while True:
        row = SYSTEM_PROCESS_INFORMATION.from_buffer(_table_buffer, offset)
        name = ctypes.wstring_at(row.ImageName.Buffer, row.ImageName.Length // 2) if row.ImageName.Buffer else ""
        table[row.UniqueProcessId or 0] = (row.InheritedFromUniqueProcessId or 0, name.lower(), row.CreateTime)
        if not row.NextEntryOffset:
            break
        offset += row.NextEntryOffset
    return table or None


def wall_time(created):
    """Windows' start time of a process (100 ns steps since 1601) as time.time() seconds."""
    return (created - 116444736000000000) / 10_000_000


def as_arguments(command):
    """The command line the way psutil gave it: split into arguments, joined with one space (no quotes)."""
    if not command.strip():
        return ""
    count = ctypes.c_int()
    arguments = shell32.CommandLineToArgvW(command, ctypes.byref(count))
    if not arguments:
        return command
    try:
        return " ".join(arguments[index] for index in range(count.value))
    finally:
        kernel32.LocalFree(arguments)


class OneProcess:
    """One process, asked about itself only: these questions never need the list of all processes."""

    def __init__(self, pid):
        self.handle = kernel32.OpenProcess(0x1000, False, pid) if pid > 4 else None    # QUERY_LIMITED_INFORMATION

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.handle:
            kernel32.CloseHandle(self.handle)

    def command(self):
        if not self.handle:
            return ""
        size = wintypes.ULONG(4096)
        for _ in range(3):
            buffer = ctypes.create_string_buffer(size.value)
            status = ntdll.NtQueryInformationProcess(self.handle, 60, buffer, len(buffer), ctypes.byref(size))
            if status != TOO_SMALL:
                break
        if status != 0:
            return ""
        text = UNICODE_STRING.from_buffer(buffer)
        return as_arguments(ctypes.wstring_at(text.Buffer, text.Length // 2)) if text.Buffer else ""

    def path(self):
        if not self.handle:
            return ""
        buffer = ctypes.create_unicode_buffer(2048)
        size = wintypes.DWORD(2048)
        return buffer.value if kernel32.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)) else ""

    def parent(self):
        basic = PROCESS_BASIC_INFORMATION()
        if not self.handle or ntdll.NtQueryInformationProcess(self.handle, 0, ctypes.byref(basic),
                                                              ctypes.sizeof(basic), None) != 0:
            return None
        return basic.InheritedFromUniqueProcessId or 0

    def created(self):
        times = [ctypes.c_longlong() for _ in range(4)]
        if not self.handle or not kernel32.GetProcessTimes(self.handle, *[ctypes.byref(one) for one in times]):
            return None
        return times[0].value


# ----------------------------------------------------------------------------------------- windows
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


fast_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]


def visible_windows():
    """{hwnd: (pid, title)} for top-level windows a person can see, without this program's own.

    Never late: no window is sent a question. Windows itself is asked for the title it keeps
    (InternalGetWindowText). GetWindowText asks a window of the same program through that program's main
    thread, and inside Buddy that waited for as long as Buddy was drawing: up to 9 s measured.
    And every question here keeps the GIL (fast_user32), so a busy thread of Buddy cannot slow the look.
    """
    found = {}
    buffer = ctypes.create_unicode_buffer(TITLE_CHARS)

    @EACH_WINDOW
    def visit(hwnd, _):
        if not fast_user32.IsWindowVisible(hwnd) or fast_user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        fast_user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == OWN_PID:
            return True                                  # Buddy's own windows are nobody's task
        if not fast_user32.InternalGetWindowText(hwnd, buffer, TITLE_CHARS):
            return True
        cloaked = ctypes.c_int(0)
        fast_dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        rect = RECT()
        fast_user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < 60 or rect.bottom - rect.top < 40:
            return True
        found[int(hwnd)] = (pid.value, buffer.value)
        return True

    fast_user32.EnumWindows(visit, 0)
    return found


def programs_with_a_window():
    """The processes that show a window of their own, or keep one hidden: a window you can see, or one with a
    title bar (an app that waits minimized or hidden has that). A command window does not count: Windows says
    it is its program's, the program made no window all the same. A script has none of these. Never late: no
    window is sent a question, and every question keeps the GIL (about 1 ms for 400 windows)."""
    found = set()
    name = ctypes.create_unicode_buffer(64)

    @EACH_WINDOW
    def visit(hwnd, _):
        if not (fast_user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION == WS_CAPTION
                or fast_user32.IsWindowVisible(hwnd)):
            return True
        if fast_user32.GetClassNameW(hwnd, name, 64) and name.value == CONSOLE_CLASS:
            return True
        pid = wintypes.DWORD()
        fast_user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.add(pid.value)
        return True

    fast_user32.EnumWindows(visit, 0)
    return found


def what_it_does(exe, title, window_class=""):
    """A short bubble line. Only the program is named, never the window title (it can hold a mail subject).
    `window_class`: the kind of window when it is known. PowerShell or Python can show a real window of
    their own, and that is not a command window."""
    lowered = title.lower()
    if "gmail" in lowered or "mail.google" in lowered:
        return "Working in Gmail"
    if exe in CONSOLE_HOSTS or (exe in CONSOLE_APPS and (not window_class or window_class in crew_screen.NOT_FOLLOWED)):
        return "Opened a command window"
    if exe in CONSOLE_APPS:
        return "Opened a window"
    if exe in BROWSERS:
        return "Working in the browser"
    return f"Opened {exe.rsplit('.', 1)[0]}"


def agent_of(exe, command, parent_exe):
    """The AI agent a new process works for, or None. A process an agent app started is the agent's (not the
    app's own helpers, and not a browser straight from the app: that is you clicking a link in it). A
    browser started with remote debugging is steered by a program, whoever started it."""
    host = AGENT_HOSTS.get(parent_exe)
    if host and exe != parent_exe and exe not in BROWSERS:
        return f"{AGENT_TEAM}\\{host}"
    if exe in BROWSERS and "--remote-debugging-p" in command and "--type=" not in command:
        return f"{AGENT_TEAM}\\{DRIVEN_BROWSER}"
    return None


def watch_clicks(watch, pid, created, command):
    """A thread of its own asks this browser where it is clicked, for as long as it runs."""
    def alive():
        return not watch.stopped.is_set() and watch.tags.get(pid, (None, None))[1] == created

    def follow():
        first_in_line()                              # it sleeps until the browser says something
        crew_browser.watch(pid, command, watch.browser_action, alive, log)

    threading.Thread(target=follow, name="BuddyTaskBrowser", daemon=True).start()


def window_at(x, y):
    """(the top-level window you see at this point of the screen, its process), or (0, 0)."""
    hwnd = int(user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(x, y)), GA_ROOT) or 0)
    pid = wintypes.DWORD()
    if hwnd:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, pid.value


def mouse_words(button):
    return "the mouse" if button == "left" else f"the mouse ({button} button)"


def has_module(pid, wanted=SCREEN_DLL):
    """Has this process loaded that DLL? None when it cannot be asked (it ended, or it is not yours to ask).
    A few ms: only the names of what the process has loaded are read."""
    handle = kernel32.OpenProcess(0x0410, False, pid)        # QUERY_INFORMATION | VM_READ
    if not handle:
        return None
    try:
        modules = (wintypes.HMODULE * 2048)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):   # 32 and 64 bit
            return None
        name = ctypes.create_unicode_buffer(260)
        for index in range(min(needed.value // ctypes.sizeof(wintypes.HMODULE), len(modules))):
            if psapi.GetModuleBaseNameW(handle, modules[index], name, 260) and name.value.lower() == wanted:
                return True
        return False
    finally:
        kernel32.CloseHandle(handle)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]


def keys_go_to():
    """(the top-level window that has the keyboard, its process, where typing goes in it) or (0, 0, None).
    The place is the text cursor when the program shows one to Windows, else the thing that has the keyboard
    when that is a small one (a box of one line, not a whole page), else None: only the window is known."""
    front = user32.GetForegroundWindow()
    if not front:
        return 0, 0, None
    pid = wintypes.DWORD()
    thread = user32.GetWindowThreadProcessId(front, ctypes.byref(pid))
    hwnd = int(user32.GetAncestor(front, GA_ROOT) or front)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
        return hwnd, pid.value, None
    caret = info.rcCaret
    if info.hwndCaret and caret.bottom > caret.top:
        point = wintypes.POINT(caret.left, (caret.top + caret.bottom) // 2)
        if user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
            return hwnd, pid.value, (point.x, point.y)
    box = wintypes.RECT()
    if info.hwndFocus and int(info.hwndFocus) != hwnd and user32.GetWindowRect(info.hwndFocus, ctypes.byref(box)) \
            and 2 <= box.bottom - box.top <= FOCUS_MAX_PX and box.right - box.left >= 2:
        return hwnd, pid.value, (box.left + min((box.right - box.left) // 2, 80), (box.top + box.bottom) // 2)
    return hwnd, pid.value, None


def is_agent(task):
    return task.startswith(AGENT_TEAM + "\\")


def short_agent(task):
    """(name, team) of the task's pointer. A pointer shows at most 24 characters: the last part of the task's
    path ("Backups\\Nightly copy" -> "Nightly copy")."""
    team, _, name = task.rpartition("\\")
    return name[:24], (team or "Scheduled task")[:24]


# ----------------------------------------------------------------------------------------- the watcher
class TaskWatch:
    """The watch. start() once, stop() for good; poll() gives the crew its news; set_pointers(False) keeps the
    trace going and shows nothing."""

    def __init__(self, on=True, ignored=(), own_hands=None):
        self.lock = threading.RLock()      # the process thread and the window loop share everything below
        self.tasks = []
        self.tasks_ready = threading.Event()
        self.processes_ready = threading.Event()
        self.known = {}           # pid -> (parent, exe, created): every process of the last list, all looked at
        self.tags = {}            # pid -> (task, created, tagged in look number)
        self.looks = 0            # looks at the list of processes begun so far (a count, not a clock:
                                  # two looks can fall in one tick of the clock)
        self.followed = {}        # task -> {"started", "announced", "alive", "pids"}
        self.handoffs = []        # (exe, task, started, exe path, noted at) of tagged browser / console processes
        self.windows = {}         # hwnd -> (pid, title, task or None, last pointed, first seen)
        self.window_exes = {}     # pid -> exe name of a window's process the list has not shown yet
        self.classes = {}         # hwnd -> (pid, the kind of window: its class), asked once per window
        self.first_windows = True
        self.on = bool(on)        # the pointers' switch: off = nothing is given to the crew (the trace goes on)
        self.applied = self.on    # what the watch has acted on; other threads only set self.on
        self.ignored = {str(name) for name in ignored or ()}    # tasks that are never followed
        self.stopped = threading.Event()
        self.threads = []
        self.asked_for_clicks = set()   # (pid, created) of the steered browsers that are asked for their clicks
        # seconds since your own hand last used a key or a mouse button (keyit_core.KeyboardHook.own_hands);
        # not known: never "just now"
        self.own_hands = own_hands or (lambda: float("inf"))
        # presses in a window worked through the screen
        self.screen = crew_screen.ScreenWatch(self.screen_action, log, own_hands=lambda: self.own_hands())
        # (button, x, y) of the clicks a program made with the mouse, and ("keys", 0, 0) for its typing
        self.mouse_clicks = queue.SimpleQueue()
        self.screen_asked = {}    # (pid, created) -> (asked again from, the wait after that, it has UI Automation loaded)
        self.keys_noted = float("-inf")     # when a program's typing was last handed on
        self.nobody_typed = float("-inf")   # when typing with no followed task was last written in the trace

    def poll(self):
        """The crew events since the last call (what crew_sources.CrewWatcher asks every source for)."""
        events = []
        while _news:
            events.append(_news.popleft())
        return events

    def set_pointers(self, on):
        """Called from Buddy's own thread: only the answer is set here, the watch acts on it (apply_switch)."""
        on = bool(on)
        if on == self.on:
            return
        if on:
            _news.clear()                            # nothing from before the switch is shown now
        self.on = on
        log(f"POINTERS {'ON' if on else 'OFF'}")

    def start(self):
        if self.threads:
            return
        log(f"task watch on (inside Buddy, pid {os.getpid()}), pointers {'on' if self.on else 'off'}")
        for name, work in (("BuddyTaskList", self.watch_tasks), ("BuddyTaskWindows", self.run),
                           ("BuddyTaskMouse", self.watch_mouse)):
            self.threads.append(threading.Thread(target=work, name=name, daemon=True))
            self.threads[-1].start()

    def stop(self):
        """For good (Buddy quits, or the setting is switched off). The pointers it showed are let go: ask
        poll() once more for that news."""
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.screen.stop()
        self.mouse_clicks.put(None)                  # wakes the thread that waits for clicks
        with self.lock:
            for task, follow in self.followed.items():
                if follow["announced"]:
                    agent, team = short_agent(task)
                    post({"agent": agent, "team": team, "id": f"task:{task}", "state": "leave"})
                    follow["announced"] = False
        log("task watch off")

    def wait_for(self, ready, seconds):
        """Wait until `ready` is set, at most `seconds`. False when it was not, or the watch was stopped."""
        until = time.monotonic() + seconds
        while not self.stopped.is_set() and time.monotonic() < until:
            if ready.wait(0.5):
                return True
        return ready.is_set() and not self.stopped.is_set()

    def apply_switch(self):
        on = self.on
        if on == self.applied:
            return
        self.applied = on
        with self.lock:
            if not on:
                for task, follow in self.followed.items():
                    if follow["announced"]:
                        agent, team = short_agent(task)
                        post({"agent": agent, "team": team, "id": f"task:{task}", "state": "leave"})
                        follow["announced"] = False
            else:
                for hwnd, (pid, title, task, _, first) in list(self.windows.items()):
                    self.windows[hwnd] = (pid, title, task, 0.0, first)       # point at what is on screen now

    # --- tasks (their own thread: PowerShell needs 5 to 15 s to list them)
    def watch_tasks(self):
        while not self.stopped.is_set():
            tasks = read_tasks(self.ignored)
            if tasks is not None:
                with self.lock:
                    self.tasks = tasks
                self.tasks_ready.set()
            self.stopped.wait(TASKS_EVERY_S if tasks is not None else 30)

    # --- processes
    def adopt(self, pid, row, command, path, launcher, startup=False, parent_exe="", left=None):
        """Under the lock: which task does this new process belong to? If one, it is followed from now on.
        `left` is the tag of a parent that ended in this same look (PowerShell opens a browser and is gone)."""
        parent, exe, created = row
        if pid == os.getpid():
            return None                                      # Buddy itself, whoever started it
        inherited = self.tags.get(parent)
        if not (inherited and inherited[1] <= created):
            inherited = left
        if inherited and inherited[1] <= created:            # older than this process: really its parent
            task = inherited[0]
        else:
            task = match_task(command, self.tasks, folders=launcher) or agent_of(exe, command, parent_exe)
            if not task and "opencode" in command.lower() and " run " in f" {command} ":
                title = TITLE_RE.search(command)
                if title:
                    task = "OpenCode\\" + next(group for group in title.groups() if group)
        if not task:
            return None
        self.tags[pid] = (task, created, self.looks)
        follow = self.followed.get(task)
        if follow is None:
            follow = self.followed[task] = {"started": time.time(), "announced": False, "alive": 0.0, "pids": set(),
                                            "moved": 0.0}
            if not startup:
                log(f"START  {task}  ({exe} pid {pid})")
        follow["pids"].add(pid)
        # an agent runs commands all day: only its browser may claim a window by timing, never its consoles
        if exe in BROWSERS and "--remote-debugging-port=" in command and "--type=" not in command \
                and (pid, created) not in self.asked_for_clicks:
            self.asked_for_clicks = {key for key in self.asked_for_clicks if key[0] in self.tags}
            self.asked_for_clicks.add((pid, created))
            watch_clicks(self, pid, created, command)
        if exe in BROWSERS or (exe in CONSOLE_APPS and not is_agent(task)):
            self.handoffs.append((exe, task, wall_time(created), (path or exe).lower(), time.monotonic()))
        return task

    def look_at_processes(self, startup=False):
        """One look of the process thread. The slow questions (the list, the command lines) are asked without
        the lock, so the window loop never waits for them."""
        self.looks += 1
        this_look = self.looks         # a tag made from now on is not judged by this list
        table = process_table()
        if table is None:
            return
        with self.lock:
            new = {pid for pid, row in table.items() if self.known.get(pid) != row
                   and not (pid in self.tags and self.tags[pid][1] == row[2])}
        asked = {}
        for pid in new:
            exe = table[pid][1]
            with OneProcess(pid) as process:
                asked[pid] = (process.command(), process.path() if exe in BROWSERS or exe in CONSOLE_APPS else "")
        ordered = []

        def parents_first(pid, depth=0):
            if pid not in new:
                return
            new.discard(pid)
            if depth < 64:
                parents_first(table[pid][0], depth + 1)
            ordered.append(pid)

        for pid in sorted(new):
            parents_first(pid)
        with self.lock:
            ended = {}
            for pid in list(self.tags):
                task, created, tagged = self.tags[pid]
                row = table.get(pid)
                if tagged < this_look and (row is None or row[2] != created):     # ended (or its pid has a new owner)
                    ended[pid] = self.tags.pop(pid)
                    follow = self.followed.get(task)
                    if follow:
                        follow["pids"].discard(pid)
            for pid in ordered:
                row = table[pid]
                if pid in self.tags and self.tags[pid][1] == row[2]:           # a window had it looked up already
                    continue
                parent = table.get(row[0])
                gone = parent is None or parent[2] > row[2]                    # the parent ended: a launcher that left
                self.adopt(pid, row, *asked[pid], launcher=gone or parent[1] in LAUNCHERS, startup=startup,
                           parent_exe="" if gone else parent[1], left=ended.get(row[0]) if gone else None)
            self.known = table
            cutoff = time.monotonic() - HANDOFF_S - LATE_S
            self.handoffs = [handoff for handoff in self.handoffs if handoff[4] >= cutoff]
            for task, follow in list(self.followed.items()):
                if not follow["pids"]:
                    seconds = int(time.time() - follow["started"])
                    log(f"END    {task}  after {seconds} s" + ("  (had a window and a pointer)" if follow["announced"] else ""))
                    if follow["announced"] and self.on:
                        agent, team = short_agent(task)
                        post({"agent": agent, "team": team, "id": f"task:{task}", "state": "done"})
                    del self.followed[task]
        self.processes_ready.set()

    def watch_processes(self):
        first_in_line()
        startup = True
        while not self.stopped.is_set():
            began = time.monotonic()
            try:
                self.look_at_processes(startup=startup)
                startup = startup and not self.processes_ready.is_set()
                self.look_for_screen_work()
            except Exception as error:                      # a watcher must not die on one odd process
                log(f"error (processes): {type(error).__name__}: {error}")
                self.stopped.wait(5)
            if time.monotonic() - began > SLOW_S:
                log(f"slow: Windows took {time.monotonic() - began:.0f} s for the process list"
                    "  (the windows were watched all the time)")
            self.stopped.wait(PROCESSES_EVERY_S)

    def look_for_screen_work(self, now=None):
        """In the process thread, after every look: does a followed task work through the screen? A program
        that presses things in other programs' windows has loaded Windows' UI Automation. A program that shows
        a window loads it too as soon as anybody looks at that window (Excel, started hidden by a task, has
        it), so only a task's programs without a window of their own count (programs_with_a_window), and of a
        scheduled task only script hosts (SCRIPT_HOSTS): any program of an AI agent counts. Each is
        asked at once and then again after 0.25 s, 0.5 s ... 30 s (a script loads it a moment after it
        starts); one that has it is never asked again."""
        now = time.monotonic() if now is None else now
        with self.lock:
            asked = {(pid, tag[1]): tag[0] for pid, tag in self.tags.items()
                     if self.known.get(pid, (0, "", 0))[1] not in NO_SCREEN_WORK
                     and (is_agent(tag[0]) or self.known.get(pid, (0, "", 0))[1] in SCRIPT_HOSTS)}
        self.screen_asked = {key: answer for key, answer in self.screen_asked.items() if key in asked}
        loaded = {}
        for key, task in asked.items():
            due, wait, found = self.screen_asked.get(key, (now, ASK_FIRST_S / 2, False))
            if not found and now >= due:
                found = has_module(key[0]) is True
                wait = min(wait * 2, ASK_LAST_S)
                self.screen_asked[key] = (now + wait, wait, found)
            if found:
                loaded[key[0]] = task
        if not loaded:
            return
        with_window = programs_with_a_window()
        working = {}
        for pid, task in loaded.items():
            if pid not in with_window:
                working.setdefault(task, pid)
        with self.lock:
            for task, pid in working.items():
                follow = self.followed.get(task)
                if follow is None:
                    continue
                if now - follow.get("screen", float("-inf")) >= SCREEN_WORK_S:
                    log(f"SCREEN {task}  works through the screen ({self.known.get(pid, (0, '?', 0))[1]} pid {pid} has"
                        " UI Automation loaded): presses in the other windows on screen are followed too")
                follow["screen"] = now

    def likely(self, screen_work=False):
        """Under the lock: the followed task that is most likely at work (an AI agent before a scheduled task,
        then the one with the latest news), or None. `screen_work`: only of those that work through the screen."""
        now = time.monotonic()
        return max((name for name, follow in self.followed.items()
                    if not screen_work or now - follow.get("screen", float("-inf")) < SCREEN_WORK_S),
                   default=None, key=lambda name: (is_agent(name), self.followed[name]["moved"],
                                                   self.followed[name]["started"]))

    def look_up_now(self, pid):
        """A window of a process the list has not shown yet (the list can be seconds late on a full PC): the
        process and its parents are asked directly, so the pointer does not wait for the list."""
        chain = []
        while pid and len(chain) < 8:
            with self.lock:
                if pid in self.known or pid in self.tags:
                    break
            with OneProcess(pid) as process:
                parent, created, path = process.parent(), process.created(), process.path()
                if parent is None or created is None or not path:
                    break
                chain.append((pid, (parent, os.path.basename(path).lower(), created), process.command(), path))
            pid = parent
        with self.lock:
            for pid, row, command, path in reversed(chain):
                if pid in self.known or pid in self.tags:
                    continue
                parent = self.known.get(row[0]) or next((other[1] for other in chain if other[0] == row[0]), None)
                # a parent that cannot be seen is not taken for a launcher here: the list decides that later
                launcher = parent is not None and parent[2] <= row[2] and parent[1] in LAUNCHERS
                seen = parent is not None and parent[2] <= row[2]
                self.adopt(pid, row, command, path, launcher=launcher, parent_exe=parent[1] if seen else "")

    # --- windows
    def owner_task(self, pid, exe, title, first, look):
        """The task this window belongs to, or None. `first` is when the window was first seen (0 = it was
        there before the watch started: never claimed by a hand-off)."""
        if look:
            self.look_up_now(pid)
        with self.lock:
            if pid in self.tags:
                return self.tags[pid][0]
            if not first or (exe not in BROWSERS and exe not in CONSOLE_HOSTS):
                return None
            lowered = title.lower()
            for handoff_exe, task, started, exe_path, _ in reversed(self.handoffs):
                if not started - 1 <= first <= started + HANDOFF_S:
                    continue
                if exe in BROWSERS and handoff_exe == exe:
                    return task                              # the URL was handed to your running browser
                if exe in CONSOLE_HOSTS and handoff_exe in CONSOLE_APPS and exe_path in lowered:
                    # Windows Terminal: the tab is titled with the program's path. OpenCode labs start hidden
                    # consoles all the time, so a console is only claimed by its title, never by timing alone
                    return task
        return None

    # --- clicks (from the thread of a steered browser, or from crew_screen's listeners)
    def browser_action(self, pid, action, what, x, y, window=""):
        """The agent clicked (or typed) at this screen point in its browser: the pointer flies there, and for a
        click it hops and ripples when it has arrived. Only the kind of thing is told, never its text. No
        point (x is None) = the spot is behind another window or off the screen: the trace has it, and the
        pointer is shown on whatever of that browser window can be seen."""
        with self.lock:
            tag = self.tags.get(pid)
        if tag:
            self.act(tag[0], pid, 0, action, what, x, y, window)

    def screen_action(self, hwnd, pid, action, what, x, y, gone=False):
        """The same for a window that is worked through the screen. Such a window can be one of your own
        browser (a task opened a page in it), so it is the window that names the task, not the process.
        `gone`: the pressed thing was gone before its place could be read (there is no point then).
        A window that is no followed task's own (an app you had open) is only listened to while a task works
        through the screen: what it tells is taken for that task, and the trace says so."""
        entry = self.windows.get(hwnd)
        if not (entry and entry[0] == pid):
            return
        now = time.monotonic()
        with self.lock:
            task = entry[2] if entry[2] in self.followed else None
            taken = task is None
            if taken:
                task = self.likely(screen_work=True)
            if task is None:
                return
            if action == "click" and any(
                    clicked and clicked[0] == hwnd and now - clicked[1] < SAME_CLICK_S
                    for clicked in (follow.get("mouse") for follow in self.followed.values())):
                return                               # the window tells the click a program's mouse just made: told already
        self.act(task, pid, hwnd, action, what, x, y, entry[1], gone, taken=taken, exe=self.exe_of(pid) if taken else "")

    # --- clicks a program makes with the mouse itself (from keyit_core.MouseClicks, through a thread of its own)
    def program_clicked(self, button, x, y):
        """Called in the thread that hears the mouse, for every press a program made: only noted here, so the
        click sounds never wait."""
        self.mouse_clicks.put((button, x, y))

    def program_typed(self):
        """Called in the thread that hears the keyboard, for every key a program pressed: only noted here (and
        only a few times a second), so no key ever waits."""
        now = time.monotonic()
        if now - self.keys_noted >= KEYS_EVERY_S:
            self.keys_noted = now
            self.mouse_clicks.put(("keys", 0, 0))

    def watch_mouse(self):
        first_in_line()                              # it sleeps until a program clicks or types
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))      # real pixels on a scaled screen
        except (AttributeError, OSError):
            pass
        while not self.stopped.is_set():
            click = self.mouse_clicks.get()
            if click is None or self.stopped.is_set():
                continue
            try:
                if click[0] == "keys":
                    self.key_action()
                else:
                    self.mouse_action(*click)
            except Exception as error:               # a watch must not die on one odd window
                log(f"error (a program's click or typing): {type(error).__name__}: {error}")

    def key_action(self, target=None):
        """A program pressed keys. They go to the window that has the keyboard: in a followed task's window it
        is that task's typing; in any other window it is taken for the followed task most likely at work, as
        a click is (mouse_action). Keys that come within a second of your own key or click answer you (a
        helper of yours that changes or completes what you type) and are nobody's work."""
        hwnd, pid, place = target or keys_go_to()
        if not hwnd or pid == OWN_PID or self.own_hands() < OWN_HANDS_S:
            return
        entry = self.windows.get(hwnd)
        entry = entry if entry and entry[0] == pid else None
        exe = self.exe_of(pid)
        with self.lock:
            task = entry[2] if entry and entry[2] in self.followed else None
            taken = task is None
            if taken:
                task = self.likely()
        if task is None:
            now = time.monotonic()
            if now - self.nobody_typed >= NOBODY_KEYS_S:
                self.nobody_typed = now
                log(f"TYPE   a program nobody follows  -> {exe} pid {pid}: the keyboard")
            return
        if place and not crew_browser.on_top(hwnd, place):
            place = None
        x, y = place or (None, None)
        self.act(task, pid, hwnd, "type", "", x, y, entry[1] if entry else "", taken=taken, exe=exe, keys=True)

    def mouse_action(self, button, x, y):
        """A program pressed a mouse button at this point of the screen. In a followed task's window it is that
        task's click. In any other window nobody can say whose it is: it is taken for the followed task most
        likely at work (an AI agent before a scheduled task, then the one with the latest news), and the
        trace says so. With no followed task, only the trace has it."""
        hwnd, pid = window_at(x, y)
        if not hwnd or pid == OWN_PID:
            return                                   # on Buddy itself (or on nothing): nobody's work
        entry = self.windows.get(hwnd)
        entry = entry if entry and entry[0] == pid else None
        exe = self.exe_of(pid)
        with self.lock:
            task = entry[2] if entry and entry[2] in self.followed else None
            taken = task is None
            if taken:
                task = self.likely()
            if task is None:
                log(f"CLICK  a program nobody follows  -> {exe} pid {pid}: {mouse_words(button)} at ({x}, {y})")
                return
            clicked = self.followed[task].get("mouse")
            if (clicked and clicked[0] == hwnd and time.monotonic() - clicked[1] < SAME_CLICK_S
                    and abs(clicked[3] - x) <= SAME_CLICK_PX and abs(clicked[4] - y) <= SAME_CLICK_PX):
                return                               # the second press of a double click
        self.act(task, pid, hwnd, "click", "", x, y, entry[1] if entry else "", mouse=button, taken=taken, exe=exe)

    def act(self, task, pid, hwnd, action, what, x, y, window, gone=False, mouse="", taken=False, exe="",
            keys=False):
        """`mouse`: the button, for a click a program made with the mouse itself. `keys`: typing a program
        did with the keyboard itself. `taken`: the work is in a window that is not the task's own."""
        with self.lock:
            follow = self.followed.get(task)
            if follow is None:
                return
            now = time.monotonic()
            if action == "type":
                # One typing is told once: the keyboard tells it (a program's keys), and the window can tell
                # it too (its box has another value). Boxes a program fills in one after the other without
                # the keyboard are each told.
                typed = follow.get("typed")
                if typed and typed[0] == hwnd and now - typed[1] < crew_screen.TYPING_EVERY_S and (keys or typed[2]):
                    return
                follow["typed"] = (hwnd, now, keys)
            follow["moved"] = now
            # where the agent last clicked: the next news about that window leaves the pointer there
            follow["spot"] = (pid, hwnd, x, y, follow["moved"]) if x is not None else None
            follow["pressed"] = (pid, hwnd, follow["moved"])     # and when, also for a press with no place
            if taken:
                follow["away"] = (hwnd, now)                      # it works in a window that is not its own
            exe = exe or self.known.get(pid, (0, "browser", 0))[1]
            if mouse:
                follow["mouse"] = (hwnd, follow["moved"], taken, x, y)
                thing, told = "here", mouse_words(mouse)
            elif keys:
                thing, told = "here", "the keyboard"
            elif what:
                thing = told = f"a {what}"
            else:
                thing = told = "the page" if exe in BROWSERS or exe == "browser" else "something"
            clicking, clicked = ("Right-clicking", "Right-clicked") if mouse == "right" else ("Clicking", "Clicked")
            if x is not None:
                where = f" at ({x}, {y})"
            elif keys:
                where = ": the program does not tell where in its window"
            else:
                where = ": the thing was gone before its place could be read" if gone else " behind another window or off the screen"
            log(f"{'TYPE  ' if action == 'type' else 'CLICK '} {task}  -> {exe} pid {pid}: {told}{where}"
                + (f"  (not its own window: taken for its {'typing' if action == 'type' else 'click'})" if taken else "")
                + ("" if self.on else "  (pointers off)"))
            if not self.on:
                return
            agent, team = short_agent(task)
            # "dock": a press in a window you cannot see is said at the edge of the screen (no ghost).
            # "press": on the window instead of on the thing (the thing is covered), the words say so. Typing
            # whose place the program does not tell is on the window with nothing covered
            event = {"agent": agent, "team": team, "id": f"task:{task}", "state": "working", "dock": True,
                     "press": not (keys and x is None)}
            if x is None:
                event["window"] = window[:80]
            else:
                event.update({"x": x, "y": y})
                if hwnd:
                    # a spot in that window: when the window goes behind another one (a script parks
                    # its browser there after a press), the pointer goes to what is left to see of it
                    event["window"] = window[:80]
            if hwnd:
                event["hwnd"] = hwnd         # this window and no other, whatever else has the same title
            if action != "type":
                say = f"{clicking} {thing}"
            elif keys:
                say = "Typing here" if x is not None else "Typing in this window"
            else:
                say = f"Typing in {thing}"
            post({**event, "say": say})
            follow["announced"] = True
        if action != "type":
            landed = threading.Timer(CLICK_LANDS_S, self.click_lands, args=(task, {**event, "say": f"{clicked} {thing}"}))
            landed.daemon = True
            landed.start()

    def click_lands(self, task, event):
        with self.lock:
            follow = self.followed.get(task)
            if self.on and follow and follow["announced"]:
                post(event)                                  # new words at the same spot: the pointer hops

    def exe_of(self, pid):
        row = self.known.get(pid)
        if row:
            return row[1]
        exe = self.window_exes.get(pid)
        if exe is None:                                      # asked once per process, not on every new title
            with OneProcess(pid) as process:
                exe = os.path.basename(process.path()).lower() or "?"
            self.window_exes[pid] = exe
        return exe

    def window_class(self, hwnd, pid):
        known = self.classes.get(hwnd)
        if known is None or known[0] != pid:
            known = self.classes[hwnd] = (pid, crew_screen.class_of(hwnd))
        return known[1]

    def scan_windows(self):
        now = time.monotonic()
        wall = time.time()
        seen = visible_windows()
        front = int(user32.GetForegroundWindow() or 0)
        for hwnd, (pid, title) in seen.items():
            before = self.windows.get(hwnd)
            if before and before[0] != pid:
                before = None                                # the window number has a new owner: a new window
            task = before[2] if before else None
            first = before[4] if before else (0.0 if self.first_windows else wall)
            # nobody owns it yet and it is new: the process list or the hand-off may only come now
            waiting = task is None and first and wall - first < HANDOFF_S + LATE_S
            again = before and task and (hwnd == front or not before[3])          # in front again, or just switched on
            if before and before[1] == title and not again and not waiting:
                continue
            exe = self.exe_of(pid)
            if task is None and (before is None or waiting):
                task = self.owner_task(pid, exe, title, first, look=before is None and not self.first_windows)
            last = before[3] if before else 0.0
            self.windows[hwnd] = (pid, title, task, last, first)
            with self.lock:
                follow = self.followed.get(task) if task else None
                if follow is None:
                    continue
                changed = before is None or before[1] != title or before[2] != task
                if changed:
                    follow["moved"] = now
                elif (last and now - last < REPOINT_S) or (is_agent(task) and now - follow["moved"] > AGENT_QUIET_S):
                    continue
                self.windows[hwnd] = (pid, title, task, now, first)
                agent, team = short_agent(task)
                say = what_it_does(exe, title, self.window_class(hwnd, pid))
                if changed:
                    log(f"WINDOW {task}  -> {exe} pid {pid}: \"{title[:90]}\"  [{say}]" + ("" if self.on else "  (pointers off)"))
                if self.on:
                    # "hwnd": this window and no other (your own Gmail has the same words in its title).
                    # "dock": the news of a new window you cannot see (opened behind your windows or off
                    # the screen) is said at the edge of the screen, and so is what a press there brings.
                    # The titles such a window gets later by itself are not
                    event = {"agent": agent, "team": team, "id": f"task:{task}", "say": say,
                             "window": title[:80], "hwnd": hwnd, "state": "working"}
                    pressed = follow.get("pressed")
                    if not last or (first and wall - first < NEW_WINDOW_S) or (
                            pressed and pressed[0] == pid and pressed[1] in (0, hwnd)
                            and now - pressed[2] < CLICK_HOLDS_S):
                        event["dock"] = True
                    spot = follow.get("spot")
                    if spot and spot[0] == pid and spot[1] in (0, hwnd) and now - spot[4] < CLICK_HOLDS_S:
                        # the agent just clicked in this window and the page changed: the pointer stays at
                        # the click (for as long as the window shows that spot: buddy_crew.Desktop.place)
                        event.update({"x": spot[2], "y": spot[3], "hwnd": hwnd})
                    post(event)
                    follow["announced"] = True
        for hwnd in list(self.windows):
            if hwnd not in seen:
                del self.windows[hwnd]
        with_window = {pid for pid, _ in seen.values()}
        self.window_exes = {pid: exe for pid, exe in self.window_exes.items() if pid in with_window}
        self.classes = {hwnd: known for hwnd, known in self.classes.items() if hwnd in seen}
        self.first_windows = False
        wanted, others = self.screen_windows(list(seen))         # Windows lists them from the front to the back
        self.screen.follow(wanted, front, others)

    def screen_windows(self, order=()):
        """({window: its process}, which of them are no task's own) of the windows to listen to, for
        crew_screen: those a followed task works in, whatever the program, and while a task works through the
        screen (look_for_screen_work) also the other windows on screen (`order`: all windows, front first),
        the apps you had open. Without the browsers that tell their clicks themselves, over their debugging
        port (crew_browser), without what has nothing to press that it tells (a command window) or was never
        measured (Firefox), both known by the kind of window, not by the program: PowerShell that shows a
        real window is followed. And without an AI app's own windows (those are never pointed at)."""
        with self.lock:
            if not self.followed:
                return {}, ()
            by_port = {pid for pid, _ in self.asked_for_clicks}
            def pressed(hwnd, pid):
                return (self.exe_of(pid) not in NOT_PRESSED
                        and self.window_class(hwnd, pid) not in crew_screen.NOT_FOLLOWED)

            wanted = {hwnd: entry[0] for hwnd, entry in self.windows.items()
                      if entry[2] in self.followed and entry[0] not in by_port and pressed(hwnd, entry[0])}
            if self.likely(screen_work=True) is None:
                return wanted, ()
            others = []
            for hwnd in order:
                entry = self.windows.get(hwnd)
                if len(others) == MOST_OTHERS:
                    break
                if entry is None or entry[2] in self.followed or entry[0] in by_port:
                    continue
                if self.exe_of(entry[0]) not in AGENT_HOSTS and pressed(hwnd, entry[0]):
                    others.append(hwnd)
                    wanted[hwnd] = entry[0]
            return wanted, tuple(others)

    def keep_alive(self):
        now = time.monotonic()
        if not self.on:
            return
        on_screen = {entry[2] for entry in self.windows.values()}
        with self.lock:
            for task, follow in self.followed.items():
                away = follow.get("away")
                if away and (away[0] in self.windows or now - away[1] < CLICK_HOLDS_S):
                    # it clicked, pressed or typed in a window that is not its own: it works there (a window
                    # without a title, the taskbar, is in no list: the pointer stays for a moment all the same)
                    on_screen.add(task)
                if follow["announced"] and is_agent(task) and (
                        task not in on_screen or now - follow["moved"] > AGENT_QUIET_S):
                    # the agent's app lives on (a chat stays open for hours): its pointer must not stay that long
                    agent, team = short_agent(task)
                    post({"agent": agent, "team": team, "id": f"task:{task}", "state": "done"})
                    follow["announced"] = False
                    log(f"REST   {task}  (" + ("its window closed" if task not in on_screen else "no news in its window") + ")")
                    continue
                if follow["announced"] and now - follow["alive"] >= ALIVE_EVERY_S:
                    agent, team = short_agent(task)
                    post({"agent": agent, "team": team, "id": f"task:{task}", "alive": True})
                    follow["alive"] = now

    def run(self):
        """The window loop (its own thread)."""
        if not self.wait_for(self.tasks_ready, 180) and not self.stopped.is_set():
            log("the scheduled tasks could not be read yet: watching goes on, they are asked for again")
        if self.stopped.is_set():
            return
        log(f"watching {len(self.tasks)} scheduled tasks")
        self.threads.append(threading.Thread(target=self.watch_processes, name="BuddyTaskProcesses", daemon=True))
        self.threads[-1].start()
        self.wait_for(self.processes_ready, 300)             # what runs already is not announced as new
        first_in_line()
        due = time.monotonic()
        while not self.stopped.is_set():
            began = time.monotonic()
            if began - due > LATE_POINTER_S:
                log(f"slow: the look at the windows came {began - due:.1f} s late  (the PC was full)")
            took = {}
            try:
                for step in (self.apply_switch, self.scan_windows, self.keep_alive):
                    step_began = time.monotonic()
                    step()
                    took[step.__name__] = time.monotonic() - step_began
            except Exception as error:                      # a watch must not die on one odd window
                log(f"error: {type(error).__name__}: {error}")
                self.stopped.wait(5)
            if time.monotonic() - began > LATE_POINTER_S:
                parts = ", ".join(f"{name} {seconds:.1f}" for name, seconds in took.items() if seconds >= 0.5)
                log(f"slow: one look at the windows took {time.monotonic() - began:.1f} s  ({parts or 'no single step'})")
            due = time.monotonic() + WINDOWS_EVERY_S
            self.stopped.wait(WINDOWS_EVERY_S)
