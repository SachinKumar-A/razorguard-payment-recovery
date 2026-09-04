"""The investigator may read everything and change nothing.

It is the one place a model is given real latitude - it decides what to query
and how many times. That is only safe because every tool it has is read-only
and there is no path from its answer back into a decision.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import revenueguard.investigator as inv
from revenueguard.audit import AuditLedger
from revenueguard.config import WorldConfig
from revenueguard.control_plane import ControlPlane
from revenueguard.detectors import default_detector
from revenueguard.investigator import (Evidence, Investigator,
                                       READ_ONLY_TOOLS, evidence_from_run)
from revenueguard.policy import PolicyConfig, PolicyEngine
from revenueguard.scenarios import default_incident_plan
from revenueguard.simulator import Observation
from revenueguard.world import World

PKG = pathlib.Path(__file__).resolve().parent.parent / "revenueguard"


@pytest.fixture(scope="module")
def evidence():
    world = World(WorldConfig(seed=7), default_incident_plan(1))
    plane = ControlPlane(world, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True)
    return evidence_from_run(plane.run(1440))


@pytest.fixture
def context(evidence):
    inv._CONTEXT = evidence
    yield evidence
    inv._CONTEXT = None


# -- the tools return real data ----------------------------------------------

def test_search_audit_filters_by_kind(context):
    result = json.loads(inv.search_audit(kind="action", limit=5))
    assert result["found"] > 0
    assert all(e["kind"] == "action" for e in result["entries"])


def test_search_audit_filters_by_window(context):
    result = json.loads(inv.search_audit(minute_from=200, minute_to=260))
    if result["found"]:
        assert all(200 <= e["minute"] <= 260 for e in result["entries"])


def test_search_audit_reports_the_rule_on_refusals(context):
    result = json.loads(inv.search_audit(kind="decision", limit=200))
    rules = {e["rule"] for e in result["entries"] if e["rule"]}
    assert rules, "an investigator cannot explain a refusal without its rule"


def test_search_audit_says_so_when_nothing_matches(context):
    result = json.loads(inv.search_audit(kind="nonsense"))
    assert result["found"] == 0
    assert "note" in result


def test_slice_traffic_returns_per_minute_counts(context):
    result = json.loads(inv.slice_traffic("gw_beta|upi|hdfc", 180, 240))
    assert result["minutes"] > 0
    assert result["total_successes"] <= result["total_attempts"]
    row = result["per_minute"][0]
    assert {"minute", "at", "attempts", "successes", "success_rate"} <= set(row)


def test_key_health_spans_every_gateway(context):
    """The measurement that matters after a shift is the whole key."""
    result = json.loads(inv.key_health("upi", "hdfc", 180, 240))
    assert len(result["by_gateway"]) > 1
    total = sum(v["attempts"] for v in result["by_gateway"].values())
    assert total == result["attempts"]


def test_slices_active_orders_worst_first(context):
    result = json.loads(inv.slices_active(180, 240))
    rates = [r["success_rate"] for r in result["worst_first"]
             if r["success_rate"] is not None]
    assert rates == sorted(rates)


def test_a_tool_called_with_no_investigation_refuses():
    inv._CONTEXT = None
    with pytest.raises(RuntimeError, match="no investigation in progress"):
        inv.search_audit()


# -- read-only, structurally -------------------------------------------------

def test_no_tool_mutates_the_evidence(context):
    before_ledger = len(context.ledger)
    before_obs = len(context.observations)
    snapshot = [(o.minute, o.slice_key, o.attempts, o.successes)
                for o in context.observations[:50]]

    inv.search_audit(kind="action")
    inv.slice_traffic("gw_beta|upi|hdfc", 0, 1440)
    inv.key_health("upi", "hdfc", 0, 1440)
    inv.slices_active(0, 1440)

    assert len(context.ledger) == before_ledger
    assert len(context.observations) == before_obs
    assert snapshot == [(o.minute, o.slice_key, o.attempts, o.successes)
                        for o in context.observations[:50]]


def test_the_toolset_contains_nothing_that_writes():
    """A tool whose name suggests mutation should never appear here."""
    for tool in READ_ONLY_TOOLS:
        name = tool.__name__
        assert not any(verb in name for verb in
                       ("set", "write", "apply", "shift", "update", "delete",
                        "record", "execute")), f"{name} sounds like a mutation"


def test_the_investigator_cannot_see_the_incident_plan():
    """As blind as the detector. An investigator with the answer key is theatre."""
    source = (PKG / "investigator.py").read_text(encoding="utf-8")
    assert "from .scenarios" not in source
    assert "import scenarios" not in source


def test_no_decision_path_module_imports_the_investigator():
    for name in ("policy.py", "routing.py", "control_plane.py", "audit.py",
                 "rootcause.py", "world.py"):
        source = (PKG / name).read_text(encoding="utf-8")
        assert "investigator" not in source, (
            f"{name} imports the investigator; explanation must not be able "
            f"to reach a decision")


# -- degradation -------------------------------------------------------------

def test_it_falls_back_to_raw_evidence_without_a_model(evidence):
    agent = Investigator(evidence)
    agent.client = None
    result = agent.ask("why did traffic move?")
    assert result.used_model is False
    assert "ledger" in result.answer.lower()
    assert len(result.answer) > 80


def test_a_model_failure_never_propagates(evidence):
    class Boom:
        class beta:
            class messages:
                @staticmethod
                def tool_runner(**_):
                    raise RuntimeError("upstream is down")

    agent = Investigator(evidence)
    agent.client = Boom()
    result = agent.ask("why?")
    assert result.used_model is False
    assert "upstream is down" in (result.note or "")


def test_construction_never_raises_without_credentials():
    agent = Investigator(Evidence(ledger=AuditLedger()))
    assert isinstance(agent.available, bool)


def test_the_context_is_released_after_an_investigation(evidence):
    agent = Investigator(evidence)
    agent.client = None
    agent.ask("anything")
    assert inv._CONTEXT is None


def test_evidence_is_built_from_a_real_run(evidence):
    assert len(evidence.ledger) > 0
    assert len(evidence.observations) > 1000
    assert isinstance(evidence.observations[0], Observation)
