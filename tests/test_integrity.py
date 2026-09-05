"""Structural guarantees, and compliance with the track's stated bar.

Two things are checked here that no amount of prose can establish:

1. **The detector cannot see the answers.** The whole evaluation rests on the
   claim that nothing downstream of the simulator knows the incident plan. That
   is a property of the import graph, so it is checked against the import graph
   rather than trusted.

2. **Every element of the track's bar is actually implemented.** Razorpay's
   Track 03 asks for measured money recovered across a batch, with compliant
   escalation, stopping rules, and an audit trail. Each of those is asserted
   against a real run, so a refactor that quietly removes one fails here.
"""
import io
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from razorguard.config import WorldConfig
from razorguard.control_plane import ControlPlane
from razorguard.detectors import default_detector
from razorguard.policy import PolicyConfig, PolicyEngine
from razorguard.scenarios import default_incident_plan
from razorguard.world import World

PKG = pathlib.Path(__file__).resolve().parent.parent / "razorguard"
RESULTS = pathlib.Path(__file__).resolve().parent.parent / "bench" / "results"


def source(*parts) -> str:
    return (PKG.joinpath(*parts)).read_text(encoding="utf-8")


# -- 1. the detector cannot see the answers ----------------------------------

def test_no_detector_can_import_the_incident_plan():
    """If a detector could read scenarios.py, every metric here is worthless."""
    for path in (PKG / "detectors").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "scenarios" not in text, f"{path.name} can see the ground truth"
        assert "Incident" not in text, f"{path.name} references incidents"


def test_the_observation_carries_no_ground_truth():
    world = World(WorldConfig(seed=7), default_incident_plan(1))
    from razorguard.routing import RoutingTable
    from razorguard.config import ISSUERS, METHODS

    observations = world.step(600, RoutingTable.default(METHODS, ISSUERS))
    assert observations
    fields = set(vars(observations[0]))
    assert fields == {"minute", "slice_key", "method", "attempts",
                      "successes", "avg_ticket_inr"}, (
        f"an Observation grew a field: {fields}. The detector must see counts "
        f"and nothing else.")


def test_the_decision_path_does_not_import_the_language_model():
    for name in ("policy.py", "routing.py", "control_plane.py", "audit.py",
                 "rootcause.py"):
        assert "narrator" not in source(name), (
            f"{name} imports the narrator; a sampled token would be in the "
            f"path of a money-moving decision")


def test_no_result_values_are_hardcoded_in_the_package():
    """Headline figures must come from a run, never from a constant."""
    forbidden = ["12291379", "1,22,91,379", "38.2%", "11,452"]
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, (
                f"{path.name} contains the literal {needle!r}; results belong "
                f"in bench/results, not in source")


# -- 2. the track's bar ------------------------------------------------------

def _run():
    world = World(WorldConfig(seed=7), default_incident_plan(2))
    plane = ControlPlane(world, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True)
    return plane, plane.run(2880)


def test_bar_detects_revenue_at_risk():
    _, out = _run()
    assert len(out.alarms) > 0


def test_bar_determines_the_intervention():
    """Attribution, then a named destination, then a verdict."""
    _, out = _run()
    proposals = out.ledger.of_kind("proposal")
    assert proposals
    assert any(e.evidence.get("target") for e in proposals)
    assert out.ledger.of_kind("detection")


def test_bar_executes_a_bounded_recovery_workflow():
    plane, out = _run()
    assert out.actions > 0
    cfg = plane.policy.config
    for event in out.ledger.of_kind("action"):
        moved = float(event.evidence.get("moved", 0))
        assert 0 < moved <= cfg.max_shift_fraction + 1e-6, (
            f"an action moved {moved:.3f}, beyond the {cfg.max_shift_fraction} "
            f"cap")


def test_bar_measures_money_recovered_across_a_batch():
    """Not one cherry-picked incident - a paired run over the whole batch."""
    payload = json.loads((RESULTS / "experiment.json").read_text(encoding="utf-8"))
    assert payload["valid"] is True
    assert payload["attempts"] > 1_000_000
    assert payload["recovered"]["revenue_inr"] > 0
    # The guard that makes the comparison meaningful at all.
    assert payload["control"]["successes"] != payload["treatment"]["successes"]


def test_bar_has_compliant_escalation():
    _, out = _run()
    assert out.escalated > 0
    escalations = [e for e in out.ledger.of_kind("decision")
                   if e.evidence.get("decision") == "escalate"]
    assert escalations
    assert all(e.rule for e in escalations), (
        "an escalation with no rule attached cannot be reviewed")


def test_bar_has_stopping_rules():
    plane, out = _run()
    rules = {e.rule for e in out.ledger.of_kind("decision") if e.rule}
    stopping = {"max_causes_per_hour", "max_actions_per_hour",
                "action_cooldown", "no_healthy_destination",
                "efficacy_breaker"}
    assert rules & stopping, (
        f"no stopping rule ever fired; only these rules appeared: {rules}")


def test_bar_has_an_audit_trail_including_refusals():
    _, out = _run()
    kinds = {e.kind for e in out.ledger}
    assert {"detection", "proposal", "decision", "action"} <= kinds
    assert out.ledger.blocked_by_rule(), (
        "the ledger records no refusals; a trail of successes only is the "
        "half worth reviewing missing")
    seqs = [e.seq for e in out.ledger]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_the_data_provenance_document_exists_and_names_the_inventions():
    """A reader must be able to find out what is real without reading the code."""
    data = io.open(pathlib.Path(__file__).resolve().parent.parent / "DATA.md",
                   encoding="utf-8").read()
    for topic in ("ticket", "congestion", "invented", "dry_run", "narrator"):
        assert topic.lower() in data.lower(), (
            f"DATA.md does not mention {topic}")
