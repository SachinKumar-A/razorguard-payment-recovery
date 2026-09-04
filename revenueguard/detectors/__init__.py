from .base import Alarm, Detector
from .ensemble import UnionDetector
from .threshold import FixedThresholdDetector
from .sequential import PosteriorDropDetector


def default_detector() -> Detector:
    """The detector the system ships with.

    A union of the fixed-threshold rule and the posterior drop test, at the
    operating point that won the matched-false-alarm comparison in
    `python -m revenueguard.sweep`: 16 of 18 incidents at a median 5 minutes,
    inside a budget of one false alarm per 1,000 slice-hours. That matches the
    best detection rate either member reaches alone and is 1.5 minutes faster
    than the member that reaches it.

    Both members run tighter here than they would alone, because false alarms
    add across a union. Changing either constant without re-running the sweep
    breaks that budget silently.
    """
    return UnionDetector([
        FixedThresholdDetector(sr_floor=0.70, consecutive=3, cooldown_min=0),
        PosteriorDropDetector(min_drop_pp=5.0, confidence=0.99, cooldown_min=0),
    ])


__all__ = ["Alarm", "Detector", "FixedThresholdDetector",
           "PosteriorDropDetector", "UnionDetector", "default_detector"]
