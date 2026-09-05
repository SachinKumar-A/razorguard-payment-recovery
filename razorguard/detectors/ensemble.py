"""Run several detectors and take the union of what they find.

The benchmark makes the case for this on its own. At a matched false-alarm rate
the fixed-threshold rule catches four incidents the posterior detector misses -
all shallow drops it is configured to ignore - while the posterior detector is
roughly twice as fast on what it does catch. Neither dominates, which is exactly
the situation where a union is worth more than either member.

The cost is that false alarms add up too. A union of two detectors each running
at some false-alarm rate produces roughly the sum, so a union is only a real
gain if each member is first tightened to keep the total inside the same budget.
That is why the sweep exists and why nothing here claims an improvement on its
own: the comparison has to be made at a matched false-alarm rate or it is
meaningless.

Ownership of suppression
------------------------
Members are driven through their `_evaluate` rather than their `observe`, so
their individual cooldowns never fire. The union owns suppression entirely.
Otherwise a member that alarmed a minute ago would stay silent while the union
believed it had simply found nothing, and the union's own cooldown would be
applied on top of an already-filtered stream.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from ..simulator import Observation
from .base import Alarm, Detector


class UnionDetector(Detector):
    """Alarms when any member alarms."""

    def __init__(self, members: Sequence[Detector], cooldown_min: int = 20,
                 name: Optional[str] = None):
        super().__init__()
        if not members:
            raise ValueError("a union needs at least one member")
        self.members: List[Detector] = list(members)
        self.cooldown_min = cooldown_min
        self._name = name
        #: How often each member was the one that fired, for the report. A
        #: member that never wins a slice is not earning its false alarms.
        self.credit = {m.name: 0 for m in self.members}

    @property
    def name(self) -> str:
        if self._name:
            return self._name
        return "union(" + " + ".join(m.name.split("(")[0]
                                     for m in self.members) + ")"

    def _evaluate(self, obs: Observation) -> Optional[Alarm]:
        # Every member sees every observation, always. Short-circuiting on the
        # first hit would starve the others' history and silently change what
        # they detect later.
        fired: List[Alarm] = []
        for member in self.members:
            alarm = member._evaluate(obs)
            if alarm is not None:
                alarm.evidence["source"] = member.name
                fired.append(alarm)

        if not fired:
            return None

        # When more than one member fires, keep the one reporting the deepest
        # drop - it is the most informative description of the same event.
        best = max(fired, key=lambda a: a.drop_pp)
        self.credit[best.evidence["source"]] = (
            self.credit.get(best.evidence["source"], 0) + 1)
        return best
