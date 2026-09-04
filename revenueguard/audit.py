"""Append-only audit ledger.

Razorpay's bar for this track asks for an audit trail. The test of one is not
that it exists but that it answers, after the fact, *why did you move that
money* -- including for the actions that were refused. A ledger that records
only what happened hides exactly the decisions worth reviewing.

So every gate the policy engine applies is written here, blocks and escalations
included, with the specific rule that fired.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuditEvent:
    seq: int
    minute: int
    kind: str                       # detection | proposal | decision | action | rollback | restore
    subject: str                    # the slice or (method, issuer) key it concerns
    summary: str
    rule: Optional[str] = None      # policy rule that decided it, when applicable
    evidence: Dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class AuditLedger:
    def __init__(self) -> None:
        self._events: List[AuditEvent] = []

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def record(self, minute: int, kind: str, subject: str, summary: str,
               rule: Optional[str] = None, **evidence: Any) -> AuditEvent:
        ev = AuditEvent(seq=len(self._events) + 1, minute=minute, kind=kind,
                        subject=subject, summary=summary, rule=rule,
                        evidence=evidence)
        self._events.append(ev)
        return ev

    def of_kind(self, *kinds: str) -> List[AuditEvent]:
        return [e for e in self._events if e.kind in kinds]

    def for_subject(self, subject: str) -> List[AuditEvent]:
        return [e for e in self._events if e.subject == subject]

    def blocked_by_rule(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in self._events:
            if e.kind == "decision" and e.rule and e.evidence.get("decision") != "allow":
                out[e.rule] = out.get(e.rule, 0) + 1
        return out

    def write_jsonl(self, path: str) -> None:
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for e in self._events:
                fh.write(e.line() + "\n")

    def trace(self, minute_from: int, minute_to: int) -> List[AuditEvent]:
        return [e for e in self._events if minute_from <= e.minute <= minute_to]
