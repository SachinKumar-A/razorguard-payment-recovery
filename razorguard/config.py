"""World configuration: the slices we monitor and their healthy behaviour.

A *slice* is the finest unit we monitor: one (gateway, method, issuer) triple.
Degradations are rarely global -- they hit one issuer on one method through one
gateway -- so this is the grain at which detection has to work.
"""
from dataclasses import dataclass, field
from itertools import product


@dataclass(frozen=True)
class Slice:
    gateway: str
    method: str
    issuer: str

    @property
    def key(self) -> str:
        return f"{self.gateway}|{self.method}|{self.issuer}"

    def __str__(self) -> str:
        return self.key


@dataclass
class SliceProfile:
    """Healthy behaviour of one slice."""
    slice: Slice
    base_success_rate: float
    base_volume_per_min: float   # Poisson mean at the daily average
    avg_ticket_inr: float        # used to convert lost transactions into rupees


GATEWAYS = ["gw_alpha", "gw_beta", "gw_gamma"]
METHODS = ["upi", "card", "netbanking"]
ISSUERS = ["hdfc", "icici", "sbi", "axis", "kotak", "yes", "idfc", "rbl"]

# Healthy success rates differ by method -- UPI is high-volume and high-success,
# netbanking is neither. These are the simulator's ground truth, not a claim
# about production Razorpay numbers.
METHOD_BASE_SR = {"upi": 0.965, "card": 0.918, "netbanking": 0.892}
METHOD_SHARE = {"upi": 0.62, "card": 0.28, "netbanking": 0.10}
METHOD_TICKET = {"upi": 640.0, "card": 2150.0, "netbanking": 3400.0}

# Issuer traffic is heavily skewed: the tail issuers carry very little volume,
# which is precisely where naive per-slice statistics fall apart.
ISSUER_SHARE = {
    "hdfc": 0.26, "icici": 0.21, "sbi": 0.18, "axis": 0.13,
    "kotak": 0.09, "yes": 0.06, "idfc": 0.04, "rbl": 0.03,
}
GATEWAY_SHARE = {"gw_alpha": 0.50, "gw_beta": 0.32, "gw_gamma": 0.18}


@dataclass
class WorldConfig:
    total_txn_per_min: float = 900.0
    seed: int = 7
    # Diurnal shape: multiplier on volume by hour of day (IST-ish e-commerce curve)
    diurnal: list = field(default_factory=lambda: [
        0.25, 0.16, 0.11, 0.09, 0.09, 0.12, 0.22, 0.38,
        0.58, 0.78, 0.94, 1.05, 1.12, 1.08, 1.02, 1.00,
        1.06, 1.18, 1.34, 1.48, 1.52, 1.30, 0.86, 0.48,
    ])

    def profiles(self) -> list:
        out = []
        for gw, method, issuer in product(GATEWAYS, METHODS, ISSUERS):
            share = GATEWAY_SHARE[gw] * METHOD_SHARE[method] * ISSUER_SHARE[issuer]
            vol = self.total_txn_per_min * share
            # Issuers vary slightly around the method's base rate.
            jitter = {"hdfc": 0.004, "icici": 0.002, "sbi": -0.006, "axis": 0.0,
                      "kotak": -0.003, "yes": -0.008, "idfc": -0.005, "rbl": -0.010}[issuer]
            out.append(SliceProfile(
                slice=Slice(gw, method, issuer),
                base_success_rate=min(0.995, METHOD_BASE_SR[method] + jitter),
                base_volume_per_min=vol,
                avg_ticket_inr=METHOD_TICKET[method],
            ))
        return out
