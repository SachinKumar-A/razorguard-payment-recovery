"""Detector interface.

A detector consumes observations in time order and emits alarms. It never sees
the incident plan. Detectors are also responsible for their own alarm
suppression: without a cooldown a single 40-minute outage produces 40 alarms per
slice and every rate in the report becomes meaningless.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..simulator import Observation


@dataclass
class Alarm:
    minute: int
    slice_key: str
    observed_sr: float
    baseline_sr: float
    confidence: float
    evidence: Dict[str, float] = field(default_factory=dict)

    @property
    def drop_pp(self) -> float:
        """Drop in percentage points -- the number an operator actually reads."""
        return (self.baseline_sr - self.observed_sr) * 100.0


class Detector(ABC):
    #: minutes to stay quiet on a slice after alarming on it
    cooldown_min: int = 20

    def __init__(self) -> None:
        self._last_alarm: Dict[str, int] = {}
        self.alarms: List[Alarm] = []

    @property
    def name(self) -> str:
        return type(self).__name__

    def _suppressed(self, slice_key: str, minute: int) -> bool:
        last = self._last_alarm.get(slice_key)
        return last is not None and (minute - last) < self.cooldown_min

    def observe(self, obs: Observation) -> Optional[Alarm]:
        alarm = self._evaluate(obs)
        if alarm is None:
            return None
        if self._suppressed(obs.slice_key, obs.minute):
            return None
        self._last_alarm[obs.slice_key] = obs.minute
        self.alarms.append(alarm)
        return alarm

    @abstractmethod
    def _evaluate(self, obs: Observation) -> Optional[Alarm]:
        """Return an Alarm if this observation looks like a degradation."""
