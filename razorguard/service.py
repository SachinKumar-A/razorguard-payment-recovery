"""HTTP service: the control plane against a live stream, with state that survives.

    uvicorn razorguard.service:app --host 0.0.0.0 --port 8000

What this is
------------
The same control loop the benchmarks run, driven on a wall-clock timer instead
of a simulated one, consuming real payment outcomes posted to `/ingest`.
`ControlPlane.tick` is called here exactly as `run` calls it, and a test asserts
the two produce identical results - so the deployed path is the tested path
rather than a reimplementation of it.

What it deliberately does not do
--------------------------------
It does not apply its own routing decisions. Acquirer selection is not an
endpoint a third party can call, so this service **emits recommendations** at
`GET /routing` and records them; something on your side acts on them.

Operational shape
-----------------
State is durable (`persistence.py`): a restart replays the stored observation
stream back through the detector, restores the routing table and re-adopts open
diversions, so a deploy is not a window in which the system is awake and blind.
`/ingest` is authenticated (`security.py`). Escalations and rollbacks reach a
webhook off the loop's thread (`alerts.py`). `/metrics` speaks Prometheus.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .advisor import Advisor, escalations_in
from .alerts import Alerter, alerts_from_ledger
from .applier import ChangeTracker, build_from_env, recommendations_from
from .config import METHOD_TICKET, METHODS
from .control_plane import HISTORY_MIN, ControlPlane, RunOutcome
from .detectors import default_detector
from .ingest import BufferedSource, PaymentOutcome
from .investigator import evidence_from_run
from .persistence import Lease, Store, checkpoint
from .policy import PolicyConfig, PolicyEngine
from .security import AuthConfig, AuthError, verify

TICK_SECONDS = float(os.environ.get("RAZORGUARD_TICK_SECONDS", "60"))
STATE_PATH = os.environ.get("RAZORGUARD_STATE", "state/razorguard.db")
#: Replay a little more than the health tracker holds, so a restart restores a
#: full baseline window rather than a partial one.
WARM_MINUTES = int(os.environ.get("RAZORGUARD_WARM_MINUTES",
                                  str(HISTORY_MIN + 40)))
#: A lease must outlive several ticks, or a slow minute looks like a dead node.
LEASE_TTL = float(os.environ.get("RAZORGUARD_LEASE_TTL",
                                 str(max(90.0, TICK_SECONDS * 3))))

logging.basicConfig(
    level=os.environ.get("RAZORGUARD_LOG_LEVEL", "INFO"),
    format='{"ts":"%(asctime)s","level":"%(levelname)s",'
           '"logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("razorguard")


def _ticket_for(method: str) -> float:
    return METHOD_TICKET.get(method, 1000.0)


class OutcomeIn(BaseModel):
    gateway: str = Field(..., min_length=1, max_length=64)
    method: str = Field(..., min_length=1, max_length=32)
    issuer: str = Field(..., min_length=1, max_length=64)
    attempts: int = Field(..., ge=0)
    successes: int = Field(..., ge=0)


class IngestIn(BaseModel):
    outcomes: List[OutcomeIn] = Field(..., max_length=5000)
    minute: Optional[int] = None


class Engine:
    """The loop, its state, and everything that outlives a restart."""

    def __init__(self) -> None:
        self.auth = AuthConfig.from_env()
        self.store = Store(STATE_PATH)
        self.lease = Lease(self.store, ttl_seconds=LEASE_TTL)
        self.alerter = Alerter()
        self.applier = build_from_env()
        self.changes = ChangeTracker()
        self.source = BufferedSource(_ticket_for)
        self.applied = 0
        self.apply_failures = 0
        self.advisor: Optional[Advisor] = None
        self.advice_given = 0

        self.plane = ControlPlane(
            None, default_detector(),
            policy=PolicyEngine(PolicyConfig()), enable_routing=True,
            source=self.source)

        self.outcome = RunOutcome(minutes=0)
        self.started = time.time()
        self.ticks = 0
        self.tick_errors = 0
        self.last_tick_ms = 0.0
        self._task: Optional[asyncio.Task] = None

        self.minute = self._restore()
        # Only one instance may act. The rest wait, warm, and take over if this
        # one dies - see persistence.Lease for why sharing is not an option.
        if self.lease.try_acquire():
            log.info("active: holding the lease as %s", self.lease.holder)
        else:
            log.info("standby: %s holds the lease; waiting",
                     self.lease.status().get("holder"))

    def _restore(self) -> int:
        """Rebuild everything derived, then re-adopt what was in flight."""
        observations = self.store.recent_observations(WARM_MINUTES)
        replayed = self.plane.warm(observations)
        weights = self.store.load_routing_into(self.plane.routing)
        diversions = self.plane.restore_diversions(self.store.load_diversions())

        # The ledger continues its sequence rather than restarting at 1.
        self.outcome.ledger._seq = self.store.max_audit_seq

        minute = self.store.minute + 1 if replayed else 0
        log.info("restored: replayed=%d weights=%d diversions=%d "
                 "next_minute=%d auth=%s alerts=%s",
                 replayed, weights, diversions, minute, self.auth.mode,
                 "on" if self.alerter.enabled else "off")
        if diversions and not replayed:
            # Weights diverted with no history to judge them by. The supervisor
            # holds until baselines refill, which is the safe behaviour, but it
            # is worth saying out loud rather than discovering later.
            log.warning("re-adopted %d diversion(s) with no replayed history; "
                        "supervision resumes once baselines refill", diversions)
        return minute

    def _become_active(self) -> None:
        """Take over from a dead holder, with state rebuilt from the store."""
        log.info("taking over the lease as %s", self.lease.holder)
        self.plane = ControlPlane(
            None, default_detector(),
            policy=PolicyEngine(PolicyConfig()), enable_routing=True,
            source=self.source)
        self.changes = ChangeTracker()
        self.minute = self._restore()

    def _attach_advice(self, minute: int) -> None:
        """Give a human something to do with a refusal, not just the refusal.

        Advisory only: it is recorded as advice and reaches the alert, and no
        routing decision reads it.
        """
        escalations = escalations_in(self.outcome.ledger, minute)
        if not escalations:
            return
        if self.advisor is None:
            self.advisor = Advisor(evidence_from_run(self.outcome))
        else:
            self.advisor.evidence = evidence_from_run(self.outcome)
        for event in escalations:
            try:
                advice = self.advisor.advise(event)
            except Exception as exc:
                log.error("advisor failed on seq %d: %s", event.seq, exc)
                continue
            self.advice_given += 1
            self.outcome.ledger.record(
                minute, "decision", event.subject,
                f"advice for the on-call engineer: {advice.summary()}",
                rule="advice", decision="advice", **advice.as_evidence())

    def _push_recommendations(self) -> None:
        recs = recommendations_from(self.plane.routing, self.minute,
                                    reason="razorguard recommendation")
        changed, skipped = self.changes.changed(recs)
        if not changed:
            return
        result = self.applier.apply(changed)
        self.applied += result.delivered
        self.apply_failures += result.failed
        for err in result.errors:
            log.error("apply failed: %s", err)

    def tick_once(self) -> None:
        started = time.perf_counter()
        try:
            self.plane.tick(self.minute, self.outcome)
        except Exception as exc:
            self.tick_errors += 1
            log.exception("tick %d failed: %s", self.minute, exc)
            self.outcome.ledger.record(
                self.minute, "decision", "engine",
                f"tick failed: {type(exc).__name__}: {exc}",
                rule="tick_error", decision="block")
        else:
            self._attach_advice(self.minute)
            for alert in alerts_from_ledger(self.outcome.ledger, self.minute):
                self.alerter.send(alert)
            if self.applier.mode != "off":
                self._push_recommendations()
        finally:
            try:
                checkpoint(self.store, self.plane, self.outcome, self.minute)
            except Exception as exc:
                # Losing durability is bad; losing the loop is worse.
                log.error("checkpoint failed at minute %d: %s", self.minute, exc)
            self.last_tick_ms = (time.perf_counter() - started) * 1000.0
            self.minute += 1
            self.ticks += 1

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            if not self.lease.is_active:
                # Standby. Poll for the lease; take over only if the holder
                # has stopped renewing.
                if await asyncio.to_thread(self.lease.try_acquire):
                    await asyncio.to_thread(self._become_active)
                continue

            # Renew before acting, never after. A process that stalled long
            # enough to lose its lease must not finish the tick it was in the
            # middle of - another instance is already the one deciding.
            if not await asyncio.to_thread(self.lease.renew):
                log.warning("lost the lease; standing down without acting")
                continue

            # Off the event loop: a tick does real statistical work and a
            # synchronous SQLite commit, neither of which should stall /ingest.
            await asyncio.to_thread(self.tick_once)

    def close(self) -> None:
        if self.lease.is_active:
            # Hand over immediately rather than making a standby wait out the
            # full TTL after a planned shutdown.
            self.lease.release()
        try:
            checkpoint(self.store, self.plane, self.outcome, self.minute - 1)
        except Exception as exc:  # pragma: no cover - shutdown path
            log.error("final checkpoint failed: %s", exc)
        self.alerter.close()
        self.store.close()
        log.info("stopped cleanly at minute %d", self.minute)


engine: Optional[Engine] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = Engine()
    engine._task = asyncio.create_task(engine.loop())
    try:
        yield
    finally:
        if engine._task:
            engine._task.cancel()
        engine.close()


app = FastAPI(title="RazorGuard", version="1.0.0", lifespan=lifespan)


def _engine() -> Engine:
    if engine is None:  # pragma: no cover - only before startup completes
        raise HTTPException(status_code=503, detail="starting up")
    return engine


# ---------------------------------------------------------------- endpoints

@app.get("/health")
def health() -> Dict[str, object]:
    e = _engine()
    return {
        "ok": True,
        "role": "active" if e.lease.is_active else "standby",
        "uptime_s": round(time.time() - e.started, 1),
        "minute": e.minute,
        "ticks": e.ticks,
        "tick_errors": e.tick_errors,
        "tick_seconds": TICK_SECONDS,
        "auth": e.auth.mode,
        "alerts": e.alerter.enabled,
        "apply_mode": e.applier.mode,
    }


@app.post("/ingest")
async def ingest(body: IngestIn, request: Request) -> Dict[str, object]:
    e = _engine()
    try:
        verify(e.auth, request.headers.get("authorization"),
               request.headers.get("x-razorguard-timestamp"),
               request.headers.get("x-razorguard-signature"),
               await request.body())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    minute = body.minute if body.minute is not None else e.minute
    accepted = 0
    for o in body.outcomes:
        outcome = PaymentOutcome(gateway=o.gateway, method=o.method,
                                 issuer=o.issuer, attempts=o.attempts,
                                 successes=o.successes)
        try:
            outcome.validate()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if e.source.push(outcome, minute):
            accepted += 1

    return {
        "accepted": accepted,
        "rejected_late": len(body.outcomes) - accepted,
        "minute": minute,
        "current_minute": e.minute,
        "buffered_minutes": e.source.buffered_minutes,
    }


@app.get("/routing")
def routing() -> Dict[str, object]:
    """Recommended weights. Applying them is the caller's decision."""
    e = _engine()
    table = e.plane.routing
    diverted = {
        f"{m}|{i}": {
            "weights": {g: round(w, 4) for g, w in table.current[(m, i)].items()},
            "baseline": {g: round(w, 4) for g, w in table.baseline[(m, i)].items()},
        }
        for (m, i) in table.current if table.is_diverted(m, i)
    }
    return {
        "minute": e.minute,
        "diverted": diverted,
        "diverted_keys": len(diverted),
        "note": ("Recommendations only. This service does not and cannot apply "
                 "acquirer routing; a caller must act on these."),
    }


@app.get("/state")
def state() -> Dict[str, object]:
    e = _engine()
    o = e.outcome
    return {
        "minute": e.minute,
        "observations_in_memory": len(o.observations),
        "alarms": len(o.alarms),
        "actions": o.actions,
        "blocked": o.blocked,
        "escalated": o.escalated,
        "rollbacks": o.rollbacks,
        "restores": o.restores,
        "audit_events": e.store.max_audit_seq,
        "open_diversions": len(e.plane.diversions),
        "last_tick_ms": round(e.last_tick_ms, 2),
        "ingest": {
            "accepted": e.source.accepted,
            "dropped_late": e.source.dropped_late,
            "buffered_minutes": e.source.buffered_minutes,
        },
        "alerts": e.alerter.stats(),
        "lease": e.lease.status(),
        "apply": {
            "mode": e.applier.mode,
            "delivered": e.applied,
            "failed": e.apply_failures,
        },
        "advice": {
            "given": e.advice_given,
            "model": (e.advisor.available if e.advisor else None),
        },
        "efficacy_breaker": {
            "open": e.plane.breaker_open,
            "trips": e.plane.breaker_trips,
            "samples": len(e.plane.efficacy),
        },
        "methods_known": METHODS,
    }


@app.get("/audit")
def audit(limit: int = 50, kind: Optional[str] = None) -> Dict[str, object]:
    """Served from the store, so it survives a restart."""
    e = _engine()
    events = e.store.audit_tail(limit=max(1, min(limit, 500)), kind=kind)
    return {"total": e.store.max_audit_seq, "returned": len(events),
            "events": events}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    e = _engine()
    o = e.outcome
    a = e.alerter.stats()
    rows = [
        ("razorguard_minute", "counter", "Control-plane minute counter.",
         e.minute),
        ("razorguard_actions_total", "counter", "Routing actions taken.",
         o.actions),
        ("razorguard_rollbacks_total", "counter", "Diversions reverted.",
         o.rollbacks),
        ("razorguard_escalations_total", "counter",
         "Decisions handed to a human.", o.escalated),
        ("razorguard_blocked_total", "counter",
         "Proposals refused by policy.", o.blocked),
        ("razorguard_open_diversions", "gauge",
         "Keys currently away from baseline.", len(e.plane.diversions)),
        ("razorguard_ingest_dropped_late_total", "counter",
         "Outcomes posted for a minute already processed.",
         e.source.dropped_late),
        ("razorguard_tick_errors_total", "counter", "Ticks that raised.",
         e.tick_errors),
        ("razorguard_tick_duration_ms", "gauge",
         "Duration of the last tick.", round(e.last_tick_ms, 2)),
        ("razorguard_alerts_failed_total", "counter",
         "Webhook deliveries that failed.", a["failed"]),
        ("razorguard_is_active", "gauge",
         "1 if this instance holds the lease.",
         1 if e.lease.is_active else 0),
        ("razorguard_efficacy_breaker_open", "gauge",
         "1 while shifting is halted for not helping.",
         1 if e.plane.breaker_open else 0),
        ("razorguard_efficacy_breaker_trips_total", "counter",
         "Times the strategy was judged to be doing harm.",
         e.plane.breaker_trips),
        ("razorguard_recommendations_applied_total", "counter",
         "Routing changes delivered downstream.", e.applied),
    ]
    out = []
    for name, kind, help_text, value in rows:
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {kind}")
        out.append(f"{name} {value}")
    return "\n".join(out) + "\n"
