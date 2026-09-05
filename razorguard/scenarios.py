"""Injected degradations -- the ground truth the detector never sees.

Every incident here is scheduled by us, so for each one we know the exact minute
it began, which slices it touched, and how deep it went. That is what makes the
evaluation honest: we are measuring detection latency against an event we caused,
not scoring a model against labels we invented.
"""
from dataclasses import dataclass, field
from typing import Callable, List

from .config import Slice


@dataclass
class Incident:
    incident_id: str
    kind: str
    start_min: int
    end_min: int
    # Returns the multiplier applied to a slice's success rate at minute t,
    # or 1.0 if this incident does not affect that slice at that minute.
    effect: Callable[[Slice, int], float] = field(repr=False)
    # Human-readable description of the blast radius, for the report.
    blast_radius: str = ""
    # Slices actually affected, filled in by the simulator as it runs.
    affected: set = field(default_factory=set, repr=False)

    def active(self, t: int) -> bool:
        return self.start_min <= t < self.end_min

    @property
    def duration(self) -> int:
        return self.end_min - self.start_min


def _match(s: Slice, gateway=None, method=None, issuer=None) -> bool:
    return ((gateway is None or s.gateway == gateway)
            and (method is None or s.method == method)
            and (issuer is None or s.issuer == issuer))


def hard_outage(iid, start, duration, gateway, depth=0.36):
    """A gateway falls over. Success rate collapses to `depth` of normal.

    The easy case. Any detector that misses this is broken.
    """
    def effect(s: Slice, t: int) -> float:
        return depth if _match(s, gateway=gateway) else 1.0
    return Incident(iid, "hard_outage", start, start + duration, effect,
                    blast_radius=f"gateway={gateway}")


def gradual_degradation(iid, start, duration, gateway, method, final_depth=0.80):
    """Success rate slides down over the first half of the window, then holds.

    The interesting case: at minute 3 the drop is still inside normal noise.
    Detecting this early without alarming on noise is the whole problem.
    """
    ramp = max(1, duration // 2)

    def effect(s: Slice, t: int) -> float:
        if not _match(s, gateway=gateway, method=method):
            return 1.0
        frac = min(1.0, (t - start) / ramp)
        return 1.0 - (1.0 - final_depth) * frac
    return Incident(iid, "gradual_degradation", start, start + duration, effect,
                    blast_radius=f"gateway={gateway} method={method}")


def issuer_degradation(iid, start, duration, issuer, method, depth=0.74):
    """One issuer's authorisation stack degrades on one method.

    Every other issuer is fine, so a global success-rate chart shows nothing.
    This is the case that justifies monitoring at slice grain at all.
    """
    def effect(s: Slice, t: int) -> float:
        return depth if _match(s, issuer=issuer, method=method) else 1.0
    return Incident(iid, "issuer_degradation", start, start + duration, effect,
                    blast_radius=f"issuer={issuer} method={method}")


def shallow_degradation(iid, start, duration, gateway, method, depth=0.93):
    """A ~7 point drop -- shallow enough that low-volume slices cannot see it.

    Included deliberately so the miss rate in the report is not flattering.
    """
    def effect(s: Slice, t: int) -> float:
        return depth if _match(s, gateway=gateway, method=method) else 1.0
    return Incident(iid, "shallow_degradation", start, start + duration, effect,
                    blast_radius=f"gateway={gateway} method={method}")


def default_incident_plan(days: int = 2) -> List[Incident]:
    """A repeatable schedule of incidents across the simulated window.

    Spread across the diurnal cycle on purpose: an outage at 04:00 has a tenth
    of the traffic of one at 20:00, and detectors that only work at peak volume
    should be exposed by that.
    """
    plan: List[Incident] = []
    day = 24 * 60
    for d in range(days):
        o = d * day
        plan += [
            hard_outage(f"INC-{d}-01", o + 3 * 60 + 10, 40, "gw_beta"),
            hard_outage(f"INC-{d}-02", o + 20 * 60 + 5, 35, "gw_gamma"),
            gradual_degradation(f"INC-{d}-03", o + 11 * 60, 90, "gw_alpha", "upi"),
            gradual_degradation(f"INC-{d}-04", o + 17 * 60 + 30, 75, "gw_beta", "card"),
            issuer_degradation(f"INC-{d}-05", o + 9 * 60 + 20, 60, "hdfc", "upi"),
            issuer_degradation(f"INC-{d}-06", o + 14 * 60, 50, "rbl", "card"),
            issuer_degradation(f"INC-{d}-07", o + 22 * 60, 45, "icici", "netbanking"),
            shallow_degradation(f"INC-{d}-08", o + 12 * 60 + 40, 70, "gw_alpha", "card"),
            shallow_degradation(f"INC-{d}-09", o + 6 * 60, 60, "gw_gamma", "upi"),
        ]
    return plan
