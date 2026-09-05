"""What a recovery costs, so the figure quoted can be net rather than gross.

Rerouting is not free. Acquirers price differently, so moving volume from one to
another changes what the merchant pays even when every payment succeeds. A
system reporting only gross recovered revenue is quietly ignoring one side of
its own ledger, and a payments company reads the other side first.

The Indian asymmetry that makes this interesting
------------------------------------------------
UPI person-to-merchant carries **zero MDR** by regulation. Cards do not. So the
economics of a recovery depend entirely on which method degraded:

- A UPI recovery is free. Move as much as the safety rules allow; there is no
  cost side to weigh against the revenue.
- A card recovery is not. Shifting card volume to a pricier acquirer can cost
  more than the payments it rescues are worth, and the system should be able
  to notice.

That asymmetry is real, it is specific to this market, and it falls straight
out of the arithmetic rather than having to be special-cased.

What is invented here
---------------------
The rates below are plausible market figures, not quoted contracts, and they
are listed in DATA.md alongside every other invented constant. The *shape* -
zero on UPI, percentage on cards, flat-ish on netbanking, small per-acquirer
spread - is real. The exact numbers are mine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .simulator import Observation

#: Merchant discount rate by method, as a fraction of transaction value.
#: UPI P2M is zero by regulation in India - not a rounding, an actual zero.
METHOD_MDR = {
    "upi": 0.0,
    "card": 0.0180,
    "netbanking": 0.0090,
}

#: Per-acquirer spread on top of the method rate. Small, because acquirers
#: compete on it - but not nothing, which is the entire point of this module.
GATEWAY_MDR_PREMIUM = {
    "gw_alpha": 0.0012,
    "gw_beta": 0.0000,
    "gw_gamma": -0.0006,
}

#: Flat per-transaction fee on netbanking, where percentage pricing is unusual.
METHOD_FLAT_FEE = {
    "upi": 0.0,
    "card": 0.0,
    "netbanking": 12.0,
}


def fee_for(gateway: str, method: str, ticket: float) -> float:
    """Cost of one successful payment through this route."""
    if METHOD_MDR.get(method, 0.0) == 0.0 and METHOD_FLAT_FEE.get(method, 0.0) == 0.0:
        # Zero-MDR methods are genuinely free; the acquirer premium does not
        # apply to a rate that does not exist.
        return 0.0
    rate = METHOD_MDR.get(method, 0.0) + GATEWAY_MDR_PREMIUM.get(gateway, 0.0)
    return max(0.0, ticket * rate) + METHOD_FLAT_FEE.get(method, 0.0)


@dataclass
class CostBreakdown:
    total: float = 0.0
    by_method: Dict[str, float] = field(default_factory=dict)
    by_gateway: Dict[str, float] = field(default_factory=dict)
    successful_payments: int = 0

    def add(self, gateway: str, method: str, amount: float, count: int) -> None:
        self.total += amount
        self.by_method[method] = self.by_method.get(method, 0.0) + amount
        self.by_gateway[gateway] = self.by_gateway.get(gateway, 0.0) + amount
        self.successful_payments += count


def processing_cost(observations: List[Observation]) -> CostBreakdown:
    """What the merchant paid in fees for the successful payments in a run.

    Fees are charged on successes, not attempts - a declined payment costs the
    merchant the sale, not a discount rate.
    """
    out = CostBreakdown()
    for obs in observations:
        if obs.successes == 0:
            continue
        gateway, method, _ = obs.slice_key.split("|")
        unit = fee_for(gateway, method, obs.avg_ticket_inr)
        out.add(gateway, method, unit * obs.successes, obs.successes)
    return out


@dataclass
class NetRecovery:
    """Gross recovery against the cost of having recovered it."""
    gross_inr: float
    control_cost_inr: float
    treatment_cost_inr: float
    payments_recovered: int

    @property
    def incremental_cost_inr(self) -> float:
        """Extra fees the routing caused, over what the control arm paid.

        Two things move this, and they pull in opposite directions. Recovering
        payments means more successful payments to pay fees on, which raises
        cost and is the good kind of cost. Moving volume between acquirers
        changes the rate, which can go either way.
        """
        return self.treatment_cost_inr - self.control_cost_inr

    @property
    def net_inr(self) -> float:
        return self.gross_inr - self.incremental_cost_inr

    @property
    def cost_ratio(self) -> Optional[float]:
        """What share of the gross recovery went on fees."""
        if self.gross_inr <= 0:
            return None
        return self.incremental_cost_inr / self.gross_inr

    @property
    def worth_doing(self) -> bool:
        return self.net_inr > 0


def net_recovery(control_observations: List[Observation],
                 treatment_observations: List[Observation],
                 gross_inr: float, payments_recovered: int) -> NetRecovery:
    return NetRecovery(
        gross_inr=gross_inr,
        control_cost_inr=processing_cost(control_observations).total,
        treatment_cost_inr=processing_cost(treatment_observations).total,
        payments_recovered=payments_recovered,
    )


def marginal_cost_of_shift(source: str, target: str, method: str,
                           ticket: float) -> float:
    """Extra fee per payment from routing this method source -> target.

    Negative means the destination is cheaper. Zero on UPI, always: there is no
    rate to differ. The policy engine can use this to refuse a shift whose fee
    cost would exceed the value of the payments it rescues.
    """
    return fee_for(target, method, ticket) - fee_for(source, method, ticket)
