"""
opportunity.py — role changes: who just got more (or less) of his team's work.

Konjure's projections start from opportunity, so a jump in a player's share of the work is the
earliest thing the model sees and the slowest thing the waiver wire prices. These are the
Konjure Pro opportunity alerts.

NFL, per completed week, for RBs, WRs and TEs: offensive snap share (nflverse snap counts),
target share and carry share (nflverse weekly stats; carry share is the player's carries over
his team's). Each is compared with the player's average over his previous three games. A
riser gained at least one of: 15 points of snap share, 8 points of target share, 15 points
of carry share. A faller lost as much. Ranked by the change in his share of team
opportunities (targets plus carries).

NBA, from the saved league log: minutes over the last three games against the player's
season average before them, for players with at least five earlier games. A riser gained 5+
minutes a game.

These are descriptions of what already happened, not predictions. A role that jumped for one
game because a starter sat can fall straight back.
"""
from __future__ import annotations

import unicodedata

import pandas as pd

_NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
_SNAP, _TGT, _CAR = 15.0, 8.0, 15.0          # percentage-point thresholds
_NBA_MIN = 5.0
_PRIOR = 3


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "").replace(",", "").replace("-", " ")
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return " ".join(s.split())


def nfl_usage(season: int) -> pd.DataFrame:
    """One row per skill player per regular-season week: snap %, target share, carry share."""
    st = pd.read_parquet(f"{_NFLVERSE}/stats_player/stats_player_week_{season}.parquet")
    st = st[(st["season_type"] == "REG") & st["position"].isin(["RB", "WR", "TE"])].copy()
    team_car = st.groupby(["team", "week"])["carries"].transform("sum")
    st["carry_share"] = (st["carries"] / team_car.where(team_car > 0)).fillna(0.0) * 100
    st["target_share"] = st["target_share"].fillna(0.0) * 100
    team_opp = st.groupby(["team", "week"])[["targets", "carries"]].transform("sum").sum(axis=1)
    st["opp_share"] = ((st["targets"] + st["carries"]) / team_opp.where(team_opp > 0)).fillna(0.0) * 100
    st["key"] = st["player_display_name"].map(_norm)
    try:
        sn = pd.read_parquet(f"{_NFLVERSE}/snap_counts/snap_counts_{season}.parquet")
        sn = sn[sn["game_type"] == "REG"].copy()
        sn["key"] = sn["player"].map(_norm)
        sn["snap_pct"] = sn["offense_pct"].astype(float) * 100
        st = st.merge(sn[["key", "team", "week", "snap_pct"]].drop_duplicates(["key", "team", "week"]),
                      on=["key", "team", "week"], how="left")
    except Exception:
        st["snap_pct"] = float("nan")             # shares still work without snap counts
    return st[["player_display_name", "position", "team", "opponent_team", "week", "snap_pct",
               "target_share", "carry_share", "opp_share", "targets", "carries"]].rename(
        columns={"player_display_name": "player", "opponent_team": "opponent"})


def nfl_changes(season: int, week: int, usage: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every RB/WR/TE who played `week`, with his change against his previous three games."""
    u = usage if usage is not None else nfl_usage(season)
    now = u[u["week"] == week]
    before = u[(u["week"] < week) & (u["week"] >= week - 6)].sort_values("week")
    base = (before.groupby(["player", "team"]).tail(_PRIOR)
                  .groupby(["player", "team"])[["snap_pct", "target_share", "carry_share", "opp_share"]]
                  .mean().add_suffix("_before"))
    d = now.join(base, on=["player", "team"], how="inner")
    for c in ("snap_pct", "target_share", "carry_share", "opp_share"):
        d[f"{c}_chg"] = d[c] - d[f"{c}_before"]
    return d


def nfl_alerts(season: int, week: int, usage: pd.DataFrame | None = None) -> dict:
    """{'risers': DataFrame, 'fallers': DataFrame} for one completed week."""
    d = nfl_changes(season, week, usage)
    up = ((d["snap_pct_chg"] >= _SNAP) | (d["target_share_chg"] >= _TGT) | (d["carry_share_chg"] >= _CAR))
    down = ((d["snap_pct_chg"] <= -_SNAP) | (d["target_share_chg"] <= -_TGT) | (d["carry_share_chg"] <= -_CAR))
    cols = ["player", "position", "team", "opponent", "snap_pct", "snap_pct_chg", "target_share",
            "target_share_chg", "carry_share", "carry_share_chg", "opp_share_chg"]
    return {"risers": d[up].sort_values("opp_share_chg", ascending=False)[cols].round(1),
            "fallers": d[down].sort_values("opp_share_chg")[cols].round(1)}


def nba_alerts(season: str | None = None, recent: int = 3, min_prior: int = 5) -> dict:
    """Minutes risers and fallers from the current NBA season's saved league log."""
    import datetime
    import nba_grades as ng
    import parlay_model as pm
    season = season or pm.nba_season_strings(datetime.date.today())[0]
    try:
        log = ng.season_log(season)
    except Exception:
        return {"risers": pd.DataFrame(), "fallers": pd.DataFrame(), "season": season}
    rows = []
    for (pid, name), g in log.sort_values("date").groupby(["PLAYER_ID", "PLAYER_NAME"]):
        if len(g) < recent + min_prior:
            continue
        last, prior = g.tail(recent), g.iloc[:-recent]
        rows.append({"player": name, "team": last["TEAM_ABBREVIATION"].iloc[-1],
                     "min_recent": last["MIN"].mean(), "min_before": prior["MIN"].mean(),
                     "pts_recent": last["PTS"].mean(), "games": len(g),
                     "last_game": last["date"].iloc[-1].date()})
    d = pd.DataFrame(rows)
    if d.empty:
        return {"risers": d, "fallers": d, "season": season}
    d["min_chg"] = d["min_recent"] - d["min_before"]
    d = d.round(1)
    return {"risers": d[d["min_chg"] >= _NBA_MIN].sort_values("min_chg", ascending=False),
            "fallers": d[d["min_chg"] <= -_NBA_MIN].sort_values("min_chg"), "season": season}
