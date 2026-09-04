"""A restart must not be a blind spot."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from revenueguard.audit import AuditLedger
from revenueguard.config import ISSUERS, METHODS
from revenueguard.control_plane import ControlPlane, RunOutcome
from revenueguard.detectors import default_detector
from revenueguard.ingest import BufferedSource
from revenueguard.persistence import Store, checkpoint
from revenueguard.policy import PolicyConfig, PolicyEngine
from revenueguard.routing import RoutingTable
from revenueguard.simulator import Observation


def obs(minute, key="gw_alpha|upi|hdfc", attempts=100, successes=90):
    return Observation(minute=minute, slice_key=key, method="upi",
                       attempts=attempts, successes=successes,
                       avg_ticket_inr=640.0)


def store(tmp_path, **kw):
    return Store(str(tmp_path / "s.db"), **kw)


# -- observations ------------------------------------------------------------

def test_observations_round_trip(tmp_path):
    s = store(tmp_path)
    s.save_observations([obs(1), obs(2)])
    s.commit()
    back = s.recent_observations(100)
    assert len(back) == 2
    assert {o.minute for o in back} == {1, 2}
    assert back[0].avg_ticket_inr == 640.0


def test_rewriting_a_minute_is_idempotent(tmp_path):
    """A producer resending a minute must not duplicate the slice."""
    s = store(tmp_path)
    s.save_observations([obs(5, successes=90)])
    s.save_observations([obs(5, successes=95)])
    s.commit()
    back = s.recent_observations(100)
    assert len(back) == 1
    assert back[0].successes == 95


def test_prune_keeps_only_what_baselines_need(tmp_path):
    s = store(tmp_path)
    s.save_observations([obs(m) for m in range(100)])
    s.commit()
    s.prune(before_minute=60)
    s.commit()
    kept = s.recent_observations(1000)
    assert min(o.minute for o in kept) == 60
    assert len(kept) == 40


# -- audit -------------------------------------------------------------------

def test_audit_round_trip_preserves_sequence(tmp_path):
    s = store(tmp_path)
    led = AuditLedger()
    led.record(1, "detection", "gw_beta", "sr fell")
    led.record(2, "action", "upi|hdfc", "moved 25%", source="gw_beta")
    s.save_audit(list(led))
    s.commit()

    assert s.max_audit_seq == 2
    events = s.audit_tail(10)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["evidence"]["source"] == "gw_beta"


def test_audit_is_append_only_across_saves(tmp_path):
    s = store(tmp_path)
    led = AuditLedger()
    led.record(1, "detection", "x", "first")
    s.save_audit(list(led))
    led.record(2, "detection", "x", "second")
    s.save_audit(list(led))       # re-saves seq 1 too
    s.commit()
    assert s.max_audit_seq == 2
    assert len(s.audit_tail(50)) == 2


def test_ledger_sequence_continues_after_restart():
    """Restarting seq at 1 would collide with everything already written."""
    first = AuditLedger()
    first.record(1, "detection", "x", "a")
    first.record(2, "detection", "x", "b")

    resumed = AuditLedger(start_seq=2)
    event = resumed.record(3, "detection", "x", "c")
    assert event.seq == 3


def test_audit_tail_can_filter_by_kind(tmp_path):
    s = store(tmp_path)
    led = AuditLedger()
    led.record(1, "detection", "x", "d")
    led.record(1, "action", "y", "a")
    s.save_audit(list(led))
    s.commit()
    assert [e["kind"] for e in s.audit_tail(10, kind="action")] == ["action"]


# -- routing and diversions --------------------------------------------------

def test_routing_round_trip(tmp_path):
    s = store(tmp_path)
    table = RoutingTable.default(METHODS, ISSUERS)
    table.shift_away("upi", "hdfc", "gw_beta", "gw_alpha", 0.8)
    diverted = dict(table.weights("upi", "hdfc"))
    s.save_routing(table)
    s.commit()

    fresh = RoutingTable.default(METHODS, ISSUERS)
    assert fresh.at_baseline("upi", "hdfc")
    s.load_routing_into(fresh)
    for gateway, weight in diverted.items():
        assert abs(fresh.weights("upi", "hdfc")[gateway] - weight) < 1e-9


def test_diversions_round_trip_so_something_still_watches_them(tmp_path):
    """Restored weights with no supervisor is worse than either extreme."""
    s = store(tmp_path)
    src = BufferedSource(lambda m: 640.0)
    plane = ControlPlane(None, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True, source=src)
    from revenueguard.control_plane import Diversion
    plane.diversions[("upi", "hdfc")] = Diversion(
        method="upi", issuer="hdfc", source="gw_beta", target="gw_alpha",
        opened_min=42, shifted=0.256, healthy_streak=3, restoring=True)
    s.save_diversions(plane.diversions)
    s.commit()

    fresh = ControlPlane(None, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True, source=src)
    assert fresh.restore_diversions(s.load_diversions()) == 1
    d = fresh.diversions[("upi", "hdfc")]
    assert (d.source, d.target, d.opened_min) == ("gw_beta", "gw_alpha", 42)
    assert d.healthy_streak == 3 and d.restoring is True


def test_saving_diversions_replaces_rather_than_accumulates(tmp_path):
    s = store(tmp_path)
    from revenueguard.control_plane import Diversion
    one = {("upi", "hdfc"): Diversion("upi", "hdfc", "gw_beta", "gw_alpha",
                                      1, 0.1)}
    s.save_diversions(one)
    s.save_diversions({})
    s.commit()
    assert s.load_diversions() == []


# -- warm replay -------------------------------------------------------------

def test_warm_rebuilds_state_without_acting():
    """Replay must not re-propose actions that were already taken."""
    src = BufferedSource(lambda m: 640.0)
    plane = ControlPlane(None, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True, source=src)
    history = [obs(m, attempts=300, successes=280) for m in range(200)]
    assert plane.warm(history) == 200

    # State was rebuilt...
    assert plane.health.baseline_sr("gw_alpha|upi|hdfc") is not None
    # ...but nothing was decided or moved.
    for key in plane.routing.current:
        assert plane.routing.at_baseline(*key)
    assert plane.diversions == {}


def test_checkpoint_writes_everything_needed_to_resume(tmp_path):
    s = store(tmp_path)
    src = BufferedSource(lambda m: 640.0)
    plane = ControlPlane(None, default_detector(),
                         policy=PolicyEngine(PolicyConfig()),
                         enable_routing=True, source=src)
    out = RunOutcome(minutes=0)
    out.observations.append(obs(7))
    out.ledger.record(7, "detection", "gw_beta", "sr fell")

    checkpoint(s, plane, out, minute=7)

    assert s.minute == 7
    assert len(s.recent_observations(100)) == 1
    assert s.max_audit_seq == 1


def test_a_fresh_store_starts_at_minute_zero(tmp_path):
    assert store(tmp_path).minute == 0
