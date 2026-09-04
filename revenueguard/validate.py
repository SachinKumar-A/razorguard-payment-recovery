"""Multi-seed validation: is the recovery real, or is it one lucky roll?

Everything else in this repo reports a single seed. A single seed cannot
distinguish "the router recovers money" from "this particular sequence of coin
flips happened to favour the treatment arm", and a reviewer is right to ask.

The comparison here is **paired**: for each seed, control and treatment face
bit-identical demand, so the per-seed difference removes almost all the variance
that comes from the traffic itself. What is left is the effect of the policy.
Across seeds we report the mean, the spread, and a t-based 95% confidence
interval on the mean difference -- and, most usefully, the number of seeds where
the router *lost* money. If that number is not zero, it is printed.

    python -m revenueguard.validate --seeds 8 --days 2
    python -m revenueguard.validate --seeds 12 --days 1 --json bench/results/validation.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from statistics import mean, stdev
from typing import Dict, List

from .config import WorldConfig
from .control_plane import ControlPlane
from .detectors import default_detector
from .experiment import exposure_inr
from .policy import PolicyConfig, PolicyEngine
from .scenarios import default_incident_plan
from .world import World

#: Two-sided t critical values at 95%, indexed by degrees of freedom.
#: Small-sample work needs the t distribution, not 1.96 -- with 8 seeds the
#: normal approximation understates the interval by about 18%.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 24: 2.064, 29: 2.045, 39: 2.023, 59: 2.001}


def _t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    for k in sorted(_T95):
        if df <= k:
            return _T95[k]
    return 1.96


def _one_arm(days: int, seed: int, routing: bool):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(PolicyConfig()), enable_routing=routing)
    out = cp.run(days * 24 * 60)
    return out, world, incidents


def run_seed(args) -> Dict[str, float]:
    """One paired trial. Module-level and picklable so it can be pooled."""
    days, seed = args
    control, cworld, cinc = _one_arm(days, seed, routing=False)
    treat, tworld, tinc = _one_arm(days, seed, routing=True)

    if control.attempts != treat.attempts:
        return {"seed": seed, "valid": False}

    exposure = exposure_inr(control, cworld, cinc)
    d_rev = treat.revenue_inr - control.revenue_inr
    return {
        "seed": seed,
        "valid": True,
        "attempts": control.attempts,
        "control_successes": control.successes,
        "treatment_successes": treat.successes,
        "payments_saved": treat.successes - control.successes,
        "recovered_inr": d_rev,
        "exposure_inr": exposure,
        "share_of_exposure": (d_rev / exposure) if exposure else 0.0,
        "sr_gain_pp": (treat.successes / treat.attempts
                       - control.successes / control.attempts) * 100.0,
        "actions": treat.actions,
        "rollbacks": treat.rollbacks,
        "escalated": treat.escalated,
    }


def summarise(rows: List[Dict[str, float]], key: str) -> Dict[str, float]:
    vals = [r[key] for r in rows]
    n = len(vals)
    m = mean(vals)
    sd = stdev(vals) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    half = _t95(n - 1) * se if n > 1 else float("nan")
    return {"n": n, "mean": m, "sd": sd, "se": se,
            "ci_lo": m - half, "ci_hi": m + half,
            "min": min(vals), "max": max(vals)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.validate")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--start-seed", type=int, default=1)
    ap.add_argument("--workers", type=int,
                    default=max(1, min(8, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    seeds = list(range(args.start_seed, args.start_seed + args.seeds))
    jobs = [(args.days, s) for s in seeds]

    print(f"Running {len(seeds)} paired trials x {args.days} days "
          f"on {args.workers} workers...")
    rows: List[Dict[str, float]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_seed, j): j[1] for j in jobs}
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            tag = "ok " if row.get("valid") else "INVALID"
            extra = ("" if not row.get("valid") else
                     f"  Rs {row['recovered_inr']:>11,.0f}  "
                     f"{row['share_of_exposure']:>6.1%} of exposure")
            print(f"  seed {row['seed']:<4} {tag}{extra}")

    invalid = [r for r in rows if not r.get("valid")]
    rows = sorted([r for r in rows if r.get("valid")], key=lambda r: r["seed"])
    if invalid:
        print(f"\n{len(invalid)} trial(s) had mismatched demand between arms and "
              f"were discarded.")
    if len(rows) < 2:
        print("Not enough valid trials to summarise.")
        return 1

    rec = summarise(rows, "recovered_inr")
    shr = summarise(rows, "share_of_exposure")
    srg = summarise(rows, "sr_gain_pp")
    act = summarise(rows, "actions")
    rbk = summarise(rows, "rollbacks")

    w = 78
    print()
    print("=" * w)
    print(f"Paired validation over {rec['n']} seeds, {args.days} days each")
    print("=" * w)
    print(f"  {'':26s} {'mean':>14s} {'sd':>12s} {'min':>12s} {'max':>12s}")
    print("  " + "-" * (w - 4))
    print(f"  {'revenue recovered (Rs)':26s} {rec['mean']:>14,.0f} "
          f"{rec['sd']:>12,.0f} {rec['min']:>12,.0f} {rec['max']:>12,.0f}")
    print(f"  {'share of exposure':26s} {shr['mean']:>13.1%} "
          f"{shr['sd']:>11.1%} {shr['min']:>11.1%} {shr['max']:>11.1%}")
    print(f"  {'success rate gain (pp)':26s} {srg['mean']:>14.3f} "
          f"{srg['sd']:>12.3f} {srg['min']:>12.3f} {srg['max']:>12.3f}")
    print(f"  {'routing actions':26s} {act['mean']:>14.1f} "
          f"{act['sd']:>12.1f} {act['min']:>12.0f} {act['max']:>12.0f}")
    print(f"  {'rollbacks':26s} {rbk['mean']:>14.1f} "
          f"{rbk['sd']:>12.1f} {rbk['min']:>12.0f} {rbk['max']:>12.0f}")

    print()
    print(f"  95% CI on mean recovery:  Rs {rec['ci_lo']:,.0f} "
          f"to Rs {rec['ci_hi']:,.0f}")

    losses = [r for r in rows if r["recovered_inr"] <= 0]
    print(f"  Seeds where the router lost money: {len(losses)}/{len(rows)}"
          + ("" if not losses else
             "  -> " + ", ".join(f"seed {r['seed']} "
                                 f"(Rs {r['recovered_inr']:,.0f})"
                                 for r in losses)))

    if rec["ci_lo"] > 0:
        print()
        print("  The interval excludes zero, so the recovery is not an artefact")
        print("  of one seed. The width of it is the honest precision: with")
        print(f"  {rec['n']} seeds the estimate is Rs {rec['mean']:,.0f} "
              f"+/- Rs {rec['mean'] - rec['ci_lo']:,.0f}.")
    else:
        print()
        print("  The interval includes zero. On this evidence the recovery is")
        print("  NOT distinguishable from noise -- more seeds, or a real effect,")
        print("  are needed before any figure here should be quoted.")
    print("=" * w)

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"config": {"seeds": seeds, "days": args.days},
                       "trials": rows,
                       "summary": {"recovered_inr": rec, "share_of_exposure": shr,
                                   "sr_gain_pp": srg, "actions": act,
                                   "rollbacks": rbk},
                       "seeds_with_loss": len(losses)}, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
