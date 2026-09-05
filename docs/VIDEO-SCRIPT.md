# RazorGuard — 5 minute pitch, word for word

Read this aloud. Nobody expects you to improvise, and a read script beats a
remembered one every time.

**Record in segments.** Six clips, not one take. If a line goes wrong, redo that
line — not the video. Ten minutes of recording gets you a clean five.

**Setup**
- Terminal at a large font. 16pt minimum; a judge may watch on a laptop.
- Run every command once before recording so nothing is cold.
- Screen recording plus your own voice. No slides, no webcam needed.
- Speak slower than feels natural. Everyone rushes on camera.

Legend: **[TYPE]** run this · **[POINT]** move the cursor to it · **[PAUSE]**
stop talking for a beat, let them read.

---

## 0:00 – 0:40 · The problem, and the thing most people would hide

*Nothing on screen yet, or the README open.*

> A payment gateway is not one pipe.
>
> Your traffic splits across several acquiring banks, three payment methods, and
> a dozen issuing banks. And failures in that system are almost never global.
> They're narrow. One issuer, on one method, through one gateway.
>
> **[PAUSE]**
>
> Here's why that's hard. Say that slice is four percent of your volume, and it
> collapses from ninety-six percent success down to forty. Your headline success
> rate moves about two points. That's inside normal daily variation. Nobody
> pages. The money just leaves, quietly, until someone spots a pattern in a
> support queue.
>
> **[PAUSE]**
>
> I built a system that catches that. It recovers about one crore twenty lakh
> over two simulated days.
>
> I'm also going to show you the gateway fleets where the same system **loses**
> money at every setting — because I measured that too, and it now detects that
> and shuts itself down.
>
> That measurement is the most useful thing in this project, so I'm putting it
> at the front rather than at the end.

---

## 0:40 – 1:50 · Watch it happen

**[TYPE]** `python -m razorguard.demo --incident INC-0-02`

*Let it run. Talk over the output as it scrolls.*

> This is one incident, replayed decision by decision. Everything here was
> written by the system at the time — none of it is composed for the demo.

**[POINT]** the detection line

> There. A gateway degraded, and it worked out the cause itself: seven out of
> seven alarming slices share one gateway, three times its share of the fleet.
> That's not a language model guessing. It's lift with coverage — deterministic,
> and it gives the same answer every time you run it.

**[POINT]** the proposal, then the decision, then the action

> Proposal. Then the policy engine decides whether it's *allowed* — and it says
> the destination is running twenty-six points better. Then the action: it moved
> fourteen percent of that traffic.

**[PAUSE]** — *scroll to find a refusal, and read it out*

> And this is the part I'd actually want you to look at. That's a **refusal**.
> The policy engine said no, and the rule that said no is right there in the
> ledger.
>
> Most systems log what they did. This one logs what it declined to do, which is
> the half worth reviewing.

---

## 1:50 – 2:45 · Why the number can be trusted

**[TYPE]** `python -m razorguard.experiment --days 2`

> Now the money. This is the part I care most about getting right.
>
> The figure isn't a projection. It's the difference between two runs of the
> **same demand** — one with routing switched off, one with it on. Both arms run
> the identical detector and raise the identical alarms. The control arm simply
> isn't allowed to act.

**[POINT]** the attempts row, which is identical in both columns

> Both arms have to see exactly the same number of payment attempts. If they
> ever diverge, the run refuses to print a recovery figure at all, because the
> comparison would be meaningless.

**[PAUSE]**

> And nothing here learns a label. I schedule the outages, so "time to detect"
> is latency against an event I caused — not agreement with a label I invented.
> Training a model on labels you wrote yourself just measures your own
> generator.

**[POINT]** the net line

> Gross one crore twenty lakh. Fees, one lakh thirty-nine. Net, one crore
> nineteen. Rerouting isn't free — acquirers price differently.
>
> Small detail worth knowing: UPI carries zero MDR by regulation in India. So a
> UPI recovery is free, and a card recovery isn't. The economics depend on which
> method broke.

---

## 2:45 – 3:20 · Eight seeds, not one

**[TYPE]** `python -m razorguard.validate --seeds 8 --days 2`

> One seed can't tell a real effect from a lucky roll, so this runs eight paired
> trials.

**[PAUSE]** — *let the table finish*

> One crore twenty-two lakh mean. Ninety-five percent confidence interval, one
> crore eighteen to one crore twenty-eight. And zero of eight seeds where the
> router lost money.
>
> The interval excludes zero. That's what makes this a measurement rather than
> an anecdote.

---

## 3:20 – 4:20 · Where I was wrong, twice

**[TYPE]** `python -m razorguard.stress --days 2`

> I set the traffic-shift cap at forty percent. Cautious choice. I wrote a
> paragraph in the docs defending it.
>
> Then I measured it.

**[POINT]** the 40% row, then the 80% row

> Twenty-two percent of exposure recovered at my cap. Thirty-seven at eighty
> percent. My careful setting was costing about sixty-one percent of the
> available recovery, and the congestion I thought justified it never showed up.
>
> So I changed it. The paragraph was wrong, and the measurement is in the repo.

**[TYPE]** `python -m razorguard.sensitivity --days 2`

> Then I asked whether that eighty percent depends on a curve I'd guessed at.
> Mostly it doesn't. But the sweep found something worse.

**[POINT]** the two rows of negative numbers

> On gateway fleets already running at capacity, this system loses money at
> **every** setting. There's no spare headroom to route into, so shifting
> traffic just piles load onto something already struggling.
>
> No per-action guardrail can catch that. Every individual move looks perfectly
> reasonable.
>
> **[PAUSE]**
>
> And my first version of this report had a bug. It divided one negative number
> by another, printed minus three hundred percent, and put the word
> "ACCEPTABLE" over the top of it. I caught it, fixed the arithmetic, and then
> built what it had been hiding: the system now measures the real effect of its
> own shifts, and halts when they stop paying.

---

## 4:20 – 4:40 · It's not only a benchmark

**[TYPE]** `docker compose up` *(or just show `streamlit run app.py`, Incident replay tab)*

> The same loop runs as a service. Real payment outcomes go in as aggregated
> counts, recommendations come out. The deployed path is the benchmarked path —
> the service calls the same tick function, and a test asserts they produce
> identical results.
>
> State survives a restart, the ingest endpoint is authenticated, and a lease
> elects one active instance so two replicas can't disagree about the fleet.
>
> It emits recommendations rather than applying them, because acquirer selection
> isn't an endpoint a third party can call. I'd rather say that than pretend.

---

## 4:40 – 5:00 · Where the AI is, and close

**[TYPE]** `python -m razorguard.investigate "why did traffic move off gw_beta at 03:12, and did it help?"`

> Last thing. This is where a model earns its place.
>
> It gets four read-only tools over the run and works out for itself what to
> look at — finds the ledger entries, pulls the traffic, checks whether the
> shift actually helped across every gateway. Multi-step, model-driven.
>
> But every tool reads and none write. No decision-path module can even import
> it. And it can't see the incident plan either, so it's reasoning from exactly
> what the system saw.
>
> Attribution stays deterministic, because a sampled token in the path of a
> money-moving decision can't be reproduced or defended.
>
> **[PAUSE]**
>
> Two hundred and two tests. Every number in the README is produced by a command
> in the repo, and a test fails if any of them drifts.
>
> Thanks for watching.

---

## Recording notes

**Six clips.** 0:00, 0:40, 1:50, 2:45, 3:20, 4:20. Stitch them. Each is short
enough to redo.

**Speak slower.** Read a line, breathe, read the next. Silence while a command
runs is fine — better than filler.

**Don't apologise for anything.** Not the simulation, not the scope. The limits
are stated as findings, which is what they are.

**If you fluff a line**, stop, breathe, say it again. Cut it in the edit. Nobody
sees the takes.

**The one line that matters most** is in the 3:20 segment: *"the paragraph was
wrong, and the measurement is in the repo."* Almost nobody else's video will
contain a sentence like that. Slow down for it.
