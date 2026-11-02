"""diary_email.py -- email the productivity diary as ONE growing conversation.

The first send is a brand-new mail (the "root"). Every later send is a *reply*
to that root, so the whole diary history stays in a single Outlook thread
instead of a new mail every day.

Outlook is driven directly through COM (``win32com``) -- the same mechanism the
``outlook-agent`` skill uses -- because a threaded reply also needs an
attachment and the skill's reply command has no attachment support.

State (``data/diary_email_state.json``):
    { "to": "...", "subject": "Productivity Diary",
      "root_entry_id": "...", "created_at": "...", "last_sent": "...",
      "sent_count": N }

Nothing is ever raised to the caller: failures log to ``logs/diary.log`` and
return ``{"status": "error"/"skipped", ...}``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOG_FILE = HERE / "logs" / "diary.log"
STATE_FILE = HERE / "data" / "diary_email_state.json"

OL_FOLDER_SENT = 5  # olFolderSentMail


def log(msg: str) -> None:
    (HERE / "logs").mkdir(parents=True, exist_ok=True)
    line = "[%s] %s" % (dt.datetime.now().isoformat(timespec="seconds"), msg)
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    try:
        with STATE_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError:
        log("diary_email: could not write state file")


def reset_thread() -> None:
    """Forget the current thread so the next send starts a fresh mail."""
    _save_state({})
    log("diary_email: thread state cleared")


def _resolve_settings(cfg: dict, to=None, subject=None):
    diary_cfg = cfg.get("diary", {}) or {}
    mail_cfg = diary_cfg.get("email", {}) or {}
    top_mail = cfg.get("email", {}) or {}
    to_addr = (to or mail_cfg.get("to") or top_mail.get("to") or "").strip()
    subj = (subject or mail_cfg.get("subject") or "Productivity Diary").strip()
    return to_addr, subj


def _search_sent_once(ns, want: str) -> str:
    try:
        sent = ns.GetDefaultFolder(OL_FOLDER_SENT)
        items = sent.Items
    except Exception:
        return ""
    try:
        count = int(items.Count)
    except Exception:
        count = 0
    try:
        items.Sort("[SentOn]", True)
        for i in range(1, min(count, 20) + 1):
            try:
                m = items.Item(i)
                if (m.Subject or "").strip() == want:
                    return str(m.EntryID or "")
            except Exception:
                continue
    except Exception:
        pass
    for i in range(count, max(0, count - 40), -1):
        try:
            m = items.Item(i)
            if (m.Subject or "").strip() == want:
                return str(m.EntryID or "")
        except Exception:
            continue
    return ""


def _find_sent_entry(ns, subject: str, attempts: int = 10, delay: float = 1.0) -> str:
    """EntryID of the mail we just sent. Send is async, so retry briefly."""
    import time

    want = subject.strip()
    for _ in range(max(1, attempts)):
        entry = _search_sent_once(ns, want)
        if entry:
            return entry
        time.sleep(delay)
    return ""


def adopt_existing_root(cfg: dict, subject=None) -> str:
    """Recover a previously-sent diary mail as the thread root (idempotent helper)."""
    to_addr, subj = _resolve_settings(cfg, subject=subject)
    try:
        import win32com.client

        ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    except Exception:
        return ""
    entry = _find_sent_entry(ns, subj, attempts=3, delay=0.5)
    if not entry:
        return ""
    now = dt.datetime.now().isoformat(timespec="seconds")
    state = _load_state()
    state.setdefault("to", to_addr)
    state["subject"] = subj
    state["root_entry_id"] = entry
    state.setdefault("created_at", now)
    state.setdefault("sent_count", 1)
    _save_state(state)
    log("diary_email: adopted existing root %s" % entry[:24])
    return entry


def _rotation_due(state: dict, cfg: dict):
    email_cfg = ((cfg.get("diary", {}) or {}).get("email", {}) or {})
    max_emails = int(email_cfg.get("max_emails_per_thread") or 120)
    max_days = int(email_cfg.get("max_thread_days") or 180)
    count = int(state.get("sent_count", 0) or 0)
    if count >= max_emails:
        return True, "sent_count=%d>=%d" % (count, max_emails)
    created = state.get("created_at")
    if created:
        try:
            age = (dt.datetime.now() - dt.datetime.fromisoformat(created)).days
        except ValueError:
            age = 0
        if age >= max_days:
            return True, "age=%dd>=%d" % (age, max_days)
    return False, ""


def send_diary_email(cfg: dict, body: str, pdf_path: str = "", to=None, subject=None) -> dict:
    """Send (or reply) the diary email. Returns a status dict; never raises."""
    to_addr, subj = _resolve_settings(cfg, to=to, subject=subject)
    if not to_addr:
        log("diary_email: skipped - no recipient configured")
        return {"status": "skipped", "message": "no recipient"}

    try:
        import win32com.client  # noqa: F401  (presence check)
    except Exception:
        log("diary_email: skipped - pywin32/Outlook COM unavailable")
        return {"status": "skipped", "message": "win32com unavailable"}

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
    except Exception:
        log("diary_email: skipped - cannot start Outlook\n" + traceback.format_exc())
        return {"status": "skipped", "message": "Outlook unavailable"}

    state = _load_state()
    root = (state.get("root_entry_id") or "").strip()
    now = dt.datetime.now().isoformat(timespec="seconds")
    attachment = pdf_path if (pdf_path and os.path.exists(pdf_path)) else ""

    if root:
        due, why = _rotation_due(state, cfg)
        if due:
            log("diary_email: rotating thread (%s)" % why)
            history = list(state.get("history") or [])
            history.append({
                "root_entry_id": root,
                "created_at": state.get("created_at"),
                "sent_count": state.get("sent_count"),
            })
            state = {"history": history}
            _save_state(state)
            root = ""

    # ---- try to reply into the existing thread -------------------------------
    if root:
        try:
            original = ns.GetItemFromID(root)
            reply = original.Reply()
            existing = ""
            try:
                existing = reply.Body or ""
            except Exception:
                existing = ""
            reply.Body = (body + "\n\n" + existing) if body else existing
            if attachment:
                reply.Attachments.Add(os.path.abspath(attachment))
            reply.Send()
            state["last_sent"] = now
            state["sent_count"] = int(state.get("sent_count", 0)) + 1
            _save_state(state)
            log("diary_email: reply sent to thread (%s)" % root[:24])
            return {"status": "sent_reply", "entry_id": root}
        except Exception as exc:
            log("diary_email: reply failed (%s) - starting a new thread" % exc)
            root = ""

    # ---- start a new thread --------------------------------------------------
    thread_subject = "%s (%s)" % (subj, dt.date.today().isoformat())
    try:
        mail = outlook.CreateItem(0)
        mail.To = to_addr
        mail.Subject = thread_subject
        mail.Body = body
        if attachment:
            mail.Attachments.Add(os.path.abspath(attachment))
        mail.Send()
    except Exception:
        log("diary_email: FATAL new mail failed\n" + traceback.format_exc())
        return {"status": "error", "message": "send failed"}

    entry_id = _find_sent_entry(ns, thread_subject)
    state = {
        "to": to_addr,
        "subject": thread_subject,
        "root_entry_id": entry_id,
        "created_at": now,
        "last_sent": now,
        "sent_count": 1,
        "history": list(state.get("history") or []),
    }
    _save_state(state)
    if entry_id:
        log("diary_email: new thread started (root %s)" % entry_id[:24])
    else:
        log("diary_email: new mail sent but root EntryID not found - next run will start another thread")
    return {"status": "sent_new", "entry_id": entry_id}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Send the productivity diary email")
    parser.add_argument("--to", default=None, help="override recipient")
    parser.add_argument("--subject", default=None, help="override subject")
    parser.add_argument("--body", default="Test diary email.", help="plain-text body")
    parser.add_argument("--pdf", default=None, help="PDF to attach")
    parser.add_argument("--reset", action="store_true", help="clear the thread state (next send starts fresh)")
    args = parser.parse_args(argv)

    if args.reset:
        reset_thread()
        return 0

    import tracker

    cfg = tracker.load_config()
    result = send_diary_email(cfg, args.body, pdf_path=args.pdf or "", to=args.to, subject=args.subject)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status", "").startswith("sent") else 1


if __name__ == "__main__":
    raise SystemExit(main())
