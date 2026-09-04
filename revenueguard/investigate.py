"""Ask the control plane why it did something.

    python -m revenueguard.investigate "why did traffic move off gw_beta at 03:12?"
    python -m revenueguard.investigate --list-questions
    python -m revenueguard.investigate --days 2 "was the 20:05 rollback justified?"

Runs the simulation, then hands the resulting audit ledger and observation
stream to an agent that can query them. The agent decides what to look at; it
is not a fixed report.

Without an API key it degrades to printing the relevant ledger window, which is
less useful than an investigation and considerably more useful than an error.
"""
from __future__ import annotations

import argparse
import sys
from typing import List

from .config import WorldConfig
from .control_plane import ControlPlane
from .detectors import default_detector
from .investigator import Investigator, evidence_from_run
from .policy import PolicyConfig, PolicyEngine
from .scenarios import default_incident_plan
from .world import World

SUGGESTED = [
    "Why did traffic move off gw_beta around 03:12, and did it help?",
    "Was any routing action refused, and which rule refused it?",
    "Find the worst-performing slice of the run and explain what the system "
    "did about it.",
    "Did any rollback happen, and was reverting the right call?",
    "Were there incidents the system detected but never acted on? Why not?",
]


def run(days: int, seed: int):
    world = World(WorldConfig(seed=seed), default_incident_plan(days))
    plane = ControlPlane(world, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True)
    return plane.run(days * 24 * 60)


def wrap(text: str, width: int = 76) -> List[str]:
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="revenueguard.investigate")
    ap.add_argument("question", nargs="*", help="what to ask")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--effort", default="medium",
                    choices=["low", "medium", "high"])
    ap.add_argument("--list-questions", action="store_true")
    ap.add_argument("--show-tools", action="store_true",
                    help="print each tool call the agent made")
    args = ap.parse_args(argv)

    if args.list_questions:
        print("Suggested questions:")
        for q in SUGGESTED:
            print(f"  - {q}")
        return 0

    question = " ".join(args.question).strip() or SUGGESTED[0]

    print("=" * 80)
    print("Incident investigation")
    print("=" * 80)
    print(f"  simulating {args.days} days (seed {args.seed})...")
    outcome = run(args.days, args.seed)
    evidence = evidence_from_run(outcome)
    print(f"  evidence: {len(evidence.ledger):,} ledger entries, "
          f"{len(evidence.observations):,} observations")

    agent = Investigator(evidence, effort=args.effort)
    print(f"  agent: {'claude ' + agent.model if agent.available else 'unavailable'}")
    print()
    print(f"  Q: {question}")
    print("  " + "-" * 76)

    result = agent.ask(question)

    if result.tool_calls and args.show_tools:
        print("  tool calls:")
        for call in result.tool_calls:
            print(f"    {call[:120]}")
        print("  " + "-" * 76)

    for line in result.answer.split("\n"):
        for wrapped in (wrap(line) or [""]):
            print(f"  {wrapped}")

    print()
    print("  " + "-" * 76)
    if result.used_model:
        print(f"  {len(result.tool_calls)} tool call(s) over {result.rounds} "
              f"round(s). Every tool is read-only; nothing here can change a "
              f"routing decision.")
    else:
        print(f"  Ran without a model. {result.note or ''}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
