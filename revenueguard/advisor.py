"""Reasoning where the rulebook runs out.

The policy engine is deliberately a set of hard bounds, and hard bounds have a
shape: they say *no* precisely and they say nothing else. When one fires, the
system escalates with a single line - "every gateway serving this issuer is
degraded", "four distinct causes this hour already" - and stops. A human is left
holding a correct refusal and no next step.

That residual is where a model earns its keep, and it is a different job from
the one the policy engine does:

- **Choosing a destination** is arithmetic over two or three candidates with a
  confidence check. A model there would be slower, non-reproducible, and no
  more accurate. The audit ledger would stop being deterministic. Not worth it.
- **Deciding what to do when routing cannot help** is judgement. There is no
  rule to write, because the useful answer depends on the shape of the
  evidence: an issuer-side fault wants somebody to call the issuer and pause
  retries; a fleet with no headroom wants capacity, not cleverness; a burst of
  unrelated causes may just want the automation paused while someone looks.

So the advisor runs only on escalations, reads the same evidence a human would
(through the investigator's read-only tools), and returns a **recommendation
for a person** - never an instruction to the router.

The boundary is unchanged
-------------------------
Its output goes into the alert and the ledger as advice, attributed as advice.
No routing decision reads it, and a test asserts no decision-path module can
import this file. The worst a wrong recommendation does is waste an engineer's
next five minutes - the same cost as a wrong runbook entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .audit import AuditEvent
from .investigator import READ_ONLY_TOOLS, Evidence
from . import investigator as _inv

MODEL = "claude-opus-5"
MAX_ROUNDS = 8

#: The courses of action a human can actually take. Constrained on purpose:
#: an open-ended recommendation is hard to act on and impossible to audit.
ACTIONS = {
    "contact_issuer": "The fault looks issuer-side. Escalate to the issuing "
                      "bank and pause retries against them.",
    "add_capacity": "No destination has headroom. This needs capacity, not "
                    "routing.",
    "manual_reroute": "A shift is worth making that the policy would not "
                      "authorise on its own. State which key and which target.",
    "pause_automation": "Stop the router for now; conditions are outside what "
                        "it was designed for.",
    "monitor": "No action needed yet. Say what would change that.",
    "insufficient_evidence": "The data does not support a recommendation. Say "
                             "what is missing.",
}

SYSTEM = """You advise an on-call payments engineer when RevenueGuard's routing
policy has refused to act and escalated.

RevenueGuard shifts payment traffic between gateways when a slice degrades. Its
policy engine has hard bounds, and when one fires it refuses and escalates. It
is correct to refuse - your job is not to overrule it. Your job is to work out
what the human should do instead.

Investigate first, using the tools. Look at what actually happened: the ledger
entries around the escalation, the traffic on the affected slices, the health of
the key across every gateway serving it. Only then recommend.

Judgement you are expected to apply:
- If every gateway serving one issuer degraded together, that is the signature
  of an issuer-side fault. Rerouting cannot reach a healthy path.
- If every gateway looks loaded, the fleet has no headroom and the answer is
  capacity.
- If several unrelated causes fired at once, the automation may be reacting to
  something systemic and pausing it is reasonable.
- If the refusal was a cooldown or rate limit and conditions have since
  improved, a manual shift may be worth making.

Rules:
- Use only what the tools return. Never invent a number, a slice or a minute.
- Recommend exactly one action, by calling the recommend tool.
- Be specific. "Investigate further" is not a recommendation.
- An on-call engineer reads this at 3am. Two or three sentences of reasoning."""


@dataclass
class Recommendation:
    action: str
    reasoning: str
    confidence: str
    evidence_cited: List[str] = field(default_factory=list)
    tool_calls: int = 0
    used_model: bool = False
    note: Optional[str] = None

    @property
    def valid(self) -> bool:
        return self.action in ACTIONS

    def summary(self) -> str:
        return f"[{self.action}] {self.reasoning}"

    def as_evidence(self) -> Dict[str, object]:
        return {"advice_action": self.action,
                "advice_confidence": self.confidence,
                "advice_source": "claude" if self.used_model else "none",
                "advice_tool_calls": self.tool_calls}


# The model delivers its verdict by calling this, which keeps the output typed
# and auditable instead of free prose that has to be parsed back.
_VERDICT: Optional[Recommendation] = None


def recommend(action: str, reasoning: str, confidence: str,
              evidence_cited: str = "") -> str:
    """Record your recommendation. Call this exactly once, at the end.

    Args:
        action: One of contact_issuer, add_capacity, manual_reroute,
            pause_automation, monitor, insufficient_evidence.
        reasoning: Two or three sentences for an on-call engineer, citing the
            specific evidence that led you here.
        confidence: high, medium or low.
        evidence_cited: Comma-separated ledger sequence numbers or slice keys
            you relied on.
    """
    global _VERDICT
    cited = [x.strip() for x in evidence_cited.split(",") if x.strip()]
    _VERDICT = Recommendation(action=action.strip(), reasoning=reasoning.strip(),
                              confidence=confidence.strip().lower(),
                              evidence_cited=cited, used_model=True)
    if _VERDICT.action not in ACTIONS:
        return (f"'{action}' is not a valid action. Choose one of: "
                f"{', '.join(ACTIONS)}. Call recommend again.")
    return "Recommendation recorded."


class Advisor:
    """Advises on escalations. Optional, read-only, never in the routing path."""

    def __init__(self, evidence: Evidence, model: str = MODEL,
                 effort: str = "medium"):
        self.evidence = evidence
        self.model = model
        self.effort = effort
        self.client = None
        self.disabled_reason: Optional[str] = None
        self.consulted = 0
        self.failed = 0
        try:
            import anthropic
        except ImportError:
            self.disabled_reason = "the anthropic SDK is not installed"
            return
        try:
            self.client = anthropic.Anthropic(timeout=90.0, max_retries=1)
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self.client is not None

    def advise(self, escalation: AuditEvent) -> Recommendation:
        global _VERDICT
        if self.client is None:
            return self._fallback(escalation)

        prompt = (
            f"RevenueGuard escalated at minute {escalation.minute}.\n\n"
            f"Subject: {escalation.subject}\n"
            f"Rule that refused: {escalation.rule}\n"
            f"What it said: {escalation.summary}\n\n"
            f"Investigate and recommend what the engineer should do."
        )

        _VERDICT = None
        _inv._CONTEXT = self.evidence
        calls = 0
        try:
            from anthropic import beta_tool

            tools = [beta_tool(fn) for fn in READ_ONLY_TOOLS + [recommend]]
            runner = self.client.beta.messages.tool_runner(
                model=self.model, max_tokens=6000, system=SYSTEM,
                output_config={"effort": self.effort}, tools=tools,
                messages=[{"role": "user", "content": prompt}])
            for message in runner:
                for block in message.content:
                    if block.type == "tool_use":
                        calls += 1
                if _VERDICT is not None and _VERDICT.valid:
                    break
                if calls >= MAX_ROUNDS * 2:
                    break
        except Exception as exc:
            self.failed += 1
            return self._fallback(escalation, f"{type(exc).__name__}: {exc}")
        finally:
            _inv._CONTEXT = None

        if _VERDICT is None or not _VERDICT.valid:
            return self._fallback(escalation, "no valid recommendation returned")

        self.consulted += 1
        verdict = _VERDICT
        verdict.tool_calls = calls
        _VERDICT = None
        return verdict

    def _fallback(self, escalation: AuditEvent,
                  note: Optional[str] = None) -> Recommendation:
        """No model: map the rule to its standing runbook answer.

        Thinner than an investigation, and better than nothing - these are the
        answers a runbook would have given anyway, which is the floor the model
        has to beat to be worth calling.
        """
        standing = {
            "no_healthy_destination": (
                "contact_issuer",
                "Every gateway serving this key degraded together, which is the "
                "signature of an issuer-side fault rather than an acquirer one. "
                "Rerouting cannot reach a healthy path."),
            "target_alarmed": (
                "monitor",
                "The only candidate destination is itself degraded. Watch for a "
                "healthy target to appear before intervening."),
            "max_causes_per_hour": (
                "pause_automation",
                "Several distinct causes fired within the hour. Something "
                "systemic may be happening; look before letting the router "
                "keep reacting."),
            "efficacy_breaker": (
                "add_capacity",
                "Recent shifts measurably failed to improve success rates, "
                "which is what a fleet with no spare headroom looks like."),
        }
        action, reasoning = standing.get(
            escalation.rule or "",
            ("insufficient_evidence",
             "No standing guidance for this rule; read the ledger around this "
             "minute."))
        return Recommendation(action=action, reasoning=reasoning,
                              confidence="low", used_model=False,
                              note=note or self.disabled_reason)


def escalations_in(ledger, minute: Optional[int] = None) -> List[AuditEvent]:
    """The events worth advising on - refusals, not successes."""
    out = []
    for e in ledger:
        if minute is not None and e.minute != minute:
            continue
        if e.kind == "decision" and e.evidence.get("decision") == "escalate":
            out.append(e)
    return out
