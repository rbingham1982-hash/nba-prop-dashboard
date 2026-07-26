"""
Season-long 9-category fantasy value rankings (draft board).

v1 ranks on the most recent completed season's per-game production (the standard
baseline). Value = sum of per-category z-scores over the fantasy-relevant pool,
the Basketball-Monster-style approach: counting cats are plain z-scores, turnovers
are negative, and percentage cats (FG%, FT%) are volume-weighted so a high-usage
efficient scorer is rewarded and a low-volume "good percentage" is not.

Projection layer (minutes/role/aging for the upcoming season) is a deliberate v2 —
this module is the foundation it will plug into.
"""
from __future__ import annotations
import time

CATS = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "FG_PCT", "FT_PCT", "TOV"]
# Display order and whether higher is better (TOV is the only negative cat).
NEG_CATS = {"TOV"}
PCT_CATS = {"FG_PCT", "FT_PCT"}


def _fetch_stats(season: str):
    from nba_api.stats.endpoints import leaguedashplayerstats as L
    time.sleep(0.3)
    return L.LeagueDashPlayerStats(
        season=season, per_mode_detailed="PerGame",
        season_type_all_star="Regular Season", timeout=25,
    ).get_data_frames()[0]


def _fetch_positions(season: str) -> dict:
    """player_id -> position (e.g. 'G', 'F', 'C'); empty dict on any failure."""
    try:
        from nba_api.stats.endpoints import playerindex
        time.sleep(0.3)
        df = playerindex.PlayerIndex(season=season, timeout=25).get_data_frames()[0]
        pid = "PERSON_ID" if "PERSON_ID" in df.columns else "PLAYER_ID"
        return {int(r[pid]): str(r.get("POSITION", "") or "") for _, r in df.iterrows()}
    except Exception:
        return {}


def get_9cat_rankings(season: str = "2025-26", min_gp: int = 25, min_mpg: float = 18.0,
                      pool_size: int = 180) -> dict:
    """
    Returns {"season", "n_pool", "players": [ {rank, player_id, name, team, pos, gp, mpg,
    stat line, per-cat z's, total, tier}, ... ] } sorted by total value desc.
    """
    import pandas as pd  # noqa
    df = _fetch_stats(season)
    df = df[(df["GP"] >= min_gp) & (df["MIN"] >= min_mpg)].copy()
    # Fantasy-relevant pool: cap to the top `pool_size` by minutes so deep-bench
    # players don't distort the category means/σ that everyone is scored against.
    df = df.sort_values("MIN", ascending=False).head(pool_size).reset_index(drop=True)
    n = len(df)
    if n == 0:
        return {"season": season, "n_pool": 0, "players": []}

    # League percentage baselines are volume-weighted (total makes / total attempts),
    # not a naive mean of per-player percentages.
    lg_fg = df["FGM"].sum() / df["FGA"].sum() if df["FGA"].sum() else 0.0
    lg_ft = df["FTM"].sum() / df["FTA"].sum() if df["FTA"].sum() else 0.0
    # Volume-weighted percentage *impact*, then z-scored like any other cat.
    df["FG_IMPACT"] = (df["FG_PCT"] - lg_fg) * df["FGA"]
    df["FT_IMPACT"] = (df["FT_PCT"] - lg_ft) * df["FTA"]

    zsrc = {
        "PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK",
        "FG3M": "FG3M", "TOV": "TOV", "FG_PCT": "FG_IMPACT", "FT_PCT": "FT_IMPACT",
    }
    z = {}
    for cat, col in zsrc.items():
        mean = df[col].mean()
        std = df[col].std(ddof=0) or 1.0
        zc = (df[col] - mean) / std
        if cat in NEG_CATS:
            zc = -zc  # fewer turnovers = more value
        z[cat] = zc

    df["TOTAL"] = sum(z[c] for c in CATS)
    for c in CATS:
        df[f"z{c}"] = z[c]
    df = df.sort_values("TOTAL", ascending=False).reset_index(drop=True)

    # Tiers from natural gaps in the sorted total (a gap ≥ 0.6 z starts a new tier),
    # capped so tiers stay readable.
    tiers, tier = [], 1
    totals = df["TOTAL"].tolist()
    for i, t in enumerate(totals):
        if i > 0 and (totals[i-1] - t) >= 0.6 and tier < 10:
            tier += 1
        tiers.append(tier)

    positions = _fetch_positions(season)
    players = []
    for i, r in df.iterrows():
        players.append({
            "rank": i + 1, "tier": tiers[i],
            "player_id": int(r["PLAYER_ID"]), "name": r["PLAYER_NAME"],
            "team": r["TEAM_ABBREVIATION"], "pos": positions.get(int(r["PLAYER_ID"]), ""),
            "gp": int(r["GP"]), "mpg": round(float(r["MIN"]), 1),
            "pts": round(float(r["PTS"]), 1), "reb": round(float(r["REB"]), 1),
            "ast": round(float(r["AST"]), 1), "stl": round(float(r["STL"]), 1),
            "blk": round(float(r["BLK"]), 1), "fg3m": round(float(r["FG3M"]), 1),
            "fg_pct": round(float(r["FG_PCT"]), 3), "ft_pct": round(float(r["FT_PCT"]), 3),
            "tov": round(float(r["TOV"]), 1),
            "total": round(float(r["TOTAL"]), 2),
            **{f"z{c}": round(float(r[f"z{c}"]), 2) for c in CATS},
        })
    return {"season": season, "n_pool": n, "players": players}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    out = get_9cat_rankings()
    print(f"season {out['season']} — pool {out['n_pool']}")
    print(f"{'#':>3} {'T':>2} {'player':<24}{'pos':>4}{'tm':>4}{'tot':>6}  PTS/REB/AST  STL/BLK/3PM")
    for p in out["players"][:20]:
        print(f"{p['rank']:>3} {p['tier']:>2} {p['name']:<24}{p['pos']:>4}{p['team']:>4}{p['total']:>6.2f}"
              f"  {p['pts']:>4}/{p['reb']:>4}/{p['ast']:>4}  {p['stl']:>3}/{p['blk']:>3}/{p['fg3m']:>3}")
