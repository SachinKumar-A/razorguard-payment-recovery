# Submission

Razorpay AI Buildathon — **Track 03, AI Revenue Recovery**.

Everything the brief asks for: a public repo, a 5-minute pitch video, and the
architecture. This file is the checklist, the text to paste into the form, the
video script, and the answers to the questions a panel will actually ask.

---

## 1. Pre-flight

Run these before recording anything. All four must pass from a **fresh clone**,
because that is what a reviewer will have.

```bash
git clone <your-repo-url> && cd razorpay
pip install -r requirements-dev.txt

pytest -q                                    # expect: 202 passed
python -m revenueguard.validate --seeds 8 --days 2
python -m revenueguard.experiment --days 2
streamlit run app.py                         # expect: console loads
```

- [ ] 202 tests pass
- [ ] `validate` reports a CI that excludes zero and 0/8 losing seeds
- [ ] `experiment` prints **identical attempts in both arms** (if it does not,
      it refuses to report a figure — that is the guard working, but fix it
      before submitting)
- [ ] console loads at `localhost:8501`
- [ ] `docker compose up --build` brings up service (`:8000/health`) and console
- [ ] `bench/results/*.json` are committed, so a reviewer sees every number
      without running anything
- [ ] repo is **public**

---

## 2. What to put in the form

**Track:** 03 — AI Revenue Recovery (use *Apply for this track* on that panel).

**One-line description**

> A control plane that detects per-slice payment degradation, attributes it,
> reroutes traffic under an explicit policy, and measures the money recovered
> against a control arm rather than projecting it.

**Longer description, if there is a box for it**

> Payment failures are almost never global — they are narrow, one gateway on one
> method for one issuing bank, and a narrow failure is invisible on the chart
> everyone watches. RevenueGuard monitors at that grain, which means running
> ~207,000 statistical tests over two days against slices thin enough that a
> z-score is meaningless. It handles both problems by pooling: each slice's rate
> is a Beta posterior shrunk toward its method's cohort.
>
> Detection is separated from permission. A policy engine with ten written
> bounds decides whether the system may act, records every refusal with the rule
> that caused it, keeps a 3% canary on every drained gateway so recovery stays
> observable, and rolls back when the destination proves worse.
>
> The recovery figure is measured, not projected: the same demand runs twice,
> once with routing off and once on, across eight paired seeds. **₹1,22,91,379**
> recovered per two simulated days, 95% CI ₹1,17,94,730 – ₹1,27,88,027, 38.2% of
> the money the injected incidents put at risk, zero of eight seeds losing money.
>
> Three design decisions were overturned by their own measurements and all three
> are documented: the anti-oscillation budget was counting the wrong thing, the
> shift cap was costing 61% of the available recovery, and a sensitivity sweep
> found gateway fleets where rerouting loses money at every setting — which is
> why the system now measures the realised effect of its own actions and halts
> when they stop paying.

**Repo:** `<your-repo-url>` · **Video:** `<your-video-url>`
**Architecture:** `docs/RevenueGuard-Complete-Documentation.pdf` (sections 8–9),
or the README.

---

## 3. The video — 5 minutes

Screen recording with voice. No slides. Every command below is real and runs.

**0:00–0:30 — the problem, concretely**

> "A payment slice degrades — one issuer, one method, one gateway. If that slice
> is 4% of volume and it collapses from 96% to 40%, the headline success rate
> moves two points. That's inside normal daily variation. Nobody pages, and the
> money leaves quietly."

**0:30–1:45 — watch it happen**

```bash
python -m revenueguard.demo --incident INC-0-01
```

Talk over the trace as it scrolls. Point at, in order: the detection line and
its root-cause sentence; a **proposal**; the **policy decision** with its rule;
the **action**. Then find a refusal and read it aloud — that is the moment that
separates this from a dashboard.

> "That's a blocked one. The policy engine refused because the destination only
> had 58 recent attempts — not enough to trust its health. The refusal is in the
> ledger with the rule that caused it."

**1:45–2:45 — the measurement, and why it can be trusted**

```bash
python -m revenueguard.experiment --days 2
```

> "The number is a difference between two runs of identical demand — routing off
> and routing on. Both arms run the same detector; the control arm simply isn't
> allowed to act. Attempts have to match exactly, and if they don't the run
> refuses to report a recovery figure at all."

Then the honest bit:

> "Nothing here learns a label. We schedule the outages, so time-to-detect is
> latency against an event we caused — not agreement with a label we invented."

**2:45–3:30 — the validated headline**

```bash
python -m revenueguard.validate --seeds 8 --days 2
```

> "One seed can't tell a real effect from a lucky roll. Eight paired seeds:
> ₹1,22,91,379, confidence interval ₹1,17,94,730 to ₹1,27,88,027, zero seeds
> losing money."

**3:30–4:20 — the finding that changed the code**

```bash
python -m revenueguard.stress --days 2
```

> "I set the shift cap at 40% as the cautious choice and wrote a paragraph
> defending it. Then I measured it. It was costing 61% of the available
> recovery, and the congestion I thought justified it never arrived — at a 100%
> cap the destination lost 700 payments to load against 11,700 recovered. So the
> default moved to 80%, the knee of that curve. The paragraph was wrong and the
> measurement is in the repo."

Then the one that matters more:

```bash
python -m revenueguard.sensitivity --days 2
```

> "Then I asked whether that 80% depends on a curve I guessed. It mostly
> doesn't — but the sweep found something worse. On gateway fleets already
> running at capacity, this system loses money at *every* setting. There's no
> spare room to route into, so shifting traffic just piles load onto something
> already struggling. No per-action guardrail can see that; every individual
> move looks fine.
>
> My first version of that report divided one negative number by another and
> printed 'ACCEPTABLE'. I caught it, fixed it, and built what it was hiding:
> the system now measures the real effect of its own moves and halts when
> they stop paying."

**This is the strongest 90 seconds in the video. Do not cut it.** Almost every
other submission will claim their system always helps.

**4:20–4:40 — it is not only a benchmark**

```bash
docker compose up
curl -X POST localhost:8000/ingest -d '{"outcomes":[...]}'
curl localhost:8000/routing
```

> "The same loop runs as a service. Real payment outcomes go in as aggregated
> counts, recommendations come out. The deployed path is the benchmarked path —
> the service calls the same `tick`, and a test asserts they produce identical
> results. It emits recommendations rather than applying them, because acquirer
> selection isn't an endpoint a third party can call."

**4:40–5:00 — where AI sits, and close**

```bash
python -m revenueguard.investigate "why did traffic move off gw_beta at 03:12, and did it help?"
```

> "And this is where a model earns its place. It gets four read-only tools over
> the run and works out for itself what to look at — finds the ledger entries,
> pulls the traffic, checks whether the shift actually helped across every
> gateway, and answers. Multi-step, model-driven. But every tool reads and none
> write, and it can't see the incident plan either — so it's reasoning from
> exactly what the system saw."

> "Attribution is deterministic — lift with coverage. The language model is
> handed the finished attribution and writes it in English for the console. It
> never decides, and a test asserts no decision-path module even imports it. A
> sampled token in the path of a money-moving action can't be reproduced or
> defended.
>
> 202 property tests. Every number in the README is printed by a command in the
> repo — none of it is typed by hand."

**Do not** show: the Streamlit console (it's slower than the CLI and says less),
or the code. Show behaviour and numbers.

---

## 4. Panel preparation

The questions a payments engineer will actually ask, and honest answers.

**"Your world is simulated. Why should I believe any of this?"**
The simulation is the environment, not the thing being evaluated. What is being
evaluated is the detector's *view* of it, and the detector sees only `(slice,
minute, attempts, successes)`. A deployment replaces `world.py` and nothing else
changes. The evaluation design — paired arms, identical demand, injected ground
truth — is what transfers, and it transfers exactly.

**"168 false alarms per 1,000 slice-hours — where does that number come from?"**
Alarms not attributable to any incident on that slice, divided by slices × hours
monitored. The denominator matters: a raw count means nothing without knowing
how many tests were run. That figure is the *fixed-threshold rule at its shipped
default*, and it is in the README specifically to show that detector is
unusable.

**"Why not just use an ML model?"**
On synthetic data it would be circular — I'd write the rule deciding which rows
are bad, train a model to learn it, and report how well it learned it. That
measures the generator. If you gave me labelled production data I'd absolutely
fit a model; the harness is already shaped to evaluate one honestly.

**"You have 39 rollbacks against 103 actions. Isn't that bad?"**
It's the cost of acting on less than certainty. Driving it to zero means acting
only when certain, and the stress sweep shows that costs far more money than the
rollbacks do. Each rollback is also a case where the system noticed its own
action made things worse and undid it — which is the behaviour I'd want.

**"Can this actually run against Razorpay?"**
The routing half, no — acquirer selection is Optimizer, not a merchant endpoint,
and any project claiming otherwise is describing something that doesn't exist.
The execution half is real: each decision creates a test-mode Order carrying the
audit reference in `notes`. Written and unit-tested; never run live, because I
had no test credentials.

**"What would you do next?"**
Fit the congestion curve to real acquirer telemetry. Its knee and slope are a
plausible shape, not an observed one, and that curve is what sets the 80% shift
cap — so it's the most load-bearing unknown left.

**"Could this actually run somewhere?"**
It runs as a container today and consumes real outcomes over HTTP; `DEPLOY.md`
has the integration. Before production it needs persistence — state is in
memory, so a restart is a three-hour blind spot while baselines refill —
authentication on `/ingest`, and the capacity curve fitted to real acquirers
rather than the plausible shape it uses now. None are hard; none are done, and
they are listed rather than glossed.

**"What's the weakest part?"**
That same curve, and that the confidence interval covers traffic randomness but
not which incidents occur — eight seeds, one incident plan. Randomising the
incident mix would widen the interval honestly.

---

## 5. Repo hygiene

- [ ] `LICENSE` present (MIT)
- [ ] `.env.example` shows the two optional keys; **no real key committed**
- [ ] `pyflakes revenueguard app.py tests docs` is silent
- [ ] commit history reads as steady work, not one dump
- [ ] README opens with the validated number, not with setup instructions
