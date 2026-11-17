"""Fast commands from the words to done, on a window this bench opens, with a built or installed Buddy.

    python bench/battery.py                                  # the code inside dist/Buddy, run in this process
    python bench/battery.py --app "C:/path/to/Buddy" --through-app   # the running Buddy there, by Buddy.exe --ask

Without --through-app no Buddy is started: the System 1 code inside the app folder is imported and run the way
bench/fast_slow.py --real does it, so this checks the files that ship. With --through-app each command is sent to
the Buddy that is already running from that folder, and Buddy's own log line gives the time.

A command is only sent when the app's own System 1 code says it will do it at once on the bench window. Anything
else would go to the brain, which takes a screenshot, so it is skipped and listed. The bench waits for 25 s
without keyboard or mouse use, acts only on its own window, and asks Windows whether the window really changed.
Windows only.
"""
import argparse
import ctypes
import json
import os
import re
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDLE_NEEDED = 25.0
BENCH_WINDOW = """
import tkinter
root = tkinter.Tk()
root.title("Buddy bench window")
root.geometry("420x200+80+80")
root.after(150000, root.destroy)
root.mainloop()
"""
OLD = [("minimize {name}", "minimize"), ("switch to {name}", "focus"), ("maximize {name}", "maximize")]
NEW = [("can you minimize {name}", "minimize"), ("bring up {name}", "focus"), ("make {name} full screen", "maximize"),
       ("put {name} away", "minimize"), ("go back to {name}", "focus"), ("maximize {name} please", "maximize"),
       ("hide {name}", "minimize"), ("could you switch to {name}?", "focus"), ("enlarge {name}", "maximize")]
LAST = [("close {name}", "close")]

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    user32.GetLastInputInfo(ctypes.byref(info))
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def say(text):
    print(time.strftime("%H:%M:%S"), text, flush=True)


def reached(kind, hwnd):
    if kind == "minimize":
        return bool(user32.IsIconic(hwnd))
    if kind == "maximize":
        return bool(user32.IsZoomed(hwnd)) and not user32.IsIconic(hwnd)
    if kind == "focus":
        return user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd)
    return not user32.IsWindow(hwnd)


def make_it_a_real_change(kind, hwnd):
    """Put the window in a state the command has to change."""
    if kind == "maximize" and user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, 9)                       # restore to its normal size
    if kind == "focus" and not user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 6)                       # minimize, so "switch to" has to bring it back
    if kind == "minimize" and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    time.sleep(0.4)
    return not reached(kind, hwnd)


def running_from(app):
    """Is a Buddy.exe from this folder running? (--ask to a Buddy that is not running would start one.)"""
    command = "(Get-Process Buddy -ErrorAction SilentlyContinue).Path"
    listed = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,
                            creationflags=0x08000000).stdout
    wanted = os.path.normcase(str(app / "Buddy.exe"))
    return any(os.path.normcase(line.strip()) == wanted for line in listed.splitlines())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", default=str(ROOT / "dist" / "Buddy"), help="folder of a built or installed Buddy")
    parser.add_argument("--through-app", action="store_true", help="send the commands to the running Buddy there")
    parser.add_argument("--wait", type=float, default=900.0, help="seconds to wait for a moment without input")
    parser.add_argument("--out", default=str(ROOT / "bench" / "results_battery"))
    args = parser.parse_args()
    app = Path(args.app).resolve()
    sys.path.insert(0, str(app / "app" / "keyit"))
    import keyit_actions
    import keyit_minds

    version = re.search(r'^BUDDY_VERSION\s*=\s*"([^"]+)"', (app / "app" / "buddy_host.py").read_text(encoding="utf-8"),
                        re.M).group(1)
    how = "installed" if args.through_app else "inprocess"
    log = app / "app" / "pips" / "logs" / "widget.log"
    if args.through_app and not running_from(app):
        say("NOT RUN: no Buddy is running from that folder")
        return 2
    started = time.time()
    while idle_seconds() < IDLE_NEEDED:
        if time.time() - started > args.wait:
            say(f"NOT RUN: no {IDLE_NEEDED:.0f} s without input in {args.wait:.0f} s")
            return 2
        time.sleep(1.0)
    say(f"{how} Buddy {version}: idle for {idle_seconds():.0f} s, starting")
    child = subprocess.Popen([sys.executable, "-c", BENCH_WINDOW], creationflags=0x08000000)   # no console
    rows, skipped = [], []
    try:
        mine = []
        for _ in range(100):
            mine = [w for w in keyit_actions.list_windows() if w["title"] == "Buddy bench window"]
            if mine:
                break
            time.sleep(0.1)
        if len(mine) != 1:
            say("NOT RUN: the bench window was not listed exactly once")
            return 3
        name, hwnd = mine[0]["process"], mine[0]["hwnd"]
        trained = getattr(keyit_minds.Minds(None), "model", None) is not None
        if args.through_app:
            habits = os.path.join(os.environ.get("APPDATA", ""), "Buddy", "habits.json")
            minds = keyit_minds.Minds(habits if os.path.exists(habits) else None)      # read here, never saved
            minds._save = lambda: None
        else:
            import keyit_buddy
            minds = keyit_minds.Minds(None)
            buddy = types.SimpleNamespace(
                minds=minds, settings={"fast_thinking": True, "buddy_speak": False}, windows=[], history=[],
                last_reply=None, last_timing=None, confirm=lambda question: False, speaker=None,
                _stop_look_ahead=lambda: None, on_status=lambda text: None, post=lambda *args: None,
                overlay=types.SimpleNamespace(say=None, hide_later=None))
        say(f"window: process {name!r}; trained System 1 in this Buddy: {trained}")
        for wording, kind in OLD * 3 + NEW + LAST:
            text = wording.format(name=name)
            if kind != "close" and not make_it_a_real_change(kind, hwnd):
                skipped.append({"said": text, "why": "could not set the window up"})
                continue
            decision = minds.decide(text, keyit_actions.list_windows())
            if decision.mind != keyit_minds.FAST or not decision.window or decision.window["hwnd"] != hwnd:
                skipped.append({"said": text, "why": f"System 1 would hand this to the brain ({decision.why})"})
                say(f"skipped {text!r}: {decision.why}")
                continue
            row = {"said": text, "wanted": kind, "confidence": round(decision.confidence, 3)}
            began = time.perf_counter()
            if args.through_app:
                size = log.stat().st_size
                subprocess.run([str(app / "Buddy.exe"), "--ask", text], timeout=30)
            else:
                row["done_by_system_1"] = bool(keyit_buddy.think_fast(buddy, text))
                row["think_fast_s"] = round(time.perf_counter() - began, 4)
            changed = False
            while time.perf_counter() - began < 15.0:
                if reached(kind, hwnd):
                    changed = True
                    break
                time.sleep(0.01)
            row["window_really_changed"] = changed
            row["wall_s"] = round(time.perf_counter() - began, 3)
            if args.through_app:
                time.sleep(0.8)
                with open(log, "rb") as handle:
                    handle.seek(size)
                    new = handle.read().decode("utf-8", "replace")
                line = next((l for l in new.splitlines() if "Fast (" in l and repr(text) in l), "")
                found = re.search(r"Fast \((\w+), sure ([\d.]+)\) in ([\d.]+)s", line)
                row["done_by_system_1"] = bool(found)
                row["buddy_log_s"] = float(found.group(3)) if found else None
                row["buddy_log"] = line.split("root: ")[-1].strip() if line else ""
                rows.append(row)
                if not found:                             # it went to the brain after all: send nothing more
                    say(f"STOP: {text!r} was not done by System 1 in the app")
                    break
            else:
                rows.append(row)
            say(json.dumps(row))
    finally:
        child.kill()
    good = [r for r in rows if r.get("done_by_system_1") and r["window_really_changed"]]
    times = sorted(r["buddy_log_s"] if args.through_app else r["think_fast_s"] for r in good) or [0.0]
    walls = sorted(r["wall_s"] for r in good) or [0.0]
    summary = {"how": how, "buddy_version": version, "trained_system_1": trained, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "commands_planned": len(OLD) * 3 + len(NEW) + len(LAST), "commands_sent": len(rows),
               "done_by_system_1_and_checked": len(good), "skipped": skipped,
               "seconds_by_buddy_median": round(statistics.median(times), 4), "seconds_by_buddy_max": times[-1],
               "wall_median_s": statistics.median(walls), "wall_max_s": walls[-1],
               "note": ("seconds_by_buddy is Buddy's own log line: from the question arriving to the action returning. "
                        "wall_s also has the start of the Buddy.exe --ask helper and this bench's polling in it."
                        if args.through_app else
                        "seconds_by_buddy is think_fast(): from the words to the action returning."),
               "rows": rows}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"battery_{how}_{version}.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    say("SUMMARY " + json.dumps({k: v for k, v in summary.items() if k not in ("rows", "note")}))
    return 0 if rows and len(good) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
