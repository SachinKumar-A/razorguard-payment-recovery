"""Delivering a recommendation, and making sure only one instance produces them."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from razorguard.applier import (ChangeTracker, FileApplier, NullApplier,
                                Recommendation, WebhookApplier,
                                  build_from_env, recommendations_from)
from razorguard.config import ISSUERS, METHODS
from razorguard.persistence import Lease, Store
from razorguard.routing import RoutingTable


def rec(key="upi|hdfc", alpha=0.75, minute=10):
    method, issuer = key.split("|")
    return Recommendation(method=method, issuer=issuer,
                          weights={"gw_alpha": alpha, "gw_beta": 1 - alpha},
                          baseline={"gw_alpha": 0.5, "gw_beta": 0.5},
                          minute=minute, reason="degraded")


# -- shaping -----------------------------------------------------------------

def test_recommendations_cover_only_diverted_keys():
    table = RoutingTable.default(METHODS, ISSUERS)
    assert recommendations_from(table, 5) == []

    table.shift_away("upi", "hdfc", "gw_beta", "gw_alpha", 0.8)
    out = recommendations_from(table, 5)
    assert len(out) == 1
    assert out[0].key == "upi|hdfc"
    assert out[0].baseline != out[0].weights


def test_payload_states_the_mode_it_was_sent_under():
    assert rec().payload("notify")["mode"] == "notify"
    assert rec().payload("auto")["mode"] == "auto"
    assert rec().payload("auto")["source"] == "razorguard"


# -- only push what changed --------------------------------------------------

def test_unchanged_keys_are_not_pushed_again():
    """Sixty identical instructions an hour is how a consumer learns to ignore."""
    tracker = ChangeTracker()
    changed, skipped = tracker.changed([rec()])
    assert len(changed) == 1 and skipped == 0

    changed, skipped = tracker.changed([rec()])
    assert changed == [] and skipped == 1


def test_a_real_change_is_pushed():
    tracker = ChangeTracker()
    tracker.changed([rec(alpha=0.75)])
    changed, _ = tracker.changed([rec(alpha=0.90)])
    assert len(changed) == 1


def test_noise_below_the_epsilon_is_not_a_change():
    tracker = ChangeTracker()
    tracker.changed([rec(alpha=0.7500)])
    changed, skipped = tracker.changed([rec(alpha=0.7510)])
    assert changed == [] and skipped == 1


def test_forgetting_a_key_lets_it_be_pushed_again():
    tracker = ChangeTracker()
    tracker.changed([rec()])
    tracker.forget("upi|hdfc")
    changed, _ = tracker.changed([rec()])
    assert len(changed) == 1


# -- appliers ----------------------------------------------------------------

def test_null_applier_publishes_nothing():
    result = NullApplier().apply([rec(), rec("card|sbi")])
    assert result.delivered == 0
    assert result.skipped_unchanged == 2


def test_file_applier_writes_the_table_atomically(tmp_path):
    path = tmp_path / "nested" / "routing.json"
    applier = FileApplier(str(path))
    result = applier.apply([rec(), rec("card|sbi")])

    assert result.delivered == 2
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document["keys"]) == {"upi|hdfc", "card|sbi"}
    assert document["source"] == "razorguard"
    # The temporary file must not be left behind for a watcher to find.
    assert not (tmp_path / "nested" / "routing.json.tmp").exists()


def test_file_applier_writing_nothing_is_a_no_op(tmp_path):
    path = tmp_path / "routing.json"
    assert FileApplier(str(path)).apply([]).delivered == 0
    assert not path.exists()


def test_webhook_failures_are_counted_not_raised():
    """A dead consumer must never reach the control loop."""
    applier = WebhookApplier("http://127.0.0.1:9/none", timeout=0.2)
    result = applier.apply([rec(), rec("card|sbi")])
    assert result.delivered == 0
    assert result.failed == 2
    assert len(result.errors) == 2
    assert applier.last_error


# -- configuration -----------------------------------------------------------

def test_default_is_publish_only(monkeypatch):
    for var in ("RAZORGUARD_APPLY_MODE", "RAZORGUARD_APPLY_FILE",
                "RAZORGUARD_APPLY_WEBHOOK"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(build_from_env(), NullApplier)


def test_a_mode_without_a_destination_is_refused(monkeypatch):
    monkeypatch.setenv("RAZORGUARD_APPLY_MODE", "auto")
    for var in ("RAZORGUARD_APPLY_FILE", "RAZORGUARD_APPLY_WEBHOOK"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="needs somewhere to send"):
        build_from_env()


def test_file_destination_is_selected(monkeypatch, tmp_path):
    monkeypatch.setenv("RAZORGUARD_APPLY_MODE", "auto")
    monkeypatch.setenv("RAZORGUARD_APPLY_FILE", str(tmp_path / "r.json"))
    monkeypatch.delenv("RAZORGUARD_APPLY_WEBHOOK", raising=False)
    assert isinstance(build_from_env(), FileApplier)


# -- one active instance -----------------------------------------------------

def _leases(tmp_path, ttl=1.0):
    path = str(tmp_path / "s.db")
    return (Lease(Store(path), ttl_seconds=ttl, holder="node-a"),
            Lease(Store(path), ttl_seconds=ttl, holder="node-b"))


def test_only_one_instance_holds_the_lease(tmp_path):
    a, b = _leases(tmp_path)
    assert a.try_acquire() is True
    assert b.try_acquire() is False
    assert a.is_active and not b.is_active


def test_the_holder_can_renew_indefinitely(tmp_path):
    a, b = _leases(tmp_path)
    a.try_acquire()
    for _ in range(3):
        assert a.renew() is True
    assert b.try_acquire() is False


def test_a_standby_takes_over_when_the_holder_stops_renewing(tmp_path):
    a, b = _leases(tmp_path, ttl=0.5)
    a.try_acquire()
    time.sleep(0.7)
    assert b.try_acquire() is True


def test_a_stalled_holder_cannot_carry_on_acting(tmp_path):
    """The classic failure: stall past the lease, wake up, keep deciding."""
    a, b = _leases(tmp_path, ttl=0.5)
    a.try_acquire()
    time.sleep(0.7)
    b.try_acquire()

    assert a.renew() is False
    assert a.is_active is False


def test_a_clean_shutdown_hands_over_immediately(tmp_path):
    a, b = _leases(tmp_path, ttl=300.0)
    a.try_acquire()
    assert b.try_acquire() is False
    a.release()
    assert b.try_acquire() is True


def test_status_reports_who_holds_it(tmp_path):
    a, b = _leases(tmp_path)
    a.try_acquire()
    assert a.status()["active"] is True
    assert b.status()["active"] is False
    assert b.status()["holder"] == "node-a"
