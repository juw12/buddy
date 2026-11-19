"""The crew's task watch (crew_tasks.py), the steered browser's clicks (crew_browser.py), the presses in a
browser worked through the screen (crew_screen.py) and the pointers' button (crew_button.py). Nothing real is
started, traced or shown: processes, windows, the browser and the taskbar icon are stand-ins."""

import ast
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crew_browser  # noqa: E402
import crew_button  # noqa: E402
import crew_screen  # noqa: E402
import crew_sources  # noqa: E402
import crew_tasks as ct  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VECTOR = "AI agent\\Vector"


def check(name, fact):
    assert fact, name


def started(second):
    """A Windows start time (100 ns steps since 1601) some seconds after a fixed moment."""
    return windows_time(1_800_000_000 + second)


def windows_time(wall):
    return 116444736000000000 + int(wall * 10_000_000)


def tag(watch, pid):
    return watch.tags.get(pid, (None,))[0]


@pytest.fixture(autouse=True)
def no_browser_is_listened_to(monkeypatch):
    """No test lets the watch put real hooks and listeners on a browser of this PC."""
    monkeypatch.setattr(crew_screen.ScreenWatch, "start", lambda self: None)


@pytest.fixture
def lab(monkeypatch):
    """Made-up processes and windows for one watch: `commands` are their command lines, `screen` the windows
    ({number: (pid, title)}), `look(watch, table)` is one look at the list of processes."""
    lab = types.SimpleNamespace(logged=[], posted=[], commands={}, watched=[], screen={})

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def command(self):
            return lab.commands.get(self.pid, "")

        def path(self):
            return ""

    def look(watch, table, **kwargs):
        monkeypatch.setattr(ct, "process_table", lambda: dict(table))
        watch.look_at_processes(**kwargs)

    lab.look = look
    monkeypatch.setattr(ct, "log", lab.logged.append)
    monkeypatch.setattr(ct, "post", lab.posted.append)
    monkeypatch.setattr(ct, "OneProcess", FakeProcess)
    monkeypatch.setattr(ct, "visible_windows", lambda: dict(lab.screen))
    # no real browser is asked for its clicks, and no real window is in front
    monkeypatch.setattr(ct, "watch_clicks", lambda watch, pid, created, command: lab.watched.append(pid))
    monkeypatch.setattr(ct, "CLICK_LANDS_S", 0.05)
    monkeypatch.setattr(ct.user32, "GetForegroundWindow", lambda: 0)
    return lab


# ----------------------------------------------------------------------------------------- AI apps and browsers
def test_an_ai_apps_programs_their_windows_and_their_clicks(lab, monkeypatch):
    logged, posted, commands, watched, screen = lab.logged, lab.posted, lab.commands, lab.watched, lab.screen
    VECTOR = "AI agent\\Vector"
    watch = ct.TaskWatch()
    watch.on = watch.applied = True
    watch.tasks = [("T\\A", ["c:\\a\\a.py", "c:\\a\\"])]
    vector, renderer, repl, user_edge = 100, 110, 120, 130
    commands[repl] = "python.exe -m vector_repl --owner-pid 100"
    base = {0: (0, "", 0), 4: (0, "system", 0), 950: (4, "explorer.exe", started(2)),
            vector: (950, "vector.exe", started(10)), renderer: (vector, "vector.exe", started(11)),
            repl: (vector, "python.exe", started(12)), user_edge: (950, "msedge.exe", started(5))}
    lab.look(watch, base, startup=True)
    check("a chat's Python (child of Vector.exe) is the Vector agent's", tag(watch, repl) == VECTOR)
    check("Vector's own windows and helpers are not", vector not in watch.tags and renderer not in watch.tags)
    check("what ran before the watch is not announced as new", not any("START" in line for line in logged))
    check("your own Edge is nobody's", user_edge not in watch.tags)

    # the chat runs PowerShell, PowerShell opens a browser and is gone before the next look
    shell, edge = 200, 210
    commands[shell] = "powershell.exe -NoProfile -NonInteractive -Command Start-Process msedge"
    commands[edge] = "msedge.exe --remote-debugging-port=9473 --user-data-dir=E:\\Temp\\p https://example.org/register"
    lab.look(watch, {**base, shell: (vector, "powershell.exe", started(100))})
    check("the chat's PowerShell is the agent's", tag(watch, shell) == VECTOR)
    lab.look(watch, {**base, edge: (shell, "msedge.exe", started(101))})          # shell ended in this same look
    check("a browser whose launcher left in the same look still belongs to the chat", tag(watch, edge) == VECTOR)
    check("the agent is not ended while the chat lives", VECTOR in watch.followed and not any("END" in line for line in logged))

    # a link you click in Vector: the browser comes straight from Vector.exe
    clicked = 220
    commands[clicked] = "msedge.exe --single-argument https://example.org"
    lab.look(watch, {**base, edge: (shell, "msedge.exe", started(101)), clicked: (vector, "msedge.exe", started(110))})
    check("a link you click in Vector is not the agent's", clicked not in watch.tags)

    # a process under a chat is the chat's, whatever script it runs; Buddy never follows itself
    script, me = 230, os.getpid()
    commands[script] = "python.exe c:\\a\\a.py"
    commands[me] = "Buddy.exe"
    lab.look(watch, {**base, edge: (shell, "msedge.exe", started(101)), script: (repl, "python.exe", started(120)),
                  me: (repl, "pythonw.exe", started(121))})
    check("a process under a chat inherits the chat (parent's tag first)", tag(watch, script) == VECTOR)
    check("Buddy itself is never followed, whoever started it", me not in watch.tags)

    # a steered browser nobody can be named for (its launcher ended long ago)
    orphan = 240
    commands[orphan] = "msedge.exe --remote-debugging-port=9222 --user-data-dir=E:\\Temp\\q"
    commands[241] = "msedge.exe --type=renderer --remote-debugging-port=9222"
    lab.look(watch, {**base, edge: (shell, "msedge.exe", started(101)), orphan: (9999, "msedge.exe", started(130)),
                  241: (orphan, "msedge.exe", started(131))})
    check("a browser started with remote debugging is the Browser agent's", tag(watch, orphan) == "AI agent\\Browser agent")
    check("its helper processes inherit", tag(watch, 241) == "AI agent\\Browser agent")
    check("only the agent's browser can claim a window by timing, never its consoles",
          all(handoff[0] in ct.BROWSERS for handoff in watch.handoffs if ct.is_agent(handoff[1])))

    check("a steered browser is asked for its clicks, once, and its helper processes are not",
          sorted(watched) == [edge, orphan])

    # windows
    logged.clear(), posted.clear()
    screen.update({1: (vector, "Vector"), 2: (user_edge, "News - Microsoft Edge")})
    watch.first_windows = False
    watch.scan_windows()
    check("no pointer for Vector's own window or your own Edge", not posted)
    screen[3] = (edge, "ORCID - Register - Microsoft Edge")
    watch.scan_windows()
    working = [event for event in posted if event.get("state") == "working"]
    check("the chat's browser window gets the pointer",
          len(working) == 1 and working[0]["agent"] == "Vector" and working[0]["team"] == "AI agent"
          and working[0]["say"] == "Working in the browser" and any("WINDOW AI agent\\Vector" in line for line in logged))
    watch.keep_alive()
    check("Buddy is told the agent is alive", any(event.get("alive") for event in posted))
    screen[3] = (edge, "Sign in to GitHub - Microsoft Edge")
    watch.scan_windows()
    check("a new page in that window moves the pointer again", len([e for e in posted if e.get("state") == "working"]) == 2)

    # clicks and typing the browser reports
    posted.clear(), logged.clear()
    watch.browser_action(edge, "click", "button", 640, 480)
    first = list(posted)
    time.sleep(0.3)
    check("a click: the pointer flies to the exact spot",
          len(first) == 1 and (first[0]["x"], first[0]["y"]) == (640, 480) and first[0]["say"] == "Clicking a button"
          and first[0]["agent"] == "Vector" and "window" not in first[0])
    check("when it has arrived it says so with new words at the same spot (Buddy's hop and ripple)",
          len(posted) == 2 and posted[1]["say"] == "Clicked a button" and (posted[1]["x"], posted[1]["y"]) == (640, 480))
    check("the click is in the trace", any(line.startswith("CLICK  AI agent\\Vector") and "a button at (640, 480)" in line for line in logged))
    posted.clear()
    watch.browser_action(edge, "type", "box", 300, 200)
    time.sleep(0.2)
    check("typing: one pointer at the box, the text is never told",
          len(posted) == 1 and posted[0]["say"] == "Typing in a box" and set(posted[0]) == {"agent", "team", "id", "x", "y", "state", "say", "dock", "press"})
    posted.clear(), logged.clear()
    watch.browser_action(edge, "click", "button", None, None, window="Sign in to GitHub - Microsoft Edge")
    time.sleep(ct.CLICK_LANDS_S + 0.3)
    check("a click behind another window: told with the window, no made-up spot, and in the trace",
          [event["say"] for event in posted] == ["Clicking a button", "Clicked a button"]
          and all(event.get("window") == "Sign in to GitHub - Microsoft Edge" and "x" not in event for event in posted)
          and any(line.startswith("CLICK  AI agent\\Vector") and "a button behind another window" in line for line in logged))

    # which of the browser's reports reach the watch, and how
    bc = ct.crew_browser
    told = []
    reader = bc.Browser(edge, 0, lambda *args, **kwargs: told.append((args, kwargs)))
    monkeypatch.setattr(bc, "page_areas", lambda pid: [(22, "ORCID - Work - Microsoft Edge", (100, 200, 1100, 900))])
    report = '{"action": "click", "what": "button", "x": 50, "y": 40, "scale": 1}'
    monkeypatch.setattr(bc, "on_top", lambda hwnd, point: True)
    monkeypatch.setattr(bc, "is_yours", lambda action, hwnd, point: False)
    reader.saw("ORCID", report)
    check("a click the browser reports, on screen: its exact screen point", told == [((edge, "click", "button", 150, 240), {})])
    told.clear()
    monkeypatch.setattr(bc, "is_yours", lambda action, hwnd, point: True)
    reader.saw("ORCID", report)
    check("your own click in the agent's browser is not told", not told)
    monkeypatch.setattr(bc, "on_top", lambda hwnd, point: False)
    reader.saw("ORCID", report)
    check("a covered click is told with the window, not dropped",
          told == [((edge, "click", "button", None, None), {"window": "ORCID - Work - Microsoft Edge"})])
    told.clear()
    reader.saw("ORCID", '{"action": "read", "x": 1, "y": 1}')
    reader.saw("ORCID", "not json")
    check("anything else a page could send is nothing", not told)

    posted.clear()
    watch.browser_action(99999, "click", "button", 1, 1)
    check("a click of a browser nobody follows is nothing", not posted)
    watch.on = False
    watch.browser_action(edge, "click", "link", 10, 10)
    time.sleep(0.2)
    watch.on = True
    check("pointers off: a click is only written in the trace", not posted and any("(pointers off)" in line for line in logged))
    real_monotonic = time.monotonic
    monkeypatch.setattr(ct.time, "monotonic", lambda: real_monotonic() + ct.AGENT_QUIET_S - 10)
    watch.browser_action(edge, "click", "link", 10, 10)
    monkeypatch.setattr(ct.time, "monotonic", lambda: real_monotonic() + ct.AGENT_QUIET_S + 5)
    posted.clear()
    watch.keep_alive()
    monkeypatch.setattr(ct.time, "monotonic", real_monotonic)
    time.sleep(0.2)
    check("a click is news: the pointer does not go while the agent clicks", not any(event.get("state") == "done" for event in posted))
    check("the pointer goes one minute after the last news", ct.AGENT_QUIET_S == 60)

    # where a spot in the page is on screen
    areas = [(11, "Inbox - Gmail - Profile 1 - Microsoft\u200b Edge", (100, 200, 1100, 900)),
             (22, "ORCID and 2 more pages - Profile 1 - Microsoft\u200b Edge", (1300, 150, 2300, 950))]
    check("the window titled with the tab is the one", bc.pick_area(areas, "ORCID", 11)[0] == 22)
    check("else the window in front", bc.pick_area(areas, "Something else", 11)[0] == 11)
    check("two windows and no hint: no guess", bc.pick_area(areas, "", 33) is None)
    check("a spot in the page becomes a screen point (page corner + spot x zoom)",
          bc.screen_point(areas[0], 170, 105, 1.25) == (100 + 212, 200 + 131))
    check("a spot scrolled out of the window is not pointed at", bc.screen_point(areas[0], 170, 2000, 1) is None)
    check("the debugging port is read from the command line",
          bc.port_of('msedge.exe --remote-debugging-port=9473 --user-data-dir="E:\\Temp\\p"') == 9473
          and bc.port_of("msedge.exe --no-startup-window") is None)

    # quiet for a long time: the pointer goes, although the chat (and so the agent) lives on
    posted.clear()
    monkeypatch.setattr(ct.time, "monotonic", lambda: real_monotonic() + 2 * ct.AGENT_QUIET_S + 10)
    watch.scan_windows()
    watch.keep_alive()
    check("no news for a long time: the pointer goes", [event.get("state") for event in posted] == ["done"]
          and not watch.followed[VECTOR]["announced"])
    screen[3] = (edge, "Welcome - Microsoft Edge")
    watch.scan_windows()
    check("news again: the pointer is back", posted[-1].get("state") == "working" and watch.followed[VECTOR]["announced"])
    posted.clear()
    del screen[3]
    watch.scan_windows()
    watch.keep_alive()
    monkeypatch.setattr(ct.time, "monotonic", real_monotonic)
    check("its window closed: the pointer goes at once", [event.get("state") for event in posted] == ["done"])


# ----------------------------------------------------------------------------------------- scheduled tasks
def test_which_processes_belong_to_a_task(lab, monkeypatch):
    logged, commands = lab.logged, lab.commands
    watch = ct.TaskWatch()
    watch.on = watch.applied = True
    watch.tasks = [("T\\A", ["c:\\a\\a.py", "c:\\a\\"]), ("T\\B", ["c:\\b\\b.py", "c:\\b\\"])]
    base = {0: (0, "", 0), 4: (0, "system", 0), 900: (4, "svchost.exe", started(1)), 950: (4, "explorer.exe", started(2))}
    lab.look(watch, base, startup=True)

    commands[5000] = "pythonw.exe c:\\a\\a.py"
    lab.look(watch, {**base, 5000: (900, "pythonw.exe", started(100))})
    check("a task's script is recognised", watch.tags.get(5000, (None,))[0] == "T\\A" and any("START  T\\A" in line for line in logged))

    commands[5000] = "pythonw.exe c:\\b\\b.py"
    lab.look(watch, {**base, 5000: (900, "pythonw.exe", started(200))})
    check("same pid, new process: old task ended, new task started",
          watch.tags[5000][0] == "T\\B" and any("END    T\\A" in line for line in logged)
          and any("START  T\\B" in line for line in logged) and "T\\A" not in watch.followed)

    commands.update({300: "python.exe -c pass", 700: "powershell.exe -File c:\\a\\helper.ps1", 5002: "python.exe -c pass"})
    table = {**base, 5000: (900, "pythonw.exe", started(200)),
             700: (900, "powershell.exe", started(300)),          # folder match, started by the task service
             300: (700, "python.exe", started(301)),              # its child has the LOWER pid: parent first
             5002: (5000, "python.exe", started(150)),            # "parent" 5000 is younger: not its real parent
             710: (950, "powershell.exe", started(302))}          # same folder, but you started it from Explorer
    commands[710] = "powershell.exe -File c:\\a\\helper.ps1"
    lab.look(watch, table)
    check("a helper in the task's folder, started by a launcher, is the task's", watch.tags.get(700, (None,))[0] == "T\\A")
    check("a child seen in the same look as its parent keeps the tag", watch.tags.get(300, (None,))[0] == "T\\A")
    check("a process older than its 'parent' does not inherit (pid of an ended parent)", 5002 not in watch.tags)
    check("the same script started by yourself is not called a task", 710 not in watch.tags)

    del table[700], table[300]
    lab.look(watch, table)
    check("the task ends when its last process is gone",
          "T\\A" not in watch.followed and sum("END    T\\A" in line for line in logged) == 2)

    # a window had a process looked up before the list showed it: the list must not drop or double it
    with watch.lock:
        watch.adopt(6000, (900, "pythonw.exe", started(400)), "pythonw.exe c:\\a\\a.py", "", launcher=True)
    starts = sum("START  T\\A" in line for line in logged)
    old_table = dict(table)
    monkeypatch.setattr(ct, "process_table", lambda: old_table)    # a list asked for BEFORE the process was tagged
    watch.tags[6000] = (watch.tags[6000][0], watch.tags[6000][1], watch.looks + 1)   # tagged after that list began
    watch.look_at_processes()
    check("a late list does not end a task a window just started", 6000 in watch.tags and "T\\A" in watch.followed)
    lab.look(watch, {**table, 6000: (900, "pythonw.exe", started(400))})
    check("the list then takes it over without a second START",
          6000 in watch.known and sum("START  T\\A" in line for line in logged) == starts)


def test_a_tasks_windows_and_hand_offs(lab, monkeypatch):
    logged, posted, commands, screen = lab.logged, lab.posted, lab.commands, lab.screen
    watch = ct.TaskWatch()
    watch.on = watch.applied = True
    watch.tasks = [("T\\Mail", ["c:\\m\\mail.py"])]
    chrome, own_python = 8000, 8100
    base = {0: (0, "", 0), 4: (0, "system", 0), 900: (4, "svchost.exe", started(1)),
            chrome: (950, "chrome.exe", started(5)), 950: (4, "explorer.exe", started(2))}
    lab.look(watch, base, startup=True)
    screen[1] = (chrome, "Old tab - Google Chrome")
    watch.scan_windows()                                          # the first look: what was there before
    logged.clear(), posted.clear()

    now = time.time()
    commands[own_python] = "pythonw.exe c:\\m\\mail.py"
    screen[2] = (chrome, "Inbox - Gmail - Google Chrome")          # the window is there BEFORE the list shows the task
    watch.scan_windows()
    check("a new Chrome window with no task around is nobody's", not posted and watch.windows[2][2] is None)
    lab.look(watch, {**base, own_python: (900, "pythonw.exe", windows_time(now - 1.0)),
                  8200: (own_python, "chrome.exe", windows_time(now - 0.4))})     # the hand-off process, seen late
    watch.scan_windows()
    told = [event for event in posted if event.get("say") == "Working in Gmail"]
    check("hand-off seen AFTER its window: the window is still given to the task",
          watch.windows[2][2] == "T\\Mail" and len(told) == 1 and any("WINDOW T\\Mail" in line for line in logged))
    check("the Chrome window from before the watch is never claimed", watch.windows[1][2] is None)
    watch.scan_windows()
    check("no second pointer for the same window while nothing changes", len([e for e in posted if e.get("state") == "working"]) == 1)

    screen[3] = (chrome, "Your own tab - Google Chrome")
    real_time = time.time
    monkeypatch.setattr(ct.time, "time", lambda: real_time() + 30)    # half a minute later you open your own window
    watch.scan_windows()
    monkeypatch.setattr(ct.time, "time", real_time)
    check("your own Chrome window long after the hand-off is not claimed", watch.windows[3][2] is None)

    watch.on = False                                               # the pointers' switch: off
    watch.apply_switch()
    check("button off: Buddy is told to let the pointer go", any(event.get("state") == "leave" for event in posted))
    posted.clear()
    screen[2] = (chrome, "Sent - Gmail - Google Chrome")
    watch.scan_windows()
    check("button off: nothing is posted, the trace goes on",
          not posted and any("Sent - Gmail" in line and "(pointers off)" in line for line in logged))
    watch.on = True
    watch.apply_switch()
    watch.scan_windows()
    check("button on again: the window on screen gets its pointer back", any(event.get("state") == "working" for event in posted))


# ----------------------------------------------------------------------------------------- the task list
NEWS, BENCH, FOLLOWUP = r"C:\Tasks\news", r"C:\Tasks\bench tools", r"C:\Tasks\followup"
TASK_RECORDS = [
    {"Name": "News", "Actions": [rf'wscript.exe //B //Nologo "{NEWS}\run_quiet.vbs" "{NEWS}\run_daily.ps1" |dir=']},
    {"Name": "NewsRefresh",
     "Actions": [rf'wscript.exe //B //Nologo "{NEWS}\run_quiet.vbs" "{NEWS}\refresh_silent.ps1" |dir=']},
    {"Name": "Benchmark",
     "Actions": [rf'wscript.exe //B //Nologo "{BENCH}\run_quiet.vbs" "{BENCH}\run_benchmark.bat" |dir=']},
    {"Name": "Followup", "Actions": [rf'wscript.exe "{FOLLOWUP}\run_hidden.vbs" check |dir={FOLLOWUP}']},
    {"Name": "Mail at logon", "Actions": [r'"C:\Program Files\Mail\mail.exe" /background |dir=']},
    {"Name": "Sync mornings", "Actions": [r"pythonw.exe sync.py --morning |dir=C:\Tasks\sync"]},
    {"Name": "Sync evenings", "Actions": [r"pythonw.exe sync.py --evening |dir=C:\Tasks\sync"]},
]


def windows_says(monkeypatch, records):
    """What PowerShell answers when the watch asks for the scheduled tasks."""
    monkeypatch.setattr(ct, "subprocess", types.SimpleNamespace(
        run=lambda *args, **kwargs: types.SimpleNamespace(stdout=json.dumps(records)), SubprocessError=Exception))


def test_which_script_names_a_task(monkeypatch):
    windows_says(monkeypatch, TASK_RECORDS)
    tasks = ct.read_tasks()
    keys = dict(tasks)
    check("a launcher two tasks share names neither of them",
          not any("run_quiet.vbs" in key for name in ("News", "NewsRefresh") for key in keys[name]))
    check("each task keeps its own script as key",
          rf"{NEWS}\run_daily.ps1".lower() in keys["News"] and rf"{NEWS}\refresh_silent.ps1".lower() in keys["NewsRefresh"]
          and rf"{BENCH}\run_benchmark.bat".lower() in keys["Benchmark"])
    check("a launcher only one task has stays that task's key", rf"{FOLLOWUP}\run_hidden.vbs".lower() in keys["Followup"])
    check("two tasks with nothing but the same script keep it: one of them is named, not nobody",
          keys["Sync mornings"] == keys["Sync evenings"] and r"c:\tasks\sync\sync.py" in keys["Sync mornings"])
    check("the program that runs a script is never a key",
          not any(key.endswith(("wscript.exe", "pythonw.exe")) for task_keys in keys.values() for key in task_keys))
    for name, script in (("News", rf"{NEWS}\run_daily.ps1"), ("NewsRefresh", rf"{NEWS}\refresh_silent.ps1")):
        launcher_line = rf'"C:\Windows\System32\wscript.exe" //B //Nologo "{NEWS}\run_quiet.vbs" "{script}"'
        shell_line = rf'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}"'
        check(f"{name}: the launcher process is that task's", ct.match_task(launcher_line, tasks, folders=False) == name)
        check(f"{name}: its PowerShell is that task's", ct.match_task(shell_line, tasks, folders=False) == name)
    check("the benchmark's cmd.exe is that task's",
          ct.match_task(rf'cmd.exe /c ""{BENCH}\run_benchmark.bat""', tasks, folders=False) == "Benchmark")
    check("a helper in a task's folder is the task's only when a launcher started it",
          ct.match_task(rf"python.exe {BENCH}\helper.py", tasks, folders=True) == "Benchmark"
          and ct.match_task(rf"python.exe {BENCH}\helper.py", tasks, folders=False) is None)


def test_tasks_you_do_not_want_followed(monkeypatch):
    windows_says(monkeypatch, TASK_RECORDS)
    check("a task is followed until you name it", "Mail at logon" in dict(ct.read_tasks()))
    check("a named task is never followed", "Mail at logon" not in dict(ct.read_tasks({"Mail at logon"})))
    watch = ct.TaskWatch(ignored=["Mail at logon"])
    check("the watch asks without the tasks in Settings (crew_tasks_ignored)", watch.ignored == {"Mail at logon"})


def test_windows_cannot_be_asked(monkeypatch):
    logged = []

    def fails(*args, **kwargs):
        raise OSError("no PowerShell")

    monkeypatch.setattr(ct, "log", logged.append)
    monkeypatch.setattr(ct, "subprocess", types.SimpleNamespace(run=fails, SubprocessError=Exception))
    check("no answer is None (asked again soon), never an empty list", ct.read_tasks() is None and len(logged) == 1)


# ----------------------------------------------------------------------------------------- one source of the crew
class FakeCrew:
    def __init__(self):
        self.posted, self.closed = [], False

    def post(self, event):
        self.posted.append(event)

    def tick(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    """Nothing of this PC: no tasks, one process, no windows, and the trace in a folder of the test."""
    monkeypatch.setattr(ct, "TRACE", tmp_path / "crew" / "trace.log")
    monkeypatch.setattr(ct, "read_tasks", lambda ignored=(): [])
    monkeypatch.setattr(ct, "process_table", lambda: {0: (0, "", 0)})
    monkeypatch.setattr(ct, "visible_windows", lambda: {})
    monkeypatch.setattr(ct, "first_in_line", lambda: None)
    ct._news.clear()
    yield tmp_path / "crew" / "trace.log"
    ct._news.clear()


def test_the_crew_gets_its_news_from_the_watch(quiet):
    watch = ct.TaskWatch()
    ct.post({"agent": "Nightly copy", "team": "Backups", "id": "task:Backups\\Nightly copy", "say": "Working in the browser",
             "window": "Drive - Chrome", "state": "working"})
    crew = FakeCrew()
    watcher = crew_sources.CrewWatcher(crew, sources=[watch])
    watcher.step()
    check("the crew's watcher gives the crew what the watch saw", [event["agent"] for event in crew.posted] == ["Nightly copy"])
    watcher.step()
    check("and only once", len(crew.posted) == 1)
    check("the event is one the crew understands",
          crew_sources.buddy_crew.normalize_event(crew.posted[0])["window"] == "Drive - Chrome")
    check("stopping the crew's watcher must not stop the watch: the trace goes on with the pointers off",
          not hasattr(watch, "close"))


def test_the_pointers_switch(quiet):
    watch = ct.TaskWatch(on=False)
    ct.post({"agent": "left over", "state": "working"})          # from the moment before the switch went off
    watch.set_pointers(True)
    check("switching on shows nothing from before", watch.poll() == [] and watch.on is True)
    watch.set_pointers(True)
    watch.set_pointers(False)
    trace = quiet.read_text(encoding="utf-8")
    check("the trace says when the pointers went on and off, once each",
          trace.count("POINTERS ON") == 1 and trace.count("POINTERS OFF") == 1)


def test_the_watch_starts_and_stops(quiet):
    watch = ct.TaskWatch()
    watch.start()
    watch.start()                                                 # a second start is nothing
    until = time.monotonic() + 10
    while time.monotonic() < until and not (watch.processes_ready.is_set() and len(watch.threads) == 4):
        time.sleep(0.05)
    check("four threads: the task list, the processes, the windows, the clicks a program makes with the mouse",
          sorted(thread.name for thread in watch.threads)
          == ["BuddyTaskList", "BuddyTaskMouse", "BuddyTaskProcesses", "BuddyTaskWindows"])
    with watch.lock:
        watch.followed["Backups\\Nightly copy"] = {"started": time.time(), "announced": True, "alive": 0.0,
                                                  "pids": {1}, "moved": 0.0}
    watch.stop()
    for thread in watch.threads:
        thread.join(5)
    check("stop ends them all", not any(thread.is_alive() for thread in watch.threads))
    leaving = [event for event in watch.poll() if event.get("state") == "leave"]
    check("and lets go of the pointer it showed", [event["agent"] for event in leaving] == ["Nightly copy"])
    trace = quiet.read_text(encoding="utf-8")
    check("the trace has the start, the tasks and the end",
          "task watch on" in trace and "watching 0 scheduled tasks" in trace and "task watch off" in trace)


def test_the_trace_starts_over_when_it_is_full(quiet, monkeypatch):
    monkeypatch.setattr(ct, "TRACE_MAX", 200)
    for number in range(8):
        ct.log(f"WINDOW a line that is long enough to fill the trace soon ({number})")
    check("the full trace is kept once as .log.old", quiet.with_suffix(".log.old").exists() and quiet.stat().st_size < 400)
    check("each line has its time", quiet.read_text(encoding="utf-8")[:2] == "20")


def test_the_real_windows_and_processes_can_be_asked():
    """Both modules ask Windows with their own copies of its libraries: neither gets in the other's way."""
    crew_browser.page_areas(os.getpid())
    windows = ct.visible_windows()
    table = ct.process_table()
    check("the windows are a table (it can be empty on a build machine)", isinstance(windows, dict))
    check("this very process is in the list, with its parent and start", os.getpid() in table and table[os.getpid()][2] > 0)
    with ct.OneProcess(os.getpid()) as me:
        check("one process tells its own command line, program and start",
              me.command() and me.path().lower().endswith(".exe") and me.created() == table[os.getpid()][2])


def test_a_busy_window_of_this_program_never_holds_the_look(monkeypatch):
    """Inside Buddy the watch shares a program with Buddy's own windows. Asking such a window for its title
    waits for the thread that owns it (Buddy's drawing: the pointer came up to 9 s late). The look asks
    Windows instead, and leaves this program's windows out."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    made, free = threading.Event(), threading.Event()
    box = {}

    def busy_window():
        # a small window that is on screen, takes no focus and has no taskbar button
        box["hwnd"] = user32.CreateWindowExW(0x08000080, "STATIC", "a window of the watch's own program",
                                             0x90000000, 0, 0, 240, 120, None, None, None, None)
        made.set()
        free.wait(30)                                    # busy: this thread answers no question
        if box["hwnd"]:
            user32.DestroyWindow(box["hwnd"])

    def look():
        seen = {}
        thread = threading.Thread(target=lambda: seen.update(ct.visible_windows()), daemon=True)
        began = time.monotonic()
        thread.start()
        thread.join(5)
        return seen, thread.is_alive() or time.monotonic() - began > 2

    owner = threading.Thread(target=busy_window, daemon=True)
    owner.start()
    made.wait(10)
    try:
        if not box.get("hwnd"):
            pytest.skip("no window can be made here (a build machine without a screen)")
        hwnd = int(box["hwnd"])
        asking = threading.Thread(target=user32.GetWindowTextLengthW, args=(hwnd,), daemon=True)
        asking.start()
        asking.join(0.5)
        check("asking the window itself does wait for its busy thread", asking.is_alive())
        seen, late = look()
        check("the look does not wait for it", not late)
        check("a window of this program is left out", hwnd not in seen)
        monkeypatch.setattr(ct, "OWN_PID", 0)            # as if it were another program's window
        seen, late = look()
        check("another program's busy window is seen with its title, and nobody waits",
              not late and seen.get(hwnd) == (os.getpid(), "a window of the watch's own program"))
    finally:
        free.set()
        owner.join(10)


def test_a_busy_thread_of_this_program_never_slows_the_look():
    """Inside Buddy other threads work too (its drawing, the list of processes). A question to Windows that
    gives the GIL back gets it again only after the busy thread's turn (5 ms), and one look asks about every
    window there is, shown or not: 0.9 s was measured for 407 windows, and 1 to 9 s inside Buddy. The look
    keeps the GIL for its quick questions, so it takes a few ms whatever the other threads do."""
    import ctypes
    import statistics
    from ctypes import wintypes

    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("the GIL is only kept on 64-bit Windows")
    user32 = ctypes.WinDLL("user32")
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    made, free, stop = threading.Event(), threading.Event(), threading.Event()
    hidden = []

    def many_windows():
        # 300 windows nobody sees: a desktop in use has about that many, a build machine has few
        for _ in range(300):
            hwnd = user32.CreateWindowExW(0x08000080, "STATIC", "hidden", 0x80000000, 0, 0, 240, 120,
                                          None, None, None, None)
            if hwnd:
                hidden.append(hwnd)
        made.set()
        free.wait(60)
        for hwnd in hidden:
            user32.DestroyWindow(hwnd)

    def busy():
        while not stop.is_set():
            sum(number * number for number in range(2000))

    owner = threading.Thread(target=many_windows, daemon=True)
    owner.start()
    made.wait(20)
    worker = threading.Thread(target=busy, daemon=True)
    try:
        if len(hidden) < 300:
            pytest.skip("no window can be made here (a build machine without a screen)")
        worker.start()
        time.sleep(0.2)                                  # the busy thread is at work
        took = []
        for _ in range(5):
            began = time.perf_counter()
            ct.visible_windows()
            took.append(time.perf_counter() - began)
            time.sleep(0.02)
        check(f"a look at 300 windows beside a busy thread takes no time (it took {statistics.median(took):.3f} s)",
              statistics.median(took) < 0.2)
    finally:
        stop.set()
        free.set()
        owner.join(20)


def test_what_a_pointer_says_and_is_called():
    check("a mail page", ct.what_it_does("chrome.exe", "Inbox - Gmail - Google Chrome") == "Working in Gmail")
    check("a console", ct.what_it_does("conhost.exe", "C:\\Windows\\py.exe") == "Opened a command window")
    check("any other page: the title is never told", ct.what_it_does("msedge.exe", "My bank - Edge") == "Working in the browser")
    check("any other program", ct.what_it_does("excel.exe", "Budget.xlsx") == "Opened excel")
    check("a task in a folder: the folder is the team", ct.short_agent("Backups\\Nightly copy") == ("Nightly copy", "Backups"))
    check("a task without a folder", ct.short_agent("Nightly copy") == ("Nightly copy", "Scheduled task"))


# ----------------------------------------------------------------------------------------- the browser's port
class FakeBrowser:
    """A WebSocket server that says what the test tells it to, one connection."""

    def __init__(self):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.request = b""
        self.got = []                 # (kind, text) of what the client sent
        self.connection = None
        self.ready = threading.Event()
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        import base64
        import hashlib

        self.connection, _ = self.server.accept()
        while b"\r\n\r\n" not in self.request:
            self.request += self.connection.recv(4096)
        key = [line.split(b": ")[1] for line in self.request.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key")][0]
        proof = base64.b64encode(hashlib.sha1(key + crew_browser.WebSocket.MAGIC.encode()).digest())
        self.connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                                b"Sec-WebSocket-Accept: " + proof + b"\r\n\r\n")
        self.ready.set()

    def say(self, data, kind=0x1, last=True):
        head = bytes([(0x80 if last else 0) | kind])
        if len(data) < 126:
            head += bytes([len(data)])
        elif len(data) < 65536:
            head += bytes([126]) + struct.pack("!H", len(data))
        else:
            head += bytes([127]) + struct.pack("!Q", len(data))
        self.raw(head + data)

    def raw(self, data):
        self.connection.sendall(data)

    def read(self, count):
        data = b""
        while len(data) < count:
            data += self.connection.recv(count - len(data))
        return data

    def hear(self):
        """One frame from the client: (kind, text)."""
        first, second = self.read(2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", self.read(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", self.read(8))[0]
        assert second & 0x80, "a client must mask what it sends"
        mask = self.read(4)
        data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(self.read(size)))
        return first & 0x0F, data.decode("utf-8")

    def close(self):
        for one in (self.connection, self.server):
            if one is not None:
                one.close()


def test_talking_to_a_browsers_port():
    browser = FakeBrowser()
    try:
        with crew_browser.WebSocket(f"ws://127.0.0.1:{browser.port}/devtools/browser/abc") as line:
            assert browser.ready.wait(5)
            check("it asks for the browser's own address, and sends no Origin (a browser refuses a program with one)",
                  browser.request.startswith(b"GET /devtools/browser/abc HTTP/1.1\r\n") and b"origin" not in browser.request.lower())
            line.send('{"id": 1, "method": "Target.setDiscoverTargets"}')
            check("a question arrives whole", browser.hear() == (0x1, '{"id": 1, "method": "Target.setDiscoverTargets"}'))
            long_question = "x" * 70_000
            line.send(long_question)
            check("a long one too", browser.hear() == (0x1, long_question))

            browser.say(b'{"id": 1, "result": {}}')
            check("an answer", line.recv(2) == '{"id": 1, "result": {}}')
            browser.say('{"title": "caf'.encode(), last=False)
            browser.say('\u00e9 \u2615"}'.encode("utf-8"), kind=0x0)
            check("an answer in two frames is one message", json.loads(line.recv(2)) == {"title": "caf\u00e9 \u2615"})
            for size in (300, 70_000):
                browser.say(b"y" * size)
                check(f"an answer of {size} bytes", line.recv(2) == "y" * size)
            browser.say(b"are you there", kind=0x9)
            browser.say(b'"after the ping"')
            check("a ping is answered and is no message", line.recv(2) == '"after the ping"' and browser.hear() == (0xA, "are you there"))

            browser.raw(bytes([0x81, 11]) + b"hello")            # half a frame, then nothing for a while
            with pytest.raises(TimeoutError):
                line.recv(0.3)
            browser.raw(b" world")
            check("what came before a timeout is not lost", line.recv(2) == "hello world")
            browser.say(b"", kind=0x8)
            with pytest.raises(ConnectionError):
                line.recv(2)
    finally:
        browser.close()


def test_a_port_that_is_not_a_browser():
    server = socket.create_server(("127.0.0.1", 0))

    def refuse():
        connection, _ = server.accept()
        connection.recv(4096)
        connection.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        connection.close()

    threading.Thread(target=refuse, daemon=True).start()
    try:
        with pytest.raises(ConnectionError):
            crew_browser.WebSocket(f"ws://127.0.0.1:{server.getsockname()[1]}/devtools/browser/abc")
        with pytest.raises(OSError):
            crew_browser.WebSocket("https://example.org/")
    finally:
        server.close()


def test_the_listener_tells_a_place_and_a_kind_only():
    script = crew_browser.LISTENER
    check("what the page hears of is its spot, the kind of thing and the zoom",
          "JSON.stringify({action, what, x: place[0], y: place[1], scale: window.devicePixelRatio || 1})" in script)
    check("never what was typed, a text or an address",
          not any(word in script for word in (".value", "innerText", "textContent", "location.href", ".href", "event.key")))


# ----------------------------------------------------------------------------------------- the button
# ----------------------------------------------------------------------------------------- a browser worked through the screen
def test_a_browser_worked_through_the_screen_is_followed_by_its_windows(lab, monkeypatch):
    """A chat's script opens Gmail in a normal Chrome (no debugging port) and presses things in it through
    UI Automation; a scheduled task has a page opened in your own, already running browser."""
    logged, posted, commands, screen = lab.logged, lab.posted, lab.commands, lab.screen
    watch = ct.TaskWatch()
    watch.on = watch.applied = True
    watch.tasks = [("Mail\\Read inbox", ["c:\\mail\\read.py", "c:\\mail\\"])]
    vector, repl, script, chrome, steered, fox, own = 100, 120, 200, 210, 220, 230, 300
    commands[repl] = "python.exe -m vector_repl --owner-pid 100"
    commands[script] = "python.exe c:\\work\\gmail_reader.py"
    commands[chrome] = "chrome.exe --profile-directory=Default --new-window https://mail.google.com/"
    commands[steered] = "msedge.exe --remote-debugging-port=9473 --user-data-dir=E:\\Temp\\p"
    commands[fox] = "firefox.exe https://example.org"
    base = {0: (0, "", 0), 4: (0, "system", 0), 950: (4, "explorer.exe", started(2)),
            vector: (950, "vector.exe", started(10)), repl: (vector, "python.exe", started(12)),
            own: (950, "chrome.exe", started(5))}
    lab.look(watch, base, startup=True)
    lab.look(watch, {**base, script: (repl, "python.exe", started(100)), chrome: (script, "chrome.exe", started(101)),
                     steered: (script, "msedge.exe", started(102)), fox: (script, "firefox.exe", started(103))})
    watch.first_windows = False
    screen.update({1: (chrome, "Inbox - Gmail - Google Chrome"), 2: (steered, "Register - Microsoft Edge"),
                   3: (fox, "Example - Mozilla Firefox"), 4: (own, "News - Google Chrome"), 5: (vector, "Vector")})
    followed = []
    monkeypatch.setattr(watch.screen, "follow", lambda windows, front=0: followed.append((dict(windows), front)))
    monkeypatch.setattr(ct.user32, "GetForegroundWindow", lambda: 4)
    watch.scan_windows()
    check("the agent's Chrome without a debugging port is followed through the screen; not the browser that "
          "tells its clicks itself, not Firefox, not your own Chrome, not the app",
          followed[-1] == ({1: chrome}, 4))

    # the browser reports a press: crew_screen hands the window, the process and the place over
    logged.clear(), posted.clear()
    watch.screen_action(1, chrome, "click", "link", 640, 480)
    time.sleep(ct.CLICK_LANDS_S + 0.3)
    check("a press through the screen: the pointer flies to the thing, then hops there",
          [(event.get("x"), event.get("y"), event["say"]) for event in posted]
          == [(640, 480, "Clicking a link"), (640, 480, "Clicked a link")] and posted[0]["agent"] == "Vector")
    check("the point is a spot in that window: behind another window it is not pointed at",
          all(event["hwnd"] == 1 and event["window"] == "Inbox - Gmail - Google Chrome" for event in posted))
    check("and it is in the trace, without any text of the page",
          any(line == "CLICK  AI agent\\Vector  -> chrome.exe pid 210: a link at (640, 480)" for line in logged))

    # the mail opens: the window has a new title
    posted.clear()
    screen[1] = (chrome, "A mail subject - Gmail - Google Chrome")
    watch.scan_windows()
    check("the page changes after the click: the pointer stays at the click, in that window (not back to the "
          "top of the window)",
          len(posted) == 1 and (posted[0].get("x"), posted[0].get("y"), posted[0].get("hwnd")) == (640, 480, 1)
          and posted[0]["window"].startswith("A mail subject") and posted[0]["say"] == "Working in Gmail")
    real_monotonic = time.monotonic
    monkeypatch.setattr(ct.time, "monotonic", lambda: real_monotonic() + ct.CLICK_HOLDS_S + 1)
    posted.clear()
    screen[1] = (chrome, "Inbox - Gmail - Google Chrome")
    watch.scan_windows()
    monkeypatch.setattr(ct.time, "monotonic", real_monotonic)
    check("long after the click a new title points at the window again: that window, by its number",
          len(posted) == 1 and "x" not in posted[0] and posted[0]["hwnd"] == 1)

    posted.clear(), logged.clear()
    watch.screen_action(1, chrome, "type", "box", 300, 200)
    watch.screen_action(1, chrome, "click", "button", None, None)
    time.sleep(ct.CLICK_LANDS_S + 0.3)
    check("typing, and a press on a thing no part of which can be seen: told with the window",
          [event["say"] for event in posted] == ["Typing in a box", "Clicking a button", "Clicked a button"]
          and (posted[0]["x"], posted[0]["y"]) == (300, 200)
          and all(event.get("window") == "Inbox - Gmail - Google Chrome" and "x" not in event for event in posted[1:])
          and any("a button behind another window" in line for line in logged))
    check("every press names its window by number and may be said at the edge of the screen (no ghost)",
          all(event["hwnd"] == 1 and event["dock"] is True and event["press"] is True for event in posted))
    posted.clear()
    screen[1] = (chrome, "Search results - Gmail - Google Chrome")
    watch.scan_windows()
    check("after a press nobody could see, the pointer is not left at an older spot", "x" not in posted[0])
    check("what that press brings (the next title) is told at the edge of the screen too, as the window's news",
          posted[0].get("dock") is True and "press" not in posted[0])

    posted.clear(), logged.clear()
    watch.screen_action(1, chrome, "click", "", None, None, gone=True)
    time.sleep(ct.CLICK_LANDS_S + 0.3)
    check("a press on a thing that was gone before its place could be read: in the trace as just that, and "
          "said at the window",
          logged == ["CLICK  AI agent\\Vector  -> chrome.exe pid 210: the page: the thing was gone before its place could be read"]
          and [event["say"] for event in posted] == ["Clicking the page", "Clicked the page"]
          and all("x" not in event and event["window"].startswith("Search results") for event in posted))

    posted.clear()
    watch.screen_action(4, own, "click", "link", 5, 5)
    watch.screen_action(1, 99999, "click", "link", 5, 5)
    watch.screen_action(77, chrome, "click", "link", 5, 5)
    check("a press in a window nobody follows (your own, another process's, a closed one) is nothing", not posted)

    # a click the browser tells over its port holds the pointer the same way
    posted.clear()
    watch.browser_action(steered, "click", "button", 900, 500)
    time.sleep(ct.CLICK_LANDS_S + 0.3)
    check("a click a steered browser told itself is a point of its own, as before",
          len(posted) == 2 and all("hwnd" not in event and "window" not in event for event in posted))
    posted.clear()
    screen[2] = (steered, "Welcome - Microsoft Edge")
    watch.scan_windows()
    check("the same after a click a steered browser told itself",
          (posted[0].get("x"), posted[0].get("y"), posted[0].get("hwnd")) == (900, 500, 2))

    # your own running browser gets a window from a scheduled task (a hand-off): the window names the task
    logged.clear(), posted.clear()
    launcher = 400
    commands[launcher] = "chrome.exe https://mail.google.com/"
    table = {**base, script: (repl, "python.exe", started(100)), chrome: (script, "chrome.exe", started(101)),
             5000: (950, "svchost.exe", started(1)), 401: (5000, "python.exe", time_now_as_windows() - 20_000_000)}
    commands[401] = "python.exe c:\\mail\\read.py"
    lab.look(watch, table)
    lab.look(watch, {**table, launcher: (401, "chrome.exe", time_now_as_windows())})
    screen[9] = (own, "Inbox - Gmail - Google Chrome")
    watch.scan_windows()
    check("a task's page in your own browser is followed through the screen too", followed[-1][0].get(9) == own)
    watch.screen_action(9, own, "click", "link", 700, 300)
    check("a press there is the task's, though the browser's process is your own",
          any(line.startswith("CLICK  Mail\\Read inbox  -> chrome.exe pid 300: a link at (700, 300)") for line in logged)
          and posted[-1]["agent"] == "Read inbox")

    # which window, and what when you cannot see it
    posted.clear()
    screen[11] = (chrome, "Gmail - Google Chrome")
    watch.scan_windows()
    check("a new window of the agent is named by its number (your own Gmail has the same words in its title), "
          "and may be told at the edge of the screen when it opened where you cannot see it",
          len(posted) == 1 and posted[0]["hwnd"] == 11 and posted[0]["dock"] is True)
    posted.clear()
    screen[11] = (chrome, "Search results - Gmail - Google Chrome")
    watch.scan_windows()
    check("while the window is new, its next title too", len(posted) == 1 and posted[0].get("dock") is True)
    real_time = time.time
    monkeypatch.setattr(ct.time, "time", lambda: real_time() + ct.NEW_WINDOW_S + 1)
    posted.clear()
    screen[11] = (chrome, "A mail - Gmail - Google Chrome")
    watch.scan_windows()
    check("the titles it gets later are only shown on the window itself, when you can see it",
          len(posted) == 1 and posted[0]["hwnd"] == 11 and "dock" not in posted[0])
    watch.screen_action(11, chrome, "click", "link", None, None)
    posted.clear()
    screen[11] = (chrome, "Another mail - Gmail - Google Chrome")
    watch.scan_windows()
    monkeypatch.setattr(ct.time, "time", real_time)
    check("but the title a press in it brings is told where the press was told: at the edge of the screen",
          len(posted) == 1 and posted[0]["say"] == "Working in Gmail" and posted[0].get("dock") is True)
    time.sleep(ct.CLICK_LANDS_S + 0.3)

    stopped = []
    monkeypatch.setattr(watch.screen, "stop", lambda: stopped.append(True))
    watch.stop()
    check("when the watch stops, the listening stops", stopped == [True])


def time_now_as_windows():
    return windows_time(time.time())


class FakeScreen:
    """Windows for crew_screen.ScreenWatch: what is hooked and listened to is only written down."""

    def __init__(self, on_sign, on_button=None):
        self.on_sign, self.on_button = on_sign, on_button
        self.hooked, self.listened, self.broken = set(), {}, set()
        self.news, self.selections = {}, {}     # process -> which news its hook brings; window -> entries too?
        self.did = []

    def hook(self, pid, events=crew_screen.HOOKED):
        self.hooked.add(pid)
        self.news[pid] = events
        self.did.append(("hook", pid))
        return [pid]

    def unhook(self, handles):
        self.hooked -= set(handles)
        self.did.append(("unhook", handles[0]))

    def listen(self, hwnd, tell, selections=False):
        if hwnd in self.broken:
            raise OSError("the window is gone")
        self.listened[hwnd] = tell
        self.selections[hwnd] = selections
        self.did.append(("listen", hwnd))
        return hwnd

    def unlisten(self, token):
        del self.listened[token]
        self.did.append(("unlisten", token))


def test_a_browser_is_only_listened_to_once_somebody_works_its_page(monkeypatch):
    told, logged = [], []
    watch = crew_screen.ScreenWatch(lambda *action: told.append(action), logged.append, screen=FakeScreen)
    screen = watch.screen = FakeScreen(watch.page_spoke)
    pages = {501: 1, 502: 2, 503: 3}                       # a page's own window -> the browser window it is in
    monkeypatch.setattr(crew_screen, "page_of", lambda hwnd: pages.get(hwnd, 0))
    monkeypatch.setattr(crew_screen, "kind_of", lambda hwnd: "page")     # every window here is a browser's
    started_threads = []

    def start(self):
        started_threads.append(True)
        self.thread = "started"
    monkeypatch.setattr(crew_screen.ScreenWatch, "start", start)

    watch.follow({}, front=7)
    check("nothing to follow: no thread, nothing asked", not started_threads and not watch.wake.is_set())
    watch.follow({1: 210, 2: 210, 3: 300}, front=7)
    check("the first browser window to follow starts the thread", started_threads == [True] and watch.wake.is_set())
    watch.settle()
    check("at first a browser is only heard the way that asks it nothing: one hook per process, no listener",
          screen.hooked == {210, 300} and not screen.listened)
    screen.on_sign(999)                                    # news from something that is not a page (a toolbar)
    screen.on_sign(504)                                    # a page of a window nobody wants
    watch.settle()
    check("news that is not about a followed window's page changes nothing", not screen.listened)
    screen.on_sign(501)
    watch.settle()
    check("the first news about a thing in the page: somebody works it through the screen, so its presses are "
          "listened for, in that window only", set(screen.listened) == {1} and screen.hooked == {210, 300})
    check("the trace says so",
          "presses in the browser window 0x1 (pid 210) are followed: it is worked through the screen" in logged)
    check("a browser is asked for presses, ticks and values; not for every entry a page chooses", screen.selections[1] is False)
    screen.on_sign(502)
    watch.settle()
    check("a process whose windows are all listened to is not hooked any more", screen.hooked == {300})
    screen.on_sign(501)
    watch.settle()
    check("a window is listened to once", [step for step in screen.did if step[0] == "listen"] == [("listen", 1), ("listen", 2)])

    watch.follow({2: 210, 3: 300}, front=7)                # window 1 closed
    watch.settle()
    check("a window that is gone is let go", set(screen.listened) == {2})
    watch.follow({2: 210}, front=7)
    watch.settle()
    check("a window that was never worked is let go too, with its hook", not screen.hooked)

    screen.broken.add(4)
    watch.follow({2: 210, 4: 400}, front=7)
    watch.settle()
    screen.on_sign(504)
    pages[504] = 4
    screen.on_sign(504)
    watch.settle()
    watch.settle()
    check("a window that cannot be listened to is told once and left alone",
          len([line for line in logged if "are not followed" in line]) == 1 and 400 not in screen.hooked
          and 4 not in screen.listened)
    watch.follow({2: 210, 4: 401}, front=7)                # the window's number has a new owner
    watch.follow({2: 210}, front=7)
    watch.settle()
    check("and forgotten when it is gone", not watch.failed)

    # the report of a listener reaches the task watch with its window and process
    monkeypatch.setattr(crew_screen.crew_browser, "on_top", lambda hwnd, point: True)
    monkeypatch.setattr(crew_screen, "mouse", lambda: (0, 0))
    screen.listened[2](crew_screen.INVOKED, 50005, (600, 400, 800, 420), False)
    check("a listener's report is the window's", told == [(2, 210, "click", "link", 700, 410)])

    watch.wanted = {}
    watch.settle()
    check("when the watch stops, every listener and hook is taken off", not screen.listened and not screen.hooked)
    check("one thread does it all", started_threads == [True])
    stopped = crew_screen.ScreenWatch(lambda *action: None, logged.append, screen=FakeScreen)
    stopped.stop()
    stopped.follow({1: 210}, front=7)
    check("a stopped watch starts nothing", started_threads == [True] and stopped.wake.is_set())


def test_which_reports_of_a_browser_are_a_press_of_the_agent(monkeypatch):
    told = []
    watch = crew_screen.ScreenWatch(lambda *action, **more: told.append(action + tuple(more.items())),
                                    lambda line: None, screen=FakeScreen)
    HWND, PID, LINK, BUTTON, TICK, EDIT = 2, 210, 50005, 50000, 50002, 50004
    visible = {"all": True, "points": set()}
    clock = {"now": 5000.0, "idle": 60.0, "mouse": (0, 0)}
    monkeypatch.setattr(crew_screen.crew_browser, "on_top",
                        lambda hwnd, point: hwnd == HWND and (visible["all"] or point in visible["points"]))
    monkeypatch.setattr(crew_screen.crew_browser, "page_areas",
                        lambda pid: [(HWND, "Inbox - Gmail", (100, 200, 1100, 900))] if pid == PID else [])
    monkeypatch.setattr(crew_screen, "mouse", lambda: clock["mouse"])
    monkeypatch.setattr(crew_screen, "idle_seconds", lambda: clock["idle"])
    monkeypatch.setattr(crew_screen.time, "monotonic", lambda: clock["now"])

    def report(what, kind, box, has_keyboard=False, after=10.0):
        clock["now"] += after
        told.clear()
        watch.saw(HWND, PID, what, kind, box, has_keyboard)
        return list(told)

    row = (582, 513, 698, 534)
    check("a pressed link: the middle of the link", report(crew_screen.INVOKED, LINK, row) == [(HWND, PID, "click", "link", 640, 523)])
    check("the kinds of things", [report(crew_screen.INVOKED, kind, row)[0][3] for kind in (BUTTON, 50031, TICK, 50013, EDIT, 50003, 50019, 50025)]
          == ["button", "button", "tick box", "tick box", "box", "list", "tab", ""])
    visible.update(all=False, points={(611, 523)})
    check("its middle is covered and a part of it shows: that part",
          report(crew_screen.INVOKED, LINK, row) == [(HWND, PID, "click", "link", 611, 523)])
    visible["points"] = set()
    check("nothing of it shows (covered, or the window is off the screen): told without a place",
          report(crew_screen.INVOKED, LINK, row) == [(HWND, PID, "click", "link", None, None)])
    visible["all"] = True
    check("a thing without a size has no place", report(crew_screen.INVOKED, LINK, (5, 5, 5, 5)) == [])

    clock.update(mouse=(640, 523), idle=0.2)
    check("your mouse is on the thing and you just used it: your own click", report(crew_screen.INVOKED, LINK, row) == [])
    clock["idle"] = 30.0
    check("your mouse only rests there: the agent's press", len(report(crew_screen.INVOKED, LINK, row)) == 1)
    clock.update(mouse=(50, 50), idle=0.05)
    watch.front = (HWND, clock["now"])
    check("you type somewhere else and the press brings the browser to the front: still the agent's press",
          len(report(crew_screen.INVOKED, LINK, row)) == 1)

    tick = (502, 272, 516, 285)
    first = report(crew_screen.TOGGLED, TICK, tick)
    again = report(crew_screen.INVOKED, TICK, tick, after=0.02)
    check("a tick box told twice (ticked, and pressed) is one press", first == [(HWND, PID, "click", "tick box", 509, 278)] and again == [])
    first = report(crew_screen.INVOKED, TICK, tick)
    burst = [report(crew_screen.TOGGLED, TICK, (502, 300 + 30 * index, 516, 313 + 30 * index), after=0.01) for index in range(20)]
    check("one press that ticks a whole list of boxes is one press", len(first) == 1 and not any(burst))
    check("two different things pressed right after each other are two presses",
          len(report(crew_screen.INVOKED, LINK, row)) == 1 and len(report(crew_screen.INVOKED, BUTTON, (332, 261, 433, 299), after=0.1)) == 1)

    # a row of mail that opens its mail so quickly that Windows could not read the row's place any more
    clock.update(mouse=(50, 50), idle=60.0)
    gone = report(crew_screen.INVOKED, 0, None)
    check("a press on a thing that was gone before its place could be read: a press all the same, without a place",
          gone == [(HWND, PID, "click", "", None, None, ("gone", True))])
    check("told once", report(crew_screen.INVOKED, 0, None, after=0.1) == [])
    check("a new value of a thing that is gone is nothing", report(crew_screen.VALUE, 0, None) == [])
    clock.update(mouse=(640, 523), idle=0.2)
    check("your mouse is in that window and you just used it: your own click", report(crew_screen.INVOKED, 0, None) == [])
    clock.update(mouse=(50, 50), idle=0.05)

    box = (332, 650, 648, 684)
    clock["idle"] = 60.0
    check("a box in the page with the keyboard gets a value: typing, pointed at the start of the box",
          report(crew_screen.VALUE, EDIT, box, has_keyboard=True) == [(HWND, PID, "type", "box", 412, 667)])
    check("more of it right after is the same typing", report(crew_screen.VALUE, EDIT, box, True, after=0.3) == [])
    check("and later it is told again", len(report(crew_screen.VALUE, EDIT, box, True, after=2.0)) == 1)
    check("a box without the keyboard gets a value (the page filled it in): nothing", report(crew_screen.VALUE, EDIT, box, False) == [])
    check("the address bar gets a value (a new page): nothing", report(crew_screen.VALUE, EDIT, (329, 151, 944, 175), True) == [])
    check("something that is no box gets a value: nothing", report(crew_screen.VALUE, 50015, box, True) == [])
    clock["idle"] = 0.05
    watch.front = (HWND, clock["now"] - 30)
    check("you type in that window yourself (it was in front already): your own typing", report(crew_screen.VALUE, EDIT, box, True) == [])
    watch.front = (HWND, clock["now"] + 10 - 0.03)
    check("you type somewhere else and the browser only just came to the front: the agent's typing",
          len(report(crew_screen.VALUE, EDIT, box, True)) == 1)
    watch.front = (99, clock["now"] - 30)
    check("another window is in front: the agent's typing", len(report(crew_screen.VALUE, EDIT, box, True)) == 1)

    watch.follow({HWND: PID}, front=HWND)
    since = watch.front
    clock["now"] += 5
    watch.follow({HWND: PID}, front=HWND)
    check("the watch tells which window is in front, and since when", watch.front == since and since[0] == HWND)

    check("spots: the middle first, then towards the edges; a wished one before all",
          crew_screen.spots((0, 0, 400, 40)) == [(200, 20), (100, 20), (300, 20), (200, 10), (200, 30)]
          and crew_screen.spots((0, 0, 400, 40), first=(80, 20))[0] == (80, 20))


def test_every_other_window_is_listened_to_at_once(monkeypatch):
    """A window that is no browser's (Notepad, a settings window, a company tool): listened to from the first
    look. Asking it costs its program nothing, and no page has to give a sign first."""
    told, logged = [], []
    watch = crew_screen.ScreenWatch(lambda *action, **more: told.append(action), logged.append, screen=FakeScreen)
    screen = watch.screen = FakeScreen(watch.page_spoke, watch.button_spoke)
    kinds = {1: "page", 2: "plain", 3: "plain", 4: None, 5: "plain"}
    asked = []
    monkeypatch.setattr(crew_screen, "kind_of", lambda hwnd: asked.append(hwnd) or kinds[hwnd])
    monkeypatch.setattr(crew_screen.ScreenWatch, "start", lambda self: setattr(self, "thread", "started"))
    monkeypatch.setattr(crew_screen.crew_browser, "on_top", lambda hwnd, point: True)
    monkeypatch.setattr(crew_screen.crew_browser, "page_areas", lambda pid: [])       # no window here has a page
    monkeypatch.setattr(crew_screen, "mouse", lambda: (0, 0))
    monkeypatch.setattr(crew_screen, "idle_seconds", lambda: 60.0)

    watch.follow({1: 210, 2: 500, 3: 500, 4: 600}, front=7)
    watch.settle()
    watch.settle()
    check("the plain windows are listened to at the first look; the browser waits for a sign; a window that "
          "tells nothing (Firefox, a command window) is left alone",
          set(screen.listened) == {2, 3} and watch.waiting == {1: 210} and 600 not in screen.hooked)
    check("a plain window is also asked for the entries that are chosen", screen.selections == {2: True, 3: True})
    check("the trace names each one once",
          [line for line in logged if "0x2" in line] == ["presses in the window 0x2 (pid 500) are followed"])
    check("what kind a window is, is asked once", sorted(asked) == [1, 2, 3, 4])
    check("its process is hooked for the one news a Windows Forms button gives, the browser's for all news",
          screen.news[500] == crew_screen.BUTTONS_HOOKED and screen.news[210] == crew_screen.HOOKED)

    # a Windows Forms button says only "my state changed", as a window of its own
    buttons = {901: (2, (100, 100, 200, 130)), 902: (9, (0, 0, 50, 20))}
    monkeypatch.setattr(crew_screen, "forms_button", lambda hwnd: buttons.get(hwnd))
    screen.on_button(901)
    check("a pressed Windows Forms button in a followed window: a press at its middle", told == [(2, 500, "click", "button", 150, 115)])
    screen.on_button(901)
    screen.on_button(902)                                  # a button of a window nobody follows
    screen.on_button(903)                                  # a window that is no such button
    check("the same button again at once, a button somewhere else, another thing: nothing more", len(told) == 1)

    told.clear()
    screen.listened[3](crew_screen.SELECTED, 50007, (300, 300, 500, 320), False)
    check("an entry of a list that is chosen is a press", told == [(3, 500, "click", "list", 400, 310)])
    screen.listened[3](crew_screen.SELECTED, 50007, (300, 320, 500, 340), False)
    check("the entries a list chooses right after are the same press", len(told) == 1)
    told.clear()
    screen.listened[2](crew_screen.VALUE, 50004, (100, 200, 400, 224), True)
    check("a box with the keyboard gets a value: typing, and a plain window has no page it must lie in",
          told == [(2, 500, "type", "box", 180, 212)])
    told.clear()
    screen.listened[2](crew_screen.VALUE, 50004, (100, 200, 400, 224), False)
    check("a box without the keyboard gets a value (its program filled it in): nothing", told == [])

    watch.follow({1: 210, 2: 500, 3: 500, 5: 210}, front=7)
    watch.settle()
    check("a process with a browser window that waits and a plain one is hooked for all news (the button's is in it)",
          set(screen.listened) == {2, 3, 5} and screen.news[210] == crew_screen.HOOKED)
    watch.follow({1: 210, 2: 500, 3: 500}, front=7)
    watch.settle()
    check("the plain window closed: let go", set(screen.listened) == {2, 3} and 5 not in watch.plain)

    monkeypatch.setattr(crew_screen, "MOST_PLAIN", 2)
    kinds[6] = "plain"
    watch.follow({2: 500, 3: 500, 6: 700}, front=7)
    watch.settle()
    check("only so many plain windows at once", set(screen.listened) == {2, 3} and 700 not in screen.hooked)
    watch.follow({3: 500, 6: 700}, front=7)
    watch.settle()
    check("one closes: the next one gets its turn", set(screen.listened) == {3, 6} and screen.hooked == {500, 700})
    watch.follow({}, front=7)
    watch.settle()
    check("nothing to follow: every listener and hook is off", not screen.listened and not screen.hooked and not watch.plain)
    screen.on_button(901)
    check("and a button's news is nobody's", told == [])


def test_which_mouse_presses_are_a_programs(monkeypatch):
    """Buddy's listener for the mouse (the click sounds) also knows who pressed: Windows gives a program's press
    no device. A touchpad's, a finger's on the screen, Buddy's own and a press over a remote desktop are not told."""
    import keyit_actions
    import keyit_core

    mouse = keyit_core.MouseClicks(player=None)
    check("as long as the touchpad cannot be listened to, nothing is told (its presses have no device either)",
          mouse.from_a_program(0, 0, 100.0) is False)
    mouse.touchpad_heard = True
    check("no device, no mark: a program's press", mouse.from_a_program(0, 0, 100.0) is True)
    check("a program may put its own mark on it", mouse.from_a_program(None, 0x1234, 100.0) is True)
    check("your mouse has a device", mouse.from_a_program(0x10041, 0, 100.0) is False)
    check("Buddy's own click (a button of the island, a macro) carries Buddy's mark",
          mouse.from_a_program(0, keyit_core.MouseClicks.OWN_CLICK_MARK, 100.0) is False)
    check("a finger or a pen on the screen carries Windows' mark",
          mouse.from_a_program(0, 0xFF515780, 100.0) is False and mouse.from_a_program(0, 0xFF515701, 100.0) is False)
    mouse.touchpad_at = 99.5
    check("a finger was on the touchpad a moment ago: your own tap", mouse.from_a_program(0, 0, 100.0) is False)
    check("and long after it: a program's again", mouse.from_a_program(0, 0, 99.5 + mouse.TOUCHPAD_S + 0.1) is True)
    check("at another PC (remote desktop) every press has no device: nothing is told",
          mouse.from_a_program(0, 0, 200.0, remote=True) is False)

    told = []
    mouse.on_program_click = lambda button, x, y: told.append((button, x, y))
    mouse.touchpad_at = float("-inf")
    monkeypatch.setattr(keyit_core.ctypes.windll.user32, "GetSystemMetrics", lambda index: 0)

    def press(code, device, extra=0):
        data = types.SimpleNamespace(header=types.SimpleNamespace(hDevice=device),
                                     mouse=types.SimpleNamespace(ulExtraInformation=extra))
        mouse._tell_program_click(code, data, types.SimpleNamespace(pt=types.SimpleNamespace(x=640, y=480)))

    press(-1, None)
    press(-2, None)
    press(-1, 0x10041)
    press(-1, None, keyit_core.MouseClicks.OWN_CLICK_MARK)
    check("a program's press is told with its button and the place the pointer had; yours and Buddy's are not",
          told == [("left", 640, 480), ("right", 640, 480)])
    mouse.on_program_click = lambda button, x, y: 1 / 0
    press(-1, None)                                        # must not raise: the click sounds go on

    check("Buddy's own clicks carry the mark the listener knows", keyit_actions.OWN_CLICK_MARK == keyit_core.MouseClicks.OWN_CLICK_MARK)
    sent = []
    monkeypatch.setattr(keyit_actions.user32, "SetCursorPos", lambda x, y: sent.append(("to", x, y)))
    monkeypatch.setattr(keyit_actions.user32, "mouse_event", lambda *words: sent.append(words))
    monkeypatch.setattr(keyit_actions.time, "sleep", lambda seconds: None)
    keyit_actions.click_at(10, 20)
    check("both halves of Buddy's own click have it",
          sent[0] == ("to", 10, 20) and [words[-1] for words in sent[1:]] == [keyit_actions.OWN_CLICK_MARK] * 2)


def test_a_click_a_program_makes_with_the_mouse_is_followed_in_every_window(lab, monkeypatch):
    """An agent that moves the mouse itself and clicks: in a window of a program it started (Notepad), and in
    one that was open already (a drawing program: nobody can say which program clicked there)."""
    logged, posted, commands, screen = lab.logged, lab.posted, lab.commands, lab.screen
    watch = ct.TaskWatch()
    watch.on = watch.applied = True
    vector, repl, script, notepad, shell, drawing, own = 100, 120, 200, 240, 250, 310, 300
    commands[repl] = "python.exe -m vector_repl --owner-pid 100"
    commands[script] = "python.exe c:\\work\\fill_in.py"
    commands[notepad] = "notepad.exe c:\\work\\notes.txt"
    commands[shell] = "cmd.exe /k dir"
    base = {0: (0, "", 0), 4: (0, "system", 0), 950: (4, "explorer.exe", started(2)),
            vector: (950, "vector.exe", started(10)), repl: (vector, "python.exe", started(12)),
            own: (950, "chrome.exe", started(5)), drawing: (950, "sldworks.exe", started(6)),
            ct.OWN_PID: (950, "buddy.exe", started(7))}
    lab.look(watch, base, startup=True)
    lab.look(watch, {**base, script: (repl, "python.exe", started(100)), notepad: (script, "notepad.exe", started(101)),
                     shell: (script, "cmd.exe", started(102))})
    watch.first_windows = False
    screen.update({1: (notepad, "notes.txt - Notepad"), 3: (shell, "C:\\Windows\\System32\\cmd.exe"),
                   4: (own, "News - Google Chrome"), 6: (drawing, "SOLIDWORKS"), 8: (ct.OWN_PID, "Buddy")})
    followed = []
    monkeypatch.setattr(watch.screen, "follow", lambda windows, front=0: followed.append(dict(windows)))
    places = {(500, 400): (1, notepad), (503, 402): (1, notepad), (600, 400): (1, notepad), (900, 300): (6, drawing), (910, 310): (6, drawing), (920, 320): (6, drawing),
              (50, 50): (8, ct.OWN_PID), (30, 700): (77, 4321)}
    monkeypatch.setattr(ct, "window_at", lambda x, y: places.get((x, y), (0, 0)))
    watch.scan_windows()
    check("a window of any program the agent started is followed through the screen, not only a browser's; "
          "a command window has nothing to press", followed[-1] == {1: notepad})

    def said():
        time.sleep(ct.CLICK_LANDS_S + 0.3)
        return [(event.get("x"), event.get("y"), event.get("hwnd"), event["say"]) for event in posted]

    logged.clear(), posted.clear()
    watch.mouse_action("left", 500, 400)
    check("a mouse click of a program in the agent's window: the pointer flies to the click and hops there",
          said() == [(500, 400, 1, "Clicking here"), (500, 400, 1, "Clicked here")] and posted[0]["agent"] == "Vector"
          and posted[0]["window"] == "notes.txt - Notepad")
    check("the trace has it, as the mouse's",
          logged == ["CLICK  AI agent\\Vector  -> notepad.exe pid 240: the mouse at (500, 400)"])
    watch.screen_action(1, notepad, "click", "button", 500, 400)
    watch.mouse_action("left", 503, 402)
    check("the button says right after that it was pressed, and the click's second half comes (a double click): "
          "the same click, told once", len(said()) == 2 and len(logged) == 1)
    watch.mouse_action("left", 600, 400)
    check("a click somewhere else right after is another click", said()[2][:2] == (600, 400) and len(logged) == 2)
    real_monotonic = time.monotonic
    monkeypatch.setattr(ct.time, "monotonic", lambda: real_monotonic() + ct.SAME_CLICK_S + 1)
    posted.clear()
    watch.screen_action(1, notepad, "click", "button", 520, 410)
    monkeypatch.setattr(ct.time, "monotonic", real_monotonic)
    check("a press the window tells later is a press of its own", said()[0] == (520, 410, 1, "Clicking a button"))
    logged.clear(), posted.clear()
    watch.screen_action(1, notepad, "click", "", None, None, gone=True)
    check("a press with no kind in a window that is no browser's is not called a page",
          [words[3] for words in said()] == ["Clicking something", "Clicked something"]
          and logged == ["CLICK  AI agent\\Vector  -> notepad.exe pid 240: something: the thing was gone before its place could be read"])

    logged.clear(), posted.clear()
    watch.mouse_action("right", 900, 300)
    check("a click in a window that is not the agent's (a program that was open already): taken for the agent "
          "that is at work, shown in that window",
          said() == [(900, 300, 6, "Right-clicking here"), (900, 300, 6, "Right-clicked here")]
          and posted[0]["agent"] == "Vector" and posted[0]["window"] == "SOLIDWORKS")
    check("and the trace says that it was taken so",
          logged == ["CLICK  AI agent\\Vector  -> sldworks.exe pid 310: the mouse (right button) at (900, 300)"
                     "  (not its own window: taken for its click)"])

    # the agent's own windows close: it works on in the window it clicked in
    del screen[1], screen[3]
    watch.scan_windows()
    posted.clear()
    watch.keep_alive()
    check("its own windows are closed, the window it clicked in is open: the pointer is not put to rest",
          not [event for event in posted if event.get("state") == "done"])
    del screen[6]
    watch.scan_windows()
    watch.keep_alive()
    check("that window just closed: the pointer stays for a moment (a click on the taskbar has no window in the list)",
          not [event for event in posted if event.get("state") == "done"])
    monkeypatch.setattr(ct.time, "monotonic", lambda: real_monotonic() + ct.CLICK_HOLDS_S + 1)
    watch.keep_alive()
    monkeypatch.setattr(ct.time, "monotonic", real_monotonic)
    check("and then it rests", [event["state"] for event in posted if event.get("state") == "done"] == ["done"])
    screen[6] = (drawing, "SOLIDWORKS")
    watch.scan_windows()

    # whose click is it, when a scheduled task runs too?
    with watch.lock:
        watch.followed["Backups\\Nightly copy"] = {"started": time.time() + 50, "announced": False, "alive": 0.0,
                                                  "pids": {1}, "moved": time.monotonic() + 50}
    logged.clear(), posted.clear()
    watch.mouse_action("left", 910, 310)
    check("an AI agent is taken before a scheduled task, though the task has the later news",
          said()[0][3] == "Clicking here" and posted[0]["agent"] == "Vector")
    with watch.lock:
        agent = watch.followed.pop("AI agent\\Vector")
    logged.clear(), posted.clear()
    watch.mouse_action("middle", 900, 300)
    check("with no agent it is the scheduled task's",
          posted and posted[0]["agent"] == "Nightly copy" and "the mouse (middle button) at (900, 300)" in logged[0])
    time.sleep(ct.CLICK_LANDS_S + 0.3)

    with watch.lock:
        watch.followed.clear()
    logged.clear(), posted.clear()
    watch.mouse_action("left", 900, 300)
    watch.mouse_action("left", 30, 700)
    check("nobody is followed: only the trace has the click, with the program it went to (no ghost without a trace)",
          not posted and logged == ["CLICK  a program nobody follows  -> sldworks.exe pid 310: the mouse at (900, 300)",
                                    "CLICK  a program nobody follows  -> ? pid 4321: the mouse at (30, 700)"])
    with watch.lock:
        watch.followed["AI agent\\Vector"] = agent
    logged.clear(), posted.clear()
    watch.mouse_action("left", 50, 50)
    watch.mouse_action("left", 1, 1)
    check("a click on Buddy itself (its own buttons) or on nothing is nobody's work", not posted and not logged)
    watch.on = False
    watch.mouse_action("left", 920, 320)
    check("pointers off: the trace goes on, nothing is shown",
          not posted and len(logged) == 1 and logged[0].endswith("(not its own window: taken for its click)  (pointers off)"))
    watch.on = True

    # from the listener's thread to the watch's own
    done = []
    monkeypatch.setattr(ct, "first_in_line", lambda: None)
    monkeypatch.setattr(watch, "mouse_action", lambda *click: done.append(click) or (1 / 0 if click[1] == 1 else None))
    worker = threading.Thread(target=watch.watch_mouse, daemon=True)
    worker.start()
    watch.program_clicked("left", 1, 2)                    # this one fails: the watch goes on
    watch.program_clicked("right", 3, 4)
    until = time.monotonic() + 5
    while len(done) < 2 and time.monotonic() < until:
        time.sleep(0.02)
    monkeypatch.setattr(watch.screen, "stop", lambda: None)
    watch.stop()
    worker.join(5)
    check("the listener only notes a click; the watch's own thread works it off, and ends with the watch",
          done == [("left", 1, 2), ("right", 3, 4)] and not worker.is_alive()
          and any("error (a program's click): ZeroDivisionError" in line for line in logged))


def test_windows_can_be_asked_about_presses():
    """The real thing, on this PC: the hook and the listeners can be put on and taken off (this test's own
    process and a window of it are used: no browser is touched)."""
    pytest.importorskip("comtypes")
    facts, signs = {}, []

    def work():                                              # a thread of its own, as in Buddy
        try:
            screen = crew_screen.Screen(signs.append)
            hooks = screen.hook(os.getpid())
            facts["hooks"] = len(hooks)
            began = time.monotonic()
            screen.pump(0.05)
            facts["pump"] = time.monotonic() - began
            screen.unhook(hooks)
            hooks = screen.hook(os.getpid(), crew_screen.BUTTONS_HOOKED)
            facts["button hooks"] = len(hooks)
            screen.unhook(hooks)
            desktop = crew_screen.user32.GetDesktopWindow()
            token = screen.listen(desktop, lambda *report: None)
            screen.unlisten(token)
            screen.unlisten(token)                           # twice is harmless
            token = screen.listen(desktop, lambda *report: None, selections=True)
            facts["events"] = token[2]
            screen.unlisten(token)
            facts["kinds"] = (crew_screen.kind_of(desktop), crew_screen.forms_button(desktop))
            screen.close()
            facts["page"] = crew_screen.page_of(desktop)
        except Exception as error:
            facts["error"] = repr(error)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(30)
    check(f"nothing failed: {facts.get('error')}", "error" not in facts and not thread.is_alive())
    check("a process can be hooked, for all its news and for its buttons only",
          facts["hooks"] == len(crew_screen.HOOKED) and facts["button hooks"] == 1)
    check("a plain window is also asked for the entries that are chosen",
          facts["events"] == (crew_screen.INVOKED, crew_screen.SELECTED))
    check("a window that is no browser's is a plain one, and no Windows Forms button",
          facts["kinds"] == ("plain", None))
    check("waiting for news comes back in time", facts["pump"] < 2)
    check("a window that is no page is nobody's page", facts["page"] == 0)
    check("you were at the keyboard or the mouse some time ago", crew_screen.idle_seconds() >= 0)
    check("the mouse is somewhere", len(crew_screen.mouse()) == 2)


def test_the_listeners_start_in_a_program_that_never_used_them_before():
    """The watch's thread can be the first of all to load comtypes (nothing else in Buddy asked Windows for a
    thing on the screen yet). Loading it sets the thread up the wrong way: the watch must get over that."""
    pytest.importorskip("comtypes")
    code = ("import sys, threading; sys.path.insert(0, sys.argv[1]); import crew_screen\n"
            "said = []\n"
            "def work():\n"
            "    try:\n"
            "        screen = crew_screen.Screen(lambda hwnd: None)\n"
            "        screen.unlisten(screen.listen(crew_screen.user32.GetDesktopWindow(), lambda *report: None, selections=True))\n"
            "        screen.close(); said.append('ok')\n"
            "    except Exception as error:\n"
            "        said.append(repr(error))\n"
            "thread = threading.Thread(target=work); thread.start(); thread.join(60); print(said)\n")
    done = subprocess.run([sys.executable, "-c", code, str(ROOT / "keyit")], capture_output=True, text=True, timeout=120,
                          creationflags=0x08000000)
    check(f"a thread that loads comtypes itself can listen: {done.stdout.strip()} {done.stderr.strip()[-300:]}",
          done.stdout.strip() == "['ok']")


class FakeIcon:
    def __init__(self):
        self.icon, self.title, self.menus = None, "", 0

    def update_menu(self):
        self.menus += 1


def test_the_button_shows_the_switch():
    switch = {"on": True, "flips": 0}
    button = crew_button.Button(is_on=lambda: switch["on"], flip=lambda: switch.update(flips=switch["flips"] + 1))
    button.show()                                                 # no icon yet: nothing to do, no error
    button.icon, button.shown = FakeIcon(), True
    button.show()
    check("nothing changed: the icon is left alone", button.icon.menus == 0)
    switch["on"] = False                                          # switched off in Settings, or by --pointers
    button.show()
    check("a change made elsewhere shows on the button", button.icon.title == crew_button.tip(False) and button.icon.menus == 1)
    button._clicked()
    check("a click asks Buddy to flip the switch; the button itself changes nothing", switch == {"on": False, "flips": 1})
    check("the words say what a click does", "OFF" in crew_button.tip(False) and "turn on" in crew_button.tip(False)
          and "ON" in crew_button.tip(True) and "turn off" in crew_button.tip(True))
    on, off = crew_button.button_image(True), crew_button.button_image(False)
    check("green arrow = on, grey arrow with a red bar = off",
          on.getpixel((20, 25))[:3] == (0x22, 0xC5, 0x5E) and off.getpixel((20, 25))[:3] == (0x9C, 0xA3, 0xAF)
          and off.getpixel((33, 33))[:3] == (0xEF, 0x44, 0x44) and on.size == off.size == (64, 64))


def test_a_click_that_fails_does_not_break_the_button():
    def broken():
        raise RuntimeError("Buddy is quitting")

    crew_button.Button(is_on=lambda: True, flip=broken)._clicked()


# ----------------------------------------------------------------------------------------- inside Buddy
HOST = (ROOT / "buddy_host.py").read_text(encoding="utf-8")
GUI = (ROOT / "keyit" / "keyit_gui.py").read_text(encoding="utf-8")


def method(name):
    """The text of one method of BuddyApp, without importing the app (it needs a screen)."""
    app = next(node for node in ast.parse(HOST).body if isinstance(node, ast.ClassDef) and node.name == "BuddyApp")
    return ast.get_source_segment(HOST, next(node for node in app.body
                                             if isinstance(node, ast.FunctionDef) and node.name == name))


def test_it_is_off_until_you_switch_it_on():
    import keyit_buddy

    check("the watch, its button and the list of tasks to skip are settings, all off or empty at first",
          keyit_buddy.DEFAULTS["crew_tasks"] is False and keyit_buddy.DEFAULTS["crew_button"] is False
          and keyit_buddy.DEFAULTS["crew_tasks_ignored"] == [])
    check("Buddy starts them from those settings",
          'self.set_tasks(self.settings.get("crew_tasks", False))' in HOST
          and 'self.set_pointers_button(self.settings.get("crew_button", False))' in HOST)
    check("Settings has a tick box for each",
          "command=self._on_tasks_toggled" in GUI and "command=self._on_crew_button_toggled" in GUI
          and 'self.host.set_tasks(self.settings["crew_tasks"])' in GUI
          and 'self.host.set_pointers_button(self.settings["crew_button"])' in GUI)


def test_buddy_runs_the_watch_itself():
    check("the watch is one more source of the crew", "sources.append(self.task_watch)" in method("set_crew"))
    check("the pointers' switch reaches the watch, which goes on tracing", "self.task_watch.set_pointers(enabled)" in method("set_crew"))
    check("the watch gets the tasks you do not want followed", 'ignored=self.settings.get("crew_tasks_ignored") or ()' in method("set_tasks"))
    check("a watch that cannot start does not stop Buddy", "except Exception:" in method("set_tasks"))
    check("the button flips the same switch as Settings and --pointers, on Buddy's own thread",
          "flip=lambda: self.post(self._flip_pointers)" in method("set_pointers_button")
          and "self.set_pointers(not self.settings.get(\"crew_enabled\", True)" in method("_flip_pointers"))
    quitting = method("quit")
    check("quitting stops the button and the watch",
          "self.pointers_button and self.pointers_button.stop()" in quitting and "self.task_watch and self.task_watch.stop()" in quitting)


def test_only_what_buddy_ships_is_needed():
    """Buddy.exe brings Python's own modules and the libraries of requirements.txt, nothing else."""
    allowed = {"base64", "collections", "ctypes", "datetime", "hashlib", "json", "logging", "os", "pathlib", "queue",
               "re", "socket", "struct", "subprocess", "threading", "time", "urllib", "PIL", "pystray",
               "crew_browser", "crew_screen", "keyit_core", "comtypes"}
    for name in ("crew_tasks.py", "crew_browser.py", "crew_screen.py", "crew_button.py"):
        tree = ast.parse((ROOT / "keyit" / name).read_text(encoding="utf-8"))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                used.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                used.add((node.module or "").split(".")[0])
        check(f"{name} needs nothing new: {sorted(used - allowed)}", used <= allowed)


def test_a_point_off_every_screen_is_not_on_top(monkeypatch):
    # Windows names the window that lies at a point off the screen all the same: a browser kept as a strip
    # at the screen edge had its presses told "at (2892, 493)" on a screen 2560 wide
    asked = []
    monkeypatch.setattr(crew_browser.user32, "WindowFromPoint", lambda point: asked.append(point) or 7)
    monkeypatch.setattr(crew_browser.user32, "GetAncestor", lambda hwnd, flag: 7)
    check("off every screen: not seen, and Windows is not even asked which window lies there",
          crew_browser.on_top(7, (-32000, -32000)) is False and not asked)
    check("on the screen: the window Windows names there", crew_browser.on_top(7, (40, 40)) is True and len(asked) == 1)
