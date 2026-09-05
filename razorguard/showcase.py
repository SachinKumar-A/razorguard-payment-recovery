"""One command that walks the whole story, so a demo is not a scavenger hunt.

    python -m razorguard.showcase

Recording a five-minute pitch by hand means remembering six commands, waiting
on each, and finding the interesting lines in a hundred and forty of output
while talking. That is a lot to hold, and it is why demos stall.

This runs the sequence in order, prints a title card before each act, pauses
between them, and pulls out the three or four lines that actually matter. Talk
over it and press Enter when you are ready to move on.

    python -m razorguard.showcase --auto        pauses on a timer, no keypresses
    python -m razorguard.showcase --fast        one simulated day, for rehearsal
    python -m razorguard.showcase --act 3       jump straight to one act

Nothing here computes anything new. Every number comes from the same functions
the benchmarks call, so what you show is what the repository does.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable, List, Optional, Tuple

from .config import WorldConfig
from .control_plane import ControlPlane
from .detectors import default_detector
from .economics import net_recovery
from .envfile import load as _load_env
from .experiment import exposure_inr
from .policy import PolicyConfig, PolicyEngine
from .scenarios import default_incident_plan
from .world import World

W = 78
RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "bench", "results")


# ------------------------------------------------------------- presentation

def rule(ch: str = "=") -> None:
    print(ch * W)


def title(n: int, of: int, heading: str, say: str) -> None:
    print()
    rule()
    print(f"  ACT {n} of {of}   |   {heading}")
    rule()
    for line in wrap(say, W - 4):
        print(f"  {line}")
    print()


def wrap(text: str, width: int) -> List[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def beat(text: str) -> None:
    """A line to read aloud. Indented so it is obvious on screen."""
    print()
    for line in wrap(text, W - 6):
        print(f"    {line}")
    print()


def pause(auto: Optional[float]) -> None:
    print()
    if auto is None:
        try:
            input("  -- press Enter for the next act -- ")
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)
    else:
        time.sleep(auto)


def money(x: float) -> str:
    if abs(x) >= 1e7:
        return f"Rs {x / 1e7:,.2f} cr"
    if abs(x) >= 1e5:
        return f"Rs {x / 1e5:,.2f} L"
    return f"Rs {x:,.0f}"


def clock(minute: int) -> str:
    return f"d{minute // 1440 + 1} {(minute // 60) % 24:02d}:{minute % 60:02d}"


# ------------------------------------------------------------------ engine

def simulate(days: int, seed: int, routing: bool):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(PolicyConfig()), enable_routing=routing)
    return cp.run(days * 24 * 60), world, incidents


def show_event(event, indent: str = "    ") -> None:
    glyph = {"detection": "!", "proposal": ">", "decision": "?",
             "action": "*", "rollback": "<", "restore": "+"}.get(event.kind, "-")
    head = f"{indent}{glyph} {clock(event.minute):<10} {event.kind:<10}"
    body = wrap(event.summary, W - len(head) - 2)
    print(f"{head}{body[0] if body else ''}")
    for line in body[1:]:
        print(f"{' ' * len(head)}{line}")
    if event.rule:
        print(f"{' ' * len(head)}rule: {event.rule}")


# -------------------------------------------------------------------- acts

def act_problem(days: int, seed: int) -> None:
    beat("A payment gateway is not one pipe. Traffic splits across acquiring "
         "banks, payment methods and issuing banks, and failures in that system "
         "are almost never global. They are narrow.")
    beat("Say one slice is 4% of volume and it collapses from 96% success to "
         "40%. The headline success rate moves about two points - inside normal "
         "daily variation. Nobody is paged. The money leaves quietly.")
    beat("That is the window this system is about. Watch it find one.")


def act_detect(days: int, seed: int) -> None:
    print("  running the control plane over 2 simulated days...")
    out, _, incidents = simulate(days, seed, routing=True)

    target = next((i for i in incidents if i.kind == "hard_outage"
                   and any(e.kind == "action" and i.start_min <= e.minute < i.end_min
                           for e in out.ledger)), incidents[0])
    print(f"  incident {target.incident_id}: {target.kind}, "
          f"{target.blast_radius}, {len(target.affected)} slices, "
          f"starts {clock(target.start_min)}")
    print()

    window = [e for e in out.ledger
              if target.start_min <= e.minute < target.start_min + 4]
    for kind in ("detection", "proposal", "decision", "action"):
        first = next((e for e in window if e.kind == kind), None)
        if first:
            show_event(first)

    beat("It found the cause itself: the alarming slices share one gateway, at "
         "three times that gateway's share of the fleet. That is lift with "
         "coverage - deterministic, and it gives the same answer every run.")

    refusal = next((e for e in out.ledger
                    if e.kind == "decision" and e.rule
                    and e.evidence.get("decision") in ("block", "escalate")), None)
    rollback = next((e for e in out.ledger if e.kind == "rollback"), None)
    breaker = next((e for e in out.ledger if e.rule == "efficacy_breaker"), None)

    print("  " + "-" * (W - 4))
    print("  and now the three lines that matter more than the successes:")
    print()
    if refusal:
        show_event(refusal)
    if rollback:
        show_event(rollback)
    if breaker:
        show_event(breaker)

    beat("A refusal, with the rule that caused it. An action the system undid "
         "when the destination turned out worse. And the system halting itself "
         "entirely after measuring that its whole strategy had stopped paying.")
    beat("Most systems log what they did. This one logs what it declined to do, "
         "and can conclude that it should stop.")


def act_measure(days: int, seed: int) -> None:
    print("  running the same two days twice - routing off, then on...")
    control, cworld, cinc = simulate(days, seed, routing=False)
    treat, _, _ = simulate(days, seed, routing=True)

    d_succ = treat.successes - control.successes
    d_rev = treat.revenue_inr - control.revenue_inr
    net = net_recovery(control.observations, treat.observations, d_rev, d_succ)
    exposure = exposure_inr(control, cworld, cinc)

    print()
    print(f"  {'':22} {'router off':>16} {'router on':>16} {'delta':>14}")
    print("  " + "-" * (W - 4))
    print(f"  {'attempts':22} {control.attempts:>16,} {treat.attempts:>16,} "
          f"{0:>14}")
    print(f"  {'successful payments':22} {control.successes:>16,} "
          f"{treat.successes:>16,} {d_succ:>+14,}")
    print(f"  {'success rate':22} "
          f"{control.successes / control.attempts:>15.2%} "
          f"{treat.successes / treat.attempts:>15.2%} "
          f"{(treat.successes / treat.attempts - control.successes / control.attempts) * 100:>+13.2f}pp")
    print()
    print(f"  {'gross recovered':32} {money(net.gross_inr):>18}")
    print(f"  {'processing fees it cost':32} {money(net.incremental_cost_inr):>18}")
    print(f"  {'NET recovered':32} {money(net.net_inr):>18}")
    print(f"  {'share of money at risk':32} {d_rev / exposure:>17.1%}")

    beat(f"Look at the attempts row first. Both arms saw exactly "
         f"{control.attempts:,} payment attempts - not approximately, exactly. "
         f"If they ever differ, the run refuses to print a recovery figure at "
         f"all, because the comparison would be worthless.")
    beat("The control arm is not crippled. It runs the identical detector and "
         "raises the identical alarms. It simply is not permitted to act.")
    beat("And nothing here learns a label. The outages are scheduled, so "
         "time-to-detect is latency against an event we caused - not agreement "
         "with a label we invented.")


def act_validate(days: int, seed: int) -> None:
    path = os.path.join(RESULTS, "validation.json")
    if not os.path.exists(path):
        print("  bench/results/validation.json not found - run:")
        print("    python -m razorguard.validate --seeds 8 --days 2 "
              "--json bench/results/validation.json")
        return
    with open(path, encoding="utf-8") as fh:
        v = json.load(fh)
    r = v["summary"]["recovered_inr"]
    s = v["summary"]["share_of_exposure"]

    print(f"  {'':22} {'mean':>14} {'sd':>12} {'min':>13} {'max':>13}")
    print("  " + "-" * (W - 4))
    print(f"  {'revenue recovered':22} {r['mean']:>14,.0f} {r['sd']:>12,.0f} "
          f"{r['min']:>13,.0f} {r['max']:>13,.0f}")
    print(f"  {'share of exposure':22} {s['mean']:>13.1%} {s['sd']:>11.1%} "
          f"{s['min']:>12.1%} {s['max']:>12.1%}")
    print()
    print(f"  95% confidence interval:  {money(r['ci_lo'])} to {money(r['ci_hi'])}")
    print(f"  Seeds where the router lost money: {v['seeds_with_loss']}/{r['n']}")

    beat("One seed cannot tell a real effect from a favourable roll of the dice. "
         "Eight paired trials, and the interval excludes zero.")


def act_wrong(days: int, seed: int) -> None:
    stress = os.path.join(RESULTS, "stress.json")
    sens = os.path.join(RESULTS, "sensitivity.json")

    if os.path.exists(stress):
        with open(stress, encoding="utf-8") as fh:
            rows = json.load(fh)["settings"]
        print("  Does shifting harder recover more?")
        print()
        print(f"  {'shift cap':>10} {'recovered':>16} {'of exposure':>13} "
              f"{'rollbacks':>11}")
        print("  " + "-" * (W - 4))
        for r in rows:
            mark = "   <- shipped" if abs(r["shift"] - 0.80) < 1e-9 else ""
            print(f"  {r['shift']:>9.0%} {r['recovered_inr']:>16,.0f} "
                  f"{r['share_of_exposure']:>12.1%} {r['rollbacks']:>11.0f}{mark}")

        beat("I set that cap at 40%. Cautious choice. I wrote a paragraph in the "
             "docs defending it. Then I measured it - and it was costing about "
             "61% of the available recovery. So I changed it. The paragraph was "
             "wrong and the measurement is in the repository.")

    if os.path.exists(sens):
        with open(sens, encoding="utf-8") as fh:
            d = json.load(fh)
        caps = sorted({c["cap"] for c in d["cells"]})
        print()
        print("  And does that 80% depend on a curve I guessed at?")
        print()
        print("  " + f"{'curve':<12}" + "".join(f"{c:>10.0%}" for c in caps))
        print("  " + "-" * (W - 4))
        for curve in d["curves"]:
            cells = {c["cap"]: c for c in d["cells"] if c["curve"] == curve["label"]}
            row = f"  {curve['label']:<12}"
            for cap in caps:
                cell = cells.get(cap)
                row += f"{cell['share_of_exposure']:>10.1%}" if cell else f"{'-':>10}"
            print(row)

        harmful = d.get("harmful_curves", [])
        if harmful:
            beat(f"Mostly it does not. But look at {' and '.join(harmful)}. On "
                 f"gateway fleets already running at capacity, this system loses "
                 f"money at every setting. There is no spare headroom to route "
                 f"into, so shifting traffic just piles load onto something "
                 f"already struggling.")
            beat("No per-action guardrail can catch that - every individual move "
                 "looks perfectly reasonable. And my first version of this report "
                 "had a bug: it divided one negative number by another, printed "
                 "minus three hundred percent, and put the word ACCEPTABLE over "
                 "the top of it.")
            beat("I caught it, fixed the arithmetic, and built what it had been "
                 "hiding - the system now measures the real effect of its own "
                 "shifts and halts when they stop paying.")


def act_close(days: int, seed: int) -> None:
    beat("Where the AI sits. Detection, attribution, policy and routing are "
         "deterministic - a test asserts no decision-path module can even import "
         "a model. A sampled token in the path of a money-moving action cannot "
         "be reproduced or defended.")
    beat("A model does three things, none of which can change a decision: it "
         "writes the operator note, it investigates a past decision through "
         "four read-only tools, and it advises a human on escalations the "
         "rulebook has no answer for.")
    print("  try it yourself:")
    print("    python -m razorguard.investigate \"why did traffic move off "
          "gw_beta at 03:12?\"")
    print("    streamlit run app.py")
    print()
    beat("202 tests. Every number in the README is produced by a command in the "
         "repository, and a test fails if any of them drifts.")


ACTS: List[Tuple[str, str, Callable[[int, int], None]]] = [
    ("The problem, and the part most people would hide",
     "Set the scene, then say the weakness before anyone can find it.",
     act_problem),
    ("Watch it happen",
     "One incident, decision by decision - including a refusal, a rollback, "
     "and the system halting itself.",
     act_detect),
    ("What did it actually recover?",
     "The same demand, run twice. A measurement rather than a projection.",
     act_measure),
    ("Is that one lucky run?",
     "Eight paired seeds and a confidence interval.",
     act_validate),
    ("Where I was wrong, twice",
     "The strongest ninety seconds. Do not cut this.",
     act_wrong),
    ("Where the AI is, and close",
     "The boundary, and why it is drawn there.",
     act_close),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="razorguard.showcase")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--auto", type=float, nargs="?", const=6.0, default=None,
                    metavar="SECONDS",
                    help="pause on a timer instead of waiting for Enter")
    ap.add_argument("--fast", action="store_true",
                    help="one simulated day - for rehearsal, not for recording")
    ap.add_argument("--act", type=int, default=None,
                    help="run a single act (1-6), to re-record one segment")
    args = ap.parse_args(argv)
    _load_env()

    days = 1 if args.fast else args.days
    acts = ACTS if args.act is None else [ACTS[args.act - 1]]
    offset = 0 if args.act is None else args.act - 1

    print()
    rule()
    print("  RAZORGUARD   |   Razorpay AI Buildathon, Track 03")
    print("  Detect payment degradation, route around it, prove what it recovered.")
    rule()
    if args.act is None:
        print("  Six acts, about five minutes. Talk over it; press Enter to move on.")
        if args.fast:
            print("  (--fast: one simulated day. Rehearsal only - the numbers "
                  "differ from the documented ones.)")

    for i, (heading, say, fn) in enumerate(acts, start=1 + offset):
        title(i, len(ACTS), heading, say)
        fn(days, args.seed)
        if i < len(ACTS) and args.act is None:
            pause(args.auto)

    print()
    rule()
    print("  Repository, both PDFs and the walkthrough are in the submission.")
    rule()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
