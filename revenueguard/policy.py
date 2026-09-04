"""The policy engine: what the system is allowed to do, and when it must stop.

The detector decides *whether something is wrong*. This decides *whether we are
permitted to act on it*. Keeping those separate is the point: a confident
detector is not authorisation to move money.

Every rule here is a bound on the blast radius of being wrong. They are checked
in order and the first failure is returned with its reason, so the audit ledger
records not just that an action was blocked but which specific rule blocked it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Decision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


@dataclass
class PolicyVerdict:
    decision: Decision
    reason: str
    rule: Optional[str] = None
    detail: Dict[str, float] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass
class PolicyConfig:
    #: Fraction of the *source gateway's current share* that one action may
    #: move -- not a fraction of all traffic for the key. A gateway holding 18%
    #: of a key gives up at most 7.2 points of it per action.
    max_shift_fraction: float = 0.40
    #: Total weight that may sit away from baseline for one key at any time.
    max_cumulative_divergence: float = 0.60
    #: Posterior confidence a detection needs before it can justify moving money.
    min_confidence: float = 0.95
    #: Minimum observed drop, in percentage points, worth acting on.
    min_drop_pp: float = 4.0
    #: Minutes before the same key may be acted on again.
    action_cooldown_min: int = 15
    #: Global rate limit. A system that reroutes forty times an hour is not
    #: responding to incidents, it is oscillating.
    max_actions_per_hour: int = 12
    #: A target gateway must be at least this much healthier than the source.
    min_target_advantage_pp: float = 8.0
    #: A target carrying its own alarm is never a valid destination.
    forbid_alarmed_target: bool = True
    #: Attempts observed on the target before its health estimate is trusted.
    min_target_attempts: int = 60


class PolicyEngine:
    def __init__(self, config: Optional[PolicyConfig] = None):
        self.config = config or PolicyConfig()
        self._last_action: Dict[Tuple[str, str], int] = {}
        self._action_times: List[int] = []

    def _recent_action_count(self, minute: int) -> int:
        self._action_times = [t for t in self._action_times if minute - t < 60]
        return len(self._action_times)

    def evaluate_shift(
        self,
        minute: int,
        method: str,
        issuer: str,
        source: str,
        target: Optional[str],
        confidence: float,
        drop_pp: float,
        source_sr: float,
        target_sr: Optional[float],
        target_attempts: int,
        target_alarmed: bool,
        current_divergence: float,
        evidence_penalty_pp: float = 0.0,
        all_candidates_alarmed: bool = False,
    ) -> PolicyVerdict:
        c = self.config

        if confidence < c.min_confidence:
            return PolicyVerdict(Decision.BLOCK,
                                 f"confidence {confidence:.3f} below {c.min_confidence}",
                                 "min_confidence", {"confidence": confidence})

        if drop_pp < c.min_drop_pp:
            return PolicyVerdict(Decision.BLOCK,
                                 f"drop {drop_pp:.1f}pp below {c.min_drop_pp}pp",
                                 "min_drop_pp", {"drop_pp": drop_pp})

        last = self._last_action.get((method, issuer))
        if last is not None and minute - last < c.action_cooldown_min:
            return PolicyVerdict(Decision.BLOCK,
                                 f"acted on this key {minute - last} min ago",
                                 "action_cooldown", {"since_min": float(minute - last)})

        if self._recent_action_count(minute) >= c.max_actions_per_hour:
            # A stopping rule, not a delay: something systemic is happening and
            # a human should look before the system keeps reacting.
            return PolicyVerdict(Decision.ESCALATE,
                                 f"{c.max_actions_per_hour} actions in the last hour",
                                 "max_actions_per_hour")

        if target is None:
            return PolicyVerdict(Decision.ESCALATE,
                                 "no alternative gateway available",
                                 "no_target")

        if all_candidates_alarmed:
            # Distinguished from the single-bad-target case on purpose: this is
            # not "pick a different gateway", it is "routing is the wrong tool".
            return PolicyVerdict(
                Decision.ESCALATE,
                f"every gateway serving {method}/{issuer} is degraded at once, "
                f"which points at the issuer rather than any gateway; rerouting "
                f"cannot reach a healthy path, so this needs a human",
                "no_healthy_destination")

        if c.forbid_alarmed_target and target_alarmed:
            return PolicyVerdict(Decision.ESCALATE,
                                 f"only candidate {target} is itself degraded",
                                 "target_alarmed")

        if target_attempts < c.min_target_attempts:
            return PolicyVerdict(Decision.BLOCK,
                                 f"target {target} has only {target_attempts} recent "
                                 f"attempts; health estimate not trustworthy",
                                 "min_target_attempts",
                                 {"target_attempts": float(target_attempts)})

        if target_sr is None:
            return PolicyVerdict(Decision.BLOCK, f"no health reading for {target}",
                                 "target_unknown")

        advantage_pp = (target_sr - source_sr) * 100.0
        # A coarser health reading clears a higher bar: pooling across issuers
        # can hide an issuer-specific fault on the destination, so the extra
        # margin buys back the resolution the estimate gave up.
        required = c.min_target_advantage_pp + evidence_penalty_pp
        if advantage_pp < required:
            extra = (f" ({c.min_target_advantage_pp:.0f}pp base + "
                     f"{evidence_penalty_pp:.0f}pp for a pooled health estimate)"
                     if evidence_penalty_pp else "")
            return PolicyVerdict(Decision.BLOCK,
                                 f"target only {advantage_pp:.1f}pp better than "
                                 f"source; needs {required:.1f}pp" + extra,
                                 "min_target_advantage",
                                 {"advantage_pp": advantage_pp,
                                  "required_pp": required})

        if current_divergence >= c.max_cumulative_divergence:
            return PolicyVerdict(Decision.ESCALATE,
                                 f"{current_divergence:.0%} of this key already diverted",
                                 "max_cumulative_divergence",
                                 {"divergence": current_divergence})

        return PolicyVerdict(Decision.ALLOW,
                             f"move up to {c.max_shift_fraction:.0%} of the share "
                             f"{source} currently holds onto {target}, which is "
                             f"running {advantage_pp:.1f}pp better",
                             detail={"advantage_pp": advantage_pp})

    def record_action(self, minute: int, method: str, issuer: str) -> None:
        self._last_action[(method, issuer)] = minute
        self._action_times.append(minute)
