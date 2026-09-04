"""Generate the two project PDFs from the repo's own result files.

    python docs/build_docs.py

Every figure quoted in either document is read out of bench/results/*.json,
which are written by the benchmark commands themselves. Nothing here is typed
by hand, so the documents cannot drift from the code the way a hand-maintained
spec does.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Dict, List, Optional

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                ListFlowable, ListItem, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Preformatted,
                                Spacer, Table, TableStyle)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "bench", "results")
OUT = os.path.dirname(os.path.abspath(__file__))

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
ACCENT = colors.HexColor("#0F766E")
RULE = colors.HexColor("#D1D5DB")
BOXBG = colors.HexColor("#F3F4F6")
CODEBG = colors.HexColor("#F7F7F8")
GOOD = colors.HexColor("#15803D")
WARN = colors.HexColor("#B45309")


# ----------------------------------------------------------------- data access

def load(name: str) -> Optional[dict]:
    path = os.path.join(RESULTS, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rupees(x: float, unit: bool = True) -> str:
    if abs(x) >= 1e7:
        return f"Rs {x / 1e7:,.2f} cr" if unit else f"{x / 1e7:,.2f} cr"
    if abs(x) >= 1e5:
        return f"Rs {x / 1e5:,.2f} L" if unit else f"{x / 1e5:,.2f} L"
    return f"Rs {x:,.0f}" if unit else f"{x:,.0f}"


class Facts:
    """Everything the documents quote, resolved once from disk."""

    def __init__(self) -> None:
        self.exp = load("experiment")
        self.val = load("validation")
        self.sweep = load("sweep")
        self.stress = load("stress")
        missing = [n for n, v in (("experiment", self.exp), ("validation", self.val))
                   if v is None]
        if missing:
            raise SystemExit(
                f"missing {', '.join(missing)}.json in bench/results -- run:\n"
                f"  python -m revenueguard.experiment --days 2 "
                f"--json bench/results/experiment.json\n"
                f"  python -m revenueguard.validate --seeds 8 --days 2 "
                f"--json bench/results/validation.json")

        v = self.val["summary"]
        self.rec = v["recovered_inr"]
        self.shr = v["share_of_exposure"]
        self.srg = v["sr_gain_pp"]
        self.act = v["actions"]
        self.rbk = v["rollbacks"]
        self.seeds = self.rec["n"]
        self.days = self.val["config"]["days"]
        self.losses = self.val["seeds_with_loss"]

        e = self.exp
        self.attempts = e["attempts"]
        self.ctrl = e["control"]
        self.treat = e["treatment"]
        self.recovered = e["recovered"]
        self.net = e.get("net", {})

        # stress.json is optional; the caps section is skipped without it.
        self.caps = (self.stress or {}).get("settings", [])
        self.shipped_cap = (self.stress or {}).get("shipped")

        self.sens = load("sensitivity") or {}
        self.sens_cells = self.sens.get("cells", [])
        self.harmful = self.sens.get("harmful_curves", [])
        self.beneficial = self.sens.get("beneficial_curves", [])
        self.worst_gap = self.sens.get("worst_relative_gap", 0.0)


# ------------------------------------------------------------------- styling

def styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle(
        "title", parent=base["Title"], fontName="Helvetica-Bold",
        fontSize=30, leading=34, textColor=INK, spaceAfter=6, alignment=0)
    s["subtitle"] = ParagraphStyle(
        "subtitle", parent=base["Normal"], fontName="Helvetica",
        fontSize=13.5, leading=19, textColor=MUTED, spaceAfter=18, alignment=0)
    s["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, leading=20, textColor=INK, spaceBefore=20, spaceAfter=8,
        keepWithNext=1)
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, leading=16, textColor=ACCENT, spaceBefore=14, spaceAfter=5,
        keepWithNext=1)
    s["body"] = ParagraphStyle(
        "body", parent=base["BodyText"], fontName="Helvetica",
        fontSize=9.6, leading=14.4, textColor=INK, alignment=TA_JUSTIFY,
        spaceAfter=7)
    s["lead"] = ParagraphStyle(
        "lead", parent=s["body"], fontSize=11, leading=16.5, spaceAfter=9)
    s["small"] = ParagraphStyle(
        "small", parent=s["body"], fontSize=8.3, leading=12, textColor=MUTED)
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], alignment=0, spaceAfter=3.5)
    s["code"] = ParagraphStyle(
        "code", parent=base["Code"], fontName="Courier", fontSize=8,
        leading=11, textColor=INK, backColor=CODEBG,
        borderPadding=(6, 6, 6, 6), spaceBefore=4, spaceAfter=9)
    s["kpi"] = ParagraphStyle(
        "kpi", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=17,
        leading=20, textColor=INK, alignment=TA_CENTER)
    s["kpilabel"] = ParagraphStyle(
        "kpilabel", parent=base["Normal"], fontName="Helvetica", fontSize=7.6,
        leading=10, textColor=MUTED, alignment=TA_CENTER)
    s["cell"] = ParagraphStyle(
        "cell", parent=base["Normal"], fontName="Helvetica", fontSize=8.4,
        leading=11.5, textColor=INK)
    s["cellb"] = ParagraphStyle(
        "cellb", parent=s["cell"], fontName="Helvetica-Bold")
    s["quote"] = ParagraphStyle(
        "quote", parent=s["body"], leftIndent=10, rightIndent=10,
        borderPadding=(9, 9, 9, 9), backColor=BOXBG, spaceBefore=5,
        spaceAfter=10, alignment=0)
    return s


S = styles()


def P(text, style="body"):
    return Paragraph(text, S[style])


def together(*flowables):
    """Keep a heading with whatever it introduces.

    Without this a section heading strands itself at the foot of a page
    while its diagram flows to the next one.
    """
    return KeepTogether(list(flowables))


def bullets(items: List[str], style="bullet"):
    return ListFlowable(
        [ListItem(Paragraph(t, S[style]), leftIndent=14, value="circle")
         for t in items],
        bulletType="bullet", start="circle", leftIndent=12,
        bulletFontSize=5, spaceAfter=8)


def code(text: str):
    return Preformatted(text.strip("\n"), S["code"])


def table(rows, widths, header=True, align_right=(), zebra=True):
    data = []
    for r, row in enumerate(rows):
        out = []
        for c, cell in enumerate(row):
            st = "cellb" if (header and r == 0) else "cell"
            if isinstance(cell, str):
                out.append(Paragraph(cell, S[st]))
            else:
                out.append(cell)
        data.append(out)

    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK if header else RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.35, RULE),
    ]
    if zebra:
        for i in range(1, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i),
                              colors.HexColor("#FAFAFA")))
    for c in align_right:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t = Table(data, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle(style))
    return t


def kpis(items):
    """A row of headline numbers."""
    cells = [[Paragraph(v, S["kpi"]) for v, _ in items],
             [Paragraph(l, S["kpilabel"]) for _, l in items]]
    w = 495 / len(items)
    t = Table(cells, colWidths=[w] * len(items), hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BOXBG),
        ("TOPPADDING", (0, 0), (-1, 0), 11),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 11),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


# ------------------------------------------------------------------ diagrams

def _box(d, x, y, w, h, label, sub=None, fill=colors.white, stroke=INK,
         bold=True, fs=8.2):
    d.add(Rect(x, y, w, h, fillColor=fill, strokeColor=stroke,
               strokeWidth=0.9, rx=3, ry=3))
    ty = y + h / 2 - (1 if sub is None else 4)
    d.add(String(x + w / 2, ty, label, fontSize=fs,
                 fontName="Helvetica-Bold" if bold else "Helvetica",
                 fillColor=INK, textAnchor="middle"))
    if sub:
        d.add(String(x + w / 2, y + h / 2 - 13, sub, fontSize=6.6,
                     fontName="Helvetica", fillColor=MUTED, textAnchor="middle"))


def _arrow(d, x1, y1, x2, y2, colour=INK, label=None, dashed=False):
    line = Line(x1, y1, x2, y2, strokeColor=colour, strokeWidth=0.9)
    if dashed:
        line.strokeDashArray = [2.5, 2.5]
    d.add(line)
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    size = 4.6
    d.add(Polygon([
        x2, y2,
        x2 - size * math.cos(ang - 0.42), y2 - size * math.sin(ang - 0.42),
        x2 - size * math.cos(ang + 0.42), y2 - size * math.sin(ang + 0.42)],
        fillColor=colour, strokeColor=colour))
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        d.add(String(mx, my + 5, label, fontSize=6.3, textAnchor="middle",
                     fontName="Helvetica", fillColor=MUTED))


def control_loop_diagram() -> Drawing:
    """The minute-by-minute loop, as it actually runs."""
    W, H = 495, 290
    d = Drawing(W, H)
    bw, bh = 96, 34

    row1_y = H - 62
    row2_y = H - 138
    row3_y = H - 214

    # Row 1: observe -> detect -> attribute
    _box(d, 8, row1_y, bw, bh, "1  OBSERVE", "world.step()", fill=BOXBG)
    _box(d, 140, row1_y, bw, bh, "2  DETECT", "Beta posterior")
    _box(d, 272, row1_y, bw, bh, "3  ATTRIBUTE", "lift x coverage")
    _box(d, 404, row1_y, 83, bh, "4  NARRATE", "LLM, display only",
         fill=colors.HexColor("#FFFDF5"), stroke=WARN)

    _arrow(d, 104, row1_y + bh / 2, 138, row1_y + bh / 2)
    _arrow(d, 236, row1_y + bh / 2, 270, row1_y + bh / 2)
    _arrow(d, 368, row1_y + bh / 2, 402, row1_y + bh / 2, colour=WARN,
           dashed=True)

    # Row 2: gate -> act
    _box(d, 272, row2_y, bw, bh, "5  GATE", "policy engine")
    _box(d, 140, row2_y, bw, bh, "6  ACT", "shift weights", fill=colors.white,
         stroke=GOOD)
    _box(d, 404, row2_y, 83, bh, "ESCALATE", "to a human",
         fill=colors.HexColor("#FDF6F6"), stroke=WARN)

    _arrow(d, 320, row1_y, 320, row2_y + bh)
    _arrow(d, 270, row2_y + bh / 2, 238, row2_y + bh / 2, colour=GOOD,
           label="allow")
    _arrow(d, 368, row2_y + bh / 2, 402, row2_y + bh / 2, colour=WARN,
           label="block")

    # Row 3: verify -> rollback / restore
    _box(d, 140, row3_y, bw, bh, "7  VERIFY", "canary + target")
    _box(d, 8, row3_y, bw, bh, "8a ROLLBACK", "target worse",
         fill=colors.HexColor("#FDF6F6"), stroke=WARN)
    _box(d, 272, row3_y, bw, bh, "8b RESTORE", "source healed",
         fill=colors.white, stroke=GOOD)
    _box(d, 404, row3_y, 83, bh, "AUDIT", "every decision", fill=BOXBG)

    _arrow(d, 188, row2_y, 188, row3_y + bh)
    _arrow(d, 138, row3_y + bh / 2, 106, row3_y + bh / 2, colour=WARN)
    _arrow(d, 236, row3_y + bh / 2, 270, row3_y + bh / 2, colour=GOOD)
    _arrow(d, 368, row3_y + bh / 2, 402, row3_y + bh / 2, colour=MUTED)

    # feedback: rollback and restore both return to observe
    d.add(Line(56, row3_y, 56, 16, strokeColor=MUTED, strokeWidth=0.8,
               strokeDashArray=[2.5, 2.5]))
    d.add(Line(56, 16, 460, 16, strokeColor=MUTED, strokeWidth=0.8,
               strokeDashArray=[2.5, 2.5]))
    d.add(Line(460, 16, 460, row1_y - 4, strokeColor=MUTED, strokeWidth=0.8,
               strokeDashArray=[2.5, 2.5]))
    _arrow(d, 460, row1_y - 4, 460, row1_y - 1, colour=MUTED)
    d.add(String(250, 20, "next minute", fontSize=6.5, fontName="Helvetica",
                 fillColor=MUTED, textAnchor="middle"))
    return d


def measurement_diagram() -> Drawing:
    """How the recovery figure is produced."""
    W, H = 495, 152
    d = Drawing(W, H)
    bw, bh = 120, 32

    d.add(String(0, H - 12, "One seed, one incident plan, one demand stream",
                 fontSize=8, fontName="Helvetica-Bold", fillColor=INK))

    _box(d, 8, H - 62, bw, bh, "DEMAND", "seeded per cell", fill=BOXBG)

    _box(d, 190, H - 50, bw + 20, bh, "CONTROL ARM", "detect, may not act")
    _box(d, 190, H - 108, bw + 20, bh, "TREATMENT ARM", "detect and route",
         stroke=GOOD)

    _arrow(d, 130, H - 46, 188, H - 34)
    _arrow(d, 130, H - 46, 188, H - 92)

    _box(d, 356, H - 79, 131, 44, "DIFFERENCE", "= revenue recovered",
         fill=colors.HexColor("#F0FDF9"), stroke=ACCENT)
    _arrow(d, 322, H - 34, 354, H - 50)
    _arrow(d, 322, H - 92, 354, H - 74)

    d.add(String(8, 32, "Guard: total attempts must match exactly across arms.",
                 fontSize=7.4, fontName="Helvetica-Bold", fillColor=INK))
    d.add(String(8, 20, "If they differ the run refuses to report a recovery "
                        "figure at all - the comparison would be invalid.",
                 fontSize=7.4, fontName="Helvetica", fillColor=MUTED))
    d.add(String(8, 7, "Repeated across seeds; the headline is the confidence "
                       "interval, not any single run.",
                 fontSize=7.4, fontName="Helvetica", fillColor=MUTED))
    return d


def layers_diagram() -> Drawing:
    """Where AI is allowed and where it is refused."""
    W, H = 495, 150
    d = Drawing(W, H)

    _box(d, 8, H - 46, 232, 36, "DETERMINISTIC", "statistics, rules, arithmetic",
         fill=colors.HexColor("#F0FDF9"), stroke=ACCENT)
    d.add(String(12, H - 62, "detection - attribution - policy - routing - "
                             "rollback - audit",
                 fontSize=7, fontName="Helvetica", fillColor=INK))
    d.add(String(12, H - 74, "Reproducible. Same input, same decision, forever.",
                 fontSize=7, fontName="Helvetica-Oblique", fillColor=MUTED))

    _box(d, 264, H - 46, 223, 36, "LANGUAGE MODEL", "prose only",
         fill=colors.HexColor("#FFFDF5"), stroke=WARN)
    d.add(String(268, H - 62, "restates a finished attribution for the operator "
                              "console",
                 fontSize=7, fontName="Helvetica", fillColor=INK))
    d.add(String(268, H - 74, "Never decides. Never reaches the ledger. Falls "
                              "back to a template.",
                 fontSize=7, fontName="Helvetica-Oblique", fillColor=MUTED))

    d.add(Line(252, H - 84, 252, H - 6, strokeColor=RULE, strokeWidth=0.8,
               strokeDashArray=[3, 3]))

    d.add(Rect(8, 8, 479, 56, fillColor=BOXBG, strokeColor=RULE,
               strokeWidth=0.6, rx=3, ry=3))
    d.add(String(18, 48, "Why the line sits here",
                 fontSize=7.6, fontName="Helvetica-Bold", fillColor=INK))
    d.add(String(18, 35, "A sampled token in the path of a money-moving action "
                         "cannot be reviewed, reproduced, or defended.",
                 fontSize=7.2, fontName="Helvetica", fillColor=INK))
    d.add(String(18, 24, "Two tests enforce the boundary: the prompt carries no "
                         "raw traffic, and no decision-path module",
                 fontSize=7.2, fontName="Helvetica", fillColor=INK))
    d.add(String(18, 13, "imports the narrator.",
                 fontSize=7.2, fontName="Helvetica", fillColor=INK))
    return d


# ------------------------------------------------------------------ document

class Doc(BaseDocTemplate):
    def __init__(self, path, title, footer):
        super().__init__(path, pagesize=A4,
                         leftMargin=50, rightMargin=50,
                         topMargin=48, bottomMargin=46,
                         title=title, author="A Sachin Kumar",
                         subject="Razorpay AI Buildathon - Track 03")
        self.footer_text = footer
        frame = Frame(self.leftMargin, self.bottomMargin,
                      self.width, self.height, id="body")
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame]),
            PageTemplate(id="main", frames=[frame], onPage=self._chrome),
        ])

    def _chrome(self, canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(50, A4[1] - 36, A4[0] - 50, A4[1] - 36)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(50, A4[1] - 32, "RevenueGuard")
        canvas.drawRightString(A4[0] - 50, A4[1] - 32, self.footer_text)
        canvas.line(50, 34, A4[0] - 50, 34)
        canvas.drawString(50, 24, "Razorpay AI Buildathon - Track 03, "
                                  "AI Revenue Recovery")
        canvas.drawRightString(A4[0] - 50, 24, f"{canvas.getPageNumber()}")
        canvas.restoreState()


def cover(title: str, subtitle: str, blurb: str, f: Facts) -> List:
    return [
        Spacer(1, 150),
        P(title, "title"),
        P(subtitle, "subtitle"),
        Table([[""]], colWidths=[495], rowHeights=[2.2],
              style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]),
              hAlign="LEFT"),
        Spacer(1, 22),
        P(blurb, "lead"),
        Spacer(1, 26),
        kpis([
            (rupees(f.rec["mean"]), f"recovered per {f.days} days (mean of "
                                    f"{f.seeds} seeds)"),
            (f"{f.shr['mean']:.1%}", "of money the incidents put at risk"),
            (f"{f.losses}/{f.seeds}", "seeds where the router lost money"),
        ]),
        Spacer(1, 26),
        P("A Sachin Kumar &nbsp;&middot;&nbsp; "
          f"{date.today().strftime('%d %B %Y')} &nbsp;&middot;&nbsp; "
          "Generated from the repository's own benchmark output", "small"),
        NextPageTemplate("main"),
        PageBreak(),
    ]
