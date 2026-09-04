"""Executing a recovery decision against Razorpay test mode.

What this can and cannot honestly claim
---------------------------------------
Razorpay's public API does not expose gateway routing weights to a third party.
Choosing which acquirer a payment traverses is Razorpay's own product
(Optimizer), not an endpoint a merchant integration can call. Any project that
claims to "reroute traffic through the Razorpay API" is describing something
that does not exist.

So the routing half of RevenueGuard stays inside the simulation, where it is
measured properly, and the executor demonstrates the half that *is* real: when
the control plane decides a failed payment is worth re-attempting, it creates a
genuine test-mode Order carrying the full decision context in `notes`, and
optionally a Payment Link for the customer-facing recovery path.

That is a real integration doing a real thing, and it is described as exactly
that. The measurements in `experiment.py` do not depend on it.

Safety
------
Dry run is the default and requires no credentials. Live calls require both
environment variables *and* an explicit `--live` flag; test-mode keys
(`rzp_test_...`) are enforced, and a live key is refused outright.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .audit import AuditEvent


@dataclass
class RecoveryIntent:
    """One re-attempt the control plane thinks is worth making."""
    audit_seq: int
    minute: int
    subject: str            # method|issuer
    source_gateway: str
    target_gateway: str
    amount_inr: float
    reason: str

    def notes(self) -> Dict[str, str]:
        """Decision context, carried on the order itself.

        The point of putting it here rather than only in our own ledger is that
        it survives outside our system: anyone looking at this order in the
        Razorpay dashboard can see which incident produced it and why.
        """
        return {
            "revenueguard_audit_seq": str(self.audit_seq),
            "revenueguard_minute": str(self.minute),
            "revenueguard_subject": self.subject,
            "revenueguard_from": self.source_gateway,
            "revenueguard_to": self.target_gateway,
            "revenueguard_reason": self.reason[:240],
        }

    @property
    def amount_paise(self) -> int:
        return int(round(self.amount_inr * 100))


@dataclass
class ExecutionResult:
    intent: RecoveryIntent
    ok: bool
    mode: str                       # dry_run | test
    order_id: Optional[str] = None
    payment_link: Optional[str] = None
    error: Optional[str] = None

    def line(self) -> str:
        status = "ok " if self.ok else "ERR"
        ref = self.order_id or self.error or "-"
        return (f"  [{status}] {self.mode:<7} seq={self.intent.audit_seq:<5} "
                f"{self.intent.subject:<22} Rs {self.intent.amount_inr:>9,.2f}  {ref}")


class DryRunExecutor:
    """Builds every request and sends none of them."""

    mode = "dry_run"

    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    def execute(self, intent: RecoveryIntent) -> ExecutionResult:
        payload = {
            "amount": intent.amount_paise,
            "currency": "INR",
            "receipt": f"rg-{intent.audit_seq}",
            "notes": intent.notes(),
        }
        self.sent.append(payload)
        return ExecutionResult(intent, ok=True, mode=self.mode,
                               order_id=f"order_DRYRUN{intent.audit_seq:06d}")


class RazorpayTestExecutor:
    """Creates real orders in Razorpay test mode.

    Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET. A key that does not begin
    `rzp_test_` is refused: nothing in this project has any business touching a
    live merchant account.
    """

    mode = "test"

    def __init__(self, key_id: Optional[str] = None,
                 key_secret: Optional[str] = None,
                 create_payment_link: bool = False):
        try:
            import razorpay  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "the razorpay SDK is not installed; `pip install razorpay` or "
                "run without --live to use the dry-run executor") from exc
        import razorpay

        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
        if not self.key_id or not secret:
            raise RuntimeError(
                "set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to run live")
        if not self.key_id.startswith("rzp_test_"):
            raise RuntimeError(
                f"refusing to run against a key that is not test mode "
                f"({self.key_id[:12]}...); this tool never touches live keys")

        self.client = razorpay.Client(auth=(self.key_id, secret))
        self.create_payment_link = create_payment_link

    def execute(self, intent: RecoveryIntent) -> ExecutionResult:
        try:
            order = self.client.order.create({
                "amount": intent.amount_paise,
                "currency": "INR",
                "receipt": f"rg-{intent.audit_seq}",
                "notes": intent.notes(),
            })
        except Exception as exc:  # pragma: no cover - network dependent
            return ExecutionResult(intent, ok=False, mode=self.mode,
                                   error=f"{type(exc).__name__}: {exc}")

        link = None
        if self.create_payment_link:
            try:
                created = self.client.payment_link.create({
                    "amount": intent.amount_paise,
                    "currency": "INR",
                    "description": f"Recovery re-attempt {intent.subject}",
                    "notes": intent.notes(),
                })
                link = created.get("short_url")
            except Exception as exc:  # pragma: no cover - network dependent
                return ExecutionResult(intent, ok=True, mode=self.mode,
                                       order_id=order.get("id"),
                                       error=f"link failed: {exc}")

        return ExecutionResult(intent, ok=True, mode=self.mode,
                               order_id=order.get("id"), payment_link=link)


def intents_from_ledger(events: List[AuditEvent], avg_ticket: Dict[str, float],
                        limit: int = 10) -> List[RecoveryIntent]:
    """Turn executed routing actions into concrete re-attempts.

    One intent per action, priced at the average ticket for that method. This is
    a demonstration of the execution path, not a claim that every diverted
    payment becomes an order.
    """
    out: List[RecoveryIntent] = []
    for ev in events:
        if ev.kind != "action":
            continue
        method = ev.subject.split("|")[0]
        out.append(RecoveryIntent(
            audit_seq=ev.seq,
            minute=ev.minute,
            subject=ev.subject,
            source_gateway=str(ev.evidence.get("source", "?")),
            target_gateway=str(ev.evidence.get("target", "?")),
            amount_inr=avg_ticket.get(method, 1000.0),
            reason=ev.summary,
        ))
        if len(out) >= limit:
            break
    return out
