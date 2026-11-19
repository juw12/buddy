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
* a browser window a followed program works through the screen instead (UI Automation, no debugging port: a
  script that reads Gmail in your own Chrome) is followed too: the pointer flies to the thing that is pressed
  (crew_screen.py). After a click the pointer stays there while the page changes, instead of going back to
  the top of the window with every new title;
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
# Apps whose AI works on this PC through programs it starts itself (its Python, PowerShell, the browser it
# opens). What those open gets a pointer too. The app's own windows are never pointed at.
AGENT_HOSTS = {"vector.exe": "Vector"}
AGENT_TEAM = "AI agent"
DRIVEN_BROWSER = "Browser agent"   # a browser a program steers, when nobody is left to say which program
AGENT_QUIET_S = 60                # an agent's app stays open all day: its pointer goes after this long without news
CLICK_LANDS_S = 0.4               # the pointer needs this long to fly to a click: then it hops and ripples there
CLICK_HOLDS_S = 10                # news about a window this soon after a click in it leaves the pointer at the click
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
fast_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
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


def what_it_does(exe, title):
    """A short bubble line. Only the program is named, never the window title (it can hold a mail subject)."""
    lowered = title.lower()
    if "gmail" in lowered or "mail.google" in lowered:
        return "Working in Gmail"
    if exe in CONSOLE_HOSTS or exe in CONSOLE_APPS:
        return "Opened a command window"
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

    def __init__(self, on=True, ignored=()):
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
        self.first_windows = True
        self.on = bool(on)        # the pointers' switch: off = nothing is given to the crew (the trace goes on)
        self.applied = self.on    # what the watch has acted on; other threads only set self.on
        self.ignored = {str(name) for name in ignored or ()}    # tasks that are never followed
        self.stopped = threading.Event()
        self.threads = []
        self.asked_for_clicks = set()   # (pid, created) of the steered browsers that are asked for their clicks
        self.screen = crew_screen.ScreenWatch(self.screen_action, log)   # presses in a browser worked through the screen

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
        for name, work in (("BuddyTaskList", self.watch_tasks), ("BuddyTaskWindows", self.run)):
            self.threads.append(threading.Thread(target=work, name=name, daemon=True))
            self.threads[-1].start()

    def stop(self):
        """For good (Buddy quits, or the setting is switched off). The pointers it showed are let go: ask
        poll() once more for that news."""
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.screen.stop()
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
            except Exception as error:                      # a watcher must not die on one odd process
                log(f"error (processes): {type(error).__name__}: {error}")
                self.stopped.wait(5)
            if time.monotonic() - began > SLOW_S:
                log(f"slow: Windows took {time.monotonic() - began:.0f} s for the process list"
                    "  (the windows were watched all the time)")
            self.stopped.wait(PROCESSES_EVERY_S)

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
        point (x is None) = the spot is behind another window: the trace has it, and the pointer is shown
        on whatever of that browser window can be seen."""
        with self.lock:
            tag = self.tags.get(pid)
        if tag:
            self.act(tag[0], pid, 0, action, what, x, y, window)

    def screen_action(self, hwnd, pid, action, what, x, y, gone=False):
        """The same for a browser window that is worked through the screen. Such a window can be one of your
        own browser (a task opened a page in it), so it is the window that names the task, not the process.
        `gone`: the pressed thing was gone before its place could be read (there is no point then)."""
        entry = self.windows.get(hwnd)
        if entry and entry[0] == pid and entry[2]:
            self.act(entry[2], pid, hwnd, action, what, x, y, entry[1], gone)

    def act(self, task, pid, hwnd, action, what, x, y, window, gone=False):
        thing = f"a {what}" if what else "the page"
        with self.lock:
            follow = self.followed.get(task)
            if follow is None:
                return
            follow["moved"] = time.monotonic()
            # where the agent last clicked: the next news about that window leaves the pointer there
            follow["spot"] = (pid, hwnd, x, y, follow["moved"]) if x is not None else None
            exe = self.known.get(pid, (0, "browser", 0))[1]
            if x is not None:
                where = f" at ({x}, {y})"
            else:
                where = ": the thing was gone before its place could be read" if gone else " behind another window"
            log(f"{'TYPE  ' if action == 'type' else 'CLICK '} {task}  -> {exe} pid {pid}: {thing}{where}"
                + ("" if self.on else "  (pointers off)"))
            if not self.on:
                return
            agent, team = short_agent(task)
            event = {"agent": agent, "team": team, "id": f"task:{task}", "state": "working"}
            if x is None:
                event["window"] = window[:80]
            else:
                event.update({"x": x, "y": y})
                if hwnd:
                    # a spot in that window: when the window goes behind another one (a script parks
                    # its browser there after a press), the pointer goes to what is left to see of it
                    event.update({"hwnd": hwnd, "window": window[:80]})
            post({**event, "say": f"Typing in {thing}" if action == "type" else f"Clicking {thing}"})
            follow["announced"] = True
        if action != "type":
            landed = threading.Timer(CLICK_LANDS_S, self.click_lands, args=(task, {**event, "say": f"Clicked {thing}"}))
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
                say = what_it_does(exe, title)
                if changed:
                    log(f"WINDOW {task}  -> {exe} pid {pid}: \"{title[:90]}\"  [{say}]" + ("" if self.on else "  (pointers off)"))
                if self.on:
                    event = {"agent": agent, "team": team, "id": f"task:{task}", "say": say,
                             "window": title[:80], "state": "working"}
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
        self.first_windows = False
        self.screen.follow(self.screen_browsers(), front)

    def screen_browsers(self):
        """{window: its process} of the browser windows a followed task works in, for crew_screen. Without the
        browsers that tell their clicks themselves, over their debugging port (crew_browser)."""
        with self.lock:
            if not self.followed:
                return {}
            by_port = {pid for pid, _ in self.asked_for_clicks}
            return {hwnd: entry[0] for hwnd, entry in self.windows.items()
                    if entry[2] in self.followed and entry[0] not in by_port
                    and self.exe_of(entry[0]) in crew_screen.BROWSERS}

    def keep_alive(self):
        now = time.monotonic()
        if not self.on:
            return
        on_screen = {entry[2] for entry in self.windows.values()}
        with self.lock:
            for task, follow in self.followed.items():
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
