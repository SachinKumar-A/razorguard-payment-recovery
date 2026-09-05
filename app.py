"""RazorGuard operator console.

    streamlit run app.py

Shows what the control plane saw, what it decided, and what the decision was
worth - including the decisions it refused to make.

The charts are the point, and the obvious chart is the wrong one. Success rate
over time draws two lines a percentage point apart, on a scale where a
percentage point is the entire story - so they read as one line and the viewer
concludes nothing happened. The money is therefore drawn twice: once as a
running total, because a quantity that accumulates should be drawn
accumulating, and once as the two arms with every action and rollback marked on
the timeline.
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

# ============================================================================
# DESIGN SYSTEM
#
# Layered dark surfaces rather than one flat ground, Razorpay's blue as the only
# interaction colour, and a separate semantic scale for decision outcomes so a
# colour never means two things at once. Their palette is referenced on purpose
# - this is a submission to their buildathon and should look like it belongs
# beside their product - but every asset here is original and their logo is not
# reproduced anywhere.
# ============================================================================
CSS = """
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {padding-top: 1.6rem; padding-bottom: 4rem; max-width: 1320px;}
body,[class*="st-"]{font-feature-settings:"kern" 1,"liga" 1}
.tnum,.kpi-v,.mono{font-variant-numeric:tabular-nums}

:root{
  --bg:#070A16; --panel2:#0C1224; --panel:#111A30; --line:#1E2A44;
  --text:#EAF1FB; --body:#AFC0D8; --muted:#7286A3;
  --accent:#3395FF; --accent2:#57A9FF; --accent-d:#0E2C55;
  --good:#2FBF71; --warn:#E8A33D; --bad:#FF5A63; --info:#6F93B8;
  --good-b:rgba(47,191,113,.10); --warn-b:rgba(232,163,61,.10);
  --bad-b:rgba(255,90,99,.10);
  --r:12px; --r-s:7px;
}
.stApp{background:
  radial-gradient(1200px 560px at 14% -10%, rgba(51,149,255,.07), transparent 62%),
  var(--bg);}
section[data-testid="stSidebar"]{background:var(--panel2);
  border-right:1px solid var(--line);}
section[data-testid="stSidebar"] *{color:var(--body)!important}
section[data-testid="stSidebar"] h3{color:var(--text)!important;
  font-size:.72rem!important; letter-spacing:.14em; text-transform:uppercase;
  margin-top:1.4rem}
h1,h2,h3,h4{color:var(--text); letter-spacing:-.02em}
p,li,label,span{color:var(--body)}

/* -------------------------------------------------------- masthead ------ */
.mast{display:flex; align-items:center; justify-content:space-between;
  gap:2rem; border-bottom:1px solid var(--line); padding-bottom:1.1rem;
  margin-bottom:1.3rem;}
.mast img{height:54px}
.mast-r{text-align:right; font-size:.74rem; color:var(--muted); line-height:1.75;
  font-variant-numeric:tabular-nums; white-space:nowrap}
.mast-r b{color:var(--text); font-weight:600}
.track{font-size:.66rem; letter-spacing:.16em; text-transform:uppercase;
  color:var(--accent); font-weight:700; margin-bottom:.35rem}

/* ------------------------------------------------------------ kpis ------ */
.kpis{display:grid; grid-template-columns:repeat(4,1fr); gap:14px}
.kpi{background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r); padding:1rem 1.15rem 1.05rem; position:relative;
  overflow:hidden}
.kpi::before{content:""; position:absolute; inset:0 0 auto 0; height:2px;
  background:var(--accent)}
.kpi.good::before{background:var(--good)} .kpi.warn::before{background:var(--warn)}
.kpi-k{font-size:.69rem; letter-spacing:.1em; text-transform:uppercase;
  color:var(--muted); font-weight:700; margin-bottom:.5rem}
.kpi-v{font-size:1.7rem; font-weight:700; color:var(--text); line-height:1.05;
  letter-spacing:-.025em}
.kpi-s{font-size:.75rem; color:var(--muted); margin-top:.35rem}
.kpi-s b{color:var(--body); font-weight:600}

/* ------------------------------------------------------------ note ------ */
.note{background:var(--panel2); border:1px solid var(--line);
  border-left:3px solid var(--accent); border-radius:0 var(--r-s) var(--r-s) 0;
  padding:.8rem 1.05rem; font-size:.83rem; color:var(--body); line-height:1.62;
  margin:.85rem 0 1.3rem; max-width:104ch}
.note b{color:var(--text)}
.sect{font-size:.71rem; letter-spacing:.14em; text-transform:uppercase;
  color:var(--muted); font-weight:700; margin:1.7rem 0 .35rem}

/* ----------------------------------------------------------- cards ------ */
.card{border:1px solid var(--line); border-left:3px solid var(--info);
  background:var(--panel); border-radius:0 var(--r-s) var(--r-s) 0;
  padding:.68rem 1rem .72rem; margin:0 0 .48rem}
.card.detection{border-left-color:var(--warn);
  background:linear-gradient(90deg,var(--warn-b),transparent 55%),var(--panel)}
.card.action{border-left-color:var(--good);
  background:linear-gradient(90deg,var(--good-b),transparent 55%),var(--panel)}
.card.rollback{border-left-color:var(--bad);
  background:linear-gradient(90deg,var(--bad-b),transparent 55%),var(--panel)}
.card.decision{border-left-color:var(--accent)}
.c-h{font-size:.67rem; letter-spacing:.09em; text-transform:uppercase;
  color:var(--muted); font-weight:700; display:flex; gap:.5rem;
  align-items:center; flex-wrap:wrap}
.c-h .g{color:var(--text); font-size:.82rem}
.c-b{font-size:.845rem; color:var(--body); margin-top:.28rem; line-height:1.5;
  max-width:96ch}
.rule{background:var(--accent-d); color:var(--accent2); border-radius:4px;
  padding:.08rem .45rem; font-size:.65rem; font-weight:700;
  font-family:ui-monospace,monospace}
.rule.bad{background:rgba(255,90,99,.14); color:var(--bad)}

/* ------------------------------------------------------------ misc ------ */
.stTabs [data-baseweb="tab-list"]{gap:2px; border-bottom:1px solid var(--line)}
.stTabs [data-baseweb="tab"]{font-weight:600; font-size:.85rem;
  color:var(--muted); padding:.6rem 1rem}
.stTabs [aria-selected="true"]{color:var(--accent)!important}
div[data-testid="stDataFrame"]{border:1px solid var(--line);
  border-radius:var(--r-s)}
.stSelectbox label,.stMultiSelect label{font-size:.76rem!important;
  color:var(--muted)!important; font-weight:600}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

BRAND = {"accent": "#3395FF", "good": "#2FBF71", "warn": "#E8A33D",
         "bad": "#FF5A63", "muted": "#7286A3", "line": "#1E2A44"}

GLYPH = {"detection": ("!", "detected"), "proposal": ("→", "proposed"),
         "decision": ("?", "decided"), "action": ("✓", "acted"),
         "rollback": ("↩", "rolled back"), "restore": ("↺", "restored")}

LOUD_RULES = {"efficacy_breaker", "rollback_target_degraded",
              "no_healthy_destination"}

#: Axis label expression reused by both charts - minutes are stored as integers
#: but a reader wants a clock.
CLOCK = ("'d' + (floor(datum.value/1440)+1) + ' ' + "
         "format(floor(datum.value/60)%24,'02') + ':00'")


def _theme():
    return {"config": {
        "background": "transparent",
        "view": {"stroke": "transparent"},
        "axis": {"domainColor": BRAND["line"], "gridColor": "#16213C",
                 "tickColor": BRAND["line"], "labelColor": BRAND["muted"],
                 "titleColor": BRAND["muted"], "labelFontSize": 10,
                 "titleFontSize": 10, "titleFontWeight": "normal",
                 "gridDash": [2, 3]},
        "legend": {"labelColor": BRAND["muted"], "titleColor": BRAND["muted"],
                   "labelFontSize": 11, "symbolStrokeWidth": 3}}}


alt.themes.register("razorguard", _theme)
alt.themes.enable("razorguard")


# ---------------------------------------------------------------- data

@st.cache_data(show_spinner="Running the control plane…")
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
        return f"₹{x / 1e7:,.2f} cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:,.2f} L"
    return f"₹{x:,.0f}"


def hhmm(minute: int) -> str:
    return f"d{minute // 1440 + 1} {(minute // 60) % 24:02d}:{minute % 60:02d}"


def kpi(label: str, value: str, sub: str, tone: str = "") -> str:
    return (f'<div class="kpi {tone}"><div class="kpi-k">{label}</div>'
            f'<div class="kpi-v">{value}</div><div class="kpi-s">{sub}</div></div>')


def card(row) -> str:
    glyph, verb = GLYPH.get(row.kind, ("·", row.kind))
    rule = (f'<span class="rule{" bad" if row.rule in LOUD_RULES else ""}">'
            f'{row.rule}</span>' if row.rule else "")
    return (f'<div class="card {row.kind}">'
            f'<div class="c-h"><span class="g">{glyph}</span>{verb}'
            f'<span>·</span>{hhmm(row.minute)}<span>·</span>'
            f'<span class="mono">{row.subject}</span>{rule}</div>'
            f'<div class="c-b">{row.summary}</div></div>')


def b64(p: pathlib.Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


HERE = pathlib.Path(__file__).parent
LOGO = HERE / "assets" / "razorguard-lockup.svg"
MARK = HERE / "assets" / "razorguard-mark.svg"

# ------------------------------------------------------------- sidebar

if MARK.exists():
    st.sidebar.markdown(
        f'<div style="text-align:center;padding:.3rem 0 .8rem">'
        f'<img src="data:image/svg+xml;base64,{b64(MARK)}" style="height:58px">'
        f'</div>', unsafe_allow_html=True)

st.sidebar.markdown("### Run")
days = st.sidebar.slider("Simulated days", 1, 3, 2)
seed = st.sidebar.number_input("Seed", value=7, step=1)

cfg = PolicyConfig()
st.sidebar.markdown("### Policy bounds")
st.sidebar.markdown(
    f"""<div style="font-size:.77rem;line-height:1.95">
    max shift per action <b>{cfg.max_shift_fraction:.0%}</b><br>
    max divergence / key <b>{cfg.max_cumulative_divergence:.0%}</b><br>
    min confidence <b>{cfg.min_confidence}</b><br>
    min drop acted on <b>{cfg.min_drop_pp}pp</b><br>
    cooldown per key <b>{cfg.action_cooldown_min} min</b><br>
    causes/hour then stop <b>{cfg.max_causes_per_hour}</b><br>
    target must be <b>{cfg.min_target_advantage_pp}pp</b> healthier<br>
    canary always left <b>3%</b>
    </div>""", unsafe_allow_html=True)

st.sidebar.markdown("### Where AI is used")
st.sidebar.markdown(
    """<div style="font-size:.77rem;line-height:1.7">
    Detection, attribution, policy and routing are <b>deterministic</b>.
    A model writes the operator note, investigates a past decision and advises
    on escalations — and can change none of them.</div>""",
    unsafe_allow_html=True)

control = run(days, seed, routing=False)
treat = run(days, seed, routing=True)

valid = control["attempts"] == treat["attempts"]
d_succ = treat["successes"] - control["successes"]
d_rev = treat["revenue"] - control["revenue"]
ctrl_sr = control["successes"] / control["attempts"]
treat_sr = treat["successes"] / treat["attempts"]
net = net_recovery(control["observations"], treat["observations"], d_rev, d_succ)

# ------------------------------------------------------------ masthead

st.markdown(
    '<div class="mast"><div>'
    + (f'<img src="data:image/svg+xml;base64,{b64(LOGO)}">' if LOGO.exists()
       else '<h2 style="margin:0">RazorGuard</h2>')
    + '</div><div class="mast-r">'
      '<div class="track">Razorpay AI Buildathon · Track 03</div>'
      f'<b>{control["attempts"]:,}</b> payment attempts &nbsp;·&nbsp; '
      f'<b>{len(treat["incidents"])}</b> injected incidents<br>'
      f'<b>{treat["audit_events"]:,}</b> audit events &nbsp;·&nbsp; '
      f'seed <b>{seed}</b> &nbsp;·&nbsp; <b>{days}</b> simulated days'
      '</div></div>', unsafe_allow_html=True)

if not valid:
    st.error("The two arms did not face identical demand. No recovery figure "
             "can be reported from this comparison.")
    st.stop()

# ---------------------------------------------------------------- kpis

st.markdown(
    '<div class="kpis">'
    + kpi("Recovered, net of fees", rupees(net.net_inr),
          f'<b>{d_rev / control["exposure"]:.1%}</b> of money at risk', "good")
    + kpi("Payments saved", f"{d_succ:,}",
          f'<b>+{(treat_sr - ctrl_sr) * 100:.2f}pp</b> success rate', "good")
    + kpi("Actions taken", f'{treat["actions"]:,}',
          f'<b>{treat["blocked"] + treat["escalated"]:,}</b> refused by policy')
    + kpi("Undone by the system", f'{treat["rollbacks"]:,}',
          f'<b>{treat["restores"]:,}</b> restore steps', "warn")
    + '</div>', unsafe_allow_html=True)

st.markdown(
    f'<div class="note"><b>This is a measurement, not a projection.</b> '
    f'The same two days ran twice — once with routing off, once on — and both '
    f'arms saw <b>exactly {control["attempts"]:,}</b> payment attempts. If they '
    f'ever differed, the run would refuse to report a figure at all. The '
    f'control arm is not crippled: it runs the identical detector and raises '
    f'the identical alarms, it simply may not act.<br><br>'
    f'Gross <b>{rupees(net.gross_inr)}</b> − processing fees '
    f'<b>{rupees(net.incremental_cost_inr)}</b> = net '
    f'<b>{rupees(net.net_inr)}</b>. UPI carries zero MDR by regulation in '
    f'India, so a UPI recovery is free and a card recovery is not.</div>',
    unsafe_allow_html=True)

# -------------------------------------------------------------- charts

merged = treat["series"].merge(
    control["series"][["minute", "successes", "success_rate"]],
    on="minute", suffixes=("", "_ctl"))
merged["gap"] = merged["successes"] - merged["successes_ctl"]
merged["cumulative"] = merged["gap"].cumsum()

inc = treat["incidents"].copy()
inc["label"] = inc["kind"].str.replace("_", " ")

acts = treat["ledger"][treat["ledger"]["kind"].isin(["action", "rollback"])].copy()
acts["when"] = acts["minute"].map(hhmm)

bands = alt.Chart(inc).mark_rect(opacity=0.13).encode(
    x="start:Q", x2="end:Q", color=alt.value(BRAND["bad"]),
    tooltip=[alt.Tooltip("id:N", title="incident"),
             alt.Tooltip("label:N", title="kind"),
             alt.Tooltip("blast_radius:N", title="blast radius"),
             alt.Tooltip("slices:Q", title="slices hit")])

st.markdown('<div class="sect">Payments recovered, accumulating</div>',
            unsafe_allow_html=True)

area = alt.Chart(merged).mark_area(
    line={"color": BRAND["accent"], "strokeWidth": 2},
    color=alt.Gradient(
        gradient="linear",
        stops=[alt.GradientStop(color="#0B1D38", offset=0),
               alt.GradientStop(color=BRAND["accent"], offset=1)],
        x1=1, x2=1, y1=1, y2=0)).encode(
    x=alt.X("minute:Q", title=None, axis=alt.Axis(labelExpr=CLOCK)),
    y=alt.Y("cumulative:Q", title="extra successful payments"),
    tooltip=[alt.Tooltip("minute:Q", title="minute"),
             alt.Tooltip("cumulative:Q", title="recovered so far", format=",")])

st.altair_chart((bands + area).properties(height=190), use_container_width=True)
st.markdown(
    f'<div class="note">Every step up is a payment that failed with routing off '
    f'and succeeded with it on. Red bands are the injected incidents — the line '
    f'climbs inside them and flattens between, which is what you would expect '
    f'if the system is doing anything at all. It ends at <b>{d_succ:,}</b>.'
    f'</div>', unsafe_allow_html=True)

st.markdown('<div class="sect">Success rate, both arms</div>',
            unsafe_allow_html=True)

long = merged.melt(id_vars="minute",
                   value_vars=["success_rate", "success_rate_ctl"],
                   var_name="arm", value_name="sr")
long["arm"] = long["arm"].map({"success_rate": "router on",
                               "success_rate_ctl": "router off"})

lines = alt.Chart(long).mark_line(strokeWidth=1.5, interpolate="monotone").encode(
    x=alt.X("minute:Q", title=None, axis=alt.Axis(labelExpr=CLOCK)),
    y=alt.Y("sr:Q", title="success rate", axis=alt.Axis(format="%"),
            scale=alt.Scale(zero=False)),
    color=alt.Color("arm:N", title=None, scale=alt.Scale(
        domain=["router on", "router off"],
        range=[BRAND["accent"], "#4A5B78"]),
        legend=alt.Legend(orient="top-right", direction="horizontal")),
    tooltip=["arm:N", alt.Tooltip("sr:Q", format=".2%")])

marks = alt.Chart(acts).mark_point(size=64, filled=True, opacity=.95).encode(
    x="minute:Q", y=alt.value(6),
    shape=alt.Shape("kind:N", title=None, scale=alt.Scale(
        domain=["action", "rollback"], range=["triangle-up", "triangle-down"]),
        legend=alt.Legend(orient="bottom-right", direction="horizontal")),
    color=alt.Color("kind:N", scale=alt.Scale(
        domain=["action", "rollback"],
        range=[BRAND["good"], BRAND["bad"]]), legend=None),
    tooltip=[alt.Tooltip("when:N", title="at"), "kind:N", "subject:N",
             alt.Tooltip("summary:N", title="what")])

st.altair_chart((bands + lines + marks).properties(height=230),
                use_container_width=True)
st.markdown(
    '<div class="note">The two lines sit close together because the gap <i>is</i> '
    'the story — a percentage point of success rate is worth crores at this '
    'volume. Green triangles are routing actions, red ones are rollbacks. Hover '
    'any of them for what the system decided and why.</div>',
    unsafe_allow_html=True)

# ---------------------------------------------------------------- tabs

tab_replay, tab_inc, tab_refused, tab_audit = st.tabs(
    ["Incident replay", "Incidents", "What it refused", "Audit ledger"])

acted_min = treat["ledger"][treat["ledger"]["kind"] == "action"]["minute"]
counts = [int(((acted_min >= r.start) & (acted_min < r.end)).sum())
          for r in inc.itertuples()]

with tab_replay:
    st.markdown(
        '<div class="note">Every line below was written by the control plane at '
        'the time. Nothing is composed for the demo — <b>including the refusals, '
        'which are the half worth reading.</b></div>', unsafe_allow_html=True)

    labels = [f"{r.id}   ·   {r.label}   ·   {hhmm(r.start)}   ·   "
              f"{r.slices} slices   ·   {n} action(s)"
              for r, n in zip(inc.itertuples(), counts)]

    c1, c2 = st.columns([3, 2])
    default = next((i for i, n in enumerate(counts) if n), 0)
    choice = c1.selectbox("Incident", range(len(labels)),
                          format_func=lambda i: labels[i], index=default)
    kinds = c2.multiselect(
        "Show", ["detection", "proposal", "decision", "action", "rollback",
                 "restore"],
        default=["detection", "decision", "action", "rollback"])

    row = inc.iloc[choice]
    lo, hi = int(row["start"]) - 8, int(row["end"]) + 30
    window = treat["ledger"]
    window = window[(window["minute"] >= lo) & (window["minute"] <= hi)]
    if kinds:
        window = window[window["kind"].isin(kinds)]

    if window.empty:
        st.info("No ledger entries in this window for the selected kinds.")
    else:
        st.markdown("".join(card(r) for r in window.head(70).itertuples()),
                    unsafe_allow_html=True)
        if len(window) > 70:
            st.caption(f"{len(window) - 70} further entries in this window.")

with tab_inc:
    view = inc.copy()
    view["actions"] = counts
    view["when"] = view["start"].map(hhmm)
    st.dataframe(
        view[["id", "label", "blast_radius", "when", "duration", "slices",
              "actions"]].rename(columns={"label": "kind",
                                          "duration": "minutes"}),
        use_container_width=True, hide_index=True)
    st.markdown(
        '<div class="note"><b>Issuer-wide faults get zero routing actions, and '
        'that is correct.</b> They degrade every gateway serving that bank at '
        'once, so no healthy destination exists — the system recognises the '
        'signature and escalates rather than shuffling traffic between equally '
        'broken routes to look busy.</div>', unsafe_allow_html=True)

with tab_refused:
    rules = treat["blocked_by_rule"]
    if not rules:
        st.info("No refusals in this run.")
    else:
        df = pd.DataFrame(sorted(rules.items(), key=lambda kv: -kv[1]),
                          columns=["rule", "times"])
        st.altair_chart(
            alt.Chart(df).mark_bar(color=BRAND["accent"], cornerRadiusEnd=3,
                                   height=18).encode(
                x=alt.X("times:Q", title="times fired"),
                y=alt.Y("rule:N", sort="-x", title=None),
                tooltip=["rule:N", "times:Q"]
            ).properties(height=34 * len(df)), use_container_width=True)
    st.markdown(
        '<div class="note">A ledger that records only successful actions hides '
        'exactly the decisions worth reviewing. Every bar here is the system '
        'declining to move money, with the rule that stopped it.</div>',
        unsafe_allow_html=True)

with tab_audit:
    kinds2 = st.multiselect(
        "Event kinds", sorted(treat["ledger"]["kind"].unique()),
        default=["detection", "action", "rollback"], key="audit_kinds")
    view = treat["ledger"]
    if kinds2:
        view = view[view["kind"].isin(kinds2)]
    view = view.copy()
    view["when"] = view["minute"].map(hhmm)
    st.dataframe(view[["seq", "when", "kind", "subject", "rule", "summary"]],
                 use_container_width=True, hide_index=True, height=460)
    st.caption(f'{treat["audit_events"]:,} events in this run. Every money '
               f'action and every refusal, with the rule that decided it.')
