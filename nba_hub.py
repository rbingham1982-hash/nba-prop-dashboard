"""
nba_hub.py — the NBA Hub, the landing view of the NBA tab in season.

The basketball counterpart of nfl_hub, and deliberately built from its parts: the same CSS,
badges, cards and component bars are imported from nfl_hub so the two pages read as one
product. Everything on it comes from nba_grades (every player's grade for a night, or a
seven-day roll-up of them) and, once it exists, the weekly NBA board.

Nightly by default, because the NBA plays most nights; the seven-day view averages each
player's nightly grades so a week reads as what it was rather than as one summed box score.
"""
from __future__ import annotations

import datetime

import pandas as pd
import streamlit as st

import nba_grades as ng
import nfl_hub as nh

_K = "nba_hub_"

# Primary colours, for the card stripe and the headshot ring. Hand-kept because the league
# publishes no colour feed; a missing club falls back to the accent.
_TEAM_COLOR = {
    "ATL": "#E03A3E", "BOS": "#007A33", "BKN": "#777D84", "CHA": "#1D1160", "CHI": "#CE1141",
    "CLE": "#860038", "DAL": "#00538C", "DEN": "#0E2240", "DET": "#C8102E", "GSW": "#1D428A",
    "HOU": "#CE1141", "IND": "#002D62", "LAC": "#C8102E", "LAL": "#552583", "MEM": "#5D76A9",
    "MIA": "#98002E", "MIL": "#00471B", "MIN": "#236192", "NOP": "#0C2340", "NYK": "#F58426",
    "OKC": "#007AC1", "ORL": "#0077C0", "PHI": "#006BB6", "PHX": "#E56020", "POR": "#E03A3E",
    "SAC": "#5A2D81", "SAS": "#C4CED4", "TOR": "#CE1141", "UTA": "#753BBD", "WAS": "#E31837",
}
_COMP = [("efficiency_pct", "Efficiency"), ("production_pct", "Production"),
         ("usage_pct", "Usage"), ("mistakes_pct", "Clean")]


def _headshot(pid) -> str:
    return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{int(pid)}.png"


def _color(team) -> str:
    c = _TEAM_COLOR.get(str(team), nh._ACCENT)
    # Near-black or near-white primaries vanish on the dark theme or wash out the ring.
    return nh._ACCENT if nh._lum(c) < 0.10 else c


def _bars(r) -> str:
    out = "<div class='nh-bars'>"
    for key, label in _COMP:
        v = r.get(key)
        if v is None or v != v:
            continue
        out += (f"<span>{label}</span><div class='nh-bar'><span style='width:{max(2, float(v)):.0f}%'></span></div>"
                f"<span style='text-align:right;color:#dfe1ea;'>{float(v):.0f}</span>")
    return out + "</div>"


def _card(r, rank: int) -> str:
    tc = _color(r["TEAM_ABBREVIATION"])
    meta = (f"{nh._esc(r['TEAM_ABBREVIATION'])} v {nh._esc(r.get('opponent', ''))}<br>{nh._esc(r['line'])}"
            if "line" in r else
            f"{nh._esc(r['TEAM_ABBREVIATION'])} · {int(r['games'])} games<br>"
            f"{r['pts']:.1f} pts, {r['reb']:.1f} reb, {r['ast']:.1f} ast")
    return (f"<div class='nh-card' style='--tc:{tc};'>"
            f"<span class='nh-rank'>{rank}</span>{nh._img(_headshot(r['PLAYER_ID']), 'hs')}"
            f"<div><div class='nm'>{nh._esc(r['PLAYER_NAME'])}</div><div class='mt'>{meta}</div></div>"
            f"<div class='sc'><span class='n'>{r['score']:.0f}</span>{nh._badge(r['grade'], 'sm')}</div></div>")


# ── data ────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _log(season: str):
    try:
        return ng.season_log(season)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _night(season: str, day):
    return ng.grade_night(season, day)


@st.cache_data(ttl=3600, show_spinner=False)
def _week(season: str, end):
    return ng.grade_range(season, 7, end)


def _season_to_show():
    """This season if it has games; otherwise last season, with the reason to say so."""
    import parlay_model as pm
    cur, prev = pm.nba_season_strings()
    if not _log(cur).empty:
        return cur, None
    return prev, f"The {cur} season has not tipped off yet — showing {prev}."


# ── sections ────────────────────────────────────────────────────────────────

def _hero(g: pd.DataFrame, title: str, kicker: str, tag: str) -> None:
    top = g.iloc[0]
    tc = _color(top["TEAM_ABBREVIATION"])
    n_a = int(g["grade"].str.startswith("A").sum())
    if "line" in g.columns:
        line = f"{top['line']} · {int(top['MIN'])} min · {top['WL']}"
        teams = g.groupby("TEAM_ABBREVIATION")["score"].agg(["mean", "size"])
    else:
        line = f"{top['pts']:.1f} pts, {top['reb']:.1f} reb, {top['ast']:.1f} ast over {int(top['games'])} games"
        teams = pd.DataFrame()
    chips = [f"<b>{len(g)}</b> players graded", f"<b>{n_a}</b> A-range",
             f"Top score <b>{top['score']:.1f}</b>"]
    teams = teams[teams["size"] >= 3].sort_values("mean", ascending=False) if not teams.empty else teams
    if not teams.empty:
        chips.append(f"Best team <b>{nh._esc(teams.index[0])}</b> ({teams['mean'].iloc[0]:.0f} avg)")
    st.markdown(
        "<div class='nh-hero'><div class='nh-hero-grid'><div>"
        f"<div class='nh-kicker'>{nh._esc(kicker)}</div>"
        f"<div class='nh-title'>{title}</div>"
        "<div class='nh-sub'>Every player with 12+ minutes graded on how well he played — "
        "efficiency, production, usage and mistakes, measured against every player-game of last season.</div>"
        "<div class='nh-chips'>" + "".join(f"<span class='nh-chip'>{c}</span>" for c in chips) + "</div></div>"
        f"<div class='nh-potw'><div class='nh-potw-tag'>★ {nh._esc(tag)}</div>"
        f"<div class='nh-potw-row' style='--ring:{nh._rgba(tc, 0.85)};'>"
        f"{nh._img(_headshot(top['PLAYER_ID']), 'nh-potw-img')}"
        "<div style='flex:1;min-width:0;'>"
        f"<div class='nh-potw-name'>{nh._esc(top['PLAYER_NAME'])}</div>"
        f"<div class='nh-meta'>{nh._esc(top['TEAM_ABBREVIATION'])}"
        + (f" vs {nh._esc(top['opponent'])}" if "opponent" in g.columns else "") + "</div>"
        f"<div style='display:flex;align-items:center;gap:0.8rem;'>{nh._badge(top['grade'], 'lg')}"
        f"<div><div style='font-size:2rem;font-weight:800;color:#fff;line-height:1;'>{top['score']:.1f}</div>"
        "<div style='font-size:0.68rem;color:#8a91a5;'>grade score</div></div></div></div></div>"
        f"<div class='nh-line'>{nh._esc(line)}</div>"
        + (_bars(top) if "efficiency_pct" in g.columns else "") +
        "</div></div></div>",
        unsafe_allow_html=True)


def _leaders(g: pd.DataFrame, sub: str) -> None:
    nh._sec("Top performers", sub)
    rows = list(g.head(12).iterrows())
    per_col = -(-len(rows) // 4)
    for c, col in enumerate(st.columns(4)):
        with col:
            chunk = rows[c * per_col:(c + 1) * per_col]
            st.markdown("".join(_card(r, c * per_col + j + 1) for j, (_, r) in enumerate(chunk)),
                        unsafe_allow_html=True)


def _teams(g: pd.DataFrame) -> None:
    import plotly.graph_objects as go
    t = (g.groupby("TEAM_ABBREVIATION")["score"].agg(["mean", "size"])
           .query("size >= 3").sort_values("mean"))
    if t.empty:
        return
    nh._sec("Team report card", "average grade of each team's graded players")
    fig = go.Figure(go.Bar(x=t["mean"], y=t.index, orientation="h",
                           marker_color=[_color(x) for x in t.index],
                           text=[f"{v:.0f}" for v in t["mean"]], textposition="outside",
                           hovertemplate="%{y}: %{x:.1f} avg over %{customdata} players<extra></extra>",
                           customdata=t["size"]))
    fig.update_layout(**nh._CHART, height=max(260, 22 * len(t) + 60), margin=dict(l=10, r=30, t=10, b=10),
                      xaxis=dict(range=[0, 100], **nh._AXIS), yaxis=dict(**nh._AXIS))
    st.plotly_chart(fig, width="stretch", config=nh._CFG, key=_K + "teams")


def _table(g: pd.DataFrame, nightly: bool) -> None:
    nh._sec("Every graded player", "sortable · filter by team")
    teams = ["All teams"] + sorted(g["TEAM_ABBREVIATION"].unique())
    team = st.selectbox("Team", teams, key=_K + "team", label_visibility="collapsed")
    v = g if team == "All teams" else g[g["TEAM_ABBREVIATION"] == team]
    if nightly:
        cols = {"PLAYER_NAME": "Player", "TEAM_ABBREVIATION": "Team", "opponent": "Opp", "MIN": "Min",
                "PTS": "Pts", "REB": "Reb", "AST": "Ast", "STL": "Stl", "BLK": "Blk", "TOV": "TO",
                "ts_pct": "TS%", "score": "Score", "grade": "Grade", "efficiency_pct": "Eff",
                "production_pct": "Prod", "usage_pct": "Usg", "mistakes_pct": "Clean"}
    else:
        cols = {"PLAYER_NAME": "Player", "TEAM_ABBREVIATION": "Team", "games": "G", "pts": "Pts",
                "reb": "Reb", "ast": "Ast", "best": "Best", "score": "Score", "grade": "Grade"}
    out = v[list(cols)].rename(columns=cols)
    fmt = {"TS%": st.column_config.NumberColumn(format="percent"),
           "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")}
    for c in ("Pts", "Reb", "Ast"):
        if not nightly:
            fmt[c] = st.column_config.NumberColumn(format="%.1f")
    st.dataframe(out, hide_index=True, width="stretch", height=420, column_config=fmt)


@st.cache_data(ttl=3600, show_spinner=False)
def _tips(year: int):
    import nba_tips
    return nba_tips.team_table(year)


def _tipoff(season: str) -> None:
    """Every team's opening-tip record, with the man jumping for it now and his own record."""
    year = int(season[:4]) + 1          # ESPN names a season for the year it ends
    try:
        t = _tips(year)
    except Exception:
        t = pd.DataFrame()
    nh._sec("Tip-off record", f"{season} · who wins the opening tip, and who is jumping")
    if t.empty:
        st.caption("No tip-off log for this season yet — it fills as games are played.")
        return
    st.dataframe(
        t.drop(columns=["games"]), hide_index=True, width="stretch", height=380,
        column_config={
            "Win %": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
            "Jumper %": st.column_config.NumberColumn(format="percent"),
        })
    st.caption("A team's rate mostly describes whoever its starting centre was. The jumper's own "
               "record follows him across teams, so read the two together — a new jumper resets "
               "the team number more than its history suggests.")


def _board() -> None:
    nh._sec("This week's board", "logged before tip-off, graded after")
    try:
        import nba_weekly_picks as nwp
        nwp.render_hub_section()
    except Exception:
        st.caption("The weekly NBA board starts with the 2026-27 season: a break-even-gated props "
                   "parlay and a scorer board, written down before tip-off and graded here after.")


def _method() -> None:
    with st.expander("How the grade works"):
        st.markdown(
            "- **Efficiency** — points added over league-average true shooting on his own attempts, "
            "shrunk on low volume so a 2-for-2 cameo cannot top the night.\n"
            "- **Production** — points + 1.2×rebounds + 1.5×assists + 3×(steals + blocks).\n"
            "- **Usage** — the share of his team's possessions he finished while on the floor.\n"
            "- **Clean** — turnovers, plus 0.3 of each foul (fewer is better).\n\n"
            "Each is a z-score against every player-game of **last season** (12+ minutes), blended "
            "30/45/15/10, and expressed as a percentile of last season's blends: a 90 is better than 90% "
            "of last season's games. The 7-day view averages a player's nightly grades. This describes "
            "the game; whether he repeats it is what the props scorer is for.")


def render() -> None:
    st.markdown(nh._CSS, unsafe_allow_html=True)
    season, note = _season_to_show()
    log = _log(season)
    if log.empty:
        st.info("NBA game logs are unavailable right now — nba_api did not respond. Try again shortly.")
        return
    if note:
        st.caption(note)

    dates = sorted(log["date"].dt.date.unique(), reverse=True)
    c1, c2 = st.columns([1.2, 1])
    with c1:
        mode = st.radio("View", ["Last night", "Last 7 days"], horizontal=True,
                        key=_K + "mode", label_visibility="collapsed")
    with c2:
        day = st.selectbox("Date", dates, index=0, key=_K + "day", label_visibility="collapsed",
                           format_func=lambda d: d.strftime("%a %b %d, %Y"))
    nightly = mode == "Last night"
    if nightly:
        g = _night(season, day)
        title = f"{day.strftime('%b %d')}<br>Report Card"
        tag, sub = "Player of the night", "the twelve best grades of the night"
    else:
        g = _week(season, day)
        min_games = 2 if (g["games"] >= 2).sum() >= 12 else 1
        g = g[g["games"] >= min_games].reset_index(drop=True)
        title = f"Week to {day.strftime('%b %d')}<br>Report Card"
        tag, sub = "Player of the week", f"mean nightly grade, {min_games}+ games"
    if g.empty:
        st.info("No graded games on that date.")
        return

    _hero(g, title, f"{season} season · NBA Hub", tag)
    _leaders(g, sub)
    left, right = st.columns([1, 1.25])
    with left:
        _teams(g)
    with right:
        _table(g, nightly)
    _tipoff(season)
    _board()
    _method()
