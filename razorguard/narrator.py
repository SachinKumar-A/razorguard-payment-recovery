"""Turning a finished attribution into an operator-readable note.

Where the LLM sits, and why it sits there
-----------------------------------------
It is handed an `Attribution` that has already been computed - the dimension,
the coverage, the lift, the slice counts - and asked to write those facts as
English. It never sees raw traffic, is never asked what caused the incident, and
its output is never read by anything that makes a decision. The return value of
every function here is a string that is displayed and then discarded.

That is a structural guarantee, not a promise: `narrate()` takes a `RootCause`
and returns `str`. There is no path from this module into the policy engine, the
router, or the audit ledger. The ledger deliberately keeps the deterministic
template, because a ledger whose text changes when you re-read it is not a
ledger.

Three failure modes, all handled the same way
---------------------------------------------
No API key, a network error, a refusal. In every case the deterministic template
is returned and the operator sees a slightly plainer sentence. Nothing retries,
nothing blocks, and an incident is never delayed because a narration call was
slow -- the timeout is deliberately short.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .rootcause import RootCause

MODEL = "claude-opus-5"

SYSTEM = """You write one-paragraph incident notes for payment operations engineers.

You are given facts that have already been computed by a deterministic
attribution engine. Your job is to state them in plain English for an on-call
engineer who has ten seconds to read.

Rules:
- Use only the facts given. Never infer a cause that is not in them.
- If the facts say the evidence is too thin to narrow the cause, say that
  plainly rather than guessing.
- No preamble, no bullet points, no headings. Two or three sentences.
- Do not recommend an action. Something else decides that.
- Plain prose. No markdown."""


class Narrator(Protocol):
    def narrate(self, cause: RootCause, baseline_sr: float,
                observed_sr: float) -> str: ...


class TemplateNarrator:
    """The deterministic default. Always available, never fails."""

    name = "template"

    def narrate(self, cause: RootCause, baseline_sr: float,
                observed_sr: float) -> str:
        return cause.narrate(baseline_sr, observed_sr)


@dataclass
class _Facts:
    drop_pp: float
    baseline_sr: float
    observed_sr: float
    slices: int
    primary: Optional[str]
    primary_coverage: Optional[float]
    primary_lift: Optional[float]
    secondary: Optional[str]
    secondary_trustworthy: bool

    def as_prompt(self) -> str:
        lines = [
            f"Success rate fell {self.drop_pp:.1f} percentage points, from "
            f"{self.baseline_sr:.1%} to {self.observed_sr:.1%}.",
            f"{self.slices} monitored slices are alarming.",
        ]
        if self.primary is None:
            lines.append("No dimension explains the alarming slices; the "
                         "incident is unattributed.")
        else:
            lines.append(
                f"Primary common factor: {self.primary}, covering "
                f"{self.primary_coverage:.0%} of alarming slices at "
                f"{self.primary_lift:.1f}x its share of the fleet.")
            if self.secondary and self.secondary_trustworthy:
                lines.append(f"Every alarming slice also shares {self.secondary}, "
                             f"so the fault is localised rather than affecting "
                             f"the whole {self.primary.split('=')[0]}.")
            elif self.secondary:
                lines.append("Too few slices have alarmed to narrow the cause "
                             "any further than the primary factor. Say so.")
        return "\n".join(lines)


def _facts(cause: RootCause, baseline_sr: float, observed_sr: float) -> _Facts:
    p, s = cause.primary, cause.secondary
    return _Facts(
        drop_pp=(baseline_sr - observed_sr) * 100.0,
        baseline_sr=baseline_sr,
        observed_sr=observed_sr,
        slices=len(cause.alarming_slices),
        primary=(f"{p.dimension}={p.value}" if p else None),
        primary_coverage=(p.coverage if p else None),
        primary_lift=(p.lift if p else None),
        secondary=(f"{s.dimension}={s.value}" if s else None),
        secondary_trustworthy=bool(s and s.confident_secondary),
    )


class ClaudeNarrator:
    """Optional LLM narration, with the template as an unconditional fallback.

    Construction never raises: if the SDK is missing or no credential resolves,
    the instance is created in a disabled state and every call falls through to
    the template. That keeps `--narrate claude` safe to leave in a demo script
    on a machine with no key.
    """

    name = "claude"

    def __init__(self, model: str = MODEL, timeout: float = 8.0,
                 effort: str = "low"):
        self.model = model
        self.effort = effort
        self.fallback = TemplateNarrator()
        self.client = None
        self.disabled_reason: Optional[str] = None
        self.calls = 0
        self.fallbacks = 0

        try:
            import anthropic
        except ImportError:
            self.disabled_reason = ("the anthropic SDK is not installed "
                                    "(pip install anthropic)")
            return
        # An unset ANTHROPIC_API_KEY does not mean there is no credential -- the
        # SDK also resolves ANTHROPIC_AUTH_TOKEN and `ant auth login` profiles,
        # so construct the client and let it decide.
        try:
            self.client = anthropic.Anthropic(timeout=timeout, max_retries=1)
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self.client is not None

    def _disable(self, reason: str) -> None:
        """Stop calling out after the first failure.

        Without this, a missing key costs one failed round trip per alarming
        slice - hundreds of them over a run - to produce text nobody reads.
        The first failure is enough information.
        """
        self.disabled_reason = reason
        self.client = None

    def narrate(self, cause: RootCause, baseline_sr: float,
                observed_sr: float) -> str:
        if self.client is None:
            self.fallbacks += 1
            return self.fallback.narrate(cause, baseline_sr, observed_sr)

        self.calls += 1
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=SYSTEM,
                output_config={"effort": self.effort},
                messages=[{"role": "user",
                           "content": _facts(cause, baseline_sr,
                                             observed_sr).as_prompt()}],
            )
        except Exception as exc:
            # Deliberately broad, and the breadth is the point. The SDK's typed
            # exceptions do not cover every failure: with no credential
            # resolvable it raises a bare TypeError from header construction,
            # which an `except anthropic.APIStatusError` chain sails straight
            # past and turns a cosmetic feature into a crash during an incident.
            # Nothing downstream reads this return value, so there is no failure
            # here worth propagating.
            self._disable(f"{type(exc).__name__}: {exc}")
            self.fallbacks += 1
            return self.fallback.narrate(cause, baseline_sr, observed_sr)

        if response.stop_reason == "refusal":
            self.fallbacks += 1
            return self.fallback.narrate(cause, baseline_sr, observed_sr)

        text = "".join(b.text for b in response.content
                       if b.type == "text").strip()
        if not text:
            self.fallbacks += 1
            return self.fallback.narrate(cause, baseline_sr, observed_sr)
        return text


def build(kind: str = "template") -> Narrator:
    """`kind` is 'template' or 'claude'. Unknown values fall back to template."""
    if kind == "claude":
        return ClaudeNarrator()
    return TemplateNarrator()


def describe(narrator: Narrator) -> str:
    if isinstance(narrator, ClaudeNarrator):
        if narrator.available:
            return f"claude ({narrator.model}, effort={narrator.effort})"
        return f"claude unavailable, using template - {narrator.disabled_reason}"
    return "deterministic template"
