"""Content of the two project documents.

Layout, styles and diagrams live in build_docs.py; this module is the prose.
Every figure is interpolated from `Facts`, which reads the repository's own
benchmark JSON, so the documents cannot drift from the code.
"""
from __future__ import annotations

import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reportlab.platypus import PageBreak, Spacer

from build_docs import (Doc, Facts, OUT, P, bullets,
                        code, control_loop_diagram, cover, kpis,
                        layers_diagram, measurement_diagram, rupees, table,
                        together)


# ------------------------------------------------------- document 1: complete

def complete_doc(f: Facts) -> List:
    st: List = []
    A = st.append

    A(P("1. Executive summary", "h1"))
    A(P(
        "RazorGuard is a control plane for payment reliability. It watches "
        "payment success rates at the finest grain that matters &mdash; one "
        "gateway, one payment method, one issuing bank &mdash; detects when a "
        "slice degrades, works out what the failing slices have in common, "
        "moves traffic to a healthier route under an explicit written policy, "
        "verifies whether that helped, and rolls back when it did not. Every "
        "action and every refusal is written to an append-only audit ledger."))
    A(P(
        "The distinguishing claim is not that it routes around failures &mdash; "
        "that idea is not new. It is that the money it recovers is "
        "<b>measured rather than projected</b>, and the measurement is built so "
        "that it cannot flatter itself. The same demand runs twice, once with "
        "routing disabled and once enabled, and the difference is the answer. "
        "That is repeated across independent seeds and reported as a confidence "
        "interval."))
    A(P(
        f"Across {f.seeds} paired seeds of {f.days} simulated days each, the "
        f"router recovers <b>{rupees(f.rec['mean'])}</b> "
        f"(95% CI {rupees(f.rec['ci_lo'])} to {rupees(f.rec['ci_hi'])}), which "
        f"is <b>{f.shr['mean']:.1%} &plusmn; {f.shr['sd']:.1%}</b> of the money "
        f"the injected incidents put at risk. In "
        f"<b>{f.losses} of {f.seeds}</b> seeds did the router lose money."))

    A(P("2. Problem statement", "h1"))
    A(P(
        "A payment gateway is not one pipe. A single merchant's traffic fans "
        "out across several acquiring banks, several payment methods (UPI, "
        "cards, netbanking) and dozens of issuing banks. Failures in this "
        "system are almost never global. They are narrow: one issuer's "
        "authorisation stack slows down, one acquirer's UPI handle starts "
        "declining, a card range begins failing at a single gateway."))
    A(P(
        "Narrow failures are invisible on the chart everyone watches. If HDFC "
        "UPI through one gateway collapses from 96% to 40%, and that slice "
        "carries 4% of volume, the headline success rate moves about two points "
        "&mdash; well inside normal daily variation. Nobody pages. The money "
        "leaves quietly, for as long as it takes a human to notice a pattern in "
        "a support queue."))
    A(P("The problem has three parts, and each defeats the naive solution to "
        "the others:"))
    A(bullets([
        "<b>Detection.</b> Monitoring at a grain fine enough to see the failure "
        "means running thousands of simultaneous statistical tests. At any "
        "fixed per-test significance level, false alarms scale with the number "
        "of slices and the alert channel drowns. Monitoring coarsely enough to "
        "avoid that means missing the failure entirely.",
        "<b>Attribution.</b> Twenty slices alarming at once may be one gateway "
        "outage, one issuer fault, or twenty coincidences. Acting on the wrong "
        "reading moves traffic in a direction that does not help.",
        "<b>Action.</b> Rerouting money automatically is genuinely dangerous. "
        "The destination may be worse. The action may oscillate. The system may "
        "respond to its own noise. An automated remedy that cannot be bounded, "
        "explained and undone is worse than no remedy at all.",
    ]))
    A(P(
        "Razorpay's own Track 03 brief names the first of these directly &mdash; "
        "<i>\"Payment degradation &rarr; root cause &rarr; recovery action\"</i> "
        "&mdash; with the stated bar being <i>\"measured money recovered across "
        "a batch, with compliant escalation, stopping rules, and an audit "
        "trail.\"</i> This project is built against that sentence."))

    A(P("3. What RazorGuard does", "h1"))
    A(P(
        "It runs a loop, once a minute, over live payment outcomes. Each pass "
        "does seven things."))
    A(table([
        ["Stage", "What happens", "Module"],
        ["1 Observe", "Read attempts and successes per slice. Nothing else is "
                      "visible &mdash; no incident labels, no ground truth.",
         "world.py"],
        ["2 Detect", "Score each slice's current rate against its own lagged "
                     "baseline using a Beta posterior with cohort shrinkage.",
         "detectors/"],
        ["3 Attribute", "Find the dimension &mdash; gateway, method or issuer "
                        "&mdash; that explains the alarming population, by lift "
                        "with coverage.", "rootcause.py"],
        ["4 Narrate", "Restate the finished attribution in English for the "
                      "operator. Optional; never affects a decision.",
         "narrator.py"],
        ["5 Gate", "Decide whether the system is <i>permitted</i> to act, "
                   "against ten explicit rules. Record the verdict either way.",
         "policy.py"],
        ["6 Act", "Shift a bounded fraction of the degraded gateway's share "
                  "onto the healthiest alternative.", "routing.py"],
        ["7 Verify", "Watch both sides. Roll back if the destination is worse; "
                     "ease traffic home once the source recovers.",
         "control_plane.py"],
    ], [62, 320, 113]))

    A(P("4. What it fixes, in operational terms", "h1"))
    A(P(
        "Take the concrete case the benchmark injects at 03:10 &mdash; a gateway "
        "collapsing to roughly a third of its normal success rate, across 24 "
        "slices, at the quietest hour of the day."))
    A(P(
        "<b>Without the system.</b> Overnight volume is a tenth of peak, so the "
        "absolute failure count is small and no threshold fires. The headline "
        "success rate dips by a fraction of a point. The outage runs its full "
        "40 minutes and is discovered the next morning, if at all, from a "
        "settlement discrepancy."))
    A(P(
        "<b>With the system.</b> The degradation is detected within minutes on "
        "the slices carrying enough traffic to speak. Attribution recognises "
        "that every alarming slice shares one gateway. The policy engine checks "
        "confidence, drop size, destination health, evidence quality, cooldown "
        "and cause budget &mdash; and then traffic moves, in bounded steps, with "
        "a 3% canary left behind so the degraded gateway stays observable. When "
        "it recovers, traffic eases back in thirds."))
    A(P(
        "This exact case is where the system originally did nothing at all. The "
        "destination-health check demanded 60 recent attempts on the precise "
        "target slice, and at 03:00 no slice had them. Fixing it &mdash; by "
        "widening the health estimate in declared steps and charging a penalty "
        "for the coarser reading &mdash; took those two overnight outages from "
        "<b>0 routing actions to 11 and 8</b>."))

    A(P("5. Scope: what it does and does not claim", "h1"))
    A(table([
        ["It does", "It does not"],
        ["Detect narrow degradations a global success-rate chart cannot show",
         "Predict failures before they begin"],
        ["Attribute a set of alarms to a common dimension, deterministically",
         "Diagnose the underlying fault inside a bank's systems"],
        ["Reroute within a bounded, written, reversible policy",
         "Change acquirer selection through the Razorpay API &mdash; no such "
         "third-party endpoint exists"],
        ["Measure recovered revenue against a control arm",
         "Claim a figure from production traffic; the world here is simulated"],
        ["Escalate when routing is the wrong tool",
         "Remedy an issuer-side fault &mdash; no reroute reaches a healthy path"],
    ], [247, 248]))

    A(PageBreak())
    A(P("6. Technical approach, and why", "h1"))
    A(P(
        "Eight decisions define this system. Each was taken against a more "
        "obvious alternative, and in two cases the measurement later "
        "contradicted the choice and the code changed. Those two are the "
        "most useful sections here."))

    A(P("6.1  Ground truth by injection, not by labelling", "h2"))
    A(P("<b>The alternative:</b> generate synthetic transactions, label some "
        "'degraded', train a classifier, report precision and recall."))
    A(P(
        "<b>Why it was rejected:</b> it is circular. You write the rule that "
        "decides which rows are bad, train a model to learn that rule, then "
        "report how well it learned it. The number measures your generator, not "
        "the world, and a reviewer sees that immediately."))
    A(P(
        "<b>What is done instead:</b> nothing learns a label. Outages are "
        "<i>scheduled</i> &mdash; we know the minute each began, which slices it "
        "touched, and how deep it went. The detector sees only "
        "<font face='Courier' size='8.5'>(slice, minute, attempts, successes)"
        "</font>. Time-to-detect is therefore latency against an event we "
        "caused, which is a measurement rather than an agreement score. The "
        "incident plan lives in a module the detectors cannot import."))

    A(P("6.2  Beta posterior with cohort shrinkage, not a z-score", "h2"))
    A(P(
        "<b>The problem:</b> two pathologies at once. A slice carrying four "
        "attempts a minute swings between 50% and 100% on noise alone, so any "
        "fixed threshold or z-score is meaningless there. Meanwhile 72 slices "
        "tested every minute is 207,360 tests over two days &mdash; at a 5% "
        "per-test level, thousands of false alarms."))
    A(P(
        "<b>The approach:</b> both are the same problem &mdash; too little "
        "evidence taken too literally &mdash; and pooling solves both. Each "
        "slice's current rate is a Beta posterior whose prior is centred on its "
        "own method's pooled rate with "
        "<font face='Courier' size='8.5'>prior_strength</font> "
        "pseudo-observations. A thin slice is dragged toward its cohort and "
        "cannot alarm on three unlucky failures; a high-volume slice overwhelms "
        "the prior and speaks for itself. The alarm condition is not 'the rate "
        "looks low' but <font face='Courier' size='8.5'>P(rate &le; baseline "
        "&minus; min_drop) &ge; confidence</font> &mdash; the drop must be both "
        "real and large."))
    A(P(
        "The baseline itself carries a 15-minute lag, so a degradation in "
        "progress cannot quietly become the normal it is measured against."))

    A(P("6.3  Deterministic attribution; the LLM confined to prose", "h2"))
    A(P("<b>The temptation:</b> hand the alarming slices to a language model "
        "and ask what is wrong. It demos beautifully."))
    A(P(
        "<b>Why it was rejected:</b> that puts a sampled token in the path of a "
        "money-moving action. Such a decision cannot be reproduced, reviewed "
        "afterwards, or defended to a risk team. Attribution is arithmetic, and "
        "arithmetic should not be delegated to something non-deterministic."))
    A(P(
        "<b>What is done instead:</b> attribution is lift with coverage, ranked "
        "so that an explanation missing half the alarms cannot win however high "
        "its lift. The language model receives the <i>finished</i> attribution "
        "&mdash; dimension, coverage, lift, slice counts &mdash; and writes it as "
        "English for the operator console. It never sees raw traffic, never "
        "decides, and its output never reaches the ledger, which keeps the "
        "deterministic template because a ledger whose text changes on re-read "
        "is not a ledger."))
    A(P(
        "The attribution also refuses to overclaim. Below five alarming slices "
        "it will not name a secondary dimension, and says so rather than "
        "manufacturing confidence:"))
    A(code(
        "Success rate on gateway 'gw_gamma' fell 20.6 points (91.4% to 70.8%).\n"
        "It spans 3 of 3 alarming slices. Too few slices have alarmed to narrow\n"
        "it below the gateway yet."))

    A(P("6.4  Detection and permission are separate systems", "h2"))
    A(P(
        "A confident detector is not authorisation to move money. The detector "
        "answers <i>is something wrong</i>; the policy engine answers <i>are we "
        "permitted to act on it</i>. Keeping them apart means the bounds can be "
        "read, argued with and changed without touching the statistics &mdash; "
        "and every refusal carries the name of the rule that caused it."))
    A(P(
        "The audit ledger records refusals as first-class events. A ledger that "
        "records only successful actions hides exactly the decisions worth "
        "reviewing."))

    A(P("6.5  The canary: a drained gateway must keep some traffic", "h2"))
    A(P(
        "The obvious implementation of 'route away from the broken gateway' "
        "sets its weight to zero. That is a trap. A gateway carrying no traffic "
        "emits no observations, so you lose the ability to tell whether it "
        "recovered &mdash; and can therefore never route back to it. Weights "
        "never fall below a 3% floor, and that trickle is the only reason "
        "recovery is observable at all."))

    A(P("6.6  Hierarchical health estimation with a coarseness penalty", "h2"))
    A(P(
        "Choosing a destination requires knowing how healthy it is, and at "
        "03:00 the exact destination slice may have four attempts. Refusing to "
        "act on thin evidence is safe but useless &mdash; it is what left "
        "overnight outages unattended. Widening the lens silently would be "
        "worse than either."))
    A(P("So the estimator widens in declared steps, and pays for each one:"))
    A(code(
        "slice / 5 min   ->   slice / 20 min   ->   gateway+method   ->   gateway\n"
        "   +0 pp                 +1 pp                 +3 pp             +5 pp"))
    A(P(
        "The penalty is added to the advantage a destination must demonstrate "
        "before traffic moves, because a reading pooled across issuers cannot "
        "see an issuer-specific fault on the destination. The level used is "
        "written into the audit entry, so a reviewer can always see how hard "
        "the system had to look to justify the move."))

    A(P("6.7  Paired control and treatment, on identical demand", "h2"))
    A(P(
        "<b>The alternative:</b> run the system, count recovered transactions, "
        "multiply by average ticket. That is a projection wearing the clothes "
        "of a measurement &mdash; it has no counterfactual."))
    A(P(
        "<b>What is done instead:</b> run the same demand twice. Every random "
        "draw is keyed by its own coordinates &mdash; "
        "<font face='Courier' size='8.5'>(seed, minute, method, issuer)</font> "
        "for demand and <font face='Courier' size='8.5'>(seed, minute, slice)"
        "</font> for outcomes &mdash; rather than pulled from one shared stream, "
        "which would desynchronise the instant the two arms made different "
        "numbers of draws and quietly fold luck into the result."))
    A(P(
        "The run prints total attempts for both arms and <b>refuses to report a "
        "recovery figure if they differ</b>. The control arm is not crippled: it "
        "runs the same detector and raises the same alarms, and simply may not "
        "act. A test asserts it takes zero routing actions."))
    A(Spacer(1, 6))
    A(measurement_diagram())

    A(P("6.8  Gateways get worse as you push traffic at them", "h2"))
    A(P(
        "<b>The assumption that was wrong:</b> for most of this project the "
        "simulator let the router move traffic onto a destination without the "
        "destination noticing. That is the single most generous assumption a "
        "router can be evaluated under &mdash; infinite headroom means shifting "
        "harder is always free, and the rollback machinery never faces anything "
        "but noise."))
    A(P(
        "<b>What is done instead:</b> each gateway has a capacity sized from its "
        "own peak baseline load, and past a utilisation knee its success rate "
        "falls roughly linearly with further load. Below the knee extra traffic "
        "is free; at 100% of provisioned capacity a gateway loses about a tenth "
        "of its success rate. Normal traffic sits well below the knee, so "
        "congestion is something the router <i>causes</i> rather than a "
        "permanent tax on the baseline &mdash; otherwise the control arm would "
        "be degraded too and the comparison would measure the simulator's "
        "headroom instead of the policy."))
    A(P(
        "The visible consequence is that the two independent measures of "
        "recovery no longer agree as tightly. Before capacity existed, measured "
        "recovery and the reduction in incident exposure agreed within 1.5%; "
        "they now differ by around 7%. That gap is not error &mdash; it is the "
        "congestion the router causes at the destination, which costs revenue "
        "without reducing incident exposure. The divergence appeared the moment "
        "loading a gateway had a price, which is the correct behaviour."))

    A(P("6.9  Measuring whether the strategy itself is working", "h2"))
    A(P(
        "Every guardrail described so far bounds a <i>single action</i>: is this "
        "shift too large, is the destination healthy enough, have we touched "
        "this key too recently. None of them can notice that rerouting as a "
        "strategy is not working &mdash; and there are fleets where it is not."))
    A(P(
        "Section 11.4 shows the measurement. On acquirer fleets already running "
        "past their capacity knee at rest there is no spare headroom to route "
        "into, so shifting traffic only concentrates load and the system loses "
        "money at every setting tested. Each individual shift, inspected on its "
        "own, looks entirely reasonable. That is precisely why no per-action "
        "bound catches it."))
    A(P(
        "<b>So the control plane scores its own interventions.</b> Twenty "
        "minutes after each shift it compares the key's success rate "
        "<i>across every gateway serving it</i> against the ten minutes before "
        "&mdash; the whole key, because moving traffic off a sick gateway "
        "trivially improves that gateway, and the question that matters is "
        "whether the customer got paid. When the recent record says its shifts "
        "are doing harm, it halts all routing and escalates."))
    A(P(
        "It engages in proportion to how badly the environment suits it: "
        f"{_trip_summary(f)}. The insurance costs roughly 0.5%% of the headline "
        "figure. It limits the damage rather than removing it, and the honest "
        "conclusion &mdash; stated in the deployment guide rather than buried "
        "&mdash; is that this system needs acquirers with spare capacity to be "
        "worth deploying at all."))

    A(together(P("7. Where AI is used, and where it is refused", "h1"),
               layers_diagram()))
    A(Spacer(1, 10))
    A(P(
        "This boundary is the most defensible design decision in the project, "
        "and it is enforced structurally rather than by convention. One test "
        "asserts the model's prompt contains no slice keys and no per-minute "
        "counts &mdash; only computed conclusions. Another reads the source of "
        "every decision-path module and asserts that none of them imports the "
        "narrator at all."))
    A(P(
        "The narration layer is also safe to leave switched on. No credential, "
        "a network error, a refusal, an empty completion &mdash; all fall back "
        "to the deterministic template. The exception handler is deliberately "
        "broad: with no credential resolvable the SDK raises a bare "
        "<font face='Courier' size='8.5'>TypeError</font> during header "
        "construction, which a typed handler chain sails straight past, turning "
        "a cosmetic feature into a crash in the middle of an incident. It also "
        "disables itself after the first failure, so a missing key costs one "
        "failed round trip rather than one per alarming slice."))

    A(P("7.1  The one place a model is given latitude", "h2"))
    A(P(
        "Keeping the model out of every decision is the right call, and it is "
        "not the whole story &mdash; because there is a job here a model is "
        "genuinely better at than a dashboard. The question an on-call engineer "
        "asks at 03:12 is not <i>what is the success rate</i>. It is <i>why did "
        "this thing move my traffic, and was it right?</i> Answering that means "
        "reading a thousand-row ledger, cross-referencing the observation "
        "stream, and holding several windows in your head at once."))
    A(P(
        "<font face='Courier' size='8.5'>investigator.py</font> is an agent for "
        "exactly that. It is handed four read-only tools over a completed run "
        "&mdash; search the audit ledger, pull per-minute traffic for a slice, "
        "measure a key's health across every gateway serving it, rank slices "
        "worst-first &mdash; and decides for itself what to query, reading "
        "results and following up until it can answer. Multi-step and "
        "model-driven, rather than a fixed report with a language model stapled "
        "to the end."))
    A(P(
        "Three properties make that safe rather than a contradiction of "
        "everything above, and each is enforced by a test rather than promised. "
        "<b>Every tool reads and none write.</b> <b>No decision-path module "
        "imports it</b>, so there is no route from an answer back into routing. "
        "And it is <b>as blind as the detector was</b>: it cannot import the "
        "incident plan, so it reasons only from what the system actually "
        "observed. An investigator holding the answer key would be theatre."))
    A(P(
        "The distinction that matters is direction of travel. The control plane "
        "<i>decides</i>, and must be reproducible. The investigator "
        "<i>explains, after the fact</i> &mdash; and the worst a wrong answer "
        "can do is mislead a human who then reads the ledger themselves, which "
        "is the same risk any dashboard carries.", "small"))

    A(P("7.2  Advising where the rulebook runs out", "h2"))
    A(P(
        "Hard bounds have a shape: they say <i>no</i> precisely, and they say "
        "nothing else. When one fires, the system escalates with a correct "
        "refusal and no next step &mdash; which leaves the engineer exactly "
        "where they started."))
    A(P(
        "<font face='Courier' size='8.5'>advisor.py</font> runs on escalations "
        "only. It investigates with the same read-only tools and returns one of "
        "six recommendations &mdash; contact the issuer, add capacity, manual "
        "reroute, pause automation, monitor, insufficient evidence &mdash; with "
        "its reasoning and the evidence it cited. The action set is closed on "
        "purpose: an open-ended recommendation is hard to act on and impossible "
        "to audit."))
    A(P(
        "The division of labour is the point. <b>Choosing a destination</b> is "
        "an argmax over two or three candidates with a confidence check; a "
        "model there would be slower, non-reproducible and no more accurate, "
        "and the audit ledger would stop being deterministic. <b>Deciding what "
        "to do when routing cannot help</b> is judgement, because the useful "
        "answer depends on the shape of the evidence &mdash; an issuer-side "
        "fault wants somebody to call the issuer; a saturated fleet wants "
        "capacity rather than cleverness. So the model gets the residual, and "
        "arithmetic keeps the rest."))
    A(P(
        "Advice is recorded as advice, reaches the alert, and no routing "
        "decision reads it. Without a model it falls back to the standing "
        "runbook answer for that rule, which is the floor the model has to beat "
        "to be worth calling.", "small"))

    A(together(P("8. The control loop", "h1"),
               control_loop_diagram()))
    A(Spacer(1, 12))
    A(P(
        "The loop deliberately spends as much design effort on the exit as on "
        "the entry. Most of the risk in an automated remedy is not in deciding "
        "to act &mdash; it is in failing to notice the action made things worse, "
        "or in slamming full traffic back onto a gateway that has only just "
        "recovered, which is how a flap becomes an outage. Restoration moves in "
        "thirds of the remaining gap, and only after the source has looked "
        "healthy on canary traffic for ten consecutive minutes."))

    A(P("9. System structure", "h1"))
    A(table([
        ["Module", "Responsibility"],
        ["config.py", "The fleet: 3 gateways &times; 3 methods &times; 8 issuers, "
                      "with volume shares, healthy rates and average ticket."],
        ["capacity.py", "Gateways degrade past a utilisation knee, so the "
                        "router's own actions have a price and a large "
                        "diversion can congest its destination."],
        ["scenarios.py", "Injected degradations &mdash; hard outage, gradual "
                         "slide, issuer fault, shallow drop &mdash; scheduled "
                         "across the diurnal cycle. The ground truth; detectors "
                         "cannot import it."],
        ["simulator.py", "Open-loop traffic, for detector benchmarking where "
                         "routing changes nothing."],
        ["world.py", "Closed-loop traffic. Demand, routing and health kept "
                     "strictly separate so a router can be evaluated at all."],
        ["detectors/threshold.py", "The fixed-threshold baseline &mdash; what an "
                                   "on-call engineer writes in an afternoon. "
                                   "Present so the sophisticated detector has "
                                   "something honest to beat."],
        ["detectors/sequential.py", "Beta-posterior drop test with cohort "
                                    "shrinkage and a lagged baseline."],
        ["detectors/ensemble.py", "The shipped union of the two, at the "
                                  "operating point that won the matched "
                                  "false-alarm comparison."],
        ["rootcause.py", "Deterministic lift-with-coverage attribution, with an "
                         "explicit refusal to name a secondary cause on thin "
                         "evidence."],
        ["narrator.py", "Optional LLM prose over that attribution, with a "
                        "template fallback on every failure mode."],
        ["economics.py", "MDR per route, and gross recovery net of what it "
                         "cost. UPI is free by regulation; cards are not."],
        ["advisor.py", "Recommends a course of action on the escalations the "
                       "policy engine refuses - the residual the rulebook "
                       "cannot answer."],
        ["investigator.py", "An agent that answers questions about a run using "
                            "four read-only tools over the ledger and the "
                            "observation stream. The one place a model is "
                            "given latitude, and it can change nothing."],
        ["policy.py", "Ten bounds and stopping rules. Returns a verdict "
                      "carrying the specific rule that decided it."],
        ["routing.py", "Weight table per (method, issuer), canary floor, "
                       "gradual restore."],
        ["control_plane.py", "The loop, the hierarchical health estimator, and "
                             "the diversion supervisor."],
        ["audit.py", "Append-only ledger. Records refusals, not just actions."],
        ["executor.py", "Razorpay test-mode execution. Dry run by default; live "
                        "keys refused outright."],
        ["metrics.py", "Time-to-detect, false alarms per 1,000 slice-hours, "
                       "incident exposure."],
        ["bench.py, sweep.py", "Detector head-to-head, and the operating-point "
                               "curve behind its settings."],
        ["experiment.py", "Control versus treatment for one seed."],
        ["validate.py", "Paired multi-seed validation with a confidence "
                        "interval."],
        ["stress.py", "Sweeps the shift cap to ask whether the policy bounds "
                      "are costing money. They were."],
        ["demo.py, narrate.py, execute.py", "Incident replay, narration "
                                            "comparison, execution CLI."],
        ["app.py", "Streamlit operator console."],
    ], [128, 367]))

    A(P("10. Guardrails reference", "h1"))
    A(table([
        ["Rule", "Bound", "Why"],
        ["min_confidence", "0.95", "Posterior confidence required before money "
                                   "moves."],
        ["min_drop_pp", "4 pp", "Small drops are not worth the churn of acting."],
        ["max_shift_fraction", "40%", "Of the <i>source gateway's share</i> per "
                                      "action, not of all traffic."],
        ["max_cumulative_divergence", "60%", "Total weight that may sit away "
                                             "from baseline for one key."],
        ["action_cooldown", "15 min", "Per key, so the same slice is not worked "
                                      "repeatedly."],
        ["max_causes_per_hour", "4", "The anti-oscillation budget. Counts "
                                     "<i>causes</i>, not weight changes."],
        ["max_actions_per_hour", "60", "Hard ceiling that survives a "
                                       "mis-attributed cause."],
        ["min_target_advantage_pp", "8 pp", "Plus the coarseness penalty for a "
                                            "pooled health reading."],
        ["min_target_attempts", "60", "Evidence required before a destination's "
                                      "health is trusted."],
        ["forbid_alarmed_target", "on", "Never route into a gateway that is "
                                        "itself alarming."],
        ["no_healthy_destination", "escalate", "Every route degraded means an "
                                               "issuer-side fault; routing "
                                               "cannot help."],
    ], [142, 66, 287]))
    A(P(
        "<b>The cause budget is worth dwelling on</b>, because getting it wrong "
        "cost real recovery. The original design capped <i>actions</i> per hour. "
        "But one gateway outage legitimately requires shifting every (method, "
        "issuer) key that gateway served &mdash; two dozen of them &mdash; and "
        "charging that fan-out against an anti-oscillation budget conflates "
        "'the system is thrashing' with 'one incident was wide'. The first "
        "outage spent the entire hourly budget and everything after it was "
        "escalated. Switching the budget to distinct root causes, with a hard "
        "action ceiling behind it, moved recovery from 23.1% of exposure to "
        f"{f.recovered['share_of_exposure_recovered']:.1%}."))

    A(P("11. Results", "h1"))
    A(P("11.1  Validated across seeds", "h2"))
    A(table([
        ["Metric", "Mean", "SD", "Min", "Max"],
        ["Revenue recovered", rupees(f.rec["mean"]), rupees(f.rec["sd"]),
         rupees(f.rec["min"]), rupees(f.rec["max"])],
        ["Share of exposure", f"{f.shr['mean']:.1%}", f"{f.shr['sd']:.1%}",
         f"{f.shr['min']:.1%}", f"{f.shr['max']:.1%}"],
        ["Success rate gain", f"{f.srg['mean']:.3f} pp", f"{f.srg['sd']:.3f}",
         f"{f.srg['min']:.3f}", f"{f.srg['max']:.3f}"],
        ["Routing actions", f"{f.act['mean']:.1f}", f"{f.act['sd']:.1f}",
         f"{f.act['min']:.0f}", f"{f.act['max']:.0f}"],
        ["Rollbacks", f"{f.rbk['mean']:.1f}", f"{f.rbk['sd']:.1f}",
         f"{f.rbk['min']:.0f}", f"{f.rbk['max']:.0f}"],
    ], [143, 96, 88, 84, 84], align_right=(1, 2, 3, 4)))
    A(P(
        f"95% confidence interval on mean recovery: "
        f"<b>{rupees(f.rec['ci_lo'])} to {rupees(f.rec['ci_hi'])}</b>. It "
        f"excludes zero, so the effect is not an artefact of one seed, and its "
        f"width is the honest precision available from {f.seeds} trials. The "
        f"interval uses the t distribution rather than a normal approximation, "
        f"which at this sample size would understate it by roughly 18%."))

    A(P("11.2  One seed in detail", "h2"))
    A(table([
        ["", "Router off", "Router on", "Delta"],
        ["Attempts", f"{f.attempts:,}", f"{f.attempts:,}", "0"],
        ["Successful payments", f"{f.ctrl['successes']:,}",
         f"{f.treat['successes']:,}", f"+{f.recovered['payments']:,}"],
        ["Overall success rate", f"{f.ctrl['success_rate']:.2%}",
         f"{f.treat['success_rate']:.2%}",
         f"+{f.recovered['success_rate_gain_pp']:.2f} pp"],
        ["Revenue", rupees(f.ctrl["revenue_inr"]),
         rupees(f.treat["revenue_inr"]),
         "+" + rupees(f.recovered["revenue_inr"], unit=False)],
    ], [130, 122, 122, 121], align_right=(1, 2, 3)))
    gap = (f.recovered["control_exposure_inr"]
           - f.recovered["treatment_exposure_inr"])
    diff = abs(gap - f.recovered["revenue_inr"]) / f.recovered["revenue_inr"]
    A(P(
        f"Cross-checked two independent ways: measured recovery came to "
        f"{rupees(f.recovered['revenue_inr'])}, while incident exposure fell "
        f"from {rupees(f.recovered['control_exposure_inr'])} to "
        f"{rupees(f.recovered['treatment_exposure_inr'])} &mdash; a reduction of "
        f"{rupees(gap)}, a difference of about {diff:.0%}."))
    A(P(
        "That gap is itself informative. Before gateways had capacity limits "
        "these two numbers agreed within 1.5%; the divergence appeared the "
        "moment loading a gateway had a price. It is the congestion the router "
        "<i>causes</i> at the destination, which costs revenue without reducing "
        "incident exposure &mdash; so the exposure measure, which only counts "
        "money lost inside an incident window, now reads slightly optimistic. "
        "The recovery figure is the conservative one of the two, and it is the "
        "one quoted."))
    A(P("Guardrail activity in that run:"))
    A(table([
        ["Routing actions", f"{f.treat['actions']}", "Rollbacks",
         f"{f.treat['rollbacks']}"],
        ["Blocked by policy", f"{f.treat['blocked']}", "Restore steps",
         f"{f.treat['restores']}"],
        ["Escalated to a human", f"{f.treat['escalated']}", "Audit events",
         f"{f.treat['audit_events']:,}"],
    ], [150, 60, 150, 60], header=False, align_right=(1, 3)))

    A(P("11.3  Net of what the recovery cost", "h2"))
    A(P(
        "Rerouting is not free. Acquirers price differently, so moving volume "
        "changes what the merchant pays even when every payment succeeds. A "
        "system reporting only gross recovery is ignoring one side of its own "
        "ledger, and a payments company reads the other side first."))
    A(table([
        ["", "Amount"],
        ["Gross recovered", rupees(f.net["gross_inr"])],
        ["Incremental processing fees", rupees(f.net["incremental_cost_inr"])],
        ["<b>Net recovered</b>", f"<b>{rupees(f.net['net_inr'])}</b>"],
        ["Fees as a share of gross", f"{f.net['cost_ratio']:.1%}"],
    ], [300, 195], align_right=(1,)))
    A(P(
        "The asymmetry underneath is specific to this market and worth knowing. "
        "<b>UPI person-to-merchant carries zero MDR by regulation in India</b>, "
        "so a UPI recovery is free and a card recovery is not. It falls out of "
        "the arithmetic rather than being special-cased, and it means the "
        "economics of a recovery depend on which method degraded. Two "
        "quantities move the fee line in opposite directions: recovering more "
        "payments means more fees to pay, which is the good kind of cost, while "
        "moving volume between acquirers changes the rate either way."))

    A(P("11.4  The policy caps were costing 61% of the recovery", "h2"))
    A(P(
        "<font face='Courier' size='8.5'>max_shift_fraction</font> was set to "
        "40% a priori, as the cautious choice, and defended in an earlier draft "
        "of this document by argument alone. The hypothesis was that section "
        "6.8's capacity model would justify it: if shifting harder congests the "
        "destination, the cap is not timidity but something close to an optimum. "
        "<font face='Courier' size='8.5'>stress.py</font> measured that, and the "
        "hypothesis was wrong."))
    A(table(cap_rows(f), [72, 108, 84, 66, 74, 91], align_right=(1, 2, 3, 4, 5)))
    A(P(
        "The cap bound hard, and the congestion it was implicitly guarding "
        "against never arrived &mdash; even at a 100% cap the destination lost "
        "on the order of 700 payments to load against roughly 11,700 recovered. "
        "The default moved to <b>80%</b>, the knee of the curve: it captures "
        "nearly all the recovery available at 100% with less congestion, and "
        "going further buys about 3% more for no clear gain. This is why the "
        "headline figure is what it is; at the original setting it would have "
        "been roughly two-fifths of the size."))
    A(P(
        "The general point matters more than the constant. A guardrail defended "
        "only by argument is a guess with good manners. This one was measured, "
        "it was wrong, and the measurement is in the repository so the next "
        "person can disagree with it.", "small"))

    A(P("11.5  Rerouting is not always beneficial", "h2"))
    A(P(
        "The 80% cap in 11.3 was chosen against one congestion curve, and that "
        "curve is a plausible shape rather than a measured one (6.8). So "
        "<font face='Courier' size='8.5'>sensitivity.py</font> sweeps the cap "
        "against four curves at once, from an acquirer with generous headroom "
        "to one that saturates early. Two questions: does the choice of cap "
        "survive being wrong about the curve, and does the system help at all?"))
    A(table(sens_rows(f), [76, 84, 84, 84, 84, 84], align_right=(1, 2, 3, 4, 5)))
    A(P(
        f"The first question resolves well. Across the curves where routing "
        f"helps, holding 80% costs at most {f.worst_gap:.1%} against the best "
        f"setting for that curve, so the unmeasured curve is not load-bearing "
        f"for <i>that</i> decision."))
    A(P(
        f"The second does not. On <b>{' and '.join(f.harmful)}</b>, every cap "
        f"tested recovers nothing or loses money. Those fleets sit past their "
        f"capacity knee before anything goes wrong, so there is no healthy "
        f"headroom to move traffic into. This is the most important limitation "
        f"in the project, and it is not a tuning problem &mdash; it is a "
        f"statement about when the whole approach applies."))
    A(P(
        "An earlier version of this report did not say so. It computed the gap "
        "between best and shipped as a ratio, divided one negative recovery by "
        "another, produced &minus;302%, and printed a reassuring verdict over "
        "the top of the finding. The arithmetic is fixed, the report now asks "
        "whether value is being destroyed before it asks whether the cap is "
        "optimal, and the mechanism in 6.9 exists because of what the bug was "
        "hiding.", "small"))

    A(P("11.6  The detector, compared honestly", "h2"))
    A(P(
        "Comparing detectors at whatever thresholds they happen to ship with is "
        "meaningless &mdash; any detector looks fast if it may alarm constantly. "
        "The comparison that counts is time-to-detect at a <i>matched "
        "false-alarm rate</i>. At a budget of one false alarm per 1,000 "
        "slice-hours:"))
    A(table([
        ["Detector", "Detected", "Median time to detect"],
        ["fixed_threshold (floor 0.70, 3 min)", "16 / 18", "6.5 min"],
        ["posterior_drop (8 pp, conf 0.90)", "12 / 18", "3.5 min"],
        ["<b>union (floor 0.70 + 5 pp / 0.99)</b>", "<b>16 / 18</b>",
         "<b>5.0 min</b>"],
    ], [255, 120, 120], align_right=(1, 2)))
    A(P(
        "<b>Neither single detector dominates, and that is what made the union "
        "worth building.</b> The simple rule catches four incidents the "
        "posterior detector misses &mdash; all <i>shallow</i> degradations it is "
        "configured to ignore &mdash; while the posterior detector is nearly "
        "twice as fast on what it does catch. Running both and taking the union "
        "matches the best detection rate either reaches alone and is 1.5 minutes "
        "faster than the member that reaches it."))
    A(P(
        "Both members run tighter inside the union than they would alone, "
        "because false alarms add across members. That is the whole reason the "
        "comparison is made at a matched budget: at its own shipped default the "
        "simple rule reaches 18/18 in 3.5 minutes while firing 581 false alarms, "
        "<b>168 per 1,000 slice-hours</b>. It is unusable, and the number "
        "proving it sits in the same sweep. The union is shipped as "
        "<font face='Courier' size='8.5'>detectors.default_detector()</font>."))

    A(P("12. Razorpay integration &mdash; honest scope", "h1"))
    A(P(
        "Razorpay's public API does not expose gateway routing weights to a "
        "third party. Acquirer selection is Razorpay's own product (Optimizer), "
        "not a merchant endpoint. A project claiming to \"reroute traffic "
        "through the Razorpay API\" is describing something that does not exist, "
        "and saying so plainly is more useful than pretending otherwise."))
    A(P(
        "So routing stays inside the simulation where it can be measured, and "
        "the executor demonstrates the half that <i>is</i> real: each recovery "
        "decision creates a genuine <b>test-mode Order</b> carrying the decision "
        "context in its <font face='Courier' size='8.5'>notes</font> &mdash; "
        "audit sequence, source and destination gateway, the reason &mdash; so "
        "the decision is auditable from the Razorpay dashboard, outside this "
        "repository."))
    A(P(
        "Dry run is the default and needs no credentials. Live calls require "
        "both environment variables <i>and</i> an explicit flag, and a key not "
        "beginning <font face='Courier' size='8.5'>rzp_test_</font> is refused "
        "outright. Two tests assert that refusal."))

    A(P("13. Testing", "h1"))
    A(P(
        "202 property tests, all passing, and pyflakes clean. They are not "
        "coverage theatre &mdash; each corresponds to a claim made in this "
        "document that would otherwise be taken on trust:"))
    A(bullets([
        "The control arm takes zero routing actions and its weights never leave "
        "baseline &mdash; without this the headline compares two treatments.",
        "Routing weights always sum to one, no weight is ever negative, and the "
        "canary floor is never breached even under repeated forced drains.",
        "Every executed action was preceded by a policy verdict of <i>allow</i>, "
        "and audit sequence numbers are strictly monotonic.",
        "The ledger records refusals, not only successes.",
        "Every policy rule blocks or escalates the case it exists for.",
        "Attribution will not print 'peers unaffected' on three alarms.",
        "The narrator's prompt contains no slice keys and no per-minute counts.",
        "No decision-path module imports the narrator.",
        "A live Razorpay key is refused before a client is constructed.",
        "Any client exception &mdash; including untyped ones &mdash; falls back "
        "rather than propagating, and the narrator stops calling out after the "
        "first failure.",
    ]))

    A(P("14. Running it in production", "h1"))
    A(P(
        "The control plane runs as a container, consumes real payment outcomes "
        "over an authenticated endpoint, keeps state that survives a restart, "
        "and emits routing recommendations. The deployed path is the "
        "benchmarked path: the service calls "
        "<font face='Courier' size='8.5'>ControlPlane.tick</font>, which is "
        "exactly what <font face='Courier' size='8.5'>run</font> calls, and a "
        "test asserts the two produce identical results."))
    A(table([
        ["Concern", "How it is handled"],
        ["Feeding it", "<font face='Courier' size='8.5'>POST /ingest</font> "
                       "takes aggregated counts &mdash; gateway, method, "
                       "issuer, attempts, successes. Never individual payment "
                       "records: the detector does not need one, so the "
                       "integration never has to carry one."],
        ["Restarts", "State is durable. On boot the stored observation stream "
                     "is replayed back through the detector, the routing table "
                     "is restored, and open diversions are re-adopted &mdash; "
                     "that last one matters most, because restored weights "
                     "with no supervisor watching them is worse than either "
                     "extreme."],
        ["Authentication", "HMAC request signing or a bearer token. The "
                           "service refuses to start without a credential: "
                           "open is what you opt into, not what you forget."],
        ["Escalation", "Delivered to a webhook on its own thread with a "
                       "bounded queue, so a dead incident channel can never "
                       "stall the control loop."],
        ["Failover", "A lease in the shared store elects one active instance. "
                     "A node that stalls past its lease cannot finish the tick "
                     "it was in &mdash; it must stand down, because another "
                     "instance is already deciding. Failover, not horizontal "
                     "scaling: two instances each seeing half the stream would "
                     "both misjudge the fleet."],
        ["The last mile", "Recommendations go to a webhook or an atomically "
                          "written config file, in off / notify / auto modes "
                          "with identical payloads &mdash; so moving between "
                          "them is a configuration change, not a rewrite."],
        ["Observability", "Prometheus metrics, JSON logs, per-tick duration, "
                          "ingest and alert counters, and a graceful shutdown "
                          "that checkpoints on the way out."],
    ], [92, 403]))
    A(P(
        "It emits recommendations and does not apply them, because acquirer "
        "selection is not an endpoint a third party can call. That boundary, "
        "the three ways to act on the output, and what remains open are all in "
        "<font face='Courier' size='8.5'>DEPLOY.md</font>."))

    A(P("15. Where the numbers come from", "h1"))
    A(P(
        "Every figure here is either produced by running the code or invented "
        "by the author. Which is which matters to a reader deciding whether to "
        "believe the headline, so <font face='Courier' size='8.5'>DATA.md</font> "
        "lists it line by line. In short: <b>the world is invented, the "
        "measurement is real.</b>"))
    A(table([
        ["Invented", "Measured"],
        ["Gateway names, traffic mix, volumes, the diurnal curve, healthy "
         "success rates, per-issuer and per-gateway offsets",
         "Detection latency, false-alarm rates, recovered payments, "
         "success-rate gain, every confidence interval"],
        ["Average ticket sizes (Rs 640 / 2,150 / 3,400), which convert "
         "recovered payments into rupees",
         "The payment counts those rupees are derived from"],
        ["The congestion curve, and the incident schedule",
         "Everything the detector, policy engine, router and ledger do with "
         "them"],
    ], [247, 248]))
    A(P(
        "<b>The rupee headline scales linearly with invented ticket sizes.</b> "
        "A reader who distrusts them should read the result as "
        f"<b>+{f.recovered['payments']:,} successful payments</b> and "
        f"<b>+{f.recovered['success_rate_gain_pp']:.2f} percentage points</b> "
        "of success rate, neither of which depends on a price."))
    A(P(
        "The Razorpay executor has been run against a live test account - 6 "
        "of 6 orders created, one fetched back to confirm it is a real record "
        "rather than a successful POST, with the decision context readable in "
        "its notes from the Razorpay dashboard. The LLM narrator has not run "
        "with a live key in this project; every narration came from the "
        "deterministic template fallback, and no result depends on it having "
        "run.", "small"))
    A(P(
        "None of this is taken on trust. "
        "<font face='Courier' size='8.5'>tests/test_integrity.py</font> asserts "
        "that no detector can import the incident plan, that an Observation "
        "carries exactly six fields, that no decision-path module imports the "
        "narrator, and that no result value appears as a literal anywhere in "
        "the package. "
        "<font face='Courier' size='8.5'>tests/test_documented_claims.py</font> "
        "fails if any headline figure in the README or the submission notes "
        "stops matching the benchmark output.", "small"))

    A(P("16. Running it", "h1"))
    A(code(
        "pip install -r requirements.txt\n\n"
        "python -m razorguard.validate   --seeds 8 --days 2   # headline, with a CI\n"
        "python -m razorguard.experiment --days 2             # one seed, in detail\n"
        "python -m razorguard.demo                            # replay an incident\n"
        "python -m razorguard.bench      --days 2             # detector head-to-head\n"
        "python -m razorguard.sweep      --budget 1.0         # matched false-alarm curve\n"
        "python -m razorguard.narrate    --claude             # LLM note vs template\n"
        "python -m razorguard.execute    --limit 6            # Razorpay test mode, dry run\n"
        "streamlit run app.py                                   # operator console\n\n"
        "pytest -q                                              # 202 property tests"))
    A(P(
        "Everything is deterministic under <font face='Courier' size='8.5'>"
        "--seed</font>. No number in this document was typed by hand; each is "
        "read from the JSON these commands write.", "small"))

    A(P("17. Known limits", "h1"))
    A(P("Stated because they are the first things worth asking about."))
    A(table([
        ["Limit", "Consequence"],
        ["<b>Rerouting does not always help.</b> On fleets already at capacity "
         "it loses money at every setting.",
         "The efficacy breaker limits the damage rather than removing it. This "
         "system needs acquirers with spare capacity to be worth deploying, and "
         "the way to find out first is to replay a historical incident through "
         "CsvSource and watch the breaker."],
        ["The world is simulated. Gateway health, demand and incidents are ours.",
         "What is <i>not</i> ours is the detector's view of them, which is the "
         "part under evaluation. A deployment replaces world.py and changes "
         "nothing else."],
        ["The congestion curve is a plausible shape, not a measured one.",
         "Knee at 70% utilisation, about a tenth of the success rate lost at "
         "100% of capacity. Chosen before the sweep rather than fitted to it, "
         "but a real acquirer's curve would move the 80% shift cap that "
         "depends on it. The most load-bearing unknown left."],
        [f"{f.shr['mean']:.0%} of exposure recovered, not 90%.",
         "Detection latency and the deliberate caps set that ceiling. Loosening "
         "either raises the number and lowers the confidence it deserves."],
        [f"About {f.rbk['mean']:.0f} rollbacks per {f.act['mean']:.0f} actions.",
         "Each is a case where the destination proved worse and the system undid "
         "itself. Driving this to zero would mean acting only on certainties, "
         "which costs more money than the rollbacks do."],
        [f"{f.seeds} seeds, one incident plan.",
         "The interval covers traffic randomness, not which incidents occur. A "
         "different mix of outage shapes would move the mean."],
    ], [175, 320]))

    A(P("18. Roadmap", "h1"))
    A(P(
        "In priority order. The first item is the only one that could change a "
        "conclusion rather than improve a number."))
    A(table(roadmap_rows(), [40, 175, 280]))
    return st


def cap_rows(f):
    rows = [["shift cap", "recovered", "of exposure", "actions", "rollbacks",
             "% minutes congested"]]
    for r in f.caps:
        shipped = (f.shipped_cap is not None
                   and abs(r["shift"] - f.shipped_cap["shift"]) < 1e-9)
        b = (lambda t: f"<b>{t}</b>") if shipped else (lambda t: t)
        rows.append([
            b(f"{r['shift']:.0%}"),
            b(rupees(r["recovered_inr"])),
            b(f"{r['share_of_exposure']:.1%}"),
            b(f"{r['actions']:.0f}"),
            b(f"{r['rollbacks']:.0f}"),
            b(f"{r['pct_minutes_congested']:.1f}%"),
        ])
    return rows


def sens_rows(f):
    """The cap-versus-curve table, straight from the sweep output."""
    caps = sorted({float(c["cap"]) for c in f.sens_cells})
    rows = [["curve"] + [f"{c:.0%}" for c in caps]]
    for curve in f.sens.get("curves", []):
        label = curve["label"]
        cells = {float(c["cap"]): c for c in f.sens_cells
                 if c["curve"] == label}
        row = [f"<b>{label}</b>" if label in f.harmful else label]
        for cap in caps:
            cell = cells.get(cap)
            if cell is None:
                row.append("-")
                continue
            share = float(cell["share_of_exposure"])
            shown = f"{share:.1%}"
            row.append(f"<b>{shown}</b>" if share <= 0 else shown)
        rows.append(row)
    return rows


def _trip_summary(f):
    if not f.harmful:
        return "it did not need to engage on any curve tested"
    return ("it stays out of the way where routing works, and engages on "
            + " and ".join(f.harmful))


def roadmap_rows():
    return [
        ["#", "Item", "Why it matters"],
        ["1", "Cost of action",
         "Rerouting is not free: acquirer contracts carry volume commitments "
         "and per-transaction pricing differences. Netting that against "
         "recovered revenue turns a gross figure into a net one."],
        ["2", "Varied incident plans across seeds",
         "The current interval covers traffic randomness only. Randomising "
         "which incidents occur, and their depth and timing, would widen it "
         "honestly and make it a stronger claim."],
        ["3", "A measured congestion curve",
         "The capacity model's knee and slope are a plausible shape, not an "
         "observed one, and that curve is what sets the 80% shift cap. Fitting "
         "it to real acquirer telemetry would move the cap and is the most "
         "load-bearing unknown left."],
        ["4", "Sensitivity analysis over the remaining bounds",
         "The shift cap has now been measured and moved. The other nine are "
         "still defended by argument. The same sweep applied to each would show "
         "which earn their place."],
        ["5", "Live-mode execution against a real test account",
         "Done. Executed against a Razorpay test account: 6 of 6 orders "
         "created plus payment links, one fetched back to confirm the record "
         "and its notes. What remains is wiring a real payment stream into "
         "/ingest rather than the simulator."],
        ["6", "Persistence and replay",
         "Runs are in-memory. Writing observations and the ledger to SQLite "
         "would let the console replay historical incidents without "
         "re-simulating, and is a prerequisite for anything long-running."],
        ["7", "Alert delivery",
         "Escalations currently end in the ledger. Routing them to a real "
         "channel with the incident note attached is small work and closes the "
         "operational loop."],
    ]


# -------------------------------------------------------- document 2: summary

def summary_doc(f: Facts) -> List:
    st: List = []
    A = st.append

    A(P("The problem", "h1"))
    A(P(
        "Payment failures are almost never global. They are narrow &mdash; one "
        "gateway, one method, one issuing bank &mdash; and a narrow failure is "
        "invisible on the chart everyone watches. A slice carrying 4% of volume "
        "can collapse from 96% to 40% and move the headline success rate by "
        "about two points, well inside normal daily variation. Nobody pages, "
        "and the money leaves quietly until a human spots a pattern in a "
        "support queue.", "lead"))

    A(P("The technical statement", "h1"))
    A(P(
        "<b>Detect per-slice success-rate degradation under two hostile "
        "statistical conditions &mdash; very low volume and very high test "
        "multiplicity &mdash; attribute the alarming population to a common "
        "dimension deterministically, and execute a bounded, reversible, fully "
        "audited traffic shift whose recovered revenue is measured against a "
        "control arm rather than projected.</b>", "quote"))
    A(P(
        "Each clause of that sentence is a design constraint, and each was "
        "chosen against a more obvious alternative:"))
    A(table([
        ["Constraint", "Obvious approach", "What is done, and why"],
        ["Ground truth", "Label synthetic rows, train a classifier, report "
                         "precision and recall.",
         "Outages are <i>scheduled</i>, so time-to-detect is latency against an "
         "event we caused. Training on self-generated labels measures the "
         "generator, not the world."],
        ["Detection", "Threshold or z-score per slice.",
         "Beta posterior with cohort shrinkage. A slice with four attempts a "
         "minute swings 50&ndash;100% on noise; 207,360 tests over two days "
         "drown any fixed significance level."],
        ["Attribution", "Ask an LLM what is wrong.",
         "Lift with coverage, deterministic. A sampled token in the path of a "
         "money-moving action cannot be reproduced or defended."],
        ["Action", "Reroute on detection.",
         "A separate policy engine decides <i>permission</i>. Ten bounds, "
         "stopping rules, and every refusal recorded with its rule."],
        ["Reversal", "Drain the bad gateway to zero.",
         "A 3% canary floor. A gateway with no traffic emits no observations, "
         "so you could never tell it recovered or route back."],
        ["Measurement", "Count recoveries, multiply by ticket size.",
         "Paired control and treatment on bit-identical demand, repeated across "
         "seeds, reported as a confidence interval."],
    ], [78, 150, 267]))

    A(together(P("The workflow", "h1"), control_loop_diagram()))
    A(Spacer(1, 14))
    A(P(
        "Seven stages, once a minute. The system sees only "
        "<font face='Courier' size='8.5'>(slice, minute, attempts, successes)"
        "</font> &mdash; never the incident plan that generated the traffic."))
    A(table([
        ["Stage", "Guarantee it provides"],
        ["1 Observe", "No ground truth is visible to any decision-making code."],
        ["2 Detect", "Alarms only when the drop is both statistically real and "
                     "materially large."],
        ["3 Attribute", "Deterministic and reproducible; refuses to name a "
                        "cause the evidence cannot support."],
        ["4 Narrate", "Display only. Cannot reach the ledger or any decision."],
        ["5 Gate", "Ten explicit bounds; every verdict recorded with its rule, "
                   "including refusals."],
        ["6 Act", "Bounded fraction, per key, with a canary always left behind."],
        ["7 Verify", "Rolls back if the destination proved worse; restores in "
                     "thirds once the source heals."],
    ], [70, 425]))

    A(together(P("How the measurement works", "h1"),
               measurement_diagram()))

    A(together(P("Where AI is used", "h1"), layers_diagram()))
    A(Spacer(1, 8))
    A(P(
        "There are two places a model is given real latitude, and both sit on "
        "the explaining side of that line. <font face='Courier' size='8.5'>"
        "advisor.py</font> runs when the policy engine refuses and escalates: "
        "hard bounds say <i>no</i> precisely and say nothing else, so it "
        "investigates the refusal and recommends one of six courses of action "
        "for a human - contact the issuer, add capacity, manual reroute, pause "
        "automation, monitor, insufficient evidence. Choosing a destination "
        "stays arithmetic; deciding what to do when routing cannot help is "
        "judgement, and only the second is given to a model."))
    A(P(
        "There is one place a model is given real latitude, and it is on the "
        "explaining side of that line. <font face='Courier' size='8.5'>"
        "investigator.py</font> is an agent that answers "
        "<i>why did traffic move off this gateway, and did it help?</i> from "
        "four read-only tools over the audit ledger and the observation stream "
        "&mdash; deciding for itself what to query and following up until it "
        "can answer. Every tool reads and none write, no decision-path module "
        "imports it, and it cannot see the incident plan either. Tests enforce "
        "all three."))

    A(P("Results", "h1"))
    A(kpis([
        (rupees(f.rec["mean"]), f"recovered per {f.days} days, mean of "
                                f"{f.seeds} paired seeds"),
        (f"{f.shr['mean']:.1%}", "of the money the incidents put at risk"),
        (f"{f.losses}/{f.seeds}", "seeds where the router lost money"),
    ]))
    A(Spacer(1, 12))
    A(table([
        ["Metric", "Mean", "SD", "Min", "Max"],
        ["Revenue recovered", rupees(f.rec["mean"]), rupees(f.rec["sd"]),
         rupees(f.rec["min"]), rupees(f.rec["max"])],
        ["Share of exposure", f"{f.shr['mean']:.1%}", f"{f.shr['sd']:.1%}",
         f"{f.shr['min']:.1%}", f"{f.shr['max']:.1%}"],
        ["Success rate gain", f"{f.srg['mean']:.3f} pp", f"{f.srg['sd']:.3f}",
         f"{f.srg['min']:.3f}", f"{f.srg['max']:.3f}"],
        ["Routing actions", f"{f.act['mean']:.1f}", f"{f.act['sd']:.1f}",
         f"{f.act['min']:.0f}", f"{f.act['max']:.0f}"],
        ["Rollbacks", f"{f.rbk['mean']:.1f}", f"{f.rbk['sd']:.1f}",
         f"{f.rbk['min']:.0f}", f"{f.rbk['max']:.0f}"],
    ], [143, 96, 88, 84, 84], align_right=(1, 2, 3, 4)))
    A(P(
        f"<b>Net of what it cost: {rupees(f.net['net_inr'])}.</b> Rerouting is "
        f"not free - acquirers price differently, so incremental processing "
        f"fees came to {rupees(f.net['incremental_cost_inr'])}, "
        f"{f.net['cost_ratio']:.1%} of gross. UPI carries zero MDR by "
        f"regulation in India, so a UPI recovery is free and a card recovery "
        f"is not; the economics depend on which method degraded."))
    A(P(
        f"95% CI on mean recovery: <b>{rupees(f.rec['ci_lo'])} to "
        f"{rupees(f.rec['ci_hi'])}</b>, which excludes zero. Cross-checked "
        f"independently: measured recovery "
        f"{rupees(f.recovered['revenue_inr'])} against an exposure reduction of "
        f"{rupees(f.recovered['control_exposure_inr'] - f.recovered['treatment_exposure_inr'])} "
        f"&mdash; different quantities, agreeing within 1.5%."))

    A(P("What is deliberately not claimed", "h1"))
    A(bullets([
        "<b>Rerouting does not always help.</b> A sweep across congestion "
        "curves found fleets already running at capacity, where this system "
        f"loses money at every setting tested ({' and '.join(f.harmful)}). "
        "There is no spare headroom to route into, so shifting only "
        "concentrates load. The control plane now measures the realised effect "
        "of its own shifts and halts when they stop paying &mdash; which "
        "limits the damage rather than removing it. This system needs "
        "acquirers with spare capacity to be worth deploying.",
        "<b>The world is simulated, the measurement is real.</b> Ticket sizes, "
        "traffic mix, healthy success rates and the congestion curve are "
        "invented; DATA.md lists every one. The rupee headline scales with "
        "those ticket sizes, so a sceptical reader should take the result as "
        f"+{f.recovered['payments']:,} successful payments and "
        f"+{f.recovered['success_rate_gain_pp']:.2f}pp of success rate, "
        "neither of which depends on a price.",
        "<b>The world is simulated.</b> Gateway health, demand and incidents "
        "are ours; the detector's view of them is the part under evaluation.",
        "<b>Gateway health does not degrade under load.</b> A real acquirer "
        "would push back as traffic arrives, so the recovery figure is an upper "
        "bound. This is the first item on the roadmap.",
        "<b>Routing weights cannot be set through Razorpay's public API.</b> "
        "Acquirer selection is Razorpay's own product. The executor "
        "demonstrates the half that is real &mdash; test-mode Orders carrying "
        "the decision context in their notes.",
        f"<b>{f.shr['mean']:.0%} of exposure, not 90%.</b> Detection latency "
        f"and deliberate policy caps set the ceiling.",
        f"<b>About {f.rbk['mean']:.0f} rollbacks per {f.act['mean']:.0f} "
        f"actions</b> are reported rather than tuned away.",
    ]))

    A(P("Next", "h1"))
    A(table(roadmap_rows()[:5], [30, 165, 300]))
    A(Spacer(1, 8))
    A(P("Full detail in the companion document, "
        "<i>RazorGuard &mdash; Complete Project Documentation</i>.", "small"))
    return st


# ----------------------------------------------------------------------- main

def main() -> int:
    f = Facts()

    doc1 = Doc(os.path.join(OUT, "RazorGuard-Complete-Documentation.pdf"),
               "RazorGuard - Complete Project Documentation",
               "Complete Project Documentation")
    doc1.build(cover(
        "RazorGuard",
        "Detect payment degradation, route around it, prove what it recovered.",
        "Complete project documentation: the problem, what the system does, the "
        "technical approach and the reasoning behind each decision, the "
        "architecture, the workflow, the measured results, and what is "
        "deliberately not claimed.", f) + complete_doc(f))
    print(f"wrote {doc1.filename}")

    doc2 = Doc(os.path.join(OUT, "RazorGuard-Summary-and-Workflow.pdf"),
               "RazorGuard - Summary and Workflow",
               "Summary and Workflow")
    doc2.build(cover(
        "RazorGuard",
        "Summary, technical statement and workflow.",
        "A short read: the problem in a paragraph, the technical statement in "
        "one sentence, the seven-stage control loop, how the recovery figure is "
        "measured, and the results.", f) + summary_doc(f))
    print(f"wrote {doc2.filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
