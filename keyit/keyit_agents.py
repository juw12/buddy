"""The AI agents on this PC that Buddy works with.

* Which ones there are: scan() looks for the agents Buddy knows (OpenCode, Claude Code, Codex). Buddy shows
  an agent as crew pointers (buddy_crew.py) only after you said yes to it; your answers are in the settings.
* Your own AI: any other program can be added by name, with the command that starts it.
* Handing over a question: "@opencode tidy the notes folder" typed to Buddy opens that agent's own window
  with the question already typed in. The agent keeps its own model, its own rules and its own "may I?"
  questions, so nothing runs that you could not have started yourself.
"""

import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

# name typed after the @  ->  (name shown, program, arguments that start it with a question)
AGENTS = {
    "opencode": ("OpenCode", "opencode", lambda prompt: ["--prompt", prompt]),
    "claude": ("Claude Code", "claude", lambda prompt: [prompt]),
    "codex": ("Codex", "codex", lambda prompt: [prompt]),
}
CUSTOM_TEAM = "My AI"                  # team name on the pointer of an AI you added yourself
STILL_RUNNING_EVERY_S = 10.0           # how often a running program of yours is said to be still at it
PROMPT_MARK = "{prompt}"
MAX_CUSTOM = 12
_CALL = re.compile(r"@([A-Za-z][\w-]*)[\s:,]+(\S.*)", re.DOTALL)
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,23}")
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
# npm installs agents as .cmd files; cmd.exe would read these characters in a question as commands
_NOT_FOR_CMD = re.compile(r'["%&|<>^\r\n]')


class NotInstalled(Exception):
    """The agent's program is not on this PC (or not on PATH)."""


# --------------------------------------------------------------------------- which agents there are
def history_places(home=None):
    """Agent -> where it keeps its history on this PC (what the crew reads, read-only)."""
    home = Path(home) if home else Path.home()
    data_home = os.environ.get("XDG_DATA_HOME") if home == Path.home() else None
    data = Path(data_home) if data_home else home / ".local" / "share"
    return {
        "opencode": data / "opencode" / "opencode.db",
        "claude": home / ".claude" / "projects",
        "codex": home / ".codex" / "sessions",
    }


def scan(home=None, which=shutil.which):
    """The agents Buddy knows, in a fixed order: [{"id", "name", "program", "history", "found"}].
    `program`: it can be started from here ("@opencode ..."). `history`: it has worked on this PC, so there is
    something to show. `found` is either of the two."""
    places = history_places(home)
    agents = []
    for name, (label_text, program, _) in AGENTS.items():
        has_program = bool(which(program))
        has_history = places[name].exists()
        agents.append({"id": name, "name": label_text, "program": has_program, "history": has_history,
                       "found": has_program or has_history})
    return agents


def answers(settings):
    """{"opencode": True, "codex": False}: what the user said to each agent. One that is missing was never asked."""
    saved = settings.get("crew_agents")
    return {name: bool(value) for name, value in saved.items() if name in AGENTS} if isinstance(saved, dict) else {}


def enabled(settings):
    """The agents the crew may show: the ones the user said yes to."""
    return {name for name, wanted in answers(settings).items() if wanted}


def unasked(found, settings):
    """The agents of scan() that are on this PC and that the user was never asked about."""
    asked = answers(settings)
    return [agent for agent in found if agent["found"] and agent["id"] not in asked]


def with_answers(settings, found, ticked):
    """The new answers after the user ticked boxes: `ticked` is {"opencode": True, ...}. An agent that is
    not on this PC and not ticked stays unasked, so Buddy asks about it the day it turns up."""
    new = answers(settings)
    for agent in found:
        wanted = bool(ticked.get(agent["id"]))
        if agent["found"] or wanted or agent["id"] in new:
            new[agent["id"]] = wanted
    return new


# --------------------------------------------------------------------------- your own AI
def clean_custom(entry):
    """One AI the user added: {"name", "command", "color"}, or None when it has no usable name."""
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    if not _NAME.fullmatch(name) or name.lower() in AGENTS:
        return None
    color = str(entry.get("color") or "").strip().lower()
    return {"name": name, "command": " ".join(str(entry.get("command") or "").split()),
            "color": color if _HEX_COLOR.fullmatch(color) else ""}


def custom_agents(settings):
    """The AIs the user added, by lower-case name; a name is kept once."""
    saved = settings.get("crew_custom")
    agents = {}
    for entry in saved if isinstance(saved, list) else []:
        clean = clean_custom(entry)
        if clean is not None and clean["name"].lower() not in agents and len(agents) < MAX_CUSTOM:
            agents[clean["name"].lower()] = clean
    return agents


def custom_looks(settings):
    """{"mybot": {"color": "#22c55e"}}: the colours picked for the user's own AIs, in the form crew_looks has."""
    return {name: {"color": agent["color"]} for name, agent in custom_agents(settings).items() if agent["color"]}


def looks(settings):
    """The looks the crew is given: the colours of the user's own AIs, then crew_looks from the settings file
    (which wins)."""
    merged = custom_looks(settings)
    saved = settings.get("crew_looks")
    for name, look in (saved.items() if isinstance(saved, dict) else ()):
        if isinstance(look, dict):
            merged[str(name).lower()] = look
    return merged


def custom_command(template, prompt, which=shutil.which):
    """The command line for an AI the user added. `template` is what they typed, e.g.
    '"C:\\Tools\\mybot.exe" ask {prompt}'; the question goes where {prompt} is, or at the end. It is never
    given to a shell: the question stays one argument, whatever is in it."""
    try:
        parts = shlex.split(template.replace(f'"{PROMPT_MARK}"', PROMPT_MARK), posix=False)
    except ValueError:
        parts = []
    parts = [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part for part in parts]
    if not parts:
        raise NotInstalled("no command")
    path = which(parts[0]) or (parts[0] if Path(parts[0]).is_file() else None)
    if not path:
        raise NotInstalled(parts[0])
    if path.lower().endswith((".cmd", ".bat")):
        prompt = " ".join(_NOT_FOR_CMD.sub(" ", prompt).split())
    arguments = parts[1:]
    if any(PROMPT_MARK in part for part in arguments):
        arguments = [part.replace(PROMPT_MARK, prompt) for part in arguments]
    else:
        arguments.append(prompt)
    return [path, *arguments]


# --------------------------------------------------------------------------- handing a question over
def parse(question, custom=None):
    """("opencode", "tidy the notes folder") for "@opencode tidy the notes folder"; None for anything else.
    `custom`: the user's own AIs (custom_agents()), which can be called the same way."""
    match = _CALL.fullmatch((question or "").strip())
    if match is None:
        return None
    name = match.group(1).lower()
    if name not in AGENTS and name not in (custom or {}):
        return None
    return name, match.group(2).strip()


def label(name, custom=None):
    return AGENTS[name][0] if name in AGENTS else (custom or {})[name]["name"]


def command(name, prompt, which=shutil.which, custom=None):
    """The command line that opens the agent with the question typed in."""
    if name not in AGENTS:
        agent = (custom or {})[name]
        if not agent["command"]:
            raise NotInstalled(agent["name"])
        return custom_command(agent["command"], prompt, which)
    _, program, arguments = AGENTS[name]
    path = which(program)
    if not path:
        raise NotInstalled(AGENTS[name][0])
    if path.lower().endswith((".cmd", ".bat")):
        prompt = " ".join(_NOT_FOR_CMD.sub(" ", prompt).split())
    return [path, *arguments(prompt)]


def launch(name, prompt, folder=None, which=shutil.which, start=subprocess.Popen, custom=None):
    """Open the agent in its own window, working in `folder` (your home folder when none is set).
    Returns (the folder, the started process)."""
    folder = Path(folder) if folder and Path(folder).is_dir() else Path.home()
    new_window = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    process = start(command(name, prompt, which, custom), cwd=os.fspath(folder), creationflags=new_window)
    return folder, process


def report(process, agent, post):
    """Show an AI the user added as a crew pointer while its program runs: Buddy cannot read what such a
    program does, but it knows when it started and when it ended, and says "still at it" in between.
    `post` is Crew.post."""
    base = {"agent": agent["name"], "team": CUSTOM_TEAM, "id": f"custom:{agent['name'].lower()}:{process.pid}"}
    post(dict(base, say="Started"))

    def wait():
        while True:
            try:
                process.wait(STILL_RUNNING_EVERY_S)
                break
            except subprocess.TimeoutExpired:
                post(dict(base, alive=True))
        post(dict(base, state="done"))

    threading.Thread(target=wait, name="BuddyCrewCustom", daemon=True).start()
