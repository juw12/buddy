# Buddy

Buddy is the app. It owns the tray icon, the main window, autostart and quitting.
It has three features:
- **Focus**: the Pips desktop pet and focus tracker, running inside Buddy.
- **Keyboard & mouse sounds**: KeyIt, plus a soft click for every mouse button (Settings: *Mouse clicks too*).
- **AI helper**: a local helper that looks at your screen and answers questions about it.

Click the orange Buddy icon by the clock to open the main window. Its tabs are Home, Focus, Keyboard sounds and AI helper.

## Run
- **App:** unzip `Buddy-win64.zip` anywhere and run `Buddy.exe`. You don't need Python.
- **From source:** run `setup.bat` once (it creates `.venv`, installs the requirements, and runs the self-test).
  After that, start Buddy with `start.bat`.
- **Build the app:** run `build.bat`. It writes `dist\Buddy\` and `dist\Buddy-win64.zip`. Your Pips data in
  `dist\Buddy\app\pips` is kept when you rebuild, and it is never put in the zip.

## Bring your Pips history along
Quit Pips first, or leave it running: the database is copied safely either way. Then run:

    Buddy.exe --import-pips "C:\path\to\your\pips"            (app)
    python buddy.py --import-pips "C:\path\to\your\pips"       (source)

This copies `config.json` and `data\`. Buddy only reads your original Pips folder. If Buddy already has a profile,
the import stops. Add `--force` to replace that profile; the old one is moved to `pips\backup\` first.

## Use
- **Ctrl+Alt+Space**, hold it: ask Buddy about your screen by voice. Release to send, and press Esc to cancel.
- **Voice**: Settings -> Voice. "Natural: ..." voices sound human (Kokoro, offline, ~120 MB downloaded once into %APPDATA%\Buddy\voices) in English (US/UK), Chinese (Mandarin), Spanish, French, Italian, Portuguese (Brazil) and Hindi. If Buddy answers in Chinese or Hindi while another language's voice is picked, a voice of the same gender reads that part; the Microsoft voices are the old robotic ones. Press **Test** to hear one. Natural voices run on the CPU, so on a very busy PC they can start a second or two later.
- **Brain** (Settings): *Smart (MAAS)* uses your company's AI gateway (vision-model, ~1-2 s answers, much smarter; your screenshot + question go to the company gateway, logging in with the MAAS account from the Pips token card). *Local GPU* / *Local CPU* keep everything on this PC (~5-15 s / ~1 min). If MAAS can't be reached, Buddy answers locally for 5 minutes and then tries MAAS again.
- **Ctrl+Alt+Space**, tap it: a small ask box opens at your mouse, so you can type when you can't talk. If you had text highlighted, Buddy reads it too (through Windows accessibility, never the clipboard). Just pressing Enter means "explain this".
- Like Clicky, a small orange buddy rests next to your mouse all the time. It shows a waveform while it listens, a spinner while it thinks, and flies to whatever it points at. To see it only while you ask, untick *Settings → Keep Buddy next to my mouse all the time*.
  Buddy answers in the pet's speech bubble, reads it aloud, and draws an orange beam from the pet
  to whatever it is talking about.
- **Ctrl+Alt+M**: mute keyboard and mouse sounds.
- Buddy plays a short chime when it wakes up (Ctrl+Alt+Space or *Ask Buddy*) and a blip when it got your question. Turn it off with *Wake-up chime* in Settings.
- **Ctrl+Alt+P**: Pips's own hotkey (unchanged).
- Right-click the pet, then go to **Buddy** to find *Ask Buddy about my screen*, *Keyboard sounds*, and *Sounds & AI settings*.

The AI part needs [Ollama](https://ollama.com) running locally with a vision model,
for example `ollama pull qwen3-vl:4b-instruct`. Everything stays on this PC. Buddy also tells the model
your current Pips focus score, so answers can take your day into account.

**Speed (Use GPU, on by default):** Ollama runs only on the CPU on Intel Arc laptops, which takes about
70–140 s per screenshot. So Buddy starts Ollama's own bundled GPU server (Vulkan) with the model files
Ollama already downloaded. A fresh screenshot takes about 12–15 s, and a follow-up or a question asked
while you were still talking takes about 5 s. The GPU server stops after 30 min idle to free memory.
If the GPU path isn't available, Buddy uses Ollama on the CPU. You can untick *Use GPU (fast)* in
Settings to always use the CPU.

**Doing things:** ask Buddy to do something and it does it. For example: "close the file explorer", "close this", "minimize Outlook", "open notepad", "open google.com", "click Insert", "type hello", or "save it" (Ctrl+S). It only acts on requests; "how do I…" questions just get an answer and an arrow. If a window is frozen, Buddy asks before force-closing it. Frozen File Explorer windows are fixed by restarting Explorer, so the taskbar blinks once. Risky shortcuts like Alt+F4 also ask first.

Buddy only does what *your words* ask for. "Close notepad" can only close Notepad, never another window. It clicks only when you say click, press or select. It never clicks a window's own close or minimize buttons. If you name an app that isn't open, it refuses instead of typing into another window. Everything Buddy does is written to `app\pips\logs\widget.log`.

**From scripts or shortcuts:** `Buddy.exe --ask "open notepad"` hands a typed question to the running Buddy, the same as the Ctrl+Alt+Space box.

**Pointing:** the arrow snaps onto the exact button, tab or text box through Windows UI Automation.
This works in Office, browsers, Electron apps and the taskbar. In apps that draw their own UI, the
arrow goes to the model's estimate.

## Data
- Pips data (tasks, history, config) is stored in `pips/data` and `pips/config.json`. In the app, that is
  `Buddy\app\pips\data` and `Buddy\app\pips\config.json`.
- Autostart (pet menu: Autostart, or the "Start with Windows" setting) adds `Buddy.lnk` to your Startup folder.
  It doesn't change a standalone `Pips.lnk`.
- Sound and AI settings are stored in `%APPDATA%\Buddy`. You can override this with the `KEYIT_HOME` environment variable.

## Notes
- Don't run standalone Pips and Buddy at the same time. Both would track your activity, and they would
  fight over Ctrl+Alt+P.
- Pips's built-in updater is disabled inside Buddy, because updates ship with Buddy.
- `python buddy.py --selftest` runs the Pips self-test with all Buddy parts attached.

## Layout
- `buddy.py`: the launcher (`--selftest`, `--import-pips`).
- `pips_import.py`: copies a Pips profile into Buddy (SQLite online backup with an integrity check).
- `buddy.spec` and `build.bat`: the PyInstaller onedir build. The sources ship as plain files in `app\`.
- `buddy_host.py`: BuddyApp, the boss. It owns the Tk root, the tray, single-instance, quitting and autostart.
  It also contains FocusFeature (Pips running under Buddy's root), the pet menu and the pet-guide overlay.
- `buddy_home.py`: the Home and Focus tabs of the Buddy window.
- `assets/buddy.ico`: Buddy's icon. `make_buddy_icon.py` draws it.
- `pips/`: Pips v2.28.0 source, unmodified.
- `keyit/`: KeyIt modules, patched so they can be hosted inside Buddy.
