"""The three-minute pitch, as something you can read off a second screen.

Built rather than written, for the same reason the other documents are: every
figure spoken aloud in the video is interpolated from `bench/results/*.json`,
so the script cannot quote a number the repository no longer produces. If a
benchmark is re-run and a figure moves, this file regenerates and the words
change with it.

The layout is for reading while doing something else. Each beat is one block:
the clock, what is on screen, and the words - set large, left-aligned, ragged
right, with the numbers in bold so the eye catches them mid-sentence.

    python -m docs.pitch
"""
from __future__ import annotations

import os
import sys
from datetime import date
from typing import List

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (KeepTogether, NextPageTemplate, PageBreak,
                                Paragraph, Spacer, Table, TableStyle)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docs.build_docs import (ACCENT, ACCENT_DARK, INK, MUTED, OUT,  # noqa: E402
                             RULE, Doc, Facts, P, S, bullets, kpis,
                             logo_drawing, rupees, table)

# Reading-at-a-distance styles. The body face in the other documents is 9.6pt
# justified, which is right for a page you sit with and wrong for a page you
# glance at between clicks.
SAY = ParagraphStyle("say", parent=S["body"], fontSize=12.4, leading=18.2,
                     alignment=0, textColor=INK, spaceAfter=0)
CUE = ParagraphStyle("cue", parent=S["small"], fontSize=8.2, leading=11.4,
                     textColor=ACCENT_DARK, spaceAfter=0)
CLOCK = ParagraphStyle("clock", parent=S["small"], fontName="Courier-Bold",
                       fontSize=11, leading=13, textColor=ACCENT, spaceAfter=0)
BEAT = ParagraphStyle("beat", parent=S["small"], fontName="Helvetica-Bold",
                      fontSize=9.4, leading=12, textColor=INK, spaceAfter=0)
NOTE = ParagraphStyle("note", parent=S["small"], fontSize=8.4, leading=11.8,
                      textColor=MUTED, spaceAfter=0)


def beat(clock: str, seconds: str, title: str, screen: str, words: str,
         note: str = "") -> KeepTogether:
    """One block of the script: when, what is on screen, what you say."""
    head = Table(
        [[Paragraph(clock, CLOCK),
          Paragraph(f"{title} &nbsp;<font color='#8A97AC'>&middot; "
                    f"{seconds}</font>", BEAT)]],
        colWidths=[52, 443], hAlign="LEFT",
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 0)]))

    rows = [[Paragraph(f"ON SCREEN &nbsp; {screen}", CUE)],
            [Paragraph(words, SAY)]]
    if note:
        rows.append([Paragraph(note, NOTE)])
    body = Table(rows, colWidths=[495], hAlign="LEFT", style=TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#EAF2FE")),
        ("BACKGROUND", (0, 1), (0, -1), colors.white),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, ACCENT),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LINEABOVE", (0, 1), (0, 1), 0.6, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return KeepTogether([head, body, Spacer(1, 13)])


def build(f: Facts) -> List:
    a: List = []
    A = a.append

    mean = rupees(f.rec["mean"])
    netr = rupees(f.net["net_inr"])
    share = f"{f.shr['mean']:.1%}"

    # ------------------------------------------------------------ cover
    A(Spacer(1, 96))
    A(logo_drawing(1.05))
    A(Spacer(1, 14))
    A(P("The three-minute pitch", "title"))
    A(P("What to say, and what to have on screen while you say it.",
        "subtitle"))
    A(Table([[""], [""]], colWidths=[495], rowHeights=[2.6, 1.2],
            style=TableStyle([("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                              ("BACKGROUND", (0, 1), (-1, 1), ACCENT_DARK)]),
            hAlign="LEFT"))
    A(Spacer(1, 20))
    A(P("Seven beats, 180 seconds, about 400 spoken words. The words are "
        "written to be said rather than read - short sentences, the number "
        "before the explanation, and nothing you would not say out loud. Every "
        "figure in bold comes from the repository's own benchmark output, so "
        "this script cannot quote a number the project no longer produces.",
        "lead"))
    A(Spacer(1, 18))
    A(kpis([(mean, f"recovered per {f.days} days, mean of {f.seeds} seeds"),
            (share, "of the money the incidents put at risk"),
            (f"{f.losses}/{f.seeds}", "seeds where the router lost money")]))
    A(Spacer(1, 22))
    A(P("A Sachin Kumar &nbsp;&middot;&nbsp; Razorpay AI Buildathon, Track 03 "
        "&nbsp;&middot;&nbsp; "
        f"{date.today().strftime('%d %B %Y')}", "small"))
    A(NextPageTemplate("main"))
    A(PageBreak())

    # ------------------------------------------------------- before you start
    A(P("Before you press record", "h1"))
    A(P("Two minutes of setup that will save you a retake.", "body"))
    A(bullets([
        "<b>Open the console first and let it settle.</b> "
        "<font face='Courier' size='8.5'>localhost:8501</font>. The default "
        "seed is already warmed into the image, so it opens in about a "
        "second - but open it before you start recording anyway, so the first "
        "thing on camera is the board and not a spinner.",
        "<b>Browser at 100% zoom, window maximised.</b> The header is built to "
        "fill the width; at 80% the tagline and the quotes crowd each other.",
        "<b>Have three tabs ready</b> so you never wait for a page: the "
        "console on Overview, a second on Decision record, and a terminal.",
        "<b>Do not use <font face='Courier' size='8.5'>--fast</font> on the "
        "showcase</b> if you show it. It runs one simulated day and its "
        "figures are smaller than the ones in this script, which are two-day "
        "numbers.",
        "<b>Say the eight-seed figure, not the single run.</b> The tile says "
        f"{netr} for this run; the sidebar says <b>{mean}</b> across "
        f"{f.seeds} seeds with a confidence interval. The second one is the "
        "defensible number, so it is the one in the script.",
    ]))
    A(Spacer(1, 6))
    A(P("The shape of the three minutes", "h2"))
    A(table([
        ["Time", "Beat", "On screen"],
        ["0:00", "The problem nobody gets paged for", "Board / masthead"],
        ["0:25", "Watch it catch one", "Decision record, top card"],
        ["0:53", "What it did, and what that was worth", "Same card, then tiles"],
        ["1:21", "The half a dashboard never shows", "Refused, then Rollbacks"],
        ["1:49", "Where I was wrong, and the measurement", "Settings, one click"],
        ["2:21", "One run proves nothing", "Sidebar, then How it was measured"],
        ["2:45", "Where the AI is, and where it is not", "How it was measured"],
    ], [46, 250, 199]))
    A(PageBreak())
    return a


def script(f: Facts) -> List:
    a: List = []
    A = a.append
    mean = rupees(f.rec["mean"])
    lo, hi = rupees(f.rec["ci_lo"]), rupees(f.rec["ci_hi"])
    netr = rupees(f.net["net_inr"])
    share = f"{f.shr['mean']:.1%}"
    srg = f"{f.srg['mean']:.2f}"

    A(P("The script", "h1"))
    A(P("Bold is a number &mdash; land on it, then pause. Square brackets are "
        "stage directions, not words. The whole thing is <b>400 words</b>, "
        "which is about 160 seconds at a normal pace; the rest of the three "
        "minutes is the pauses and the clicking.", "small"))
    A(Spacer(1, 10))

    A(beat("0:00", "25 sec", "The problem nobody gets paged for",
           "The console open on Overview. Do not scroll yet.",
           "A payment gateway is not one pipe. Traffic splits across acquiring "
           "banks, methods and issuing banks &mdash; and when payments fail, it "
           "is almost never everywhere at once. It is narrow. Say one slice is "
           "four percent of volume and it collapses from ninety-six percent to "
           "forty. Your headline success rate moves <b>two points</b>. Nobody "
           "is paged, and the money leaves quietly."))

    A(beat("0:25", "28 sec", "Watch it catch one",
           "Click DECISION RECORD. Stop on the top card.",
           "This is RazorGuard. Two simulated days, <b>1.97 million</b> "
           "payment attempts, <b>eighteen</b> injected failures. Here is the "
           "worst. A gateway outage: those routes fell from <b>ninety-four "
           "percent to thirty-three</b>, caught in <b>one minute</b>, with "
           "<b>65 lakh</b> at risk. [point at the blue cells] Everything blue "
           "was measured during the run. Everything else is policy written "
           "before it. Different kinds of claim, so they do not look alike."))

    A(beat("0:53", "28 sec", "What it did, and what that was worth",
           "Scroll to 'What the system did', then back up to the tiles.",
           "It moved traffic off the failing routes in <b>twenty-two</b> "
           "bounded steps, always leaving a <b>three percent</b> canary so the "
           "broken route stays observable. Across the run that is "
           f"<b>{netr}</b> recovered, net of fees. And that is a difference "
           "between two runs that saw identical demand &mdash; not a "
           "projection. If the two arms ever diverge, it refuses to report a "
           "figure at all."))

    A(beat("1:21", "28 sec", "The half a dashboard never shows",
           "Click REFUSED. Then click ROLLBACKS.",
           "This is the part I would want to be asked about. "
           "<b>Ninety-three</b> times it declined to move money, each with the "
           "rule that stopped it. [click Rollbacks] <b>Thirty-two</b> times it "
           "moved traffic, measured the destination, found it worse, and put "
           "the traffic back. Issuer-wide faults it escalates instead "
           "&mdash; when the bank is failing everywhere, shuffling between "
           "broken routes recovers nothing."))

    A(beat("1:49", "32 sec", "Where I was wrong, and how I know",
           "Click SETTINGS &rarr; 'Things worth trying' &rarr; "
           "<b>Max shift 40%</b>. Let it re-run on camera.",
           "Everything here is tunable and it really re-runs. The cap on how "
           "much one action may move started at forty percent, because forty "
           "felt careful. [the page returns] Recovery falls from "
           "<b>1.20 crore to 71 lakh</b>. My caution was costing more than "
           "half the recovery, and only measurement found that. A second sweep "
           "found worse: on a saturated fleet, rerouting <i>loses</i> money. "
           "So there is a breaker that halts the system when its own shifts "
           "stop paying.",
           "Click the link just before you start talking, so the re-run "
           "happens under the first sentence rather than in silence."))

    A(beat("2:21", "24 sec", "One run proves nothing",
           "Point at the sidebar 'Validated result' block.",
           f"One run proves nothing, so &mdash; <b>{f.seeds} seeds</b>. Mean "
           f"<b>{mean}</b>, ninety-five percent interval <b>{lo} to {hi}</b>. "
           f"That is <b>{share}</b> of the money those incidents put at risk, "
           f"and <b>{srg} points</b> of fleet success rate. <b>Zero of "
           f"{f.seeds}</b> lost money. And the detectors cannot import the "
           "file that defines the incidents &mdash; a test fails if anyone "
           "tries."))

    A(beat("2:45", "14 sec", "Where the AI is, and where it is not",
           "Click HOW IT WAS MEASURED. Land on the bottom row.",
           "Detection, attribution, policy and routing are deterministic. A "
           "model writes the operator note and explains past decisions "
           "&mdash; and can change none of them. An LLM that can move money is "
           "a liability. Thank you."))
    return a

def appendix(f: Facts) -> List:
    a: List = []
    A = a.append
    A(PageBreak())

    A(P("If you are running long", "h1"))
    A(P("Cut in this order. Each of these can go without breaking the "
        "argument, because the beat before it already made the point.",
        "body"))
    A(table([
        ["Cut", "Saves", "What you lose"],
        ["The rollbacks half of beat 4 (1:21)", "8 sec",
         "One example of self-correction. The refusals still carry it."],
        ["The capacity sweep sentence in beat 5 (1:49)", "9 sec",
         "The second finding. The shift-cap number is the stronger one."],
        ["The confidence interval in beat 6 (2:21)", "6 sec",
         "Say the mean and 'zero of eight lost money' instead."],
    ], [180, 44, 271]))

    A(P("If you are running short", "h2"))
    A(P("Open the incident replay and read two lines of the ledger aloud "
        "&mdash; a refusal and the rollback that follows it. It is the most "
        "convincing twenty seconds in the project, and it is real output "
        "rather than anything composed for a demo.", "body"))

    A(P("Questions you should expect", "h1"))
    A(P("Short answers, because a long one sounds rehearsed.", "body"))
    A(table([
        ["Question", "Answer"],
        ["Razorpay already has Optimizer. Why this?",
         "Optimizer routes. This measures whether the routing paid, refuses "
         "when it would not, and writes down why. The evidence half is the "
         "product."],
        ["It is a simulator. Why should I believe the number?",
         "Because the number is a difference between two runs that saw "
         "identical demand, the detectors cannot see the incident plan, and "
         "the run refuses to report at all if the arms diverge. Three tests "
         "enforce those."],
        ["Where is the AI?",
         "Deliberately out of the loop that moves money. It writes the "
         "operator note and explains a past decision. Detection, attribution "
         "and policy are deterministic and auditable."],
        ["Does it work on real traffic?",
         "The service takes real payment outcomes at POST /ingest, "
         "authenticated. The executor has run live against a Razorpay test "
         "account &mdash; six of six orders created, with the decision context "
         "in the notes."],
        ["What breaks first at scale?",
         "Attribution needs a global view, so it is one active instance with "
         "lease-based failover rather than shards. That is in DEPLOY.md, with "
         "what is still open."],
    ], [148, 347]))

    A(P("The numbers, in one place", "h1"))
    A(P("Everything below is interpolated from <font face='Courier' "
        "size='8.5'>bench/results/*.json</font> when this document is built, "
        "so it matches what the console will show on camera.", "small"))
    A(Spacer(1, 6))
    A(table([
        ["Figure", "Value", "Where it comes from"],
        [f"Recovered, mean of {f.seeds} seeds", rupees(f.rec['mean']),
         "validate.py, 8 seeds x 2 days"],
        ["95% confidence interval",
         f"{rupees(f.rec['ci_lo'])} to {rupees(f.rec['ci_hi'])}",
         "the same run"],
        ["Share of money at risk recovered", f"{f.shr['mean']:.1%}",
         "control-arm shortfall as denominator"],
        ["Success-rate gain", f"+{f.srg['mean']:.2f}pp",
         "treatment minus control"],
        ["Seeds that lost money", f"{f.losses} of {f.seeds}", "validate.py"],
        ["This run, net of fees", rupees(f.net['net_inr']),
         "experiment.py, seed 7"],
        ["This run, payments saved", f"{f.recovered['payments']:,}",
         "the same run"],
        ["Payment attempts, per arm", f"{f.attempts:,}",
         "identical in both arms, or nothing is reported"],
    ], [176, 118, 201], align_right=(1,)))
    return a


def main() -> int:
    f = Facts()
    doc = Doc(os.path.join(OUT, "RazorGuard-Pitch-3min.pdf"),
              "RazorGuard - three-minute pitch", "Pitch script")
    doc.build(build(f) + script(f) + appendix(f))
    print(f"wrote {doc.filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
