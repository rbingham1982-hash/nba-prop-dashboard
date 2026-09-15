"""
nfl_hub.py — the NFL Week Hub, the landing view of the NFL tab in season.

Rendered by nba_prop_dashboard with one call, and kept in its own module because it is the
largest single view in the NFL tab. Everything on it comes from two places: nfl_grades
(every skill player's grade for the week) and weekly_picks (the board we published, with
its result attached).

Interactive by design: the position toggle drives every section, clicking a bubble on the
week-at-a-glance chart or a row in the table spotlights that player, and clicking a team in
the report card filters the table to it. The three selection sources share one rule —
whichever changed most recently wins — enforced in _sync_selections.
"""
from __future__ import annotations

import datetime
import html

import pandas as pd
import streamlit as st

import nfl_grades as ng

_K = "nfl_hub_"                      # session-state prefix for every widget on the page
_POS = ("QB", "RB", "WR", "TE")
_ACCENT = "#818cf8"
_GRADE_COLOR = {"A": "#34d399", "B": "#60a5fa", "C": "#fbbf24", "D": "#fb923c", "F": "#f87171"}
_COMP = [("efficiency", "Efficiency"), ("production", "Production"),
         ("usage", "Usage"), ("mistakes", "Clean")]

# Mirrors the dashboard's _SHARED_CHART palette, which this module cannot import.
_CHART = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#191c23",
              font=dict(family="Inter, sans-serif", size=11, color="#8a91a5"),
              hoverlabel=dict(bgcolor="#1f2330", bordercolor="#2e3341",
                              font=dict(color="#dfe1ea", size=12)))
_AXIS = dict(gridcolor="#252a35", linecolor="#252a35", zerolinecolor="#252a35",
             tickfont=dict(size=10, color="#5c6272"))
_CFG = {"displayModeBar": False}

_CSS = """<style>
.nh-hero { position:relative; overflow:hidden; border-radius:20px; padding:1.5rem 1.7rem; margin-bottom:1rem;
  background: radial-gradient(900px 360px at 0% 0%, rgba(129,140,248,0.30), transparent 62%),
              radial-gradient(800px 360px at 100% 100%, rgba(167,139,250,0.22), transparent 60%),
              linear-gradient(135deg,#181b26 0%,#111318 100%);
  border:1px solid #2a2f3d; box-shadow:0 18px 50px rgba(0,0,0,0.35); }
.nh-hero-grid { display:grid; grid-template-columns:1.15fr 1fr; gap:1.6rem; align-items:center; }
.nh-kicker { font-size:0.66rem; font-weight:800; letter-spacing:0.22em; text-transform:uppercase; color:#a5b0ff; }
.nh-title { font-size:2.7rem; font-weight:800; line-height:1.04; margin:0.3rem 0 0.4rem;
  background:linear-gradient(135deg,#ffffff 0%,#a5b0ff 60%,#c4b5fd 100%);
  -webkit-background-clip:text; background-clip:text; color:transparent; }
.nh-sub { color:#8a91a5; font-size:0.86rem; max-width:36rem; line-height:1.5; }
.nh-chips { display:flex; flex-wrap:wrap; gap:0.45rem; margin-top:0.95rem; }
.nh-chip { background:rgba(129,140,248,0.10); border:1px solid rgba(129,140,248,0.28); color:#c7cdfc;
  padding:0.3rem 0.75rem; border-radius:999px; font-size:0.74rem; font-weight:600; }
.nh-chip b { color:#fff; }
.nh-potw { background:rgba(17,19,24,0.55); border:1px solid rgba(255,255,255,0.07); border-radius:18px;
  padding:1.1rem 1.2rem; backdrop-filter:blur(6px); }
.nh-potw-tag { font-size:0.62rem; font-weight:800; letter-spacing:0.2em; text-transform:uppercase; color:#fbbf24; margin-bottom:0.6rem; }
.nh-potw-row { display:flex; gap:1.05rem; align-items:center; }
.nh-potw-img { width:112px; height:112px; border-radius:50%; object-fit:cover; object-position:top; background:#1f2330;
  box-shadow:0 0 0 4px var(--ring), 0 0 36px var(--ring); flex-shrink:0; }
.nh-potw-name { font-size:1.5rem; font-weight:800; color:#fff; line-height:1.1; }
.nh-meta { color:#8a91a5; font-size:0.78rem; margin:0.2rem 0 0.45rem; display:flex; align-items:center; gap:0.4rem; }
.nh-logo { width:22px; height:22px; object-fit:contain; }
.nh-badge { display:inline-flex; align-items:center; justify-content:center; font-weight:900; color:#0b0e14; flex-shrink:0; }
.nh-badge.lg { width:70px; height:70px; font-size:1.9rem; border-radius:16px; animation:nhglow 3.2s ease-in-out infinite; }
.nh-badge.md { width:52px; height:52px; font-size:1.35rem; border-radius:13px; }
.nh-badge.sm { width:34px; height:34px; font-size:0.9rem; border-radius:9px; }
@keyframes nhglow { 0%,100% { box-shadow:0 0 14px var(--glow); } 50% { box-shadow:0 0 34px var(--glow); } }
.nh-line { color:#dfe1ea; font-size:0.84rem; margin-top:0.35rem; }
.nh-bars { display:grid; grid-template-columns:78px 1fr 28px; gap:0.3rem 0.55rem; align-items:center;
  margin-top:0.75rem; font-size:0.7rem; color:#8a91a5; }
.nh-bar { height:7px; border-radius:7px; background:#252a35; overflow:hidden; }
.nh-bar > span { display:block; height:100%; border-radius:7px; background:linear-gradient(90deg,#818cf8,#a78bfa); }
.nh-sec { font-size:0.7rem; font-weight:800; letter-spacing:0.18em; text-transform:uppercase; color:#818cf8; margin:1.4rem 0 0.65rem; }
.nh-sec span { color:#5c6272; font-weight:600; letter-spacing:0.04em; text-transform:none; margin-left:0.5rem; }
.nh-colhead { font-size:0.66rem; font-weight:800; letter-spacing:0.16em; color:#a5b0ff; margin:0 0 0.45rem 0.1rem; }
.nh-card { display:flex; align-items:center; gap:0.65rem; padding:0.55rem 0.7rem; margin-bottom:0.5rem; border-radius:13px;
  background:#191c23; border:1px solid #252a35; border-left:4px solid var(--tc);
  transition:transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
.nh-card:hover { transform:translateY(-2px); box-shadow:0 10px 26px rgba(0,0,0,0.4); border-color:#3a4154; }
.nh-card img.hs { width:44px; height:44px; border-radius:50%; object-fit:cover; object-position:top; background:#1f2330; flex-shrink:0; }
.nh-card .nm { font-weight:700; color:#e7e9f1; font-size:0.85rem; line-height:1.2; }
.nh-card .mt { color:#6b7286; font-size:0.69rem; line-height:1.35; }
.nh-card .sc { margin-left:auto; display:flex; align-items:center; gap:0.5rem; }
.nh-card .n { font-weight:800; color:#fff; font-size:0.95rem; min-width:1.6rem; text-align:right; }
.nh-rank { color:#4b5163; font-weight:800; font-size:0.76rem; width:1rem; text-align:center; }
.nh-spot { background:#191c23; border:1px solid #252a35; border-radius:16px; padding:1rem 1.1rem;
  border-top:4px solid var(--tc); }
.nh-spot-row { display:flex; gap:0.9rem; align-items:center; }
.nh-spot-img { width:78px; height:78px; border-radius:50%; object-fit:cover; object-position:top; background:#1f2330;
  box-shadow:0 0 0 3px var(--tc); flex-shrink:0; }
.nh-spot-name { font-size:1.2rem; font-weight:800; color:#fff; line-height:1.15; }
.nh-tiles { display:grid; grid-template-columns:repeat(4,1fr); gap:0.6rem; }
.nh-tile { background:#191c23; border:1px solid #252a35; border-radius:14px; padding:0.8rem 1rem; }
.nh-tile .l { font-size:0.62rem; letter-spacing:0.16em; text-transform:uppercase; color:#6b7286; font-weight:800; }
.nh-tile .v { font-size:1.75rem; font-weight:800; color:#fff; line-height:1.15; margin-top:0.15rem; }
.nh-tile .s { color:#8a91a5; font-size:0.72rem; margin-top:0.15rem; }
.nh-pick { display:flex; align-items:center; gap:0.55rem; padding:0.5rem 0.75rem; border-radius:12px; background:#191c23;
  border:1px solid #252a35; margin-bottom:0.45rem; font-size:0.8rem; color:#dfe1ea; }
.nh-pick .sub { color:#6b7286; font-size:0.7rem; }
.nh-pick .res { margin-left:auto; font-weight:800; font-size:0.66rem; letter-spacing:0.08em; padding:0.22rem 0.6rem; border-radius:999px; flex-shrink:0; }
.nh-win { background:rgba(52,211,153,0.14); color:#34d399; border:1px solid rgba(52,211,153,0.38); }
.nh-loss { background:rgba(248,113,113,0.12); color:#f87171; border:1px solid rgba(248,113,113,0.36); }
.nh-push, .nh-pend { background:rgba(251,191,36,0.12); color:#fbbf24; border:1px solid rgba(251,191,36,0.32); }
.nh-dnp { background:rgba(92,98,114,0.18); color:#8a91a5; border:1px solid #2e3341; }
@media (max-width: 900px) {
  .nh-hero-grid { grid-template-columns:1fr; } .nh-title { font-size:2rem; }
  .nh-potw-img { width:84px; height:84px; } .nh-tiles { grid-template-columns:repeat(2,1fr); }
}
</style>"""


# ── data ────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _weeks(season):
    return ng.weeks_available(season)


@st.cache_data(ttl=3600, show_spinner=False)
def _completed(season):
    return ng.completed_weeks(season)


@st.cache_data(ttl=3600, show_spinner=False)
def _grades(season, week):
    return ng.grade_week(season, week)


@st.cache_data(ttl=3600, show_spinner=False)
def _season_grades(season):
    return ng.grade_season(season)


@st.cache_data(ttl=86400, show_spinner=False)
def _teams():
    return ng.team_meta()


@st.cache_data(ttl=3600, show_spinner=False)
def _picks(season, week):
    # grade() is idempotent and only writes when a result is new, so the hub keeps the
    # published record current without waiting for the next newsletter build.
    import weekly_picks as wp
    try:
        wp.grade()
    except Exception:
        pass
    return wp._load().get(wp._key(season, week)) or {}, wp.record(season=season)


# ── small builders ──────────────────────────────────────────────────────────

def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _gc(grade) -> str:
    return _GRADE_COLOR.get(str(grade)[:1], "#94a3b8")


def _rgba(hex_color: str, a: float) -> str:
    h = str(hex_color or _ACCENT).lstrip("#")
    if len(h) != 6:
        h = _ACCENT.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def _team(meta: dict, abbr: str):
    m = meta.get(abbr) or {}
    return m.get("color") or _ACCENT, m.get("logo"), m.get("nick") or abbr


def _img(url, cls: str) -> str:
    return f"<img class='{cls}' src='{_esc(url)}' loading='lazy'>" if url else f"<div class='{cls}'></div>"


def _logo(meta, abbr) -> str:
    _, logo, _ = _team(meta, abbr)
    return f"<img class='nh-logo' src='{_esc(logo)}'>" if logo else ""


def _badge(grade, size: str) -> str:
    c = _gc(grade)
    return (f"<span class='nh-badge {size}' style='background:linear-gradient(135deg,{c},{_rgba(c, 0.72)});"
            f"--glow:{_rgba(c, 0.55)};'>{_esc(grade)}</span>")


def _bars(r) -> str:
    out = "<div class='nh-bars'>"
    for key, label in _COMP:
        v = r.get(key)
        if v is None or v != v:
            continue
        out += (f"<span>{label}</span><div class='nh-bar'><span style='width:{max(2, float(v)):.0f}%'></span></div>"
                f"<span style='text-align:right;color:#dfe1ea;'>{float(v):.0f}</span>")
    return out + "</div>"


def _card(r, rank: int, meta: dict) -> str:
    tc, _, _ = _team(meta, r["team"])
    return (f"<div class='nh-card' style='--tc:{tc};'>"
            f"<span class='nh-rank'>{rank}</span>{_img(r.get('headshot'), 'hs')}"
            f"<div><div class='nm'>{_esc(r['player'])}</div>"
            f"<div class='mt'>{_esc(r['position'])} · {_esc(r['team'])} v {_esc(r['opponent'])}<br>{_esc(r['line'])}</div></div>"
            f"<div class='sc'><span class='n'>{r['score']:.0f}</span>{_badge(r['grade'], 'sm')}</div></div>")


def _sec(title: str, sub: str = "") -> None:
    st.markdown(f"<div class='nh-sec'>{_esc(title)}{f'<span>{_esc(sub)}</span>' if sub else ''}</div>",
                unsafe_allow_html=True)


# ── selection plumbing ──────────────────────────────────────────────────────

def _sync_selections(g: pd.DataFrame) -> None:
    """
    Fold chart and table clicks into the widgets they drive, before those widgets render.

    Each source keeps the signature of the last selection it applied, so a click is applied
    once and a later choice in the picker is not overwritten by a stale chart selection that
    Streamlit keeps in session state across reruns.
    """
    ids = set(g["player_id"])

    def take(src, value, target):
        sig = _K + "sig_" + src
        if value is not None and st.session_state.get(sig) != value:
            st.session_state[sig] = value
            st.session_state[target] = value

    try:
        pts = (st.session_state.get(_K + "scatter") or {}).get("selection", {}).get("points", [])
        if pts:
            pid = (pts[0].get("customdata") or [None])[0]
            if pid in ids:
                take("scatter", pid, _K + "spot")
    except Exception:
        pass
    try:
        rows = (st.session_state.get(_K + "table") or {}).get("selection", {}).get("rows", [])
        shown = st.session_state.get(_K + "table_ids") or []
        if rows and rows[0] < len(shown) and shown[rows[0]] in ids:
            take("table", shown[rows[0]], _K + "spot")
    except Exception:
        pass
    try:
        pts = (st.session_state.get(_K + "teams") or {}).get("selection", {}).get("points", [])
        if pts:
            team = (pts[0].get("customdata") or [None])[0]
            if team in set(g["team"]):
                take("teams", team, _K + "team")
    except Exception:
        pass


# ── sections ────────────────────────────────────────────────────────────────

def _hero(g: pd.DataFrame, season: int, week: int, meta: dict) -> None:
    top = g.iloc[0]
    tc, logo, nick = _team(meta, top["team"])
    n_a = int(g["grade"].str.startswith("A").sum())
    teams = g.groupby("team")["score"].agg(["mean", "size"])
    teams = teams[teams["size"] >= 3].sort_values("mean", ascending=False)
    best = (f"Best offense <b>{_esc(teams.index[0])}</b> ({teams['mean'].iloc[0]:.0f} avg)"
            if not teams.empty else "")
    chips = [f"<b>{len(g)}</b> players graded", f"<b>{n_a}</b> A-range weeks",
             f"Top score <b>{top['score']:.1f}</b>"] + ([best] if best else [])
    st.markdown(
        "<div class='nh-hero'><div class='nh-hero-grid'>"
        "<div>"
        f"<div class='nh-kicker'>{season} season &nbsp;·&nbsp; NFL Week Hub</div>"
        f"<div class='nh-title'>Week {week}<br>Report Card</div>"
        "<div class='nh-sub'>Every QB, RB, WR and TE graded on how well he played — efficiency, "
        "production, usage and mistakes, measured against every player-week of last season.</div>"
        "<div class='nh-chips'>" + "".join(f"<span class='nh-chip'>{c}</span>" for c in chips) + "</div>"
        "</div>"
        "<div class='nh-potw'><div class='nh-potw-tag'>★ Player of the week</div>"
        f"<div class='nh-potw-row' style='--ring:{_rgba(tc, 0.85)};'>"
        f"{_img(top.get('headshot'), 'nh-potw-img')}"
        "<div style='flex:1;min-width:0;'>"
        f"<div class='nh-potw-name'>{_esc(top['player'])}</div>"
        f"<div class='nh-meta'>{_logo(meta, top['team'])}{_esc(top['position'])} · {_esc(nick)} vs {_esc(top['opponent'])}</div>"
        f"<div style='display:flex;align-items:center;gap:0.8rem;'>{_badge(top['grade'], 'lg')}"
        f"<div><div style='font-size:2rem;font-weight:800;color:#fff;line-height:1;'>{top['score']:.1f}</div>"
        "<div style='font-size:0.68rem;color:#8a91a5;'>grade score</div></div></div>"
        "</div></div>"
        f"<div class='nh-line'>{_esc(top['line'])} &nbsp;·&nbsp; {top['ppr']:.1f} PPR</div>"
        f"{_bars(top)}"
        "</div></div></div>",
        unsafe_allow_html=True)


def _leaders(view: pd.DataFrame, pos: str, meta: dict) -> None:
    if pos == "All":
        _sec("Top of the week", "the five best grades at each position")
        for col, p in zip(st.columns(4), _POS):
            with col:
                rows = view[view["position"] == p].head(5)
                st.markdown(f"<div class='nh-colhead'>{p}</div>"
                            + "".join(_card(r, i + 1, meta) for i, (_, r) in enumerate(rows.iterrows())),
                            unsafe_allow_html=True)
    else:
        _sec(f"Top {pos}s", "the twelve best grades this week")
        rows = list(view.head(12).iterrows())
        cols = st.columns(4)
        for i, (_, r) in enumerate(rows):
            with cols[i % 4]:
                st.markdown(_card(r, i + 1, meta), unsafe_allow_html=True)


def _scatter(view: pd.DataFrame) -> None:
    import plotly.graph_objects as go
    _sec("Week at a glance", "click a bubble to spotlight him · bubble size = usage")
    v = view.copy()
    v["usage_sz"] = v["usage"].astype(float).fillna(55.0).clip(0, 100)
    fig = go.Figure()
    for band in "ABCDF":
        s = v[v["grade"].str[:1] == band]
        if s.empty:
            continue
        fig.add_trace(go.Scatter(
            x=s["efficiency"], y=s["production"], mode="markers", name=f"{band} grades",
            marker=dict(size=9 + s["usage_sz"] / 100 * 22, color=_GRADE_COLOR[band], opacity=0.86,
                        line=dict(width=1, color="#0b0e14")),
            customdata=s[["player_id", "player", "team", "opponent", "grade", "score", "line"]].values,
            hovertemplate=("<b>%{customdata[1]}</b> · %{customdata[2]} v %{customdata[3]}<br>"
                           "Grade <b>%{customdata[4]}</b> · %{customdata[5]:.0f}<br>%{customdata[6]}"
                           "<extra></extra>")))
    for _, r in v.head(6).iterrows():
        fig.add_annotation(x=r["efficiency"], y=r["production"], text=str(r["player"]).split()[-1],
                           showarrow=False, yshift=15, font=dict(color="#dfe1ea", size=10))
    for x, y, t, xa, ya in ((99, 100, "Efficient + productive", "right", "top"),
                            (1, 100, "Volume over efficiency", "left", "top"),
                            (99, 1, "Efficient, little volume", "right", "bottom"),
                            (1, 1, "Quiet week", "left", "bottom")):
        fig.add_annotation(x=x, y=y, text=t, showarrow=False, xanchor=xa, yanchor=ya,
                           font=dict(color="#3d4354", size=11))
    fig.add_shape(type="line", x0=50, x1=50, y0=0, y1=100, line=dict(color="#2e3341", dash="dot"))
    fig.add_shape(type="line", x0=0, x1=100, y0=50, y1=50, line=dict(color="#2e3341", dash="dot"))
    fig.update_layout(**_CHART, height=470, margin=dict(t=30, b=40, l=44, r=10), dragmode=False,
                      hovermode="closest", clickmode="event+select",
                      legend=dict(orientation="h", y=1.07, x=0, font=dict(color="#8a91a5")),
                      xaxis=dict(**_AXIS, title="Efficiency percentile", range=[-4, 104]),
                      yaxis=dict(**_AXIS, title="Production percentile", range=[-4, 106]))
    st.plotly_chart(fig, width="stretch", config=_CFG, on_select="rerun",
                    selection_mode="points", key=_K + "scatter")


def _spotlight(g: pd.DataFrame, view: pd.DataFrame, season: int, meta: dict) -> None:
    import plotly.graph_objects as go
    _sec("Player spotlight", "pick anyone, or click the chart or table")
    order = g.sort_values("score", ascending=False)
    ids = list(order["player_id"])
    by_id = order.set_index("player_id")
    if st.session_state.get(_K + "spot") not in ids:
        st.session_state[_K + "spot"] = (view.iloc[0]["player_id"] if not view.empty else ids[0])
    pid = st.selectbox(
        "Spotlight", ids, key=_K + "spot", label_visibility="collapsed",
        format_func=lambda i: f"{by_id.at[i, 'player']} · {by_id.at[i, 'position']} {by_id.at[i, 'team']} · {by_id.at[i, 'grade']}")
    r = by_id.loc[pid]
    tc, _, nick = _team(meta, r["team"])
    same = g[g["position"] == r["position"]].sort_values("score", ascending=False).reset_index(drop=True)
    rank = int(same.index[same["player_id"] == pid][0]) + 1
    st.markdown(
        f"<div class='nh-spot' style='--tc:{tc};'><div class='nh-spot-row'>"
        f"{_img(r.get('headshot'), 'nh-spot-img')}"
        "<div style='flex:1;min-width:0;'>"
        f"<div class='nh-spot-name'>{_esc(r['player'])}</div>"
        f"<div class='nh-meta'>{_logo(meta, r['team'])}{_esc(r['position'])} · {_esc(nick)} vs {_esc(r['opponent'])}</div>"
        f"<div style='font-size:0.74rem;color:#8a91a5;'>#{rank} of {len(same)} {_esc(r['position'])}s this week</div></div>"
        f"<div style='text-align:center;'>{_badge(r['grade'], 'md')}"
        f"<div style='font-weight:800;color:#fff;margin-top:0.2rem;'>{r['score']:.1f}</div></div></div>"
        f"<div class='nh-line'>{_esc(r['line'])} &nbsp;·&nbsp; {r['ppr']:.1f} PPR</div></div>",
        unsafe_allow_html=True)

    comps = [(k, lbl) for k, lbl in _COMP if not (r["position"] == "QB" and k == "usage")]
    theta = [lbl for _, lbl in comps] + [comps[0][1]]
    mine = [float(r[k]) for k, _ in comps]
    avg = [float(same[k].mean()) for k, _ in comps]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=avg + [avg[0]], theta=theta, name=f"{r['position']} average",
                                  line=dict(color="#5c6272", dash="dot")))
    fig.add_trace(go.Scatterpolar(r=mine + [mine[0]], theta=theta, name=str(r["player"]),
                                  fill="toself", fillcolor=_rgba(tc, 0.35), line=dict(color=_rgba(tc, 1.0), width=2)))
    fig.update_layout(**_CHART, height=300, margin=dict(t=24, b=16, l=40, r=40), showlegend=True,
                      legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
                      polar=dict(bgcolor="#191c23",
                                 radialaxis=dict(range=[0, 100], gridcolor="#252a35", linecolor="#252a35",
                                                 tickfont=dict(size=9, color="#5c6272"), angle=90),
                                 angularaxis=dict(gridcolor="#252a35", linecolor="#252a35",
                                                  tickfont=dict(size=11, color="#dfe1ea"))))
    st.plotly_chart(fig, width="stretch", config=_CFG, key=_K + "radar")

    try:
        hist = _season_grades(season)
        hist = hist[hist["player_id"] == pid].sort_values("week") if not hist.empty else hist
    except Exception:
        hist = pd.DataFrame()
    if len(hist) >= 2:
        tf = go.Figure(go.Scatter(x=hist["week"], y=hist["score"], mode="lines+markers+text",
                                  text=hist["grade"], textposition="top center",
                                  line=dict(color=_rgba(tc, 1.0), width=3), marker=dict(size=9),
                                  hovertemplate="Week %{x}: %{y:.0f}<extra></extra>"))
        tf.update_layout(**_CHART, height=170, margin=dict(t=16, b=24, l=30, r=10),
                         xaxis=dict(**_AXIS, dtick=1, title=None), yaxis=dict(**_AXIS, range=[0, 108], title=None))
        st.plotly_chart(tf, width="stretch", config=_CFG, key=_K + "trend")
    else:
        st.caption("His season trend line appears here once he has two graded weeks.")


def _team_card(g: pd.DataFrame, meta: dict) -> None:
    import plotly.graph_objects as go
    _sec("Team report card", "average grade of each offense's graded players · click a team to filter the table")
    t = (g.groupby("team").agg(avg=("score", "mean"), n=("score", "size"))
           .query("n >= 3").sort_values("avg", ascending=True).tail(16))
    if t.empty:
        st.caption("Not enough graded players per team yet.")
        return
    colors = [_team(meta, tm)[0] for tm in t.index]
    fig = go.Figure(go.Bar(
        x=t["avg"], y=t.index, orientation="h", marker=dict(color=colors, line=dict(width=0)),
        text=[f"{v:.0f}" for v in t["avg"]], textposition="outside", textfont=dict(color="#dfe1ea"),
        customdata=[[tm, n] for tm, n in zip(t.index, t["n"])],
        hovertemplate="<b>%{customdata[0]}</b><br>Avg grade %{x:.1f} across %{customdata[1]} players<extra></extra>"))
    fig.update_layout(**_CHART, height=430, margin=dict(t=6, b=30, l=44, r=30), dragmode=False,
                      clickmode="event+select", showlegend=False,
                      xaxis=dict(**_AXIS, range=[0, 105], title=None), yaxis=dict(**_AXIS, title=None))
    st.plotly_chart(fig, width="stretch", config=_CFG, on_select="rerun",
                    selection_mode="points", key=_K + "teams")


def _curve(view: pd.DataFrame) -> None:
    import plotly.graph_objects as go
    _sec("Grade curve", "how the week's grades fell")
    letters = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"]
    counts = view["grade"].value_counts().reindex(letters).fillna(0).astype(int)
    fig = go.Figure(go.Bar(x=letters, y=counts.values, marker=dict(color=[_gc(l) for l in letters]),
                           text=counts.values, textposition="outside", textfont=dict(color="#dfe1ea"),
                           hovertemplate="%{x}: %{y} players<extra></extra>"))
    fig.update_layout(**_CHART, height=430, margin=dict(t=6, b=30, l=30, r=10), showlegend=False,
                      xaxis=dict(**_AXIS, title=None), yaxis=dict(**_AXIS, title=None, showgrid=False,
                                                                  showticklabels=False))
    st.plotly_chart(fig, width="stretch", config=_CFG, key=_K + "curve")


def _table(view: pd.DataFrame) -> None:
    _sec("Every graded player", "click a row to spotlight him")
    f1, f2 = st.columns([1, 1.4])
    with f1:
        teams = ["All teams"] + sorted(view["team"].unique())
        if st.session_state.get(_K + "team") not in teams:
            st.session_state[_K + "team"] = "All teams"
        team = st.selectbox("Team", teams, key=_K + "team", label_visibility="collapsed")
    with f2:
        q = st.text_input("Search", "", placeholder="Search player…", key=_K + "search",
                          label_visibility="collapsed")
    v = view
    if team != "All teams":
        v = v[v["team"] == team]
    if q:
        v = v[v["player"].str.contains(q, case=False, regex=False)]
    st.session_state[_K + "table_ids"] = list(v["player_id"])
    if v.empty:
        st.caption("No players match the current filters.")
        return
    tbl = pd.DataFrame({
        "": v["headshot"], "Grade": v["grade"], "Score": v["score"], "Player": v["player"],
        "Pos": v["position"], "Tm": v["team"], "Opp": v["opponent"], "Stat line": v["line"],
        "PPR": v["ppr"], "Eff": v["efficiency"], "Prod": v["production"], "Usage": v["usage"],
        "Clean": v["mistakes"]})
    pct = lambda label, h: st.column_config.NumberColumn(label, format="%.0f", help=h)
    st.dataframe(
        tbl, width="stretch", hide_index=True, height=min(640, 46 + 38 * len(tbl)),
        on_select="rerun", selection_mode="single-row", key=_K + "table", row_height=38,
        column_config={
            "": st.column_config.ImageColumn("", width="small"),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%.0f",
                help="Percentile of last season's player-weeks at his position"),
            "PPR": st.column_config.NumberColumn("PPR", format="%.1f"),
            "Eff": pct("Eff", "Efficiency percentile: EPA per play, plus CPOE for QBs"),
            "Prod": pct("Prod", "Production percentile: yards, a TD counted as 20"),
            "Usage": pct("Usage", "Share of the team's work (not graded for QBs)"),
            "Clean": pct("Clean", "Mistakes percentile, higher = cleaner: INTs, lost fumbles, QB sacks"),
        })


def _chip(outcome) -> str:
    cls = {"win": "nh-win", "loss": "nh-loss", "push": "nh-push", "dnp": "nh-dnp"}.get(outcome, "nh-pend")
    txt = {"win": "WIN", "loss": "LOSS", "push": "PUSH", "dnp": "DNP"}.get(outcome, "PENDING")
    return f"<span class='res {cls}'>{txt}</span>"


def _board(season: int, week: int, meta: dict) -> None:
    _sec(f"Our published board · Week {week}", "what we put out before kickoff, and how it landed")
    try:
        e, rec = _picks(season, week)
    except Exception as err:
        st.caption(f"Pick record unavailable: {err}")
        return
    ats, legs, tds = e.get("ats") or [], e.get("parlay") or [], e.get("td") or []
    if not (ats or legs or tds):
        st.caption("Nothing was published for this week.")
        return

    def wl(rows):
        o = [r.get("outcome") for r in rows]
        return o.count("win"), o.count("loss"), o.count("push"), o.count("dnp"), sum(1 for x in o if not x)

    aw, al, ap, _, apend = wl(ats)
    lw, ll, _, ld, lpend = wl(legs)
    tw, tl, _, _, tpend = wl(tds)
    texp = [float(r.get("blended_prob") or 0) for r in tds if r.get("outcome") in ("win", "loss")]
    tiles = [
        ("ATS", f"{aw}-{al}" + (f"-{ap}" if ap else ""), f"{apend} pending" if apend else "model vs the spread"),
        ("Parlay legs", f"{lw}-{ll}", f"{ld} did not play" if ld else (f"{lpend} pending" if lpend else "5-leg card")),
        ("TD scorers", f"{tw}-{tl}", f"{sum(texp) / len(texp) * 100:.0f}% expected" if texp else f"{tpend} pending"),
        ("Season ATS", rec.get("ats_record", "—"),
         f"{rec['ats_pct']:.1f}% vs 52.4% break-even" if rec.get("ats_pct") is not None else "no games yet"),
    ]
    st.markdown("<div class='nh-tiles'>" + "".join(
        f"<div class='nh-tile'><div class='l'>{_esc(l)}</div><div class='v'>{_esc(v)}</div>"
        f"<div class='s'>{_esc(s)}</div></div>" for l, v, s in tiles) + "</div>", unsafe_allow_html=True)
    st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("<div class='nh-colhead'>MODEL VS THE LINE</div>" + "".join(
            f"<div class='nh-pick'>{_logo(meta, r.get('away_team'))}<span class='sub'>@</span>{_logo(meta, r.get('home_team'))}"
            f"<div><b>{_esc(r['pick_label'])}</b><div class='sub'>{_esc(r['game'])} · edge {float(r.get('edge') or 0):.1f}</div></div>"
            f"{_chip(r.get('outcome'))}</div>" for r in ats), unsafe_allow_html=True)
    with c2:
        st.markdown("<div class='nh-colhead'>PARLAY LEGS</div>" + "".join(
            f"<div class='nh-pick'><div><b>{_esc(r['player'])}</b>"
            f"<div class='sub'>{_esc(str(r['side']).upper())} {_esc(r['line'])} {_esc(r['stat_type'])}"
            + (f" · had {float(r['actual']):g}" if r.get("actual") is not None else "") +
            f"</div></div>{_chip(r.get('outcome'))}</div>" for r in legs), unsafe_allow_html=True)
    with c3:
        st.markdown("<div class='nh-colhead'>TOUCHDOWN SCORERS</div>" + "".join(
            f"<div class='nh-pick'><div><b>{_esc(r['player'])}</b>"
            f"<div class='sub'>{float(r.get('blended_prob') or 0) * 100:.0f}% chance"
            + (f" · {float(r['actual']):g} TD" if r.get("actual") is not None else "") +
            f"</div></div>{_chip(r.get('outcome'))}</div>" for r in tds), unsafe_allow_html=True)
    st.caption("The ATS board backtests at 49.3% against a 52.4% break-even, so a weak ATS record is "
               "the expected outcome, reported as it lands. Analysis, not picks.")


def _method() -> None:
    with st.expander("How the grade works"):
        st.markdown(
            "Each player gets four component percentiles, blended with position weights:\n\n"
            "| | Efficiency | Production | Usage | Mistakes |\n|---|---|---|---|---|\n"
            "| QB | 45 | 35 | — | 20 |\n| RB | 30 | 40 | 20 | 10 |\n| WR / TE | 35 | 40 | 20 | 5 |\n\n"
            "- **Efficiency** — EPA per play (plus completion % over expected for QBs), shrunk toward "
            "zero on small samples so one long catch cannot top the week.\n"
            "- **Production** — yards, with a touchdown counted as 20 yards.\n"
            "- **Usage** — share of the team's carries and targets (WOPR for receivers).\n"
            "- **Mistakes** — interceptions and lost fumbles, plus a fifth of each sack for QBs.\n\n"
            "The score is a percentile against last season's player-weeks at the same position, so 90 "
            "means better than 90% of them, and an A means the same in Week 1 as in Week 17. Minimum work "
            "to be graded: 15 dropbacks, 6 touches, 3 targets. On 2025, a QB's grade tracked his team's "
            "wins (rank correlation 0.40) better than his fantasy points did (0.29). A grade describes the "
            "week; it is not a projection.")


# ── page ────────────────────────────────────────────────────────────────────

def render() -> None:
    import nfl_analysis as nfl
    st.markdown(_CSS, unsafe_allow_html=True)

    # Pre-season the new season has no stats yet, so fall back to the last one played.
    season = nfl.season_for_date(datetime.date.today())
    weeks = _weeks(season)
    if not weeks:
        season -= 1
        weeks = _weeks(season)
    if not weeks:
        st.info("No NFL weekly stats available yet — nflverse posts them after each week's games.")
        return

    c1, c2 = st.columns([5, 1])
    with c2:
        # Default to the latest FINISHED week. By Friday the stats already hold Thursday
        # night's game, and opening on a two-team "Week N" would crown a player of the week
        # from one game. An unfinished week is still selectable, and labelled as such.
        done = set(_completed(season))
        opts = weeks[::-1]
        default = next((i for i, w in enumerate(opts) if w in done), 0)
        week = st.selectbox("Week", opts, index=default,
                            format_func=lambda w: f"Week {w}" + ("" if w in done else " (in progress)"),
                            key=_K + "week", label_visibility="collapsed")
    with c1:
        st.caption("Grades post once nflverse publishes the week's stats, usually the morning after Monday night.")

    try:
        with st.spinner("Grading the week…"):
            g = _grades(season, week)
    except Exception as err:
        st.error(f"Couldn't grade the week: {err}")
        return
    if g is None or g.empty:
        st.info("No graded players for this week yet.")
        return
    meta = _teams()
    _sync_selections(g)

    _hero(g, season, week, meta)

    pos = st.segmented_control("Position", ["All", *_POS], default="All", key=_K + "pos",
                               label_visibility="collapsed") or "All"
    view = g if pos == "All" else g[g["position"] == pos]

    _leaders(view, pos, meta)

    left, right = st.columns([1.55, 1], gap="large")
    with left:
        _scatter(view)
    with right:
        _spotlight(g, view, season, meta)

    left, right = st.columns([1.3, 1], gap="large")
    with left:
        _team_card(g, meta)
    with right:
        _curve(view)

    _table(view)
    st.divider()
    _board(season, week, meta)
    _method()
