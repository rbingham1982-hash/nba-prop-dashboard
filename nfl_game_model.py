"""
nfl_game_model.py — NFL game picker against the spread.

Predicts a game's margin from team EPA ratings and compares it to the posted spread. The
question this answers is narrow and the bar is unambiguous: at -110 a side must win 52.4%
of the time to break even, so anything below that is a losing bet no matter how good the
accuracy looks against a coin flip.

Why this is a better-founded exercise than the props model was: nfldata publishes
`spread_line` and `result` for every game back to 1999, so the model can be evaluated
against the ACTUAL closing spread out of sample, on thousands of games, before a single
bet is placed. The props side had to accumulate its own closing lines over weeks and still
has only partial coverage.

Ratings use EPA, which is the best-known public predictor of NFL team strength:

    offensive EPA  = passing_epa + rushing_epa, summed per team-game
    defensive EPA  = the same quantity allowed to opponents
    rating         = offense - defense

receiving_epa is deliberately excluded. It correlates 0.922 with passing_epa because they
are the same plays credited to different players, and including it would double-count the
passing game.

Everything is computed WALK-FORWARD: a rating used to predict week W is built only from
weeks before W. The whole point is an honest out-of-sample number, and a rating that has
seen the game it is predicting produces a beautiful backtest and no edge.
"""
from __future__ import annotations

_GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# Games of prior data before a team's own rating outweighs the league mean. Early in a
# season every rating is a small sample, and an unshrunk week-2 rating is mostly noise.
_RATING_SHRINK_GAMES = 6

_cache: dict = {}


def games(seasons=None):
    """Completed regular-season games with closing spread and final margin."""
    import pandas as pd
    key = ("games", tuple(seasons) if seasons else None)
    if key in _cache:
        return _cache[key]
    d = pd.read_csv(_GAMES_URL)
    d = d[(d["game_type"] == "REG") & d["home_score"].notna() & d["spread_line"].notna()]
    if seasons:
        d = d[d["season"].isin(list(seasons))]
    d = d.sort_values(["season", "week"]).reset_index(drop=True)
    _cache[key] = d
    return d


def team_game_epa(seasons):
    """
    (season, week, team) -> offensive EPA for that game.

    Summed from player rows, which is the only EPA the weekly release exposes.
    """
    import pandas as pd
    import nfl_analysis as nfl
    key = ("epa", tuple(seasons))
    if key in _cache:
        return _cache[key]
    frames = []
    for s in seasons:
        try:
            _, wk = nfl.get_season(s)
        except Exception:
            continue
        wk = wk.copy()
        wk["off_epa"] = wk["passing_epa"].fillna(0) + wk["rushing_epa"].fillna(0)
        g = wk.groupby(["team", "week"], as_index=False)["off_epa"].sum()
        g["season"] = s
        frames.append(g)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    _cache[key] = out
    return out


def _ratings_through(epa_rows, sched, season: int, week: int) -> dict:
    """
    Team ratings built ONLY from games before `week` in `season`.

    Defensive EPA has to come from the schedule: a team's weekly row carries what its own
    players produced, so what it ALLOWED is the opponent's offensive EPA in the same game.
    """
    prior = epa_rows[(epa_rows["season"] == season) & (epa_rows["week"] < week)]
    if prior.empty:
        return {}
    off = {}
    for _, r in prior.iterrows():
        off.setdefault(r["team"], []).append(float(r["off_epa"]))

    # Map each prior game to its two teams so offence can be flipped into defence.
    sc = sched[(sched["season"] == season) & (sched["week"] < week)]
    by_week: dict = {}
    for _, r in prior.iterrows():
        by_week[(int(r["week"]), r["team"])] = float(r["off_epa"])
    deff: dict = {}
    for _, g in sc.iterrows():
        w = int(g["week"])
        h, a = g["home_team"], g["away_team"]
        ho, ao = by_week.get((w, h)), by_week.get((w, a))
        if ho is not None:
            deff.setdefault(a, []).append(ho)   # away team allowed home's offence
        if ao is not None:
            deff.setdefault(h, []).append(ao)

    all_off = [v for vs in off.values() for v in vs]
    league = sum(all_off) / len(all_off) if all_off else 0.0

    out = {}
    for team in set(off) | set(deff):
        o = off.get(team, [])
        d = deff.get(team, [])
        # Shrink both sides toward the league mean by how little has been seen.
        o_m = ((sum(o) + _RATING_SHRINK_GAMES * league) / (len(o) + _RATING_SHRINK_GAMES)
               if o or league else 0.0)
        d_m = ((sum(d) + _RATING_SHRINK_GAMES * league) / (len(d) + _RATING_SHRINK_GAMES)
               if d or league else 0.0)
        out[team] = {"off": o_m, "def": d_m, "rating": o_m - d_m,
                     "n_off": len(o), "n_def": len(d)}
    return out


def fit(train_seasons) -> dict:
    """
    Least-squares fit of margin on rating difference, walk-forward within each season.

    Returns {scale, hfa, n, rmse}. scale converts an EPA rating gap into points; hfa is
    home-field advantage in points, fitted rather than assumed.
    """
    import numpy as np
    sched = games(train_seasons)
    epa = team_game_epa(train_seasons)
    X, y = [], []
    for season in sorted(sched["season"].unique()):
        for week in sorted(sched[sched["season"] == season]["week"].unique()):
            R = _ratings_through(epa, sched, int(season), int(week))
            if not R:
                continue
            wk = sched[(sched["season"] == season) & (sched["week"] == week)]
            for _, g in wk.iterrows():
                h, a = R.get(g["home_team"]), R.get(g["away_team"])
                if not h or not a:
                    continue
                X.append(h["rating"] - a["rating"])
                y.append(float(g["result"]))
    if len(X) < 50:
        return {"scale": 0.0, "hfa": 0.0, "n": len(X), "rmse": None}
    A = np.column_stack([np.array(X), np.ones(len(X))])
    beta, *_ = np.linalg.lstsq(A, np.array(y), rcond=None)
    pred = A @ beta
    rmse = float(np.sqrt(((np.array(y) - pred) ** 2).mean()))
    return {"scale": float(beta[0]), "hfa": float(beta[1]), "n": len(X), "rmse": rmse}


def backtest(test_seasons, params: dict) -> dict:
    """
    Walk-forward evaluation against the ACTUAL closing spread.

    The only number that matters is `ats_pct`. A side must clear 52.4% at -110 to break
    even, so beating a coin flip is not the bar and beating the spread's own accuracy is
    not the bar either — covering it is.

    Pushes are excluded from the percentage rather than counted as half a win, because a
    push returns the stake and is not a result.
    """
    sched = games(test_seasons)
    epa = team_game_epa(test_seasons)
    rows = []
    for season in sorted(sched["season"].unique()):
        for week in sorted(sched[sched["season"] == season]["week"].unique()):
            R = _ratings_through(epa, sched, int(season), int(week))
            if not R:
                continue
            wk = sched[(sched["season"] == season) & (sched["week"] == week)]
            for _, g in wk.iterrows():
                h, a = R.get(g["home_team"]), R.get(g["away_team"])
                if not h or not a:
                    continue
                pred = params["scale"] * (h["rating"] - a["rating"]) + params["hfa"]
                spread = float(g["spread_line"])
                result = float(g["result"])
                pick_home = pred > spread
                if result == spread:
                    outcome = "push"
                elif (result > spread) == pick_home:
                    outcome = "win"
                else:
                    outcome = "loss"
                rows.append({
                    "season": int(season), "week": int(week),
                    "game": f"{g['away_team']} @ {g['home_team']}",
                    "pred_margin": round(pred, 2), "spread": spread,
                    "result": result, "pick": "home" if pick_home else "away",
                    "edge": round(pred - spread, 2), "outcome": outcome,
                })
    wins = sum(1 for r in rows if r["outcome"] == "win")
    losses = sum(1 for r in rows if r["outcome"] == "loss")
    pushes = sum(1 for r in rows if r["outcome"] == "push")
    decided = wins + losses
    return {
        "n": len(rows), "wins": wins, "losses": losses, "pushes": pushes,
        "ats_pct": round(wins / decided * 100, 2) if decided else None,
        "breakeven": 52.38,
        "roi_pct": round(((wins * (100 / 110) - losses) / decided) * 100, 2) if decided else None,
        "picks": rows,
    }


def predict_slate(season: int, week: int, params: dict, upcoming=None) -> list:
    """Rate an upcoming slate. Ratings use only games before `week`."""
    sched = games([season])
    epa = team_game_epa([season])
    R = _ratings_through(epa, sched, season, week)
    out = []
    for g in (upcoming if upcoming is not None else []):
        h, a = R.get(g.get("home_team")), R.get(g.get("away_team"))
        if not h or not a:
            out.append({**g, "pred_margin": None, "note": "no rating yet"})
            continue
        pred = params["scale"] * (h["rating"] - a["rating"]) + params["hfa"]
        sp = g.get("spread_line")
        out.append({**g, "pred_margin": round(pred, 2),
                    "edge": round(pred - float(sp), 2) if sp is not None else None,
                    "pick": ("home" if sp is not None and pred > float(sp) else "away")
                            if sp is not None else None})
    return out
