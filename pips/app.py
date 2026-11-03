"""
Pips — Main Application
Tkinter front-end: compact floating card, dashboard, history browser.
Orchestrates tracker, scoring, coach, audio modules per CONTRACT.md.
"""

import argparse
import copy
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

try:
    import i18n
    from i18n import t as T
except ImportError:  # pragma: no cover - i18n ships with Pips
    i18n = None

    def T(key, lang=None, **kwargs):
        return key

APP_VERSION = "2.28.0"
APP_OWNER = "Juwen Lie"
USER_GUIDE_URL = "https://juw12.github.io/projects/pips/download/Pips-Guide.pptx"
SITE_URL = "https://juw12.github.io/projects/pips/"

try:
    import updater
except ImportError:  # pragma: no cover
    updater = None

try:
    import tray                  # v2.17: icon next to the clock + Ctrl+Alt+P
except ImportError:  # pragma: no cover
    tray = None

# Windows-specific imports (guarded)
try:
    import win32gui
    import win32process
    import win32api
    import win32con
    from win32com.client import Dispatch as WScriptDispatch
except ImportError:
    win32gui = win32process = win32api = win32con = WScriptDispatch = None

# =============================================================================
# CONFIGURATION & PATHS
# =============================================================================

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "data" / "productivity.db"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "widget.log"

# Ensure log directory exists
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Keep the log small: past 2 MB it moves to widget.log.1 at start-up (the frozen app has no
# logging.handlers, so no RotatingFileHandler)
try:
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2_000_000:
        LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))
except OSError:
    pass

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("widget")



# =============================================================================
# SINGLE INSTANCE - one pet per Windows session
# =============================================================================

class SingleInstance:
    """Named mutex + 'show yourself' event. Opening Pips a second time (Desktop / Start Menu
    shortcut, autostart, installer) wakes the running pet instead of starting a second one."""

    MUTEX = "Local\\Pips.DesktopPet.Instance"
    EVENT = "Local\\Pips.DesktopPet.ShowRequest"
    ERROR_ALREADY_EXISTS = 183
    EVENT_MODIFY_STATE = 0x0002

    def __init__(self):
        self.mutex = None
        self.event = None
        self._k32 = None
        self._ctypes = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CreateEventW.restype = wintypes.HANDLE
            k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            k32.OpenEventW.restype = wintypes.HANDLE
            k32.SetEvent.argtypes = [wintypes.HANDLE]
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._k32 = k32
            self._ctypes = ctypes
        except Exception as e:  # never let the guard stop Pips from starting
            logger.warning(f"Single-instance guard unavailable: {e}")

    def acquire(self, wait_s: float = 10.0) -> bool:
        """True if this process may run. Otherwise the running Pips is asked to show itself.

        Waits a little before giving up, so an update restart (old Pips still closing) works."""
        k32 = self._k32
        if k32 is None:
            return True
        deadline = time.monotonic() + wait_s
        asked = False
        while True:
            handle = k32.CreateMutexW(None, False, self.MUTEX)
            exists = self._ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS
            if not handle:
                return True
            if not exists:
                self.mutex = handle
                self.event = k32.CreateEventW(None, False, False, self.EVENT)
                return True
            k32.CloseHandle(handle)
            if not asked:
                asked = self.request_show()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def request_show(self) -> bool:
        event = self._k32.OpenEventW(self.EVENT_MODIFY_STATE, False, self.EVENT)
        if not event:
            return False
        self._k32.SetEvent(event)
        self._k32.CloseHandle(event)
        return True

    def show_requested(self) -> bool:
        return bool(self.event) and self._k32.WaitForSingleObject(self.event, 0) == 0

# =============================================================================
# DEFAULT CONFIGURATION (falls back if config.json missing)
# =============================================================================

DEFAULT_CONFIG = {
    "pomodoro": {
        "work_minutes": 25,
        "short_break_minutes": 5,
        "long_break_minutes": 15,
        "long_break_every": 4,
        "auto_start_next": True
    },
    "audio": {
        "default": "rain",
        "autoplay_on_focus": True,
        "stop_on_break": False,
        "volume": 60
    },
    "tracking": {
        "poll_seconds": 1,
        "idle_seconds": 120
    },
    "category": {
        "productive": ["code.exe", "windowsterminal.exe", "devenv.exe", "pycharm64.exe",
                       "excel.exe", "winword.exe", "powerpnt.exe", "notepad.exe", "explorer.exe"],
        "distracting": ["steam.exe", "discord.exe", "spotify.exe", "tiktok.exe", "netflix.exe"],
        "neutral": []
    },
    "title_rules": [],
    "goals": {
        "deep_work_hours": 4,
        "pomodoros": 8,
        "max_distraction_hours": 2
    },
    "scoring": {
        "weights": {"productive": 1.0, "neutral": 0.3, "distracting": -0.8, "idle": 0.0},
        "star_thresholds": [20, 40, 60, 80]
    },
    "coach": {
        "enabled": True,
        "llm": {"enabled": False, "endpoint": "", "api_key": "", "model": ""}
    },
    "ui": {
        "alpha": 0.94,
        "pos": None,
        "compact_width": 260,
        "card_height": 310
    },
    "tokens": {"enabled": True, "log_path": ""},
    "maas": {
        "enabled": False,
        "base_url": "https://ai-gateway.example.com/maas/api/v1",
        "username": "",
        "password": "",
        "verify_ssl": True,
        "cache_minutes": 10
    },
    "diary": {"enabled": True,
              "repo_dir": str(Path(__file__).parent.parent / "productivity-diary"),
              "auto_write": True, "auto_publish": False},
    # Desktop pet - full defaults live in pet.DEFAULT_PET_SETTINGS
    "pet": {"enabled": True}
}


# =============================================================================
# THEME — Newspaper "Morning Paper" Light Palette
# =============================================================================

THEME = {
    # Backgrounds
    "paper_bg": "#faf8f3",       # Main paper/card background
    "outer_bg": "#f0ede6",        # Outer/panel background
    "white": "#ffffff",           # White surface
    
    # Text colors (dark ink on light paper)
    "ink_black": "#1a1a1a",       # Primary text
    "ink_light": "#4a4a4a",       # Secondary text
    "muted": "#6b6b6b",           # Muted text
    
    # Accent colors
    "accent_red": "#8b1a1a",      # Primary accent (buttons, progress, links)
    "accent_red_dark": "#6b1212", # Accent hover/pressed
    
    # Borders
    "border": "#d4cfc7",          # Standard border
    "border_light": "#e8e4dc",    # Light border
    
    # Semantic colors
    "productive": "#1a7f37",      # Good/productive
    "distracting": "#b3261e",     # Bad/distracting
    "neutral": "#b8860b",         # Neutral/flat gold (stars)
    
    # Fonts
    "font_serif": ("Georgia", 16, "bold"),      # Masthead/section headings
    "font_serif_small": ("Georgia", 11, "bold"), # Sub-headings
    "font_mono": ("Courier New", 24, "bold"),   # Timer digits (monospaced)
    "font_mono_small": ("Courier New", 9),      # Code/monospace text
    "font_sans": ("Segoe UI", 10, "bold"),      # Bold labels
    "font_sans_small": ("Segoe UI", 9),         # Body text
    "font_sans_tiny": ("Segoe UI", 7),          # Tiny labels
}

# Card chrome (rounded floating card)
CARD_KEY = "#010203"          # transparent colour key for the rounded corners
CARD_RADIUS = 14
PILL_COLORS = {
    "focus": ("#8b1a1a", "#f6e1e1"),
    "break": ("#1a7f37", "#e1f2e6"),
    "paused": ("#8a6508", "#f6eed7"),
    "idle": ("#6b6b6b", "#ece9e2"),
}


def load_config(path: Optional[str] = None) -> dict:
    """Load config from JSON file, merge with defaults."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg_path = Path(path) if path else CONFIG_PATH
    
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                user_cfg = json.load(f)
            # Deep merge
            for key, val in user_cfg.items():
                if key in cfg and isinstance(cfg[key], dict) and isinstance(val, dict):
                    cfg[key].update(val)
                else:
                    cfg[key] = val
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load config from {cfg_path}: {e}")
    
    return cfg


# =============================================================================
# MODULE IMPORTS (guarded - siblings may not exist yet)
# =============================================================================

tracker = None
scoring = None
coach = None
audio = None
reminders = None
Tracker = None
SoundEngine = None
tokens = None
diary = None
maas_tokens = None
pet_mod = None

def import_modules():
    """Import sibling modules with graceful degradation."""
    global tracker, scoring, coach, audio, reminders, Tracker, SoundEngine, tokens, diary, maas_tokens, pet_mod
    
    try:
        import tracker as tracker_mod
        tracker = tracker_mod
        Tracker = getattr(tracker_mod, "Tracker", None)
        logger.info("tracker module loaded")
    except ImportError as e:
        logger.warning(f"tracker module not available: {e}")
    
    try:
        import scoring as scoring_mod
        scoring = scoring_mod
        logger.info("scoring module loaded")
    except ImportError as e:
        logger.warning(f"scoring module not available: {e}")
    
    try:
        import coach as coach_mod
        coach = coach_mod
        logger.info("coach module loaded")
    except ImportError as e:
        logger.warning(f"coach module not available: {e}")
    
    try:
        import audio as audio_mod
        audio = audio_mod
        SoundEngine = getattr(audio_mod, "SoundEngine", None)
        logger.info("audio module loaded")
    except ImportError as e:
        logger.warning(f"audio module not available: {e}")
    
    try:
        import reminders as reminders_mod
        reminders = reminders_mod
        logger.info("reminders module loaded")
    except ImportError as e:
        logger.warning(f"reminders module not available: {e}")
    
    try:
        import tokens as tokens_mod
        tokens = tokens_mod
        logger.info("tokens module loaded")
    except ImportError as e:
        logger.warning(f"tokens module not available: {e}")
    
    try:
        import diary as diary_mod
        diary = diary_mod
        logger.info("diary module loaded")
    except ImportError as e:
        logger.warning(f"diary module not available: {e}")
    
    try:
        import maas_tokens as maas_tokens_mod
        maas_tokens = maas_tokens_mod
        logger.info("maas_tokens module loaded")
    except ImportError as e:
        logger.warning(f"maas_tokens module not available: {e}")
    
    try:
        import pet as pet_module
        pet_mod = pet_module
        logger.info("pet module loaded")
    except ImportError as e:
        logger.warning(f"pet module not available: {e}")


# =============================================================================
# DATABASE HELPERS
# =============================================================================

def get_db_connection(db_path: str = None) -> sqlite3.Connection:
    """Create a SQLite connection with WAL mode."""
    path = db_path or str(DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def ensure_db_schema(con: sqlite3.Connection) -> None:
    """Create database tables if they don't exist (mirrors tracker.ensure_db)."""
    cur = con.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_usage (
            day TEXT NOT NULL,
            app TEXT NOT NULL,
            title TEXT,
            seconds INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, app)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hour_usage (
            day TEXT NOT NULL,
            hour INTEGER NOT NULL,
            app TEXT NOT NULL,
            seconds INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, hour, app)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            task TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            completed INTEGER NOT NULL DEFAULT 0
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            day TEXT PRIMARY KEY,
            productive_seconds INTEGER NOT NULL DEFAULT 0,
            neutral_seconds INTEGER NOT NULL DEFAULT 0,
            distracting_seconds INTEGER NOT NULL DEFAULT 0,
            idle_seconds INTEGER NOT NULL DEFAULT 0,
            focus_seconds INTEGER NOT NULL DEFAULT 0,
            app_switches INTEGER NOT NULL DEFAULT 0,
            pomodoros_done INTEGER NOT NULL DEFAULT 0,
            score REAL,
            stars INTEGER,
            goal_met INTEGER NOT NULL DEFAULT 0,
            xp INTEGER NOT NULL DEFAULT 0,
            coach_text TEXT,
            updated_at TEXT
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    con.commit()


# =============================================================================
# POMODORO STATE MACHINE
# =============================================================================

class PomodoroState:
    """Manages pomodoro timer state machine."""
    
    WORK = "work"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"
    
    def __init__(self, cfg: dict, db_con: sqlite3.Connection):
        self.cfg = cfg
        self.con = db_con
        self.state = self.WORK
        self.remaining_seconds = cfg["pomodoro"]["work_minutes"] * 60
        self.completed_pomodoros = 0
        self.current_session_id: Optional[int] = None
        self._lock = threading.Lock()
        self._timer_thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks = []
    
    def add_callback(self, cb):
        """Register callback for state changes (called from main thread via after)."""
        self._callbacks.append(cb)
    
    def _notify_callbacks(self):
        """Notify all registered callbacks."""
        for cb in self._callbacks:
            try:
                cb()
            except Exception as e:
                logger.warning(f"Callback error: {e}")
    
    def _get_current_day(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")
    
    def _get_current_iso(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def _create_session(self, kind: str) -> Optional[int]:
        """Create a new session record. Returns session ID."""
        try:
            cur = self.con.cursor()
            cur.execute("""
                INSERT INTO sessions (day, kind, started_at, completed)
                VALUES (?, ?, ?, 0)
            """, (self._get_current_day(), kind, self._get_current_iso()))
            self.con.commit()
            return cur.lastrowid
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return None
    
    def _complete_session(self, session_id: int, completed: bool = True):
        """Mark session as completed and set ended_at."""
        try:
            cur = self.con.cursor()
            cur.execute("""
                UPDATE sessions SET ended_at = ?, completed = ?
                WHERE id = ?
            """, (self._get_current_iso(), 1 if completed else 0, session_id))
            self.con.commit()
        except Exception as e:
            logger.error(f"Failed to complete session: {e}")
    
    def _increment_pomodoros(self):
        """Increment today's pomodoros_done in daily_summary."""
        try:
            day = self._get_current_day()
            cur = self.con.cursor()
            cur.execute("""
                INSERT INTO daily_summary (day, pomodoros_done)
                VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET
                    pomodoros_done = pomodoros_done + 1,
                    updated_at = ?
            """, (day, self._get_current_iso()))
            self.con.commit()
            self.completed_pomodoros += 1
        except Exception as e:
            logger.error(f"Failed to increment pomodoros: {e}")
    
    def _tick(self):
        """Timer tick - decrement remaining and handle transitions."""
        with self._lock:
            if not self._running or self.remaining_seconds <= 0:
                return
            
            self.remaining_seconds -= 1
            
            if self.remaining_seconds <= 0:
                self._on_timer_complete()
    
    def _on_timer_complete(self):
        """Handle timer completion - transition to next state."""
        if self.state == self.WORK:
            # Complete work session
            if self.current_session_id:
                self._complete_session(self.current_session_id, True)
            self._increment_pomodoros()
            
            # Determine next break type
            if self.completed_pomodoros % self.cfg["pomodoro"]["long_break_every"] == 0:
                self.state = self.LONG_BREAK
                self.remaining_seconds = self.cfg["pomodoro"]["long_break_minutes"] * 60
            else:
                self.state = self.SHORT_BREAK
                self.remaining_seconds = self.cfg["pomodoro"]["short_break_minutes"] * 60
            
            self.current_session_id = self._create_session(self.state)
            
        elif self.state in (self.SHORT_BREAK, self.LONG_BREAK):
            # Complete break session
            if self.current_session_id:
                self._complete_session(self.current_session_id, True)
            
            # Transition back to work
            self.state = self.WORK
            self.remaining_seconds = self.cfg["pomodoro"]["work_minutes"] * 60
            
            if self.cfg["pomodoro"]["auto_start_next"]:
                self.current_session_id = self._create_session(self.WORK)
            else:
                self.current_session_id = None
                self._running = False
        
        self._notify_callbacks()
    
    def _timer_loop(self):
        """Background timer loop (runs in separate thread)."""
        while self._running:
            time.sleep(1.0)
            self._tick()
    
    def start(self):
        """Start or resume the timer."""
        with self._lock:
            if self._running:
                return
            
            self._running = True
            
            if not self.current_session_id:
                self.current_session_id = self._create_session(self.state)
        
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()
        self._notify_callbacks()
    
    def pause(self):
        """Pause the timer."""
        with self._lock:
            self._running = False
        self._notify_callbacks()
    
    def reset(self):
        """Reset to initial work state."""
        with self._lock:
            self._running = False
            self.state = self.WORK
            self.remaining_seconds = self.cfg["pomodoro"]["work_minutes"] * 60
            if self.current_session_id:
                self._complete_session(self.current_session_id, False)
                self.current_session_id = None
        self._notify_callbacks()
    
    def get_state(self) -> tuple:
        """Return (state, remaining_seconds, is_running)."""
        with self._lock:
            return (self.state, self.remaining_seconds, self._running)
    
    def get_display_time(self) -> str:
        """Return formatted time string like '25:00'."""
        _, remaining, _ = self.get_state()
        mins = remaining // 60
        secs = remaining % 60
        return f"{mins:02d}:{secs:02d}"


# =============================================================================
# UI APPLICATION
# =============================================================================

class ProductivityWidget:
    """Main tkinter application with compact card, dashboard, and history windows."""
    
    def __init__(self, cfg: dict, db_con: sqlite3.Connection, no_track: bool = False):
        self.cfg = cfg
        self.con = db_con
        self.no_track = no_track
        
        # Core components
        self.tracker = None
        self.sound_engine = None
        self.pomodoro = None
        
        # Windows
        self.root: Optional[tk.Tk] = None
        self.card_frame: Optional[tk.Frame] = None
        self.dashboard_window: Optional[tk.Toplevel] = None
        self.history_window: Optional[tk.Toplevel] = None
        
        # UI state
        self._drag_data = {"x": 0, "y": 0, "active": False}
        self._refresh_after_id: Optional[str] = None
        self._history_date: Optional[str] = None  # None = today
        
        # Widget references (for updates)
        self._card_widgets = {}
        self._dashboard_widgets = {}
        self._history_widgets = {}
        
        # Desktop pet + card chrome state
        self.pet = None
        self._card_bg = None
        self._card_main = None
        self._card_size = None
        self._distract_secs = 0
        self._distract_next = 0.0
        self._goal_announced_day = None
        
        # MAAS account usage (fetched on a slow cadence; drawn from cache only)
        self._maas_today: dict = {}
        self._maas_daily: dict = {}
        self._maas_fetching = False
        self._maas_dirty = False
        
        # Focus sound selection (initialized from config, fallback to "rain")
        self._focus_sound: Optional[str] = None
        
        # Initialize modules
        self._init_modules()
        
        # Initialize focus sound (after sound_engine is ready)
        self._init_focus_sound()
    
    def _init_modules(self):
        """Initialize tracker, audio, pomodoro, reminders."""
        # Tracker
        if not self.no_track and Tracker:
            try:
                self.tracker = Tracker(self.cfg, str(DB_PATH), str(BASE_DIR))
                self.tracker.start()
                logger.info("Tracker started")
            except Exception as e:
                logger.error(f"Failed to start tracker: {e}")
                self.tracker = None
        elif self.no_track:
            logger.info("Tracker disabled (--no-track)")
        
        # Audio
        if SoundEngine:
            try:
                self.sound_engine = SoundEngine(self.cfg, str(BASE_DIR))
                self.sound_engine.ensure_assets()
                logger.info("Audio engine initialized")
            except Exception as e:
                logger.error(f"Failed to init audio: {e}")
                self.sound_engine = None
        
        # Pomodoro
        try:
            self.pomodoro = PomodoroState(self.cfg, self.con)
            self.pomodoro.add_callback(lambda: self.root.after(0, self._update_card))
            logger.info("Pomodoro initialized")
        except Exception as e:
            logger.error(f"Failed to init pomodoro: {e}")
            self.pomodoro = None
        
        # Reminders
        try:
            if reminders:
                self.reminders = reminders.ReminderScheduler(reminders.load_settings(self.cfg))
                logger.info("Reminders scheduler initialized")
            else:
                self.reminders = None
        except Exception as e:
            logger.error(f"Failed to init reminders: {e}")
            self.reminders = None
        
        # Reminder popup state
        self._reminder_popup = None
        self._prev_pomo_state = None
        self._snooze_job = None
        
        if tokens:
            try:
                tokens.ensure_db(self.con)
                tokens.refresh(self.con)
            except Exception as e:
                logger.warning(f"Initial tokens ingest failed: {e}")
    
    def _init_focus_sound(self):
        """Initialize focus sound from config after sound_engine is ready."""
        default_sound = self.cfg["audio"].get("default", "rain")
        if self.sound_engine:
            sounds = self.sound_engine.sounds()
            if sounds and default_sound in sounds:
                self._focus_sound = default_sound
            elif sounds:
                self._focus_sound = sounds[0]
            else:
                self._focus_sound = "rain"
        else:
            self._focus_sound = default_sound
    
    def build(self):
        """Build all UI components (offscreen for selftest)."""
        self.root = tk.Tk()
        self.root.title("Pips")
        self.root.withdraw()  # Start hidden; show card only
        
        # Build card (always)
        self._build_card()
        
        # Build dashboard (hidden initially)
        self._build_dashboard()
        
        # Build history (hidden initially)
        self._build_history()
        
        # Position card
        self._position_card()
        
        # Desktop pet (lives on the desktop / taskbar; click it to open the card)
        self._build_pet()
        if not self.pet or self.pet.s.get("start_with_card", False):
            self.card_frame.deiconify()     # no pet (or the user asked for it): show the card
        
        # Start refresh loop
        self._schedule_refresh()
        
        # Background update checks (first one shortly after start, then every 6 hours)
        self._update_busy = False
        self._update_manifest = None
        self._update_snooze_until = 0.0      # "Later": hide the update bubble until this time.monotonic()
        self._update_dialog_shown = set()    # versions already offered in a dialog this session
        self._update_installing = False
        self.root.after(25_000, self._auto_update_tick)
        self.root.after(self.UPDATE_WATCH_MS, self._update_watchdog)
        self.root.after(8_000, self._announce_installed_update)
        
        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Someone opened Pips again (shortcut / autostart): wake this pet instead
        if getattr(self, "single_instance", None) is not None:
            self.root.after(1000, self._poll_instance)

        # Always a way back to the pet: tray icon next to the clock + Ctrl+Alt+P
        self._tray = None
        self.root.after(1200, self._start_tray)

    def _poll_instance(self):
        try:
            if self.single_instance.show_requested():
                self._greet_second_launch()
        except Exception as e:
            logger.warning(f"Show request failed: {e}")
        self.root.after(700, self._poll_instance)

    def _greet_second_launch(self):
        """Pips was opened again while running: bring the pet back (even if hidden / off-screen) and say hi."""
        self._summon_pet(T("already_running"), source="launch")

    # ------------------------------------------------------------------
    # Find your pet (v2.17): tray icon, Ctrl+Alt+P, opening Pips again
    # ------------------------------------------------------------------
    HIDE_TIPS = 5   # how many times hiding the pet shows "how to bring it back"

    def _pet_name(self) -> str:
        return (self.pet.s.get("name") if self.pet else None) or "Pip"

    def _start_tray(self):
        if tray is None or os.name != "nt":
            return
        try:
            icon = BASE_DIR / "assets" / "widget.ico"
            self._tray = tray.TrayIcon(icon if icon.exists() else None, self._tray_tip(""), self._tray_labels)
            if not self._tray.start():
                self._tray = None
                return
            key = self._tray.hotkey_label
            self._tray.set_tooltip(self._tray_tip(key))
            if self.pet:
                self.pet.summon_hint = key
            logger.info(f"Tray icon ready (hotkey {key or 'none'})")
            self.root.after(150, self._poll_tray)
        except Exception as e:
            logger.warning(f"Tray icon unavailable: {e}")
            self._tray = None

    def _tray_labels(self) -> dict:
        """Menu texts (called from the tray thread each time the menu opens)."""
        name = self._pet_name()
        return {"summon": T("tray_summon", name=name), "card": T("tray_card"),
                "friends": T("tray_friends"), "quit": T("tray_quit")}

    def _tray_tip(self, key: str) -> str:
        name = self._pet_name()
        return T("tray_tip_key", name=name, hotkey=key) if key else T("tray_tip", name=name)

    def _poll_tray(self):
        if not self._tray:
            return
        for command in self._tray.drain():
            try:
                self._tray_command(command)
            except Exception as e:
                logger.warning(f"Tray command {command} failed: {e}")
        if self._tray:          # "Quit Pips" from the tray menu has already closed everything
            try:
                self.root.after(150, self._poll_tray)
            except tk.TclError:
                pass

    def _tray_command(self, command: str):
        if command in ("summon", "hotkey"):
            now = time.monotonic()
            if now - getattr(self, "_last_tray_summon", 0.0) < 0.6:
                return          # double-click on the icon = one summon
            self._last_tray_summon = now
            self._summon_pet(source="hotkey" if command == "hotkey" else "tray")
        elif command == "card":
            self._show_card(near_pet=True)
        elif command == "friends":
            if self.pet:
                self.pet.open_hub()
            else:
                self._show_card()
        elif command == "quit":
            self._on_close()

    def _summon_pet(self, text=None, source="menu"):
        """Bring the pet back wherever it is (hidden, asleep, off-screen, behind a fullscreen app)."""
        logger.info(f"Pet summoned ({source})")
        if not self.pet:
            self._show_card()
            return
        self.pet.summon(text or T("summon_here"))

    def _hide_tip(self):
        """After hiding: tell how to bring the pet back (the first few times)."""
        if not self._tray or not self.pet:
            return
        shown = int(self.pet.s.get("hide_tips_shown", 0) or 0)
        if shown >= self.HIDE_TIPS:
            return
        name = self._pet_name()
        key = self._tray.hotkey_label
        body = T("tray_hidden_text_key", name=name, hotkey=key) if key else T("tray_hidden_text", name=name)
        if self._tray.notify(T("tray_hidden_title", name=name), body):
            self.pet.update_setting("hide_tips_shown", shown + 1)
    
    def _build_card(self):
        """Build the compact floating card."""
        # Create frameless toplevel
        self.card_frame = tk.Toplevel(self.root)
        self.card_frame.withdraw()      # 2.24.1: hidden until asked for (no flash at start-up)
        self.card_frame.overrideredirect(True)
        self.card_frame.attributes("-topmost", True)
        self.card_frame.attributes("-alpha", self.cfg["ui"]["alpha"])
        
        # Styling - Newspaper light theme
        bg_color = THEME["paper_bg"]
        fg_color = THEME["ink_black"]
        accent_color = THEME["accent_red"]
        
        # Rounded card: the window itself is transparent (colour key) and a
        # background canvas paints the rounded paper card + soft shadow.
        self.card_frame.configure(bg=CARD_KEY)
        try:
            self.card_frame.attributes("-transparentcolor", CARD_KEY)
        except tk.TclError:
            pass
        
        width = self.cfg["ui"]["compact_width"]
        self._card_bg = tk.Canvas(self.card_frame, bg=CARD_KEY, highlightthickness=0, bd=0)
        self._card_bg.pack(fill=tk.BOTH, expand=True)
        
        # Main container (placed inside the rounded card)
        main_frame = tk.Frame(self._card_bg, bg=bg_color)
        self._card_bg.create_window(14, 12, window=main_frame, anchor="nw", width=width - 30)
        self._card_main = main_frame
        
        # === Header: App name + productive time ===
        header_frame = tk.Frame(main_frame, bg=bg_color)
        header_frame.pack(fill=tk.X, pady=(0, 6))
        
        # App name (left) - Georgia serif for masthead
        app_label = tk.Label(
            header_frame,
            text=T("loading"),
            font=("Georgia", 10, "bold"),
            fg=fg_color,
            bg=bg_color,
            anchor="w"
        )
        app_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._card_widgets["app_name"] = app_label
        
        # Hide-to-pet button (far right of the header)
        hide_btn = tk.Button(
            header_frame,
            text="\u2013",
            font=("Segoe UI", 9, "bold"),
            bg=bg_color,
            fg=THEME["muted"],
            relief=tk.FLAT,
            bd=0,
            padx=5,
            cursor="hand2",
            command=self._hide_card
        )
        hide_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._hoverize(hide_btn, bg_color, THEME["border_light"], THEME["muted"], THEME["ink_black"])
        
        # Productive time (right)
        time_label = tk.Label(
            header_frame,
            text="0h 0m",
            font=("Segoe UI", 9, "bold"),
            fg=accent_color,
            bg=bg_color,
            anchor="e"
        )
        time_label.pack(side=tk.RIGHT)
        self._card_widgets["productive_time"] = time_label
        
        # === Masthead accent rule (1px line under header) ===
        rule_frame = tk.Frame(main_frame, height=1, bg=accent_color)
        rule_frame.pack(fill=tk.X, pady=(0, 8))
        
        # === Pomodoro Section ===
        pomo_frame = tk.Frame(main_frame, bg=bg_color)
        pomo_frame.pack(fill=tk.X, pady=(0, 6))
        
        # Status pill row
        status_frame = tk.Frame(pomo_frame, bg=bg_color)
        status_frame.pack(fill=tk.X, pady=(0, 4))
        
        status_pill = tk.Label(
            status_frame,
            text=T("idle"),
            font=("Segoe UI", 8, "bold"),
            fg=PILL_COLORS["idle"][0],
            bg=PILL_COLORS["idle"][1],
            padx=8,
            pady=1,
            anchor="w"
        )
        status_pill.pack(side=tk.LEFT)
        self._card_widgets["status_pill"] = status_pill
        
        # Cycle dots (right side)
        cycle_dots = tk.Label(
            status_frame,
            text="○○○○  0/4",
            font=("Segoe UI", 9),
            fg=THEME["border"],
            bg=bg_color,
            anchor="e"
        )
        cycle_dots.pack(side=tk.RIGHT)
        self._card_widgets["cycle_dots"] = cycle_dots
        
        # Timer ring canvas (centered)
        timer_canvas = tk.Canvas(
            pomo_frame,
            width=118,
            height=118,
            bg=bg_color,
            highlightthickness=0,
            cursor="hand2"
        )
        timer_canvas.pack(pady=(0, 4))
        self._card_widgets["timer"] = timer_canvas
        timer_canvas.bind("<Button-1>", lambda e: self._toggle_pomodoro())
        
        # Live session panel (below timer)
        session_frame = tk.Frame(pomo_frame, bg=bg_color)
        session_frame.pack(fill=tk.X, pady=(0, 6))
        
        session_label = tk.Label(
            session_frame,
            text="—",
            font=("Segoe UI", 9),
            fg=THEME["muted"],
            bg=bg_color,
            anchor="center"
        )
        session_label.pack(fill=tk.X)
        self._card_widgets["session_info"] = session_label
        
        # Pomodoro controls
        controls_frame = tk.Frame(pomo_frame, bg=bg_color)
        controls_frame.pack(fill=tk.X, pady=(0, 4))
        
        # Start/Pause button
        start_btn = tk.Button(
            controls_frame,
            text=T("start"),
            font=("Segoe UI", 8, "bold"),
            bg=accent_color,
            fg=THEME["white"],
            activebackground=THEME["accent_red_dark"],
            activeforeground=THEME["white"],
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=3,
            cursor="hand2",
            command=self._toggle_pomodoro
        )
        start_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._card_widgets["start_btn"] = start_btn
        self._hoverize(start_btn, accent_color, THEME["accent_red_dark"])
        
        # Reset button
        reset_btn = tk.Button(
            controls_frame,
            text=T("reset"),
            font=("Segoe UI", 8),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            bd=0,
            padx=5,
            pady=3,
            cursor="hand2",
            command=self._reset_pomodoro
        )
        reset_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._hoverize(reset_btn, THEME["border_light"], THEME["border"])
        
        # Sound toggle
        sound_btn = tk.Button(
            controls_frame,
            text=T("muted"),
            font=("Segoe UI", 8),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            bd=0,
            padx=5,
            pady=3,
            cursor="hand2",
            command=self._toggle_sound
        )
        sound_btn.pack(side=tk.LEFT)
        self._card_widgets["sound_btn"] = sound_btn
        self._hoverize(sound_btn, THEME["border_light"], THEME["border"])
        
        # Sound picker button (shows current focus sound with volume)
        sound_pick_btn = tk.Button(
            controls_frame,
            text=self._focus_sound_label(),
            font=("Segoe UI", 8),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            bd=0,
            padx=5,
            pady=3,
            cursor="hand2",
            command=self._open_sound_picker
        )
        sound_pick_btn.pack(side=tk.LEFT, padx=(4, 0))
        self._card_widgets["sound_pick"] = sound_pick_btn
        self._hoverize(sound_pick_btn, THEME["border_light"], THEME["border"])
        # Mouse wheel over the picker adjusts the current sound's volume
        # (Windows delivers <MouseWheel> to the focus widget, so listen app-wide
        # and route only events whose pointer is over the button).
        if not getattr(self, "_wheel_bound", False):
            self.root.bind_all("<MouseWheel>", self._on_sound_wheel, add="+")
            self._wheel_bound = True
        
        # === Day Strip: Hour timeline + label ===
        day_strip_frame = tk.Frame(main_frame, bg=bg_color)
        day_strip_frame.pack(fill=tk.X, pady=(4, 0))
        
        day_strip_canvas = tk.Canvas(
            day_strip_frame,
            width=self.cfg["ui"]["compact_width"] - 30,
            height=14,
            bg=bg_color,
            highlightthickness=0,
            cursor="hand2"
        )
        day_strip_canvas.pack(fill=tk.X)
        self._card_widgets["day_strip"] = day_strip_canvas
        day_strip_canvas.bind("<Button-1>", lambda e: self._open_dashboard())
        
        day_strip_label = tk.Label(
            day_strip_frame,
            text=T("today_total", h=0, m=0),
            font=("Segoe UI", 7),
            fg=THEME["muted"],
            bg=bg_color,
            anchor="w"
        )
        day_strip_label.pack(fill=tk.X, pady=(2, 0))
        self._card_widgets["day_strip_label"] = day_strip_label
        
        tokens_label = tk.Label(
            day_strip_frame,
            text=T("ai_tokens", n=0),
            font=("Segoe UI", 7),
            fg=THEME["muted"],
            bg=bg_color,
            anchor="w"
        )
        tokens_label.pack(fill=tk.X, pady=(2, 0))
        self._card_widgets["tokens_label"] = tokens_label
        
        maas_label = tk.Label(
            day_strip_frame,
            text="",
            font=("Segoe UI", 7),
            fg=THEME["muted"],
            bg=bg_color,
            anchor="w"
        )
        maas_label.pack(fill=tk.X, pady=(2, 0))
        self._card_widgets["maas_label"] = maas_label
        
        # === Footer: Stars + Expand + Close ===
        footer_frame = tk.Frame(main_frame, bg=bg_color)
        footer_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(4, 0))
        
        # Star rating
        stars_label = tk.Label(
            footer_frame,
            text="☆☆☆☆☆",
            font=("Segoe UI", 12),
            fg=THEME["neutral"],
            bg=bg_color
        )
        stars_label.pack(side=tk.LEFT)
        self._card_widgets["stars"] = stars_label
        
        # Expand button
        expand_btn = tk.Button(
            footer_frame,
            text=T("dashboard_btn"),
            font=("Segoe UI", 8, "bold"),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._open_dashboard
        )
        expand_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self._hoverize(expand_btn, THEME["border_light"], THEME["border"])
        
        # Close button (hides to the pet when the pet is around; quits otherwise)
        close_btn = tk.Button(
            footer_frame,
            text="✕",
            font=("Segoe UI", 8, "bold"),
            bg=THEME["border_light"],
            fg=THEME["muted"],
            relief=tk.FLAT,
            bd=0,
            padx=5,
            pady=3,
            cursor="hand2",
            command=self._close_button
        )
        close_btn.pack(side=tk.RIGHT)
        self._hoverize(close_btn, THEME["border_light"], THEME["distracting"], THEME["muted"], THEME["white"])
        
        # Make draggable
        self._make_draggable(self.card_frame)
        
        # Right-click context menu
        self.card_frame.bind("<Button-3>", self._show_card_menu)
        
        # Track pomodoro start snapshots for live session
        self._pomo_start_totals: Optional[dict] = None
        self._pulse_state = False  # For timer ring pulse effect
        
        self._fit_card()
    
    def _hoverize(self, btn, normal_bg, hover_bg, normal_fg=None, hover_fg=None):
        """Give a flat tk.Button a hover colour (tracks bg changes made elsewhere)."""
        def on_enter(_e):
            btn._normal_bg = btn.cget("bg")
            btn.config(bg=hover_bg)
            if hover_fg:
                btn.config(fg=hover_fg)
        
        def on_leave(_e):
            btn.config(bg=getattr(btn, "_normal_bg", normal_bg))
            if normal_fg:
                btn.config(fg=normal_fg)
        
        btn.config(activebackground=hover_bg)
        btn.bind("<Enter>", on_enter, add="+")
        btn.bind("<Leave>", on_leave, add="+")
    
    def _fit_card(self):
        """Size the card to its content and repaint the rounded background."""
        if not self.card_frame or not self._card_main:
            return
        try:
            self._card_main.update_idletasks()
            width = self.cfg["ui"]["compact_width"]
            height = self._card_main.winfo_reqheight() + 28
            if self._card_size == (width, height):
                return
            self._card_size = (width, height)
            self.card_frame.geometry(f"{width}x{height}")
            canvas = self._card_bg
            canvas.delete("chrome")
            r = CARD_RADIUS
            # soft shadow (two offset layers), then the paper card with a hairline border
            for offset, colour in ((3, THEME.get("shadow_1", "#d8d2c7")), (2, THEME.get("shadow_2", "#e4dfd6"))):
                canvas.create_polygon(
                    self._round_rect_pts(1 + offset // 2, 1 + offset, width - 3 + offset // 2, height - 4 + offset, r),
                    smooth=True, fill=colour, outline="", tags="chrome")
            canvas.create_polygon(
                self._round_rect_pts(1, 1, width - 3, height - 4, r),
                smooth=True, fill=THEME["paper_bg"], outline=THEME["border"], width=1, tags="chrome")
            canvas.tag_lower("chrome")
        except Exception as e:
            logger.warning(f"Card fit failed: {e}")
    
    @staticmethod
    def _round_rect_pts(x1, y1, x2, y2, r):
        return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    
    # ------------------------------------------------------------------
    # Desktop pet integration
    # ------------------------------------------------------------------
    def _build_pet(self):
        """Create the desktop pet (if the module is available)."""
        if not pet_mod:
            return
        try:
            icon = BASE_DIR / "assets" / "widget.ico"
            self.pet = pet_mod.DesktopPet(
                self.root,
                self.cfg,
                BASE_DIR,
                callbacks={
                    "toggle_window": self._toggle_card,
                    "is_window_open": self._card_visible,
                    "open_dashboard": self._open_dashboard,
                    "save_settings": self._save_pet_settings,
                    "hide_pet": self._hide_pet,
                    "quit": self._on_close,
                    "set_language": self._set_language,
                    "set_theme": self._set_theme,
                    "check_updates": lambda: self._check_updates(manual=True),
                    "open_user_guide": self._open_user_guide,
                    "open_focus_trail": self._open_focus_trail,
                    "open_token_board": self._open_token_board if self._token_board_ok() else None,
                    "token_board_label": self._token_board_label,
                    "token_board_hint": self._token_board_hint,
                    "version": lambda: APP_VERSION,
                    "owner": lambda: APP_OWNER,
                    "update_info": lambda: self._update_manifest,
                    "show_update": self._show_pending_update,
                    "icon_path": str(icon) if icon.exists() else None,
                },
            )
            logger.info("Desktop pet created")
        except Exception as e:
            logger.error(f"Failed to create desktop pet: {e}")
            self.pet = None
    
    def _save_pet_settings(self, settings: dict):
        """Persist pet settings to config.json (merge-write)."""
        self.cfg["pet"] = dict(settings)
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    disk = json.load(f)
            disk["pet"] = dict(settings)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save pet settings: {e}")
    
    def _card_visible(self) -> bool:
        try:
            return bool(self.card_frame) and self.card_frame.state() != "withdrawn"
        except tk.TclError:
            return False
    
    def _toggle_card(self):
        if self._card_visible():
            self.card_frame.withdraw()
        else:
            self._show_card(near_pet=True)
    
    def _show_card(self, near_pet: bool = False):
        if not self.card_frame:
            return
        if (near_pet and self.pet and self.pet.is_visible()
                and self.pet.s.get("open_near_pet", True)):
            self._place_card_near_pet()
        if self.pet:
            self.pet.dismiss_chatter()
        self.card_frame.deiconify()
        self.card_frame.lift()
        self._update_card()
    
    def _hide_card(self):
        """Hide the card to the pet (summons the pet if it was hidden)."""
        if not self.pet:
            self._on_close()
            return
        if not self.pet.visible:
            self.pet.summon()
        self.card_frame.withdraw()
        self.pet.react("card_hidden")
    
    def _close_button(self):
        if self.pet:
            self._hide_card()
        else:
            self._on_close()
    
    def _hide_pet(self):
        """Hide the desktop pet. 2.25.1: the Pomodoro card no longer pops up - the tray icon and the
        hotkey bring the pet back. Only without a tray icon (nothing else on screen) does the card show."""
        if not self.pet:
            return
        self.pet.set_visible(False)
        if not self._tray and not self._card_visible():
            self._show_card()
        self._hide_tip()
    
    def _toggle_pet_visible(self):
        if not self.pet:
            return
        if self.pet.visible:
            self._hide_pet()
        else:
            self._summon_pet()
    
    def _place_card_near_pet(self):
        """Pop the card up right next to the pet, kept inside the monitor's work area."""
        try:
            self._fit_card()
            bx, by, bw, bh = self.pet.screen_bbox()
            width, height = self._card_size or (self.cfg["ui"]["compact_width"], 340)
            _mon, work = pet_mod.monitor_rects(bx + bw / 2, by + bh / 2, self.root)
            x = bx + bw / 2 - width / 2
            y = by - height - 14
            if y < work[1] + 8:
                # no room above: open beside the pet instead
                y = by + bh / 2 - height / 2
                x = bx + bw + 14 if bx + bw + 14 + width <= work[2] else bx - width - 14
            x = max(work[0] + 8, min(work[2] - width - 8, x))
            y = max(work[1] + 8, min(work[3] - height - 8, y))
            self.card_frame.geometry(f"+{int(x)}+{int(y)}")
        except Exception as e:
            logger.warning(f"Could not place card near pet: {e}")
    
    def _build_dashboard(self):
        """Build the dashboard window (hidden initially)."""
        self.dashboard_window = tk.Toplevel(self.root)
        self.dashboard_window.title(T("dash_title"))
        self.dashboard_window.withdraw()
        
        bg_color = THEME["paper_bg"]
        fg_color = THEME["ink_black"]
        accent_color = THEME["accent_red"]
        
        self.dashboard_window.configure(bg=bg_color)
        self.dashboard_window.geometry("880x800")
        self.dashboard_window.minsize(820, 750)
        
        # Header
        header = tk.Label(
            self.dashboard_window,
            text=T("dash_heading"),
            font=("Georgia", 16, "bold"),
            fg=fg_color,
            bg=bg_color,
            pady=10
        )
        header.pack(fill=tk.X)
        
        # Scrollable content
        canvas = tk.Canvas(self.dashboard_window, bg=bg_color, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.dashboard_window, orient=tk.VERTICAL, command=canvas.yview)
        
        content_frame = tk.Frame(canvas, bg=bg_color)
        def update_scrollregion(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        content_frame.bind("<Configure>", update_scrollregion)
        # Force initial scrollregion update after layout
        content_frame.after(100, update_scrollregion)
        
        win_id = canvas.create_window((0, 0), window=content_frame, anchor="nw")
        self._dashboard_canvas = canvas
        self._dashboard_canvas_win = win_id
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # === Charts Row 1: Donut + Top Apps ===
        charts_row1 = tk.Frame(content_frame, bg=bg_color)
        charts_row1.pack(fill=tk.X, padx=15, pady=5)
        
        # Donut chart (left)
        donut_frame = tk.LabelFrame(
            charts_row1,
            text="  " + T("time_split") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        donut_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        donut_canvas = tk.Canvas(donut_frame, width=200, height=200, bg=bg_color, highlightthickness=0)
        donut_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["donut"] = donut_canvas
        
        # Top apps bar chart (right)
        apps_frame = tk.LabelFrame(
            charts_row1,
            text="  " + T("top_apps") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        apps_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        
        apps_canvas = tk.Canvas(apps_frame, width=320, height=200, bg=bg_color, highlightthickness=0)
        apps_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["top_apps"] = apps_canvas
        
        # === Charts Row 2: 7-day Trend ===
        trend_frame = tk.LabelFrame(
            content_frame,
            text="  " + T("score_trend") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        trend_frame.pack(fill=tk.X, padx=15, pady=5)
        
        trend_canvas = tk.Canvas(trend_frame, width=820, height=160, bg=bg_color, highlightthickness=0)
        trend_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["trend"] = trend_canvas
        
        # === Charts Row 3: Hour Heatmap ===
        heatmap_frame = tk.LabelFrame(
            content_frame,
            text="  " + T("heatmap") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        heatmap_frame.pack(fill=tk.X, padx=15, pady=5)
        
        heatmap_canvas = tk.Canvas(heatmap_frame, width=820, height=180, bg=bg_color, highlightthickness=0)
        heatmap_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["heatmap"] = heatmap_canvas
        
        # Detail line (for click interactions)
        detail_label = tk.Label(
            heatmap_frame,
            text=T("click_chart"),
            font=("Segoe UI", 9),
            fg=THEME["muted"],
            bg=bg_color,
            anchor="w"
        )
        detail_label.pack(fill=tk.X, pady=(0, 5))
        self._dashboard_widgets["detail"] = detail_label
        
        # === Focus Timeline ===
        timeline_frame = tk.LabelFrame(
            content_frame,
            text="  " + T("focus_timeline") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        timeline_frame.pack(fill=tk.X, padx=15, pady=5)
        
        timeline_canvas = tk.Canvas(timeline_frame, width=820, height=80, bg=bg_color, highlightthickness=0)
        timeline_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["focus_timeline"] = timeline_canvas
        
        # === Pomodoros ===
        pomodoros_frame = tk.LabelFrame(
            content_frame,
            text="  " + T("pomos_7d") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        pomodoros_frame.pack(fill=tk.X, padx=15, pady=5)
        
        pomodoros_canvas = tk.Canvas(pomodoros_frame, width=320, height=180, bg=bg_color, highlightthickness=0)
        pomodoros_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["pomodoros_chart"] = pomodoros_canvas
        
        # === AI Tokens ===
        tokens_frame = tk.LabelFrame(
            content_frame,
            text="  " + T("ai_7d") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        tokens_frame.pack(fill=tk.X, padx=15, pady=5)
        tokens_canvas = tk.Canvas(tokens_frame, width=820, height=180, bg=bg_color, highlightthickness=0)
        tokens_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["tokens_chart"] = tokens_canvas
        
        # === MAAS Token Usage ===
        maas_frame = tk.LabelFrame(
            content_frame,
            text="  " + T("maas_usage") + "  ",
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=5,
            pady=5
        )
        maas_frame.pack(fill=tk.X, padx=15, pady=5)
        maas_canvas = tk.Canvas(maas_frame, width=820, height=180, bg=bg_color, highlightthickness=0)
        maas_canvas.pack(padx=5, pady=5)
        self._dashboard_widgets["maas_chart"] = maas_canvas
        
        # === Goals Progress ===
        goals_frame = tk.LabelFrame(
            content_frame,
            text=T("goals"),
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=10,
            pady=10
        )
        goals_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Deep work
        tk.Label(goals_frame, text=T("deep_work"), font=("Segoe UI", 9), fg=fg_color, bg=bg_color).grid(row=0, column=0, sticky="w")
        deep_progress = tk.Canvas(goals_frame, height=16, bg=THEME["border_light"], highlightthickness=0, width=200)
        deep_progress.grid(row=0, column=1, padx=10)
        self._dashboard_widgets["deep_progress"] = deep_progress
        
        # Pomodoros
        tk.Label(goals_frame, text=T("pomodoros_lbl"), font=("Segoe UI", 9), fg=fg_color, bg=bg_color).grid(row=1, column=0, sticky="w")
        pomo_label = tk.Label(goals_frame, text="0 / 8", font=("Segoe UI", 9), fg=accent_color, bg=bg_color)
        pomo_label.grid(row=1, column=1, sticky="w")
        self._dashboard_widgets["pomodoros_label"] = pomo_label
        
        # === Streak & XP ===
        streak_frame = tk.LabelFrame(
            content_frame,
            text=T("streak_xp"),
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=10,
            pady=10
        )
        streak_frame.pack(fill=tk.X, padx=20, pady=10)
        
        streak_label = tk.Label(streak_frame, text=T("streak", n=0), font=("Segoe UI", 12), fg=fg_color, bg=bg_color)
        streak_label.pack(anchor="w")
        self._dashboard_widgets["streak"] = streak_label
        
        xp_label = tk.Label(streak_frame, text=T("xp", n=0), font=("Segoe UI", 12), fg=THEME["neutral"], bg=bg_color)
        xp_label.pack(anchor="w", pady=(5, 0))
        self._dashboard_widgets["xp"] = xp_label
        
        # === Coach Text ===
        coach_frame = tk.LabelFrame(
            content_frame,
            text=T("coach"),
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color,
            padx=10,
            pady=10
        )
        coach_frame.pack(fill=tk.X, padx=20, pady=10)
        
        coach_text = tk.Text(
            coach_frame,
            height=4,
            font=("Segoe UI", 9),
            fg=fg_color,
            bg=THEME["white"],
            wrap=tk.WORD,
            padx=10,
            pady=10
        )
        coach_text.pack(fill=tk.X)
        self._dashboard_widgets["coach"] = coach_text
        
        # Close button
        close_btn = tk.Button(
            content_frame,
            text=T("close"),
            font=("Segoe UI", 10),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._close_dashboard
        )
        close_btn.pack(pady=10)
        
        # Handle window close - cleanup tooltips
        self.dashboard_window.protocol("WM_DELETE_WINDOW", self._close_dashboard)
        
        # Tooltip window (shared)
        self._tooltip: Optional[tk.Toplevel] = None
        
        # Bind motion for tooltips
        for widget_name in ["donut", "top_apps", "trend", "heatmap", "focus_timeline", "pomodoros_chart", "tokens_chart", "maas_chart"]:
            c = self._dashboard_widgets.get(widget_name)
            if c and isinstance(c, tk.Canvas):
                c.bind("<Motion>", lambda e, n=widget_name: self._on_chart_motion(e, n))
                c.bind("<Leave>", self._on_chart_leave)
                c.bind("<Button-1>", lambda e, n=widget_name: self._on_chart_click(e, n))
        
        def _on_mousewheel(event):
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass
        
        def _bind_wheel(widget):
            try:
                widget.bind("<MouseWheel>", _on_mousewheel, add="+")
                for child in widget.winfo_children():
                    _bind_wheel(child)
            except Exception:
                pass
        
        _bind_wheel(content_frame)
        _bind_wheel(canvas)
        self.dashboard_window.bind("<MouseWheel>", _on_mousewheel, add="+")
        
        def _refresh_scrollregion(_event=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass
        
        self._dashboard_refresh_scrollregion = _refresh_scrollregion
        self.dashboard_window.bind("<Map>", lambda e: canvas.after(60, _refresh_scrollregion))
        self.dashboard_window.bind("<Configure>", lambda e: _refresh_scrollregion())
    
    def _build_history(self):
        """Build the history browser window."""
        self.history_window = tk.Toplevel(self.root)
        self.history_window.title(T("hist_title"))
        self.history_window.withdraw()
        
        bg_color = THEME["paper_bg"]
        fg_color = THEME["ink_black"]
        accent_color = THEME["accent_red"]
        
        self.history_window.configure(bg=bg_color)
        self.history_window.geometry("500x450")
        
        # Navigation
        nav_frame = tk.Frame(self.history_window, bg=bg_color)
        nav_frame.pack(fill=tk.X, pady=10)
        
        prev_btn = tk.Button(
            nav_frame,
            text=T("prev"),
            font=("Segoe UI", 9),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._history_prev
        )
        prev_btn.pack(side=tk.LEFT, padx=20)
        
        date_label = tk.Label(
            nav_frame,
            text=datetime.now().strftime("%Y-%m-%d"),
            font=("Georgia", 11, "bold"),
            fg=fg_color,
            bg=bg_color
        )
        date_label.pack(side=tk.LEFT, expand=True)
        self._history_widgets["date_label"] = date_label
        
        next_btn = tk.Button(
            nav_frame,
            text=T("next"),
            font=("Segoe UI", 9),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._history_next
        )
        next_btn.pack(side=tk.LEFT, padx=20)
        
        today_btn = tk.Button(
            nav_frame,
            text=T("today"),
            font=("Segoe UI", 9),
            bg=accent_color,
            fg=THEME["white"],
            relief=tk.FLAT,
            cursor="hand2",
            command=self._history_today
        )
        today_btn.pack(side=tk.RIGHT, padx=20)
        
        # Score display
        score_frame = tk.Frame(self.history_window, bg=bg_color)
        score_frame.pack(fill=tk.X, padx=20, pady=10)
        
        score_label = tk.Label(
            score_frame,
            text=T("score", s="--"),
            font=("Georgia", 14, "bold"),
            fg=accent_color,
            bg=bg_color
        )
        score_label.pack(side=tk.LEFT, padx=10)
        self._history_widgets["score"] = score_label
        
        stars_label = tk.Label(
            score_frame,
            text="☆☆☆☆☆",
            font=("Segoe UI", 14),
            fg=THEME["neutral"],
            bg=bg_color
        )
        stars_label.pack(side=tk.LEFT)
        self._history_widgets["stars"] = stars_label
        
        pomo_label = tk.Label(
            score_frame,
            text=T("pomos_n", n=0),
            font=("Segoe UI", 10),
            fg=fg_color,
            bg=bg_color
        )
        pomo_label.pack(side=tk.RIGHT)
        self._history_widgets["pomodoros"] = pomo_label
        
        # App breakdown
        breakdown_text = tk.Text(
            self.history_window,
            height=15,
            font=("Courier New", 9),
            fg=fg_color,
            bg=THEME["white"],
            wrap=tk.WORD,
            padx=10,
            pady=10
        )
        breakdown_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        self._history_widgets["breakdown"] = breakdown_text
        
        # Close button
        close_btn = tk.Button(
            self.history_window,
            text=T("close"),
            font=("Segoe UI", 10),
            bg=THEME["border_light"],
            fg=fg_color,
            relief=tk.FLAT,
            cursor="hand2",
            command=self.history_window.withdraw
        )
        close_btn.pack(pady=10)
    
    def _make_draggable(self, widget):
        """Make a widget draggable by mouse."""
        def on_press(event):
            self._drag_data["x"] = event.x
            self._drag_data["y"] = event.y
            self._drag_data["active"] = True
        
        def on_drag(event):
            if not self._drag_data["active"]:
                return
            delta_x = event.x - self._drag_data["x"]
            delta_y = event.y - self._drag_data["y"]
            x = widget.winfo_x() + delta_x
            y = widget.winfo_y() + delta_y
            widget.geometry(f"+{x}+{y}")
        
        def on_release(event):
            self._drag_data["active"] = False
            # Save position
            self._save_position()
        
        widget.bind("<Button-1>", on_press)
        widget.bind("<B1-Motion>", on_drag)
        widget.bind("<ButtonRelease-1>", on_release)
    
    def _position_card(self):
        """Position the card based on config or center (always kept on-screen)."""
        pos = self.cfg["ui"].get("pos")
        width, height = self._card_size or (self.cfg["ui"]["compact_width"], 340)
        if pos and isinstance(pos, (list, tuple)) and len(pos) == 2:
            x, y = pos
        else:
            # Center on screen
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            x = (screen_width - self.cfg["ui"]["compact_width"]) // 2
            y = screen_height // 4
        # Clamp to the monitor's work area so a stale position can't hide the card
        if pet_mod:
            _mon, work = pet_mod.monitor_rects(x + width / 2, y + 20, self.root)
        else:
            work = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 40)
        x = int(max(work[0], min(work[2] - width, x)))
        y = int(max(work[1], min(work[3] - height, y)))
        self.card_frame.geometry(f"+{x}+{y}")
    
    def _save_position(self):
        """Save current card position to config."""
        if not self.card_frame:
            return
        pos = [self.card_frame.winfo_x(), self.card_frame.winfo_y()]
        self.cfg["ui"]["pos"] = pos
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    disk = json.load(f)
            disk.setdefault("ui", {})["pos"] = pos
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save position: {e}")
    

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def _auto_update_enabled(self) -> bool:
        pet_settings = self.pet.s if self.pet else (self.cfg.get("pet") or {})
        return bool(pet_settings.get("auto_update", True))
    
    def _auto_install_enabled(self) -> bool:
        """v2.28: found updates install themselves (not in the developer copy - that one uses release.py)."""
        if not updater or (BASE_DIR / "release.py").exists():
            return False
        pet_settings = self.pet.s if self.pet else (self.cfg.get("pet") or {})
        return self._auto_update_enabled() and bool(pet_settings.get("auto_install", True))

    def _auto_install_pending(self, manifest) -> bool:
        """True while an update is waiting to install itself (so no 'Update now?' bubble is shown)."""
        return (bool(manifest) and self._auto_install_enabled()
                and manifest.get("version") != getattr(self, "_auto_install_failed", None))

    def _focus_running(self) -> bool:
        try:
            _state, _remaining, running = self.pomodoro.get_state()
            return bool(running)
        except Exception:
            return False

    AUTO_INSTALL_RETRY_MS = 60 * 1000      # a focus session is running: look again in a minute

    def _auto_install(self, manifest: dict):
        if self._update_installing or not self._auto_install_pending(manifest):
            return
        current = self._update_manifest or {}
        if current.get("version") != manifest.get("version"):
            return                          # a newer manifest arrived meanwhile - that one wins
        if self._focus_running():
            self.root.after(self.AUTO_INSTALL_RETRY_MS, lambda: self._auto_install(manifest))
            return
        version = manifest.get("version", "?")
        logger.info(f"Auto-installing Pips {version}")
        self._set_updates_value("announce_version", version)
        self._tell(T("upd_auto", v=version), duration=20, priority=3)
        self.root.after(3000, lambda: self._install_update(manifest, auto=True))

    def _set_updates_value(self, key: str, value):
        """Merge-write cfg['updates'][key] into config.json (same pattern as _skip_version)."""
        updates = self.cfg.setdefault("updates", {})
        if value is None:
            updates.pop(key, None)
        else:
            updates[key] = value
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    disk = json.load(f)
            block = disk.setdefault("updates", {})
            if value is None:
                block.pop(key, None)
            else:
                block[key] = value
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save updates.{key}: {e}")

    def _announce_installed_update(self):
        """After an automatic update + restart: the pet tells what's new (once)."""
        pending = (self.cfg.get("updates") or {}).get("announce_version")
        if not pending:
            return
        if pending != APP_VERSION:
            if updater and not updater.is_newer(pending, APP_VERSION):
                self._set_updates_value("announce_version", None)   # stale marker
            return
        self._set_updates_value("announce_version", None)
        notes = []
        try:
            with open(BASE_DIR / "CHANGELOG.json", "r", encoding="utf-8-sig") as f:
                releases = json.load(f).get("releases") or []
            release = next((r for r in releases if r.get("version") == APP_VERSION), None)
            if release and updater:
                notes = updater.notes_for(release, i18n.get_language() if i18n else "en")[:4]
        except Exception as e:
            logger.info(f"What's new unavailable: {e}")
        notes_text = "\n".join("\u2022 " + n for n in notes)
        self._tell(T("upd_whats_new", v=APP_VERSION, notes=notes_text), duration=25, priority=2)

    UPDATE_CHECK_MS = 30 * 60 * 1000       # background check - no need to press "Check for updates"
    UPDATE_OFFLINE_MS = 15 * 60 * 1000     # no connection (VPN off?): try again soon
    UPDATE_WATCH_MS = 60 * 1000            # keep the update bubble on screen until the user decides
    UPDATE_SNOOZE_S = 60 * 60              # "Later" / x hides the bubble for an hour
    UPDATE_BUBBLE_S = 24 * 3600            # the update bubble stays up (it never times out by itself)

    def _auto_update_tick(self):
        started = self._auto_update_enabled() and self._check_updates(manual=False)
        if not started:
            self.root.after(self.UPDATE_CHECK_MS, self._auto_update_tick)

    def _schedule_auto_update(self, kind: str):
        delay = self.UPDATE_OFFLINE_MS if kind == "offline" else self.UPDATE_CHECK_MS
        self.root.after(delay, self._auto_update_tick)

    def _update_skipped(self, manifest) -> bool:
        skipped = (self.cfg.get("updates") or {}).get("skipped_version")
        return bool(manifest) and skipped == manifest.get("version")

    def _update_bubble_showing(self) -> bool:
        bubble = getattr(self.pet, "bubble", None) if self.pet else None
        return bool(bubble and bubble.visible and getattr(bubble, "_pips_update", False))

    def _update_watchdog(self):
        """While an update is waiting, keep its bubble on screen (it comes back after other messages)."""
        try:
            manifest = self._update_manifest
            if (manifest and self._auto_update_enabled() and not self._update_busy
                    and not self._update_installing
                    and not self._auto_install_pending(manifest)
                    and not self._update_skipped(manifest)
                    and time.monotonic() >= self._update_snooze_until
                    and not self._update_bubble_showing()):
                bubble = getattr(self.pet, "bubble", None) if self.pet else None
                if not (bubble and bubble.visible):
                    self._offer_update(manifest, manual=False)
        except Exception as e:
            logger.info(f"Update watchdog: {e}")
        self.root.after(self.UPDATE_WATCH_MS, self._update_watchdog)
    
    def _check_updates(self, manual: bool = False):
        """Check the published manifest in a background thread."""
        if not updater or self._update_busy:
            return False
        self._update_busy = True
        
        def worker():
            try:
                manifest = updater.check(APP_VERSION)
                outcome = ("found", manifest) if manifest else ("latest", None)
            except Exception as e:
                logger.info(f"Update check failed: {e}")
                outcome = ("offline", str(e))
            self.root.after(0, lambda: self._on_update_result(outcome, manual))
        
        threading.Thread(target=worker, daemon=True).start()
        return True
    
    def _on_update_result(self, outcome, manual: bool):
        self._update_busy = False
        kind, payload = outcome
        if not manual:
            self._schedule_auto_update(kind)
        if kind == "found":
            previous = (self._update_manifest or {}).get("version")
            self._update_manifest = payload
            if manual:
                self._update_snooze_until = 0.0
                self._offer_update(payload, manual=True)
                return
            if previous and previous != payload.get("version"):
                self._update_snooze_until = 0.0   # an even newer version: show it right away
            if self._update_skipped(payload):
                return
            if self._auto_install_pending(payload):
                self._auto_install(payload)
                return
            if time.monotonic() < self._update_snooze_until:
                return
            if not self._update_bubble_showing():
                self._offer_update(payload, manual=False)
        elif kind == "latest":
            self._update_manifest = None
            if self._update_bubble_showing():
                self.pet.bubble.hide()
            if manual:
                self._tell(T("upd_latest", v=APP_VERSION))
        elif manual:
            self._tell(T("upd_offline"))
    
    def _tell(self, text, actions=None, duration=6.0, priority=2):
        if self.pet and self.pet.is_visible():
            self.pet.say(text, actions=actions, duration=duration, priority=priority)
            return
        try:
            from tkinter import messagebox
            messagebox.showinfo("Pips", text, parent=self.card_frame)
        except Exception:
            logger.info(text)
    
    def _snooze_update(self):
        self._update_snooze_until = time.monotonic() + self.UPDATE_SNOOZE_S

    def _offer_update(self, manifest: dict, manual: bool = True):
        version = manifest.get("version", "?")
        lang = i18n.get_language() if i18n else "en"
        notes = updater.notes_for(manifest, lang)[:4]
        notes_text = "\n".join("\u2022 " + n for n in notes)
        text = T("upd_available", v=version, notes=notes_text)
        if self.pet and self.pet.is_visible():
            # stays up until the user picks something; if another message pushes it away,
            # the watchdog brings it back a minute later
            shown = self.pet.say(
                text,
                actions=[
                    (T("upd_now"), lambda: self._install_update(manifest)),
                    (T("upd_later"), self._snooze_update),
                    (T("upd_skip"), lambda: self._skip_version(version)),
                ],
                duration=self.UPDATE_BUBBLE_S,
                priority=2,
                sticky=True,
                on_close=self._snooze_update,
            )
            if shown:
                self.pet.bubble._pips_update = True
            return
        if not manual:
            # pet hidden in the tray (not fullscreen): ask once per session, otherwise wait for the pet
            if not self.pet or self.pet.fs_hidden or version in self._update_dialog_shown:
                return
            self._update_dialog_shown.add(version)
        from tkinter import messagebox
        if messagebox.askyesno("Pips", text + "\n\n" + T("upd_now") + "?", parent=self.card_frame):
            self._install_update(manifest)
        else:
            self._snooze_update()

    def _show_pending_update(self):
        """Menu banner click: show the update offer (or re-check if it went stale)."""
        if self._update_manifest:
            self._offer_update(self._update_manifest)
        else:
            self._check_updates(manual=True)
    
    def _skip_version(self, version: str):
        self.cfg.setdefault("updates", {})["skipped_version"] = version
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    disk = json.load(f)
            disk.setdefault("updates", {})["skipped_version"] = version
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save skipped version: {e}")
    
    def _install_update(self, manifest: dict, auto: bool = False):
        if not updater:
            return
        if self._update_installing:
            return
        self._update_installing = True
        version = manifest.get("version", "?")
        self._tell(T("upd_downloading"), duration=120, priority=3)
        
        def progress(stage):
            if stage == "install":
                self.root.after(0, lambda: self._tell(T("upd_installing"), duration=120, priority=3))
        
        def worker():
            try:
                updater.install(manifest, BASE_DIR, APP_VERSION, progress=progress)
                self.root.after(0, lambda: self._finish_update(version))
            except Exception as e:
                logger.error(f"Update failed: {e}")
                message = str(e)
                self._update_installing = False
                if auto:
                    # don't retry silently: from now on this version is offered with the normal bubble
                    self._auto_install_failed = version
                    self.root.after(0, lambda: self._set_updates_value("announce_version", None))
                self._update_snooze_until = time.monotonic() + self.UPDATE_SNOOZE_S
                self.root.after(0, lambda: self._tell(
                    T("upd_failed", err=message),
                    actions=[(T("upd_open_site"), lambda: os.startfile(SITE_URL))],
                    duration=30, priority=3))
        
        threading.Thread(target=worker, daemon=True).start()
    
    def _finish_update(self, version: str):
        self._tell(T("upd_done", v=version), duration=10, priority=3)
        
        def relaunch():
            try:
                updater.restart(BASE_DIR)
            finally:
                self._on_close()
        
        self.root.after(2200, relaunch)
    
    def _open_focus_trail(self):
        """v2.26: open (or bring back) the Strava-style Focus Trail sticker window."""
        try:
            import focus_trail
        except Exception as e:
            logger.warning(f"Focus Trail unavailable: {e}")
            return False
        window = getattr(self, "_focus_trail_win", None)
        try:
            if window is not None and window.alive():
                window.show()
                return True
        except Exception:
            pass
        pet = getattr(self, "pet", None)
        notify = pet.say if pet is not None and hasattr(pet, "say") else None
        try:
            self._focus_trail_win = focus_trail.open_window(self.root, self.con, self.cfg, notify=notify)
            return True
        except Exception as e:
            logger.error(f"Focus Trail failed to open: {e}")
            return False

    # v2.28: Token Board (token_board.py - private build only, needs the MAAS login)
    def _token_board_ok(self) -> bool:
        try:
            import token_board
            return token_board.available(self.cfg)
        except Exception:
            return False

    def _token_board_label(self) -> str:
        try:
            import token_board
            return token_board.menu_label()
        except Exception:
            return "Token Board"

    def _token_board_hint(self) -> str:
        try:
            import token_board
            return token_board.menu_hint()
        except Exception:
            return ""

    def _token_board_watch_period(self) -> int:
        """Seconds between background checks; 0 = watcher off / unavailable."""
        if not self._token_board_ok():
            return 0
        try:
            import token_board
            return token_board.watch_minutes(self.cfg) * 60
        except Exception:
            return 0

    def _token_board_watch_async(self):
        if getattr(self, "_tb_watching", False):
            return
        self._tb_watching = True

        def work():
            try:
                import token_board
                self._tb_news = token_board.check_for_news(self.cfg)
            except Exception as e:
                logger.warning(f"Token Board watch failed: {e}")
            finally:
                self._tb_watching = False

        threading.Thread(target=work, name="token-board-watch", daemon=True).start()

    def _deliver_token_news(self, news):
        try:
            import token_board
            if not (self.pet and token_board.alerts_enabled()):
                return
            open_label = token_board.tr("open")
        except Exception:
            return
        for index, message in enumerate(news[:3]):
            logger.info(f"Token Board alert: {message}")
            self.root.after(index * 9000, lambda m=message: self.pet.say(
                m, actions=[(open_label, self._open_token_board)], duration=9, priority=1))

    def _open_token_board(self):
        """Open (or bring back) the live MAAS usage + company Top-30 token board."""
        try:
            import token_board
        except Exception as e:
            logger.warning(f"Token Board unavailable: {e}")
            return False
        window = getattr(self, "_token_board_win", None)
        try:
            if window is not None and window.alive():
                window.show()
                return True
        except Exception:
            pass
        pet = getattr(self, "pet", None)
        notify = pet.say if pet is not None and hasattr(pet, "say") else None
        try:
            self._token_board_win = token_board.open_window(self.root, self.cfg, notify=notify)
            return True
        except Exception as e:
            logger.error(f"Token Board failed to open: {e}")
            return False

    def _open_user_guide(self):
        local = BASE_DIR / "Pips-Guide.pptx"
        try:
            os.startfile(str(local) if local.exists() else USER_GUIDE_URL)
        except Exception as e:
            logger.warning(f"Could not open user guide: {e}")
    
    # ------------------------------------------------------------------
    # Language
    # ------------------------------------------------------------------
    def _set_language(self, code: str):
        """Switch the UI language live (rebuilds the card/dashboard/history; timer keeps running)."""
        if not i18n:
            return
        code = i18n.set_language(code)
        self.cfg["language"] = code
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    disk = json.load(f)
            disk["language"] = code
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save language: {e}")
        self._rebuild_ui()
        if self.pet:
            self.pet.refresh_language()
            self.pet.react("language_changed")
        if getattr(self, "_tray", None):
            self._tray.set_tooltip(self._tray_tip(self._tray.hotkey_label))
        logger.info(f"Language set to {code}")
    
    def _set_theme(self, name: str):
        """Switch light / dark live: save it, recolour the card/dashboard and the pet windows."""
        try:
            import theme as theme_mod
        except ImportError:
            return
        name = theme_mod.set_theme(name)
        theme_mod.apply_to_app(THEME, PILL_COLORS)
        self.cfg.setdefault("ui", {})["theme"] = name
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    disk = json.load(f)
            disk.setdefault("ui", {})["theme"] = name
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save theme: {e}")
        self._rebuild_ui()
        if self.pet:
            try:
                self.pet.on_theme_changed()
            except Exception as e:
                logger.debug(f"pet theme refresh failed: {e}")
        logger.info(f"Theme set to {name}")

    def _rebuild_ui(self):
        """Destroy and rebuild translated windows, keeping card position/visibility."""
        try:
            card_visible = self._card_visible()
            pos = (self.card_frame.winfo_x(), self.card_frame.winfo_y()) if self.card_frame else None
            self._hide_tooltip()
            for win in (self.card_frame, self.dashboard_window, self.history_window, self._reminder_popup):
                if win is not None:
                    try:
                        win.destroy()
                    except tk.TclError:
                        pass
            self._reminder_popup = None
            self._card_widgets = {}
            self._dashboard_widgets = {}
            self._history_widgets = {}
            self._card_size = None
            self._card_main = None
            self._build_card()
            self._build_dashboard()
            self._build_history()
            if pos:
                self.card_frame.geometry(f"+{pos[0]}+{pos[1]}")
            if not card_visible:
                self.card_frame.withdraw()
            self._update_card()
        except Exception as e:
            logger.error(f"UI rebuild after language change failed: {e}")

    def _schedule_refresh(self):
        """Schedule UI refresh every 1 second."""
        self._refresh_card()
        self._refresh_after_id = self.root.after(1000, self._schedule_refresh)
    
    def _refresh_card(self):
        """Update card UI with current data and check reminders."""
        try:
            self._update_card()
            
            self._tokens_tick = getattr(self, "_tokens_tick", 0) + 1
            if tokens and self._tokens_tick % 30 == 0:
                try:
                    tokens.refresh(self.con)
                except Exception as e:
                    logger.warning(f"Tokens refresh error: {e}")
            
            self._maas_tick = getattr(self, "_maas_tick", 0) + 1
            if maas_tokens and self._maas_tick % 300 == 1:
                self._refresh_maas_async()
            
            # v2.28: Token Board watcher (rank changes / daily milestones -> pet bubble)
            self._tb_tick = getattr(self, "_tb_tick", 0) + 1
            if self._tb_tick == 1:
                self._tb_period = self._token_board_watch_period()
            if self._tb_period and self._tb_tick % self._tb_period == 30:
                self._token_board_watch_async()
            news = getattr(self, "_tb_news", None)
            if news:
                self._tb_news = None
                self._deliver_token_news(news)
            
            if self._maas_dirty:
                self._maas_dirty = False
                if self.dashboard_window and self.dashboard_window.winfo_viewable():
                    self._update_dashboard()
            
            if self.pomodoro:
                state, remaining, running = self.pomodoro.get_state()
                now = time.monotonic()
                prev_state = getattr(self, "_prev_pomo_state", None)
                
                if prev_state == PomodoroState.WORK and state in (PomodoroState.SHORT_BREAK, PomodoroState.LONG_BREAK):
                    # Work -> break: fade the soundscape out (or stop per config).
                    self._handle_break_audio()
                    
                    reminder = self.reminders.break_reminder() if self.reminders else None
                    if self.pet:
                        self.pet.react("pomodoro_done", announce=not reminder,
                                       count=self._pomodoros_today())
                        self._check_goal_reached()
                    if reminder:
                        self._show_reminder(reminder)
                
                focusing = state == PomodoroState.WORK and running
                if self.pet:
                    self.pet.set_focus(focusing)
                    self._check_distraction(focusing)
                
                if self.reminders:
                    if state == PomodoroState.WORK and running and prev_state != PomodoroState.WORK:
                        self.reminders.begin_work(now)
                    
                    if state == PomodoroState.WORK and running:
                        reminder = self.reminders.due(now, working=True)
                        if reminder:
                            self._show_reminder(reminder)
                
                if state in (PomodoroState.SHORT_BREAK, PomodoroState.LONG_BREAK) and hasattr(self, "_reminder_data") and self._reminder_popup and self._reminder_popup.winfo_viewable():
                    data = self._reminder_data
                    if data.get("kind") == "break" and data.get("countdown_label"):
                        mins = remaining // 60
                        secs = remaining % 60
                        data["countdown_label"].config(text=T("break_ends", m=mins, s=f"{secs:02d}"))
                
                self._prev_pomo_state = state
                
        except Exception as e:
            logger.error(f"Card refresh error: {e}")
    
    def _pomodoros_today(self) -> int:
        try:
            day = datetime.now().strftime("%Y-%m-%d")
            row = self.con.execute(
                "SELECT pomodoros_done FROM daily_summary WHERE day = ?", (day,)
            ).fetchone()
            return int(row[0]) if row and row[0] else 0
        except Exception:
            return 0
    
    def _check_goal_reached(self):
        """Celebrate (once per day) when today's pomodoro goal is reached."""
        day = datetime.now().strftime("%Y-%m-%d")
        goal = int(self.cfg.get("goals", {}).get("pomodoros", 0) or 0)
        if goal and self._goal_announced_day != day and self._pomodoros_today() >= goal:
            self._goal_announced_day = day
            self.root.after(9000, lambda: self.pet and self.pet.react("goal"))
    
    def _check_distraction(self, focusing: bool):
        """Nudge (via the pet) after ~2 min on a distracting app during a focus pomodoro."""
        if not focusing or not self.tracker:
            self._distract_secs = 0
            return
        try:
            current = self.tracker.current() or {}
            app = current.get("app", "") or ""
            title = current.get("title", "") or ""
            if scoring:
                category = scoring.categorize(app, title, self.cfg)
            else:
                category = "distracting" if app in self.cfg["category"]["distracting"] else "neutral"
        except Exception:
            return
        if category != "distracting":
            self._distract_secs = 0
            return
        self._distract_secs += 1
        if self._distract_secs >= 120 and time.time() >= self._distract_next:
            self._distract_secs = 0
            self._distract_next = time.time() + 600
            name = app[:-4] if app.lower().endswith(".exe") else app
            self.pet.react("distracted", app=name or "that")
    
    def _refresh_maas_async(self):
        """Fetch MAAS usage off the UI thread; store results for the draw path.

        Runs at most one fetch at a time and swallows all errors so the periodic
        updater can never crash the widget.
        """
        if not maas_tokens or self._maas_fetching:
            return
        self._maas_fetching = True
        
        def worker():
            try:
                if maas_tokens.enabled():
                    today = maas_tokens.today_summary(force=True)
                    daily = maas_tokens.daily_totals(7, force=True)
                    self._maas_today = today
                    self._maas_daily = daily
                    self._maas_dirty = True
            except Exception as e:
                logger.warning(f"MAAS fetch error: {e}")
            finally:
                self._maas_fetching = False
        
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception as e:
            self._maas_fetching = False
            logger.warning(f"MAAS fetch thread failed: {e}")
    
    def _refresh_maas_now(self):
        """Force a MAAS refresh: drop caches, then fetch asynchronously."""
        try:
            if not maas_tokens:
                logger.warning("maas_tokens module not available")
                return
            maas_tokens.invalidate_cache()
            self._refresh_maas_async()
        except Exception as e:
            logger.error(f"Refresh MAAS failed: {e}")
    
    def _update_card(self):
        """Update card widget values."""
        if not self._card_widgets:
            return
        
        # Get current app
        app_name = "Unknown"
        productive_time = "0h 0m"
        
        if self.tracker:
            try:
                current = self.tracker.current()
                app_name = current.get("app", "Unknown")
                if app_name.endswith(".exe"):
                    app_name = app_name[:-4]
                
                # Today's productive time
                today = datetime.now().strftime("%Y-%m-%d")
                totals = self.tracker.today_totals().get(today, {})
                productive_secs = sum(
                    secs for app, secs in totals.items()
                    if scoring and scoring.categorize(app, "", self.cfg) == "productive"
                ) if scoring else sum(
                    secs for app, secs in totals.items()
                    if app in self.cfg["category"]["productive"]
                )
                
                hours = productive_secs // 3600
                mins = (productive_secs % 3600) // 60
                productive_time = f"{hours}h {mins}m"
            except Exception as e:
                logger.warning(f"Tracker read error: {e}")
        
        # Update labels
        self._card_widgets["app_name"].config(text=app_name[:20] + "..." if len(app_name) > 20 else app_name)
        self._card_widgets["productive_time"].config(text=productive_time)
        
        # Update pomodoro UI
        if self.pomodoro:
            state, remaining, running = self.pomodoro.get_state()
            total = self._get_pomodoro_total(state)
            progress = (total - remaining) / total if total > 0 else 0
            elapsed = total - remaining
            
            # Update status pill
            self._update_status_pill(state, running, total, remaining)
            
            # Update cycle dots
            self._update_cycle_dots()
            
            # Draw timer ring
            self._draw_timer_ring(progress, state, running)
            
            # Update live session panel
            self._update_session_panel(state, running, elapsed)
            
            # Update start/pause button
            btn_text = T("pause") if running else T("start")
            self._card_widgets["start_btn"].config(text=btn_text)
        
        # Update stars
        if scoring:
            try:
                day = datetime.now().strftime("%Y-%m-%d")
                summary = scoring.compute_daily(self.con, day, self.cfg)
                stars = summary.get("stars", 0)
                star_str = "★" * stars + "☆" * (5 - stars)
                self._card_widgets["stars"].config(text=star_str)
            except Exception as e:
                logger.warning(f"Stars compute error: {e}")
        
        self._draw_day_strip()
        
        if tokens:
            try:
                summ = tokens.today_summary(self.con)
                self._card_widgets["tokens_label"].config(
                    text=T("ai_tokens", n=tokens.format_tokens(summ.get("total", 0)))
                )
            except Exception as e:
                logger.warning(f"Tokens card update error: {e}")
        
        if maas_tokens:
            try:
                label = self._card_widgets.get("maas_label")
                data = self._maas_today or {}
                if label and maas_tokens.enabled() and data.get("ok"):
                    label.config(text=T("maas_tokens", n=maas_tokens.format_tokens(data.get("total", 0))))
                elif label:
                    label.config(text="")
            except Exception as e:
                logger.warning(f"MAAS card update error: {e}")
        
        self._fit_card()
    
    def _update_status_pill(self, state: str, running: bool, total: int, remaining: int):
        """Update status pill text and color based on pomodoro state."""
        pill = self._card_widgets.get("status_pill")
        if not pill:
            return
        
        at_full_time = (remaining == total)
        
        if state == PomodoroState.WORK and running:
            fg, bg = PILL_COLORS["focus"]
            pill.config(text=T("focus"), fg=fg, bg=bg)
        elif state in (PomodoroState.SHORT_BREAK, PomodoroState.LONG_BREAK) and running:
            if state == PomodoroState.LONG_BREAK:
                break_len = self.cfg["pomodoro"]["long_break_minutes"]
            else:
                break_len = self.cfg["pomodoro"]["short_break_minutes"]
            fg, bg = PILL_COLORS["break"]
            pill.config(text=T("break_m", m=break_len), fg=fg, bg=bg)
        elif not running and not at_full_time:
            # Mid-session paused
            fg, bg = PILL_COLORS["paused"]
            pill.config(text=T("paused"), fg=fg, bg=bg)
        else:
            # Fresh/idle
            fg, bg = PILL_COLORS["idle"]
            pill.config(text=T("idle"), fg=fg, bg=bg)
    
    def _update_cycle_dots(self):
        """Update cycle dots showing progress toward long break."""
        dots_label = self._card_widgets.get("cycle_dots")
        if not dots_label or not self.pomodoro:
            return
        
        long_break_every = self.cfg["pomodoro"].get("long_break_every", 4)
        completed = self.pomodoro.completed_pomodoros % long_break_every
        
        filled = "●" * completed
        empty = "○" * (long_break_every - completed)
        dots_label.config(text=f"{filled}{empty}  {completed}/{long_break_every}", fg=THEME["accent_red"] if completed > 0 else THEME["border"])
    
    def _draw_timer_ring(self, progress: float, state: str, running: bool):
        """Draw ring progress timer on canvas."""
        canvas = self._card_widgets.get("timer")
        if not canvas or not isinstance(canvas, tk.Canvas):
            return
        
        canvas.delete("all")
        
        # Canvas dimensions
        width = 118
        height = 118
        center = width // 2
        radius = 50
        track_width = 9
        
        # Pulse effect when running
        if running:
            self._pulse_state = not self._pulse_state
            # gentle breathing track instead of a hard red/grey blink
            track_color = THEME.get("pulse_focus", "#efdede") if self._pulse_state else THEME["border_light"]
            if state != PomodoroState.WORK:
                track_color = THEME.get("pulse_break", "#dcefe2") if self._pulse_state else THEME["border_light"]
        else:
            track_color = THEME["border_light"]
        
        # Draw track arc (full circle)
        bbox = [center - radius, center - radius, center + radius, center + radius]
        canvas.create_arc(
            bbox,
            start=90,
            extent=359.99,
            style=tk.ARC,
            outline=track_color,
            width=track_width
        )
        
        # Draw progress arc
        if progress > 0:
            progress_color = THEME["accent_red"] if state == PomodoroState.WORK else THEME["productive"]
            canvas.create_arc(
                bbox,
                start=90,
                extent=-progress * 359.99,  # Clockwise from top
                style=tk.ARC,
                outline=progress_color,
                width=track_width
            )
        
        # Draw timer digits centered
        if self.pomodoro:
            time_text = self.pomodoro.get_display_time()
            canvas.create_text(
                center, center - 8,
                text=time_text,
                font=("Segoe UI Semibold", 21),
                fill=THEME["ink_black"]
            )
            
            # Draw percentage below
            pct = int(progress * 100)
            canvas.create_text(
                center, center + 14,
                text=f"{pct}%" if (running or pct) else T("click_to_start"),
                font=("Segoe UI", 8),
                fill=THEME["muted"]
            )
    
    def _draw_day_strip(self):
        """Draw day timeline strip showing hours from day_start_hour to day_end_hour."""
        canvas = self._card_widgets.get("day_strip")
        label = self._card_widgets.get("day_strip_label")
        if not canvas or not label or not scoring:
            return
        
        try:
            canvas.delete("all")
            
            day_start = self.cfg["ui"].get("day_start_hour", 8)
            day_end = self.cfg["ui"].get("day_end_hour", 20)
            hours_span = day_end - day_start
            
            if hours_span <= 0:
                hours_span = 12
                day_start = 8
                day_end = 20
            
            canvas_width = canvas.winfo_width()
            canvas_height = canvas.winfo_height()
            
            if canvas_width < 10:
                canvas_width = 236
            
            cell_width = canvas_width / hours_span
            
            today = datetime.now().strftime("%Y-%m-%d")
            matrix = scoring.hour_matrix(self.con, 1)
            day_hours = matrix.get(today, {})
            
            for i, hour in enumerate(range(day_start, day_end)):
                x = i * cell_width
                w = x + cell_width - 1
                secs = day_hours.get(hour, 0)
                
                if secs == 0:
                    color = THEME["border_light"]
                else:
                    cur = self.con.cursor()
                    cur.execute("""
                        SELECT app FROM hour_usage
                        WHERE day = ? AND hour = ?
                        ORDER BY seconds DESC LIMIT 1
                    """, (today, hour))
                    row = cur.fetchone()
                    if row:
                        app = row[0]
                        cat = scoring.categorize(app, "", self.cfg)
                        if cat == "productive":
                            color = THEME["productive"]
                        elif cat == "distracting":
                            color = THEME["distracting"]
                        else:
                            color = THEME["neutral"]
                    else:
                        color = THEME["border_light"]
                
                canvas.create_rectangle(x, 1, w - 1, canvas_height - 1, fill=color, outline="", width=0)
            
            now = datetime.now()
            current_hour = now.hour
            if day_start <= current_hour < day_end:
                minute_fraction = now.minute / 60.0
                hour_index = current_hour - day_start
                now_x = (hour_index + minute_fraction) * cell_width
                canvas.create_line(now_x, 0, now_x, canvas_height, fill=THEME["accent_red"], width=2)
            
            try:
                cur = self.con.cursor()
                cur.execute("""
                    SELECT SUM(h.seconds) FROM hour_usage h
                    JOIN app_usage a ON h.day = a.day AND h.app = a.app
                    WHERE h.day = ? AND h.hour BETWEEN ? AND ?
                """, (today, day_start, day_end - 1))
                row = cur.fetchone()
                productive_secs = row[0] if row and row[0] else 0
            except Exception:
                productive_secs = 0
            
            hours = productive_secs // 3600
            mins = (productive_secs % 3600) // 60
            label.config(text=T("today_total", h=hours, m=mins))
            
        except Exception:
            pass
    
    def _update_session_panel(self, state: str, running: bool, elapsed: int):
        """Update live session panel with elapsed time and top app."""
        session_label = self._card_widgets.get("session_info")
        if not session_label:
            return
        
        # Show session info only during work pomodoro
        if state != PomodoroState.WORK or not running:
            if state in (PomodoroState.SHORT_BREAK, PomodoroState.LONG_BREAK) and running:
                text = T("break_time_hint")
            elif not running and elapsed > 0:
                text = T("paused_hint")
            else:
                text = T("ready")
            session_label.config(text=text, fg=THEME["muted"])
            self._pomo_start_totals = None
            return
        
        # Snapshot totals when pomodoro starts
        if self._pomo_start_totals is None and self.tracker:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                self._pomo_start_totals = self.tracker.today_totals().get(today, {}).copy()
            except Exception:
                self._pomo_start_totals = {}
        
        # Calculate delta since session start
        if self._pomo_start_totals is not None and self.tracker:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                current_totals = self.tracker.today_totals().get(today, {})
                
                # Find app with most seconds gained since start
                deltas = {}
                for app, secs in current_totals.items():
                    start_secs = self._pomo_start_totals.get(app, 0)
                    delta = secs - start_secs
                    if delta > 0:
                        deltas[app] = delta
                
                if deltas:
                    top_app = max(deltas, key=deltas.get)
                    top_elapsed = deltas[top_app]
                    
                    # Format elapsed time
                    mins = top_elapsed // 60
                    if top_app.endswith(".exe"):
                        top_app = top_app[:-4]
                    
                    session_label.config(text=T("this_pomo_app", m=mins, app=top_app), fg=THEME["ink_light"])
                else:
                    mins = elapsed // 60
                    session_label.config(text=T("this_pomo", m=mins), fg=THEME["ink_light"])
            except Exception:
                mins = elapsed // 60
                session_label.config(text=T("this_pomo", m=mins), fg=THEME["ink_light"])
        else:
            mins = elapsed // 60
            session_label.config(text=T("this_pomo", m=mins), fg=THEME["ink_light"])
    
    def _get_pomodoro_total(self, state: str) -> int:
        """Get total seconds for current pomodoro state."""
        if state == PomodoroState.WORK:
            return self.cfg["pomodoro"]["work_minutes"] * 60
        elif state == PomodoroState.SHORT_BREAK:
            return self.cfg["pomodoro"]["short_break_minutes"] * 60
        elif state == PomodoroState.LONG_BREAK:
            return self.cfg["pomodoro"]["long_break_minutes"] * 60
        return 25 * 60
    
    def _toggle_pomodoro(self):
        """Toggle pomodoro start/pause."""
        if not self.pomodoro:
            return
        
        _, _, running = self.pomodoro.get_state()
        if running:
            self.pomodoro.pause()
        else:
            state_before, _, _ = self.pomodoro.get_state()
            self.pomodoro.start()
            if self.pet and state_before == PomodoroState.WORK:
                self.pet.react("focus_start")
            # Autoplay sound on focus start
            if self.sound_engine and self.cfg["audio"].get("autoplay_on_focus", True):
                sound = self._focus_sound or self.cfg["audio"].get("default", "rain")
                try:
                    if hasattr(self.sound_engine, "fade_in"):
                        self.sound_engine.fade_in(sound)
                    else:
                        self.sound_engine.play(sound)
                except Exception as e:
                    logger.warning(f"Focus sound failed: {e}")
    
    def _reset_pomodoro(self):
        """Reset pomodoro to initial state."""
        if self.pomodoro:
            self.pomodoro.reset()
    
    def _toggle_sound(self):
        """Toggle sound on/off."""
        if self.sound_engine:
            is_playing = self.sound_engine.toggle()
            self._card_widgets["sound_btn"].config(text=T("sound") if is_playing else T("muted"))
    
    def _open_sound_picker(self):
        """Open a menu to select the focus sound."""
        if not self.sound_engine or not self.card_frame:
            return
        
        menu = tk.Menu(self.card_frame, tearoff=0, bg=THEME["white"], fg=THEME["ink_black"])
        current = self._focus_sound
        
        for sound in self.sound_engine.sounds():
            label = f"\u25cf {sound}" if sound == current else sound
            menu.add_command(label=label, command=lambda s=sound: self._choose_focus_sound(s))
        
        try:
            menu.tk_popup(self.card_frame.winfo_rootx() + 10, self.card_frame.winfo_rooty() + 200)
        finally:
            menu.grab_release()
    
    def _choose_focus_sound(self, name: str):
        """Set the focus sound, preview it, and persist to config."""
        try:
            # Set the focus sound
            self._focus_sound = name
            
            # Update the picker button label
            if "sound_pick" in self._card_widgets:
                self._card_widgets["sound_pick"].config(text=self._focus_sound_label())
            
            # Preview the sound
            if self.sound_engine:
                self.sound_engine.play(name)
            
            # Persist to config.json (merge write to preserve other keys)
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    disk = json.load(f)
            disk.setdefault("audio", {})["default"] = name
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
            
            logger.info(f"Focus sound set to '{name}'")
        except Exception as e:
            logger.error(f"Failed to set focus sound: {e}")
    
    def _focus_sound_label(self) -> str:
        """Label text for the sound picker button: '♪ <name> <volume>'."""
        name = self._focus_sound or self.cfg["audio"].get("default", "rain")
        vol = self._sound_volume(name)
        if vol is None:
            return f"\u266a {name}"
        return f"\u266a {name} {vol}"
    
    def _sound_volume(self, name: str) -> Optional[int]:
        """Current volume (0-100) for a sound, or None if unavailable."""
        if self.sound_engine and hasattr(self.sound_engine, "volume_for"):
            try:
                vol = self.sound_engine.volume_for(name)
                if vol is not None:
                    return max(0, min(100, int(vol)))
            except Exception as e:
                logger.warning(f"volume_for failed for '{name}': {e}")
        try:
            vols = self.cfg["audio"].get("volumes") or {}
            if name in vols:
                return max(0, min(100, int(vols[name])))
        except Exception:
            pass
        return None
    
    def _persist_sound_volume(self, name: str, pct: int):
        """Persist sound volume to config.json via merge-write."""
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    disk = json.load(f)
            disk.setdefault("audio", {}).setdefault("volumes", {})[name] = pct
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist sound volume: {e}")
    
    def _adjust_focus_sound_volume(self, step: int):
        """Adjust current focus sound volume by step (clamped 0-100), live-apply and persist."""
        try:
            name = self._focus_sound
            if not name:
                return
            cur = self._sound_volume(name)
            if cur is None:
                cur = int(self.cfg["audio"].get("volume", 60))
            new = max(0, min(100, cur + int(step)))
            if new == cur:
                return
            # Live apply
            if self.sound_engine and hasattr(self.sound_engine, "set_sound_volume"):
                self.sound_engine.set_sound_volume(name, new)
            # In-memory cfg
            self.cfg["audio"].setdefault("volumes", {})[name] = new
            # Label
            btn = self._card_widgets.get("sound_pick")
            if btn:
                btn.config(text=self._focus_sound_label())
            # Persist (merge write)
            self._persist_sound_volume(name, new)
            logger.info(f"Sound volume: {name} {cur} -> {new}")
        except Exception as e:
            logger.warning(f"Failed to adjust sound volume: {e}")
    
    def _on_sound_wheel(self, event):
        """Handle mouse wheel over the sound picker to adjust volume."""
        try:
            # Route only if pointer is over the sound picker button
            btn = self._card_widgets.get("sound_pick")
            if not btn:
                return
            try:
                target = self.root.winfo_containing(event.x_root, event.y_root)
            except Exception:
                return
            if target is not btn:
                return
            # event.delta > 0 means scroll up (increase volume)
            step = 5 if event.delta > 0 else -5
            self._adjust_focus_sound_volume(step)
        except Exception as e:
            logger.warning(f"Sound volume wheel error: {e}")
    
    def _handle_break_audio(self):
        """Fade the soundscape out when a break begins (fallback: stop)."""
        try:
            if not self.sound_engine:
                return
            audio_cfg = self.cfg["audio"]
            if audio_cfg.get("fade_on_break", True) and hasattr(self.sound_engine, "fade_out"):
                self.sound_engine.fade_out()
                logger.info("Break started: soundscape fading out")
            elif audio_cfg.get("stop_on_break", False) and hasattr(self.sound_engine, "stop"):
                self.sound_engine.stop()
        except Exception as e:
            logger.warning(f"Break audio handling failed: {e}")
    
    def _show_reminder(self, payload: dict):
        """Show a health reminder popup with chime and pulse animation."""
        if not payload or not self.root:
            return
        if i18n:
            payload = i18n.translate_reminder(payload)
        
        # Route through the pet's cute chat bubble when it's around
        if self.pet and self.pet.is_visible() and self.pet.s.get("bubble_reminders", True):
            try:
                snooze = None
                if payload.get("kind", "break") == "break":
                    def snooze():
                        if self._snooze_job:
                            self.root.after_cancel(self._snooze_job)
                        self._snooze_job = self.root.after(5 * 60 * 1000, lambda: self._show_reminder(payload))
                if self.pet.show_reminder(payload, on_snooze=snooze):
                    if self.reminders and self.reminders.s.get("chime", True) and self.sound_engine:
                        try:
                            self.sound_engine.chime()
                        except Exception as e:
                            logger.warning(f"Chime failed: {e}")
                    return
            except Exception as e:
                logger.warning(f"Pet reminder bubble failed, using popup: {e}")
        
        try:
            kind = payload.get("kind", "break")
            title = payload.get("title", "Reminder")
            tip = payload.get("tip", "")
            
            # Reuse existing popup or create new
            if self._reminder_popup is None:
                popup = tk.Toplevel(self.root)
                popup.title(title)
                popup.attributes("-topmost", True)
                popup.overrideredirect(True)
                popup.configure(bg=THEME["paper_bg"])
                popup.geometry("380x190")
                
                # Center on screen
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                x = (screen_width - 380) // 2
                y = (screen_height - 190) // 2
                popup.geometry(f"+{x}+{y}")
                
                # Border frame (card look)
                border_frame = tk.Frame(popup, bg=THEME["border"], height=2)
                border_frame.pack(fill=tk.X, padx=8, pady=8)
                
                # Main content frame
                content = tk.Frame(popup, bg=THEME["paper_bg"])
                content.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
                
                # Title label
                title_label = tk.Label(
                    content,
                    text=title,
                    font=("Georgia", 15, "bold"),
                    fg=THEME["accent_red"],
                    bg=THEME["paper_bg"],
                    anchor="center"
                )
                title_label.pack(pady=(5, 5))
                
                # Tip label
                tip_label = tk.Label(
                    content,
                    text=tip,
                    font=("Segoe UI", 12),
                    fg=THEME["ink_black"],
                    bg=THEME["paper_bg"],
                    wraplength=340,
                    justify="center"
                )
                tip_label.pack(pady=(0, 10))
                
                # Countdown label (for break reminders)
                countdown_label = tk.Label(
                    content,
                    text="",
                    font=("Segoe UI", 10),
                    fg=THEME["muted"],
                    bg=THEME["paper_bg"]
                )
                countdown_label.pack(pady=(0, 5))
                
                # Buttons frame
                btn_frame = tk.Frame(content, bg=THEME["paper_bg"])
                btn_frame.pack(pady=(5, 0))
                
                def on_done():
                    if self._snooze_job:
                        self.root.after_cancel(self._snooze_job)
                        self._snooze_job = None
                    if self._reminder_popup:
                        self._reminder_popup.withdraw()
                
                # Done button (always)
                done_btn = tk.Button(
                    btn_frame,
                    text=T("done"),
                    font=("Segoe UI", 9, "bold"),
                    bg=THEME["accent_red"],
                    fg=THEME["white"],
                    relief=tk.FLAT,
                    cursor="hand2",
                    command=on_done
                )
                done_btn.pack(side=tk.LEFT, padx=5)
                
                # Snooze button (break only)
                snooze_btn = None
                if kind == "break":
                    def on_snooze():
                        if self._snooze_job:
                            self.root.after_cancel(self._snooze_job)
                        # Re-show after 5 minutes
                        self._snooze_job = self.root.after(5 * 60 * 1000, lambda: self._show_reminder(payload))
                        on_done()
                    
                    snooze_btn = tk.Button(
                        btn_frame,
                        text=T("snooze5"),
                        font=("Segoe UI", 9),
                        bg=THEME["border_light"],
                        fg=THEME["ink_black"],
                        relief=tk.FLAT,
                        cursor="hand2",
                        command=on_snooze
                    )
                    snooze_btn.pack(side=tk.LEFT, padx=5)
                
                # Store references for updates
                self._reminder_popup = popup
                self._reminder_data = {"title_label": title_label, "tip_label": tip_label, 
                                       "countdown_label": countdown_label, "done_btn": done_btn,
                                       "snooze_btn": snooze_btn, "kind": kind, "popup": popup}
            else:
                # Update existing popup
                popup = self._reminder_popup
                if hasattr(self, "_reminder_data"):
                    data = self._reminder_data
                    data["title_label"].config(text=title)
                    data["tip_label"].config(text=tip)
                    data["kind"] = kind
                popup.deiconify()
                popup.lift()
                popup.focus_force()
            
            # Grab attention
            popup.lift()
            popup.focus_force()
            try:
                popup.bell()
            except Exception:
                pass
            
            # Pulse animation (alternate bg color ~5 times over ~2s)
            pulse_colors = [THEME["paper_bg"], THEME.get("pulse_bg", "#fff5f5")]
            def pulse(count=[0]):
                if count[0] < 10 and popup.winfo_exists():
                    idx = count[0] % 2
                    try:
                        popup.configure(bg=pulse_colors[idx])
                        data = getattr(self, "_reminder_data", {})
                        if data:
                            for key in ["title_label", "tip_label", "countdown_label"]:
                                if key in data:
                                    data[key].config(bg=pulse_colors[idx])
                            if "done_btn" in data:
                                data["done_btn"].master.config(bg=pulse_colors[idx])
                    except Exception:
                        pass
                    count[0] += 1
                    popup.after(200, pulse, count)
            
            pulse()
            
            # Chime
            if self.reminders and self.reminders.s.get("chime", True) and self.sound_engine:
                try:
                    self.sound_engine.chime()
                except Exception as e:
                    logger.warning(f"Chime failed: {e}")
            
            # Auto-dismiss for eye/hydration (20s)
            if kind in ("eye", "hydration"):
                popup.after(20000, lambda: popup.winfo_exists() and popup.withdraw())
            
            # Store countdown update job reference
            self._countdown_job = None
            
        except Exception as e:
            logger.error(f"Failed to show reminder: {e}")
    
    def _open_dashboard(self):
        """Open dashboard window."""
        if self.dashboard_window:
            self._update_dashboard()
            self.dashboard_window.deiconify()
            self.dashboard_window.lift()
    
    def _close_dashboard(self):
        """Close dashboard window and cleanup tooltips."""
        self._hide_tooltip()
        if self.dashboard_window:
            self.dashboard_window.withdraw()
    
    def _update_dashboard(self):
        """Refresh dashboard data."""
        if not scoring:
            return
        
        day = datetime.now().strftime("%Y-%m-%d")
        try:
            summary = scoring.compute_daily(self.con, day, self.cfg)
            
            # Goals
            deep_goal = self.cfg["goals"]["deep_work_hours"] * 3600
            deep_actual = summary.get("productive_seconds", 0)
            deep_pct = min(1.0, deep_actual / deep_goal) if deep_goal > 0 else 0
            
            canvas = self._dashboard_widgets.get("deep_progress")
            if canvas:
                canvas.delete("all")
                width = canvas.winfo_width()
                fill_width = int(width * deep_pct)
                canvas.create_rectangle(0, 0, fill_width, 16, fill=THEME["accent_red"], outline="")
            
            # Pomodoros
            pomo_count = summary.get("pomodoros_done", 0)
            pomo_goal = self.cfg["goals"]["pomodoros"]
            self._dashboard_widgets["pomodoros_label"].config(text=f"{pomo_count} / {pomo_goal}")
            
            # Streak & XP
            streak_data = scoring.current_streak(self.con, self.cfg)
            self._dashboard_widgets["streak"].config(text=T("streak", n=streak_data.get('streak_days', 0)))
            self._dashboard_widgets["xp"].config(text=T("xp", n=streak_data.get('total_xp', 0)))
            
            # Draw all charts
            self._draw_donut_chart(day)
            self._draw_top_apps_chart(day)
            self._draw_trend_chart()
            self._draw_heatmap_chart()
            self._draw_focus_timeline()
            self._draw_pomodoros_chart()
            self._draw_tokens_chart()
            self._draw_maas_chart()
            
            # Coach
            if self.cfg["coach"]["enabled"]:
                coach_txt = coach.daily_coach(self.con, day, self.cfg) if coach else "Coach unavailable"
                coach_widget = self._dashboard_widgets["coach"]
                coach_widget.delete("1.0", tk.END)
                coach_widget.insert("1.0", coach_txt)
        except Exception as e:
            logger.error(f"Dashboard update error: {e}")
    
    def _draw_donut_chart(self, day: str):
        """Draw donut chart showing today's time split with delta vs yesterday."""
        canvas = self._dashboard_widgets.get("donut")
        if not canvas or not scoring:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            summary = scoring.compute_daily(self.con, day, self.cfg)
            p = summary.get("productive_seconds", 0)
            n = summary.get("neutral_seconds", 0)
            d = summary.get("distracting_seconds", 0)
            total = p + n + d
            
            if total == 0:
                canvas.create_text(100, 100, text=T("no_data"), font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            # Compute delta: today's productive time vs yesterday's
            try:
                from datetime import timedelta
                yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
                yesterday_summary = scoring.compute_daily(self.con, yesterday, self.cfg)
                yesterday_p = yesterday_summary.get("productive_seconds", 0)
                delta_text = self._delta_str(p, yesterday_p, higher_is_better=True)
                # Determine color
                if p > yesterday_p:
                    delta_color = THEME["productive"]
                elif p < yesterday_p:
                    delta_color = THEME["distracting"]
                else:
                    delta_color = THEME["muted"]
            except Exception:
                delta_text = ""
                delta_color = THEME["muted"]
            
            # Donut parameters
            center = 100
            outer_r = 70
            inner_r = 45
            
            # Draw segments
            start_angle = 90
            categories = [
                ("productive", p, THEME["productive"]),
                ("neutral", n, THEME["neutral"]),
                ("distracting", d, THEME["distracting"]),
            ]
            
            for cat, secs, color in categories:
                if secs <= 0:
                    continue
                extent = -359.99 * (secs / total)
                bbox = [center - outer_r, center - outer_r, center + outer_r, center + outer_r]
                canvas.create_arc(bbox, start=start_angle, extent=extent, style=tk.ARC, outline=color, width=outer_r - inner_r)
                start_angle += extent
            
            # Center: total time
            total_mins = total // 60
            hours = total_mins // 60
            mins = total_mins % 60
            if hours > 0:
                center_text = f"{hours}h{mins}m"
            else:
                center_text = f"{mins}m"
            canvas.create_text(center, center - 5, text=center_text, font=("Georgia", 13, "bold"), fill=THEME["ink_black"])
            canvas.create_text(center, center + 10, text=T("total"), font=("Segoe UI", 8), fill=THEME["muted"])
            
            # Delta indicator (top-right inside chart area)
            if delta_text:
                canvas.create_text(194, 6, text=delta_text, font=("Segoe UI", 8), fill=delta_color, anchor="ne")
            
            # Legend (bottom) - with proper left padding
            legend_y = 185
            legend_x = 10
            for i, (cat, secs, color) in enumerate(categories):
                if secs <= 0:
                    continue
                # Swatch
                canvas.create_rectangle(legend_x, legend_y + i * 16, legend_x + 10, legend_y + 10 + i * 16, fill=color, outline="")
                # Label
                mins = secs // 60
                pct = int(100 * secs / total) if total > 0 else 0
                label = f"{cat[:4]}: {mins}m ({pct}%)"
                canvas.create_text(legend_x + 15, legend_y + 5 + i * 16, text=label, font=("Segoe UI", 7), fill=THEME["ink_light"], anchor="nw")
                
                # Store hit box for tooltip
                canvas.addtag_withtag(f"legend_{cat}", canvas.create_rectangle(legend_x, legend_y + i * 16, legend_x + 140, legend_y + 12 + i * 16, outline=""))
        except Exception as e:
            logger.warning(f"Donut chart error: {e}")
    
    def _draw_top_apps_chart(self, day: str):
        """Draw horizontal bar chart of top apps today."""
        canvas = self._dashboard_widgets.get("top_apps")
        if not canvas or not scoring:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            breakdown_list = scoring.breakdown(self.con, day)
            if not breakdown_list:
                canvas.create_text(160, 100, text=T("no_data"), font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            # Top 7 apps
            top_apps = breakdown_list[:7]
            max_secs = max(item["seconds"] for item in top_apps) if top_apps else 1
            
            # Chart parameters - reserve space for labels on right
            bar_height = 18
            bar_spacing = 6
            start_y = 15
            max_bar_width = 140  # Reduced to leave room for labels
            label_x = max_bar_width + 8
            duration_x = label_x + 110  # Position for duration (right-aligned area)
            
            for i, item in enumerate(top_apps):
                app = item["app"]
                secs = item["seconds"]
                cat = item["category"]
                
                # Color by category
                if cat == "productive":
                    color = THEME["productive"]
                elif cat == "distracting":
                    color = THEME["distracting"]
                else:
                    color = THEME["neutral"]
                
                y = start_y + i * (bar_height + bar_spacing)
                bar_width = int(max_bar_width * (secs / max_secs)) if max_secs > 0 else 0
                
                # Bar
                canvas.create_rectangle(0, y, bar_width, y + bar_height, fill=color, outline="", tags=f"app_{app}")
                
                # App label (truncated)
                app_short = app.replace(".exe", "")[:12]
                canvas.create_text(label_x, y + bar_height // 2, text=app_short, font=("Segoe UI", 7), fill=THEME["ink_light"], anchor="w")
                
                # Duration - right-aligned in reserved space
                mins = secs // 60
                if mins >= 60:
                    duration = f"{mins // 60}h{mins % 60}m"
                else:
                    duration = f"{mins}m"
                canvas.create_text(duration_x, y + bar_height // 2, text=duration, font=("Segoe UI", 7, "bold"), fill=THEME["ink_black"], anchor="e")
        except Exception as e:
            logger.warning(f"Top apps chart error: {e}")
    
    def _draw_trend_chart(self):
        """Draw 7-day score trend chart with week-over-week delta."""
        canvas = self._dashboard_widgets.get("trend")
        if not canvas or not scoring:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            today = date.today()
            days_data = []
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                day_str = d.isoformat()
                summary = scoring.compute_daily(self.con, day_str, self.cfg)
                score = summary.get("score", 0)
                stars = summary.get("stars", 0)
                days_data.append((day_str, d.strftime("%a"), score, stars))
            
            if not days_data:
                return
            
            # Compute week-over-week delta: last 7 days avg vs prior 7 days avg
            try:
                current_7_avg = sum(d[2] for d in days_data) / 7.0
                prior_7_data = []
                for i in range(13, 7, -1):  # days 13..7 ago (prior 7 days)
                    d = today - timedelta(days=i)
                    day_str = d.isoformat()
                    summary = scoring.compute_daily(self.con, day_str, self.cfg)
                    score = summary.get("score", 0)
                    prior_7_data.append(score)
                prior_7_avg = sum(prior_7_data) / 7.0 if prior_7_data else 0
                delta_text = self._delta_str(current_7_avg, prior_7_avg, higher_is_better=True)
                # Determine color
                if current_7_avg > prior_7_avg + 0.01:
                    delta_color = THEME["productive"]
                elif current_7_avg < prior_7_avg - 0.01:
                    delta_color = THEME["distracting"]
                else:
                    delta_color = THEME["muted"]
            except Exception:
                delta_text = ""
                delta_color = THEME["muted"]
            
            # Chart parameters
            chart_left = 45
            chart_right = 800
            chart_top = 32
            chart_bottom = 120
            chart_height = chart_bottom - chart_top
            bar_width = (chart_right - chart_left) / 7 - 12
            
            # Draw axis
            canvas.create_line(chart_left, chart_top, chart_left, chart_bottom, fill=THEME["border"], width=1)
            canvas.create_line(chart_left, chart_bottom, chart_right, chart_bottom, fill=THEME["border"], width=1)
            
            # Draw title with delta (top-right inside chart area)
            canvas.create_text(chart_right - 4, 4, text=delta_text, font=("Segoe UI", 8), fill=delta_color, anchor="ne")
            
            # Y-axis labels
            for y_val, label in [(0, "0"), (50, "50"), (100, "100")]:
                y = chart_bottom - (y_val / 100) * chart_height
                canvas.create_text(chart_left - 8, y, text=label, font=("Segoe UI", 7), fill=THEME["muted"], anchor="e")
                canvas.create_line(chart_left, y, chart_right, y, fill=THEME["border_light"], dash=(2, 2))
            
            # Draw bars
            for i, (day_str, weekday, score, stars) in enumerate(days_data):
                x = chart_left + 10 + i * (bar_width + 10)
                bar_height = (score / 100) * chart_height if score > 0 else 2
                y = chart_bottom - bar_height
                
                color = THEME["accent_red"] if score > 0 else THEME["border_light"]
                canvas.create_rectangle(x, y, x + bar_width, chart_bottom, fill=color, outline="", tags=f"trend_{day_str}")
                
                # Stars above bar
                star_str = "★" * stars if stars > 0 else ""
                if star_str:
                    canvas.create_text(x + bar_width // 2, y - 8, text=star_str, font=("Segoe UI", 9), fill=THEME["neutral"], anchor="s")
                
                # Weekday label
                canvas.create_text(x + bar_width // 2, chart_bottom + 12, text=weekday, font=("Segoe UI", 8), fill=THEME["ink_light"], anchor="n")
                
                # Score value
                if score > 0:
                    canvas.create_text(x + bar_width // 2, y - 4, text=f"{int(score)}", font=("Segoe UI", 8, "bold"), fill=THEME["ink_black"], anchor="s")
        except Exception as e:
            logger.warning(f"Trend chart error: {e}")
    
    def _draw_heatmap_chart(self):
        """Draw true hour-of-day heatmap grid using hour_matrix."""
        canvas = self._dashboard_widgets.get("heatmap")
        if not canvas or not scoring:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            matrix = scoring.hour_matrix(self.con, 7)
            if not matrix:
                canvas.create_text(340, 100, text=T("no_hour_data"), font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            # Get last 7 days in order
            today = date.today()
            days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
            
            # Chart parameters
            cell_w = 26
            cell_h = 18
            start_x = 40
            start_y = 20
            
            # Collect all values for color scaling
            all_secs = []
            for day_str in days:
                if day_str in matrix:
                    all_secs.extend(matrix[day_str].values())
            
            max_secs = max(all_secs) if all_secs else 3600
            
            # Color scale function (5-step ramp)
            def color_for_secs(secs: int) -> str:
                if secs <= 0:
                    return THEME["white"]
                ratio = secs / max_secs
                if ratio < 0.2:
                    return THEME["border_light"]
                elif ratio < 0.4:
                    return "#d4c9c0"
                elif ratio < 0.6:
                    return "#b89988"
                elif ratio < 0.8:
                    return "#a35555"
                else:
                    return THEME["accent_red"]
            
            # Draw grid
            for row_i, day_str in enumerate(days):
                # Row label (weekday)
                day = date.fromisoformat(day_str)
                weekday = day.strftime("%a")
                canvas.create_text(start_x - 8, start_y + row_i * cell_h + cell_h // 2, text=weekday, font=("Segoe UI", 7), fill=THEME["ink_light"], anchor="e")
                
                # Cells for this day
                day_hours = matrix.get(day_str, {})
                for col in range(24):
                    secs = day_hours.get(col, 0)
                    color = color_for_secs(secs)
                    x = start_x + col * cell_w
                    y = start_y + row_i * cell_h
                    
                    canvas.create_rectangle(x, y, x + cell_w - 1, y + cell_h - 1, fill=color, outline=THEME["border_light"], tags=f"heat_{day_str}_{col}")
            
            # Hour ticks (00, 06, 12, 18)
            for hour in [0, 6, 12, 18]:
                x = start_x + hour * cell_w + cell_w // 2
                canvas.create_text(x, start_y + 7 * cell_h + 8, text=f"{hour:02d}", font=("Segoe UI", 7), fill=THEME["muted"], anchor="n")
            
            # Legend
            legend_x = start_x
            legend_y = start_y + 7 * cell_h + 20
            canvas.create_text(legend_x, legend_y, text=T("less"), font=("Segoe UI", 7), fill=THEME["muted"], anchor="e")
            
            legend_colors = [THEME["border_light"], "#d4c9c0", "#b89988", "#a35555", THEME["accent_red"]]
            for i, color in enumerate(legend_colors):
                lx = legend_x + 10 + i * 16
                canvas.create_rectangle(lx, legend_y - 5, lx + 12, legend_y + 5, fill=color, outline=THEME["border_light"])
            
            canvas.create_text(legend_x + 90, legend_y, text=T("more"), font=("Segoe UI", 7), fill=THEME["muted"], anchor="w")
        except Exception as e:
            logger.warning(f"Heatmap chart error: {e}")
    
    def _draw_focus_timeline(self):
        """Draw focus timeline strip showing today's hourly dominant category."""
        canvas = self._dashboard_widgets.get("focus_timeline")
        if not canvas or not scoring:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            matrix = scoring.hour_matrix(self.con, 1)
            today = date.today().isoformat()
            day_hours = matrix.get(today, {})
            
            # Chart parameters
            cell_w = 34  # 820 / 24 ≈ 34px per hour
            cell_h = 32
            start_x = 5
            start_y = 8
            
            # Draw 24 hour segments
            for hour in range(24):
                secs = day_hours.get(hour, 0)
                
                # Determine dominant category for this hour
                if secs == 0:
                    color = THEME["border_light"]  # No data
                else:
                    # Categorize based on the hour's activity
                    # We need to query the actual apps for this hour
                    cur = self.con.cursor()
                    cur.execute("""
                        SELECT app, seconds FROM hour_usage
                        WHERE day = ? AND hour = ?
                        ORDER BY seconds DESC LIMIT 1
                    """, (today, hour))
                    row = cur.fetchone()
                    if row:
                        app = row[0]
                        cat = scoring.categorize(app, "", self.cfg)
                        if cat == "productive":
                            color = THEME["productive"]
                        elif cat == "distracting":
                            color = THEME["distracting"]
                        else:
                            color = THEME["neutral"]
                    else:
                        color = THEME["border_light"]
                
                x = start_x + hour * cell_w
                y = start_y
                
                # Draw segment
                canvas.create_rectangle(x, y, x + cell_w - 2, y + cell_h, fill=color, outline=THEME["border_light"], tags=f"timeline_{hour}")
            
            # Draw "now" marker
            now_hour = datetime.now().hour
            now_x = start_x + now_hour * cell_w + cell_w // 2
            canvas.create_line(now_x, start_y, now_x, start_y + cell_h, fill=THEME["accent_red"], width=2, tags="now_marker")
            
            # Draw hour ticks (00, 06, 12, 18)
            for hour in [0, 6, 12, 18]:
                x = start_x + hour * cell_w + cell_w // 2
                canvas.create_text(x, start_y + cell_h + 10, text=f"{hour:02d}", font=("Segoe UI", 7), fill=THEME["muted"], anchor="n")
            
            # Legend
            legend_y = start_y + cell_h + 22
            legend_items = [
                (THEME["productive"], "Productive"),
                (THEME["neutral"], "Neutral"),
                (THEME["distracting"], "Distracting"),
                (THEME["border_light"], "No data"),
            ]
            legend_x = 5
            for color, label in legend_items:
                canvas.create_rectangle(legend_x, legend_y, legend_x + 10, legend_y + 8, fill=color, outline=THEME["border_light"])
                canvas.create_text(legend_x + 14, legend_y + 4, text=label, font=("Segoe UI", 7), fill=THEME["ink_light"], anchor="w")
                legend_x += 75
            
        except Exception as e:
            logger.warning(f"Focus timeline error: {e}")
    
    def _draw_pomodoros_chart(self):
        """Draw bar chart of pomodoros completed per day for last 7 days with week-over-week delta."""
        canvas = self._dashboard_widgets.get("pomodoros_chart")
        if not canvas:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            today = date.today()
            days_data = []
            
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                day_str = d.isoformat()
                
                # Query pomodoros_done from daily_summary
                cur = self.con.cursor()
                cur.execute("SELECT pomodoros_done FROM daily_summary WHERE day = ?", (day_str,))
                row = cur.fetchone()
                pomo_count = row[0] if row else 0
                
                # Fallback: count completed work sessions
                if pomo_count == 0:
                    cur.execute("""
                        SELECT COUNT(*) FROM sessions
                        WHERE day = ? AND kind = 'work' AND completed = 1
                    """, (day_str,))
                    row = cur.fetchone()
                    pomo_count = row[0] if row else 0
                
                days_data.append((day_str, d.strftime("%a"), pomo_count))
            
            if not days_data:
                canvas.create_text(160, 90, text=T("no_data"), font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            # Compute week-over-week delta: last 7 days total vs prior 7 days total
            try:
                current_7_total = sum(d[2] for d in days_data)
                prior_7_total = 0
                for i in range(13, 7, -1):  # days 13..7 ago (prior 7 days)
                    d = today - timedelta(days=i)
                    day_str = d.isoformat()
                    cur = self.con.cursor()
                    cur.execute("SELECT pomodoros_done FROM daily_summary WHERE day = ?", (day_str,))
                    row = cur.fetchone()
                    prior_7_total += row[0] if row else 0
                delta_text = self._delta_str(current_7_total, prior_7_total, higher_is_better=True)
                # Determine color
                if current_7_total > prior_7_total:
                    delta_color = THEME["productive"]
                elif current_7_total < prior_7_total:
                    delta_color = THEME["distracting"]
                else:
                    delta_color = THEME["muted"]
            except Exception:
                delta_text = ""
                delta_color = THEME["muted"]
            
            # Chart parameters
            chart_left = 35
            chart_right = 305
            chart_top = 38
            chart_bottom = 140
            chart_height = chart_bottom - chart_top
            bar_width = (chart_right - chart_left) / 7 - 8
            
            # Find max for scaling
            max_pomos = max(d[2] for d in days_data) if days_data else 1
            if max_pomos == 0:
                max_pomos = 1
            
            # Draw axis
            canvas.create_line(chart_left, chart_top, chart_left, chart_bottom, fill=THEME["border"], width=1)
            canvas.create_line(chart_left, chart_bottom, chart_right, chart_bottom, fill=THEME["border"], width=1)
            
            # Draw title with delta (top-right inside chart area)
            canvas.create_text(chart_right - 4, 3, text=delta_text, font=("Segoe UI", 8), fill=delta_color, anchor="ne")
            
            # Y-axis labels
            for y_val, label in [(0, "0"), (max_pomos // 2, str(max_pomos // 2)), (max_pomos, str(max_pomos))]:
                y = chart_bottom - (y_val / max_pomos) * chart_height if max_pomos > 0 else chart_bottom
                canvas.create_text(chart_left - 8, y, text=label, font=("Segoe UI", 7), fill=THEME["muted"], anchor="e")
                canvas.create_line(chart_left, y, chart_right, y, fill=THEME["border_light"], dash=(2, 2))
            
            # Draw bars
            for i, (day_str, weekday, count) in enumerate(days_data):
                x = chart_left + 6 + i * (bar_width + 8)
                bar_height = (count / max_pomos) * chart_height if count > 0 else 2
                y = chart_bottom - bar_height
                
                canvas.create_rectangle(x, y, x + bar_width, chart_bottom, fill=THEME["accent_red"], outline="", tags=f"pomo_{day_str}")
                
                # Count label above bar
                if count > 0:
                    canvas.create_text(x + bar_width // 2, y - 4, text=str(count), font=("Segoe UI", 8, "bold"), fill=THEME["ink_black"], anchor="s")
                
                # Weekday label below
                canvas.create_text(x + bar_width // 2, chart_bottom + 10, text=weekday, font=("Segoe UI", 7), fill=THEME["ink_light"], anchor="n")
        
        except Exception as e:
            logger.warning(f"Pomodoros chart error: {e}")
    
    def _draw_tokens_chart(self):
        """Draw bar chart of AI tokens used per day for last 7 days."""
        canvas = self._dashboard_widgets.get("tokens_chart")
        if not canvas:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            if not tokens:
                canvas.create_text(410, 90, text=T("no_token_data"), font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            totals = tokens.daily_totals(self.con, 7)
            
            today = date.today()
            days_data = []
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                day_str = d.isoformat()
                entry = (totals or {}).get(day_str, {})
                day_total = entry.get("total", 0)
                days_data.append((day_str, d.strftime("%a"), day_total))
            
            max_total = max((d[2] for d in days_data), default=0)
            if max_total <= 0:
                canvas.create_text(410, 90, text=T("no_token_data"), font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            chart_left = 60
            chart_right = 800
            chart_top = 38
            chart_bottom = 140
            chart_height = chart_bottom - chart_top
            bar_width = (chart_right - chart_left) / 7 - 12
            
            canvas.create_line(chart_left, chart_top, chart_left, chart_bottom, fill=THEME["border"], width=1)
            canvas.create_line(chart_left, chart_bottom, chart_right, chart_bottom, fill=THEME["border"], width=1)
            
            for i, (day_str, weekday, day_total) in enumerate(days_data):
                x = chart_left + 10 + i * (bar_width + 10)
                bar_height = (day_total / max_total) * chart_height if day_total > 0 else 2
                y = chart_bottom - bar_height
                
                canvas.create_rectangle(x, y, x + bar_width, chart_bottom, fill=THEME["accent_red"], outline="", tags=f"tok_{day_str}")
                
                if day_total > 0:
                    canvas.create_text(x + bar_width // 2, y - 4, text=tokens.format_tokens(day_total), font=("Segoe UI", 8, "bold"), fill=THEME["ink_black"], anchor="s")
                
                canvas.create_text(x + bar_width // 2, chart_bottom + 10, text=weekday, font=("Segoe UI", 8), fill=THEME["ink_light"], anchor="n")
        except Exception as e:
            logger.warning(f"Tokens chart error: {e}")
    
    def _draw_maas_chart(self):
        """Draw MAAS account usage: today's total, 7-day bars, per-model split.

        Uses only data already fetched by the slow periodic updater -- it never
        performs a network request itself.
        """
        canvas = self._dashboard_widgets.get("maas_chart")
        if not canvas:
            return
        
        self._hide_tooltip()
        canvas.delete("all")
        
        try:
            if not maas_tokens:
                canvas.create_text(410, 90, text="MAAS module unavailable", font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            today = self._maas_today or {}
            daily = self._maas_daily or {}
            
            canvas.create_text(14, 12, text="Today (account)", font=("Georgia", 11, "bold"), fill=THEME["ink_black"], anchor="nw")
            if today.get("ok"):
                canvas.create_text(14, 36, text=maas_tokens.format_tokens(today.get("total", 0)), font=("Courier New", 24, "bold"), fill=THEME["accent_red"], anchor="nw")
                canvas.create_text(14, 80, text="in %s \u00b7 out %s" % (
                    maas_tokens.format_tokens(today.get("prompt", 0)),
                    maas_tokens.format_tokens(today.get("completion", 0))
                ), font=("Segoe UI", 8), fill=THEME["muted"], anchor="nw")
                
                models = today.get("models") or []
                if models:
                    canvas.create_text(14, 100, text="By model", font=("Segoe UI", 8, "bold"), fill=THEME["ink_light"], anchor="nw")
                    for i, model in enumerate(models[:4]):
                        name = str(model.get("model", "unknown")).split("/")[-1]
                        if len(name) > 26:
                            name = name[:25] + "\u2026"
                        canvas.create_text(14, 116 + i * 14, text="%s  %s" % (
                            name, maas_tokens.format_tokens(model.get("total", 0))
                        ), font=("Segoe UI", 7), fill=THEME["ink_light"], anchor="nw")
            else:
                canvas.create_text(14, 48, text="No MAAS data", font=("Segoe UI", 11), fill=THEME["muted"], anchor="nw")
            
            chart_left = 340
            chart_right = 806
            chart_top = 30
            chart_bottom = 148
            chart_height = chart_bottom - chart_top
            
            canvas.create_text(chart_left, 12, text="Last 7 days (account)", font=("Georgia", 11, "bold"), fill=THEME["ink_black"], anchor="nw")
            
            now = date.today()
            days_data = []
            for i in range(6, -1, -1):
                d = now - timedelta(days=i)
                day_str = d.isoformat()
                days_data.append((day_str, d.strftime("%a"), int(daily.get(day_str, 0) or 0)))
            
            max_total = max((d[2] for d in days_data), default=0)
            if max_total <= 0:
                canvas.create_text((chart_left + chart_right) // 2, 90, text="No MAAS data", font=("Segoe UI", 11), fill=THEME["muted"])
                return
            
            bar_span = (chart_right - chart_left) / 7.0
            bar_width = bar_span - 12
            
            canvas.create_line(chart_left, chart_top, chart_left, chart_bottom, fill=THEME["border"], width=1)
            canvas.create_line(chart_left, chart_bottom, chart_right, chart_bottom, fill=THEME["border"], width=1)
            
            for i, (day_str, weekday, day_total) in enumerate(days_data):
                x = chart_left + 6 + i * bar_span
                bar_height = (day_total / max_total) * chart_height if day_total > 0 else 2
                y = chart_bottom - bar_height
                
                canvas.create_rectangle(x, y, x + bar_width, chart_bottom, fill=THEME["accent_red"], outline="", tags=f"maas_{day_str}")
                
                if day_total > 0:
                    canvas.create_text(x + bar_width // 2, y - 4, text=maas_tokens.format_tokens(day_total), font=("Segoe UI", 8, "bold"), fill=THEME["ink_black"], anchor="s")
                
                canvas.create_text(x + bar_width // 2, chart_bottom + 10, text=weekday, font=("Segoe UI", 8), fill=THEME["ink_light"], anchor="n")
        except Exception as e:
            logger.warning(f"MAAS chart error: {e}")
    
    def _delta_str(self, cur: float, prev: float, higher_is_better: bool) -> str:
        """
        Return a small delta text like '▲ 12m vs last wk' / '▼ 8m vs last wk' / '– no change'.
        Uses BMP chars: ▲ U+25B2, ▼ U+25BC.
        
        Direction convention: arrow = direction the metric moved.
        Colour (determined by caller): improved → productive; worsened → distracting; unchanged → muted.
        
        Per factor:
        - deep work / score / pomodoros / focus → higher_is_better=True
        - distraction / idle / app switches → higher_is_better=False
        - neutral → treat as higher_is_better=False (lower is better or neutral)
        """
        if prev == 0 and cur == 0:
            return "– no change"
        
        diff = cur - prev
        if abs(diff) < 0.01:  # Effectively no change
            return "– no change"
        
        # Determine direction
        if diff > 0:
            direction_char = "\u25b2"  # ▲
        else:
            direction_char = "\u25bc"  # ▼
        
        # Format the numeric difference
        if abs(diff) >= 3600:
            diff_str = f"{abs(diff) / 3600:.1f}h"
        elif abs(diff) >= 60:
            diff_str = f"{abs(diff) / 60:.0f}m"
        elif abs(diff) >= 1:
            diff_str = f"{abs(diff):.0f}s"
        else:
            diff_str = f"{abs(diff):.1f}"
        
        return f"{direction_char} {diff_str} vs last wk"
    
    def _on_chart_motion(self, event, chart_name: str):
        """Handle mouse motion over charts for tooltips."""
        canvas = self._dashboard_widgets.get(chart_name)
        if not canvas:
            return
        
        # Find item under cursor
        item = canvas.find_withtag(tk.CURRENT)
        if not item:
            self._hide_tooltip()
            return
        
        # Get tags for this item
        tags = canvas.gettags(item[0]) if item else ()
        
        tooltip_text = None
        for tag in tags:
            if tag.startswith("legend_"):
                cat = tag.replace("legend_", "")
                tooltip_text = f"{cat.capitalize()}"
            elif tag.startswith("app_"):
                app = tag.replace("app_", "")
                tooltip_text = app
            elif tag.startswith("trend_"):
                day_str = tag.replace("trend_", "")
                tooltip_text = day_str
            elif tag.startswith("heat_"):
                parts = tag.replace("heat_", "").split("_")
                if len(parts) == 2:
                    day_str, hour = parts
                    tooltip_text = f"{day_str} {hour}:00"
        
        if tooltip_text:
            self._show_tooltip(event.x_root, event.y_root, tooltip_text)
        else:
            self._hide_tooltip()
    
    def _on_chart_leave(self, event):
        """Hide tooltip when leaving chart."""
        self._hide_tooltip()
    
    def _on_chart_click(self, event, chart_name: str):
        """Handle click on chart elements."""
        canvas = self._dashboard_widgets.get(chart_name)
        if not canvas:
            return
        
        item = canvas.find_withtag(tk.CURRENT)
        if not item:
            return
        
        tags = canvas.gettags(item[0]) if item else ()
        detail = self._dashboard_widgets.get("detail")
        
        for tag in tags:
            if tag.startswith("trend_"):
                day_str = tag.replace("trend_", "")
                if detail:
                    detail.config(text=T("clicked_open", day=day_str), fg=THEME["accent_red"])
                # Could open history here
            elif tag.startswith("heat_"):
                parts = tag.replace("heat_", "").split("_")
                if len(parts) == 2:
                    day_str, hour = parts
                    if detail:
                        detail.config(text=T("clicked_at", day=day_str, hour=hour), fg=THEME["accent_red"])
    
    def _show_tooltip(self, x: int, y: int, text: str):
        """Show tooltip at screen coordinates."""
        if self._tooltip is not None:
            self._tooltip.destroy()
        
        self._tooltip = tk.Toplevel(self.dashboard_window)
        self._tooltip.overrideredirect(True)
        self._tooltip.attributes("-topmost", True)
        
        label = tk.Label(
            self._tooltip,
            text=text,
            font=("Segoe UI", 8),
            fg=THEME["ink_black"],
            bg=THEME["white"],
            relief=tk.SOLID,
            borderwidth=1,
            padx=4,
            pady=2
        )
        label.pack()
        
        self._tooltip.geometry(f"+{x + 10}+{y + 10}")
    
    def _hide_tooltip(self):
        """Hide tooltip."""
        if self._tooltip is not None:
            self._tooltip.destroy()
            self._tooltip = None
    
    def _open_history(self):
        """Open history window."""
        if self.history_window:
            self._history_date = None  # Today
            self._update_history()
            self.history_window.deiconify()
            self.history_window.lift()
    
    def _history_prev(self):
        """Navigate to previous day."""
        if self._history_date is None:
            self._history_date = datetime.now() - timedelta(days=1)
        else:
            self._history_date = self._history_date - timedelta(days=1)
        self._update_history()
    
    def _history_next(self):
        """Navigate to next day (max today)."""
        today = datetime.now().date()
        if self._history_date is None:
            return  # Already at today
        elif self._history_date.date() >= today:
            self._history_date = None
        else:
            self._history_date = self._history_date + timedelta(days=1)
            if self._history_date.date() >= today:
                self._history_date = None
        self._update_history()
    
    def _history_today(self):
        """Jump to today."""
        self._history_date = None
        self._update_history()
    
    def _update_history(self):
        """Refresh history window data."""
        if not scoring:
            return
        
        day_str = self._history_date.strftime("%Y-%m-%d") if self._history_date else datetime.now().strftime("%Y-%m-%d")
        self._history_widgets["date_label"].config(text=day_str)
        
        try:
            summary = scoring.compute_daily(self.con, day_str, self.cfg)
            
            # Score & stars
            score = summary.get("score", 0)
            stars = summary.get("stars", 0)
            self._history_widgets["score"].config(text=T("score", s=f"{score:.0f}"))
            star_str = "★" * stars + "☆" * (5 - stars)
            self._history_widgets["stars"].config(text=star_str)
            
            # Pomodoros
            pomo_count = summary.get("pomodoros_done", 0)
            self._history_widgets["pomodoros"].config(text=T("pomos_n", n=pomo_count))
            
            # Breakdown
            breakdown_list = scoring.breakdown(self.con, day_str)
            breakdown_text = self._history_widgets["breakdown"]
            breakdown_text.delete("1.0", tk.END)
            
            if not breakdown_list:
                breakdown_text.insert("1.0", "No data for this day")
            else:
                for item in breakdown_list:
                    app = item.get("app", "unknown")
                    secs = item.get("seconds", 0)
                    cat = item.get("category", "neutral")
                    hours = secs // 3600
                    mins = (secs % 3600) // 60
                    icon = "✓" if cat == "productive" else "⚠" if cat == "distracting" else "•"
                    breakdown_text.insert(tk.END, f"{icon} {app}: {hours}h {mins}m\n")
        except Exception as e:
            logger.error(f"History update error: {e}")
    
    def _show_card_menu(self, event):
        """Show the right-click menu on the card (animated card menu, classic fallback)."""
        try:
            import fancy_menu
            self._show_fancy_card_menu(event, fancy_menu)
            return
        except Exception as e:
            logger.warning(f"Fancy card menu failed, using classic menu: {e}")
        self._show_native_card_menu(event)
    
    def _show_fancy_card_menu(self, event, fm):
        try:
            import theme as theme_mod
            accent = theme_mod.palette()["accent"]
        except Exception:
            accent = "#8b1a1a"
        menu = fm.FancyMenu(self.card_frame, accent=accent)
        menu.header("Pips", T("fm_card_sub", v=APP_VERSION), avatar="P")
        update = self._update_manifest
        if update:
            menu.banner(
                T("fm_upd_title", v=update.get("version", "")),
                T("fm_upd_sub"),
                self._show_pending_update,
                icon="\U0001F381",
                badge=T("fm_new"),
            )
        menu.tiles([
            ("\U0001F4CA", T("fm_stats"), self._open_dashboard),
            ("\U0001F3C3", T("mg_trail"), self._open_focus_trail),
            ("\U0001F570", T("fm_history"), self._open_history),
        ])
        
        # v2.27.2: weekly / monthly reports live with the diary instead of crowding the tiles.
        def build_reports(sub):
            sub.item("\U0001F4C5", T("m_weekly"), self._open_weekly_report)
            sub.item("\U0001F5D3", T("m_monthly"), self._open_monthly_report)
            sub.section(T("fm_diary"))
            sub.item("\u270D", T("m_gen_diary"), self._generate_diary)
            sub.item("\U0001F4C2", T("m_open_diary"), self._open_diary_folder)
            sub.item("\U0001F680", T("m_publish"), self._publish_diary)
        menu.submenu("\U0001F4D1", T("fm_reports"), build_reports, width=250)
        menu.item("\U0001F504", T("m_refresh_maas"), self._refresh_maas_now)
        if self._token_board_ok():
            menu.item("\u26A1", self._token_board_label(), self._open_token_board,
                      hint=self._token_board_hint())
        
        if self.sound_engine:
            def build_sounds(sub):
                current = self._focus_sound
                for sound in self.sound_engine.sounds():
                    vol = self._sound_volume(sound)
                    entry = sub.choice("\U0001F3B5", sound, sound == current,
                                       lambda s=sound: self._choose_focus_sound(s))
                    if vol is not None:
                        entry["hint"] = T("fm_volume", v=vol)
            menu.submenu("\U0001F3A7", T("m_sound"), build_sounds, hint=str(self._focus_sound or ""))
        
        if self.pet:
            name = self.pet.s.get("name", "Pip")
            
            def build_pet(sub):
                sub.item("\U0001F441", T("m_hide_pet", name=name) if self.pet.visible else T("m_show_pet", name=name),
                         self._toggle_pet_visible)
                sub.item("\U0001F36A", T("m_snack"), self.pet.feed)
                sub.item("\u2699", T("m_pet_settings"), self.pet.open_settings)
            menu.submenu("\U0001F43E", T("m_pet", name=name), build_pet, width=250)
        
        menu.section(T("fm_app"))
        if i18n:
            def build_languages(sub):
                for code, lang_name, _hello in i18n.LANGUAGES:
                    sub.choice("\U0001F310", lang_name, code == i18n.get_language(),
                               lambda c=code: self._set_language(c))
            current_lang = next((n for c, n, _h in i18n.LANGUAGES if c == i18n.get_language()), "")
            menu.submenu("\U0001F310", T("m_language"), build_languages, hint=current_lang)
        
        def build_help(sub):
            if update:
                sub.item("\U0001F381", T("fm_upd_item", v=update.get("version", "")),
                         self._show_pending_update, badge=T("fm_new"), highlight=True)
            if self.pet:
                sub.item("\U0001F9ED", T("m_guide"), self.pet.start_tour)
            sub.item("\U0001F4D6", T("m_user_guide"), self._open_user_guide)
            sub.item("\U0001F504", T("m_check_updates"), lambda: self._check_updates(manual=True))
            sub.item("\u2139", T("m_about", v=APP_VERSION),
                     lambda: self._tell(T("about_text", v=APP_VERSION, owner=APP_OWNER), priority=1))
        menu.submenu("\u2753", T("m_help"), build_help, badge=T("fm_new") if update else None, width=250)
        if self.pet:
            menu.item("\U0001F43E", T("m_hide_to_pet"), self._hide_card)
        menu.item("\U0001F680", T("m_autostart"), self._toggle_autostart)
        menu.separator()
        menu.item("\u23FB", T("m_quit"), self._on_close, danger=True)
        menu.popup(event.x_root, event.y_root)
    
    def _show_native_card_menu(self, event):
        menu = tk.Menu(self.card_frame, tearoff=0, bg=THEME["white"], fg=THEME["ink_black"])
        menu.add_command(label=T("m_dashboard"), command=self._open_dashboard)
        menu.add_command(label=T("mg_trail"), command=self._open_focus_trail)
        if self._token_board_ok():
            menu.add_command(label=self._token_board_label(), command=self._open_token_board)
        menu.add_command(label=T("m_history"), command=self._open_history)
        reports_menu = tk.Menu(menu, tearoff=0, bg=THEME["white"], fg=THEME["ink_black"])
        reports_menu.add_command(label=T("m_weekly"), command=self._open_weekly_report)
        reports_menu.add_command(label=T("m_monthly"), command=self._open_monthly_report)
        reports_menu.add_separator()
        reports_menu.add_command(label=T("m_gen_diary"), command=self._generate_diary)
        reports_menu.add_command(label=T("m_open_diary"), command=self._open_diary_folder)
        reports_menu.add_command(label=T("m_publish"), command=self._publish_diary)
        menu.add_cascade(label=T("fm_reports"), menu=reports_menu)
        menu.add_command(label=T("m_refresh_maas"), command=self._refresh_maas_now)
        menu.add_separator()
        
        # Sound submenu
        sound_menu = tk.Menu(menu, tearoff=0, bg=THEME["white"], fg=THEME["ink_black"])
        if self.sound_engine:
            current = self._focus_sound
            for sound in self.sound_engine.sounds():
                label = f"\u25cf {sound}" if sound == current else sound
                vol = self._sound_volume(sound)
                if vol is not None:
                    label = f"{label} ({vol}%)"
                sound_menu.add_command(label=label, command=lambda s=sound: self._choose_focus_sound(s))
        menu.add_cascade(label=T("m_sound"), menu=sound_menu)
        
        # Pet submenu
        if self.pet:
            pet_menu = tk.Menu(menu, tearoff=0, bg=THEME["white"], fg=THEME["ink_black"])
            name = self.pet.s.get("name", "Pip")
            pet_menu.add_command(label=T("m_hide_pet", name=name) if self.pet.visible else T("m_show_pet", name=name),
                                 command=self._toggle_pet_visible)
            pet_menu.add_command(label=T("m_snack"), command=self.pet.feed)
            pet_menu.add_command(label=T("m_pet_settings"), command=self.pet.open_settings)
            menu.add_cascade(label=T("m_pet", name=name), menu=pet_menu)
        
        if i18n:
            lang_menu = tk.Menu(menu, tearoff=0, bg=THEME["white"], fg=THEME["ink_black"])
            for code, lang_name, _hello in i18n.LANGUAGES:
                mark = "\u25cf " if code == i18n.get_language() else "   "
                lang_menu.add_command(label=mark + lang_name, command=lambda c=code: self._set_language(c))
            menu.add_cascade(label=T("m_language"), menu=lang_menu)
        
        help_menu = tk.Menu(menu, tearoff=0, bg=THEME["white"], fg=THEME["ink_black"])
        if self.pet:
            help_menu.add_command(label=T("m_guide"), command=self.pet.start_tour)
        help_menu.add_command(label=T("m_user_guide"), command=self._open_user_guide)
        help_menu.add_command(label=T("m_check_updates"), command=lambda: self._check_updates(manual=True))
        help_menu.add_command(label=T("m_about", v=APP_VERSION),
                              command=lambda: self._tell(T("about_text", v=APP_VERSION, owner=APP_OWNER), priority=1))
        menu.add_cascade(label=T("m_help"), menu=help_menu)
        
        menu.add_separator()
        if self.pet:
            menu.add_command(label=T("m_hide_to_pet"), command=self._hide_card)
        menu.add_command(label=T("m_autostart"), command=self._toggle_autostart)
        menu.add_separator()
        menu.add_command(label=T("m_quit"), command=self._on_close)
        
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    
    def _select_sound(self, name: str):
        """Select and play a sound (delegates to _choose_focus_sound)."""
        self._choose_focus_sound(name)
    
    def _open_weekly_report(self):
        """Generate the 7-day HTML report and open it in the default browser."""
        if not coach:
            logger.warning("coach module not available; cannot build weekly report")
            return
        try:
            day = datetime.now().strftime("%Y-%m-%d")
            html = coach.weekly_report(self.con, self.cfg, day)
            out_dir = os.path.join(str(BASE_DIR), "output")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"weekly-report-{day}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"Weekly report written: {path}")
            try:
                os.startfile(path)
            except Exception as e:
                logger.warning(f"Could not open weekly report: {e}")
        except Exception as e:
            logger.error(f"Weekly report failed: {e}")
    
    def _open_monthly_report(self):
        """Generate the monthly HTML report and open it in the default browser."""
        if not coach or not hasattr(coach, "monthly_report"):
            logger.warning("coach.monthly_report not available; cannot build monthly report")
            return
        try:
            month = datetime.now().strftime("%Y-%m")
            html = coach.monthly_report(self.con, self.cfg, datetime.now().strftime("%Y-%m-%d"))
            out_dir = os.path.join(str(BASE_DIR), "output")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"monthly-report-{month}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info(f"Monthly report written: {path}")
            try:
                os.startfile(path)
            except Exception as e:
                logger.warning(f"Could not open monthly report: {e}")
        except Exception as e:
            logger.error(f"Monthly report failed: {e}")
    
    def _diary_repo_dir(self) -> str:
        try:
            d = (self.cfg.get("diary") or {}).get("repo_dir") or ""
            if d:
                return d
        except Exception:
            pass
        return str(Path(__file__).parent.parent / "productivity-diary")

    def _generate_diary(self):
        try:
            if not diary:
                logger.warning("diary module not available")
                return
            if tokens:
                tokens.refresh(self.con)
            repo_dir = self._diary_repo_dir()
            day = datetime.now().strftime("%Y-%m-%d")
            p1 = diary.write_entry(self.con, self.cfg, day, repo_dir)
            p2 = diary.write_index(self.con, self.cfg, repo_dir, 30)
            p3 = diary.write_json(self.con, self.cfg, repo_dir, 30)
            logger.info(f"Diary written: {p1} | {p2} | {p3}")
        except Exception as e:
            logger.error(f"Generate diary failed: {e}")

    def _open_diary_folder(self):
        try:
            repo_dir = self._diary_repo_dir()
            Path(repo_dir).mkdir(parents=True, exist_ok=True)
            os.startfile(repo_dir)  # noqa: only on Windows
        except Exception as e:
            logger.error(f"Open diary folder failed: {e}")

    def _publish_diary(self):
        try:
            self._generate_diary()
            repo_dir = self._diary_repo_dir()
            script = Path(repo_dir) / "publish.ps1"
            if not script.exists():
                logger.warning(f"publish.ps1 not found at {script}")
                return
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                cwd=repo_dir,
            )
            logger.info(f"Diary publish started: {script}")
        except Exception as e:
            logger.error(f"Publish diary failed: {e}")
    
    def _toggle_autostart(self):
        """Toggle autostart on Windows login."""
        if WScriptDispatch is None:
            logger.warning("win32com not available for autostart")
            return
        
        try:
            startup_folder = os.path.join(
                os.getenv("APPDATA"),
                r"Microsoft\Windows\Start Menu\Programs\Startup"
            )
            shortcut_path = os.path.join(startup_folder, "Pips.lnk")
            
            if os.path.exists(shortcut_path):
                os.remove(shortcut_path)
                logger.info("Autostart disabled")
            else:
                shell = WScriptDispatch("WScript.Shell")
                shortcut = shell.CreateShortCut(shortcut_path)
                pythonw = Path(sys.executable).with_name("pythonw.exe")
                shortcut.TargetPath = str(pythonw if pythonw.exists() else Path(sys.executable))
                shortcut.Arguments = subprocess.list2cmdline([str(Path(__file__).absolute())])
                shortcut.WorkingDirectory = str(BASE_DIR)
                shortcut.IconLocation = str(BASE_DIR / "assets" / "widget.ico")
                shortcut.save()
                logger.info("Autostart enabled")
        except Exception as e:
            logger.error(f"Autostart error: {e}")
    
    def _on_close(self):
        """Handle application close."""
        if getattr(self, "_tray", None):
            self._tray.stop()
            self._tray = None
        # Stop refresh
        if self._refresh_after_id:
            self.root.after_cancel(self._refresh_after_id)
        
        # Stop tracker
        if self.tracker:
            self.tracker.stop()
        
        # Stop audio
        if self.sound_engine:
            self.sound_engine.stop()
        
        # Save & remove the pet
        if self.pet:
            try:
                self.pet.destroy()
            except Exception as e:
                logger.warning(f"Pet shutdown failed: {e}")
        
        # Save position
        self._save_position()
        
        # Destroy
        self.root.destroy()
    
    def run(self):
        """Enter main event loop."""
        self.root.mainloop()


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main(argv=None) -> int:
    """Main entry point with CLI flags."""
    parser = argparse.ArgumentParser(description="Pips")
    parser.add_argument("--selftest", action="store_true", help="Self-test mode: init all, render offscreen, exit 0")
    parser.add_argument("--no-track", action="store_true", help="Disable tracker")
    parser.add_argument("--reset-pos", action="store_true", help="Reset window position")
    parser.add_argument("--choose-language", action="store_true", help="Show the language picker again")
    parser.add_argument("--version", action="version", version=f"Pips {APP_VERSION} - by {APP_OWNER}")
    
    args = parser.parse_args(argv)

    # One Pips at a time: a second launch just wakes the running pet
    instance = None
    if not args.selftest:
        instance = SingleInstance()
        if not instance.acquire():
            logger.info("Pips is already running - asked it to show itself")
            return 0
    
    # Load config
    cfg = load_config()
    if args.choose_language:
        cfg.pop("language", None)
    
    # Reset position if requested
    if args.reset_pos:
        cfg["ui"]["pos"] = None
        try:
            disk = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    disk = json.load(f)
            disk.setdefault("ui", {})["pos"] = None
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to reset position: {e}")
    
    # Import modules
    import_modules()

    # Colour theme (light / dark) - applied before any window is built
    try:
        import theme as theme_mod
        theme_mod.set_theme((cfg.get("ui") or {}).get("theme", "light"))
        theme_mod.apply_to_app(THEME, PILL_COLORS)
    except Exception as e:
        logger.warning(f"Theme not applied: {e}")
    
    # Language: use the saved choice, or ask once before Pips starts
    if i18n:
        language = cfg.get("language")
        if not language and not args.selftest:
            try:
                icon = BASE_DIR / "assets" / "widget.ico"
                language = i18n.pick_language(
                    default=i18n.detect_system_language(),
                    icon_path=str(icon) if icon.exists() else None,
                )
                cfg["language"] = language
                disk = {}
                if CONFIG_PATH.exists():
                    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                        disk = json.load(f)
                disk["language"] = language
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(disk, f, indent=2)
            except Exception as e:
                logger.warning(f"Language picker failed, using English: {e}")
                language = "en"
        i18n.set_language(language or "en")
        logger.info(f"UI language: {i18n.get_language()}")
    
    # Initialize database
    try:
        con = get_db_connection()
        ensure_db_schema(con)
        
        # Also call tracker.ensure_db if available
        if tracker and hasattr(tracker, "ensure_db"):
            tracker.ensure_db(con)
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        return 1
    
    # Self-test mode
    if args.selftest:
        logger.info("=== SELF-TEST MODE ===")
        
        # Initialize widget (offscreen)
        app = ProductivityWidget(cfg, con, no_track=args.no_track)  # init tracker unless --no-track
        
        try:
            app.build()
            
            # Simulate refresh
            app._update_card()
            app._update_dashboard()
            app._update_history()
            
            # Print totals
            day = datetime.now().strftime("%Y-%m-%d")
            if scoring:
                summary = scoring.compute_daily(con, day, cfg)
                logger.info(f"Today's summary: score={summary.get('score', 0):.1f}, "
                           f"stars={summary.get('stars', 0)}, "
                           f"productive={summary.get('productive_seconds', 0)}s, "
                           f"pomodoros={summary.get('pomodoros_done', 0)}")
            else:
                logger.info("Scoring module not available")
            
            # Close without showing
            app._on_close()
            
            logger.info("=== SELF-TEST PASSED ===")
            return 0
        except Exception as e:
            logger.error(f"Self-test failed: {e}")
            return 1
    
    # Normal mode
    try:
        app = ProductivityWidget(cfg, con, no_track=args.no_track)
        app.single_instance = instance
        app.build()
        app.run()
        return 0
    except Exception as e:
        logger.error(f"Application error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
