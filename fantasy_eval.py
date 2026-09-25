"""
fantasy_eval.py — does the fantasy projection beat a naive baseline?

The betting side has had a tracker for months. The fantasy boards, which are the part of
this project with a plausible edge — no bookmaker's hold, and the competition is consensus
rather than a market maker — had nothing. This measures the weekly projection every board
is built on, walk-forward: for week W the engine sees only the weeks before it, and the
depth chart as it stood before kickoff.

Four methods, each projecting one week of PPR points for every player who actually played
that week and has at least _MIN_PRIOR games behind him:

    engine         score_prop_nfl on prior weeks only, depth-aware (what the boards use)
    season_mean    his own mean PPR over the weeks before this one
    last4          his mean over his last four games — the "hot hand" baseline
    position_mean  the position's mean that season, the dumbest baseline that isn't zero

Scored three ways, because they answer different questions:

    MAE / RMSE     how far off the number is
    rank corr      whether the ORDER is right, which is what a start/sit call needs
    top-K hit      of the K it ranks highest at a position, how many finish top K —
                   the waiver/DFS question, where only the top of the list is acted on

Projected points use the PPR formula on projected stats. Turnovers are excluded because
the model does not project them; actuals come from nflverse's fantasy_points_ppr, which
does count them, so the engine carries a small structural penalty here rather than a
flattering one.
"""
from __future__ import annotations

_POS = ("QB", "RB", "WR", "TE")
# Roughly a 12-team league's starters at each position: the slice of the ranking anyone acts on.
_TOPK = {"QB": 12, "RB": 24, "WR": 24, "TE": 12}
_MIN_PRIOR = 3
# The engine and a player's own season average finished within a rounding error of each
# other on 2025, which is the sort of tie that usually means the two know different things.
# A half-and-half blend is the cheapest test of that, and it costs nothing to carry.
_METHODS = ("engine", "blend", "season_mean", "last4", "position_mean")


def _proj_ppr(proj: dict) -> float:
    return (0.04 * float(proj.get("Passing Yards", 0) or 0)
            + 4.0 * float(proj.get("Passing TDs", 0) or 0)
            + 0.1 * float(proj.get("Rushing Yards", 0) or 0)
            + 6.0 * float(proj.get("Rushing TDs", 0) or 0)
            + 1.0 * float(proj.get("Receptions", 0) or 0)
            + 0.1 * float(proj.get("Receiving Yards", 0) or 0)
            + 6.0 * float(proj.get("Receiving TDs", 0) or 0))


def _kickoff(season: int, week: int):
    """First kickoff of a week — the moment a projection for it has to be made by."""
    import pandas as pd
    import nfl_game_model as gm
    s = gm.schedule()
    g = s[(s["season"] == season) & (s["week"] == week)]
    return pd.Timestamp(min(g["gameday"]), tz="UTC") if not g.empty else None


def evaluate_week(season: int, week: int, depth: bool = True):
    """One week's projections from every method, plus what actually happened."""
    import pandas as pd
    import nfl_analysis as nfl
    import nfl_grades as ng
    df = ng._frame(season)
    prior, actual = df[df["week"] < week], df[df["week"] == week]
    if prior.empty or actual.empty:
        return pd.DataFrame()

    idx = nfl.player_index(prior)
    priors, vol = nfl.position_priors(prior), nfl.team_volume(prior)
    rates, dcache, cvcache = nfl.league_rates(prior), {}, {}
    # teams is left unset on purpose: current_teams() would hand the model an end-of-season
    # roster, which knows about trades that had not happened in week W. Without it
    # project_usage falls back to the team his prior games were played for.
    dep = nfl.depth_context(season, season, prior, as_of=_kickoff(season, week)) if depth else {}

    counts = prior.groupby("player_id").size()
    hist = prior.sort_values("week").groupby("player_id")["fantasy_points_ppr"]
    season_mean = hist.mean()
    last4 = hist.apply(lambda s: s.tail(4).mean())
    rows = []
    for r in actual[actual["position"].isin(_POS)].itertuples():
        pid = str(r.player_id)
        if int(counts.get(pid, 0)) < _MIN_PRIOR:
            continue
        proj = {}
        for stat in nfl._USAGE_MODEL:
            try:
                s = nfl.score_prop_nfl(prior, r.player_display_name, stat, 0.5, priors=priors,
                                       vol=vol, idx=idx, dcache=dcache, rates=rates,
                                       cvcache=cvcache, depth=dep)
            except Exception:
                s = None
            if s:
                proj[stat] = s.get("projection", 0.0)
        if not proj:
            continue
        rows.append({"week": week, "pid": pid, "player": r.player_display_name,
                     "position": r.position,
                     "actual": float(getattr(r, "fantasy_points_ppr", 0) or 0),
                     "engine": _proj_ppr(proj),
                     "season_mean": float(season_mean.get(pid, 0) or 0),
                     "last4": float(last4.get(pid, 0) or 0)})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    pos_mean = prior[prior["position"].isin(_POS)].groupby("position")["fantasy_points_ppr"].mean()
    out["position_mean"] = out["position"].map(pos_mean).astype(float)
    out["blend"] = 0.5 * out["engine"] + 0.5 * out["season_mean"]
    return out


def backtest(season: int, weeks=None, depth: bool = True):
    """Every eligible week of a season, projected walk-forward."""
    import pandas as pd
    import nfl_grades as ng
    have = ng.weeks_available(season)
    # Week 5 at the earliest: the engine needs _MIN_PRIOR games of history, and a week-4
    # projection off three games measures the sample size, not the method.
    weeks = weeks or [w for w in have if w >= 5]
    frames = [evaluate_week(season, w, depth=depth) for w in weeks]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def summarize(df, methods=_METHODS):
    """Error, ordering and top-K accuracy for each method."""
    import pandas as pd
    rows = []
    for m in methods:
        err = df[m] - df["actual"]
        corrs, prec = [], []
        for (_w, pos), g in df.groupby(["week", "position"]):
            if len(g) >= 5:
                corrs.append(g[m].rank().corr(g["actual"].rank()))
            k = min(_TOPK.get(pos, 12), len(g))
            if k:
                prec.append(len(set(g.nlargest(k, m)["pid"]) & set(g.nlargest(k, "actual")["pid"])) / k)
        rows.append({"method": m, "MAE": round(err.abs().mean(), 2),
                     "RMSE": round((err ** 2).mean() ** 0.5, 2),
                     "rank corr": round(sum(corrs) / len(corrs), 3) if corrs else None,
                     "top-K hit": round(sum(prec) / len(prec), 3) if prec else None,
                     "bias": round(err.mean(), 2), "n": len(df)})
    return pd.DataFrame(rows)


def by_position(df, methods=_METHODS):
    """The same comparison per position — the engine is not equally good at all four."""
    import pandas as pd
    rows = []
    for pos, g in df.groupby("position"):
        row = {"position": pos, "n": len(g)}
        for m in methods:
            row[f"{m} MAE"] = round((g[m] - g["actual"]).abs().mean(), 2)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("position")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    season = int(args[0]) if args else 2025
    depth = "--no-depth" not in sys.argv
    out = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--save=")), None)
    df = backtest(season, depth=depth)
    if df.empty:
        print(f"no eligible weeks in {season}")
    else:
        if out:
            df.to_parquet(out)
        print(f"{season}: {df['week'].nunique()} weeks, {len(df)} player-weeks, "
              f"depth chart {'on' if depth else 'off'}\n")
        print(summarize(df).to_string(index=False))
        print()
        print(by_position(df).to_string(index=False))
