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

# How much a game from LAST season counts toward this season's rating, relative to a game
# from this one. Without carryover there is no week-1 rating at all — _ratings_through had
# nothing to average and returned {}, so the backtest silently skipped every opening week
# and predict_slate could not price the games people most want priced. Rosters and coaching
# turn over, so a prior-season game is real evidence but not equal evidence. The value is
# fitted in sweep_carryover() rather than assumed.
_CARRYOVER_WEIGHT = 0.5

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


def _with_prior(seasons):
    """Requested seasons plus the one before each, so week 1 has something to carry over."""
    ss = {int(x) for x in seasons}
    return sorted(ss | {x - 1 for x in ss})


def _ratings_through(epa_rows, sched, season: int, week: int,
                     carryover: float = _CARRYOVER_WEIGHT) -> dict:
    """
    Team ratings built ONLY from games that finished before `week` of `season`.

    Prior-season games are included at `carryover` weight when the frames contain them.
    That is what makes a week-1 rating possible; by mid-season the current year's games
    outweigh it naturally, because weight accumulates and the carryover block does not.

    Defensive EPA has to come from the schedule: a team's weekly row carries what its own
    players produced, so what it ALLOWED is the opponent's offensive EPA in the same game.
    """
    cur = epa_rows[(epa_rows["season"] == season) & (epa_rows["week"] < week)]
    prev = epa_rows[epa_rows["season"] == season - 1] if carryover > 0 else cur.iloc[:0]
    if cur.empty and prev.empty:
        return {}

    # (season, week, team) -> offensive EPA, with the weight that observation carries.
    by_game: dict = {}
    for rows, w in ((cur, 1.0), (prev, float(carryover))):
        for _, r in rows.iterrows():
            by_game[(int(r["season"]), int(r["week"]), r["team"])] = (float(r["off_epa"]), w)

    off: dict = {}
    for (_, _, team), (v, w) in by_game.items():
        o = off.setdefault(team, [0.0, 0.0])
        o[0] += v * w
        o[1] += w

    # Flip offence into defence through the schedule.
    sc = sched[((sched["season"] == season) & (sched["week"] < week))
               | (sched["season"] == season - 1)]
    deff: dict = {}
    for _, g in sc.iterrows():
        sn, wk = int(g["season"]), int(g["week"])
        h, a = g["home_team"], g["away_team"]
        ho, ao = by_game.get((sn, wk, h)), by_game.get((sn, wk, a))
        if ho is not None:
            d = deff.setdefault(a, [0.0, 0.0])   # away team allowed home's offence
            d[0] += ho[0] * ho[1]
            d[1] += ho[1]
        if ao is not None:
            d = deff.setdefault(h, [0.0, 0.0])
            d[0] += ao[0] * ao[1]
            d[1] += ao[1]

    tw = sum(w for _, w in off.values())
    league = (sum(v for v, _ in off.values()) / tw) if tw else 0.0

    out = {}
    for team in set(off) | set(deff):
        os_, ow = off.get(team, [0.0, 0.0])
        ds_, dw = deff.get(team, [0.0, 0.0])
        # Shrink both sides toward the league mean by how little has been seen.
        o_m = (os_ + _RATING_SHRINK_GAMES * league) / (ow + _RATING_SHRINK_GAMES)
        d_m = (ds_ + _RATING_SHRINK_GAMES * league) / (dw + _RATING_SHRINK_GAMES)
        out[team] = {"off": o_m, "def": d_m, "rating": o_m - d_m,
                     "n_off": round(ow, 2), "n_def": round(dw, 2)}
    return out


def fit(train_seasons, carryover: float = _CARRYOVER_WEIGHT) -> dict:
    """
    Least-squares fit of margin on rating difference, walk-forward within each season.

    Returns {scale, hfa, n, rmse}. scale converts an EPA rating gap into points; hfa is
    home-field advantage in points, fitted rather than assumed.
    """
    import numpy as np
    want = {int(x) for x in train_seasons}
    sched = games(_with_prior(train_seasons))
    epa = team_game_epa(_with_prior(train_seasons))
    X, y = [], []
    for season in sorted(s for s in sched["season"].unique() if int(s) in want):
        for week in sorted(sched[sched["season"] == season]["week"].unique()):
            R = _ratings_through(epa, sched, int(season), int(week), carryover)
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


def backtest(test_seasons, params: dict, carryover: float = _CARRYOVER_WEIGHT) -> dict:
    """
    Walk-forward evaluation against the ACTUAL closing spread.

    The only number that matters is `ats_pct`. A side must clear 52.4% at -110 to break
    even, so beating a coin flip is not the bar and beating the spread's own accuracy is
    not the bar either — covering it is.

    Pushes are excluded from the percentage rather than counted as half a win, because a
    push returns the stake and is not a result.
    """
    want = {int(x) for x in test_seasons}
    sched = games(_with_prior(test_seasons))
    epa = team_game_epa(_with_prior(test_seasons))
    rows = []
    for season in sorted(s for s in sched["season"].unique() if int(s) in want):
        for week in sorted(sched[sched["season"] == season]["week"].unique()):
            R = _ratings_through(epa, sched, int(season), int(week), carryover)
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
    sched = games([season - 1, season])
    epa = team_game_epa([season - 1, season])
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


def sweep_carryover(train_seasons, test_seasons, weights=(0.0, 0.25, 0.5, 0.75, 1.0)) -> list:
    """
    Out-of-sample ATS by carryover weight, refitting scale and hfa at each one.

    The point is to choose the weight on evidence instead of taste, and to see what
    including week 1 does — the old backtest had no week-1 rating and quietly dropped those
    games, so its number described fifteen weeks of the season and was reported as all of
    it.
    """
    rows = []
    for w in weights:
        params = fit(train_seasons, carryover=w)
        bt = backtest(test_seasons, params, carryover=w)
        wk1 = [r for r in bt["picks"] if r["week"] == 1]
        w1 = [r for r in wk1 if r["outcome"] in ("win", "loss")]
        rows.append({"carryover": w, "n": bt["n"], "ats_pct": bt["ats_pct"],
                     "roi_pct": bt["roi_pct"], "scale": round(params["scale"], 4),
                     "hfa": round(params["hfa"], 3), "week1_n": len(wk1),
                     "week1_ats": (round(sum(1 for r in w1 if r["outcome"] == "win")
                                         / len(w1) * 100, 2) if w1 else None)})
    return rows
