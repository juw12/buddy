# Pips — Internal Contract

Every module obeys this file. If a field name, function signature, or DB column
changes, update **all** consumers. Modules must be **importable without side effects**
(no work at import time; no GUI/thread/network at import).

- Python 3.13 on Windows 10. **Standard library + `pywin32` + `Pillow` only.**
  Do NOT add third-party deps. Do NOT use `psutil`, `pygame`, `playsound`.
- All file paths resolve relative to the project folder (`Path(__file__).parent`).
- Never raise out of the public API on a transient failure — log to `logs/widget.log`
  and degrade gracefully.

## Files

| File | Role |
|------|------|
| `config.json` | user settings (schema below) |
| `tracker.py` | foreground-window sampling → sqlite |
| `scoring.py` | pure metrics: categorise, score, stars, streaks/XP, heatmap |
| `coach.py` | narrative insights + weekly report (+ optional LLM hook) |
| `audio.py` | soundscape synthesis + `winsound` playback |
| `app.py` | tkinter UI (compact floating card + dashboard) + orchestration |
| `data/productivity.db` | sqlite database |
| `data/sounds/` | generated `.wav` cache |
| `logs/widget.log` | runtime log |

## SQLite schema (`data/productivity.db`)

`tracker.ensure_db(con)` creates these idempotently. WAL mode on.

```sql
CREATE TABLE IF NOT EXISTS app_usage (
  day   TEXT NOT NULL,          -- 'YYYY-MM-DD' local date
  app   TEXT NOT NULL,          -- lowercased exe basename, e.g. 'chrome.exe'
  title TEXT,                   -- most recent window title seen
  seconds INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, app)
);

CREATE TABLE IF NOT EXISTS hour_usage (
  day   TEXT NOT NULL,
  hour  INTEGER NOT NULL,       -- 0..23 local
  app   TEXT NOT NULL,
  seconds INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, hour, app)
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,           -- 'work' | 'short_break' | 'long_break'
  task TEXT,
  started_at TEXT NOT NULL,     -- ISO local
  ended_at TEXT,
  completed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_summary (
  day TEXT PRIMARY KEY,
  productive_seconds INTEGER NOT NULL DEFAULT 0,
  neutral_seconds   INTEGER NOT NULL DEFAULT 0,
  distracting_seconds INTEGER NOT NULL DEFAULT 0,
  idle_seconds      INTEGER NOT NULL DEFAULT 0,
  focus_seconds     INTEGER NOT NULL DEFAULT 0,
  app_switches      INTEGER NOT NULL DEFAULT 0,
  pomodoros_done    INTEGER NOT NULL DEFAULT 0,
  score REAL,                   -- 0..100
  stars INTEGER,                -- 1..5
  goal_met INTEGER NOT NULL DEFAULT 0,
  xp INTEGER NOT NULL DEFAULT 0,
  coach_text TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
```

## config.json schema

```jsonc
{
  "pomodoro": { "work_minutes": 25, "short_break_minutes": 5, "long_break_minutes": 15,
                "long_break_every": 4, "auto_start_next": true },
  "audio":    { "default": "rain", "autoplay_on_focus": true, "stop_on_break": false, "volume": 60 },
  "tracking": { "poll_seconds": 1, "idle_seconds": 120 },
  "category": {
    "productive": ["code.exe","windowsterminal.exe","devenv.exe","pycharm64.exe","excel.exe","winword.exe","powerpnt.exe","notepad.exe","explorer.exe"],
    "distracting": ["steam.exe","discord.exe","spotify.exe","tiktok.exe","netflix.exe"],
    "neutral": []
  },
  "title_rules": [
    { "match": "youtube", "category": "distracting", "where": "title" },
    { "match": "reddit",  "category": "distracting", "where": "title" },
    { "match": "github",  "category": "productive",  "where": "title" }
  ],
  "goals":    { "deep_work_hours": 4, "pomodoros": 8, "max_distraction_hours": 2 },
  "scoring":  { "weights": { "productive": 1.0, "neutral": 0.3, "distracting": -0.8, "idle": 0.0 },
                "star_thresholds": [20, 40, 60, 80] },
  "coach":    { "enabled": true, "llm": { "enabled": false, "endpoint": "", "api_key": "", "model": "" } },
  "ui":       { "alpha": 0.94, "pos": null, "compact_width": 260 }
}
```

Unknown keys are ignored; missing keys fall back to these defaults.

## `tracker.py`

```python
def ensure_db(con) -> None
def load_config(path: str | None = None) -> dict          # returns merged config (defaults + file + env PROXY honored by callers only)

class Tracker:
    def __init__(self, cfg: dict, db_path: str, base_dir: str): ...
    def sample(self) -> dict          # one shot: {"app","title","pid","idle_seconds","idle":bool,"ts"}
    def start(self) -> None           # daemon thread; polls cfg['tracking']['poll_seconds']
    def stop(self) -> None
    def current(self) -> dict         # latest sample (never None; safe defaults)
    def today_totals(self) -> dict    # {"YYYY-MM-DD": {app: seconds, ...}}
    def day_totals(self, day: str) -> dict   # {app: seconds}
    def flush(self) -> None           # force write current second
```

Behaviour:
- Active window via `win32gui.GetForegroundWindow()`; PID via `win32process.GetWindowThreadProcessId`;
  exe path via `win32process.GetModuleFileNameEx` on an `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` handle.
  Wrap every win32 call in try/except; on failure return app `"unknown"`.
- If `app.py` is the foreground window, attribute to the **previous** non-self app
  (skip self so the widget doesn't measure itself). Detect self by pid.
- Idle: `win32api.GetLastInputInfo()` + `GetTickCount()` → seconds since last input.
  When `idle_seconds >= cfg['tracking']['idle_seconds']`, accrue to `idle_seconds` for the
  current `app` row's day instead of that app's `seconds`.
- Each poll adds `poll_seconds` to `app_usage[day][app].seconds` and `hour_usage[day][hour][app].seconds`.
  Increment an `app_switches` counter in `meta` (`switch:<day>`) when the foreground app changes.
- Use a single `sqlite3` connection guarded by a `threading.Lock`. Commit every poll (cheap at 1 Hz).

## `scoring.py` — pure functions, no I/O except the passed `con`

```python
def categorize(app: str, title: str, cfg: dict) -> str
    # -> "productive" | "neutral" | "distracting". title_rules first, then category lists, else "neutral".

def compute_daily(con, day: str, cfg: dict) -> dict
    # -> {"day","productive_seconds","neutral_seconds","distracting_seconds","idle_seconds",
    #     "focus_seconds","app_switches","pomodoros_done","score","stars","goal_met","xp","breakdown":{app:seconds}}

def score_to_stars(score: float, cfg: dict) -> int      # thresholds -> 1..5
def aggregate_hours(con, days: int = 30) -> dict        # {hour: total_seconds} over last N days, productive only
def current_streak(con, cfg: dict) -> dict              # {"streak_days":int,"total_xp":int,"best_day":str,"goal_days":int}
def save_daily(con, day: str, data: dict) -> None       # upsert into daily_summary
def breakdown(con, day: str) -> list                     # [{"app","category","seconds"}...] sorted desc
```

Score rule (document in code): `score = clamp(0..100, 100 * (productive + 0.3*neutral) / max(1, productive+neutral+distracting*1.5) )`
then adjust for idle ratio and pomodoro completion; keep it monotonic (more productive time never lowers score
unless distraction grows). XP = `productive_minutes//25 * 10 + pomodoros_done * 20 + (50 if goal_met else 0)`.

## `coach.py`

```python
def insights(con, day: str, cfg: dict) -> list[str]     # rule-based natural-language lines, always works offline
def daily_coach(con, day: str, cfg: dict) -> str        # joined paragraph; uses LLM if cfg['coach']['llm']['enabled'] and key set, else insights
def weekly_report(con, cfg: dict, end_day: str) -> str  # returns a self-contained HTML string (7 days)
def top_distraction(con, day: str, cfg: dict) -> str | None
def best_focus_hour(con, days: int = 14) -> int | None
```

Insights must reference **real numbers** from the DB (e.g. "You logged 4h 12m deep work —
2h ahead of yesterday"). LLM call (when enabled) uses `requests` with the configured
endpoint/key/model and a compact JSON payload; on any error, fall back to rule-based output.

## `audio.py`

```python
class SoundEngine:
    def __init__(self, cfg: dict, base_dir: str): ...
    def sounds(self) -> list[str]              # ["rain","thunder","brown","cafe"]
    def ensure_assets(self) -> None            # synthesize missing wavs into data/sounds/
    def play(self, name: str = "rain") -> None # start looping (async). Idempotent.
    def stop(self) -> None
    def toggle(self, name: str | None = None) -> bool   # returns new is_playing
    def is_playing(self) -> bool
    def set_volume(self, percent: int) -> None          # 0..100; re-synth cached gain file, resume if playing
```

Rules: synthesise **seamlessly loopable** mono 22.05 kHz 16-bit WAVs (~10 s).
Volume implemented by pre-scaling samples; cache per integer percent in `data/sounds/<name>_<vol>.wav`.
Use `winsound.PlaySound(path, SND_FILENAME | SND_ASYNC | SND_LOOP)`.
`stop()` = `winsound.PlaySound(None, SND_PURGE)`. Never block the UI thread (synthesis on demand is OK, ~1 s).

## `app.py`

```python
def main(argv=None) -> int
# flags: --selftest  (init everything, render offscreen, do NOT enter mainloop, exit 0)
#        --no-track  (skip starting tracker)
#        --reset-pos (ignore saved position)
if __name__ == "__main__": raise SystemExit(main())
```

UI requirements:
- Frameless (`overrideredirect(True)`), `-topmost True`, `-alpha` from config, draggable by mouse.
- **Compact card** (default): pomodoro ring/label + start/pause/reset, current app name + today's
  productive time, star rating, sound toggle. Small "expand" chevron + close; right-click menu
  with: Dashboard, History, Sound, Settings, Quit, Autostart.
- **Dashboard** (expanded window): today's breakdown bars, hour-of-day heatmap
  (`scoring.aggregate_hours`), goals progress, streak/XP, coach text (`coach.daily_coach`).
- **History**: date navigation (prev/next/jump-to-today) showing that day's breakdown, score,
  stars, pomodoros, app list — read from `daily_summary` + `breakdown()`.
- Pomodoro state machine: work → short/long break → repeat; writes `sessions` rows and,
  on work completion, `pomodoros_done`. Optional autoplay sound on focus start.
- Refresh the card every 1 s (tracker current + today totals + stars).
- Save window position to `config.json` on move; graceful `--selftest`.
- Keep the widget out of its own measurements (tracker already skips self).
