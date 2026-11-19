# Buddy Lite for Mac

A buddy next to your mouse, on macOS 12 or newer. One universal download runs natively on Apple Silicon and Intel Macs.

- **Hold Control+Option+Space** and ask out loud. Release to send.
- **Tap Control+Option+Space** to type instead.
- **Buddy Island:** on a MacBook with a notch, Buddy wraps the notch. Click the island to type, hold it to talk, click an answer to dismiss it. On Macs without a notch it hangs from the top centre of the screen. Turn it off in the menu: *Buddy Island in the notch*.
- Buddy takes one look at the display under your mouse, flies to what it means and reads the answer aloud.
- The arrow snaps onto the exact words it means ("Save As…") using Apple's on-device text recognition:
  on our test desktop that lifts the free brain from 10 to 13 of 16 on target. Nothing extra leaves the Mac.
- Menu bar buddy: Brain…, Send feedback…, Read answers aloud, Keep Buddy next to my mouse, Colour, Allow screen access…
- **Not only English.** Menu bar, *Voice language*: hold-to-talk listens in Indonesian, Chinese (Taiwan or
  Mainland), Cantonese, Japanese, Korean, Malay, Thai, Vietnamese, Hindi, German, French, Spanish, Portuguese
  or English (only the ones your Mac's speech recognition has are listed). Buddy answers in the language you
  asked in and reads the answer with a Mac voice for that language. Languages your Mac can't recognise
  offline go through Apple's servers, like dictation does.
- **The agent pointer:** when a program (an AI agent, a script) clicks with the mouse, a second pointer
  flies to the click and names the program. [More below](#the-agent-pointer-1120-typing-since-1130).
- Works out of the box on a free cloud brain. No account, no key, no model download.

**Lite** means pointing and answers only. Clicking and typing for you, the Pips pet and keyboard sounds are
Windows-only for now.

## Install

**One line (easiest, free):** paste this in Terminal.

    curl -fsSL https://juw12.github.io/buddy/mac-install.sh | bash

It downloads the newest Mac build, checks its SHA-256, puts Buddy in Applications, clears the download
quarantine (so there's no "unidentified developer" block, no Apple Developer ID needed) and opens it. The
script is [short and readable](../docs/mac-install.sh). Then do step 4 below.

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

## The agent pointer (1.12.0, typing since 1.13.0)

An AI agent or a script that uses this Mac should not look like a ghost. When a program clicks with the
mouse, in any app, a second pointer in another colour than your Buddy flies to the click, shows a ring
there and says who it was: *Terminal clicked here*, *cliclick right-clicked here*. It leaves 2.5 seconds
after the last click.

- **How Buddy knows:** macOS marks every mouse press with the process that posted it. A press from your
  own mouse or trackpad has none, so your own clicks are never shown.
- **No new permission for clicks:** hearing mouse presses needs none, so macOS asks nothing.
- **Typing (1.13.0), only if you allow it:** macOS lets no app hear keys unless you tick it under
  *System Settings > Privacy & Security > Accessibility*. Buddy never asks by itself. The menu has a line
  *Show an agent's typing too: allow Accessibility…* that opens that list: tick Buddy, then quit and start
  Buddy again (the line is gone when it works). After an update macOS may want the tick again, because
  the app is not signed with a paid Apple certificate. Then, when a program types, the pointer goes to
  the top of the window in front and says *osascript is typing here*, at most every 1.5 seconds. Buddy
  reads only which process posted the key, never which key, and your own keyboard is never shown.
- **Always a trace:** every such click is a line in `~/Library/Logs/Buddy/buddy.log` (menu: *Open log*),
  with the program, its process number, the button and the point. Never what was typed.
- **Off:** menu bar buddy, untick *Show where an agent clicks*. The log line is still written.
- **Never shown:** a press a program makes without the mouse and without keys (through Accessibility,
  "AXPress"). macOS tells no other app about it, so no update of Buddy can show it. Where in the window
  the typing goes is not shown either: Buddy does not read other apps' windows.
  Clicks passed on by mouse and trackpad tools (BetterTouchTool, LinearMouse, Mac Mouse Fix, SteerMouse,
  USB Overdrive, Logitech, Karabiner) and by screen sharing are your own hand and are left out.
  An agent that works from the command line is named by its tool (*python3.12*, *cliclick*), not by the
  app that started it.

Checked on GitHub's Macs (macOS 15, Apple Silicon and Intel) with the built app: a click that another
program posted (`osascript`) was heard with that program on it at the exact point, and the pointer flew
there and said *osascript clicked here*; a click Buddy posted itself was heard and left alone; macOS asked
for nothing. Not checked: a real mouse or trackpad (those machines have none, so "your own clicks are
never shown" rests on how macOS marks them), the mouse tools in the list above, more than one display,
and a MacBook Pro 2019.

Typing (1.13.0), checked on the same machines with the built app: a key press handed to the app is read
(only the process that posted it), your own keys and Buddy's are left alone, another program's are told
once per burst, and the pointer flies to the window in front and says *… is typing here*. Each run's
self-test report says under `agent_typing` whether that machine lets Buddy hear real keys; where it does,
a key another program posted (`osascript`, F13) is sent and reported under `live_key`. Not checked: the
tick under Accessibility set by hand in System Settings, a real keyboard, and a MacBook Pro 2019 Intel.
One minute to check it yourself: allow Buddy, start it again, open TextEdit, and in Terminal run
`osascript -e 'tell application "System Events" to keystroke "hello"'` (macOS asks to allow Terminal the
first time). The second pointer should fly to the TextEdit window and say *System Events is typing here*
(the program macOS types through for that line), and `~/Library/Logs/Buddy/buddy.log` gets a line `agent typing: …`.

## From source

    cd mac
    python3 -m pip install -r requirements.txt
    python3 BuddyMac.py

`python3 BuddyMac.py --selftest OUT_DIR` runs the end-to-end check that CI runs on every change
(fake brain, renders of each state, pointer landing accuracy).
