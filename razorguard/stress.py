"""Are the policy caps costing money, or earning their keep?

The router leaves exposure on the table by design: only part of a gateway's
share moves per action, a key may not diverge from baseline without limit, and a
3% canary always stays behind. Those bounds were originally defended by argument
alone. This measures them.

The hypothesis when this was written was that `capacity.py` would justify them -
gateways degrade under load, so a router that shifts harder congests the
destination it is shifting onto, and past some point the extra traffic it moves
simply fails at the destination instead of the source. If that point sat near
the shipped configuration, the caps would not be timidity but something close to
an optimum.

That hypothesis was wrong, and the measurement is why `max_shift_fraction` is no
longer 40%. At 40% the cap bound hard, costing roughly 61% of the available
recovery, while the congestion it was implicitly guarding against never arrived:
even at a 100% cap the destination lost on the order of 1,600 payments to load
against roughly 14,000 recovered. The default moved to 80%, the knee of the
curve below - which captures nearly all the recovery available at 100% with
fewer rollbacks and under half the congestion cost.

Re-run this after any change to the capacity model. This curve is what sets that
constant, and a different congestion shape would move it.

    python -m razorguard.stress --days 2
    python -m razorguard.stress --days 2 --json bench/results/stress.json

Each row is a full paired control/treatment run at one setting, so the recovery
figures are comparable with `experiment.py` and with each other.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List

from .config import WorldConfig
from .control_plane import ControlPlane
from .detectors import default_detector
from .experiment import exposure_inr
from .policy import PolicyConfig, PolicyEngine
from .scenarios import default_incident_plan
from .world import World

def _arm(days: int, seed: int, routing: bool, policy: PolicyConfig):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(policy), enable_routing=routing)
    return cp.run(days * 24 * 60), world, incidents


def run_setting(args) -> Dict[str, float]:
    """One paired run at one shift cap. Module-level so it can be pooled."""
    days, seed, shift, divergence = args
    policy = PolicyConfig(max_shift_fraction=shift,
                          max_cumulative_divergence=divergence)

    control, cworld, cinc = _arm(days, seed, False, policy)
    treat, tworld, tinc = _arm(days, seed, True, policy)

    if control.attempts != treat.attempts:
        return {"shift": shift, "valid": False}

    exposure = exposure_inr(control, cworld, cinc)
    d_rev = treat.revenue_inr - control.revenue_inr
    minutes = days * 24 * 60
    congested = max(tworld.minutes_congested.values())

    return {
        "shift": shift,
        "divergence": divergence,
        "valid": True,
        "recovered_inr": d_rev,
        "share_of_exposure": (d_rev / exposure) if exposure else 0.0,
        "actions": treat.actions,
        "rollbacks": treat.rollbacks,
        "escalated": treat.escalated,
        "peak_utilisation": max(tworld.peak_utilisation.values()),
        "minutes_congested": congested,
        "pct_minutes_congested": 100.0 * congested / minutes,
        # Successful payments the destination lost to congestion the router
        # itself caused, over and above what the control arm lost.
        "congestion_cost_txn": (tworld.success_lost_to_congestion
                                - cworld.success_lost_to_congestion),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="razorguard.stress")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int,
                    default=max(1, min(6, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    # Divergence is raised alongside the shift cap: holding it at 60% would cap
    # the aggressive settings by a different rule and confound the sweep.
    settings = [(0.20, 0.40), (0.40, 0.60), (0.60, 0.80),
                (0.80, 0.90), (1.00, 0.97)]
    jobs = [(args.days, args.seed, s, d) for s, d in settings]

    print(f"Sweeping the shift cap over {len(jobs)} settings, "
          f"{args.days} days paired each...")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows: List[Dict[str, float]] = list(pool.map(run_setting, jobs))

    rows = [r for r in rows if r.get("valid")]
    rows.sort(key=lambda r: r["shift"])
    shipped = next((r for r in rows
                    if abs(r["shift"] - PolicyConfig().max_shift_fraction) < 1e-9),
                   None)

    w = 92
    print()
    print("=" * w)
    print("Does shifting harder recover more?")
    print("=" * w)
    print(f"  {'shift cap':>9} {'recovered':>14} {'of exposure':>12} "
          f"{'actions':>8} {'rollbk':>7} {'peak u':>7} {'congested':>10}")
    print("  " + "-" * (w - 4))
    for r in rows:
        mark = "  <- shipped" if r is shipped else ""
        print(f"  {r['shift']:>8.0%} {r['recovered_inr']:>14,.0f} "
              f"{r['share_of_exposure']:>11.1%} {r['actions']:>8.0f} "
              f"{r['rollbacks']:>7.0f} {r['peak_utilisation']:>7.2f} "
              f"{r['pct_minutes_congested']:>9.1f}%{mark}")

    best = max(rows, key=lambda r: r["recovered_inr"])
    print()
    if shipped is not None:
        gap = best["recovered_inr"] - shipped["recovered_inr"]
        pct = gap / shipped["recovered_inr"] * 100 if shipped["recovered_inr"] else 0
        if best is shipped:
            print("  The shipped cap is the best setting tested. Shifting "
                  "harder does not recover")
            print("  more, because the traffic it moves congests the "
                  "destination it moves onto.")
        else:
            print(f"  Best setting is {best['shift']:.0%}, worth "
                  f"{gap:,.0f} more than the shipped "
                  f"{shipped['shift']:.0%} ({pct:+.1f}%).")
            print(f"  Whether that is worth {best['rollbacks']:.0f} rollbacks "
                  f"against {shipped['rollbacks']:.0f} is a judgement, not a "
                  f"calculation.")

    print()
    print("  Congestion cost, in successful payments the destination lost")
    print("  because the router loaded it (treatment above control):")
    for r in rows:
        print(f"    shift {r['shift']:>4.0%}   "
              f"{r['congestion_cost_txn']:>9,.0f} payments")
    print("=" * w)

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"config": {"days": args.days, "seed": args.seed},
                       "settings": rows,
                       "shipped": shipped, "best": best}, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
