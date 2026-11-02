"""
outreach.py - reach Pips users outside the app (v2.15, staff update reminders).

* detect_work_email()  the Outlook account e-mail of this Windows user (read from the
                       registry - Outlook is not started).
* send_mail()          send through the Outlook desktop app (COM); raises OutreachError.
* open_mail_draft()    mailto: fallback - the default mail app opens a ready draft.
* open_teams_chat()    Microsoft Teams 1:1 chat with the message ready (msteams: deep link).
* resolve_address()    look a name up in the Outlook address book -> SMTP address.
* reminder_*()         the update reminder text (e-mail HTML / plain text).

The e-mail is in English, Bahasa Indonesia and Chinese so every colleague can read it;
the short Teams message comes from i18n (the sender's language).
"""

from __future__ import annotations

import html
import os
import re
import urllib.parse
import webbrowser

SITE_URL = "https://juw12.github.io/projects/pips/"
SETUP_URL = SITE_URL + "download/Pips-Setup.exe"
EMAIL_RE = re.compile(r"^[^\s@<>\"',;:()\[\]]+@[^\s@<>\"',;:()\[\]]+\.[A-Za-z]{2,}$")
OUTLOOK_ACCOUNTS_KEY = "9375CFF0413111d3B88A00104B2A6676"


class OutreachError(Exception):
    """code: no_outlook | send_failed | not_found"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def valid_email(text) -> bool:
    return isinstance(text, str) and len(text.strip()) <= 120 and bool(EMAIL_RE.match(text.strip()))


# ---------------------------------------------------------------- who am I (work e-mail)
_detected: str | None = None


def detect_work_email() -> str:
    """E-mail of the Outlook account on this PC ('' if Outlook isn't set up). Cached."""
    global _detected
    if _detected is None:
        try:
            _detected = _outlook_account_email()
        except Exception:
            _detected = ""
    return _detected


def _subkeys(key) -> list:
    import winreg
    names = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            return names
        index += 1


def _outlook_account_email() -> str:
    import winreg
    for office in ("16.0", "15.0"):
        root = rf"Software\Microsoft\Office\{office}\Outlook"
        default = ""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as key:
                default = str(winreg.QueryValueEx(key, "DefaultProfile")[0])
        except OSError:
            pass
        try:
            profiles = winreg.OpenKey(winreg.HKEY_CURRENT_USER, root + r"\Profiles")
        except OSError:
            continue
        with profiles:
            names = sorted(_subkeys(profiles), key=lambda name: name != default)   # default profile first
            for profile in names:
                try:
                    accounts = winreg.OpenKey(profiles, profile + "\\" + OUTLOOK_ACCOUNTS_KEY)
                except OSError:
                    continue
                with accounts:
                    for sub in _subkeys(accounts):
                        try:
                            with winreg.OpenKey(accounts, sub) as account:
                                value = winreg.QueryValueEx(account, "Account Name")[0]
                        except OSError:
                            continue
                        if isinstance(value, bytes):
                            value = value.decode("utf-16-le", "ignore")
                        value = str(value).replace("\x00", "").strip()
                        if valid_email(value):
                            return value
    return ""


# ---------------------------------------------------------------- Outlook
def _outlook():
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise OutreachError("no_outlook") from exc
    pythoncom.CoInitialize()
    try:
        return pythoncom, win32com.client.Dispatch("Outlook.Application")
    except Exception as exc:
        pythoncom.CoUninitialize()
        raise OutreachError("no_outlook") from exc


def send_mail(to, subject: str, html_body: str, bcc=()) -> None:
    """Send right away through Outlook (call from a worker thread)."""
    to = [a for a in to if a]
    bcc = [a for a in bcc if a]
    if not to and not bcc:
        raise OutreachError("send_failed")
    pythoncom, outlook = _outlook()
    try:
        mail = outlook.CreateItem(0)
        if to:
            mail.To = "; ".join(to)
        if bcc:
            mail.BCC = "; ".join(bcc)
        mail.Subject = subject
        mail.HTMLBody = html_body
        try:
            mail.Recipients.ResolveAll()
        except Exception:
            pass
        try:
            mail.Send()
        except Exception as exc:
            raise OutreachError("send_failed") from exc
    finally:
        pythoncom.CoUninitialize()


def resolve_address(query: str):
    """Outlook address book lookup -> (smtp, display name) or None."""
    query = (query or "").strip()
    if not query:
        return None
    pythoncom, outlook = _outlook()
    try:
        recipient = outlook.Session.CreateRecipient(query)
        if not recipient.Resolve():
            return None
        entry = recipient.AddressEntry
        try:
            user = entry.GetExchangeUser()
        except Exception:
            user = None
        if user is not None and valid_email(user.PrimarySmtpAddress or ""):
            return user.PrimarySmtpAddress, user.Name
        address = str(entry.Address or "")
        return (address, entry.Name) if valid_email(address) else None
    finally:
        pythoncom.CoUninitialize()


def open_mail_draft(to, subject: str, body: str, bcc=()) -> None:
    """Fallback without Outlook: the default mail app opens a ready-to-send draft."""
    query = {"subject": subject, "body": body}
    if bcc:
        query["bcc"] = ",".join(bcc)
    url = ("mailto:" + urllib.parse.quote(",".join(to), safe="@,") + "?"
           + urllib.parse.urlencode(query, quote_via=urllib.parse.quote))
    webbrowser.open(url)


# ---------------------------------------------------------------- Microsoft Teams
def teams_chat_url(emails, message: str = "", app: bool = True) -> str:
    query = {"users": ",".join(emails)}
    if message:
        query["message"] = message
    prefix = "msteams:/l/chat/0/0?" if app else "https://teams.microsoft.com/l/chat/0/0?"
    return prefix + urllib.parse.urlencode(query, quote_via=urllib.parse.quote, safe="@,")


def open_teams_chat(email: str, message: str) -> str:
    """Open a 1:1 Teams chat with the message typed in (new Teams may leave it empty -
    the caller also puts the message on the clipboard). -> "app" | "web"."""
    try:
        os.startfile(teams_chat_url([email], message, app=True))
        return "app"
    except OSError:
        webbrowser.open(teams_chat_url([email], message, app=False))
        return "web"


# ---------------------------------------------------------------- the reminder itself
def version_text(ver: str) -> str:
    if ver == "2.14":
        return "2.14.x"
    if not ver or ver.startswith("<"):
        return "an older version"
    return ver


def reminder_subject(latest: str) -> str:
    return f"Pips {latest} is out - please update your pet \U0001F43E"


def _lines(name: str, ver: str, latest: str, title: str) -> dict:
    hi = f"Hi {name}" if name else "Hi"
    shown = version_text(ver)
    what = f" ({title})" if title else ""
    if ver:
        en = f"{hi}, your Pips is still on {shown} - the latest version is {latest}{what}."
        id_ = (f"Hai {name}, Pips kamu masih versi {shown.replace('an older version', 'lama')} - "
               f"versi terbaru {latest}.") if name else f"Hai, Pips kamu masih versi lama - versi terbaru {latest}."
        zh = (f"{name} 你好，你的 Pips 還是 {shown.replace('an older version', '舊版')}，最新版本是 {latest}。"
              if name else f"你好，你的 Pips 還是舊版，最新版本是 {latest}。")
    else:
        en = f"{hi}! Pips {latest}{what} is out and your pet is still on an older version."
        id_ = f"Hai! Pips {latest} sudah rilis dan pet kamu masih versi lama."
        zh = f"你好！Pips {latest} 已推出，你的寵物還是舊版。"
    return {"en": en, "id": id_, "zh": zh}


def reminder_html(name: str, ver: str, latest: str, sender: str = "", notes=None, title: str = "") -> str:
    """Friendly 3-language reminder. name/ver empty = the generic "everyone" version."""
    lines = _lines(name, ver, latest, title)
    esc = html.escape
    red = "#8b1a1a"
    whatsnew = ""
    if notes:
        items = "".join(f"<li>{esc(n)}</li>" for n in notes[:6])
        whatsnew = f'<p style="margin:10px 0 2px"><b>What\'s new in {esc(latest)}</b></p><ul style="margin-top:0">{items}</ul>'
    link = f'<a href="{SETUP_URL}">{SETUP_URL}</a>'
    sign = f"<p>Thanks! \U0001F43E<br>{esc(sender)}</p>" if sender else "<p>Thanks! \U0001F43E</p>"
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#2b2a28;line-height:1.5">
<p>{esc(lines['en'])}</p>
<p>Updating takes about a minute and keeps your pet, coins and history:</p>
<ol style="margin-top:0">
<li>Right-click your pet &rarr; <b>Help &rarr; Check for updates</b> &rarr; <b>Update now</b>.</li>
<li>Or download and run the installer: {link}</li>
</ol>
{whatsnew}
<hr style="border:none;border-top:1px solid #e2dcce">
<p style="color:#555">{esc(lines['id'])} Update cuma &plusmn;1 menit dan pet, koin serta riwayatmu tetap aman:
klik kanan pet &rarr; <b>Bantuan &rarr; Cek update</b> &rarr; <b>Update sekarang</b>, atau unduh installer: {link}</p>
<p style="color:#555">{esc(lines['zh'])}更新只要約 1 分鐘，寵物、金幣和紀錄都會保留：
在寵物上按右鍵 &rarr; <b>說明 &rarr; 檢查更新</b> &rarr; <b>立即更新</b>，或下載安裝程式：{link}</p>
{sign}
<p style="font-size:11px;color:{red}">Pips &middot; {SITE_URL}</p>
</div>"""


def reminder_text(name: str, ver: str, latest: str, sender: str = "") -> str:
    """Plain-text version (mailto: drafts)."""
    lines = _lines(name, ver, latest, "")
    return (f"{lines['en']}\n\n"
            "Updating takes about a minute and keeps your pet, coins and history:\n"
            "1. Right-click your pet -> Help -> Check for updates -> Update now.\n"
            f"2. Or download and run the installer: {SETUP_URL}\n\n"
            f"{lines['id']} Klik kanan pet -> Bantuan -> Cek update -> Update sekarang.\n"
            f"{lines['zh']}在寵物上按右鍵 -> 說明 -> 檢查更新 -> 立即更新。\n\n"
            f"Thanks!{(chr(10) + sender) if sender else ''}")
