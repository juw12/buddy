"""diary_export.py -- render the productivity diary to a human-readable PDF.

Reads the same data source the diary uses (``diary.build_json``) and lays it out
as a paginated PDF: a title block, a day-by-day summary table, then one section
per day (KPI line, AI tokens, top apps, coach note).

Layout uses ReportLab for real, selectable text. ReportLab is already present on
this machine; if it (or a font) is missing the caller gets ``""`` and a log line
-- nothing is ever raised to the caller, matching the widget's conventions.

CLI:
    python diary_export.py                 # last 30 days -> <repo>/exports/
    python diary_export.py --days 7
    python diary_export.py --date 2026-10-24 --days 1
    python diary_export.py --out C:\\path\\to\\diary.pdf
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LOG_FILE = HERE / "logs" / "diary.log"

_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_FONT = {"body": "Helvetica", "bold": "Helvetica-Bold", "sym": None}


def log(msg: str) -> None:
    (HERE / "logs").mkdir(parents=True, exist_ok=True)
    line = "[%s] %s" % (dt.datetime.now().isoformat(timespec="seconds"), msg)
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# fonts
# --------------------------------------------------------------------------- #
def _register_fonts() -> None:
    """Prefer the Windows Segoe family (has the star glyphs); else keep Helvetica."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception:
        return

    def reg(name: str, filename: str) -> bool:
        path = _FONTS / filename
        if not path.exists():
            return False
        try:
            pdfmetrics.registerFont(TTFont(name, str(path)))
            return True
        except Exception:
            return False

    if reg("DiaryBody", "segoeui.ttf"):
        _FONT["body"] = "DiaryBody"
        _FONT["bold"] = "DiaryBody"
        if reg("DiaryBold", "segoeuib.ttf"):
            _FONT["bold"] = "DiaryBold"
    # Segoe UI Symbol carries U+2605/U+2606 (the star ratings).
    if reg("DiarySym", "seguisym.ttf"):
        _FONT["sym"] = "DiarySym"


# --------------------------------------------------------------------------- #
# small formatters
# --------------------------------------------------------------------------- #
def _esc(text) -> str:
    return str(text if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _stars_frag(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    if _FONT["sym"]:
        return '<font name="%s">%s</font>' % (_FONT["sym"], "\u2605" * n + "\u2606" * (5 - n))
    return "%d/5" % n


def _hm(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        total = 0
    hours, rem = divmod(max(0, total), 3600)
    minutes = rem // 60
    if hours and minutes:
        return "%dh %dm" % (hours, minutes)
    if hours:
        return "%dh" % hours
    return "%dm" % minutes


def _num(n) -> str:
    try:
        return "{:,}".format(int(n or 0))
    except (TypeError, ValueError):
        return "0"


def _score(s) -> str:
    try:
        return "%.1f" % float(s)
    except (TypeError, ValueError):
        return "0.0"


# --------------------------------------------------------------------------- #
# PDF build
# --------------------------------------------------------------------------- #
def build_pdf(con, cfg, repo_dir, out_path=None, days: int = 30, data: dict | None = None) -> str:
    """Write the diary PDF and return its path (or ``""`` on failure)."""
    try:
        import diary
    except Exception:
        log("diary_export: cannot import diary module\n" + traceback.format_exc())
        return ""

    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception:
        log("diary_export: reportlab unavailable\n" + traceback.format_exc())
        return ""

    _register_fonts()

    if data is None:
        data = diary.build_json(con, cfg, days=days)
    records = data.get("days", []) or []
    totals = data.get("totals", {}) or {}

    repo_dir = Path(repo_dir)
    if out_path is None:
        out_dir = repo_dir / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / ("Productivity-Diary-%s.pdf" % dt.date.today().isoformat())
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    body_name = _FONT["body"]
    bold_name = _FONT["bold"]

    title_style = ParagraphStyle("title", fontName=bold_name, fontSize=22, leading=26, spaceAfter=2 * mm)
    sub_style = ParagraphStyle("sub", fontName=body_name, fontSize=9.5, leading=13, textColor=colors.HexColor("#555555"))
    h2_style = ParagraphStyle("h2", fontName=bold_name, fontSize=13, leading=16, spaceBefore=4 * mm, spaceAfter=1.5 * mm)
    h3_style = ParagraphStyle("h3", fontName=bold_name, fontSize=10, leading=13, spaceBefore=2.5 * mm, spaceAfter=1 * mm, textColor=colors.HexColor("#333333"))
    body_style = ParagraphStyle("body", fontName=body_name, fontSize=10, leading=14, alignment=TA_LEFT)
    kpi_style = ParagraphStyle("kpi", fontName=body_name, fontSize=10.5, leading=15)

    story = []
    story.append(Paragraph("Productivity Diary", title_style))
    generated = _esc(data.get("generated_at", ""))
    story.append(
        Paragraph(
            "Generated %s &nbsp;&middot;&nbsp; %d days &nbsp;&middot;&nbsp; avg score %s "
            "&nbsp;&middot;&nbsp; %s pomodoros &nbsp;&middot;&nbsp; %s AI tokens"
            % (
                generated,
                int(totals.get("days", len(records)) or 0),
                _score(totals.get("score_avg", 0)),
                _num(totals.get("pomodoros_total", 0)),
                _num(totals.get("tokens_total", 0)),
            ),
            sub_style,
        )
    )
    story.append(Spacer(1, 4 * mm))

    if not records:
        story.append(Paragraph("No diary data available yet.", body_style))
    else:
        # Summary table
        header = ["Date", "Day", "Score", "Stars", "Pomos", "Deep Work", "AI Tokens"]
        rows = [[Paragraph("<b>%s</b>" % h, body_style) for h in header]]
        for rec in records:
            rows.append(
                [
                    Paragraph(_esc(rec.get("day", "")), body_style),
                    Paragraph(_esc(rec.get("weekday", "")), body_style),
                    Paragraph(_score(rec.get("score", 0)), body_style),
                    Paragraph(_stars_frag(rec.get("stars", 0)), body_style),
                    Paragraph(str(int(rec.get("pomodoros", 0) or 0)), body_style),
                    Paragraph(_hm(rec.get("deep_work_seconds", 0)), body_style),
                    Paragraph(_num((rec.get("tokens") or {}).get("total", 0)), body_style),
                ]
            )
        col_w = [24 * mm, 22 * mm, 15 * mm, 24 * mm, 15 * mm, 20 * mm, 26 * mm]
        table = Table(rows, colWidths=col_w, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#efefe6")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#999999")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)

        story.append(PageBreak())
        story.append(Paragraph("Daily entries", h2_style))

        app_hdr = ["App", "Category", "Time"]
        for rec in records:
            day = rec.get("day", "")
            weekday = rec.get("weekday", "")
            story.append(Paragraph("%s &mdash; %s" % (_esc(day), _esc(weekday)), h2_style))
            story.append(
                Paragraph(
                    "Score: <b>%s</b>/100 &nbsp;&middot;&nbsp; Stars: %s &nbsp;&middot;&nbsp; "
                    "Pomodoros: %d &nbsp;&middot;&nbsp; Deep work: %s"
                    % (_score(rec.get("score", 0)), _stars_frag(rec.get("stars", 0)),
                       int(rec.get("pomodoros", 0) or 0), _hm(rec.get("deep_work_seconds", 0))),
                    kpi_style,
                )
            )
            tok = rec.get("tokens") or {}
            story.append(Paragraph("AI tokens", h3_style))
            story.append(
                Paragraph(
                    "Input: %s &nbsp;&middot;&nbsp; Output: %s &nbsp;&middot;&nbsp; Total: %s "
                    "&nbsp;&middot;&nbsp; Requests: %s"
                    % (_num(tok.get("input", 0)), _num(tok.get("output", 0)),
                       _num(tok.get("total", 0)), _num(tok.get("requests", 0))),
                    body_style,
                )
            )
            top_apps = rec.get("top_apps") or []
            story.append(Paragraph("Top apps", h3_style))
            arows = [[Paragraph("<b>%s</b>" % h, body_style) for h in app_hdr]]
            if top_apps:
                for app in top_apps:
                    arows.append(
                        [
                            Paragraph(_esc(app.get("app", "")), body_style),
                            Paragraph(_esc(app.get("category", "")), body_style),
                            Paragraph(_hm(app.get("seconds", 0)), body_style),
                        ]
                    )
            else:
                arows.append([Paragraph("none", body_style), Paragraph("-", body_style), Paragraph("-", body_style)])
            atable = Table(arows, colWidths=[70 * mm, 34 * mm, 24 * mm], repeatRows=1)
            atable.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5ee")),
                        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]
                )
            )
            story.append(atable)
            coach = (rec.get("coach") or "").strip()
            if coach:
                story.append(Paragraph("Coach", h3_style))
                story.append(Paragraph(_esc(coach), body_style))
            story.append(Spacer(1, 3 * mm))

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(body_name, 8)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, "Productivity Diary \u00b7 page %d" % doc_.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title="Productivity Diary",
        author="Productivity Widget",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)

    # Keep a rolling "latest" copy alongside the dated file.
    try:
        shutil.copyfile(str(out_path), str(out_path.parent / "Productivity-Diary.pdf"))
    except OSError:
        pass

    log("diary_export: wrote %s (%d bytes)" % (out_path, out_path.stat().st_size))
    return str(out_path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export the productivity diary to PDF")
    parser.add_argument("--date", default=None, help="reference day YYYY-MM-DD (default today)")
    parser.add_argument("--days", type=int, default=30, help="how many days to include (default 30)")
    parser.add_argument("--out", default=None, help="output PDF path")
    args = parser.parse_args(argv)

    import sqlite3

    import tracker

    cfg = tracker.load_config()
    diary_cfg = cfg.get("diary", {}) or {}
    repo_dir = diary_cfg.get("repo_dir") or str(HERE.parent / "productivity-diary")

    con = sqlite3.connect(str(HERE / "data" / "productivity.db"))
    tracker.ensure_db(con)

    path = build_pdf(con, cfg, repo_dir, out_path=args.out, days=args.days)
    if path:
        print("PDF:", path)
        return 0
    print("PDF export failed (see logs/diary.log)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
