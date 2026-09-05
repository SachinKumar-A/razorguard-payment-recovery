# Where the data comes from

Every number in this project is either **produced by running the code** or
**invented by me**. This document says exactly which is which, because a reader
deciding whether to believe the headline figure needs to know what it rests on.

Short version: **the world is invented, the measurement is real.** No result is
hardcoded, no output is mocked, and no number in the documentation is unverified
prose — `tests/test_documented_claims.py` fails if a figure in the README or
SUBMIT.md stops matching `bench/results/*.json`.

---

## 1. Invented: the world

Everything in this section is a made-up environment. None of it came from
Razorpay, from a public dataset, or from any real payment system. I chose these
values to be *plausible*, not to be *true*.

### The fleet — `razorguard/config.py`

| what | value | how it was chosen |
|---|---|---|
| Gateways | `gw_alpha`, `gw_beta`, `gw_gamma` | Deliberately fictional names. Naming them after real acquirers would imply knowledge of their behaviour I do not have. |
| Payment methods | `upi`, `card`, `netbanking` | The three that matter in India. Real categories, invented proportions. |
| Issuers | `hdfc`, `icici`, `sbi`, `axis`, `kotak`, `yes`, `idfc`, `rbl` | Real bank names, **entirely invented behaviour**. Nothing here reflects any real bank's actual success rate. |
| Method traffic split | UPI 62%, card 28%, netbanking 10% | Roughly matches the public direction of Indian payment mix. Not sourced. |
| Gateway traffic split | 50% / 32% / 18% | Invented. Chosen so one gateway is small enough that absorbing another's traffic hurts. |
| Issuer traffic split | 26% down to 3% | Invented, deliberately skewed so the tail issuers are thin enough to break naive statistics. |
| Healthy success rates | UPI 96.5%, card 91.8%, netbanking 89.2% | Invented, in the region industry commentary suggests. Not measured. |
| Per-issuer offsets | ±0.4 to −1.0 points | Invented, to stop every slice being identical. |
| Per-gateway offsets | +0.4 / 0.0 / −0.7 points | Invented. |
| Average ticket | UPI ₹640, card ₹2,150, netbanking ₹3,400 | **Invented.** These convert recovered transactions into rupees, so the headline rupee figure scales directly with them. |
| Baseline volume | 900 payments/minute | Invented. |
| Diurnal curve | 24 hourly multipliers, 0.09 to 1.52 | Invented shape for an Indian e-commerce day. |

**Consequence worth stating plainly:** the rupee headline is
`recovered transactions × invented ticket size`. The *transaction* count is
measured; the *rupee* conversion is not. A reader who distrusts the ticket sizes
should read the figure as **+11,452 successful payments** and
**+0.58 percentage points of success rate**, both of which are independent of
them.

### Congestion — `razorguard/capacity.py`

| parameter | value | status |
|---|---|---|
| Knee | 0.70 utilisation | **Invented.** |
| Slope past the knee | 0.35 | **Invented.** ~10% of success rate lost at 100% of capacity. |
| Floor | 0.55 | Invented. |
| Headroom | 1.6× peak baseline | Invented. |

This is the most consequential invention in the project, and it is the one thing
I could not close. `razorguard/sensitivity.py` exists specifically because of
it: it sweeps the shift cap against four different curves and reports whether
the conclusion survives. It mostly does — and it also found that on two of those
curves the system **loses money at every setting**, which is now the headline
limitation in both the README and DEPLOY.md.

### The incidents — `razorguard/scenarios.py`

18 injected degradations per two-day run: 4 hard outages, 4 gradual slides,
6 issuer-side faults, 4 shallow drops, placed at fixed times across the diurnal
cycle.

These are invented **on purpose, and that is the point of the design.** They are
not an attempt to model real outages; they are a schedule *we control*, which is
what makes time-to-detect a measurement rather than an opinion. See §3.

---

## 2. Real: everything that processes it

Nothing in this section is faked, stubbed, or shortcut.

| component | status |
|---|---|
| Beta-posterior detector with cohort shrinkage | Real statistics, real `scipy` |
| Fixed-threshold baseline | Real, and present so the sophisticated detector has something honest to beat |
| Union detector | Real; its operating point was chosen by the sweep, not by hand |
| Lift-with-coverage attribution | Real arithmetic |
| Policy engine | Real; all ten bounds enforced, every refusal recorded |
| Routing table, canary floor, rollback, restore | Real |
| Efficacy circuit breaker | Real; measures the realised effect of its own shifts |
| Audit ledger | Real, append-only, sequence continues across restarts |
| Persistence (SQLite/WAL), warm replay | Real; verified by restarting a live service |
| Authentication (HMAC / bearer) | Real; verified 401 / 401 / 200 against a running service |
| Alerting | Real HTTP delivery, bounded queue, deduplicated |
| Lease-based failover | Real; verified with two live instances |
| Prometheus metrics, JSON logs | Real |
| Confidence intervals | Real t-distribution arithmetic over 8 paired seeds |

### Razorpay integration — `razorguard/executor.py`

**Real code, never executed against Razorpay's servers.** It builds genuine
test-mode Order requests with the decision context in `notes` and calls the
official `razorpay` SDK. Dry run is the default and a key not beginning
`rzp_test_` is refused (two tests assert that refusal).

I had no Razorpay test credentials, so the live path has **never been run**.
Order IDs shown in the dry-run output are `order_DRYRUN000004`-style
placeholders, and are labelled `mode=dry_run` in every line so they can never be
mistaken for real ones.

### LLM narration — `razorguard/narrator.py`

Real Anthropic API code, **never executed with a live key in this project**. No
`ANTHROPIC_API_KEY` was available, so every narration you have seen came from
the deterministic template fallback. That fallback is what the audit ledger uses
in all cases by design, so nothing in any result depends on the model having run.

---

## 3. Why invented incidents make the measurement *more* trustworthy

This is the part that sounds backwards, so it is worth being explicit.

The usual way to evaluate a detector on synthetic data is circular: you write a
rule deciding which rows are "bad", label them, train a model to learn that rule,
then report how well it learned it. The number measures your generator.

**Nothing here learns a label.** The detector sees only
`(slice, minute, attempts, successes)` — no incident flags, no ground truth, and
`scenarios.py` is not importable from any detector. Because *we* scheduled each
outage, "time to detect" is latency against an event we caused: a measurement,
not an agreement score.

The same logic covers the recovery figure. It is the difference between two runs
of **bit-identical demand**, one with routing off and one on. The experiment
prints total attempts for both arms and **refuses to report a figure if they
differ**.

---

## 4. What would change with real data

| if you replaced | the effect |
|---|---|
| The ticket sizes | The rupee headline scales linearly. Transaction and percentage-point figures are unaffected. |
| The traffic mix and volumes | Detector sensitivity changes: thinner slices are harder, fatter ones easier. |
| The congestion curve | **Could change whether the system helps at all** — see the sensitivity sweep. |
| The incident mix | The confidence interval covers traffic randomness, not which incidents occur. A different mix moves the mean. |
| `world.py` entirely (real traffic via `/ingest`) | Everything downstream is unchanged. That seam is why the constraint "the loop only ever sees four numbers" was held from the first commit. |

---

## 5. How to check this yourself

```bash
pytest tests/test_documented_claims.py -q   # docs vs bench/results
grep -rn "scenarios" razorguard/detectors/ # empty: detectors cannot see ground truth
python -m razorguard.experiment --days 2   # regenerate the headline
python -m razorguard.validate --seeds 8    # regenerate the interval
python -m razorguard.sensitivity --days 2  # regenerate the limitation
```

Every JSON file under `bench/results/` is committed, so a reviewer can compare
what the documents claim against what the code last produced without running
anything.
