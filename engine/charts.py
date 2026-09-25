"""Vector figures for the plain-English plan, drawn with reportlab.

Palette: the validated categorical slots 1-3 (blue, orange, aqua), which
pass all-pairs colorblind checks. Every series is direct-labeled, so no
identity depends on color alone. Text uses ink colors, never series colors.
"""
from __future__ import annotations

import datetime as dt

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors

import money as m
from pdf import _fonts

BLUE, ORANGE, AQUA = colors.HexColor("#2a78d6"), colors.HexColor("#eb6834"), colors.HexColor("#1baf7a")
INK, INK2, MUTED = colors.HexColor("#0b0b0b"), colors.HexColor("#52514e"), colors.HexColor("#8a8983")
GRID, SURFACE, TINT = colors.HexColor("#e6e5e1"), colors.HexColor("#fcfcfb"), colors.HexColor("#eef4fc")
WIDTH = 497  # text width of the PDF page in points


def _font():
    body, bold, _ = _fonts()
    return body, bold


def _x_for(d: dt.date, start: dt.date, end: dt.date, x0: float, x1: float) -> float:
    return x0 + (x1 - x0) * (d - start).days / (end - start).days


def timeline(ledger: dict, today: dt.date) -> Drawing:
    """When each goal is done, for three monthly amounts."""
    body, bold = _font()
    rows = [(1000, "+$1,000 a month"), (2500, "+$2,500 a month"), (5000, "+$5,000 a month")]
    lads = [(label, extra, m.ladder(ledger, today, float(extra))) for extra, label in rows]
    end_year = max(l.fund_full.year for _, _, l in lads if l.fund_full) + 1
    start, end = dt.date(today.year, 1, 1), dt.date(end_year, 7, 1)
    h, x0, x1 = 156, 104, WIDTH - 6
    d = Drawing(WIDTH, h)
    # Legend
    lx = x0
    for color, name in ((BLUE, "Paying off cards"), (ORANGE, "Building the emergency fund"), (AQUA, "Investing")):
        d.add(Rect(lx, h - 12, 10, 10, rx=2, ry=2, fillColor=color, strokeColor=None))
        d.add(String(lx + 14, h - 10.5, name, fontName=body, fontSize=8, fillColor=INK2))
        lx += 14 + len(name) * 4.3 + 18
    # Year grid
    base = 16
    for y in range(start.year, end_year + 1):
        gx = _x_for(dt.date(y, 1, 1), start, end, x0, x1)
        d.add(Line(gx, base, gx, h - 24, strokeColor=GRID, strokeWidth=0.6))
        d.add(String(gx, base - 12, str(y), fontName=body, fontSize=7.5, fillColor=MUTED, textAnchor="middle"))
    tx = _x_for(today, start, end, x0, x1)
    d.add(Line(tx, base, tx, h - 24, strokeColor=INK2, strokeWidth=0.8, strokeDashArray=[2, 2]))
    d.add(String(tx, h - 22, "today", fontName=body, fontSize=7, fillColor=INK2, textAnchor="middle"))
    # Rows
    bar_h, gap = 14, 2
    for i, (label, extra, lad) in enumerate(lads):
        y = h - 48 - i * 36
        strong = extra == 2500
        d.add(String(0, y + 4.5, label, fontName=bold if strong else body, fontSize=8.5, fillColor=INK))
        if strong:
            d.add(String(0, y - 6, "the target", fontName=body, fontSize=7, fillColor=INK2))
        segs = [(today, lad.cards_zero, BLUE), (lad.cards_zero, lad.fund_full, ORANGE), (lad.fund_full, end, AQUA)]
        for a, b, color in segs:
            if not a or not b or b <= a:
                continue
            xa = _x_for(max(a, start), start, end, x0, x1)
            xb = min(_x_for(min(b, end), start, end, x0, x1), x1)
            d.add(Rect(xa + gap / 2, y, max(1, xb - xa - gap), bar_h, rx=4, ry=4, fillColor=color, strokeColor=None))
        last_x = None
        for when, what in ((lad.cards_zero, "cards $0"), (lad.fund_full, "fund full")):
            if when and when < end:
                wx = _x_for(when, start, end, x0, x1)
                ly = y - 9
                if last_x is not None and wx - last_x < 90:
                    ly = y + bar_h + 3
                d.add(String(wx, ly, f"{what}: {when:%b %Y}", fontName=body, fontSize=7, fillColor=INK2,
                             textAnchor="middle"))
                last_x = wx
    return d


def growth(ledger: dict, today: dt.date, years: int = 30) -> Drawing:
    """What invested money can grow to: three paths, direct-labeled."""
    body, bold = _font()
    series = [("+$5,000 a month", 5000.0, AQUA), ("+$2,500 a month", 2500.0, BLUE),
              ("Do nothing new", None, ORANGE)]
    paths = [(name, [sum(float(r["balance"]) for r in ledger.get("retirement") or [])]
              + m.wealth_path(ledger, today, extra, years), color) for name, extra, color in series]
    top = max(max(p) for _, p, _ in paths)
    step = 1_000_000 if top > 3_000_000 else 500_000
    ymax = (int(top // step) + 1) * step
    h, x0, x1, y0, y1 = 210, 44, WIDTH - 118, 20, 196
    d = Drawing(WIDTH, h)
    for k in range(0, int(ymax // step) + 1):
        v = k * step
        gy = y0 + (y1 - y0) * v / ymax
        d.add(Line(x0, gy, x1, gy, strokeColor=GRID, strokeWidth=0.6))
        label = "$0" if v == 0 else (f"${v / 1e6:g}M" if v >= 1e6 else f"${v / 1e3:g}K")
        d.add(String(x0 - 6, gy - 2.5, label, fontName=body, fontSize=7.5, fillColor=MUTED, textAnchor="end"))
    for yr in range(0, years + 1, 5):
        gx = x0 + (x1 - x0) * yr / years
        d.add(String(gx, y0 - 13, str(today.year + yr), fontName=body, fontSize=7.5, fillColor=MUTED,
                     textAnchor="middle"))
    ends = []
    for name, path, color in paths:
        pts = [(x0 + (x1 - x0) * i / years, y0 + (y1 - y0) * v / ymax) for i, v in enumerate(path)]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            d.add(Line(ax, ay, bx, by, strokeColor=color, strokeWidth=2))
        ex, ey = pts[-1]
        d.add(Rect(ex - 3, ey - 3, 6, 6, rx=3, ry=3, fillColor=color, strokeColor=colors.white, strokeWidth=1.5))
        ends.append([ey, name, path[-1]])
    # Direct labels at the right, nudged apart so they never collide.
    ends.sort()
    for i in range(1, len(ends)):
        if ends[i][0] - ends[i - 1][0] < 22:
            ends[i][0] = ends[i - 1][0] + 22
    for ey, name, val in ends:
        d.add(String(x1 + 8, ey + 1, m.money(val), fontName=bold, fontSize=8.5, fillColor=INK))
        d.add(String(x1 + 8, ey - 9, name, fontName=body, fontSize=7, fillColor=INK2))
    return d


MACHINE_DEFAULTS = {
    "business": ["Business checking", "the one place", "client money lands"],
    "tax": ["Tax savings", "a set share of every", "payment, moved Fridays"],
    "paycheck": ["Your paycheck", "Payroll puts it in", "personal checking"],
}


def machine(labels: dict | None = None) -> Drawing:
    """Where every dollar goes: the automatic money machine. Account names
    come from the ledger (settings.money_machine) so none live in code."""
    lab = {**MACHINE_DEFAULTS, **(labels or {})}
    body, bold = _font()
    h = 214
    d = Drawing(WIDTH, h)
    bw, bh = 106, 44
    cols = [0, 130, 260, 391]
    top, mid, low = 158, 85, 12

    def box(x, y, title, sub, fill=SURFACE, edge=GRID, tag=None):
        d.add(Rect(x, y, bw, bh, rx=6, ry=6, fillColor=fill, strokeColor=edge, strokeWidth=0.8))
        if tag is not None:
            d.add(Rect(x, y + 6, 3.5, bh - 12, rx=1.5, ry=1.5, fillColor=tag, strokeColor=None))
        d.add(String(x + 10, y + bh - 17, title, fontName=bold, fontSize=8.8, fillColor=INK))
        for k, line in enumerate(sub):
            d.add(String(x + 10, y + bh - 29 - k * 9.5, line, fontName=body, fontSize=7.3, fillColor=INK2))

    def head(x, y):
        d.add(Polygon([x, y, x - 7, y + 3.5, x - 7, y - 3.5], fillColor=INK2, strokeColor=None))

    def fan(x_from, y_from, x_to, ys):
        """Elbow connector: out along a trunk, then across into each box."""
        trunk = x_from + 12
        d.add(Line(x_from, y_from, trunk, y_from, strokeColor=INK2, strokeWidth=1))
        d.add(Line(trunk, min(ys + [y_from]), trunk, max(ys + [y_from]), strokeColor=INK2, strokeWidth=1))
        for y in ys:
            d.add(Line(trunk, y, x_to - 6, y, strokeColor=INK2, strokeWidth=1))
            head(x_to, y)

    c = bh / 2
    box(cols[0], mid, "Clients pay you", ["Every invoice, deposit,", "and licensing fee"], fill=TINT, edge=BLUE)
    box(cols[1], mid, lab["business"][0], lab["business"][1:])
    box(cols[2], top, lab["tax"][0], lab["tax"][1:], tag=ORANGE)
    box(cols[2], mid, "Business costs", ["Payroll taxes, gear loans,", "software, insurance"])
    box(cols[2], low, lab["paycheck"][0], lab["paycheck"][1:])
    box(cols[3], top, "Fixed costs", ["Rent share, bills.", "Autopay."])
    box(cols[3], mid, "Guilt-free spending", ["Debit card. Spend it", "on what you love."])
    box(cols[3], low, "Goal money", ["Friday sweep: cards now,", "then fund, then investing"],
        fill=TINT, edge=BLUE, tag=BLUE)
    d.add(Line(cols[0] + bw, mid + c, cols[1] - 6, mid + c, strokeColor=INK2, strokeWidth=1))
    head(cols[1], mid + c)
    fan(cols[1] + bw, mid + c, cols[2], [top + c, mid + c, low + c])
    fan(cols[2] + bw, low + c, cols[3], [top + c, mid + c, low + c])
    return d


FIGURES = {"timeline": timeline, "growth": growth}


def build(ledger: dict, today: dt.date) -> dict:
    figs = {name: fn(ledger, today) for name, fn in FIGURES.items()}
    figs["machine"] = machine(m.settings(ledger).get("money_machine"))
    return figs
