# Buddy

Buddy = **Pips** (desktop pet + productivity tracking) + **KeyIt** (keyboard sounds)
+ a **local AI screen helper**, all in one app and one tray/pet.

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
- **Ctrl+Alt+Space** (hold): ask Buddy about your screen by voice. Release to send, and press Esc to cancel.
  Buddy answers in the pet's speech bubble, reads it aloud, and draws an orange beam from the pet
  to whatever it is talking about.
- **Ctrl+Alt+M**: mute keyboard sounds.
- **Ctrl+Alt+P**: Pips's own hotkey (unchanged).
- Right-click the pet, then go to **Buddy** to find *Ask Buddy about my screen*, *Keyboard sounds*, and *Sounds & AI settings*.

The AI part needs [Ollama](https://ollama.com) running locally with a vision model,
for example `ollama pull qwen3-vl:4b`. Everything stays on this PC. Buddy also tells the model
your current Pips focus score, so answers can take your day into account.

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
- `buddy_host.py`: the glue. It provides the BuddyWidget (a Pips subclass), the pet menu, the pet-guide overlay, and the focus context.
- `pips/`: Pips v2.28.0 source, unmodified.
- `keyit/`: KeyIt modules, patched so they can be hosted inside Buddy.
