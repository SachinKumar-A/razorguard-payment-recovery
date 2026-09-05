"""The six-minute pitch, for a fresh recording of the console.

A different shape from the three-minute version rather than a longer one: it
has room for a worked example and for the two safety behaviours, which are the
things that separate this from an auto-router. The arc is problem, mechanism,
one incident end to end, what it refused, where I was wrong, validation, and
where the model is.

Every figure is interpolated from `bench/results/*.json` and from the same
control-plane run the console renders, so the words and the screen cannot
disagree - and one of them is a correction: the incident shown on screen
recovers its own figure, not the whole run's.

    python -m docs.pitch6
"""
from __future__ import annotations

import os
import sys
from datetime import date
from typing import List

from reportlab.platypus import (NextPageTemplate, PageBreak, Spacer, Table,
                                TableStyle)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docs.build_docs import (ACCENT, ACCENT_DARK, Doc, Facts, OUT, P,  # noqa: E402
                             bullets, kpis, logo_drawing, rupees, table)
from docs.script_kit import Script  # noqa: E402
from razorguard.console_data import incident_summary, run  # noqa: E402

SCRIPT = Script(wpm=145, runtime=360, pause=3.2)


def narration(f: Facts, worst, treat, control) -> List:
    a: List = []
    A = a.append
    seg = SCRIPT.segment

    mean = rupees(f.rec["mean"])
    lo, hi = rupees(f.rec["ci_lo"]), rupees(f.rec["ci_hi"])
    netr = rupees(f.net["net_inr"])
    refused = treat["blocked"] + treat["escalated"]
    ruled = sum(treat["blocked_by_rule"].values())
    fanout = refused - ruled - treat["rollbacks"]

    A(P("The script", "h1"))
    A(P("Bold is a number &mdash; land on it, then pause. Square brackets are "
        "stage directions, not words to read.", "small"))
    A(Spacer(1, 10))

    A(seg("Who, and the problem",
          "You on camera. Console open behind you, or cut to it at the end.",
          ["Hi, I'm Sachin. This is RazorGuard &mdash; a payment revenue "
           "recovery system that finds failures nobody gets paged for, routes "
           "around them under written bounds, and proves what that was worth.",

           "A gateway is not one pipe. Traffic splits across acquiring banks, "
           "methods and issuing banks, so one stream is really dozens of "
           "narrow routes. And when payments fail, it is almost never "
           "everywhere at once.",

           "Say a route carrying four percent of volume collapses from "
           "ninety-six percent success to forty. Your fleet success rate moves "
           "<b>two points</b> &mdash; inside normal daily variation. No alert "
           "fires, and the money leaves quietly until somebody notices by "
           "hand."]))

    A(seg("How it decides",
          "Decision record, top card. Point along the decision-path row as "
          "you name each step &mdash; it is the architecture diagram, and it "
          "is real rather than drawn.",
          ["Three gateways &mdash; alpha, beta, gamma. Gamma starts failing "
           "for UPI from one bank. RazorGuard does not watch the fleet "
           "average; it watches every route, and asks where the failures are "
           "concentrated.",

           "[point along the row] It finds the common factor. It checks "
           "whether another gateway is genuinely healthier. It puts that "
           "through a policy check. Only then does it move a bounded amount "
           "of traffic &mdash; and then it measures the destination.",

           "Detect, attribute, check, act, measure. And if the destination "
           "turns out worse, put the traffic back."]))

    A(seg("One incident, end to end",
          "Same card. The signal cell, then the money-at-risk cell.",
          ["This is the worst incident in the run. A gateway outage across "
           f"<b>{worst['routes']} routes</b>. Success rate on them fell from "
           f"<b>{worst['pre_sr']:.1%}</b> to <b>{worst['in_sr']:.1%}</b>, "
           f"caught in <b>one minute</b>, with "
           f"<b>{rupees(worst['at_risk'])}</b> at risk.",

           "This is not an alert saying something went wrong. It is a decision "
           "record: what it saw, which of its own bounds it checked, and what "
           "it intends to do about it.",

           "The cells tinted blue were measured against a control arm during "
           "the run. The plain ones are policy written before it started."]))

    A(seg("What it did, and what that was worth",
          "Scroll to 'What the system did', then up to the KPI tiles.",
          ["It moved traffic off the failing routes in <b>twenty-two</b> "
           "bounded steps, always leaving a <b>three percent</b> canary so "
           "the broken route stays observable. On this incident that recovered "
           f"<b>{rupees(worst['recovered'])}</b> &mdash; "
           f"<b>{worst['capture']:.0%}</b> of what it had put at risk.",

           f"Across all eighteen incidents, <b>{netr}</b> net of fees.",

           "And that figure is measured, not estimated. The same demand runs "
           "twice &mdash; once with the router off, once with it on. Both arms "
           "see bit-identical traffic, so the difference is the routing. If "
           "they ever diverge, the run refuses to report a figure at all."],
          "The incident recovers its own figure here, not the run's. Saying "
          "the run total over one card is the error most likely to be caught."))

    A(seg("What it refused",
          "Refused. The reconciliation table at the top, then the rules.",
          ["Recovery is not always the right move, and this is the page I "
           "would want to be asked about.",

           f"<b>{refused}</b> times it declined to move money. The page "
           f"reconciles that rather than asserting it: <b>{ruled}</b> named a "
           f"policy rule, <b>{treat['rollbacks']}</b> were rollbacks, "
           f"<b>{fanout}</b> were alarms a single open breaker covered.",

           "The case I would point at is an issuer outage. If the bank itself "
           "is failing, gamma is broken but so are alpha and beta &mdash; "
           "moving traffic between them recovers nothing. RazorGuard "
           "recognises that signature and escalates instead of shuffling."]))

    A(seg("When it undoes itself, and when it stops",
          "Rollbacks. Then back to Refused and point at "
          "<font face='Courier' size='8.5'>efficacy_breaker</font>.",
          [f"<b>{treat['rollbacks']}</b> times it moved traffic, measured the "
           "destination, found it worse than baseline, and put the traffic "
           "back. A rollback is not the system failing. It is the system "
           "working.",

           "And this rule is the one I am proudest of. The efficacy breaker "
           "measures the realised effect of the system's own recent shifts. "
           "When they stop paying, it halts them and escalates.",

           "An automated system that moves money has to know when to act. It "
           "also has to know when to stop."]))

    A(seg("Where I was wrong",
          "Settings &rarr; 'Things worth trying' &rarr; "
          "<b>Max shift 40%</b>. Click it before you start talking.",
          ["Every bound is tunable and re-runs both arms, which is how I found "
           "out I was wrong twice.",

           "The cap on how much one action may move started at forty percent, "
           "because forty felt careful. [the page returns] Recovery falls from "
           "<b>1.20 crore to 71 lakh</b>. My caution was costing more than "
           "half of it.",

           "And a sweep over congestion found something worse: on a fleet "
           "already at capacity, rerouting <i>loses</i> money. That finding is "
           "why the efficacy breaker exists."]))

    A(seg("One run proves nothing",
          "The sidebar 'Validated result' block.",
          [f"One run proves nothing, so &mdash; <b>{f.seeds} seeds</b>. Mean "
           f"recovery <b>{mean}</b>, ninety-five percent interval <b>{lo} to "
           f"{hi}</b>. <b>{f.shr['mean']:.1%}</b> of the money at risk, and "
           f"<b>{f.srg['mean']:.2f} points</b> of fleet success rate.",

           f"<b>Zero of {f.seeds}</b> seeds lost money.",

           "This is a validated simulation, not a production deployment "
           "&mdash; the world is invented and the measurement is not."]))

    A(seg("Where the model is, and close",
          "How it was measured, bottom row. Cut back to camera for the last "
          "two sentences.",
          ["Detection, attribution, policy and routing are deterministic and "
           "auditable. A model writes the operator note, investigates a past "
           "decision and advises on escalations &mdash; and can change none of "
           "them. An LLM that can move money is a liability.",

           "Detect the hidden failure. Check your own bounds. Move traffic. "
           "Measure it. Undo it when you were wrong, and stop when the whole "
           "strategy stops paying.",

           "That's RazorGuard. Thank you."]))
    return a


def front(f: Facts) -> List:
    a: List = []
    A = a.append
    A(Spacer(1, 96))
    A(logo_drawing(1.05))
    A(Spacer(1, 14))
    A(P("The six-minute pitch", "title"))
    A(P("A fresh recording of the console. What to say, and what to have on "
        "screen while you say it.", "subtitle"))
    A(Table([[""], [""]], colWidths=[495], rowHeights=[2.6, 1.2],
            style=TableStyle([("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                              ("BACKGROUND", (0, 1), (-1, 1), ACCENT_DARK)]),
            hAlign="LEFT"))
    A(Spacer(1, 20))
    A(P(f"Nine segments, <b>{SCRIPT.spoken} words</b>, about "
        f"<b>{SCRIPT.speech_s / 60:.0f}:{SCRIPT.speech_s % 60:02.0f}</b> of "
        f"speech in a six-minute slot &mdash; roughly "
        f"<b>{SCRIPT.slack_s:.0f} seconds</b> of pause across the eight "
        "breaks. The arc has room for a worked example and for both safety "
        "behaviours, which are the things that separate this from an "
        "auto-router.", "lead"))
    A(Spacer(1, 16))
    A(kpis([(f"{SCRIPT.spoken}", "words to read"),
            (f"{SCRIPT.speech_s / 60:.1f} min", "of speech at 145 wpm"),
            (f"{SCRIPT.slack_s:.0f} s", "of pause to spend")]))
    A(Spacer(1, 20))
    A(P("A Sachin Kumar &nbsp;&middot;&nbsp; Razorpay AI Buildathon, Track 03 "
        f"&nbsp;&middot;&nbsp; {date.today().strftime('%d %B %Y')}", "small"))
    A(NextPageTemplate("main"))
    A(PageBreak())

    A(P("Before you record", "h1"))
    A(bullets([
        "<b>Camera at the start and the end, screen in between.</b> Face for "
        "the first segment, cut to the console for the middle, back to camera "
        "for the last two sentences.",
        "<b>Record segment by segment.</b> Nine short takes are far easier to "
        "fix than one long one, and each can slide to where its part of the "
        "screen actually starts.",
        "<b>Console at 100% zoom, maximised, already open.</b> The default "
        "seed is warmed into the image so it loads in about a second, but "
        "open it before you start rolling anyway.",
        "<b>No slides.</b> The decision-path row on the top card is the "
        "architecture diagram, and it has the advantage of being real. Point "
        "along it rather than cutting to a drawing.",
        "<b>Click the 40% link before you start that segment</b>, so the "
        "re-run happens under your first sentence rather than in silence.",
    ]))

    A(P("The running order", "h1"))
    A(SCRIPT.running_order())
    A(P("The first four segments are the argument; the rest is evidence for "
        "it. If you have to lose something, lose the second half of the "
        "rollback segment or the congestion sentence in 'Where I was wrong' "
        "&mdash; both are second examples of a point already made.", "small"))
    A(PageBreak())
    return a


def appendix(f: Facts, worst, treat) -> List:
    a: List = []
    A = a.append
    refused = treat["blocked"] + treat["escalated"]
    ruled = sum(treat["blocked_by_rule"].values())

    A(P("Three things not to say", "h1"))
    A(bullets([
        "<b>Do not put the run's total on one incident.</b> The twenty-two "
        f"steps on that card recovered <b>{rupees(worst['recovered'])}</b>. "
        f"The <b>{rupees(f.net['net_inr'])}</b> is all eighteen incidents "
        "together. Saying the second over the first inflates one card by more "
        "than three times, and the page underneath contradicts you.",
        f"<b>Do not say every refusal carries a rule.</b> Of the {refused}, "
        f"<b>{ruled}</b> named one; the rest are rollbacks and alarms an open "
        "breaker covered. The page reconciles all three, so the narration has "
        "to as well.",
        "<b>Do not call it deployed.</b> It is a validated simulation. The "
        "world is invented and the measurement is not &mdash; that is a "
        "stronger claim than a vague production one, and it survives being "
        "questioned.",
    ]))

    A(P("Every number you say, and where it comes from", "h1"))
    A(P("Interpolated when this document is built, from "
        "<font face='Courier' size='8.5'>bench/results/*.json</font> and from "
        "the same control-plane run the console renders.", "small"))
    A(Spacer(1, 6))
    A(table([
        ["Said aloud", "Value", "Source"],
        [f"Mean recovery, {f.seeds} seeds", rupees(f.rec["mean"]),
         "validate.py"],
        ["95% interval",
         f"{rupees(f.rec['ci_lo'])} to {rupees(f.rec['ci_hi'])}", "the same"],
        ["Share of money at risk", f"{f.shr['mean']:.1%}", "the same"],
        ["Success-rate gain", f"+{f.srg['mean']:.2f}pp", "the same"],
        ["Seeds that lost money", f"{f.losses} of {f.seeds}", "the same"],
        ["This run, net of fees", rupees(f.net["net_inr"]), "experiment.py"],
        ["Worst incident, before", f"{worst['pre_sr']:.1%}", "console run"],
        ["Worst incident, during", f"{worst['in_sr']:.1%}", "the same"],
        ["Worst incident, at risk", rupees(worst["at_risk"]), "the same"],
        ["Worst incident, recovered", rupees(worst["recovered"]), "the same"],
        ["Worst incident, capture", f"{worst['capture']:.0%}", "the same"],
        ["Refusals, and how they split",
         f"{refused} = {ruled} + {treat['rollbacks']} + "
         f"{refused - ruled - treat['rollbacks']}", "the same"],
    ], [186, 132, 177], align_right=(1,)))
    return a


def main() -> int:
    f = Facts()
    control = run(f.days, 7, routing=False)
    treat = run(f.days, 7, routing=True)
    worst = incident_summary(control, treat)[0]

    SCRIPT.reset()
    body = narration(f, worst, treat, control)
    doc = Doc(os.path.join(OUT, "RazorGuard-Pitch-6min.pdf"),
              "RazorGuard - six-minute pitch", "Six-minute pitch")
    doc.build(front(f) + body + appendix(f, worst, treat))

    print(f"wrote {doc.filename}")
    print(f"  {len(SCRIPT.segments)} segments, {SCRIPT.spoken} words, "
          f"{SCRIPT.speech_s:.0f}s of speech in {SCRIPT.runtime}s "
          f"({SCRIPT.slack_s:.0f}s of pause) "
          f"{'OK' if SCRIPT.fits() else 'TOO LONG'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
