"""Gateways get worse as you push traffic at them.

Until this module existed, the simulation let the router move traffic onto a
destination without the destination noticing. That is the one assumption in the
project generous enough to change a conclusion rather than a number: a router
evaluated in a world with infinite headroom will always look better than one
deployed into a world with queues, connection pools and rate limits.

The shape
---------
Real acquirer behaviour under load is not linear. Below some utilisation
everything is fine and extra traffic costs nothing; past a knee, queues build,
timeouts start, and the success rate falls roughly linearly with further load.
So:

    u = offered_load / capacity

    congestion(u) = 1                       for u <= knee
                  = 1 - slope * (u - knee)  for u >  knee

with a floor, because even a badly overloaded gateway does not go to zero.

Why this makes the project honest rather than worse
---------------------------------------------------
It introduces a real cost to the router's own actions. Draining a degraded
gateway onto a healthy one now raises the healthy one's utilisation, and a large
enough diversion can push the destination past its knee and degrade it. The
system already has the machinery to notice that and undo it - the rollback path
watches the destination through the same estimator it used to choose it - and
this is what finally exercises it against something other than noise.

The measured recovery drops as a result. That is the correct direction, and the
figure that survives it is worth more than the one that did not face it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class CapacityModel:
    """How a gateway responds to being loaded.

    Defaults are chosen so that normal traffic, at its diurnal peak, sits
    comfortably below the knee on every gateway - congestion is something the
    router can cause, not a permanent tax on the baseline. If normal operation
    were already past the knee, the control arm would be degraded too and the
    comparison would measure the simulator's headroom rather than the policy.
    """

    #: Utilisation below which extra traffic is free.
    knee: float = 0.70
    #: Success-rate multiplier lost per unit of utilisation past the knee.
    #: Calibrated so a gateway at 100% of provisioned capacity loses about a
    #: tenth of its success rate and one at 150% loses about a quarter - the
    #: rough shape of a queueing system past saturation. Chosen before running
    #: the experiment, not tuned afterwards to produce a wanted result.
    slope: float = 0.35
    #: A saturated gateway still processes some traffic.
    floor: float = 0.55
    #: Capacity as a multiple of each gateway's own peak baseline load.
    headroom: float = 1.60

    def factor(self, offered: float, capacity: float) -> float:
        if capacity <= 0:
            return self.floor
        u = offered / capacity
        if u <= self.knee:
            return 1.0
        return max(self.floor, 1.0 - self.slope * (u - self.knee))

    def utilisation(self, offered: float, capacity: float) -> float:
        return (offered / capacity) if capacity > 0 else float("inf")


def capacities(total_txn_per_min: float, gateway_share: Dict[str, float],
               peak_multiplier: float, model: CapacityModel) -> Dict[str, float]:
    """Per-gateway capacity, sized from each one's own peak baseline load.

    Sizing from the baseline share rather than a flat number matters: a gateway
    that normally carries 18% of traffic has been provisioned for 18% of
    traffic, so dumping another gateway's 50% onto it is exactly the situation
    that should hurt. A uniform capacity would hide that.
    """
    return {g: total_txn_per_min * share * peak_multiplier * model.headroom
            for g, share in gateway_share.items()}
