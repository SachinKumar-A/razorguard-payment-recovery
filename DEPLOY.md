# Deploying RevenueGuard

**What is deployable today:** the control plane runs as a container, consumes
real payment outcomes over an authenticated endpoint, keeps durable state that
survives a restart, delivers escalations to a webhook, exposes Prometheus
metrics, and emits routing recommendations with a full audit trail.

**What it does not do:** apply its own decisions. Acquirer selection is not an
endpoint a third party can call, so RevenueGuard emits recommendations and
something on your side acts on them. Also, the numbers in the README come from a
simulator; pointing this at production tells you what it detects *there*, which
is not a thing this repository can claim in advance.

---

## Run it

```bash
cp .env.example .env          # set REVENUEGUARD_HMAC_KEY
docker compose up --build
```

- service → `http://localhost:8000` (`/docs` for OpenAPI)
- console → `http://localhost:8501`

Without Docker:

```bash
pip install -r requirements-service.txt
export REVENUEGUARD_HMAC_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
uvicorn revenueguard.service:app --host 0.0.0.0 --port 8000
```

The service **refuses to start without a credential**. Open is something you opt
into (`REVENUEGUARD_ALLOW_INSECURE=1`), not something you forget — the safe
configuration should not be the one you have to remember.

---

## Feeding it real data

The system only ever needed four things per slice per minute, which is why the
integration is small:

```bash
curl -X POST localhost:8000/ingest \
  -H "Authorization: Bearer $REVENUEGUARD_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"outcomes":[
        {"gateway":"gw_beta","method":"upi","issuer":"hdfc",
         "attempts":400,"successes":150},
        {"gateway":"gw_alpha","method":"upi","issuer":"hdfc",
         "attempts":600,"successes":580}]}'
```

Aggregated counts, not individual payments — deliberately. The detector never
needs a transaction record, so this integration never carries one, which keeps
both the blast radius and the data it holds uninteresting.

Post once a minute per slice from wherever you already aggregate: a stream
consumer, a scheduled query, or a webhook off existing monitoring.

**Late data is refused, not absorbed.** An outcome arriving for a minute the loop
has already processed is counted in `dropped_late` and dropped. Folding it into
the current minute would corrupt the trailing baseline the detector compares
against, which is a subtle way to make every subsequent number wrong. Alert on
`revenueguard_ingest_dropped_late_total`.

### Signed requests (preferred)

```python
import hmac, hashlib, json, time, requests

body = json.dumps({"outcomes": [...]}).encode()
ts = str(int(time.time()))
sig = hmac.new(KEY.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

requests.post("http://revenueguard:8000/ingest", data=body, headers={
    "Content-Type": "application/json",
    "X-RevenueGuard-Timestamp": ts,
    "X-RevenueGuard-Signature": sig,
})
```

The timestamp is bound into the signature and requests outside a 5-minute window
are refused in **both** directions — a far-future stamp is as suspicious as a
stale one.

### Replaying history

The fastest way to find out what this would have caught is to feed it a real
past incident:

```python
from revenueguard.config import METHOD_TICKET
from revenueguard.control_plane import ControlPlane
from revenueguard.detectors import default_detector
from revenueguard.ingest import CsvSource

source = CsvSource("incident.csv", lambda m: METHOD_TICKET.get(m, 1000.0))
plane = ControlPlane(None, default_detector(), source=source)
for event in plane.run(max(source.minutes) + 1).ledger:
    print(event.minute, event.kind, event.summary)
```

CSV columns: `minute, gateway, method, issuer, attempts, successes`.

---

## Reading it

| endpoint | purpose |
|---|---|
| `GET /health` | liveness; the container healthcheck uses it |
| `GET /state` | minute, alarms, actions, rollbacks, ingest and alert counters |
| `GET /routing` | **the output** — recommended weights per diverted key |
| `GET /audit` | the ledger from disk, refusals included (`?kind=`, `?limit=`) |
| `GET /metrics` | Prometheus |

`/routing` is what a caller consumes. Everything else is observability.

### Alerts worth having

| condition | why |
|---|---|
| `revenueguard_tick_errors_total` rising | the loop is failing; it keeps going, which is why you must watch it |
| `revenueguard_ingest_dropped_late_total` rising | a producer is behind; baselines are being starved |
| `revenueguard_open_diversions` stuck for hours | something diverted and never healed |
| `revenueguard_alerts_failed_total` rising | escalations are not reaching anyone |
| `revenueguard_minute` flat | the tick loop has stopped |

---

## Restarts

A restart replays the stored observation stream back through the detector,
restores the routing table, and re-adopts open diversions. Verified end to end:

```
restored: replayed=16 weights=72 diversions=1 next_minute=19 auth=token
```

Re-adopting diversions matters as much as restoring weights. Without it a
restart would leave traffic diverted with **nothing watching it** — no
supervisor to roll it back or ease it home. That is worse than either extreme.

The audit ledger continues its sequence across restarts rather than restarting
at 1, so it stays append-only across process boundaries and not merely within
one.

---

## Acting on the output

Not a technical decision.

1. **Human-in-the-loop.** Escalations go to on-call; a person changes weights.
   Slowest, safest, the right default for the first weeks.
2. **Semi-automatic.** Apply below some blast radius, escalate above it. The
   policy engine already draws that line — every `escalate` verdict is the
   system saying a human should look.
3. **Automatic.** Wire `/routing` to your traffic manager. Only once you have
   watched it long enough to trust the rollback path, because that path is what
   stops a bad recommendation compounding.

The audit ledger is identical under all three. What changes is who reads it
before money moves.

---

## Before production

Closed:

- [x] **Persistence** — SQLite in WAL mode, checkpointed every tick, state on a
      volume that outlives the container.
- [x] **Authentication** — HMAC request signing or bearer token; the service
      will not start without one.
- [x] **Alert delivery** — escalations and rollbacks to a webhook, off the
      loop's thread, bounded queue, deduplicated.
- [x] **Observability** — Prometheus metrics, JSON logs, per-tick duration.
- [x] **Graceful shutdown** — final checkpoint on the way out.

Still open, honestly:

- [ ] **Calibrate the capacity curve.** `capacity.py` uses a plausible shape,
      not a measured one, and that curve is what sets the 80% shift cap. Fit it
      to your acquirers' real behaviour under load. **The most load-bearing
      unknown in the project.**
- [ ] **Re-run the sweeps against your fleet.** `sweep.py` chose the detector's
      operating point and `stress.py` chose the shift cap, both against the
      simulator. Your slice count, volume distribution and failure shapes will
      move both.
- [ ] **Single process by design.** All state is in this process, so two
      replicas would each see half the ingest stream and disagree about the
      fleet. Run one. For HA the state has to move to Postgres and the loop
      needs leader election — a real piece of work, not a config change.
- [ ] **TLS.** Terminate at your ingress. The service speaks plain HTTP.
- [ ] **Back up the state volume.** Losing it costs a three-hour blind spot
      while baselines refill. Not fatal, but avoidable.

---

## Configuration

| variable | default | meaning |
|---|---|---|
| `REVENUEGUARD_HMAC_KEY` | — | Request-signing secret. Preferred. |
| `REVENUEGUARD_TOKEN` | — | Shared bearer token. Used if no HMAC key. |
| `REVENUEGUARD_ALLOW_INSECURE` | — | `1` to run with no auth. Private networks only. |
| `REVENUEGUARD_STATE` | `state/revenueguard.db` | SQLite path. `/data/...` in the image. |
| `REVENUEGUARD_TICK_SECONDS` | `60` | Seconds per tick. Detector windows are counted in ticks, so lowering it for a demo compresses baselines too. |
| `REVENUEGUARD_WARM_MINUTES` | `220` | History replayed on boot. |
| `REVENUEGUARD_ALERT_WEBHOOK` | — | Where escalations go. Unset disables alerting. |
| `REVENUEGUARD_LOG_LEVEL` | `INFO` | |

Policy bounds live in `PolicyConfig` (`revenueguard/policy.py`), not in
environment variables, on purpose: changing one should be a reviewed commit with
the sweep that justifies it, not a restart with a different value.
