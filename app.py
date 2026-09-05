"""RazorGuard operator console.

    streamlit run app.py

Shows what the control plane saw, what it decided, what the decision was worth,
and - the half that matters - what it refused to do.

Three things drove the design.

The charts are zoomable because the interesting events are minutes wide inside a
two-day window. A static view of 2,880 minutes renders every incident as a
smudge; you have to be able to scroll into 20:05 and read what happened. Wheel
to zoom, drag to pan, and a crosshair reports the values under the pointer.

The incident record is a decision card, not a table row. A table can tell you an
incident happened. It cannot tell you what the system saw, which of its own
bounds it checked, why it picked that destination, or why it declined - and that
reasoning is the product. Each card carries its decision path as a factor grid
with the provenance of every input marked, so a reader can see at a glance which
numbers were measured against the control arm and which were declared policy.

The layout follows a trading terminal rather than a dashboard: the chart takes
the room, a fixed rail beside it carries the numbers that do not change as you
pan, and the decision record sits underneath. The design system - layered dark
surfaces rather than one flat ground, a single restrained accent for
interaction, and a separate semantic scale for risk so a colour never means two
things at once - is adapted from the CYBER SNS console.
"""
from __future__ import annotations

import base64
import html
import json
import pathlib
from typing import List, Tuple

import altair as alt
import streamlit as st

from razorguard.console_data import run as console_run
from razorguard.economics import NetRecovery
from razorguard.policy import PolicyConfig

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
    "route": "M6 3v12M6 21a3 3 0 100-6 3 3 0 000 6zM18 9a3 3 0 100-6 3 3 0 000 6zM18 9v3a4 4 0 01-4 4h-3",
    "gauge": "M12 22a10 10 0 100-20 10 10 0 000 20zM12 12l4-4",
    "target": "M12 22a10 10 0 100-20 10 10 0 000 20zM12 18a6 6 0 100-12 6 6 0 000 12zM12 14a2 2 0 100-4 2 2 0 000 4z",
    "database": "M12 8c4.42 0 8-1.34 8-3s-3.58-3-8-3-8 1.34-8 3 3.58 3 8 3zM4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3",
    "zap": "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
    "slash": "M12 22a10 10 0 100-20 10 10 0 000 20zM4.9 4.9l14.2 14.2",
    "lock": "M5 11h14v11H5zM8 11V7a4 4 0 018 0v4",
    "globe": "M12 22a10 10 0 100-20 10 10 0 000 20zM2 12h20M12 2a15 15 0 010 20 15 15 0 010-20z",
}


def icon(name: str, size: int = None) -> str:
    d = _ICONS.get(name, "")
    dim = f' width="{size}" height="{size}"' if size else ""
    return (f'<svg{dim} viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round"><path d="{d}"/></svg>')


def esc(s) -> str:
    return html.escape(str(s))


# ============================================================================
# DESIGN SYSTEM
# Layered dark surfaces rather than one flat ground, a single restrained accent
# for interaction, and a separate semantic scale for outcomes so a colour never
# means two things at once.
# ============================================================================
CSS = """
<style>
#MainMenu, footer, header {visibility:hidden;}
.block-container {padding-top:2.1rem; padding-bottom:4rem; max-width:1500px;}

html{-webkit-text-size-adjust:100%}
body,[class*="st-"]{font-feature-settings:"kern" 1,"liga" 1}
.why,.trace{max-width:80ch}
.ctitle{text-wrap:balance}
.tnum,.score b,.fcell .n{font-variant-numeric:tabular-nums}

:root {
  /* surfaces, darkest to most elevated */
  --bg:      #0a0e14;
  --panel2:  #0d131c;
  --panel:   #121a25;
  --raised:  #17212e;
  --line:    #1e2a38;
  --line2:   #2c3b4d;

  /* text */
  --text:    #e9eff7;
  --body:    #b4c1d1;
  --muted:   #7a8798;

  /* interaction - one accent, used sparingly */
  --accent:  #38bdf8;
  --accent2: #0ea5e9;
  --accent-d:#0b3a4d;

  /* semantic outcome - never reused for interaction */
  --act:     #f2564f;
  --attend:  #e0a13c;
  --track:   #6f93b8;
  --ok:      #3fb87c;
  --act-b:   rgba(242,86,79,.10);
  --attend-b:rgba(224,161,60,.10);
  --track-b: rgba(111,147,184,.10);
  --ok-b:    rgba(63,184,124,.10);

  /* elevation + geometry */
  --r:       10px;
  --r-sm:    6px;
  --sh:      0 1px 2px rgba(0,0,0,.4);
  --sh-lg:   0 8px 26px rgba(0,0,0,.45);
}
body{background:var(--bg)}
.stApp{background:
  radial-gradient(1100px 520px at 18% -8%, rgba(56,189,248,.055), transparent 60%),
  var(--bg)}

/* ---------- masthead ---------- */
.mast {border-bottom:1px solid var(--line); padding-bottom:1.3rem;
  margin-bottom:1.4rem; display:flex; gap:2rem; align-items:flex-start;}
.mast .ml{flex:1; min-width:0}
.mast .eyebrow {display:flex; align-items:center; gap:.5rem; font-size:.7rem;
  letter-spacing:.16em; color:var(--accent); text-transform:uppercase;
  font-weight:600; margin-bottom:.7rem;}
.mast .eyebrow svg{width:14px; height:14px; stroke:var(--accent)}
.mast h1 {font-size:2.3rem; font-weight:700; margin:0 0 .4rem 0; line-height:1.1;
  letter-spacing:-.02em;}
.mast img{height:52px; display:block; margin:0 0 .55rem -2px}
.mast .sub {color:var(--body); font-size:.93rem; max-width:74ch; line-height:1.6;}
.mast-r{flex:none; text-align:right; font-size:.78rem; color:var(--muted);
  line-height:2; border-left:1px solid var(--line); padding-left:1.6rem;}
.mast-r b{color:var(--text); font-variant-numeric:tabular-nums}

/* ---------- context chips ---------- */
.chips {margin:1rem 0 0 0; display:flex; flex-wrap:wrap; gap:.4rem;}
.chip {display:inline-flex; align-items:center; gap:.4rem;
  border:1px solid var(--line2); border-radius:var(--r-sm);
  padding:.28rem .6rem; font-size:.74rem; color:var(--body);
  background:var(--panel2); transition:border-color .18s ease, color .18s ease;}
.chip svg{width:12px; height:12px; stroke:var(--muted); flex:none}
.chip b{color:var(--text); font-weight:600}
.chip.hot {border-color:var(--accent-d); color:var(--accent);
  background:rgba(56,189,248,.07)}
.chip.hot svg{stroke:var(--accent)}
.chip.good{border-color:rgba(63,184,124,.4); color:var(--ok);
  background:var(--ok-b)}
.chip.good svg{stroke:var(--ok)}

/* ---------- KPI cards ---------- */
.funnel {display:grid; grid-template-columns:repeat(5,1fr); gap:.7rem;
  margin:1.4rem 0 .6rem 0;}
.fcell {background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r); padding:.95rem 1rem 1rem; position:relative;
  box-shadow:var(--sh); transition:border-color .18s ease, transform .18s ease;}
.fcell:hover{border-color:var(--line2); transform:translateY(-1px)}
.fcell .kh{display:flex; align-items:center; justify-content:space-between;
  margin-bottom:.65rem}
.fcell .kh svg{width:15px; height:15px; stroke:var(--muted)}
.fcell .n {font-size:1.85rem; font-weight:700; line-height:1;
  letter-spacing:-.02em; font-variant-numeric:tabular-nums;}
.fcell .k {font-size:.7rem; letter-spacing:.06em; color:var(--muted);
  margin-top:.4rem; font-weight:500;}
.fcell.ok     .n{color:var(--ok)}     .fcell.ok     .kh svg{stroke:var(--ok)}
.fcell.act    .n{color:var(--act)}    .fcell.act    .kh svg{stroke:var(--act)}
.fcell.attend .n{color:var(--attend)} .fcell.attend .kh svg{stroke:var(--attend)}
.fcell.drop   .n{color:var(--muted)}
.fcell.ok{background:linear-gradient(180deg,var(--ok-b),transparent 62%),var(--panel)}
.fcell.act{background:linear-gradient(180deg,var(--act-b),transparent 62%),var(--panel)}
.fcell.attend{background:linear-gradient(180deg,var(--attend-b),transparent 62%),var(--panel)}

/* ---------- notes ---------- */
.note {display:flex; gap:.8rem; border:1px solid var(--line);
  border-left:3px solid var(--attend); background:var(--panel);
  padding:.85rem 1.05rem; border-radius:0 var(--r) var(--r) 0;
  font-size:.83rem; color:var(--body); margin:1rem 0 0 0; line-height:1.6;}
.note svg{width:17px; height:17px; stroke:var(--attend); flex:none; margin-top:.15rem}
.note b{color:var(--text)}
.note .nt{font-size:.7rem; letter-spacing:.1em; text-transform:uppercase;
  color:var(--attend); font-weight:600; display:block; margin-bottom:.28rem}
.note.ok{border-left-color:var(--ok)} .note.ok svg{stroke:var(--ok)}
.note.ok .nt{color:var(--ok)}
.note.info{border-left-color:var(--track)} .note.info svg{stroke:var(--track)}
.note.info .nt{color:var(--track)}

/* ---------- section labels ---------- */
.sect{display:flex; align-items:center; gap:.5rem; font-size:.72rem;
  letter-spacing:.15em; text-transform:uppercase; color:var(--muted);
  font-weight:600; margin:1.4rem 0 .55rem 0;}
.sect svg{width:14px; height:14px; stroke:var(--accent)}
.hint{font-size:.72rem; color:var(--muted); margin:.2rem 0 .5rem}

/* ---------- decision cards ---------- */
.card {border:1px solid var(--line); border-radius:12px; background:var(--panel);
  padding:1.25rem 1.4rem; margin-bottom:1rem; position:relative; overflow:hidden;
  transition:border-color .18s ease, transform .18s ease, box-shadow .18s ease;}
.card::before {content:""; position:absolute; left:0; top:0; bottom:0; width:4px;}
.card.act::before    {background:var(--act);}
.card.attend::before {background:var(--attend);}
.card.track::before  {background:var(--track);}
.card:hover{border-color:color-mix(in srgb, var(--track) 45%, var(--line))}
/* The #1 card must win the 60-second test on its own, so it is deliberately
   heavier than the rest: wider spine, more room, larger title. */
.card.hero {padding:2rem 2.2rem; margin-bottom:1.6rem;
  box-shadow:0 0 0 1px rgba(242,86,79,.18), 0 8px 28px rgba(0,0,0,.35);}
.card.hero::before {width:7px;}
.card.hero:hover{transform:translateY(-1px)}

.crow {display:flex; align-items:center; gap:.6rem; margin-bottom:.85rem;
  flex-wrap:wrap;}
.band {font-size:.68rem; font-weight:800; letter-spacing:.13em;
  padding:.28rem .6rem; border-radius:4px; color:#0d1117;}
.band.act {background:var(--act);} .band.attend {background:var(--attend);}
.band.track {background:var(--track);}
.rank {font-size:.8rem; color:var(--muted); font-weight:700;}
.flag {font-size:.66rem; letter-spacing:.08em; border:1px solid var(--attend);
  color:var(--attend); padding:.18rem .5rem; border-radius:4px;}
.flag.ok{border-color:var(--ok); color:var(--ok)}
.spacer {flex:1;}
.score {text-align:right; line-height:1.15;}
.score b {font-size:1.15rem;} .score span {font-size:.62rem; color:var(--muted);
  display:block; letter-spacing:.08em; text-transform:uppercase;}

.ctitle {font-size:1.15rem; font-weight:700; line-height:1.3; margin:0 0 .35rem 0;}
.card.hero .ctitle {font-size:1.75rem; line-height:1.25; letter-spacing:-.01em;}
.cmeta {font-size:.76rem; color:var(--muted); font-family:ui-monospace,monospace;
  margin-bottom:1.1rem;}

/* ---------- explainability: the decision path -----------------------------
   A factor grid rather than a sentence list. Each cell names one input, its
   reading, and where that reading came from - so a reader can separate what
   was measured against the control arm from what was declared policy. */
.chain {display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:.5rem; margin:.35rem 0 1.25rem 0; align-items:stretch;}
.node {border:1px solid var(--line); border-radius:var(--r-sm);
  padding:.65rem .8rem; background:var(--panel2); min-width:0;
  transition:border-color .18s ease, background-color .18s ease;
  display:flex; flex-direction:column; justify-content:flex-start;}
.node:hover{border-color:var(--line2)}
.node .nk {font-size:.6rem; letter-spacing:.12em; text-transform:uppercase;
  color:var(--muted); margin-bottom:.34rem; font-weight:600;}
.node .nv {font-size:.88rem; font-weight:700; margin-bottom:.2rem;
  color:var(--text); line-height:1.25;}
.node .ns {font-size:.67rem; color:var(--muted); line-height:1.35;}
/* measured inputs carry the accent, so what came off the control arm is
   distinguishable from what was written into the policy file */
.node.meas {border-color:var(--accent-d); background:rgba(56,189,248,.05)}
.node.meas .nk{color:var(--accent)} .node.meas .ns {color:var(--accent)}
.node.end {display:flex; align-items:center; justify-content:center;
  padding:.65rem 1rem;}
.node.end.ok     {border-color:var(--ok);     background:var(--ok-b)}
.node.end.attend {border-color:var(--attend); background:var(--attend-b)}
.node.end.track  {border-color:var(--track);  background:var(--track-b)}
.node.end b {font-size:.95rem; letter-spacing:.06em; line-height:1.2;
  text-align:center;}
.node.end.ok b{color:var(--ok)} .node.end.attend b{color:var(--attend)}
.node.end.track b{color:var(--track)}

/* ---------- factor bars ---------- */
.sec {font-size:.66rem; letter-spacing:.13em; text-transform:uppercase;
  color:var(--muted); margin:0 0 .6rem 0;}
.bar {margin-bottom:.6rem;}
.bar .bl {display:flex; justify-content:space-between; gap:1rem;
  font-size:.76rem; margin-bottom:.24rem;}
.bar .bl .src {color:var(--muted); font-size:.68rem; flex:none;
  font-variant-numeric:tabular-nums;}
.bar .track2 {height:5px; background:var(--panel2); border-radius:3px;
  overflow:hidden;}
.bar .fill {height:100%; border-radius:3px;}

/* A haircut is not an additive contribution, so it is not drawn as a bar. */
.mult {display:flex; align-items:baseline; gap:.6rem; margin-top:.8rem;
  padding:.55rem .8rem; border:1px dashed var(--accent2); border-radius:8px;
  background:rgba(14,165,233,.07); flex-wrap:wrap;}
.mult .ml {font-size:.74rem; font-weight:700; color:var(--accent);}
.mult .mv {font-size:.78rem; color:var(--body);}
.mult .ms {margin-left:auto; font-size:.68rem; color:var(--accent);}

/* ---------- prose blocks ---------- */
.why {font-size:.85rem; line-height:1.6; color:var(--body);}
.next {border:1px solid rgba(63,184,124,.55); background:var(--ok-b);
  border-radius:8px; padding:.85rem 1.05rem; margin:1.1rem 0 .9rem 0;}
.next .nl {font-size:.64rem; letter-spacing:.13em; text-transform:uppercase;
  color:var(--ok); margin-bottom:.3rem;}
.next .nt {font-size:.95rem; font-weight:600; line-height:1.45;}
/* Provenance is the graded element, so it must stay legible on a projector -
   quiet, but never small enough to be dismissed as fine print. */
.trace {border-top:1px solid var(--line); padding-top:.85rem; margin-top:.6rem;
  font-size:.78rem; color:#9aa4ae; line-height:1.75;}
.trace b {color:var(--text);}
.trace code {font-family:ui-monospace,monospace; color:var(--body);
  background:var(--panel2); padding:.08rem .35rem; border-radius:4px;}
.card.hero .trace {font-size:.82rem;}

/* ---------- refusals ---------- */
.ex {border:1px solid var(--line); border-left:3px solid var(--muted);
  border-radius:0 8px 8px 0; padding:.85rem 1.1rem; margin-bottom:.7rem;
  background:var(--panel);
  transition:border-left-color .18s ease, background-color .18s ease;}
.ex:hover{border-left-color:var(--act); background:var(--panel2)}
.ex .top {display:flex; align-items:baseline; gap:.8rem; flex-wrap:wrap;}
.ex .id {font-family:ui-monospace,monospace; font-weight:700; font-size:.88rem;}
.ex .pr {color:var(--muted); font-size:.82rem;}
.ex .sev {margin-left:auto; font-size:.72rem; color:var(--muted);}
.ex .sev b {color:var(--act); font-size:.9rem;}
.ex .rsn {font-size:.79rem; color:var(--body); margin-top:.45rem; line-height:1.55;}

/* ---------- scoreboard ----------------------------------------------------
   One claim, its interval, and how it was arrived at. It sits in the sidebar
   because it is the one number a reader should not have to scroll to find. */
.sb {border:1px solid var(--line); border-radius:var(--r); padding:.85rem 1rem;
  margin-bottom:.7rem; background:var(--panel); box-shadow:var(--sh);}
.sb .sbt {display:flex; justify-content:space-between; align-items:baseline;
  margin-bottom:.5rem; gap:.5rem;}
.sb .sbn {font-size:.74rem; font-weight:600; color:var(--muted);}
.sb .sbv {font-size:1.3rem; font-weight:800; color:var(--ok);
  font-variant-numeric:tabular-nums;}
.sb .track2 {height:8px; background:var(--panel2); border-radius:5px;
  overflow:hidden; position:relative;}
.sb .fill {height:100%; border-radius:5px; background:var(--ok); opacity:.55;
  position:absolute;}
.sb .tick {position:absolute; top:-2px; bottom:-2px; width:2px;
  background:var(--ok);}
.sb .cap {font-size:.68rem; color:var(--muted); margin-top:.5rem;
  line-height:1.55;}
.sb .cap b{color:var(--text)}

/* ---------- scoreboard rail ---------- */
.rail{border:1px solid var(--line); border-radius:var(--r);
  background:var(--panel); padding:.4rem .95rem .9rem; box-shadow:var(--sh);}
.rail-h{font-size:.62rem; letter-spacing:.14em; text-transform:uppercase;
  color:var(--muted); font-weight:700; margin:1rem 0 .45rem;
  padding-bottom:.35rem; border-bottom:1px solid var(--line)}
.rail-r{display:flex; justify-content:space-between; gap:.8rem;
  font-size:.78rem; padding:.24rem 0}
.rail-k{color:var(--muted)}
.rail-v{color:var(--text); font-weight:600; font-variant-numeric:tabular-nums}
.rail-v.ok{color:var(--ok)} .rail-v.warn{color:var(--attend)}
.rail-v.bad{color:var(--act)}

/* ---------- ledger lines ---------- */
.lrow{border:1px solid var(--line); border-left:3px solid var(--line2);
  border-radius:0 8px 8px 0; background:var(--panel); padding:.6rem .9rem;
  margin-bottom:.4rem; transition:background-color .18s ease}
.lrow:hover{background:var(--panel2)}
.lrow.action{border-left-color:var(--ok)}
.lrow.rollback{border-left-color:var(--act)}
.lrow.detection{border-left-color:var(--attend)}
.lrow.decision{border-left-color:var(--track)}
.lrow .lh{display:flex; align-items:center; gap:.5rem; font-size:.7rem;
  color:var(--muted); margin-bottom:.28rem; flex-wrap:wrap}
.lrow .lh svg{width:13px; height:13px}
.lrow .lk{font-weight:700; letter-spacing:.08em; text-transform:uppercase}
.lrow.action .lk{color:var(--ok)} .lrow.rollback .lk{color:var(--act)}
.lrow.detection .lk{color:var(--attend)} .lrow.decision .lk{color:var(--track)}
.lrow .mono{font-family:ui-monospace,monospace; color:var(--body)}
.lrow .rule{margin-left:auto; font-family:ui-monospace,monospace; font-size:.66rem;
  border:1px solid var(--line2); border-radius:4px; padding:.1rem .4rem}
.lrow .rule.bad{border-color:var(--act); color:var(--act)}
.lrow .lb{font-size:.83rem; color:var(--text); line-height:1.5}

/* ---------- sidebar ---------- */
section[data-testid="stSidebar"]{border-right:1px solid var(--line);
  background:var(--panel2)}
section[data-testid="stSidebar"] .block-container{padding-top:1.25rem}
.brand{display:flex; align-items:center; gap:.65rem; padding:0 0 1rem;
  border-bottom:1px solid var(--line); margin-bottom:1rem}
.brand img{width:34px; height:34px; flex:none}
.brand .nm{font-size:.95rem; font-weight:700; color:var(--text); line-height:1.15}
.brand .tag{font-size:.63rem; color:var(--muted); letter-spacing:.05em}
.sbhead {display:flex; align-items:center; gap:.45rem; font-size:.64rem;
  letter-spacing:.14em; text-transform:uppercase; color:var(--muted);
  font-weight:700; margin:1.3rem 0 .55rem; padding-top:1rem;
  border-top:1px solid var(--line)}
.sbhead svg{width:13px; height:13px; stroke:var(--muted); flex:none}
.sbhead.first {border-top:none; padding-top:0; margin-top:0;}
.sbl{font-size:.75rem; color:var(--body); line-height:1.85}
.sbl b{color:var(--text); font-variant-numeric:tabular-nums}
section[data-testid="stSidebar"] [data-baseweb="select"] > div,
section[data-testid="stSidebar"] [data-baseweb="input"] > div{
  background:var(--panel); border-color:var(--line)}

/* ---------- navigation ----------------------------------------------------
   The tab strip is this application's navigation, so it is treated as one:
   sticky, glass-backed, with a clear active indicator. Selectors target
   BaseWeb's own attributes rather than generated class names, and every rule
   here is presentational - the widget's behaviour is untouched.             */
.stTabs [data-baseweb="tab-list"]{
  position:sticky; top:0; z-index:60;
  gap:.15rem; padding:.35rem .1rem 0;
  border-bottom:1px solid var(--line);
  /* solid first: if color-mix is unsupported the bar stays opaque and legible
     rather than falling back to transparent over scrolling content */
  background:var(--panel2);
  background:color-mix(in srgb, var(--panel2) 82%, transparent);
  backdrop-filter:saturate(1.6) blur(14px);
  -webkit-backdrop-filter:saturate(1.6) blur(14px);
  overflow-x:auto; overflow-y:hidden;
  scroll-snap-type:x proximity; scrollbar-width:none;
  -webkit-overflow-scrolling:touch;
  mask-image:linear-gradient(90deg,#000 0,#000 calc(100% - 26px),transparent 100%);
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar{display:none}
.stTabs [data-baseweb="tab"]{
  height:44px; padding:0 .95rem; font-size:.845rem; font-weight:500;
  color:var(--muted); white-space:nowrap; scroll-snap-align:start;
  border-radius:7px 7px 0 0; position:relative;
  transition:color .18s ease, background-color .18s ease;
}
.stTabs [data-baseweb="tab"]:hover{color:var(--text); background:var(--panel)}
.stTabs [data-baseweb="tab"][aria-selected="true"]{color:var(--text); font-weight:600}
.stTabs [data-baseweb="tab"]::after{
  content:""; position:absolute; left:50%; right:50%; bottom:0; height:2px;
  background:var(--accent); border-radius:2px 2px 0 0;
  transition:left .22s ease, right .22s ease;
}
.stTabs [data-baseweb="tab"][aria-selected="true"]::after{left:.6rem; right:.6rem}
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"]{background:transparent!important}

/* ---------- widgets ---------- */
[data-baseweb="radio"] label{font-size:.8rem}
.stRadio [role="radiogroup"]{gap:.15rem}
div[data-testid="stDataFrame"]{border:1px solid var(--line); border-radius:var(--r)}

/* ---------- accessibility ---------- */
:focus-visible{outline:2px solid var(--accent); outline-offset:2px; border-radius:4px}
.stTabs [data-baseweb="tab"]:focus-visible{outline-offset:-3px}
@media (prefers-contrast:more){
  :root{--muted:#b8c2cc; --line:#4a545f}
  .stTabs [data-baseweb="tab"][aria-selected="true"]::after{height:3px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition-duration:.01ms!important;
    animation-duration:.01ms!important}
}

/* ---------- responsive ---------- */
@media (max-width:1100px){
  .block-container{padding-left:1.1rem; padding-right:1.1rem}
  .mast{flex-direction:column; gap:1rem}
  .mast-r{border-left:none; padding-left:0; text-align:left}
}
@media (max-width:820px){
  .funnel{grid-template-columns:repeat(2,1fr)}
  .fcell .n{font-size:1.4rem}
  .card.hero .ctitle{font-size:1.4rem}
}
@media (max-width:600px){
  .block-container{padding-top:1.4rem}
  .mast h1{font-size:1.65rem}
  .card{padding:1rem 1.05rem} .card.hero{padding:1.25rem 1.3rem}
  .card.hero .ctitle{font-size:1.22rem}
  .chain{gap:.3rem}
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# The chart library cannot read CSS variables, so the same palette is declared
# once here and every mark takes its colour from it. One source, two consumers.
C = {"bg": "#0a0e14", "panel": "#121a25", "line": "#1e2a38", "text": "#e9eff7",
     "body": "#b4c1d1", "muted": "#7a8798", "accent": "#38bdf8",
     "act": "#f2564f", "attend": "#e0a13c", "track": "#6f93b8", "ok": "#3fb87c"}


@alt.theme.register("razorguard", enable=True)
def _theme():
    ax = {"labelColor": C["muted"], "titleColor": C["muted"],
          "gridColor": C["line"], "domainColor": C["line"], "tickColor": C["line"],
          "labelFontSize": 10, "titleFontSize": 10, "titleFontWeight": "normal",
          "gridOpacity": .5}
    return {"config": {
        "background": "transparent",
        "view": {"stroke": "transparent"},
        "font": "Inter, ui-sans-serif, system-ui, sans-serif",
        "axis": ax, "axisX": ax, "axisY": ax,
        "legend": {"labelColor": C["body"], "titleColor": C["muted"],
                   "labelFontSize": 10, "titleFontSize": 10,
                   "symbolStrokeWidth": 2},
        "range": {"category": [C["accent"], C["ok"], C["attend"], C["act"],
                               C["track"]]}}}



# ============================================================================
# DATA
# One control-plane run per (days, seed, routing). The expensive part lives in
# `razorguard.console_data`, which keeps its own pickle cache on disk; this
# adds Streamlit's in-session memoisation on top so a widget change does not
# even hit the filesystem.
# ============================================================================

run = st.cache_data(show_spinner=False, max_entries=8)(console_run)

# ------------------------------------------------------------------- helpers

def rupees(x: float) -> str:
    if abs(x) >= 1e7:
        return f"\u20b9{x / 1e7:,.2f} cr"
    if abs(x) >= 1e5:
        return f"\u20b9{x / 1e5:,.2f} L"
    return f"\u20b9{x:,.0f}"


def hhmm(m: int) -> str:
    return f"d{m // 1440 + 1} {(m // 60) % 24:02d}:{m % 60:02d}"


def fcell(ic: str, value: str, label: str, tone: str = "") -> str:
    return (f'<div class="fcell {tone}"><div class="kh">{icon(ic)}</div>'
            f'<div class="n">{value}</div><div class="k">{label}</div></div>')


def chip(ic: str, text: str, tone: str = "") -> str:
    return f'<span class="chip {tone}">{icon(ic)}{text}</span>'


def rail_row(k: str, v: str, tone: str = "") -> str:
    return (f'<div class="rail-r"><span class="rail-k">{k}</span>'
            f'<span class="rail-v {tone}">{v}</span></div>')


def node(key: str, value: str, source: str, cls: str = "", title: str = "") -> str:
    """One cell of a decision path: the input, its reading, its provenance."""
    t = f' title="{esc(title)}"' if title else ""
    return (f'<div class="node {cls}"{t}><div class="nk">{esc(key)}</div>'
            f'<div class="nv">{value}</div>'
            f'<div class="ns">{esc(source)}</div></div>')


def bar(label: str, detail: str, pct: float, colour: str, source: str) -> str:
    """A factor bar. Every width is a percentage of a named whole, stated in
    the source column - so no two bars are silently on different scales."""
    w = max(0.0, min(pct, 100.0))
    return (f'<div class="bar"><div class="bl">'
            f'<span>{esc(label)} &mdash; {detail}</span>'
            f'<span class="src">{source}</span></div>'
            f'<div class="track2"><div class="fill" style="width:{w:.0f}%;'
            f'background:{colour}"></div></div></div>')


def b64(p: pathlib.Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


HERE = pathlib.Path(__file__).parent
# The light-ground lockup that heads the PDFs sets its wordmark in near-black,
# which is invisible here. Same geometry, different type colours.
LOGO = HERE / "assets/razorguard-lockup-dark.svg"
MARK = HERE / "assets/razorguard-mark.svg"

KIND_LABEL = {
    "hard_outage": "Gateway hard outage",
    "gradual_degradation": "Gradual gateway degradation",
    "issuer_degradation": "Issuer-wide degradation",
    "shallow_degradation": "Shallow slice degradation",
}
GLYPH = {"detection": "alert", "proposal": "search", "decision": "shield",
         "action": "route", "rollback": "undo", "restore": "check"}
# Rules whose firing is the interesting event, not a footnote.
LOUD = {"efficacy_breaker", "no_healthy_target", "cumulative_divergence",
        "cause_budget", "issuer_wide"}


# ------------------------------------------------------------------- sidebar

st.sidebar.markdown(
    '<div class="brand">'
    + (f'<img src="data:image/svg+xml;base64,{b64(MARK)}">' if MARK.exists() else "")
    + '<div><div class="nm">RazorGuard</div>'
      '<div class="tag">Payment recovery control plane</div></div></div>',
    unsafe_allow_html=True)

st.sidebar.markdown(f'<div class="sbhead first">{icon("sliders")}Run</div>',
                    unsafe_allow_html=True)
days = st.sidebar.slider("Simulated days", 1, 3, 2)
seed = st.sidebar.number_input("Seed", value=7, step=1)

cfg = PolicyConfig()
st.sidebar.markdown(f'<div class="sbhead">{icon("lock")}Policy bounds</div>',
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

VALIDATION = HERE / "bench/results/validation.json"
if VALIDATION.is_file():
    _v = json.loads(VALIDATION.read_text(encoding="utf-8"))
    _r = _v["summary"]["recovered_inr"]
    _seeds = _v["config"]["seeds"]
    _losing = sum(1 for t in _v["trials"] if t["recovered_inr"] <= 0)
    _invalid = sum(1 for t in _v["trials"] if not t["valid"])
    # The interval, drawn to scale against the range the seeds actually
    # spanned, so its width is visible rather than merely stated.
    _lo, _hi = _r["min"], _r["max"]
    _span = (_hi - _lo) or 1.0
    _f0 = (_r["ci_lo"] - _lo) / _span * 100
    _f1 = (_r["ci_hi"] - _lo) / _span * 100
    _mean = (_r["mean"] - _lo) / _span * 100
    st.sidebar.markdown(
        f'<div class="sbhead">{icon("target")}Validated result</div>'
        f'<div class="sb"><div class="sbt">'
        f'<span class="sbn">recovered, mean of {len(_seeds)} seeds</span>'
        f'<span class="sbv">{rupees(_r["mean"])}</span></div>'
        f'<div class="track2">'
        f'<div class="fill" style="left:{_f0:.0f}%;width:{_f1 - _f0:.0f}%"></div>'
        f'<div class="tick" style="left:{_mean:.0f}%"></div></div>'
        f'<div class="cap">95% CI <b>{rupees(_r["ci_lo"])}</b> to '
        f'<b>{rupees(_r["ci_hi"])}</b>, spanning '
        f'{rupees(_r["min"])}–{rupees(_r["max"])} across seeds. '
        f'<b>{_losing} of {len(_seeds)}</b> lost money; '
        f'<b>{_invalid}</b> were refused for divergent demand.</div></div>',
        unsafe_allow_html=True)

st.sidebar.markdown(f'<div class="sbhead">{icon("cpu")}Where AI is used</div>',
                    unsafe_allow_html=True)
st.sidebar.markdown(
    '<div class="sbl">Detection, attribution, policy and routing are '
    '<b>deterministic</b>. A model writes the operator note, investigates a '
    'past decision, and advises on escalations \u2014 and can change none of '
    'them.</div>', unsafe_allow_html=True)

with st.spinner("Running both arms of the experiment\u2026"):
    control = run(days, seed, routing=False)
    treat = run(days, seed, routing=True)

valid = control["attempts"] == treat["attempts"]
d_succ = treat["successes"] - control["successes"]
d_rev = treat["revenue"] - control["revenue"]
ctrl_sr = control["successes"] / control["attempts"]
treat_sr = treat["successes"] / treat["attempts"]
net = NetRecovery(gross_inr=d_rev,
                  control_cost_inr=control["processing_cost"],
                  treatment_cost_inr=treat["processing_cost"],
                  payments_recovered=d_succ)

# ------------------------------------------------------------------ masthead

st.markdown(
    '<div class="mast"><div class="ml">'
    f'<div class="eyebrow">{icon("shield")}Razorpay AI Buildathon &middot; '
    f'Track 03 &middot; AI Revenue Recovery</div>'
    + (f'<img src="data:image/svg+xml;base64,{b64(LOGO)}">' if LOGO.exists()
       else '<h1>RazorGuard</h1>')
    + '<div class="sub">Payment degradation caught at the slice a dashboard '
      'cannot see, routed around under written bounds, and the recovery '
      '<b>measured against a control arm rather than projected</b>. Every card '
      'below carries the decision path that produced it, including the '
      'refusals.</div>'
      '<div class="chips">'
    + chip("database", f'<b>{control["attempts"]:,}</b> payment attempts')
    + chip("layers", f'<b>{len(treat["incidents"])}</b> injected incidents')
    + chip("alert", f'<b>{treat["alarms"]}</b> alarms raised')
    + chip("lock", f'<b>{treat["audit_events"]:,}</b> audit events')
    + chip("clock", f'seed <b>{seed}</b> &middot; <b>{days}</b> days', "hot")
    + chip("check", "arms identical" if valid else "arms diverged",
           "good" if valid else "")
    + '</div></div>'
      '<div class="mast-r">'
      f'success rate, router off <b>{ctrl_sr:.2%}</b><br>'
      f'success rate, router on <b>{treat_sr:.2%}</b><br>'
      f'gain <b>+{(treat_sr - ctrl_sr) * 100:.2f}pp</b><br>'
      f'money at risk <b>{rupees(control["exposure"])}</b><br>'
      f'recovered <b>{d_rev / control["exposure"]:.1%}</b> of it'
      '</div></div>', unsafe_allow_html=True)

if not valid:
    st.error("The two arms did not face identical demand. No recovery figure "
             "can be reported from this comparison.")
    st.stop()

st.markdown(
    '<div class="funnel">'
    + fcell("coin", rupees(net.net_inr), "Recovered, net of fees", "ok")
    + fcell("trending", f"{d_succ:,}", "Payments saved", "ok")
    + fcell("route", f'{treat["actions"]:,}', "Routing actions taken")
    + fcell("ban", f'{treat["blocked"] + treat["escalated"]:,}',
            "Refused by policy", "attend")
    + fcell("undo", f'{treat["rollbacks"]:,}', "Undone by the system", "act")
    + '</div>', unsafe_allow_html=True)

# -------------------------------------------------------------------- charts

merged = treat["series"].merge(
    control["series"][["minute", "successes", "success_rate"]],
    on="minute", suffixes=("", "_ctl"))
merged["gap"] = merged["successes"] - merged["successes_ctl"]
merged["cumulative"] = merged["gap"].cumsum()
merged["clock"] = merged["minute"].map(hhmm)

inc = treat["incidents"].copy()
inc["label"] = inc["kind"].map(lambda k: KIND_LABEL.get(k, k.replace("_", " ")))
ctl_inc = control["incidents"].set_index("id")

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
    st.markdown('<div class="hint">Scroll to zoom &middot; drag to pan &middot; '
                'double-click to reset &middot; hover for the values under the '
                'pointer</div>', unsafe_allow_html=True)

    win = merged[(merged["minute"] >= lo) & (merged["minute"] <= hi)]
    winc = inc[(inc["end"] >= lo) & (inc["start"] <= hi)]
    wacts = acts[(acts["minute"] >= lo) & (acts["minute"] <= hi)]

    # Zoom and pan on the x axis only - the y scales are meaningful and should
    # not be squashed by a stray wheel.
    zoom = alt.selection_interval(bind="scales", encodings=["x"])
    hover = alt.selection_point(nearest=True, on="pointermove",
                               fields=["minute"], empty=False)

    axis = alt.Axis(labelExpr="'d' + (floor(datum.value/1440)+1) + ' ' + "
                              "format(floor(datum.value/60)%24,'02') + ':' + "
                              "format(datum.value%60,'02')",
                    labelAngle=0, tickCount=7)

    bands = alt.Chart(winc).mark_rect(opacity=.14).encode(
        x="start:Q", x2="end:Q", color=alt.value(C["act"]),
        tooltip=[alt.Tooltip("id:N", title="incident"),
                 alt.Tooltip("label:N", title="kind"),
                 alt.Tooltip("blast_radius:N", title="blast radius"),
                 alt.Tooltip("slices:Q", title="slices hit")])

    base = alt.Chart(win)

    area = base.mark_area(
        line={"color": C["accent"], "strokeWidth": 2},
        color=alt.Gradient(gradient="linear",
                           stops=[alt.GradientStop(color="#0a1a26", offset=0),
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
        (bands + area + xrule + dot).add_params(zoom).properties(height=215),
        width="stretch")

    st.markdown(
        f'<div class="note info">{icon("trending")}<div>'
        f'<span class="nt">How to read this</span>'
        f'Every step up is a payment that failed with routing off and succeeded '
        f'with it on. Red bands are the injected incidents \u2014 the line '
        f'climbs inside them and flattens between, which is what you would '
        f'expect if the system is doing anything at all. Over the full run it '
        f'reaches <b>{d_succ:,}</b>.</div></div>', unsafe_allow_html=True)

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
            domain=["router on", "router off"], range=[C["accent"], "#48586f"]),
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
            domain=["action", "rollback"], range=[C["ok"], C["act"]]),
            legend=None),
        tooltip=[alt.Tooltip("when:N", title="at"), "kind:N", "subject:N",
                 alt.Tooltip("summary:N", title="what")])

    st.altair_chart(
        (bands + lines + xrule2 + marks).add_params(zoom).properties(height=255),
        width="stretch")

    st.markdown(
        f'<div class="note info">{icon("activity")}<div>'
        f'<span class="nt">Why the lines look close</span>'
        f'The gap <i>is</i> the story \u2014 a percentage point of success rate '
        f'is worth crores at this volume. Green triangles are routing actions, '
        f'red ones are rollbacks; hover either for what the system decided and '
        f'why. Zoom into an incident band to watch one unfold minute by minute.'
        f'</div></div>', unsafe_allow_html=True)

with right:
    st.markdown(
        '<div class="rail">'
        '<div class="rail-h">Selected window</div>'
        + rail_row("Range", f"{hhmm(lo)} \u2192 {hhmm(hi)}")
        + rail_row("Minutes", f"{hi - lo:,}")
        + rail_row("Incidents", f"{len(winc)}")
        + rail_row("Actions", f"{len(wacts[wacts['kind'] == 'action'])}", "ok")
        + rail_row("Rollbacks", f"{len(wacts[wacts['kind'] == 'rollback'])}", "bad")
        + '<div class="rail-h">Money, whole run</div>'
        + rail_row("Gross recovered", rupees(net.gross_inr))
        + rail_row("Processing fees", "\u2212" + rupees(net.incremental_cost_inr),
                   "warn")
        + rail_row("Net recovered", rupees(net.net_inr), "ok")
        + rail_row("Money at risk", rupees(control["exposure"]))
        + rail_row("Share recovered", f'{d_rev / control["exposure"]:.1%}', "ok")
        + '<div class="rail-h">Both arms</div>'
        + rail_row("Attempts, control", f'{control["attempts"]:,}')
        + rail_row("Attempts, treatment", f'{treat["attempts"]:,}')
        + rail_row("Identical?", "yes", "ok")
        + rail_row("Success rate, off", f"{ctrl_sr:.2%}")
        + rail_row("Success rate, on", f"{treat_sr:.2%}", "ok")
        + rail_row("Gain", f"+{(treat_sr - ctrl_sr) * 100:.2f}pp", "ok")
        + '<div class="rail-h">Decisions</div>'
        + rail_row("Alarms raised", f'{treat["alarms"]:,}')
        + rail_row("Actions", f'{treat["actions"]:,}', "ok")
        + rail_row("Blocked", f'{treat["blocked"]:,}', "warn")
        + rail_row("Escalated", f'{treat["escalated"]:,}', "warn")
        + rail_row("Restores", f'{treat["restores"]:,}')
        + '</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint" style="margin-top:.7rem">Both arms must see the '
        'same number of attempts. If they ever differ the run refuses to '
        'report a recovery figure at all.</div>', unsafe_allow_html=True)


# ============================================================================
# THE DECISION RECORD
# An incident is not a table row. A table can tell you that something happened;
# it cannot tell you what the system saw, which of its own bounds it checked,
# why it chose that destination, or why it declined. That reasoning is the
# product, so each incident gets a card that carries it - with the provenance
# of every input marked, because "measured against the control arm" and
# "written in the policy file" are very different kinds of claim.
# ============================================================================

def analyse(t, c, ledger, run_attempts, run_exposure):
    """Everything one card needs, all of it derived from the two arms.

    Two slice sets, deliberately. The *source* set - the routes the incident
    actually broke - is where the success-rate collapse is visible, so the
    signal is measured there. The *cohort* - every route serving the same
    (method, issuer) pairs - is where the recovery lands, because succeeding at
    routing means emptying the broken slices. Measure recovery on the source
    set and a working system reports a large loss on its biggest save.
    """
    pre_sr = (c["src_pre_suc"] / c["src_pre_att"]) if c["src_pre_att"] else 0.0
    in_sr = (c["src_suc"] / c["src_att"]) if c["src_att"] else 0.0

    coh_pre_sr = (c["coh_pre_suc"] / c["coh_pre_att"]) if c["coh_pre_att"] else 0.0
    ticket = (c["coh_pre_rev"] / c["coh_pre_suc"]) if c["coh_pre_suc"] else 0.0

    # What the incident cost, measured: the successes the cohort's own
    # pre-incident baseline would have produced over the same attempts, minus
    # the ones the control arm actually got, valued at what a success on these
    # routes is worth.
    lost = max(0.0, coh_pre_sr * c["coh_att"] - c["coh_suc"])
    at_risk = lost * ticket

    # Time alone is not enough to attribute a ledger entry to an incident:
    # eighteen of them across two days overlap, and a window filter hands one
    # incident another's actions. Actions, decisions and rollbacks name the
    # route they touched, so they are matched on the cohort; detections name
    # the cause the attributor settled on, which may be a gateway or an issuer.
    w = ledger[(ledger["minute"] >= t["start"] - 8) &
               (ledger["minute"] <= t["end"] + 30) &
               (ledger["subject"].isin(t["cause_keys"]))]
    det = w[w["kind"] == "detection"]
    acted = w[(w["kind"] == "action") & (w["subject"].isin(t["cohort_keys"]))]
    rolled = w[(w["kind"] == "rollback") & (w["subject"].isin(t["cohort_keys"]))]
    refused = w[(w["kind"] == "decision") & (w["rule"] != "") &
                (w["subject"].isin(t["cohort_keys"]))]

    ttd = (int(det["minute"].min()) - int(t["start"])) if not det.empty else None
    subjects = sorted(set(acted["subject"])) if not acted.empty else []
    top_rule = (refused["rule"].value_counts().idxmax()
                if not refused.empty else "")

    recovered = t["coh_rev"] - c["coh_rev"]
    saved = int(t["coh_suc"] - c["coh_suc"])

    return {
        "pre_sr": pre_sr, "in_sr": in_sr, "drop_pp": (pre_sr - in_sr) * 100,
        "ticket": ticket, "at_risk": at_risk, "recovered": recovered,
        "saved": saved, "ttd": ttd,
        "src_att": int(c["src_att"]), "coh_att": int(c["coh_att"]),
        # If the two arms ever saw different demand on this cohort, the
        # difference between them is not a recovery figure and is not shown
        # as one.
        "conserved": int(c["coh_att"]) == int(t["coh_att"]),
        "coverage": (c["coh_att"] / run_attempts) if run_attempts else 0.0,
        "exposure_share": (at_risk / run_exposure) if run_exposure else 0.0,
        "capture": (recovered / at_risk) if at_risk > 0 else 0.0,
        "collapse": ((pre_sr - in_sr) / pre_sr) if pre_sr else 0.0,
        "window": w, "n_det": len(det), "n_act": len(acted),
        "n_roll": len(rolled), "n_ref": len(refused),
        "subjects": subjects, "top_rule": top_rule,
        "seq_lo": int(w["seq"].min()) if not w.empty else None,
        "seq_hi": int(w["seq"].max()) if not w.empty else None,
    }


def band_of(a):
    """Severity as a share of the run's own losses, not an absolute rupee
    figure - a threshold in rupees would call every incident critical on a
    long run and none on a short one."""
    if a["exposure_share"] >= 0.10:
        return "act", "CRITICAL"
    if a["exposure_share"] >= 0.03:
        return "attend", "MAJOR"
    return "track", "MINOR"


def outcome_of(a, kind):
    if a["n_act"] and a["recovered"] > 0:
        return "ok", "ROUTED AROUND"
    if a["n_act"]:
        return "attend", "ACTED, NO GAIN"
    if kind == "issuer_degradation":
        return "attend", "ESCALATED &middot; NO HEALTHY TARGET"
    if a["n_det"]:
        return "track", "DETECTED, HELD"
    return "track", "BELOW ACTION FLOOR"


def chain_html(a, row):
    """The decision path, as a factor grid rather than a list of sentences.

    Cells marked `meas` were read off the paired control arm during this run.
    The rest were declared before it started. Keeping the two visually
    separate is the point: a reader should never have to guess which numbers
    the system measured and which it was simply told.
    """
    detect = (f'{max(a["ttd"], 0)} min' if a["ttd"] is not None
              else "not detected")
    detect_src = ("union of fixed-threshold and posterior-drop"
                  if a["ttd"] is not None else "stayed under the alarm floor")
    if a["subjects"]:
        moved = f'{len(a["subjects"])} route key(s)'
        moved_src = "e.g. " + esc(a["subjects"][0])
    else:
        moved = "none"
        moved_src = "no destination met the bar"

    ref = (f'{a["n_ref"]} refused' if a["n_ref"] else "none refused")
    oc, label = outcome_of(a, row["kind"])
    return ('<div class="chain">'
            + node("Signal", f'&minus;{a["drop_pp"]:.1f}pp',
                   "vs. its own prior hour", "meas",
                   f'{a["pre_sr"]:.2%} before, {a["in_sr"]:.2%} during')
            + node("Broke", esc(row["blast_radius"]),
                   f'{row["slices"]} routes, {row["duration"]} min')
            + node("Measured over", f'{row["cohort"]} method/issuer pairs',
                   f'{a["coh_att"]:,} attempts, both arms', "meas",
                   "Recovery lands on the destination route, not the broken "
                   "one, so it is measured across every gateway serving these "
                   "pairs")
            + node("Time to detect", detect, detect_src, "meas")
            + node("Money at risk", rupees(a["at_risk"]),
                   "control-arm shortfall", "meas")
            + node("Routes moved", moved, moved_src)
            + node("Policy", f'{a["n_act"]} acted', ref)
            + f'<div class="node end {oc}"><b>{label}</b></div>'
            + '</div>')


def bars_html(a):
    """What moved this incident up the list.

    Every bar is a percentage of a whole named in the right-hand column, so no
    two bars are silently drawn on different scales.
    """
    return ('<div class="sec">What moved this up the list</div>'
            + bar("Success-rate collapse, on the broken routes",
                  f'{a["pre_sr"]:.1%} &rarr; {a["in_sr"]:.1%}',
                  a["collapse"] * 100, "var(--act)",
                  f'{a["collapse"]:.0%} of its own baseline')
            + bar("Money at risk", rupees(a["at_risk"]),
                  a["exposure_share"] * 100, "var(--attend)",
                  f'{a["exposure_share"]:.1%} of the run')
            + bar("Traffic in the measured cohort",
                  f'{a["coh_att"]:,} attempts',
                  a["coverage"] * 100 * 8, "var(--track)",
                  f'{a["coverage"]:.2%} of the run')
            + bar("Recovered", rupees(a["recovered"]),
                  a["capture"] * 100, "var(--ok)",
                  f'{a["capture"]:.0%} of its own risk'))


def render_incident(i, row, a, hero=False):
    cls, sev = band_of(a)
    kind = KIND_LABEL.get(row["kind"], row["kind"])
    flag = ('<span class="flag ok">RECOVERED</span>' if a["recovered"] > 0
            else '<span class="flag">NO GAIN AVAILABLE</span>')
    conserved = (
        f'<b>Measured, not projected.</b> Both arms saw the same '
        f'{a["coh_att"]:,} attempts across this cohort; the only difference '
        f'between them is where the routing sent them.'
        if a["conserved"] else
        '<b>Demand diverged across this cohort.</b> No recovery figure is '
        'claimed from a comparison the two arms did not share.')

    if a["n_act"] and a["recovered"] > 0:
        did = (f'Moved traffic off the failing routes on '
               f'{len(a["subjects"])} route key(s) in {a["n_act"]} bounded '
               f'step(s), each capped at {cfg.max_shift_fraction:.0%} and '
               f'leaving a 3% canary behind. Across the cohort '
               f'{a["saved"]:,} more payments settled than in the control arm, '
               f'worth {rupees(a["recovered"])} gross.')
    elif a["n_act"]:
        # Reported rather than smoothed over. A system that only shows its wins
        # is not showing you a measurement.
        did = (f'Acted {a["n_act"]} time(s) and the cohort ended '
               f'{abs(a["saved"]):,} payments <b>worse</b> than the control arm '
               f'— {rupees(abs(a["recovered"]))} of value. The efficacy '
               f'breaker exists for exactly this: it watches the realised '
               f'effect of recent shifts and halts them when they stop paying.')
    elif row["kind"] == "issuer_degradation":
        did = ('Escalated rather than routed. An issuer-wide fault degrades '
               'every gateway serving that bank at once, so no healthy '
               'destination exists - shuffling traffic between equally broken '
               'routes would look like action and recover nothing.')
    else:
        did = ('Held. The drop never cleared the action floor of '
               f'{cfg.min_drop_pp}pp at {cfg.min_confidence:.0%} confidence, so '
               'the system watched it and left the weights alone.')

    a["ttd_phrase"] = (
        "- not raised" if a["ttd"] is None else
        "in under a minute" if a["ttd"] <= 0 else
        f'in {a["ttd"]} minute' + ("s" if a["ttd"] != 1 else ""))
    ref_line = (f'{a["n_ref"]} proposal(s) refused, most often by '
                f'<code>{esc(a["top_rule"])}</code>. ' if a["n_ref"] else "")
    seq = (f'<code>seq {a["seq_lo"]}&ndash;{a["seq_hi"]}</code>'
           if a["seq_lo"] is not None else "<code>no entries</code>")

    st.markdown(f"""
<div class="card {cls}{' hero' if hero else ''}">
  <div class="crow">
    <span class="band {cls}">{sev}</span>
    <span class="rank">#{i}</span>
    {flag}
    <span class="spacer"></span>
    <span class="score"><b>{rupees(a["at_risk"])}</b><span>money at risk</span></span>
  </div>

  <div class="ctitle">{esc(kind)} on {esc(row["blast_radius"])}</div>
  <div class="cmeta">{esc(row["id"])} &nbsp;&middot;&nbsp; {hhmm(int(row["start"]))}
    &rarr; {hhmm(int(row["end"]))} &nbsp;&middot;&nbsp; {row["duration"]} min
    &nbsp;&middot;&nbsp; {row["slices"]} broken routes
    &nbsp;&middot;&nbsp; {a["coh_att"]:,} attempts measured</div>

  <div class="sec">How it was decided</div>
  {chain_html(a, row)}

  {bars_html(a)}

  <div class="mult"><span class="ml">Net of fees</span>
    <span class="mv">card and netbanking recoveries carry MDR; UPI carries none,
      so the gross figure above is not what lands</span>
    <span class="ms">run-level net {rupees(net.net_inr)}</span></div>

  <div class="next">
    <div class="nl">What the system did</div>
    <div class="nt">{did}</div>
  </div>

  <div class="trace">
    {conserved}<br>
    Detected by the union detector
      {a["ttd_phrase"]}
      &nbsp;&middot;&nbsp; {a["n_det"]} detection, {a["n_act"]} action,
      {a["n_roll"]} rollback entries<br>
    {ref_line}Traced to {seq} of the run's {treat["audit_events"]:,} audit events.
  </div>
</div>
""", unsafe_allow_html=True)


# Plain English for every rule that can stop a money movement. A refusal a
# reviewer cannot read is not accountability, it is a log line.
RULE_TEXT = {
    "min_confidence":
        "The evidence was not strong enough. The posterior probability that "
        "this slice had really dropped sat below the bar, so the drop could "
        "still have been ordinary variance on a thin slice.",
    "min_drop_pp":
        "The drop was real but too small to be worth moving money over. "
        "Routing has a cost; a shallow dip does not clear it.",
    "action_cooldown":
        "This slice key had been acted on too recently. Acting again before "
        "the last change has had time to show up in the data is how a control "
        "loop starts oscillating.",
    "max_actions_per_hour":
        "The hourly action budget for the whole fleet was spent. A system that "
        "can act without limit during a broad incident can make it worse "
        "faster than a human can stop it.",
    "max_causes_per_hour":
        "Too many distinct root causes appeared within the hour. That "
        "signature says the fleet is broadly unwell rather than one route "
        "being sick, and shuffling traffic inside a sick fleet loses money.",
    "no_target":
        "There was no alternative route for this slice at all.",
    "no_healthy_destination":
        "Every candidate destination was as degraded as the source. This is "
        "the issuer-wide signature: the bank is failing everywhere, so no "
        "amount of rerouting helps.",
    "target_alarmed":
        "The destination was itself under an open alarm. Moving traffic into "
        "a route that is already failing converts one incident into two.",
    "min_target_attempts":
        "The destination had too little recent traffic to have a trustworthy "
        "health reading. A route that looks perfect on nine payments is not "
        "known to be healthy.",
    "target_unknown":
        "No health reading existed for the destination, even after widening "
        "the estimate. The system will not move money towards a route it "
        "cannot describe.",
    "min_target_advantage":
        "The destination was healthier, but not by enough to pay for the "
        "move. Below the advantage bar the expected gain is inside the noise.",
    "max_cumulative_divergence":
        "This slice key had already been moved as far from its configured "
        "weights as policy allows. Beyond that point the routing table stops "
        "resembling anything an operator signed off on.",
    "efficacy_breaker":
        "The circuit breaker was open. The system measures the realised effect "
        "of its own recent shifts, and when they stop paying it stops making "
        "them - the single most valuable rule in the file, because it is the "
        "one that fires when the system is wrong.",
    "canary_floor":
        "The shift would have taken the source route below its 3% canary. "
        "Some traffic always stays, so recovery is observable rather than "
        "assumed.",
}

analyses = []
for _, row in inc.iterrows():
    c = ctl_inc.loc[row["id"]]
    analyses.append(analyse(row, c, treat["ledger"],
                            control["attempts"], control["exposure"]))
order = sorted(range(len(analyses)), key=lambda k: -analyses[k]["at_risk"])

tab_dec, tab_replay, tab_ref, tab_audit, tab_method = st.tabs(
    [f"Decision record \u00b7 {len(analyses)}",
     "Incident replay",
     f"What it refused \u00b7 {sum(treat['blocked_by_rule'].values())}",
     f"Audit ledger \u00b7 {treat['audit_events']:,}",
     "How this was measured"])

with tab_dec:
    st.markdown(
        f'<div class="note">{icon("alert")}<div>'
        f'<span class="nt">Read the top card first</span>'
        f'The {len(analyses)} injected incidents, ranked by the money the '
        f'control arm actually lost to them \u2014 not by how loud they were. '
        f'Each card shows what the system saw, which bounds it checked, what it '
        f'did, and what that was worth. <b>Nothing here was composed for the '
        f'demo</b>; every number is a difference between two runs that saw '
        f'identical demand. Where two incidents overlap in time their cohorts '
        f'overlap too, so these figures sum to slightly more than the run '
        f'total — the run total is the one that carries a confidence '
        f'interval.</div></div>', unsafe_allow_html=True)
    st.markdown("<div style='height:.9rem'></div>", unsafe_allow_html=True)

    for i, k in enumerate(order, 1):
        render_incident(i, inc.iloc[k], analyses[k], hero=(i == 1))

with tab_replay:
    st.markdown(
        f'<div class="note">{icon("lock")}<div>'
        f'<span class="nt">Written at the time, not for the demo</span>'
        f'Every line below was written by the control plane as it ran '
        f'\u2014 <b>including the refusals, which are the half worth reading.'
        f'</b></div></div>', unsafe_allow_html=True)

    labels = [f'{inc.iloc[k]["id"]}   \u00b7   {inc.iloc[k]["label"]}   \u00b7   '
              f'{hhmm(int(inc.iloc[k]["start"]))}   \u00b7   '
              f'{rupees(analyses[k]["at_risk"])} at risk   \u00b7   '
              f'{analyses[k]["n_act"]} action(s)' for k in order]
    c1, c2 = st.columns([3, 2])
    choice = c1.selectbox("Incident", range(len(labels)),
                          format_func=lambda i: labels[i], index=0)
    kinds = c2.multiselect(
        "Show", ["detection", "proposal", "decision", "action", "rollback",
                 "restore"],
        default=["detection", "decision", "action", "rollback"])

    k = order[choice]
    w = analyses[k]["window"]
    if kinds:
        w = w[w["kind"].isin(kinds)]

    if w.empty:
        st.info("No ledger entries in this window for the selected kinds.")
    else:
        rows = []
        for r in w.head(80).itertuples():
            ic = GLYPH.get(r.kind, "layers")
            rule = (f'<span class="rule{" bad" if r.rule in LOUD else ""}">'
                    f'{esc(r.rule)}</span>' if r.rule else "")
            rows.append(
                f'<div class="lrow {r.kind}"><div class="lh">{icon(ic)}'
                f'<span class="lk">{r.kind}</span><span>{hhmm(r.minute)}</span>'
                f'<span class="mono">{esc(r.subject)}</span>{rule}</div>'
                f'<div class="lb">{esc(r.summary)}</div></div>')
        st.markdown("".join(rows), unsafe_allow_html=True)
        if len(w) > 80:
            st.caption(f"{len(w) - 80} further entries in this window.")

with tab_ref:
    rules = treat["blocked_by_rule"]
    if not rules:
        st.info("No refusals in this run.")
    else:
        st.markdown(
            f'<div class="note">{icon("ban")}<div>'
            f'<span class="nt">The half a dashboard never shows</span>'
            f'A ledger that records only successful actions hides exactly the '
            f'decisions worth reviewing. Every row here is the system '
            f'<b>declining to move money</b>, with the rule that stopped it and '
            f'what that rule is protecting against.</div></div>',
            unsafe_allow_html=True)
        st.markdown("<div style='height:.9rem'></div>", unsafe_allow_html=True)

        tot = sum(rules.values())
        for name, n in sorted(rules.items(), key=lambda kv: -kv[1]):
            st.markdown(
                f'<div class="ex"><div class="top">'
                f'<span class="id">{esc(name)}</span>'
                f'<span class="pr">{n / tot:.0%} of all refusals</span>'
                f'<span class="sev">fired <b>{n:,}</b> times</span></div>'
                f'<div class="rsn">{RULE_TEXT.get(name, "")}</div></div>',
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
                 width="stretch", hide_index=True, height=460)
    st.caption(f'{treat["audit_events"]:,} events in this run. Every money '
               f'action and every refusal, with the rule that decided it.')

with tab_method:
    st.markdown(
        f'<div class="sect">{icon("target")}Why these numbers can be trusted'
        f'</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="chain">'
        + node("Design", "Paired arms", "same seed, same demand", "meas",
               "Demand is drawn from a per-cell RNG keyed on (seed, minute, "
               "method, issuer), so both arms face bit-identical traffic")
        + node("Difference", f"{d_succ:,} payments",
               "treatment minus control", "meas")
        + node("Guard", "Refuses to report",
               "if attempt counts ever differ", "meas")
        + node("Valuation", "Per-slice ticket",
               "not a fleet-wide average", "meas")
        + node("Cost", "MDR applied", "UPI 0%, card 1.8%, netbanking 0.9%")
        + node("Blindness", "Detectors cannot import",
               "the incident plan", "", "A test enforces this: the detector "
                                        "package may not import scenarios.py")
        + '</div>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="note ok">{icon("check")}<div>'
        f'<span class="nt">What the headline figure is</span>'
        f'<b>{rupees(net.net_inr)} net</b> over {days} simulated day(s) at seed '
        f'{seed}: {rupees(net.gross_inr)} of extra settled value, minus '
        f'{rupees(net.incremental_cost_inr)} of processing fees on the payments '
        f'that only succeeded because of the routing. It is a measured '
        f'difference between two runs, not a projection from a success-rate '
        f'delta.</div></div>', unsafe_allow_html=True)

    st.markdown(
        f'<div class="note">{icon("alert")}<div>'
        f'<span class="nt">What it is not</span>'
        f'This is a simulator, and a simulator can be made to say anything. '
        f'Three things stop it here: the detectors are barred from importing '
        f'the incident plan, the control arm is the only source of the '
        f'recovery figure, and the run is refused outright if the two arms ever '
        f'see different demand. The bench directory holds the same experiment '
        f'across eight seeds with a confidence interval, and the sensitivity '
        f'sweep that found routing <i>losing</i> money on a saturated fleet '
        f'\u2014 which is why the efficacy breaker exists.</div></div>',
        unsafe_allow_html=True)

    st.markdown(f'<div class="sect">{icon("cpu")}Where the model is, and is not'
                f'</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="chain">'
        + node("Detection", "Deterministic", "beta posterior, no model")
        + node("Attribution", "Deterministic", "lift with coverage")
        + node("Policy", "Deterministic", "a written rule file")
        + node("Routing", "Deterministic", "bounded weight shifts")
        + node("Narrator", "Model", "writes the operator note", "meas")
        + node("Investigator", "Model", "explains a past decision", "meas")
        + node("Advisor", "Model", "advises on escalations", "meas")
        + '<div class="node end track"><b>MODEL CHANGES NOTHING</b></div>'
        + '</div>', unsafe_allow_html=True)
    st.caption("An LLM that can move money is a liability. Here it can read the "
               "ledger and write English about it, and that is all.")
