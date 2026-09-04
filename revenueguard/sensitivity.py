"""Does the 80% shift cap depend on a curve we guessed?

`stress.py` chose `max_shift_fraction = 0.80` by sweeping the cap against one
congestion curve. But that curve - knee at 70% utilisation, a tenth of the
success rate lost at capacity - is a plausible shape, not a measured one. If the
best cap moves a lot when the curve is wrong, then the project's headline number
rests on a guess, and saying "we chose 80% by measurement" would be misleading.

So: sweep the cap **against several curves at once** and see whether the answer
is stable.

    python -m revenueguard.sensitivity --days 2

Three outcomes, and each is worth knowing:

- The best cap barely moves across curves. The guess does not matter much; the
  choice is robust and can be defended without the real curve.
- The best cap moves, but 80% stays close to optimal everywhere. Also fine, and
  the honest phrasing is "80% is a good compromise across plausible curves".
- The best cap swings wildly. Then the curve *is* load-bearing, the headline
  depends on it, and the only fix is real acquirer telemetry.

The curves span from an acquirer with lots of headroom that degrades gently, to
a brittle one that falls over early and hard. Real behaviour should sit inside
that range.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Tuple

from .capacity import CapacityModel
from .config import WorldConfig
from .control_plane import ControlPlane
from .detectors import default_detector
from .experiment import exposure_inr
from .policy import PolicyConfig, PolicyEngine
from .scenarios import default_incident_plan
from .world import World

#: (label, knee, slope, headroom). "shipped" is the curve stress.py used.
CURVES: List[Tuple[str, float, float, float]] = [
    ("forgiving", 0.85, 0.15, 2.00),
    ("shipped",   0.70, 0.35, 1.60),
    ("brittle",   0.55, 0.60, 1.30),
    ("severe",    0.45, 0.90, 1.15),
]

CAPS = [0.20, 0.40, 0.60, 0.80, 1.00]
#: Divergence rises with the cap so a different rule does not bind first.
DIVERGENCE = {0.20: 0.40, 0.40: 0.60, 0.60: 0.80, 0.80: 0.90, 1.00: 0.97}


def _arm(days, seed, routing, policy, capacity):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents, capacity=capacity)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(policy), enable_routing=routing)
    return cp.run(days * 24 * 60), world, incidents


def run_cell(args) -> Dict[str, object]:
    """One paired run: one curve, one cap. Module-level so it can be pooled."""
    days, seed, label, knee, slope, headroom, cap = args
    capacity = CapacityModel(knee=knee, slope=slope, headroom=headroom)
    policy = PolicyConfig(max_shift_fraction=cap,
                          max_cumulative_divergence=DIVERGENCE[cap])

    control, cworld, cinc = _arm(days, seed, False, policy, capacity)
    treat, tworld, _ = _arm(days, seed, True, policy, capacity)

    if control.attempts != treat.attempts:
        return {"curve": label, "cap": cap, "valid": False}

    exposure = exposure_inr(control, cworld, cinc)
    recovered = treat.revenue_inr - control.revenue_inr
    return {
        "curve": label, "cap": cap, "valid": True,
        "knee": knee, "slope": slope, "headroom": headroom,
        "recovered_inr": recovered,
        "share_of_exposure": (recovered / exposure) if exposure else 0.0,
        "rollbacks": treat.rollbacks,
        "congestion_cost_txn": (tworld.success_lost_to_congestion
                                - cworld.success_lost_to_congestion),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.sensitivity")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int,
                    default=max(1, min(10, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    jobs = [(args.days, args.seed, label, knee, slope, headroom, cap)
            for (label, knee, slope, headroom) in CURVES for cap in CAPS]

    print(f"Sweeping {len(CAPS)} caps against {len(CURVES)} congestion curves "
          f"({len(jobs)} paired runs, {args.days} days each)...")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = [r for r in pool.map(run_cell, jobs) if r.get("valid")]

    by_curve: Dict[str, List[Dict[str, object]]] = {}
    for r in rows:
        by_curve.setdefault(str(r["curve"]), []).append(r)
    for v in by_curve.values():
        v.sort(key=lambda r: r["cap"])

    shipped_cap = PolicyConfig().max_shift_fraction
    w = 88
    print()
    print("=" * w)
    print("Share of exposure recovered, by congestion curve and shift cap")
    print("=" * w)
    header = "  " + f"{'curve':<12}" + "".join(f"{c:>10.0%}" for c in CAPS) \
             + f"{'best':>8}"
    print(header)
    print("  " + "-" * (w - 4))

    best_caps = []
    for label, _, _, _ in CURVES:
        cells = by_curve.get(label, [])
        if not cells:
            continue
        best = max(cells, key=lambda r: r["recovered_inr"])
        best_caps.append(float(best["cap"]))
        row = f"  {label:<12}"
        for c in cells:
            mark = "*" if c is best else " "
            row += f"{c['share_of_exposure']:>9.1%}{mark}"
        row += f"{best['cap']:>8.0%}"
        print(row)

    print()
    print("  * best cap for that curve")
    print()

    # Where does the router destroy value outright? That question comes first:
    # a relative gap between two negative numbers is meaningless, and an earlier
    # version of this report divided by one and printed a reassuring verdict on
    # curves where every setting lost money.
    harmful = sorted({str(r["curve"]) for r in rows
                      if float(r["recovered_inr"]) <= 0})
    beneficial = [label for label, _, _, _ in CURVES if label not in harmful]

    print("  Cost of holding the shipped cap, on curves where routing helps")
    print("  " + "-" * (w - 4))
    worst_gap = 0.0
    for label in beneficial:
        cells = by_curve.get(label, [])
        shipped = next((c for c in cells
                        if abs(float(c["cap"]) - shipped_cap) < 1e-9), None)
        best = max(cells, key=lambda r: r["recovered_inr"]) if cells else None
        if shipped is None or best is None or float(best["recovered_inr"]) <= 0:
            continue
        gap = float(best["recovered_inr"]) - float(shipped["recovered_inr"])
        rel = gap / float(best["recovered_inr"])
        worst_gap = max(worst_gap, rel)
        print(f"    {label:<12} best {float(best['cap']):.0%}, shipped "
              f"{shipped_cap:.0%} -> gives up Rs {gap:>11,.0f} ({rel:.1%})")

    spread = (max(best_caps) - min(best_caps)) if best_caps else 0.0
    print()
    print("=" * w)
    if harmful:
        print("  ROUTING IS NOT ALWAYS BENEFICIAL.")
        print(f"  On {', '.join(harmful)}, every cap tested recovers nothing or")
        print("  loses money. Those fleets are already past their capacity knee")
        print("  at rest, so there is no spare headroom to route into and")
        print("  shifting traffic only concentrates load.")
        print()
        print("  This is why the efficacy circuit breaker exists: no per-action")
        print("  bound can see that the strategy itself is not working. The")
        print("  breaker measures the realised effect of its own shifts and")
        print("  halts when they stop paying.")
        print()
    if beneficial:
        print(f"  Where routing does help ({', '.join(beneficial)}), the shipped")
        print(f"  {shipped_cap:.0%} cap stays within {worst_gap:.1%} of the best "
              f"setting, so the")
        print("  unmeasured curve is not load-bearing for that choice.")
    print("=" * w)

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"config": {"days": args.days, "seed": args.seed,
                                  "shipped_cap": shipped_cap},
                       "curves": [{"label": c[0], "knee": c[1], "slope": c[2],
                                   "headroom": c[3]} for c in CURVES],
                       "cells": rows,
                       "best_cap_spread": spread,
                       "worst_relative_gap": worst_gap,
                       "harmful_curves": harmful,
                       "beneficial_curves": beneficial}, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
