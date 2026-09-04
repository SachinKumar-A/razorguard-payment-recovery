"""CLI for the execution path.

    python -m revenueguard.execute                 # dry run, no credentials
    python -m revenueguard.execute --limit 5 --live --payment-links

Live mode needs RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET, both test-mode.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from .config import METHOD_TICKET, WorldConfig
from .control_plane import ControlPlane
from .detectors import default_detector
from .executor import (DryRunExecutor, ExecutionResult, RazorpayTestExecutor,
                       intents_from_ledger)
from .policy import PolicyConfig, PolicyEngine
from .scenarios import default_incident_plan
from .world import World


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.execute")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--live", action="store_true",
                    help="actually call Razorpay test mode (needs env keys)")
    ap.add_argument("--payment-links", action="store_true",
                    help="also create a payment link per recovery")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args(argv)

    incidents = default_incident_plan(args.days)
    world = World(WorldConfig(seed=args.seed), incidents)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(PolicyConfig()), enable_routing=True)
    out = cp.run(args.days * 24 * 60)

    intents = intents_from_ledger(list(out.ledger), METHOD_TICKET, args.limit)
    if not intents:
        print("no routing actions in this run; nothing to execute")
        return 0

    if args.live:
        try:
            ex = RazorpayTestExecutor(create_payment_link=args.payment_links)
        except RuntimeError as exc:
            print(f"cannot run live: {exc}")
            return 2
        print(f"executing {len(intents)} recoveries against Razorpay TEST mode "
              f"({ex.key_id[:16]}...)")
    else:
        ex = DryRunExecutor()
        print(f"dry run: building {len(intents)} recovery orders, sending none")
        print("  (add --live with RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET to send)")

    print()
    results: List[ExecutionResult] = [ex.execute(i) for i in intents]
    for r in results:
        print(r.line())
        if r.payment_link:
            print(f"        link: {r.payment_link}")

    ok = sum(1 for r in results if r.ok)
    print()
    print(f"  {ok}/{len(results)} succeeded, mode={ex.mode}")
    print()
    print("  Note: routing weights are not settable through Razorpay's public")
    print("  API -- acquirer selection is Razorpay's own product. What is")
    print("  executed here is the re-attempt, carrying the decision context in")
    print("  the order's notes so it is auditable outside this repo too.")

    if args.json:
        import os
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([{
                "audit_seq": r.intent.audit_seq,
                "subject": r.intent.subject,
                "amount_inr": r.intent.amount_inr,
                "from": r.intent.source_gateway,
                "to": r.intent.target_gateway,
                "ok": r.ok, "mode": r.mode,
                "order_id": r.order_id, "payment_link": r.payment_link,
                "error": r.error,
            } for r in results], fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
