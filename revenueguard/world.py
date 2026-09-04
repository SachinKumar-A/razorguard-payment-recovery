"""Closed-loop world.

The open-loop simulator in `simulator.py` generates traffic per slice and is
fine for benchmarking a detector, which changes nothing. It cannot be used to
evaluate a *router*, because a router's whole purpose is to change where traffic
goes -- and in that simulator, where traffic goes is fixed.

So here demand and health are separated:

    demand(method, issuer)  -- how many payments arrive. Happens to us.
    routing weights         -- which gateway each one is sent to. Ours to change.
    health(gateway, method, issuer) -- how many succeed. Happens to us.

Reproducibility across policies
-------------------------------
Comparing "router on" against "router off" is only meaningful if both runs face
the same demand. Every random draw is therefore keyed by its own coordinates --
(seed, minute, method, issuer) for demand, (seed, minute, slice) for outcomes --
rather than drawn from one shared stream. A stream would desynchronise the
moment the two runs made different numbers of draws, and the measured difference
would be partly luck. Here demand is bit-identical between runs by construction.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from .config import (GATEWAYS, ISSUER_SHARE, ISSUERS, METHOD_BASE_SR,
                     METHOD_SHARE, METHOD_TICKET, METHODS, Slice, WorldConfig)
from .routing import RoutingTable
from .scenarios import Incident
from .simulator import Observation

_METHOD_IDX = {m: i for i, m in enumerate(METHODS)}
_ISSUER_IDX = {s: i for i, s in enumerate(ISSUERS)}
_GATEWAY_IDX = {g: i for i, g in enumerate(GATEWAYS)}

#: Per-issuer offsets from the method's base success rate. Kept identical to the
#: open-loop simulator so detector numbers stay comparable across both worlds.
ISSUER_SR_JITTER = {"hdfc": 0.004, "icici": 0.002, "sbi": -0.006, "axis": 0.0,
                    "kotak": -0.003, "yes": -0.008, "idfc": -0.005, "rbl": -0.010}

#: Gateways are not equally good even when healthy.
GATEWAY_SR_JITTER = {"gw_alpha": 0.004, "gw_beta": 0.0, "gw_gamma": -0.007}


class World:
    def __init__(self, config: WorldConfig, incidents: List[Incident]):
        self.config = config
        self.incidents = incidents
        self.demand_per_min: Dict[Tuple[str, str], float] = {}
        for m in METHODS:
            for i in ISSUERS:
                self.demand_per_min[(m, i)] = (
                    config.total_txn_per_min * METHOD_SHARE[m] * ISSUER_SHARE[i])

    # -- environment ------------------------------------------------------

    def volume_multiplier(self, minute: int) -> float:
        hour = (minute // 60) % 24
        nxt = (hour + 1) % 24
        frac = (minute % 60) / 60.0
        return (self.config.diurnal[hour] * (1 - frac)
                + self.config.diurnal[nxt] * frac)

    def healthy_sr(self, gateway: str, method: str, issuer: str) -> float:
        return float(np.clip(
            METHOD_BASE_SR[method] + ISSUER_SR_JITTER[issuer]
            + GATEWAY_SR_JITTER[gateway], 0.0, 0.995))

    def actual_sr(self, gateway: str, method: str, issuer: str, minute: int) -> float:
        sr = self.healthy_sr(gateway, method, issuer)
        sl = Slice(gateway, method, issuer)
        for inc in self.incidents:
            if not inc.active(minute):
                continue
            factor = inc.effect(sl, minute)
            if factor != 1.0:
                sr *= factor
                inc.affected.add(sl.key)
        return float(np.clip(sr, 0.0, 1.0))

    # -- deterministic per-cell randomness --------------------------------

    def _demand_rng(self, minute: int, method: str, issuer: str) -> np.random.Generator:
        return np.random.default_rng(
            [self.config.seed, 1, minute, _METHOD_IDX[method], _ISSUER_IDX[issuer]])

    def _outcome_rng(self, minute: int, gateway: str, method: str,
                     issuer: str) -> np.random.Generator:
        return np.random.default_rng(
            [self.config.seed, 2, minute, _GATEWAY_IDX[gateway],
             _METHOD_IDX[method], _ISSUER_IDX[issuer]])

    # -- one tick ---------------------------------------------------------

    def step(self, minute: int, routing: RoutingTable) -> List[Observation]:
        """Advance one minute under the given routing table."""
        mult = self.volume_multiplier(minute)
        out: List[Observation] = []

        for method in METHODS:
            for issuer in ISSUERS:
                rng = self._demand_rng(minute, method, issuer)
                demand = int(rng.poisson(self.demand_per_min[(method, issuer)] * mult))
                if demand == 0:
                    continue

                weights = routing.weights(method, issuer)
                gws = list(weights.keys())
                probs = np.array([weights[g] for g in gws], dtype=float)
                probs = probs / probs.sum()
                # Splitting demand is part of the environment's draw, not the
                # outcome draw, so it uses the demand RNG and stays stable.
                split = rng.multinomial(demand, probs)

                for g, attempts in zip(gws, split):
                    if attempts == 0:
                        continue
                    p = self.actual_sr(g, method, issuer, minute)
                    orng = self._outcome_rng(minute, g, method, issuer)
                    successes = int(orng.binomial(int(attempts), p))
                    out.append(Observation(
                        minute=minute,
                        slice_key=Slice(g, method, issuer).key,
                        method=method,
                        attempts=int(attempts),
                        successes=successes,
                        avg_ticket_inr=METHOD_TICKET[method],
                    ))
        return out

    def counterfactual_healthy_successes(self, obs: Observation) -> float:
        """Successes this cell would have produced with no incident running.

        Used only for reporting exposure, never fed to a detector.
        """
        gw, method, issuer = obs.slice_key.split("|")
        return obs.attempts * self.healthy_sr(gw, method, issuer)
