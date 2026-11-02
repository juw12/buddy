"""Buddy launcher: Pips pet + KeyIt keyboard sounds + local AI screen helper, in one app.

  python buddy.py              run Buddy
  python buddy.py --selftest   Pips self-test with all Buddy parts attached, then exit
"""
import os
import sys

import buddy_host

if __name__ == "__main__":
    exit_code = buddy_host.main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(exit_code or 0)      # don't wait on COM/audio/hook background threads
