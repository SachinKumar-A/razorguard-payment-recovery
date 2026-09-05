"""Guards on the properties the report depends on.

If any of these break, a number in the README is a lie.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from razorguard.config import WorldConfig
from razorguard.detectors import FixedThresholdDetector, PosteriorDropDetector
from razorguard.metrics import evaluate
from razorguard.scenarios import default_incident_plan
from razorguard.simulator import Simulator


def _run(detector, days=1, seed=7):
    incidents = default_incident_plan(days)
    sim = Simulator(WorldConfig(seed=seed), incidents)
    minutes = days * 24 * 60
    obs = sim.collect(minutes)
    for o in obs:
        detector.observe(o)
    result = evaluate(detector.name, obs, sim.profiles, incidents,
                      detector.alarms, minutes)
    return result, obs, incidents


def test_successes_never_exceed_attempts():
    _, obs, _ = _run(FixedThresholdDetector())
    assert all(0 <= o.successes <= o.attempts for o in obs)


def test_simulation_is_deterministic():
    _, a, _ = _run(FixedThresholdDetector(), seed=11)
    _, b, _ = _run(FixedThresholdDetector(), seed=11)
    key = lambda o: (o.minute, o.slice_key, o.attempts, o.successes)
    assert [key(o) for o in a] == [key(o) for o in b]


def test_detector_cannot_see_incident_labels():
    """The Observation record carries no incident information at all."""
    _, obs, _ = _run(FixedThresholdDetector())
    fields = set(vars(obs[0]))
    assert not (fields & {"incident_id", "incident", "degraded", "label"})


def test_hard_outages_are_always_detected():
    res, _, _ = _run(FixedThresholdDetector(sr_floor=0.85, consecutive=3))
    hard = [i for i in res.incidents if i.kind == "hard_outage"]
    assert hard and all(i.detected for i in hard)


def test_time_to_detect_is_never_negative():
    for det in (FixedThresholdDetector(), PosteriorDropDetector()):
        res, _, _ = _run(det)
        assert all(i.time_to_detect is None or i.time_to_detect >= 0
                   for i in res.incidents)


def test_cooldown_suppresses_repeat_alarms():
    det = FixedThresholdDetector(cooldown_min=20)
    _run(det)
    seen = {}
    for a in det.alarms:
        if a.slice_key in seen:
            assert a.minute - seen[a.slice_key] >= 20
        seen[a.slice_key] = a.minute


def test_loose_threshold_produces_false_alarms():
    """A detector allowed to alarm constantly must be shown to do so."""
    res, _, _ = _run(FixedThresholdDetector(sr_floor=0.90, consecutive=3))
    assert res.false_alarms > 0
    assert res.false_alarms <= res.total_alarms


def test_exposure_before_detection_never_exceeds_total():
    res, _, _ = _run(PosteriorDropDetector())
    for i in res.incidents:
        assert 0 <= i.exposure_before_detect_inr <= i.exposure_inr + 1e-6
