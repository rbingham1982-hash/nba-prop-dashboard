"""
ats_eval.py — is the spread model learning anything the closing line has not already priced?

The companion to fantasy_eval, and it exists because of a counting problem. Telling the
spread model's 49.25% backtest apart from the 52.38% break-even needs about 2,000 graded
picks at 80% power — 400 weeks at five picks a week, roughly 22 seasons. The tracked ATS
record in weekly_picks.json can never answer the question it was built to ask.

Margin accuracy can, in one backtest of 1,359 games. So that is what this measures: how
far `pred_margin` lands from the actual result, against how far the closing spread lands
from it. Any change to the model gets an answer the same afternoon instead of in 2048.

What it found, fitted on 2016-2020 and scored on 2021-2025 (1,359 games):

    model rating only      MAE 10.282   RMSE 13.256   corr 0.359   ATS 49.10%
    + QB                   MAE 10.280   RMSE 13.219   corr 0.366   ATS 48.19%
    + injury               MAE 10.368   RMSE 13.373   corr 0.341   ATS 47.66%
    closing spread         MAE  9.752   RMSE 12.633   corr 0.456

The line is the better estimator, by half a point of MAE and a full 0.1 of correlation.
Adding a quarterback adjustment moves MAE by 0.002; adding injuries makes it worse. Nor is
there a subgroup where the quarterback term earns its place — bucketed by how much the
starter's quality departed from the team's baseline, it helps by plus or minus 0.02 in
every bucket, including the 827 games where it departed most.

`orthogonality` says why, and it is the finding that matters:

    corr(rating difference, residual of the closing line) = +0.000, p = 0.988

Zero, to three decimals, over 1,359 games. Whatever the ratings know, the market has
already priced. That is not a statement about these two features; it is a statement about
the board. An ATS board is built on `model - market`, so when the model carries no
information the market lacks, that difference is the model's error and nothing else — and
no feature added to the model can change it, because the market prices those features too.

Weather came out the same way, and `wind_check` is built so the null can be trusted.
Totals are the control: wind is known to suppress scoring, so if that shows up and the
margin relationship does not, the margin null is an answer rather than a broken feed. On
736 outdoor games 2022-2025, with FORECAST wind rather than the measured wind that made
the original finding a look-ahead artifact:

    corr(wind, total points)    -0.159     mean total under  8 mph   45.7
    corr(wind, home margin)     +0.021     mean total over  13 mph   39.8
    corr(wind, |model error|)   -0.020

Nearly six points of scoring between the calm and windy bands, and nothing at all in the
margin. Which is what the physics says: wind suppresses both offences at once, so it moves
the total and leaves the difference alone. There is no spread edge in weather to find.

The loose thread is in totals, not spreads, and this project publishes no totals board.
Wind correlates -0.128 with total-minus-closing-total, and unders ran 64.5% in the 10-14
mph band (n=124). Treat that as unproven: at 13 mph and above it is 45-30, which is 60%
with p=0.105 and a 95% band of 48.7%-70.5% that contains both a coin flip and the 52.4%
break-even. The band was also chosen after looking. It needs a pre-registered threshold
and another season before it is anything.

It is also why `edge` behaves the way weekly_picks publishes it. Bucketed by size, the
disagreement is flat at ~49% from 0-2 points through 8+, and the published rule (top five
per week by absolute edge) hits 49.56% against 48.86% for every other game, p=0.817. The
ranking dimension is inert. Not adverse — inert, which is a different and cleaner thing.

Run it: `python ats_eval.py`, or `python ats_eval.py --wind` to include the weather test.
"""
from __future__ import annotations

import sys

TRAIN = list(range(2016, 2021))
TEST = [2021, 2022, 2023, 2024, 2025]

# Wind forecasts only reach back to about 2022 in Open-Meteo's archive, so the weather
# question is asked over a shorter span than the rest.
WIND_SEASONS = [2022, 2023, 2024, 2025]

_FEATURES = ("rating_diff", "qb_h", "qb_a", "inj_h", "inj_a")


def _player_prior(season: int):
    """
    Per player-week: his mean EPA per game over the weeks BEFORE this one.

    A shifted expanding mean, so week W never sees week W. Both the quarterback and the
    injury features are denominated in this, which keeps their fitted coefficients on the
    same scale as the model's own `scale` and makes them readable as points per EPA.
    """
    import nfl_analysis as nfl
    _, wk = nfl.get_season(season)
    wk = wk.copy()
    wk["tot_epa"] = (wk["passing_epa"].fillna(0) + wk["rushing_epa"].fillna(0)
                     + wk["receiving_epa"].fillna(0))
    wk = wk.sort_values(["player_id", "week"])
    g = wk.groupby("player_id")["tot_epa"]
    wk["prior_epa"] = g.transform(lambda s: s.shift(1).expanding().mean())
    wk["prior_n"] = g.transform(lambda s: s.shift(1).expanding().count())
    return wk[["player_id", "player_display_name", "position", "team", "week",
               "attempts", "tot_epa", "prior_epa", "prior_n"]]


def _qb_frame(pw):
    """
    (week, team) -> how far this week's starter departs from the team's quarterback baseline.

    The baseline is what the team RATING already reflects: its quarterbacks' mean EPA per
    game over prior weeks. The feature is only the gap, because the rating has the level
    covered and adding it twice would just re-weight the rating.

    The starter is taken as the quarterback with the most attempts in the game, which is a
    mild look-ahead — a Friday board knows the announced starter, not who actually took the
    snaps, so a late scratch leaks in. The market prices the announced starter too, so this
    flatters the feature slightly. It is an upper bound, and it is still worth nothing.
    """
    import numpy as np
    qb = pw[pw["position"] == "QB"].copy()
    starters = qb.sort_values("attempts").groupby(["week", "team"], as_index=False).last()
    qb = qb.sort_values(["team", "week"])
    team_qb = (qb.groupby(["team", "week"], as_index=False)["tot_epa"].sum()
                 .sort_values(["team", "week"]))
    team_qb["qb_base"] = (team_qb.groupby("team")["tot_epa"]
                          .transform(lambda s: s.shift(1).expanding().mean()))
    out = starters.merge(team_qb[["team", "week", "qb_base"]], on=["team", "week"], how="left")
    out["qb_delta"] = out["prior_epa"] - out["qb_base"]
    # No prior games means no information, which is not the same as a replacement-level
    # quarterback. Left missing so `evaluate` scores it as "no adjustment".
    out.loc[out["prior_n"].fillna(0) < 1, "qb_delta"] = np.nan
    return out[["week", "team", "qb_delta"]]


def _inj_frame(season: int, pw, weeks):
    """
    (week, team) -> EPA per game ruled out of the lineup.

    Out and Doubtful only. Questionable is excluded because 55% of questionable players
    play: it is a probability, not an absence, and counting it at full weight would
    penalise a team for a designation that usually means nothing.

    A player who misses a week has no row for it, so his team comes from the last week he
    did play.
    """
    import pandas as pd
    import nfl_analysis as nfl
    rows = []
    for w in weeks:
        try:
            status = nfl.injury_context(season, int(w))
        except Exception:
            continue
        out_ids = {str(k) for k, v in (status or {}).items()
                   if str(v) in ("Out", "Doubtful")}
        if not out_ids:
            continue
        hist = pw[(pw["week"] < w) & (pw["player_id"].astype(str).isin(out_ids))]
        if hist.empty:
            continue
        last = hist.sort_values("week").groupby("player_id").last()
        # Two games of history at least, or the sum is dominated by one-week samples.
        last = last[last["prior_n"].fillna(0) >= 2]
        for team, v in last.groupby("team")["prior_epa"].sum().items():
            rows.append({"week": int(w), "team": team, "inj_epa": float(v)})
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["week", "team", "inj_epa"])


def build(seasons, carryover=None):
    """One row per played game: the model's rating difference plus the candidate features."""
    import numpy as np
    import pandas as pd
    import nfl_game_model as gm
    carryover = gm._CARRYOVER_WEIGHT if carryover is None else carryover
    want = {int(x) for x in seasons}
    sched = gm.games(gm._with_prior(seasons))
    epa = gm.team_game_epa(gm._with_prior(seasons))
    rows = []
    for season in sorted(s for s in sched["season"].unique() if int(s) in want):
        pw = _player_prior(int(season))
        weeks = sorted(sched[sched["season"] == season]["week"].unique())
        qbf = _qb_frame(pw).set_index(["week", "team"])["qb_delta"].to_dict()
        injf = _inj_frame(int(season), pw, weeks)
        inj = (injf.set_index(["week", "team"])["inj_epa"].to_dict()
               if not injf.empty else {})
        for week in weeks:
            R = gm._ratings_through(epa, sched, int(season), int(week), carryover)
            if not R:
                continue
            wk = sched[(sched["season"] == season) & (sched["week"] == week)]
            for _, g in wk.iterrows():
                h, a = R.get(g["home_team"]), R.get(g["away_team"])
                if not h or not a or pd.isna(g.get("result")):
                    continue
                hk, ak = (int(week), g["home_team"]), (int(week), g["away_team"])
                rows.append({
                    "season": int(season), "week": int(week),
                    "home_team": g["home_team"], "away_team": g["away_team"],
                    "gameday": str(g.get("gameday"))[:10],
                    "roof": str(g.get("roof", "")),
                    "result": float(g["result"]),
                    "spread": (float(g["spread_line"])
                               if pd.notna(g.get("spread_line")) else np.nan),
                    "rating_diff": h["rating"] - a["rating"],
                    "qb_h": qbf.get(hk, np.nan), "qb_a": qbf.get(ak, np.nan),
                    "inj_h": inj.get(hk, 0.0), "inj_a": inj.get(ak, 0.0),
                })
    return pd.DataFrame(rows)


def _design(d, feats):
    import numpy as np
    return np.column_stack([d[f].astype(float).fillna(0.0).values for f in feats]
                           + [np.ones(len(d))])


def evaluate(tr, te, feats, label: str) -> dict:
    """
    Fit on `tr`, score on `te`, report margin accuracy first and ATS second.

    Margin accuracy leads because it is the number that can actually move in a single
    backtest. ATS is carried along because it is what gets published, and because a change
    that improves MAE while lowering ATS is worth seeing rather than hiding.

    A missing feature is scored as zero — no adjustment — rather than dropping the game,
    since a quarterback with no history is a real weekly occurrence and the board still has
    to price that game.
    """
    import numpy as np
    trc = tr.dropna(subset=["result", "rating_diff"])
    beta, *_ = np.linalg.lstsq(_design(trc, feats), trc["result"].values, rcond=None)
    pred = _design(te, feats) @ beta
    err = pred - te["result"].values
    ok = te["spread"].notna().values
    sp, res, p = te["spread"].values[ok], te["result"].values[ok], pred[ok]
    dec = res != sp                      # pushes are not a result
    return {"model": label,
            "MAE": round(float(np.abs(err).mean()), 3),
            "RMSE": round(float((err ** 2).mean() ** 0.5), 3),
            "corr": round(float(np.corrcoef(pred, te["result"].values)[0, 1]), 3),
            "ATS%": round(100 * float(((res > sp) == (p > sp))[dec].mean()), 2),
            "n": len(te),
            "coefs": {f: round(float(b), 3) for f, b in zip(feats, beta)}}


def orthogonality(tr, te, feats=_FEATURES) -> dict:
    """
    Does any feature explain what the closing line got wrong?

    The decisive test, and the one to run first on any new idea. Fit the margin on the
    spread alone, then correlate each candidate against what is left over. A feature that
    cannot move this number cannot improve a board built on model-minus-market, however
    good it looks on its own — the market has already priced it.

    Reported as correlations with p-values rather than a fitted model, because a
    multivariate fit on collinear features hands back unstable signs: the injury term comes
    out of the regression negative while its raw correlation here is positive, which is
    what noise looks like from two directions at once.
    """
    import numpy as np
    from scipy import stats
    trc = tr.dropna(subset=["result", "spread"])
    tec = te.dropna(subset=["result", "spread"])
    b, *_ = np.linalg.lstsq(_design(trc, ["spread"]), trc["result"].values, rcond=None)
    resid = tec["result"].values - (_design(tec, ["spread"]) @ b)
    out = {}
    for name in feats:
        x = tec[name].astype(float).fillna(0.0).values
        if x.std() == 0:
            continue
        r, p = stats.pearsonr(x, resid)
        out[name] = {"corr": round(float(r), 3), "p": round(float(p), 3), "n": len(tec)}
    return out


def wind_check(seasons=None, limit=None) -> dict:
    """
    Wind against totals and against margins, on outdoor games only.

    Totals are the control. Wind is known to suppress scoring, so if the totals
    relationship shows up and the margin one does not, the margin null is a real answer
    rather than a broken pipeline. Forecast wind, not measured wind — nfl_weather exists
    because the wind column in games.csv is only populated after kickoff, which made the
    original wind result a look-ahead artifact.
    """
    import numpy as np
    import nfl_weather as nw
    import nfl_game_model as gm
    te = build(seasons or WIND_SEASONS)
    te = te[~te["roof"].astype(str).isin(["dome", "closed"])].copy()
    if limit:
        te = te.head(limit)
    te["wind"] = [(_safe_wind(nw, r.home_team, r.gameday)) for r in te.itertuples()]
    te = te[te["wind"].notna()].copy()
    sched = gm.games(seasons or WIND_SEASONS)[
        ["season", "week", "home_team", "total_line", "home_score", "away_score"]]
    te = te.merge(sched, on=["season", "week", "home_team"], how="left")
    te["total"] = te["home_score"] + te["away_score"]
    return {"n": len(te),
            "corr_wind_total": round(float(te["wind"].corr(te["total"])), 3),
            "corr_wind_margin": round(float(te["wind"].corr(te["result"])), 3),
            "corr_wind_abs_margin": round(float(te["wind"].corr(te["result"].abs())), 3)}


def _safe_wind(nw, team, day):
    try:
        return nw.forecast_wind(team, day)
    except Exception:
        return None


if __name__ == "__main__":
    import pandas as pd
    sys.stdout.reconfigure(encoding="utf-8")
    tr, te = build(TRAIN), build(TEST)
    print(f"train {len(tr)} games {TRAIN[0]}-{TRAIN[-1]} | "
          f"test {len(te)} games {TEST[0]}-{TEST[-1]}")
    print()
    runs = [(["rating_diff"], "baseline (rating only)"),
            (["rating_diff", "qb_h", "qb_a"], "+ QB"),
            (["rating_diff", "inj_h", "inj_a"], "+ injury"),
            (["rating_diff", "qb_h", "qb_a", "inj_h", "inj_a"], "+ QB + injury"),
            (["spread"], "closing spread alone"),
            (["spread", "rating_diff"], "spread + model rating")]
    res = [evaluate(tr, te, f, l) for f, l in runs]
    print(pd.DataFrame(res)[["model", "MAE", "RMSE", "corr", "ATS%", "n"]]
          .to_string(index=False))
    print()
    print("does anything explain what the line got wrong?")
    for name, r in orthogonality(tr, te).items():
        mark = "" if r["p"] > 0.05 else "   <- nominally significant"
        print(f"  {name:<13} corr {r['corr']:+.3f}  p={r['p']:.3f}{mark}")
    if "--wind" in sys.argv:
        print()
        print("wind (outdoor games only):", wind_check())
