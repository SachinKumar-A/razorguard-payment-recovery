"""Operating-point sweep.

Comparing two detectors at whatever thresholds they happen to ship with is
meaningless: any detector looks fast if it is allowed to alarm constantly. The
only fair comparison is **time to detect at a matched false-alarm rate**.

This sweeps both detectors across their thresholds over identical traffic and
reports the curve, then picks the operating point of each that sits under a
false-alarm budget an on-call team would actually accept.

    python -m revenueguard.sweep --days 2 --budget 1.0
"""
import argparse
import json
import sys
from typing import List, Optional, Tuple

from .config import WorldConfig
from .detectors import (FixedThresholdDetector, PosteriorDropDetector,
                        UnionDetector)
from .metrics import BenchResult, evaluate
from .scenarios import default_incident_plan
from .simulator import Simulator


def simulate_once(days: int, seed: int):
    """One traffic realisation, shared by every operating point.

    Sharing it removes simulation noise from the comparison: any difference in
    the table below is the detector, not a different roll of the dice.
    """
    incidents = default_incident_plan(days)
    sim = Simulator(WorldConfig(seed=seed), incidents)
    minutes = days * 24 * 60
    observations = sim.collect(minutes)
    return observations, sim.profiles, incidents, minutes


def score(detector, observations, profiles, incidents, minutes) -> BenchResult:
    for obs in observations:
        detector.observe(obs)
    return evaluate(detector.name, observations, profiles,
                    incidents, detector.alarms, minutes)


def operating_points() -> List[Tuple[str, callable]]:
    pts: List[Tuple[str, callable]] = []
    for floor in (0.70, 0.75, 0.80, 0.85, 0.90):
        for consec in (3, 5, 10):
            pts.append((
                f"fixed  floor={floor:.2f} n={consec:<2d}",
                lambda f=floor, c=consec: FixedThresholdDetector(
                    sr_floor=f, consecutive=c),
            ))
    for drop in (2.0, 3.0, 5.0, 8.0):
        for conf in (0.90, 0.99, 0.999):
            pts.append((
                f"posterior drop={drop:.0f}pp conf={conf:<5g}",
                lambda d=drop, c=conf: PosteriorDropDetector(
                    min_drop_pp=d, confidence=c),
            ))
    # Union members are deliberately tighter than the same detector would be
    # run alone: false alarms add across members, so each has to give some
    # sensitivity back for the union to stay inside the same budget.
    for floor, drop, conf in ((0.70, 5.0, 0.99), (0.75, 5.0, 0.999),
                              (0.70, 8.0, 0.99), (0.75, 3.0, 0.999),
                              (0.80, 8.0, 0.999)):
        pts.append((
            f"union floor={floor:.2f} drop={drop:.0f}pp conf={conf:<5g}",
            lambda fl=floor, d=drop, c=conf: UnionDetector([
                FixedThresholdDetector(sr_floor=fl, consecutive=3, cooldown_min=0),
                PosteriorDropDetector(min_drop_pp=d, confidence=c, cooldown_min=0),
            ]),
        ))
    return pts


def best_under_budget(rows, family: str, budget: float) -> Optional[dict]:
    """Most detections under the false-alarm budget; ties broken by faster TTD."""
    cands = [r for r in rows
             if r["family"] == family and r["fa_per_1k_slice_hours"] <= budget]
    if not cands:
        return None
    return sorted(cands, key=lambda r: (-r["detected"],
                                        r["median_ttd"] if r["median_ttd"] is not None else 1e9))[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.sweep")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--budget", type=float, default=1.0,
                    help="false alarms per 1,000 slice-hours an operator will accept")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    observations, profiles, incidents_template, minutes = simulate_once(
        args.days, args.seed)
    n_inc = len(incidents_template)

    # One simulation, reused by every operating point. The incident objects
    # carry their populated `affected` sets, which are a property of the traffic
    # rather than of any detector, so sharing them is safe and makes the sweep
    # roughly 30x cheaper.
    incidents = incidents_template

    rows = []
    for label, make in operating_points():
        res = score(make(), observations, profiles, incidents, minutes)
        rows.append({
            "label": label,
            "family": label.split()[0],
            "detected": res.detected_count,
            "incidents": len(res.incidents),
            "median_ttd": res.median_ttd,
            "alarms": res.total_alarms,
            "false_alarms": res.false_alarms,
            "fa_per_1k_slice_hours": round(res.false_alarms_per_1k_slice_hours, 3),
        })

    rows.sort(key=lambda r: r["fa_per_1k_slice_hours"])
    w = 86
    print("=" * w)
    print(f"Operating-point sweep -- {args.days} days, {len(profiles)} slices, "
          f"{n_inc} incidents")
    print("=" * w)
    print(f"  {'operating point':38s} {'det':>7s} {'medTTD':>8s} "
          f"{'alarms':>7s} {'FA':>6s} {'FA/1k sh':>9s}")
    print("  " + "-" * (w - 4))
    for r in rows:
        ttd = "--" if r["median_ttd"] is None else f"{r['median_ttd']:g}"
        print(f"  {r['label']:38s} {r['detected']:>3d}/{r['incidents']:<3d} "
              f"{ttd:>8s} {r['alarms']:>7d} {r['false_alarms']:>6d} "
              f"{r['fa_per_1k_slice_hours']:>9.2f}")

    print()
    print(f"  Matched comparison at <= {args.budget} false alarms "
          f"per 1,000 slice-hours:")
    print()
    verdict = {}
    for fam in ("fixed", "posterior", "union"):
        b = best_under_budget(rows, fam, args.budget)
        verdict[fam] = b
        if b is None:
            print(f"    {fam:10s} no operating point meets the budget")
        else:
            ttd = "--" if b["median_ttd"] is None else f"{b['median_ttd']:g} min"
            print(f"    {fam:10s} {b['label']:38s} "
                  f"detected {b['detected']}/{b['incidents']}, median TTD {ttd}")
    print("=" * w)

    if args.json:
        import os
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"config": vars(args), "sweep": rows, "matched": verdict},
                      fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
