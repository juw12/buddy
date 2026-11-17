"""Does a conversation really hold the process above normal priority - seen from OUTSIDE the process?

Talk mode enters keyit_speech.urgent() for the whole conversation (keyit_buddy.talk_toggle passes it as
`hold`). The unit tests ask the process about itself. This script starts a second process that runs a real
keyit_talk.TalkSession thread wired as the app wires it (hold=keyit_speech.urgent, read_ahead=True) on a fake
microphone and voice, and reads that process's priority class from here, as Task Manager would, at each step.
No microphone, no sound, no speech model.

    python bench/talk_priority.py --out bench/results_talk/talk_priority.json

Three cases:
  conversation   start, a read during it, the read back, end
  read first     a read is already running when the conversation starts and comes back during it
  chosen         somebody set Buddy to below normal (from outside): Buddy must leave that alone

Windows only (priority classes). It is the session thread and the priority code that are checked, not the
installed app: no hotkey is pressed and no window is shown.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ("conversation", "read first", "chosen")
SAMPLES, SPACING = 5, 0.05                                       # readings per step, seconds apart


# ----------------------------------------------------------------------------- the process that is watched
class Mic:
    sample_rate = 16000

    def __init__(self):
        self.blocks = []

    def stop(self):
        pass


class Voice:
    recent = ()

    def speaking(self):
        return False

    def reference(self):
        return True

    def listen(self, callback):
        pass

    def pause(self):
        pass

    def resume(self):
        pass


class Silence:
    since = None

    def hear(self, audio):
        return []


def step(name):
    """Tell the watcher where we are and wait until it has looked."""
    print(name, flush=True)
    sys.stdin.readline()


def child(case):
    sys.path.insert(0, str(ROOT / "keyit"))
    import keyit_speech
    import keyit_talk

    ended = threading.Event()
    session = keyit_talk.TalkSession(
        Mic(), lambda audio: audio, Silence, lambda audio: "", ask=lambda text: None, interrupt=lambda: None,
        speaker=Voice(), on_end=lambda reason: ended.set(), read_ahead=True, hold=keyit_speech.urgent)
    reading, back = threading.Event(), threading.Event()

    def read():                                                  # what Speech.transcribe does around a decode
        with keyit_speech.urgent():
            reading.set()
            back.wait(30)

    def start_read():
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        reading.wait(5)
        return thread

    def start_talking():
        session.start()
        deadline = time.monotonic() + 5
        while session.ticked_at is None and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)                                          # a few more turns of the loop

    print(f"pid {os.getpid()}", flush=True)                      # see watch(): who to look at
    step("before")
    if case == "read first":
        reader = start_read()
        step("a read is running")
        start_talking()
        step("talking, the read still running")
    else:
        start_talking()
        step("talking")
        reader = start_read()
        step("talking, a read is running")
    back.set()
    reader.join(5)
    step("talking, the read is back")
    session.stop("hotkey")
    ended.wait(5)
    step("ended")


# ----------------------------------------------------------------------------- the watcher
def watch(case, psutil):
    names = {psutil.NORMAL_PRIORITY_CLASS: "normal", psutil.ABOVE_NORMAL_PRIORITY_CLASS: "above normal",
             psutil.BELOW_NORMAL_PRIORITY_CLASS: "below normal", psutil.HIGH_PRIORITY_CLASS: "high",
             psutil.IDLE_PRIORITY_CLASS: "idle", psutil.REALTIME_PRIORITY_CLASS: "realtime"}
    process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), "--child", case],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # In a virtual environment python.exe is a launcher that starts the real interpreter as its child,
    # so process.pid is the wrong process to look at. The interpreter says which one it is; its
    # priority is still read from here.
    seen = psutil.Process(int(process.stdout.readline().split()[1]))
    steps = []
    try:
        for line in process.stdout:
            name = line.strip()
            if name == "before" and case == "chosen":
                seen.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)    # as somebody would in Task Manager
            readings = []
            for _ in range(SAMPLES):
                readings.append(names.get(seen.nice(), str(seen.nice())))
                time.sleep(SPACING)
            steps.append({"step": name, "seen": sorted(set(readings)), "readings": len(readings)})
            process.stdin.write("\n")
            process.stdin.flush()
        process.wait(10)
    finally:
        if process.poll() is None:
            process.kill()
    return {"case": case, "exit_code": process.returncode, "watched_pid": seen.pid,
            "launcher_pid": process.pid, "steps": steps}


def expected(case, name):
    if case == "chosen":
        return "below normal"
    return "normal" if name in ("before", "ended") else "above normal"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("--out", help="write the readings here as JSON")
    arguments = parser.parse_args()
    if arguments.child:
        return child(arguments.child)
    if sys.platform != "win32":
        raise SystemExit("Windows only: this reads Windows priority classes.")
    import psutil

    cases, wrong = [], 0
    for case in CASES:
        found = watch(case, psutil)
        for one in found["steps"]:
            one["expected"] = expected(case, one["step"])
            one["ok"] = one["seen"] == [one["expected"]]
            wrong += not one["ok"]
            print(f"  {case:13s} {one['step']:33s} seen {', '.join(one['seen']):22s} "
                  f"expected {one['expected']:13s} {'ok' if one['ok'] else 'WRONG'}")
        if found["exit_code"] != 0 or len(found["steps"]) != 5:
            wrong += 1
            print(f"  {case}: the watched process did not finish its steps (exit {found['exit_code']})")
        cases.append(found)
    report = {"when": time.strftime("%Y-%m-%d %H:%M"), "readings_per_step": SAMPLES, "wrong": wrong,
              "cases": cases}
    print(f"{wrong} step(s) wrong")
    if arguments.out:
        pathlib.Path(arguments.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8",
                                               newline="\n")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
