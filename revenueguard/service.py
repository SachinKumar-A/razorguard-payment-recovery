"""HTTP service: run the control plane against a live stream of outcomes.

    uvicorn revenueguard.service:app --host 0.0.0.0 --port 8000

What this is
------------
The same control loop the benchmarks run, driven on a wall-clock timer instead
of a simulated one, consuming real payment outcomes posted to `/ingest` instead
of a simulator. `ControlPlane.tick` is called here exactly as `run` calls it, so
the deployed path is the tested path.

What it deliberately does not do
--------------------------------
It does not apply its own routing decisions. Acquirer selection is not an
endpoint a third party can call, so this service **emits recommendations** at
`GET /routing` and records them in the audit ledger; something on your side -
a traffic manager, a config service, or a human - decides whether to act. That
boundary is the honest one, and pretending otherwise would be the single
easiest way to make this project untrue.

Endpoints
---------
    POST /ingest    push a batch of aggregated outcomes for the current minute
    GET  /routing   current recommended weights, and which are diverted
    GET  /state     health of the loop: minute, alarms, actions, buffer depth
    GET  /audit     recent ledger entries, refusals included
    GET  /health    liveness
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import METHOD_TICKET, METHODS
from .control_plane import ControlPlane, RunOutcome
from .detectors import default_detector
from .ingest import BufferedSource, PaymentOutcome
from .policy import PolicyConfig, PolicyEngine

#: Seconds per control-plane tick. One minute in production; overridable so a
#: demo does not have to run in real time.
TICK_SECONDS = float(os.environ.get("REVENUEGUARD_TICK_SECONDS", "60"))


def _ticket_for(method: str) -> float:
    return METHOD_TICKET.get(method, 1000.0)


class OutcomeIn(BaseModel):
    gateway: str = Field(..., min_length=1, max_length=64)
    method: str = Field(..., min_length=1, max_length=32)
    issuer: str = Field(..., min_length=1, max_length=64)
    attempts: int = Field(..., ge=0)
    successes: int = Field(..., ge=0)


class IngestIn(BaseModel):
    outcomes: List[OutcomeIn]
    #: Optional explicit minute. Omit and the service uses its current tick,
    #: which is what a live producer should do.
    minute: Optional[int] = None


class Engine:
    """Holds the loop, the buffer, and the clock."""

    def __init__(self) -> None:
        self.source = BufferedSource(_ticket_for)
        # No World at all: with a source configured the simulator is never
        # reachable, and passing a half-built one would only invite someone to
        # start reading from it.
        self.plane = ControlPlane(
            None, default_detector(),
            policy=PolicyEngine(PolicyConfig()), enable_routing=True,
            source=self.source)
        self.outcome = RunOutcome(minutes=0)
        self.minute = 0
        self.started = time.time()
        self.ticks = 0
        self._task: Optional[asyncio.Task] = None

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                self.plane.tick(self.minute, self.outcome)
            except Exception as exc:  # pragma: no cover - defensive
                # A bad minute must not kill the loop; the next one may be fine.
                self.outcome.ledger.record(
                    self.minute, "decision", "engine",
                    f"tick failed: {type(exc).__name__}: {exc}",
                    rule="tick_error", decision="block")
            self.minute += 1
            self.ticks += 1


engine = Engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine._task = asyncio.create_task(engine.loop())
    yield
    if engine._task:
        engine._task.cancel()


app = FastAPI(title="RevenueGuard", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> Dict[str, object]:
    return {
        "ok": True,
        "uptime_s": round(time.time() - engine.started, 1),
        "tick_seconds": TICK_SECONDS,
        "ticks": engine.ticks,
    }


@app.post("/ingest")
def ingest(body: IngestIn) -> Dict[str, object]:
    minute = body.minute if body.minute is not None else engine.minute
    accepted = 0
    for o in body.outcomes:
        outcome = PaymentOutcome(gateway=o.gateway, method=o.method,
                                 issuer=o.issuer, attempts=o.attempts,
                                 successes=o.successes)
        try:
            outcome.validate()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if engine.source.push(outcome, minute):
            accepted += 1

    return {
        "accepted": accepted,
        "rejected_late": len(body.outcomes) - accepted,
        "minute": minute,
        "current_minute": engine.minute,
        "buffered_minutes": engine.source.buffered_minutes,
    }


@app.get("/routing")
def routing() -> Dict[str, object]:
    """Recommended weights. Applying them is the caller's decision."""
    table = engine.plane.routing
    diverted = {
        f"{m}|{i}": {
            "weights": {g: round(w, 4) for g, w in table.current[(m, i)].items()},
            "baseline": {g: round(w, 4) for g, w in table.baseline[(m, i)].items()},
        }
        for (m, i) in table.current if table.is_diverted(m, i)
    }
    return {
        "diverted": diverted,
        "diverted_keys": len(diverted),
        "note": ("Recommendations only. This service does not and cannot apply "
                 "acquirer routing; a caller must act on these."),
    }


@app.get("/state")
def state() -> Dict[str, object]:
    o = engine.outcome
    return {
        "minute": engine.minute,
        "observations": len(o.observations),
        "alarms": len(o.alarms),
        "actions": o.actions,
        "blocked": o.blocked,
        "escalated": o.escalated,
        "rollbacks": o.rollbacks,
        "restores": o.restores,
        "audit_events": len(o.ledger),
        "open_diversions": len(engine.plane.diversions),
        "ingest": {
            "accepted": engine.source.accepted,
            "dropped_late": engine.source.dropped_late,
            "buffered_minutes": engine.source.buffered_minutes,
        },
        "methods_known": METHODS,
    }


@app.get("/audit")
def audit(limit: int = 50, kind: Optional[str] = None) -> Dict[str, object]:
    events = list(engine.outcome.ledger)
    if kind:
        events = [e for e in events if e.kind == kind]
    tail = events[-max(1, min(limit, 500)):]
    return {
        "total": len(engine.outcome.ledger),
        "returned": len(tail),
        "events": [
            {"seq": e.seq, "minute": e.minute, "kind": e.kind,
             "subject": e.subject, "rule": e.rule, "summary": e.summary}
            for e in tail
        ],
    }
