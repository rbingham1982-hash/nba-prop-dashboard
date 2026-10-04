"""
nba_eval.py — walk-forward harness for the NBA props scorer.

The NBA counterpart of stat_eval / ats_eval / fantasy_eval, and the thing every NBA model
change is measured on before it ships. It runs the LIVE scorer, parlay_model._nba_hit_rate,
unmodified: the module's data hooks are pointed at a league-wide game log cut off the day
before each game, so the scorer sees exactly what it would have seen that morning.

What it scores is the model half of the price. _nba_hit_rate returns
0.7 x model + 0.3 x implied; called with implied 0.5 and no calibration, the model term is
recovered exactly as (rate - 0.15) / 0.7. The market blend and calibration that sit on top
are separate questions with their own measurements in parlay_tracker.

Lines. There is no history of NBA lines outside our own logs, so each prop is scored at
the half point nearest the player's average over his last 20 games (both seasons) — where a
book hangs a line, give or take. Every method is scored at the same line on the same rows.

What it found first (2026-10-02). The scorer switched to this season after a player's
FIRST game, so opening weeks were priced off one to four games. Brier of P(over), the live
scorer against the same scorer with last season's regular season in front:

                          2025-26                    2024-25
    games this season   live    combined          live    combined
    0 (last season)     0.2409  0.2409            0.2370  0.2370
    1-4                 0.3031  0.2391            0.3109  0.2386     <- worse than a coin flip
    5-14                0.2467  0.2409            0.2460  0.2405     p<0.001 both
    15+                 0.2438  0.2437            0.2433  0.2431

Combined became the live rule (parlay_model._nba_hit_rate), so the harness no longer
carries it as a separate arm. Also worth keeping: once a player is established the scorer
beats a normal around his last-ten mean (0.2438 vs 0.2499, 0.2433 vs 0.2488), so the
minutes model is earning its place.

    python nba_eval.py            # 2025-26, 2024-25 as the prior season
"""
from __future__ import annotations

import sys

STATS = ["Points", "Rebounds", "Assists", "3-PT Made", "Pts+Rebs+Asts"]
_COMBO = {"PRA": ("PTS", "REB", "AST")}
# Prop-relevant rows only: a player averaging under this many minutes over his last ten
# games is not who books post lines on, and his zero-heavy logs would dominate the sample.
MIN_RECENT_MINUTES = 20.0
MIN_PRIOR_GAMES = 5


def season_log(season: str):
    """Every regular-season player-game of a season, oldest first, in PlayerGameLog shape."""
    import pandas as pd
    from nba_api.stats.endpoints import leaguegamelog
    df = leaguegamelog.LeagueGameLog(season=season, player_or_team_abbreviation="P",
                                     season_type_all_star="Regular Season",
                                     timeout=60).get_data_frames()[0]
    df["SEASON"] = season
    df["SEASON_TYPE"] = "Regular Season"
    df["_date"] = pd.to_datetime(df["GAME_DATE"])
    # PlayerGameLog's date format, which parlay_model's sorter parses.
    df["GAME_DATE"] = df["_date"].dt.strftime("%b %d, %Y")
    df["MIN"] = pd.to_numeric(df["MIN"], errors="coerce").fillna(0.0)
    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    return df.sort_values(["_date", "GAME_ID"]).reset_index(drop=True)


class _Feed:
    """Serves the scorer's data hooks from preloaded logs, cut off before `self.cutoff`."""

    def __init__(self, cur, prev, cur_s, prev_s):
        self.by = {cur_s: {p: g for p, g in cur.groupby("PLAYER_ID")},
                   prev_s: {p: g for p, g in prev.groupby("PLAYER_ID")}}
        self.cur_s, self.prev_s = cur_s, prev_s
        self.cutoff = None

    def gamelogs(self, pid, seasons):
        import pandas as pd
        out = []
        for s in seasons:
            g = self.by.get(s, {}).get(pid)
            if g is not None:
                out.append(g[g["_date"] < self.cutoff] if s == self.cur_s else g)
        df = pd.concat(out) if out else pd.DataFrame()
        return df.drop(columns=[c for c in ("_date",) if c in df.columns]).reset_index(drop=True)


def build(season: str = "2025-26", prev: str = "2024-25", verbose: bool = True,
          every_nth_day: int = 1):
    import numpy as np
    import pandas as pd
    import parlay_model as pm

    cur, old = season_log(season), season_log(prev)
    names = dict(zip(pd.concat([cur, old])["PLAYER_NAME"], pd.concat([cur, old])["PLAYER_ID"]))
    feeds = {"live": _Feed(cur, old, season, prev)}

    saved = (pm.get_gamelogs, pm.get_player_id, pm.nba_season_strings)
    pm.get_player_id = lambda n: names.get(n)
    pm.nba_season_strings = lambda today=None: (season, prev)

    hist_all = pd.concat([old, cur]).sort_values(["_date", "GAME_ID"])
    by_pid = {p: g for p, g in hist_all.groupby("PLAYER_ID")}
    rows = []
    try:
        days = sorted(cur["_date"].unique())[::every_nth_day]
        for i, day in enumerate(days):
            games = cur[(cur["_date"] == day) & (cur["MIN"] > 0)]
            for f in feeds.values():
                f.cutoff = day
            for r in games.itertuples():
                past = by_pid[r.PLAYER_ID]
                past = past[past["_date"] < day]
                if len(past) < MIN_PRIOR_GAMES or past["MIN"].tail(10).mean() < MIN_RECENT_MINUTES:
                    continue
                n_cur = int((past["SEASON"] == season).sum())
                for stat in STATS:
                    col = pm.NBA_STAT_COL[stat]
                    hist20 = past[col].tail(20)
                    line = float(np.floor(hist20.mean()) + 0.5)
                    actual = float(getattr(r, col))
                    row = {"date": day, "player": r.PLAYER_NAME, "stat": stat, "line": line,
                           "actual": actual, "over": actual > line, "n_cur": n_cur}
                    for name, f in feeds.items():
                        pm.get_gamelogs = f.gamelogs
                        rate, _n = pm._nba_hit_rate(r.PLAYER_NAME, stat, line, implied_override=0.5)
                        row[f"p_{name}"] = (rate - 0.15) / 0.7
                    # Baseline: a normal around his last-10 mean, both seasons.
                    v = past[col].tail(10).to_numpy(dtype=float)
                    mu, sd = v.mean(), max(v.std(), 0.3 * max(v.mean(), 0.5))
                    from math import erf, sqrt
                    row["p_recent_normal"] = 0.5 * (1 - erf((line - mu) / (sd * sqrt(2))))
                    rows.append(row)
            if verbose and i % 20 == 0:
                print(f"  {pd.Timestamp(day).date()}: {len(rows)} rows", flush=True)
    finally:
        pm.get_gamelogs, pm.get_player_id, pm.nba_season_strings = saved
    return pd.DataFrame(rows)


def summarize(d, methods=("live", "recent_normal")):
    import numpy as np
    import pandas as pd
    from scipy import stats as st
    d = d.copy()
    y = d["over"].astype(float)
    for m in methods:
        p = d[f"p_{m}"].clip(1e-4, 1 - 1e-4)
        d[f"b_{m}"] = (p - y) ** 2
    d["phase"] = pd.cut(d["n_cur"], [-1, 0, 4, 14, 999],
                        labels=["0 games this season", "1-4", "5-14", "15+"])
    out = d.groupby("phase", observed=True).apply(lambda g: pd.Series(
        {"n": len(g), "over_rate": g["over"].mean(),
         **{f"P_{m}": g[f"p_{m}"].mean() for m in methods},
         **{f"Brier_{m}": g[f"b_{m}"].mean() for m in methods},
         "p_live_vs_baseline": st.ttest_rel(g["b_live"], g["b_recent_normal"]).pvalue if len(g) > 2 else np.nan}),
        include_groups=False)
    by_stat = d.groupby("stat").apply(lambda g: pd.Series(
        {"n": len(g), "over_rate": g["over"].mean(),
         **{f"P_{m}": g[f"p_{m}"].mean() for m in methods},
         **{f"Brier_{m}": g[f"b_{m}"].mean() for m in methods}}), include_groups=False)
    return out, by_stat


if __name__ == "__main__":
    import pandas as pd
    pd.set_option("display.width", 250)
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"
    prev = sys.argv[2] if len(sys.argv) > 2 else "2024-25"
    d = build(season, prev)
    a, b = summarize(d)
    print(a.round(4).to_string())
    print(b.round(4).to_string())
