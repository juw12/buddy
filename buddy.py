"""Buddy launcher: Pips pet + KeyIt keyboard sounds + local AI screen helper, in one app.

  python buddy.py                         run Buddy
  python buddy.py --selftest              Pips self-test with all Buddy parts attached, then exit
  python buddy.py --import-pips FOLDER    copy a standalone Pips profile (config + data) into Buddy
                  [--force]               ...replacing Buddy's current one (it is backed up first)

In the built app (Buddy.exe) all sources live as plain files in <exe folder>/app, so Pips keeps
its data in <exe folder>/app/pips/data exactly like a normal Pips install.
"""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent / "app"
else:
    APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def report(message, error=False):
    """print() in a console; a message box in the windowed Buddy.exe (it has no stdout)."""
    if sys.stdout is not None:
        print(message)
        return
    import ctypes

    icon = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, message, "Buddy", icon)


def run_import(args):
    import pips_import

    force = "--force" in args
    folders = [arg for arg in args if arg != "--force"]
    if len(folders) != 1:
        report("usage: buddy --import-pips FOLDER [--force]", error=True)
        return 2
    try:
        lines = pips_import.import_pips(folders[0], APP_DIR / "pips", force=force)
    except Exception as error:
        report(f"Import failed: {error}", error=True)
        return 1
    report("Imported Pips profile:\n" + "\n".join("  " + line for line in lines))
    return 0


def main(argv):
    if argv[:1] == ["--import-pips"]:
        return run_import(argv[1:])
    import buddy_host

    return buddy_host.main(argv)


if __name__ == "__main__":
    exit_code = main(sys.argv[1:])
    if sys.stdout is not None:            # windowed exe: no console streams
        sys.stdout.flush()
    os._exit(exit_code or 0)      # don't wait on COM/audio/hook background threads
