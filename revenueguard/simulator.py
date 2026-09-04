"""Traffic simulator.

Emits one row per (slice, minute): attempts and successes. That is the *only*
thing the detector is allowed to see -- no incident labels, no slice metadata
beyond its identity.
"""
from dataclasses import dataclass
from typing import Iterator, List

import numpy as np

from .config import SliceProfile, WorldConfig
from .scenarios import Incident


@dataclass
class Observation:
    minute: int
    slice_key: str
    method: str
    attempts: int
    successes: int
    avg_ticket_inr: float

    @property
    def failures(self) -> int:
        return self.attempts - self.successes


class Simulator:
    def __init__(self, config: WorldConfig, incidents: List[Incident]):
        self.config = config
        self.profiles = config.profiles()
        self.incidents = incidents
        self.rng = np.random.default_rng(config.seed)

    def _volume_multiplier(self, minute: int) -> float:
        hour = (minute // 60) % 24
        nxt = (hour + 1) % 24
        frac = (minute % 60) / 60.0
        # Linear interpolation between hourly points so volume moves smoothly.
        return (self.config.diurnal[hour] * (1 - frac)
                + self.config.diurnal[nxt] * frac)

    def run(self, minutes: int) -> Iterator[Observation]:
        for t in range(minutes):
            mult = self._volume_multiplier(t)
            active = [inc for inc in self.incidents if inc.active(t)]
            for prof in self.profiles:
                lam = prof.base_volume_per_min * mult
                attempts = int(self.rng.poisson(lam))
                if attempts == 0:
                    continue

                p = prof.base_success_rate
                for inc in active:
                    factor = inc.effect(prof.slice, t)
                    if factor != 1.0:
                        p *= factor
                        inc.affected.add(prof.slice.key)
                p = float(np.clip(p, 0.0, 1.0))

                successes = int(self.rng.binomial(attempts, p))
                yield Observation(
                    minute=t,
                    slice_key=prof.slice.key,
                    method=prof.slice.method,
                    attempts=attempts,
                    successes=successes,
                    avg_ticket_inr=prof.avg_ticket_inr,
                )

    def collect(self, minutes: int) -> List[Observation]:
        return list(self.run(minutes))
