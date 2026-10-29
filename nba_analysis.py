"""
nba_analysis.py — the NBA Player Analysis page's data: one season's player-games, today's
rosters, a player's game log, hit rates at any line, a recency projection and opponent splits.

The basketball counterpart of nfl_analysis, shaped the same way so the page reads the same:
a season is chosen, then a team, a player and a stat. Rosters are always this season's
(the league's own roster index), so a player appears under the team he plays for now even
when the season being read is last year's — the game log's own Team column says where
each game was played.

Regular season only, from nba_grades.season_log (the saved ESPN-built logs on the cloud,
stats.nba.com at home).
"""
from __future__ import annotations

import pandas as pd

# Display name -> column. The combos are the books' own names, so a line read off
# FanDuel can be typed in against the matching column.
STATS = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3-PT Made": "FG3M",
         "Pts+Rebs+Asts": "PRA", "Pts+Rebs": "PR", "Pts+Asts": "PA", "Rebs+Asts": "RA",
         "Steals": "STL", "Blocks": "BLK", "Turnovers": "TOV", "Minutes": "MIN"}
POS_NAMES = {"G": "Guard", "F": "Forward", "C": "Center"}


def seasons(today=None) -> tuple[str, str]:
    import parlay_model as pm
    return pm.nba_season_strings(today)


def _prev(season: str) -> str:
    y = int(season[:4]) - 1
    return f"{y}-{str(y + 1)[-2:]}"


def get_season(season: str) -> pd.DataFrame:
    """Every regular-season player-game of `season` with the combo columns, oldest first."""
    import nba_grades as ng
    try:
        df = ng.season_log(season).copy()
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    df["PR"] = df["PTS"] + df["REB"]
    df["PA"] = df["PTS"] + df["AST"]
    df["RA"] = df["REB"] + df["AST"]
    opp = df["MATCHUP"].str.extract(r"@ (\w+)|vs\. (\w+)")
    df["OPPONENT"] = opp[0].fillna(opp[1])
    df["IS_HOME"] = df["MATCHUP"].str.contains(r"vs\.", na=False)
    df["PLAYER_ID"] = df["PLAYER_ID"].astype(int)
    return df


def seasons_with_data() -> list:
    """This season first, then the two before it, keeping only those with games saved."""
    cur, prev = seasons()
    out = []
    for s in (cur, prev, _prev(prev)):
        try:
            if not get_season(s).empty:
                out.append(s)
        except Exception:
            pass
    return out


def pos_group(pos) -> str:
    """'G-F' -> 'G', 'F-C' -> 'F', 'C' -> 'C'; the first listed position is the primary."""
    p = str(pos or "").strip().upper()
    return p[0] if p and p[0] in POS_NAMES else "F"


def roster(season: str | None = None) -> pd.DataFrame:
    """Today's rosters: player_id, player, team, pos (G/F/C), from the league's roster index."""
    import nba_snapshot as ns
    cur, prev = seasons()
    for s in ([season] if season else []) + [cur, prev]:
        idx = ns.frame(f"index-NBA-{s}")
        if not idx.empty:
            break
    if idx.empty:
        return pd.DataFrame(columns=["player_id", "player", "team", "pos"])
    idx = idx[idx["TEAM_ABBREVIATION"].notna() & (idx["ROSTER_STATUS"].fillna(0) > 0)]
    return pd.DataFrame({
        "player_id": idx["PERSON_ID"].astype(int),
        "player": (idx["PLAYER_FIRST_NAME"].fillna("") + " " + idx["PLAYER_LAST_NAME"].fillna("")).str.strip(),
        "team": idx["TEAM_ABBREVIATION"].astype(str),
        "pos": idx["POSITION"].map(pos_group),
        "position": idx["POSITION"].fillna("").astype(str)})


def teams_in(rost: pd.DataFrame) -> list:
    return sorted(rost["team"].unique())


def players_on_team(df: pd.DataFrame, rost: pd.DataFrame, team: str) -> list:
    """[(name, player_id, pos)] on `team` today, most minutes per game in `df` first;
    players with no games in the season read come last, alphabetically."""
    r = rost[rost["team"] == team]
    mpg = df.groupby("PLAYER_ID")["MIN"].mean() if not df.empty else pd.Series(dtype=float)
    r = r.assign(mpg=r["player_id"].map(mpg).fillna(-1.0))
    r = r.sort_values(["mpg", "player"], ascending=[False, True])
    return [(n, int(i), p) for n, i, p in zip(r["player"], r["player_id"], r["pos"])]


def game_log(df: pd.DataFrame, player_id: int) -> list:
    """One player's games in `df`, oldest first, as dicts keyed by display stat name."""
    sub = df[df["PLAYER_ID"] == int(player_id)].sort_values(["date", "GAME_ID"])
    out = []
    for _, r in sub.iterrows():
        g = {"date": r["date"], "opp": r["OPPONENT"], "home": bool(r["IS_HOME"]),
             "team": r["TEAM_ABBREVIATION"], "wl": r.get("WL", ""), "game_id": str(r["GAME_ID"])}
        for name, col in STATS.items():
            g[name] = float(r[col])
        out.append(g)
    return out


def hit_rate(log: list, stat: str, line: float) -> dict:
    """Over-hit rate for a prop line across the game log, plus the per-game values."""
    vals = [g[stat] for g in log]
    n = len(vals)
    if n == 0:
        return {"n": 0, "hit_rate": None, "avg": None, "over": 0, "values": []}
    over = sum(1 for v in vals if v > line)
    return {"n": n, "hit_rate": round(over / n, 3), "avg": round(sum(vals) / n, 1),
            "over": over, "values": vals}


def project(log: list, stat: str, recent_n: int = 5) -> float | None:
    """Recency-weighted mean (last recent_n games weighted 2x), nfl_analysis.project's rule."""
    vals = [g[stat] for g in log]
    if not vals:
        return None
    recent = vals[-recent_n:]
    num = 2.0 * (sum(recent) / len(recent)) + 1.0 * (sum(vals) / len(vals))
    return round(num / 3.0, 1)


def opponent_splits(log: list, stat: str) -> list:
    """Per-game average of a stat by opponent (min 1 game), best first."""
    by: dict = {}
    for g in log:
        by.setdefault(g["opp"], []).append(g[stat])
    rows = [{"opp": o, "games": len(v), "avg": round(sum(v) / len(v), 1)} for o, v in by.items()]
    return sorted(rows, key=lambda r: r["avg"], reverse=True)


def player_grades(season: str, player_id: int) -> dict:
    """{GAME_ID: (grade, score)} for his games, from nba_grades against the season before."""
    import nba_grades as ng
    log = ng.season_log(season)
    games = set(log.loc[log["PLAYER_ID"].astype(int) == int(player_id), "GAME_ID"])
    if not games:
        return {}
    g = ng.grade_games(log[log["GAME_ID"].isin(games)], _prev(season))
    g = g[g["PLAYER_ID"].astype(int) == int(player_id)]
    ids = log.loc[log["PLAYER_ID"].astype(int) == int(player_id)].set_index("date")["GAME_ID"]
    return {str(ids.get(d, "")): (gr, float(sc)) for d, gr, sc in zip(g["date"], g["grade"], g["score"])}
