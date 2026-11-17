"""Hand a typed question to an AI agent that is installed on this PC: "@opencode tidy the notes folder".

Buddy only opens the agent's own window with the question already typed in. The agent keeps its own model,
its own rules and its own "may I?" questions, so nothing runs that you could not have started yourself.
What it then does shows up as a crew pointer (buddy_crew.py).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

# name typed after the @  ->  (name shown, program, arguments that start it with a question)
AGENTS = {
    "opencode": ("OpenCode", "opencode", lambda prompt: ["--prompt", prompt]),
    "claude": ("Claude Code", "claude", lambda prompt: [prompt]),
    "codex": ("Codex", "codex", lambda prompt: [prompt]),
}
_CALL = re.compile(r"@([A-Za-z]+)[\s:,]+(\S.*)", re.DOTALL)
# npm installs agents as .cmd files; cmd.exe would read these characters in a question as commands
_NOT_FOR_CMD = re.compile(r'["%&|<>^\r\n]')


class NotInstalled(Exception):
    """The agent's program is not on this PC (or not on PATH)."""


def parse(question):
    """("opencode", "tidy the notes folder") for "@opencode tidy the notes folder"; None for anything else."""
    match = _CALL.fullmatch((question or "").strip())
    if match is None or match.group(1).lower() not in AGENTS:
        return None
    return match.group(1).lower(), match.group(2).strip()


def label(name):
    return AGENTS[name][0]


def command(name, prompt, which=shutil.which):
    """The command line that opens the agent with the question typed in."""
    _, program, arguments = AGENTS[name]
    path = which(program)
    if not path:
        raise NotInstalled(AGENTS[name][0])
    if path.lower().endswith((".cmd", ".bat")):
        prompt = " ".join(_NOT_FOR_CMD.sub(" ", prompt).split())
    return [path, *arguments(prompt)]


def launch(name, prompt, folder=None, which=shutil.which, start=subprocess.Popen):
    """Open the agent in its own window, working in `folder` (your home folder when none is set)."""
    folder = Path(folder) if folder and Path(folder).is_dir() else Path.home()
    new_window = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    start(command(name, prompt, which), cwd=os.fspath(folder), creationflags=new_window)
    return folder
