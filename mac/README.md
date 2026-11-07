# Buddy Lite for Mac

A buddy next to your mouse, on macOS 12 or newer. One universal download runs natively on Apple Silicon and Intel Macs.

- **Hold Control+Option+Space** and ask out loud. Release to send.
- **Tap Control+Option+Space** to type instead.
- **Buddy Island:** on a MacBook with a notch, Buddy wraps the notch. Click the island to type, hold it to talk, click an answer to dismiss it. On Macs without a notch it hangs from the top centre of the screen. Turn it off in the menu: *Buddy Island in the notch*.
- Buddy takes one look at the display under your mouse, flies to what it means and reads the answer aloud.
- The arrow snaps onto the exact words it means ("Save As…") using Apple's on-device text recognition:
  on our test desktop that lifts the free brain from 10 to 13 of 16 on target. Nothing extra leaves the Mac.
- Menu bar buddy: Brain…, Send feedback…, Read answers aloud, Keep Buddy next to my mouse, Colour, Allow screen access…
- Works out of the box on a free cloud brain. No account, no key, no model download.

**Lite** means pointing and answers only. Clicking and typing for you, the Pips pet and keyboard sounds are
Windows-only for now.

## Install

**One line (easiest, free):** paste this in Terminal.

    curl -fsSL https://juw12.github.io/buddy/mac-install.sh | bash

It downloads the latest release, puts Buddy in Applications, clears the download quarantine (so there's no
"unidentified developer" block, no Apple Developer ID needed) and opens it. The script is
[40 readable lines](../docs/mac-install.sh). Then do step 4 below.

**Or by hand:**

1. Download `Buddy-mac.zip` from the [latest release](https://github.com/juw12/buddy/releases/latest) and unzip it.
2. Drag **Buddy** to Applications.
3. The first time, **right-click Buddy → Open → Open**. (It isn't signed with an Apple Developer ID yet, so a
   plain double-click shows "can't be opened".) If macOS still refuses, run
   `xattr -dr com.apple.quarantine /Applications/Buddy.app` once in Terminal.
4. Allow **Screen Recording** (so Buddy can see what you ask about), and **Microphone** and
   **Speech Recognition** (for hold-to-talk). Quit and reopen Buddy after allowing Screen Recording.
   If macOS asks to let Buddy find devices on your **local network**, allow it: that's how it reaches a
   brain such as Ollama.

## Brain

Out of the box Buddy uses its **free cloud brain** (Llama 4 Scout on Cloudflare Workers AI, through the small
Worker in [cloud/brain](../cloud/brain)). Nothing to set up. It's shared by everyone, so it has a daily limit
(about 180 questions a day in total, a few per minute each). The screenshot and question go to Cloudflare to
be answered and are not stored. If it's out of questions or unreachable, Buddy tries a local Ollama next.

To change it: menu bar buddy → **Brain…** (any OpenAI-compatible `/chat/completions` endpoint that reads images):

- **Fully local, unlimited:** install [Ollama](https://ollama.com), run `ollama pull qwen3-vl:4b-instruct`, set the
  endpoint to `http://localhost:11434/v1/chat/completions` and the model to `qwen3-vl:4b-instruct`.
- **Hosted:** any OpenAI-compatible endpoint and model that read images, plus your API key.

The screenshot and your question go only to the endpoint you set (and, if that brain can't answer, to the
free brain or a local Ollama; a brain with an API key never falls back to the free cloud brain). Settings live in
`~/Library/Application Support/Buddy/settings.json`, the log in `~/Library/Logs/Buddy/buddy.log`.

## From source

    cd mac
    python3 -m pip install -r requirements.txt
    python3 BuddyMac.py

`python3 BuddyMac.py --selftest OUT_DIR` runs the end-to-end check that CI runs on every change
(fake brain, renders of each state, pointer landing accuracy).
