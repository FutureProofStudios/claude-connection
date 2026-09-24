"""Render a small markdown subset to a clean PDF.

Supports: # title, ## section, ### subsection, paragraphs, - bullets (two
levels), 1. numbered items, | tables | (first row is the header), > callouts,
**bold**, *italic*, and a line with only `<<<pagebreak>>>`. `{{key}}` is
replaced from the values dict before rendering, so every number comes from
the engine.
"""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
INK = colors.HexColor("#1b1f24")
MUTED = colors.HexColor("#5b636e")
ACCENT = colors.HexColor("#1f4e8c")
RULE = colors.HexColor("#d6dbe1")
SHADE = colors.HexColor("#f1f4f8")


def _fonts() -> tuple[str, str, str]:
    try:
        pdfmetrics.registerFont(TTFont("Body", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("Body-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("Body-Oblique", str(FONT_DIR / "DejaVuSans-Oblique.ttf")))
        pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Oblique",
                                      boldItalic="Body-Bold")
        return "Body", "Body-Bold", "Body-Oblique"
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def _styles():
    body, bold, _ = _fonts()
    base = dict(fontName=body, textColor=INK, alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("title", fontName=bold, fontSize=22, leading=27, textColor=INK, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", fontName=body, fontSize=10.5, leading=14, textColor=MUTED,
                                   spaceAfter=14),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=14, leading=18, textColor=ACCENT,
                             spaceBefore=14, spaceAfter=6),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=11, leading=14.5, textColor=INK,
                             spaceBefore=8, spaceAfter=3),
        "p": ParagraphStyle("p", fontSize=9.6, leading=13.4, spaceAfter=5, **base),
        "li": ParagraphStyle("li", fontSize=9.6, leading=13.2, leftIndent=14, bulletIndent=3,
                             spaceAfter=2.5, **base),
        "li2": ParagraphStyle("li2", fontSize=9.2, leading=12.6, leftIndent=28, bulletIndent=17,
                              spaceAfter=2, **base),
        "cell": ParagraphStyle("cell", fontSize=8.8, leading=11.4, **base),
        "cellh": ParagraphStyle("cellh", fontName=bold, fontSize=8.8, leading=11.4, textColor=INK),
        "callout": ParagraphStyle("callout", fontSize=9.8, leading=13.8, **base),
    }


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    return text


def fill(text: str, values: dict) -> str:
    def sub(m):
        key = m.group(1).strip()
        if key not in values:
            raise KeyError(f"no value for {{{{{key}}}}}")
        return str(values[key])
    return re.sub(r"\{\{(.+?)\}\}", sub, text)


def _table(rows: list[list[str]], st, width: float) -> Table:
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    data = [[Paragraph(_inline(c), st["cellh" if i == 0 else "cell"]) for c in r] for i, r in enumerate(rows)]
    # Give text-heavy columns more room.
    weights = [max(len(r[j]) for r in rows) + 6 for j in range(ncols)]
    total = sum(weights)
    t = Table(data, colWidths=[width * w / total for w in weights], repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SHADE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _callout(lines: list[str], st, width: float) -> Table:
    paras = [Paragraph(_inline(x), st["callout"]) for x in lines if x.strip()]
    t = Table([[paras]], colWidths=[width], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SHADE),
        ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def render(markdown: str, out: Path, footer: str = "") -> None:
    st = _styles()
    doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.75 * inch, title=footer or "Document")
    width = letter[0] - 1.6 * inch
    story, lines, i = [], markdown.splitlines(), 0
    first_title = True
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.strip() == "<<<pagebreak>>>":
            story.append(PageBreak())
            i += 1
        elif line.startswith("# "):
            story.append(Paragraph(_inline(line[2:]), st["title"]))
            first_title = False
            i += 1
            if i < len(lines) and lines[i].startswith("_") and lines[i].rstrip().endswith("_"):
                story.append(Paragraph(_inline(lines[i].strip("_ ")), st["subtitle"]))
                i += 1
        elif line.startswith("## "):
            story.append(Paragraph(_inline(line[3:]), st["h2"]))
            i += 1
        elif line.startswith("### "):
            # Keep a subheading with the block that follows it.
            head = Paragraph(_inline(line[4:]), st["h3"])
            i += 1
            block = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
                block.append(lines[i])
                i += 1
            sub = []
            for b in block:
                sub.extend(_line_flowables(b, st))
            story.append(KeepTogether([head] + sub))
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            story.append(_table(rows, st, width))
            story.append(Spacer(1, 8))
        elif line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i][1:].strip())
                i += 1
            story.append(_callout(block, st, width))
            story.append(Spacer(1, 8))
        else:
            story.extend(_line_flowables(line, st))
            i += 1

    def page(canvas, doc_):
        canvas.saveState()
        canvas.setFont(st["p"].fontName, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.8 * inch, 0.45 * inch, footer)
        canvas.drawRightString(letter[0] - 0.8 * inch, 0.45 * inch, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page, onLaterPages=page)


def _line_flowables(line: str, st) -> list:
    if line.startswith("  - ") or line.startswith("    - "):
        return [Paragraph(_inline(line.strip()[2:]), st["li2"], bulletText="–")]
    if line.startswith("- "):
        return [Paragraph(_inline(line[2:]), st["li"], bulletText="•")]
    m = re.match(r"^(\d+)\.\s+(.*)", line)
    if m:
        return [Paragraph(_inline(m.group(2)), st["li"], bulletText=f"{m.group(1)}.")]
    return [Paragraph(_inline(line.strip()), st["p"])]
