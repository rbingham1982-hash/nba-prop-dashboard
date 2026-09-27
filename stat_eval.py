"""
stat_eval.py — does the usage engine beat a recency mean on individual STAT LINES?

The third of the walk-forward harnesses, and the one that covers what the props board
actually sells. fantasy_eval answered this for PPR and found a tie; ats_eval answered it
for game margins and found the market already knew everything. But PPR is an aggregate,
and a prop is priced on one stat, so the question had never been asked on the quantity the
scorer is scoring.

The answer is the same, with structure underneath it.

Fitted nowhere and scored on 11,350 player-week-stat rows of 2025, every projection built
only from the weeks before the one it projects:

    engine       MAE 11.737   RMSE 24.898   rank corr 0.465   bias -1.083
    blend        MAE 11.680   RMSE 24.624   rank corr 0.472   bias -0.724
    recency      MAE 11.795   RMSE 24.692   rank corr 0.468   bias -0.366
    season_mean  MAE 11.716   RMSE 24.561   rank corr 0.467   bias -0.626

Paired on identical rows, engine against recency is -0.058 MAE at p=0.257 —
indistinguishable, the same verdict PPR returned. The engine also carries the largest
negative bias of the four, which is the same under-projection fantasy_eval found in early
weeks.

The blend beats both parents again: -0.114 against recency at p=0.000 and -0.057 against
the engine at p=0.032. That is a genuine replication rather than the same result twice,
because the target is different — seven stat lines instead of one aggregate — and it is
the evidence behind fantasy_tools shipping the blend. The props scorer still does not.

Per stat the tie hides real, opposite effects:

    Receiving Yards  n=3678   engine 17.20 vs recency 17.45   p=0.007   engine better
    Carries          n=1121   engine  3.50 vs recency  3.41   p=0.009   engine worse
    Completions      n= 438   engine  6.07 vs recency  5.84   p=0.010   engine worse
    Passing Yards, Rushing Yards, Receptions, Passing TDs     no difference

Which is mechanically sensible and worth keeping in mind before adding model anywhere.
Receiving yards depend on target share and team passing volume, both of which the usage
model explicitly represents and a player's own average cannot see. Carries and completions
are close to a direct readout of the role he already holds, where a recency mean is
already the right answer and the model only adds noise.

    python stat_eval.py          # 2025
    python stat_eval.py 2024
"""
from __future__ import annotations

import sys

MIN_PRIOR = 3

# Per position, the stats a props board would actually price. "Fantasy (PPR)" is left out
# because fantasy_eval already owns that question and answering it here twice would make
# the aggregate look like another independent result.
POS_STATS = {
    "QB": ["Passing Yards", "Passing TDs", "Completions", "Rushing Yards"],
    "RB": ["Rushing Yards", "Carries", "Receptions", "Receiving Yards"],
    "WR": ["Receptions", "Receiving Yards"],
    "TE": ["Receptions", "Receiving Yards"],
}
_METHODS = ("engine", "blend", "recency", "season_mean")


def _recency(vals, recent_n: int = 5) -> float:
    """The Player Analysis tab's projection: last five weighted 2x against the full mean."""
    recent = vals[-recent_n:]
    return (2.0 * (sum(recent) / len(recent)) + 1.0 * (sum(vals) / len(vals))) / 3.0


def build(season: int | None = None, verbose: bool = True):
    """
    One row per player-week-stat, with every method's projection and the actual.

    The per-player history is assembled once up front. Calling game_log for each player in
    each week would rescan the whole frame thousands of times and turn a minute into an
    afternoon.
    """
    import pandas as pd
    import nfl_analysis as nfl

    season, df = nfl.get_season(season)
    cols = {lab: c for lab, (c, _) in nfl.PROP_STATS.items()}
    hist: dict = {}
    for _, r in df.iterrows():
        nm = r["player_display_name"]
        hist.setdefault(nm, {"pos": r.get("position"), "weeks": []})
        hist[nm]["weeks"].append(
            (int(r["week"]), {lab: float(r.get(c, 0) or 0) for lab, c in cols.items()}))
    for v in hist.values():
        v["weeks"].sort(key=lambda t: t[0])

    rows = []
    for w in sorted(int(x) for x in df["week"].unique()):
        prior = df[df["week"] < w]
        if prior.empty:
            continue
        idx = nfl.player_index(prior)
        priors, vol = nfl.position_priors(prior), nfl.team_volume(prior)
        rates = nfl.league_rates(prior)
        # Shared across every call in this week. Without them the run is hours, not minutes.
        dcache, cvcache = {}, {}
        n_here = 0
        for nm, h in hist.items():
            if h["pos"] not in POS_STATS:
                continue
            past = [vals for k, vals in h["weeks"] if k < w]
            now = [vals for k, vals in h["weeks"] if k == w]
            if len(past) < MIN_PRIOR or not now:
                continue
            for stat in POS_STATS[h["pos"]]:
                vals = [v[stat] for v in past]
                try:
                    s = nfl.score_prop_nfl(prior, nm, stat, 0.5, priors=priors, vol=vol,
                                           idx=idx, rates=rates, dcache=dcache,
                                           cvcache=cvcache)
                except Exception:
                    s = None
                if not s or s.get("projection") is None:
                    continue
                rows.append({"week": w, "player": nm, "position": h["pos"], "stat": stat,
                             "actual": now[0][stat], "engine": float(s["projection"]),
                             "recency": _recency(vals),
                             "season_mean": sum(vals) / len(vals)})
                n_here += 1
        if verbose:
            print(f"  week {w:>2}: {n_here:>5} rows", flush=True)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["blend"] = 0.5 * out["engine"] + 0.5 * out["recency"]
    return out


def summarize(d, methods=_METHODS):
    """Error, ordering and bias per method."""
    import numpy as np
    import pandas as pd
    rows = []
    for m in methods:
        err = d[m] - d["actual"]
        corrs = []
        # Ranked within a week AND a stat: ordering receivers by receiving yards is a real
        # question, ordering a receiver against a quarterback's passing yards is not.
        for (_w, _s), g in d.groupby(["week", "stat"]):
            if len(g) >= 5:
                c = g[m].rank().corr(g["actual"].rank())
                if c == c:
                    corrs.append(c)
        rows.append({"method": m, "MAE": round(err.abs().mean(), 3),
                     "RMSE": round((err ** 2).mean() ** 0.5, 3),
                     "rank corr": round(float(np.mean(corrs)), 3) if corrs else None,
                     "bias": round(err.mean(), 3), "n": len(d)})
    return pd.DataFrame(rows)


def compare(d, a: str = "engine", b: str = "recency"):
    """
    Paired test of two methods, overall and per stat.

    Paired because the alternative is the mistake this harness was built after catching:
    averaging each method over whatever rows it happened to produce. The engine declines to
    project until a player has MIN_PRIOR games, so its rows are the easier ones, and an
    unpaired comparison rewards it for the weeks it sat out.
    """
    import pandas as pd
    from scipy import stats
    ae, be = (d[a] - d["actual"]).abs(), (d[b] - d["actual"]).abs()
    rows = [{"stat": "ALL", "n": len(d), a: round(ae.mean(), 3), b: round(be.mean(), 3),
             "diff": round(ae.mean() - be.mean(), 3),
             "p": round(float(stats.ttest_rel(ae, be).pvalue), 4)}]
    for stat, g in d.groupby("stat"):
        x, y = (g[a] - g["actual"]).abs(), (g[b] - g["actual"]).abs()
        rows.append({"stat": stat, "n": len(g), a: round(x.mean(), 3), b: round(y.mean(), 3),
                     "diff": round(x.mean() - y.mean(), 3),
                     "p": round(float(stats.ttest_rel(x, y).pvalue), 4)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    season = int(args[0]) if args else None
    d = build(season)
    if d.empty:
        print("no rows")
        raise SystemExit
    print(f"\n{len(d):,} player-week-stat rows\n")
    print(summarize(d).to_string(index=False))
    print("\nengine vs recency, paired:")
    print(compare(d, "engine", "recency").to_string(index=False))
    print("\nblend vs recency, paired:")
    print(compare(d, "blend", "recency").to_string(index=False))
