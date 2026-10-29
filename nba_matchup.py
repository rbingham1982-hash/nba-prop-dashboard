"""
nba_matchup.py — how each defense has played against each role: starting and bench guards,
forwards and centers. The basketball counterpart of nfl_matchup.

A role is set BEFORE the game, among the players who actually played: each team's five
with the most minutes per game in its earlier games are that night's starters (in a
player's first game of the season, his minutes from last season stand in). So when a
starter sits, the man who takes his minutes counts as a starter that night, and a role is
never decided by what happened in the game being measured. Positions are the league's
roster listing, first position primary (G-F counts as G).

Figures are averages per player-game, not team totals: a defense that faced three starting
guards would otherwise look three-halves as generous as one that faced two. Every number
carries the offenses that defense happened to face and, this early in a season, only a few
games; the last-season column is printed beside it for that reason.
"""
from __future__ import annotations

import pandas as pd

import nba_analysis as na

ROLES = ["Starter G", "Starter F", "Starter C", "Bench G", "Bench F", "Bench C"]
ROLE_STATS = [("PTS", "PTS"), ("REB", "REB"), ("AST", "AST"), ("3PM", "FG3M"), ("PRA", "PRA")]
_STARTERS = 5


def _positions(season: str) -> dict:
    """player_id -> G/F/C, from this season's roster index, then last season's."""
    import nba_snapshot as ns
    out: dict = {}
    for s in (na._prev(season), season):            # later wins
        idx = ns.frame(f"index-NBA-{s}")
        if not idx.empty:
            out.update(dict(zip(idx["PERSON_ID"].astype(int), idx["POSITION"].map(na.pos_group))))
    return out


def role_games(season: str) -> pd.DataFrame:
    """Every player-game of `season` with its pre-game role and the defense it faced."""
    df = na.get_season(season)
    if df.empty:
        return df
    df = df.sort_values(["date", "GAME_ID"]).reset_index(drop=True)
    # Minutes per game in his EARLIER games this season; his first game falls back to last
    # season's average, and a rookie's to zero (the bench, until he earns minutes).
    prior = df.groupby("PLAYER_ID")["MIN"].transform(lambda s: s.shift().expanding().mean())
    prev = na.get_season(na._prev(season))
    last = prev.groupby("PLAYER_ID")["MIN"].mean() if not prev.empty else pd.Series(dtype=float)
    df["prior_min"] = prior.fillna(df["PLAYER_ID"].map(last)).fillna(0.0)
    rank = df.groupby(["GAME_ID", "TEAM_ABBREVIATION"])["prior_min"].rank(method="first", ascending=False)
    df["starter"] = rank <= _STARTERS
    pos = _positions(season)
    df["pos"] = df["PLAYER_ID"].map(pos).fillna("F")
    df["role"] = df["starter"].map({True: "Starter", False: "Bench"}) + " " + df["pos"]
    return df[["date", "GAME_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "OPPONENT",
               "role", "pos", "starter", "MIN", "PTS", "REB", "AST", "FG3M", "PRA"]].rename(
        columns={"OPPONENT": "defense"})


def defense_vs_role(rg: pd.DataFrame, role: str) -> pd.DataFrame:
    """Per defense: games, per-player-game averages allowed to `role`, PTS/36, and ranks
    (1 = allowed the fewest, the toughest)."""
    sub = rg[rg["role"] == role]
    if sub.empty:
        return pd.DataFrame()
    g = sub.groupby("defense")
    out = pd.DataFrame({"G": g["GAME_ID"].nunique(), "MIN": g["MIN"].mean()})
    for lab, col in ROLE_STATS:
        out[lab] = g[col].mean()
    out["PTS/36"] = g["PTS"].sum() * 36.0 / g["MIN"].sum().where(g["MIN"].sum() > 0)
    for lab in [l for l, _ in ROLE_STATS] + ["PTS/36"]:
        out[lab + " rk"] = out[lab].rank(method="min").astype(int)
    return out.sort_values("PTS rk")


def league_avg(rg: pd.DataFrame, role: str) -> dict:
    sub = rg[rg["role"] == role]
    if sub.empty:
        return {}
    out = {lab: float(sub[col].mean()) for lab, col in ROLE_STATS}
    out["MIN"] = float(sub["MIN"].mean())
    out["PTS/36"] = float(sub["PTS"].sum() * 36.0 / sub["MIN"].sum()) if sub["MIN"].sum() else 0.0
    return out


def role_log(rg: pd.DataFrame, defense: str, role: str) -> pd.DataFrame:
    """Game by game: who played `role` against `defense`, newest first."""
    sub = rg[(rg["defense"] == defense) & (rg["role"] == role)].sort_values("date", ascending=False)
    return pd.DataFrame({"Date": sub["date"].dt.strftime("%b %d"), "Player": sub["PLAYER_NAME"],
                         "Team": sub["TEAM_ABBREVIATION"], "MIN": sub["MIN"], "PTS": sub["PTS"],
                         "REB": sub["REB"], "AST": sub["AST"], "3PM": sub["FG3M"]})


def player_role(rg: pd.DataFrame, player_id: int):
    """(role in his latest game, {role: games}) — None if he has no games."""
    mine = rg[rg["PLAYER_ID"] == int(player_id)].sort_values("date")
    if mine.empty:
        return None
    return mine["role"].iloc[-1], mine["role"].value_counts().to_dict()


def next_game(team: str, days: int = 10):
    """{'opponent', 'home', 'date'} for `team`'s next game from ESPN's scoreboard, or None."""
    import datetime
    import nba_espn as ne
    today = datetime.date.today()
    teams = ne._Teams("00")
    for k in range(days):
        day = today + datetime.timedelta(days=k)
        try:
            board = ne._get("00", "scoreboard", dates=day.strftime("%Y%m%d"))
        except Exception:
            return None
        for e in board.get("events", []):
            if e["status"]["type"].get("completed"):
                continue
            comp = e["competitions"][0]["competitors"]
            side = {c["homeAway"]: teams.get(c["team"])[0] for c in comp}
            if team in side.values():
                home = side.get("home") == team
                return {"opponent": side["away"] if home else side["home"], "home": home,
                        "date": day.isoformat()}
    return None
