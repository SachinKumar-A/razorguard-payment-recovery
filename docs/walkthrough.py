"""Generate the hands-on walkthrough PDF.

    python docs/walkthrough.py

A different document from the other two. The complete documentation explains
why the system is built the way it is; this one is for someone sitting in front
of a terminal who wants to see it work and know what they are looking at.

Every block of output in it was captured from a real run, not written to look
plausible. If a command's output changes, re-capture rather than edit the prose.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reportlab.platypus import PageBreak, Spacer

from build_docs import (Doc, Facts, OUT, P, bullets, code, logo_drawing,
                        table)

# --------------------------------------------------------------- helpers

def step(n: int, title: str):
    return P(f"Step {n} &nbsp;·&nbsp; {title}", "h1")


def what_it_proves(text: str):
    return P(f"<b>What this proves:</b> {text}", "quote")


def build(f: Facts):
    st = []
    A = st.append

    # ------------------------------------------------------------ cover
    A(Spacer(1, 100))
    A(logo_drawing(1.15))
    A(Spacer(1, 16))
    A(P("Walkthrough", "title"))
    A(P("Run it yourself, and know what you are looking at.", "subtitle"))
    A(Spacer(1, 18))
    A(P(
        "A hands-on guide. Every command below is real, every block of output "
        "was captured from an actual run, and each step says what it proves. "
        "Start to finish takes about fifteen minutes; the five-minute path is "
        "on the next page if that is all you have.", "lead"))
    A(PageBreak())

    # -------------------------------------------------- the scenario
    A(P("The problem, as it actually happens", "h1"))
    A(P(
        "It is 20:05 on a Tuesday. Payments are flowing normally &mdash; about "
        "1,300 a minute across three gateways, three methods and eight issuing "
        "banks."))
    A(P(
        "Then one gateway starts failing. Not all of it: UPI and card traffic "
        "through <b>gw_gamma</b> drops from roughly 90% success to 63%. That "
        "gateway carries 18% of volume, so on the dashboard everyone watches, "
        "the overall success rate moves about four points. Well inside a normal "
        "evening's variation."))
    A(P(
        "<b>Nobody is paged.</b> Customers see failed payments, some retry, most "
        "leave. The money goes quietly for thirty-five minutes until somebody "
        "notices a pattern in the support queue &mdash; or until the next "
        "morning's settlement looks wrong."))
    A(P(
        "That is the thirty-five minutes this project is about. What follows "
        "shows the system finding it, deciding what to do, doing it, checking "
        "whether it helped, and undoing it when it did not."))

    A(P("What you will be looking at", "h1"))
    A(table([
        ["Interface", "What it is", "When you use it"],
        ["<b>Command line</b><br/><font size='7.5' color='#5A6B8C'>python -m "
         "razorguard.*</font>",
         "Nine commands. Each one runs a simulation and prints what happened. "
         "This is where the evidence lives.",
         "Verifying the system. Recording the demo."],
        ["<b>HTTP service</b><br/><font size='7.5' color='#5A6B8C'>:8000</font>",
         "The same control loop on a wall-clock timer, taking real payment "
         "outcomes over HTTP and emitting routing recommendations.",
         "Deployment. Feeding it live data."],
        ["<b>Console</b><br/><font size='7.5' color='#5A6B8C'>:8501</font>",
         "A browser view of a completed run: headline numbers, a success-rate "
         "chart, and an incident replay showing every decision as a card.",
         "Showing somebody, rather than telling them."],
    ], [118, 232, 145]))

    A(P("If you only have five minutes", "h1"))
    A(code(
        "pip install -r requirements-dev.txt\n\n"
        "python -m razorguard.demo          # 20s  - watch one incident\n"
        "python -m razorguard.experiment    # 60s  - what it recovered\n"
        "streamlit run app.py               #      - the same, in a browser"))
    A(P(
        "Those three answer the three questions that matter: does it detect, "
        "does the recovery figure mean anything, and does it look like a real "
        "product. The rest of this document is the long version.", "small"))

    A(PageBreak())

    # ------------------------------------------------------------ setup
    A(P("Setup &nbsp;·&nbsp; once", "h1"))
    A(code(
        "git clone <your-repo-url>\n"
        "cd razorguard\n"
        "pip install -r requirements-dev.txt"))
    A(P(
        "That is everything for the walkthrough. Two optional extras, neither "
        "needed to follow this document:"))
    A(table([
        ["Optional", "Gives you", "Without it"],
        ["<font face='Courier' size='8'>pip install razorpay</font><br/>"
         "+ test keys in <font face='Courier' size='8'>.env</font>",
         "Creating real orders in a Razorpay test account (Step 7)",
         "Dry run prints the exact requests it would send"],
        ["<font face='Courier' size='8'>pip install anthropic</font><br/>"
         "+ <font face='Courier' size='8'>ANTHROPIC_API_KEY</font>",
         "The investigation agent answers questions in English (Step 8)",
         "Falls back to printing the relevant ledger window"],
    ], [150, 200, 145]))
    A(P(
        "Nothing in the walkthrough fails without them. Both degrade to "
        "something useful rather than to an error.", "small"))

    # ------------------------------------------------------------ step 1
    A(step(1, "Watch one incident, decision by decision"))
    A(code("python -m razorguard.demo --incident INC-0-02"))
    A(P("Takes about 20 seconds. You will see a timeline like this:"))
    A(code(
        " |d1 20:06  33.3%   276  #......  ! detection Success rate on gateway\n"
        "                                    'gw_gamma' fell 27.1 points (90.5%\n"
        "                                    to 63.4%). It spans 7 of 7 alarming\n"
        "                                    slices, 3.0x its share of the fleet,\n"
        "                                    so the gateway is the common factor.\n"
        "                                  > proposal  shift upi/hdfc off gw_gamma\n"
        "                                    onto gw_beta, health read at slice\n"
        "                                    level (356 attempts over 5min)\n"
        "                                  ? decision  move up to 80% of the share\n"
        "                                    gw_gamma holds onto gw_beta, which is\n"
        "                                    running 26.7pp better\n"
        "                                  * action    moved 14.4% of upi/hdfc\n"
        "                                    from gw_gamma to gw_beta"))
    A(P("Read it left to right. The bar is the health of the affected slices. "
        "The symbols are what the system did:"))
    A(table([
        ["Symbol", "Stage", "Meaning"],
        ["!", "detection", "It noticed, and worked out what the failures share"],
        [">", "proposal", "It picked a destination and said why"],
        ["?", "decision", "The policy engine allowed or refused it"],
        ["*", "action", "Traffic actually moved"],
        ["&lt;", "rollback", "The destination turned out worse; undone"],
        ["+", "restore", "The original healed; traffic easing back"],
    ], [30, 80, 385]))
    A(what_it_proves(
        "The system is not a threshold alarm. It found that seven alarming "
        "slices shared one gateway, at three times that gateway's share of the "
        "fleet, and said so in a sentence a human can check."))

    A(P("Now find the interesting lines", "h2"))
    A(P("Scroll through the same output for these two. They matter more than "
        "the successes."))
    A(code(
        " |d1 20:12  25.0%    52  .......  < rollback  destination gw_beta fell to\n"
        "                                    84.1% against a 90.5% baseline on 82\n"
        "                                    attempts; reverted to baseline weights\n"
        "                                    and escalated"))
    A(P("<b>It undid its own action.</b> It moved traffic to gw_beta, watched "
        "gw_beta get worse, and put the weights back."))
    A(code(
        " |d1 20:27  36.7%    79  ##.....  ? decision  our last 20 shifts changed\n"
        "                                    the affected keys by -1.01pp on\n"
        "                                    average, so rerouting is making things\n"
        "                                    worse rather than better. Halting all\n"
        "                                    shifts for 120 min and escalating.\n"
        "                                    rule: efficacy_breaker"))
    A(P("<b>It stopped itself entirely.</b> Not one bad action &mdash; it "
        "measured that its whole strategy had stopped paying, and halted."))
    A(what_it_proves(
        "Most systems log what they did. This one logs what it refused to do, "
        "and can conclude that it should stop. That is the part almost nothing "
        "else has."))

    A(P("Try other incidents", "h2"))
    A(code(
        "python -m razorguard.demo --list                 # all 18 incidents\n"
        "python -m razorguard.demo --incident INC-0-01    # a 3am outage\n"
        "python -m razorguard.demo --incident INC-0-05    # an issuer-side fault"))
    A(P(
        "<b>INC-0-05 is worth a look.</b> It gets zero routing actions, and that "
        "is correct: an issuer-side fault degrades every gateway serving that "
        "bank at once, so there is no healthy destination to move to. The "
        "system recognises the signature and escalates instead of shuffling "
        "traffic between equally broken routes to look busy."))

    A(PageBreak())

    # ------------------------------------------------------------ step 2
    A(step(2, "What did it actually recover?"))
    A(code("python -m razorguard.experiment --days 2"))
    A(P("About a minute. It runs the same two days twice &mdash; once with "
        "routing switched off, once on."))
    A(code(
        "                     router off        router on         delta\n"
        "  -------------------------------------------------------------\n"
        "  attempts            1,966,458        1,966,458             0\n"
        "  successful payments 1,829,522        1,840,974       +11,452\n"
        "  overall success rate   93.04%           93.62%       +0.58pp\n"
        "  revenue (Rs)    2,404,325,620    2,416,366,240   +12,040,620\n"
        "\n"
        "  Net of what the recovery cost\n"
        "  -------------------------------------------------------------\n"
        "  gross recovered                Rs     12,040,620\n"
        "  incremental processing fees    Rs        138,653\n"
        "  NET recovered                  Rs     11,901,967"))
    A(P("<b>Look at the attempts row first.</b> Both arms saw exactly "
        "1,966,458 payment attempts &mdash; not approximately, exactly. That is "
        "the guard that makes the rest meaningful. If the two runs ever differ, "
        "the command refuses to print a recovery figure at all, because the "
        "comparison would be worthless."))
    A(P(
        "The control arm is not crippled. It runs the identical detector and "
        "raises the identical alarms. It simply is not permitted to act."))
    A(what_it_proves(
        "The number is a <i>difference between two measured runs</i>, not "
        "recovered-transactions multiplied by an average ticket. That is the "
        "difference between a measurement and a projection, and it is the "
        "thing most submissions get wrong."))
    A(P("The net line matters too", "h2"))
    A(P(
        "Rerouting is not free &mdash; acquirers price differently, so moving "
        "volume changes what the merchant pays. Gross minus fees gives net. "
        "One detail falls out of the arithmetic rather than being special-cased: "
        "<b>UPI carries zero MDR by regulation in India</b>, so a UPI recovery "
        "is free and a card recovery is not."))

    # ------------------------------------------------------------ step 3
    A(step(3, "Is that one lucky run?"))
    A(code("python -m razorguard.validate --seeds 8 --days 2"))
    A(P("Three to four minutes; it runs eight paired trials in parallel."))
    A(code(
        "                            mean         sd         min         max\n"
        "  -----------------------------------------------------------------\n"
        f"  revenue recovered   {f.rec['mean']:>11,.0f} {f.rec['sd']:>10,.0f} "
        f"{f.rec['min']:>11,.0f} {f.rec['max']:>11,.0f}\n"
        f"  share of exposure        {f.shr['mean']:>6.1%}     {f.shr['sd']:>6.1%}"
        f"      {f.shr['min']:>6.1%}      {f.shr['max']:>6.1%}\n"
        "\n"
        f"  95% CI on mean recovery:  Rs {f.rec['ci_lo']:,.0f} to "
        f"Rs {f.rec['ci_hi']:,.0f}\n"
        f"  Seeds where the router lost money: {f.losses}/{f.seeds}"))
    A(what_it_proves(
        "One seed cannot tell a real effect from a favourable roll of the dice. "
        "The interval excludes zero across eight independent trials, and the "
        "comparison is paired &mdash; both arms in each seed face bit-identical "
        "demand, so what is left is the policy rather than the weather."))

    A(PageBreak())

    # ------------------------------------------------------------ step 4
    A(step(4, "Where the author was wrong"))
    A(P("These two commands are the reason to take the rest seriously, and "
        "they are the part to show a reviewer."))
    A(code("python -m razorguard.stress --days 2"))
    A(code(
        "  shift cap    recovered  of exposure  actions  rollbk  congested\n"
        "  ---------------------------------------------------------------\n"
        "       20%    2,131,970        6.6%       71      13       0.1%\n"
        "       40%    7,096,530       22.0%       91      26       0.6%\n"
        "       60%    9,882,790       30.6%       90      25       1.6%\n"
        "       80%   12,040,620       37.3%      100      32       2.6%  <- shipped\n"
        "      100%   12,442,360       38.5%       79      33       4.3%"))
    A(P(
        "The traffic-shift cap was originally 40%, chosen as the cautious "
        "setting and defended in writing. This measured it: that cap was "
        "costing about <b>61% of the available recovery</b>, and the congestion "
        "it was implicitly guarding against never arrived. The default moved to "
        "80%, the knee of the curve."))

    A(code("python -m razorguard.sensitivity --days 2"))
    A(P("Around four minutes. It sweeps the cap against four different "
        "congestion curves, because the shipped one is a plausible shape rather "
        "than a measured one."))
    A(code(
        "  curve          20%      40%      60%      80%     100%\n"
        "  ------------------------------------------------------\n"
        "  forgiving     6.6%    22.1%    32.4%    41.9%    43.6%\n"
        "  shipped       6.6%    22.0%    30.6%    37.3%    38.5%\n"
        "  brittle       0.2%    -2.8%    -3.0%    -4.0%    -5.1%\n"
        "  severe       -0.4%    -0.8%    -0.1%    -0.9%    -0.1%"))
    A(P(
        "<b>Read the bottom two rows.</b> On gateway fleets already running at "
        "capacity, this system loses money at <i>every</i> setting. Those fleets "
        "sit past their capacity knee before anything goes wrong, so there is no "
        "spare headroom to route into and shifting traffic only concentrates "
        "load."))
    A(what_it_proves(
        "The project measures its own failure mode and publishes it. That is "
        "also why the efficacy breaker you saw in Step 1 exists &mdash; no "
        "per-action guardrail can see that the whole strategy has stopped "
        "working, so the system watches the realised effect of its own shifts "
        "and halts when they stop paying."))

    A(PageBreak())

    # ------------------------------------------------------------ step 5
    A(step(5, "See it in a browser"))
    A(code("streamlit run app.py"))
    A(P("Opens at <font face='Courier' size='8.5'>localhost:8501</font>. The "
        "first run of a given set of settings takes about forty seconds while "
        "both arms execute; the result is written to <font face='Courier' "
        "size='8.5'>.cache/</font> and every load after that is about a "
        "second. The Docker image ships with the defaults already warmed."))
    A(P("Ten pages, grouped by what you are doing. Every route lives in the "
        "URL &mdash; including the simulation settings &mdash; so the tiles "
        "along the top are real links, a view can be sent to somebody as a "
        "URL, and the address bar describes the run that produced what is on "
        "screen."))
    A(bullets([
        "<b>Top row</b> &mdash; recovered net of fees, payments saved, actions "
        "taken, refusals, rollbacks. Click any tile to land on the page that "
        "explains it.",
        "<b>Overview</b> &mdash; cumulative recovery, then success rate with "
        "the router on and off. Red bands are injected incidents; triangles "
        "are actions and rollbacks. Scroll to zoom into a single incident, "
        "drag to pan, hover for the values under the pointer.",
        "<b>Decision record</b> &mdash; the eighteen incidents as ranked "
        "cards, heaviest first, each carrying the decision path that produced "
        "it: what the drop was, how fast it was caught, how much money was at "
        "risk, which bounds were checked, what was done, and what it was "
        "worth. Cells tinted blue were measured against the control arm; the "
        "rest were declared before the run started.",
        "<b>Incident replay</b> &mdash; one incident's ledger, line by line, "
        "matched by subject as well as by time so an overlapping incident "
        "cannot lend this one its actions.",
        "<b>Recovery</b> &mdash; gross, the fees on it, net, and which "
        "incident each rupee came from. Negative bars are shown, not dropped.",
        "<b>Actions</b> and <b>Rollbacks</b> &mdash; every money movement, and "
        "every one the system undid after measuring the destination.",
        "<b>Refused</b> &mdash; every rule that stopped a money movement, how "
        "often, and what it protects against. Worth more than the successes. "
        "The engine's 93 refusals are reconciled on the page against the 57 "
        "that named a rule, so the two counters cannot be mistaken for a "
        "contradiction.",
        "<b>Audit ledger</b> &mdash; the full append-only record, filterable.",
        "<b>How it was measured</b> &mdash; the paired design, the guard that "
        "refuses to report when the arms diverge, and where the model is and "
        "is not.",
        "<b>Settings</b> &mdash; change the seed, the simulated days, fleet "
        "traffic, and all seven policy bounds, then re-run. These are not "
        "cosmetic: they feed WorldConfig and PolicyConfig directly. Tighten "
        "the shift cap to the original 40% and gross recovery falls from "
        "Rs 1.20 cr to Rs 0.71 cr in front of you.",
    ]))
    A(what_it_proves(
        "The same evidence a terminal shows, in a form you can put in front of "
        "somebody who will not read a log."))

    # ------------------------------------------------------------ step 6
    A(step(6, "Run it as a service"))
    A(P("This is the deployment path &mdash; the control loop on a wall-clock "
        "timer, taking real payment outcomes over HTTP."))
    A(code(
        "# without Docker (this path is what the tests cover)\n"
        "pip install -r requirements-service.txt\n"
        "export RAZORGUARD_HMAC_KEY=$(python -c \"import secrets;"
        "print(secrets.token_hex(32))\")\n"
        "uvicorn razorguard.service:app --host 0.0.0.0 --port 8000\n\n"
        "# or with Docker\n"
        "cp -n .env.example .env      # set RAZORGUARD_HMAC_KEY\n"
        "docker compose up --build"))
    A(P("Then, in another terminal:"))
    A(code(
        "curl localhost:8000/health\n"
        '{\"ok\":true,\"role\":\"active\",\"minute\":0,\"auth\":\"hmac\",...}\n\n'
        "# unauthenticated ingest is refused\n"
        "curl -X POST localhost:8000/ingest -d '{\"outcomes\":[]}'\n"
        "  -> HTTP 401"))
    A(P("Feed it aggregated counts &mdash; never individual payment records, "
        "because the detector does not need one:"))
    A(code(
        "{\"outcomes\":[\n"
        "  {\"gateway\":\"gw_beta\", \"method\":\"upi\", \"issuer\":\"hdfc\",\n"
        "   \"attempts\":400, \"successes\":150},\n"
        "  {\"gateway\":\"gw_alpha\",\"method\":\"upi\", \"issuer\":\"hdfc\",\n"
        "   \"attempts\":600, \"successes\":580}]}"))
    A(table([
        ["Endpoint", "What it gives you"],
        ["GET /health", "Liveness, and whether this instance holds the lease"],
        ["GET /routing", "<b>The output.</b> Recommended weights per diverted key"],
        ["GET /state", "Minute, alarms, actions, rollbacks, ingest counters"],
        ["GET /audit", "The ledger from disk, refusals included"],
        ["GET /metrics", "Prometheus"],
    ], [110, 385]))
    A(what_it_proves(
        "It is a service, not a script. State survives a restart, the ingest "
        "endpoint is authenticated with request signing, and a lease elects one "
        "active instance so two replicas cannot disagree about the fleet."))

    A(PageBreak())

    # ------------------------------------------------------------ step 7
    A(step(7, "Create real orders in Razorpay"))
    A(P("Optional, and free. Test-mode keys need no business verification: sign "
        "up at dashboard.razorpay.com, confirm the toggle reads <b>Test Mode</b>, "
        "then Settings &rarr; API Keys &rarr; Generate Test Key."))
    A(code(
        "# .env\n"
        "RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxxxx\n"
        "RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx"))
    A(code(
        "python -m razorguard.execute --limit 6 --live\n\n"
        "  executing 6 recoveries against Razorpay TEST mode (rzp_test_TYDp...)\n"
        "  [ok ] test  seq=4   upi|sbi   Rs 640.00  order_TYDrGJBKKSVf3x\n"
        "  [ok ] test  seq=8   upi|hdfc  Rs 640.00  order_TYDrGTjnLgI6Dz\n"
        "  ...\n"
        "  6/6 succeeded, mode=test"))
    A(P("Those are real orders. Open the Razorpay dashboard and look at one:"))
    A(code(
        "id      : order_TYDrGJBKKSVf3x     status  : created\n"
        "amount  : 64000  (Rs 640.00)       receipt : rg-4\n"
        "notes   : razorguard_audit_seq : 4\n"
        "          razorguard_subject   : upi|sbi\n"
        "          razorguard_from      : gw_beta\n"
        "          razorguard_to        : gw_alpha\n"
        "          razorguard_reason    : moved 25.6% of upi/sbi from gw_beta\n"
        "                                 to gw_alpha"))
    A(what_it_proves(
        "The reasoning behind a recovery is readable from the Razorpay "
        "dashboard, outside this repository entirely. Without credentials the "
        "same command dry-runs and prints the exact requests it would send, "
        "labelled <font face='Courier' size='8'>mode=dry_run</font> so a "
        "placeholder can never be mistaken for a real order."))
    A(P(
        "A key not beginning <font face='Courier' size='8.5'>rzp_test_</font> "
        "is refused outright, so there is no way to point this at a live "
        "merchant account by accident.", "small"))

    # ------------------------------------------------------------ step 8
    A(step(8, "Ask the system why it did something"))
    A(code(
        'python -m razorguard.investigate \\\n'
        '  "why did traffic move off gw_beta at 03:12, and did it help?"'))
    A(P(
        "An agent with four read-only tools over the run &mdash; search the "
        "audit ledger, pull per-minute traffic for a slice, measure a key's "
        "health across every gateway serving it, rank slices worst-first. It "
        "decides for itself what to query and follows up until it can answer."))
    A(code(
        "python -m razorguard.investigate --list-questions   # suggestions\n"
        "python -m razorguard.investigate --show-tools ...   # see each call"))
    A(what_it_proves(
        "Every tool reads and none write. No decision-path module can import "
        "it, and it cannot see the incident plan either &mdash; so it reasons "
        "from exactly what the system saw. Without an API key it prints the "
        "relevant ledger window instead, which is less useful than an "
        "investigation and much more useful than an error."))

    # ------------------------------------------------------------ step 9
    A(step(9, "Check the claims yourself"))
    A(code("pytest -q          # 202 tests, about four minutes"))
    A(P("Two of those files are worth knowing about:"))
    A(table([
        ["File", "What it enforces"],
        ["tests/test_integrity.py",
         "No detector can import the incident plan. An Observation carries "
         "exactly six fields. No decision-path module imports a language "
         "model. No result value is hardcoded anywhere in the package. And "
         "each clause of the track's stated bar, asserted against a real run."],
        ["tests/test_documented_claims.py",
         "Every headline figure in the README and the submission notes still "
         "matches the benchmark output. Documentation that drifts is a failing "
         "test, not something a reader has to catch."],
    ], [150, 345]))

    A(PageBreak())

    # ---------------------------------------------------------- troubleshooting
    A(P("If something goes wrong", "h1"))
    A(table([
        ["What you see", "Why", "Fix"],
        ["<font face='Courier' size='7.5'>docker: not recognized</font>",
         "Docker Desktop is not installed", "Skip it. The uvicorn path in Step "
         "6 is the one the tests cover."],
        ["<font face='Courier' size='7.5'>docker-credential-desktop not "
         "found</font>",
         "Docker's helper is not on PATH after a fresh install",
         "Open a new terminal, or prepend<br/>"
         "<font face='Courier' size='7'>$env:ProgramFiles\\Docker\\Docker\\"
         "resources\\bin</font>"],
        ["<font face='Courier' size='7.5'>refusing to run against a key that "
         "is not test mode</font>",
         "You generated a live key",
         "Switch the dashboard toggle to Test Mode and regenerate"],
        ["<font face='Courier' size='7.5'>set RAZORPAY_KEY_ID and "
         "RAZORPAY_KEY_SECRET</font>",
         "No .env found, or it is named wrong",
         "The file must be exactly <font face='Courier' size='7.5'>.env</font> "
         "in the project root"],
        ["Service will not start",
         "No credential configured &mdash; deliberate",
         "Set RAZORGUARD_HMAC_KEY. Open is something you opt into, not "
         "something you forget."],
        ["Narration is plain, not written by a model",
         "No ANTHROPIC_API_KEY",
         "Expected. It falls back to a deterministic template, and no result "
         "depends on the model having run."],
    ], [130, 175, 190]))

    A(P("What is simulated, and what is not", "h1"))
    A(P(
        "Worth being clear about before you draw conclusions from any number "
        "above. <b>The world is invented; the measurement is real.</b>"))
    A(table([
        ["Invented", "Measured"],
        ["Gateway names, traffic mix, volumes, the daily curve, healthy success "
         "rates, average ticket sizes, the congestion curve, and the incident "
         "schedule",
         "Detection latency, false-alarm rates, recovered payments, "
         "success-rate gain, processing cost, and every confidence interval"],
    ], [247, 248]))
    A(P(
        f"The rupee figures scale with invented ticket sizes. A sceptical "
        f"reader should take the result as <b>+{f.recovered['payments']:,} "
        f"successful payments</b> and "
        f"<b>+{f.recovered['success_rate_gain_pp']:.2f} percentage points</b> "
        f"of success rate, neither of which depends on a price. "
        f"<font face='Courier' size='8.5'>DATA.md</font> lists every invented "
        f"constant line by line."))
    A(P(
        "The reason invented incidents make the measurement <i>more</i> "
        "trustworthy rather than less: nothing here learns a label. The outages "
        "are scheduled, so time-to-detect is latency against an event we caused "
        "&mdash; not agreement with a label we invented. The detector sees only "
        "(slice, minute, attempts, successes), and a test asserts it cannot "
        "import the incident plan."))

    A(P("Where to go next", "h1"))
    A(table([
        ["Document", "For"],
        ["README.md", "The short version, with the architecture diagram"],
        ["RazorGuard-Complete-Documentation.pdf",
         "Why each decision was made, and the ones measurement overturned"],
        ["RazorGuard-Summary-and-Workflow.pdf", "The five-page version"],
        ["DEPLOY.md", "Running it against real traffic, and what is still open"],
        ["DATA.md", "Every invented constant, and which results depend on it"],
    ], [230, 265]))
    return st


def main() -> int:
    f = Facts()
    doc = Doc(os.path.join(OUT, "RazorGuard-Walkthrough.pdf"),
              "RazorGuard - Walkthrough", "Walkthrough")
    doc.build(build(f))
    print(f"wrote {doc.filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
