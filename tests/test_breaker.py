"""The system must notice when its own strategy stops working.

Every other guardrail bounds a single action. None can see that rerouting
itself is not helping - and a sensitivity sweep found fleets where it is not:
if every gateway is already past its capacity knee at rest, there is no spare
headroom to route into and shifting only concentrates load.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from revenueguard.capacity import CapacityModel
from revenueguard.config import WorldConfig
from revenueguard.control_plane import (EFFICACY_COOLDOWN_MIN,
                                        EFFICACY_MIN_SAMPLES,
                                        EFFICACY_TRIP_PP, ControlPlane,
                                        RunOutcome)
from revenueguard.detectors import default_detector
from revenueguard.ingest import BufferedSource
from revenueguard.policy import PolicyConfig, PolicyEngine
from revenueguard.scenarios import default_incident_plan
from revenueguard.simulator import Observation
from revenueguard.world import World


def plane():
    return ControlPlane(None, default_detector(),
                        policy=PolicyEngine(PolicyConfig()),
                        enable_routing=True,
                        source=BufferedSource(lambda m: 640.0))


def test_breaker_starts_closed():
    cp = plane()
    assert cp.breaker_open is False
    assert cp.breaker_trips == 0


def test_no_opinion_before_enough_evidence():
    """A handful of shifts is not grounds for condemning the strategy."""
    cp = plane()
    out = RunOutcome(minutes=0)
    for _ in range(EFFICACY_MIN_SAMPLES - 1):
        cp.efficacy.append(-0.05)
    cp._update_breaker(10, out)
    assert cp.breaker_open is False


def test_trips_once_shifts_are_measurably_harmful():
    cp = plane()
    out = RunOutcome(minutes=0)
    for _ in range(EFFICACY_MIN_SAMPLES):
        cp.efficacy.append(EFFICACY_TRIP_PP - 0.01)
    cp._update_breaker(10, out)

    assert cp.breaker_open is True
    assert cp.breaker_trips == 1
    assert out.escalated == 1
    tripped = [e for e in out.ledger if e.rule == "efficacy_breaker"]
    assert tripped and tripped[0].evidence["decision"] == "escalate"


def test_does_not_trip_when_shifts_are_helping():
    cp = plane()
    out = RunOutcome(minutes=0)
    for _ in range(EFFICACY_MIN_SAMPLES * 2):
        cp.efficacy.append(+0.02)
    cp._update_breaker(10, out)
    assert cp.breaker_open is False


def test_an_open_breaker_blocks_every_shift():
    cp = plane()
    out = RunOutcome(minutes=0)
    for _ in range(EFFICACY_MIN_SAMPLES):
        cp.efficacy.append(-0.10)
    cp._update_breaker(10, out)

    before = out.actions
    from revenueguard.detectors.base import Alarm
    cp._handle_alarms(11, [Alarm(minute=11, slice_key="gw_beta|upi|hdfc",
                                 observed_sr=0.4, baseline_sr=0.95,
                                 confidence=0.999)], out)
    assert out.actions == before
    assert out.blocked >= 1


def test_it_reopens_after_the_cooldown_with_a_clean_slate():
    """One bad spell must not condemn the strategy forever."""
    cp = plane()
    out = RunOutcome(minutes=0)
    for _ in range(EFFICACY_MIN_SAMPLES):
        cp.efficacy.append(-0.10)
    cp._update_breaker(100, out)
    assert cp.breaker_open is True

    cp._update_breaker(100 + EFFICACY_COOLDOWN_MIN - 1, out)
    assert cp.breaker_open is True

    cp._update_breaker(100 + EFFICACY_COOLDOWN_MIN, out)
    assert cp.breaker_open is False
    assert len(cp.efficacy) == 0
    assert any(e.rule == "efficacy_breaker_reset" for e in out.ledger)


# -- the measurement itself --------------------------------------------------

def test_efficacy_is_measured_across_every_gateway_for_the_key():
    """Moving traffic off a sick gateway trivially improves that gateway.

    The question is whether the customer got paid, which is a property of the
    key as a whole - so the measurement has to span all its gateways.
    """
    cp = plane()
    for minute in range(10):
        for key, successes in (("gw_alpha|upi|hdfc", 95),
                               ("gw_beta|upi|hdfc", 30)):
            cp.health.update(Observation(minute=minute, slice_key=key,
                                         method="upi", attempts=100,
                                         successes=successes,
                                         avg_ticket_inr=640.0))

    combined = cp.health.key_sr("upi", "hdfc", 0, 10)
    assert abs(combined - 0.625) < 1e-9      # (95+30) / 200

    # A different issuer is not folded in.
    assert cp.health.key_sr("upi", "sbi", 0, 10) is None


def test_a_shift_is_scored_only_after_it_settles():
    cp = plane()
    cp._pending_efficacy.append((50, "upi", "hdfc", 0.90))
    cp._settle_efficacy(40)
    assert len(cp.efficacy) == 0
    assert len(cp._pending_efficacy) == 1


def test_settling_with_no_traffic_discards_rather_than_guesses():
    cp = plane()
    cp._pending_efficacy.append((50, "upi", "hdfc", 0.90))
    cp._settle_efficacy(60)
    assert len(cp.efficacy) == 0          # no observations -> no verdict
    assert cp._pending_efficacy == []


# -- end to end --------------------------------------------------------------

def _run(curve: CapacityModel):
    world = World(WorldConfig(seed=7), default_incident_plan(2), capacity=curve)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(PolicyConfig()), enable_routing=True)
    return cp, cp.run(2880)


def test_it_stays_out_of_the_way_on_a_healthy_fleet():
    """A fleet with headroom must not be throttled by the safety mechanism."""
    cp, _ = _run(CapacityModel(knee=0.85, slope=0.15, headroom=2.00))
    assert cp.breaker_trips == 0


def test_it_engages_on_a_fleet_with_no_headroom():
    """Every gateway already past its knee: rerouting cannot help."""
    cp, out = _run(CapacityModel(knee=0.45, slope=0.90, headroom=1.15))
    assert cp.breaker_trips > 0
    assert out.blocked > 0
