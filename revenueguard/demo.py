"""Replay one incident, minute by minute, with the decisions beside it.

This is the view that answers the only question that matters in review: *why
did you move that money, and how do you know it worked?* Everything printed
here is read back out of the audit ledger and the observation stream -- nothing
is composed for the demo.

    python -m revenueguard.demo
    python -m revenueguard.demo --incident INC-0-01
    python -m revenueguard.demo --list
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .config import WorldConfig
from .control_plane import ControlPlane, RunOutcome
from .detectors import PosteriorDropDetector
from .policy import PolicyConfig, PolicyEngine
from .scenarios import Incident, default_incident_plan
from .world import World

BAR_W = 26
SYMBOL = {"detection": "!", "proposal": ">", "decision": "?", "action": "*",
          "rollback": "<", "restore": "+"}


def _run(days: int, seed: int):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    detector = PosteriorDropDetector(min_drop_pp=3.0, confidence=0.99)
    cp = ControlPlane(world, detector, policy=PolicyEngine(PolicyConfig()),
                      enable_routing=True)
    out = cp.run(days * 24 * 60)
    return cp, out, incidents, world


def _sr_by_minute(out: RunOutcome, slices: set,
                  lo: int, hi: int) -> Dict[int, Tuple[int, int]]:
    acc: Dict[int, List[int]] = defaultdict(lambda: [0, 0])
    for o in out.observations:
        if lo <= o.minute <= hi and o.slice_key in slices:
            acc[o.minute][0] += o.attempts
            acc[o.minute][1] += o.successes
    return {m: (a, s) for m, (a, s) in acc.items()}


def _bar(sr: Optional[float], lo: float = 0.30, hi: float = 1.0) -> str:
    if sr is None:
        return " " * BAR_W
    frac = max(0.0, min(1.0, (sr - lo) / (hi - lo)))
    n = int(round(frac * BAR_W))
    return "#" * n + "." * (BAR_W - n)


def clock(minute: int) -> str:
    return f"d{minute // 1440 + 1} {(minute // 60) % 24:02d}:{minute % 60:02d}"


def list_incidents(out: RunOutcome, incidents: List[Incident]) -> None:
    acted = {e.minute: e for e in out.ledger.of_kind("action")}
    print(f"{'id':<12} {'kind':<22} {'when':<10} {'dur':>4}  {'slices':>6}  actions")
    print("-" * 72)
    for inc in incidents:
        n = sum(1 for m in acted
                if inc.start_min <= m < inc.end_min + 15)
        print(f"{inc.incident_id:<12} {inc.kind:<22} {clock(inc.start_min):<10} "
              f"{inc.duration:>4}  {len(inc.affected):>6}  {n}")


def replay(out: RunOutcome, inc: Incident, pre: int = 12, post: int = 45) -> None:
    lo = max(0, inc.start_min - pre)
    hi = inc.end_min + post
    series = _sr_by_minute(out, inc.affected, lo, hi)
    events = defaultdict(list)
    for e in out.ledger.trace(lo, hi):
        events[e.minute].append(e)

    w = 100
    print("=" * w)
    print(f"  {inc.incident_id}  --  {inc.kind}   {inc.blast_radius}")
    print(f"  starts {clock(inc.start_min)}, runs {inc.duration} min, "
          f"touches {len(inc.affected)} slices")
    print("=" * w)
    print(f"  {'time':<9} {'succ rate':>9}  {'volume':>7}  {'health':<{BAR_W}}  events")
    print("  " + "-" * (w - 4))

    for t in range(lo, hi + 1):
        att, suc = series.get(t, (0, 0))
        sr = (suc / att) if att else None
        marker = "|" if inc.start_min <= t < inc.end_min else " "
        evs = events.get(t, [])

        if not evs and t % 5 and att:
            continue  # thin the quiet stretches; every event minute still prints

        srtxt = f"{sr:8.1%}" if sr is not None else "       --"
        head = (f" {marker}{clock(t):<8} {srtxt}  {att:>7,}  {_bar(sr)}  ")
        if not evs:
            print(" " + head)
            continue
        for n, e in enumerate(evs):
            prefix = head if n == 0 else " " * len(head)
            tag = SYMBOL.get(e.kind, " ")
            print(f" {prefix}{tag} {e.kind:<9} {e.summary}")
            if e.kind == "decision" and e.rule:
                print(f" {' ' * len(head)}  {'':<9} rule: {e.rule}")

    print("  " + "-" * (w - 4))
    print("  ! detection   > proposal   ? policy decision   "
          "* action   < rollback   + restore")
    print("=" * w)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.demo")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--incident", type=str, default=None)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    _, out, incidents, _ = _run(args.days, args.seed)

    if args.list:
        list_incidents(out, incidents)
        return 0

    if args.incident:
        match = [i for i in incidents if i.incident_id == args.incident]
        if not match:
            print(f"no incident {args.incident}; try --list")
            return 1
        replay(out, match[0])
        return 0

    # Default: the first incident that actually provoked a routing action, so
    # the demo shows the full loop rather than a quiet detection.
    acted_minutes = [e.minute for e in out.ledger.of_kind("action")]
    for inc in incidents:
        if any(inc.start_min <= m < inc.end_min for m in acted_minutes):
            replay(out, inc)
            return 0
    replay(out, incidents[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
