"""What the console shows, computed once and kept.

Separated from `app.py` for two reasons. It is importable without Streamlit, so
the Docker build can warm the cache while the image is being built and the
container opens in a second rather than three-quarters of a minute. And the
caching is then a plain function with a plain test, rather than a decorator
whose behaviour depends on which Streamlit runtime happens to be present -
`persist="disk"` is a no-op outside a full server session, which is exactly the
case the container hits.

The payload is deliberately small. It once carried the raw observations - two
hundred thousand dataclass instances per arm - which made it too heavy to be
worth writing to disk at all. The only thing anyone needed from them was a fee
total, so that is what it carries now.
"""
from __future__ import annotations

import hashlib
import pathlib
import pickle
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd

from razorguard.config import WorldConfig
from razorguard.control_plane import ControlPlane
from razorguard.detectors import default_detector
from razorguard.economics import processing_cost
from razorguard.experiment import exposure_inr
from razorguard.policy import PolicyConfig, PolicyEngine
from razorguard.scenarios import default_incident_plan
from razorguard.world import World

CACHE = pathlib.Path(__file__).resolve().parent.parent / ".cache"
CACHE_VERSION = 4     # bump when the shape of the payload below changes


#: Settings the console is allowed to vary, with the value each falls back to.
#: Named here rather than in the console so the cache key and the settings page
#: cannot drift apart: add a knob in one place and both follow.
TUNABLE = {
    "txn_per_min":       ("world",  900.0),
    "max_shift":         ("policy", 0.80),
    "max_divergence":    ("policy", 0.90),
    "min_confidence":    ("policy", 0.95),
    "min_drop_pp":       ("policy", 4.0),
    "cooldown_min":      ("policy", 15),
    "causes_per_hour":   ("policy", 4),
    "target_advantage":  ("policy", 8.0),
}

POLICY_FIELD = {
    "max_shift": "max_shift_fraction",
    "max_divergence": "max_cumulative_divergence",
    "min_confidence": "min_confidence",
    "min_drop_pp": "min_drop_pp",
    "cooldown_min": "action_cooldown_min",
    "causes_per_hour": "max_causes_per_hour",
    "target_advantage": "min_target_advantage_pp",
}


def defaults() -> Dict[str, float]:
    return {k: v for k, (_, v) in TUNABLE.items()}


def _configs(tuning: Dict[str, float], seed: int):
    t = {**defaults(), **(tuning or {})}
    world = WorldConfig(seed=seed, total_txn_per_min=float(t["txn_per_min"]))
    policy = PolicyConfig(**{POLICY_FIELD[k]: type(TUNABLE[k][1])(t[k])
                             for k in POLICY_FIELD})
    return world, policy, t


def _compute(days: int, seed: int, routing: bool, tuning: Dict[str, float]):
    world_cfg, policy_cfg, _ = _configs(tuning, seed)
    incidents = default_incident_plan(days)
    world = World(world_cfg, incidents)
    cp = ControlPlane(world, default_detector(),
                      policy=PolicyEngine(policy_cfg), enable_routing=routing)
    out = cp.run(days * 24 * 60)

    per_min: Dict[int, List[float]] = defaultdict(lambda: [0, 0, 0.0])
    for o in out.observations:
        b = per_min[o.minute]
        b[0] += o.attempts
        b[1] += o.successes
        b[2] += o.successes * o.avg_ticket_inr
    series = pd.DataFrame(
        [{"minute": m, "attempts": int(a), "successes": int(s), "revenue": r,
          "success_rate": (s / a) if a else None}
         for m, (a, s, r) in sorted(per_min.items())])

    # Per-incident aggregates, over two different sets of slices - and the
    # difference between them is the whole reason the first version of this
    # was wrong.
    #
    # A slice key is `gateway|method|issuer`. The *source* set is the slices
    # the incident actually touched, which for a gateway outage means that
    # gateway only. Measuring recovery there gives a large negative number,
    # because succeeding at routing means emptying exactly those slices: the
    # traffic, and the recovery with it, lands on a different gateway.
    #
    # So recovery is measured over the *cohort*: every route serving the same
    # (method, issuer) pairs, whichever gateway carries it. Demand is drawn per
    # (minute, method, issuer), so the cohort sees identical attempts in both
    # arms and the difference between them is real. The source set is still
    # needed - it is where the success-rate collapse is visible - but it
    # answers "what broke", not "what was recovered".
    #
    # `affected` is filled in by the simulator as it runs, so it is the real
    # blast radius rather than the intended one. `pre` covers the hour before
    # the incident on the same slices: the baseline is measured, not assumed.
    LOOKBACK = 60
    active: Dict[int, List[Tuple[int, bool]]] = defaultdict(list)
    for n, i in enumerate(incidents):
        for t in range(max(0, i.start_min - LOOKBACK), i.end_min):
            active[t].append((n, t >= i.start_min))

    cohorts = [{k.split("|", 1)[1] for k in i.affected} for i in incidents]

    def _blank():
        return {"att": 0, "suc": 0, "rev": 0.0,
                "pre_att": 0, "pre_suc": 0, "pre_rev": 0.0}

    src = [_blank() for _ in incidents]
    coh = [_blank() for _ in incidents]
    for o in out.observations:
        for n, inside in active.get(o.minute, ()):
            in_cohort = o.slice_key.split("|", 1)[1] in cohorts[n]
            if not in_cohort:
                continue
            in_source = o.slice_key in incidents[n].affected
            for bucket, wanted in ((src[n], in_source), (coh[n], True)):
                if not wanted:
                    continue
                p = "" if inside else "pre_"
                bucket[p + "att"] += o.attempts
                bucket[p + "suc"] += o.successes
                bucket[p + "rev"] += o.successes * o.avg_ticket_inr

    ledger = pd.DataFrame([{
        "seq": e.seq, "minute": e.minute, "kind": e.kind, "subject": e.subject,
        "rule": e.rule or "", "summary": e.summary,
    } for e in out.ledger])

    incident_rows = pd.DataFrame([{
        "id": i.incident_id, "kind": i.kind, "blast_radius": i.blast_radius,
        "start": i.start_min, "end": i.end_min, "duration": i.duration,
        "slices": len(i.affected), "cohort": len(cohorts[n]),
        # Ledger subjects, so the console can scope the audit trail to the
        # routes this incident actually concerns. Without it an issuer-wide
        # fault that overlapped a gateway outage inherits the outage's actions
        # and reports itself as having been routed around, which is the one
        # thing it was not.
        "cohort_keys": sorted(cohorts[n]),
        "cause_keys": sorted(
            {k.split("|")[0] for k in i.affected} |          # gateways
            {k.split("|")[2] for k in i.affected} |          # issuers
            cohorts[n]),
        **{"src_" + k: v for k, v in src[n].items()},
        **{"coh_" + k: v for k, v in coh[n].items()},
    } for n, i in enumerate(incidents)])

    # Deliberately not returning `out.observations`. The only thing outside
    # this function needed from them was the fee total, and 200,000 dataclass
    # instances per arm are what made this cache too heavy to be worth keeping
    # on disk - which is to say, what made the console take a minute to open
    # every single time.
    return {
        "processing_cost": processing_cost(out.observations).total,
        "attempts": out.attempts,
        "successes": out.successes, "revenue": out.revenue_inr,
        "actions": out.actions, "blocked": out.blocked,
        "escalated": out.escalated, "rollbacks": out.rollbacks,
        "restores": out.restores, "alarms": len(out.alarms),
        "audit_events": len(out.ledger),
        "blocked_by_rule": out.ledger.blocked_by_rule(),
        "exposure": exposure_inr(out, world, incidents),
        "series": series, "ledger": ledger, "incidents": incident_rows,
    }


def cache_key(days: int, seed: int, routing: bool,
              tuning: Optional[Dict[str, float]] = None) -> str:
    """One filename per distinct run. The tuning is hashed rather than spelled
    out so adding a knob does not change the shape of every existing name."""
    t = {**defaults(), **(tuning or {})}
    digest = hashlib.sha1(
        repr(sorted((k, float(v)) for k, v in t.items())).encode()
    ).hexdigest()[:10]
    return f"run-{days}-{seed}-{int(routing)}-{digest}-v{CACHE_VERSION}.pkl"


def run(days: int, seed: int, routing: bool,
        tuning: Optional[Dict[str, float]] = None):
    f = CACHE / cache_key(days, seed, routing, tuning)
    if f.is_file():
        try:
            return pickle.loads(f.read_bytes())
        except Exception:
            pass          # a stale or half-written cache is not a reason to fail
    data = _compute(days, seed, routing, tuning or {})
    try:
        CACHE.mkdir(exist_ok=True)
        f.write_bytes(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))
    except OSError:
        pass              # read-only filesystem: just be slow, do not break
    return data

