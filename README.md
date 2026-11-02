# Buddy

Buddy = **Pips** (desktop pet + productivity tracking) + **KeyIt** (keyboard sounds)
+ a **local AI screen helper**, all in one app and one tray/pet.

## Run
1. `setup.bat` (once): creates `.venv`, installs requirements, runs the self-test.
2. `start.bat`: starts Buddy (no console).

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
- Pips data (tasks, history, config) is stored in `pips/data` and `pips/config.json`, next to the code.
- Sound and AI settings are stored in `%APPDATA%\Buddy`. You can override this with the `KEYIT_HOME` environment variable.

## Notes
- Don't run standalone Pips and Buddy at the same time. Both would track your activity, and they would
  fight over Ctrl+Alt+P.
- Pips's built-in updater is disabled inside Buddy, because updates ship with Buddy.
- `python buddy.py --selftest` runs the Pips self-test with all Buddy parts attached.

## Layout
- `buddy.py`: the launcher.
- `buddy_host.py`: the glue. It provides the BuddyWidget (a Pips subclass), the pet menu, the pet-guide overlay, and the focus context.
- `pips/`: Pips v2.28.0 source, unmodified.
- `keyit/`: KeyIt modules, patched so they can be hosted inside Buddy.
