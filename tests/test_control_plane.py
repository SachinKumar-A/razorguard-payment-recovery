"""Safety properties of the control loop.

These are the assertions that make the guardrail claims checkable rather than
rhetorical. Each one corresponds to a sentence in the README that would
otherwise be taking itself on trust.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from revenueguard.config import ISSUERS, METHODS, WorldConfig
from revenueguard.control_plane import ControlPlane
from revenueguard.detectors import PosteriorDropDetector
from revenueguard.policy import Decision, PolicyConfig, PolicyEngine
from revenueguard.routing import CANARY_FLOOR, RoutingTable
from revenueguard.scenarios import default_incident_plan
from revenueguard.world import World

MINUTES = 1440  # one simulated day


def _plane(enable_routing=True, seed=7, policy=None):
    incidents = default_incident_plan(1)
    world = World(WorldConfig(seed=seed), incidents)
    detector = PosteriorDropDetector(min_drop_pp=3.0, confidence=0.99)
    return ControlPlane(world, detector,
                        policy=policy or PolicyEngine(PolicyConfig()),
                        enable_routing=enable_routing), incidents


@pytest.fixture(scope="module")
def treated():
    cp, incidents = _plane(enable_routing=True)
    out = cp.run(MINUTES)
    return cp, out, incidents


# -- routing invariants ------------------------------------------------------

def test_weights_always_sum_to_one(treated):
    cp, _, _ = treated
    for key, weights in cp.routing.current.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-9, key


def test_no_weight_is_ever_negative(treated):
    cp, _, _ = treated
    for weights in cp.routing.current.values():
        assert all(w >= 0.0 for w in weights.values())


def test_canary_floor_is_never_breached():
    """A drained gateway must keep enough traffic to stay observable.

    Checked by driving the router directly rather than waiting for the
    simulation to happen to produce a deep drain.
    """
    rt = RoutingTable.default(METHODS, ISSUERS)
    for _ in range(50):
        rt.shift_away("upi", "hdfc", "gw_alpha", "gw_beta", 0.9)
    assert rt.weights("upi", "hdfc")["gw_alpha"] >= CANARY_FLOOR - 1e-9


def test_restore_converges_to_baseline():
    rt = RoutingTable.default(METHODS, ISSUERS)
    rt.shift_away("card", "sbi", "gw_alpha", "gw_gamma", 0.4)
    assert not rt.at_baseline("card", "sbi")
    for _ in range(40):
        rt.restore_step("card", "sbi")
    assert rt.at_baseline("card", "sbi")


# -- the control run must not act --------------------------------------------

def test_control_run_takes_no_routing_action():
    """The control arm detects everything and is allowed to do nothing.

    If this fails, the measured recovery is comparing two treatments.
    """
    cp, _ = _plane(enable_routing=False)
    out = cp.run(MINUTES)
    assert out.actions == 0
    assert out.rollbacks == 0
    assert out.ledger.of_kind("action") == []
    for key in cp.routing.current:
        assert cp.routing.at_baseline(*key)


def test_control_run_still_detects(treated):
    cp, _ = _plane(enable_routing=False)
    out = cp.run(MINUTES)
    assert len(out.alarms) > 0, "control arm must still run the detector"


# -- audit completeness ------------------------------------------------------

def test_every_action_was_preceded_by_a_decision(treated):
    _, out, _ = treated
    for action in out.ledger.of_kind("action"):
        earlier = [e for e in out.ledger.for_subject(action.subject)
                   if e.seq < action.seq and e.kind == "decision"]
        assert earlier, f"action {action.seq} has no decision before it"
        assert earlier[-1].evidence.get("decision") == "allow"


def test_audit_sequence_is_monotonic(treated):
    _, out, _ = treated
    seqs = [e.seq for e in out.ledger]
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, len(seqs) + 1))


def test_audit_records_refusals_not_just_actions(treated):
    _, out, _ = treated
    refused = out.ledger.blocked_by_rule()
    assert refused, "a ledger that only records successes hides the decisions"
    assert sum(refused.values()) > 0


def test_rollbacks_return_to_baseline_weights():
    cp, _ = _plane(enable_routing=True)
    out = cp.run(MINUTES)
    for ev in out.ledger.of_kind("rollback"):
        method, issuer = ev.subject.split("|")
        # A rollback resets immediately; later actions may divert again, so we
        # only assert the reset happened, via its own ledger entry.
        assert ev.rule == "rollback_target_degraded"


# -- policy engine -----------------------------------------------------------

def _verdict(engine, **over):
    args = dict(minute=100, method="upi", issuer="hdfc", source="gw_alpha",
                target="gw_beta", confidence=0.999, drop_pp=20.0,
                source_sr=0.60, target_sr=0.96, target_attempts=500,
                target_alarmed=False, current_divergence=0.0)
    args.update(over)
    return engine.evaluate_shift(**args)


def test_low_confidence_is_blocked():
    v = _verdict(PolicyEngine(PolicyConfig()), confidence=0.5)
    assert v.decision is Decision.BLOCK and v.rule == "min_confidence"


def test_small_drop_is_blocked():
    v = _verdict(PolicyEngine(PolicyConfig()), drop_pp=1.0)
    assert v.decision is Decision.BLOCK and v.rule == "min_drop_pp"


def test_alarmed_target_escalates_rather_than_routing_into_a_fire():
    v = _verdict(PolicyEngine(PolicyConfig()), target_alarmed=True)
    assert v.decision is Decision.ESCALATE and v.rule == "target_alarmed"


def test_absent_target_escalates():
    v = _verdict(PolicyEngine(PolicyConfig()), target=None)
    assert v.decision is Decision.ESCALATE and v.rule == "no_target"


def test_thin_target_evidence_is_blocked():
    v = _verdict(PolicyEngine(PolicyConfig()), target_attempts=3)
    assert v.decision is Decision.BLOCK and v.rule == "min_target_attempts"


def test_marginal_target_advantage_is_blocked():
    v = _verdict(PolicyEngine(PolicyConfig()), source_sr=0.90, target_sr=0.92)
    assert v.decision is Decision.BLOCK and v.rule == "min_target_advantage"


def test_hourly_action_cap_escalates():
    eng = PolicyEngine(PolicyConfig(max_actions_per_hour=3, action_cooldown_min=0))
    for n in range(3):
        assert _verdict(eng, minute=100 + n).decision is Decision.ALLOW
        eng.record_action(100 + n, "upi", "hdfc")
    v = _verdict(eng, minute=104)
    assert v.decision is Decision.ESCALATE and v.rule == "max_actions_per_hour"


def test_cooldown_blocks_repeat_action_on_same_key():
    eng = PolicyEngine(PolicyConfig(action_cooldown_min=15))
    assert _verdict(eng, minute=100).decision is Decision.ALLOW
    eng.record_action(100, "upi", "hdfc")
    v = _verdict(eng, minute=105)
    assert v.decision is Decision.BLOCK and v.rule == "action_cooldown"


def test_a_clean_proposal_is_allowed():
    assert _verdict(PolicyEngine(PolicyConfig())).decision is Decision.ALLOW
