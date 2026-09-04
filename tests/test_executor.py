"""The execution path must be safe by default and honest about mode."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from revenueguard.audit import AuditLedger
from revenueguard.config import METHOD_TICKET
from revenueguard.executor import (DryRunExecutor, RazorpayTestExecutor,
                                   RecoveryIntent, intents_from_ledger)


def _intent(seq=1, amount=1499.50):
    return RecoveryIntent(audit_seq=seq, minute=120, subject="upi|hdfc",
                          source_gateway="gw_beta", target_gateway="gw_alpha",
                          amount_inr=amount, reason="moved 7.2% off gw_beta")


def test_amount_is_converted_to_paise_without_float_drift():
    assert _intent(amount=1499.50).amount_paise == 149950
    assert _intent(amount=0.01).amount_paise == 1
    assert _intent(amount=2150.0).amount_paise == 215000


def test_notes_carry_the_decision_context_out_of_this_repo():
    notes = _intent().notes()
    assert notes["revenueguard_audit_seq"] == "1"
    assert notes["revenueguard_from"] == "gw_beta"
    assert notes["revenueguard_to"] == "gw_alpha"
    # Razorpay caps note values; a long reason must not blow the request up.
    long_reason = _intent()
    long_reason.reason = "x" * 5000
    assert len(long_reason.notes()["revenueguard_reason"]) <= 240


def test_dry_run_sends_nothing_and_says_so():
    ex = DryRunExecutor()
    result = ex.execute(_intent())
    assert result.ok
    assert result.mode == "dry_run"
    assert result.order_id.startswith("order_DRYRUN")
    assert len(ex.sent) == 1
    assert ex.sent[0]["currency"] == "INR"


def test_live_keys_are_refused():
    """A live key must be rejected before any client is constructed."""
    pytest.importorskip("razorpay")
    with pytest.raises(RuntimeError, match="never touches live keys"):
        RazorpayTestExecutor(key_id="rzp_live_abc123", key_secret="secret")


def test_missing_credentials_are_refused():
    pytest.importorskip("razorpay")
    with pytest.raises(RuntimeError, match="RAZORPAY_KEY_ID"):
        RazorpayTestExecutor(key_id="", key_secret="")


def test_intents_are_built_only_from_executed_actions():
    led = AuditLedger()
    led.record(10, "detection", "gw_beta", "sr fell")
    led.record(10, "proposal", "upi|hdfc", "shift off gw_beta")
    led.record(10, "decision", "upi|hdfc", "blocked", rule="min_confidence",
               decision="block")
    led.record(11, "action", "upi|hdfc", "moved 7.2%",
               source="gw_beta", target="gw_alpha")

    intents = intents_from_ledger(list(led), METHOD_TICKET, limit=10)
    assert len(intents) == 1
    assert intents[0].audit_seq == 4
    assert intents[0].source_gateway == "gw_beta"
    assert intents[0].amount_inr == METHOD_TICKET["upi"]


def test_intent_limit_is_respected():
    led = AuditLedger()
    for n in range(20):
        led.record(n, "action", "card|sbi", "moved", source="a", target="b")
    assert len(intents_from_ledger(list(led), METHOD_TICKET, limit=5)) == 5
