"""Shared machinery for the spoken-word documents.

There are three of them now - a three-minute pitch, a six-minute pitch and a
ten-minute voiceover - and they all want the same block: a derived timecode,
what is on screen, and the words set large enough to read off a second monitor
while your hands are busy. Three copies of that would be three places for the
layout to drift.

The timecode is derived rather than typed. A hand-written timecode is a number
that goes stale the moment a sentence is cut, and every one of these scripts
has been cut at least twice.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (KeepTogether, Paragraph, Spacer, Table,
                                TableStyle)

from docs.build_docs import ACCENT, INK, RULE, S, table

# Reading-at-a-distance styles. The body face in the other documents is 9.6pt
# justified, which is right for a page you sit with and wrong for a page you
# glance at between clicks.
SAY = ParagraphStyle("say", parent=S["body"], fontSize=12.4, leading=18.2,
                     alignment=0, textColor=INK, spaceAfter=0)
CUE = ParagraphStyle("cue", parent=S["small"], fontSize=8.2, leading=11.4,
                     textColor=colors.HexColor("#1B6FD1"), spaceAfter=0)
TIME = ParagraphStyle("time", parent=S["small"], fontName="Courier-Bold",
                      fontSize=11, leading=13, textColor=ACCENT, spaceAfter=0)
HEAD = ParagraphStyle("shead", parent=S["small"], fontName="Helvetica-Bold",
                      fontSize=9.4, leading=12, textColor=INK, spaceAfter=0)
NOTE = ParagraphStyle("snote", parent=S["small"], fontSize=8.4, leading=11.8,
                      textColor=colors.HexColor("#5A6B8C"), spaceAfter=0)


def words_in(paragraphs: List[str]) -> int:
    """Spoken words only: markup, entities and stage directions do not count."""
    n = 0
    for block in paragraphs:
        plain = re.sub(r"\[[^\]]*\]", "", re.sub(r"<[^>]+>", "", block))
        for entity in ("&mdash;", "&rarr;", "&nbsp;", "&middot;", "&amp;"):
            plain = plain.replace(entity, " ")
        n += len(plain.split())
    return n


class Script:
    """A spoken script that knows how long it is.

    `wpm` is a delivery pace, `runtime` the slot it has to fit, and `pause` the
    breathing room left between segments. Everything else follows from the
    words themselves.
    """

    def __init__(self, wpm: int = 145, runtime: int = 360,
                 pause: float = 3.4) -> None:
        self.wpm, self.runtime, self.pause = wpm, runtime, pause
        self.segments: List[Tuple[str, str, int]] = []
        self.clock_s = 0.0

    def reset(self) -> None:
        self.segments.clear()
        self.clock_s = 0.0

    # ----------------------------------------------------------- measurement
    @property
    def spoken(self) -> int:
        return sum(w for _c, _t, w in self.segments)

    @property
    def speech_s(self) -> float:
        return self.spoken / self.wpm * 60

    @property
    def slack_s(self) -> float:
        return self.runtime - self.speech_s

    def fits(self) -> bool:
        """Enough room to breathe, not merely enough room to finish."""
        return self.slack_s >= 2.0 * len(self.segments)

    # --------------------------------------------------------------- render
    def segment(self, title: str, screen: str, paragraphs: List[str],
                note: str = "") -> KeepTogether:
        spoken = words_in(paragraphs)
        clock = f"{int(self.clock_s) // 60}:{int(self.clock_s) % 60:02d}"
        self.clock_s += spoken / self.wpm * 60 + self.pause
        self.segments.append((clock, title, spoken))

        head = Table(
            [[Paragraph(clock, TIME),
              Paragraph(f"{title} &nbsp;<font color='#8A97AC'>&middot; "
                        f"{spoken} words &middot; "
                        f"~{spoken / self.wpm * 60:.0f}s</font>", HEAD)]],
            colWidths=[62, 433], hAlign="LEFT",
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 0)]))

        rows = [[Paragraph(f"ON SCREEN &nbsp; {screen}", CUE)]]
        rows += [[Paragraph(text, SAY)] for text in paragraphs]
        style = [
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#EAF2FE")),
            ("BACKGROUND", (0, 1), (0, -1), colors.white),
            ("LINEBEFORE", (0, 0), (0, -1), 2.4, ACCENT),
            ("BOX", (0, 0), (-1, -1), 0.6, RULE),
            ("LINEABOVE", (0, 1), (0, 1), 0.6, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 11),
            ("RIGHTPADDING", (0, 0), (-1, -1), 11),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]
        if note:
            rows.append([Paragraph(note, NOTE)])
            style.append(("LINEABOVE", (0, -1), (0, -1), 0.6, RULE))
        body = Table(rows, colWidths=[495], hAlign="LEFT",
                     style=TableStyle(style))
        return KeepTogether([head, body, Spacer(1, 12)])

    def running_order(self):
        return table([["Starts", "Segment", "Words", "Speech"]] +
                     [[c, t, str(w), f"{w / self.wpm * 60:.0f}s"]
                      for c, t, w in self.segments],
                     [50, 300, 55, 90], align_right=(2, 3))
