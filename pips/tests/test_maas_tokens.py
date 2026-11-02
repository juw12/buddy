"""tests/test_maas_tokens.py -- stdlib-only hermetic tests for maas_tokens.py.

NO real network: ``urllib.request.urlopen`` and the module's config reader are
monkeypatched with canned login + daily + range JSON. Exits 0 on success, 1 on
any failed assertion. No third-party deps.

Run:  python tests\\test_maas_tokens.py   (from the pips folder)
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from datetime import date, timedelta

# Make ``import maas_tokens`` work when run from anywhere.
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
import maas_tokens  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
TODAY = date.today()
THREE_AGO = TODAY - timedelta(days=3)

DAILY = {
    "date": "2026-10-24",
    "total_tokens": 455235241.0,
    "breakdown": [
        {
            "prompt_tokens": 75019594.0,
            "completion_tokens": 482529.0,
            "ai_model": "Qwen/Qwen3.5-397B-A17B-FP8",
            "user_group": "Your_Name",
            "model_type": "public",
            "value": 75019594.0,
            "total_tokens": 75502123.0,
        },
        {
            "prompt_tokens": 1000.0,
            "completion_tokens": 2000.0,
            "ai_model": "gpt-oss-120b",
            "user_group": "Your_Name",
            "model_type": "public",
            "value": 1000.0,
            "total_tokens": 3000.0,
        },
    ],
}

RANGE = {
    "period": "7d",
    "data": [
        {"date": THREE_AGO.isoformat(), "total_tokens": 41128027.69},
        {"date": TODAY.isoformat(), "total_tokens": 455235241.0},
    ],
}

BASE_URL = "https://example.test/maas/api/v1"


def test_config() -> dict:
    return {
        "enabled": True,
        "base_url": BASE_URL,
        "username": "u",
        "password": "p",
        "verify_ssl": True,
        "cache_minutes": 10,
    }


class FakeResponse:
    """Minimal stand-in for the object returned by ``urlopen``."""

    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url, code, "err", {}, io.BytesIO(b'{"detail":"Not authenticated"}')
    )


class FakeHTTP:
    """Routes canned responses by request path; records every call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.login_status = 200
        self.login_payload = {"access_token": "TOK", "user": {"username": "u"}}
        self.daily_status = 200
        self.daily_payload = DAILY
        self.range_status = 200
        self.range_payload = RANGE
        self.raise_exc: Exception | None = None

    def __call__(self, request, timeout=None, context=None):
        path = request.full_url.split("?", 1)[0]
        self.calls.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "data": request.data,
                "headers": dict(request.header_items()),
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        if path.endswith("/auth/login"):
            if self.login_status != 200:
                raise _http_error(request.full_url, self.login_status)
            return FakeResponse(self.login_payload)
        if path.endswith("/token-usage/daily"):
            if self.daily_status != 200:
                raise _http_error(request.full_url, self.daily_status)
            return FakeResponse(self.daily_payload)
        if path.endswith("/token-usage/range"):
            if self.range_status != 200:
                raise _http_error(request.full_url, self.range_status)
            return FakeResponse(self.range_payload)
        raise _http_error(request.full_url, 404)

    def count(self, suffix: str) -> int:
        return sum(1 for c in self.calls if c["url"].split("?", 1)[0].endswith(suffix))

    def last(self, suffix: str) -> dict | None:
        for c in reversed(self.calls):
            if c["url"].split("?", 1)[0].endswith(suffix):
                return c
        return None


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
_ORIG_URLOPEN = maas_tokens.urllib.request.urlopen
_ORIG_CONFIG = maas_tokens._config


def use(cfg: dict | None, fake: FakeHTTP | None) -> None:
    """Point maas_tokens at a fixed config + fake transport, resetting caches."""
    maas_tokens._config = (lambda: dict(cfg)) if cfg is not None else (lambda: {})
    if fake is not None:
        maas_tokens.urllib.request.urlopen = fake
    maas_tokens.invalidate_cache()


def check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError("FAILED: %s" % label)
    print("  ok - %s" % label)


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_format_tokens() -> None:
    print("[format_tokens]")
    check("1234567 -> 1.23M", maas_tokens.format_tokens(1234567) == "1.23M")
    check("12345 -> 12.3k", maas_tokens.format_tokens(12345) == "12.3k")
    check("950 -> 950", maas_tokens.format_tokens(950) == "950")
    check("0 -> 0", maas_tokens.format_tokens(0) == "0")
    check("1000 -> 1.0k", maas_tokens.format_tokens(1000) == "1.0k")
    check("float -> int truncation", maas_tokens.format_tokens(75502123.0) == "75.50M")
    check("bad -> 0", maas_tokens.format_tokens(None) == "0")


def test_enabled() -> None:
    print("[enabled]")
    use(test_config(), None)
    check("creds present -> True", maas_tokens.enabled() is True)

    disabled = test_config()
    disabled["enabled"] = False
    use(disabled, None)
    check("enabled=false -> False", maas_tokens.enabled() is False)

    blank = test_config()
    blank["password"] = ""
    use(blank, None)
    check("blank password -> False", maas_tokens.enabled() is False)

    use(None, None)  # no config at all
    check("no config -> False", maas_tokens.enabled() is False)


def test_today_summary() -> None:
    print("[today_summary parsing + by-model sorting]")
    fake = FakeHTTP()
    use(test_config(), fake)

    summary = maas_tokens.today_summary()

    check("ok", summary["ok"] is True)
    check("date parsed", summary["date"] == "2026-10-24")
    check("total from total_tokens", summary["total"] == 455235241)
    check("prompt summed", summary["prompt"] == 75020594)
    check("completion summed", summary["completion"] == 484529)
    check("no error", summary["error"] == "")

    models = summary["models"]
    check("two models", len(models) == 2)
    check("models sorted desc (0)",
          models[0]["model"] == "Qwen/Qwen3.5-397B-A17B-FP8")
    check("models sorted desc (0) total", models[0]["total"] == 75502123)
    check("models sorted desc (1)", models[1]["model"] == "gpt-oss-120b")
    check("models sorted desc (1) total", models[1]["total"] == 3000)

    login = fake.last("/auth/login")
    check("login is POST", login["method"] == "POST")
    check("login body urlencoded",
          login["data"] == b"username=u&password=p")
    daily = fake.last("/token-usage/daily")
    check("daily is GET", daily["method"] == "GET")
    check("daily carries bearer token",
          daily["headers"].get("Authorization") == "Bearer TOK")


def test_daily_totals() -> None:
    print("[daily_totals zero-fill + ascending]")
    fake = FakeHTTP()
    use(test_config(), fake)

    totals = maas_tokens.daily_totals(7)

    check("7 keys", len(totals) == 7)
    keys = list(totals.keys())
    check("ascending", keys == sorted(keys))
    check("starts 6 days ago",
          keys[0] == (TODAY - timedelta(days=6)).isoformat())
    check("ends today", keys[-1] == TODAY.isoformat())
    check("today value present", totals[TODAY.isoformat()] == 455235241)
    check("populated past day", totals[THREE_AGO.isoformat()] == 41128027)
    missing = TODAY - timedelta(days=1)
    check("missing day zero-filled", totals[missing.isoformat()] == 0)

    rng = fake.last("/token-usage/range")
    check("range has start_date", "start_date=" in rng["url"])
    check("range has end_date", "end_date=" in rng["url"])


def test_401_relogin_then_graceful() -> None:
    print("[401 -> re-login once, then give up gracefully]")
    fake = FakeHTTP()
    fake.daily_status = 401
    use(test_config(), fake)

    summary = maas_tokens.today_summary()

    check("ok False on 401", summary["ok"] is False)
    check("error set", summary["error"] == "request failed")
    check("counts zeroed", summary["total"] == 0 and summary["prompt"] == 0)
    check("models empty", summary["models"] == [])
    check("logged in twice (re-login once)", fake.count("/auth/login") == 2)
    check("daily attempted twice", fake.count("/token-usage/daily") == 2)


def test_network_error_graceful() -> None:
    print("[network error -> graceful]")
    fake = FakeHTTP()
    fake.raise_exc = urllib.error.URLError("boom")
    use(test_config(), fake)

    summary = maas_tokens.today_summary()
    check("ok False on network error", summary["ok"] is False)
    check("error set", summary["error"] == "request failed")

    totals = maas_tokens.daily_totals(7)
    check("daily_totals still zero-filled", len(totals) == 7)
    check("daily_totals all zero", all(v == 0 for v in totals.values()))

    # A 500 (non-401) must not trigger a retry loop.
    fake2 = FakeHTTP()
    fake2.daily_status = 500
    use(test_config(), fake2)
    summary2 = maas_tokens.today_summary()
    check("ok False on 500", summary2["ok"] is False)
    check("no re-login on 500", fake2.count("/auth/login") == 1)
    check("single daily attempt on 500", fake2.count("/token-usage/daily") == 1)


def test_caching() -> None:
    print("[token + usage caching, force, invalidate]")
    fake = FakeHTTP()
    use(test_config(), fake)

    maas_tokens.today_summary()
    check("first call logs in", fake.count("/auth/login") == 1)
    check("first call fetches daily", fake.count("/token-usage/daily") == 1)

    before = len(fake.calls)
    maas_tokens.today_summary()
    check("second call served from cache", len(fake.calls) == before)

    maas_tokens.daily_totals(7)
    check("range reuses token (no new login)", fake.count("/auth/login") == 1)
    check("range fetched once", fake.count("/token-usage/range") == 1)

    maas_tokens.today_summary(force=True)
    check("force refetches daily", fake.count("/token-usage/daily") == 2)
    check("force keeps cached token", fake.count("/auth/login") == 1)

    maas_tokens.invalidate_cache()
    maas_tokens.today_summary()
    check("invalidate forces re-login", fake.count("/auth/login") == 2)


def test_disabled_no_network() -> None:
    print("[disabled -> no network]")
    fake = FakeHTTP()
    disabled = test_config()
    disabled["enabled"] = False
    use(disabled, fake)

    summary = maas_tokens.today_summary()
    check("ok False when disabled", summary["ok"] is False)
    check("error disabled", summary["error"] == "disabled")
    check("no network when disabled", len(fake.calls) == 0)


def main() -> int:
    try:
        test_format_tokens()
        test_enabled()
        test_today_summary()
        test_daily_totals()
        test_401_relogin_then_graceful()
        test_network_error_graceful()
        test_caching()
        test_disabled_no_network()
    finally:
        maas_tokens._config = _ORIG_CONFIG
        maas_tokens.urllib.request.urlopen = _ORIG_URLOPEN
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
