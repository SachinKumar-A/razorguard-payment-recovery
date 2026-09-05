"""Run the benchmark.

    python -m razorguard.bench --days 2 --json bench/results/latest.json

Runs every detector over identical traffic and the identical incident plan, so
the comparison is like-for-like. The fixed-threshold detector is not a strawman
to beat -- if it wins on a row, that row says so.
"""
import argparse
import json
import sys
from typing import List

from .config import WorldConfig
from .detectors import FixedThresholdDetector, PosteriorDropDetector
from .metrics import BenchResult, evaluate
from .scenarios import default_incident_plan
from .simulator import Simulator


def run_one(detector, days: int, seed: int) -> BenchResult:
    # Fresh incidents per run: Incident.affected is populated during simulation.
    incidents = default_incident_plan(days)
    config = WorldConfig(seed=seed)
    sim = Simulator(config, incidents)
    minutes = days * 24 * 60

    observations = sim.collect(minutes)
    for obs in observations:
        detector.observe(obs)

    return evaluate(detector.name, observations, sim.profiles,
                    incidents, detector.alarms, minutes)


def fmt(v, suffix="", dash="--"):
    return dash if v is None else f"{v:g}{suffix}"


def print_report(results: List[BenchResult]) -> None:
    w = 78
    print("=" * w)
    print("RazorGuard -- degradation detection benchmark")
    print("=" * w)
    r0 = results[0]
    print(f"  window            {r0.minutes} min ({r0.minutes/1440:g} days)")
    print(f"  slices monitored  {r0.slices_monitored}")
    print(f"  slice-hours       {r0.slice_hours:,.0f}")
    print(f"  incidents         {len(r0.incidents)}")
    print()

    rows = [
        ("detector", "det.", "missed", "med TTD", "alarms", "FA", "FA/1k sh"),
    ]
    for r in results:
        rows.append((
            r.detector[:34],
            f"{r.detected_count}/{len(r.incidents)}",
            str(r.miss_count),
            fmt(r.median_ttd, " min"),
            str(r.total_alarms),
            str(r.false_alarms),
            f"{r.false_alarms_per_1k_slice_hours:.2f}",
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for n, row in enumerate(rows):
        print("  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))
        if n == 0:
            print("  " + "  ".join("-" * widths[i] for i in range(len(widths))))
    print()

    for r in results:
        print(f"  {r.detector}")
        det = r.detection_by_kind()
        ttd = r.ttd_by_kind()
        for kind in sorted(det):
            print(f"    {kind:24s} detected {det[kind]:>6s}   "
                  f"median TTD {fmt(ttd[kind], ' min')}")
        exposed = sum(i.exposure_inr for i in r.incidents)
        before = sum(i.exposure_before_detect_inr for i in r.incidents)
        print(f"    {'exposure (total)':24s} Rs {exposed:>12,.0f}")
        print(f"    {'  before detection':24s} Rs {before:>12,.0f}")
        print(f"    {'  after detection':24s} Rs {exposed - before:>12,.0f}"
              f"   <- addressable by routing, NOT recovered (no router yet)")
        print()

    print("  Exposure is failed volume above each slice's healthy failure rate,")
    print("  priced at that method's average ticket. No routing action is taken")
    print("  in this version, so no rupee here is claimed as recovered.")
    print("=" * w)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="razorguard.bench")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    detectors = [
        FixedThresholdDetector(sr_floor=0.85, consecutive=3),
        PosteriorDropDetector(),
    ]
    results = [run_one(d, args.days, args.seed) for d in detectors]
    print_report(results)

    if args.json:
        import os
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"config": {"days": args.days, "seed": args.seed},
                       "results": [r.to_dict() for r in results]}, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
