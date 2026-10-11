"""
nfl_prop_eval.py — the NFL props projection against FanDuel's actual lines. Read-only.

stat_eval and history_frame's walk-forward scored P(over) at a NEUTRAL line, which never
asks the question a props board lives on: is the projection a better estimate than the
line the book posts? This does. For every graded NFL FanDuel prop in the logs it rebuilds
the projection the live scorer would have made from games BEFORE that week
(history_frame, blend_recency=True, usage path only: depth charts and injuries are today's
and would leak), then compares the projection mean, the market line and the actual stat.

    venv/Scripts/python.exe nfl_prop_eval.py

First run, 2026-10-11, 785 props from weeks 1-5 (rebuilt P(over) correlates 0.82 with the
logged one; Brier rebuilt 0.274, market 0.249, coin flip 0.250):

  * The LINE estimates the stat better: MAE 18.66 against the projection's 20.48
    (+1.81 +/- 0.51). Rushing Yards +3.15 +/- 0.74 and Passing Yards +5.4 +/- 2.9 are
    clearly worse; Receptions and Passing TDs tie the line.
  * The projection's disagreement with the line carries no direction: rank corr with
    (actual - line) -0.04, right side 51.6%. It is not reversed; it is uninformative.
  * Its stated spread is too narrow: SD of (actual - mu)/sigma is 1.54, |z|>2 at 8.9%
    against 4.6%. sigma is the player's game-to-game spread only and leaves out the
    uncertainty of the projection itself (role changes, thin samples), so coin-flip
    disagreements print as 90%+. Widening sigma x1.5 moves continuous-stat Brier only
    0.278 -> 0.271 (the market is 0.249), and a wider gamma also drags the median down,
    so it is not a fix on its own.
  * Worst with thin history: Brier 0.316 at <=6 games, 0.269 at 13-20.

The verdict matches fantasy_eval and ats_eval: the usage engine has no information the
posted line lacks. That is why get_market_blend gives it zero weight.
"""
import sys, math, collections, datetime
sys.stdout.reconfigure(encoding="utf-8")
import pandas as pd
import nfl_analysis as nfl
import nfl_game_model as gm
import parlay_tracker as pt

PLAY = 2026
hist = nfl.history_frame(PLAY)
cur = hist[hist["week"] > 0]
games = gm.schedule() if hasattr(gm, "schedule") else pd.read_csv(gm._GAMES_URL)
games = games[games["season"] == PLAY]
day_week = {str(r["gameday"])[:10]: int(r["week"]) for _, r in games.iterrows()}

def week_of(start):
    # start_time is UTC; US evening kickoffs land on the next UTC date.
    d = datetime.datetime.fromisoformat(start.replace("Z", "+00:00")) - datetime.timedelta(hours=8)
    return day_week.get(d.date().isoformat())

col = {lab: c for lab, (c, _) in nfl.PROP_STATS.items()}

# One row per prop, expressed as the OVER.
props, seen = [], set()
for p in pt._load()["parlays"]:
    if p.get("sport") != "NFL" or p.get("sportsbook") != "FanDuel":
        continue
    for l in p["legs"]:
        if pt._leg_hit(l) not in (True, False) or l.get("stat_type") not in col:
            continue
        k = (l["player_name"], l["stat_type"], float(l["line_score"]), l.get("game_id"))
        if k in seen:
            continue
        seen.add(k)
        w = week_of(l.get("start_time") or "")
        if w is None:
            continue
        imp = float(l["implied_prob"])
        props.append({"player": l["player_name"], "stat": l["stat_type"], "line": float(l["line_score"]),
                      "week": w, "mkt_over": imp if l.get("side", "over") == "over" else 1 - imp,
                      "logged_model_over": (float(l["model_hit_rate"]) if l.get("model_hit_rate") is not None else None)
                      if l.get("side", "over") == "over" else
                      (1 - float(l["model_hit_rate"]) if l.get("model_hit_rate") is not None else None)})
print("unique graded props with a week:", len(props))

ctx = {}
rows = []
for pr in props:
    w = pr["week"]
    if w not in ctx:
        prior = hist[hist["week"] < w]
        ctx[w] = dict(df=prior, priors=nfl.position_priors(prior), vol=nfl.team_volume(prior),
                      idx=nfl.player_index(prior), rates=nfl.league_rates(prior), dcache={}, cvcache={})
    c = ctx[w]
    act = cur[(cur["week"] == w) & (cur["player_display_name"] == pr["player"])]
    if act.empty:
        continue                                  # did not play / name mismatch
    actual = float(act.iloc[0].get(col[pr["stat"]], 0) or 0)
    try:
        s = nfl.score_prop_nfl(c["df"], pr["player"], pr["stat"], pr["line"], priors=c["priors"],
                               vol=c["vol"], idx=c["idx"], rates=c["rates"], dcache=c["dcache"],
                               cvcache=c["cvcache"], blend_recency=True)
    except Exception:
        s = None
    if not s or s.get("projection") is None:
        continue
    rows.append({**pr, "actual": actual, "mu": float(s["projection"]), "sigma": float(s.get("sigma") or 0),
                 "model_over": float(s["model_over"]), "n": int(s.get("n", 0)),
                 "over_hit": actual > pr["line"]})
d = pd.DataFrame(rows)
print("rebuilt:", len(d), "of", len(props))
if d.empty:
    sys.exit()

def brier(p, y): return float(((p - y) ** 2).mean())
y = d["over_hit"].astype(float)
print(f"\nReproduction check: corr(rebuilt P(over), logged P(over)) = "
      f"{d['model_over'].corr(d['logged_model_over']):.2f}")
print(f"Brier: rebuilt model {brier(d['model_over'], y):.4f}  market {brier(d['mkt_over'], y):.4f}")

print("\n1. Who estimates the stat better, the projection or the line? (MAE vs actual)")
for st, g in [("ALL", d)] + list(d.groupby("stat")):
    e_mu = (g["mu"] - g["actual"]).abs(); e_ln = (g["line"] - g["actual"]).abs()
    diff = e_mu - e_ln
    se = diff.std() / math.sqrt(len(g)) if len(g) > 1 else float("nan")
    print(f"  {st:16s} n={len(g):4d}  projection MAE {e_mu.mean():7.2f}  line MAE {e_ln.mean():7.2f}  "
          f"diff {diff.mean():+6.2f} ±{se:.2f}   bias(mu-actual) {(g['mu']-g['actual']).mean():+6.2f}")

print("\n2. Does the projection's disagreement with the line point the right way?")
gap = d["mu"] - d["line"]; res = d["actual"] - d["line"]
for st, g in [("ALL", d)] + list(d.groupby("stat")):
    gg = g["mu"] - g["line"]; rr = g["actual"] - g["line"]
    sc = gg.rank().corr(rr.rank())
    same = ((gg > 0) == (rr > 0))[(gg != 0) & (rr != 0)].mean()
    print(f"  {st:16s} rank corr(gap, actual-line) {sc:+.3f}   right direction {100*same:5.1f}%")

print("\n3. Is the stated uncertainty right? z = (actual - mu) / sigma should have SD ~1")
z = (d["actual"] - d["mu"]) / d["sigma"].where(d["sigma"] > 0)
for st, g in [("ALL", d)] + list(d.groupby("stat")):
    zz = ((g["actual"] - g["mu"]) / g["sigma"].where(g["sigma"] > 0)).dropna()
    print(f"  {st:16s} SD(z) {zz.std():5.2f}   |z|>2: {100*(zz.abs()>2).mean():5.1f}% (normal: 4.6%)")

print("\n4. Where the projection sits relative to the line, and what happened")
d["gap_sd"] = (d["mu"] - d["line"]) / d["sigma"].where(d["sigma"] > 0)
bins = [-99, -1, -0.5, -0.2, 0.2, 0.5, 1, 99]
d["gb"] = pd.cut(d["gap_sd"], bins)
for b, g in d.groupby("gb", observed=True):
    print(f"  (mu-line)/sigma {str(b):14s} n={len(g):4d} model P(over) {100*g['model_over'].mean():5.1f}%  "
          f"market {100*g['mkt_over'].mean():5.1f}%  over happened {100*g['over_hit'].mean():5.1f}%")

print("\n5. By games of history behind the projection")
d["nb"] = pd.cut(d["n"], [0, 6, 12, 20, 99])
for b, g in d.groupby("nb", observed=True):
    print(f"  n {str(b):9s} rows={len(g):4d}  Brier model {brier(g['model_over'], g['over_hit'].astype(float)):.4f}"
          f"  market {brier(g['mkt_over'], g['over_hit'].astype(float)):.4f}")
