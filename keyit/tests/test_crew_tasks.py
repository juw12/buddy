"""The crew's task watch (crew_tasks.py), the steered browser's clicks (crew_browser.py) and the pointers'
button (crew_button.py). Nothing real is started, traced or shown: processes, windows, the browser and the
taskbar icon are stand-ins."""

import ast
import json
import os
import socket
import struct
import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crew_browser  # noqa: E402
import crew_button  # noqa: E402
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
          len(posted) == 1 and posted[0]["say"] == "Typing in a box" and set(posted[0]) == {"agent", "team", "id", "x", "y", "state", "say"})
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
    watch.tags[6000] = (watch.tags[6000][0], watch.tags[6000][1], time.monotonic() + 5)   # tagged after that list began
    watch.look_at_processes()
    check("a late list does not end a task a window just started", 6000 in watch.tags and "T\\A" in watch.followed)
    watch.tags[6000] = (watch.tags[6000][0], watch.tags[6000][1], time.monotonic() - 5)
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
    while time.monotonic() < until and not (watch.processes_ready.is_set() and len(watch.threads) == 3):
        time.sleep(0.05)
    check("three threads: the task list, the processes, the windows",
          sorted(thread.name for thread in watch.threads) == ["BuddyTaskList", "BuddyTaskProcesses", "BuddyTaskWindows"])
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
    allowed = {"base64", "collections", "ctypes", "datetime", "hashlib", "json", "logging", "os", "pathlib", "re",
               "socket", "struct", "subprocess", "threading", "time", "urllib", "PIL", "pystray",
               "crew_browser", "keyit_core"}
    for name in ("crew_tasks.py", "crew_browser.py", "crew_button.py"):
        tree = ast.parse((ROOT / "keyit" / name).read_text(encoding="utf-8"))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                used.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                used.add((node.module or "").split(".")[0])
        check(f"{name} needs nothing new: {sorted(used - allowed)}", used <= allowed)
