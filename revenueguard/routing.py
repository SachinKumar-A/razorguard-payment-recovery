"""The routing table: how demand is split across gateways.

This is the only thing the control plane is allowed to change. Everything else
in the world -- how much demand arrives, how healthy each gateway is -- happens
to us.

A weight vector is held per (method, issuer), because that is the grain at which
a payment can actually be re-pointed: you cannot move a UPI transaction to a
card gateway, and issuer performance differs enough that one blanket weight per
gateway would be a lie.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .config import GATEWAY_SHARE, GATEWAYS

#: Never drain a gateway completely. A gateway carrying zero traffic emits zero
#: observations, so we would lose the ability to tell whether it recovered --
#: and we would never be able to route back to it. This floor is the canary.
CANARY_FLOOR = 0.03


def _normalise(weights: Dict[str, float]) -> Dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        n = len(weights)
        return {g: 1.0 / n for g in weights}
    return {g: w / total for g, w in weights.items()}


@dataclass
class RoutingTable:
    """Live weights, plus the baseline they decay back toward."""

    baseline: Dict[Tuple[str, str], Dict[str, float]] = field(default_factory=dict)
    current: Dict[Tuple[str, str], Dict[str, float]] = field(default_factory=dict)

    @classmethod
    def default(cls, methods: Iterable[str], issuers: Iterable[str]) -> "RoutingTable":
        base: Dict[Tuple[str, str], Dict[str, float]] = {}
        for m in methods:
            for i in issuers:
                base[(m, i)] = _normalise(dict(GATEWAY_SHARE))
        return cls(baseline={k: dict(v) for k, v in base.items()},
                   current={k: dict(v) for k, v in base.items()})

    def weights(self, method: str, issuer: str) -> Dict[str, float]:
        return self.current[(method, issuer)]

    def is_diverted(self, method: str, issuer: str) -> bool:
        cur = self.current[(method, issuer)]
        base = self.baseline[(method, issuer)]
        return any(abs(cur[g] - base[g]) > 1e-9 for g in cur)

    def diverted_keys(self) -> List[Tuple[str, str]]:
        return [k for k in self.current if self.is_diverted(*k)]

    def shift_away(self, method: str, issuer: str, source: str,
                   target: str, fraction: float) -> Dict[str, float]:
        """Move `fraction` of the source gateway's weight onto the target.

        Returns the new weight vector. The source is never taken below
        CANARY_FLOOR, so the shift actually applied may be smaller than asked;
        the caller reads the result rather than assuming.
        """
        cur = dict(self.current[(method, issuer)])
        movable = max(0.0, cur[source] - CANARY_FLOOR)
        moved = min(movable, cur[source] * fraction)
        if moved <= 0:
            return cur
        cur[source] -= moved
        cur[target] += moved
        self.current[(method, issuer)] = _normalise(cur)
        return self.current[(method, issuer)]

    def restore_step(self, method: str, issuer: str, step: float = 0.34) -> Dict[str, float]:
        """Ease weights back toward baseline by `step` of the remaining gap.

        Gradual on purpose: slamming full traffic back onto a gateway that has
        only just recovered is how a flap becomes an outage.
        """
        cur = dict(self.current[(method, issuer)])
        base = self.baseline[(method, issuer)]
        for g in cur:
            cur[g] += (base[g] - cur[g]) * step
        self.current[(method, issuer)] = _normalise(cur)
        return self.current[(method, issuer)]

    def reset(self, method: str, issuer: str) -> Dict[str, float]:
        self.current[(method, issuer)] = dict(self.baseline[(method, issuer)])
        return self.current[(method, issuer)]

    def at_baseline(self, method: str, issuer: str, tol: float = 0.005) -> bool:
        cur = self.current[(method, issuer)]
        base = self.baseline[(method, issuer)]
        return all(abs(cur[g] - base[g]) <= tol for g in cur)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {f"{m}|{i}": dict(w) for (m, i), w in self.current.items()
                if self.is_diverted(m, i)}
