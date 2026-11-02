"""tokens.py -- AI token usage ingest for Pips.

Implements the ``tokens.py`` section of TOKENS_DIARY_CONTRACT.md. Parses the
OpenCode log (``~/.local/share/opencode/log/opencode.log``) for
``otel: api_request`` usage lines and stores per-request token counts in the
``token_usage`` table of the existing SQLite database.

Design notes
------------
* **Standard library only** (``re``, ``sqlite3``, ``datetime``, ``pathlib``,
  ``logging``, ``os``, ``json``). No third-party deps.
* **Importable with zero side effects** -- nothing but definitions runs at import
  time (no file writes, no DB, no GUI, no threads).
* **Never raise to the caller** -- every public API catches its own exceptions,
  logs to ``logging.getLogger("tokens")`` and returns a safe default.
* **Incremental** -- the byte offset of the last processed newline is stored in
  the ``meta`` table under key ``tokens_log_offset``. Only newly appended bytes
  are read. If the file shrinks below the stored offset it is treated as rotated
  and the offset resets to ``0``.
* **Timezone** -- log timestamps are UTC (``...Z``); they are converted to the
  local system timezone before deriving ``day`` / ``ts``.
* All database work uses the ``sqlite3`` connection passed in by the caller.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

__all__ = [
    "ensure_db",
    "log_path",
    "parse_line",
    "refresh",
    "today_summary",
    "daily_totals",
    "by_model",
    "by_agent",
    "format_tokens",
]

logger = logging.getLogger("tokens")

# Marker that identifies a usage line.
_USAGE_MARKER = "otel: api_request"

# Byte offset of the last fully-processed newline.
_OFFSET_KEY = "tokens_log_offset"

# ``key=value`` where the value may be double-quoted (may contain spaces) or a
# bare non-space token. ``\w`` covers the letters/digits/underscore used by the
# log's field names (sessionID, cost_usd, input_tokens, ...).
_KV_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')

# Log a missing-log-file notice at most once per process.
_MISSING_LOGGED = False


# --------------------------------------------------------------------------- #
# paths / config
# --------------------------------------------------------------------------- #
def _default_log_path() -> str:
    """Resolve the default OpenCode log path for the current user."""
    return str(
        Path.home() / ".local" / "share" / "opencode" / "log" / "opencode.log"
    )


def _resolve_log_path() -> str:
    """Return the configured log path, falling back to the default location.

    ``config.json`` -> ``tokens.log_path`` wins when it is a non-empty string.
    Any failure (missing/invalid config) silently falls back to the default.
    """
    default = _default_log_path()
    try:
        cfg_file = Path(__file__).parent / "config.json"
        with open(cfg_file, encoding="utf-8") as fh:
            cfg = json.load(fh)
        candidate = (cfg.get("tokens") or {}).get("log_path")
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    except Exception:  # pragma: no cover - config is optional
        pass
    return default


def log_path() -> str:
    """Public accessor: resolved OpenCode log path (config override wins)."""
    try:
        return _resolve_log_path()
    except Exception:  # pragma: no cover - _resolve_log_path is already safe
        return _default_log_path()


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def ensure_db(con) -> None:
    """Create the ``token_usage`` table (and ``meta``) idempotently.

    Only adds tables; never touches existing ones. Failures are logged and
    swallowed.
    """
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS token_usage (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              ts            TEXT NOT NULL,
              day           TEXT NOT NULL,
              session_id    TEXT,
              model         TEXT,
              agent         TEXT,
              input_tokens  INTEGER NOT NULL DEFAULT 0,
              output_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens  INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_token_usage_day "
            "ON token_usage(day)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS meta ("
            "  key TEXT PRIMARY KEY,"
            "  value TEXT"
            ")"
        )
        con.commit()
    except sqlite3.Error:
        logger.exception("tokens.ensure_db failed")


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def _to_int(value) -> int:
    """Best-effort int conversion; ``None``/garbage -> 0."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _parse_utc(raw: str):
    """Parse an ISO-8601 UTC timestamp (``...Z``) into an aware datetime."""
    text = (raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_line(line: str):
    """Parse one log line into a usage record, or ``None`` if it is not usage.

    Quoted (``agent="Sisyphus - ultraworker"``) and bare (``agent=unknown``)
    values are both supported. Timestamps are converted from UTC to local time.
    """
    if not line or _USAGE_MARKER not in line:
        return None
    try:
        fields: dict[str, str] = {}
        for match in _KV_RE.finditer(line):
            key = match.group(1)
            value = match.group(2) if match.group(2) is not None else match.group(3)
            fields[key] = value

        dt = _parse_utc(fields.get("timestamp", ""))
        if dt is None:
            return None

        local = dt.astimezone()
        input_tokens = _to_int(fields.get("input_tokens"))
        output_tokens = _to_int(fields.get("output_tokens"))

        return {
            "ts": local.strftime("%Y-%m-%d %H:%M:%S"),
            "day": local.strftime("%Y-%m-%d"),
            "session_id": fields.get("sessionID"),
            "model": fields.get("model"),
            "agent": fields.get("agent"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    except Exception:
        logger.exception("tokens.parse_line failed")
        return None


# --------------------------------------------------------------------------- #
# meta offset helpers
# --------------------------------------------------------------------------- #
def _get_offset(con) -> int:
    try:
        row = con.execute(
            "SELECT value FROM meta WHERE key = ?", (_OFFSET_KEY,)
        ).fetchone()
    except sqlite3.Error:
        return 0
    if not row or row[0] is None:
        return 0
    try:
        return int(float(row[0]))
    except (TypeError, ValueError):
        return 0


def _set_offset(con, offset: int) -> None:
    con.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_OFFSET_KEY, str(int(offset))),
    )


def _existing_keys(con) -> set:
    """Return {(ts, session_id, input_tokens, output_tokens), ...} already stored."""
    try:
        rows = con.execute(
            "SELECT ts, session_id, input_tokens, output_tokens FROM token_usage"
        ).fetchall()
    except sqlite3.Error:
        return set()
    return {
        (r[0], r[1], _to_int(r[2]), _to_int(r[3]))
        for r in rows
    }


def _insert_records(con, records: list) -> int:
    """Insert non-duplicate records; return the number actually added."""
    if not records:
        return 0
    seen = _existing_keys(con)
    added = 0
    for rec in records:
        key = (
            rec["ts"],
            rec["session_id"],
            int(rec["input_tokens"]),
            int(rec["output_tokens"]),
        )
        if key in seen:
            continue
        try:
            con.execute(
                "INSERT INTO token_usage "
                "(ts, day, session_id, model, agent, "
                " input_tokens, output_tokens, total_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec["ts"],
                    rec["day"],
                    rec["session_id"],
                    rec["model"],
                    rec["agent"],
                    rec["input_tokens"],
                    rec["output_tokens"],
                    rec["total_tokens"],
                ),
            )
        except sqlite3.Error:
            logger.exception("tokens: insert failed for %r", rec.get("ts"))
            continue
        seen.add(key)
        added += 1
    return added


# --------------------------------------------------------------------------- #
# ingestion
# --------------------------------------------------------------------------- #
def refresh(con, log_path: str | None = None) -> int:
    """Incrementally parse NEW bytes of the log; return the number of rows added.

    Only the bytes after the stored offset are read, and only up to the last
    complete newline (a trailing partial line is left for the next call). If the
    file size is smaller than the stored offset the file is treated as rotated
    and the offset resets to ``0``. Newly inserted rows are deduplicated by
    ``(ts, session_id, input_tokens, output_tokens)``.

    Never raises: a missing log file returns ``0`` (logged once).
    """
    global _MISSING_LOGGED
    try:
        path = log_path or _resolve_log_path()
        if not path or not os.path.exists(path):
            if not _MISSING_LOGGED:
                logger.info("tokens: log file not found at %s (skipping)", path)
                _MISSING_LOGGED = True
            return 0

        try:
            size = os.path.getsize(path)
        except OSError:
            logger.exception("tokens.refresh: cannot stat %s", path)
            return 0

        offset = _get_offset(con)
        if size < offset:
            # Rotation detected: the file shrank below our position.
            logger.info("tokens: log rotated (size %d < offset %d); reset", size, offset)
            offset = 0
        if size <= offset:
            return 0

        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()

        last_nl = data.rfind(b"\n")
        if last_nl == -1:
            # No complete line available yet; do not advance the offset.
            return 0

        complete = data[: last_nl + 1]
        new_offset = offset + len(complete)
        text = complete.decode("utf-8", errors="replace")

        records = []
        for line in text.split("\n"):
            if not line.strip():
                continue
            rec = parse_line(line)
            if rec is not None:
                records.append(rec)

        added = _insert_records(con, records)
        _set_offset(con, new_offset)
        con.commit()
        return added
    except Exception:
        logger.exception("tokens.refresh failed")
        return 0


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #
def today_summary(con) -> dict:
    """Today's totals -> {"input","output","total","requests","last_ts"}."""
    default = {"input": 0, "output": 0, "total": 0, "requests": 0, "last_ts": ""}
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(input_tokens), 0), "
            "       COALESCE(SUM(output_tokens), 0), "
            "       COALESCE(SUM(total_tokens), 0), "
            "       COUNT(*), MAX(ts) "
            "FROM token_usage WHERE day = ?",
            (date.today().isoformat(),),
        ).fetchone()
    except sqlite3.Error:
        return default
    if not row:
        return default
    return {
        "input": _to_int(row[0]),
        "output": _to_int(row[1]),
        "total": _to_int(row[2]),
        "requests": _to_int(row[3]),
        "last_ts": row[4] or "",
    }


def daily_totals(con, days: int = 7) -> dict:
    """Totals for the last ``days`` days (inclusive of today).

    -> {"YYYY-MM-DD": {"input","output","total","requests"}} ascending by date;
    days with no data are zero-filled so charts stay continuous.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    if days <= 0:
        days = 7

    end = date.today()
    start = end - timedelta(days=days - 1)

    result: dict[str, dict] = {}
    cursor = start
    while cursor <= end:
        result[cursor.isoformat()] = {
            "input": 0,
            "output": 0,
            "total": 0,
            "requests": 0,
        }
        cursor += timedelta(days=1)

    try:
        rows = con.execute(
            "SELECT day, "
            "       COALESCE(SUM(input_tokens), 0), "
            "       COALESCE(SUM(output_tokens), 0), "
            "       COALESCE(SUM(total_tokens), 0), "
            "       COUNT(*) "
            "FROM token_usage WHERE day >= ? AND day <= ? "
            "GROUP BY day",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    except sqlite3.Error:
        return result

    for day, inp, out, total, requests in rows:
        result[str(day)] = {
            "input": _to_int(inp),
            "output": _to_int(out),
            "total": _to_int(total),
            "requests": _to_int(requests),
        }
    return result


def _group_by(con, column: str, day: str) -> list:
    """Shared implementation for ``by_model`` / ``by_agent`` (column is internal)."""
    sql = (
        "SELECT COALESCE({col}, 'unknown') AS grp, "
        "       COALESCE(SUM(input_tokens), 0), "
        "       COALESCE(SUM(output_tokens), 0), "
        "       COALESCE(SUM(total_tokens), 0) "
        "FROM token_usage WHERE day = ? "
        "GROUP BY COALESCE({col}, 'unknown') "
        "ORDER BY COALESCE(SUM(total_tokens), 0) DESC"
    ).format(col=column)
    try:
        rows = con.execute(sql, (day,)).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            column: row[0],
            "input": _to_int(row[1]),
            "output": _to_int(row[2]),
            "total": _to_int(row[3]),
        }
        for row in rows
    ]


def by_model(con, day: str) -> list:
    """-> [{"model","input","output","total"}, ...] sorted by total desc."""
    return _group_by(con, "model", day)


def by_agent(con, day: str) -> list:
    """-> [{"agent","input","output","total"}, ...] sorted by total desc."""
    return _group_by(con, "agent", day)


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def format_tokens(n) -> str:
    """Human-readable token count: 1234567 -> "1.23M", 12345 -> "12.3k", 950 -> "950"."""
    try:
        value = int(n)
    except (TypeError, ValueError):
        try:
            value = int(float(n))
        except (TypeError, ValueError):
            return "0"

    negative = value < 0
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        text = "%.2fM" % (magnitude / 1_000_000.0)
    elif magnitude >= 1000:
        text = "%.1fk" % (magnitude / 1000.0)
    else:
        text = str(magnitude)
    return ("-" + text) if negative else text
