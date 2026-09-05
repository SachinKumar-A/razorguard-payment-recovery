"""RazorGuard operator console.

    streamlit run app.py

Shows what the control plane saw, what it decided, and what the decision was
worth -- including the decisions it refused to make.
"""
from __future__ import annotations

import base64
import pathlib
from collections import defaultdict
from typing import Dict, List

import altair as alt
import pandas as pd
import streamlit as st

from razorguard.config import WorldConfig
from razorguard.control_plane import ControlPlane
from razorguard.detectors import default_detector
from razorguard.economics import net_recovery
from razorguard.experiment import exposure_inr
from razorguard.policy import PolicyConfig, PolicyEngine
from razorguard.scenarios import default_incident_plan
from razorguard.world import World

st.set_page_config(page_title="RazorGuard", page_icon="assets/razorguard-mark.svg",
                   layout="wide", initial_sidebar_state="expanded")

# Razorpay's palette. Referenced on purpose - this is a submission to their
# buildathon and it should look like it belongs beside their product - but every
# asset here is original and their logo is not reproduced anywhere.
BRAND = {
    "blue": "#3395FF",
    "blue_dark": "#1B6FD1",
    "navy": "#02042B",
    "ink": "#0D2366",
    "muted": "#5A6B8C",
    "surface": "#F4F7FC",
    "line": "#E3EAF5",
    "good": "#0F9D58",
    "warn": "#B45309",
    "bad": "#FF4D57",
}

st.markdown(f"""
<style>
  .block-container {{ padding-top: 2.2rem; max-width: 1280px; }}
  h1, h2, h3 {{ color: {BRAND['navy']}; letter-spacing: -0.02em; }}
  h1 {{ font-weight: 700; }}
  [data-testid="stMetricValue"] {{
      color: {BRAND['navy']}; font-weight: 700; letter-spacing: -0.02em;
  }}
  [data-testid="stMetricLabel"] {{ color: {BRAND['muted']}; }}
  [data-testid="stMetric"] {{
      background: {BRAND['surface']};
      border: 1px solid {BRAND['line']};
      border-radius: 12px;
      padding: 16px 18px;
  }}
  section[data-testid="stSidebar"] {{
      background: {BRAND['navy']};
  }}
  section[data-testid="stSidebar"] * {{ color: #E8EEF9 !important; }}
  section[data-testid="stSidebar"] strong {{ color: #FFFFFF !important; }}
  .stTabs [data-baseweb="tab"] {{ font-weight: 600; }}
  .stTabs [aria-selected="true"] {{ color: {BRAND['blue']}; }}
  .rg-rule {{
      height: 3px; border: 0; border-radius: 2px; margin: 0 0 18px 0;
      background: linear-gradient(90deg, {BRAND['blue']}, {BRAND['ink']});
  }}
  .rg-note {{
      color: {BRAND['muted']}; font-size: 0.86rem; line-height: 1.5;
  }}
</style>
""", unsafe_allow_html=True)

KIND_COLOUR = {
    "detection": "#B45309", "proposal": "#5A6B8C", "decision": "#3395FF",
    "action": "#0F9D58", "rollback": "#FF4D57", "restore": "#1B6FD1",
}


@st.cache_data(show_spinner="Running control plane...")
def run(days: int, seed: int, routing: bool):
    incidents = default_incident_plan(days)
    world = World(WorldConfig(seed=seed), incidents)
    cp = ControlPlane(world, default_detector(),
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
        "observations": out.observations,
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



# Colour and glyph per decision kind. The trace is the most persuasive thing
# this system produces - it is the difference between "trust me" and "here is
# every decision, including the ones it refused to make" - so it gets rendered
# as cards rather than buried in a dataframe.
CARD = {
    "detection": ("#B45309", "#FFFBF0", "!", "detected"),
    "proposal":  ("#5A6B8C", "#F7F9FD", ">", "proposed"),
    "decision":  ("#3395FF", "#F4F7FC", "?", "decided"),
    "action":    ("#0F9D58", "#F1FBF5", "*", "acted"),
    "rollback":  ("#FF4D57", "#FFF5F5", "<", "rolled back"),
    "restore":   ("#1B6FD1", "#F4F7FC", "+", "restored"),
}


def decision_card(row) -> str:
    colour, bg, glyph, verb = CARD.get(row.kind, ("#5A6B8C", "#F7F9FD", "-", row.kind))
    rule = (f'<span style="background:{colour};color:#fff;border-radius:4px;'
            f'padding:1px 7px;font-size:0.68rem;font-weight:700;'
            f'margin-left:8px;letter-spacing:.02em">{row.rule}</span>'
            if row.rule else "")
    return (
        f'<div style="border-left:3px solid {colour};background:{bg};'
        f'border-radius:0 8px 8px 0;padding:9px 14px;margin:0 0 7px 0">'
        f'<div style="font-size:0.72rem;color:#5A6B8C;letter-spacing:.04em;'
        f'text-transform:uppercase;font-weight:700">'
        f'{glyph} {verb} &nbsp;·&nbsp; {hhmm(row.minute)} &nbsp;·&nbsp; '
        f'{row.subject}{rule}</div>'
        f'<div style="font-size:0.88rem;color:#02042B;margin-top:3px;'
        f'line-height:1.45">{row.summary}</div></div>')


# ---------------------------------------------------------------- sidebar
_LOGO = pathlib.Path(__file__).parent / "assets" / "razorguard-lockup.svg"
if _LOGO.exists():
    st.markdown(
        f'<img src="data:image/svg+xml;base64,'
        f'{base64.b64encode(_LOGO.read_bytes()).decode()}" '
        f'style="height:64px;margin-bottom:6px">', unsafe_allow_html=True)
st.markdown('<hr class="rg-rule">', unsafe_allow_html=True)

st.sidebar.title("RazorGuard")
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

net = net_recovery(control["observations"], treat["observations"],
                   d_rev, d_succ)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Recovered, net of fees", rupees(net.net_inr),
          f"{d_rev / control['exposure']:.1%} of exposure")
c2.metric("Payments saved", f"{d_succ:,}",
          f"{(treat_sr - ctrl_sr) * 100:+.2f}pp success rate")
c3.metric("Routing actions", f"{treat['actions']:,}",
          f"{treat['blocked'] + treat['escalated']:,} refused", delta_color="off")
c4.metric("Rollbacks", f"{treat['rollbacks']:,}",
          f"{treat['restores']:,} restore steps", delta_color="off")

st.markdown(
    f'<div class="rg-note">Gross <b>{rupees(net.gross_inr)}</b> '
    f'&nbsp;−&nbsp; processing fees <b>{rupees(net.incremental_cost_inr)}</b> '
    f'&nbsp;=&nbsp; net <b>{rupees(net.net_inr)}</b>. '
    f'UPI carries zero MDR by regulation in India, so a UPI recovery is free '
    f'and a card recovery is not.<br>'
    f'Both arms faced <b>identical demand</b>: {control["attempts"]:,} attempts '
    f'each. The control arm runs the same detector and raises the same alarms '
    f'&mdash; it simply is not permitted to act.</div>',
    unsafe_allow_html=True)

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

bands = alt.Chart(treat["incidents"]).mark_rect(
    opacity=0.10, color=BRAND["bad"]).encode(
    x="start:Q", x2="end:Q")

line = alt.Chart(long).mark_line(strokeWidth=1.4).encode(
    x=alt.X("minute:Q", title="minute"),
    y=alt.Y("sr:Q", title="success rate", scale=alt.Scale(zero=False)),
    color=alt.Color("run:N", scale=alt.Scale(
        domain=["router on", "router off"],
        range=[BRAND["blue"], "#AEBBD1"]),
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
tab_replay, tab_inc, tab_audit, tab_refused = st.tabs(
    ["Incident replay", "Incidents", "Audit ledger", "What was refused"])

with tab_replay:
    st.markdown("#### Watch one incident, decision by decision")
    st.markdown(
        '<div class="rg-note">Every line below was written by the control '
        'plane at the time. Nothing here is composed for the demo &mdash; '
        'including the refusals, which are the half worth reading.</div>',
        unsafe_allow_html=True)
    st.write("")

    inc_df = treat["incidents"]
    acted_min = treat["ledger"][treat["ledger"]["kind"] == "action"]["minute"]
    labels = []
    for r in inc_df.itertuples():
        n = int(((acted_min >= r.start) & (acted_min < r.end)).sum())
        labels.append(f"{r.id}  ·  {r.kind}  ·  {hhmm(r.start)}  ·  "
                      f"{n} action(s)")

    # Default to an incident that actually provoked a response, so the first
    # thing a visitor sees is the whole loop rather than a quiet detection.
    default = next((i for i, lab in enumerate(labels)
                    if not lab.endswith("0 action(s)")), 0)
    choice = st.selectbox("Incident", range(len(labels)),
                          format_func=lambda i: labels[i], index=default)
    row = inc_df.iloc[choice]

    lo, hi = int(row["start"]) - 8, int(row["end"]) + 30
    window = treat["ledger"]
    window = window[(window["minute"] >= lo) & (window["minute"] <= hi)]

    kinds = st.multiselect(
        "Show", ["detection", "proposal", "decision", "action", "rollback",
                 "restore"],
        default=["detection", "decision", "action", "rollback"])
    if kinds:
        window = window[window["kind"].isin(kinds)]

    if window.empty:
        st.info("No ledger entries in this window for the selected kinds.")
    else:
        st.markdown(
            "".join(decision_card(r) for r in window.head(60).itertuples()),
            unsafe_allow_html=True)
        if len(window) > 60:
            st.caption(f"{len(window) - 60} further entries in this window.")

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
            alt.Chart(df).mark_bar(color=BRAND["blue"]).encode(
                x="times fired:Q", y=alt.Y("rule:N", sort="-x", title=None),
                tooltip=["rule:N", "times fired:Q"]).properties(height=240),
            use_container_width=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("A ledger that records only successful actions hides exactly the "
               "decisions worth reviewing.")
