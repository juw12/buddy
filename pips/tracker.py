"""Foreground-window usage tracker for Pips.

Samples the active window (app exe basename + title + pid + idle state) and
persists per-second usage into sqlite. See CONTRACT.md for the locked interface.

Importing this module has NO side effects: no threads, no DB access, no logging
handlers, no GUI. Everything happens when a ``Tracker`` is constructed / started.

Idle / AFK approach
-------------------
``win32api.GetLastInputInfo()`` returns the tick (ms since boot) of the last
keyboard/mouse input; ``win32api.GetTickCount()`` is the current tick. Their
difference is the idle time. The subtraction is masked with ``0xFFFFFFFF`` to
tolerate the ~49.7-day wrap of the 32-bit tick counter. When the idle time
reaches ``cfg['tracking']['idle_seconds']`` the polled second is accrued to that
day's idle bucket (``daily_summary.idle_seconds``) instead of the app's seconds,
so AFK time never inflates an app's active usage.

Self-measurement guard
----------------------
When the foreground window belongs to THIS process (``pid == os.getpid()``) we
do not attribute time to the widget itself; the previously seen non-self app is
used instead, so the widget never pollutes its own metrics.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

try:  # pywin32 — validated on Windows 10 / Python 3.13
    import win32api
    import win32gui
    import win32process
except Exception:  # pragma: no cover - non-Windows / missing pywin32
    win32api = win32gui = win32process = None


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "productivity.db"
LOG_PATH = BASE_DIR / "logs" / "widget.log"


# --------------------------------------------------------------------------- #
# Logging (lazily configured so import stays side-effect free)
# --------------------------------------------------------------------------- #
_logger = logging.getLogger("productivity.tracker")
_logger.addHandler(logging.NullHandler())
_logger_ready = False


def _get_logger() -> logging.Logger:
    """Attach a file handler to logs/widget.log on first use. Never raises."""
    global _logger_ready
    if not _logger_ready:
        _logger_ready = True
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
            _logger.addHandler(handler)
            _logger.setLevel(logging.INFO)
        except Exception:
            pass
    return _logger


def _warn(message: str) -> None:
    try:
        _get_logger().warning(message)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "pomodoro": {
        "work_minutes": 25,
        "short_break_minutes": 5,
        "long_break_minutes": 15,
        "long_break_every": 4,
        "auto_start_next": True,
    },
    "audio": {
        "default": "rain",
        "autoplay_on_focus": True,
        "stop_on_break": False,
        "volume": 60,
    },
    "tracking": {"poll_seconds": 1, "idle_seconds": 120},
    "category": {
        "productive": [
            "code.exe", "windowsterminal.exe", "devenv.exe", "pycharm64.exe",
            "excel.exe", "winword.exe", "powerpnt.exe", "notepad.exe", "explorer.exe",
        ],
        "distracting": [
            "steam.exe", "discord.exe", "spotify.exe", "tiktok.exe", "netflix.exe",
        ],
        "neutral": [],
    },
    "title_rules": [
        {"match": "youtube", "category": "distracting", "where": "title"},
        {"match": "reddit", "category": "distracting", "where": "title"},
        {"match": "github", "category": "productive", "where": "title"},
    ],
    "goals": {"deep_work_hours": 4, "pomodoros": 8, "max_distraction_hours": 2},
    "scoring": {
        "weights": {"productive": 1.0, "neutral": 0.3, "distracting": -0.8, "idle": 0.0},
        "star_thresholds": [20, 40, 60, 80],
    },
    "coach": {
        "enabled": True,
        "llm": {"enabled": False, "endpoint": "", "api_key": "", "model": ""},
    },
    "ui": {"alpha": 0.94, "pos": None, "compact_width": 260},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` (mutates and returns base)."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | None = None) -> dict:
    """Return the built-in defaults deep-merged with the JSON file at ``path``.

    Missing file or malformed JSON falls back to defaults (missing keys never
    crash the caller). ``path=None`` resolves to ``<base_dir>/config.json``.
    """
    cfg = copy.deepcopy(DEFAULTS)
    cfg_path = Path(path) if path else (BASE_DIR / "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _deep_merge(cfg, data)
    except FileNotFoundError:
        pass
    except Exception as exc:  # malformed JSON / IO — degrade to defaults
        _warn(f"load_config: could not read {cfg_path}: {exc}")
    return cfg


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_usage (
  day   TEXT NOT NULL,
  app   TEXT NOT NULL,
  title TEXT,
  seconds INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, app)
);

CREATE TABLE IF NOT EXISTS hour_usage (
  day   TEXT NOT NULL,
  hour  INTEGER NOT NULL,
  app   TEXT NOT NULL,
  seconds INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, hour, app)
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  task TEXT,
  started_at TEXT NOT NULL,
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
  score REAL,
  stars INTEGER,
  goal_met INTEGER NOT NULL DEFAULT 0,
  xp INTEGER NOT NULL DEFAULT 0,
  coach_text TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def ensure_db(con: sqlite3.Connection) -> None:
    """Create every contract table idempotently and enable WAL mode."""
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA_SQL)
    con.commit()


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
_DEFAULT_SAMPLE = {
    "app": "unknown",
    "title": "",
    "pid": 0,
    "idle_seconds": 0,
    "idle": False,
    "ts": "",
}


class Tracker:
    """Sample the foreground window and persist usage into sqlite."""

    def __init__(self, cfg: dict, db_path: str, base_dir: str):
        self.cfg = cfg or {}
        self.db_path = str(db_path)
        self.base_dir = str(base_dir)

        tracking = self.cfg.get("tracking", {}) or {}
        self._poll = max(1, int(tracking.get("poll_seconds", 1) or 1))
        self._idle_threshold = int(tracking.get("idle_seconds", 120) or 0)

        self._lock = threading.Lock()
        self._con: sqlite3.Connection | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._running = False

        self._last: dict = dict(_DEFAULT_SAMPLE)
        self._last_app = "unknown"          # last non-self foreground app
        self._prev_effective: str | None = None  # for app-switch counting

    # -- low level win32 helpers ------------------------------------------- #
    @staticmethod
    def _exe_basename(pid: int) -> str | None:
        """Lowercased exe basename for ``pid`` via pywin32, or None on error."""
        if win32api is None or win32process is None:
            return None
        handle = None
        try:
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = win32api.OpenProcess(0x1000, False, pid)
            path = win32process.GetModuleFileNameEx(handle, 0)
            if not path:
                return None
            return os.path.basename(path).lower() or None
        except Exception as exc:
            _warn(f"_exe_basename(pid={pid}): {exc}")
            return None
        finally:
            if handle is not None:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass

    def _idle_state(self) -> tuple[int, bool]:
        """Return ``(idle_seconds, is_idle)``; safe defaults on any error."""
        if win32api is None:
            return 0, False
        try:
            last_input = int(win32api.GetLastInputInfo())
            now = int(win32api.GetTickCount())
            # Mask to 32 bits to survive the ~49.7 day tick wrap.
            idle_ms = (now - last_input) & 0xFFFFFFFF
            idle_seconds = idle_ms // 1000
        except Exception as exc:
            _warn(f"_idle_state: {exc}")
            return 0, False
        return idle_seconds, idle_seconds >= self._idle_threshold

    # -- public sampling API ----------------------------------------------- #
    def sample(self) -> dict:
        """One-shot foreground sample. Never raises; unknown on win32 error."""
        ts = datetime.now().isoformat(timespec="seconds")
        app, title, pid = "unknown", "", 0
        try:
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pid = int(pid)
            app = self._exe_basename(pid) or "unknown"
        except Exception as exc:
            _warn(f"sample: {exc}")
        idle_seconds, idle = self._idle_state()
        return {
            "app": app,
            "title": title,
            "pid": pid,
            "idle_seconds": idle_seconds,
            "idle": idle,
            "ts": ts,
        }

    def current(self) -> dict:
        """Latest sample (never None; safe defaults before the first poll)."""
        return dict(self._last)

    # -- DB connection (guarded by self._lock) ------------------------------ #
    def _connect_locked(self) -> sqlite3.Connection:
        """Return the shared connection, opening + ensuring schema on first use.

        Caller MUST hold ``self._lock``.
        """
        if self._con is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
            ensure_db(con)
            self._con = con
        return self._con

    # -- attribution / persistence ------------------------------------------ #
    def _effective_app(self, sample: dict) -> str:
        """Resolve the app to attribute, skipping this widget's own window."""
        if sample.get("pid") == os.getpid():
            return self._last_app  # keep attributing to the previous non-self app
        app = sample.get("app") or "unknown"
        self._last_app = app
        return app

    @staticmethod
    def _split_ts(ts: str) -> tuple[str, int]:
        try:
            dt = datetime.fromisoformat(ts)
        except Exception:
            dt = datetime.now()
        return dt.strftime("%Y-%m-%d"), dt.hour

    def _record_locked(self, con: sqlite3.Connection, sample: dict) -> None:
        """Persist one poll. Caller MUST hold ``self._lock``."""
        app = self._effective_app(sample)
        day, hour = self._split_ts(sample.get("ts") or "")
        poll = self._poll

        if sample.get("idle"):
            self._add_idle_locked(con, day, poll)
        else:
            self._add_app_locked(con, day, app, sample.get("title") or "", poll)
            self._add_hour_locked(con, day, hour, app, poll)

        self._count_switch_locked(con, day, app)

    @staticmethod
    def _add_app_locked(con: sqlite3.Connection, day: str, app: str,
                        title: str, seconds: int) -> None:
        con.execute(
            """
            INSERT INTO app_usage(day, app, title, seconds) VALUES(?, ?, ?, ?)
            ON CONFLICT(day, app) DO UPDATE SET
              seconds = app_usage.seconds + excluded.seconds,
              title = CASE WHEN excluded.title IS NOT NULL AND excluded.title <> ''
                           THEN excluded.title ELSE app_usage.title END
            """,
            (day, app, title, seconds),
        )

    @staticmethod
    def _add_hour_locked(con: sqlite3.Connection, day: str, hour: int,
                         app: str, seconds: int) -> None:
        con.execute(
            """
            INSERT INTO hour_usage(day, hour, app, seconds) VALUES(?, ?, ?, ?)
            ON CONFLICT(day, hour, app) DO UPDATE SET
              seconds = hour_usage.seconds + excluded.seconds
            """,
            (day, hour, app, seconds),
        )

    def _add_idle_locked(self, con: sqlite3.Connection, day: str, seconds: int) -> None:
        """Accrue AFK seconds to the day's idle bucket (daily_summary.idle_seconds)."""
        con.execute(
            """
            INSERT INTO daily_summary(day, idle_seconds, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
              idle_seconds = daily_summary.idle_seconds + excluded.idle_seconds,
              updated_at = excluded.updated_at
            """,
            (day, seconds, datetime.now().isoformat(timespec="seconds")),
        )

    def _count_switch_locked(self, con: sqlite3.Connection, day: str, app: str) -> None:
        """Increment meta['switch:<day>'] when the effective foreground app changes."""
        if self._prev_effective is None:
            self._prev_effective = app
            return
        if app == self._prev_effective:
            return
        self._prev_effective = app
        key = f"switch:{day}"
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        current = 0
        if row and row[0] is not None and str(row[0]).lstrip("-").isdigit():
            current = int(row[0])
        con.execute(
            """
            INSERT INTO meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(current + 1)),
        )

    def _poll_and_write(self) -> dict:
        """Sample once, remember it, and persist it. Never raises upward."""
        sample = self.sample()
        self._last = sample
        try:
            with self._lock:
                con = self._connect_locked()
                self._record_locked(con, sample)
                con.commit()
        except Exception as exc:
            _warn(f"_poll_and_write: {exc}")
        return sample

    def flush(self) -> None:
        """Force-write the current second immediately (best effort)."""
        self._poll_and_write()

    # -- totals ------------------------------------------------------------- #
    @staticmethod
    def _day_totals_locked(con: sqlite3.Connection, day: str) -> dict:
        rows = con.execute(
            "SELECT app, seconds FROM app_usage WHERE day = ?", (day,)
        ).fetchall()
        return {app: int(seconds) for app, seconds in rows}

    def day_totals(self, day: str) -> dict:
        """Return ``{app: seconds}`` for ``day`` ('YYYY-MM-DD')."""
        try:
            with self._lock:
                con = self._connect_locked()
                return self._day_totals_locked(con, day)
        except Exception as exc:
            _warn(f"day_totals({day}): {exc}")
            return {}

    def today_totals(self) -> dict:
        """Return ``{'YYYY-MM-DD': {app: seconds, ...}}`` for today."""
        day = datetime.now().strftime("%Y-%m-%d")
        return {day: self.day_totals(day)}

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        """Start the polling daemon thread (idempotent)."""
        if self._running:
            return
        try:
            with self._lock:
                self._connect_locked()
        except Exception as exc:
            _warn(f"start: could not open db: {exc}")
        self._stop_evt.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run, name="pw-tracker", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._poll_and_write()
            except Exception as exc:  # defensive; _poll_and_write already guards
                _warn(f"_run: {exc}")
            self._stop_evt.wait(self._poll)

    def stop(self) -> None:
        """Stop the polling thread and release the DB connection."""
        self._running = False
        self._stop_evt.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=max(2, self._poll * 2))
            except Exception:
                pass
        self._thread = None
        with self._lock:
            if self._con is not None:
                try:
                    self._con.commit()
                    self._con.close()
                except Exception as exc:
                    _warn(f"stop: {exc}")
                finally:
                    self._con = None


# --------------------------------------------------------------------------- #
# Smoke test — runs the tracker ~3 s and prints a sample + today's totals.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _cfg = load_config()
    _tracker = Tracker(_cfg, str(DEFAULT_DB_PATH), str(BASE_DIR))
    print("sample:", _tracker.sample())
    _tracker.start()
    time.sleep(3)
    print("current:", _tracker.current())
    _tracker.stop()
    print("today_totals:", _tracker.today_totals())
