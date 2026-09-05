"""Evaluation.

Every number produced here comes from comparing a detector's alarms against the
incident plan that generated the traffic. Nothing is hand-picked and nothing is
asserted that the harness cannot recompute.

Definitions, stated explicitly because they are where this kind of report
usually cheats:

- **Detected** -- at least one alarm on a slice the incident actually touched,
  raised while the incident was still running. An alarm after it ended is not a
  detection.
- **Time to detect** -- minutes from incident start to that first alarm.
- **False alarm** -- an alarm that cannot be attributed to any incident on that
  slice, allowing a short grace window after an incident ends for the detector's
  own baseline to settle. Reported per 1,000 slice-hours monitored, because a
  raw count means nothing without knowing how many tests were run.
- **Exposure** -- rupees of failed volume above the slice's healthy failure
  rate. Split into before/after first detection. Money after detection is
  *addressable*, not recovered: routing is not implemented in this version and
  the report must not imply it is.
"""
from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Optional

from .config import SliceProfile
from .detectors.base import Alarm
from .scenarios import Incident
from .simulator import Observation

RECOVERY_GRACE_MIN = 10


@dataclass
class IncidentResult:
    incident_id: str
    kind: str
    blast_radius: str
    start_min: int
    duration: int
    affected_slices: int
    detected: bool
    time_to_detect: Optional[int]
    exposure_inr: float
    exposure_before_detect_inr: float

    @property
    def exposure_after_detect_inr(self) -> float:
        return self.exposure_inr - self.exposure_before_detect_inr


@dataclass
class BenchResult:
    detector: str
    minutes: int
    slices_monitored: int
    incidents: List[IncidentResult] = field(default_factory=list)
    total_alarms: int = 0
    false_alarms: int = 0

    @property
    def detected_count(self) -> int:
        return sum(1 for i in self.incidents if i.detected)

    @property
    def miss_count(self) -> int:
        return len(self.incidents) - self.detected_count

    @property
    def slice_hours(self) -> float:
        return self.slices_monitored * self.minutes / 60.0

    @property
    def false_alarms_per_1k_slice_hours(self) -> float:
        return self.false_alarms / self.slice_hours * 1000.0

    @property
    def median_ttd(self) -> Optional[float]:
        vals = [i.time_to_detect for i in self.incidents if i.time_to_detect is not None]
        return median(vals) if vals else None

    def ttd_by_kind(self) -> Dict[str, Optional[float]]:
        out: Dict[str, List[int]] = {}
        for i in self.incidents:
            out.setdefault(i.kind, [])
            if i.time_to_detect is not None:
                out[i.kind].append(i.time_to_detect)
        return {k: (median(v) if v else None) for k, v in out.items()}

    def detection_by_kind(self) -> Dict[str, str]:
        tot: Dict[str, int] = {}
        hit: Dict[str, int] = {}
        for i in self.incidents:
            tot[i.kind] = tot.get(i.kind, 0) + 1
            hit[i.kind] = hit.get(i.kind, 0) + (1 if i.detected else 0)
        return {k: f"{hit[k]}/{tot[k]}" for k in sorted(tot)}

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "minutes": self.minutes,
            "slices_monitored": self.slices_monitored,
            "slice_hours": round(self.slice_hours, 1),
            "incidents": len(self.incidents),
            "detected": self.detected_count,
            "missed": self.miss_count,
            "median_time_to_detect_min": self.median_ttd,
            "total_alarms": self.total_alarms,
            "false_alarms": self.false_alarms,
            "false_alarms_per_1k_slice_hours": round(
                self.false_alarms_per_1k_slice_hours, 2),
            "detection_by_kind": self.detection_by_kind(),
            "median_ttd_by_kind": self.ttd_by_kind(),
            "per_incident": [
                {
                    "id": i.incident_id,
                    "kind": i.kind,
                    "blast_radius": i.blast_radius,
                    "start_min": i.start_min,
                    "duration_min": i.duration,
                    "affected_slices": i.affected_slices,
                    "detected": i.detected,
                    "ttd_min": i.time_to_detect,
                    "exposure_inr": round(i.exposure_inr),
                    "exposure_before_detect_inr": round(i.exposure_before_detect_inr),
                    "exposure_after_detect_inr": round(i.exposure_after_detect_inr),
                }
                for i in self.incidents
            ],
        }


def evaluate(
    detector_name: str,
    observations: List[Observation],
    profiles: List[SliceProfile],
    incidents: List[Incident],
    alarms: List[Alarm],
    minutes: int,
) -> BenchResult:
    base_sr = {p.slice.key: p.base_success_rate for p in profiles}
    ticket = {p.slice.key: p.avg_ticket_inr for p in profiles}

    # First in-window alarm per incident.
    first_alarm: Dict[str, int] = {}
    attributed = set()
    for idx, al in enumerate(alarms):
        for inc in incidents:
            if al.slice_key not in inc.affected:
                continue
            if inc.start_min <= al.minute < inc.end_min + RECOVERY_GRACE_MIN:
                attributed.add(idx)
                if al.minute < inc.end_min:
                    prev = first_alarm.get(inc.incident_id)
                    if prev is None or al.minute < prev:
                        first_alarm[inc.incident_id] = al.minute

    # Exposure, bucketed per incident by whether it accrued before detection.
    exposure: Dict[str, List[float]] = {i.incident_id: [0.0, 0.0] for i in incidents}
    by_minute: Dict[int, List[Observation]] = {}
    for obs in observations:
        by_minute.setdefault(obs.minute, []).append(obs)

    for inc in incidents:
        det = first_alarm.get(inc.incident_id)
        for t in range(inc.start_min, inc.end_min):
            for obs in by_minute.get(t, ()):
                if obs.slice_key not in inc.affected:
                    continue
                expected = obs.attempts * base_sr[obs.slice_key]
                lost = expected - obs.successes
                if lost <= 0:
                    continue
                inr = lost * ticket[obs.slice_key]
                exposure[inc.incident_id][0] += inr
                if det is None or t < det:
                    exposure[inc.incident_id][1] += inr

    results = []
    for inc in incidents:
        det = first_alarm.get(inc.incident_id)
        total, before = exposure[inc.incident_id]
        results.append(IncidentResult(
            incident_id=inc.incident_id,
            kind=inc.kind,
            blast_radius=inc.blast_radius,
            start_min=inc.start_min,
            duration=inc.duration,
            affected_slices=len(inc.affected),
            detected=det is not None,
            time_to_detect=(det - inc.start_min) if det is not None else None,
            exposure_inr=total,
            exposure_before_detect_inr=before,
        ))

    return BenchResult(
        detector=detector_name,
        minutes=minutes,
        slices_monitored=len(profiles),
        incidents=results,
        total_alarms=len(alarms),
        false_alarms=len(alarms) - len(attributed),
    )
