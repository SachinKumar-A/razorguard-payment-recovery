"""RevenueGuard operator console.

    streamlit run app.py

Shows what the control plane saw, what it decided, and what the decision was
worth -- including the decisions it refused to make.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import altair as alt
import pandas as pd
import streamlit as st

from revenueguard.config import WorldConfig
from revenueguard.control_plane import ControlPlane
from revenueguard.detectors import PosteriorDropDetector
from revenueguard.experiment import exposure_inr
from revenueguard.policy import PolicyConfig, PolicyEngine
from revenueguard.scenarios import default_incident_plan
from revenueguard.world import World

st.set_page_config(page_title="RevenueGuard", page_icon="::", layout="wide")

KIND_COLOUR = {
    "detection": "#d97706", "proposal": "#64748b", "decision": "#0891b2",
    "action": "#16a34a", "rollback": "#dc2626", "restore": "#7c3aed",
}


@st.cache_data(show_spinner="Running control plane...")
def run(days: int, seed: int, routing: bool):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    cp = ControlPlane(world, PosteriorDropDetector(min_drop_pp=3.0, confidence=0.99),
                      policy=PolicyEngine(PolicyConfig()), enable_routing=routing)
    out = cp.run(days * 24 * 60)

    per_min: Dict[int, List[int]] = defaultdict(lambda: [0, 0])
    for o in out.observations:
        per_min[o.minute][0] += o.attempts
        per_min[o.minute][1] += o.successes
    series = pd.DataFrame(
        [{"minute": m, "attempts": a, "successes": s,
          "success_rate": (s / a) if a else None}
         for m, (a, s) in sorted(per_min.items())])

    ledger = pd.DataFrame([{
        "seq": e.seq, "minute": e.minute, "kind": e.kind, "subject": e.subject,
        "rule": e.rule or "", "summary": e.summary,
    } for e in out.ledger])

    incident_rows = pd.DataFrame([{
        "id": i.incident_id, "kind": i.kind, "blast_radius": i.blast_radius,
        "start": i.start_min, "end": i.end_min, "duration": i.duration,
        "slices": len(i.affected),
    } for i in incidents])

    return {
        "attempts": out.attempts, "successes": out.successes,
        "revenue": out.revenue_inr, "actions": out.actions,
        "blocked": out.blocked, "escalated": out.escalated,
        "rollbacks": out.rollbacks, "restores": out.restores,
        "alarms": len(out.alarms), "audit_events": len(out.ledger),
        "blocked_by_rule": out.ledger.blocked_by_rule(),
        "exposure": exposure_inr(out, world, incidents),
        "series": series, "ledger": ledger, "incidents": incident_rows,
    }


def rupees(x: float) -> str:
    if abs(x) >= 1e7:
        return f"Rs {x / 1e7:,.2f} cr"
    if abs(x) >= 1e5:
        return f"Rs {x / 1e5:,.2f} L"
    return f"Rs {x:,.0f}"


def hhmm(minute: int) -> str:
    return f"d{minute // 1440 + 1} {(minute // 60) % 24:02d}:{minute % 60:02d}"


# ---------------------------------------------------------------- sidebar
st.sidebar.title("RevenueGuard")
st.sidebar.caption("Payment degradation detection and bounded recovery")
days = st.sidebar.slider("Simulated days", 1, 3, 2)
seed = st.sidebar.number_input("Seed", value=7, step=1)
st.sidebar.divider()
cfg = PolicyConfig()
st.sidebar.subheader("Policy bounds")
st.sidebar.write(
    f"- max shift per action: **{cfg.max_shift_fraction:.0%}** of source share\n"
    f"- max divergence per key: **{cfg.max_cumulative_divergence:.0%}**\n"
    f"- min confidence: **{cfg.min_confidence}**\n"
    f"- min drop acted on: **{cfg.min_drop_pp}pp**\n"
    f"- cooldown per key: **{cfg.action_cooldown_min} min**\n"
    f"- stop after: **{cfg.max_actions_per_hour} actions/hour**\n"
    f"- target must be: **{cfg.min_target_advantage_pp}pp** healthier"
)

control = run(days, seed, routing=False)
treat = run(days, seed, routing=True)

# ---------------------------------------------------------------- headline
st.title("Revenue recovered, measured against a control run")

valid = control["attempts"] == treat["attempts"]
if not valid:
    st.error("The two runs did not face identical demand. No recovery figure "
             "can be reported from this comparison.")
    st.stop()

d_succ = treat["successes"] - control["successes"]
d_rev = treat["revenue"] - control["revenue"]
ctrl_sr = control["successes"] / control["attempts"]
treat_sr = treat["successes"] / treat["attempts"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Revenue recovered", rupees(d_rev),
          f"{d_rev / control['exposure']:.1%} of exposure")
c2.metric("Payments saved", f"{d_succ:,}",
          f"{(treat_sr - ctrl_sr) * 100:+.2f}pp success rate")
c3.metric("Routing actions", f"{treat['actions']:,}",
          f"{treat['blocked'] + treat['escalated']:,} refused", delta_color="off")
c4.metric("Rollbacks", f"{treat['rollbacks']:,}",
          f"{treat['restores']:,} restore steps", delta_color="off")

st.caption(
    f"Identical demand in both runs: {control['attempts']:,} attempts. "
    f"The control run detects the same incidents and is simply not permitted "
    f"to act on them."
)

# ---------------------------------------------------------------- chart
st.subheader("Success rate, with incidents and interventions")

merged = treat["series"].merge(
    control["series"][["minute", "success_rate"]], on="minute",
    suffixes=("", "_control"))
long = merged.melt(id_vars="minute",
                   value_vars=["success_rate", "success_rate_control"],
                   var_name="run", value_name="sr")
long["run"] = long["run"].map({"success_rate": "router on",
                               "success_rate_control": "router off"})

bands = alt.Chart(treat["incidents"]).mark_rect(opacity=0.10, color="#dc2626").encode(
    x="start:Q", x2="end:Q")

line = alt.Chart(long).mark_line(strokeWidth=1.4).encode(
    x=alt.X("minute:Q", title="minute"),
    y=alt.Y("sr:Q", title="success rate", scale=alt.Scale(zero=False)),
    color=alt.Color("run:N", scale=alt.Scale(
        domain=["router on", "router off"], range=["#16a34a", "#94a3b8"]),
        legend=alt.Legend(title=None, orient="top-right")),
    tooltip=["minute:Q", "run:N", alt.Tooltip("sr:Q", format=".2%")])

acts = treat["ledger"][treat["ledger"]["kind"].isin(["action", "rollback"])]
marks = alt.Chart(acts).mark_point(size=42, filled=True, opacity=0.85).encode(
    x="minute:Q", y=alt.value(8),
    color=alt.Color("kind:N", scale=alt.Scale(
        domain=list(KIND_COLOUR), range=list(KIND_COLOUR.values())),
        legend=alt.Legend(title=None, orient="bottom")),
    tooltip=["minute:Q", "kind:N", "subject:N", "summary:N"])

st.altair_chart((bands + line + marks).properties(height=340),
                use_container_width=True)
st.caption("Red bands are injected incidents. Dots on the baseline are routing "
           "actions and rollbacks.")

# ---------------------------------------------------------------- tabs
tab_inc, tab_audit, tab_refused = st.tabs(
    ["Incidents", "Audit ledger", "What was refused"])

with tab_inc:
    inc = treat["incidents"].copy()
    acted = treat["ledger"][treat["ledger"]["kind"] == "action"]
    inc["actions"] = [
        int(((acted["minute"] >= r.start) & (acted["minute"] < r.end)).sum())
        for r in inc.itertuples()]
    inc["when"] = inc["start"].map(hhmm)
    st.dataframe(
        inc[["id", "kind", "blast_radius", "when", "duration", "slices", "actions"]],
        use_container_width=True, hide_index=True)
    st.caption(
        "Issuer-wide degradations receive zero routing actions by design: every "
        "gateway reaches the same issuer, so no healthy destination exists and "
        "the system escalates instead of shuffling traffic between equally "
        "broken routes.")

with tab_audit:
    kinds = st.multiselect("Event kinds", sorted(treat["ledger"]["kind"].unique()),
                           default=["detection", "action", "rollback"])
    view = treat["ledger"]
    if kinds:
        view = view[view["kind"].isin(kinds)]
    view = view.copy()
    view["when"] = view["minute"].map(hhmm)
    st.dataframe(view[["seq", "when", "kind", "subject", "rule", "summary"]],
                 use_container_width=True, hide_index=True, height=460)
    st.caption(f"{treat['audit_events']:,} events. Every money action and every "
               f"refusal is here, with the rule that decided it.")

with tab_refused:
    rules = treat["blocked_by_rule"]
    if not rules:
        st.info("No refusals in this run.")
    else:
        df = pd.DataFrame(sorted(rules.items(), key=lambda kv: -kv[1]),
                          columns=["rule", "times fired"])
        st.altair_chart(
            alt.Chart(df).mark_bar(color="#0891b2").encode(
                x="times fired:Q", y=alt.Y("rule:N", sort="-x", title=None),
                tooltip=["rule:N", "times fired:Q"]).properties(height=240),
            use_container_width=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("A ledger that records only successful actions hides exactly the "
               "decisions worth reviewing.")
