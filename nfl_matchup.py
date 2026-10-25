"""
nfl_matchup.py — how each defense has played against each offensive role: RB1, WR3, TE1.

"The Jets allow 106 rushing yards a game" says nothing about whether a team's RB2 will eat.
This splits every defense's season by the role of the player it faced, so a player's
matchup can be read against his own job.

ROLE IN A GAME is decided before it, among the players who actually played: first by work
per game in the team's earlier games (carries + targets for a back, targets for a receiver,
dropbacks for a quarterback), then by the depth chart as it stood before kickoff (nflverse's
daily ESPN snapshots, the ones nfl_analysis.depth_context uses), then by work that day.
When an RB1 sits, the next man up is that game's RB1, which is the job the defense faced.

Not the depth chart alone: ESPN's chart lags. In 2026 Week 4 it still listed Jaylen Wright
as Miami's RB1 while Ollie Gordon II had 20 carries to his 3 over the two games before. And
not work in the game itself, which would make "RB1" mean "whoever got the ball" and credit
every big day to the role after the fact.

The samples are a few games per defense per role, and a defense's numbers carry the
offenses it happened to face. The page prints the number of games beside every figure.
"""
from __future__ import annotations

import pandas as pd

ROLES = {"QB": 1, "RB": 2, "WR": 3, "TE": 2}       # how deep each position is tracked

# (label, column or ratio, higher-is-better-for-the-offense). Per game unless a ratio.
ROLE_STATS = {
    "QB": [("PPR", "fantasy_points_ppr"), ("Pass yds", "passing_yards"), ("Pass TD", "passing_tds"),
           ("INT", "passing_interceptions"), ("Sacks", "sacks_suffered"), ("Rush yds", "rushing_yards")],
    "RB": [("PPR", "fantasy_points_ppr"), ("Carries", "carries"), ("Rush yds", "rushing_yards"),
           ("Rush TD", "rushing_tds"), ("Targets", "targets"), ("Rec yds", "receiving_yards")],
    "WR": [("PPR", "fantasy_points_ppr"), ("Targets", "targets"), ("Rec", "receptions"),
           ("Rec yds", "receiving_yards"), ("Rec TD", "receiving_tds")],
    "TE": [("PPR", "fantasy_points_ppr"), ("Targets", "targets"), ("Rec", "receptions"),
           ("Rec yds", "receiving_yards"), ("Rec TD", "receiving_tds")],
}
_LOWER_IS_BETTER = {"INT", "Sacks"}     # for the offense; every other stat, more is better
_COLS = sorted({c for v in ROLE_STATS.values() for _, c in v}
               | {"rushing_epa", "receiving_epa", "passing_epa", "attempts", "completions"})


def role_games(season: int) -> pd.DataFrame:
    """One row per skill player per game, with his role in that game (RB1, WR3, ...)."""
    import nfl_analysis as nfl
    import nfl_game_model as gm
    wk = nfl._load_weekly(season)
    wk = wk[wk["position"].isin(list(ROLES))].copy()
    wk["player_id"] = wk["player_id"].astype(str)
    depth = nfl._depth_frame(season)
    snaps = {t: g for t, g in depth.groupby("team")}
    sch = gm.schedule()
    sch = sch[sch["season"] == season]
    kickoff = {}
    for w, day, h, a in zip(sch["week"], sch["gameday"], sch["home_team"], sch["away_team"]):
        kickoff[(h, int(w))] = kickoff[(a, int(w))] = pd.Timestamp(day, tz="UTC")
    # Work per game played BEFORE each week, for the same team (a trade starts him over).
    work = {"QB": wk["attempts"].fillna(0) + wk["sacks_suffered"].fillna(0),
            "RB": wk["carries"].fillna(0) + wk["targets"].fillna(0)}
    wk["_work"] = work["QB"].where(wk["position"] == "QB",
                                   work["RB"].where(wk["position"] == "RB", wk["targets"].fillna(0)))
    wk = wk.sort_values("week")
    grp = wk.groupby(["player_id", "team"])["_work"]
    wk["_prior"] = (grp.cumsum() - wk["_work"]) / grp.cumcount().replace(0, pd.NA)
    wk["_prior"] = pd.to_numeric(wk["_prior"], errors="coerce").fillna(-1)
    out = []
    for (team, week, pos), g in wk.groupby(["team", "week", "position"]):
        ko = kickoff.get((team, int(week)))
        snap = snaps.get(team)
        listed = {}
        if ko is not None and snap is not None:
            before = snap[(snap["dt"] < ko) & (snap["pos_abb"] == pos)]
            if not before.empty:
                last = before[before["dt"] == before["dt"].max()]
                listed = dict(zip(last["gsis_id"], last["pos_rank"]))
        g = g.assign(_listed=g["player_id"].map(listed).fillna(99))
        g = g.sort_values(["_prior", "_listed", "_work"], ascending=[False, True, False])
        for i, (_, r) in enumerate(g.iterrows(), 1):
            if i > ROLES[pos]:
                break
            row = {"season": season, "week": int(week), "team": team, "opponent": r["opponent_team"],
                   "player_id": r["player_id"], "player": r["player_display_name"], "position": pos,
                   "role": f"{pos}{i}", "listed": None if r["_listed"] == 99 else int(r["_listed"])}
            row.update({c: float(r.get(c) or 0) for c in _COLS})
            out.append(row)
    return pd.DataFrame(out)


def defense_vs_role(rg: pd.DataFrame, role: str) -> pd.DataFrame:
    """Every defense against one role: games, per-game averages, and a rank per stat.

    Rank 1 is the toughest defense: the fewest allowed, or for INT and sacks the most."""
    pos = role.rstrip("0123456789")
    d = rg[rg["role"] == role]
    if d.empty:
        return pd.DataFrame()
    stats = ROLE_STATS[pos]
    agg = d.groupby("opponent").agg(G=("week", "nunique"), **{lab: (col, "mean") for lab, col in stats})
    for lab, _ in stats:
        agg[f"{lab} rk"] = agg[lab].rank(ascending=lab not in _LOWER_IS_BETTER, method="min").astype(int)
    return agg.sort_values("PPR rk")


def league_avg(rg: pd.DataFrame, role: str) -> dict:
    pos = role.rstrip("0123456789")
    d = rg[rg["role"] == role]
    return {lab: float(d[col].mean()) for lab, col in ROLE_STATS[pos]} if not d.empty else {}


def position_summary(rg: pd.DataFrame, pos: str) -> pd.DataFrame:
    """A defense against a whole position group (every tracked role combined), per game,
    with the efficiency numbers a per-role split is too thin for. Rank 1 = toughest."""
    d = rg[rg["position"] == pos]
    if d.empty:
        return pd.DataFrame()
    g = d.groupby(["opponent", "week"]).sum(numeric_only=True).reset_index()
    t = g.groupby("opponent").agg(G=("week", "nunique"), **{c: (c, "sum") for c in _COLS})
    out = pd.DataFrame(index=t.index)
    out["G"] = t["G"]
    if pos == "RB":
        out["Rush yds/g"] = t["rushing_yards"] / t["G"]
        out["Yds/carry"] = t["rushing_yards"] / t["carries"]
        out["EPA/carry"] = t["rushing_epa"] / t["carries"]
        out["Rush TD/g"] = t["rushing_tds"] / t["G"]
        out["Rec yds/g"] = t["receiving_yards"] / t["G"]
    elif pos == "QB":
        out["Pass yds/g"] = t["passing_yards"] / t["G"]
        out["EPA/dropback"] = t["passing_epa"] / (t["attempts"] + t["sacks_suffered"])
        out["Pass TD/g"] = t["passing_tds"] / t["G"]
        out["INT/g"] = t["passing_interceptions"] / t["G"]
        out["Sacks/g"] = t["sacks_suffered"] / t["G"]
    else:
        out["Targets/g"] = t["targets"] / t["G"]
        out["Rec yds/g"] = t["receiving_yards"] / t["G"]
        out["Yds/target"] = t["receiving_yards"] / t["targets"]
        out["Catch %"] = 100 * t["receptions"] / t["targets"]
        out["EPA/target"] = t["receiving_epa"] / t["targets"]
        out["Rec TD/g"] = t["receiving_tds"] / t["G"]
    for c in [c for c in out.columns if c != "G"]:
        out[f"{c} rk"] = out[c].rank(ascending=c not in ("INT/g", "Sacks/g"), method="min").astype(int)
    return out


def player_role(season: int, player_id) -> tuple | None:
    """(position, rank on the newest depth chart, team) for a player, or None if unlisted."""
    import nfl_analysis as nfl
    d = nfl._depth_frame(season)
    d = d[d["gsis_id"] == str(player_id)]
    if d.empty:
        return None
    d = d[d["dt"] == d["dt"].max()].sort_values("pos_rank").iloc[0]
    return d["pos_abb"], int(d["pos_rank"]), d["team"]


def next_game(season: int, team: str) -> dict | None:
    """The team's next unplayed game: {'week', 'opponent', 'home', 'gameday'}, or None."""
    import nfl_game_model as gm
    s = gm.schedule()
    s = s[(s["season"] == season) & s["home_score"].isna()
          & ((s["home_team"] == team) | (s["away_team"] == team))].sort_values("gameday")
    if s.empty:
        return None
    r = s.iloc[0]
    home = r["home_team"] == team
    return {"week": int(r["week"]), "opponent": r["away_team"] if home else r["home_team"],
            "home": bool(home), "gameday": str(r["gameday"])}


def role_log(rg: pd.DataFrame, defense: str, role: str) -> pd.DataFrame:
    """Who filled `role` against `defense` each week, and what he did."""
    pos = role.rstrip("0123456789")
    d = rg[(rg["opponent"] == defense) & (rg["role"] == role)].sort_values("week")
    cols = ["week", "team", "player"] + [c for _, c in ROLE_STATS[pos]]
    out = d[cols].rename(columns={"week": "Wk", "team": "Offense", "player": "Player",
                                  **{c: lab for lab, c in ROLE_STATS[pos]}})
    return out.reset_index(drop=True)
