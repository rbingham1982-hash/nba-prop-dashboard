"""
nba_grades.py — a nightly performance grade for every NBA player with real minutes.

The basketball counterpart of nfl_grades, built the same way: one number, 0-100, for how
well a player played THAT NIGHT, with a letter on top, out of four components:

  efficiency  points added over league-average true shooting on his own attempts,
              shrunk on small volume so a 2-for-2 cameo cannot top the night.
  production  a box-score composite: points, rebounds, assists, steals, blocks.
  usage       usage rate — the share of his team's possessions he finished while on the
              floor, from the team's totals in the same game.
  mistakes    turnovers, plus a fraction of each foul.

Each component is a z-score against a REFERENCE season's player-games, the components are
blended, and the blend is expressed as a percentile of that reference season's blends. A
90 means "better than 90% of last season's player-games". That means the same thing on
opening night as in April, and a night nobody played well does not force someone to an A+.

Graded across all players rather than by position. NBA positions are fluid enough that
splitting would grade a stretch big against centres one night and forwards the next, and
the components already describe what a player did, not what his listed position expects.

This is a description of the game, not a projection. Whether he does it again is what the
props scorer is for.
"""
from __future__ import annotations

import bisect

# Minutes to be graded at all. Below this a game is a cameo, and a percentile of a cameo
# is noise wearing a letter.
_MIN_MINUTES = 12.0

# Pseudo true-shooting attempts at league average, added before measuring efficiency —
# the same shrinkage idea as nfl_grades' pseudo-plays.
_TS_SHRINK = 6.0

_FOUL_WEIGHT = 0.3   # a foul is partly the job (a big contesting at the rim), so a fraction

_WEIGHTS = {"efficiency": 0.30, "production": 0.45, "usage": 0.15, "mistakes": 0.10}
_COMPONENTS = ("efficiency", "production", "usage", "mistakes")

# Same letter scale as nfl_grades, so an A means the same thing on both pages.
_LETTERS = [(97, "A+"), (90, "A"), (85, "A-"), (80, "B+"), (70, "B"), (65, "B-"),
            (60, "C+"), (45, "C"), (40, "C-"), (20, "D"), (0, "F")]

_REF: dict = {}
_LOGS: dict = {}
_LOG_TTL = 3600


def season_log(season: str, season_type: str = "Regular Season"):
    """Every player-game of a season, oldest first, cached for an hour."""
    import time
    import pandas as pd
    key = (season, season_type)
    hit = _LOGS.get(key)
    if hit and time.time() - hit[0] < _LOG_TTL:
        return hit[1]
    from nba_api.stats.endpoints import leaguegamelog
    df = leaguegamelog.LeagueGameLog(season=season, player_or_team_abbreviation="P",
                                     season_type_all_star=season_type,
                                     timeout=60).get_data_frames()[0]
    df["date"] = pd.to_datetime(df["GAME_DATE"])
    df["MIN"] = pd.to_numeric(df["MIN"], errors="coerce").fillna(0.0)
    df = df.sort_values(["date", "GAME_ID"]).reset_index(drop=True)
    _LOGS[key] = (time.time(), df)
    return df


def letter(score) -> str:
    if score is None or score != score:
        return "—"
    for floor, grade in _LETTERS:
        if score >= floor:
            return grade
    return "F"


def components(df):
    """Raw component values for every player-game with real minutes, before any scaling."""
    d = df.copy()
    tsa = d["FGA"] + 0.44 * d["FTA"]
    team = (d.assign(_tsa=tsa)
              .groupby(["GAME_ID", "TEAM_ID"])[["MIN", "FGA", "FTA", "TOV", "_tsa", "PTS"]].sum()
              .rename(columns=lambda c: "team_" + c.strip("_")))
    d = d.join(team, on=["GAME_ID", "TEAM_ID"])
    d = d[d["MIN"] >= _MIN_MINUTES].copy()
    tsa = d["FGA"] + 0.44 * d["FTA"]

    # League true-shooting from the frame itself, so the baseline is the season being
    # graded's own scoring environment rather than a constant.
    lg_ts = float(df["PTS"].sum() / (2.0 * (df["FGA"] + 0.44 * df["FTA"]).sum()))
    d["efficiency"] = (d["PTS"] - 2.0 * lg_ts * tsa) * tsa / (tsa + _TS_SHRINK)
    d["production"] = d["PTS"] + 1.2 * d["REB"] + 1.5 * d["AST"] + 3.0 * d["STL"] + 3.0 * d["BLK"]
    poss = d["FGA"] + 0.44 * d["FTA"] + d["TOV"]
    team_poss = d["team_FGA"] + 0.44 * d["team_FTA"] + d["team_TOV"]
    d["usage"] = (poss * (d["team_MIN"] / 5.0)) / (d["MIN"] * team_poss).where(team_poss > 0)
    d["mistakes"] = -(d["TOV"] + _FOUL_WEIGHT * d["PF"])
    d["ts_pct"] = (d["PTS"] / (2.0 * tsa)).where(tsa > 0)
    return d


def _reference(season: str) -> dict:
    """Component means/sds and the sorted blend distribution for the reference season."""
    if season in _REF:
        return _REF[season]
    c = components(season_log(season))
    ref = {k: (float(c[k].mean()), float(c[k].std()) or 1.0) for k in _COMPONENTS}
    blend = sum(_WEIGHTS[k] * (c[k] - ref[k][0]) / ref[k][1] for k in _COMPONENTS)
    ref["_blends"] = sorted(float(x) for x in blend.dropna())
    _REF[season] = ref
    return ref


def _prev_season(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


def grade_games(df, ref_season: str):
    """Grade the player-games in `df` (a slice of a season log) against `ref_season`."""
    import pandas as pd
    ref = _reference(ref_season)
    c = components(df)
    if c.empty:
        return c
    blend = sum(_WEIGHTS[k] * (c[k] - ref[k][0]) / ref[k][1] for k in _COMPONENTS)
    blends = ref["_blends"]
    n = len(blends)
    c["score"] = [round(100.0 * bisect.bisect_left(blends, float(b)) / n, 1) if b == b else None
                  for b in blend]
    c["grade"] = c["score"].map(letter)
    # Component percentiles, for the "why" columns on the hub.
    for k in _COMPONENTS:
        z = (c[k] - ref[k][0]) / ref[k][1]
        c[f"{k}_pct"] = (100 * pd.Series(z).rank(pct=True)).round(0)
    c["opponent"] = c["MATCHUP"].str.extract(r"(?:@|vs\.)\s+(\w+)")[0]
    c["line"] = (c["PTS"].astype(int).astype(str) + " pts, " + c["REB"].astype(int).astype(str)
                 + " reb, " + c["AST"].astype(int).astype(str) + " ast"
                 + c["ts_pct"].map(lambda t: f" · {t:.0%} TS" if t == t else ""))
    keep = ["date", "PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "opponent", "WL", "MIN",
            "PTS", "REB", "AST", "STL", "BLK", "TOV", "PLUS_MINUS", "ts_pct", "line",
            "score", "grade", *[f"{k}_pct" for k in _COMPONENTS]]
    return c[keep].sort_values("score", ascending=False).reset_index(drop=True)


def grade_night(season: str, day=None):
    """Every graded player on one date (default: the latest date in the log)."""
    import pandas as pd
    log = season_log(season)
    if log.empty:
        return log
    day = pd.Timestamp(day) if day is not None else log["date"].max()
    return grade_games(log[log["date"] == day], _prev_season(season))


def grade_range(season: str, days: int = 7, end=None):
    """
    The roll-up: each player's mean nightly score over the last `days` days, with games
    played. A mean of nightly grades rather than a grade of summed stats, so a 40-point
    night and a 10-point night average to what they were rather than to one big line.
    """
    import pandas as pd
    log = season_log(season)
    if log.empty:
        return log
    end = pd.Timestamp(end) if end is not None else log["date"].max()
    window = log[(log["date"] > end - pd.Timedelta(days=days)) & (log["date"] <= end)]
    g = grade_games(window, _prev_season(season))
    if g.empty:
        return g
    agg = (g.groupby(["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION"])
             .agg(games=("score", "size"), score=("score", "mean"), pts=("PTS", "mean"),
                  reb=("REB", "mean"), ast=("AST", "mean"), best=("score", "max"))
             .reset_index())
    agg["score"] = agg["score"].round(1)
    agg["grade"] = agg["score"].map(letter)
    return agg.sort_values(["score", "games"], ascending=False).reset_index(drop=True)
