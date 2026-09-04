# RevenueGuard

**Detect payment degradation, route around it, prove what it recovered.**

Razorpay AI Buildathon — Track 03, AI Revenue Recovery.

A payment slice degrades — one issuer, on one method, through one gateway — and
money bleeds out for as long as nobody notices. RevenueGuard notices, works out
what the failing slices have in common, moves traffic to a healthier gateway
under an explicit policy, watches whether that helped, and rolls back when it
did not. Every action *and every refusal* is written to an audit ledger.

The figure it reports for money recovered is measured, not projected: the same
demand runs twice, once with routing off and once with it on, and the difference
is the answer — repeated across seeds, with a confidence interval.

---

## Result

**₹85,62,895 recovered per two days, 95% CI ₹82,28,635 – ₹88,97,155.**
26.6% ± 1.2% of the money the incidents put at risk. **Zero of eight seeds lost
money.**

```
python -m revenueguard.validate --seeds 8 --days 2
```

| across 8 paired seeds | mean | sd | min | max |
|---|---:|---:|---:|---:|
| revenue recovered | ₹85,62,895 | ₹3,99,759 | ₹79,88,780 | ₹91,02,180 |
| share of exposure | 26.6% | 1.2% | 24.7% | 28.2% |
| success rate gain | 0.394pp | 0.019 | 0.368 | 0.425 |
| routing actions | 127.2 | 6.7 | 113 | 135 |
| rollbacks | 29.4 | 2.7 | 26 | 35 |

A single seed cannot distinguish "the router recovers money" from "this
sequence of coin flips favoured the treatment arm", so the headline is the
interval, not one run. The comparison is **paired** — control and treatment face
bit-identical demand within each seed — which removes the traffic's own variance
and leaves the policy's effect.

One seed in detail (`--seed 7`, `python -m revenueguard.experiment`):

| | router off | router on | delta |
|---|---:|---:|---:|
| attempts | 1,966,458 | 1,966,458 | 0 |
| successful payments | 1,829,528 | 1,837,575 | **+8,047** |
| overall success rate | 93.04% | 93.45% | **+0.41pp** |
| revenue | ₹240.43 cr | ₹241.31 cr | **+₹87.3 L** |

Cross-checked two ways: measured recovery ₹87.3 L, while incident exposure
independently fell from ₹3.23 cr to ₹2.37 cr — ₹86.0 L. Different quantities,
1.5% apart.

```
routing actions taken            126
proposals blocked by policy        9
escalated to a human              67
rollbacks (target got worse)      29
restore steps (source healed)    488
audit events written           1,153
```

---

## Why the evaluation is trustworthy

Most detection projects built on synthetic data are circular: you write a rule
that decides which rows are bad, train a model to learn that rule, then report
how well it learned it. You have measured your own generator.

Nothing here learns a label.

**We schedule the outages.** For each incident we know the minute it began,
which slices it touched and how deep it went — and the detector sees none of
that. It sees `(slice, minute, attempts, successes)` and nothing else.

**Demand is identical across arms.** Every random draw is keyed by its own
coordinates — `(seed, minute, method, issuer)` for demand, `(seed, minute,
slice)` for outcomes — rather than pulled from one shared stream, which would
desynchronise the moment the two arms made different numbers of draws. The
experiment prints total attempts for both and **refuses to report a recovery
figure if they differ**.

**The control arm is not crippled.** It runs the same detector and raises the
same alarms; it simply may not act. Tests assert it takes zero routing actions
and that its weights never leave baseline.

---

## Run it

```bash
pip install -r requirements.txt

python -m revenueguard.validate   --seeds 8 --days 2   # the headline, with a CI
python -m revenueguard.experiment --days 2             # one seed, in detail
python -m revenueguard.demo                            # replay an incident
python -m revenueguard.demo --list
python -m revenueguard.bench      --days 2             # detector head-to-head
python -m revenueguard.sweep      --budget 1.0         # matched false-alarm curve
python -m revenueguard.narrate    --claude             # LLM note vs template
python -m revenueguard.execute    --limit 6            # Razorpay test mode, dry run
streamlit run app.py                                   # operator console

pytest -q                                              # 50 property tests
```

Deterministic under `--seed`. **No number in this README was typed by hand** —
each is printed by one of these commands.

---

## The detector, and an honest comparison

A slice carrying four attempts a minute swings between 50% and 100% on noise
alone, so a z-score on it is meaningless. And we run 207,360 tests over two
days, so at any fixed per-test significance level the false-alarm count scales
with the fleet and the alert channel drowns.

`PosteriorDropDetector` handles both by pooling. Each slice's current rate is a
Beta posterior whose prior is centred on its own method's pooled rate with
`prior_strength` pseudo-observations. A thin slice is dragged toward its cohort
and cannot alarm on three unlucky failures; a fat slice overwhelms the prior and
speaks for itself. It alarms only when `P(rate ≤ baseline − min_drop) ≥
confidence`. The baseline carries a 15-minute lag so a degradation in progress
cannot become the normal it is compared against.

Against a fixed-threshold rule — what an on-call engineer writes in an afternoon
— at a matched budget of ≤1 false alarm per 1,000 slice-hours:

| detector | detected | median time to detect |
|---|---|---|
| `fixed_threshold` (floor 0.70, 3 min) | **16 / 18** | 6.5 min |
| `posterior_drop` (8pp, conf 0.90) | 12 / 18 | **3.5 min** |

**Neither dominates.** The dumb rule catches four more; the posterior detector
is nearly twice as fast on what it catches. The misses are all *shallow*
degradations — a threshold choice, not a modelling failure.

At its shipped default the fixed rule reaches 18/18 in 3.5 min — while firing
581 false alarms, **168 per 1,000 slice-hours**. It is unusable, and the number
proving it is in the table. Comparing detectors at whatever thresholds they ship
with is how that gets hidden.

---

## Where AI is used, and where it is refused

Root-cause attribution is **deterministic**: lift with coverage over the
alarming population, ranked so an explanation missing half the alarms cannot win
however high its lift.

The LLM (`narrator.py`, Claude Opus 5) is handed the *finished* attribution —
dimension, coverage, lift, slice counts — and asked to write it as English for
the operator console. It never sees raw traffic, is never asked what caused the
incident, and its output is never read by anything that decides. A test asserts
the prompt contains no slice keys and no per-minute counts; another asserts that
`policy.py`, `routing.py`, `control_plane.py` and `audit.py` do not import the
narrator at all. **The ledger always keeps the deterministic template**, because
a ledger whose text changes when you re-read it is not a ledger.

Enabled with `--claude`, off by default, and safe either way: no key, a network
error, a refusal and an empty completion all fall back to the template. The
handler is deliberately broad — with no credential the SDK raises a bare
`TypeError` from header construction, which a typed `except anthropic.*` chain
sails straight past and turns a cosmetic feature into a crash mid-incident. It
also disables itself after the first failure, so a missing key costs one failed
round trip rather than one per alarming slice.

The attribution also declines to overclaim. Below five alarming slices it
refuses to name a secondary dimension and says so:

```
Success rate on gateway 'gw_gamma' fell 20.6 points (91.4% to 70.8%).
It spans 3 of 3 alarming slices. Too few slices have alarmed to narrow
it below the gateway yet.
```

A test asserts the phrase `peers unaffected` cannot appear on three alarms.

---

## Bounded, gated, reversible

The detector decides *whether something is wrong*. The policy engine decides
*whether we may act on it*. A confident detector is not authorisation to move
money.

| rule | bound |
|---|---|
| `min_confidence` | 0.95 posterior confidence before money moves |
| `min_drop_pp` | ignore drops under 4pp |
| `max_shift_fraction` | ≤40% of the source gateway's share, per action |
| `max_cumulative_divergence` | ≤60% of a key away from baseline at once |
| `action_cooldown` | 15 min before the same key is touched again |
| `max_causes_per_hour` | 4 distinct root causes, then **escalate** |
| `max_actions_per_hour` | 60 weight changes — the hard ceiling |
| `min_target_advantage_pp` | destination must be ≥8pp healthier |
| `no_healthy_destination` | every route degraded ⇒ escalate, never shuffle |
| `min_target_attempts` | 60 attempts before a destination's health is trusted |

**The anti-oscillation budget counts causes, not weight changes.** One gateway
outage legitimately requires shifting every (method, issuer) key that gateway
served — two dozen of them — and charging that fan-out against an oscillation
budget conflates "the system is thrashing" with "one incident was wide". An
earlier design counted actions, spent its whole hourly budget on the first
outage and escalated everything after it; switching to causes (with a hard
ceiling behind it) took recovery from 23.1% of exposure to 27.0%.

Two exits matter as much as the entry:

**The canary.** A drained gateway emits no observations, so you lose the ability
to tell whether it recovered — and can never route back. Weights never fall
below a 3% floor, and that trickle is the only reason recovery is observable.

**Rollback.** Both sides stay under watch through the same pooled estimator that
chose the destination. If it falls more than 6pp below its own baseline on
trustworthy volume, weights reset and the incident escalates. Recovery eases
home in thirds, because slamming full traffic onto a gateway that has only just
recovered is how a flap becomes an outage.

### Reading health when there is barely any traffic

At 03:00 a slice may see four attempts in five minutes — not enough to route on.
Refusing outright left overnight outages unattended, so the estimator widens its
lens in declared steps:

```
slice/5min  ->  slice/20min  ->  gateway+method  ->  gateway
```

Each step charges a **coarseness penalty** on the advantage a destination must
show (0 / 1 / 3 / 5 pp), because pooling across issuers can hide an
issuer-specific fault on the destination. The level used is written into the
audit entry. This took the two 03:10 outages from **0 routing actions to 11 and
8**.

### Issuer-side faults

Six of eighteen incidents receive zero routing actions, and that is correct.
`issuer_degradation` hits every gateway serving that issuer at once — all routes
terminate at the same bank, so no healthy destination exists. The system
recognises the signature and **escalates under `no_healthy_destination`** rather
than shuffling traffic between equally broken routes to look busy.

---

## Executing against Razorpay

Razorpay's public API does not expose gateway routing weights to a third party —
acquirer selection is Razorpay's own product (Optimizer), not a merchant
endpoint. A project claiming to "reroute traffic through the Razorpay API" is
describing something that does not exist.

So routing stays inside the simulation where it can be measured, and
`executor.py` demonstrates the half that *is* real: each recovery decision
creates a genuine **test-mode Order** carrying the decision context in `notes` —
audit sequence, source and destination gateway, the reason — so it is auditable
from the Razorpay dashboard, outside this repo.

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
  detectors/        fixed-threshold baseline; Beta-posterior drop test
  rootcause.py      deterministic lift-with-coverage attribution
  narrator.py       optional LLM prose over that attribution; template fallback
  policy.py         the bounds, the stopping rules, the reasons
  routing.py        weight table, canary floor, gradual restore
  control_plane.py  observe -> detect -> attribute -> gate -> act -> verify -> roll back
  audit.py          append-only ledger, refusals included
  executor.py       Razorpay test-mode execution, dry run by default
  metrics.py        TTD, false alarms per 1k slice-hours, exposure
  bench.py sweep.py detector benchmarks
  experiment.py     control vs treatment - one seed
  validate.py       paired multi-seed validation with a confidence interval
  demo.py narrate.py execute.py
tests/              50 property tests
```

`pyflakes` clean. No number claimed that a command here does not print.

---

## Limits

Stated because they are the first things worth asking about.

- **The world is simulated.** Gateway health, demand and incidents are ours.
  What is *not* ours is the detector's view of them, which is the part under
  evaluation. A real deployment replaces `world.py` and changes nothing else.
- **Gateway health does not depend on load.** A real acquirer degrades further
  as you push traffic onto it, which would make aggressive shifting
  self-defeating in a way this simulation cannot punish. The most important
  missing dynamic, and the first thing I would add next.
- **27% of exposure, not 90%.** Detection latency and the deliberate caps set
  that ceiling. Loosening either raises the number and lowers the confidence it
  deserves.
- **~29 rollbacks against ~127 actions.** Every one is a case where the
  destination turned out worse and the system undid itself. Reported rather than
  tuned away; driving it to zero would mean acting only on certainties, which
  costs more money than the rollbacks do.
- **Eight seeds, one incident plan.** The confidence interval covers traffic
  randomness, not the choice of which incidents occur. A different mix of
  outage shapes would move the mean.
