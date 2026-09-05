"""The union must alarm when either member does, and own suppression itself."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from razorguard.detectors import (FixedThresholdDetector,
                                  PosteriorDropDetector, UnionDetector,
                                    default_detector)
from razorguard.detectors.base import Alarm, Detector
from razorguard.simulator import Observation


class Never(Detector):
    def __init__(self):
        super().__init__()
        self.seen = 0

    @property
    def name(self):
        return "never"

    def _evaluate(self, obs):
        self.seen += 1
        return None


class Always(Detector):
    def __init__(self, drop=10.0, name="always"):
        super().__init__()
        self.seen = 0
        self._n = name
        self.drop = drop

    @property
    def name(self):
        return self._n

    def _evaluate(self, obs):
        self.seen += 1
        return Alarm(minute=obs.minute, slice_key=obs.slice_key,
                     observed_sr=1.0 - self.drop / 100.0, baseline_sr=1.0,
                     confidence=0.99)


def obs(minute=0, key="gw_alpha|upi|hdfc"):
    return Observation(minute=minute, slice_key=key, method="upi",
                       attempts=200, successes=100, avg_ticket_inr=640.0)


def test_a_union_needs_members():
    with pytest.raises(ValueError):
        UnionDetector([])


def test_alarms_when_any_member_alarms():
    u = UnionDetector([Never(), Always()])
    assert u.observe(obs()) is not None


def test_silent_when_no_member_alarms():
    u = UnionDetector([Never(), Never()])
    assert u.observe(obs()) is None


def test_every_member_sees_every_observation():
    """Short-circuiting on the first hit would starve the others' history."""
    quiet, loud = Never(), Always()
    u = UnionDetector([loud, quiet])
    for m in range(5):
        u.observe(obs(minute=m * 30))
    assert quiet.seen == 5
    assert loud.seen == 5


def test_deepest_drop_wins_when_several_fire():
    shallow, deep = Always(drop=5.0, name="shallow"), Always(drop=40.0, name="deep")
    u = UnionDetector([shallow, deep])
    alarm = u.observe(obs())
    assert alarm.evidence["source"] == "deep"


def test_union_owns_the_cooldown():
    u = UnionDetector([Always()], cooldown_min=20)
    assert u.observe(obs(minute=0)) is not None
    assert u.observe(obs(minute=5)) is None
    assert u.observe(obs(minute=25)) is not None


def test_credit_records_which_member_fired():
    u = UnionDetector([Always(name="a"), Never()])
    u.observe(obs(minute=0))
    u.observe(obs(minute=100))
    assert u.credit["a"] == 2


def test_shipped_detector_is_a_union_of_both_families():
    d = default_detector()
    assert isinstance(d, UnionDetector)
    kinds = {type(m) for m in d.members}
    assert FixedThresholdDetector in kinds
    assert PosteriorDropDetector in kinds


def test_shipped_members_have_their_own_cooldowns_disabled():
    """Otherwise the union filters an already-filtered stream."""
    for m in default_detector().members:
        assert m.cooldown_min == 0
