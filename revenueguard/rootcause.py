"""Root-cause attribution.

Given the slices alarming right now, which single dimension explains them?

This is deterministic and it stays deterministic. An LLM is not asked whether
there is an outage or what caused it -- it is handed this attribution afterwards
and asked to write it down in English. Letting a language model decide the cause
would put a sampled token in the path of a money-moving action, and there is no
version of that which survives review.

The method is lift with coverage. For each candidate value (a gateway, an
issuer, a method) we compare how much of the alarming population it covers
against how much of the monitored population it represents. A value covering
90% of alarms while being 33% of the fleet is doing explanatory work; one
covering 35% of alarms while being 33% of the fleet is a coincidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

DIMENSIONS = ("gateway", "method", "issuer")


def _parts(slice_key: str) -> Dict[str, str]:
    gw, method, issuer = slice_key.split("|")
    return {"gateway": gw, "method": method, "issuer": issuer}


@dataclass
class Attribution:
    dimension: str
    value: str
    coverage: float        # share of alarming slices carrying this value
    prevalence: float      # share of all monitored slices carrying it
    lift: float
    alarming: int
    total_alarming: int

    #: Below this many alarming slices, no secondary claim is trustworthy:
    #: with three alarms, any dimension covering all three has perfect coverage
    #: by coincidence, and the narrative would assert "peers unaffected" on
    #: evidence that cannot distinguish that from an early, partial rollout of a
    #: much wider outage.
    MIN_SLICES_FOR_SECONDARY = 5

    @property
    def confident(self) -> bool:
        return self.coverage >= 0.6 and self.lift >= 1.8

    @property
    def confident_secondary(self) -> bool:
        return (self.total_alarming >= self.MIN_SLICES_FOR_SECONDARY
                and self.coverage >= 0.8 and self.lift >= 2.0)

    def describe(self) -> str:
        return (f"{self.dimension}={self.value} "
                f"({self.alarming}/{self.total_alarming} alarming slices, "
                f"lift {self.lift:.1f}x)")


@dataclass
class RootCause:
    primary: Optional[Attribution]
    secondary: Optional[Attribution]
    alarming_slices: List[str]

    @property
    def label(self) -> str:
        if self.primary is None:
            return "unattributed"
        if self.secondary is not None and self.secondary.confident_secondary:
            return f"{self.primary.value}+{self.secondary.value}"
        return self.primary.value

    def narrate(self, baseline_sr: float, observed_sr: float) -> str:
        """Plain-language incident note.

        Deliberately a template rather than a model call: this text is written
        into the audit ledger, and the ledger must be reproducible. An LLM
        narration layer can sit on top of the same Attribution objects for the
        operator console, where a re-run producing different prose is harmless.
        """
        drop = (baseline_sr - observed_sr) * 100.0
        if self.primary is None:
            return (f"{len(self.alarming_slices)} slices degraded with no common "
                    f"dimension; treating as unattributed and escalating.")

        head = (f"Success rate on {self.primary.dimension} "
                f"'{self.primary.value}' fell {drop:.1f} points "
                f"({baseline_sr:.1%} to {observed_sr:.1%}).")

        if self.secondary is not None and self.secondary.confident_secondary:
            body = (f" All {self.secondary.alarming} alarming slices also share "
                    f"{self.secondary.dimension} '{self.secondary.value}' "
                    f"({self.secondary.lift:.1f}x its share of the fleet), "
                    f"which points at a localised failure rather than a total "
                    f"{self.primary.dimension} outage.")
        elif self.secondary is not None:
            body = (f" It spans {self.primary.alarming} of "
                    f"{self.primary.total_alarming} alarming slices. Too few "
                    f"slices have alarmed to narrow it below the "
                    f"{self.primary.dimension} yet.")
        else:
            body = (f" It spans {self.primary.alarming} of "
                    f"{self.primary.total_alarming} alarming slices, "
                    f"{self.primary.lift:.1f}x its share of the fleet, "
                    f"so the {self.primary.dimension} is the common factor.")
        return head + body


def attribute(alarming_slices: Sequence[str],
              all_slices: Sequence[str]) -> RootCause:
    if not alarming_slices:
        return RootCause(None, None, [])

    total = len(alarming_slices)
    alarm_parts = [_parts(s) for s in alarming_slices]
    all_parts = [_parts(s) for s in all_slices]

    scored: List[Attribution] = []
    for dim in DIMENSIONS:
        values = {p[dim] for p in alarm_parts}
        for val in values:
            n_alarm = sum(1 for p in alarm_parts if p[dim] == val)
            n_all = sum(1 for p in all_parts if p[dim] == val)
            if n_all == 0:
                continue
            coverage = n_alarm / total
            prevalence = n_all / len(all_parts)
            lift = coverage / prevalence if prevalence > 0 else 0.0
            scored.append(Attribution(dim, val, coverage, prevalence, lift,
                                      n_alarm, total))

    if not scored:
        return RootCause(None, None, list(alarming_slices))

    # Rank by coverage first: an explanation that misses half the alarms is not
    # an explanation, however high its lift.
    scored.sort(key=lambda a: (a.coverage, a.lift), reverse=True)
    primary = scored[0]
    secondary = next((a for a in scored[1:] if a.dimension != primary.dimension
                      and a.coverage >= 0.6), None)
    if not primary.confident:
        primary = primary if primary.coverage >= 0.5 else None
    return RootCause(primary, secondary, list(alarming_slices))
