"""Compare the deterministic note against the LLM one, side by side.

    python -m revenueguard.narrate                 # template only
    python -m revenueguard.narrate --claude        # both, if a key resolves

Runs a short simulation, captures the attributions the control plane produced,
and renders each one twice. The point of showing both is that the *facts* are
identical -- only the prose differs, because the facts were computed before the
model was involved.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Tuple

from .config import GATEWAYS, ISSUERS, METHODS, WorldConfig
from .control_plane import ControlPlane
from .detectors import PosteriorDropDetector
from .narrator import ClaudeNarrator, TemplateNarrator, describe
from .policy import PolicyConfig, PolicyEngine
from .rootcause import RootCause, attribute
from .scenarios import default_incident_plan
from .world import World

ALL_SLICES = [f"{g}|{m}|{i}" for g in GATEWAYS for m in METHODS for i in ISSUERS]


def collect(days: int, seed: int, limit: int) -> List[Tuple[int, RootCause, float, float]]:
    """Re-derive the attributions, keeping the alarm evidence each rested on."""
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    detector = PosteriorDropDetector(min_drop_pp=3.0, confidence=0.99)
    cp = ControlPlane(world, detector, policy=PolicyEngine(PolicyConfig()),
                      enable_routing=True)

    captured: List[Tuple[int, RootCause, float, float]] = []
    by_minute = {}
    for alarm in cp.run(days * 24 * 60).alarms:
        by_minute.setdefault(alarm.minute, []).append(alarm)

    for minute in sorted(by_minute):
        alarms = by_minute[minute]
        cause = attribute([a.slice_key for a in alarms], ALL_SLICES)
        worst = min(alarms, key=lambda a: a.observed_sr)
        captured.append((minute, cause, worst.baseline_sr, worst.observed_sr))
        if len(captured) >= limit:
            break
    return captured


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.narrate")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--claude", action="store_true",
                    help="also render with Claude, if a credential resolves")
    args = ap.parse_args(argv)

    template = TemplateNarrator()
    claude = ClaudeNarrator() if args.claude else None

    print("=" * 78)
    print("Incident narration")
    print("=" * 78)
    print(f"  ledger text : {describe(template)}")
    if claude is not None:
        print(f"  console text: {describe(claude)}")
    print()
    print("  The facts below were computed deterministically before any model")
    print("  was involved. The LLM restates them; it does not derive them, and")
    print("  its output never reaches the policy engine or the ledger.")
    print("=" * 78)

    for minute, cause, base, obs in collect(args.days, args.seed, args.limit):
        stamp = f"d{minute // 1440 + 1} {(minute // 60) % 24:02d}:{minute % 60:02d}"
        print()
        print(f"  [{stamp}]  {len(cause.alarming_slices)} slices alarming, "
              f"cause label '{cause.label}'")
        print(f"  {'-' * 74}")
        print("  deterministic (this is what the ledger stores):")
        for line in _wrap(template.narrate(cause, base, obs)):
            print(f"    {line}")
        if claude is not None:
            print()
            print("  claude (console only):")
            for line in _wrap(claude.narrate(cause, base, obs)):
                print(f"    {line}")

    if claude is not None:
        print()
        print("=" * 78)
        print(f"  {claude.calls} model call(s), {claude.fallbacks} fell back "
              f"to the template.")
        if claude.disabled_reason:
            print(f"  last fallback reason: {claude.disabled_reason}")
        print("=" * 78)
    return 0


def _wrap(text: str, width: int = 72) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        if len(cur) + len(word) + 1 > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


if __name__ == "__main__":
    sys.exit(main())
