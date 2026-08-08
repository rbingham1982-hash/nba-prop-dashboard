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


def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    import math
    if sigma <= 0:
        return 1.0 if x < mu else 0.0
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _implied_from_odds(american) -> float:
    a = float(american)
    return (100 / (a + 100)) if a > 0 else (abs(a) / (abs(a) + 100))


def score_prop(df, player: str, stat: str, line: float, american_odds=None,
               market_blend: float = 0.35, recent_n: int = 5) -> dict:
    """
    Model probability that `player` goes OVER `line` on `stat`, from a normal(projection,
    historical σ) — the projection is forward-looking (recency-weighted), σ carries the
    player's game-to-game variance. When market odds are given, blend toward the book's
    implied prob (market_blend = model's share) and report the edge — mirroring how the
    MLB/WNBA scorer trusts the market as the primary signal and the model as an edge nudge.

    Returns {} if the player has too little history to score.
    """
    import statistics as _st
    log = game_log(df, player)
    vals = [g[stat] for g in log]
    if len(vals) < 3:
        return {}
    mu = project(log, stat, recent_n=recent_n)
    sigma = _st.pstdev(vals) if len(vals) > 1 else max(mu * 0.5, 1.0)
    model_over = round(1 - _norm_cdf(line, mu, sigma), 4)

    out = {"player": player, "stat": stat, "line": float(line), "n": len(vals),
           "projection": mu, "sigma": round(sigma, 1),
           "model_over": model_over, "hit_rate_hist": round(sum(1 for v in vals if v > line) / len(vals), 3)}
    if american_odds is not None:
        implied = _implied_from_odds(american_odds)
        blended = round(market_blend * model_over + (1 - market_blend) * implied, 4)
        out.update({"american_odds": int(american_odds), "implied": round(implied, 4),
                    "blended_over": blended, "edge": round(blended - implied, 4),
                    "model_edge": round(model_over - implied, 4)})
    return out


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
