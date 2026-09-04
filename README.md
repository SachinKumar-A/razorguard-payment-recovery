# RevenueGuard

**Detect payment degradation, route around it, prove what it recovered.**

Razorpay AI Buildathon — Track 03, AI Revenue Recovery.

A payment slice degrades — one issuer, on one method, through one gateway — and
money bleeds out for as long as nobody notices. RevenueGuard notices, works out
what the failing slices have in common, moves traffic to a healthier gateway
under an explicit written policy, verifies whether that helped, and rolls back
when it did not. Every action *and every refusal* is written to an audit ledger.

The figure it reports for money recovered is measured, not projected: the same
demand runs twice, once with routing off and once with it on, and the difference
is the answer — repeated across seeds, with a confidence interval, in a world
where gateways get worse as you push traffic at them.

---

## Result

**₹1,23,56,984 recovered per two days, 95% CI ₹1,18,56,612 – ₹1,28,57,356.**
38.4% ± 1.9% of the money the incidents put at risk. **Zero of eight seeds lost
money.**

```
python -m revenueguard.validate --seeds 8 --days 2
```

| across 8 paired seeds | mean | sd | min | max |
|---|---:|---:|---:|---:|
| revenue recovered | ₹1,23,56,984 | ₹5,98,421 | ₹1,12,78,430 | ₹1,31,17,900 |
| share of exposure | 38.4% | 1.9% | 35.6% | 41.0% |
| success rate gain | 0.578pp | 0.020 | 0.542 | 0.604 |
| routing actions | 103.1 | 5.7 | 98 | 114 |
| rollbacks | 39.2 | 4.1 | 34 | 47 |

A single seed cannot distinguish "the router recovers money" from "this sequence
of coin flips favoured the treatment arm", so the headline is the interval. The
comparison is **paired** — control and treatment face bit-identical demand within
each seed.

One seed in detail (`python -m revenueguard.experiment`):

| | router off | router on | delta |
|---|---:|---:|---:|
| attempts | 1,966,458 | 1,966,458 | 0 |
| successful payments | 1,829,522 | 1,841,236 | **+11,714** |
| overall success rate | 93.04% | 93.63% | **+0.60pp** |
| revenue | ₹240.43 cr | ₹241.67 cr | **+₹1.23 cr** |

Cross-checked independently: measured recovery ₹1.23 cr, while incident exposure
fell ₹1.32 cr. The ~7% gap is itself informative — it is largely the congestion
the router *causes* at the destination, which costs revenue without reducing
incident exposure. Before gateways had capacity limits these two numbers agreed
within 1.5%; the divergence appeared the moment loading a gateway had a price.

---

## Two things the measurements changed

Both were designed one way and rebuilt after the numbers disagreed.

### The policy caps were costing 61% of the recovery

`max_shift_fraction` was set to 40% a priori — the cautious choice. The
hypothesis was that `capacity.py` would justify it: gateways degrade under load,
so shifting harder should congest the destination and stop paying. `stress.py`
measured that and it was wrong.

```
python -m revenueguard.stress --days 2
```

| shift cap | recovered | of exposure | actions | rollbacks | peak u | congested |
|---:|---:|---:|---:|---:|---:|---:|
| 20% | ₹41,47,580 | 12.8% | 134 | 26 | 0.74 | 0.2% |
| 40% | ₹78,20,670 | 24.2% | 128 | 32 | 0.79 | 0.5% |
| 60% | ₹1,04,52,600 | 32.4% | 115 | 32 | 0.78 | 2.1% |
| **80%** | **₹1,23,25,460** | **38.2%** | 114 | 39 | 0.83 | 2.7% |
| 100% | ₹1,27,00,650 | 39.3% | 91 | 37 | 0.87 | 4.3% |

The cap bound hard and the congestion it guarded against never arrived — at
100%, the destination lost 707 payments to load against ~11,700 recovered. The
default moved to **80%**, the knee: it captures nearly everything available at
100% (38.2% against 39.3%) with less congestion. Going to 100% buys 3% more
recovery and is not obviously worth it.

Re-run the sweep after touching the capacity model; that curve is what sets the
constant.

### The detector ensemble beats both members

The benchmark already showed neither detector dominated: at a matched
false-alarm budget the fixed rule caught more incidents, the posterior detector
was faster. That is precisely when a union is worth building.

At ≤1 false alarm per 1,000 slice-hours:

| detector | detected | median time to detect |
|---|---|---|
| `fixed_threshold` (floor 0.70, 3 min) | 16 / 18 | 6.5 min |
| `posterior_drop` (8pp, conf 0.90) | 12 / 18 | 3.5 min |
| **`union` (floor 0.70 + 5pp/0.99)** | **16 / 18** | **5.0 min** |

Same detection as the best single detector, 1.5 minutes faster. Both members run
tighter inside the union than they would alone, because false alarms add across
members — which is why the comparison only means anything at a matched budget.
Shipped as `detectors.default_detector()`.

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
desynchronise the moment the arms made different numbers of draws. The
experiment prints total attempts for both and **refuses to report a recovery
figure if they differ**.

**Gateways are not infinitely elastic.** `capacity.py` degrades a gateway's
success rate past a utilisation knee, so the router's own actions have a price
and diverting traffic onto a healthy destination can congest it. This is what
finally exercises the rollback path against something other than noise.

**The control arm is not crippled.** It runs the same detector and raises the
same alarms; it simply may not act. Tests assert it takes zero routing actions.

---

## Run it

```bash
pip install -r requirements.txt

python -m revenueguard.validate   --seeds 8 --days 2   # the headline, with a CI
python -m revenueguard.experiment --days 2             # one seed, in detail
python -m revenueguard.stress     --days 2             # are the caps costing money?
python -m revenueguard.demo                            # replay an incident
python -m revenueguard.bench      --days 2             # detector head-to-head
python -m revenueguard.sweep      --budget 1.0         # matched false-alarm curve
python -m revenueguard.narrate    --claude             # LLM note vs template
python -m revenueguard.execute    --limit 6            # Razorpay test mode, dry run
streamlit run app.py                                   # operator console

pytest -q                                              # 67 property tests
```

Deterministic under `--seed`. **No number in this README was typed by hand.**

---

## The detector

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

At its shipped default the *fixed* rule reaches 18/18 in 3.5 min — while firing
581 false alarms, **168 per 1,000 slice-hours**. It is unusable, and the number
proving it is in the sweep. Comparing detectors at whatever thresholds they ship
with is how that gets hidden.

---

## Where AI is used, and where it is refused

Root-cause attribution is **deterministic**: lift with coverage over the
alarming population, ranked so an explanation missing half the alarms cannot win
however high its lift.

The LLM (`narrator.py`, Claude Opus 5) is handed the *finished* attribution and
asked to write it as English for the operator console. It never sees raw
traffic, is never asked what caused the incident, and its output is never read
by anything that decides. A test asserts the prompt contains no slice keys and
no per-minute counts; another asserts that `policy.py`, `routing.py`,
`control_plane.py` and `audit.py` do not import the narrator at all. **The
ledger always keeps the deterministic template**, because a ledger whose text
changes when you re-read it is not a ledger.

Off by default, and safe either way: no key, a network error, a refusal and an
empty completion all fall back to the template. The handler is deliberately
broad — with no credential the SDK raises a bare `TypeError` from header
construction, which a typed `except anthropic.*` chain sails past and turns a
cosmetic feature into a crash mid-incident. It disables itself after the first
failure, so a missing key costs one failed round trip rather than one per alarm.

Attribution also declines to overclaim. Below five alarming slices it refuses to
name a secondary dimension and says so:

```
Success rate on gateway 'gw_gamma' fell 20.6 points (91.4% to 70.8%).
It spans 3 of 3 alarming slices. Too few slices have alarmed to narrow
it below the gateway yet.
```

---

## Bounded, gated, reversible

The detector decides *whether something is wrong*. The policy engine decides
*whether we may act on it*. A confident detector is not authorisation to move
money.

| rule | bound |
|---|---|
| `min_confidence` | 0.95 posterior confidence before money moves |
| `min_drop_pp` | ignore drops under 4pp |
| `max_shift_fraction` | ≤80% of the source gateway's share, per action |
| `max_cumulative_divergence` | ≤90% of a key away from baseline at once |
| `action_cooldown` | 15 min before the same key is touched again |
| `max_causes_per_hour` | 4 distinct root causes, then **escalate** |
| `max_actions_per_hour` | 60 weight changes — the hard ceiling |
| `min_target_advantage_pp` | destination must be ≥8pp healthier |
| `no_healthy_destination` | every route degraded ⇒ escalate, never shuffle |
| `min_target_attempts` | 60 attempts before a destination's health is trusted |

**The anti-oscillation budget counts causes, not weight changes.** One gateway
outage legitimately requires shifting every (method, issuer) key that gateway
served, and charging that fan-out against an oscillation budget conflates "the
system is thrashing" with "one incident was wide". An earlier design counted
actions, spent its whole hourly budget on the first outage and escalated
everything after it.

**The canary.** A drained gateway emits no observations, so you lose the ability
to tell whether it recovered — and can never route back. Weights never fall
below a 3% floor, and that trickle is the only reason recovery is observable.

**Rollback.** Both sides stay under watch through the same pooled estimator that
chose the destination. If it falls more than 6pp below its own baseline on
trustworthy volume, weights reset and the incident escalates. Recovery eases
home in thirds.

### Reading health when there is barely any traffic

At 03:00 a slice may see four attempts in five minutes. Refusing outright left
overnight outages unattended, so the estimator widens in declared steps:

```
slice/5min  ->  slice/20min  ->  gateway+method  ->  gateway
   +0pp            +1pp             +3pp             +5pp
```

Each step charges a **coarseness penalty** on the advantage a destination must
show, because pooling across issuers can hide an issuer-specific fault there.
The level used is written into the audit entry. This took the two 03:10 outages
from **0 routing actions to 11 and 8**.

### Issuer-side faults

Several incidents receive zero routing actions, and that is correct.
`issuer_degradation` hits every gateway serving that issuer at once — all routes
terminate at the same bank, so no healthy destination exists. The system
recognises the signature and **escalates under `no_healthy_destination`** rather
than shuffling traffic between equally broken routes.

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
from the Razorpay dashboard.

Dry run is the default. Live calls require both environment variables *and*
`--live`, and a key not beginning `rzp_test_` is refused outright. Two tests
assert that refusal.

---

## Layout

```
app.py              operator console (streamlit)
revenueguard/
  config.py         the fleet: gateways, methods, issuers, volumes, tickets
  capacity.py       gateways degrade under load; the router's actions have a price
  scenarios.py      injected degradations - ground truth, unreadable by detectors
  simulator.py      open-loop traffic, for detector benchmarking
  world.py          closed-loop traffic: demand, routing, health, congestion
  detectors/        fixed threshold; Beta-posterior drop; the shipped union
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
  stress.py         does shifting harder recover more?
  demo.py narrate.py execute.py
docs/               the two project PDFs, generated from bench/results
tests/              67 property tests
```

`pyflakes` clean.

---

## Limits

- **The world is simulated.** Gateway health, demand, incidents and the
  congestion curve are ours. What is *not* ours is the detector's view of them,
  which is the part under evaluation. A deployment replaces `world.py`.
- **The congestion curve is a plausible shape, not a measured one.** Knee at 70%
  utilisation, ~10% success-rate loss at 100% of capacity. It was chosen before
  running the sweep, but a real acquirer's curve would move the 80% cap.
- **38% of exposure, not 90%.** Detection latency and the remaining caps set
  that ceiling.
- **~39 rollbacks against ~103 actions.** Each is a case where the destination
  proved worse and the system undid itself. Reported rather than tuned away.
- **Eight seeds, one incident plan.** The interval covers traffic randomness,
  not the choice of which incidents occur.
