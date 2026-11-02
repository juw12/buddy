# Pips — Tokens & Diary Extension Contract

Addendum to `CONTRACT.md`. Same rules apply, non-negotiable:

- **Python 3.13 on Windows**, **standard library + `pywin32` + `Pillow` ONLY**. No new third-party deps.
- Modules are **importable without side effects** (no GUI / thread / network / file writes at import time).
- All paths resolve relative to `Path(__file__).parent` unless configured otherwise.
- **Never raise** out of a public API on transient failure — log to `logs/widget.log` and degrade gracefully.
- Every module must keep the existing DB (`data/productivity.db`) intact; only ADD tables.

This extension adds two modules (`tokens.py`, `diary.py`) plus a separate **diary repo** and a **web dashboard**.

---

## 1. `tokens.py` — AI token usage from the OpenCode log

**Data source (verified):** `~/.local/share/opencode/log/opencode.log`

Each usage line looks like:

```
timestamp=2026-10-21T10:56:04.274Z level=INFO run=4069de62 message="otel: api_request" sessionID=ses_... model=coder-model agent="Sisyphus - ultraworker" cost_usd=0 duration_ms=4397 input_tokens=34857 output_tokens=12
```

Notes:
- `timestamp` is **UTC** (`...Z`). Convert to **local time** (`Asia/Bangkok`) before deriving the day.
- Values may be **quoted** (`agent="Sisyphus - ultraworker"`) or bare. Parse both.
- Only lines containing `otel: api_request` are usage records. Ignore everything else.
- The file is appended to and **may rotate** (a new file may replace it, shrinking size).

### Public API

```python
def ensure_db(con) -> None
def log_path() -> str                       # resolved opencode log path (config override wins)
def parse_line(line: str) -> dict | None    # -> record dict or None if not a usage line
def refresh(con, log_path: str | None = None) -> int
    # Incrementally parse NEW bytes since the last stored offset; insert rows; return #rows added.
    # Offset stored in meta key 'tokens_log_offset'. If file size < offset, treat as rotated -> reset offset to 0.
    # Deduplicate by (ts, session_id, input_tokens, output_tokens).
def today_summary(con) -> dict              # {"input","output","total","requests","last_ts"}
def daily_totals(con, days: int = 7) -> dict # {"YYYY-MM-DD": {"input","output","total","requests"}}
def by_model(con, day: str) -> list         # [{"model","input","output","total"}] desc
def by_agent(con, day: str) -> list         # [{"agent","input","output","total"}] desc
def format_tokens(n: int) -> str            # 1234567 -> "1.23M", 12345 -> "12.3k", 950 -> "950"
```

### New DB table

```sql
CREATE TABLE IF NOT EXISTS token_usage (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,      -- ISO LOCAL timestamp
  day           TEXT NOT NULL,      -- local date 'YYYY-MM-DD'
  session_id    TEXT,
  model         TEXT,
  agent         TEXT,
  input_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_token_usage_day ON token_usage(day);
```

### Record dict shape (returned by `parse_line`)

```python
{"ts": "2026-10-21 17:56:04", "day": "2026-10-21", "session_id": "ses_...",
 "model": "coder-model", "agent": "Sisyphus - ultraworker",
 "input_tokens": 34857, "output_tokens": 12, "total_tokens": 34869}
```

### Behaviour rules
- INCREMENTAL only: never re-parse the whole 14 MB file on every tick. Track byte offset in `meta`.
- Thread-safe write: use the passed `con` (the app already provides one). Wrap in try/except.
- Handle a missing log file gracefully (`refresh` returns 0, logs once).

---

## 2. `diary.py` — auto-generated daily productivity diary

Produces a **Markdown entry per day** plus a machine-readable **JSON** for the web dashboard.

### Public API

```python
def ensure_db(con) -> None                  # no-op allowed; diary reads existing tables
def build_entry(con, cfg: dict, day: str) -> str
    # Markdown for ONE day. Sections: header (date, weekday), KPI line (score, stars,
    # pomodoros, deep-work hh mm), AI tokens (input/output/total/requests), top apps table,
    # and the coach paragraph (coach.daily_coach) when coach is available.
def write_entry(con, cfg: dict, day: str, repo_dir: str) -> str
    # Writes <repo_dir>/diary/YYYY-MM-DD.md ; returns the path written.
def write_index(con, cfg: dict, repo_dir: str, days: int = 30) -> str
    # Writes <repo_dir>/diary/README.md — reverse-chron index with a one-line summary per day.
def build_json(con, cfg: dict, days: int = 30) -> dict
    # Structured data for the web dashboard (see schema below).
def write_json(con, cfg: dict, repo_dir: str, days: int = 30) -> str
    # Writes <repo_dir>/data.json ; returns path.
```

### data.json schema (consumed by the web dashboard)

```jsonc
{
  "generated_at": "2026-10-22T18:40:00+07:00",
  "totals": { "days": 30, "score_avg": 61.2, "tokens_total": 1234567, "pomodoros_total": 42 },
  "days": [
    {
      "day": "2026-10-22",
      "weekday": "Thursday",
      "score": 78.0, "stars": 4, "pomodoros": 6,
      "deep_work_seconds": 18000, "productive_seconds": 18000,
      "neutral_seconds": 3000, "distracting_seconds": 1200, "idle_seconds": 900,
      "tokens": { "input": 812345, "output": 6200, "total": 818545, "requests": 214 },
      "top_apps": [ { "app": "code.exe", "category": "productive", "seconds": 5400 } ],
      "coach": "…"
    }
  ]
}
```

`days` is **newest-first**. Missing data → zeros / empty list (never null the whole entry).

---

## 3. New diary repo (separate git repo, NOT the widget)

Target: **`https://github.com/juw12/diary.git`**  (repo is named `diary`)
Local path: `C:\Users\you\Desktop\opencode_project\productivity-diary`

Layout:
```
productivity-diary/
  diary/YYYY-MM-DD.md      # auto-generated entries (written by the widget)
  diary/README.md          # auto-generated index
  index.html               # web dashboard (static, GitHub Pages)
  data.json                # generated by the widget (dashboard data source)
  README.md                # human intro
  .github/workflows/pages.yml   # deploy index.html to GitHub Pages
  publish.ps1              # commit + push (uses the installed git, not on PATH)
  create_repo.ps1          # one-time repo creation helper (see below)
  .gitignore
```

Rules:
- The web dashboard (`index.html`) **must work with zero backend**: fetch `./data.json`, render client-side. No absolute paths (must work on GitHub Pages project sites).
- `publish.ps1` uses `C:\Users\you\AppData\Local\Programs\Git\cmd\git.exe` (git is NOT on PATH). Auth via the existing Git Credential Manager (`git credential manager`), which is already configured for the `juw12` account.
- **Repo creation** requires auth we may not have non-interactively. `create_repo.ps1` should:
  1. Try `git credential fill` (non-interactive, `GIT_TERMINAL_PROMPT=0`) to obtain the stored GitHub token, then
  2. `POST https://api.github.com/user/repos` via `curl.exe` with `{"name":"productivity-diary","private":true}`,
  3. Never PRINT the token; if it cannot get one, exit with a clear message telling the user to create the empty repo once on github.com (then `publish.ps1` works).

---

## 4. `app.py` integration (UI)

Add, following existing THEME + patterns:
- **Card**: a tokens line (e.g. under `day_strip_label`): `AI · 1.23M tok today`.
- **Dashboard**: a new `LabelFrame` "AI Tokens (Last 7 days)" with a Canvas bar chart of daily total tokens + today's in/out/requests, added in `_build_dashboard`, drawn in `_update_dashboard` via a new `_draw_tokens_chart()`.
- **Right-click menu** (`_show_card_menu`): add `Generate Diary Now` → `diary.write_entry` + `write_index` + `write_json`; `Open Diary Folder`; `Publish Diary to GitHub` (runs `publish.ps1` detached).
- **`import_modules()`**: import `tokens as tokens_mod` and `diary as diary_mod` with graceful degradation (same pattern as siblings).
- **`_update_card`**: call `tokens.today_summary(self.con)` (cheap; cached) and update the label. Call `tokens.refresh(self.con)` on a slower cadence (every ~30 s) to ingest new log lines.
- **Config** additions (merge with defaults; ignore if absent):
  ```jsonc
  "tokens": { "enabled": true, "log_path": "" },
  "diary":  { "enabled": true,
              "repo_dir": "C:\\Users\\you\\Desktop\\opencode_project\\productivity-diary",
              "auto_write": true, "auto_publish": false }
  ```

---

## 5. Verification (all agents)

- `python app.py --selftest` must still exit `0`.
- `tokens.py` / `diary.py`: provide a stdlib test (`tests/` style) that uses a **temp DB + a tiny fixture log** and exits `0`/`1`.
- Do NOT break existing commands (`--no-track`, `--reset-pos`).

---

## 6. `maas_tokens.py` — MAAS account token usage

Live token usage for your company **MAAS** account, read from the gateway
REST API. Standard library only (`urllib.request`, `urllib.parse`,
`urllib.error`, `json`, `ssl`, `datetime`, `time`). TLS verification is **always
on** (default `ssl` context — never disabled). Importable with **zero side
effects**; every public function catches its own errors and returns a safe
default — it never raises to the caller.

### API (verified)

Base URL: `https://ai-gateway.example.com/maas/api/v1`

- **Login** — `POST {base}/auth/login`, header `Content-Type: application/x-www-form-urlencoded`,
  body `username=<u>&password=<p>` → JSON containing `access_token` (string) and
  `user` (`{username,email,full_name}`).
- **Daily usage** — `GET {base}/token-usage/daily`, header `Authorization: Bearer <token>`
  → `{"date", "total_tokens", "breakdown": [{"ai_model", "prompt_tokens",
  "completion_tokens", "model_type", "total_tokens", ...}]}`.
- **Range usage** — `GET {base}/token-usage/range?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`,
  header `Authorization: Bearer <token>` → `{"period", "data": [{"date", "total_tokens"}]}`.

`total_tokens` values are **floats**. A missing/expired bearer yields HTTP 401
`{"detail":"Not authenticated"}` → the module **re-logs-in once and retries**,
then gives up gracefully.

### Config block (`config.json` → `"maas"`)

```jsonc
"maas": {
  "enabled": true,
  "base_url": "https://ai-gateway.example.com/maas/api/v1",
  "username": "",
  "password": "",
  "verify_ssl": true,
  "cache_minutes": 10
}
```

A missing/invalid config (or missing `config.json`) falls back to **disabled**.

### Public API

```python
def enabled() -> bool
    # True only if maas.enabled AND username+password are non-empty.
def today_summary(force: bool = False) -> dict
    # {"ok","date","total","prompt","completion","models","error"};
    # models = [{"model","prompt","completion","total"}] sorted by total desc.
def daily_totals(days: int = 7, force: bool = False) -> dict
    # {"YYYY-MM-DD": int} ascending, zero-filled for days with no data.
def format_tokens(n) -> str        # 1234567 -> "1.23M", 12345 -> "12.3k", 950 -> "950"
def invalidate_cache() -> None     # drop cached token + usage responses + logged-failure set
```

### Behaviour rules
- **Caching**: the `access_token` is cached and reused for ~25 min (or until a 401
  forces a re-login + one retry); usage responses are cached for
  `maas.cache_minutes` (default 10). `force=True` bypasses the usage cache.
- **No network at import, and none during `python app.py --selftest`.** The widget
  fetches only from the slow periodic updater (~every 5 min, off the UI thread),
  never from the render/draw path (the chart draws only from the cached dict).
- **Failure logging is de-duplicated** — each distinct failure reason is logged at
  most once per process; `invalidate_cache()` resets it.
