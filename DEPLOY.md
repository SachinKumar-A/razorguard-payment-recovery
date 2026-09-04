# Deploying RevenueGuard

Be clear about what deployment means here, because the honest answer is not the
flattering one.

**What is deployable today:** the control plane runs as a container, consumes
real payment outcomes over HTTP, and emits routing recommendations with a full
audit trail. The deployed code path is the benchmarked code path — the service
calls `ControlPlane.tick`, which is what `run` calls, and a test asserts the two
produce identical results.

**What is not:** it cannot apply its own decisions. Acquirer selection is not an
endpoint a third party can call, so RevenueGuard emits recommendations and
something on your side acts on them. Also, the numbers in the README come from a
simulator; pointing this at production traffic tells you what it detects there,
which is not a thing this repository can claim in advance.

---

## Run it

```bash
docker compose up --build
```

- service  → `http://localhost:8000` (`/docs` for the OpenAPI page)
- console  → `http://localhost:8501`

Or without Docker:

```bash
pip install -r requirements-service.txt
uvicorn revenueguard.service:app --host 0.0.0.0 --port 8000
```

---

## Feeding it real data

The system only ever needed four things per slice per minute, which is why the
integration is small:

```
POST /ingest
{
  "outcomes": [
    {"gateway": "gw_beta",  "method": "upi", "issuer": "hdfc",
     "attempts": 400, "successes": 150},
    {"gateway": "gw_alpha", "method": "upi", "issuer": "hdfc",
     "attempts": 600, "successes": 580}
  ]
}
```

Aggregated counts, not individual payments — deliberately. The detector never
needs a transaction record, so this integration never has to carry one, which
keeps both the blast radius and the data it holds uninteresting.

Post once a minute per slice from wherever you already aggregate: a stream
consumer, a scheduled query against your payments table, or a webhook off your
existing monitoring.

**Late data is refused, not absorbed.** An outcome arriving for a minute the
loop has already processed is counted in `dropped_late` and dropped. Folding it
into the current minute would corrupt the trailing baseline the detector
compares against, which is a subtle way to make every subsequent number wrong.
Alert on that counter.

### Replaying history

The fastest way to find out what this would have caught is to feed it a real
past incident:

```csv
minute,gateway,method,issuer,attempts,successes
0,gw_alpha,upi,hdfc,612,588
0,gw_beta,upi,hdfc,388,371
1,gw_alpha,upi,hdfc,634,609
```

```python
from revenueguard.config import METHOD_TICKET
from revenueguard.control_plane import ControlPlane
from revenueguard.detectors import default_detector
from revenueguard.ingest import CsvSource

source = CsvSource("incident.csv", lambda m: METHOD_TICKET.get(m, 1000.0))
plane = ControlPlane(None, default_detector(), source=source)
out = plane.run(max(source.minutes) + 1)

for event in out.ledger:
    print(event.minute, event.kind, event.summary)
```

---

## Reading it

| endpoint | what it is for |
|---|---|
| `GET /health` | liveness; container healthcheck uses it |
| `GET /state` | loop minute, alarms, actions, rollbacks, ingest counters |
| `GET /routing` | **the output** — recommended weights per diverted key |
| `GET /audit` | the ledger, refusals included (`?kind=action`, `?limit=200`) |

`/routing` is what a caller consumes. Everything else is observability.

---

## Acting on the output

This is the part that needs a decision from you, and it is not a technical one.

1. **Human-in-the-loop.** Route escalations and recommendations to on-call;
   a person changes weights. Slowest, safest, and the right default for the
   first weeks.
2. **Semi-automatic.** Apply recommendations automatically below some blast
   radius, escalate above it. The policy engine already draws that line — every
   `escalate` verdict is the system saying a human should look.
3. **Automatic.** Wire `/routing` to your traffic manager. Only reasonable once
   you have watched it for long enough to trust the rollback path, because that
   path is what stops a bad recommendation compounding.

The audit ledger is the same under all three. What changes is who reads it
before money moves.

---

## Before production

Honest list. None of these are hard; none are done.

- [ ] **Persistence.** State is in memory: a restart loses baselines, open
      diversions and the ledger. The loop needs ~3 hours of history before its
      baselines mean anything, so a restart is currently a three-hour blind
      spot. SQLite or Postgres behind `HealthTracker` and `AuditLedger` fixes it.
- [ ] **Authentication.** `/ingest` is unauthenticated. It accepts data that
      steers money decisions — put it behind mTLS or a signed token before it
      touches anything real.
- [ ] **One process only.** The control plane holds all state in memory, so two
      replicas would each see half the traffic and disagree. Run one, or move
      state out first.
- [ ] **Calibrate the capacity curve.** `capacity.py` uses a plausible shape,
      not a measured one, and that curve is what sets the 80% shift cap. Fit it
      to your acquirers' real behaviour under load.
- [ ] **Re-run the sweeps against your fleet.** `sweep.py` chose the detector's
      operating point and `stress.py` chose the shift cap, both against the
      simulator. Your slice count, volume distribution and failure shapes will
      move both.
- [ ] **Alert delivery.** Escalations end in the ledger. Route them somewhere a
      human reads.

---

## Configuration

| variable | default | meaning |
|---|---|---|
| `REVENUEGUARD_TICK_SECONDS` | `60` | Seconds per control-plane tick. The detector's windows are counted in ticks, so lowering this for a demo compresses its baselines too. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | unset | Only for `revenueguard.execute --live`. Test-mode keys only; a `rzp_live_` key is refused. |
| `ANTHROPIC_API_KEY` | unset | Only for LLM narration. Absent, it falls back to the deterministic template. |

Policy bounds are in `PolicyConfig` (`revenueguard/policy.py`) rather than
environment variables, on purpose: changing one should be a reviewed commit with
the sweep that justifies it, not a restart with a different env var.
