"""
nba_spreads.py — NBA team ratings, a predicted margin for each game, and the posted spread.

The rating is the simplest one that is not naive: every team's scoring margin, adjusted for
who it played and where, fitted by least squares over the season so far (a game's margin =
home rating - away rating + home court). Early in a season there are too few games to fit
thirty teams, so each rating is pulled toward last season's, itself regressed 40% toward
average, with the pull worth `PRIOR_GAMES` games. Walk-forward throughout: a game is only
ever predicted from games that finished before its date.

The spread is ESPN's posted line (DraftKings, via the public scoreboard), kept as the HOME
team's number, the way ESPN stores it: -3.5 means the home side gives 3.5.

Measured against the market before any pick is published: see backtest(). An NBA rating
this simple is not expected to beat the closing line, and the record will say whether it does.
"""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

PRIOR_GAMES = 8.0          # how many games' weight last season's rating carries
PRIOR_KEEP = 0.6           # last season's rating kept after regressing toward average
_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
_ESPN_ABBR = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}


def team_games(season: str) -> pd.DataFrame:
    """One row per game: date, home, away, home_pts, away_pts, from the league game log."""
    import nba_grades as ng
    log = ng.season_log(season)
    t = (log.groupby(["GAME_ID", "date", "TEAM_ABBREVIATION", "MATCHUP"], as_index=False)["PTS"].sum())
    t["home"] = t["MATCHUP"].str.contains("vs.", regex=False)
    h = t[t["home"]].rename(columns={"TEAM_ABBREVIATION": "home_team", "PTS": "home_pts"})
    a = t[~t["home"]].rename(columns={"TEAM_ABBREVIATION": "away_team", "PTS": "away_pts"})
    g = h[["GAME_ID", "date", "home_team", "home_pts"]].merge(a[["GAME_ID", "away_team", "away_pts"]], on="GAME_ID")
    g["margin"] = g["home_pts"] - g["away_pts"]
    return g.sort_values("date").reset_index(drop=True)


def fit(games: pd.DataFrame, prior: dict | None = None, prior_games: float = PRIOR_GAMES) -> dict:
    """{'ratings': {team: points}, 'hca': points} by ridge least squares toward `prior`."""
    prior = prior or {}
    teams = sorted(set(games["home_team"]) | set(games["away_team"]) | set(prior))
    if not teams:
        return {"ratings": {}, "hca": 2.0}
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    rows, y = [], []
    for hm, aw, m in zip(games["home_team"], games["away_team"], games["margin"]):
        x = np.zeros(n + 1)
        x[ix[hm]], x[ix[aw]], x[n] = 1.0, -1.0, 1.0
        rows.append(x); y.append(float(m))
    # The prior as pseudo-observations: rating_t = prior_t, weight sqrt(prior_games).
    w = np.sqrt(prior_games)
    for t in teams:
        x = np.zeros(n + 1); x[ix[t]] = w
        rows.append(x); y.append(w * float(prior.get(t, 0.0)))
    # Home court gets a weak prior of 2 points so a handful of games cannot swing it.
    x = np.zeros(n + 1); x[n] = 2.0
    rows.append(x); y.append(2.0 * 2.0)
    sol = np.linalg.lstsq(np.array(rows), np.array(y), rcond=None)[0]
    r = sol[:n] - sol[:n].mean()
    return {"ratings": {t: float(r[ix[t]]) for t in teams}, "hca": float(sol[n])}


def season_prior(prev_season: str) -> dict:
    """Last season's final ratings, regressed toward average."""
    try:
        m = fit(team_games(prev_season), prior_games=0.5)
    except Exception:
        return {}
    return {t: PRIOR_KEEP * v for t, v in m["ratings"].items()}


def predict(model: dict, home: str, away: str) -> float:
    r = model["ratings"]
    return r.get(home, 0.0) - r.get(away, 0.0) + model["hca"]


def model_for(day: datetime.date) -> dict:
    """Ratings using only games before `day`, with last season as the prior."""
    import parlay_model as pm
    cur, prev = pm.nba_season_strings(day)
    try:
        g = team_games(cur)
        g = g[g["date"].dt.date < day]
    except Exception:
        g = pd.DataFrame(columns=["home_team", "away_team", "margin"])
    return fit(g, season_prior(prev))


def slate(day: datetime.date) -> list:
    """The day's games from ESPN with the posted home spread, not yet started."""
    import requests
    js = requests.get(f"{_SITE}/scoreboard", params={"dates": day.strftime("%Y%m%d")}, timeout=20,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
    out = []
    for e in js.get("events", []):
        if int(e.get("season", {}).get("type", 0)) not in (2, 3):
            continue
        c = e["competitions"][0]
        if e["status"]["type"].get("state") != "pre":
            continue                                       # under way or final: no pick
        side = {x["homeAway"]: _ESPN_ABBR.get(x["team"]["abbreviation"], x["team"]["abbreviation"])
                for x in c["competitors"]}
        odds = (c.get("odds") or [{}])[0]
        if odds.get("spread") is None:
            continue
        out.append({"event": e["id"], "game": f"{side['away']} @ {side['home']}", "home": side["home"],
                    "away": side["away"], "start_time": e["date"], "home_spread": float(odds["spread"]),
                    "book": (odds.get("provider") or {}).get("name", "")})
    return out


def picks(day: datetime.date, min_edge: float = 0.0) -> list:
    """Every game on the slate with the model's side against the posted spread, biggest edge first."""
    m = model_for(day)
    rows = []
    for g in slate(day):
        pred = predict(m, g["home"], g["away"])
        edge = pred + g["home_spread"]               # > 0: the home side covers in the model
        side = "home" if edge > 0 else "away"
        team = g["home"] if side == "home" else g["away"]
        line = g["home_spread"] if side == "home" else -g["home_spread"]
        rows.append({**g, "pred_margin": round(pred, 2), "edge": round(abs(edge), 2), "pick": side,
                     "pick_label": f"{team} {line:+g}", "outcome": None, "home_pts": None, "away_pts": None})
    return sorted([r for r in rows if r["edge"] >= min_edge], key=lambda r: -r["edge"])


def backtest(spreads: pd.DataFrame, season: str = "2025-26", prev: str = "2024-25") -> dict:
    """
    Walk-forward ATS over a season of posted spreads (columns date, home, away, home_spread,
    home_pts, away_pts): the model's side at each spread, and how its margin error compares
    with the spread's own.
    """
    games = team_games(season)
    prior = season_prior(prev)
    sp = spreads.dropna(subset=["home_spread"]).copy()
    sp["date"] = pd.to_datetime(sp["date"]).dt.date
    sp["home"] = sp["home"].map(lambda a: _ESPN_ABBR.get(a, a))
    sp["away"] = sp["away"].map(lambda a: _ESPN_ABBR.get(a, a))
    res, cache = [], {}
    for _, r in sp.sort_values("date").iterrows():
        d = r["date"]
        if d not in cache:
            cache[d] = fit(games[games["date"].dt.date < d], prior)
        pred = predict(cache[d], r["home"], r["away"])
        margin = r["home_pts"] - r["away_pts"]
        cover = margin + r["home_spread"]
        if cover == 0:
            continue                                     # push
        edge = pred + r["home_spread"]
        res.append({"date": d, "edge": edge, "won": (edge > 0) == (cover > 0),
                    "err_model": pred - margin, "err_line": -r["home_spread"] - margin})
    d = pd.DataFrame(res)
    out = {"games": len(d), "ats_pct": round(100 * d["won"].mean(), 2),
           "rmse_model": round(float(np.sqrt((d["err_model"] ** 2).mean())), 2),
           "rmse_line": round(float(np.sqrt((d["err_line"] ** 2).mean())), 2)}
    for t in (1, 2, 3, 4):
        s = d[d["edge"].abs() >= t]
        out[f"ats_edge{t}+"] = (len(s), round(100 * s["won"].mean(), 2) if len(s) else None)
    late = d[d["date"] >= datetime.date(2026, 1, 1)]
    out["ats_after_jan1"] = (len(late), round(100 * late["won"].mean(), 2))
    return out
