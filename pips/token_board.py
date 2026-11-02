"""
token_board.py - Token Board for Pips (private build only, v2.29).

A live, refreshable window that combines:
  * my MAAS token usage (Pips' own MAAS login, see maas_tokens.py):
    today so far, tokens/min right now, daily history with a 7-day average,
    today's split by model;
  * the company-wide Top-30 token ranking from the company Grafana
    dashboard (same source as the daily "token leaderboard" e-mail):
    my rank, gaps to #1 / the next person, catch-up estimates at the
    current 30-day pace, and my rank history over the dashboard's 5-year
    window (back-filled from the dashboard, plus a point on every refresh).

Charts are drawn on tkinter Canvas and are interactive: hover for exact
values, range / metric / Top-N toggles, click a bar or row to compare
that user with me. A compact "mini" mode floats on top as a small widget.

Optional config.json block (all keys optional):
  "token_board": {
      "user": "your.username",               # defaults to maas.username
      "dashboard_base": "https://monitor.example.com",
      "dashboard_token": "<public dashboard access token>",
      "user_panel": 112, "top_n": 30, "long_range": "now-5y", "pace_days": 30
  }

Company-internal (like MAAS): NOT part of the public package (release.py).
Importable without side effects. Standard library only (+ Pips modules).
Never raises out of public API: failures are logged and shown in the window.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

try:
    import i18n
except Exception:  # pragma: no cover - i18n always ships with Pips
    i18n = None

try:
    import theme as theme_mod
except Exception:  # pragma: no cover
    theme_mod = None

try:
    import maas_tokens
except Exception:  # pragma: no cover
    maas_tokens = None

logger = logging.getLogger("widget")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_FILE = DATA_DIR / "token_rank_snapshots.json"
HISTORY_FILE = DATA_DIR / "token_rank_history.json"   # dashboard back-fill (v2.29)
PREFS_FILE = DATA_DIR / "token_board_prefs.json"
MAAS_PAGE = "https://ai-gateway.example.com/maas/token-usage"

DEFAULTS = {
    "user": "",
    "dashboard_base": "https://monitor.example.com",
    "dashboard_token": "5c22b4c5a91c4f26a10d73b63f333c3e",
    "user_panel": 112,
    "top_n": 30,
    "long_range": "now-5y",
    "pace_days": 30,
    "history_days": 180,
}
SNAPSHOT_MIN_GAP_S = 15 * 60
AUTO_CHOICES = [0, 1, 5, 15, 30]
RANGE_CHOICES = [7, 14, 30, 90]
HISTORY_YEARS = 5            # rank = cumulative tokens over the 5 years up to each day
HISTORY_DAILY_DAYS = 60      # daily points for the recent past, weekly before that
HISTORY_WORKERS = 6
HISTORY_RANGES = [("all", "all"), (365, "1Y"), (90, "90D"), (30, "30D")]
FULL_GEOMETRY = "1180x820"
MINI_GEOMETRY = "340x520"

CHART = {
    "bar": "#5794F2",
    "today": "#E0A82E",
    "me": "#E0A82E",
    "selected": "#D0484C",
    "avg": "#D0484C",
    "good": "#2fa84f",
    "bad": "#d93838",
}

# --------------------------------------------------------------------------- #
# strings (en, id, zh_TW, zh_CN) - kept here so the public i18n.py stays clean
# --------------------------------------------------------------------------- #
STRINGS = {
    "title": ("Token Board", "Papan Token", "Token 看板", "Token 看板"),
    "menu": ("Token Board (company AI)", "Papan Token (company AI)", "Token 看板 (company AI)", "Token 看板 (company AI)"),
    "refresh": ("\u27F3 Refresh", "\u27F3 Muat ulang", "\u27F3 重新整理", "\u27F3 刷新"),
    "refreshing": ("Refreshing\u2026", "Memuat\u2026", "更新中\u2026", "更新中\u2026"),
    "auto": ("Auto", "Otomatis", "自動", "自动"),
    "off": ("off", "mati", "關", "关"),
    "minutes": ("{n} min", "{n} mnt", "{n} 分", "{n} 分"),
    "next": ("next {t}", "berikutnya {t}", "下次 {t}", "下次 {t}"),
    "pin": ("\U0001F4CC On top", "\U0001F4CC Di atas", "\U0001F4CC 置頂", "\U0001F4CC 置顶"),
    "mini": ("\u25A3 Mini", "\u25A3 Mini", "\u25A3 迷你", "\u25A3 迷你"),
    "full": ("\u25A3 Full", "\u25A3 Penuh", "\u25A3 完整", "\u25A3 完整"),
    "updated": ("Updated {t} \u00B7 {s}s", "Diperbarui {t} \u00B7 {s}d", "更新於 {t} \u00B7 {s} 秒", "更新于 {t} \u00B7 {s} 秒"),
    "loading": ("Loading company AI data\u2026", "Memuat data company AI\u2026", "正在載入 company AI 資料\u2026", "正在加载 company AI 数据\u2026"),
    "today": ("Today so far", "Hari ini", "今日至今", "今日至今"),
    "rate": ("Tokens / min now", "Token / menit", "目前 Tokens/分", "当前 Tokens/分"),
    "yesterday": ("Yesterday", "Kemarin", "昨日", "昨日"),
    "last7": ("Last 7 days", "7 hari terakhir", "近 7 日", "近 7 日"),
    "last30": ("Last 30 days", "30 hari terakhir", "近 30 日", "近 30 日"),
    "rank": ("My rank", "Peringkatku", "我的排名", "我的排名"),
    "gap_top": ("Gap to #1", "Selisih ke #1", "與 #1 差距", "与 #1 差距"),
    "gap_next": ("Gap to #{r}", "Selisih ke #{r}", "與 #{r} 差距", "与 #{r} 差距"),
    "avg_day": ("avg {n}/day", "rata2 {n}/hari", "平均 {n}/日", "平均 {n}/日"),
    "of_users": ("of {n}", "dari {n}", "共 {n} 人", "共 {n} 人"),
    "pct_top": ("{p}% of #1", "{p}% dari #1", "達 #1 的 {p}%", "达 #1 的 {p}%"),
    "since": ("+{n} since last refresh", "+{n} sejak muat ulang", "較上次 +{n}", "较上次 +{n}"),
    "rank_up": ("\u25B2 {n} since last refresh", "\u25B2 {n} sejak muat ulang", "\u25B2 {n}（較上次）", "\u25B2 {n}（较上次）"),
    "rank_down": ("\u25BC {n} since last refresh", "\u25BC {n} sejak muat ulang", "\u25BC {n}（較上次）", "\u25BC {n}（较上次）"),
    "eta": ("\u2248 {d} days ({date})", "\u2248 {d} hari ({date})", "約 {d} 天 ({date})", "约 {d} 天 ({date})"),
    "never": ("not closing at this pace", "tidak terkejar dengan laju ini", "以目前速度追不上", "以目前速度追不上"),
    "ahead": ("already ahead", "sudah di depan", "已超越", "已超越"),
    "safe": ("not at this pace", "tidak dengan laju ini", "以目前速度不會", "以目前速度不会"),
    "pace_line": ("My {d}-day avg {mine}/day \u00B7 #1 {leader} {theirs}/day \u2192 catch #1: {eta}",
                  "Rata2 {d} hari-ku {mine}/hari \u00B7 #1 {leader} {theirs}/hari \u2192 kejar #1: {eta}",
                  "我近 {d} 日平均 {mine}/日 \u00B7 #1 {leader} {theirs}/日 \u2192 追上 #1：{eta}",
                  "我近 {d} 日平均 {mine}/日 \u00B7 #1 {leader} {theirs}/日 \u2192 追上 #1：{eta}"),
    "pace_next": ("#{r} {user} {theirs}/day \u2192 catch: {eta}",
                  "#{r} {user} {theirs}/hari \u2192 kejar: {eta}",
                  "#{r} {user} {theirs}/日 \u2192 追上：{eta}",
                  "#{r} {user} {theirs}/日 \u2192 追上：{eta}"),
    "pace_behind": ("#{r} {user} is {gap} behind at {theirs}/day \u2192 overtakes me: {eta}",
                    "#{r} {user} tertinggal {gap}, {theirs}/hari \u2192 menyalipku: {eta}",
                    "#{r} {user} 落後 {gap}，{theirs}/日 \u2192 超越我：{eta}",
                    "#{r} {user} 落后 {gap}，{theirs}/日 \u2192 超越我：{eta}"),
    "daily": ("My daily tokens", "Token harianku", "我的每日 Token", "我的每日 Token"),
    "daily_hint": ("today in gold \u00B7 red line = 7-day average \u00B7 hover for values",
                   "hari ini emas \u00B7 garis merah = rata2 7 hari \u00B7 arahkan kursor",
                   "金色 = 今日 \u00B7 紅線 = 7 日平均 \u00B7 滑鼠移上看數值",
                   "金色 = 今日 \u00B7 红线 = 7 日平均 \u00B7 鼠标移上看数值"),
    "models": ("Today by model", "Hari ini per model", "今日模型分布", "今日模型分布"),
    "no_usage": ("No usage yet today", "Belum ada pemakaian hari ini", "今日尚無使用", "今日尚无使用"),
    "top": ("Top {n} \u00B7 company ranking ({range})", "Top {n} \u00B7 peringkat perusahaan ({range})",
            "Top {n} \u00B7 全公司排行（{range}）", "Top {n} \u00B7 全公司排行（{range}）"),
    "m_total": ("Total", "Total", "總量", "总量"),
    "m_recent": ("Last 30d", "30 hari", "近 30 日", "近 30 日"),
    "m_perday": ("Per day", "Per hari", "每日", "每日"),
    "top_hint": ("hover for values \u00B7 click a bar to compare with me \u00B7 scroll for more",
                 "arahkan kursor \u00B7 klik batang untuk membandingkan \u00B7 gulir",
                 "滑鼠移上看數值 \u00B7 點擊長條與我比較 \u00B7 可捲動",
                 "鼠标移上看数值 \u00B7 点击长条与我比较 \u00B7 可滚动"),
    "history": ("My rank history", "Riwayat peringkatku", "我的排名紀錄", "我的排名记录"),
    "history_hint": ("company rank since company AI data began (5-year window) \u00B7 log scale \u00B7 hover for values",
                     "peringkat perusahaan sejak data company AI ada (jendela 5 tahun) \u00B7 skala log \u00B7 arahkan kursor",
                     "company AI 開始記錄至今的全公司排名（5 年區間）\u00B7 對數刻度 \u00B7 滑鼠移上看數值",
                     "company AI 开始记录至今的全公司排名（5 年区间）\u00B7 对数刻度 \u00B7 鼠标移上看数值"),
    "hist_all": ("All", "Semua", "全部", "全部"),
    "hist_loading": ("loading history\u2026 {d}/{n}", "memuat riwayat\u2026 {d}/{n}",
                     "載入紀錄中\u2026 {d}/{n}", "载入记录中\u2026 {d}/{n}"),
    "hist_searching": ("finding where history starts\u2026", "mencari awal riwayat\u2026",
                       "尋找紀錄起點\u2026", "寻找记录起点\u2026"),
    "hist_before": ("not ranked yet", "belum masuk peringkat", "尚未上榜", "尚未上榜"),
    "hist_of": ("#{r} of {n}", "#{r} dari {n}", "#{r} / {n} 人", "#{r} / {n} 人"),
    "d_total": ("Total tokens", "Total token", "總 Token", "总 Token"),
    "d_pct": ("% of #1", "% dari #1", "達 #1 比例", "达 #1 比例"),
    "d_uses": ("Uses", "Pemakaian", "使用次數", "使用次数"),
    "d_days": ("Active days", "Hari aktif", "使用天數", "使用天数"),
    "d_perday": ("Tokens / active day", "Token / hari aktif", "Token / 使用日", "Token / 使用日"),
    "d_recent": ("Last {d} days", "{d} hari terakhir", "近 {d} 日", "近 {d} 日"),
    "d_ahead": ("Ahead of me by", "Di depanku", "領先我", "领先我"),
    "d_behind": ("Behind me by", "Di belakangku", "落後我", "落后我"),
    "d_catch": ("I catch them", "Aku menyusul", "我追上", "我追上"),
    "d_caught": ("They catch me", "Mereka menyusul", "對方追上我", "对方追上我"),
    "d_pace": ("Pace (them vs me)", "Laju (mereka vs aku)", "速度（對方 vs 我）", "速度（对方 vs 我）"),
    "me": ("me", "aku", "我", "我"),
    "cutoff": ("Top {n} cut-off", "Batas Top {n}", "Top {n} 門檻", "Top {n} 门槛"),
    "in_top": ("You're in the Top {n}", "Kamu di Top {n}", "你在 Top {n} 內", "你在 Top {n} 内"),
    "need": ("need +{n}", "perlu +{n}", "還差 +{n}", "还差 +{n}"),
    "progress": ("Progress to #1", "Menuju #1", "距離 #1 進度", "距离 #1 进度"),
    "err_maas": ("MAAS usage unavailable \u2013 check the MAAS login in config.json",
                 "Pemakaian MAAS tidak tersedia \u2013 cek login MAAS di config.json",
                 "無法讀取 MAAS 用量 \u2013 請檢查 config.json 的 MAAS 登入",
                 "无法读取 MAAS 用量 \u2013 请检查 config.json 的 MAAS 登录"),
    "err_rank": ("Company ranking unavailable (dashboard unreachable)",
                 "Peringkat perusahaan tidak tersedia (dashboard tidak terjangkau)",
                 "無法讀取全公司排行（無法連到儀表板）",
                 "无法读取全公司排行（无法连到仪表板）"),
    "not_found": ("{user} not found in the ranking", "{user} tidak ditemukan", "排行中找不到 {user}", "排行中找不到 {user}"),
    "dashboard": ("Dashboard \u2197", "Dashboard \u2197", "儀表板 \u2197", "仪表板 \u2197"),
    "maas_page": ("MAAS \u2197", "MAAS \u2197", "MAAS \u2197", "MAAS \u2197"),
    "scope_all": ("\U0001F3E2 Whole company ({n})", "\U0001F3E2 Seluruh perusahaan ({n})",
                  "\U0001F3E2 全公司（{n}）", "\U0001F3E2 全公司（{n}）"),
    "scope_company": ("company", "perusahaan", "全公司", "全公司"),
    "scope_sep": ("\u2500\u2500\u2500 other divisions \u2500\u2500\u2500", "\u2500\u2500\u2500 divisi lain \u2500\u2500\u2500",
                  "\u2500\u2500\u2500 其他一級單位 \u2500\u2500\u2500", "\u2500\u2500\u2500 其他一级单位 \u2500\u2500\u2500"),
    "scope_tip": ("Rank within", "Peringkat di", "排名範圍", "排名范围"),
    "top_scoped": ("Top {n} \u00B7 {scope} ({range})", "Top {n} \u00B7 {scope} ({range})",
                   "Top {n} \u00B7 {scope}（{range}）", "Top {n} \u00B7 {scope}（{range}）"),
    "of_scope": ("of {n} in {scope}", "dari {n} di {scope}", "{scope} 共 {n} 人", "{scope} 共 {n} 人"),
    "guest": ("guest", "tamu", "客座", "客座"),
    "guest_of": ("as a guest among {n} in {scope}", "sebagai tamu di antara {n} di {scope}",
                 "以客座身分加入 {scope}（{n} 人）", "以客座身份加入 {scope}（{n} 人）"),
    "lead": ("Lead over #2 {user}", "Unggul dari #2 {user}", "領先 #2 {user}", "领先 #2 {user}"),
    "top_spot": ("You're #1 here \U0001F3C6", "Kamu #1 di sini \U0001F3C6", "你是這裡的 #1 \U0001F3C6", "你是这里的 #1 \U0001F3C6"),
    "company_rank": ("Company rank", "Peringkat perusahaan", "全公司排名", "全公司排名"),
    "not_in_scope": ("You're not in {scope}", "Kamu tidak ada di {scope}", "你不在 {scope}", "你不在 {scope}"),
    "scope_leader": ("{scope}: {n} people \u00B7 #1 {user} {tokens} \u00B7 total {total}",
                     "{scope}: {n} orang \u00B7 #1 {user} {tokens} \u00B7 total {total}",
                     "{scope}：{n} 人 \u00B7 #1 {user} {tokens} \u00B7 合計 {total}",
                     "{scope}：{n} 人 \u00B7 #1 {user} {tokens} \u00B7 合计 {total}"),
    "alerts": ("\U0001F514 Pet alerts", "\U0001F514 Notif pet", "\U0001F514 寵物提醒", "\U0001F514 宠物提醒"),
    "hint": ("#{r} \u00B7 {n} today", "#{r} \u00B7 {n} hari ini", "#{r} \u00B7 今日 {n}", "#{r} \u00B7 今日 {n}"),
    "open": ("Open board", "Buka papan", "打開看板", "打开看板"),
    "ev_up": ("\u26A1 You moved up to #{r} in company AI tokens \u2013 you passed {user}!",
              "\u26A1 Kamu naik ke #{r} token company AI \u2013 menyalip {user}!",
              "\u26A1 你的 company AI Token 排名升到 #{r}，超越了 {user}！",
              "\u26A1 你的 company AI Token 排名升到 #{r}，超越了 {user}！"),
    "ev_down": ("\U0001F440 {user} just passed you. You're #{r} now, {gap} behind.",
                "\U0001F440 {user} baru menyalipmu. Kamu #{r}, tertinggal {gap}.",
                "\U0001F440 {user} 剛超越你。你現在 #{r}，落後 {gap}。",
                "\U0001F440 {user} 刚超越你。你现在 #{r}，落后 {gap}。"),
    "ev_top": ("\U0001F3C6 You're in the company AI Top {n}!", "\U0001F3C6 Kamu masuk Top {n} company AI!",
               "\U0001F3C6 你進入 company AI Top {n} 了！", "\U0001F3C6 你进入 company AI Top {n} 了！"),
    "ev_milestone": ("\u26A1 {n} tokens today! ({rate}/min right now)",
                     "\u26A1 {n} token hari ini! ({rate}/menit sekarang)",
                     "\u26A1 今天已用 {n} Token！（目前 {rate}/分）",
                     "\u26A1 今天已用 {n} Token！（当前 {rate}/分）"),
}
_LANG_INDEX = {"en": 0, "id": 1, "zh_TW": 2, "zh_CN": 3}


def tr(key: str, **kwargs) -> str:
    row = STRINGS.get(key)
    if not row:
        return key
    lang = "en"
    try:
        if i18n:
            lang = i18n.get_language()
    except Exception:
        pass
    text = row[_LANG_INDEX.get(lang, 0)] or row[0]
    try:
        return text.format(**kwargs) if kwargs else text
    except Exception:
        return text


def menu_label() -> str:
    return tr("menu")


# --------------------------------------------------------------------------- #
# config + formatting helpers
# --------------------------------------------------------------------------- #
def settings(cfg: dict | None) -> dict:
    merged = dict(DEFAULTS)
    try:
        merged.update({k: v for k, v in ((cfg or {}).get("token_board") or {}).items() if v not in (None, "")})
    except Exception:
        pass
    if not merged.get("user"):
        merged["user"] = str(((cfg or {}).get("maas") or {}).get("username") or "")
    return merged


def available(cfg: dict | None) -> bool:
    """Shown in the menus only when MAAS is configured (company-internal feature)."""
    try:
        return bool(maas_tokens and maas_tokens.enabled())
    except Exception:
        return False


def fmt(value) -> str:
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return "\u2014"
    magnitude = abs(value)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            digits = 1 if magnitude >= threshold * 100 else 2
            return f"{value / threshold:.{digits}f} {suffix}"
    return f"{value:,.0f}"


def catch_up(gap: float, my_rate: float, their_rate: float):
    """-> (text, good) days needed to close ``gap`` at the given daily paces."""
    if gap <= 0:
        return tr("ahead"), True
    closing = my_rate - their_rate
    if closing <= 0:
        return tr("never"), False
    days = gap / closing
    eta = date.today() + timedelta(days=days)
    return tr("eta", d=f"{math.ceil(days):,}", date=eta.isoformat()), True


def norm_unit(unit) -> str:
    """'(QA)第六事業群-第六事業處-...' -> '第六事業群-第六事業處-...' (site tag removed)."""
    return re.sub(r"^\([^)]*\)", "", str(unit or "")).strip()


def scope_members(rows: list, scope: dict | None) -> list:
    """Rows inside a ranking scope: {'kind': 'all'} | {'kind': 'level1', 'value': 一級單位}
    | {'kind': 'path', 'value': unit-path prefix}."""
    scope = scope or {}
    kind, value = scope.get("kind"), scope.get("value")
    if kind == "level1":
        return [r for r in rows if r["level1"] == value]
    if kind == "path" and value:
        return [r for r in rows if norm_unit(r["unit"]) == value or norm_unit(r["unit"]).startswith(value + "-")]
    return list(rows)


def scope_name(scope: dict | None) -> str:
    scope = scope or {}
    if scope.get("kind") == "level1":
        return str(scope.get("value"))
    if scope.get("kind") == "path":
        parts = str(scope.get("value")).split("-")
        return ("\u2026" if len(parts) > 2 else "") + "-".join(parts[-2:])
    return tr("scope_company")


def scope_options(rows: list, me: dict | None) -> list:
    """[(label, scope or None for a separator)] - company, my own unit path, then other divisions."""
    options = [(tr("scope_all", n=f"{len(rows):,}"), {"kind": "all"})]
    if me:
        parts = norm_unit(me["unit"]).split("-")
        for depth in range(2, len(parts) + 1):
            scope = {"kind": "path", "value": "-".join(parts[:depth])}
            options.append((f"\U0001F465 {scope_name(scope)} ({len(scope_members(rows, scope))})", scope))
    totals = {}
    for row in rows:
        if row["level1"]:
            count, tokens = totals.get(row["level1"], (0, 0))
            totals[row["level1"]] = (count + 1, tokens + (row["tokens"] or 0))
    others = sorted((name for name in totals if not me or name != me["level1"]),
                    key=lambda name: totals[name][1], reverse=True)
    if others:
        options.append((tr("scope_sep"), None))
        for name in others:
            options.append((f"\U0001F3DB {name} ({totals[name][0]})", {"kind": "level1", "value": name}))
    return options


def _palette() -> dict:
    fallback = {"bg": "#faf8f3", "card": "#ffffff", "ink": "#1a1a1a", "muted": "#6b6b6b",
                "accent": "#8b1a1a", "accent_ink": "#ffffff", "soft": "#f1ece4",
                "border": "#e2ddd4", "gold": "#c8961e", "warm": "#fbf3e6",
                "online": "#2fa84f", "busy": "#d93838", "name": "light"}
    try:
        return {**fallback, **theme_mod.palette()} if theme_mod else fallback
    except Exception:
        return fallback


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"token_board: could not write {path.name}: {exc}")


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def _http_json(url: str, payload: dict | None = None, timeout: float = 60.0) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=body, method="POST" if body else "GET",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def query_user_panel(conf: dict, time_from: str, time_to: str = "now") -> list:
    """Rows [level1, unit, user, uses, tokens, days] sorted by tokens desc."""
    url = (f"{conf['dashboard_base']}/api/public/dashboards/"
           f"{conf['dashboard_token']}/panels/{int(conf['user_panel'])}/query")
    payload = {
        "intervalMs": 86400000,
        "maxDataPoints": 1000,
        "timeRange": {"from": time_from, "to": time_to, "timezone": "browser"},
    }
    result = _http_json(url, payload)
    for response in (result.get("results") or {}).values():
        if response.get("error"):
            raise RuntimeError(response["error"])
        for frame in response.get("frames", []):
            columns = frame.get("data", {}).get("values") or []
            if columns:
                rows = [list(r) for r in zip(*columns)]
                return sorted(rows, key=lambda r: r[4] or 0, reverse=True)
    return []


def fetch_ranking(conf: dict) -> dict:
    me_name = str(conf.get("user") or "").lower()
    with ThreadPoolExecutor(max_workers=2) as pool:
        long_job = pool.submit(query_user_panel, conf, conf["long_range"])
        recent_job = pool.submit(query_user_panel, conf, f"now-{int(conf['pace_days'])}d")
        ranked = long_job.result()
        recent_rows = recent_job.result()
    recent = {str(r[2]).lower(): r[4] or 0 for r in recent_rows}
    recent_rank = {str(r[2]).lower(): i for i, r in enumerate(recent_rows, start=1)}
    rows = []
    for rank, (level1, unit, user, uses, tokens, days) in enumerate(ranked, start=1):
        rows.append({
            "rank": rank, "level1": level1, "unit": unit, "user": str(user),
            "uses": uses or 0, "tokens": tokens or 0, "days": days or 0,
            "recent": recent.get(str(user).lower(), 0),
            "is_me": bool(me_name) and str(user).lower() == me_name,
        })
    me = next((r for r in rows if r["is_me"]), None)
    return {
        "rows": rows,                      # everyone (~1.6k rows) so departments can be ranked locally
        "total_users": len(rows),
        "me": me,
        "my_recent_rank": recent_rank.get(me_name),
    }


def fetch_personal(conf: dict) -> dict:
    if not (maas_tokens and maas_tokens.enabled()):
        raise RuntimeError("MAAS not configured")
    today = date.today()
    start = today - timedelta(days=int(conf["history_days"]) - 1)
    get = maas_tokens._authorized_get
    with ThreadPoolExecutor(max_workers=3) as pool:
        history_job = pool.submit(get, "/token-usage/range",
                                  {"start_date": start.isoformat(), "end_date": today.isoformat()})
        daily_job = pool.submit(get, "/token-usage/daily")
        rate_job = pool.submit(get, "/token-usage/per-minute")
        history = history_job.result()
        daily = daily_job.result()
        rate = rate_job.result() or {}
    if history is None or daily is None:
        raise RuntimeError("MAAS request failed")
    by_date = {d.get("date"): float(d.get("total_tokens") or 0) for d in history.get("data", [])}
    today_total = float(daily.get("total_tokens") or 0)
    by_date[today.isoformat()] = max(by_date.get(today.isoformat(), 0.0), today_total)
    series = []
    for offset in range(int(conf["history_days"]) - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        series.append((day, by_date.get(day, 0.0)))
    models = sorted(
        ({"model": b.get("ai_model_short") or b.get("ai_model") or "?",
          "full": b.get("ai_model") or "",
          "total": float(b.get("total_tokens") or 0)} for b in daily.get("breakdown", [])),
        key=lambda m: m["total"], reverse=True,
    )
    return {
        "series": series,
        "today": today_total,
        "rate": float(rate.get("total_tokens_per_minute") or 0),
        "rate_in": float(rate.get("input_tokens_per_minute") or 0),
        "rate_out": float(rate.get("output_tokens_per_minute") or 0),
        "models": models,
    }


def record_snapshot(ranking: dict) -> list:
    snapshots = _read_json(SNAPSHOT_FILE, [])
    if not isinstance(snapshots, list):
        snapshots = []
    me = ranking.get("me")
    if not me or not ranking.get("rows"):
        return snapshots
    point = {"t": time.time(), "rank": me["rank"], "tokens": me["tokens"],
             "gap": ranking["rows"][0]["tokens"] - me["tokens"]}
    if snapshots and point["t"] - snapshots[-1].get("t", 0) < SNAPSHOT_MIN_GAP_S:
        snapshots[-1] = point
    else:
        snapshots.append(point)
    snapshots = snapshots[-3000:]
    _write_json(SNAPSHOT_FILE, snapshots)
    return snapshots


# --------------------------------------------------------------------------- #
# long rank history: back-filled from the dashboard (v2.29)
# --------------------------------------------------------------------------- #
# The dashboard panel accepts any time window, so my company rank on a past day
# is "rank by tokens over the 5 years up to that day". Past days never change,
# so each day is fetched once and cached in HISTORY_FILE.
def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _day_end(day: date) -> datetime:
    return datetime.combine(day, dtime(23, 59, 59))


def _rows_at(conf: dict, when: datetime) -> list:
    start = when - timedelta(days=365 * HISTORY_YEARS)
    return query_user_panel(conf, str(_ms(start)), str(_ms(when)))


def _rank_point(rows: list, me_name: str, when: datetime) -> dict:
    top = (rows[0][4] or 0) if rows else 0
    point = {"t": when.timestamp(), "users": len(rows), "rank": None, "tokens": 0, "gap": top}
    for rank, row in enumerate(rows, start=1):
        if str(row[2]).lower() == me_name:
            tokens = row[4] or 0
            point.update(rank=rank, tokens=tokens, gap=top - tokens)
            break
    return point


def _first_day(predicate, low: date, high: date):
    """First day in (low, high] where predicate(day) is True (monotonic), searched
    HISTORY_WORKERS points at a time. None if not even ``high`` qualifies."""
    if not predicate(high):
        return None
    with ThreadPoolExecutor(max_workers=HISTORY_WORKERS) as pool:
        while (high - low).days > 1:
            span = (high - low).days
            probes = sorted({low + timedelta(days=max(1, span * i // (HISTORY_WORKERS + 1)))
                             for i in range(1, HISTORY_WORKERS + 1)} - {high})
            results = dict(zip(probes, pool.map(predicate, probes)))
            hits = [d for d in probes if results[d]]
            misses = [d for d in probes if not results[d]]
            high = hits[0] if hits else high
            low = max([d for d in misses if d < high], default=low)
    return high


def history_sample_days(today: date, first_seen: date) -> list:
    """Completed days to sample: every Sunday since I first appeared + every day of the last N days."""
    yesterday = today - timedelta(days=1)
    first = max(first_seen, today - timedelta(days=365 * HISTORY_YEARS))
    days = {first, yesterday} if first <= yesterday else set()
    sunday = yesterday - timedelta(days=(yesterday.weekday() + 1) % 7)
    while sunday >= first:
        days.add(sunday)
        sunday -= timedelta(days=7)
    for offset in range(1, HISTORY_DAILY_DAYS + 1):
        day = today - timedelta(days=offset)
        if day >= first:
            days.add(day)
    return sorted(days)


def _empty_history(me_name: str) -> dict:
    return {"version": 1, "user": me_name, "years": HISTORY_YEARS,
            "data_start": None, "first_seen": None, "searched": None, "points": {}}


def load_rank_history(cache: dict | None = None) -> dict:
    """{'points': [ranked points, oldest first], 'data_start': ts|None, 'first_seen': ts|None}"""
    cache = cache if cache is not None else _read_json(HISTORY_FILE, {})
    if not isinstance(cache, dict):
        cache = {}
    points = sorted((p for p in (cache.get("points") or {}).values() if p.get("rank")),
                    key=lambda p: p["t"])

    def stamp(key):
        return _day_end(date.fromisoformat(cache[key])).timestamp() - 86399 if cache.get(key) else None

    return {"points": points, "data_start": stamp("data_start"), "first_seen": stamp("first_seen")}


def update_rank_history(conf: dict, progress=None) -> dict:
    """Fetch whatever days are missing from the cache (slow only the first time). Never raises."""
    me_name = str(conf.get("user") or "").lower()
    cache = _read_json(HISTORY_FILE, {})
    if (not isinstance(cache, dict) or cache.get("version") != 1 or cache.get("user") != me_name
            or cache.get("years") != HISTORY_YEARS):
        cache = _empty_history(me_name)
    if not me_name:
        return load_rank_history(cache)
    today = date.today()
    yesterday = today - timedelta(days=1)
    window_start = today - timedelta(days=365 * HISTORY_YEARS)
    try:
        if not cache.get("first_seen") and cache.get("searched") != today.isoformat():
            if progress:
                progress(0, 0)
            if not cache.get("data_start"):
                found = _first_day(lambda d: bool(_rows_at(conf, _day_end(d))), window_start, yesterday)
                cache["data_start"] = found.isoformat() if found else None
            if cache.get("data_start"):
                low = date.fromisoformat(cache["data_start"]) - timedelta(days=1)
                found = _first_day(lambda d: _rank_point(_rows_at(conf, _day_end(d)), me_name,
                                                         _day_end(d))["rank"] is not None, low, yesterday)
                cache["first_seen"] = found.isoformat() if found else None
            cache["searched"] = today.isoformat()
            _write_json(HISTORY_FILE, cache)
    except Exception as exc:
        logger.warning(f"token_board: history start search failed: {exc!r}")
        return load_rank_history(cache)
    if not cache.get("first_seen"):
        return load_rank_history(cache)

    missing = [d for d in history_sample_days(today, date.fromisoformat(cache["first_seen"]))
               if d.isoformat() not in cache["points"]]
    done = 0
    if progress and missing:
        progress(0, len(missing))
    with ThreadPoolExecutor(max_workers=HISTORY_WORKERS) as pool:
        jobs = {pool.submit(_rows_at, conf, _day_end(day)): day for day in missing}
        for job in as_completed(jobs):
            day = jobs[job]
            try:
                cache["points"][day.isoformat()] = _rank_point(job.result(), me_name, _day_end(day))
            except Exception as exc:
                logger.warning(f"token_board: history {day} failed: {exc!r}")
            done += 1
            if progress:
                progress(done, len(missing))
            if done % 20 == 0:
                _write_json(HISTORY_FILE, cache)
    _write_json(HISTORY_FILE, cache)
    return load_rank_history(cache)


def merged_history(history: dict | None, snapshots: list | None) -> list:
    """Back-filled daily/weekly points + the live 15-min snapshots after them, oldest first."""
    points = list((history or {}).get("points") or [])
    last = points[-1]["t"] if points else 0
    points += [dict(s, live=True) for s in (snapshots or []) if s.get("rank") and s.get("t", 0) > last]
    return sorted(points, key=lambda p: p["t"])


def collect(cfg: dict | None) -> dict:
    """Fetch everything (blocking, ~5 s). Never raises."""
    conf = settings(cfg)
    started = time.time()
    out = {"personal": None, "ranking": None, "errors": [], "conf": conf}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {"personal": pool.submit(fetch_personal, conf), "ranking": pool.submit(fetch_ranking, conf)}
        for name, job in jobs.items():
            try:
                out[name] = job.result()
            except Exception as exc:
                logger.warning(f"token_board: {name} failed: {exc!r}")
                out["errors"].append(tr("err_maas" if name == "personal" else "err_rank"))
    out["snapshots"] = record_snapshot(out["ranking"]) if out["ranking"] else _read_json(SNAPSHOT_FILE, [])
    out["fetched_at"] = datetime.now()
    out["elapsed"] = round(time.time() - started, 1)
    return out


# --------------------------------------------------------------------------- #
# background watcher: pet alerts + live menu hint (v2.28)
# --------------------------------------------------------------------------- #
WATCH_FILE = DATA_DIR / "token_board_watch.json"
TODAY_MILESTONES = [1e8, 2.5e8, 5e8, 7.5e8, 1e9, 1.5e9, 2e9, 3e9, 5e9, 1e10]


def watch_minutes(cfg: dict | None) -> int:
    """Minutes between background checks (config token_board.watch_minutes, 0 = off)."""
    try:
        return max(0, int(((cfg or {}).get("token_board") or {}).get("watch_minutes", 10)))
    except (TypeError, ValueError):
        return 10


def alerts_enabled() -> bool:
    return bool(_read_json(PREFS_FILE, {}).get("announce", True))


def menu_hint() -> str:
    """Short live status for menus, e.g. '#14 · 292 M today' (from the last check)."""
    state = _read_json(WATCH_FILE, {})
    if not state.get("rank") or state.get("day") != date.today().isoformat():
        return ""
    return tr("hint", r=state["rank"], n=fmt(state.get("today", 0)))


def check_for_news(cfg: dict | None) -> list:
    """Blocking (~5 s, run in a thread). Fetches, compares with the last check, returns
    pet-ready messages. First run ever only records a baseline. Never raises."""
    try:
        data = collect(cfg)
    except Exception as exc:
        logger.warning(f"token_board watch failed: {exc!r}")
        return []
    ranking, personal = data.get("ranking"), data.get("personal")
    state = _read_json(WATCH_FILE, {})
    first_run = not state
    today = date.today().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["milestones"] = []
    events = []
    me = (ranking or {}).get("me")
    if me:
        by_rank = {r["rank"]: r for r in ranking["rows"]}
        old_rank = state.get("rank")
        top_n = int(data["conf"]["top_n"])
        if old_rank and me["rank"] < old_rank:
            passed = by_rank.get(me["rank"] + 1)
            events.append(tr("ev_up", r=me["rank"], user=passed["user"] if passed else "?"))
            if old_rank > top_n >= me["rank"]:
                events.append(tr("ev_top", n=top_n))
        elif old_rank and me["rank"] > old_rank:
            ahead = by_rank.get(me["rank"] - 1)
            if ahead:
                events.append(tr("ev_down", user=ahead["user"], r=me["rank"],
                                 gap=fmt(ahead["tokens"] - me["tokens"])))
        state["rank"] = me["rank"]
        state["tokens"] = me["tokens"]
    if personal:
        reached = [m for m in TODAY_MILESTONES if personal["today"] >= m]
        fresh = [m for m in reached if m not in state.get("milestones", [])]
        if fresh and not first_run:
            events.append(tr("ev_milestone", n=fmt(max(fresh)), rate=fmt(personal["rate"])))
        state["milestones"] = reached
        state["today"] = personal["today"]
    state["checked"] = time.time()
    _write_json(WATCH_FILE, state)
    return events


# --------------------------------------------------------------------------- #
# window
# --------------------------------------------------------------------------- #
class TokenBoardWindow:
    def __init__(self, master, cfg: dict, notify=None):
        import tkinter as tk

        self.tk = tk
        self.cfg = cfg or {}
        self.notify = notify
        self.p = _palette()
        prefs = _read_json(PREFS_FILE, {})
        self.auto_minutes = int(prefs.get("auto", 5)) if prefs.get("auto", 5) in AUTO_CHOICES else 5
        self.range_days = int(prefs.get("range", 30)) if prefs.get("range", 30) in RANGE_CHOICES else 30
        self.metric = prefs.get("metric", "tokens") if prefs.get("metric") in ("tokens", "recent", "perday") else "tokens"
        self.show_n = int(prefs.get("show", 30)) if prefs.get("show", 30) in (10, 20, 30) else 30
        history_keys = [key for key, _label in HISTORY_RANGES]
        self.hist_range = prefs.get("hist_range", "all") if prefs.get("hist_range", "all") in history_keys else "all"
        self.rank_history = load_rank_history()          # cached back-fill, instant
        self._history_loading = False
        self._history_progress = None
        self._history_result = None
        self.on_top = bool(prefs.get("on_top", False))
        self.mini = bool(prefs.get("mini", False))
        self.announce = bool(prefs.get("announce", True))
        scope = prefs.get("scope")
        self.scope = scope if isinstance(scope, dict) and scope.get("kind") in ("all", "level1", "path") else {"kind": "all"}
        self._scope_choices = {}
        self.data = None
        self.prev = None
        self.selected = None
        self.loading = False
        self.next_at = 0.0
        self._result = None
        self._geom = {}
        self._buttons = {}

        p = self.p
        self.top = tk.Toplevel(master)
        self.top.title("Pips \u00B7 " + tr("title"))
        self.top.configure(bg=p["bg"])
        self.top.geometry(MINI_GEOMETRY if self.mini else FULL_GEOMETRY)
        self.top.minsize(320, 400)
        icon = BASE_DIR / "assets" / "widget.ico"
        if icon.exists():
            try:
                self.top.iconbitmap(str(icon))
            except Exception:
                pass
        self.top.protocol("WM_DELETE_WINDOW", self.top.withdraw)
        self.top.bind("<F5>", lambda _e: self.refresh())

        self._build_header()
        self.error_label = tk.Label(self.top, text="", bg=p["bg"], fg=CHART["bad"],
                                    font=("Segoe UI", 9), anchor="w", justify="left")
        self.error_label.pack(fill="x", padx=14)
        self.full = tk.Frame(self.top, bg=p["bg"])
        self.compact = tk.Frame(self.top, bg=p["bg"])
        self._build_full(self.full)
        self._build_compact(self.compact)
        self._apply_mode()
        self.top.attributes("-topmost", self.on_top)
        self._tick()
        self.top.after(800, self.start_history_backfill)

    # ---- small widget helpers -------------------------------------------------
    def _button(self, parent, text, command, primary=False):
        p = self.p
        bg = p["accent"] if primary else p["soft"]
        fg = p["accent_ink"] if primary else p["ink"]
        button = self.tk.Label(parent, text=text, bg=bg, fg=fg, padx=10, pady=4, cursor="hand2",
                               font=("Segoe UI Semibold" if primary else "Segoe UI", 9))
        button.bind("<Button-1>", lambda _e: command())
        return button

    def _seg(self, parent, group, options, current, command):
        frame = self.tk.Frame(parent, bg=self.p["card"])
        self._buttons[group] = {}
        for value, label in options:
            button = self._button(frame, label, lambda v=value: command(v))
            button.configure(padx=7, pady=2)
            button.pack(side="left", padx=1)
            self._buttons[group][value] = button
        self._paint_seg(group, current)
        return frame

    def _paint_seg(self, group, current):
        p = self.p
        for value, button in self._buttons.get(group, {}).items():
            active = value == current
            button.configure(bg=p["accent"] if active else p["soft"],
                             fg=p["accent_ink"] if active else p["ink"])

    def _panel(self, parent, title=None):
        p = self.p
        frame = self.tk.Frame(parent, bg=p["card"], highlightthickness=1,
                              highlightbackground=p["border"], padx=10, pady=8)
        head = self.tk.Frame(frame, bg=p["card"])
        head.pack(fill="x")
        if title is not None:
            label = self.tk.Label(head, text=title, bg=p["card"], fg=p["ink"],
                                  font=("Segoe UI Semibold", 10))
            label.pack(side="left")
            frame.title_label = label
        frame.head = head
        return frame

    def _card(self, parent, key):
        p = self.p
        frame = self.tk.Frame(parent, bg=p["card"], highlightthickness=1,
                              highlightbackground=p["border"], padx=10, pady=6)
        label = self.tk.Label(frame, text="", bg=p["card"], fg=p["muted"], font=("Segoe UI", 8), anchor="w")
        value = self.tk.Label(frame, text="\u2014", bg=p["card"], fg=p["accent"],
                              font=("Segoe UI Semibold", 16), anchor="w")
        sub = self.tk.Label(frame, text="", bg=p["card"], fg=p["muted"], font=("Segoe UI", 8),
                            anchor="w", justify="left", wraplength=150)
        label.pack(fill="x")
        value.pack(fill="x")
        sub.pack(fill="x")
        self.cards[key] = (label, value, sub)
        return frame

    def _set_card(self, key, label=None, value=None, sub=None, sub_good=None):
        widgets = self.cards.get(key)
        if not widgets:
            return
        label_w, value_w, sub_w = widgets
        if label is not None:
            label_w.configure(text=label)
        if value is not None:
            value_w.configure(text=value)
        if sub is not None:
            color = self.p["muted"] if sub_good is None else (CHART["good"] if sub_good else CHART["bad"])
            sub_w.configure(text=sub, fg=color)

    # ---- layout ---------------------------------------------------------------
    def _build_header(self):
        tk, p = self.tk, self.p
        head = tk.Frame(self.top, bg=p["bg"], padx=14, pady=8)
        head.pack(fill="x")
        self.title_label = tk.Label(head, text="\u26A1 " + tr("title"), bg=p["bg"], fg=p["accent"],
                                    font=("Segoe UI Semibold", 14))
        self.title_label.pack(side="left")
        self.status = tk.Label(head, text=tr("loading"), bg=p["bg"], fg=p["muted"], font=("Segoe UI", 9))
        self.status.pack(side="left", padx=10)
        right = tk.Frame(head, bg=p["bg"])
        right.pack(side="right")
        self.mode_button = self._button(right, "", self.toggle_mini)
        self.mode_button.pack(side="right", padx=2)
        self.pin_button = self._button(right, tr("pin"), self.toggle_on_top)
        self.pin_button.pack(side="right", padx=2)
        self.alert_button = self._button(right, tr("alerts"), self.toggle_alerts)
        self.alert_button.pack(side="right", padx=2)
        self.refresh_button = self._button(right, tr("refresh"), self.refresh, primary=True)
        self.refresh_button.pack(side="right", padx=2)
        self.countdown = tk.Label(right, text="", bg=p["bg"], fg=p["muted"], font=("Segoe UI", 8))
        self.countdown.pack(side="right", padx=4)
        self.auto_frame = tk.Frame(right, bg=p["bg"])
        self.auto_frame.pack(side="right", padx=4)
        tk.Label(self.auto_frame, text=tr("auto"), bg=p["bg"], fg=p["muted"], font=("Segoe UI", 8)).pack(side="left")
        self._buttons["auto"] = {}
        for minutes in AUTO_CHOICES:
            label = tr("off") if minutes == 0 else tr("minutes", n=minutes)
            button = self._button(self.auto_frame, label, lambda m=minutes: self.set_auto(m))
            button.configure(padx=5, pady=2, font=("Segoe UI", 8))
            button.pack(side="left", padx=1)
            self._buttons["auto"][minutes] = button
        self._paint_seg("auto", self.auto_minutes)

    def _build_full(self, root):
        tk, p = self.tk, self.p
        self.cards = {}
        cards = tk.Frame(root, bg=p["bg"])
        cards.pack(fill="x", padx=14, pady=(2, 4))
        keys = ["today", "rate", "yesterday", "last7", "last30", "rank", "gap_top", "gap_next"]
        for column, key in enumerate(keys):
            self._card(cards, key).grid(row=0, column=column, sticky="nsew", padx=3)
            cards.grid_columnconfigure(column, weight=1, uniform="cards")
        for key in keys:
            self._set_card(key, label=tr(key) if key in STRINGS else key)

        pace = tk.Frame(root, bg=p["bg"])
        pace.pack(fill="x", padx=17)
        from tkinter import ttk
        scope_row = tk.Frame(pace, bg=p["bg"])
        scope_row.pack(fill="x", pady=(2, 2))
        tk.Label(scope_row, text=tr("scope_tip"), bg=p["bg"], fg=p["muted"],
                 font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        self.scope_var = tk.StringVar(value="")
        self.scope_box = ttk.Combobox(scope_row, textvariable=self.scope_var, state="readonly",
                                      width=42, height=24, font=("Segoe UI", 9))
        self.scope_box.pack(side="left")
        self.scope_box.bind("<<ComboboxSelected>>", self._on_scope_selected)
        self.progress = tk.Canvas(pace, height=10, bg=p["bg"], highlightthickness=0)
        self.progress.pack(fill="x", pady=(2, 2))
        self.pace_label = tk.Label(pace, text="", bg=p["bg"], fg=p["ink"], font=("Segoe UI", 9),
                                   justify="left", anchor="w")
        self.pace_label.pack(fill="x")

        middle = tk.Frame(root, bg=p["bg"])
        middle.pack(fill="x", padx=14, pady=6)
        daily = self._panel(middle, tr("daily"))
        daily.pack(side="left", fill="both", expand=True, padx=3)
        self._seg(daily.head, "range", [(d, f"{d}D") for d in RANGE_CHOICES],
                  self.range_days, self.set_range).pack(side="right")
        self.daily_canvas = tk.Canvas(daily, height=165, bg=p["card"], highlightthickness=0)
        self.daily_canvas.pack(fill="both", expand=True, pady=(6, 0))
        tk.Label(daily, text=tr("daily_hint"), bg=p["card"], fg=p["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        self.daily_canvas.bind("<Motion>", self._daily_hover)
        self.daily_canvas.bind("<Leave>", lambda _e: self.daily_canvas.delete("tip"))
        self.daily_canvas.bind("<Configure>", lambda _e: self.draw_daily())

        models = self._panel(middle, tr("models"))
        models.configure(width=300)
        models.pack(side="left", fill="y", padx=3)
        models.pack_propagate(False)
        self.models_frame = tk.Frame(models, bg=p["card"])
        self.models_frame.pack(fill="both", expand=True, pady=(6, 0))
        links = tk.Frame(models, bg=p["card"])
        links.pack(fill="x", side="bottom")
        for text, url_getter in ((tr("maas_page"), lambda: MAAS_PAGE),
                                 (tr("dashboard"), self._dashboard_url)):
            link = tk.Label(links, text=text, bg=p["card"], fg="#3b78d8", cursor="hand2", font=("Segoe UI", 9))
            link.pack(side="left", padx=(0, 10))
            link.bind("<Button-1>", lambda _e, g=url_getter: webbrowser.open(g()))

        bottom = tk.Frame(root, bg=p["bg"])
        bottom.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        top_panel = self._panel(bottom, tr("top", n=30, range="5y"))
        top_panel.pack(side="left", fill="both", expand=True, padx=3)
        self.top_title = top_panel.title_label
        self._seg(top_panel.head, "show", [(n, str(n)) for n in (10, 20, 30)],
                  self.show_n, self.set_show).pack(side="right", padx=(6, 0))
        self._seg(top_panel.head, "metric",
                  [("tokens", tr("m_total")), ("recent", tr("m_recent")), ("perday", tr("m_perday"))],
                  self.metric, self.set_metric).pack(side="right")
        holder = tk.Frame(top_panel, bg=p["card"])
        holder.pack(fill="both", expand=True, pady=(6, 0))
        self.top_canvas = tk.Canvas(holder, bg=p["card"], highlightthickness=0)
        scroll = tk.Scrollbar(holder, orient="vertical", command=self.top_canvas.yview)
        self.top_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.top_canvas.pack(side="left", fill="both", expand=True)
        tk.Label(top_panel, text=tr("top_hint"), bg=p["card"], fg=p["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        self.top_canvas.bind("<Motion>", self._top_hover)
        self.top_canvas.bind("<Leave>", lambda _e: self.top_canvas.delete("tip"))
        self.top_canvas.bind("<Button-1>", self._top_click)
        self.top_canvas.bind("<MouseWheel>",
                             lambda e: self.top_canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.top_canvas.bind("<Configure>", lambda _e: self.draw_top())

        side = tk.Frame(bottom, bg=p["bg"], width=320)
        side.pack(side="left", fill="y", padx=3)
        side.pack_propagate(False)
        detail = self._panel(side)
        detail.pack(fill="x")
        self.detail_frame = tk.Frame(detail, bg=p["card"])
        self.detail_frame.pack(fill="x")
        # v2.29: the long rank history gets its own full-height column (it was a thin strip under the detail)
        history_column = tk.Frame(bottom, bg=p["bg"], width=390)
        history_column.pack(side="left", fill="y", padx=3)
        history_column.pack_propagate(False)
        history = self._panel(history_column, tr("history"))
        history.pack(fill="both", expand=True)
        self._seg(history.head, "hist_range",
                  [(key, tr("hist_all") if key == "all" else label) for key, label in HISTORY_RANGES],
                  self.hist_range, self.set_hist_range).pack(side="right")
        self.history_canvas = tk.Canvas(history, height=150, bg=p["card"], highlightthickness=0)
        self.history_canvas.pack(fill="both", expand=True, pady=(6, 0))
        tk.Label(history, text=tr("history_hint"), bg=p["card"], fg=p["muted"], font=("Segoe UI", 8),
                 wraplength=365, justify="left").pack(anchor="w")
        self.history_canvas.bind("<Motion>", self._history_hover)
        self.history_canvas.bind("<Leave>", lambda _e: self.history_canvas.delete("tip"))
        self.history_canvas.bind("<Configure>", lambda _e: self.draw_history())

    def _build_compact(self, root):
        tk, p = self.tk, self.p
        self.mini_values = {}
        grid = tk.Frame(root, bg=p["bg"])
        grid.pack(fill="x", padx=10, pady=4)
        for index, key in enumerate(["today", "rate", "rank", "gap_next"]):
            cell = tk.Frame(grid, bg=p["card"], highlightthickness=1, highlightbackground=p["border"], padx=8, pady=5)
            cell.grid(row=index // 2, column=index % 2, sticky="nsew", padx=3, pady=3)
            grid.grid_columnconfigure(index % 2, weight=1, uniform="mini")
            label = tk.Label(cell, text=tr(key, r="?"), bg=p["card"], fg=p["muted"], font=("Segoe UI", 8), anchor="w")
            value = tk.Label(cell, text="\u2014", bg=p["card"], fg=p["accent"], font=("Segoe UI Semibold", 15), anchor="w")
            sub = tk.Label(cell, text="", bg=p["card"], fg=p["muted"], font=("Segoe UI", 8), anchor="w",
                           wraplength=140, justify="left")
            label.pack(fill="x")
            value.pack(fill="x")
            sub.pack(fill="x")
            self.mini_values[key] = (label, value, sub)
        self.mini_progress_label = tk.Label(root, text=tr("progress"), bg=p["bg"], fg=p["muted"],
                                            font=("Segoe UI", 8), anchor="w")
        self.mini_progress_label.pack(fill="x", padx=13)
        self.mini_progress = tk.Canvas(root, height=10, bg=p["bg"], highlightthickness=0)
        self.mini_progress.pack(fill="x", padx=13, pady=(0, 4))
        self.mini_canvas = tk.Canvas(root, height=95, bg=p["card"], highlightthickness=1,
                                     highlightbackground=p["border"])
        self.mini_canvas.pack(fill="x", padx=13, pady=4)
        self.mini_canvas.bind("<Configure>", lambda _e: self.draw_mini())
        self.mini_canvas.bind("<Motion>", self._mini_hover)
        self.mini_canvas.bind("<Leave>", lambda _e: self.mini_canvas.delete("tip"))
        self.neighbors = tk.Frame(root, bg=p["card"], highlightthickness=1, highlightbackground=p["border"])
        self.neighbors.pack(fill="x", padx=13, pady=4)
        self.mini_status = tk.Label(root, text="", bg=p["bg"], fg=p["muted"], font=("Segoe UI", 8), anchor="w")
        self.mini_status.pack(fill="x", padx=13)

    def _apply_mode(self):
        if self.mini:
            self.full.pack_forget()
            self.auto_frame.pack_forget()
            self.status.pack_forget()
            self.countdown.pack_forget()
            self.title_label.configure(text="\u26A1")
            self.compact.pack(fill="both", expand=True)
            self.mode_button.configure(text=tr("full"))
        else:
            self.compact.pack_forget()
            self.title_label.configure(text="\u26A1 " + tr("title"))
            self.status.pack(side="left", padx=10, after=self.title_label)
            self.countdown.pack(side="right", padx=4, after=self.refresh_button)
            self.auto_frame.pack(side="right", padx=4, after=self.countdown)
            self.full.pack(fill="both", expand=True)
            self.mode_button.configure(text=tr("mini"))
        self._paint_toggle(self.pin_button, self.on_top)
        self._paint_toggle(self.alert_button, self.announce)
        if self.mini:
            self.alert_button.pack_forget()
        else:
            self.alert_button.pack(side="right", padx=2, after=self.pin_button)

    def _paint_toggle(self, button, active):
        p = self.p
        button.configure(bg=p["accent"] if active else p["soft"], fg=p["accent_ink"] if active else p["ink"])

    # ---- controls -------------------------------------------------------------
    def _save_prefs(self):
        _write_json(PREFS_FILE, {"auto": self.auto_minutes, "range": self.range_days, "metric": self.metric,
                                 "show": self.show_n, "on_top": self.on_top, "mini": self.mini,
                                 "announce": self.announce, "scope": self.scope, "hist_range": self.hist_range})

    def set_auto(self, minutes):
        self.auto_minutes = minutes
        self._paint_seg("auto", minutes)
        self._schedule_next()
        self._save_prefs()

    def set_range(self, days):
        self.range_days = days
        self._paint_seg("range", days)
        self._save_prefs()
        self.draw_daily()

    def set_hist_range(self, key):
        self.hist_range = key
        self._paint_seg("hist_range", key)
        self._save_prefs()
        self.draw_history()

    def start_history_backfill(self):
        """Fill in missing past days from the dashboard in the background (once per day after the first run)."""
        if self._history_loading or not self.alive():
            return
        self._history_loading = True
        self._history_result = None
        conf = settings(self.cfg)

        def progress(done, total):
            self._history_progress = (done, total)

        def work():
            try:
                self._history_result = update_rank_history(conf, progress)
            except Exception as exc:  # update_rank_history already guards; belt and braces
                logger.warning(f"token_board: history back-fill failed: {exc!r}")
                self._history_result = self.rank_history

        threading.Thread(target=work, name="token-board-history", daemon=True).start()
        self.top.after(400, self._poll_history)

    def _poll_history(self):
        if not self.alive():
            return
        if self._history_result is None:
            if self._history_progress and not self.mini:
                self.draw_history()
            self.top.after(700, self._poll_history)
            return
        self.rank_history = self._history_result
        self._history_loading = False
        self._history_progress = None
        if not self.mini:
            self.draw_history()

    def set_metric(self, metric):
        self.metric = metric
        self._paint_seg("metric", metric)
        self._save_prefs()
        self.draw_top()

    def set_show(self, count):
        self.show_n = count
        self._paint_seg("show", count)
        self._save_prefs()
        self.draw_top()

    def toggle_on_top(self):
        self.on_top = not self.on_top
        self.top.attributes("-topmost", self.on_top)
        self._apply_mode()
        self._save_prefs()

    def toggle_alerts(self):
        self.announce = not self.announce
        self._apply_mode()
        self._save_prefs()

    def toggle_mini(self):
        self.mini = not self.mini
        if self.mini and not self.on_top:
            self.on_top = True
            self.top.attributes("-topmost", True)
        self.top.geometry(MINI_GEOMETRY if self.mini else FULL_GEOMETRY)
        self._apply_mode()
        self._save_prefs()
        self.top.after(50, self.render)

    def _dashboard_url(self):
        conf = settings(self.cfg)
        return f"{conf['dashboard_base']}/public-dashboards/{conf['dashboard_token']}"

    # ---- refresh loop ---------------------------------------------------------
    def refresh(self):
        if self.loading:
            return
        self.loading = True
        self.refresh_button.configure(text=tr("refreshing"))
        self._result = None

        def work():
            self._result = collect(self.cfg)

        threading.Thread(target=work, name="token-board", daemon=True).start()
        self.top.after(150, self._poll)

    def _poll(self):
        if not self.alive():
            return
        if self._result is None:
            self.top.after(150, self._poll)
            return
        self.prev, self.data = self.data, self._result
        self.loading = False
        self.refresh_button.configure(text=tr("refresh"))
        stamp = self.data["fetched_at"].strftime("%H:%M")
        self.status.configure(text=tr("updated", t=stamp, s=self.data["elapsed"]))
        self.mini_status.configure(text=tr("updated", t=stamp, s=self.data["elapsed"]))
        self.error_label.configure(text="  \u00B7  ".join(self.data["errors"]))
        self._schedule_next()
        try:
            self.render()
        except Exception as exc:
            logger.error(f"token_board render failed: {exc!r}")
        self.start_history_backfill()

    def _schedule_next(self):
        self.next_at = time.time() + self.auto_minutes * 60 if self.auto_minutes else 0.0

    def _tick(self):
        if not self.alive():
            return
        if self.next_at:
            remaining = max(0, int(self.next_at - time.time()))
            self.countdown.configure(text=tr("next", t=f"{remaining // 60}:{remaining % 60:02d}"))
            visible = self.top.state() != "withdrawn"
            if remaining == 0 and visible and not self.loading:
                self.refresh()
        else:
            self.countdown.configure(text="")
        self.top.after(1000, self._tick)

    # ---- derived numbers -------------------------------------------------------
    def _stats(self):
        personal = (self.data or {}).get("personal")
        if not personal:
            return None
        series = personal["series"]
        past = series[:-1]
        values = [v for _d, v in past]
        best = max(past, key=lambda item: item[1]) if past else ("", 0)
        return {
            "yesterday": values[-1] if values else 0,
            "day_before": values[-2] if len(values) > 1 else 0,
            "sum7": sum(values[-7:]),
            "sum30": sum(values[-30:]),
            "active30": sum(1 for v in values[-30:] if v > 0),
            "best": best,
        }

    def _scoped(self, ranking):
        """Rows of the chosen department re-ranked 1..n (company rank kept as 'company_rank')."""
        if not ranking or not ranking.get("rows"):
            return []
        members = scope_members(ranking["rows"], self.scope)
        if not members:
            self.scope = {"kind": "all"}
            members = list(ranking["rows"])
        company_me = ranking.get("me")
        if company_me and not any(r["is_me"] for r in members):
            # not my department: rank me inside it as a guest so I can see where I'd stand
            members.append(dict(company_me, guest=True))
        members.sort(key=lambda r: r["tokens"] or 0, reverse=True)
        return [dict(r, rank=i, company_rank=r["rank"]) for i, r in enumerate(members, start=1)]

    def _ranking_bits(self):
        ranking = (self.data or {}).get("ranking")
        rows = self._scoped(ranking)
        if not rows:
            return None
        conf = self.data["conf"]
        me = next((r for r in rows if r["is_me"]), None)
        pace_days = int(conf["pace_days"])
        by_rank = {r["rank"]: r for r in rows}
        by_recent = sorted(rows, key=lambda r: r["recent"] or 0, reverse=True)
        my_recent_rank = next((i for i, r in enumerate(by_recent, start=1) if r["is_me"]), None)
        return {
            "ranking": ranking, "rows": rows, "me": me, "leader": rows[0], "pace_days": pace_days,
            "total": len(rows), "scope": scope_name(self.scope), "my_recent_rank": my_recent_rank,
            "guest": bool(me and me.get("guest")),
            "members": sum(1 for r in rows if not r.get("guest")),
            "company_me": ranking.get("me"),
            "ahead": by_rank.get(me["rank"] - 1) if me else None,
            "behind": by_rank.get(me["rank"] + 1) if me else None,
            "cutoff": by_rank.get(int(conf["top_n"])),
            "pace": lambda r: (r["recent"] or 0) / pace_days,
        }

    def _refresh_scope_options(self):
        ranking = (self.data or {}).get("ranking")
        if not ranking or not ranking.get("rows"):
            return
        options = scope_options(ranking["rows"], ranking.get("me"))
        self._scope_choices = {label: scope for label, scope in options}
        self.scope_box.configure(values=[label for label, _scope in options])
        current = next((label for label, scope in options if scope == self.scope), None)
        if current is None:
            self.scope = {"kind": "all"}
            current = options[0][0]
        self.scope_var.set(current)

    def _on_scope_selected(self, _event=None):
        scope = self._scope_choices.get(self.scope_var.get())
        if scope is None:                     # the separator line
            self._refresh_scope_options()
            return
        self.scope = scope
        self.selected = None
        self._save_prefs()
        self.render()

    # ---- rendering ------------------------------------------------------------
    def render(self):
        if not self.data:
            return
        self._refresh_scope_options()
        self._render_cards()
        if self.mini:
            self.draw_mini()
            self._render_neighbors()
        else:
            self.draw_daily()
            self._render_models()
            self.draw_top()
            self._render_detail()
            self.draw_history()

    def _render_cards(self):
        personal = self.data.get("personal")
        stats = self._stats()
        prev_personal = (self.prev or {}).get("personal")
        if personal and stats:
            delta = personal["today"] - prev_personal["today"] if prev_personal else 0
            today_sub = tr("since", n=fmt(delta)) if delta > 0 else ""
            self._set_card("today", label=f"{tr('today')} ({datetime.now():%H:%M})",
                           value=fmt(personal["today"]), sub=today_sub, sub_good=True if today_sub else None)
            self._set_card("rate", value=fmt(personal["rate"]),
                           sub=f"in {fmt(personal['rate_in'])} \u00B7 out {fmt(personal['rate_out'])}")
            change = ""
            good = None
            if stats["day_before"]:
                pct = 100 * (stats["yesterday"] - stats["day_before"]) / stats["day_before"]
                arrow = "\u25B2" if pct >= 0 else "\u25BC"
                change = f"{arrow} {abs(pct):.0f}%"
                good = pct >= 0
            self._set_card("yesterday", value=fmt(stats["yesterday"]), sub=change, sub_good=good)
            self._set_card("last7", value=fmt(stats["sum7"]), sub=tr("avg_day", n=fmt(stats["sum7"] / 7)))
            self._set_card("last30", value=fmt(stats["sum30"]),
                           sub=tr("avg_day", n=fmt(stats["sum30"] / 30)) + f" \u00B7 {stats['active30']}/30d")
            self._mini_set("today", value=fmt(personal["today"]), sub=today_sub, good=True if today_sub else None)
            self._mini_set("rate", value=fmt(personal["rate"]))

        bits = self._ranking_bits()
        if not bits:
            return
        me, leader, ahead, pace = bits["me"], bits["leader"], bits["ahead"], bits["pace"]
        scoped = self.scope.get("kind") != "all"
        self.mini_status.configure(text=self.status.cget("text") + (f" \u00B7 {bits['scope']}" if scoped else ""))
        if not me:
            scope_total = sum(r["tokens"] or 0 for r in bits["rows"])
            company_me = bits.get("company_me")
            self._set_card("rank", value="\u2014", sub=tr("not_in_scope", scope=bits["scope"]), sub_good=None)
            self._set_card("gap_top", label=f"#1 {leader['user']}", value=fmt(leader["tokens"]),
                           sub=tr("of_scope", n=bits["total"], scope=bits["scope"]))
            self._set_card("gap_next", label=tr("company_rank"),
                           value=f"#{company_me['rank']}" if company_me else "\u2014", sub="")
            self._mini_set("rank", value="\u2014", sub=tr("not_in_scope", scope=bits["scope"]))
            for canvas in (self.progress, self.mini_progress):
                self._draw_progress(canvas, 0)
            self.pace_label.configure(text=tr("scope_leader", scope=bits["scope"], n=bits["total"], user=leader["user"],
                                              tokens=fmt(leader["tokens"]), total=fmt(scope_total)))
            return
        prev_rows = self._scoped((self.prev or {}).get("ranking"))
        prev_me = next((r for r in prev_rows if r["is_me"]), None)
        if bits["guest"]:
            rank_sub = tr("guest_of", n=f"{bits['members']:,}", scope=bits["scope"])
        elif scoped:
            rank_sub = tr("of_scope", n=f"{bits['total']:,}", scope=bits["scope"])
        else:
            rank_sub = tr("of_users", n=f"{bits['total']:,}")
        rank_good = None
        if prev_me and prev_me["rank"] != me["rank"]:
            moved = prev_me["rank"] - me["rank"]
            rank_sub = tr("rank_up" if moved > 0 else "rank_down", n=abs(moved))
            rank_good = moved > 0
        if bits.get("my_recent_rank"):
            rank_sub += f"\n#{bits['my_recent_rank']} \u00B7 {tr('m_recent')}"
        if scoped:
            rank_sub += f"\n{tr('company_rank')} #{me['company_rank']}"
        self._set_card("rank", value=f"#{me['rank']}", sub=rank_sub, sub_good=rank_good)
        self._mini_set("rank", value=f"#{me['rank']}", sub=rank_sub.split("\n")[0], good=rank_good)

        pct = 100 * me["tokens"] / leader["tokens"] if leader["tokens"] else 0
        self._set_card("gap_top", label=f"{tr('gap_top')} {leader['user']}",
                       value=fmt(leader["tokens"] - me["tokens"]) if me["rank"] > 1 else "#1",
                       sub=tr("pct_top", p=f"{pct:.1f}"))
        if ahead:
            text, good = catch_up(ahead["tokens"] - me["tokens"], pace(me), pace(ahead))
            label = f"{tr('gap_next', r=ahead['rank'])} {ahead['user']}"
            self._set_card("gap_next", label=label, value=fmt(ahead["tokens"] - me["tokens"]), sub=text, sub_good=good)
            self._mini_set("gap_next", label=label, value=fmt(ahead["tokens"] - me["tokens"]), sub=text, good=good)
        else:
            # I'm #1 in this scope: show my lead over #2 (and whether they are closing in)
            runner_up = bits["behind"]
            if runner_up:
                lead = me["tokens"] - runner_up["tokens"]
                eta, closing = catch_up(lead, pace(runner_up), pace(me))
                sub = tr("pace_behind", r=2, user=runner_up["user"], gap=fmt(lead), theirs=fmt(pace(runner_up)),
                         eta=eta if closing else tr("safe")).split("\u2192")[-1].strip()
                label = tr("lead", user=runner_up["user"])
                self._set_card("gap_next", label=label, value=fmt(lead), sub=sub, sub_good=not closing)
                self._mini_set("gap_next", label=label, value=fmt(lead), sub=sub, good=not closing)
            else:
                self._set_card("gap_next", label=tr("top_spot"), value="#1", sub="", sub_good=None)
                self._mini_set("gap_next", label=tr("top_spot"), value="#1", sub="")
        if me["rank"] == 1:
            self._set_card("gap_top", label=tr("top_spot"), value="#1", sub=tr("of_scope", n=bits["total"], scope=bits["scope"])
                           if self.scope.get("kind") != "all" else tr("of_users", n=f"{bits['total']:,}"))
        for canvas in (self.progress, self.mini_progress):
            self._draw_progress(canvas, pct)
        self.mini_progress_label.configure(text=f"{tr('progress')} \u00B7 {pct:.1f}%")

        lines = []
        if me["rank"] > 1:
            eta_top, _good = catch_up(leader["tokens"] - me["tokens"], pace(me), pace(leader))
            lines.append(tr("pace_line", d=bits["pace_days"], mine=fmt(pace(me)), leader=leader["user"],
                            theirs=fmt(pace(leader)), eta=eta_top))
        if ahead:
            eta, _g = catch_up(ahead["tokens"] - me["tokens"], pace(me), pace(ahead))
            lines.append(tr("pace_next", r=ahead["rank"], user=ahead["user"], theirs=fmt(pace(ahead)), eta=eta))
        behind = bits["behind"]
        if behind:
            eta, closing = catch_up(me["tokens"] - behind["tokens"], pace(behind), pace(me))
            lines.append(tr("pace_behind", r=behind["rank"], user=behind["user"],
                            gap=fmt(me["tokens"] - behind["tokens"]), theirs=fmt(pace(behind)),
                            eta=eta if closing else tr("safe")))
        cutoff = bits["cutoff"]
        if cutoff:
            need = cutoff["tokens"] - me["tokens"]
            top_n = int(self.data["conf"]["top_n"])
            lines.append(f"{tr('cutoff', n=top_n)}: {fmt(cutoff['tokens'])} \u00B7 "
                         + (tr("in_top", n=top_n) if need <= 0 else tr("need", n=fmt(need))))
        self.pace_label.configure(text="\n".join(lines))

    def _mini_set(self, key, label=None, value=None, sub=None, good=None):
        widgets = self.mini_values.get(key)
        if not widgets:
            return
        if label is not None:
            widgets[0].configure(text=label)
        if value is not None:
            widgets[1].configure(text=value)
        if sub is not None:
            widgets[2].configure(text=sub, fg=self.p["muted"] if good is None else (CHART["good"] if good else CHART["bad"]))

    def _draw_progress(self, canvas, pct):
        canvas.delete("all")
        width = max(canvas.winfo_width(), 100)
        canvas.create_rectangle(0, 1, width, 9, fill=self.p["soft"], width=0)
        canvas.create_rectangle(0, 1, max(3, width * min(pct, 100) / 100), 9, fill=CHART["me"], width=0)

    def _render_models(self):
        for child in self.models_frame.winfo_children():
            child.destroy()
        personal = self.data.get("personal")
        models = personal["models"] if personal else []
        total = sum(m["total"] for m in models) or 1
        if not models:
            self.tk.Label(self.models_frame, text=tr("no_usage"), bg=self.p["card"], fg=self.p["muted"],
                          font=("Segoe UI", 9)).pack(anchor="w")
            return
        palette = ["#5794F2", "#73BF69", "#FF9830", "#B877D9", "#E0A82E", "#8AB8FF"]
        for index, model in enumerate(models[:7]):
            row = self.tk.Frame(self.models_frame, bg=self.p["card"])
            row.pack(fill="x", pady=2)
            share = model["total"] / total
            self.tk.Label(row, text=model["model"], bg=self.p["card"], fg=self.p["ink"],
                          font=("Segoe UI", 9), anchor="w").pack(fill="x")
            bar = self.tk.Canvas(row, height=12, bg=self.p["card"], highlightthickness=0)
            bar.pack(fill="x")
            bar.update_idletasks()
            width = max(bar.winfo_width(), 200)
            bar.create_rectangle(0, 2, width - 90, 10, fill=self.p["soft"], width=0)
            bar.create_rectangle(0, 2, max(2, (width - 90) * share), 10, fill=palette[index % len(palette)], width=0)
            bar.create_text(width - 2, 6, anchor="e", text=f"{fmt(model['total'])} \u00B7 {share * 100:.0f}%",
                            fill=self.p["muted"], font=("Segoe UI", 8))

    # ---- tooltip helper -------------------------------------------------------
    def _tip(self, canvas, x, y, text):
        canvas.delete("tip")
        item = canvas.create_text(x + 14, y + 14, text=text, anchor="nw", fill=self.p["ink"],
                                  font=("Segoe UI", 9), tags="tip")
        x0, y0, x1, y1 = canvas.bbox(item)
        right = canvas.canvasx(canvas.winfo_width())
        bottom = canvas.canvasy(canvas.winfo_height())
        dx = (right - 4 - x1) if x1 > right - 4 else 0
        dy = (y - 14 - y1) if y1 > bottom - 4 else 0
        canvas.move(item, dx, dy)
        x0, y0, x1, y1 = canvas.bbox(item)
        box = canvas.create_rectangle(x0 - 6, y0 - 4, x1 + 6, y1 + 4, fill=self.p["bg"],
                                      outline=self.p["border"], tags="tip")
        canvas.tag_raise(item, box)

    @staticmethod
    def _nice_max(value):
        if value <= 0:
            return 1
        exponent = 10 ** math.floor(math.log10(value))
        for step in (1, 2, 2.5, 5, 10):
            if value <= step * exponent:
                return step * exponent
        return 10 * exponent

    # ---- daily chart ----------------------------------------------------------
    def draw_daily(self):
        canvas = self.daily_canvas
        canvas.delete("all")
        personal = (self.data or {}).get("personal")
        if not personal:
            return
        series = personal["series"]
        values_all = [v for _d, v in series]
        start = max(0, len(series) - self.range_days)
        rows = series[start:]
        averages = []
        for index in range(start, len(series)):
            window = values_all[max(0, index - 6): index + 1]
            averages.append(sum(window) / len(window))
        width, height = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 150)
        left, right, top, bottom = 56, 10, 10, 22
        plot_h = height - top - bottom
        vmax = self._nice_max(max([v for _d, v in rows] + averages + [1]))
        for step in range(5):
            y = top + plot_h * step / 4
            canvas.create_line(left, y, width - right, y, fill=self.p["soft"])
            canvas.create_text(left - 6, y, text=fmt(vmax * (4 - step) / 4), anchor="e",
                               fill=self.p["muted"], font=("Segoe UI", 8))
        slot = (width - left - right) / max(1, len(rows))
        today = date.today().isoformat()
        label_every = max(1, math.ceil(len(rows) / 12))
        for index, (day, value) in enumerate(rows):
            x0 = left + index * slot + slot * 0.15
            x1 = left + (index + 1) * slot - slot * 0.15
            y = top + plot_h * (1 - value / vmax)
            canvas.create_rectangle(x0, y, x1, top + plot_h, width=0,
                                    fill=CHART["today"] if day == today else CHART["bar"])
            if index % label_every == 0 or index == len(rows) - 1:
                canvas.create_text((x0 + x1) / 2, height - 10, text=day[5:], fill=self.p["muted"],
                                   font=("Segoe UI", 7))
        points = []
        for index, average in enumerate(averages):
            points += [left + (index + 0.5) * slot, top + plot_h * (1 - average / vmax)]
        if len(points) >= 4:
            canvas.create_line(*points, fill=CHART["avg"], width=2, smooth=True)
        self._geom["daily"] = (left, slot, rows, averages, today)

    def _daily_hover(self, event):
        geom = self._geom.get("daily")
        if not geom:
            return
        left, slot, rows, averages, today = geom
        index = int((event.x - left) // slot) if slot else -1
        if not 0 <= index < len(rows):
            self.daily_canvas.delete("tip")
            return
        day, value = rows[index]
        label = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
        if day == today:
            label += " (today, partial)"
        self._tip(self.daily_canvas, event.x, event.y,
                  f"{label}\n{fmt(value)}  ({value:,.0f})\n7-day avg {fmt(averages[index])}")

    # ---- top-N chart ----------------------------------------------------------
    def _metric(self, row):
        if self.metric == "recent":
            return row["recent"] or 0
        if self.metric == "perday":
            return (row["tokens"] / row["days"]) if row["days"] else 0
        return row["tokens"] or 0

    def _top_rows(self):
        bits = self._ranking_bits()
        if not bits:
            return []
        top_n = int(self.data["conf"]["top_n"])
        rows = [r for r in bits["rows"] if r["rank"] <= top_n]
        rows.sort(key=self._metric, reverse=True)
        rows = rows[: self.show_n]
        if bits["me"] and not any(r["is_me"] for r in rows):
            rows.append(bits["me"])
        return rows

    def draw_top(self):
        canvas = self.top_canvas
        canvas.delete("all")
        rows = self._top_rows()
        if not rows:
            return
        conf = self.data["conf"]
        range_label = str(conf["long_range"]).replace("now-", "")
        if self.scope.get("kind") == "all":
            self.top_title.configure(text=tr("top", n=self.show_n, range=range_label))
        else:
            self.top_title.configure(text=tr("top_scoped", n=self.show_n, scope=scope_name(self.scope), range=range_label))
        width = max(canvas.winfo_width(), 300)
        left, right, row_h, top = 150, 70, 20, 6
        vmax = self._nice_max(max(self._metric(r) for r in rows) or 1)
        plot_w = width - left - right
        for index, row in enumerate(rows):
            y = top + index * row_h
            value = self._metric(row)
            rank_label = row["rank"] if self.metric == "tokens" else index + 1
            name = f"{rank_label}. {row['user']}" + (f" ({tr('guest')})" if row.get("guest") else "")
            color = CHART["me"] if row["is_me"] else (CHART["selected"] if row["user"] == self.selected else CHART["bar"])
            weight = "Segoe UI Semibold" if row["is_me"] or row["user"] == self.selected else "Segoe UI"
            canvas.create_text(left - 6, y + row_h / 2, text=name, anchor="e", fill=self.p["ink"], font=(weight, 9))
            canvas.create_rectangle(left, y + 3, left + max(2, plot_w * value / vmax), y + row_h - 3,
                                    fill=color, width=0)
            canvas.create_text(left + max(2, plot_w * value / vmax) + 4, y + row_h / 2, text=fmt(value),
                               anchor="w", fill=self.p["muted"], font=("Segoe UI", 8))
        canvas.configure(scrollregion=(0, 0, width, top * 2 + len(rows) * row_h))
        self._geom["top"] = (top, row_h, rows)

    def _top_row_at(self, event):
        geom = self._geom.get("top")
        if not geom:
            return None
        top, row_h, rows = geom
        index = int((self.top_canvas.canvasy(event.y) - top) // row_h)
        return rows[index] if 0 <= index < len(rows) else None

    def _top_hover(self, event):
        row = self._top_row_at(event)
        if not row:
            self.top_canvas.delete("tip")
            return
        bits = self._ranking_bits()
        me = bits["me"] if bits else None
        lines = [f"#{row['rank']} {row['user']} \u00B7 {row['level1']}",
                 f"{tr('d_total')}: {fmt(row['tokens'])} \u00B7 {row['days']}d \u00B7 {row['uses']:,} uses",
                 f"{tr('d_recent', d=bits['pace_days'])}: {fmt(row['recent'])}"]
        if self.scope.get("kind") != "all":
            lines.append(f"{tr('company_rank')}: #{row['company_rank']}")
        if me and not row["is_me"]:
            gap = row["tokens"] - me["tokens"]
            lines.append(f"{tr('d_ahead') if gap > 0 else tr('d_behind')} {fmt(abs(gap))}")
        self._tip(self.top_canvas, self.top_canvas.canvasx(event.x), self.top_canvas.canvasy(event.y), "\n".join(lines))

    def _top_click(self, event):
        row = self._top_row_at(event)
        if row:
            self.selected = row["user"]
            self.draw_top()
            self._render_detail()

    def _render_detail(self):
        for child in self.detail_frame.winfo_children():
            child.destroy()
        bits = self._ranking_bits()
        if not bits:
            return
        me, leader, pace = bits["me"], bits["leader"], bits["pace"]
        row = next((r for r in bits["rows"] if r["user"] == self.selected), None) or me or leader
        p = self.p
        title = f"#{row['rank']} {row['user']}" + (
            f" ({tr('guest') if row.get('guest') else tr('me')})" if row["is_me"] else "")
        self.tk.Label(self.detail_frame, text=title, bg=p["card"], fg=p["accent"],
                      font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x")
        self.tk.Label(self.detail_frame, text=row["unit"], bg=p["card"], fg=p["muted"], font=("Segoe UI", 8),
                      anchor="w", justify="left", wraplength=290).pack(fill="x", pady=(0, 4))
        pairs = [
            (tr("d_total"), fmt(row["tokens"]), None),
        ]
        if self.scope.get("kind") != "all":
            pairs.append((tr("company_rank"), f"#{row['company_rank']}", None))
        pairs += [
            (tr("d_pct"), f"{100 * row['tokens'] / (leader['tokens'] or 1):.1f}%", None),
            (tr("d_uses"), f"{row['uses']:,}", None),
            (tr("d_days"), str(row["days"]), None),
            (tr("d_perday"), fmt(row["tokens"] / row["days"] if row["days"] else 0), None),
            (tr("d_recent", d=bits["pace_days"]), f"{fmt(row['recent'])} ({fmt(pace(row))}/d)", None),
        ]
        if me and not row["is_me"]:
            gap = row["tokens"] - me["tokens"]
            if gap > 0:
                eta, good = catch_up(gap, pace(me), pace(row))
                pairs += [(tr("d_ahead"), fmt(gap), None), (tr("d_catch"), eta, good)]
            else:
                eta, closing = catch_up(-gap, pace(row), pace(me))
                pairs += [(tr("d_behind"), fmt(-gap), None),
                          (tr("d_caught"), eta if closing else tr("safe"), not closing)]
            pairs.append((tr("d_pace"), f"{fmt(pace(row))} vs {fmt(pace(me))}", None))
        grid = self.tk.Frame(self.detail_frame, bg=p["card"])
        grid.pack(fill="x")
        for index, (label, value, good) in enumerate(pairs):
            color = p["ink"] if good is None else (CHART["good"] if good else CHART["bad"])
            self.tk.Label(grid, text=label, bg=p["card"], fg=p["muted"], font=("Segoe UI", 9),
                          anchor="w").grid(row=index, column=0, sticky="w")
            self.tk.Label(grid, text=value, bg=p["card"], fg=color, font=("Segoe UI Semibold", 9),
                          anchor="e").grid(row=index, column=1, sticky="e")
        grid.grid_columnconfigure(1, weight=1)

    # ---- rank history (v2.29: back-filled, time axis, log rank scale) --------
    def _history_window(self):
        """(points, t0, t1, pre_first_ts) for the chosen range."""
        history = self.rank_history or {}
        points = merged_history(history, (self.data or {}).get("snapshots"))
        now = time.time()
        if self.hist_range == "all":
            floor = now - 365 * HISTORY_YEARS * 86400
            starts = [t for t in (history.get("data_start"), points[0]["t"] if points else None) if t]
            t0 = max(floor, min(starts)) if starts else floor
        else:
            t0 = now - int(self.hist_range) * 86400
        visible = [pt for pt in points if pt["t"] >= t0]
        # carry the last point before the window in so the line starts at the left edge
        before = [pt for pt in points if pt["t"] < t0]
        if before:
            visible.insert(0, dict(before[-1], t=t0))
        return visible, t0, now, history.get("first_seen")

    @staticmethod
    def _time_ticks(t0, t1, plot_w):
        span_days = (t1 - t0) / 86400
        start = datetime.fromtimestamp(t0)
        ticks = []
        if span_days > 60:
            months = 1
            while span_days / 30.4 / months * 46 > plot_w and months < 24:
                months = {1: 2, 2: 3, 3: 6, 6: 12}.get(months, 24)
            year, month = start.year, start.month + 1
            while True:
                if month > 12:
                    year, month = year + 1, month - 12
                moment = datetime(year, month, 1)
                if moment.timestamp() > t1:
                    break
                if (month - 1) % months == 0:
                    label = moment.strftime("%b %y") if month == 1 or months >= 3 or not ticks else moment.strftime("%b")
                    ticks.append((moment.timestamp(), label))
                month += 1
        else:
            step = 1
            while span_days / step * 40 > plot_w:
                step = {1: 2, 2: 7, 7: 14}.get(step, 30)
            day = start.date() + timedelta(days=1)
            if step >= 7:
                day += timedelta(days=(7 - day.weekday()) % 7)
            while datetime.combine(day, dtime()).timestamp() <= t1:
                ticks.append((datetime.combine(day, dtime()).timestamp(), day.strftime("%m/%d")))
                day += timedelta(days=step)
        return ticks

    def draw_history(self):
        canvas = self.history_canvas
        canvas.delete("all")
        self._geom["history"] = None
        p = self.p
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 100)
        left, right, top, bottom = 38, 10, 10, 18
        plot_w, plot_h = width - left - right, height - top - bottom
        if self._history_progress:
            done, total = self._history_progress
            status = tr("hist_loading", d=done, n=total) if total else tr("hist_searching")
            canvas.create_text(width - right, top, text=status, anchor="ne", fill=p["muted"],
                               font=("Segoe UI", 8, "italic"))
        points, t0, t1, first_seen = self._history_window()
        if not points:
            return
        ranks = [pt["rank"] for pt in points]
        low, high = min(ranks), max(ranks)
        if low == high:
            low, high = max(1, low - 1), high + 1
        log_low, log_high = math.log(low), math.log(high)

        def x_of(t):
            return left + plot_w * (t - t0) / max(1.0, t1 - t0)

        def y_of(rank):
            return top + plot_h * (math.log(rank) - log_low) / (log_high - log_low)

        # "not ranked yet" stretch before I first appeared
        if first_seen and first_seen > t0:
            x_end = min(x_of(first_seen), left + plot_w)
            canvas.create_rectangle(left, top, x_end, top + plot_h, fill=p["soft"], outline="")
            if x_end - left > 70:
                canvas.create_text((left + x_end) / 2, top + plot_h / 2, text=tr("hist_before"),
                                   fill=p["muted"], font=("Segoe UI", 8, "italic"))
        # rank grid (log scale): 1-2-3-5 steps that fit, plus the latest rank
        grid = [r for r in (1, 2, 3, 5, 10, 20, 30, 50, 100, 200, 300, 500, 1000, 2000, 3000) if low <= r <= high]
        labels, last_y = [], None
        for rank in sorted(set(grid) | {low, high}):
            y = y_of(rank)
            if last_y is None or abs(y - last_y) >= 13:
                labels.append(rank)
                last_y = y
        for rank in labels:
            canvas.create_line(left, y_of(rank), width - right, y_of(rank), fill=p["soft"])
            canvas.create_text(left - 4, y_of(rank), text=f"#{rank}", anchor="e", fill=p["muted"],
                               font=("Segoe UI", 8))
        top_n = int(settings(self.cfg)["top_n"])
        if low <= top_n <= high:
            y = y_of(top_n)
            canvas.create_line(left, y, width - right, y, fill=CHART["good"], dash=(3, 3))
            canvas.create_text(width - right - 2, y - 2, text=tr("cutoff", n=top_n), anchor="se",
                               fill=CHART["good"], font=("Segoe UI", 7))
        # time axis
        for t, label in self._time_ticks(t0, t1, plot_w):
            x = x_of(t)
            canvas.create_line(x, top + plot_h, x, top + plot_h + 3, fill=p["muted"])
            canvas.create_text(x, height - 7, text=label, fill=p["muted"], font=("Segoe UI", 7))
        # the line: back-filled days solid, live refresh snapshots as dots
        coords = []
        for pt in points:
            coords += [x_of(pt["t"]), y_of(pt["rank"])]
        if len(coords) >= 4:
            canvas.create_line(*coords, fill=CHART["me"], width=2)
        dot = 2 if len(points) > 60 else 3
        for pt in points:
            x, y = x_of(pt["t"]), y_of(pt["rank"])
            canvas.create_oval(x - dot, y - dot, x + dot, y + dot, fill=CHART["me"], outline="")
        best = min(points, key=lambda pt: (pt["rank"], -pt["t"]))
        x, y = x_of(best["t"]), y_of(best["rank"])
        canvas.create_oval(x - 4, y - 4, x + 4, y + 4, outline=CHART["good"], width=2)
        self._geom["history"] = ([x_of(pt["t"]) for pt in points], points)

    def _history_hover(self, event):
        geom = self._geom.get("history")
        if not geom:
            self.history_canvas.delete("tip")
            return
        xs, points = geom
        index = min(range(len(xs)), key=lambda i: abs(xs[i] - event.x))
        if abs(xs[index] - event.x) > 40:
            self.history_canvas.delete("tip")
            return
        pt = points[index]
        stamp = "%a %d %b %Y %H:%M" if pt.get("live") else "%a %d %b %Y"
        rank_text = tr("hist_of", r=pt["rank"], n=f"{pt['users']:,}") if pt.get("users") else f"#{pt['rank']}"
        self._tip(self.history_canvas, event.x, event.y,
                  f"{datetime.fromtimestamp(pt['t']).strftime(stamp)}\n{rank_text} \u00B7 {fmt(pt['tokens'])}"
                  f"\n{tr('gap_top')}: {fmt(pt['gap'])}")

    # ---- mini mode ------------------------------------------------------------
    def draw_mini(self):
        canvas = self.mini_canvas
        canvas.delete("all")
        personal = (self.data or {}).get("personal")
        if not personal:
            return
        rows = personal["series"][-14:]
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 80)
        vmax = max([v for _d, v in rows] + [1])
        slot = (width - 8) / len(rows)
        today = date.today().isoformat()
        for index, (day, value) in enumerate(rows):
            x0 = 4 + index * slot + slot * 0.15
            x1 = 4 + (index + 1) * slot - slot * 0.15
            y = 6 + (height - 22) * (1 - value / vmax)
            canvas.create_rectangle(x0, y, x1, height - 16, width=0,
                                    fill=CHART["today"] if day == today else CHART["bar"])
        canvas.create_text(4, height - 7, text=rows[0][0][5:], anchor="w", fill=self.p["muted"], font=("Segoe UI", 7))
        canvas.create_text(width - 4, height - 7, text=rows[-1][0][5:], anchor="e", fill=self.p["muted"],
                           font=("Segoe UI", 7))
        self._geom["mini"] = (slot, rows)

    def _mini_hover(self, event):
        geom = self._geom.get("mini")
        if not geom:
            return
        slot, rows = geom
        index = int((event.x - 4) // slot)
        if 0 <= index < len(rows):
            day, value = rows[index]
            self._tip(self.mini_canvas, min(event.x, 120), 2, f"{day[5:]}  {fmt(value)}")
        else:
            self.mini_canvas.delete("tip")

    def _render_neighbors(self):
        for child in self.neighbors.winfo_children():
            child.destroy()
        bits = self._ranking_bits()
        if not bits:
            return
        me = bits["me"]
        if me:
            shown = [r for r in bits["rows"] if abs(r["rank"] - me["rank"]) <= 1 or r["rank"] == 1]
        else:
            shown = bits["rows"][:4]           # not in this scope: show its leaders
        for row in shown:
            bg = self.p["warm"] if row["is_me"] else self.p["card"]
            line = self.tk.Frame(self.neighbors, bg=bg)
            line.pack(fill="x")
            font = ("Segoe UI Semibold", 9) if row["is_me"] else ("Segoe UI", 9)
            self.tk.Label(line, text=f"#{row['rank']} {row['user']}", bg=bg, fg=self.p["ink"], font=font,
                          anchor="w").pack(side="left", padx=6, pady=1)
            self.tk.Label(line, text=fmt(row["tokens"]), bg=bg, fg=self.p["muted"], font=font).pack(side="right", padx=6)

    # ---- lifecycle --------------------------------------------------------------
    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except Exception:
            return False

    def show(self):
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        self.top.attributes("-topmost", self.on_top)
        stale = not self.data or (datetime.now() - self.data["fetched_at"]).total_seconds() > 60
        if stale:
            self.refresh()


def open_window(master, cfg: dict, notify=None) -> TokenBoardWindow:
    window = TokenBoardWindow(master, cfg, notify=notify)
    window.show()
    return window


if __name__ == "__main__":  # standalone preview: python token_board.py [--dump]
    import sys
    import tkinter

    logging.basicConfig(level=logging.INFO)
    try:
        import tracker
        config = tracker.load_config()
    except Exception:
        config = _read_json(BASE_DIR / "config.json", {})
    if "--dump" in sys.argv:
        result = collect(config)
        me_row = (result["ranking"] or {}).get("me")
        print({"errors": result["errors"], "elapsed": result["elapsed"],
               "today": (result["personal"] or {}).get("today"), "me": me_row})
    else:
        root = tkinter.Tk()
        root.withdraw()
        board = open_window(root, config)
        board.top.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()
