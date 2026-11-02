# Pips

**Owner:** Juwen Lie · https://juw12.github.io/projects/pips/

An always-on-top desktop widget that turns your day into a score. It combines a
**Pomodoro timer**, looping **rain / thunder / brown-noise / café soundscapes**,
**live app-usage tracking**, a **daily productivity score with a 1–5 star rating**,
**goals / streaks / XP**, an **hour-of-day focus heatmap**, a **browsable history log**,
and a **daily AI-style coach + weekly report** — all running locally on Windows.

It lives as a small floating card you can drag anywhere. Click to expand a full
dashboard or open your history; right-click for everything else.

---

## New in 2.1 — interactive guide & in-app updates

- **Interactive first-run guide**: your pet teaches you clicking, dragging, petting and the
  right-click menu in about a minute. Replay it any time: right-click the pet → *Help* → *Interactive guide*.
- **In-app updates**: Pips checks `https://juw12.github.io/projects/pips/download/latest.json`,
  your pet announces new versions with what's new, and *Update now* downloads the zip, verifies its
  SHA-256, backs up the current files to `backup\`, installs and restarts. `config.json`, `data\`,
  `logs\` and `output\` are never touched. Turn the automatic check off in *Pet status & settings*.
- **User guide**: `Pips-Guide.pptx` in this folder (also *Help* → *User guide*).
- Version history: `CHANGELOG.json`.

---

## What you get

- **Compact floating card** (frameless, always-on-top, translucent, draggable) showing:
  a **status pill** (`● Focus` / `● Break` / `● Paused` / `○ Idle`), a **circular progress
  ring** around the Pomodoro timer with a live `%`, **cycle dots** (`●●○○ 2/4`) toward the
  long break, a **live session panel** ("This pomodoro: 12m · code.exe"), the current
  foreground app, today's productive time, a **1–5 ★ rating**, Start/Pause + Reset, a sound
  toggle, and expand/close controls.
- **Pomodoro timer** — work → short break → long break (long break every 4th),
  auto-starts the next session (configurable). Completed sessions are logged.
- **Break & health reminders** — when a work Pomodoro ends, an always-on-top popup grabs your
  attention with a rotating **stand up / stretch / drink water** tip and a soft chime; plus
  **20-20-20 eye-rest** and **hydration** nudges during work.
- **Soundscapes** — four procedurally generated, seamlessly looping ambience tracks
  (**rain, thunder, brown noise, café**). No audio files to download; adjustable volume.
- **Live app tracking** — samples the foreground window ~once per second and tallies
  minutes per app for the day, updating live. Time away from the keyboard (idle/AFK)
  is excluded from app time.
- **Daily score + stars** — a 0–100 score and 1–5 ★ rating computed from your
  productive vs. distracting vs. neutral time, idle ratio, and Pomodoros completed.
- **Goals, streaks & XP** — daily deep-work-hour and Pomodoro goals, a consecutive
  goal-day streak, and XP.
- **Hour-of-day focus heatmap** — see when you're most productive.
- **History log** — browse any past day: score, stars, Pomodoros and per-app breakdown.
- **Daily coach** — natural-language insights built from your real numbers
  (goal progress, biggest distraction, best focus hour, streak, comparison to yesterday).
  Optionally powered by an LLM (see *Customising*), and works fully offline by default.
- **Weekly report** — a self-contained HTML report for the last 7 days, opened in your
  browser from the right-click menu.

## Files

| File | Purpose |
|------|---------|
| `app.py` | The widget: tkinter UI (card + dashboard + history), Pomodoro, orchestration. |
| `tracker.py` | Foreground-app sampling, idle/AFK detection, SQLite writes. |
| `scoring.py` | Categorisation, daily score/stars, streaks/XP, heatmap aggregation. |
| `coach.py` | Coach insights, weekly report, optional LLM hook. |
| `audio.py` | Soundscape synthesis + `winsound` looping playback. |
| `maas_tokens.py` | Live MAAS (Model-as-a-Service) account token usage from the gateway. |
| `config.json` | **Edit this**: app categories, title rules, Pomodoro lengths, goals, scoring, coach. |
| `CONTRACT.md` | Internal data contract shared by all modules. |
| `Launch Widget.bat` | Double-click to start the widget with no console window. |
| `install_autostart.ps1` | Adds/removes a Start-at-login shortcut (`-Remove` to undo). |
| `make_shortcuts.ps1` | Creates Desktop + Start Menu launcher shortcuts (`-Remove` to undo). |
| `daily_email.py` | Builds and emails the daily progress report. |
| `run_daily_email.ps1` | Wrapper the 22:00 email task calls. |
| `setup_email_task.ps1` | Registers the Sun–Thu 22:00 email task. |
| `run_monthly_email.ps1` | Wrapper the monthly email task calls. |
| `setup_monthly_email_task.ps1` | Registers the monthly roll-up email (1st of month, 08:00). |
| `assets/make_icon.py` | Regenerates `assets/widget.ico` + `assets/widget.png`. |
| `assets/widget.ico` / `widget.png` | Widget icon. |
| `data/productivity.db` | Your usage database (SQLite). |
| `data/sounds/*.wav` | Generated soundscape cache. |
| `output/weekly-report-YYYY-MM-DD.html` | Weekly reports you open from the menu. |
| `logs/widget.log` | Runtime log. |

## Install Pips for Windows

1. Visit [the official Pips page](https://juw12.github.io/projects/pips/).
2. Click **Download Pips for Windows**, then open the downloaded installer.
3. Click **Install Pips**. Choose your language when the pet opens.

**No ZIP extraction, separate Python installation, terminal, or administrator
password.** Requires 64-bit Windows 10/11. The installer includes a private
Python runtime, Tkinter, Pillow, pywin32 and the optional coach's HTTP helpers.
It does not change your system Python or PATH.

Pips is added to your **Desktop** and **Start Menu**. By default it lives in
`%LOCALAPPDATA%\Programs\Pips`. Reopen it from either shortcut.
To start with Windows, opt in through the card's **Autostart** menu.

**Windows warning:** the installer is not code-signed yet. SmartScreen may warn.
If you trust this official download, choose **More info → Run anyway** if it is
available. On a managed work PC, ask IT if blocked; do not disable security tools.

### Already use Pips?

Keep your current copy and use **Help → Check for updates**. Settings and history
stay in that copy. The installer preserves data when reinstalling to the same
installed location; it does not import profiles from other ZIP folders.

### Uninstall

Quit Pips, then use **Windows Settings → Apps → Pips → Uninstall**. The program
and its runtime are removed, while local settings, progress, logs and backups
are left in place for a later reinstall. An opt-in startup shortcut is removed
only if it points at this installed copy. Back up your profile before moving it.

### Advanced: source/update archive

The ZIP remains available for in-app updates and developers, not as the normal
first-time download. A source copy needs Python 3.13+ with Tkinter, pywin32 and
Pillow. After extracting, `Start Pips.bat` starts it. The installed copy always
uses its private runtime, including when started via the helper scripts.

Configuration is stored in `config.json`; local data is in `data/`. They are
never included in a public build. Edit settings in the app where possible.

## Using the widget

- **Drag** the card to move it; its position is remembered.
- **▶ Start / ⏸ Pause** begins or pauses a Pomodoro; **↻ Reset** clears it.
- The **sound button** (shows `Sound` / `Muted`) toggles the current soundscape.
- The **★☆☆☆☆** row is today's star rating; the slim bar under the timer is session progress.
- **Right-click** the card for: **Dashboard**, **History**, **Weekly Report**, **Sound**
  (pick rain / thunder / brown / café), **Settings**, **Autostart**, **Quit**.
- **⌄** expands the dashboard; **✕** quits.

### Dashboard
Six Canvas-drawn, interactive charts:
- **Donut** — today's productive / neutral / distracting split, with a legend.
- **Top apps** — ranked horizontal bars, colour-coded by category.
- **7-day score trend** — daily score bars with star markers.
- **Hour-of-day heatmap** — a 7-day × 24-hour grid with a `less ▢▢▢▢▢ more` legend.
- **Focus timeline** — today's hours as one colour-coded strip (productive / neutral / distracting).
- **Pomodoros** — completed Pomodoros per day across the last 7 days.

Plus goals progress, streak/XP, and the daily coach text. **Hover** any chart element for a
tooltip; **click** a segment/bar/cell to inspect it. The dashboard scrolls (mouse wheel).

### History
Step through previous days (Prev / Next / Today) and see each day's score, stars,
Pomodoros and app breakdown.

### MAAS token usage

The **MAAS Token Usage (account)** dashboard panel shows live token usage for your
company **MAAS** account, read from the gateway REST API:

- today's **total** tokens (big number) with the prompt/completion split,
- a **7-day bar chart** of daily account totals,
- today's **per-model** breakdown (top models by tokens).

The compact card also gains a `MAAS · <total> tok today` line, and the right-click
menu has **Refresh MAAS Tokens** to force an immediate refresh.

Add your MAAS credentials to `config.json`:

```jsonc
"maas": {
  "enabled": true,
  "base_url": "https://ai-gateway.example.com/maas/api/v1",
  "username": "your.username",
  "password": "your-password",
  "verify_ssl": true,
  "cache_minutes": 10
}
```

Usage is fetched **at most once every ~5 minutes** (and cached for `cache_minutes`),
never during `--selftest`. With no credentials (or the module/config missing) the
widget **degrades gracefully**: the panel shows *No MAAS data*, the card line stays
empty, and nothing is fetched.

## Break & health reminders

When a work Pomodoro **finishes**, an always-on-top popup grabs your attention with a rotating
tip to **stand up, stretch, walk, or drink water** (plus a soft chime), a `Done` / `Snooze 5 min`
pair, and a live countdown of the break. During a work session it also nudges you:

- **20-20-20 eye rest** every 20 minutes (look 20 feet away for 20 seconds).
- **Hydration** every ~40 minutes.

The chime temporarily ducks any playing soundscape, then resumes it. Control it all in
`config.json` → `reminders`.

## Email a daily progress report

A scheduled task emails you a **chart-based daily progress report** (newspaper-styled,
self-contained HTML) at **22:00 Sunday–Thursday**, so it is waiting in your inbox on **weekday
mornings**. It sends through your Outlook desktop via the `outlook-agent` skill, exactly like
The Morning Paper.

The report is built from **chart visuals** — a score bar, KPI tiles, a **time-split** stacked
bar, a **focus-timeline** (today by hour), a **7-day score** chart, a **Top apps** bar chart,
**Pomodoros (7 days)**, and a **separate 7-day daily chart for each factor** (deep work,
distraction, neutral, idle, app switches, focus). All charts are drawn with email-safe HTML
tables so they render in Outlook's Word engine, Gmail, and a browser alike.

```powershell
powershell -ExecutionPolicy Bypass -File setup_email_task.ps1                     # register
Start-ScheduledTask -TaskName "ProductivityWidgetEmail"                            # send one now
Unregister-ScheduledTask -TaskName "ProductivityWidgetEmail" -Confirm:$false       # remove
```

Manual / preview:

```powershell
python daily_email.py             # today's report; sends it
python daily_email.py --no-email  # write output\daily-report-YYYY-MM-DD.html only
python daily_email.py --preview   # write + open it in the browser
python daily_email.py --date 2026-10-20
python daily_email.py --monthly   # month roll-up; emails the previous month
```

**Monthly roll-up:** a second task emails the **monthly report** on the **1st of each month at
08:00** (reporting the previous month). Register it once:

```powershell
powershell -ExecutionPolicy Bypass -File setup_monthly_email_task.ps1
```

Outlook must be installed, signed in and running for the send to work; if it isn't, the HTML is
still written and a warning goes to `logs\email.log`. Configure recipients and schedule days in
`config.json` → `email`.

## Customising

Open `config.json`:

- **`category.productive` / `category.distracting` / `category.neutral`** — add app
  executables (lowercased, e.g. `"code.exe"`) to the right bucket. Anything unlisted
  counts as *neutral*.
- **`title_rules`** — categorise by window **title** substring (e.g. a browser tab
  showing `github` → productive, `youtube` → distracting). These take priority over
  the app lists.
- **`pomodoro`** — `work_minutes`, `short_break_minutes`, `long_break_minutes`,
  `long_break_every`, `auto_start_next`.
- **`audio`** — `default` sound, `autoplay_on_focus` (start a soundscape when a focus
  session begins), `stop_on_break`, `volume`.
- **`tracking`** — `poll_seconds`, `idle_seconds` (how long before you count as AFK).
- **`goals`** — `deep_work_hours`, `pomodoros`, `max_distraction_hours`.
- **`scoring`** — `weights` and `star_thresholds`.
- **`email`** — `enabled`, `to`, `cli` (outlook-agent CLI path), `subject_prefix`, and the
  schedule (`send_hour`, `send_days`).
- **`reminders`** — `enabled`, `chime`, `break_popup`, `eye_enabled` / `eye_every_minutes`,
  `hydration_enabled` / `hydration_every_minutes` (add `break_tips` / `eye_tips` /
  `hydration_tips` arrays to override the default rotation).
- **`coach`** — `enabled`, and an optional `llm` block (`enabled`, `endpoint`,
  `api_key`, `model`) pointing at an OpenAI-compatible chat endpoint. When disabled
  (default), the coach is fully offline and rule-based.
- **`maas`** — `enabled`, `base_url`, `username`, `password` (your MAAS
  account; blank disables the panel), `verify_ssl`, `cache_minutes`.
- **`ui`** — `alpha` (card opacity), `pos` (saved window position), `compact_width`,
  `card_height`.

After editing, restart the widget (`python app.py`).

## How the score works

```
base  = 100 * (productive + 0.3 * neutral) / max(1, productive + neutral + 1.5 * distracting)
score = clamp(0..100, base * (1 - 0.25 * idle_ratio) + pomodoro_bonus)
```

- `pomodoro_bonus` = up to +5 for completing your daily Pomodoro goal.
- **Stars**: 1★ < 20, 2★ < 40, 3★ < 60, 4★ < 80, else 5★ (thresholds in `config.json`).
- **XP** = `productive_minutes // 25 * 10 + pomodoros_done * 20 + (50 if goal_met else 0)`.
- **Goal met** = you reached `deep_work_hours` *and* stayed under `max_distraction_hours`.

The score is monotonic: more productive time never lowers it unless distraction grows too.

## Data & privacy

- **Focus history is stored locally** in `data/productivity.db` (SQLite).
- Friends, parties, chat, profile photos and other sharing features use the
  Pips online service when you enable/use them. Automatic update checks also
  contact the official download site.
- The optional coach LLM and MAAS gateway are used only if configured/enabled.
  MAAS is off by default in public builds. Keep private configuration private.
- The tracker never counts the widget itself as usage, and never counts idle/AFK time
  toward an app.

## Notes & troubleshooting

- **No sound?** The widget does not auto-play audio at launch. Start a soundscape via
  the sound button or the right-click **Sound** menu. Sound is played with `winsound`
  (one track at a time); volume is baked into cached WAVs under `data/sounds/`.
- **An app is scored wrongly?** Add it to `category.*` or a `title_rules` entry in
  `config.json`.
- **Score looks low at the start of the day?** The score reflects time so far; with
  little productive time it stays low until you do focused work.
- **Want a clean slate?** Stop the widget and delete `data/productivity.db`.
- **Widget vanished behind windows?** It is `-topmost`; if another always-on-top app
  overlaps, use the taskbar/Alt-Tab or restart it.
- Logs are in `logs/widget.log` if something misbehaves.

## Desktop pet ("Pip")

A tiny animated pet now lives on your desktop / taskbar (`pet.py`). It replaces the
"always-visible card" as the way into Pips:

- **Click** it -> tickle animation and the Pips card pops up next to it
  (or set *When I click -> Just tickle*; then **double-click** opens Pips).
- **Drag** it anywhere. Drop it on (or just above) the **taskbar** and it lives there and
  wanders left/right on its own. Turn on *Gravity* and it falls to the taskbar when dropped.
- **Rub** the mouse back and forth over it -> petting (hearts). **Shake** it while
  dragging -> it gets dizzy. Tickle it too much -> it gets grumpy and pouts.
- **Moods** - very happy / happy / okay / sad / very sad / tired / hungry / grumpy /
  sleeping - driven by happiness, energy, food and bond. Petting, snacks and finished
  pomodoros make it happy; shaking, over-tickling, neglect and hunger make it sad.
- **Sleeps** when tired or when you're away (default 5 min idle) and wakes up with
  "welcome back!" when you return. Click to wake it (it's grumpy if still tired).
- **Chat bubbles** for break / eye / hydration reminders, pomodoro done, daily goal,
  and a gentle nudge after ~2 min on a distracting app during focus.
- Wears a red **focus headband** during a work pomodoro; hides during full-screen apps.
- **23 animals in 3 groups** - Land (cat, dog, bunny, bear, panda, fox, hamster, piggy, sprout) walk;
  Water (fish, whale, octopus, axolotl, penguin, turtle, frog, jellyfish) swim/float with bubbles;
  Sky (bird, owl, bat, bee, butterfly, dragon) hover and fly with flapping wings.
- **Right-click** -> snack, sleep/wake, animal, colour, size, language,
  click behaviour, *Pet status & settings...* (live stats + every option).

Settings are stored in `config.json -> "pet"`; the pet's state in `data/pet_state.json`.
The card's x button now hides the card to the pet; use right-click -> Quit to exit.

## Languages

English, Bahasa Indonesia, 繁體中文 and 简体中文 (`i18n.py`). On first launch Pips asks which
language to use (pre-selecting your Windows language). Change it any time from the card's or the
pet's right-click menu -> Language, or in Pet settings - the UI switches live without resetting
the timer. Run `pythonw app.py --choose-language` to see the picker again. Generated reports,
the diary and coach texts stay in English.
