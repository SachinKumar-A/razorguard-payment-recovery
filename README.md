# RevenueGuard

**Detect payment degradation, route around it, prove what it recovered.**

Razorpay AI Buildathon — Track 03, AI Revenue Recovery.

A payment slice degrades — one issuer, on one method, through one gateway — and
money bleeds out for as long as nobody notices. RevenueGuard notices, works out
what the failing slices have in common, moves traffic to a healthier gateway
under an explicit policy, watches whether that helped, and rolls back when it
did not. Every action *and every refusal* is written to an audit ledger.

The figure it reports for money recovered is a measurement, not a projection:
the same demand is run twice, once with routing off and once with it on, and the
difference is the answer.

---

## Result

Two simulated days. 1,966,458 payment attempts, 18 injected incidents, 72 slices
(3 gateways × 3 methods × 8 issuers).

| | router off | router on | delta |
|---|---:|---:|---:|
| attempts | 1,966,458 | 1,966,458 | 0 |
| successful payments | 1,829,528 | 1,836,933 | **+7,405** |
| overall success rate | 93.04% | 93.41% | **+0.38pp** |
| revenue | ₹240.43 cr | ₹241.18 cr | **+₹74.5 L** |

**₹74,45,470 recovered — 23.1% of the ₹3.23 cr the incidents put at risk.**

Two independent calculations agree, which is the check worth trusting: measured
recovery came to ₹74.5 L, while incident exposure fell from ₹3.23 cr to
₹2.49 cr — a drop of ₹74.1 L. They are computed from different quantities and
land within 0.5% of each other.

```
routing actions taken            100
proposals blocked by policy        9
escalated to a human              96
rollbacks (target got worse)      25
restore steps (source healed)    431
audit events written           1,078
```

Refusals, by the rule that fired: `max_actions_per_hour` 59,
`no_healthy_destination` 12, `min_target_advantage` 8, `action_cooldown` 1.

The 77% not recovered is structural and stated rather than hidden: money lost
before detection fires, money the policy declines to chase (no more than 40% of
a gateway's share moves per action, and a 3% canary always stays behind), and
incidents routing genuinely cannot fix — see *Issuer-side faults* below.

---

## Why the evaluation is trustworthy

Most detection projects built on synthetic data are circular: you write a rule
that decides which rows are bad, train a model to learn that rule, then report
how well it learned it. You have measured your own generator.

Nothing here learns a label.

**We schedule the outages.** For each incident we know the minute it began,
which slices it touched and how deep it went — and the detector sees none of
that. It sees `(slice, minute, attempts, successes)` and nothing else. Time to
detect is latency against an event we caused.

**Demand is identical across the two runs.** Every random draw is keyed by its
own coordinates — `(seed, minute, method, issuer)` for demand, `(seed, minute,
slice)` for outcomes — rather than pulled from one shared stream, which would
desynchronise the moment the two runs made different numbers of draws. The
experiment prints total attempts for both arms and **refuses to report a
recovery figure if they differ**.

**The control arm is not crippled.** It runs the same detector and raises the
same alarms. It simply is not permitted to act. A test asserts it takes zero
routing actions and that its weights never leave baseline.

---

## Run it

```bash
pip install -r requirements.txt

# the headline: money recovered, control vs treatment
python -m revenueguard.experiment --days 2 \
    --json bench/results/experiment.json --audit bench/results/audit.jsonl

# one incident replayed minute by minute, with the decisions beside it
python -m revenueguard.demo
python -m revenueguard.demo --list
python -m revenueguard.demo --incident INC-0-01

# detector benchmark and the operating-point sweep behind its settings
python -m revenueguard.bench --days 2
python -m revenueguard.sweep --days 2 --budget 1.0

# execution path against Razorpay test mode (dry run by default)
python -m revenueguard.execute --limit 6

# operator console
streamlit run app.py

pytest -q          # 40 property tests
```

Everything is deterministic under `--seed`. **No number in this README was typed
by hand** — each is printed by one of the commands above.

---

## The detector, and an honest comparison

Two problems make per-slice monitoring hard. A slice carrying four attempts a
minute swings between 50% and 100% on noise alone, so a z-score on it is
meaningless. And we run 207,360 tests over two days, so at any fixed per-test
significance level the false-alarm count scales with the fleet and the alert
channel drowns.

`PosteriorDropDetector` handles both by pooling. Each slice's current rate is a
Beta posterior whose prior is centred on its own method's pooled rate with
`prior_strength` pseudo-observations. A thin slice is dragged toward its cohort
and cannot alarm on three unlucky failures; a fat slice overwhelms the prior and
speaks for itself. It alarms only when `P(rate ≤ baseline − min_drop) ≥
confidence`. The baseline carries a 15-minute lag so a degradation in progress
cannot become the normal it is compared against.

Against a fixed-threshold rule — the thing an on-call engineer writes in an
afternoon — at a matched budget of ≤1 false alarm per 1,000 slice-hours:

| detector | detected | median time to detect |
|---|---|---|
| `fixed_threshold` (floor 0.70, 3 min) | **16 / 18** | 6.5 min |
| `posterior_drop` (8pp, conf 0.90) | 12 / 18 | **3.5 min** |

**Neither dominates.** The dumb rule catches four more incidents; the posterior
detector is nearly twice as fast on the ones it catches. That is the honest
result and it is more useful than a flattering one — it says the misses are all
*shallow* degradations, which is a threshold choice rather than a modelling
failure.

At its shipped default the fixed rule reaches 18/18 in 3.5 min — while firing
581 false alarms, **168 per 1,000 slice-hours**. It is unusable, and the number
proving it is in the table. Comparing detectors at whatever thresholds they
happen to ship with is how that gets hidden.

---

## Where AI is used, and where it is refused

Root-cause attribution is **deterministic**: lift with coverage over the
alarming population, ranked so that an explanation missing half the alarms
cannot win however high its lift.

An LLM is never asked whether there is an outage, what caused it, or whether to
act. That would put a sampled token in the path of a money-moving decision. It
is handed the finished attribution and asked to write it in English, for the
operator console only — the ledger keeps the deterministic template, because a
ledger that reads differently on re-run is not a ledger.

The attribution also declines to overclaim. Below five alarming slices it
refuses to name a secondary dimension at all, and says so:

```
Success rate on gateway 'gw_gamma' fell 20.6 points (91.4% to 70.8%).
It spans 3 of 3 alarming slices. Too few slices have alarmed to narrow
it below the gateway yet.
```

A test asserts the phrase `peers unaffected` cannot appear on three alarms.

---

## Bounded, gated, reversible

The detector decides *whether something is wrong*. The policy engine decides
*whether we are permitted to act on it*. A confident detector is not
authorisation to move money.

| rule | bound |
|---|---|
| `min_confidence` | 0.95 posterior confidence before money moves |
| `min_drop_pp` | ignore drops under 4pp |
| `max_shift_fraction` | ≤40% of the source gateway's share, per action |
| `max_cumulative_divergence` | ≤60% of a key away from baseline at once |
| `action_cooldown` | 15 min before the same key is touched again |
| `max_actions_per_hour` | 12, then **escalate** — a stopping rule, not a delay |
| `min_target_advantage_pp` | destination must be ≥8pp healthier |
| `no_healthy_destination` | every route degraded ⇒ escalate, never shuffle |
| `forbid_alarmed_target` | never route into a gateway that is itself alarming |
| `min_target_attempts` | 60 attempts before a destination's health is trusted |

Two exits matter as much as the entry:

**The canary.** A drained gateway emits no observations, so you lose the ability
to tell whether it recovered — and can never route back. Weights never fall
below a 3% floor, and that trickle is the only reason recovery is observable.

**Rollback.** After diverting, both sides stay under watch through the same
pooled estimator used to choose the destination. If it falls more than 6pp below
its own baseline on trustworthy volume, weights reset and the incident
escalates. Recovery eases home in thirds, because slamming full traffic onto a
gateway that has only just recovered is how a flap becomes an outage.

### Reading health when there is barely any traffic

At 03:00 a single slice may see four attempts in five minutes — not enough to
route on. Refusing outright left overnight outages completely unattended, so the
estimator widens its lens in declared steps:

```
slice/5min  ->  slice/20min  ->  gateway+method  ->  gateway
```

and each step charges a **coarseness penalty** on the advantage the destination
must show (0 / 1 / 3 / 5 pp), because pooling across issuers can hide an
issuer-specific fault on the destination. The level used is written into the
audit entry. Adding this took the two 03:10 gateway outages from **0 routing
actions to 11 and 8**.

### Issuer-side faults

Six of eighteen incidents receive zero routing actions, and that is correct.
`issuer_degradation` hits every gateway serving that issuer at once — there is
no healthy destination, because all routes terminate at the same bank. The
system detects it, recognises the signature, and **escalates under
`no_healthy_destination` (12 times)** rather than shuffling traffic between
equally broken routes to look busy.

---

## Executing against Razorpay

Razorpay's public API does not expose gateway routing weights to a third party —
acquirer selection is Razorpay's own product (Optimizer), not a merchant
endpoint. A project claiming to "reroute traffic through the Razorpay API" is
describing something that does not exist.

So routing stays inside the simulation where it can be measured, and
`revenueguard/executor.py` demonstrates the half that *is* real: each recovery
decision creates a genuine **test-mode Order** carrying the full decision
context in `notes` — audit sequence, source and destination gateway, the reason
— so the decision is auditable from the Razorpay dashboard, outside this repo.

Dry run is the default and needs no credentials. Live calls require both
environment variables *and* `--live`, and a key not beginning `rzp_test_` is
refused outright. Two tests assert that refusal.

---

## Layout

```
app.py              operator console (streamlit)
revenueguard/
  config.py         the fleet: gateways, methods, issuers, volumes, tickets
  scenarios.py      injected degradations - ground truth, unreadable by detectors
  simulator.py      open-loop traffic, for detector benchmarking
  world.py          closed-loop traffic: demand, routing, health kept separate
  detectors/
    threshold.py    the fixed-threshold baseline
    sequential.py   Beta-posterior drop test with cohort shrinkage
  rootcause.py      deterministic lift-with-coverage attribution
  policy.py         the bounds, the stopping rules, the reasons
  routing.py        weight table, canary floor, gradual restore
  control_plane.py  observe -> detect -> attribute -> gate -> act -> verify -> roll back
  audit.py          append-only ledger, refusals included
  executor.py       Razorpay test-mode execution, dry run by default
  metrics.py        TTD, false alarms per 1k slice-hours, exposure
  bench.py          detector head-to-head
  sweep.py          operating points, matched false-alarm comparison
  experiment.py     control vs treatment - the recovery number
  execute.py        execution CLI
  demo.py           incident replay
tests/              40 property tests
```

~3,400 lines. No number claimed that a command in this repo does not print.

---

## Limits

Stated because they are the first things worth asking about.

- **The world is simulated.** Gateway health, demand and incidents are ours.
  What is *not* ours is the detector's view of them, which is the part under
  evaluation. A real deployment replaces `world.py` and changes nothing else.
- **23% of exposure, not 90%.** Detection latency and the deliberate policy caps
  set that ceiling. Loosening either raises the number and lowers the confidence
  it deserves.
- **25 rollbacks against 100 actions.** Every one is a case where the
  destination turned out worse and the system undid itself. Reported rather than
  tuned away; driving it to zero would mean acting only on certainties, which
  costs more money than the rollbacks do.
- **Gateway health does not depend on load in this world.** A real acquirer
  degrades further as you push traffic onto it, which would make aggressive
  shifting self-defeating in a way this simulation cannot punish. That is the
  most important missing dynamic.
