"""release.py - one-command Pips releases (dev tool, NOT shipped in the zip).

    python release.py build            guide PPT + public package + zip + Windows installer + latest.json  -> .release/out/
    python release.py test             clean-room ZIP and native installer / upgrade / uninstall tests
    python release.py publish          push zip/manifest/guide/web page to juw12/projects (GitHub Pages),
                                       then post the release to #announcements in the Pips Community
    python release.py announce         (re)post the current release to the Pips Community
    python release.py email            preview the announcement e-mail (writes .release/out/email.html)
    python release.py email --send     send it with Outlook: first time a new mail, afterwards a
                                       Reply-All on the same thread (with the PPT attached)
    python release.py all              build + test + publish (+ e-mail preview)

Release checklist:
    1. bump APP_VERSION in app.py
    2. add the new release on top of CHANGELOG.json (notes in en / id / zh_TW / zh_CN)
    3. python release.py all     (announces the release in the Pips Community automatically;
       python release.py email --send is optional)

Settings live in .release/release.json (recipients, repo, ...). The e-mail thread
state (subject, recipients, versions sent) is kept in .release/email_thread.json.
Nothing personal (config.json, data/, e-mail/diary scripts) ever goes into the zip.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE_DIR = HERE / ".release"
OUT_DIR = RELEASE_DIR / "out"
TEMPLATE_DIR = RELEASE_DIR / "public_template"
SETTINGS_FILE = RELEASE_DIR / "release.json"
THREAD_FILE = RELEASE_DIR / "email_thread.json"

SITE_URL = "https://juw12.github.io/projects/pips/"
DOWNLOAD_BASE = SITE_URL + "download/"
ZIP_NAME = "Pips-Windows.zip"
SETUP_NAME = "Pips-Setup.exe"
GUIDE_NAME = "Pips-Guide.pptx"

# Program files copied from this folder into the public package.
PUBLIC_MODULES = [
    "app.py", "pet.py", "pet_sounds.py", "pet_songs.py", "weather_live.py", "social.py", "social_ui.py", "pet_shop.py", "theme.py", "arena_ui.py", "minigames.py", "weather_fx.py", "fancy_menu.py", "party_ui.py", "nudge_ui.py", "outreach.py", "tray.py", "tama.py", "roam.py", "visit_ui.py", "i18n.py", "updater.py", "audio.py", "coach.py",
    "scoring.py", "tracker.py", "tokens.py", "maas_tokens.py", "reminders.py",
    "diary.py", "focus_trail.py", "CHANGELOG.json", "CONTRACT.md", "install_autostart.ps1",
    "make_shortcuts.ps1", "Launch Widget.bat",
]
PUBLIC_ASSETS = ["widget.ico", "widget.png", "make_icon.py"]
# Things that must never end up in the public zip.
FORBIDDEN = {"config.json", "token_board.py", "daily_email.py", "diary_email.py", "diary_daily.py",
             "diary_export.py", "diary_obsidian.py", "release.py", "make_guide.py",
             "installer_builder.py", "installer_tests.py"}

DEFAULT_SETTINGS = {
    "repo_url": "https://juw12@github.com/juw12/projects.git",
    "repo_dir": str(RELEASE_DIR / "projects"),
    "app_path": "apps/pips",
    "apps_json": "scripts/apps.json",
    "git_name": "",
    "git_email": "",
    "email": {
        "to": [],
        "cc": [],
        "subject": "Pips - a cute desktop pet + focus timer for Windows",
        "sender_name": "Juwen",
    },
}


# ---------------------------------------------------------------- helpers
def log(msg: str) -> None:
    print(f"[release] {msg}", flush=True)


def load_settings() -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if SETTINGS_FILE.exists():
        user = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(settings.get(key), dict):
                settings[key].update(value)
            else:
                settings[key] = value
    return settings


def app_version() -> str:
    text = (HERE / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        sys.exit("APP_VERSION not found in app.py")
    return match.group(1)


def changelog() -> list[dict]:
    data = json.loads((HERE / "CHANGELOG.json").read_text(encoding="utf-8"))
    return data["releases"]


def current_release() -> dict:
    version = app_version()
    releases = changelog()
    if releases[0]["version"] != version:
        sys.exit(f"CHANGELOG.json starts with {releases[0]['version']} but app.py is {version} - "
                 "add the new release on top of CHANGELOG.json first.")
    return releases[0]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(cmd, cwd=None, check=True, env=None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


def git_env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    return env


# ---------------------------------------------------------------- build

def personal_strings() -> set[str]:
    """Passwords, user names and e-mail addresses from the private config.json."""
    found: set[str] = set()
    cfg_path = HERE / "config.json"
    if not cfg_path.exists():
        return found

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k.lower())
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and len(node) >= 5:
            if any(word in key for word in ("password", "username", "api_key", "token", "email", "to", "from")) \
                    or "@" in node:
                found.add(node)

    walk(json.loads(cfg_path.read_text(encoding="utf-8")))
    # The owner's contact address is shown in the app on purpose (credits / contact).
    public_ok = {"your-password", "you@example.com", "73946469+juw12@users.noreply.github.com"}
    return {s for s in found if s.lower() not in public_ok}

def make_public_app(source: str) -> str:
    """Public build: MAAS token tracking (company-internal) is off by default."""
    patched, count = re.subn(r'("maas":\s*\{\s*"enabled":\s*)True', r"\1False", source, count=1)
    if count == 1:
        return patched
    if re.search(r'"maas":\s*\{\s*"enabled":\s*False', source):
        return source           # already off by default in the source (since 2.25.2)
    raise RuntimeError("could not switch MAAS off in the public app.py")


def build(*, reuse_guide: bool = False) -> dict:
    release = current_release()
    version = release["version"]
    if not TEMPLATE_DIR.exists():
        sys.exit(f"missing {TEMPLATE_DIR} (Start Pips.bat, READ ME FIRST.txt, README.md, ...)")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    guide = OUT_DIR / GUIDE_NAME
    if reuse_guide:
        if not guide.exists():
            raise RuntimeError("--reuse-guide requires an already generated guide")
        with zipfile.ZipFile(guide) as presentation:
            slides = "".join(
                presentation.read(name).decode("utf-8")
                for name in presentation.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
        if version not in slides:
            raise RuntimeError("The existing guide does not contain this release version")
        log("reusing the already generated and checked guide")
    else:
        log("building the user guide (PPT)")
        run([sys.executable, str(HERE / "make_guide.py"), "--out", str(guide)], cwd=HERE)

    stage = OUT_DIR / "Pips"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(TEMPLATE_DIR, stage)
    for name in PUBLIC_MODULES:
        shutil.copy2(HERE / name, stage / name)
    # Icons always come from the live assets/ folder (regenerate with assets/make_icon.py).
    for name in PUBLIC_ASSETS:
        (stage / "assets").mkdir(exist_ok=True)
        shutil.copy2(HERE / "assets" / name, stage / "assets" / name)
    (stage / "app.py").write_text(make_public_app((HERE / "app.py").read_text(encoding="utf-8")),
                                  encoding="utf-8")
    shutil.copy2(guide, stage / GUIDE_NAME)
    for bad in FORBIDDEN:
        if (stage / bad).exists():
            raise RuntimeError(f"{bad} must not be in the public package")
    secrets = personal_strings()
    for path in stage.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json", ".txt", ".md", ".bat", ".ps1"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = [s for s in secrets if s in text]
            if hits:
                raise RuntimeError(f"personal data ({len(hits)} value(s)) found in {path}")

    log("zipping")
    zip_path = OUT_DIR / ZIP_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, Path("Pips") / path.relative_to(stage))

    log("building the self-contained Windows installer")
    from installer_builder import build_installer
    installer = build_installer(stage, OUT_DIR, version)
    installer["url"] = DOWNLOAD_BASE + SETUP_NAME

    manifest = {
        "installer": installer,
        "version": version,
        "date": release["date"],
        "title": release.get("title", ""),
        "url": DOWNLOAD_BASE + ZIP_NAME,
        "sha256": sha256_of(zip_path),
        "size": zip_path.stat().st_size,
        "notes": release["notes"],
        "guide": DOWNLOAD_BASE + GUIDE_NAME,
    }
    (OUT_DIR / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    log(f"built {zip_path} ({manifest['size']:,} bytes, sha256 {manifest['sha256'][:12]}...)")
    return manifest


# ---------------------------------------------------------------- test
def test() -> None:
    zip_path = OUT_DIR / ZIP_NAME
    manifest = json.loads((OUT_DIR / "latest.json").read_text(encoding="utf-8"))
    if sha256_of(zip_path) != manifest["sha256"]:
        raise RuntimeError("zip does not match latest.json")
    with tempfile.TemporaryDirectory(prefix="pips-cleanroom-") as tmp:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            archive.extractall(tmp)
        for bad in FORBIDDEN:
            if any(n.endswith("/" + bad) for n in names):
                raise RuntimeError(f"{bad} found in zip")
        app_dir = Path(tmp) / "Pips"
        version = run([sys.executable, "app.py", "--version"], cwd=app_dir).stdout.strip()
        if manifest["version"] not in version:
            raise RuntimeError(f"--version says {version!r}, expected {manifest['version']}")
        proc = run([sys.executable, "app.py", "--selftest"], cwd=app_dir, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"selftest failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
        cfg = json.loads((app_dir / "config.json").read_text(encoding="utf-8")) \
            if (app_dir / "config.json").exists() else {}
        if cfg.get("maas", {}).get("enabled"):
            raise RuntimeError("MAAS is enabled in the public default config")
    log(f"clean-room test passed ({version}, {len(names)} files)")
    from installer_tests import test_installer
    report = test_installer(OUT_DIR, manifest)
    (OUT_DIR / "installer-test-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"installer tests passed ({len(report['checks'])} checks)")


# ---------------------------------------------------------------- publish
def ensure_repo(settings: dict) -> Path:
    repo = Path(settings["repo_dir"])
    env = git_env()
    if not (repo / ".git").exists():
        log(f"cloning {settings['repo_url']} (sparse) into {repo}")
        repo.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--filter=blob:none", "--sparse", settings["repo_url"], str(repo)], env=env)
        run(["git", "sparse-checkout", "set", settings["app_path"], "scripts"], cwd=repo, env=env)
    run(["git", "pull", "--rebase", "--autostash"], cwd=repo, env=env)
    return repo


def update_index_html(path: Path, release: dict) -> None:
    text = path.read_text(encoding="utf-8")
    major_minor = ".".join(release["version"].split(".")[:2])
    text = re.sub(r"(The Morning Paper &middot; )v[\d.]+", rf"\1v{major_minor}", text)
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in release["notes"]["en"])
    block = (
        "<!-- whatsnew:start -->\n"
        f'    <h2>New in {release["version"]} <span class="small">({release["date"]})</span></h2>\n'
        f"    <ul>{notes}</ul>\n"
        '    <p class="small">Already have Pips? Keep your current installation and profile: '
        'right-click your pet &rarr; Help &rarr; Check for updates. No reinstall needed.</p>\n'
        "    <!-- whatsnew:end -->"
    )
    if "<!-- whatsnew:start -->" in text:
        text = re.sub(r"<!-- whatsnew:start -->.*?<!-- whatsnew:end -->", lambda m: block, text, flags=re.S)
    else:
        raise RuntimeError("Missing release-notes marker on the download page")

    download = f"""<div class="download">
      <a class="btn primary big" href="download/{SETUP_NAME}" download>&#11015; Download Pips for Windows</a>
      <p class="small">Windows 10/11 (64-bit) &middot; Free &middot; Python included &middot; No administrator password.</p>
      <p>Open the download and click <strong>Install Pips</strong>. Your pet opens automatically. Nothing to unzip.</p>
      <p class="small"><a href="download/{GUIDE_NAME}">User guide (PowerPoint)</a></p>
    </div>"""
    text, count = re.subn(r'<div class="download">.*?</div>', lambda m: download, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError("Missing primary download section")

    install = f"""<h2>Install</h2>
    <ol>
      <li>Click <strong>Download Pips for Windows</strong> above.</li>
      <li>Open <strong>{SETUP_NAME}</strong> from your browser and click <strong>Install Pips</strong>.</li>
      <li>Choose your language and meet your pet. Pips is also in your Desktop and Start Menu shortcuts.</li>
    </ol>
    <p class="small">No Python setup, no ZIP extraction, no commands. Installs only for your Windows account.
    You can opt in to start with Windows from Pips &rarr; Autostart.</p>
    <p class="small">This installer is currently unsigned. Windows SmartScreen may show &ldquo;Windows protected your PC&rdquo;.
    If you trust this official download, choose <strong>More info &rarr; Run anyway</strong> if available.
    On a managed work PC, ask IT if installation is blocked; do not disable security software.</p>
    <details><summary>Troubleshooting, existing users &amp; advanced downloads</summary>
      <p>Already use a ZIP copy? Update inside that copy via Help &rarr; Check for updates to keep the same profile.
      The installer preserves data in its own installation folder; it does not automatically import another ZIP folder.</p>
      <p>To remove the installed app, use Windows Settings &rarr; Apps &rarr; Pips &rarr; Uninstall.
      Your local settings and progress are left in place for a later reinstall.</p>
      <p><a href="download/{ZIP_NAME}" download>Source/update archive (advanced; Python required)</a></p>
    </details>
    <p class="small">Focus history is stored locally. Optional Friends and sharing features use the Pips online service.</p>
    """
    start = text.find("<h2>Install</h2>")
    end = text.find('<div class="btns"', start)
    if start < 0 or end < 0:
        raise RuntimeError("Missing installation instructions on the download page")
    text = text[:start] + install + text[end:]

    # A PWA install button installs the website, not the desktop pet. Do not show
    # two different meanings of 'Install' on the native download page.
    text = re.sub(r'\s*<button id="btn-install-app"[^>]*>.*?</button>', "", text)
    callout = text.find('<div id="install-callout"')
    if callout >= 0:
        script = text.find("<script>", callout)
        text = text[:callout] + text[script:]
    registration = """<script>
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('./sw.js', {updateViaCache: 'none'}).catch(function () {});
    });
  }
  </script>"""
    text = re.sub(
        r"<script>.*?</script>",
        lambda match: registration if "beforeinstallprompt" in match.group(0) else match.group(0),
        text,
        flags=re.S,
    )
    path.write_text(text, encoding="utf-8")


def publish(settings: dict, push: bool = True) -> str:
    release = current_release()
    manifest = json.loads((OUT_DIR / "latest.json").read_text(encoding="utf-8"))
    if manifest["version"] != release["version"]:
        raise RuntimeError("Build and test this version before publishing")
    for name, expected in ((ZIP_NAME, manifest["sha256"]), (SETUP_NAME, manifest["installer"]["sha256"])):
        if sha256_of(OUT_DIR / name) != expected:
            raise RuntimeError(f"Release artifact does not match its manifest: {name}")
    report = json.loads((OUT_DIR / "installer-test-report.json").read_text(encoding="utf-8"))
    if not report.get("passed") or report.get("installer_sha256") != manifest["installer"]["sha256"]:
        raise RuntimeError("Run release.py test on this installer before publishing")
    repo = ensure_repo(settings)
    app_dir = repo / settings["app_path"]
    stage = OUT_DIR / "Pips"

    # source mirror (public build) - keep web/ and repo-only files
    for path in stage.rglob("*"):
        if path.is_file() and path.name != GUIDE_NAME:
            dest = app_dir / path.relative_to(stage)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    download = app_dir / "web" / "download"
    download.mkdir(parents=True, exist_ok=True)
    for name in (SETUP_NAME, ZIP_NAME, "latest.json", GUIDE_NAME):
        shutil.copy2(OUT_DIR / name, download / name)
    update_index_html(app_dir / "web" / "index.html", release)
    worker_path = app_dir / "web" / "sw.js"
    worker = worker_path.read_text(encoding="utf-8")
    worker = re.sub(r"const CACHE_NAME = '[^']+';", f"const CACHE_NAME = 'pips-v{release['version']}';", worker)
    worker_path.write_text(worker, encoding="utf-8")

    apps_path = repo / settings["apps_json"]
    apps = json.loads(apps_path.read_text(encoding="utf-8"))
    for entry in apps["apps"]:
        if entry.get("id") == "pips":
            entry["version"] = release["version"]
            links = entry.setdefault("extra_links", [])
            links[:] = [link for link in links if ZIP_NAME not in link.get("url", "") and SETUP_NAME not in link.get("url", "")]
            links.insert(0, {"label": "Download Pips for Windows", "url": DOWNLOAD_BASE + SETUP_NAME})
            for card_link in entry.get("card_links", []):
                if card_link.get("label", "").startswith("Download"):
                    card_link["label"] = "Download for Windows"
                    card_link["path"] = "pips/download/" + SETUP_NAME
            entry["description"] = entry.get("description", "").replace(
                "Python + tkinter; everything stays local.",
                "Easy Windows installer; Python is included. Focus history stays local; optional social features use the Pips online service."
            )
            if not any(GUIDE_NAME in link.get("url", "") for link in links):
                links.insert(1, {"label": "User guide (PPT)", "url": DOWNLOAD_BASE + GUIDE_NAME})
    apps_path.write_text(json.dumps(apps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    env = git_env()
    run(["git", "add", "-A", settings["app_path"], settings["apps_json"]], cwd=repo, env=env)
    if not run(["git", "status", "--porcelain"], cwd=repo, env=env).stdout.strip():
        log("nothing to publish (already up to date)")
        return ""
    message = f"pips {release['version']}: {release.get('title', '')}".strip()
    cmd = ["git"]
    if settings.get("git_name"):
        cmd += ["-c", f"user.name={settings['git_name']}"]
    if settings.get("git_email"):
        cmd += ["-c", f"user.email={settings['git_email']}"]
    run(cmd + ["commit", "-m", message], cwd=repo, env=env)
    sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, env=env).stdout.strip()
    if push:
        run(["git", "push"], cwd=repo, env=env)
        log(f"pushed {sha} - GitHub Pages updates in ~1 minute: {SITE_URL}")
    return sha


# ---------------------------------------------------------------- Pips Community announcement
def pips_api_url() -> str:
    text = (HERE / "social.py").read_text(encoding="utf-8")
    match = re.search(r'^API_URL\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise RuntimeError("API_URL not found in social.py")
    return match.group(1)


def _open_url(request, timeout=30):
    """urlopen with the environment proxy; if that doesn't answer, retry with the Windows proxy settings."""
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError):
        try:
            proxies = urllib.request.getproxies_registry()
        except Exception:
            proxies = {}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        return opener.open(request, timeout=timeout)


def wait_for_pages(version: str, timeout_s: int = 600) -> bool:
    """Wait until GitHub Pages serves this version's latest.json (so "Update now" finds it)."""
    deadline = time.time() + timeout_s
    while True:
        try:
            request = urllib.request.Request(f"{DOWNLOAD_BASE}latest.json?t={int(time.time())}",
                                             headers={"user-agent": "Pips-release", "cache-control": "no-cache"})
            with _open_url(request, timeout=20) as response:
                if json.loads(response.read().decode("utf-8")).get("version") == version:
                    return True
        except Exception as exc:  # network hiccup - keep waiting
            log(f"waiting for GitHub Pages ({exc.__class__.__name__})")
        if time.time() > deadline:
            return False
        time.sleep(15)


def announce(wait: bool = True) -> None:
    """Post the current release to #announcements in the Pips Community (needs the owner / a moderator
    account in data/social.json). Safe to repeat: the server keeps one post per version."""
    release = current_release()
    version = release["version"]
    account_file = HERE / "data" / "social.json"
    try:
        account = json.loads(account_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        account = {}
    if not account.get("id") or not account.get("secret"):
        raise RuntimeError(f"No Friends account in {account_file} - turn Friends on in Pips first")
    if account.get("role") not in ("owner", "mod"):
        raise RuntimeError("Only the owner or a moderator can post announcements")
    if wait:
        log(f"waiting until GitHub Pages serves {version} ...")
        if not wait_for_pages(version):
            raise RuntimeError("GitHub Pages still serves an older version - run `python release.py announce` later")
    notes = release.get("notes") or {}
    body = {
        "v": version,
        "title": release.get("title", ""),
        "date": release.get("date", dt.date.today().isoformat()),
        "notes": {lang: list(notes[lang])[:8] for lang in ("en", "id", "zh_TW", "zh_CN") if notes.get(lang)},
    }
    request = urllib.request.Request(
        pips_api_url() + "/v1/send",
        data=json.dumps({"channel": "news", "kind": "release", "body": body}).encode("utf-8"),
        method="POST",
    )
    request.add_header("content-type", "application/json")
    request.add_header("user-agent", "Pips-release")      # not "Pips/x.y.z": don't touch the account's app version
    request.add_header("authorization", f"Bearer {account['id']}.{account['secret']}")
    try:
        with _open_url(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        raise RuntimeError(f"announce failed: HTTP {err.code} {detail}") from None
    if result.get("duplicate"):
        log(f"Pips {version} was already announced in #announcements")
    else:
        log(f"announced Pips {version} in the Pips Community (#announcements, message {result.get('id')})")


# ---------------------------------------------------------------- e-mail
CSS = ("font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#2b2a28;line-height:1.5")


def _history_html(releases: list[dict]) -> str:
    rows = []
    for rel in releases:
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in rel["notes"]["en"])
        rows.append(
            f'<tr><td style="padding:6px 10px;vertical-align:top;white-space:nowrap"><b>{rel["version"]}</b><br>'
            f'<span style="color:#6e6a62">{rel["date"]}</span></td>'
            f'<td style="padding:6px 10px;vertical-align:top"><b>{html.escape(rel.get("title", ""))}</b>'
            f'<ul style="margin:2px 0 0 18px;padding:0">{notes}</ul></td></tr>')
    return ('<table style="border-collapse:collapse;border:1px solid #e2dcce;font-size:13px">'
            + "".join(rows) + "</table>")


def _first_name(address: str) -> str:
    local = address.split("@")[0].split("_")[0]
    letters = "".join(ch for ch in local if ch.isalpha())
    return letters.capitalize() or address


def email_html(first: bool, settings: dict, unsent: list | None = None, newcomers: list | None = None,
               note: str = "") -> str:
    release = current_release()
    releases = changelog()
    version = release["version"]
    name = settings["email"].get("sender_name", "")
    unsent = unsent or [release]
    if len(unsent) == 1:
        whatsnew = "".join(f"<li>{html.escape(n)}</li>" for n in release["notes"]["en"])
    else:
        whatsnew = "".join(
            f'<li><b>{rel["version"]} &ndash; {html.escape(rel.get("title", ""))}</b><ul>'
            + "".join(f"<li>{html.escape(n)}</li>" for n in rel["notes"]["en"])
            + "</ul></li>"
            for rel in unsent)
    welcome = ""
    if newcomers:
        names = " &amp; ".join(html.escape(_first_name(a)) for a in newcomers)
        welcome = (f"<p>{names}, welcome &ndash; I've added you to this thread. Pips is a cute desktop pet "
                   "that lives on your Windows taskbar plus a Pomodoro focus timer (free, English / Bahasa "
                   "Indonesia / 中文). Use the installer button below: no unzipping or Python setup. "
                   "The updated user guide is attached.</p>")
    red = "#8b1a1a"
    note = (f'<div style="background:#fbf3e6;border-left:4px solid {red};padding:8px 12px;margin:8px 0">'
            f'{note}</div>') if note else ""
    btn = (f'<a href="{DOWNLOAD_BASE}{SETUP_NAME}" style="background:{red};color:#fff;padding:9px 16px;'
           'border-radius:4px;text-decoration:none;font-weight:bold">Download Pips for Windows</a>')
    if first:
        body = f"""
<p>Hi all,</p>
<p>I made a small Windows app called <b>Pips</b> and would love for you to try it: a cute
<b>desktop pet</b> that lives on your taskbar, plus a <b>Pomodoro focus timer</b> that gently
reminds you to take breaks, drink water and rest your eyes. It works in English,
Bahasa Indonesia, 繁體中文 and 简体中文.</p>
<p><img src="cid:pips-hero" alt="Pips animals" width="520" style="border:1px solid #e2dcce;border-radius:6px"></p>
<p>{btn}&nbsp;&nbsp;<a href="{SITE_URL}">Pips website</a></p>
<h3 style="color:{red};margin-bottom:4px">Install in 3 steps</h3>
<ol style="margin-top:0">
<li>Click <b>Download Pips for Windows</b> above and open the downloaded installer.</li>
<li>Click <b>Install Pips</b>. Python is included, nothing to unzip, and no administrator password is needed.</li>
<li>Pick your language &ndash; your pet appears on the taskbar and gives you a 1-minute interactive guide.</li>
</ol>
<p style="font-size:12px;color:#666">The installer is unsigned, so Windows may show SmartScreen. For the trusted official download, choose More info &rarr; Run anyway if available; ask IT if your work PC blocks it.</p>
<h3 style="color:{red};margin-bottom:4px">Quick how-to</h3>
<ul style="margin-top:0">
<li><b>Click</b> the pet &rarr; opens the Pomodoro card (click the ring to start 25 minutes of focus).</li>
<li><b>Drag</b> it anywhere; drop it on the taskbar and it walks along.</li>
<li><b>Rub the mouse</b> over it to pet it &hearts;. <b>Right-click</b> for snacks, 23 animals, colours, language and settings.</li>
<li><b>Updates are built in</b>: when there is a new version your pet tells you &ndash; one click and it updates itself (your settings stay).</li>
</ul>
<p>Focus history stays on your PC; optional Friends and sharing features use the Pips online service. The full user guide is attached
(PowerPoint). Feedback and ideas are very welcome &ndash; just reply to this mail!</p>
<h3 style="color:{red};margin-bottom:4px">What's in {version}</h3>
<ul style="margin-top:0">{whatsnew}</ul>
<h3 style="color:{red};margin-bottom:4px">Version history</h3>
{_history_html(releases)}
<p>Cheers,<br>{html.escape(name)}</p>"""
    else:
        body = f"""
<p>Hi all,</p>
{welcome}
{note}
<p><b>Pips {version}</b> is out ({release['date']}) &ndash; <i>{html.escape(release.get('title', ''))}</i>.</p>
<h3 style="color:{red};margin-bottom:4px">What's new{" in " + html.escape(unsent[-1]["version"]) + " &ndash; " + html.escape(unsent[0]["version"]) if len(unsent) > 1 else ""}</h3>
<ul style="margin-top:0">{whatsnew}</ul>
<h3 style="color:{red};margin-bottom:4px">How to update</h3>
<ul style="margin-top:0">
<li>Your pet will offer the update by itself &ndash; click <b>Update now</b> (settings and history are kept).</li>
<li>Or right-click the pet &rarr; <b>Help &rarr; Check for updates</b>.</li>
<li>New here? {btn}</li>
</ul>
<p>New installs: open the download, click <b>Install Pips</b>, and choose your language. The updated user guide is attached.</p>
<p style="font-size:12px;color:#666">The installer is unsigned, so Windows may show SmartScreen. For the trusted official download, choose More info &rarr; Run anyway if available; ask IT if your work PC blocks it.</p>
<h3 style="color:{red};margin-bottom:4px">Version history</h3>
{_history_html(releases)}
<p>Cheers,<br>{html.escape(name)}</p>"""
    return f'<div style="{CSS}">{body}</div>'


def _resolve_smtp(recipient) -> str:
    try:
        user = recipient.AddressEntry.GetExchangeUser()
        if user is not None and user.PrimarySmtpAddress:
            return user.PrimarySmtpAddress.lower()
    except Exception:
        pass
    return str(recipient.Address or "").lower()


MAIL_FOLDER_NAMES = ("sent items", "sent", "inbox", "寄件備份", "已傳送郵件", "收件匣", "已发送邮件", "收件箱")


def _mail_folders(namespace):
    """Sent Items / Inbox of every store (mail may be delivered to a PST, not the default mailbox)."""
    seen = set()
    for store in namespace.Stores:
        candidates = []
        for folder_id in (5, 6):  # olFolderSentMail, olFolderInbox
            try:
                candidates.append(store.GetDefaultFolder(folder_id))
            except Exception:
                pass
        try:
            for sub in store.GetRootFolder().Folders:
                if sub.Name.strip().lower() in MAIL_FOLDER_NAMES:
                    candidates.append(sub)
        except Exception:
            pass
        for folder in candidates:
            try:
                key = folder.EntryID
            except Exception:
                continue
            if key not in seen:
                seen.add(key)
                yield folder


def _find_thread_item(namespace, topic: str, thread: dict | None = None):
    """Latest message of the thread (any store's Sent Items / Inbox) by conversation topic."""
    prop = "http://schemas.microsoft.com/mapi/proptag/0x0070001F"
    safe = topic.replace("'", "''")
    best = None
    best_time = None
    for folder in _mail_folders(namespace):
        try:
            items = folder.Items.Restrict(f"@SQL=\"{prop}\" = '{safe}'")
        except Exception:
            continue
        for item in items:
            when = getattr(item, "SentOn", None) or getattr(item, "ReceivedTime", None)
            if when is None:
                continue
            stamp = dt.datetime(when.year, when.month, when.day, when.hour, when.minute, when.second)
            if best_time is None or stamp > best_time:
                best, best_time = item, stamp
    if best is None and thread and thread.get("entry_id"):
        try:
            best = namespace.GetItemFromID(thread["entry_id"], thread.get("store_id"))
        except Exception:
            best = None
    return best


def _remember_sent_item(namespace, thread: dict, subject: str, sent_after: dt.datetime | None = None) -> None:
    """Store EntryID/StoreID of the mail we just sent (fallback for the next Reply-All)."""
    import time
    for _ in range(20):
        time.sleep(3)
        item = _find_thread_item(namespace, subject)
        if item is None:
            continue
        when = getattr(item, "SentOn", None)
        stamp = dt.datetime(when.year, when.month, when.day, when.hour, when.minute, when.second) if when else None
        if sent_after is None or (stamp is not None and stamp >= sent_after - dt.timedelta(seconds=5)):
            thread["entry_id"] = item.EntryID
            thread["store_id"] = item.Parent.StoreID
            return


def email(settings: dict, send: bool = False, note: str = "") -> None:
    release = current_release()
    cfg = settings["email"]
    recipients = [r.strip() for r in cfg.get("to", []) if r.strip()]
    cc = [r.strip() for r in cfg.get("cc", []) if r.strip()]
    thread = json.loads(THREAD_FILE.read_text(encoding="utf-8")) if THREAD_FILE.exists() else {}
    first = not thread.get("versions")
    if release["version"] in thread.get("versions", []):
        log(f"{release['version']} was already e-mailed on {thread.get('last_sent')} - nothing to do")
        if send:
            return
    sent_versions = set(thread.get("versions", []))
    unsent = []
    for rel in changelog():
        if rel["version"] in sent_versions:
            break
        unsent.append(rel)
    newcomers = [] if first else [a for a in recipients
                                  if a.lower() not in {r.lower() for r in thread.get("recipients", [])}]
    body = email_html(first, settings, unsent, newcomers, note)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    preview = OUT_DIR / "email.html"
    preview.write_text(f"<html><meta charset='utf-8'><body>{body}</body></html>", encoding="utf-8")
    subject = thread.get("subject") or cfg["subject"]
    log(("NEW e-mail" if first else "REPLY-ALL on thread") + f": {subject!r}")
    log(f"to: {', '.join(recipients) or '(none set in .release/release.json)'}" + (f"  cc: {', '.join(cc)}" if cc else ""))
    log(f"versions: {', '.join(r['version'] for r in unsent)}" + (f"  new people: {', '.join(newcomers)}" if newcomers else ""))
    log(f"preview: {preview}")
    if not send:
        return
    if not recipients:
        sys.exit("no recipients configured in .release/release.json -> email.to")
    guide = OUT_DIR / GUIDE_NAME
    if not guide.exists():
        sys.exit("run `python release.py build` first (the guide PPT is attached)")

    import win32com.client  # pywin32

    outlook = win32com.client.Dispatch("Outlook.Application")
    namespace = outlook.GetNamespace("MAPI")
    if first:
        mail = outlook.CreateItem(0)
        mail.Subject = subject
        mail.To = "; ".join(recipients)
        if cc:
            mail.CC = "; ".join(cc)
        hero = HERE / "guide_assets" / "hero_pets.png"
        if hero.exists():
            att = mail.Attachments.Add(str(hero))
            att.PropertyAccessor.SetProperty("http://schemas.microsoft.com/mapi/proptag/0x3712001F", "pips-hero")
        else:
            body = body.replace('<img src="cid:pips-hero"', '<img src=""')
        mail.HTMLBody = body
    else:
        original = _find_thread_item(namespace, thread.get("topic") or subject, thread)
        if original is None:
            sys.exit(f"could not find the previous mail {subject!r} in Sent Items / Inbox")
        mail = original.ReplyAll()
        present = {_resolve_smtp(r) for r in mail.Recipients}
        for address in recipients:
            if address.lower() not in present:
                mail.Recipients.Add(address).Type = 1
        mail.Recipients.ResolveAll()
        mail.HTMLBody = body + "<br><hr>" + mail.HTMLBody
    mail.Attachments.Add(str(guide))
    sent_at = dt.datetime.now()
    mail.Send()
    for rel in reversed(unsent):
        if rel["version"] not in thread.setdefault("versions", []):
            thread["versions"].append(rel["version"])
    thread["subject"] = subject
    thread["topic"] = thread.get("topic") or subject
    thread["recipients"] = recipients
    thread["cc"] = cc
    thread["last_sent"] = dt.datetime.now().isoformat(timespec="seconds")
    _remember_sent_item(namespace, thread, thread["topic"], sent_at)
    THREAD_FILE.write_text(json.dumps(thread, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"sent ({'new mail' if first else 'reply-all'}) - thread saved to {THREAD_FILE}")


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build, publish and announce a Pips release.")
    parser.add_argument("step", choices=["build", "test", "publish", "announce", "email", "all"])
    parser.add_argument("--reuse-guide", action="store_true", help="build: reuse the already generated guide for this version")
    parser.add_argument("--send", action="store_true", help="email: actually send (default: preview only)")
    parser.add_argument("--note", default="", help="email: extra HTML paragraph for this mail only")
    parser.add_argument("--no-push", action="store_true", help="publish: commit but do not push")
    parser.add_argument("--repo", help="publish: use this existing clone of juw12/projects")
    parser.add_argument("--no-announce", action="store_true", help="publish: don't post to the Pips Community")
    parser.add_argument("--no-wait", action="store_true", help="announce: don't wait for GitHub Pages")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.repo:
        settings["repo_dir"] = args.repo
    if args.step in ("build", "all"):
        build(reuse_guide=args.reuse_guide)
    if args.step in ("test", "all"):
        test()
    if args.step in ("publish", "all"):
        sha = publish(settings, push=not args.no_push)
        if sha and not args.no_push and not args.no_announce:
            try:
                announce(wait=True)
            except Exception as exc:
                log(f"could not announce in the Pips Community: {exc}")
    if args.step == "announce":
        announce(wait=not args.no_wait)
    if args.step == "email" or args.step == "all":
        email(settings, send=args.send and args.step == "email", note=args.note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
