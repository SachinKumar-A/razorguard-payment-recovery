"""Control vs treatment: how much money the router actually recovers.

The only honest way to claim recovered revenue is to run the same demand twice
-- once with the router off, once with it on -- and take the difference. Anything
else is a projection dressed up as a measurement.

Demand is bit-identical across the two runs by construction (see `world.py`), so
the total number of payment attempts must come out equal. The run prints that
equality as a check: if it ever fails, the comparison is invalid and the number
below it means nothing.

    python -m revenueguard.experiment --days 2 --json bench/results/experiment.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

from .config import WorldConfig
from .control_plane import ControlPlane, RunOutcome
from .detectors import default_detector
from .policy import PolicyConfig, PolicyEngine
from .routing import CANARY_FLOOR
from .scenarios import Incident, default_incident_plan
from .world import World


def _run(days: int, seed: int, enable_routing: bool,
         detector_kw: Optional[dict] = None):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(PolicyConfig()),
                      enable_routing=enable_routing)
    return cp.run(days * 24 * 60), world, incidents


def exposure_inr(out: RunOutcome, world: World,
                 incidents: List[Incident]) -> float:
    """Money lost *to the incidents*, against a no-incident counterfactual.

    Restricted to cells an incident was actually degrading at that minute. The
    obvious wider definition -- every shortfall against the healthy rate,
    anywhere -- is wrong, and wrong in the flattering direction for the
    denominator: ordinary binomial noise puts roughly half of all healthy
    slice-minutes below their own mean, so counting those inflates "money that
    was there to win" by an order of magnitude and makes any recovery figure
    look small and arbitrary. Only incident-attributable loss belongs here.
    """
    windows = [(inc.start_min, inc.end_min, inc.affected) for inc in incidents]
    total = 0.0
    for o in out.observations:
        hit = any(start <= o.minute < end and o.slice_key in affected
                  for start, end, affected in windows)
        if not hit:
            continue
        shortfall = world.counterfactual_healthy_successes(o) - o.successes
        if shortfall > 0:
            total += shortfall * o.avg_ticket_inr
    return total


def _revenue_by_method(out: RunOutcome) -> Dict[str, float]:
    acc: Dict[str, float] = {}
    for o in out.observations:
        acc[o.method] = acc.get(o.method, 0.0) + o.successes * o.avg_ticket_inr
    return acc


def report(control: RunOutcome, treat: RunOutcome, days: int,
           control_exposure: float = 0.0, treat_exposure: float = 0.0) -> dict:
    w = 82
    print("=" * w)
    print(f"RevenueGuard -- control vs treatment over {days} simulated days")
    print("=" * w)

    same_demand = control.attempts == treat.attempts
    print(f"  demand identical across runs   "
          f"{control.attempts:,} vs {treat.attempts:,}   "
          f"{'OK' if same_demand else 'MISMATCH -- comparison invalid'}")
    if not same_demand:
        print("  Refusing to report recovery: the two runs did not face the same "
              "demand.")
        print("=" * w)
        return {"valid": False}

    d_succ = treat.successes - control.successes
    d_rev = treat.revenue_inr - control.revenue_inr
    ctrl_sr = control.successes / control.attempts
    treat_sr = treat.successes / treat.attempts

    print()
    print(f"  {'':22s} {'router off':>16s} {'router on':>16s} {'delta':>16s}")
    print("  " + "-" * (w - 4))
    print(f"  {'attempts':22s} {control.attempts:>16,} {treat.attempts:>16,} "
          f"{0:>16,}")
    print(f"  {'successful payments':22s} {control.successes:>16,} "
          f"{treat.successes:>16,} {d_succ:>+16,}")
    print(f"  {'overall success rate':22s} {ctrl_sr:>15.2%} {treat_sr:>15.2%} "
          f"{(treat_sr - ctrl_sr) * 100:>+15.2f}pp")
    print(f"  {'revenue (Rs)':22s} {control.revenue_inr:>16,.0f} "
          f"{treat.revenue_inr:>16,.0f} {d_rev:>+16,.0f}")

    print()
    print(f"  Recovered: Rs {d_rev:,.0f} across {days} days "
          f"({d_succ:,} payments that failed under the control policy and "
          f"succeeded under routing).")

    if control_exposure > 0:
        efficiency = d_rev / control_exposure
        print()
        print("  Against what was there to win")
        print("  " + "-" * (w - 4))
        print(f"  {'incident exposure, router off':32s} "
              f"Rs {control_exposure:>14,.0f}")
        print(f"  {'incident exposure, router on':32s} "
              f"Rs {treat_exposure:>14,.0f}")
        print(f"  {'recovered share of exposure':32s} {efficiency:>17.1%}")
        print()
        print("  The rest is structural, not a bug to hide: money lost before")
        print("  detection fires, plus money the policy deliberately leaves on")
        print(f"  the table -- no more than {PolicyConfig().max_shift_fraction:.0%} "
              f"of a key moves per action and a")
        print(f"  {CANARY_FLOOR:.0%} canary always stays on the degraded gateway.")

    print()
    print("  Guardrails")
    print("  " + "-" * (w - 4))
    print(f"  {'routing actions taken':32s} {treat.actions:>8,}")
    print(f"  {'proposals blocked by policy':32s} {treat.blocked:>8,}")
    print(f"  {'escalated to a human':32s} {treat.escalated:>8,}")
    print(f"  {'rollbacks (target got worse)':32s} {treat.rollbacks:>8,}")
    print(f"  {'restore steps (source healed)':32s} {treat.restores:>8,}")
    print(f"  {'audit events written':32s} {len(treat.ledger):>8,}")

    blocked = treat.ledger.blocked_by_rule()
    if blocked:
        print()
        print("  Why proposals were refused")
        print("  " + "-" * (w - 4))
        for rule, n in sorted(blocked.items(), key=lambda kv: -kv[1]):
            print(f"    {rule:34s} {n:>6,}")

    print()
    print("  Every line above is a difference between two runs of the same")
    print("  demand, not a projection. The control run detects the same")
    print("  incidents; it simply is not allowed to act on them.")
    print("=" * w)

    return {
        "valid": True,
        "days": days,
        "attempts": control.attempts,
        "control": {
            "successes": control.successes,
            "success_rate": round(ctrl_sr, 6),
            "revenue_inr": round(control.revenue_inr),
            "revenue_by_method": {k: round(v) for k, v in
                                  _revenue_by_method(control).items()},
        },
        "treatment": {
            "successes": treat.successes,
            "success_rate": round(treat_sr, 6),
            "revenue_inr": round(treat.revenue_inr),
            "revenue_by_method": {k: round(v) for k, v in
                                  _revenue_by_method(treat).items()},
            "actions": treat.actions,
            "blocked": treat.blocked,
            "escalated": treat.escalated,
            "rollbacks": treat.rollbacks,
            "restores": treat.restores,
            "audit_events": len(treat.ledger),
            "blocked_by_rule": blocked,
        },
        "recovered": {
            "payments": d_succ,
            "revenue_inr": round(d_rev),
            "success_rate_gain_pp": round((treat_sr - ctrl_sr) * 100, 4),
            "control_exposure_inr": round(control_exposure),
            "treatment_exposure_inr": round(treat_exposure),
            "share_of_exposure_recovered": (
                round(d_rev / control_exposure, 4) if control_exposure else None),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.experiment")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--audit", type=str, default=None,
                    help="write the treatment run's audit ledger as JSONL")
    args = ap.parse_args(argv)

    # The detector is fixed in detectors/default_detector(), chosen by the
    # sweep rather than tuned here.
    control, cworld, cinc = _run(args.days, args.seed, enable_routing=False)
    treat, tworld, tinc = _run(args.days, args.seed, enable_routing=True)

    payload = report(control, treat, args.days,
                     exposure_inr(control, cworld, cinc),
                     exposure_inr(treat, tworld, tinc))

    if args.audit:
        treat.ledger.write_jsonl(args.audit)
        print(f"\nwrote {args.audit} ({len(treat.ledger):,} events)")
    if args.json:
        import os
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"wrote {args.json}")
    return 0 if payload.get("valid") else 1


if __name__ == "__main__":
    sys.exit(main())
