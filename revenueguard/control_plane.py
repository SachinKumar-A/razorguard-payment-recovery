"""The control loop: observe, detect, attribute, gate, act, verify, roll back.

One minute at a time. Nothing here reads the incident plan -- the loop learns
what "healthy" means from the traffic it has already seen, the same way a real
one would, so every baseline it compares against is earned rather than given.

The loop deliberately does the boring thing on the way out as well as in: after
diverting traffic it keeps watching both sides, rolls back if the destination
turns out to be worse, and eases traffic home in steps once the source proves it
has recovered. Most of the value in a system like this is in the exit, not the
entry.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .audit import AuditLedger
from .config import GATEWAYS, ISSUERS, METHODS, WorldConfig
from .detectors.base import Alarm, Detector
from .policy import Decision, PolicyConfig, PolicyEngine
from .rootcause import attribute
from .routing import CANARY_FLOOR, RoutingTable
from .scenarios import Incident
from .simulator import Observation
from .world import World

#: Minutes of history kept per slice for baseline estimation.
HISTORY_MIN = 180
#: Most recent minutes ignored when estimating a baseline, so a degradation in
#: progress cannot become the normal it is measured against.
BASELINE_LAG_MIN = 20
#: Window used for "how is this slice doing right now".
RECENT_MIN = 5


#: Attempts a health estimate needs before it is trusted without widening.
TRUST_ATTEMPTS = 60


@dataclass
class HealthEstimate:
    """A read on how a gateway is doing, plus how hard we had to look.

    `level` records what was pooled to get here, and it matters: a reading taken
    across every issuer on a gateway cannot see an issuer-specific fault on the
    destination. So a coarser estimate has to clear a higher bar before it can
    justify moving money, and `penalty_pp` carries that extra bar.
    """
    sr: Optional[float]
    attempts: int
    level: str
    window: int

    #: Extra advantage, in percentage points, demanded of a coarser estimate.
    PENALTY = {"slice": 0.0, "slice_wide": 1.0, "gateway_method": 3.0,
               "gateway": 5.0, "none": 0.0}

    @property
    def penalty_pp(self) -> float:
        return self.PENALTY.get(self.level, 0.0)


class HealthTracker:
    """Per-slice rolling health, learned from observations only."""

    def __init__(self) -> None:
        self._hist: Dict[str, Deque[Tuple[int, int, int]]] = defaultdict(
            lambda: deque(maxlen=HISTORY_MIN))

    def update(self, obs: Observation) -> None:
        self._hist[obs.slice_key].append((obs.minute, obs.attempts, obs.successes))

    def _sum(self, keys: List[str], minutes: int) -> Tuple[int, int]:
        att = suc = 0
        for k in keys:
            h = self._hist.get(k)
            if not h:
                continue
            for _, a, sc in list(h)[-minutes:]:
                att += a
                suc += sc
        return att, suc

    def estimate(self, gateway: str, method: str, issuer: str,
                 need: int = TRUST_ATTEMPTS) -> "HealthEstimate":
        """Health of a candidate destination, widening only as far as needed.

        At 03:00 a single (gateway, method, issuer) slice may see four attempts
        in five minutes - not enough to route on. Rather than refuse outright,
        widen the lens in fixed steps and declare which step was used:

            slice/5min  ->  slice/20min  ->  gateway+method  ->  gateway

        Refusing outright is what left overnight outages unattended. Widening
        without saying so would be worse than either.
        """
        exact = [f"{gateway}|{method}|{issuer}"]
        ladder = (
            ("slice", exact, RECENT_MIN),
            ("slice_wide", exact, 20),
            ("gateway_method", [f"{gateway}|{method}|{i}" for i in ISSUERS], 20),
            ("gateway", [f"{gateway}|{m}|{i}" for m in METHODS for i in ISSUERS], 20),
        )
        for level, keys, window in ladder:
            att, suc = self._sum(keys, window)
            if att >= need:
                return HealthEstimate(suc / att, att, level, window)

        att, suc = self._sum(ladder[-1][1], 20)
        if att == 0:
            return HealthEstimate(None, 0, "none", 20)
        return HealthEstimate(suc / att, att, "gateway", 20)

    def baseline_sr(self, slice_key: str) -> Optional[float]:
        h = self._hist.get(slice_key)
        if not h or len(h) < BASELINE_LAG_MIN + RECENT_MIN:
            return None
        rows = list(h)[:-BASELINE_LAG_MIN]
        att = sum(a for _, a, _ in rows)
        suc = sum(s for _, _, s in rows)
        return (suc / att) if att else None


@dataclass
class Diversion:
    method: str
    issuer: str
    source: str
    target: str
    opened_min: int
    shifted: float
    healthy_streak: int = 0
    restoring: bool = False


@dataclass
class RunOutcome:
    minutes: int
    observations: List[Observation] = field(default_factory=list)
    ledger: AuditLedger = field(default_factory=AuditLedger)
    alarms: List[Alarm] = field(default_factory=list)
    actions: int = 0
    blocked: int = 0
    escalated: int = 0
    rollbacks: int = 0
    restores: int = 0

    @property
    def successes(self) -> int:
        return sum(o.successes for o in self.observations)

    @property
    def attempts(self) -> int:
        return sum(o.attempts for o in self.observations)

    @property
    def revenue_inr(self) -> float:
        return sum(o.successes * o.avg_ticket_inr for o in self.observations)


class ControlPlane:
    def __init__(
        self,
        world: Optional[World],
        detector: Detector,
        policy: Optional[PolicyEngine] = None,
        enable_routing: bool = True,
        restore_after_healthy_min: int = 10,
        rollback_drop_pp: float = 6.0,
        source=None,
    ):
        #: Where observations come from. `None` means the world's own simulator,
        #: which is the benchmark path. A deployment passes an ObservationSource
        #: (see ingest.py) and nothing else in this class changes - that seam is
        #: the whole reason the loop was never allowed to see the incident plan.
        self.source = source
        self.world = world
        if world is None and source is None:
            raise ValueError(
                "a control plane needs either a World to simulate or an "
                "ObservationSource to read from")
        self.detector = detector
        self.policy = policy or PolicyEngine(PolicyConfig())
        self.enable_routing = enable_routing
        self.restore_after = restore_after_healthy_min
        self.rollback_drop = rollback_drop_pp / 100.0

        self.routing = RoutingTable.default(METHODS, ISSUERS)
        self.health = HealthTracker()
        self.diversions: Dict[Tuple[str, str], Diversion] = {}
        self.all_slices = [f"{g}|{m}|{i}" for g in GATEWAYS
                           for m in METHODS for i in ISSUERS]
        self._alarmed_recently: Dict[str, int] = {}

    # -- helpers ----------------------------------------------------------

    def _is_alarmed(self, slice_key: str, minute: int, within: int = 15) -> bool:
        last = self._alarmed_recently.get(slice_key)
        return last is not None and (minute - last) <= within

    def _pick_target(self, method: str, issuer: str, source: str, minute: int):
        """Healthiest gateway other than the source, with its evidence.

        Returns (gateway, HealthEstimate, alarmed). Candidates are ranked on
        their rate *after* the coarseness penalty, so a confidently measured
        gateway beats one that merely looks better through a blurrier lens.
        """
        best = None
        best_est: Optional[HealthEstimate] = None
        best_alarmed = False
        candidates = 0
        alarmed_candidates = 0
        for g in GATEWAYS:
            if g == source:
                continue
            est = self.health.estimate(g, method, issuer)
            if est.sr is None:
                continue
            candidates += 1
            alarmed = self._is_alarmed(f"{g}|{method}|{issuer}", minute)
            alarmed_candidates += 1 if alarmed else 0
            adjusted = est.sr - est.penalty_pp / 100.0
            incumbent = (best_est.sr - best_est.penalty_pp / 100.0
                         if best_est is not None else None)
            if incumbent is None or adjusted > incumbent:
                best, best_est, best_alarmed = g, est, alarmed
        # Every route to this issuer is degraded at once. That is the signature
        # of a fault on the issuer's side rather than any gateway's, and no
        # amount of rerouting reaches a healthy path -- they all terminate at
        # the same bank. Saying so is the correct response; quietly shuffling
        # traffic between equally broken routes would look like action and
        # accomplish nothing.
        all_alarmed = candidates > 0 and alarmed_candidates == candidates
        return best, best_est, best_alarmed, all_alarmed

    def _divergence(self, method: str, issuer: str) -> float:
        cur = self.routing.current[(method, issuer)]
        base = self.routing.baseline[(method, issuer)]
        return sum(max(0.0, base[g] - cur[g]) for g in cur)

    # -- the loop ---------------------------------------------------------

    def observe_minute(self, minute: int) -> List[Observation]:
        """Fetch one minute of outcomes from whichever source is configured."""
        if self.source is not None:
            return self.source.poll(minute)
        return self.world.step(minute, self.routing)

    def tick(self, minute: int, out: RunOutcome) -> List[Alarm]:
        """Advance the loop by exactly one minute.

        Split out of `run` so a long-lived service can drive the same code path
        on a wall-clock timer. A deployment that reimplemented this loop would
        be running something the benchmarks never tested.
        """
        observations = self.observe_minute(minute)
        out.observations.extend(observations)

        new_alarms: List[Alarm] = []
        for obs in observations:
            self.health.update(obs)
            alarm = self.detector.observe(obs)
            if alarm is not None:
                new_alarms.append(alarm)
                self._alarmed_recently[obs.slice_key] = minute

        out.alarms.extend(new_alarms)

        if new_alarms:
            self._handle_alarms(minute, new_alarms, out)

        if self.enable_routing:
            self._supervise(minute, out)

        return new_alarms

    def run(self, minutes: int) -> RunOutcome:
        out = RunOutcome(minutes=minutes)
        for t in range(minutes):
            self.tick(t, out)
        return out

    def _handle_alarms(self, minute: int, alarms: List[Alarm],
                       out: RunOutcome) -> None:
        keys = [a.slice_key for a in alarms]
        cause = attribute(keys, self.all_slices)

        worst = min(alarms, key=lambda a: a.observed_sr)
        note = cause.narrate(worst.baseline_sr, worst.observed_sr)
        out.ledger.record(
            minute, "detection", cause.label, note,
            slices=len(alarms),
            primary=(cause.primary.describe() if cause.primary else None),
            secondary=(cause.secondary.describe() if cause.secondary else None),
            worst_slice=worst.slice_key,
            worst_sr=round(worst.observed_sr, 4),
            baseline_sr=round(worst.baseline_sr, 4),
        )

        if not self.enable_routing:
            return

        for alarm in alarms:
            source, method, issuer = alarm.slice_key.split("|")
            subject = f"{method}|{issuer}"

            target, est, target_alarmed, all_alarmed = self._pick_target(
                method, issuer, source, minute)

            lens = ("" if est is None else
                    f", health read at {est.level} level "
                    f"({est.attempts:,} attempts over {est.window}min)")
            out.ledger.record(
                minute, "proposal", subject,
                f"shift {method}/{issuer} traffic off {source}"
                + (f" onto {target}" if target else " (no candidate)") + lens,
                source=source, target=target,
                source_sr=round(alarm.observed_sr, 4),
                target_sr=(round(est.sr, 4)
                           if est is not None and est.sr is not None else None),
                evidence_level=(est.level if est is not None else None),
                evidence_attempts=(est.attempts if est is not None else 0),
            )

            verdict = self.policy.evaluate_shift(
                minute=minute, method=method, issuer=issuer, source=source,
                target=target, confidence=alarm.confidence,
                drop_pp=alarm.drop_pp, source_sr=alarm.observed_sr,
                target_sr=(est.sr if est is not None else None),
                target_attempts=(est.attempts if est is not None else 0),
                target_alarmed=target_alarmed,
                current_divergence=self._divergence(method, issuer),
                evidence_penalty_pp=(est.penalty_pp if est is not None else 0.0),
                all_candidates_alarmed=all_alarmed,
                cause_key=cause.label,
            )

            out.ledger.record(
                minute, "decision", subject, verdict.reason, rule=verdict.rule,
                decision=verdict.decision.value,
            )

            if verdict.decision is Decision.ESCALATE:
                out.escalated += 1
                continue
            if verdict.decision is Decision.BLOCK:
                out.blocked += 1
                continue

            before = dict(self.routing.weights(method, issuer))
            after = self.routing.shift_away(
                method, issuer, source, target,
                self.policy.config.max_shift_fraction)
            moved = before[source] - after[source]
            if moved <= 1e-9:
                out.blocked += 1
                out.ledger.record(minute, "decision", subject,
                                  f"{source} already at the {CANARY_FLOOR:.0%} "
                                  f"canary floor; nothing left to move",
                                  rule="canary_floor", decision="block")
                continue

            self.policy.record_action(minute, method, issuer, cause.label)
            out.actions += 1
            self.diversions[(method, issuer)] = Diversion(
                method=method, issuer=issuer, source=source, target=target,
                opened_min=minute, shifted=moved)

            out.ledger.record(
                minute, "action", subject,
                f"moved {moved:.1%} of {method}/{issuer} from {source} to {target}",
                source=source, target=target, moved=round(moved, 4),
                weights_after={k: round(v, 4) for k, v in after.items()},
            )

    def _supervise(self, minute: int, out: RunOutcome) -> None:
        """Watch every open diversion: roll back if worse, ease home if better."""
        for key, div in list(self.diversions.items()):
            method, issuer = key
            subject = f"{method}|{issuer}"
            target_key = f"{div.target}|{method}|{issuer}"
            source_key = f"{div.source}|{method}|{issuer}"

            # Read the destination through the same pooled estimator used to
            # choose it. A raw five-minute rate on a thin slice swings wide
            # enough to clear any fixed rollback threshold on noise alone, and
            # rolling back on noise is worse than never having moved: it churns
            # traffic and then reports the churn as a safety feature.
            target_est = self.health.estimate(div.target, method, issuer)
            target_sr = target_est.sr
            target_base = self.health.baseline_sr(target_key)

            # Did the recovery action make things worse?
            if (target_sr is not None and target_base is not None
                    and target_est.attempts >= TRUST_ATTEMPTS
                    and target_sr < target_base - self.rollback_drop):
                self.routing.reset(method, issuer)
                del self.diversions[key]
                out.rollbacks += 1
                out.escalated += 1
                out.ledger.record(
                    minute, "rollback", subject,
                    f"destination {div.target} fell to {target_sr:.1%} against a "
                    f"{target_base:.1%} baseline on {target_est.attempts:,} "
                    f"attempts ({target_est.level}); reverted to baseline weights "
                    f"and escalated",
                    rule="rollback_target_degraded",
                    target=div.target, target_sr=round(target_sr, 4),
                    target_baseline=round(target_base, 4),
                    evidence_level=target_est.level,
                    evidence_attempts=target_est.attempts,
                )
                continue

            # Has the source recovered? The canary is the only reason we can see.
            source_est = self.health.estimate(div.source, method, issuer)
            source_sr = source_est.sr
            source_base = self.health.baseline_sr(source_key)
            # The canary carries little traffic by design, so require the
            # estimate to rest on real attempts before believing a recovery.
            healthy = (source_sr is not None and source_base is not None
                       and source_est.attempts >= TRUST_ATTEMPTS
                       and source_sr >= source_base - 0.02)

            div.healthy_streak = div.healthy_streak + 1 if healthy else 0

            if div.healthy_streak >= self.restore_after:
                self.routing.restore_step(method, issuer)
                div.restoring = True
                out.restores += 1
                out.ledger.record(
                    minute, "restore", subject,
                    f"{div.source} healthy for {div.healthy_streak} min on canary "
                    f"traffic ({source_sr:.1%}); easing weights back",
                    source=div.source, source_sr=round(source_sr, 4),
                    weights={k: round(v, 4) for k, v in
                             self.routing.weights(method, issuer).items()},
                )
                div.healthy_streak = 0
                if self.routing.at_baseline(method, issuer):
                    del self.diversions[key]
                    out.ledger.record(minute, "restore", subject,
                                      "weights fully restored to baseline")


def build(days: int, seed: int, detector: Detector, enable_routing: bool,
          incidents: Optional[List[Incident]] = None,
          policy: Optional[PolicyEngine] = None) -> Tuple[ControlPlane, List[Incident]]:
    from .scenarios import default_incident_plan
    incidents = incidents if incidents is not None else default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    cp = ControlPlane(world, detector, policy=policy, enable_routing=enable_routing)
    return cp, incidents
