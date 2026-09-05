"""An agent that investigates the control plane's own decisions.

The question an on-call engineer actually asks at 03:12 is not "what is the
success rate" - it is *"why did this thing move my traffic, and was it right?"*
Answering that today means reading a thousand-row ledger, cross-referencing the
observation stream, and holding several windows in your head. It is exactly the
shape of work a tool-using model is good at and a dashboard is not.

So this is a genuine agent: it decides which questions to ask of the data,
issues them as tool calls, reads the results, and asks follow-ups until it can
answer. Multi-step, model-driven, not a fixed pipeline with a language model
stapled on the end.

Where it sits, and why that is not a contradiction
--------------------------------------------------
Everything else in this project keeps the model out of the decision path, and
that has not changed. The distinction is direction of travel:

- The control plane *decides*. It is deterministic, and a test asserts no
  decision-path module can even import a model.
- The investigator *explains, after the fact*. Every tool it has is read-only.
  There is no code path from an answer here back into routing, policy, or the
  ledger. The worst a wrong answer can do is mislead a human who then reads the
  ledger themselves - which is the same risk a wrong dashboard carries.

It is also **as blind as the detector was**. It cannot see `scenarios.py`, so it
does not know what the injected incident was. It reasons from exactly the
evidence the system had at the time, which is what makes its answer worth
anything: an investigator with access to the answer key would be theatre.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .audit import AuditLedger
from .simulator import Observation

MODEL = "claude-opus-5"
MAX_TOOL_ROUNDS = 12

SYSTEM = """You investigate decisions made by RazorGuard, a payment routing
control plane, on behalf of an on-call engineer.

The system monitors payment success rates per slice - one gateway, one payment
method, one issuing bank - and when a slice degrades it may shift traffic to a
healthier gateway under an explicit policy. Every decision, including refusals,
is written to an audit ledger.

Your job is to answer the engineer's question from the evidence, using the
tools. Work like an investigator:

- Start by finding the relevant ledger entries. Do not guess at minutes.
- Pull the underlying traffic to check whether the decision was justified by
  what the system could see at the time.
- If a decision was refused, the rule that refused it is in the ledger. Report
  the rule.
- Follow up on what you find. One tool call is rarely enough.

Rules:
- Use only what the tools return. Never invent a number, a slice, or a minute.
- If the evidence does not answer the question, say so and say what is missing.
- Distinguish what the system observed from what you infer.
- Be concise. An on-call engineer is reading this during an incident.
- Plain prose, no markdown headers."""


# The tools need data. A module-level context is the simplest thing that works
# with schema-generating decorators, and it is set once per investigation.
_CONTEXT: Optional["Evidence"] = None


@dataclass
class Evidence:
    """Everything the investigator is allowed to see. Read-only by construction."""
    ledger: AuditLedger
    observations: List[Observation] = field(default_factory=list)

    def by_minute(self) -> Dict[int, List[Observation]]:
        out: Dict[int, List[Observation]] = {}
        for o in self.observations:
            out.setdefault(o.minute, []).append(o)
        return out


def _ctx() -> "Evidence":
    if _CONTEXT is None:
        raise RuntimeError("no investigation in progress")
    return _CONTEXT


def _clock(minute: int) -> str:
    return f"d{minute // 1440 + 1} {(minute // 60) % 24:02d}:{minute % 60:02d}"


# ----------------------------------------------------------------- the tools
# Every one of these reads. None of them writes. That is the whole safety
# argument, and it is checked by a test rather than promised here.

def search_audit(kind: str = "", subject: str = "", minute_from: int = 0,
                 minute_to: int = 10 ** 9, limit: int = 40) -> str:
    """Search the decision ledger.

    Args:
        kind: One of detection, proposal, decision, action, rollback, restore.
            Empty for all kinds.
        subject: Filter by subject, e.g. "upi|hdfc" or a gateway name. Empty
            for all subjects. Matched as a substring.
        minute_from: Earliest minute to include.
        minute_to: Latest minute to include.
        limit: Maximum entries to return, newest last.
    """
    rows = []
    for e in _ctx().ledger:
        if kind and e.kind != kind:
            continue
        if subject and subject not in e.subject:
            continue
        if not (minute_from <= e.minute <= minute_to):
            continue
        rows.append({"seq": e.seq, "minute": e.minute, "at": _clock(e.minute),
                     "kind": e.kind, "subject": e.subject, "rule": e.rule,
                     "summary": e.summary})
    if not rows:
        return json.dumps({"found": 0, "note": "no ledger entries match"})
    return json.dumps({"found": len(rows), "entries": rows[-limit:]}, default=str)


def slice_traffic(slice_key: str, minute_from: int, minute_to: int) -> str:
    """Per-minute attempts and successes for one slice.

    Args:
        slice_key: Full slice, formatted "gateway|method|issuer",
            e.g. "gw_beta|upi|hdfc".
        minute_from: First minute, inclusive.
        minute_to: Last minute, inclusive.
    """
    rows = [{"minute": o.minute, "at": _clock(o.minute), "attempts": o.attempts,
             "successes": o.successes,
             "success_rate": round(o.successes / o.attempts, 4) if o.attempts else None}
            for o in _ctx().observations
            if o.slice_key == slice_key and minute_from <= o.minute <= minute_to]
    if not rows:
        return json.dumps({"found": 0,
                           "note": f"no traffic recorded for {slice_key} in that window"})
    total_a = sum(r["attempts"] for r in rows)
    total_s = sum(r["successes"] for r in rows)
    return json.dumps({
        "slice": slice_key, "minutes": len(rows),
        "total_attempts": total_a, "total_successes": total_s,
        "window_success_rate": round(total_s / total_a, 4) if total_a else None,
        "per_minute": rows[:120],
    })


def key_health(method: str, issuer: str, minute_from: int,
               minute_to: int) -> str:
    """Success rate for one (method, issuer) across every gateway serving it.

    Use this to check whether a shift actually helped: moving traffic off a
    sick gateway always improves that gateway's own numbers, so the question
    is whether the key as a whole did better.

    Args:
        method: upi, card or netbanking.
        issuer: Issuing bank, e.g. hdfc.
        minute_from: First minute, inclusive.
        minute_to: Last minute, inclusive.
    """
    per_gateway: Dict[str, List[int]] = {}
    for o in _ctx().observations:
        gw, m, i = o.slice_key.split("|")
        if m != method or i != issuer:
            continue
        if not (minute_from <= o.minute <= minute_to):
            continue
        bucket = per_gateway.setdefault(gw, [0, 0])
        bucket[0] += o.attempts
        bucket[1] += o.successes

    if not per_gateway:
        return json.dumps({"found": 0, "note": "no traffic for that key"})

    attempts = sum(v[0] for v in per_gateway.values())
    successes = sum(v[1] for v in per_gateway.values())
    return json.dumps({
        "key": f"{method}|{issuer}",
        "window": [minute_from, minute_to],
        "combined_success_rate": round(successes / attempts, 4) if attempts else None,
        "attempts": attempts,
        "by_gateway": {g: {"attempts": v[0], "successes": v[1],
                           "success_rate": round(v[1] / v[0], 4) if v[0] else None}
                       for g, v in sorted(per_gateway.items())},
    })


def slices_active(minute_from: int, minute_to: int, gateway: str = "") -> str:
    """Which slices carried traffic in a window, and how they did.

    Use this when you do not yet know which slice to look at.

    Args:
        minute_from: First minute, inclusive.
        minute_to: Last minute, inclusive.
        gateway: Restrict to one gateway. Empty for all.
    """
    agg: Dict[str, List[int]] = {}
    for o in _ctx().observations:
        if not (minute_from <= o.minute <= minute_to):
            continue
        if gateway and not o.slice_key.startswith(f"{gateway}|"):
            continue
        bucket = agg.setdefault(o.slice_key, [0, 0])
        bucket[0] += o.attempts
        bucket[1] += o.successes

    rows = [{"slice": k, "attempts": v[0], "successes": v[1],
             "success_rate": round(v[1] / v[0], 4) if v[0] else None}
            for k, v in agg.items()]
    rows.sort(key=lambda r: (r["success_rate"] is None, r["success_rate"]))
    return json.dumps({"found": len(rows), "worst_first": rows[:40]})


READ_ONLY_TOOLS = [search_audit, slice_traffic, key_health, slices_active]


# ------------------------------------------------------------- the investigator

@dataclass
class Investigation:
    question: str
    answer: str
    tool_calls: List[str] = field(default_factory=list)
    rounds: int = 0
    used_model: bool = False
    note: Optional[str] = None


class Investigator:
    """Answers questions about the ledger. Read-only, post-hoc, optional."""

    def __init__(self, evidence: Evidence, model: str = MODEL,
                 effort: str = "medium"):
        self.evidence = evidence
        self.model = model
        self.effort = effort
        self.client = None
        self.disabled_reason: Optional[str] = None
        try:
            import anthropic
        except ImportError:
            self.disabled_reason = ("the anthropic SDK is not installed "
                                    "(pip install anthropic)")
            return
        try:
            self.client = anthropic.Anthropic(timeout=120.0, max_retries=1)
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self.client is not None

    def ask(self, question: str) -> Investigation:
        global _CONTEXT
        if self.client is None:
            return self._offline(question)

        _CONTEXT = self.evidence
        calls: List[str] = []
        rounds = 0
        answer_parts: List[str] = []
        try:
            from anthropic import beta_tool

            tools = [beta_tool(fn) for fn in READ_ONLY_TOOLS]
            runner = self.client.beta.messages.tool_runner(
                model=self.model,
                max_tokens=8000,
                system=SYSTEM,
                output_config={"effort": self.effort},
                tools=tools,
                messages=[{"role": "user", "content": question}],
            )
            for message in runner:
                rounds += 1
                for block in message.content:
                    if block.type == "tool_use":
                        calls.append(f"{block.name}({json.dumps(block.input)})")
                    elif block.type == "text" and block.text.strip():
                        answer_parts = [block.text.strip()]
                if rounds >= MAX_TOOL_ROUNDS:
                    break
        except Exception as exc:
            # An investigation is a convenience. It never fails an incident.
            return self._offline(question,
                                 note=f"{type(exc).__name__}: {exc}")
        finally:
            _CONTEXT = None

        if not answer_parts:
            return self._offline(question, note="model returned no text")
        return Investigation(question=question, answer=answer_parts[-1],
                             tool_calls=calls, rounds=rounds, used_model=True)

    def _offline(self, question: str,
                 note: Optional[str] = None) -> Investigation:
        """No model: hand back the raw evidence rather than nothing.

        Less useful than an investigation, but an engineer with the relevant
        ledger window in front of them is strictly better off than one with an
        error message.
        """
        entries = [e for e in self.evidence.ledger
                   if e.kind in ("detection", "action", "rollback")]
        lines = [f"  {_clock(e.minute)}  {e.kind:<9} {e.summary}"
                 for e in entries[-15:]]
        body = ("No model available, so this is the raw ledger rather than an "
                "investigation. The most recent decisions were:\n"
                + "\n".join(lines))
        return Investigation(question=question, answer=body, used_model=False,
                             note=note or self.disabled_reason)


def evidence_from_run(outcome) -> Evidence:
    """Build the investigator's view from a completed run."""
    return Evidence(ledger=outcome.ledger, observations=outcome.observations)
