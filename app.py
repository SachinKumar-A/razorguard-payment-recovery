"""RazorGuard operator console.

    streamlit run app.py

Shows what the control plane saw, what it decided, what the decision was worth,
and - the half that matters - what it refused to do.

Four things drove the design.

The ground is paper, not a dark panel. An operator console gets read under
fluorescent light, projected, and printed; the palette is the "light clinical"
system - white ground, near-black ink, hairline rules, structure carried by
typography and alignment rather than filled boxes - so the only saturated
colour on screen is risk, and it always means the same thing.

Every route lives in the URL. The navigation, the incident you are looking at
and the simulation settings are all query parameters, which means the KPI tiles
can be real links, a view can be sent to somebody as a URL, and the back button
does what a back button should.

The incident record is a decision card, not a table row. A table can tell you
an incident happened. It cannot tell you what the system saw, which of its own
bounds it checked, why it picked that destination, or why it declined - and
that reasoning is the product. Each card carries its decision path as a factor
grid with the provenance of every input marked, so a reader can separate the
numbers measured against the control arm from the ones declared in advance.

The charts are zoomable because the interesting events are minutes wide inside
a two-day window. A static view of 2,880 minutes renders every incident as a
smudge; you have to be able to scroll into 20:05 and read what happened.
"""
from __future__ import annotations

import base64
import html
import json
import pathlib
from typing import Dict, List, Tuple
from urllib.parse import urlencode

import altair as alt
import streamlit as st

from razorguard import console_data
from razorguard.console_data import run as console_run
from razorguard.economics import NetRecovery

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
    "target": "M12 22a10 10 0 100-20 10 10 0 000 20zM12 18a6 6 0 100-12 6 6 0 000 12zM12 14a2 2 0 100-4 2 2 0 000 4z",
    "database": "M12 8c4.42 0 8-1.34 8-3s-3.58-3-8-3-8 1.34-8 3 3.58 3 8 3zM4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3",
    "zap": "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
    "lock": "M5 11h14v11H5zM8 11V7a4 4 0 018 0v4",
    "arrow": "M5 12h14M13 6l6 6-6 6",
    "book": "M4 19.5A2.5 2.5 0 016.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z",
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
# DESIGN SYSTEM - LIGHT CLINICAL
# Paper ground, near-black ink, hairline rules. Structure is carried by
# typography and alignment rather than by filled boxes, so the only saturated
# colour on screen is risk and it always means the same thing. A card is a
# hairline and nothing else: no shadow, no fill, no radius large enough to read
# as a bubble.
# ============================================================================
CSS_TOKENS = """
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap");

#MainMenu, footer, header {visibility:hidden;}
.block-container {padding:0 1.5rem 4rem; max-width:1560px;}

:root {
  /* grounds, lightest first */
  --paper:  #ffffff;
  --sunk:   #fafafa;
  --wash:   #f4f4f5;
  --panel:  #ffffff;

  /* hairlines */
  --rule:   #e4e4e7;
  --rule2:  #d4d4d8;

  /* ink */
  --ink:    #09090b;
  --body:   #3f3f46;
  --muted:  #71717a;
  --faint:  #a1a1aa;

  /* interaction - one accent, never used for risk */
  --accent: #1d4ed8;
  --accent2:#1e40af;
  --tint:   #eff4ff;

  /* risk - never used for interaction */
  --critical:#dc2626;
  --high:    #ea580c;
  --medium:  #ca8a04;
  --low:     #059669;
  --info:    #2563eb;
  --critical-t:#fef2f2;
  --high-t:    #fff7ed;
  --medium-t:  #fefce8;
  --low-t:     #ecfdf5;
  --info-t:    #eff6ff;

  --chrome: #3b3a7c;
  --chrome2:#2f2e66;

  --r:      6px;
  --r-sm:   4px;
  --mono:   "JetBrains Mono", ui-monospace, monospace;
}

/* One turn of the whole type scale. Everything below is sized in rem, so
   raising the root raises the widgets Streamlit draws for itself at the same
   time - which is the point: a console where the chrome and the content
   disagree about scale reads as two applications stapled together. */
html{-webkit-text-size-adjust:100%; font-size:17.5px}
body, .stApp {background:var(--paper); color:var(--body);
  font-family:"Inter", ui-sans-serif, system-ui, sans-serif;
  font-feature-settings:"kern" 1,"liga" 1,"cv05" 1,"ss01" 1;
  -webkit-font-smoothing:antialiased;}
::selection {background:#dbeafe; color:var(--ink)}
.why,.trace{max-width:80ch}
.ctitle{text-wrap:balance}
.tnum,.score b,.fcell .n,.sb .sbv{font-variant-numeric:tabular-nums}
a{text-decoration:none; color:inherit}

"""

CSS_NAV = """
/* ---------- exchange chrome -----------------------------------------------
   The header is modelled on an exchange front page, because that is the
   closest thing to what this console is: a live board where a handful of
   headline numbers, a tape and a wide primary navigation have to be readable
   from across a room. Four stacked bands, each edge to edge within the main
   column: a status strip, the board itself, the navigation, then the tape. */
.bleed{margin-left:-1.5rem; margin-right:-1.5rem; padding-left:1.5rem;
  padding-right:1.5rem;}

/* band 1 - status strip. No links: everything it names is either on the
   navigation below it or is a fact about the run, and a row of duplicate
   routes was making the reader choose between two identical paths. */
.topbar{background:var(--chrome); color:#c9c7ec; padding-top:.7rem;
  padding-bottom:.7rem; display:flex; align-items:center; gap:1.6rem;
  flex-wrap:wrap;}
.tb-grid{display:flex; gap:2.4rem; flex-wrap:wrap; flex:1}
.tb-i{line-height:1.35}
.tb-k{font-size:.66rem; letter-spacing:.14em; text-transform:uppercase;
  color:#9d9ad0; font-weight:600; margin-bottom:.16rem}
.tb-v{font-size:1.02rem; color:#fff; font-weight:600;
  font-variant-numeric:tabular-nums; letter-spacing:-.01em}
.tb-v small{font-size:.78rem; font-weight:400; color:#c9c7ec;
  letter-spacing:0; margin-left:.3rem}

/* band 2 - the board */
.board{background:var(--paper); border-bottom:1px solid var(--rule);
  display:flex; align-items:center; gap:1.9rem; flex-wrap:wrap;
  padding-top:1.1rem; padding-bottom:1.1rem;}
.board img{height:104px; display:block; flex:none}
.board .tag{font-size:.95rem; color:var(--body); line-height:1.6;
  max-width:40ch; border-left:1px solid var(--rule); padding-left:1.4rem;}
.board .tag b{color:var(--ink); font-weight:600}
.board .sp{flex:1; min-width:.5rem}
.quotes{display:flex; align-items:stretch; flex-wrap:wrap}
.q{padding:0 1.25rem; border-left:1px solid var(--rule); min-width:9.6rem}
.q:first-child{border-left:none}
.q .qk{font-size:.68rem; letter-spacing:.1em; text-transform:uppercase;
  color:var(--muted); font-weight:600; margin-bottom:.28rem}
.q .qv{font-size:1.62rem; font-weight:700; color:var(--ink); line-height:1.1;
  font-variant-numeric:tabular-nums; display:flex; align-items:center;
  gap:.3rem; letter-spacing:-.025em}
.q .qv svg{width:15px; height:15px}
.q.up .qv{color:var(--low)} .q.down .qv{color:var(--critical)}
.q .qd{font-size:.82rem; color:var(--muted); margin-top:.3rem;
  font-variant-numeric:tabular-nums}
.q .qd b{color:var(--ink); font-weight:600}
.live{display:inline-flex; align-items:center; gap:.4rem; font-size:.72rem;
  color:var(--low); font-weight:600; margin-top:.4rem; letter-spacing:.04em}
.live .dot{width:8px; height:8px; border-radius:50%; background:var(--low);
  animation:pulseDot 2.2s ease-in-out infinite}
@keyframes pulseDot{0%,100%{opacity:1} 50%{opacity:.25}}

/* band 3 - primary navigation ----------------------------------------------
   Real Streamlit buttons rather than anchors. An anchor navigates the whole
   document, so every click was a full page load: a white flash, the scroll
   position lost, and the websocket reconnecting. A button reruns the script
   in place and the URL is updated alongside it, so links stay shareable and
   the back button still works, without the reload.                       */
.st-key-rgnav{background:var(--paper); border-bottom:2px solid var(--rule);
  position:sticky; top:0; z-index:50;}
.st-key-rgnav [data-testid="stHorizontalBlock"]{gap:0!important;
  flex-wrap:nowrap!important}
.st-key-rgnav [data-testid="stColumn"]{min-width:0}
.st-key-rgnav [data-testid="stElementContainer"]{margin:0}
.st-key-rgnav button{width:100%; border:none!important; border-radius:0!important;
  background:transparent!important; padding:1.05rem .35rem!important;
  position:relative; box-shadow:none!important; min-height:0!important}
.st-key-rgnav button p{font-size:.9rem!important; font-weight:600!important;
  letter-spacing:.04em; text-transform:uppercase; color:var(--body);
  white-space:nowrap; margin:0!important; line-height:1.2}
.st-key-rgnav button:hover{background:var(--sunk)!important}
.st-key-rgnav button:hover p{color:var(--chrome)}
.st-key-rgnav button::after{content:""; position:absolute; left:50%; right:50%;
  bottom:-2px; height:3px; background:var(--accent);
  transition:left .2s ease, right .2s ease}
.st-key-rgnav [data-testid="stBaseButton-primary"] p{color:var(--accent)!important}
.st-key-rgnav [data-testid="stBaseButton-primary"]::after{left:.5rem; right:.5rem}
.st-key-rgnav button p code{font-family:inherit; font-size:.76rem!important;
  font-weight:700!important; background:var(--wash); color:var(--muted);
  border-radius:99px; padding:.05rem .4rem; margin-left:.3rem;
  letter-spacing:0; vertical-align:.06em}
.st-key-rgnav [data-testid="stBaseButton-primary"] p code{background:var(--accent);
  color:#fff}

/* band 4 - the tape */
.tape{background:var(--sunk); border-bottom:1px solid var(--rule);
  overflow:hidden; margin-bottom:1.1rem; position:relative;}
.tape-in{display:flex; width:max-content; animation:tape 74s linear infinite}
.tape:hover .tape-in{animation-play-state:paused}
@keyframes tape{from{transform:translateX(0)} to{transform:translateX(-50%)}}
.tk{display:inline-flex; align-items:baseline; gap:.55rem; padding:.5rem 1rem;
  font-size:.87rem; border-left:3px solid var(--rule2); white-space:nowrap;
  margin:.5rem 0; color:var(--muted);}
.tk.up{border-left-color:var(--low)} .tk.down{border-left-color:var(--critical)}
.tk b{color:var(--ink); font-weight:700; font-family:var(--mono);
  font-size:.83rem; letter-spacing:-.02em}
.tk .v{color:var(--body); font-variant-numeric:tabular-nums}
.tk .c{font-variant-numeric:tabular-nums; font-weight:600}
.tk.up .c{color:var(--low)} .tk.down .c{color:var(--critical)}
.tape-tag{position:absolute; left:0; top:0; bottom:0; z-index:2;
  background:var(--chrome); color:#fff; font-size:.68rem; font-weight:700;
  letter-spacing:.13em; display:flex; align-items:center;
  padding:0 .9rem 0 1.5rem;}
.tape-fade{position:absolute; right:0; top:0; bottom:0; width:56px; z-index:2;
  background:linear-gradient(90deg,rgba(250,250,250,0),var(--sunk))}

/* ---------- KPI board -----------------------------------------------------
   Buttons, for the same reason the navigation is: these are the fastest route
   into the detail and they should not cost a page load. The corner fold and
   the underline carry the semantic colour, an exchange convention that lets
   the row be read at a glance rather than word by word.                   */
.st-key-rgkpi [data-testid="stElementContainer"]{margin:0}
.st-key-rgkpi button{width:100%; text-align:left!important; position:relative;
  background:var(--paper)!important; border:1px solid var(--rule)!important;
  border-bottom:3px solid var(--rule2)!important; border-radius:var(--r);
  padding:1rem 1.15rem 1.1rem!important; overflow:hidden;
  box-shadow:none!important; min-height:0!important;
  transition:background-color .15s ease}
.st-key-rgkpi button:hover{background:var(--sunk)!important}
.st-key-rgkpi button::after{content:""; position:absolute; top:0; right:0;
  border-width:0 17px 17px 0; border-style:solid;
  border-color:var(--rule2) transparent;}
.st-key-rgkpi button p{margin:0!important; color:var(--muted);
  font-size:.86rem!important; line-height:1.45; font-weight:500}
.st-key-rgkpi button p strong{display:block; font-size:2.15rem; line-height:1.05;
  letter-spacing:-.035em; color:var(--ink); font-weight:700;
  font-variant-numeric:tabular-nums; margin-bottom:.35rem}
.st-key-rgkpi button p em{display:block; font-style:normal; font-size:.72rem;
  letter-spacing:.09em; text-transform:uppercase; color:var(--faint);
  font-weight:600; margin-bottom:.5rem}
.st-key-rgkpi0 button,
.st-key-rgkpi1 button{border-bottom-color:var(--low)!important}
.st-key-rgkpi0 button::after,
.st-key-rgkpi1 button::after{border-color:var(--low) transparent}
.st-key-rgkpi0 button p strong,
.st-key-rgkpi1 button p strong{color:var(--low)}
.st-key-rgkpi0 button p em,
.st-key-rgkpi1 button p em{color:var(--low)}
.st-key-rgkpi3 button{border-bottom-color:var(--high)!important}
.st-key-rgkpi3 button::after{border-color:var(--high) transparent}
.st-key-rgkpi3 button p strong{color:var(--high)}
.st-key-rgkpi3 button p em{color:var(--high)}
.st-key-rgkpi4 button{border-bottom-color:var(--critical)!important}
.st-key-rgkpi4 button::after{border-color:var(--critical) transparent}
.st-key-rgkpi4 button p strong{color:var(--critical)}
.st-key-rgkpi4 button p em{color:var(--critical)}
.st-key-rgkpi2 button{border-bottom-color:var(--chrome)!important}
.st-key-rgkpi2 button::after{border-color:var(--chrome) transparent}
.st-key-rgkpi2 button p em{color:var(--chrome)}

/* ---------- page headers and section labels ---------- */
.pagehead{display:flex; align-items:baseline; gap:.9rem; flex-wrap:wrap;
  border-bottom:1px solid var(--rule); padding-bottom:.6rem;
  margin:1.1rem 0 1.1rem}
.pagehead h2{font-size:1.62rem; font-weight:700; color:var(--ink); margin:0;
  letter-spacing:-.025em}
.pagehead .ph-sub{font-size:.92rem; color:var(--muted)}
.pagehead .ph-sp{flex:1}
.pagehead .ph-as{font-size:.78rem; color:var(--faint);
  font-variant-numeric:tabular-nums}
.sect{display:flex; align-items:center; gap:.5rem; font-size:.76rem;
  letter-spacing:.12em; text-transform:uppercase; color:var(--muted);
  font-weight:700; margin:1.6rem 0 .65rem 0;}
.sect svg{width:15px; height:15px; stroke:var(--accent)}
.hint{font-size:.8rem; color:var(--faint); margin:.2rem 0 .5rem}
.lede{font-size:.98rem; color:var(--body); line-height:1.68; max-width:82ch;
  margin:0 0 1rem}
.lede b{color:var(--ink); font-weight:600}
"""

CSS_CARD = """
/* ---------- notes ---------- */
.note {display:flex; gap:.7rem; border:1px solid var(--rule);
  border-left:3px solid var(--medium); background:var(--paper);
  padding:.8rem 1rem; border-radius:0 var(--r) var(--r) 0;
  font-size:.93rem; color:var(--body); margin:1rem 0 0 0; line-height:1.65;}
.note svg{width:18px; height:18px; stroke:var(--medium); flex:none;
  margin-top:.15rem}
.note b{color:var(--ink); font-weight:600}
.note .nt{font-size:.75rem; letter-spacing:.09em; text-transform:uppercase;
  color:var(--medium); font-weight:600; display:block; margin-bottom:.25rem}
.note.ok{border-left-color:var(--low)} .note.ok svg{stroke:var(--low)}
.note.ok .nt{color:var(--low)}
.note.info{border-left-color:var(--info)} .note.info svg{stroke:var(--info)}
.note.info .nt{color:var(--info)}

/* ---------- decision cards ------------------------------------------------
   A hairline and nothing else, with a coloured spine carrying the severity.
   The top card is deliberately heavier - more room, larger title, a wider
   spine - because it has to win the sixty-second test on its own.          */
.card {border:1px solid var(--rule); border-radius:var(--r);
  background:var(--panel); padding:1.3rem 1.45rem; margin-bottom:.85rem;
  position:relative; overflow:hidden; transition:border-color .15s ease;}
.card::before {content:""; position:absolute; left:0; top:0; bottom:0; width:3px;}
.card.critical::before {background:var(--critical);}
.card.high::before     {background:var(--high);}
.card.info::before     {background:var(--info);}
.card:hover{border-color:var(--rule2)}
.card.hero {padding:1.8rem 2rem; margin-bottom:1.5rem;
  border-color:var(--rule2); background:var(--paper);}
.card.hero::before {width:5px;}

.crow {display:flex; align-items:center; gap:.55rem; margin-bottom:.8rem;
  flex-wrap:wrap;}
.band {font-size:.72rem; font-weight:700; letter-spacing:.11em;
  padding:.24rem .5rem; border-radius:3px; border:1px solid;}
.band.critical{color:var(--critical); background:var(--critical-t);
  border-color:#fecaca}
.band.high    {color:var(--high); background:var(--high-t);
  border-color:#fed7aa}
.band.info    {color:var(--info); background:var(--info-t);
  border-color:#bfdbfe}
.rank {font-size:.85rem; color:var(--faint); font-weight:700;}
.flag {font-size:.72rem; letter-spacing:.06em; border:1px solid var(--rule2);
  color:var(--muted); padding:.16rem .45rem; border-radius:3px;}
.flag.ok{border-color:#a7f3d0; color:var(--low); background:var(--low-t)}
.spacer {flex:1;}
.score {text-align:right; line-height:1.15;}
.score b {font-size:1.25rem; color:var(--ink);}
.score span {font-size:.66rem; color:var(--faint); display:block;
  letter-spacing:.07em; text-transform:uppercase;}

.ctitle {font-size:1.22rem; font-weight:700; line-height:1.3; margin:0 0 .3rem 0;
  color:var(--ink);}
.card.hero .ctitle {font-size:1.78rem; line-height:1.25; letter-spacing:-.02em;}
.cmeta {font-size:.82rem; color:var(--muted); font-family:var(--mono);
  margin-bottom:1rem;}

/* ---------- explainability: the decision path ---------- */
.chain {display:grid; grid-template-columns:repeat(auto-fit,minmax(166px,1fr));
  gap:1px; margin:.3rem 0 1.2rem 0; align-items:stretch;
  background:var(--rule); border:1px solid var(--rule); border-radius:var(--r);
  overflow:hidden;}
.node {padding:.72rem .88rem; background:var(--paper); min-width:0;
  transition:background-color .15s ease;
  display:flex; flex-direction:column; justify-content:flex-start;}
.node:hover{background:var(--sunk)}
.node .nk {font-size:.68rem; letter-spacing:.11em; text-transform:uppercase;
  color:var(--faint); margin-bottom:.3rem; font-weight:600;}
.node .nv {font-size:.98rem; font-weight:700; margin-bottom:.18rem;
  color:var(--ink); line-height:1.25;}
.node .ns {font-size:.75rem; color:var(--muted); line-height:1.35;}
/* measured inputs carry the accent, so what came off the control arm is
   distinguishable from what was written into the policy file */
.node.meas {background:var(--tint)}
.node.meas:hover{background:#e6eeff}
.node.meas .nk{color:var(--accent)} .node.meas .ns {color:var(--accent2)}
.node.end {align-items:center; justify-content:center; text-align:center;}
.node.end.low  {background:var(--low-t)}
.node.end.high {background:var(--high-t)}
.node.end.info {background:var(--info-t)}
.node.end b {font-size:1rem; letter-spacing:.04em; line-height:1.25;}
.node.end.low b{color:var(--low)} .node.end.high b{color:var(--high)}
.node.end.info b{color:var(--info)}
"""

CSS_DETAIL = """
/* ---------- factor bars ---------- */
.sec {font-size:.72rem; letter-spacing:.12em; text-transform:uppercase;
  color:var(--faint); margin:0 0 .55rem 0; font-weight:600;}
.bar {margin-bottom:.55rem;}
.bar .bl {display:flex; justify-content:space-between; gap:1rem;
  font-size:.85rem; margin-bottom:.22rem; color:var(--body);}
.bar .bl .src {color:var(--faint); font-size:.76rem; flex:none;
  font-variant-numeric:tabular-nums;}
.bar .track2 {height:4px; background:var(--wash); border-radius:2px;
  overflow:hidden;}
.bar .fill {height:100%; border-radius:2px;}

/* A haircut is not an additive contribution, so it is not drawn as a bar. */
.mult {display:flex; align-items:baseline; gap:.55rem; margin-top:.75rem;
  padding:.5rem .75rem; border:1px dashed #bfdbfe; border-radius:var(--r);
  background:var(--tint); flex-wrap:wrap;}
.mult .ml {font-size:.82rem; font-weight:700; color:var(--accent);}
.mult .mv {font-size:.86rem; color:var(--body);}
.mult .ms {margin-left:auto; font-size:.76rem; color:var(--accent2);}

/* ---------- prose blocks ---------- */
.why {font-size:.95rem; line-height:1.6; color:var(--body);}
.next {border:1px solid #a7f3d0; background:var(--low-t); border-radius:var(--r);
  padding:.8rem 1rem; margin:1rem 0 .85rem 0;}
.next .nl {font-size:.71rem; letter-spacing:.12em; text-transform:uppercase;
  color:var(--low); margin-bottom:.28rem; font-weight:600;}
.next .nt {font-size:1.02rem; font-weight:500; line-height:1.5; color:var(--ink);}
.next .nt b{font-weight:700}
/* Provenance is the graded element, so it must stay legible on a projector -
   quiet, but never small enough to be dismissed as fine print. */
.trace {border-top:1px solid var(--rule); padding-top:.85rem; margin-top:.5rem;
  font-size:.86rem; color:var(--muted); line-height:1.75;}
.trace b {color:var(--ink); font-weight:600;}
.trace code {font-family:var(--mono); color:var(--body); font-size:.92em;
  background:var(--wash); padding:.06rem .32rem; border-radius:3px;}
.card.hero .trace {font-size:.9rem;}

/* ---------- refusals ---------- */
.ex {border:1px solid var(--rule); border-left:3px solid var(--rule2);
  border-radius:0 var(--r) var(--r) 0; padding:.8rem 1rem; margin-bottom:.6rem;
  background:var(--paper);
  transition:border-left-color .15s ease, background-color .15s ease;}
.ex:hover{border-left-color:var(--critical); background:var(--sunk)}
.ex .top {display:flex; align-items:baseline; gap:.75rem; flex-wrap:wrap;}
.ex .id {font-family:var(--mono); font-weight:600; font-size:.95rem;
  color:var(--ink);}
.ex .pr {color:var(--faint); font-size:.88rem;}
.ex .sev {margin-left:auto; font-size:.8rem; color:var(--muted);}
.ex .sev b {color:var(--critical); font-size:1rem;}
.ex .rsn {font-size:.89rem; color:var(--body); margin-top:.4rem; line-height:1.55;}

/* ---------- ledger lines ---------- */
.lrow{border:1px solid var(--rule); border-left:3px solid var(--rule2);
  border-radius:0 var(--r) var(--r) 0; background:var(--paper);
  padding:.55rem .85rem; margin-bottom:.35rem;
  transition:background-color .15s ease}
.lrow:hover{background:var(--sunk)}
.lrow.action{border-left-color:var(--low)}
.lrow.rollback{border-left-color:var(--critical)}
.lrow.detection{border-left-color:var(--medium)}
.lrow.decision{border-left-color:var(--info)}
.lrow.proposal{border-left-color:var(--faint)}
.lrow.restore{border-left-color:var(--rule2)}
.lrow .lh{display:flex; align-items:center; gap:.5rem; font-size:.78rem;
  color:var(--faint); margin-bottom:.25rem; flex-wrap:wrap}
.lrow .lh svg{width:14px; height:14px}
.lrow .lk{font-weight:700; letter-spacing:.07em; text-transform:uppercase}
.lrow.action .lk{color:var(--low)} .lrow.rollback .lk{color:var(--critical)}
.lrow.detection .lk{color:var(--medium)} .lrow.decision .lk{color:var(--info)}
.lrow .mono{font-family:var(--mono); color:var(--body)}
.lrow .rule{margin-left:auto; font-family:var(--mono); font-size:.74rem;
  border:1px solid var(--rule); border-radius:3px; padding:.08rem .35rem;
  color:var(--muted)}
.lrow .rule.bad{border-color:#fecaca; color:var(--critical);
  background:var(--critical-t)}
.lrow .lb{font-size:.93rem; color:var(--ink); line-height:1.5}

/* ---------- reconciliation table ---------- */
.rec{border:1px solid var(--rule); border-radius:var(--r); overflow:hidden;
  margin:.4rem 0 1rem}
.rec .rr{display:flex; gap:1rem; padding:.72rem 1.05rem; font-size:.93rem;
  border-top:1px solid var(--rule); align-items:baseline}
.rec .rr:first-child{border-top:none}
.rec .rr.tot{background:var(--sunk); font-weight:600; color:var(--ink)}
.rec .rn{flex:1; color:var(--body)}
.rec .rn small{display:block; color:var(--faint); font-size:.82rem;
  margin-top:.15rem; line-height:1.45}
.rec .rv{font-variant-numeric:tabular-nums; font-weight:600; color:var(--ink);
  min-width:3.5rem; text-align:right}
"""

CSS_CHROME = """
/* ---------- the moment panel ----------------------------------------------
   Click a point on the timeline and this opens under it: the numbers at that
   minute, which incident was live, and the ledger entries either side. Frosted
   rather than solid because it is a transient overlay on a reading surface -
   the colour behind the glass is the semantic palette out of focus, so the
   panel reads as belonging to the chart rather than as another card.      */
.moment{position:relative; border:1px solid var(--rule2); border-radius:10px;
  overflow:hidden; margin:.85rem 0 1.1rem;}
.mo-bg{position:absolute; inset:-50px; filter:blur(30px);
  background:
    radial-gradient(430px 210px at 12% 0%,  rgba(29,78,216,.34), transparent 65%),
    radial-gradient(470px 230px at 78% 100%,rgba(5,150,105,.30), transparent 65%),
    radial-gradient(320px 190px at 48% 35%, rgba(220,38,38,.18), transparent 70%),
    var(--wash);}
.mo-in{position:relative; background:rgba(255,255,255,.68);
  backdrop-filter:blur(11px) saturate(1.6);
  -webkit-backdrop-filter:blur(11px) saturate(1.6);
  padding:1.15rem 1.35rem 1.2rem;}
.mo-h{display:flex; align-items:baseline; gap:.75rem; flex-wrap:wrap;
  margin-bottom:.85rem}
.mo-t{font-size:1.3rem; font-weight:700; color:var(--ink);
  font-family:var(--mono); letter-spacing:-.02em}
.mo-tag{font-size:.72rem; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; padding:.2rem .5rem; border-radius:3px;
  border:1px solid #fecaca; color:var(--critical);
  background:rgba(254,242,242,.85)}
.mo-tag.calm{border-color:#a7f3d0; color:var(--low);
  background:rgba(236,253,245,.85)}
.mo-sp{flex:1}
.mo-hint{font-size:.78rem; color:var(--muted)}
.mo-figs{display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:1px; background:rgba(212,212,216,.7); border:1px solid rgba(212,212,216,.7);
  border-radius:var(--r); overflow:hidden; margin-bottom:1rem}
.mo-f{background:rgba(255,255,255,.72); padding:.7rem .85rem}
.mo-fk{font-size:.68rem; letter-spacing:.1em; text-transform:uppercase;
  color:var(--muted); font-weight:600; margin-bottom:.25rem}
.mo-fv{font-size:1.12rem; font-weight:700; color:var(--ink);
  font-variant-numeric:tabular-nums; line-height:1.15}
.mo-fv.up{color:var(--low)} .mo-fv.down{color:var(--critical)}
.mo-fs{font-size:.72rem; color:var(--muted); margin-top:.2rem}
.mo-in .lrow{background:rgba(255,255,255,.78)}
.mo-in .lrow:hover{background:rgba(255,255,255,.95)}
.mo-none{font-size:.9rem; color:var(--muted); padding:.5rem 0}

/* ---------- data tables ---------------------------------------------------
   The exchange treatment: a solid header band, zebra rows, right-aligned
   figures on tabular numerals so columns of money line up on the decimal.
   Rendered as HTML rather than through st.dataframe, which draws to a canvas
   and cannot be themed to match anything.                                 */
.tblwrap{border:1px solid var(--rule); border-radius:var(--r);
  overflow:hidden; margin-bottom:.7rem}
.tblscroll{overflow-x:auto}
.tbl{width:100%; border-collapse:collapse; font-size:.88rem}
.tbl thead th{background:var(--chrome); color:#fff; font-size:.72rem;
  letter-spacing:.09em; text-transform:uppercase; font-weight:700;
  padding:.58rem .8rem; text-align:left; white-space:nowrap;
  position:sticky; top:0}
.tbl thead th.num{text-align:right}
.tbl tbody td{padding:.56rem .85rem; border-top:1px solid var(--rule);
  color:var(--body); vertical-align:top}
.tbl tbody tr:nth-child(even){background:var(--sunk)}
.tbl tbody tr:hover{background:var(--tint)}
.tbl td.num{text-align:right; font-variant-numeric:tabular-nums;
  white-space:nowrap}
.tbl td.mono{font-family:var(--mono); font-size:.94em; white-space:nowrap;
  color:var(--ink)}
.tbl td.wide{min-width:24rem; color:var(--ink)}
.tbl .kd{font-size:.7rem; font-weight:700; letter-spacing:.07em;
  text-transform:uppercase; padding:.1rem .4rem; border-radius:3px;
  border:1px solid; white-space:nowrap}
.kd.action{color:var(--low); background:var(--low-t); border-color:#a7f3d0}
.kd.rollback{color:var(--critical); background:var(--critical-t);
  border-color:#fecaca}
.kd.detection{color:var(--medium); background:var(--medium-t);
  border-color:#fde68a}
.kd.decision{color:var(--info); background:var(--info-t); border-color:#bfdbfe}
.kd.proposal,.kd.restore{color:var(--muted); background:var(--wash);
  border-color:var(--rule)}
.kd.critical{color:var(--critical); background:var(--critical-t);
  border-color:#fecaca}
.kd.high{color:var(--high); background:var(--high-t); border-color:#fed7aa}
.kd.low{color:var(--low); background:var(--low-t); border-color:#a7f3d0}
.tbl .rulechip{font-family:var(--mono); font-size:.9em; color:var(--muted)}
.tbl .rulechip.bad{color:var(--critical); font-weight:600}
.tbl a{color:var(--accent); font-weight:600}
.tbl a:hover{text-decoration:underline}
.pager{display:flex; align-items:center; gap:.6rem; font-size:.85rem;
  color:var(--muted); margin:.1rem 0 1rem}
.pager b{color:var(--ink); font-variant-numeric:tabular-nums}

/* ---------- scoreboard ---------- */
.sb {border:1px solid var(--rule); border-radius:var(--r); padding:.8rem .95rem;
  margin-bottom:.6rem; background:var(--paper);}
.sb .sbt {display:flex; justify-content:space-between; align-items:baseline;
  margin-bottom:.45rem; gap:.5rem;}
.sb .sbn {font-size:.8rem; font-weight:500; color:var(--muted);}
.sb .sbv {font-size:1.32rem; font-weight:700; color:var(--low);}
.sb .track2 {height:7px; background:var(--wash); border-radius:4px;
  overflow:hidden; position:relative;}
.sb .fill {height:100%; border-radius:4px; background:#a7f3d0; position:absolute;}
.sb .tick {position:absolute; top:-2px; bottom:-2px; width:2px;
  background:var(--low);}
.sb .cap {font-size:.77rem; color:var(--faint); margin-top:.45rem;
  line-height:1.55;}
.sb .cap b{color:var(--body); font-weight:600}

/* ---------- stat rail ---------- */
.rail{border:1px solid var(--rule); border-radius:var(--r);
  background:var(--paper); padding:.35rem .9rem .85rem;}
.rail-h{font-size:.7rem; letter-spacing:.13em; text-transform:uppercase;
  color:var(--faint); font-weight:700; margin:.95rem 0 .4rem;
  padding-bottom:.3rem; border-bottom:1px solid var(--rule)}
.rail-r{display:flex; justify-content:space-between; gap:.8rem;
  font-size:.87rem; padding:.26rem 0}
.rail-k{color:var(--muted)}
.rail-v{color:var(--ink); font-weight:600; font-variant-numeric:tabular-nums}
.rail-v.ok{color:var(--low)} .rail-v.warn{color:var(--high)}
.rail-v.bad{color:var(--critical)}

/* ---------- sidebar ---------- */
section[data-testid="stSidebar"]{border-right:1px solid var(--rule);
  background:var(--sunk)}
section[data-testid="stSidebar"] .block-container{padding-top:1.15rem}
.brand{display:flex; align-items:center; gap:.6rem; padding:0 0 .9rem;
  border-bottom:1px solid var(--rule); margin-bottom:.9rem}
.brand img{width:32px; height:32px; flex:none}
.brand .nm{font-size:1.02rem; font-weight:700; color:var(--ink); line-height:1.15}
.brand .tag{font-size:.71rem; color:var(--muted); letter-spacing:.03em}
.sbhead {display:flex; align-items:center; gap:.4rem; font-size:.71rem;
  letter-spacing:.13em; text-transform:uppercase; color:var(--faint);
  font-weight:700; margin:1.15rem 0 .5rem; padding-top:.9rem;
  border-top:1px solid var(--rule)}
.sbhead svg{width:12px; height:12px; stroke:var(--faint); flex:none}
.sbhead.first {border-top:none; padding-top:0; margin-top:0;}
.sbl{font-size:.85rem; color:var(--body); line-height:1.85}
.sbl b{color:var(--ink); font-variant-numeric:tabular-nums; font-weight:600}
.sbl a{color:var(--accent); font-weight:500}
.sbl a:hover{text-decoration:underline}

/* ---------- widgets ---------- */
[data-baseweb="radio"] label{font-size:.88rem}
.stRadio [role="radiogroup"]{gap:.15rem}
div[data-testid="stDataFrame"]{border:1px solid var(--rule);
  border-radius:var(--r)}
[data-baseweb="select"] > div, [data-baseweb="input"] > div{
  border-color:var(--rule)!important}
.stSlider [data-baseweb="slider"] div[role="slider"]{
  border-color:var(--accent)!important}

/* ---------- accessibility ---------- */
:focus-visible{outline:2px solid var(--accent); outline-offset:2px;
  border-radius:3px}
@media (prefers-contrast:more){
  :root{--muted:#3f3f46; --faint:#52525b; --rule:#a1a1aa; --rule2:#71717a}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition-duration:.01ms!important;
    animation-duration:.01ms!important}
  /* Collapsing the duration would make the tape and the live dot flicker
     rather than stop, which is worse than the motion was. Both are decoration
     over information that is also on the page, so they simply hold still. */
  .tape-in{animation:none!important; transform:none!important}
  .live .dot{animation:none!important; opacity:1!important}
}

/* ---------- responsive ---------- */
@media (max-width:1100px){
  .block-container{padding-left:1rem; padding-right:1rem}
  .mast{flex-direction:column; gap:1rem}
  .mast-r{border-left:none; padding-left:0; text-align:left}
}
@media (max-width:820px){
  .funnel{grid-template-columns:repeat(2,1fr)}
  .fcell{border-top:1px solid var(--rule)}
  .fcell .n{font-size:1.35rem}
  .card.hero .ctitle{font-size:1.35rem}
  .nav .blurb{display:none}
}
@media (max-width:600px){
  .mast h1{font-size:1.6rem}
  .card{padding:.95rem 1rem} .card.hero{padding:1.2rem 1.25rem}
  .card.hero .ctitle{font-size:1.18rem}
}
"""

st.markdown("<style>" + CSS_TOKENS + CSS_NAV + CSS_CARD + CSS_DETAIL
            + CSS_CHROME + "</style>", unsafe_allow_html=True)

# The chart library cannot read CSS variables, so the same palette is declared
# once here and every mark takes its colour from it. One source, two consumers.
C = {"paper": "#ffffff", "wash": "#f4f4f5", "rule": "#e4e4e7", "ink": "#09090b",
     "body": "#3f3f46", "muted": "#71717a", "faint": "#a1a1aa",
     "accent": "#1d4ed8", "critical": "#dc2626", "high": "#ea580c",
     "medium": "#ca8a04", "low": "#059669", "info": "#2563eb",
     "chrome": "#3b3a7c"}


@alt.theme.register("razorguard", enable=True)
def _theme():
    ax = {"labelColor": C["muted"], "titleColor": C["muted"],
          "gridColor": C["rule"], "domainColor": C["rule"],
          "tickColor": C["rule"], "labelFontSize": 10, "titleFontSize": 10,
          "titleFontWeight": "normal", "gridOpacity": .85}
    return {"config": {
        "background": "transparent",
        "view": {"stroke": "transparent"},
        "font": "Inter, ui-sans-serif, system-ui, sans-serif",
        "axis": ax, "axisX": ax, "axisY": ax,
        "legend": {"labelColor": C["body"], "titleColor": C["muted"],
                   "labelFontSize": 10, "titleFontSize": 10,
                   "symbolStrokeWidth": 2},
        "range": {"category": [C["accent"], C["low"], C["medium"],
                               C["critical"], C["info"]]}}}


# ============================================================================
# STATE
# Every piece of state lives in the URL: which page you are on, which incident
# you opened, and every simulation setting. That buys three things a session
# variable would not. The KPI tiles and the navigation can be ordinary links,
# so they work with the back button and with middle-click. A view can be sent
# to somebody as a URL and arrive showing the same thing. And a reader can see
# in the address bar exactly which settings produced the figure on screen.
# ============================================================================
DEFAULTS = {"days": 2, "seed": 7, **console_data.defaults()}
INT_KEYS = {"days", "seed", "cooldown_min", "causes_per_hour"}


def read_state() -> Dict[str, float]:
    """Query parameters, coerced and bounded. A hand-edited URL is untrusted
    input like any other: a bad value falls back to the default rather than
    taking the console down."""
    q = st.query_params
    out: Dict[str, float] = {}
    for key, default in DEFAULTS.items():
        raw = q.get(key)
        if raw is None:
            out[key] = default
            continue
        try:
            out[key] = int(raw) if key in INT_KEYS else float(raw)
        except (TypeError, ValueError):
            out[key] = default
    out["days"] = min(max(int(out["days"]), 1), 3)
    out["seed"] = int(out["seed"]) % 100000
    out["txn_per_min"] = min(max(out["txn_per_min"], 100.0), 3000.0)
    return out


S = read_state()
VIEW = st.query_params.get("view", "overview")
TUNING = {k: S[k] for k in console_data.defaults()}
NON_DEFAULT = {k: v for k, v in TUNING.items()
               if abs(float(v) - float(DEFAULTS[k])) > 1e-9}


def link(view: str = None, **overrides) -> str:
    """A URL for another view that keeps the current settings.

    Settings at their default are left out, so a URL only ever names what has
    actually been changed - the address bar stays a readable description of the
    run rather than a wall of parameters.
    """
    params = {"view": view or VIEW}
    merged = {**{k: S[k] for k in DEFAULTS}, **overrides}
    for key, value in merged.items():
        if abs(float(value) - float(DEFAULTS[key])) > 1e-9:
            params[key] = (int(value) if key in INT_KEYS else
                           f"{float(value):g}")
    return "?" + urlencode(params)


def goto(view: str) -> None:
    """Change page without navigating the document.

    Writing to `st.query_params` updates the address bar in place, so a view is
    still a link somebody can be sent and the back button still works - but the
    page is not reloaded, which is what an `<a href>` was costing on every
    single click.
    """
    st.query_params["view"] = view
    st.rerun()


# ---------------------------------------------------------------------- pages
# Every route is backed by real control-plane output. Nothing is invented to
# pad the menu, and each carries a live count so a label arrives with its size
# already attached.
NAV = [
    ("overview",  "Overview",        "evidence", None,
     "What the run did, minute by minute."),
    ("decisions", "Decision record", "evidence", "incidents",
     "Every incident as a card, with the reasoning that produced it."),
    ("replay",    "Incident replay", "evidence", None,
     "One incident's ledger, line by line, as it was written."),
    ("recovery",  "Recovery",        "money",    None,
     "What was recovered, what it cost, and how it was measured."),
    ("actions",   "Actions",         "money",    "actions",
     "Every routing action, where it moved traffic and why."),
    ("refused",   "Refused",         "money",    "refused",
     "Every time the system declined to move money, and the rule that stopped it."),
    ("rollbacks", "Rollbacks",       "money",    "rollbacks",
     "Actions the system undid after measuring the destination."),
    ("audit",     "Audit ledger",    "trust",    "audit",
     "The full append-only record, filterable."),
    ("method",    "How it was measured", "trust", None,
     "The paired design, and where the model is and is not."),
    ("settings",  "Settings",        "trust",    None,
     "Change the seed, the traffic and the policy bounds, then re-run."),
]
GROUPS = ["evidence", "money", "trust"]
# Settings is reachable from the sidebar and from the notes that mention it.
# It is not a place a reader goes while reading, so it does not earn a slot in
# a bar whose whole job is to stay legible at a glance.
NAVBAR = [n[0] for n in NAV if n[0] != "settings"]
BLURB = {n[0]: n[4] for n in NAV}
if VIEW not in BLURB:
    VIEW = "overview"


# ------------------------------------------------------------------- helpers

def rupees(x: float) -> str:
    if abs(x) >= 1e7:
        return f"\u20b9{x / 1e7:,.2f} cr"
    if abs(x) >= 1e5:
        return f"\u20b9{x / 1e5:,.2f} L"
    return f"\u20b9{x:,.0f}"


def hhmm(m: int) -> str:
    return f"d{m // 1440 + 1} {(m // 60) % 24:02d}:{m % 60:02d}"


def fcell(ic: str, value: str, label: str, view: str, tone: str = "",
          head: str = "") -> str:
    """A KPI tile. It is a link, because a number you cannot click is a number
    you then have to go and look for."""
    return (f'<a class="fcell {tone}" href="{link(view)}" target="_self">'
            f'<div class="kh">{icon(ic)}{esc(head)}</div>'
            f'<div class="n">{value}</div>'
            f'<div class="k">{esc(label)}{icon("arrow")}</div></a>')


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


def note(body: str, tone: str = "", label: str = "", ic: str = "alert") -> str:
    head = f'<span class="nt">{esc(label)}</span>' if label else ""
    return f'<div class="note {tone}">{icon(ic)}<div>{head}{body}</div></div>'


def b64(p: pathlib.Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


HERE = pathlib.Path(__file__).parent
# The stacked lockup, not the horizontal one: at the width the board gives it,
# the side-by-side version rendered the shield small enough to be a smudge.
# Same mark geometry, wordmark underneath.
LOGO = HERE / "assets/razorguard-stack.svg"
MARK = HERE / "assets/razorguard-mark.svg"
VALIDATION = HERE / "bench/results/validation.json"

KIND_LABEL = {
    "hard_outage": "Gateway hard outage",
    "gradual_degradation": "Gradual gateway degradation",
    "issuer_degradation": "Issuer-wide degradation",
    "shallow_degradation": "Shallow slice degradation",
}
GLYPH = {"detection": "alert", "proposal": "search", "decision": "shield",
         "action": "route", "rollback": "undo", "restore": "check"}
# Rules whose firing is the interesting event, not a footnote.
LOUD = {"efficacy_breaker", "no_healthy_destination", "max_cumulative_divergence",
        "max_causes_per_hour", "target_alarmed"}


# ============================================================================
# DATA
# One control-plane run per (days, seed, routing, tuning). The expensive part
# lives in `razorguard.console_data`, which keeps its own pickle cache on disk;
# this adds Streamlit's in-session memoisation on top so a page change does not
# even hit the filesystem.
# ============================================================================

@st.cache_data(show_spinner=False, max_entries=12)
def run(days: int, seed: int, routing: bool, tuning: Tuple[Tuple[str, float], ...]):
    return console_run(days, seed, routing, dict(tuning))


_t = tuple(sorted((k, float(v)) for k, v in TUNING.items()))
_spin = ("Running both arms of the experiment\u2026 first run of these settings "
         "takes about forty seconds")
with st.spinner(_spin):
    control = run(S["days"], S["seed"], False, _t)
    treat = run(S["days"], S["seed"], True, _t)

valid = control["attempts"] == treat["attempts"]
d_succ = treat["successes"] - control["successes"]
d_rev = treat["revenue"] - control["revenue"]
ctrl_sr = control["successes"] / control["attempts"]
treat_sr = treat["successes"] / treat["attempts"]
net = NetRecovery(gross_inr=d_rev,
                  control_cost_inr=control["processing_cost"],
                  treatment_cost_inr=treat["processing_cost"],
                  payments_recovered=d_succ)

ledger = treat["ledger"]
inc = treat["incidents"].copy()
inc["label"] = inc["kind"].map(lambda k: KIND_LABEL.get(k, k.replace("_", " ")))
ctl_inc = control["incidents"].set_index("id")

acted = ledger[ledger["kind"] == "action"]
rolled = ledger[ledger["kind"] == "rollback"]
refused_rows = ledger[(ledger["kind"] == "decision") & (ledger["rule"] != "")]

# Refusals reconcile, and are shown reconciling. The engine's own counters and
# the ledger's rule tally disagree for two legitimate reasons, and a console
# that prints both without saying so is inviting the first question a reviewer
# will ask.
REFUSED_TOTAL = treat["blocked"] + treat["escalated"]
BY_RULE = sum(treat["blocked_by_rule"].values())
ROLLBACK_ESC = treat["rollbacks"]
BREAKER_FANOUT = REFUSED_TOTAL - BY_RULE - ROLLBACK_ESC

COUNTS = {"incidents": len(inc), "actions": treat["actions"],
          "refused": REFUSED_TOTAL, "rollbacks": treat["rollbacks"],
          "audit": treat["audit_events"]}


# ------------------------------------------------------------------- sidebar

st.sidebar.markdown(
    '<div class="brand">'
    + (f'<img src="data:image/svg+xml;base64,{b64(MARK)}">' if MARK.exists() else "")
    + '<div><div class="nm">RazorGuard</div>'
      '<div class="tag">Payment recovery control plane</div></div></div>',
    unsafe_allow_html=True)

st.sidebar.markdown(
    f'<div class="sbhead first">{icon("sliders")}This run</div>'
    f'<div class="sbl">seed <b>{S["seed"]}</b> &middot; '
    f'<b>{S["days"]}</b> simulated day(s)<br>'
    f'traffic <b>{S["txn_per_min"]:,.0f}</b> payments/min<br>'
    + (f'<b>{len(NON_DEFAULT)}</b> setting(s) changed from default<br>'
       if NON_DEFAULT else 'all settings at their defaults<br>')
    + f'<a href="{link("settings")}" target="_self">Change these \u2192</a></div>',
    unsafe_allow_html=True)

if VALIDATION.is_file():
    _v = json.loads(VALIDATION.read_text(encoding="utf-8"))
    _r = _v["summary"]["recovered_inr"]
    _seeds = _v["config"]["seeds"]
    _losing = sum(1 for t in _v["trials"] if t["recovered_inr"] <= 0)
    _invalid = sum(1 for t in _v["trials"] if not t["valid"])
    _lo, _hi = _r["min"], _r["max"]
    _span = (_hi - _lo) or 1.0
    _f0 = (_r["ci_lo"] - _lo) / _span * 100
    _f1 = (_r["ci_hi"] - _lo) / _span * 100
    _mean = (_r["mean"] - _lo) / _span * 100
    st.sidebar.markdown(
        f'<div class="sbhead">{icon("target")}Validated result</div>'
        f'<div class="sb"><div class="sbt">'
        f'<span class="sbn">mean of {len(_seeds)} seeds</span>'
        f'<span class="sbv">{rupees(_r["mean"])}</span></div>'
        f'<div class="track2">'
        f'<div class="fill" style="left:{_f0:.0f}%;width:{_f1 - _f0:.0f}%"></div>'
        f'<div class="tick" style="left:{_mean:.0f}%"></div></div>'
        f'<div class="cap">95% CI <b>{rupees(_r["ci_lo"])}</b> to '
        f'<b>{rupees(_r["ci_hi"])}</b>, spanning '
        f'{rupees(_r["min"])}\u2013{rupees(_r["max"])} across seeds. '
        f'<b>{_losing} of {len(_seeds)}</b> lost money; '
        f'<b>{_invalid}</b> refused for divergent demand.</div></div>'
        f'<div class="sbl" style="font-size:.68rem;color:var(--faint)">'
        f'Benchmarked at the default settings. The figures on this page come '
        f'from the run above, whatever it is set to.</div>',
        unsafe_allow_html=True)

st.sidebar.markdown(
    f'<div class="sbhead">{icon("lock")}Policy bounds</div>'
    f'<div class="sbl">'
    f'max shift per action <b>{S["max_shift"]:.0%}</b><br>'
    f'max divergence / key <b>{S["max_divergence"]:.0%}</b><br>'
    f'min confidence <b>{S["min_confidence"]:.2f}</b><br>'
    f'min drop acted on <b>{S["min_drop_pp"]:g}pp</b><br>'
    f'cooldown per key <b>{int(S["cooldown_min"])} min</b><br>'
    f'causes/hour then stop <b>{int(S["causes_per_hour"])}</b><br>'
    f'target must be <b>{S["target_advantage"]:g}pp</b> healthier<br>'
    f'canary always left <b>3%</b></div>', unsafe_allow_html=True)

st.sidebar.markdown(
    f'<div class="sbhead">{icon("cpu")}Where AI is used</div>'
    '<div class="sbl">Detection, attribution, policy and routing are '
    '<b>deterministic</b>. A model writes the operator note, investigates a '
    'past decision, and advises on escalations \u2014 and can change none of '
    'them.</div>', unsafe_allow_html=True)

# ============================================================================
# THE HEADER
# Four bands: a status strip, the board, the primary navigation, and a tape.
# The model is an exchange front page, because that is what this console is -
# a live board whose headline numbers and navigation have to be readable from
# across a room, not a settings panel with a chart under it.
# ============================================================================

if not valid:
    st.error("The two arms did not face identical demand. No recovery figure "
             "can be reported from this comparison.")
    st.stop()

_gain_pp = (treat_sr - ctrl_sr) * 100
_share = d_rev / control["exposure"]


def tb(key: str, value: str, sub: str = "") -> str:
    tail = f"<small>{sub}</small>" if sub else ""
    return (f'<div class="tb-i"><div class="tb-k">{esc(key)}</div>'
            f'<div class="tb-v">{value}{tail}</div></div>')


# --------------------------------------------------------------- band 1
# Facts about the run, not routes. This strip used to carry five links that
# duplicated the navigation directly below it, which made a reader choose
# between two identical paths to the same page for no reason.
st.markdown(
    '<div class="topbar bleed"><div class="tb-grid">'
    + tb("Run", f'seed {S["seed"]}',
         f'{S["days"]} simulated day(s) &middot; '
         f'{S["txn_per_min"]:,.0f} payments/min')
    + tb("Payment attempts", f'{control["attempts"]:,}',
         "identical in both arms")
    + tb("Injected incidents", f"{len(inc)}",
         f'{treat["alarms"]} alarms raised')
    + tb("Audit events", f'{treat["audit_events"]:,}',
         f'{treat["actions"]} actions &middot; {REFUSED_TOTAL} refusals')
    + tb("Policy", "defaults" if not NON_DEFAULT
         else f"{len(NON_DEFAULT)} changed",
         "all bounds as benchmarked" if not NON_DEFAULT
         else "figures differ from the benchmark")
    + '</div></div>', unsafe_allow_html=True)

# --------------------------------------------------------------- band 2
_quotes = (
    f'<div class="q up"><div class="qk">Success rate &middot; router on</div>'
    f'<div class="qv">{icon("trending")}{treat_sr:.2%}</div>'
    f'<div class="qd">+{_gain_pp:.2f}pp vs <b>{ctrl_sr:.2%}</b> off</div>'
    f'<div class="live"><span class="dot"></span>PAIRED CONTROL ARM</div></div>'

    f'<div class="q"><div class="qk">Money at risk</div>'
    f'<div class="qv">{rupees(control["exposure"])}</div>'
    f'<div class="qd">lost by the control arm</div></div>'

    f'<div class="q up"><div class="qk">Recovered, net</div>'
    f'<div class="qv">{rupees(net.net_inr)}</div>'
    f'<div class="qd"><b>{_share:.1%}</b> of what was at risk</div></div>'

    f'<div class="q"><div class="qk">Payments saved</div>'
    f'<div class="qv">{d_succ:,}</div>'
    f'<div class="qd">of <b>{control["attempts"]:,}</b> attempts</div></div>')

st.markdown(
    '<div class="board bleed">'
    + (f'<img src="data:image/svg+xml;base64,{b64(LOGO)}" alt="RazorGuard">'
       if LOGO.exists() else '<h1 style="margin:0">RazorGuard</h1>')
    + '<div class="tag">Degradation caught at the slice a dashboard cannot '
      'see, routed around under written bounds, and the recovery <b>measured '
      'against a control arm rather than projected</b>.</div>'
      '<div class="sp"></div>'
      f'<div class="quotes">{_quotes}</div></div>', unsafe_allow_html=True)

# --------------------------------------------------------------- band 3
# Buttons, not anchors. An anchor navigates the document, so every click cost
# a full page load - a white flash, the scroll position lost, the websocket
# reconnecting. A button reruns the script in place; the URL is updated
# alongside it, so a view is still a link somebody can be sent.
_bar = [n for n in NAV if n[0] in NAVBAR]
_widths = [len(n[1]) + (4 if COUNTS.get(n[3]) is not None else 0) for n in _bar]
with st.container(key="rgnav"):
    for _col, (_vid, _label, _grp, _ck, _blurb) in zip(
            st.columns(_widths), _bar):
        _n = COUNTS.get(_ck)
        _lbl = _label + (f"  `{_n:,}`" if _n is not None else "")
        if _col.button(_lbl, key=f"nav_{_vid}", help=_blurb,
                       type="primary" if _vid == VIEW else "tertiary",
                       width="stretch"):
            goto(_vid)

# --------------------------------------------------------------- band 4
# The tape is the eighteen incidents in the order they happened, each showing
# what it put at risk and what came back. Built from the two arms directly
# rather than from the card analysis, which is not computed until further down.
_tape = []
for _r in inc.sort_values("start").itertuples():
    _c = ctl_inc.loc[_r.id]
    _pre = (_c["coh_pre_suc"] / _c["coh_pre_att"]) if _c["coh_pre_att"] else 0.0
    _tick = (_c["coh_pre_rev"] / _c["coh_pre_suc"]) if _c["coh_pre_suc"] else 0.0
    _risk = max(0.0, _pre * _c["coh_att"] - _c["coh_suc"]) * _tick
    _got = _r.coh_rev - _c["coh_rev"]
    _pct = (_got / _risk) if _risk > 0 else 0.0
    _dir = "up" if _got > 0 else ("down" if _got < 0 else "")
    _tape.append(
        f'<span class="tk {_dir}"><b>{esc(_r.id)}</b>'
        f'<span class="v">{rupees(_risk)} at risk</span>'
        f'<span class="c">{"+" if _got > 0 else ""}{rupees(_got)}'
        f' ({_pct:+.0%})</span></span>')
st.markdown('<div class="tape bleed"><div class="tape-tag">INCIDENTS</div>'
            '<div class="tape-in">' + "".join(_tape) * 2
            + '</div><div class="tape-fade"></div></div>',
            unsafe_allow_html=True)

# ------------------------------------------------------------------- tiles
_tiles = [
    ("Recovery", rupees(net.net_inr), "Recovered, net of fees", "recovery"),
    ("Payments", f"{d_succ:,}", "Payments saved", "recovery"),
    ("Actions", f'{treat["actions"]:,}', "Routing actions taken", "actions"),
    ("Refusals", f"{REFUSED_TOTAL:,}", "Refused by policy", "refused"),
    ("Rollbacks", f'{treat["rollbacks"]:,}', "Undone by the system",
     "rollbacks"),
]
with st.container(key="rgkpi"):
    for _i, (_col, _tile) in enumerate(zip(st.columns(5, gap="small"), _tiles)):
        _eyebrow, _value, _label, _view = _tile
        with _col:
            # The tile's colour comes from its position, through a keyed
            # container: Streamlit stamps `st-key-<key>` on the element it
            # actually renders, which is the only reliable CSS hook a widget
            # offers - a div opened by st.markdown is closed before the next
            # widget is drawn, so it can never wrap one.
            with st.container(key=f"rgkpi{_i}"):
                if st.button(f"*{_eyebrow}*  \n**{_value}**  \n{_label}",
                             key=f"kpi_{_eyebrow}", width="stretch",
                             help=BLURB[_view]):
                    goto(_view)

_PAGE = next(n for n in NAV if n[0] == VIEW)
st.markdown(
    f'<div class="pagehead"><h2>{esc(_PAGE[1])}</h2>'
    f'<span class="ph-sub">{esc(_PAGE[4])}</span>'
    f'<span class="ph-sp"></span>'
    f'<span class="ph-as">seed {S["seed"]} &middot; {S["days"]} simulated '
    f'day(s) &middot; {S["txn_per_min"]:,.0f} payments/min</span></div>',
    unsafe_allow_html=True)


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
        return "critical", "CRITICAL"
    if a["exposure_share"] >= 0.03:
        return "high", "MAJOR"
    return "info", "MINOR"


def outcome_of(a, kind):
    if a["n_act"] and a["recovered"] > 0:
        return "low", "ROUTED AROUND"
    if a["n_act"]:
        return "high", "ACTED, NO GAIN"
    if kind == "issuer_degradation":
        return "high", "ESCALATED &middot; NO HEALTHY TARGET"
    if a["n_det"]:
        return "info", "DETECTED, HELD"
    return "info", "BELOW ACTION FLOOR"


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
                  a["collapse"] * 100, "var(--critical)",
                  f'{a["collapse"]:.0%} of its own baseline')
            + bar("Money at risk", rupees(a["at_risk"]),
                  a["exposure_share"] * 100, "var(--medium)",
                  f'{a["exposure_share"]:.1%} of the run')
            + bar("Traffic in the measured cohort",
                  f'{a["coh_att"]:,} attempts',
                  a["coverage"] * 100 * 8, "var(--info)",
                  f'{a["coverage"]:.2%} of the run')
            + bar("Recovered", rupees(a["recovered"]),
                  a["capture"] * 100, "var(--low)",
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
               f'step(s), each capped at {S["max_shift"]:.0%} and '
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
               f'{S["min_drop_pp"]:g}pp at {S["min_confidence"]:.0%} '
               f'confidence, so the system watched it and left the weights '
               f'alone.')

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
    "rollback_target_degraded":
        "The destination fell below its own baseline after traffic arrived. "
        "The shift was reverted to baseline weights and escalated.",
}

analyses = []
for _, _row in inc.iterrows():
    analyses.append(analyse(_row, ctl_inc.loc[_row["id"]], ledger,
                            control["attempts"], control["exposure"]))
order = sorted(range(len(analyses)), key=lambda k: -analyses[k]["at_risk"])


def table(headers, rows) -> str:
    """An HTML data table in the exchange treatment.

    `headers` is a list of (label, css class) and every row is a list of
    already-escaped cells. Rendered as HTML rather than through st.dataframe,
    which draws to a canvas and cannot be themed to match the rest of the page.
    """
    head = "".join(f'<th class="{c}">{esc(h)}</th>' for h, c in headers)
    body = "".join("<tr>" + "".join(
        f'<td class="{c}">{v}</td>' for v, (_h, c) in zip(r, headers)) + "</tr>"
        for r in rows)
    return (f'<div class="tblwrap"><div class="tblscroll"><table class="tbl">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
            f'</div></div>')


def ledger_rows(frame, limit=80):
    out = []
    for r in frame.head(limit).itertuples():
        ic = GLYPH.get(r.kind, "layers")
        rule = (f'<span class="rule{" bad" if r.rule in LOUD else ""}">'
                f'{esc(r.rule)}</span>' if r.rule else "")
        out.append(
            f'<div class="lrow {r.kind}"><div class="lh">{icon(ic)}'
            f'<span class="lk">{r.kind}</span><span>{hhmm(r.minute)}</span>'
            f'<span class="mono">{esc(r.subject)}</span>{rule}</div>'
            f'<div class="lb">{esc(r.summary)}</div></div>')
    return "".join(out)


def moment_panel(minute: int, merged) -> str:
    """What the control plane was doing at one minute, opened by clicking the
    timeline. Frosted rather than solid: it is a transient overlay on a reading
    surface, and the colour behind the glass is the semantic palette out of
    focus, so it reads as part of the chart rather than as another card."""
    row = merged[merged["minute"] == minute]
    if row.empty:
        return ""
    r = row.iloc[0]

    live = inc[(inc["start"] <= minute) & (inc["end"] > minute)]
    if len(live):
        names = ", ".join(f'{x.id} \u00b7 {x.label}' for x in live.itertuples())
        tag = f'<span class="mo-tag">INSIDE {esc(names)}</span>'
    else:
        tag = '<span class="mo-tag calm">NO INCIDENT ACTIVE</span>'

    gap = int(r["gap"])
    figs = [
        ("Recovered by now", f'{int(r["cumulative"]):,}',
         "extra settled payments", "up" if r["cumulative"] > 0 else ""),
        ("This minute", f"{gap:+,}", "treatment minus control",
         "up" if gap > 0 else ("down" if gap < 0 else "")),
        ("Attempts", f'{int(r["attempts"]):,}', "identical in both arms", ""),
        ("Success rate, on", f'{r["success_rate"]:.2%}', "router allowed to act",
         "up"),
        ("Success rate, off", f'{r["success_rate_ctl"]:.2%}', "control arm", ""),
    ]
    fig_html = "".join(
        f'<div class="mo-f"><div class="mo-fk">{esc(k)}</div>'
        f'<div class="mo-fv {t}">{v}</div>'
        f'<div class="mo-fs">{esc(s)}</div></div>' for k, v, s, t in figs)

    near = ledger[(ledger["minute"] >= minute - 3)
                  & (ledger["minute"] <= minute + 3)]
    if near.empty:
        body = ('<div class="mo-none">Nothing was written to the ledger within '
                'three minutes either side. The line moves here because earlier '
                'shifts are still paying, not because a new decision was '
                'made.</div>')
    else:
        body = ('<div class="sec">Ledger, three minutes either side '
                f'({len(near)} entries)</div>' + ledger_rows(near, limit=14))

    return (f'<div class="moment"><div class="mo-bg"></div><div class="mo-in">'
            f'<div class="mo-h"><span class="mo-t">{hhmm(minute)}</span>{tag}'
            f'<span class="mo-sp"></span>'
            f'<span class="mo-hint">click another point to move '
            f'&middot; double-click the chart to clear</span></div>'
            f'<div class="mo-figs">{fig_html}</div>{body}</div></div>')


# ============================================================================
# PAGES
# ============================================================================

if VIEW == "overview":
    merged = treat["series"].merge(
        control["series"][["minute", "successes", "success_rate"]],
        on="minute", suffixes=("", "_ctl"))
    merged["gap"] = merged["successes"] - merged["successes_ctl"]
    merged["cumulative"] = merged["gap"].cumsum()
    merged["clock"] = merged["minute"].map(hhmm)

    acts = ledger[ledger["kind"].isin(["action", "rollback"])].copy()
    acts["when"] = acts["minute"].map(hhmm)

    total = int(S["days"]) * 1440
    RANGES: List[Tuple[str, Tuple[int, int]]] = [("Full run", (0, total))]
    for d in range(int(S["days"])):
        RANGES.append((f"Day {d + 1}", (d * 1440, (d + 1) * 1440)))
    RANGES.append(("Evening peak", (1150, 1350)))
    RANGES.append(("Overnight", (120, 320)))

    left, right = st.columns([3.15, 1], gap="medium")

    with left:
        st.markdown(f'<div class="sect">{icon("activity")}Recovery over time'
                    f'</div>', unsafe_allow_html=True)
        pick_range = st.radio("Window", [r[0] for r in RANGES], horizontal=True,
                              label_visibility="collapsed")
        lo, hi = dict(RANGES)[pick_range]
        st.markdown('<div class="hint">Scroll to zoom &middot; drag to pan '
                    '&middot; double-click to reset &middot; hover for the '
                    'values under the pointer &middot; <b>click any point to '
                    'open that minute</b></div>', unsafe_allow_html=True)

        win = merged[(merged["minute"] >= lo) & (merged["minute"] <= hi)]
        winc = inc[(inc["end"] >= lo) & (inc["start"] <= hi)].copy()
        winc["mid"] = (winc[["start", "end"]].mean(axis=1)).astype(int)
        wacts = acts[(acts["minute"] >= lo) & (acts["minute"] <= hi)]

        # Zoom and pan on the x axis only - the y scales are meaningful and
        # should not be squashed by a stray wheel. The click selection is a
        # separate parameter so the two do not fight over the same gesture.
        zoom = alt.selection_interval(bind="scales", encodings=["x"],
                                      name="zoom")
        moment = alt.selection_point(fields=["minute"], on="click",
                                     empty=False, name="moment")
        hover = alt.selection_point(nearest=True, on="pointermove",
                                    fields=["minute"], empty=False, name="hover")
        axis = alt.Axis(labelExpr="'d' + (floor(datum.value/1440)+1) + ' ' + "
                                  "format(floor(datum.value/60)%24,'02') + ':' "
                                  "+ format(datum.value%60,'02')",
                        labelAngle=0, tickCount=7, labelPadding=6)

        bands = alt.Chart(winc).mark_rect(opacity=.085).encode(
            x="start:Q", x2="end:Q", color=alt.value(C["critical"]),
            tooltip=[alt.Tooltip("id:N", title="incident"),
                     alt.Tooltip("label:N", title="kind"),
                     alt.Tooltip("blast_radius:N", title="blast radius"),
                     alt.Tooltip("slices:Q", title="routes hit")])
        # Each band gets its id at the top, so an incident can be named without
        # hovering it - the difference between a chart you can read and a chart
        # you have to interrogate.
        band_ids = alt.Chart(winc).mark_text(
            align="center", baseline="top", dy=2, fontSize=9,
            fontWeight="bold", color=C["critical"], opacity=.75).encode(
            x="mid:Q", y=alt.value(0), text="id:N")

        base = alt.Chart(win)
        area = base.mark_area(
            line={"color": C["accent"], "strokeWidth": 2},
            color=alt.Gradient(
                gradient="linear",
                stops=[alt.GradientStop(color="#ffffff", offset=0),
                       alt.GradientStop(color="#dbe6fd", offset=.55),
                       alt.GradientStop(color="#b9cdfa", offset=1)],
                x1=1, x2=1, y1=1, y2=0)).encode(
            x=alt.X("minute:Q", title=None, axis=axis,
                    scale=alt.Scale(domain=[lo, hi], nice=False)),
            y=alt.Y("cumulative:Q",
                    title="cumulative extra successful payments",
                    axis=alt.Axis(format=",", tickCount=5)))
        zero = alt.Chart(win).mark_rule(
            color=C["rule"], strokeWidth=1).encode(y=alt.datum(0))

        # The run's final figure, written on the chart rather than left to the
        # reader to find by hovering the last pixel.
        _last = win.iloc[-1:]
        endpoint = alt.Chart(_last).mark_point(
            size=64, filled=True, color=C["accent"]).encode(
            x="minute:Q", y="cumulative:Q")
        endlabel = alt.Chart(_last).mark_text(
            align="right", dx=-8, dy=-12, fontSize=12, fontWeight="bold",
            color=C["accent"]).encode(
            x="minute:Q", y="cumulative:Q",
            text=alt.Text("cumulative:Q", format=","))

        xrule = base.mark_rule(color=C["faint"], strokeDash=[3, 3]).encode(
            x="minute:Q",
            opacity=alt.condition(hover, alt.value(.85), alt.value(0)),
            tooltip=[alt.Tooltip("clock:N", title="at"),
                     alt.Tooltip("cumulative:Q", title="recovered so far",
                                 format=","),
                     alt.Tooltip("gap:Q", title="this minute", format="+,"),
                     alt.Tooltip("attempts:Q", title="attempts", format=",")]
        ).add_params(hover, moment)
        dot = base.mark_point(size=58, filled=True, color=C["accent"]).encode(
            x="minute:Q", y="cumulative:Q",
            opacity=alt.condition(hover, alt.value(1), alt.value(0)))
        picked = base.mark_rule(color=C["chrome"], strokeWidth=2).encode(
            x="minute:Q",
            opacity=alt.condition(moment, alt.value(.9), alt.value(0)))

        _ev = st.altair_chart(
            (bands + band_ids + zero + area + xrule + dot + picked + endpoint
             + endlabel).add_params(zoom).properties(height=250),
            width="stretch", on_select="rerun", key="tl")

        # The chart click is the usual way in. `?at=` is the other one, so a
        # particular minute can be sent to somebody as a link - and so this
        # panel is reachable from a test, which a chart selection is not.
        _sel = (_ev.get("selection", {}) or {}).get("moment") or []
        _minute = None
        if _sel:
            _minute = int(_sel[0]["minute"])
        else:
            try:
                _minute = int(st.query_params.get("at"))
            except (TypeError, ValueError):
                _minute = None
        if _minute is not None:
            st.markdown(moment_panel(_minute, merged), unsafe_allow_html=True)
        else:
            st.markdown(note(
                f'Every step up is a payment that failed with routing off and '
                f'succeeded with it on. Red bands are the injected incidents '
                f'\u2014 the line climbs inside them and flattens between, '
                f'which is what you would expect if the system is doing '
                f'anything at all. Over the full run it reaches '
                f'<b>{d_succ:,}</b>. <b>Click any point on the line</b> to '
                f'open what the control plane was doing at that minute.',
                "info", "How to read this", "trending"),
                unsafe_allow_html=True)

        st.markdown(f'<div class="sect">{icon("trending")}Success rate, both '
                    f'arms</div>', unsafe_allow_html=True)

        long = win.melt(id_vars=["minute", "clock"],
                        value_vars=["success_rate", "success_rate_ctl"],
                        var_name="arm", value_name="sr")
        long["arm"] = long["arm"].map({"success_rate": "router on",
                                       "success_rate_ctl": "router off"})
        lines = alt.Chart(long).mark_line(
            strokeWidth=1.5, interpolate="monotone").encode(
            x=alt.X("minute:Q", title=None, axis=axis,
                    scale=alt.Scale(domain=[lo, hi], nice=False)),
            y=alt.Y("sr:Q", title="success rate", axis=alt.Axis(format="%"),
                    scale=alt.Scale(zero=False)),
            color=alt.Color("arm:N", title=None, scale=alt.Scale(
                domain=["router on", "router off"],
                range=[C["accent"], "#b8bcc4"]),
                legend=alt.Legend(orient="top-right", direction="horizontal")))
        hover2 = alt.selection_point(nearest=True, on="pointermove",
                                     fields=["minute"], empty=False)
        xrule2 = alt.Chart(win).mark_rule(
            color=C["faint"], strokeDash=[3, 3]).encode(
            x="minute:Q",
            opacity=alt.condition(hover2, alt.value(.85), alt.value(0)),
            tooltip=[alt.Tooltip("clock:N", title="at"),
                     alt.Tooltip("success_rate:Q", title="router on",
                                 format=".2%"),
                     alt.Tooltip("success_rate_ctl:Q", title="router off",
                                 format=".2%"),
                     alt.Tooltip("attempts:Q", title="attempts", format=",")]
        ).add_params(hover2)
        marks = alt.Chart(wacts).mark_point(
            size=68, filled=True, opacity=.95).encode(
            x="minute:Q", y=alt.value(8),
            shape=alt.Shape("kind:N", title=None, scale=alt.Scale(
                domain=["action", "rollback"],
                range=["triangle-up", "triangle-down"]),
                legend=alt.Legend(orient="bottom-right",
                                  direction="horizontal")),
            color=alt.Color("kind:N", scale=alt.Scale(
                domain=["action", "rollback"],
                range=[C["low"], C["critical"]]), legend=None),
            tooltip=[alt.Tooltip("when:N", title="at"), "kind:N", "subject:N",
                     alt.Tooltip("summary:N", title="what")])

        st.altair_chart(
            (bands + lines + xrule2 + marks).add_params(zoom)
            .properties(height=255), width="stretch")
        st.markdown(note(
            'The gap <i>is</i> the story \u2014 a percentage point of success '
            'rate is worth crores at this volume. Green triangles are routing '
            'actions, red ones are rollbacks; hover either for what the system '
            'decided and why. Zoom into an incident band to watch one unfold '
            'minute by minute.', "info", "Why the lines look close",
            "activity"), unsafe_allow_html=True)

    with right:
        st.markdown(
            '<div class="rail">'
            '<div class="rail-h">Selected window</div>'
            + rail_row("Range", f"{hhmm(lo)} \u2192 {hhmm(hi)}")
            + rail_row("Minutes", f"{hi - lo:,}")
            + rail_row("Incidents", f"{len(winc)}")
            + rail_row("Actions", f"{len(wacts[wacts['kind'] == 'action'])}",
                       "ok")
            + rail_row("Rollbacks",
                       f"{len(wacts[wacts['kind'] == 'rollback'])}", "bad")
            + '<div class="rail-h">Money, whole run</div>'
            + rail_row("Gross recovered", rupees(net.gross_inr))
            + rail_row("Processing fees",
                       "\u2212" + rupees(net.incremental_cost_inr), "warn")
            + rail_row("Net recovered", rupees(net.net_inr), "ok")
            + rail_row("Money at risk", rupees(control["exposure"]))
            + rail_row("Share recovered",
                       f'{d_rev / control["exposure"]:.1%}', "ok")
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
            '<div class="hint" style="margin-top:.6rem">Both arms must see the '
            'same number of attempts. If they ever differ the run refuses to '
            'report a recovery figure at all.</div>', unsafe_allow_html=True)


elif VIEW == "decisions":
    st.markdown(note(
        f'The {len(analyses)} injected incidents, ranked by the money the '
        f'control arm actually lost to them \u2014 not by how loud they were. '
        f'Each card shows what the system saw, which bounds it checked, what '
        f'it did, and what that was worth. <b>Nothing here was composed for '
        f'the demo</b>. Where two incidents overlap in time their cohorts '
        f'overlap too, so these figures sum to slightly more than the run '
        f'total; the run total is the one that carries a confidence interval.',
        "", "Read the top card first"), unsafe_allow_html=True)

    # The board first, then the cards. A reader who wants the shape of the run
    # gets it in one screen; a reader who wants the reasoning scrolls.
    st.markdown(f'<div class="sect">{icon("layers")}All {len(order)} incidents,'
                f' worst first</div>', unsafe_allow_html=True)
    _rows = []
    for i, k in enumerate(order, 1):
        r, a = inc.iloc[k], analyses[k]
        cls, sev = band_of(a)
        oc, label = outcome_of(a, r["kind"])
        _rows.append([
            f'{i}',
            f'<a href="{link("replay", )}&inc={esc(r["id"])}" target="_self">'
            f'{esc(r["id"])}</a>',
            f'<span class="kd {"decision" if cls == "info" else cls}">{sev}</span>',
            esc(r["label"]),
            f'{hhmm(int(r["start"]))} · {r["duration"]}m',
            f'{r["slices"]}',
            rupees(a["at_risk"]),
            (f'<span style="color:var(--low);font-weight:600">'
             f'+{rupees(a["recovered"])}</span>' if a["recovered"] > 0 else
             f'<span style="color:var(--critical)">{rupees(a["recovered"])}'
             f'</span>' if a["recovered"] < 0 else
             '<span style="color:var(--faint)">—</span>'),
            f'{a["capture"]:.0%}',
            # A detection at or before the injected start minute is not a
            # zero-minute detection, it is a sub-minute one; printing "0m"
            # reads as a missing value.
            (('&lt;1m' if a["ttd"] <= 0 else f'{a["ttd"]}m')
             if a["ttd"] is not None else
             '<span style="color:var(--faint)">not raised</span>'),
            f'<span class="kd {"decision" if oc == "info" else oc}">'
            f'{label}</span>'])
    st.markdown(table(
        [("#", "num"), ("Incident", "mono"), ("Severity", ""), ("Kind", ""),
         ("Window", "mono"), ("Routes", "num"), ("At risk", "num"),
         ("Recovered", "num"), ("Capture", "num"), ("Caught in", "num"),
         ("Outcome", "")], _rows), unsafe_allow_html=True)
    st.markdown('<div class="hint">Severity is the share of the run’s own '
                'losses this incident accounts for, not an absolute rupee '
                'threshold. Click an incident id to replay its ledger.</div>',
                unsafe_allow_html=True)

    st.markdown(f'<div class="sect">{icon("shield")}The reasoning, card by '
                f'card</div>', unsafe_allow_html=True)
    for i, k in enumerate(order, 1):
        render_incident(i, inc.iloc[k], analyses[k], hero=(i == 1))


elif VIEW == "replay":
    st.markdown(note(
        'Every line below was written by the control plane as it ran \u2014 '
        '<b>including the refusals, which are the half worth reading.</b> '
        'Entries are matched to the incident by subject as well as by time, so '
        'an overlapping incident cannot lend this one its actions.',
        "", "Written at the time, not for the demo", "lock"),
        unsafe_allow_html=True)

    labels = [f'{inc.iloc[k]["id"]}   \u00b7   {inc.iloc[k]["label"]}   '
              f'\u00b7   {hhmm(int(inc.iloc[k]["start"]))}   \u00b7   '
              f'{rupees(analyses[k]["at_risk"])} at risk   \u00b7   '
              f'{analyses[k]["n_act"]} action(s)' for k in order]
    c1, c2 = st.columns([3, 2])
    _want = st.query_params.get("inc", "")
    _idx = next((i for i, k in enumerate(order)
                 if inc.iloc[k]["id"] == _want), 0)
    choice = c1.selectbox("Incident", range(len(labels)),
                          format_func=lambda i: labels[i], index=_idx)
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
        st.markdown(ledger_rows(w), unsafe_allow_html=True)
        if len(w) > 80:
            st.caption(f"{len(w) - 80} further entries in this window.")


elif VIEW == "recovery":
    st.markdown(
        '<div class="lede">The headline figure is a <b>difference between two '
        'runs</b>, not a projection from a success-rate delta. Both arms face '
        'bit-identical demand because every draw is keyed on '
        '<code>(seed, minute, method, issuer)</code>; the only thing that '
        'differs is whether the router is allowed to act.</div>',
        unsafe_allow_html=True)

    st.markdown(
        f'<div class="rec">'
        f'<div class="rr"><span class="rn">Gross value recovered'
        f'<small>Extra settled value in the treatment arm, valued at each '
        f'slice\u2019s own average ticket.</small></span>'
        f'<span class="rv">{rupees(net.gross_inr)}</span></div>'
        f'<div class="rr"><span class="rn">Processing fees on it'
        f'<small>MDR the control arm never paid, because those payments never '
        f'settled. UPI carries none, card 1.80%, netbanking 0.90%.</small>'
        f'</span><span class="rv">\u2212{rupees(net.incremental_cost_inr)}'
        f'</span></div>'
        f'<div class="rr tot"><span class="rn">Net recovered</span>'
        f'<span class="rv">{rupees(net.net_inr)}</span></div>'
        f'</div>', unsafe_allow_html=True)

    a, b, c2 = st.columns(3)
    a.markdown(
        f'<div class="sect">{icon("coin")}Against the money at risk</div>'
        f'<div class="why">The control arm lost <b>{rupees(control["exposure"])}'
        f'</b> to these incidents. The treatment arm recovered '
        f'<b>{d_rev / control["exposure"]:.1%}</b> of it. The rest is either '
        f'unroutable \u2014 issuer-wide faults have no healthy destination \u2014 '
        f'or below the action floor.</div>', unsafe_allow_html=True)
    b.markdown(
        f'<div class="sect">{icon("trending")}In payments</div>'
        f'<div class="why"><b>{d_succ:,}</b> payments settled that failed in '
        f'the control arm, out of <b>{control["attempts"]:,}</b> attempts '
        f'seen identically by both. Fleet success rate moved '
        f'{ctrl_sr:.2%} \u2192 {treat_sr:.2%}, a gain of '
        f'<b>{(treat_sr - ctrl_sr) * 100:.2f}pp</b>.</div>',
        unsafe_allow_html=True)
    c2.markdown(
        f'<div class="sect">{icon("target")}Why UPI matters</div>'
        f'<div class="why">Zero MDR on UPI is regulation, not a discount. A '
        f'recovered UPI payment costs nothing to settle, so it is pure margin; '
        f'a recovered card payment gives 1.8% of itself back. Fees came to '
        f'<b>{net.incremental_cost_inr / net.gross_inr:.1%}</b> of gross here.'
        f'</div>', unsafe_allow_html=True)

    st.markdown(f'<div class="sect">{icon("layers")}Where it came from</div>',
                unsafe_allow_html=True)
    _rows = sorted(range(len(analyses)),
                   key=lambda k: -analyses[k]["recovered"])
    _top = max(abs(analyses[k]["recovered"]) for k in _rows) or 1.0
    st.markdown("".join(
        bar(f'{inc.iloc[k]["id"]} \u00b7 {inc.iloc[k]["label"]}',
            rupees(analyses[k]["recovered"]),
            abs(analyses[k]["recovered"]) / _top * 100,
            "var(--low)" if analyses[k]["recovered"] > 0 else "var(--critical)",
            f'{analyses[k]["capture"]:.0%} of its own risk')
        for k in _rows), unsafe_allow_html=True)
    st.markdown(note(
        'Cohorts overlap where incidents overlap in time, so these sum to '
        'slightly more than the run total. Negative bars are incidents where '
        'routing did not pay \u2014 they are shown rather than dropped, because '
        'a system that only reports its wins is not reporting a measurement.',
        "info", "", "alert"), unsafe_allow_html=True)


elif VIEW == "actions":
    st.markdown(
        f'<div class="lede">Every one of the <b>{treat["actions"]:,}</b> times '
        f'this run moved money. Each action shifts at most '
        f'<b>{S["max_shift"]:.0%}</b> of the source gateway\u2019s share for one '
        f'(method, issuer) key, leaves a 3% canary behind so the source stays '
        f'observable, and is reversible by construction.</div>',
        unsafe_allow_html=True)

    if acted.empty:
        st.info("No routing actions in this run.")
    else:
        by_key = acted["subject"].value_counts()
        k1, k2, k3 = st.columns(3)
        k1.markdown(
            f'<div class="sect">{icon("route")}Spread</div><div class="why">'
            f'<b>{len(by_key)}</b> distinct route keys touched, most often '
            f'<code>{esc(by_key.index[0])}</code> at <b>{by_key.iloc[0]}</b> '
            f'action(s). A wide incident legitimately fans out across every key '
            f'its gateway served.</div>', unsafe_allow_html=True)
        k2.markdown(
            f'<div class="sect">{icon("undo")}Reversed</div><div class="why">'
            f'<b>{treat["rollbacks"]:,}</b> were undone after the destination '
            f'was measured and found wanting \u2014 '
            f'{treat["rollbacks"] / max(treat["actions"], 1):.0%} of them. '
            f'<a href="{link("rollbacks")}" target="_self">See which \u2192</a>'
            f'</div>', unsafe_allow_html=True)
        k3.markdown(
            f'<div class="sect">{icon("check")}Restored</div><div class="why">'
            f'<b>{treat["restores"]:,}</b> keys were returned to their baseline '
            f'weights once the incident cleared. Nothing stays diverted because '
            f'everybody forgot about it.</div>', unsafe_allow_html=True)

        st.markdown(f'<div class="sect">{icon("layers")}Actions by route key'
                    f'</div>', unsafe_allow_html=True)
        _top = int(by_key.iloc[0])
        st.markdown("".join(
            bar(key, f"{n} action(s)", n / _top * 100, "var(--low)",
                f"{n / len(acted):.0%} of actions")
            for key, n in by_key.head(12).items()), unsafe_allow_html=True)

        st.markdown(f'<div class="sect">{icon("clock")}The ledger</div>',
                    unsafe_allow_html=True)
        st.markdown(ledger_rows(acted), unsafe_allow_html=True)
        if len(acted) > 80:
            st.caption(f"{len(acted) - 80} further actions; the full record is "
                       f"on the audit ledger page.")


elif VIEW == "refused":
    st.markdown(
        '<div class="lede">A ledger that records only successful actions hides '
        'exactly the decisions worth reviewing. Everything on this page is the '
        'system <b>declining to move money</b>.</div>', unsafe_allow_html=True)

    # Two counters, honestly reconciled. The engine counts every refusal; the
    # ledger's rule tally counts the ones that named a rule on a `decision`
    # entry. They differ for two real reasons, and showing the difference is
    # better than showing two numbers and hoping nobody subtracts them.
    st.markdown(
        f'<div class="rec">'
        f'<div class="rr"><span class="rn">Refusals with a named policy rule'
        f'<small>One <code>decision</code> ledger entry each, listed below.'
        f'</small></span><span class="rv">{BY_RULE:,}</span></div>'
        f'<div class="rr"><span class="rn">Rollbacks'
        f'<small>An action undone after the destination was measured. The '
        f'engine counts these as escalations; they are written as '
        f'<code>rollback</code> entries, not <code>decision</code> entries, so '
        f'they carry no policy rule. '
        f'<a href="{link("rollbacks")}" target="_self">See them \u2192</a>'
        f'</small></span><span class="rv">{ROLLBACK_ESC:,}</span></div>'
        f'<div class="rr"><span class="rn">Alarms covered by an already-open '
        f'breaker<small>When the efficacy breaker is open the engine suppresses '
        f'every alarm in that tick but writes one ledger line for the tick, so '
        f'the tally under-counts by the fan-out.</small></span>'
        f'<span class="rv">{BREAKER_FANOUT:,}</span></div>'
        f'<div class="rr tot"><span class="rn">Refusals in this run</span>'
        f'<span class="rv">{REFUSED_TOTAL:,}</span></div>'
        f'</div>', unsafe_allow_html=True)

    rules = treat["blocked_by_rule"]
    if not rules:
        st.info("No rule-named refusals in this run.")
    else:
        st.markdown(f'<div class="sect">{icon("ban")}Which rule stopped it, '
                    f'and what that rule is protecting against</div>',
                    unsafe_allow_html=True)
        for name, n in sorted(rules.items(), key=lambda kv: -kv[1]):
            st.markdown(
                f'<div class="ex"><div class="top">'
                f'<span class="id">{esc(name)}</span>'
                f'<span class="pr">{n / BY_RULE:.0%} of rule-named refusals'
                f'</span>'
                f'<span class="sev">fired <b>{n:,}</b> times</span></div>'
                f'<div class="rsn">{RULE_TEXT.get(name, "")}</div></div>',
                unsafe_allow_html=True)


elif VIEW == "rollbacks":
    st.markdown(
        f'<div class="lede">The system moved traffic, measured the '
        f'destination, found it worse than its own baseline, and <b>put the '
        f'traffic back</b> \u2014 <b>{treat["rollbacks"]:,}</b> times. This is '
        f'why the source route always keeps a 3% canary: without traffic '
        f'staying behind there is nothing to compare the destination '
        f'against.</div>', unsafe_allow_html=True)
    if rolled.empty:
        st.info("No rollbacks in this run.")
    else:
        st.markdown(ledger_rows(rolled), unsafe_allow_html=True)
        st.markdown(note(
            'A rollback is not a failure of the system, it is the system '
            'working. What would be a failure is moving traffic onto a '
            'degrading route and leaving it there because nothing was watching.',
            "ok", "", "check"), unsafe_allow_html=True)


elif VIEW == "audit":
    st.markdown(
        f'<div class="lede">Every event the control plane wrote, in the order '
        f'it wrote them. Append-only and sequence-numbered: '
        f'<b>{treat["audit_events"]:,}</b> entries covering every money '
        f'movement, every refusal with the rule that decided it, and every '
        f'rollback. Written as the run proceeded, not assembled '
        f'afterwards.</div>', unsafe_allow_html=True)

    f1, f2, f3 = st.columns([2, 1, 1])
    kinds2 = f1.multiselect(
        "Event kinds", sorted(ledger["kind"].unique()),
        default=["detection", "action", "rollback"], key="audit_kinds")
    needle = f2.text_input("Search subject or summary", "")
    per = f3.selectbox("Rows per page", [50, 100, 250], index=0)

    v = ledger
    if kinds2:
        v = v[v["kind"].isin(kinds2)]
    if needle:
        m = needle.lower()
        v = v[v["subject"].str.lower().str.contains(m, regex=False)
              | v["summary"].str.lower().str.contains(m, regex=False)
              | v["rule"].str.lower().str.contains(m, regex=False)]

    pages = max(1, (len(v) + per - 1) // per)
    page = st.number_input("Page", 1, pages, 1, key="audit_page") if pages > 1 else 1
    lo_i = (int(page) - 1) * per
    chunk = v.iloc[lo_i:lo_i + per]

    st.markdown(
        f'<div class="pager">Showing <b>{lo_i + 1:,}</b>–'
        f'<b>{min(lo_i + per, len(v)):,}</b> of <b>{len(v):,}</b> matching '
        f'entries &middot; page <b>{int(page)}</b> of <b>{pages}</b></div>',
        unsafe_allow_html=True)

    if chunk.empty:
        st.info("No events match this filter.")
    else:
        st.markdown(table(
            [("Seq", "num"), ("When", "mono"), ("Kind", ""),
             ("Subject", "mono"), ("Rule", "mono"), ("What happened", "wide")],
            [[f"{r.seq}", hhmm(r.minute),
              f'<span class="kd {r.kind}">{r.kind}</span>',
              esc(r.subject),
              (f'<span class="rulechip{" bad" if r.rule in LOUD else ""}">'
               f'{esc(r.rule)}</span>' if r.rule else
               '<span style="color:var(--faint)">—</span>'),
              esc(r.summary)]
             for r in chunk.itertuples()]), unsafe_allow_html=True)

    st.download_button(
        "Download the full ledger as CSV",
        ledger.to_csv(index=False).encode(), "razorguard-ledger.csv", "text/csv")


elif VIEW == "method":
    st.markdown(f'<div class="sect">{icon("target")}Why these numbers can be '
                f'trusted</div>', unsafe_allow_html=True)
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
        + node("Blindness", "Detectors cannot import", "the incident plan", "",
               "A test enforces this: the detector package may not import "
               "scenarios.py")
        + '</div>', unsafe_allow_html=True)

    st.markdown(note(
        f'<b>{rupees(net.net_inr)} net</b> over {int(S["days"])} simulated '
        f'day(s) at seed {S["seed"]}: {rupees(net.gross_inr)} of extra settled '
        f'value, minus {rupees(net.incremental_cost_inr)} of processing fees on '
        f'the payments that only succeeded because of the routing. It is a '
        f'measured difference between two runs, not a projection from a '
        f'success-rate delta.', "ok", "What the headline figure is", "check"),
        unsafe_allow_html=True)

    st.markdown(note(
        'This is a simulator, and a simulator can be made to say anything. '
        'Three things stop it here: the detectors are barred from importing '
        'the incident plan, the control arm is the only source of the recovery '
        'figure, and the run is refused outright if the two arms ever see '
        'different demand. The bench directory holds the same experiment across '
        'eight seeds with a confidence interval, and the sensitivity sweep that '
        'found routing <i>losing</i> money on a saturated fleet \u2014 which is '
        'why the efficacy breaker exists.', "", "What it is not"),
        unsafe_allow_html=True)

    st.markdown(f'<div class="sect">{icon("layers")}Attributing recovery to an '
                f'incident</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="why">A slice key is <code>gateway|method|issuer</code>. '
        'The routes an incident <b>breaks</b> are not the routes where its '
        'recovery <b>lands</b> \u2014 succeeding at routing means emptying the '
        'broken ones, and the traffic takes the money with it to another '
        'gateway. So per-incident recovery is measured over the <b>cohort</b>: '
        'every route serving the same (method, issuer) pairs, whichever gateway '
        'carries it. Demand is drawn per (minute, method, issuer), so the '
        'cohort sees identical attempts in both arms. Measured on the broken '
        'routes alone, the largest and best-handled outage in this run reports '
        'a loss rather than its largest gain; four tests in '
        '<code>tests/test_console_data.py</code> hold the distinction in '
        'place.</div>', unsafe_allow_html=True)

    st.markdown(f'<div class="sect">{icon("cpu")}Where the model is, and is '
                f'not</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="chain">'
        + node("Detection", "Deterministic", "beta posterior, no model")
        + node("Attribution", "Deterministic", "lift with coverage")
        + node("Policy", "Deterministic", "a written rule file")
        + node("Routing", "Deterministic", "bounded weight shifts")
        + node("Narrator", "Model", "writes the operator note", "meas")
        + node("Investigator", "Model", "explains a past decision", "meas")
        + node("Advisor", "Model", "advises on escalations", "meas")
        + '<div class="node end info"><b>MODEL CHANGES NOTHING</b></div>'
        + '</div>', unsafe_allow_html=True)
    st.caption("An LLM that can move money is a liability. Here it can read "
               "the ledger and write English about it, and that is all.")


elif VIEW == "settings":
    st.markdown(
        '<div class="lede">Change the world the control plane runs against, or '
        'the bounds it runs under, and the whole console re-computes. Nothing '
        'here is cosmetic: every control feeds the actual simulation and the '
        'actual policy engine. <b>Settings live in the URL</b>, so a run you '
        'like is a link you can send.</div>', unsafe_allow_html=True)
    st.markdown(note(
        'A combination nobody has run yet takes about forty seconds while both '
        'arms execute; it is then cached to disk and instant thereafter. The '
        'benchmark figures in the sidebar were measured at the defaults and do '
        'not move when you change these.', "info", "", "clock"),
        unsafe_allow_html=True)

    with st.form("settings"):
        st.markdown(f'<div class="sect">{icon("database")}The world</div>',
                    unsafe_allow_html=True)
        w1, w2, w3 = st.columns(3)
        f_seed = w1.number_input(
            "Seed", value=int(S["seed"]), step=1, min_value=0,
            max_value=99999,
            help="Changes every random draw: traffic, outcomes, and where the "
                 "injected incidents land. Both arms always share it.")
        f_days = w2.slider(
            "Simulated days", 1, 3, int(S["days"]),
            help="Each day is 1,440 one-minute ticks with a diurnal traffic "
                 "curve. Two days gives 18 injected incidents.")
        f_tpm = w3.slider(
            "Payments per minute", 100, 3000, int(S["txn_per_min"]), step=50,
            help="Fleet-wide traffic at the diurnal midpoint. Lower volumes "
                 "make slices thin, which is where naive threshold detectors "
                 "start firing on noise.")

        st.markdown(f'<div class="sect">{icon("lock")}Policy bounds</div>',
                    unsafe_allow_html=True)
        p1, p2, p3, p4 = st.columns(4)
        f_shift = p1.slider(
            "Max shift per action", 0.1, 1.0, float(S["max_shift"]), step=0.05,
            help="Fraction of the source gateway's current share one action "
                 "may move. Measured at 80%; 40% cost about 61% of the "
                 "available recovery.")
        f_div = p2.slider(
            "Max divergence per key", 0.1, 1.0, float(S["max_divergence"]),
            step=0.05,
            help="Total weight that may sit away from baseline for one key at "
                 "any time.")
        f_conf = p3.slider(
            "Min confidence", 0.50, 0.999, float(S["min_confidence"]),
            step=0.005, format="%.3f",
            help="Posterior probability a detection needs before it can "
                 "justify moving money.")
        f_drop = p4.slider(
            "Min drop acted on (pp)", 0.5, 20.0, float(S["min_drop_pp"]),
            step=0.5,
            help="Below this the dip is not worth the cost of routing.")

        p5, p6, p7 = st.columns(3)
        f_cool = p5.slider(
            "Cooldown per key (min)", 0, 60, int(S["cooldown_min"]),
            help="Acting again before the last change has shown up in the data "
                 "is how a control loop starts oscillating.")
        f_cause = p6.slider(
            "Causes per hour, then stop", 1, 20, int(S["causes_per_hour"]),
            help="Counts distinct root causes, not weight changes - one wide "
                 "outage legitimately fans out across every key its gateway "
                 "served.")
        f_adv = p7.slider(
            "Target must be healthier by (pp)", 0.0, 30.0,
            float(S["target_advantage"]), step=0.5,
            help="Below this the expected gain from moving is inside the noise.")

        c_go, c_reset = st.columns([1, 5])
        go = c_go.form_submit_button("Run it", type="primary")
        reset = c_reset.form_submit_button("Reset to defaults")

    if go:
        st.query_params.clear()
        st.query_params.update({
            k: str(v) for k, v in
            {"view": "overview", "seed": int(f_seed), "days": int(f_days),
             "txn_per_min": float(f_tpm), "max_shift": float(f_shift),
             "max_divergence": float(f_div), "min_confidence": float(f_conf),
             "min_drop_pp": float(f_drop), "cooldown_min": int(f_cool),
             "causes_per_hour": int(f_cause),
             "target_advantage": float(f_adv)}.items()})
        st.rerun()
    if reset:
        st.query_params.clear()
        st.query_params["view"] = "overview"
        st.rerun()

    st.markdown(f'<div class="sect">{icon("search")}What this run is doing '
                f'differently</div>', unsafe_allow_html=True)
    if not NON_DEFAULT:
        st.markdown('<div class="why">Nothing. Every setting is at the value '
                    'the benchmarks were measured with, so the figures on '
                    'screen are directly comparable to the ones in the README '
                    'and the PDFs.</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="rec">' + "".join(
            f'<div class="rr"><span class="rn">{esc(k)}</span>'
            f'<span class="rv">{DEFAULTS[k]:g} \u2192 {float(v):g}</span></div>'
            for k, v in sorted(NON_DEFAULT.items())) + '</div>',
            unsafe_allow_html=True)
        st.markdown(note(
            'The sidebar\u2019s validated result is still the eight-seed '
            'benchmark at the defaults. It is left there deliberately, as the '
            'reference these figures should be read against \u2014 not quietly '
            're-labelled to match whatever is on screen.', "", "", "alert"),
            unsafe_allow_html=True)

    st.markdown(f'<div class="sect">{icon("book")}Things worth trying</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="hint">Each of these is a real re-run, and each was checked '
        'before being suggested — the effects described are the ones these '
        'settings actually produce at seed 7 over two days.</div>',
        unsafe_allow_html=True)
    st.markdown(
        f'<div class="why">'
        f'<b><a href="{link("overview", max_shift=0.4)}" target="_self">'
        f'Max shift 40%</a></b> — the original a-priori cap. Gross '
        f'recovery falls from ₹1.20 cr to ₹0.71 cr. This is the '
        f'measurement that changed the default.<br>'
        f'<b><a href="{link("overview", causes_per_hour=1)}" target="_self">'
        f'One cause per hour</a></b> — the tightest anti-oscillation '
        f'budget. Recovery roughly halves and refusals climb from 93 to 147: '
        f'the system spends its budget on the first outage and escalates '
        f'everything after it.<br>'
        f'<b><a href="{link("overview", min_confidence=0.999)}" target="_self">'
        f'Confidence 0.999</a></b> — demand near-certainty before moving '
        f'money. Actions drop from 100 to 74 and about ₹4.8 L of recovery '
        f'goes with them. Caution is not free.<br>'
        f'<b><a href="{link("overview", txn_per_min=200)}" target="_self">'
        f'200 payments/min</a></b> — thin slices, where a naive threshold '
        f'detector fires on noise. Everything shrinks; the interesting part is '
        f'that the refusal mix changes shape, not just size.'
        f'</div>', unsafe_allow_html=True)
