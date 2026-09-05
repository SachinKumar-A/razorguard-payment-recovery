"""Net recovery, and advice on refusals the rulebook cannot answer."""
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from razorguard.advisor import (ACTIONS, Advisor, Recommendation,
                                escalations_in)
from razorguard.audit import AuditLedger
from razorguard.config import WorldConfig
from razorguard.control_plane import ControlPlane
from razorguard.detectors import default_detector
from razorguard.economics import (fee_for, marginal_cost_of_shift,
                                  net_recovery, processing_cost)
from razorguard.investigator import Evidence, evidence_from_run
from razorguard.policy import PolicyConfig, PolicyEngine
from razorguard.scenarios import default_incident_plan
from razorguard.simulator import Observation
from razorguard.world import World

PKG = pathlib.Path(__file__).resolve().parent.parent / "razorguard"


def obs(slice_key, attempts=100, successes=90, ticket=640.0, minute=0):
    return Observation(minute=minute, slice_key=slice_key,
                       method=slice_key.split("|")[1], attempts=attempts,
                       successes=successes, avg_ticket_inr=ticket)


# -- the Indian asymmetry ----------------------------------------------------

def test_upi_is_free_because_its_mdr_is_zero_by_regulation():
    """Not a rounding. UPI person-to-merchant carries no MDR in India."""
    for gateway in ("gw_alpha", "gw_beta", "gw_gamma"):
        assert fee_for(gateway, "upi", 640.0) == 0.0


def test_cards_are_not_free():
    assert fee_for("gw_beta", "card", 2150.0) > 0


def test_netbanking_carries_a_flat_fee_as_well_as_a_rate():
    small = fee_for("gw_beta", "netbanking", 100.0)
    assert small > 100.0 * 0.02, "the flat component should dominate a small ticket"


def test_acquirers_are_priced_differently():
    a = fee_for("gw_alpha", "card", 2150.0)
    c = fee_for("gw_gamma", "card", 2150.0)
    assert a != c


def test_a_upi_shift_costs_nothing_whichever_way_it_goes():
    assert marginal_cost_of_shift("gw_beta", "gw_alpha", "upi", 640.0) == 0.0
    assert marginal_cost_of_shift("gw_alpha", "gw_gamma", "upi", 640.0) == 0.0


def test_a_card_shift_to_a_pricier_acquirer_costs_money():
    delta = marginal_cost_of_shift("gw_gamma", "gw_alpha", "card", 2150.0)
    assert delta > 0


# -- cost accounting ---------------------------------------------------------

def test_fees_are_charged_on_successes_not_attempts():
    """A declined payment costs the merchant the sale, not a discount rate."""
    a = processing_cost([obs("gw_beta|card|hdfc", attempts=100, successes=100,
                             ticket=2150.0)])
    b = processing_cost([obs("gw_beta|card|hdfc", attempts=100, successes=50,
                             ticket=2150.0)])
    assert a.total == pytest.approx(2 * b.total)


def test_cost_breaks_down_by_method_and_gateway():
    cost = processing_cost([obs("gw_beta|card|hdfc", ticket=2150.0),
                            obs("gw_alpha|upi|hdfc")])
    assert cost.by_method["upi"] == 0.0
    assert cost.by_method["card"] > 0
    assert set(cost.by_gateway) == {"gw_alpha", "gw_beta"}


def test_zero_success_rows_are_skipped():
    assert processing_cost([obs("gw_beta|card|hdfc", successes=0)]).total == 0.0


# -- net recovery ------------------------------------------------------------

def test_net_is_gross_minus_the_incremental_fees():
    control = [obs("gw_beta|card|hdfc", successes=80, ticket=2150.0)]
    treatment = [obs("gw_alpha|card|hdfc", successes=90, ticket=2150.0)]
    net = net_recovery(control, treatment, gross_inr=21500.0,
                       payments_recovered=10)
    assert net.incremental_cost_inr == pytest.approx(
        net.treatment_cost_inr - net.control_cost_inr)
    assert net.net_inr == pytest.approx(net.gross_inr - net.incremental_cost_inr)


def test_recovering_more_costs_more_in_fees_and_that_is_the_good_kind():
    control = [obs("gw_beta|card|hdfc", successes=80, ticket=2150.0)]
    treatment = [obs("gw_beta|card|hdfc", successes=95, ticket=2150.0)]
    net = net_recovery(control, treatment, gross_inr=32250.0,
                       payments_recovered=15)
    assert net.incremental_cost_inr > 0
    assert net.worth_doing


def test_a_recovery_that_costs_more_than_it_saves_is_flagged():
    control = [obs("gw_gamma|card|hdfc", successes=100, ticket=2150.0)]
    treatment = [obs("gw_alpha|card|hdfc", successes=100, ticket=2150.0)]
    net = net_recovery(control, treatment, gross_inr=1.0, payments_recovered=0)
    assert net.incremental_cost_inr > 0
    assert net.worth_doing is False


def test_a_pure_upi_recovery_is_all_profit():
    control = [obs("gw_beta|upi|hdfc", successes=80)]
    treatment = [obs("gw_alpha|upi|hdfc", successes=95)]
    net = net_recovery(control, treatment, gross_inr=9600.0,
                       payments_recovered=15)
    assert net.incremental_cost_inr == 0.0
    assert net.net_inr == net.gross_inr


def test_cost_ratio_is_none_without_a_gross_recovery():
    assert net_recovery([], [], gross_inr=0.0, payments_recovered=0).cost_ratio is None


# -- the advisor -------------------------------------------------------------

@pytest.fixture(scope="module")
def run():
    world = World(WorldConfig(seed=7), default_incident_plan(2))
    plane = ControlPlane(world, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True)
    return plane.run(2880)


def test_it_advises_only_on_refusals(run):
    escalations = escalations_in(run.ledger)
    assert escalations
    assert all(e.evidence.get("decision") == "escalate" for e in escalations)
    assert all(e.kind == "decision" for e in escalations)


def test_every_escalation_rule_has_a_standing_answer(run):
    """A refusal with no next step leaves the engineer where they started."""
    advisor = Advisor(evidence_from_run(run))
    advisor.client = None                       # force the runbook fallback
    seen = set()
    for event in escalations_in(run.ledger):
        if event.rule in seen:
            continue
        seen.add(event.rule)
        advice = advisor.advise(event)
        assert advice.valid, f"no standing answer for rule {event.rule}"
        assert advice.reasoning
    assert len(seen) >= 3


def test_issuer_wide_faults_are_pointed_at_the_issuer():
    ledger = AuditLedger()
    event = ledger.record(10, "decision", "upi|hdfc",
                          "every gateway serving upi/hdfc is degraded",
                          rule="no_healthy_destination", decision="escalate")
    advisor = Advisor(Evidence(ledger=ledger))
    advisor.client = None
    assert advisor.advise(event).action == "contact_issuer"


def test_a_failing_model_falls_back_rather_than_propagating():
    ledger = AuditLedger()
    event = ledger.record(10, "decision", "upi|hdfc", "x",
                          rule="no_healthy_destination", decision="escalate")

    class Boom:
        class beta:
            class messages:
                @staticmethod
                def tool_runner(**_):
                    raise RuntimeError("upstream down")

    advisor = Advisor(Evidence(ledger=ledger))
    advisor.client = Boom()
    advice = advisor.advise(event)
    assert advice.valid
    assert advice.used_model is False
    assert "upstream down" in (advice.note or "")


def test_an_invalid_action_is_rejected():
    assert not Recommendation("delete_everything", "x", "high").valid
    assert Recommendation("contact_issuer", "x", "high").valid


def test_the_action_set_is_closed_and_small():
    """An open-ended recommendation is hard to act on and impossible to audit."""
    assert 4 <= len(ACTIONS) <= 8


# -- the boundary is unchanged -----------------------------------------------

def test_no_decision_path_module_imports_the_advisor():
    for name in ("policy.py", "routing.py", "control_plane.py", "audit.py",
                 "rootcause.py", "world.py"):
        source = (PKG / name).read_text(encoding="utf-8")
        assert "advisor" not in source, (
            f"{name} imports the advisor; advice must not reach a decision")


def test_advice_is_recorded_as_advice_not_as_a_decision():
    advice = Recommendation("contact_issuer", "because", "high", used_model=True)
    evidence = advice.as_evidence()
    assert evidence["advice_source"] == "claude"
    assert "decision" not in evidence
