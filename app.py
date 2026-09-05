"""RazorGuard operator console.

    streamlit run app.py

Shows what the control plane saw, what it decided, and what the decision was
worth - including the decisions it refused to make.

Two things drove the design.

The charts are zoomable because the interesting events are minutes wide inside
a two-day window. A static view of 2,880 minutes renders every incident as a
smudge; you have to be able to scroll into 20:05 and read what happened. Wheel
to zoom, drag to pan, and a crosshair reports the values under the pointer.

The layout follows a trading terminal rather than a dashboard: the chart takes
the room, a fixed rail beside it carries the numbers that do not change as you
pan, and the decision record sits underneath. Structure and design tokens are
adapted from the CYBER SNS console - layered dark surfaces rather than one flat
ground, one accent used sparingly, and a separate semantic scale for outcomes so
a colour never means two things at once.
"""
from __future__ import annotations

import base64
import pathlib
from collections import defaultdict
from typing import Dict, List, Tuple

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
# ICONS
# Inline SVG so glyphs inherit colour and size from CSS instead of arriving as
# emoji that render differently on every machine.
# ============================================================================
_ICONS = {
    "shield": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
    "activity": "M22 12h-4l-3 9L9 3l-3 9H2",
    "trending": "M23 6l-9.5 9.5-5-5L1 18M17 6h6v6",
    "alert": "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01",
    "check": "M20 6L9 17l-5-5",
    "undo": "M3 7v6h6M3 13a9 9 0 103-7.7L3 8",
    "ban": "M12 22a10 10 0 100-20 10 10 0 000 20zM4.9 4.9l14.2 14.2",
    "layers": "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
    "sliders": "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
    "cpu": "M4 4h16v16H4zM9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3",
    "clock": "M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2",
    "search": "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.35-4.35",
    "coin": "M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v12M15 9.5a2.5 2.5 0 00-2.5-2h-1a2.5 2.5 0 000 5h1a2.5 2.5 0 010 5h-1A2.5 2.5 0 019 20.5",
}


def icon(name: str, size: int = None) -> str:
    d = _ICONS.get(name, "")
    dim = f' width="{size}" height="{size}"' if size else ""
    return (f'<svg{dim} viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true"><path d="{d}"/></svg>')


# ============================================================================
# DESIGN SYSTEM
# ============================================================================
CSS = """
<style>
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding-top:1.5rem; padding-bottom:4rem; max-width:1420px;}
body,[class*="st-"]{font-feature-settings:"kern" 1,"liga" 1}
.n,.rail-v,.mono,.tnum{font-variant-numeric:tabular-nums}

:root{
  --bg:#080B12; --panel2:#0C1018; --panel:#111722; --raised:#161E2C;
  --line:#1D2735; --line2:#2A3648;
  --text:#E8EEF7; --body:#AEBDCF; --muted:#75879C;
  --accent:#38BDF8; --accent2:#7DD3FC; --accent-d:#0A3446;
  --good:#3FB87C; --warn:#E0A13C; --bad:#F2564F; --info:#6F93B8;
  --good-b:rgba(63,184,124,.10); --warn-b:rgba(224,161,60,.10);
  --bad-b:rgba(242,86,79,.10);
  --r:10px; --r-s:6px;
}
.stApp{background:
  radial-gradient(1100px 520px at 18% -8%, rgba(56,189,248,.055), transparent 60%),
  var(--bg)}
h1,h2,h3,h4{color:var(--text); letter-spacing:-.02em}
p,li,label,span{color:var(--body)}

/* ---------------------------------------------------------- sidebar ----- */
section[data-testid="stSidebar"]{background:var(--panel2);
  border-right:1px solid var(--line)}
section[data-testid="stSidebar"] *{color:var(--body)!important}
.brand{display:flex; align-items:center; gap:.65rem; padding:0 0 1rem;
  border-bottom:1px solid var(--line); margin-bottom:.3rem}
.brand img{width:38px; height:38px; flex:none}
.brand .nm{font-size:.95rem; font-weight:700; color:var(--text)!important;
  line-height:1.15; letter-spacing:-.01em}
.brand .tag{font-size:.63rem; color:var(--muted)!important; letter-spacing:.06em;
  text-transform:uppercase; margin-top:.12rem}
.sbhead{display:flex; align-items:center; gap:.45rem; font-size:.66rem;
  letter-spacing:.15em; text-transform:uppercase; color:var(--muted)!important;
  font-weight:700; border-top:1px solid var(--line); padding-top:.9rem;
  margin:1.1rem 0 .55rem}
.sbhead svg{width:13px; height:13px; stroke:var(--muted); flex:none}
.sbhead.first{border-top:none; padding-top:0; margin-top:.9rem}
.sbl{font-size:.76rem; line-height:1.95; color:var(--body)!important}
.sbl b{color:var(--text)!important; font-weight:600}

/* --------------------------------------------------------- masthead ----- */
.mast{border-bottom:1px solid var(--line); padding-bottom:1.15rem;
  margin-bottom:1.3rem; display:flex; align-items:flex-end;
  justify-content:space-between; gap:2rem; flex-wrap:wrap}
.mast .eyebrow{display:flex; align-items:center; gap:.45rem; font-size:.68rem;
  letter-spacing:.16em; color:var(--accent); text-transform:uppercase;
  font-weight:700; margin-bottom:.55rem}
.mast .eyebrow svg{width:14px; height:14px; stroke:var(--accent)}
.mast img{height:46px; display:block; margin-bottom:.45rem}
.mast .sub{color:var(--body); font-size:.88rem; max-width:76ch; margin:0}
.mast-r{text-align:right; font-size:.74rem; color:var(--muted); line-height:1.8;
  white-space:nowrap}
.mast-r b{color:var(--text); font-weight:600}

/* ------------------------------------------------------------ funnel ---- */
.funnel{display:grid; grid-template-columns:repeat(5,1fr); gap:.7rem;
  margin-bottom:.4rem}
.fcell{background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r); padding:.85rem 1rem .95rem; position:relative}
.fcell.good{background:linear-gradient(180deg,var(--good-b),transparent 62%),var(--panel)}
.fcell.warn{background:linear-gradient(180deg,var(--warn-b),transparent 62%),var(--panel)}
.fcell.bad{background:linear-gradient(180deg,var(--bad-b),transparent 62%),var(--panel)}
.fcell .kh{color:var(--muted); margin-bottom:.35rem}
.fcell .kh svg{width:15px; height:15px}
.fcell.good .kh{color:var(--good)} .fcell.warn .kh{color:var(--warn)}
.fcell.bad .kh{color:var(--bad)}
.fcell .n{font-size:1.42rem; font-weight:700; color:var(--text);
  line-height:1.05; letter-spacing:-.025em}
.fcell .k{font-size:.7rem; color:var(--muted); margin-top:.3rem; line-height:1.35}

/* -------------------------------------------------------------- rail ---- */
.rail{background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r); padding:.35rem 1rem .8rem}
.rail-h{font-size:.65rem; letter-spacing:.15em; text-transform:uppercase;
  color:var(--muted); font-weight:700; border-bottom:1px solid var(--line);
  padding:.8rem 0 .55rem; margin-bottom:.15rem}
.rail-r{display:flex; justify-content:space-between; align-items:baseline;
  gap:1rem; padding:.34rem 0; border-bottom:1px solid rgba(29,39,53,.55);
  font-size:.79rem}
.rail-r:last-child{border-bottom:none}
.rail-k{color:var(--muted)}
.rail-v{color:var(--text); font-weight:600}
.rail-v.good{color:var(--good)} .rail-v.bad{color:var(--bad)}
.rail-v.warn{color:var(--warn)}

/* -------------------------------------------------------------- note ---- */
.note{background:var(--panel2); border:1px solid var(--line);
  border-left:3px solid var(--accent); border-radius:0 var(--r-s) var(--r-s) 0;
  padding:.78rem 1.05rem; font-size:.82rem; color:var(--body); line-height:1.6;
  margin:.7rem 0 1.1rem; max-width:108ch}
.note b{color:var(--text)}
.sect{display:flex; align-items:center; gap:.45rem; font-size:.68rem;
  letter-spacing:.15em; text-transform:uppercase; color:var(--muted);
  font-weight:700; margin:1.4rem 0 .3rem}
.sect svg{width:13px; height:13px; stroke:var(--muted)}
.hint{font-size:.72rem; color:var(--muted); margin:.15rem 0 .55rem}

/* ------------------------------------------------------------- cards ---- */
.card{border:1px solid var(--line); border-left:3px solid var(--info);
  background:var(--panel); border-radius:0 var(--r-s) var(--r-s) 0;
  padding:.65rem .95rem .7rem; margin:0 0 .45rem}
.card.detection{border-left-color:var(--warn);
  background:linear-gradient(90deg,var(--warn-b),transparent 52%),var(--panel)}
.card.action{border-left-color:var(--good);
  background:linear-gradient(90deg,var(--good-b),transparent 52%),var(--panel)}
.card.rollback{border-left-color:var(--bad);
  background:linear-gradient(90deg,var(--bad-b),transparent 52%),var(--panel)}
.card.decision{border-left-color:var(--accent)}
.c-h{font-size:.66rem; letter-spacing:.09em; text-transform:uppercase;
  color:var(--muted); font-weight:700; display:flex; gap:.45rem;
  align-items:center; flex-wrap:wrap}
.c-h svg{width:12px; height:12px}
.c-h .t{color:var(--text)}
.c-b{font-size:.84rem; color:var(--body); margin-top:.26rem; line-height:1.5;
  max-width:98ch}
.rule{background:var(--accent-d); color:var(--accent2); border-radius:4px;
  padding:.07rem .42rem; font-size:.64rem; font-weight:700;
  font-family:ui-monospace,monospace}
.rule.bad{background:rgba(242,86,79,.15); color:var(--bad)}

/* -------------------------------------------------------------- misc ---- */
.stTabs [data-baseweb="tab-list"]{gap:2px; border-bottom:1px solid var(--line)}
.stTabs [data-baseweb="tab"]{font-weight:600; font-size:.84rem;
  color:var(--muted); padding:.55rem 1rem}
.stTabs [aria-selected="true"]{color:var(--accent)!important}
div[data-testid="stDataFrame"]{border:1px solid var(--line);
  border-radius:var(--r-s)}
.stSelectbox label,.stMultiSelect label,.stRadio label{font-size:.74rem!important;
  color:var(--muted)!important; font-weight:600}
div[role="radiogroup"]{gap:.35rem}
@media (max-width:1100px){.funnel{grid-template-columns:repeat(2,1fr)}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

C = {"accent": "#38BDF8", "good": "#3FB87C", "warn": "#E0A13C",
     "bad": "#F2564F", "info": "#6F93B8", "muted": "#75879C",
     "line": "#1D2735", "text": "#E8EEF7"}

GLYPH = {"detection": ("alert", "detected"), "proposal": ("search", "proposed"),
         "decision": ("sliders", "decided"), "action": ("check", "acted"),
         "rollback": ("undo", "rolled back"), "restore": ("trending", "restored")}
LOUD = {"efficacy_breaker", "rollback_target_degraded", "no_healthy_destination"}


def _theme():
    return {"config": {
        "background": "transparent",
        "view": {"stroke": "transparent"},
        "axis": {"domainColor": C["line"], "gridColor": "#151D29",
                 "tickColor": C["line"], "labelColor": C["muted"],
                 "titleColor": C["muted"], "labelFontSize": 10,
                 "titleFontSize": 10, "titleFontWeight": "normal",
                 "gridDash": [2, 3]},
        "legend": {"labelColor": C["muted"], "titleColor": C["muted"],
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
        "observations": out.observations, "attempts": out.attempts,
        "successes": out.successes, "revenue": out.revenue_inr,
        "actions": out.actions, "blocked": out.blocked,
        "escalated": out.escalated, "rollbacks": out.rollbacks,
        "restores": out.restores, "alarms": len(out.alarms),
        "audit_events": len(out.ledger),
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


def hhmm(m: int) -> str:
    return f"d{m // 1440 + 1} {(m // 60) % 24:02d}:{m % 60:02d}"


def fcell(ic: str, value: str, label: str, tone: str = "") -> str:
    return (f'<div class="fcell {tone}"><div class="kh">{icon(ic)}</div>'
            f'<div class="n">{value}</div><div class="k">{label}</div></div>')


def rail_row(k: str, v: str, tone: str = "") -> str:
    return (f'<div class="rail-r"><span class="rail-k">{k}</span>'
            f'<span class="rail-v {tone}">{v}</span></div>')


def card(row) -> str:
    ic, verb = GLYPH.get(row.kind, ("layers", row.kind))
    rule = (f'<span class="rule{" bad" if row.rule in LOUD else ""}">{row.rule}'
            f'</span>' if row.rule else "")
    return (f'<div class="card {row.kind}"><div class="c-h">{icon(ic)}'
            f'<span class="t">{verb}</span><span>·</span>{hhmm(row.minute)}'
            f'<span>·</span><span class="mono">{row.subject}</span>{rule}</div>'
            f'<div class="c-b">{row.summary}</div></div>')


def b64(p: pathlib.Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


HERE = pathlib.Path(__file__).parent
LOGO, MARK = HERE / "assets/razorguard-lockup.svg", HERE / "assets/razorguard-mark.svg"

# ------------------------------------------------------------- sidebar

st.sidebar.markdown(
    '<div class="brand">'
    + (f'<img src="data:image/svg+xml;base64,{b64(MARK)}">' if MARK.exists() else "")
    + '<div><div class="nm">RazorGuard</div>'
      '<div class="tag">Payment recovery</div></div></div>',
    unsafe_allow_html=True)

st.sidebar.markdown(f'<div class="sbhead first">{icon("sliders")}Run</div>',
                    unsafe_allow_html=True)
days = st.sidebar.slider("Simulated days", 1, 3, 2)
seed = st.sidebar.number_input("Seed", value=7, step=1)

cfg = PolicyConfig()
st.sidebar.markdown(f'<div class="sbhead">{icon("shield")}Policy bounds</div>',
                    unsafe_allow_html=True)
st.sidebar.markdown(
    f'<div class="sbl">'
    f'max shift per action <b>{cfg.max_shift_fraction:.0%}</b><br>'
    f'max divergence / key <b>{cfg.max_cumulative_divergence:.0%}</b><br>'
    f'min confidence <b>{cfg.min_confidence}</b><br>'
    f'min drop acted on <b>{cfg.min_drop_pp}pp</b><br>'
    f'cooldown per key <b>{cfg.action_cooldown_min} min</b><br>'
    f'causes/hour then stop <b>{cfg.max_causes_per_hour}</b><br>'
    f'target must be <b>{cfg.min_target_advantage_pp}pp</b> healthier<br>'
    f'canary always left <b>3%</b></div>', unsafe_allow_html=True)

st.sidebar.markdown(f'<div class="sbhead">{icon("cpu")}Where AI is used</div>',
                    unsafe_allow_html=True)
st.sidebar.markdown(
    '<div class="sbl">Detection, attribution, policy and routing are '
    '<b>deterministic</b>. A model writes the operator note, investigates a '
    'past decision, and advises on escalations — and can change none of '
    'them.</div>', unsafe_allow_html=True)

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
    f'<div class="eyebrow">{icon("shield")}Razorpay AI Buildathon · Track 03</div>'
    + (f'<img src="data:image/svg+xml;base64,{b64(LOGO)}">' if LOGO.exists()
       else '<h1 style="margin:0">RazorGuard</h1>')
    + '<p class="sub">Payment degradation detected at the slice a dashboard '
      'cannot see, routed around under written bounds, and the recovery '
      'measured against a control arm rather than projected.</p></div>'
      '<div class="mast-r">'
      f'<b>{control["attempts"]:,}</b> payment attempts<br>'
      f'<b>{len(treat["incidents"])}</b> injected incidents · '
      f'<b>{treat["alarms"]}</b> alarms<br>'
      f'<b>{treat["audit_events"]:,}</b> audit events<br>'
      f'seed <b>{seed}</b> · <b>{days}</b> simulated days'
      '</div></div>', unsafe_allow_html=True)

if not valid:
    st.error("The two arms did not face identical demand. No recovery figure "
             "can be reported from this comparison.")
    st.stop()

st.markdown(
    '<div class="funnel">'
    + fcell("coin", rupees(net.net_inr), "Recovered, net of fees", "good")
    + fcell("trending", f"{d_succ:,}", "Payments saved", "good")
    + fcell("check", f'{treat["actions"]:,}', "Routing actions taken")
    + fcell("ban", f'{treat["blocked"] + treat["escalated"]:,}',
            "Refused by policy", "warn")
    + fcell("undo", f'{treat["rollbacks"]:,}', "Undone by the system", "bad")
    + '</div>', unsafe_allow_html=True)

# --------------------------------------------------------------- charts

merged = treat["series"].merge(
    control["series"][["minute", "successes", "success_rate"]],
    on="minute", suffixes=("", "_ctl"))
merged["gap"] = merged["successes"] - merged["successes_ctl"]
merged["cumulative"] = merged["gap"].cumsum()
merged["clock"] = merged["minute"].map(hhmm)

inc = treat["incidents"].copy()
inc["label"] = inc["kind"].str.replace("_", " ")

acts = treat["ledger"][treat["ledger"]["kind"].isin(["action", "rollback"])].copy()
acts["when"] = acts["minute"].map(hhmm)

total = days * 1440
RANGES: List[Tuple[str, Tuple[int, int]]] = [("Full run", (0, total))]
for d in range(days):
    RANGES.append((f"Day {d + 1}", (d * 1440, (d + 1) * 1440)))
RANGES.append(("Evening peak", (1150, 1350)))
RANGES.append(("Overnight", (120, 320)))

left, right = st.columns([3.15, 1], gap="medium")

with left:
    st.markdown(f'<div class="sect">{icon("activity")}Recovery over time</div>',
                unsafe_allow_html=True)
    pick = st.radio("Window", [r[0] for r in RANGES], horizontal=True,
                    label_visibility="collapsed")
    lo, hi = dict(RANGES)[pick]
    st.markdown(
        '<div class="hint">Scroll to zoom · drag to pan · double-click to '
        'reset · hover for the values under the pointer</div>',
        unsafe_allow_html=True)

    win = merged[(merged["minute"] >= lo) & (merged["minute"] <= hi)]
    winc = inc[(inc["end"] >= lo) & (inc["start"] <= hi)]
    wacts = acts[(acts["minute"] >= lo) & (acts["minute"] <= hi)]

    # Zoom and pan on the x axis only - the y scales are meaningful and should
    # not be squashed by a stray wheel.
    zoom = alt.selection_interval(bind="scales", encodings=["x"])
    # Crosshair: nearest point to the pointer, driving a rule and a readout.
    hover = alt.selection_point(nearest=True, on="pointermove",
                                fields=["minute"], empty=False)

    axis = alt.Axis(labelExpr="'d' + (floor(datum.value/1440)+1) + ' ' + "
                              "format(floor(datum.value/60)%24,'02') + ':' + "
                              "format(datum.value%60,'02')",
                    labelAngle=0, tickCount=7)

    bands = alt.Chart(winc).mark_rect(opacity=.14).encode(
        x="start:Q", x2="end:Q", color=alt.value(C["bad"]),
        tooltip=[alt.Tooltip("id:N", title="incident"),
                 alt.Tooltip("label:N", title="kind"),
                 alt.Tooltip("blast_radius:N", title="blast radius"),
                 alt.Tooltip("slices:Q", title="slices hit")])

    base = alt.Chart(win)

    area = base.mark_area(
        line={"color": C["accent"], "strokeWidth": 2},
        color=alt.Gradient(gradient="linear",
                           stops=[alt.GradientStop(color="#0A1A26", offset=0),
                                  alt.GradientStop(color=C["accent"], offset=1)],
                           x1=1, x2=1, y1=1, y2=0)).encode(
        x=alt.X("minute:Q", title=None, axis=axis,
                scale=alt.Scale(domain=[lo, hi], nice=False)),
        y=alt.Y("cumulative:Q", title="extra successful payments"))

    xrule = base.mark_rule(color=C["muted"], strokeDash=[3, 3]).encode(
        x="minute:Q", opacity=alt.condition(hover, alt.value(.75), alt.value(0)),
        tooltip=[alt.Tooltip("clock:N", title="at"),
                 alt.Tooltip("cumulative:Q", title="recovered so far", format=","),
                 alt.Tooltip("gap:Q", title="this minute", format="+,"),
                 alt.Tooltip("attempts:Q", title="attempts", format=",")]
    ).add_params(hover)

    dot = base.mark_point(size=52, filled=True, color=C["accent"]).encode(
        x="minute:Q", y="cumulative:Q",
        opacity=alt.condition(hover, alt.value(1), alt.value(0)))

    st.altair_chart(
        (bands + area + xrule + dot).add_params(zoom).properties(height=210),
        use_container_width=True)

    st.markdown(
        f'<div class="note">Every step up is a payment that failed with routing '
        f'off and succeeded with it on. Red bands are the injected incidents — '
        f'the line climbs inside them and flattens between, which is what you '
        f'would expect if the system is doing anything at all. Over the full run '
        f'it reaches <b>{d_succ:,}</b>.</div>', unsafe_allow_html=True)

    st.markdown(f'<div class="sect">{icon("trending")}Success rate, both arms'
                f'</div>', unsafe_allow_html=True)

    long = win.melt(id_vars=["minute", "clock"],
                    value_vars=["success_rate", "success_rate_ctl"],
                    var_name="arm", value_name="sr")
    long["arm"] = long["arm"].map({"success_rate": "router on",
                                   "success_rate_ctl": "router off"})

    lines = alt.Chart(long).mark_line(
        strokeWidth=1.6, interpolate="monotone").encode(
        x=alt.X("minute:Q", title=None, axis=axis,
                scale=alt.Scale(domain=[lo, hi], nice=False)),
        y=alt.Y("sr:Q", title="success rate", axis=alt.Axis(format="%"),
                scale=alt.Scale(zero=False)),
        color=alt.Color("arm:N", title=None, scale=alt.Scale(
            domain=["router on", "router off"], range=[C["accent"], "#48586F"]),
            legend=alt.Legend(orient="top-right", direction="horizontal")))

    hover2 = alt.selection_point(nearest=True, on="pointermove",
                                 fields=["minute"], empty=False)
    xrule2 = alt.Chart(win).mark_rule(
        color=C["muted"], strokeDash=[3, 3]).encode(
        x="minute:Q", opacity=alt.condition(hover2, alt.value(.75), alt.value(0)),
        tooltip=[alt.Tooltip("clock:N", title="at"),
                 alt.Tooltip("success_rate:Q", title="router on", format=".2%"),
                 alt.Tooltip("success_rate_ctl:Q", title="router off",
                             format=".2%"),
                 alt.Tooltip("attempts:Q", title="attempts", format=",")]
    ).add_params(hover2)

    marks = alt.Chart(wacts).mark_point(size=72, filled=True, opacity=.95).encode(
        x="minute:Q", y=alt.value(8),
        shape=alt.Shape("kind:N", title=None, scale=alt.Scale(
            domain=["action", "rollback"],
            range=["triangle-up", "triangle-down"]),
            legend=alt.Legend(orient="bottom-right", direction="horizontal")),
        color=alt.Color("kind:N", scale=alt.Scale(
            domain=["action", "rollback"], range=[C["good"], C["bad"]]),
            legend=None),
        tooltip=[alt.Tooltip("when:N", title="at"), "kind:N", "subject:N",
                 alt.Tooltip("summary:N", title="what")])

    st.altair_chart(
        (bands + lines + xrule2 + marks).add_params(zoom).properties(height=250),
        use_container_width=True)

    st.markdown(
        '<div class="note">The two lines sit close because the gap <i>is</i> the '
        'story — a percentage point of success rate is worth crores at this '
        'volume. Green triangles are routing actions, red ones are rollbacks; '
        'hover either for what the system decided and why. Zoom into an '
        'incident band to watch one unfold minute by minute.</div>',
        unsafe_allow_html=True)

with right:
    st.markdown(
        '<div class="rail">'
        '<div class="rail-h">Selected window</div>'
        + rail_row("Range", f"{hhmm(lo)} → {hhmm(hi)}")
        + rail_row("Minutes", f"{hi - lo:,}")
        + rail_row("Incidents", f"{len(winc)}")
        + rail_row("Actions", f"{len(wacts[wacts['kind'] == 'action'])}", "good")
        + rail_row("Rollbacks", f"{len(wacts[wacts['kind'] == 'rollback'])}", "bad")
        + '<div class="rail-h">Money, whole run</div>'
        + rail_row("Gross recovered", rupees(net.gross_inr))
        + rail_row("Processing fees", "−" + rupees(net.incremental_cost_inr), "warn")
        + rail_row("Net recovered", rupees(net.net_inr), "good")
        + rail_row("Share of money at risk", f'{d_rev / control["exposure"]:.1%}')
        + '<div class="rail-h">Both arms</div>'
        + rail_row("Attempts, control", f'{control["attempts"]:,}')
        + rail_row("Attempts, treatment", f'{treat["attempts"]:,}')
        + rail_row("Identical?", "yes", "good")
        + rail_row("Success rate, off", f"{ctrl_sr:.2%}")
        + rail_row("Success rate, on", f"{treat_sr:.2%}", "good")
        + rail_row("Gain", f"+{(treat_sr - ctrl_sr) * 100:.2f}pp", "good")
        + '<div class="rail-h">Decisions</div>'
        + rail_row("Alarms raised", f'{treat["alarms"]:,}')
        + rail_row("Actions", f'{treat["actions"]:,}', "good")
        + rail_row("Blocked", f'{treat["blocked"]:,}', "warn")
        + rail_row("Escalated", f'{treat["escalated"]:,}', "warn")
        + rail_row("Restores", f'{treat["restores"]:,}')
        + '</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint" style="margin-top:.7rem">Both arms must see the '
        'same number of attempts. If they ever differ the run refuses to '
        'report a recovery figure at all.</div>', unsafe_allow_html=True)

# ----------------------------------------------------------------- tabs

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
    w = treat["ledger"]
    w = w[(w["minute"] >= int(row["start"]) - 8) &
          (w["minute"] <= int(row["end"]) + 30)]
    if kinds:
        w = w[w["kind"].isin(kinds)]

    if w.empty:
        st.info("No ledger entries in this window for the selected kinds.")
    else:
        st.markdown("".join(card(r) for r in w.head(70).itertuples()),
                    unsafe_allow_html=True)
        if len(w) > 70:
            st.caption(f"{len(w) - 70} further entries in this window.")

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
            alt.Chart(df).mark_bar(color=C["accent"], cornerRadiusEnd=3,
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
    v = treat["ledger"]
    if kinds2:
        v = v[v["kind"].isin(kinds2)]
    v = v.copy()
    v["when"] = v["minute"].map(hhmm)
    st.dataframe(v[["seq", "when", "kind", "subject", "rule", "summary"]],
                 use_container_width=True, hide_index=True, height=440)
    st.caption(f'{treat["audit_events"]:,} events in this run. Every money '
               f'action and every refusal, with the rule that decided it.')
