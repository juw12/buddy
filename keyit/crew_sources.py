"""Where Buddy's crew hears what AI agents are doing on this PC. Everything is read-only and stays on the PC.

* the inbox: a file any tool can append one JSON line to (`Buddy.exe --crew "{...}"` does it for you).
  It is always read; the three agents below only after the user said yes to them (keyit_agents.enabled).
  A line with "do" asks Buddy to click or type for the agent: that goes to crew_hands.py;
* Claude Code: its session files under ~/.claude/projects (sub-agents have their own files);
* Codex: its rollout files under ~/.codex/sessions;
* OpenCode: its database ~/.local/share/opencode/opencode.db (a sub-agent is a child session).

Each source turns what it reads into crew events (buddy_crew.normalize_event) with a short, safe line of
text: "Editing notes.md", "Running git push". Only the file's name and the program's name are shown, never
the rest of a command line, a search pattern or file contents: they can hold passwords, and the screen may
be shared.

An agent is followed only while it works. Between two steps an agent can be silent for minutes (a
long command, a helper it waits for), so each source also says "still at it" ({"alive": true}) every few
seconds for as long as it can see that: a tool call without its result, a message that is not finished.
"""

import datetime
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

import buddy_crew
import keyit_agents
import keyit_core

logger = logging.getLogger(__name__)

INBOX_FILE = keyit_core.USER_DIR / "crew" / "inbox.jsonl"
INBOX_MAX_BYTES = 1_000_000
CATCH_UP_BYTES = 262_144              # after a long gap only the newest part of a file is read
LIVE_EVERY_S = 3.0                    # how often "still at it" is said
LIVE_CAP_S = 600.0                    # a step that shows no sign of life for this long has been given up
BACK_TO_WORK = "Back to work"


# --------------------------------------------------------------------------- words for a tool call
_FILE_KEYS = ("file_path", "filePath", "path", "notebook_path", "target_file")
_COMMAND_KEYS = ("command", "cmd", "script")
_SHELLS = {"bash", "sh", "zsh", "pwsh", "powershell", "cmd"}
_TOOL_WORDS = {
    "read": "Reading", "view": "Reading", "cat": "Reading", "notebookread": "Reading",
    "edit": "Editing", "multiedit": "Editing", "str_replace": "Editing", "apply_patch": "Editing",
    "patch": "Editing", "notebookedit": "Editing", "str_replace_editor": "Editing",
    "write": "Writing", "create": "Writing",
}
_TOOL_LINES = {
    "grep": "Searching the files", "search": "Searching the files", "codesearch": "Searching the files",
    "glob": "Looking through the files", "ls": "Looking through the files", "list": "Looking through the files",
    "webfetch": "Reading a web page", "web_fetch": "Reading a web page", "fetch": "Reading a web page",
    "websearch": "Searching the web", "web_search": "Searching the web",
    "todowrite": "Updating its plan", "todoread": "Checking its plan", "update_plan": "Updating its plan",
    "compress": "Tidying its memory", "skill": "Reading a skill",
}
_RUN_TOOLS = {"bash", "shell", "exec_command", "execute", "local_shell", "run", "container.exec", "powershell"}
_HAND_OVER_TOOLS = {"task", "agent", "delegate"}
# Tools that are a question to the user: the agent waits for you, it does not work
_ASK_TOOLS = {"question", "ask", "askuserquestion", "ask_user", "ask_user_question", "exitplanmode",
              "request_user_input"}
_VERB = re.compile(r"[a-z][a-z-]{1,14}")
# Only for these is the second word shown ("git push"): it is a verb there, never a secret
_VERB_PROGRAMS = {"git", "gh", "npm", "pnpm", "yarn", "npx", "pip", "uv", "poetry", "conda", "cargo", "go",
                  "dotnet", "docker", "kubectl", "make", "winget", "choco", "brew", "apt", "opencode", "cairn"}
_NOT_THE_NEWS = {"cd", "pushd", "set-location", "export", "set", "echo"}


_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'')
_HELD = re.compile(r"\x00(\d+)\x00")
_PROGRAM_NAME = re.compile(r"[\w.+-]{1,20}")


def program_of(command):
    """'git push' from 'cd repo && git push https://token@host/...': the program and, for programs that take a
    verb, the verb. Nothing else of the command line is shown."""
    if isinstance(command, (list, tuple)):
        words = [str(word) for word in command]
        if len(words) >= 3 and Path(words[0]).stem.lower() in _SHELLS and words[1].startswith("-"):
            words = [words[-1]]                   # ["bash", "-lc", "the real command"]
        command = " ".join(words)
    held = []

    def hold(match):                              # what is in quotes is never split, and shown only as a program
        held.append(match.group(0)[1:-1])
        return f"\x00{len(held) - 1}\x00"

    for step in re.split(r"&&|\|\||[;|\n]", _QUOTED.sub(hold, str(command or ""))):
        words = step.split()
        while words and ("=" in words[0] or words[0] in ("&", "sudo")):
            words = words[1:]                     # VAR=1, PowerShell's call sign
        if not words:
            continue
        quoted = _HELD.fullmatch(words[0])        # "C:\Program Files\Git\bin\git.exe" status
        first = held[int(quoted.group(1))] if quoted else words[0]
        program = re.split(r"[\\/]", first.strip())[-1]
        program = re.sub(r"\.(exe|cmd|bat|ps1|sh)$", "", program, flags=re.IGNORECASE)
        if program.lower() in _NOT_THE_NEWS or not _PROGRAM_NAME.fullmatch(program):
            continue                              # $token, a path with odd characters: say nothing of it
        if program.lower() in _VERB_PROGRAMS and len(words) > 1 and _VERB.fullmatch(words[1]):
            return f"{program} {words[1]}"
        return program
    return ""


def describe(tool, arguments):
    """(what to say, file it is about or "") for one tool call of any of the agents."""
    arguments = arguments if isinstance(arguments, dict) else {}
    name = str(tool or "").strip()
    key = name.lower()
    file_name = ""
    for file_key in _FILE_KEYS:
        if isinstance(arguments.get(file_key), str) and arguments[file_key]:
            file_name = re.split(r"[\\/]", arguments[file_key].rstrip("\\/"))[-1]
            break
    if key in _TOOL_WORDS:
        return f"{_TOOL_WORDS[key]} {file_name or 'a file'}", file_name
    if key in _TOOL_LINES:
        return _TOOL_LINES[key], ""
    if key in _ASK_TOOLS:
        return "Needs your answer", ""
    if key in _RUN_TOOLS:
        command = next((arguments[k] for k in _COMMAND_KEYS if arguments.get(k)), "")
        program = program_of(command)
        return (f"Running {program}" if program else "Running a command"), ""
    if key in _HAND_OVER_TOOLS:
        helper = arguments.get("subagent_type") or arguments.get("agent") or "a helper"
        return f"Handing work to {str(helper)[:20]}", ""
    if key.startswith("mcp__"):                   # mcp__server__tool
        parts = [part for part in key.split("__")[1:] if part]
        return f"Using {' '.join(parts)[:30]}" if parts else "Using a tool", ""
    return (f"Using {name[:30]}" if name else "Working"), file_name


def is_question(tool):
    return str(tool or "").strip().lower() in _ASK_TOOLS


def tool_event(base, tool, arguments):
    """The crew event for one tool call: working, or waiting when the call is a question to the user."""
    say, file_name = describe(tool, arguments)
    return dict(base, say=say, file=file_name, state="waiting" if is_question(tool) else "working")


def still_at_it(base):
    return dict(base, state="working", alive=True)


def _folder_name(path):
    return re.split(r"[\\/]", str(path or "").rstrip("\\/"))[-1]


# --------------------------------------------------------------------------- files that grow
class GrowingFile:
    """Reads the whole lines added to a file since last time."""

    def __init__(self, path, offset):
        self.path = path
        self.offset = offset
        self.rest = b""
        self.context = {}                 # what the parser remembers about this file
        self.grown = time.monotonic()

    def new_lines(self):
        try:
            size = os.stat(self.path).st_size
        except OSError:
            return []
        if size < self.offset:                    # started over
            self.offset, self.rest = 0, b""
        if size == self.offset:
            return []
        skipped = False
        if size - self.offset > CATCH_UP_BYTES:
            self.offset, self.rest, skipped = size - CATCH_UP_BYTES, b"", True
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                data = handle.read(size - self.offset)
        except OSError:
            return []
        self.offset += len(data)
        self.grown = time.monotonic()
        lines = (self.rest + data).split(b"\n")
        self.rest = lines.pop()                   # no newline yet: the writer is in the middle of it
        if skipped and lines:
            lines.pop(0)                          # we landed in the middle of a line
        return [line for line in lines if line.strip()]


def _json_lines(lines):
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record


class InboxSource:
    """Ready-made events, one JSON object per line. Lines written before Buddy started are not replayed."""

    def __init__(self, path=INBOX_FILE):
        self.path = Path(path)
        try:
            size = self.path.stat().st_size
            if size > INBOX_MAX_BYTES:            # nobody else tidies this file
                self.path.write_bytes(b"")
                size = 0
        except OSError:
            size = 0
        self._file = GrowingFile(self.path, size)

    def poll(self):
        return list(_json_lines(self._file.new_lines()))


def post_event(event, path=INBOX_FILE):
    """Append one event to the inbox (what `Buddy.exe --crew` does). The running Buddy reads it within a second."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def event_from_text(text):
    """The event for `Buddy.exe --crew TEXT`: a JSON object, or the short form "explorer: Reading the docs"
    ("explorer: done" when it has finished). ValueError when it names no agent."""
    text = (text or "").strip()
    if text.startswith("{"):
        event = json.loads(text)
    else:
        agent, _, say = (part.strip() for part in text.partition(":"))
        event = {"agent": agent, "state": say.lower()} if say.lower() in ("done", "leave") \
            else {"agent": agent, "say": say}
    if buddy_crew.normalize_event(event) is None:
        raise ValueError("a crew event needs an agent")
    return event


class TranscriptSource:
    """Follows the session files of one kind of agent. `find()` lists (path, size) of the files there are now;
    `parse(record, context, path)` turns one JSON line into events. A parser that keeps context["open"] (the
    tool calls without a result yet) and context["base"] (who it is) gets "still at it" said for it."""

    SCAN_EVERY_S = 3.0
    FORGET_AFTER_S = 900.0
    LIVE_EVERY_S = LIVE_EVERY_S

    def __init__(self, find, parse):
        self._find = find
        self._parse = parse
        self._known = None                # path -> size at the last scan; None until the first scan
        self._active = {}                 # path -> GrowingFile
        self._scanned = 0.0
        self._said_alive = time.monotonic()

    def poll(self):
        now = time.monotonic()
        if self._known is None or now - self._scanned >= self.SCAN_EVERY_S:
            self._scan()
            self._scanned = now
        events = []
        for path, growing in list(self._active.items()):
            for record in _json_lines(growing.new_lines()):
                try:
                    events.extend(self._parse(record, growing.context, path))
                except Exception:
                    logger.debug("Crew: could not read a line of %s", path, exc_info=True)
            if now - growing.grown > self.FORGET_AFTER_S:
                self._known[path] = growing.offset
                del self._active[path]
        if now - self._said_alive >= self.LIVE_EVERY_S:
            self._said_alive = now
            for growing in self._active.values():
                context = growing.context
                if context.get("open") and context.get("base") and now - growing.grown < LIVE_CAP_S:
                    events.append(still_at_it(context["base"]))
        return events

    def _scan(self):
        first = self._known is None
        if first:
            self._known = {}
        try:
            files = list(self._find())
        except OSError:
            return
        for path, size in files:
            if path in self._active:
                continue
            if first:
                self._known[path] = size          # what is there already is history, not news
            elif size != self._known.get(path, 0):
                self._active[path] = GrowingFile(path, min(size, self._known.get(path, 0)))


# --------------------------------------------------------------------------- Claude Code
HELPERS_OF_SESSIONS_NEWER_THAN_S = 3600.0


def claude_files(root):
    """Session files, plus the sub-agent files of the sessions written to in the last hour."""
    try:
        projects = [entry for entry in os.scandir(root) if entry.is_dir()]
    except OSError:
        return
    recent = time.time() - HELPERS_OF_SESSIONS_NEWER_THAN_S
    for project in projects:
        try:
            entries = list(os.scandir(project.path))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith(".jsonl"):
                continue
            status = entry.stat()
            yield entry.path, status.st_size
            if status.st_mtime < recent:
                continue
            try:
                helpers = list(os.scandir(os.path.join(entry.path[:-len(".jsonl")], "subagents")))
            except OSError:
                continue
            for helper in helpers:
                if helper.is_file() and helper.name.endswith(".jsonl"):
                    yield helper.path, helper.stat().st_size


def _claude_helper_name(path):
    """A sub-agent's kind ("explorer") from the small file Claude Code writes next to its transcript."""
    try:
        meta = json.loads(Path(path).with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(meta.get("agentType") or "") if isinstance(meta, dict) else ""


def claude_events(record, context, path=""):
    kind = record.get("type")
    if kind not in ("assistant", "user"):
        return []
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), list) else []
    open_calls = context.setdefault("open", {})   # call id -> is it a question to the user
    if kind == "user":
        results = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]
        if not results:
            open_calls.clear()                    # the user speaks: whatever was running has been stopped
            return []
        answered = [open_calls.pop(block.get("tool_use_id"), False) for block in results]
        return [dict(context["base"], say=BACK_TO_WORK, state="working")] if any(answered) else []
    session = str(record.get("sessionId") or Path(path).stem)
    helper = bool(record.get("isSidechain")) or "subagents" in Path(path).parts
    if helper:
        if "name" not in context:
            context["name"] = _claude_helper_name(path) or "helper"
        agent = context["name"]
        key = f"claude:{session}:{record.get('agentId') or Path(path).stem}"
    else:
        agent, key = "Claude", f"claude:{session}"
    base = {"id": key, "agent": agent, "team": "Claude Code", "where": _folder_name(record.get("cwd"))}
    context["base"] = base
    events = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            open_calls[block.get("id")] = is_question(block.get("name"))
            events.append(tool_event(base, block.get("name"), block.get("input")))
    if not events and message.get("stop_reason") == "end_turn":
        open_calls.clear()
        events.append(dict(base, state="done"))
    return events


# --------------------------------------------------------------------------- Codex
def codex_files(root, today=None):
    """Today's and yesterday's rollout files (Codex files them by date)."""
    today = today or datetime.date.today()
    for day in (today, today - datetime.timedelta(days=1)):
        folder = os.path.join(root, f"{day.year:04d}", f"{day.month:02d}", f"{day.day:02d}")
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".jsonl"):
                try:
                    yield entry.path, os.stat(entry.path).st_size     # Codex keeps the file open: ask the file
                except OSError:
                    continue


_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)
_CODEX_CALLS = ("function_call", "custom_tool_call", "local_shell_call")
_CODEX_RESULTS = tuple(call + "_output" for call in _CODEX_CALLS)


def codex_events(record, context, path=""):
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    kind, inner = record.get("type"), payload.get("type")
    if kind == "session_meta":
        context["where"] = _folder_name(payload.get("cwd"))
        return []
    base = {"id": f"codex:{Path(path).stem}", "agent": "Codex", "team": "Codex", "where": context.get("where", "")}
    context["base"] = base
    open_calls = context.setdefault("open", {})   # call id -> is it a question to the user
    if kind == "response_item" and inner in _CODEX_RESULTS:
        answered = open_calls.pop(payload.get("call_id"), False)
        return [dict(base, say=BACK_TO_WORK, state="working")] if answered else []
    if kind == "response_item" and inner in _CODEX_CALLS:
        arguments = payload.get("arguments") if inner == "function_call" else payload.get("input")
        if isinstance(arguments, str):
            patched = _PATCH_FILE.search(arguments)
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"path": patched.group(1).strip()} if patched else {}
        if inner == "local_shell_call":
            action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
            name, arguments = "shell", {"command": action.get("command")}
        else:
            name = payload.get("name")
        if payload.get("call_id"):
            open_calls[payload["call_id"]] = is_question(name)
        return [tool_event(base, name, arguments)]
    if kind == "event_msg" and inner in ("task_complete", "turn_complete", "turn_aborted"):
        open_calls.clear()
        return [dict(base, state="done")]
    return []


# --------------------------------------------------------------------------- OpenCode
def opencode_database(home=None):
    return keyit_agents.history_places(home)["opencode"]


class OpenCodeSource:
    """Reads OpenCode's database, read-only. Two layouts are known: `part` rows (OpenCode 1.2 and later) and
    tool calls inside `session_message` rows (the layout of the build this was written on)."""

    POLL_EVERY_S = 1.0
    RETRY_EVERY_S = 30.0
    RECENT_MS = 30 * 60 * 1000
    LIVE_EVERY_S = LIVE_EVERY_S
    BETWEEN_STEPS_S = 30.0                # from the end of one step to the first word of the next
    LIVE_SESSIONS = 50

    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else opencode_database()
        self._clock = clock
        self._connection = None
        self.layout = None
        self._next = 0.0
        self._since = int(clock() * 1000)         # only what happens from now on
        self._last_part = None
        self._seen = set()                        # tool calls and finished messages already told
        self._waiting = set()                     # part ids seen before the agent had filled them in
        self._live = {}                           # session -> who it is, for the sessions seen at work
        self._said_alive = time.monotonic()

    def poll(self):
        now = time.monotonic()
        if now < self._next:
            return []
        self._next = now + self.POLL_EVERY_S
        try:
            if self._connection is None:
                self._open()
            if self.layout == "session_message":
                return self._poll_messages()
            if self.layout == "part":
                return self._poll_parts()
        except sqlite3.Error:
            logger.debug("Crew: OpenCode database not readable", exc_info=True)
            self.close()
        self._next = now + self.RETRY_EVERY_S
        return []

    def close(self):
        if self._connection is not None:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
        self._connection = None

    def _open(self):
        if not self.path.is_file():
            raise sqlite3.OperationalError("no OpenCode database")
        address = "file:" + urllib.parse.quote(self.path.as_posix(), safe="/:") + "?mode=ro"
        self._connection = sqlite3.connect(address, uri=True, timeout=1.0, check_same_thread=False)
        tables = {row[0] for row in self._connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if {"session_message", "session_v2"} <= tables:
            self.layout = "session_message"
        elif {"part", "message", "session"} <= tables:
            self.layout = "part"
            row = self._connection.execute("SELECT MAX(id) FROM part").fetchone()
            self._last_part = row[0] or ""
        else:
            self.layout = None
            raise sqlite3.OperationalError("unknown OpenCode database layout")

    def _remember(self, key):
        if len(self._seen) > 20000:
            self._seen.clear()
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    @staticmethod
    def _base(session, agent, directory):
        return {"id": f"opencode:{session}", "agent": agent or "OpenCode", "team": "OpenCode",
                "where": _folder_name(directory)}

    def _poll_messages(self):
        rows = self._connection.execute(
            "SELECT m.id, m.session_id, m.type, m.time_updated, m.data, s.agent, s.directory "
            "FROM session_message m JOIN session_v2 s ON s.id = m.session_id "
            "WHERE m.time_created > ? AND m.time_updated >= ? AND m.type IN ('assistant', 'idle') "
            "ORDER BY m.time_updated", (self._since - self.RECENT_MS, self._since)).fetchall()
        events = []
        for message_id, session, kind, updated, data, session_agent, directory in rows:
            self._since = max(self._since, updated)
            try:
                message = json.loads(data)
            except ValueError:
                continue
            base = self._base(session, message.get("agent") or session_agent, directory)
            if kind == "idle":
                base = self._live.pop(session, None) or base      # an idle row does not say which agent it was
                if self._remember(("idle", message_id)):
                    events.append(dict(base, state="done"))
                continue
            for item in message.get("content") or []:
                if not isinstance(item, dict) or item.get("type") != "tool":
                    continue
                state = item.get("state") if isinstance(item.get("state"), dict) else {}
                call = (message_id, item.get("id"))
                if is_question(item.get("name")) and state.get("status") in ("completed", "error"):
                    if self._remember(("answered",) + call):
                        self._seen.add(("tool",) + call)
                        events.append(dict(base, say=BACK_TO_WORK, state="working"))
                elif self._remember(("tool",) + call):
                    events.append(tool_event(base, item.get("name"), state.get("input")))
                else:
                    continue
                self._live.pop(session, None)     # newest last
                self._live[session] = base
            if message.get("finish") == "stop":
                self._live.pop(session, None)
                if self._remember(("stop", message_id)):
                    events.append(dict(base, state="done"))
        while len(self._live) > self.LIVE_SESSIONS:
            del self._live[next(iter(self._live))]
        return events + self._still_at_it()

    def _still_at_it(self):
        """"Still at it" for each session seen at work whose newest message is not the end of a turn: a command
        that runs, a helper it waits for, an answer being written. OpenCode does not touch the message while a
        tool runs, so without this a long command would look like an agent that stopped."""
        now = time.monotonic()
        if now - self._said_alive < self.LIVE_EVERY_S:
            return []
        self._said_alive = now
        wall_ms = self._clock() * 1000
        events = []
        for session, base in list(self._live.items()):
            row = self._connection.execute(
                "SELECT type, time_updated, data FROM session_message WHERE session_id = ? "
                "ORDER BY seq DESC LIMIT 1", (session,)).fetchone()
            busy = False
            if row is not None and row[0] != "idle":
                try:
                    message = json.loads(row[2])
                except ValueError:
                    message = {}
                finish = message.get("finish") if isinstance(message, dict) else "stop"
                in_a_step = row[0] == "assistant" and finish is None
                patience = LIVE_CAP_S if in_a_step else self.BETWEEN_STEPS_S
                busy = finish != "stop" and wall_ms - row[1] < patience * 1000
            if busy:
                events.append(still_at_it(base))
            else:
                del self._live[session]
        return events

    def _poll_parts(self):
        query = ("SELECT p.id, p.session_id, p.data, m.data, s.agent, s.directory FROM part p "
                 "JOIN message m ON m.id = p.message_id JOIN session s ON s.id = p.session_id WHERE ")
        rows = self._connection.execute(query + "p.id > ? ORDER BY p.id LIMIT 300", (self._last_part,)).fetchall()
        if self._waiting:
            waiting = sorted(self._waiting)[:50]
            marks = ",".join("?" * len(waiting))
            rows += self._connection.execute(query + f"p.id IN ({marks})", waiting).fetchall()
        events = []
        for part_id, session, data, message_data, session_agent, directory in rows:
            self._last_part = max(self._last_part, part_id)
            try:
                part, message = json.loads(data), json.loads(message_data)
            except ValueError:
                continue
            agent = message.get("agent") or message.get("mode") or session_agent
            base = self._base(session, agent, directory)
            if part.get("type") == "tool":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                ready = bool(state.get("input")) or state.get("status") in ("completed", "error")
                if not ready and part_id not in self._waiting and len(self._waiting) < 50:
                    self._waiting.add(part_id)    # the agent is still writing this call: look once more
                    continue
                self._waiting.discard(part_id)
                if self._remember(("part", part_id)):
                    events.append(tool_event(base, part.get("tool"), state.get("input")))
            elif part.get("type") == "step-finish" and part.get("reason") == "stop":
                events.append(dict(base, state="done"))
        return events


# --------------------------------------------------------------------------- the thread that feeds the crew
def default_sources(home=None, enabled=None):
    """The inbox, and one source for each agent in `enabled` (None: every agent Buddy knows)."""
    places = keyit_agents.history_places(home)
    claude_root, codex_root = str(places["claude"]), str(places["codex"])
    makers = {
        "claude": lambda: TranscriptSource(lambda: claude_files(claude_root), claude_events),
        "codex": lambda: TranscriptSource(lambda: codex_files(codex_root), codex_events),
        "opencode": lambda: OpenCodeSource(places["opencode"]),
    }
    return [InboxSource()] + [make() for name, make in makers.items() if enabled is None or name in enabled]


class CrewWatcher:
    """Asks every source for news a few times a second and gives it to the crew."""

    TICK_S = 0.25

    def __init__(self, crew, sources=None, enabled=None, hands=None):
        self.crew = crew
        self.hands = hands                # crew_hands.Hands: does what an inbox line asks for with "do"
        self.sources = default_sources(enabled=enabled) if sources is None else sources
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="BuddyCrew", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def step(self):
        for source in self.sources:
            try:
                asks = self.hands is not None and isinstance(source, InboxSource)
                for event in source.poll():
                    # only a line somebody put in the inbox can ask for a click: never what an agent read or wrote
                    if not (asks and self.hands.submit(event)):
                        self.crew.post(event)
            except Exception:
                logger.exception("Crew source %s failed", type(source).__name__)
        self.crew.tick()

    def _run(self):
        logger.info("Crew watcher started (%d sources)", len(self.sources))
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                logger.exception("Crew step failed")
            self._stop.wait(self.TICK_S)
        self.crew.close()
        for source in self.sources:
            if hasattr(source, "close"):
                source.close()
        logger.info("Crew watcher stopped")
