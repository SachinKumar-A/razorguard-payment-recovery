"""Alerting must reach a human without ever being able to stall the loop."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from razorguard.alerts import Alert, Alerter, alerts_from_ledger
from razorguard.audit import AuditLedger


def alert(kind="escalation", subject="upi|hdfc", rule="no_healthy_destination"):
    return Alert(minute=5, kind=kind, subject=subject,
                 summary="every route degraded", rule=rule)


def test_no_webhook_configured_means_disabled(monkeypatch):
    monkeypatch.delenv("RAZORGUARD_ALERT_WEBHOOK", raising=False)
    a = Alerter()
    assert a.enabled is False
    assert a.send(alert()) is False
    assert a.stats()["enabled"] is False


def test_duplicates_inside_the_window_are_folded_not_repeated():
    """One wide outage escalates on many slices; that must not page twice."""
    a = Alerter(url="http://localhost:0/hook")
    a._thread = None                      # do not actually deliver
    assert a.send(alert()) is True
    assert a.send(alert()) is False
    assert a.send(alert()) is False
    assert a.suppressed == 2
    a.close()


def test_a_different_subject_is_not_suppressed():
    a = Alerter(url="http://localhost:0/hook")
    a._thread = None
    assert a.send(alert(subject="upi|hdfc")) is True
    assert a.send(alert(subject="card|sbi")) is True
    assert a.suppressed == 0
    a.close()


def test_suppression_expires():
    a = Alerter(url="http://localhost:0/hook", dedupe_seconds=0)
    a._thread = None
    assert a.send(alert()) is True
    assert a.send(alert()) is True
    assert a.suppressed == 0
    a.close()


def test_a_full_queue_drops_rather_than_blocking_the_loop():
    """Backpressure from an incident channel must never reach the router."""
    a = Alerter(url="http://localhost:0/hook", dedupe_seconds=0)
    a._thread = None                      # nothing drains the queue
    for n in range(400):
        a.send(alert(subject=f"upi|issuer{n}"))
    assert a.dropped_full > 0
    assert a.stats()["dropped_queue_full"] == a.dropped_full
    a.close()


def test_payload_carries_the_suppressed_count():
    payload = alert().payload(suppressed=4)
    assert payload["source"] == "razorguard"
    assert payload["suppressed_duplicates"] == 4
    assert payload["rule"] == "no_healthy_destination"


def test_payload_omits_the_count_when_nothing_was_suppressed():
    assert "suppressed_duplicates" not in alert().payload()


# -- picking what a human should see -----------------------------------------

def _ledger():
    led = AuditLedger()
    led.record(5, "detection", "gw_beta", "sr fell 20 points")
    led.record(5, "proposal", "upi|hdfc", "shift off gw_beta")
    led.record(5, "decision", "upi|hdfc", "allowed", rule=None,
               decision="allow")
    led.record(5, "action", "upi|hdfc", "moved 25%")
    led.record(5, "decision", "card|sbi", "every route degraded",
               rule="no_healthy_destination", decision="escalate")
    led.record(5, "rollback", "upi|axis", "destination fell to 71%",
               rule="rollback_target_degraded")
    led.record(6, "decision", "upi|yes", "later minute",
               rule="max_causes_per_hour", decision="escalate")
    return led


def test_only_escalations_and_rollbacks_page():
    """Paging on detections and allowed actions is how a channel gets muted."""
    picked = alerts_from_ledger(_ledger(), minute=5)
    assert {a.kind for a in picked} == {"escalation", "rollback"}
    assert {a.subject for a in picked} == {"card|sbi", "upi|axis"}


def test_only_the_current_minute_is_considered():
    assert all(a.minute == 5 for a in alerts_from_ledger(_ledger(), minute=5))
    later = alerts_from_ledger(_ledger(), minute=6)
    assert [a.subject for a in later] == ["upi|yes"]


def test_a_quiet_minute_pages_nobody():
    assert alerts_from_ledger(_ledger(), minute=99) == []
