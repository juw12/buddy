"""Build the Pips user guide (PowerPoint) from CHANGELOG.json + guide_assets/.

    python make_guide.py                 -> Pips-Guide.pptx next to this file
    python make_guide.py --out X.pptx

Dev tool: needs python-pptx (pip install python-pptx). Not shipped in the zip.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "guide_assets"

SITE_URL = "https://juw12.github.io/projects/pips/"
SETUP_URL = "https://juw12.github.io/projects/pips/download/Pips-Setup.exe"

# Pips palette
CREAM = RGBColor(0xF7, 0xF4, 0xEC)
PAPER = RGBColor(0xFF, 0xFD, 0xF8)
INK = RGBColor(0x2B, 0x2A, 0x28)
MUTED = RGBColor(0x6E, 0x6A, 0x62)
RED = RGBColor(0x8B, 0x1A, 0x1A)
GREEN = RGBColor(0x4C, 0xB8, 0x6B)
GREEN_SOFT = RGBColor(0xE4, 0xF5, 0xE8)
LINE = RGBColor(0xE2, 0xDC, 0xCE)
FONT = "Segoe UI"

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def app_version() -> str:
    text = (HERE / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else "?"


def app_owner() -> str:
    text = (HERE / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_OWNER\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else ""


def load_changelog() -> list[dict]:
    data = json.loads((HERE / "CHANGELOG.json").read_text(encoding="utf-8"))
    return data["releases"]


# ---------------------------------------------------------------- helpers
def set_background(slide, color=CREAM):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text(slide, x, y, w, h, text, size=18, color=INK, bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT):
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = Inches(0.05)
    frame.margin_top = frame.margin_bottom = Inches(0.03)
    lines = text.split("\n") if isinstance(text, str) else text
    for index, line in enumerate(lines):
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        para.alignment = align
        run = para.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = font
    return box


def add_bullets(slide, x, y, w, h, items, size=17, color=INK, gap=6):
    """items: str or (bold_lead, rest) tuples. Nested list => sub-bullets."""
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.word_wrap = True
    first = True
    for item in items:
        level = 0
        if isinstance(item, list):
            for sub in item:
                para = frame.paragraphs[0] if first else frame.add_paragraph()
                first = False
                _bullet_runs(para, sub, size - 2, MUTED, level=1)
                para.space_after = Pt(gap / 2)
            continue
        para = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        _bullet_runs(para, item, size, color, level)
        para.space_after = Pt(gap)
    return box


def _bullet_runs(para, item, size, color, level):
    marker = "•  " if level == 0 else "–  "
    indent = "" if level == 0 else "      "
    lead, rest = (item if isinstance(item, tuple) else ("", item))
    run = para.add_run()
    run.text = indent + marker
    run.font.size = Pt(size)
    run.font.color.rgb = GREEN if level == 0 else MUTED
    run.font.bold = True
    run.font.name = FONT
    if lead:
        run = para.add_run()
        run.text = lead + " "
        run.font.size = Pt(size)
        run.font.bold = True
        run.font.color.rgb = color
        run.font.name = FONT
    run = para.add_run()
    run.text = rest
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.name = FONT


def add_card(slide, x, y, w, h, fill=PAPER, line=LINE):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    shape.adjustments[0] = 0.06
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line
    shape.line.width = Pt(1)
    shape.shadow.inherit = False
    return shape


def add_picture_fit(slide, path, x, y, w, h, border=True):
    """Place an image inside the box (x, y, w, h), keeping its aspect ratio."""
    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(w / iw, h / ih)
    pw, ph = int(iw * scale), int(ih * scale)
    px = x + (w - pw) // 2
    py = y + (h - ph) // 2
    pic = slide.shapes.add_picture(str(path), Emu(px), Emu(py), Emu(pw), Emu(ph))
    if border:
        pic.line.color.rgb = LINE
        pic.line.width = Pt(1)
    return pic


def add_caption(slide, x, y, w, text):
    return add_text(slide, x, y, w, Inches(0.4), text, size=12, color=MUTED,
                    align=PP_ALIGN.CENTER)


def add_header(slide, title, kicker=None, page=None):
    set_background(slide)
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.18), SLIDE_H)
    bar.fill.solid()
    bar.fill.fore_color.rgb = RED
    bar.line.fill.background()
    if kicker:
        add_text(slide, Inches(0.6), Inches(0.35), Inches(10), Inches(0.4),
                 kicker.upper(), size=12, color=GREEN, bold=True)
    add_text(slide, Inches(0.6), Inches(0.62), Inches(12), Inches(0.8),
             title, size=32, color=INK, bold=True)
    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.62), Inches(1.42),
                                  Inches(1.2), Inches(0.05))
    rule.fill.solid()
    rule.fill.fore_color.rgb = RED
    rule.line.fill.background()
    if page is not None:
        add_text(slide, Inches(11.6), Inches(7.0), Inches(1.5), Inches(0.35),
                 f"Pips · {page}", size=10, color=MUTED, align=PP_ALIGN.RIGHT)


def add_step_badge(slide, x, y, number, size=Inches(0.5)):
    dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, x, y, size, size)
    dot.fill.solid()
    dot.fill.fore_color.rgb = RED
    dot.line.fill.background()
    frame = dot.text_frame
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    para = frame.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    run = para.add_run()
    run.text = str(number)
    run.font.size = Pt(16)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    run.font.name = FONT


def add_table(slide, x, y, w, rows, col_widths, header_fill=RED, size=13,
              row_h=Inches(0.42)):
    table_shape = slide.shapes.add_table(len(rows), len(rows[0]), x, y, w,
                                         row_h * len(rows))
    table = table_shape.table
    for index, width in enumerate(col_widths):
        table.columns[index].width = width
    table.rows[0].height = Inches(0.42)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = header_fill
            else:
                cell.fill.fore_color.rgb = PAPER if r % 2 else CREAM
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            frame = cell.text_frame
            frame.word_wrap = True
            lines = str(value).split("\n")
            for li, line in enumerate(lines):
                para = frame.paragraphs[0] if li == 0 else frame.add_paragraph()
                run = para.add_run()
                run.text = line
                run.font.size = Pt(size)
                run.font.name = FONT
                run.font.bold = r == 0 or (c == 0 and li == 0)
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF) if r == 0 else INK
    return table_shape


def asset(name):
    path = ASSETS / name
    return path if path.exists() else None


# ---------------------------------------------------------------- slides
def build(out_path: Path) -> Path:
    version = app_version()
    releases = load_changelog()
    latest = releases[0]

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    blank = prs.slide_layouts[6]
    page = 0

    def new_slide(title=None, kicker=None):
        nonlocal page
        page += 1
        slide = prs.slides.add_slide(blank)
        if title:
            add_header(slide, title, kicker, page)
        else:
            set_background(slide)
        return slide

    # 1. Title -------------------------------------------------------------
    s = new_slide()
    band = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.3), SLIDE_H)
    band.fill.solid()
    band.fill.fore_color.rgb = RED
    band.line.fill.background()
    add_text(s, Inches(0.9), Inches(1.4), Inches(6.5), Inches(0.5),
             "USER GUIDE", size=16, color=GREEN, bold=True)
    add_text(s, Inches(0.9), Inches(1.85), Inches(6.5), Inches(1.4),
             "Pips", size=80, color=INK, bold=True, font="Georgia")
    add_text(s, Inches(0.9), Inches(3.25), Inches(6.2), Inches(1.2),
             "A cute social desktop pet for Windows - friends, chat & a shop",
             size=24, color=INK)
    add_text(s, Inches(0.9), Inches(4.55), Inches(6.2), Inches(0.9),
             f"Version {version}  ·  {latest['date']}  ·  Made by {app_owner()} (owner)\nContact: 73946469+juw12@users.noreply.github.com\nEnglish · Bahasa Indonesia · 繁體中文 · 简体中文",
             size=15, color=MUTED)
    add_text(s, Inches(0.9), Inches(6.3), Inches(6.5), Inches(0.5),
             SITE_URL, size=14, color=RED, bold=True)
    if asset("hero_pets.png"):
        add_card(s, Inches(7.4), Inches(1.2), Inches(5.4), Inches(4.4))
        add_picture_fit(s, asset("hero_pets.png"), Inches(7.6), Inches(1.4),
                        Inches(5.0), Inches(4.0), border=False)
        add_caption(s, Inches(7.4), Inches(5.75), Inches(5.4),
                    "Pick from 23 animals - land, water and sky")

    # 2. What is Pips -----------------------------------------------------
    s = new_slide("What is Pips?", "Overview")
    add_bullets(s, Inches(0.6), Inches(1.8), Inches(7.2), Inches(5), [
        ("A desk buddy.", "A little animal lives on your desktop - land animals walk on the taskbar and "
         "on top of windows, water animals swim and splash, flyers fly around. It naps, chats and reacts "
         "when you play with it."),
        ("A social pet.", "Click the pet to open the Pips hub: friends, chat, groups, "
         "and a shop with props and moves for your pet."),
        ("A focus timer.", "Right-click → Focus timer: a 25-minute Pomodoro with soundscapes."),
        ("Gentle reminders.", "Breaks, drinking water, 20-20-20 eye rest and your daily "
         "goal pop up as friendly chat bubbles."),
        ("Insights.", "A daily 1-5 star focus score, app-usage tracking and charts in "
         "the dashboard."),
        ("Private.", "Everything stays in the Pips folder on your PC. Nothing is uploaded."),
        ("4 languages.", "English, Bahasa Indonesia, 繁體中文, 简体中文."),
    ], size=18, gap=10)
    if asset("card.png"):
        add_picture_fit(s, asset("card.png"), Inches(8.3), Inches(1.7),
                        Inches(2.6), Inches(3.9))
        add_caption(s, Inches(8.0), Inches(5.65), Inches(3.2), "The Pips card")
    if asset("bee_taskbar.png"):
        add_picture_fit(s, asset("bee_taskbar.png"), Inches(10.9), Inches(3.4),
                        Inches(2.1), Inches(1.6))
        add_caption(s, Inches(10.8), Inches(5.05), Inches(2.3), "Your pet on the taskbar")

    # 3. Install ----------------------------------------------------------
    s = new_slide("Install in 3 steps", "Getting started")
    steps = [
        ("Download", "Click Download Pips for Windows at\n" + SITE_URL),
        ("Install", "Open Pips-Setup.exe, then click Install Pips.\nPython and every helper are included. Nothing to unzip."),
        ("Meet your pet", "Pips opens automatically. Choose your language.\nDesktop and Start Menu shortcuts are ready for next time."),
    ]
    for index, (title, body) in enumerate(steps):
        x = Inches(0.6 + index * 4.2)
        add_card(s, x, Inches(1.9), Inches(3.9), Inches(3.1))
        add_step_badge(s, x + Inches(0.25), Inches(2.15), index + 1)
        add_text(s, x + Inches(0.9), Inches(2.13), Inches(2.9), Inches(0.6),
                 title, size=22, bold=True)
        add_text(s, x + Inches(0.25), Inches(2.9), Inches(3.45), Inches(2.0),
                 body, size=14, color=INK)
    add_card(s, Inches(0.6), Inches(5.35), Inches(12.2), Inches(1.45),
             fill=GREEN_SOFT, line=GREEN)
    add_bullets(s, Inches(0.85), Inches(5.45), Inches(11.8), Inches(1.3), [
        ("Windows 10/11, 64-bit:", "no separate Python setup, terminal or administrator password."),
        ("Already have Pips?", "use Help → Check for updates in your current copy. No reinstall needed."),
    ], size=14, gap=4)

    # 4. First run --------------------------------------------------------
    s = new_slide("First start: language + interactive guide", "Getting started")
    if asset("lang_picker.png"):
        add_picture_fit(s, asset("lang_picker.png"), Inches(0.6), Inches(1.8),
                        Inches(3.9), Inches(4.1))
        add_caption(s, Inches(0.6), Inches(5.95), Inches(3.9), "① Pick your language")
    if asset("tour_welcome.png"):
        add_picture_fit(s, asset("tour_welcome.png"), Inches(4.8), Inches(1.8),
                        Inches(4.3), Inches(2.9))
        add_caption(s, Inches(4.8), Inches(4.75), Inches(4.3),
                    "② Your pet says hi and offers a 1-minute guide")
    add_bullets(s, Inches(9.4), Inches(1.8), Inches(3.6), Inches(5), [
        "A language window appears once. You can change it later (right-click the pet → Language).",
        "Then your pet walks you through the basics - just follow the bubble.",
        "Press “Skip guide” anytime.",
        ("Replay:", "right-click the pet → Help → Interactive guide."),
    ], size=15, gap=8)

    # 5. Guide steps ------------------------------------------------------
    s = new_slide("The interactive guide, step by step", "Getting started")
    rows = [
        ["Step", "What the pet asks", "What it teaches"],
        ["1", "Click me once", "Clicking the pet opens the Pips hub (friends, chat, shop)"],
        ["2", "Click the ring", "Start / pause a 25-minute focus session; – or ✕ hides the card"],
        ["3", "Drag me anywhere", "Move the pet; land animals drop onto a window or the taskbar, water animals "
                                  "dive back in, flyers stay where you let go"],
        ["4", "Rub your mouse over me", "Petting makes it happy (hearts!)"],
        ["5", "Right-click me", "The menu: snacks, sleep, animals, colours, language, settings"],
        ["6", "I have moods!", "Keep it happy: pet, feed and finish pomodoros"],
    ]
    add_table(s, Inches(0.6), Inches(1.8), Inches(7.6), rows,
              [Inches(0.8), Inches(2.6), Inches(4.2)], size=13, row_h=Inches(0.55))
    pics = [("tour_click.png", "Step 1"), ("tour_drag.png", "Step 3"),
            ("tour_pet.png", "Step 4"), ("tour_menu.png", "Step 5")]
    for index, (name, cap) in enumerate(pics):
        if not asset(name):
            continue
        col, row = index % 2, index // 2
        x = Inches(8.5 + col * 2.35)
        y = Inches(1.8 + row * 2.45)
        add_picture_fit(s, asset(name), x, y, Inches(2.2), Inches(1.95))
        add_caption(s, x, y + Inches(1.98), Inches(2.2), cap)

    # 6. Pet interactions -------------------------------------------------
    s = new_slide("Playing with your pet", "Your pet")
    rows = [
        ["Do this", "What happens"],
        ["Click", "Giggles and opens the Pips hub (friends, chat, groups, shop)"],
        ["Drag & drop", "Carry it anywhere. Land animals fall onto the window edge below (or the taskbar), "
                        "water animals splash back into the water, flyers hover where you let go"],
        ["Rub the mouse back and forth", "Petting ♥ - happiness and bond go up"],
        ["Shake while dragging", "Dizzy! (don't overdo it)"],
        ["Right-click", "Menu: snack, sleep / wake, Animal, colours, size, Language, Help…"],
        ["Give a snack ♨", "Fills its tummy (right-click → Give a snack)"],
        ["Leave the PC", "It naps after a few minutes and wakes up when you're back"],
        ["Listen ♪", "51 animal voices that change with its mood. Sing & dance tricks play a song "
                                 "you pick: right-click → Do a trick → Song"],
        ["Battle ⚔", "Arena duels: compare stats (level + gear), pick 3 skills, choose best of 1/3/5 rounds "
                     "and take turns until one pet is knocked out - press the skills yourself or switch on 🤖 Auto"],
        ["Games 🎲", "5 minigames - Treat Catch, Bonk the Bugs, Memory Match, Pet Says, Hop Hop - alone or "
                     "with up to 7 friends (same round, live scores), plus Pet Duel "
                     "(Hub → Play → Minigames or right-click → Games)"],
        ["Weather ☔", "Live weather around your pet (rain cloud + umbrella, snow, sun, stars, storms). "
                      "Right-click → Weather & location to follow your exact spot or pick a place"],
    ]
    add_table(s, Inches(0.6), Inches(1.8), Inches(8.0), rows,
              [Inches(3.0), Inches(5.0)], size=14, row_h=Inches(0.52))
    if asset("tour_menu.png"):
        add_picture_fit(s, asset("tour_menu.png"), Inches(8.9), Inches(1.8),
                        Inches(4.1), Inches(2.8))
        add_caption(s, Inches(8.9), Inches(4.65), Inches(4.1), "Right-click = everything")
    add_text(s, Inches(8.9), Inches(5.2), Inches(4.1), Inches(1.5),
             "Just want to tickle? Right-click → When I click → "
             "Only tickle (never open Pips).", size=13, color=MUTED)

    # 7. Animals ----------------------------------------------------------
    s = new_slide("23 animals in 3 groups", "Your pet")
    if asset("species_sheet.png"):
        add_picture_fit(s, asset("species_sheet.png"), Inches(0.6), Inches(1.75),
                        Inches(7.7), Inches(4.6))
    add_bullets(s, Inches(8.6), Inches(1.8), Inches(4.4), Inches(5), [
        ("Land (9):", "cat, dog, bunny, bear, panda, fox, hamster, pig, sprout - "
         "they walk & hop."),
        ("Water (8):", "fish, whale, octopus, axolotl, penguin, turtle, frog, "
         "jellyfish - they swim with bubbles."),
        ("Sky (6):", "bird, owl, bat, bee, butterfly, dragon - they hover and flap."),
        ("Change:", "right-click → Animal, or the tabs in Pet status & settings."),
        ("Make it yours:", "name, body colour, cheek colour and size S / M / L."),
        ("Own voice ♪:", "every animal sounds different - happy, sleepy, hungry or grumpy."),
    ], size=15, gap=8)

    # 8. Moods ------------------------------------------------------------
    s = new_slide("Moods & needs", "Your pet")
    rows = [
        ["Stat", "Goes up when…", "Goes down when…"],
        ["Happiness", "You pet it, finish pomodoros, hit your goal", "Ignored for long"],
        ["Energy", "It naps (when you're away or via Sleep)", "Time awake, lots of play"],
        ["Food", "You give a snack", "Slowly over the day"],
        ["Bond", "Every interaction, every day together", "(never really drops)"],
    ]
    add_table(s, Inches(0.6), Inches(1.8), Inches(8.2), rows,
              [Inches(1.6), Inches(3.8), Inches(2.8)], size=14, row_h=Inches(0.6))
    add_text(s, Inches(0.6), Inches(5.1), Inches(8.2), Inches(1.6),
             "Moods you'll see: very happy · happy · okay · sad · very sad · tired · "
             "hungry · grumpy · sleeping.\nCheck them anytime: right-click → "
             "Pet status & settings.", size=15, color=INK)
    if asset("tour_moods.png"):
        add_picture_fit(s, asset("tour_moods.png"), Inches(9.1), Inches(1.8),
                        Inches(3.9), Inches(2.7))
    if asset("tour_finish.png"):
        add_picture_fit(s, asset("tour_finish.png"), Inches(9.1), Inches(4.7),
                        Inches(3.9), Inches(2.1))

    # 9. Pomodoro card ----------------------------------------------------
    s = new_slide("Focus timer (right-click → Focus timer)", "Focus")
    if asset("tour_card.png"):
        add_picture_fit(s, asset("tour_card.png"), Inches(0.6), Inches(1.7),
                        Inches(3.6), Inches(5.2))
    add_bullets(s, Inches(4.6), Inches(1.8), Inches(8.3), Inches(5.2), [
        ("Start:", "click the ring or ► Start. 25 min focus → 5 min break; every "
         "4th break is longer."),
        ("Pause / reset:", "click the ring again, or ↻ Reset."),
        ("Sound:", "rain, thunder, brown noise, café, ocean, forest, fireplace, "
         "crickets, wind - pick one and set its volume (♪ button)."),
        ("Status pill:", "shows what you're doing now (Focus / Idle / Break) and the "
         "app in front."),
        ("Score:", "★ 1-5 focus score for today, updated live."),
        ("Hide:", "– or ✕ tucks the card back into your pet. Click the pet to bring it back."),
        ("While you focus,", "your pet wears a little headband and nudges you if you "
         "drift to distracting apps."),
    ], size=16, gap=9)

    # 10. Reminders & dashboard ------------------------------------------
    s = new_slide("Reminders, goals & dashboard", "Focus")
    add_bullets(s, Inches(0.6), Inches(1.8), Inches(6.1), Inches(5), [
        ("Reminders as bubbles:", "stand-up breaks, drink water, 20-20-20 eye rest, "
         "pomodoro done, daily goal reached."),
        ("Daily goal & streaks:", "set a pomodoro goal; streaks and XP keep you going."),
        ("Coach tips:", "short, friendly suggestions based on your day."),
        ("Turn bubbles off:", "Pet status & settings → uncheck “Show reminders & "
         "notifications as chat bubbles” (you'll get normal notifications)."),
    ], size=16, gap=10)
    add_card(s, Inches(7.0), Inches(1.8), Inches(5.9), Inches(4.8))
    add_text(s, Inches(7.3), Inches(1.95), Inches(5.4), Inches(0.5),
             "Dashboard (right-click the card → Dashboard)", size=16, bold=True)
    add_bullets(s, Inches(7.3), Inches(2.55), Inches(5.4), Inches(4.0), [
        "Focus timeline of today",
        "Hour-of-day heatmap",
        "7-day focus score trend",
        "Pomodoros per day",
        "Top apps & time per app",
        "History, weekly and monthly reports",
    ], size=15, gap=6)

    # 10b. Friends, levels & achievements --------------------------------
    s = new_slide("Friends, profiles, ranks & badges", "Together")
    add_bullets(s, Inches(0.6), Inches(1.8), Inches(6.1), Inches(5), [
        ("Levels & XP:", "every finished pomodoro gives XP - level up as you focus."),
        ("22 achievements:", "first tomato, 4 / 8 in a day, 3 / 7 / 14-day streaks, early bird, "
         "night owl, pet lover... Your pet celebrates each one."),
        ("Open:", "click the pet (or right-click \u2192 Pips hub\u2026)."),
        ("Works offline:", "levels and achievements are calculated on your PC."),
    ], size=16, gap=10)
    add_card(s, Inches(7.0), Inches(1.8), Inches(5.9), Inches(4.8))
    add_text(s, Inches(7.3), Inches(1.95), Inches(5.4), Inches(0.5),
             "Friends (optional, off by default)", size=16, bold=True)
    add_bullets(s, Inches(7.3), Inches(2.55), Inches(5.4), Inches(4.0), [
        "Turn on Friends, swap 6-letter friend codes",
        "Chat with friends and in your own groups",
        "Wave, pet, snack, high five, visit - point at a visiting pet's name tag to play",
        "Poke, tickle, tease, boop or hug a friend from afar - it pops up on their screen",
        "Affinity: friendship grows from Acquaintance to Soulmates",
        "Create a private group and invite friends",
        "Ranks: everyone on Pips by XP, badges or streak",
        "Profile: photo, bio, info, status (online / busy / away) - public or private",
        "Label friends: Bro, Bestie, Partner, Soulmate...",
        "Never shared: apps, window titles, scores, files",
        "One click turns it off and deletes your online data",
    ], size=13, gap=4)

    # 10c. Shop, coins, props & moves -----------------------------------
    s = new_slide("Shop: props & moves for your pet", "Together")
    add_bullets(s, Inches(0.6), Inches(1.8), Inches(6.1), Inches(5), [
        ("Open:", "click the pet \u2192 Shop tab (or right-click \u2192 Shop\u2026)."),
        ("Coins:", "level up +40, badges +25, open Pips each day +10, friendship +15 per "
         "affinity level, wave / chat / visit a friend +2, pomodoro +5. Start gift: 100."),
        ("Props:", "17 to collect - crown, wizard hat, headphones, shades, scarf, cape, wings... "
         "Wear one per slot: head, face, neck, back."),
        ("Moves:", "spin, dance, moonwalk, backflip, kung fu, spider swing. Right-click \u2192 "
         "Do a trick - your happy pet also shows off on its own."),
        ("Friends see it:", "your props appear on your avatar, and your pet does a move "
         "when it visits a friend's desktop."),
    ], size=15, gap=9)
    if asset("shop.png"):
        add_picture_fit(s, asset("shop.png"), Inches(7.6), Inches(1.6),
                        Inches(4.6), Inches(5.3))
        add_caption(s, Inches(7.6), Inches(6.95), Inches(4.6), "The Shop tab")

    # 11. Customize -------------------------------------------------------
    s = new_slide("Make it yours: Pet status & settings", "Customize")
    if asset("settings.png"):
        add_picture_fit(s, asset("settings.png"), Inches(0.6), Inches(1.65),
                        Inches(2.9), Inches(5.3))
        add_caption(s, Inches(0.4), Inches(6.98), Inches(3.3), "(shown in Bahasa Indonesia)")
    add_text(s, Inches(3.8), Inches(1.75), Inches(9), Inches(0.5),
             "Right-click the pet → Pet status & settings…", size=17, bold=True, color=RED)
    add_bullets(s, Inches(3.8), Inches(2.35), Inches(4.5), Inches(4.6), [
        ("Status:", "mood + happiness, energy, food, bond bars."),
        ("Animal:", "Land / Water / Sky tabs - click a tile."),
        ("Look:", "name, colour, cheeks, size S / M / L."),
        ("Language:", "switch instantly."),
    ], size=15, gap=9)
    add_bullets(s, Inches(8.5), Inches(2.35), Inches(4.5), Inches(4.6), [
        "Click action: open hub · open focus timer · tickle (double-click opens) · only tickle",
        "Show reminders as chat bubbles",
        "Move around by myself (walk on windows / swim & splash / fly)",
        "Nap when I'm away",
        "Open the card next to the pet",
        "Chatty (little messages)",
        "Hide during full-screen apps",
        "Check for updates automatically",
        "Animal sounds + volume (quiet while focusing)",
        "Dark mode (also: right-click the pet \u2192 Dark mode)",
    ], size=14, gap=3)

    # 12. Languages -------------------------------------------------------
    s = new_slide("Languages", "Customize")
    langs = [("English", "Hello!"), ("Bahasa Indonesia", "Halo!"),
             ("繁體中文", "你好！"), ("简体中文", "你好！")]
    for index, (name, hello) in enumerate(langs):
        x = Inches(0.6 + index * 3.1)
        add_card(s, x, Inches(1.9), Inches(2.85), Inches(1.6))
        add_text(s, x, Inches(2.1), Inches(2.85), Inches(0.6), name, size=20,
                 bold=True, align=PP_ALIGN.CENTER, font="Microsoft JhengHei")
        add_text(s, x, Inches(2.75), Inches(2.85), Inches(0.5), hello, size=15,
                 color=MUTED, align=PP_ALIGN.CENTER, font="Microsoft JhengHei")
    add_bullets(s, Inches(0.6), Inches(3.9), Inches(12), Inches(3), [
        ("Change anytime:", "right-click the pet (or the card) → Language. Everything "
         "switches instantly - menus, bubbles, reminders, the card."),
        ("First start:", "Pips guesses from your Windows language and asks once."),
        ("Update notes", "are shown in your language too."),
    ], size=17, gap=10)

    # 13. Updates ---------------------------------------------------------
    s = new_slide("Updates - right inside Pips", "New in 2.1")
    if asset("update_offer.png"):
        add_picture_fit(s, asset("update_offer.png"), Inches(0.6), Inches(1.8),
                        Inches(5.4), Inches(3.6))
        add_caption(s, Inches(0.6), Inches(5.45), Inches(5.4),
                    "Example of an update notice (sample version text)")
    add_bullets(s, Inches(6.4), Inches(1.8), Inches(6.5), Inches(5), [
        ("Automatic check:", "shortly after start and every 6 hours (needs internet)."),
        ("Your pet tells you", "what's new and offers: Update now · Later · Skip this version."),
        ("Update now:", "downloads, verifies (SHA-256), installs and restarts Pips "
         "by itself - about 10 seconds."),
        ("Your stuff is safe:", "settings, pet, history and reports are never touched. "
         "The old version is backed up in the backup\\ folder."),
        ("Manual check:", "right-click the pet → Help → Check for updates."),
        ("Don't want it?", "Pet status & settings → uncheck “Check for updates "
         "automatically”."),
    ], size=15, gap=9)

    # 14. FAQ -------------------------------------------------------------
    s = new_slide("Troubleshooting & FAQ", "Help")
    rows = [
        ["Problem", "Try this"],
        ["Nothing happens when I start", "Use the Pips Desktop / Start Menu shortcut. If needed, quit Pips and run Pips-Setup.exe again."],
        ["“Windows protected your PC”", "The installer is unsigned. For the trusted official download: More info → Run anyway, if allowed. Otherwise ask IT."],
        ["Where is my pet?", "Click the Pips icon next to the clock (it may be under ^) or press Ctrl+Alt+P. "
         "Opening Pips again also brings it back. It hides during full-screen apps."],
        ["My pet is small / a baby", "Pets grow up: baby → child → teen → grown-up, with focus sessions, daily visits, games and cuddles. "
         "Pet settings show the progress; 🍼 Raise again starts over."],
        ["My pet is sick 🤧", "You worked 2.5 h without a 5-minute break. Take a short break (or right-click → Medicine) and it "
         "feels better. Turn off: Pet settings → Behaviour."],
        ["My pet is up on a window / flying", "Land animals walk on window tops, flyers fly anywhere. Right-click → Go to the "
         "taskbar / Rest on the taskbar, or turn off \"Move around by myself\" in Pet settings."],
        ["The focus card is gone", "Right-click the pet → Focus timer → Open. Or restart Pips."],
        ["Update failed", "Try Help → Check for updates later. Installed users can quit Pips and rerun setup; ZIP users should keep their original folder."],
        ["Where are my settings?", "Installed app: %LOCALAPPDATA%\\Programs\\Pips. config.json and data\\ hold your profile and progress; back them up before moving."],
        ["Uninstall", "Quit Pips, then Windows Settings → Apps → Pips → Uninstall. Settings and progress are left in place for a later reinstall."],
    ]
    add_table(s, Inches(0.6), Inches(1.7), Inches(12.2), rows,
              [Inches(3.4), Inches(8.8)], size=12, row_h=Inches(0.45))

    # 15. What's new ------------------------------------------------------
    s = new_slide(f"What's new in {latest['version']}", "Release notes")
    add_text(s, Inches(0.6), Inches(1.7), Inches(12), Inches(0.5),
             f"{latest['title']}  ·  {latest['date']}", size=18, color=RED, bold=True)
    add_bullets(s, Inches(0.6), Inches(2.35), Inches(7.5), Inches(4.5),
                latest["notes"]["en"], size=17, gap=10)
    other = latest["notes"].get("id")
    if other:
        add_card(s, Inches(8.4), Inches(2.3), Inches(4.5), Inches(4.4), fill=GREEN_SOFT,
                 line=GREEN)
        add_text(s, Inches(8.6), Inches(2.4), Inches(4.1), Inches(0.4),
                 "Bahasa Indonesia", size=13, bold=True, color=GREEN)
        add_bullets(s, Inches(8.6), Inches(2.8), Inches(4.1), Inches(3.8),
                    other, size=12, gap=5)

    # 16. Version history -------------------------------------------------
    s = new_slide("Version history", "Release notes")
    rows = [["Version", "Date", "Highlights"]]
    for rel in releases:
        notes = rel["notes"]["en"]
        rows.append([rel["version"], rel["date"],
                     rel["title"] + "\n" + "; ".join(notes[:4])])
    add_table(s, Inches(0.6), Inches(1.8), Inches(12.2), rows,
              [Inches(1.3), Inches(1.6), Inches(9.3)], size=12, row_h=Inches(0.9))

    # 17. Thanks ----------------------------------------------------------
    s = new_slide()
    add_text(s, Inches(0.6), Inches(2.2), Inches(12.1), Inches(1.0),
             "Have fun & stay focused ♥", size=40, bold=True,
             align=PP_ALIGN.CENTER, font="Georgia")
    add_text(s, Inches(0.6), Inches(3.4), Inches(12.1), Inches(1.0),
             f"Pips is made and owned by {app_owner()}\nDownload & updates: {SITE_URL}\n"
             "Questions or ideas? Just reply to the Pips email.", size=18, color=MUTED, align=PP_ALIGN.CENTER)
    if asset("tour_finish.png"):
        add_picture_fit(s, asset("tour_finish.png"), Inches(4.65), Inches(4.6),
                        Inches(4.0), Inches(2.4), border=False)

    prs.core_properties.title = f"Pips {version} - User Guide"
    prs.core_properties.author = app_owner() or "Pips"
    prs.core_properties.subject = "Desktop pet + Pomodoro focus widget"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "Pips-Guide.pptx"))
    args = parser.parse_args()
    path = build(Path(args.out))
    print(path)


if __name__ == "__main__":
    main()

# APP_OWNER is read from app.py
