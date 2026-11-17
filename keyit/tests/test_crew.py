"""Buddy's crew: the extra pointers for other AI agents (buddy_crew.py) and where their news comes from
(crew_sources.py). No window is opened here: pointers, the desktop and the clock are stand-ins."""

import datetime
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buddy_crew  # noqa: E402
import crew_sources  # noqa: E402
from buddy_crew import Crew, Desktop, normalize_event  # noqa: E402
from crew_sources import describe, program_of  # noqa: E402


class FakePointer:
    def __init__(self):
        self.calls = []
        self.style = None
        self.on_screen = False

    def show(self, text, target, hop=False, enter_from=None):
        self.calls.append(("show", text, target, hop, enter_from))
        self.on_screen = True

    def leave(self):
        self.calls.append(("leave",))
        self.on_screen = False

    def set_style(self, color, icon):
        self.style = (color, icon)

    @property
    def text(self):
        shows = [call for call in self.calls if call[0] == "show"]
        return shows[-1][1] if shows else None


class FakeDesktop:
    def spot(self, slot, point=None, file_name="", window="", team=""):
        return point or (1000, 100 + slot * 50)

    def entrance(self, slot):
        return 1900, 100 + slot * 50


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CrewCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.pointers = []
        self.crew = self.make_crew()

    def make_crew(self, **options):
        def pointer():
            self.pointers.append(FakePointer())
            return self.pointers[-1]
        return Crew(pointer_factory=pointer, desktop=FakeDesktop(), clock=self.clock, **options)

    def post(self, agent, say="Reading a file", crew=None, **more):
        crew = crew or self.crew
        crew.post(dict({"agent": agent, "say": say}, **more))
        crew.tick()


class NormalizeTests(unittest.TestCase):
    def test_an_event_needs_an_agent(self):
        self.assertIsNone(normalize_event({"say": "hello"}))
        self.assertIsNone(normalize_event({"agent": "   "}))
        self.assertIsNone(normalize_event("explorer"))
        self.assertIsNone(normalize_event(None))

    def test_only_the_agent_is_needed(self):
        event = normalize_event({"agent": "explorer"})
        self.assertEqual((event["agent"], event["state"], event["say"], event["key"]),
                         ("explorer", "working", "", "/explorer"))
        self.assertIsNone(event["color"])
        self.assertIsNone(event["icon"])
        self.assertIsNone(event["point"])

    def test_text_is_one_short_line(self):
        event = normalize_event({"agent": "a" * 60, "say": "line one\nline two " + "x" * 80})
        self.assertEqual(len(event["agent"]), buddy_crew.NAME_CHARS)
        self.assertEqual(len(event["say"]), buddy_crew.SAY_CHARS)
        self.assertNotIn("\n", event["say"])
        self.assertTrue(event["say"].endswith("\u2026"))

    def test_what_is_not_understood_is_dropped_not_guessed(self):
        event = normalize_event({"agent": "x", "state": "exploding", "color": "red", "icon": "rocket",
                                 "x": "left", "y": 4})
        self.assertEqual(event["state"], "working")
        self.assertIsNone(event["color"])
        self.assertIsNone(event["icon"])
        self.assertIsNone(event["point"])

    def test_colour_icon_point_and_key(self):
        event = normalize_event({"name": "Fixer", "team": "OpenCode", "color": "#22C55E", "icon": "star",
                                 "x": 800, "y": "400", "state": "done"})
        self.assertEqual((event["agent"], event["key"]), ("Fixer", "opencode/fixer"))
        self.assertEqual((event["color"], event["icon"], event["point"], event["state"]),
                         ("#22c55e", "star", (800, 400), "done"))
        self.assertEqual(normalize_event({"agent": "x", "id": "session-7"})["key"], "session-7")


class CrewTests(CrewCase):
    def test_a_working_agent_gets_a_pointer_that_flies_in_and_says_what_it_does(self):
        self.post("explorer", "Reading README.md", team="OpenCode")
        self.assertEqual(self.crew.members(),
                         [("explorer", "OpenCode", "working", "Reading README.md", buddy_crew.CREW_COLORS[0])])
        self.assertEqual(self.pointers[0].calls,
                         [("show", "explorer: Reading README.md", (1000, 100), True, (1900, 100))])
        self.assertEqual(self.pointers[0].style, (buddy_crew.CREW_COLORS[0], buddy_crew.CREW_ICON))

    def test_after_a_quiet_while_only_the_name_stays_and_news_brings_the_words_back(self):
        self.post("explorer", "Reading README.md")
        self.clock.now += buddy_crew.QUIET_S + 1
        self.crew.tick()
        self.assertEqual(self.pointers[0].calls[-1], ("show", "explorer", (1000, 100), False, None))
        self.post("explorer", "Editing notes.md")
        self.assertEqual(self.pointers[0].calls[-1], ("show", "explorer: Editing notes.md", (1000, 100), True, None))

    def test_done_keeps_its_words_until_the_pointer_leaves(self):
        self.post("explorer", "Reading README.md")
        self.post("explorer", state="done", say="")
        self.clock.now += buddy_crew.DONE_LINGER_S - 1
        self.crew.tick()
        self.assertEqual(self.pointers[0].text, "explorer: done \u2713")

    def test_nothing_new_means_nothing_is_redrawn(self):
        self.post("explorer", "Reading README.md")
        self.post("explorer", "Reading README.md")
        self.crew.tick()
        self.assertEqual(len(self.pointers[0].calls), 1)

    def test_a_new_step_hops_and_does_not_fly_in_again(self):
        self.post("explorer", "Reading README.md")
        self.post("explorer", "Editing notes.md")
        self.assertEqual(self.pointers[0].calls[-1], ("show", "explorer: Editing notes.md", (1000, 100), True, None))

    def test_an_event_without_words_keeps_the_last_words(self):
        self.post("explorer", "Reading README.md")
        self.post("explorer", "")
        self.assertEqual(self.crew.members()[0][3], "Reading README.md")

    def test_every_agent_has_its_own_colour_and_place(self):
        for name in ("orchestrator", "explorer", "fixer"):
            self.post(name)
        colours = [member[4] for member in self.crew.members()]
        self.assertEqual(colours, list(buddy_crew.CREW_COLORS[:3]))
        self.assertEqual([pointer.calls[0][2] for pointer in self.pointers], [(1000, 100), (1000, 150), (1000, 200)])

    def test_the_users_own_buddy_colour_is_never_handed_out(self):
        crew = self.make_crew(avoid_color=buddy_crew.CREW_COLORS[0].upper())
        for index in range(buddy_crew.MAX_MEMBERS):
            self.post(f"agent{index}", crew=crew)
        self.assertNotIn(buddy_crew.CREW_COLORS[0], [member[4] for member in crew.members()])

    def test_a_colour_or_icon_in_the_event_or_in_the_settings_wins(self):
        crew = self.make_crew(looks={"explorer": {"color": "#112233", "icon": "star"},
                                     "opencode/fixer": {"color": "#445566"},
                                     "oracle": {"color": "not a colour", "icon": "rocket"}})
        self.post("explorer", crew=crew)
        self.post("fixer", crew=crew, team="OpenCode")
        self.post("oracle", crew=crew)
        self.post("painter", crew=crew, color="#abcdef", icon="heart")
        looks = {member[0]: member[4] for member in crew.members()}
        self.assertEqual((looks["explorer"], looks["fixer"], looks["painter"]), ("#112233", "#445566", "#abcdef"))
        self.assertIn(looks["oracle"], buddy_crew.CREW_COLORS)
        self.assertEqual(self.pointers[0].style, ("#112233", "star"))
        self.assertEqual(self.pointers[2].style[1], buddy_crew.CREW_ICON)
        self.assertEqual(self.pointers[3].style, ("#abcdef", "heart"))

    def test_two_agents_with_one_name_are_told_apart_by_folder(self):
        self.post("orchestrator", "Reading a.md", id="s1", where="titip")
        self.post("orchestrator", "Reading b.md", id="s2", where="buddy")
        self.assertEqual([pointer.text for pointer in self.pointers],
                         ["orchestrator (titip): Reading a.md", "orchestrator (buddy): Reading b.md"])

    def test_two_agents_with_one_name_in_one_folder_are_numbered(self):
        self.post("orchestrator", "Reading a.md", id="s1", where="buddy")
        self.post("orchestrator", "Reading b.md", id="s2", where="buddy")
        self.assertEqual([pointer.text for pointer in self.pointers],
                         ["orchestrator 1: Reading a.md", "orchestrator 2: Reading b.md"])

    def test_done_is_said_then_the_pointer_leaves(self):
        self.post("explorer", "Reading README.md")
        self.post("explorer", "", state="done")
        self.assertEqual(self.pointers[0].text, "explorer: done \u2713")
        self.clock.now += buddy_crew.DONE_LINGER_S - 1
        self.crew.tick()
        self.assertTrue(self.pointers[0].on_screen)
        self.clock.now += 2
        self.crew.tick()
        self.assertFalse(self.pointers[0].on_screen)
        self.assertEqual(self.crew.members(), [])

    def test_done_or_leave_from_an_agent_never_shown_shows_nothing(self):
        self.post("explorer", "", state="done")
        self.post("fixer", "", state="leave")
        self.assertEqual(self.crew.members(), [])
        self.assertEqual(self.pointers, [])

    def test_leave_takes_the_pointer_away_at_once(self):
        self.post("explorer")
        self.post("explorer", "", state="leave")
        self.assertFalse(self.pointers[0].on_screen)

    def test_an_agent_that_goes_silent_leaves(self):
        self.post("explorer")
        self.clock.now += buddy_crew.SILENT_LEAVE_S - 1
        self.crew.tick()
        self.assertTrue(self.pointers[0].on_screen)
        self.clock.now += 2
        self.crew.tick()
        self.assertFalse(self.pointers[0].on_screen)

    def test_an_agent_that_comes_back_looks_the_same_and_reuses_a_pointer(self):
        self.post("orchestrator")
        self.post("explorer")
        colour = self.crew.members()[1][4]
        self.post("explorer", "", state="leave")
        self.post("fixer")
        self.post("explorer", "Back again")
        looks = {member[0]: member[4] for member in self.crew.members()}
        self.assertEqual(looks["explorer"], colour)
        self.assertNotEqual(looks["fixer"], colour)
        self.assertEqual(len(self.pointers), 3)

    def test_no_more_than_eight_and_a_finished_one_makes_room(self):
        for index in range(buddy_crew.MAX_MEMBERS):
            self.post(f"agent{index}")
        self.post("late")
        self.assertEqual(len(self.crew.members()), buddy_crew.MAX_MEMBERS)
        self.assertNotIn("late", [member[0] for member in self.crew.members()])
        self.post("agent3", "", state="done")
        self.post("late")
        names = [member[0] for member in self.crew.members()]
        self.assertIn("late", names)
        self.assertNotIn("agent3", names)
        self.assertEqual(len(names), buddy_crew.MAX_MEMBERS)

    def test_pointers_hide_for_a_screenshot_and_come_back_without_flying_in(self):
        self.post("explorer", "Reading README.md")
        self.crew.clear_for_screenshot(seconds=1.0)
        self.assertFalse(self.pointers[0].on_screen)
        self.post("explorer", "Editing notes.md")
        self.assertFalse(self.pointers[0].on_screen)
        self.clock.now += 1.1
        self.crew.tick()
        self.assertEqual(self.pointers[0].calls[-1][:3], ("show", "explorer: Editing notes.md", (1000, 100)))
        self.assertIsNone(self.pointers[0].calls[-1][4])

    def test_a_given_point_is_where_the_pointer_goes(self):
        self.post("vector", "Drawing the bracket", x=640, y=360)
        self.assertEqual(self.pointers[0].calls[0][2], (640, 360))

    def test_close_takes_everyone_off_the_screen(self):
        self.post("explorer")
        self.post("fixer")
        self.crew.close()
        self.assertEqual(self.crew.members(), [])
        self.assertFalse(any(pointer.on_screen for pointer in self.pointers))


class DesktopTests(unittest.TestCase):
    AREA = (0, 0, 1920, 1040)

    def desktop(self, windows=(), on_top=True, clock=None):
        self.listed = 0

        def list_windows():
            self.listed += 1
            return list(windows)
        return Desktop(windows=list_windows, on_top_at=lambda hwnd, x, y: on_top, area=lambda: self.AREA,
                       clock=clock or Clock())

    def test_the_dock_is_a_column_on_the_right_that_stays_on_the_screen(self):
        desktop = self.desktop()
        self.assertEqual(desktop.dock(0), (1920 - buddy_crew.DOCK_FROM_RIGHT, buddy_crew.DOCK_FROM_TOP))
        self.assertEqual(desktop.dock(2)[1] - desktop.dock(1)[1], buddy_crew.SLOT_HEIGHT)
        self.assertLessEqual(desktop.dock(40)[1], 1040 - 60)
        self.assertEqual(desktop.entrance(1), (1890, desktop.dock(1)[1]))

    def test_the_window_with_the_file_in_its_title_is_where_the_work_is(self):
        windows = [(1, "Chrome", (0, 0, 900, 800)), (2, "notes.md - Editor", (200, 100, 1200, 900))]
        desktop = self.desktop(windows)
        self.assertEqual(desktop.spot(0, file_name=r"C:\work\notes.md"), (200 + 360, 100 + 18))
        self.assertEqual(desktop.spot(1, file_name="notes.md")[1], 100 + 18 + buddy_crew.WINDOW_SLOT_HEIGHT)

    def test_a_covered_window_or_no_window_means_the_dock(self):
        windows = [(2, "notes.md - Editor", (200, 100, 1200, 900))]
        self.assertEqual(self.desktop(windows, on_top=False).spot(0, file_name="notes.md"), self.desktop().dock(0))
        self.assertEqual(self.desktop(windows).spot(0, file_name="other.md"), self.desktop().dock(0))
        self.assertEqual(self.desktop(windows).spot(3), self.desktop().dock(3))

    def test_a_short_file_name_is_not_looked_for(self):
        self.assertEqual(buddy_crew.title_wanted(("a.py", "", "")), [])
        self.assertEqual(buddy_crew.title_wanted((r"src\main.py", "Editor", "OpenCode")),
                         ["main.py", "editor", "opencode"])

    def test_the_file_counts_for_more_than_the_team(self):
        windows = [(1, "OpenCode", (0, 0, 900, 800)), (2, "README.md - Editor", (200, 100, 1200, 900))]
        self.assertEqual(buddy_crew.pick_window(["readme.md", "opencode"], windows)[0], 2)
        self.assertEqual(buddy_crew.pick_window(["missing.md", "opencode"], windows)[0], 1)
        self.assertIsNone(buddy_crew.pick_window(["missing.md"], windows))

    def test_a_given_point_wins_and_windows_are_listed_once_a_second(self):
        clock = Clock()
        desktop = self.desktop([(1, "OpenCode", (0, 0, 900, 800))], clock=clock)
        self.assertEqual(desktop.spot(0, point=(5, 6), team="OpenCode"), (5, 6))
        self.assertEqual(self.listed, 0)
        desktop.spot(0, team="OpenCode")
        desktop.spot(1, team="OpenCode")
        self.assertEqual(self.listed, 1)
        clock.now += 1.5
        desktop.spot(0, team="OpenCode")
        self.assertEqual(self.listed, 2)


class WordsTests(unittest.TestCase):
    """What is shown of a tool call: the file's name and the program's name, never the rest."""

    def test_files_are_shown_by_name_only(self):
        self.assertEqual(describe("Read", {"file_path": r"C:\Users\me\secret-project\README.md"}),
                         ("Reading README.md", "README.md"))
        self.assertEqual(describe("edit", {"filePath": "/home/me/app/main.py"}), ("Editing main.py", "main.py"))
        self.assertEqual(describe("Write", {"path": "notes.md", "content": "password=hunter2"}),
                         ("Writing notes.md", "notes.md"))
        self.assertEqual(describe("Edit", {}), ("Editing a file", ""))

    def test_searches_never_show_what_is_searched_for(self):
        self.assertEqual(describe("Grep", {"pattern": "API_KEY=sk-123", "path": "src"})[0], "Searching the files")
        self.assertEqual(describe("glob", {"pattern": "**/*.pem"})[0], "Looking through the files")
        self.assertEqual(describe("WebFetch", {"url": "https://host/?token=abc"})[0], "Reading a web page")

    def test_a_command_shows_only_its_program(self):
        cases = {
            "cd repo && git push https://ghp_SECRET@github.com/me/repo.git": "git push",
            "curl -H 'Authorization: Bearer sk-SECRET' https://api.host/v1": "curl",
            "API_KEY=sk-SECRET python deploy.py --password hunter2": "python",
            "export TOKEN='a;SECRET' && npm run build": "npm run",
            'echo "one; SECRET two" | clip': "clip",
            "$env:TOKEN = 'SECRET'; pytest -q": "pytest",
            '$py = "C:\\Python313\\python.exe"; & $py -m pytest': "",
            '& "C:\\Program Files\\Git\\bin\\git.exe" status': "git status",
            "/usr/bin/python3 -c \"print('SECRET')\"": "python3",
            "git -c http.extraheader=SECRET fetch": "git",
            "sudo docker run -e PASS=SECRET image": "docker run",
            "": "",
        }
        for command, shown in cases.items():
            with self.subTest(command=command):
                self.assertEqual(program_of(command), shown)
                self.assertNotIn("SECRET", describe("bash", {"command": command})[0])

    def test_a_command_given_as_a_list(self):
        self.assertEqual(program_of(["bash", "-lc", "cd app && npm test --token SECRET"]), "npm test")
        self.assertEqual(program_of(["git", "status"]), "git status")
        self.assertEqual(describe("shell", {"command": ["pwsh", "-Command", "Get-ChildItem C:\\secret"]})[0],
                         "Running Get-ChildItem")
        self.assertEqual(describe("shell", {})[0], "Running a command")

    def test_other_tools(self):
        self.assertEqual(describe("Task", {"subagent_type": "explorer", "prompt": "SECRET"})[0],
                         "Handing work to explorer")
        self.assertEqual(describe("mcp__github__create_issue", {"body": "SECRET"})[0], "Using github create_issue")
        self.assertEqual(describe("TodoWrite", {})[0], "Updating its plan")
        self.assertEqual(describe("some_new_tool", {"q": "SECRET"})[0], "Using some_new_tool")
        self.assertEqual(describe("", None)[0], "Working")


class FilesCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    @staticmethod
    def add(path, *records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as handle:
            for record in records:
                handle.write((record if isinstance(record, str) else json.dumps(record)).encode("utf-8") + b"\n")

    @staticmethod
    def follow(find, parse):
        source = crew_sources.TranscriptSource(find, parse)
        source.SCAN_EVERY_S = 0.0
        return source


class GrowingFileTests(FilesCase):
    def test_only_whole_new_lines_are_read(self):
        path = self.root / "log.jsonl"
        path.write_bytes(b"old line\n")
        growing = crew_sources.GrowingFile(str(path), path.stat().st_size)
        self.assertEqual(growing.new_lines(), [])
        with open(path, "ab") as handle:
            handle.write(b"one\ntw")
        self.assertEqual(growing.new_lines(), [b"one"])
        with open(path, "ab") as handle:
            handle.write(b"o\n\n")
        self.assertEqual(growing.new_lines(), [b"two"])

    def test_a_file_that_started_over_is_read_from_the_top(self):
        path = self.root / "log.jsonl"
        path.write_bytes(b"a long old line\n")
        growing = crew_sources.GrowingFile(str(path), path.stat().st_size)
        path.write_bytes(b"new\n")
        self.assertEqual(growing.new_lines(), [b"new"])

    def test_after_a_long_gap_only_the_newest_part_is_read(self):
        path = self.root / "log.jsonl"
        line = b"x" * 99 + b"\n"
        path.write_bytes(line * (crew_sources.CATCH_UP_BYTES // 100 + 50) + b"last\n")
        lines = crew_sources.GrowingFile(str(path), 0).new_lines()
        self.assertLess(len(lines), crew_sources.CATCH_UP_BYTES // 100 + 2)
        self.assertEqual(lines[-1], b"last")
        self.assertTrue(all(len(one) == 99 for one in lines[:-1]))

    def test_a_missing_file_is_no_news(self):
        self.assertEqual(crew_sources.GrowingFile(str(self.root / "none.jsonl"), 0).new_lines(), [])


class InboxTests(FilesCase):
    def test_lines_from_before_are_not_replayed_and_new_ones_are_read(self):
        inbox = self.root / "crew" / "inbox.jsonl"
        crew_sources.post_event({"agent": "old"}, inbox)
        source = crew_sources.InboxSource(inbox)
        self.assertEqual(source.poll(), [])
        crew_sources.post_event({"agent": "vector", "say": "Drawing the br\u00e4cket"}, inbox)
        self.add(inbox, "not json", "[1, 2]")
        crew_sources.post_event({"agent": "fixer", "state": "done"}, inbox)
        self.assertEqual(source.poll(), [{"agent": "vector", "say": "Drawing the br\u00e4cket"},
                                         {"agent": "fixer", "state": "done"}])
        self.assertEqual(source.poll(), [])

    def test_the_inbox_may_not_exist_yet(self):
        inbox = self.root / "crew" / "inbox.jsonl"
        source = crew_sources.InboxSource(inbox)
        self.assertEqual(source.poll(), [])
        crew_sources.post_event({"agent": "first"}, inbox)
        self.assertEqual(source.poll(), [{"agent": "first"}])

    def test_an_inbox_that_grew_too_big_is_emptied(self):
        inbox = self.root / "inbox.jsonl"
        inbox.write_bytes(b"x" * (crew_sources.INBOX_MAX_BYTES + 1))
        source = crew_sources.InboxSource(inbox)
        self.assertEqual(inbox.stat().st_size, 0)
        crew_sources.post_event({"agent": "next"}, inbox)
        self.assertEqual(source.poll(), [{"agent": "next"}])


def claude_line(session, *blocks, stop=None, cwd=r"C:\work\titip", **more):
    return dict({"type": "assistant", "sessionId": session, "cwd": cwd,
                 "message": {"role": "assistant", "content": list(blocks), "stop_reason": stop}}, **more)


def tool_use(name, **arguments):
    return {"type": "tool_use", "id": "toolu_1", "name": name, "input": arguments}


class ClaudeCodeTests(FilesCase):
    def source(self):
        return self.follow(lambda: crew_sources.claude_files(str(self.root)), crew_sources.claude_events)

    def test_history_is_not_news_and_a_new_step_is(self):
        session = self.root / "C--work-titip" / "abc.jsonl"
        self.add(session, claude_line("abc", tool_use("Read", file_path=r"C:\work\titip\old.md")))
        source = self.source()
        self.assertEqual(source.poll(), [])
        self.add(session,
                 {"type": "user", "message": {"content": "password is SECRET"}},
                 claude_line("abc", {"type": "text", "text": "I will edit it"},
                             tool_use("Edit", file_path=r"C:\work\titip\app.py", old_string="SECRET")))
        self.assertEqual(source.poll(), [{"id": "claude:abc", "agent": "Claude", "team": "Claude Code", "where": "titip",
                                          "say": "Editing app.py", "file": "app.py", "state": "working"}])
        self.add(session, claude_line("abc", {"type": "text", "text": "All done"}, stop="end_turn"))
        self.assertEqual(source.poll(), [{"id": "claude:abc", "agent": "Claude", "team": "Claude Code",
                                          "where": "titip", "state": "done"}])

    def test_a_session_started_later_is_read_from_its_first_line(self):
        source = self.source()
        self.assertEqual(source.poll(), [])
        self.add(self.root / "proj" / "new.jsonl", claude_line("new", tool_use("Bash", command="git status")))
        events = source.poll()
        self.assertEqual([(event["agent"], event["say"]) for event in events], [("Claude", "Running git status")])

    def test_a_sub_agent_is_its_own_member_named_by_its_kind(self):
        main = self.root / "proj" / "abc.jsonl"
        self.add(main, claude_line("abc", tool_use("Task", subagent_type="explorer")))
        source = self.source()
        source.poll()
        helper = self.root / "proj" / "abc" / "subagents" / "agent-7f.jsonl"
        helper.parent.mkdir(parents=True)
        helper.with_suffix(".meta.json").write_text(json.dumps({"agentType": "explorer", "description": "look"}),
                                                    encoding="utf-8")
        self.add(helper, claude_line("abc", tool_use("Grep", pattern="SECRET"), isSidechain=True, agentId="7f"))
        events = source.poll()
        self.assertEqual(events, [{"id": "claude:abc:7f", "agent": "explorer", "team": "Claude Code",
                                   "where": "titip", "say": "Searching the files", "file": "", "state": "working"}])

    def test_lines_that_are_not_an_assistant_step_say_nothing(self):
        self.assertEqual(crew_sources.claude_events({"type": "user", "message": {"content": []}}, {}), [])
        self.assertEqual(crew_sources.claude_events({"type": "assistant", "message": "odd"}, {}), [])
        self.assertEqual(crew_sources.claude_events(claude_line("s", {"type": "text", "text": "hm"}), {}), [])


class CodexTests(FilesCase):
    DAY = datetime.date(2026, 11, 17)

    def rollout(self, day=None):
        day = day or self.DAY
        return self.root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / "rollout-1.jsonl"

    def test_only_today_and_yesterday_are_looked_at(self):
        self.add(self.rollout(), {})
        self.add(self.rollout(self.DAY - datetime.timedelta(days=1)), {})
        self.add(self.rollout(self.DAY - datetime.timedelta(days=2)), {})
        found = [path for path, _ in crew_sources.codex_files(str(self.root), today=self.DAY)]
        self.assertEqual(found, [str(self.rollout()), str(self.rollout(self.DAY - datetime.timedelta(days=1)))])

    def test_calls_patches_and_the_end_of_a_task(self):
        source = self.follow(lambda: crew_sources.codex_files(str(self.root), today=self.DAY),
                             crew_sources.codex_events)
        source.poll()
        patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old SECRET\n+new\n*** End Patch"
        self.add(self.rollout(),
                 {"type": "session_meta", "payload": {"id": "1", "cwd": "/home/me/buddy"}},
                 {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "SECRET"}},
                 {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments":
                                                       json.dumps({"command": ["bash", "-lc", "git status"]})}},
                 {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                                                       "input": patch}},
                 {"type": "response_item", "payload": {"type": "local_shell_call",
                                                       "action": {"command": ["pytest", "-q"]}}},
                 {"type": "event_msg", "payload": {"type": "task_complete"}})
        events = source.poll()
        self.assertEqual([(event.get("say"), event.get("file"), event["state"]) for event in events],
                         [("Running git status", "", "working"), ("Editing app.py", "app.py", "working"),
                          ("Running pytest", "", "working"), (None, None, "done")])
        self.assertEqual({(event["id"], event["agent"], event["team"], event["where"]) for event in events},
                         {("codex:rollout-1", "Codex", "Codex", "buddy")})


class OpenCodeCase(FilesCase):
    NOW_S = 1_800_000_000.0

    def setUp(self):
        super().setUp()
        self.path = self.root / "open code" / "opencode.db"
        self.path.parent.mkdir()
        self.database = sqlite3.connect(self.path)
        self.addCleanup(self.database.close)

    def source(self):
        source = crew_sources.OpenCodeSource(self.path, clock=lambda: self.NOW_S)
        source.POLL_EVERY_S = -1.0
        self.addCleanup(source.close)
        return source

    def run_sql(self, statement, *values):
        self.database.execute(statement, values)
        self.database.commit()


class OpenCodeMessagesTests(OpenCodeCase):
    """The layout of the OpenCode build on the PC this was written on: tool calls inside session_message rows."""

    def setUp(self):
        super().setUp()
        self.database.executescript(
            "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, directory TEXT);"
            "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,"
            " time_created INTEGER, time_updated INTEGER, data TEXT);")
        self.run_sql("INSERT INTO session_v2 VALUES ('ses_1', NULL, 'orchestrator', 'E:\\Projects\\titip')")
        self.run_sql("INSERT INTO session_v2 VALUES ('ses_2', 'ses_1', 'explorer', 'E:\\Projects\\titip')")
        self.now = int(self.NOW_S * 1000)

    def message(self, message_id, session, created, updated, data, kind="assistant"):
        self.run_sql("INSERT OR REPLACE INTO session_message VALUES (?, ?, ?, 0, ?, ?, ?)",
                     message_id, session, kind, created, updated, json.dumps(data))

    @staticmethod
    def tool(call, name, **arguments):
        return {"type": "tool", "id": call, "name": name, "state": {"status": "running", "input": arguments}}

    def test_what_happened_before_is_not_told(self):
        self.message("m0", "ses_1", self.now - 5000, self.now - 4000,
                     {"agent": "orchestrator", "content": [self.tool("c0", "read", filePath="old.md")]})
        self.assertEqual(self.source().poll(), [])

    def test_each_tool_call_is_told_once_and_a_sub_agent_is_its_own_member(self):
        source = self.source()
        self.message("m1", "ses_1", self.now + 10, self.now + 20,
                     {"agent": "orchestrator", "content": [{"type": "reasoning", "text": "SECRET"},
                                                           self.tool("c1", "shell", command="git status")]})
        self.message("m2", "ses_2", self.now + 30, self.now + 40,
                     {"content": [self.tool("c2", "read", filePath=r"E:\Projects\titip\README.md")]})
        events = source.poll()
        self.assertEqual(events, [
            {"id": "opencode:ses_1", "agent": "orchestrator", "team": "OpenCode", "where": "titip",
             "say": "Running git status", "file": "", "state": "working"},
            {"id": "opencode:ses_2", "agent": "explorer", "team": "OpenCode", "where": "titip",
             "say": "Reading README.md", "file": "README.md", "state": "working"}])
        self.assertEqual(source.poll(), [])
        # the same message grows: a second call is added to it
        self.message("m1", "ses_1", self.now + 10, self.now + 60,
                     {"agent": "orchestrator", "content": [self.tool("c1", "shell", command="git status"),
                                                           self.tool("c3", "edit", filePath="app.py")]})
        self.assertEqual([event["say"] for event in source.poll()], ["Editing app.py"])

    def test_the_end_of_a_turn_is_done(self):
        source = self.source()
        self.message("m1", "ses_1", self.now + 10, self.now + 20,
                     {"agent": "orchestrator", "content": [{"type": "text", "text": "All done"}], "finish": "stop"})
        self.message("m2", "ses_2", self.now + 30, self.now + 40, {}, kind="idle")
        self.assertEqual([(event["id"], event["state"]) for event in source.poll()],
                         [("opencode:ses_1", "done"), ("opencode:ses_2", "done")])
        self.assertEqual(source.poll(), [])


class OpenCodePartsTests(OpenCodeCase):
    """The layout of OpenCode 1.2 and later, as documented: one `part` row for every tool call."""

    def setUp(self):
        super().setUp()
        self.database.executescript(
            "CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, directory TEXT);"
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);"
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER,"
            " data TEXT);")
        self.run_sql("INSERT INTO session VALUES ('ses_1', NULL, NULL, '/home/me/buddy')")
        self.run_sql("INSERT INTO message VALUES ('msg_1', 'ses_1', 1, ?)",
                     json.dumps({"role": "assistant", "agent": "build"}))

    def part(self, part_id, data):
        self.run_sql("INSERT OR REPLACE INTO part VALUES (?, 'msg_1', 'ses_1', 1, ?)", part_id, json.dumps(data))

    def test_new_parts_are_told_and_old_ones_are_not(self):
        self.part("prt_001", {"type": "tool", "tool": "read", "state": {"status": "completed",
                                                                         "input": {"filePath": "old.md"}}})
        source = self.source()
        self.assertEqual(source.poll(), [])
        self.part("prt_002", {"type": "tool", "tool": "bash", "callID": "c2",
                              "state": {"status": "running", "input": {"command": "npm test -- --token SECRET"}}})
        self.part("prt_003", {"type": "text", "text": "SECRET"})
        self.part("prt_004", {"type": "step-finish", "reason": "stop"})
        self.assertEqual(source.poll(), [
            {"id": "opencode:ses_1", "agent": "build", "team": "OpenCode", "where": "buddy",
             "say": "Running npm test", "file": "", "state": "working"},
            {"id": "opencode:ses_1", "agent": "build", "team": "OpenCode", "where": "buddy", "state": "done"}])
        self.assertEqual(source.poll(), [])

    def test_a_call_that_is_still_being_written_is_looked_at_once_more(self):
        source = self.source()
        self.assertEqual(source.poll(), [])
        self.part("prt_002", {"type": "tool", "tool": "edit", "state": {"status": "pending", "input": {}}})
        self.assertEqual(source.poll(), [])
        self.part("prt_002", {"type": "tool", "tool": "edit", "state": {"status": "running",
                                                                         "input": {"filePath": "src/app.py"}}})
        self.assertEqual([event["say"] for event in source.poll()], ["Editing app.py"])
        self.assertEqual(source.poll(), [])


class OpenCodeMissingTests(FilesCase):
    def test_no_database_or_an_unknown_one_is_no_news_and_no_error(self):
        source = crew_sources.OpenCodeSource(self.root / "none.db")
        self.assertEqual(source.poll(), [])
        other = self.root / "other.db"
        database = sqlite3.connect(other)
        database.execute("CREATE TABLE something (id TEXT)")
        database.commit()
        database.close()
        source = crew_sources.OpenCodeSource(other)
        self.assertEqual(source.poll(), [])
        source.close()

    def test_the_database_is_found_in_the_home_folder(self):
        self.assertEqual(crew_sources.opencode_database(self.root),
                         self.root / ".local" / "share" / "opencode" / "opencode.db")


class WatcherTests(CrewCase):
    class Source:
        def __init__(self, *batches):
            self.batches = list(batches)
            self.closed = False

        def poll(self):
            return self.batches.pop(0) if self.batches else []

        def close(self):
            self.closed = True

    class Broken:
        def poll(self):
            raise RuntimeError("cannot read")

    def test_news_from_every_source_reaches_the_crew_even_when_one_source_fails(self):
        source = self.Source([{"agent": "explorer", "say": "Reading README.md"}], [{"agent": "fixer"}])
        watcher = crew_sources.CrewWatcher(self.crew, sources=[self.Broken(), source])
        with self.assertLogs(crew_sources.logger, level="ERROR"):
            watcher.step()
        self.assertEqual([member[0] for member in self.crew.members()], ["explorer"])
        with self.assertLogs(crew_sources.logger, level="ERROR"):
            watcher.step()
        self.assertEqual([member[0] for member in self.crew.members()], ["explorer", "fixer"])

    def test_stopping_takes_the_pointers_away_and_closes_the_sources(self):
        source = self.Source([{"agent": "explorer"}])
        watcher = crew_sources.CrewWatcher(self.crew, sources=[source])
        watcher.TICK_S = 0.01
        watcher.start()
        for _ in range(200):
            if self.crew.members():
                break
            time.sleep(0.01)
        self.assertEqual([member[0] for member in self.crew.members()], ["explorer"])
        watcher.stop()
        self.assertEqual(self.crew.members(), [])
        self.assertTrue(source.closed)
        self.assertFalse(self.pointers[0].on_screen)

    def test_the_default_sources_are_the_inbox_and_the_three_agents(self):
        with tempfile.TemporaryDirectory() as home:
            sources = crew_sources.default_sources(home)
            self.assertEqual([type(source).__name__ for source in sources],
                             ["InboxSource", "TranscriptSource", "TranscriptSource", "OpenCodeSource"])
            self.assertEqual(sources[3].path, Path(home) / ".local" / "share" / "opencode" / "opencode.db")
            for source in sources[1:]:
                self.assertEqual(source.poll(), [])


class CrewLineTests(FilesCase):
    """What `Buddy.exe --crew TEXT` understands."""

    def test_the_short_form_and_json(self):
        text = crew_sources.event_from_text
        self.assertEqual(text("explorer: Reading the docs"), {"agent": "explorer", "say": "Reading the docs"})
        self.assertEqual(text("  explorer : Done  "), {"agent": "explorer", "state": "done"})
        self.assertEqual(text("explorer: leave"), {"agent": "explorer", "state": "leave"})
        self.assertEqual(text("explorer"), {"agent": "explorer", "say": ""})
        self.assertEqual(text('{"agent": "fixer", "team": "Vector", "color": "#22c55e"}'),
                         {"agent": "fixer", "team": "Vector", "color": "#22c55e"})

    def test_a_line_without_an_agent_is_refused(self):
        for bad in ("", ": Reading the docs", '{"say": "hello"}', "{not json", '{"agent": ""}'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                crew_sources.event_from_text(bad)

    def test_a_posted_line_becomes_a_pointer(self):
        inbox = self.root / "crew" / "inbox.jsonl"
        source = crew_sources.InboxSource(inbox)
        pointers = []

        def pointer():
            pointers.append(FakePointer())
            return pointers[-1]
        crew = Crew(pointer_factory=pointer, desktop=FakeDesktop(), clock=Clock())
        watcher = crew_sources.CrewWatcher(crew, sources=[source])
        crew_sources.post_event(crew_sources.event_from_text("drafter: Drawing the bracket"), inbox)
        watcher.step()
        self.assertEqual(pointers[0].text, "drafter: Drawing the bracket")
        crew_sources.post_event(crew_sources.event_from_text("drafter: done"), inbox)
        watcher.step()
        self.assertEqual(pointers[0].text, "drafter: done \u2713")


class HandOverTests(unittest.TestCase):
    """'@opencode tidy the notes folder' typed to Buddy opens that agent with the question (keyit_agents)."""

    def setUp(self):
        import keyit_agents
        import keyit_buddy

        self.agents = keyit_agents
        self.buddy_module = keyit_buddy

    def test_only_a_question_for_a_known_agent_is_handed_over(self):
        parse = self.agents.parse
        self.assertEqual(parse("@opencode tidy the notes folder"), ("opencode", "tidy the notes folder"))
        self.assertEqual(parse("  @Claude: fix the\nfailing test "), ("claude", "fix the\nfailing test"))
        self.assertEqual(parse("@codex, add a README"), ("codex", "add a README"))
        for other in ("open notepad", "@opencode", "@opencode   ", "@someone hello", "mail me@codex now", "", None):
            with self.subTest(other=other):
                self.assertIsNone(parse(other))

    def test_the_agent_is_opened_with_the_question_typed_in(self):
        which = {"opencode": r"C:\Apps\opencode.exe", "claude": r"C:\Apps\claude.exe",
                 "codex": r"C:\npm\codex.cmd"}.get
        self.assertEqual(self.agents.command("opencode", 'say "hi" & more', which),
                         [r"C:\Apps\opencode.exe", "--prompt", 'say "hi" & more'])
        self.assertEqual(self.agents.command("claude", "fix it", which), [r"C:\Apps\claude.exe", "fix it"])

    def test_a_question_cannot_become_a_command_for_cmd(self):
        which = {"codex": r"C:\npm\codex.cmd"}.get
        self.assertEqual(self.agents.command("codex", 'say "hi" & del %TEMP% | more\n> x ^', which),
                         [r"C:\npm\codex.cmd", "say hi del TEMP more x"])

    def test_it_starts_in_the_folder_from_the_settings_or_at_home(self):
        started = []

        def start(command, cwd, creationflags):
            started.append((command, cwd))
        which = {"opencode": r"C:\Apps\opencode.exe"}.get
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.agents.launch("opencode", "tidy", folder, which, start), Path(folder))
            self.assertEqual(started[-1], ([r"C:\Apps\opencode.exe", "--prompt", "tidy"], folder))
        for missing in ("", None, r"Z:\no\such\folder"):
            self.assertEqual(self.agents.launch("opencode", "tidy", missing, which, start), Path.home())
        with self.assertRaises(self.agents.NotInstalled):
            self.agents.launch("claude", "tidy", "", which, start)
        self.assertEqual(len(started), 4)

    def buddy(self):
        buddy = types.SimpleNamespace(settings={"agent_folder": ""}, overlay=mock.Mock(), monitor=None,
                                      post=lambda call, *arguments: call(*arguments))
        buddy.overlay.local.return_value = (0, 0)
        return buddy

    def test_buddy_says_where_the_question_went(self):
        buddy = self.buddy()
        with mock.patch.object(self.agents, "launch", return_value=Path(r"C:\Users\me")) as launch:
            self.assertTrue(self.buddy_module.Buddy._hand_to_agent(buddy, "@opencode tidy the notes", (5, 5)))
        launch.assert_called_once_with("opencode", "tidy the notes", "")
        buddy.overlay.say.assert_called_once_with("Handed to OpenCode. It works in me.")
        buddy.overlay.hide_later.assert_called_once()

    def test_buddy_says_so_when_the_agent_is_not_installed(self):
        buddy = self.buddy()
        with mock.patch.object(self.agents, "launch", side_effect=self.agents.NotInstalled("Codex")):
            self.assertTrue(self.buddy_module.Buddy._hand_to_agent(buddy, "@codex tidy the notes", (5, 5)))
        buddy.overlay.say.assert_called_once_with(
            "Codex isn't installed on this PC, so I can't hand that over.")

    def test_any_other_question_goes_to_buddys_own_brain(self):
        buddy = self.buddy()
        with mock.patch.object(self.agents, "launch") as launch:
            self.assertFalse(self.buddy_module.Buddy._hand_to_agent(buddy, "what is on my screen?", (5, 5)))
        launch.assert_not_called()
        buddy.overlay.say.assert_not_called()

    def test_the_crew_is_hidden_when_buddy_looks_at_the_screen(self):
        buddy = self.buddy()
        buddy.crew = mock.Mock()
        self.buddy_module.Buddy._clear_for_screenshot(buddy)
        buddy.overlay.clear_for_screenshot.assert_called_once_with()
        buddy.crew.clear_for_screenshot.assert_called_once_with()
        buddy.crew = None
        self.buddy_module.Buddy._clear_for_screenshot(buddy)
        self.assertEqual(buddy.overlay.clear_for_screenshot.call_count, 2)
        self.assertTrue(self.buddy_module.DEFAULTS["crew_enabled"])
        self.assertIsNone(self.buddy_module.Buddy.crew)


if __name__ == "__main__":
    unittest.main()
