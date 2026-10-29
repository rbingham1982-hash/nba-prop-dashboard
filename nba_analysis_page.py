"""
nba_analysis_page.py — the NBA Player Analysis page, laid out like the NFL one: season,
team, player and stat; the game log against any line; best and worst opponents; how the
next defense has played his role; and the projection's own record, game by game.

Built in October with a few games per team, on purpose: the samples are small, so every
figure carries its game count, the season it comes from, and last season beside it.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import nba_analysis as na
import nba_matchup as nm

_K = "nba_an_"


@st.cache_data(ttl=3600, show_spinner=False)
def _season(season: str):
    return na.get_season(season)


@st.cache_data(ttl=86400, show_spinner=False)
def _seasons():
    return na.seasons_with_data()


@st.cache_data(ttl=3600, show_spinner=False)
def _roster():
    return na.roster()


@st.cache_data(ttl=3600, show_spinner=False)
def _roles(season: str):
    return nm.role_games(season)


@st.cache_data(ttl=1800, show_spinner=False)
def _next(team: str):
    try:
        return nm.next_game(team)
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _grades(season: str, pid: int):
    try:
        return na.player_grades(season, pid)
    except Exception:
        return {}


def _header() -> None:
    st.markdown(
        "<div style='margin:0.2rem 0 0.9rem;'>"
        "<div style='font-size:0.62rem;font-weight:800;letter-spacing:0.18em;text-transform:uppercase;color:#818cf8;'>"
        "NBA Player Analysis &nbsp;·&nbsp; Game Logs · Prop Hit-Rates · Matchups · Projections</div>"
        "<div style='font-size:0.8rem;color:var(--text-muted);margin-top:0.2rem;'>"
        "Game-by-game production, over/under hit rates at any line, how tonight's defense has played "
        "his role, and a recency-weighted next-game projection with its own record."
        "</div></div>",
        unsafe_allow_html=True)


def render() -> None:
    _header()
    try:
        opts = _seasons() or [na.seasons()[0]]
    except Exception:
        opts = [na.seasons()[0]]
    cur, prev = na.seasons()
    season = st.radio("Season", opts, horizontal=True, key=_K + "season", label_visibility="collapsed")
    with st.spinner("Loading the season's game logs…"):
        df = _season(season)
    rost = _roster()
    if df.empty or rost.empty:
        st.info("NBA game logs or rosters are unavailable right now — try again shortly.")
        return

    ngames = df.groupby("TEAM_ABBREVIATION")["GAME_ID"].nunique()
    st.caption(
        f"Season {season} · {df['GAME_ID'].nunique()} games · {len(df):,} regular-season player-games "
        f"({int(ngames.min())}-{int(ngames.max())} per team). Rosters are today's, so a player appears "
        "under the team he plays for now; the game log's Team column says where each game was played."
        + (f"  Early-season samples are small — read them beside {prev}, which is one click away."
           if season == cur and ngames.median() < 15 else ""))

    q1, q2, q3 = st.columns([1, 1.5, 1.4])
    with q1:
        team = st.selectbox("Team", na.teams_in(rost), key=_K + "team")
    plist = na.players_on_team(df, rost, team)
    with q2:
        labels = [f"{n}  ·  {p}" for n, _, p in plist]
        sel = st.selectbox("Player", labels, key=_K + "player") if labels else None
    if sel is None:
        st.caption("No players listed for this team.")
        return
    player, pid, pos = plist[labels.index(sel)]
    with q3:
        stat = st.selectbox("Stat", list(na.STATS), key=_K + "stat")

    log = na.game_log(df, pid)
    proj = na.project(log, stat)
    default = (int(proj) + 0.5) if proj else 0.5     # a half-point line, so it can't push
    line = st.number_input(f"{stat} line (over/under)", value=float(max(default, 0.5)), step=0.5,
                           key=f"{_K}line_{pid}_{stat}_{season}")
    hr = na.hit_rate(log, stat, line)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Games", hr["n"])
    m2.metric(f"Avg · {season}", hr["avg"] if hr["avg"] is not None else "—")
    m3.metric("Next-Game Proj", proj if proj is not None else "—")
    m4.metric(f"Over {line:g}", f"{hr['hit_rate'] * 100:.0f}%" if hr["hit_rate"] is not None else "—",
              f"{hr['over']}/{hr['n']} games" if hr["n"] else None, delta_color="off")

    if not log:
        st.info(f"{player} has no {season} regular-season games. "
                + (f"Switch to {prev} for last season's log." if season == cur else ""))
        return

    _chart(log, stat, line)
    _tables(log, stat, season)
    st.markdown("---")
    _matchup(season, team, player, pid, pos)
    st.markdown("---")
    _record(log, stat, season, pid)


def _chart(log: list, stat: str, line: float) -> None:
    import plotly.graph_objects as go
    x = [f"{g['date'].strftime('%b %d')} {'v' if g['home'] else '@'} {g['opp']}" for g in log]
    vals = [g[stat] for g in log]
    fig = go.Figure(go.Bar(x=x, y=vals, marker_color=["#22c55e" if v > line else "#f87171" for v in vals],
                           hovertemplate="%{x}<br>" + stat + ": %{y}<extra></extra>"))
    fig.add_hline(y=line, line_dash="dash", line_color="#818cf8",
                  annotation_text=f"line {line:g}", annotation_position="top left")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(gridcolor="#20242e", type="category"),
                      yaxis=dict(title=stat, gridcolor="#20242e"),
                      font=dict(color="#9294a8", size=12), showlegend=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=_K + "bars")


def _tables(log: list, stat: str, season: str) -> None:
    lc, rc = st.columns([1.5, 1])
    with lc:
        st.markdown("**Game log**")
        others = [s for s in ("Points", "Rebounds", "Assists", "3-PT Made", "Minutes") if s != stat][:4]
        gl = pd.DataFrame([{"Date": g["date"].strftime("%b %d"), "Team": g["team"],
                            "Opp": ("vs " if g["home"] else "@ ") + g["opp"], "W/L": g["wl"],
                            stat: g[stat], **{s: g[s] for s in others}} for g in reversed(log)])
        st.dataframe(gl, width="stretch", hide_index=True, height=min(430, 40 + 34 * len(gl)))
    with rc:
        st.markdown("**Best / worst matchups**")
        spl = na.opponent_splits(log, stat)
        top, bot = spl[:4], (spl[-3:] if len(spl) > 4 else [])
        # Nullable dtypes through the "…" gap: an empty string there made the columns
        # object-typed and st.dataframe raise ArrowInvalid (the NFL tab's lesson).
        sd = pd.DataFrame([{"Opp": r["opp"], "Avg": r["avg"], "G": r["games"]} for r in top]
                          + ([{"Opp": "…", "Avg": None, "G": None}] if bot else [])
                          + [{"Opp": r["opp"], "Avg": r["avg"], "G": r["games"]} for r in bot]
                          ).astype({"Avg": "Float64", "G": "Int64"})
        st.dataframe(sd, width="stretch", hide_index=True)
        st.caption(f"Per-game average of the selected stat by opponent ({season} only).")
    st.caption("Projection is a recency-weighted mean (last 5 games ×2), the NFL tab's rule. "
               "Live prop pricing is in the Edge Finder tab.")


def _matchup(season: str, team: str, player: str, pid: int, pos: str) -> None:
    st.markdown("**🛡️ Matchup: the defense against his role**")
    prev = na._prev(season)
    try:
        with st.spinner("Splitting every defense by the role it faced…"):
            rg = _roles(season)
            rg_prev = _roles(prev)
    except Exception as e:
        st.caption(f"Matchup data unavailable right now ({e}).")
        return
    if rg.empty:
        st.caption("No games in this season yet.")
        return
    mine = nm.player_role(rg, pid)
    role0 = mine[0] if mine else f"Bench {pos}"
    nx = _next(team) if season == na.seasons()[0] else None
    defs = sorted(rg["defense"].dropna().unique())
    def0 = nx["opponent"] if nx and nx["opponent"] in defs else defs[0]
    c1, c2 = st.columns(2)
    role = c1.selectbox("Role", nm.ROLES, index=nm.ROLES.index(role0), key=f"{_K}role_{pid}")
    dfn = c2.selectbox("Defense", defs, index=defs.index(def0), key=f"{_K}def_{pid}")
    ctx = []
    if mine:
        counts = ", ".join(f"{r} {n}" for r, n in sorted(mine[1].items(), key=lambda kv: -kv[1]))
        ctx.append(f"{player} played as **{mine[0]}** in his latest {season} game ({counts})")
    else:
        ctx.append(f"{player} has no {season} games, so his role is a guess from his listed position")
    if nx:
        ctx.append(f"next: {'vs' if nx['home'] else '@'} {nx['opponent']} ({nx['date']})")
    st.caption("; ".join(ctx) + ".")

    dvr, lg = nm.defense_vs_role(rg, role), nm.league_avg(rg, role)
    dvr_p, lg_p = (nm.defense_vs_role(rg_prev, role), nm.league_avg(rg_prev, role)) if not rg_prev.empty else (pd.DataFrame(), {})
    labs = [l for l, _ in nm.ROLE_STATS] + ["PTS/36"]
    if dfn in dvr.index:
        r = dvr.loc[dfn]
        st.markdown(f"**{dfn} against {role}s · {season}** · {int(r['G'])} games, "
                    f"{len(dvr)} defenses ranked")
        cols = st.columns(len(labs))
        for col, lab in zip(cols, labs):
            col.metric(f"{lab} · #{int(r[lab + ' rk'])}", f"{r[lab]:.1f}", f"{r[lab] - lg[lab]:+.1f} vs avg")
    if dfn in dvr_p.index:
        r = dvr_p.loc[dfn]
        bits = " · ".join(f"{lab} {r[lab]:.1f} (#{int(r[lab + ' rk'])}, {r[lab] - lg_p[lab]:+.1f})" for lab in labs)
        st.caption(f"**{prev}, for comparison** ({int(r['G'])} games): {bits}.")

    ml, mr = st.columns([1.2, 1])
    with ml:
        st.markdown(f"**Game by game: who played {role} against {dfn}**")
        rl = nm.role_log(rg, dfn, role)
        if rl.empty:
            st.caption("No games at this role yet.")
        else:
            st.dataframe(rl, width="stretch", hide_index=True, height=min(320, 40 + 34 * len(rl)))
    with mr:
        st.markdown(f"**Every defense against {role}s**")
        if not dvr.empty:
            tab = dvr[["G", "MIN"] + [l for l, _ in nm.ROLE_STATS] + ["PTS rk"]].copy()
            if not dvr_p.empty:
                tab[f"PTS {prev}"] = dvr_p["PTS"].reindex(tab.index)
            tab = tab.round(1).rename(columns={"PTS rk": "Rank"}).reset_index().rename(columns={"defense": "Defense"})
            st.dataframe(tab, width="stretch", hide_index=True, height=320)
    st.caption(
        "Averages are per player-game at that role. Ranks run from 1, the defense that allowed the "
        "fewest; arrows compare with the league average for the role, so green is better for the "
        "player. **Role in a game** is set before it: each team's five with the most minutes per game "
        "in its earlier games are that night's starters (a player's first game uses last season's "
        "minutes), so when a starter sits, the player who takes his minutes counts as a starter. "
        "Positions are the league's listing, first position primary. This early, each defense is a "
        "handful of games and carries the offenses it happened to face; the game-by-game table shows "
        "who they were, and last season sits beside it for that reason.")


def _record(log: list, stat: str, season: str, pid: int) -> None:
    st.markdown("**Projection vs reality, game by game**")
    grades = _grades(season, pid)
    rows, beat, dec, errs = [], 0, 0, []
    for i, g in enumerate(log):
        prior = log[:i]                      # only earlier games: a record, not a retrofit
        pj = na.project(prior, stat) if prior else None
        act = g[stat]
        gr = grades.get(g["game_id"])
        if pj is not None:
            dec += 1
            errs.append(abs(act - pj))
            beat += act > pj
        rows.append({"Date": g["date"].strftime("%b %d"), "Opp": g["opp"], stat: act,
                     "Proj": "—" if pj is None else f"{pj:.1f}",
                     "Δ": "—" if pj is None else f"{act - pj:+.1f}",
                     "Beat": "—" if pj is None else ("✓" if act > pj else "✗"),
                     "Grade": gr[0] if gr else "—", "Score": f"{gr[1]:.0f}" if gr else "—"})
    b1, b2, b3 = st.columns(3)
    b1.metric("Beat projection", f"{beat}/{dec}" if dec else "—",
              f"{beat / dec * 100:.0f}%" if dec else None, delta_color="off")
    b2.metric("Avg miss", f"{sum(errs) / len(errs):.1f}" if errs else "—",
              f"over {len(errs)} games" if errs else None, delta_color="off")
    b3.metric("Graded games", len(grades) or "—",
              ("A-range: %d" % sum(1 for v in grades.values() if v[0].startswith("A"))) if grades else None,
              delta_color="off")
    st.dataframe(pd.DataFrame(rows[::-1]), width="stretch", hide_index=True,
                 height=min(430, 40 + 34 * len(rows)))
    st.caption("**Proj** is what this page would have said going into that game, built only from "
               "earlier games, so the first game is blank. **Grade** is how he played, from the Hub's "
               "grade (efficiency, production, usage, mistakes against every player-game of the season "
               "before) — a separate question from whether he beat the projection.")
