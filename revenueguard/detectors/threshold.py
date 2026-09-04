"""The dumb baseline.

Fixed threshold on success rate, sustained for N minutes. This is what an
on-call engineer would write in an afternoon, and it is the number every
sophisticated detector has to beat. Reporting a detector without this
comparison tells you nothing about whether the sophistication earned its keep.
"""
from collections import defaultdict, deque
from typing import Optional

from ..simulator import Observation
from .base import Alarm, Detector


class FixedThresholdDetector(Detector):
    def __init__(self, sr_floor: float = 0.85, consecutive: int = 3,
                 min_attempts: int = 5, cooldown_min: int = 20):
        super().__init__()
        self.sr_floor = sr_floor
        self.consecutive = consecutive
        self.min_attempts = min_attempts
        self.cooldown_min = cooldown_min
        self._streak = defaultdict(int)
        self._recent = defaultdict(lambda: deque(maxlen=consecutive))

    @property
    def name(self) -> str:
        return f"fixed_threshold(sr<{self.sr_floor}, {self.consecutive}min)"

    def _evaluate(self, obs: Observation) -> Optional[Alarm]:
        if obs.attempts < self.min_attempts:
            # Too little traffic to say anything; the streak is not reset,
            # because a quiet minute is not evidence of health.
            return None

        sr = obs.successes / obs.attempts
        self._recent[obs.slice_key].append(sr)

        if sr < self.sr_floor:
            self._streak[obs.slice_key] += 1
        else:
            self._streak[obs.slice_key] = 0

        if self._streak[obs.slice_key] < self.consecutive:
            return None

        return Alarm(
            minute=obs.minute,
            slice_key=obs.slice_key,
            observed_sr=sr,
            baseline_sr=self.sr_floor,
            confidence=1.0,
            evidence={"consecutive_minutes": float(self._streak[obs.slice_key]),
                      "attempts": float(obs.attempts)},
        )
