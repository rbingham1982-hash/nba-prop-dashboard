"""
NFL player analysis — the ANALYZE foundation (game logs, prop hit-rates, opponent splits,
next-game projections). This is also the projection layer the future NFL betting pipeline
will score props against.

Data: nflverse weekly player stats parquet, read directly (nfl_data_py won't install on this
py3.14 venv — see nfl_fantasy_rankings.py for the same pattern).
"""
from __future__ import annotations
import datetime

_WEEKLY = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{y}.parquet"

# Bettable stat -> (weekly column, display label). The markets a props pipeline will price.
PROP_STATS = {
    "Passing Yards":   ("passing_yards", "Pass Yds"),
    "Passing TDs":     ("passing_tds", "Pass TD"),
    "Completions":     ("completions", "Comp"),
    "Rushing Yards":   ("rushing_yards", "Rush Yds"),
    "Rushing TDs":     ("rushing_tds", "Rush TD"),
    "Carries":         ("carries", "Carries"),
    "Receptions":      ("receptions", "Rec"),
    "Receiving Yards": ("receiving_yards", "Rec Yds"),
    "Receiving TDs":   ("receiving_tds", "Rec TD"),
    "Fantasy (PPR)":   ("fantasy_points_ppr", "PPR"),
}
# Which stats matter by position (so the UI doesn't offer Passing Yards for a WR).
POS_STATS = {
    "QB": ["Passing Yards", "Passing TDs", "Completions", "Rushing Yards", "Fantasy (PPR)"],
    "RB": ["Rushing Yards", "Rushing TDs", "Carries", "Receptions", "Receiving Yards", "Fantasy (PPR)"],
    "WR": ["Receptions", "Receiving Yards", "Receiving TDs", "Rushing Yards", "Fantasy (PPR)"],
    "TE": ["Receptions", "Receiving Yards", "Receiving TDs", "Fantasy (PPR)"],
}


def _load_weekly(season: int):
    import pandas as pd
    df = pd.read_parquet(_WEEKLY.format(y=season))
    return df[df["season_type"] == "REG"].copy()


def latest_season_with_data(guess: int | None = None) -> int:
    """Most recent season that has weekly data (current season before Week 1 falls back a year)."""
    import pandas as pd
    y = guess or datetime.datetime.now().year
    for cand in (y, y - 1):
        try:
            pd.read_parquet(_WEEKLY.format(y=cand))
            return cand
        except Exception:
            continue
    return y - 1


def get_season(season: int | None = None):
    """Cached weekly frame for a season (resolved to the latest available if None)."""
    s = season or latest_season_with_data()
    return s, _load_weekly(s)


def teams_in(df) -> list:
    return sorted(t for t in df["team"].dropna().unique())


def players_on_team(df, team: str) -> list:
    """Fantasy-relevant players on a team, most-used first (by games played)."""
    sub = df[(df["team"] == team) & (df["position"].isin(["QB", "RB", "WR", "TE"]))]
    order = (sub.groupby(["player_display_name", "position"])
                .size().reset_index(name="g").sort_values("g", ascending=False))
    return [(r["player_display_name"], r["position"]) for _, r in order.iterrows()]


def game_log(df, player: str) -> list:
    """Week-by-week rows for a player, oldest→newest."""
    sub = df[df["player_display_name"] == player].sort_values("week")
    out = []
    for _, r in sub.iterrows():
        row = {"week": int(r["week"]), "opp": r.get("opponent_team", ""), "team": r.get("team", "")}
        for label, (col, _) in PROP_STATS.items():
            row[label] = float(r.get(col, 0) or 0)
        out.append(row)
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
    """
    Next-game projection: recency-weighted mean (last recent_n games weighted 2×). Simple and
    honest — a real matchup/pace model is a later layer; this is the baseline hit-rates use.
    """
    vals = [g[stat] for g in log]
    if not vals:
        return None
    recent = vals[-recent_n:]
    w_recent, w_all = 2.0, 1.0
    num = w_recent * (sum(recent) / len(recent)) + w_all * (sum(vals) / len(vals))
    return round(num / (w_recent + w_all), 1)


def opponent_splits(df, player: str, stat: str) -> list:
    """Player's per-game average of a stat, grouped by opponent (min 1 game)."""
    col = PROP_STATS[stat][0]
    sub = df[df["player_display_name"] == player]
    rows = []
    for opp, g in sub.groupby("opponent_team"):
        rows.append({"opp": opp, "games": len(g), "avg": round(float(g[col].mean()), 1)})
    return sorted(rows, key=lambda r: r["avg"], reverse=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    s, df = get_season()
    print(f"season {s} — {len(df)} REG player-weeks, {df['team'].nunique()} teams")
    log = game_log(df, "Puka Nacua")
    print(f"\nPuka Nacua game log ({len(log)} games):")
    for g in log[:6]:
        print(f"  Wk{g['week']:>2} vs {g['opp']:<3}  {g['Receptions']:.0f} rec, {g['Receiving Yards']:.0f} yds")
    hr = hit_rate(log, "Receiving Yards", 79.5)
    print(f"\nRec Yds o79.5: {hr['over']}/{hr['n']} = {hr['hit_rate']*100:.0f}% | avg {hr['avg']} | proj {project(log,'Receiving Yards')}")
