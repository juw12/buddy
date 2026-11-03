"""maas_tokens.py -- MAAS (Model-as-a-Service) account token usage for Pips.

Implements the ``maas_tokens.py`` section of TOKENS_DIARY_CONTRACT.md. Reads
live token usage for your company MAAS account from the gateway REST
API and returns tidy summaries for the widget's card + dashboard.

Design notes
------------
* **Standard library only** (``urllib.request``, ``urllib.parse``,
  ``urllib.error``, ``json``, ``ssl``, ``datetime``, ``pathlib``, ``logging``,
  ``time``). No third-party deps.
* **Importable with zero side effects** -- nothing but definitions runs at
  import time (no network, no file writes, no DB, no GUI, no threads).
* **Never raise to the caller** -- every public API catches its own exceptions,
  logs to ``logging.getLogger("maas_tokens")`` and returns a safe default.
* **TLS verification stays ON** -- a default (verifying) ``ssl`` context is
  always used; certificate validation is never disabled.
* **In-memory caching** -- the ``access_token`` is cached and re-used until it
  is ~25 min old (or a 401 forces a re-login + one retry); usage responses are
  cached for ``maas.cache_minutes`` (default 10). Use ``force=True`` to bypass.
* **Config** -- read from ``config.json`` next to this module, under the
  ``"maas"`` block. Any read/parse failure falls back to *disabled*.
* **Failure logging is de-duplicated** -- each distinct failure reason is
  logged at most once per process (cleared by ``invalidate_cache``) so the
  periodic updater does not spam the log.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

__all__ = [
    "access_token",
    "enabled",
    "today_summary",
    "daily_totals",
    "format_tokens",
    "invalidate_cache",
]

logger = logging.getLogger("maas_tokens")

# --- defaults --------------------------------------------------------------- #
_DEFAULT_BASE_URL = "https://ai-gateway.example.com/maas/api/v1"
_TIMEOUT = 10.0            # seconds per HTTP request
_TOKEN_TTL = 25 * 60.0     # re-login after ~25 minutes
_DEFAULT_CACHE_MINUTES = 10.0

# --- process-wide in-memory caches ------------------------------------------ #
_token = {"value": None, "at": 0.0}   # {"value": str|None, "at": monotonic}
_usage_cache: dict = {}               # cache_key -> {"at": monotonic, "data": dict}
_logged: set = set()                  # failure reasons already logged


class _HttpError(Exception):
    """Raised for a non-2xx HTTP response, carrying the numeric status code."""

    def __init__(self, code: int):
        super().__init__("HTTP %s" % code)
        self.code = code


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def _config() -> dict:
    """Return the ``maas`` config block, or ``{}`` when unavailable.

    Reads ``config.json`` next to this module. Any failure (missing file,
    invalid JSON, wrong shape) silently yields an empty block -> disabled.
    """
    try:
        cfg_file = Path(__file__).parent / "config.json"
        with open(cfg_file, encoding="utf-8") as fh:
            cfg = json.load(fh)
        block = cfg.get("maas")
        if isinstance(block, dict):
            return block
    except Exception:  # pragma: no cover - config is optional
        pass
    return {}


def _base_url() -> str:
    """Resolve the gateway base URL (config override wins)."""
    base = _config().get("base_url")
    if isinstance(base, str) and base.strip():
        return base.strip()
    return _DEFAULT_BASE_URL


def _cache_ttl() -> float:
    """Usage-cache lifetime in seconds (``maas.cache_minutes``, default 10)."""
    try:
        minutes = float(_config().get("cache_minutes", _DEFAULT_CACHE_MINUTES))
    except (TypeError, ValueError):
        minutes = _DEFAULT_CACHE_MINUTES
    if minutes < 0:
        minutes = _DEFAULT_CACHE_MINUTES
    return minutes * 60.0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _to_int(value) -> int:
    """Best-effort int conversion; ``None``/garbage -> 0 (floats truncate)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _log_once(key: str, message: str) -> None:
    """Log ``message`` at WARNING the first time ``key`` is seen this process."""
    if key in _logged:
        return
    _logged.add(key)
    logger.warning("maas: %s", message)


def _endpoint(path: str, params: dict | None = None) -> str:
    """Build the absolute endpoint URL from the base URL + path + query."""
    url = _base_url().rstrip("/") + path
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    return url


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _http_json(method: str, url: str, headers: dict | None = None,
               form: dict | None = None) -> dict:
    """Perform one HTTP request and parse a JSON body.

    ``form`` (when given) is urlencoded into the request body and the
    ``Content-Type`` header is set accordingly. TLS verification is always on
    (default ``ssl`` context). Raises ``_HttpError`` on a non-2xx status and
    lets network/parse errors propagate to the caller.
    """
    data = None
    hdrs = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    context = ssl.create_default_context()

    try:
        response = urllib.request.urlopen(request, timeout=_TIMEOUT, context=context)
        try:
            raw = response.read()
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
    except urllib.error.HTTPError as exc:
        try:
            exc.read()
        except Exception:
            pass
        raise _HttpError(exc.code) from exc

    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def _login() -> str | None:
    """Log in and cache the returned ``access_token``; ``None`` on failure."""
    try:
        cfg = _config()
        username = cfg.get("username") or ""
        password = cfg.get("password") or ""
        if not (isinstance(username, str) and username.strip()
                and isinstance(password, str) and password.strip()):
            return None
        payload = _http_json(
            "POST",
            _endpoint("/auth/login"),
            form={"username": username, "password": password},
        )
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            _log_once("login-no-token", "login returned no access_token")
            return None
        _token["value"] = token
        _token["at"] = time.monotonic()
        return token
    except _HttpError as exc:
        _log_once("login-http", "login failed (HTTP %s)" % exc.code)
        return None
    except Exception:
        _log_once("login-error", "login failed (network/parse)")
        return None


def _get_token(force: bool = False) -> str | None:
    """Return a cached token, re-logging in when stale or ``force`` is set."""
    try:
        if (not force and _token["value"]
                and (time.monotonic() - _token["at"]) < _TOKEN_TTL):
            return _token["value"]
        return _login()
    except Exception:
        logger.exception("maas._get_token failed")
        return None


def _authorized_get(path: str, params: dict | None = None,
                    force_token: bool = False) -> dict | None:
    """GET ``path`` with a bearer token; re-login once on 401.

    Returns the parsed JSON dict, or ``None`` on any failure (missing token,
    network/timeout, JSON error, HTTP error, persistent 401). Never raises.
    """
    try:
        token = _get_token(force=force_token)
        if not token:
            _log_once("no-token", "no access token available")
            return None

        url = _endpoint(path, params)
        try:
            return _http_json("GET", url, headers={"Authorization": "Bearer " + token})
        except _HttpError as exc:
            if exc.code != 401:
                _log_once("http-%s" % exc.code, "request failed (HTTP %s)" % exc.code)
                return None
            # Expired/absent bearer token: re-login once and retry.
            token = _get_token(force=True)
            if not token:
                return None
            try:
                return _http_json(
                    "GET", url, headers={"Authorization": "Bearer " + token}
                )
            except _HttpError as retry_exc:
                if retry_exc.code == 401:
                    _log_once("unauthorized", "still unauthorized after re-login")
                else:
                    _log_once("http-%s" % retry_exc.code,
                              "request failed (HTTP %s)" % retry_exc.code)
                return None
            except Exception:
                _log_once("request-error", "request failed (network/parse)")
                return None
        except Exception:
            _log_once("request-error", "request failed (network/parse)")
            return None
    except Exception:
        logger.exception("maas._authorized_get failed")
        return None


# --------------------------------------------------------------------------- #
# cached fetches
# --------------------------------------------------------------------------- #
def _cached_get(cache_key: str, path: str, params: dict | None,
                force: bool) -> dict | None:
    """Fetch ``path`` through the usage cache; ``None`` on failure."""
    now = time.monotonic()
    entry = _usage_cache.get(cache_key)
    if not force and entry and (now - entry["at"]) < _cache_ttl():
        return entry["data"]
    data = _authorized_get(path, params)
    if data is None:
        return None
    _usage_cache[cache_key] = {"at": now, "data": data}
    return data


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    """True only if ``maas.enabled`` is set and username + password are non-empty."""
    try:
        cfg = _config()
        if not cfg.get("enabled"):
            return False
        username = cfg.get("username") or ""
        password = cfg.get("password") or ""
        return bool(
            isinstance(username, str) and username.strip()
            and isinstance(password, str) and password.strip()
        )
    except Exception:
        return False


def today_summary(force: bool = False) -> dict:
    """Today's account usage -> ``{"ok","date","total","prompt","completion",
    "models","error"}``.

    ``models`` is a list of ``{"model","prompt","completion","total"}`` sorted
    by ``total`` descending. On any failure ``ok`` is False, counts are 0,
    ``models`` is empty and ``error`` describes the reason.
    """
    result = {
        "ok": False,
        "date": "",
        "total": 0,
        "prompt": 0,
        "completion": 0,
        "models": [],
        "error": "",
    }
    try:
        if not enabled():
            result["error"] = "disabled"
            return result
        raw = _cached_get("daily", "/token-usage/daily", None, force)
        if raw is None:
            result["error"] = "request failed"
            return result
        if not isinstance(raw, dict):
            result["error"] = "bad response"
            return result

        result["ok"] = True
        result["date"] = str(raw.get("date") or "")
        result["total"] = _to_int(raw.get("total_tokens"))

        models: dict = {}
        prompt_total = 0
        completion_total = 0
        for item in raw.get("breakdown") or []:
            if not isinstance(item, dict):
                continue
            model = str(item.get("ai_model") or "unknown")
            prompt = _to_int(item.get("prompt_tokens"))
            completion = _to_int(item.get("completion_tokens"))
            entry = models.get(model)
            if entry is None:
                entry = {"model": model, "prompt": 0, "completion": 0, "total": 0}
                models[model] = entry
            entry["prompt"] += prompt
            entry["completion"] += completion
            entry["total"] += prompt + completion
            prompt_total += prompt
            completion_total += completion

        result["prompt"] = prompt_total
        result["completion"] = completion_total
        result["models"] = sorted(
            models.values(), key=lambda m: m["total"], reverse=True
        )
        return result
    except Exception:
        logger.exception("maas.today_summary failed")
        result["ok"] = False
        result["error"] = "unexpected error"
        return result


def daily_totals(days: int = 7, force: bool = False) -> dict:
    """Account totals for the last ``days`` days -> ``{"YYYY-MM-DD": int}``.

    Ascending by date; days with no data are zero-filled so charts stay
    continuous. On any failure a zero-filled mapping is still returned.
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    if days <= 0:
        days = 7

    end = date.today()
    start = end - timedelta(days=days - 1)

    result: dict = {}
    cursor = start
    while cursor <= end:
        result[cursor.isoformat()] = 0
        cursor += timedelta(days=1)

    try:
        if not enabled():
            return result
        raw = _cached_get(
            "range:%d" % days,
            "/token-usage/range",
            {"start_date": start.isoformat(), "end_date": end.isoformat()},
            force,
        )
        if not isinstance(raw, dict):
            return result
        for item in raw.get("data") or []:
            if not isinstance(item, dict):
                continue
            day = str(item.get("date") or "")
            if day in result:
                result[day] = _to_int(item.get("total_tokens"))
        return result
    except Exception:
        logger.exception("maas.daily_totals failed")
        return result


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


def invalidate_cache() -> None:
    """Drop the cached token, cached usage responses and logged-failure set."""
    try:
        _usage_cache.clear()
        _token["value"] = None
        _token["at"] = 0.0
        _logged.clear()
    except Exception:
        logger.exception("maas.invalidate_cache failed")


def access_token(force: bool = False) -> str | None:
    """Bearer token for the MAAS gateway (Buddy's "Smart" brain uses it); ``None`` when not logged in.
    Cached ~25 min like the usage calls; ``force=True`` logs in again (after a 401)."""
    try:
        if not enabled():
            return None
        return _get_token(force=force)
    except Exception:
        logger.exception("maas.access_token failed")
        return None
