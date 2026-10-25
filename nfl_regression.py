"""
nfl_regression.py — the Tuesday issue's "Regression watch": teams whose record has run
ahead of, or behind, how well they actually play.

The basis is a check run on nflverse 2010-2025 (460 team-seasons, 2026-10-25). Each stat
was measured over Weeks 1-8 and correlated with results from Week 9 on:

    point differential / game        r 0.47 with later point differential
    win %                            r 0.44
    EPA per dropback, off - def      r 0.42
    net yards per pass attempt       r 0.38
    turnover margin / game           r 0.24
    rushing yards / game             r 0.23
    one-score game win %             r 0.22
    yards per carry, off - def       r 0.15

Within a single game, turnovers decide a lot: +2 or better won 87% of 1,495 team-games.
But turnover margin barely carries from the first half of a season to the second (r 0.15),
while passing efficiency carries (r 0.38). So a record built on turnovers and close wins,
with middling passing efficiency, is the one most likely to slide.

What this is NOT: a betting edge. The same data, walk-forward over 3,066 games against the
closing spread, found no stat gap (passing efficiency, point differential, turnovers,
record-versus-efficiency) that predicts the cover in both 2010-2019 and 2020-2025. The
market prices all of it. The segment is for reading a season, and for fantasy schedules.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STUDY = {"team_seasons": 460, "seasons": "2010-2025", "pd_r": 0.47, "epa_r": 0.42, "to_r": 0.24,
         "rush_r": 0.23, "close_r": 0.22, "to_carry": 0.15, "epa_carry": 0.38, "to_plus2_win": 0.87,
         "ats_games": 3066}


def table(season: int, through_week: int | None = None) -> pd.DataFrame:
    """One row per team over its completed games: record, point differential, passing
    efficiency (offense minus defense), turnover margin, and wins expected from efficiency."""
    import nfl_analysis as nfl
    import nfl_game_model as gm
    w = nfl._load_weekly(season)
    if through_week:
        w = w[w["week"] <= through_week]
    c = lambda k: w[k].fillna(0)
    off = pd.DataFrame({"week": w["week"], "team": w["team"], "db": c("attempts") + c("sacks_suffered"),
                        "epa": c("passing_epa"),
                        "to": c("passing_interceptions") + c("sack_fumbles_lost") + c("rushing_fumbles_lost")
                              + c("receiving_fumbles_lost")}).groupby(["week", "team"]).sum().reset_index()
    sch = gm.schedule()
    sch = sch[(sch["season"] == season) & (sch["game_type"] == "REG") & sch["home_score"].notna()]
    if through_week:
        sch = sch[sch["week"] <= through_week]
    g = pd.concat([sch.assign(team=sch["home_team"], opp=sch["away_team"], pf=sch["home_score"], pa=sch["away_score"]),
                   sch.assign(team=sch["away_team"], opp=sch["home_team"], pf=sch["away_score"], pa=sch["home_score"])])
    g = g[["week", "team", "opp", "pf", "pa"]]
    g = g.merge(off, on=["week", "team"]).merge(
        off.rename(columns={"team": "opp", "db": "d_db", "epa": "d_epa", "to": "d_to"}), on=["week", "opp"])
    g["m"] = g["pf"] - g["pa"]
    rows = []
    for team, d in g.groupby("team"):
        close = d[d["m"].abs() <= 8]
        rows.append({"team": team, "G": len(d), "W": int((d["m"] > 0).sum()), "L": int((d["m"] < 0).sum()),
                     "T": int((d["m"] == 0).sum()), "pd_g": d["m"].mean(),
                     "epa_db": d["epa"].sum() / d["db"].sum() - d["d_epa"].sum() / d["d_db"].sum(),
                     "to_g": (d["d_to"].sum() - d["to"].sum()) / len(d),
                     "close": f"{int((close['m'] > 0).sum())}-{int((close['m'] < 0).sum())}"})
    t = pd.DataFrame(rows).set_index("team")
    t["win_pct"] = (t["W"] + 0.5 * t["T"]) / t["G"]
    slope, icpt = np.polyfit(t["epa_db"], t["win_pct"], 1)       # this season's own league
    t["exp_wins"] = (icpt + slope * t["epa_db"]).clip(0, 1) * t["G"]
    t["gap"] = t["W"] + 0.5 * t["T"] - t["exp_wins"]
    t["epa_rank"] = t["epa_db"].rank(ascending=False, method="min").astype(int)
    return t


def watch(season: int, through_week: int, n: int = 4) -> dict:
    t = table(season, through_week)
    if t.empty or t["G"].max() < 3:
        return {}
    rec = lambda r: f"{r['W']}-{r['L']}" + (f"-{r['T']}" if r["T"] else "")
    def rows(d):
        return [{"team": k, "record": rec(r), "pd_g": round(r.pd_g, 1), "epa_db": round(r.epa_db, 3),
                 "epa_rank": int(r.epa_rank), "to_g": round(r.to_g, 2), "close": r.close,
                 "gap": round(r.gap, 1)} for k, r in d.iterrows()]
    # A straight line cannot reach 6-0, so an unbeaten team with the league's second-best
    # passing efficiency "outruns" it without being a candidate for anything. Ahead means a
    # record a win or more above the efficiency, from efficiency outside the top 10; behind,
    # a win or more below, from efficiency in the top 20.
    ahead = t[(t["gap"] >= 1) & (t["epa_rank"] > 10)].sort_values("gap", ascending=False)
    behind = t[(t["gap"] <= -1) & (t["epa_rank"] <= 20)].sort_values("gap")
    return {"week": through_week, "ahead": rows(ahead.head(n)), "behind": rows(behind.head(n))}
