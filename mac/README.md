# Buddy Lite for Mac

A buddy next to your mouse, on macOS 12 or newer. One universal download runs natively on Apple Silicon and Intel Macs.

- **Hold Control+Option+Space** and ask out loud. Release to send.
- **Tap Control+Option+Space** to type instead.
- Buddy takes one look at the display under your mouse, flies to what it means and reads the answer aloud.
- Menu bar buddy: Brain…, Read answers aloud, Keep Buddy next to my mouse, Colour, Allow screen access…

**Lite** means pointing and answers only. Clicking and typing for you, the Pips pet and keyboard sounds are
Windows-only for now.

## Install

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

Buddy needs a vision model behind an OpenAI-compatible `/chat/completions` endpoint. Set it in
menu bar buddy → **Brain…**:

- **Fully local:** install [Ollama](https://ollama.com), run `ollama pull qwen3-vl:4b-instruct`, keep the
  default endpoint `http://localhost:11434/v1/chat/completions`.
- **Hosted:** any OpenAI-compatible endpoint and model that read images, plus your API key.

The screenshot and your question go only to the endpoint you set. Settings live in
`~/Library/Application Support/Buddy/settings.json`, the log in `~/Library/Logs/Buddy/buddy.log`.

## From source

    cd mac
    python3 -m pip install -r requirements.txt
    python3 BuddyMac.py

`python3 BuddyMac.py --selftest OUT_DIR` runs the end-to-end check that CI runs on every change
(fake brain, renders of each state, pointer landing accuracy).
