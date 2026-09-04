"""The seam where real data enters must not corrupt what the detector sees."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from revenueguard.config import METHOD_TICKET, WorldConfig
from revenueguard.control_plane import ControlPlane
from revenueguard.detectors import default_detector
from revenueguard.ingest import BufferedSource, CsvSource, PaymentOutcome
from revenueguard.policy import PolicyConfig, PolicyEngine
from revenueguard.scenarios import default_incident_plan
from revenueguard.world import World


def ticket(method):
    return METHOD_TICKET.get(method, 1000.0)


def outcome(attempts=100, successes=90, gateway="gw_alpha", issuer="hdfc"):
    return PaymentOutcome(gateway=gateway, method="upi", issuer=issuer,
                          attempts=attempts, successes=successes)


# -- validation --------------------------------------------------------------

def test_successes_cannot_exceed_attempts():
    with pytest.raises(ValueError, match="exceeds attempts"):
        outcome(attempts=5, successes=9).validate()


def test_negative_counts_are_rejected():
    with pytest.raises(ValueError):
        outcome(attempts=-1, successes=0).validate()
    with pytest.raises(ValueError):
        outcome(attempts=10, successes=-1).validate()


def test_a_valid_outcome_becomes_a_well_formed_observation():
    obs = outcome().to_observation(42, ticket("upi"))
    assert obs.slice_key == "gw_alpha|upi|hdfc"
    assert obs.minute == 42
    assert obs.attempts == 100 and obs.successes == 90
    assert obs.avg_ticket_inr == METHOD_TICKET["upi"]


# -- buffering ---------------------------------------------------------------

def test_observations_are_released_for_their_own_minute():
    src = BufferedSource(ticket)
    src.push(outcome(), 5)
    src.push(outcome(issuer="icici"), 5)
    src.push(outcome(), 6)

    assert src.poll(4) == []
    assert len(src.poll(5)) == 2
    assert len(src.poll(6)) == 1


def test_late_arrivals_are_dropped_and_counted():
    """Folding a late outcome into the current minute would corrupt the
    baseline the detector compares against, so it is refused loudly."""
    src = BufferedSource(ticket)
    src.poll(10)                       # the loop has now processed minute 10
    assert src.push(outcome(), 9) is False
    assert src.push(outcome(), 10) is False
    assert src.dropped_late == 2
    assert src.accepted == 0


def test_pushing_ahead_of_the_loop_is_fine():
    src = BufferedSource(ticket)
    src.poll(10)
    assert src.push(outcome(), 11) is True
    assert src.buffered_minutes == 1


def test_polling_drains_so_a_minute_is_never_replayed():
    src = BufferedSource(ticket)
    src.push(outcome(), 3)
    assert len(src.poll(3)) == 1
    assert src.poll(3) == []


def test_push_many_reports_how_many_landed():
    src = BufferedSource(ticket)
    src.poll(5)
    assert src.push_many([outcome(), outcome()], 6) == 2
    assert src.push_many([outcome(), outcome()], 4) == 0


# -- csv replay --------------------------------------------------------------

def test_csv_source_reads_a_replay_file(tmp_path):
    p = tmp_path / "replay.csv"
    p.write_text(
        "minute,gateway,method,issuer,attempts,successes\n"
        "0,gw_alpha,upi,hdfc,100,95\n"
        "0,gw_beta,upi,hdfc,80,30\n"
        "1,gw_alpha,upi,hdfc,110,104\n", encoding="utf-8")

    src = CsvSource(str(p), ticket)
    assert len(src) == 3
    assert src.minutes == [0, 1]
    assert len(src.poll(0)) == 2
    assert src.poll(1)[0].successes == 104


def test_csv_source_rejects_an_impossible_row(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text(
        "minute,gateway,method,issuer,attempts,successes\n"
        "0,gw_alpha,upi,hdfc,10,50\n", encoding="utf-8")
    with pytest.raises(ValueError):
        CsvSource(str(p), ticket)


# -- the control plane reads whichever source it was given -------------------

def test_a_control_plane_needs_a_world_or_a_source():
    with pytest.raises(ValueError, match="either a World"):
        ControlPlane(None, default_detector())


def test_a_source_replaces_the_simulator_entirely():
    src = BufferedSource(ticket)
    for minute in range(3):
        src.push(outcome(), minute)

    cp = ControlPlane(None, default_detector(),
                      policy=PolicyEngine(PolicyConfig()),
                      enable_routing=True, source=src)
    out = cp.run(3)
    assert len(out.observations) == 3
    assert all(o.slice_key == "gw_alpha|upi|hdfc" for o in out.observations)


def test_the_simulator_path_still_works_without_a_source():
    world = World(WorldConfig(seed=7), default_incident_plan(1))
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(PolicyConfig()), enable_routing=True)
    assert len(cp.observe_minute(600)) > 0


def test_tick_and_run_walk_the_same_path():
    """A service driving `tick` must not diverge from the benchmarked `run`."""
    from revenueguard.control_plane import RunOutcome

    def build():
        return ControlPlane(World(WorldConfig(seed=7), default_incident_plan(1)),
                            default_detector(),
                            policy=PolicyEngine(PolicyConfig()),
                            enable_routing=True)

    via_run = build().run(120)

    cp = build()
    via_tick = RunOutcome(minutes=120)
    for t in range(120):
        cp.tick(t, via_tick)

    assert via_run.successes == via_tick.successes
    assert via_run.actions == via_tick.actions
    assert len(via_run.ledger) == len(via_tick.ledger)
