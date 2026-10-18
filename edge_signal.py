"""
edge_signal.py — a live, pre-registered test of one betting rule: take the OVER on a
FanDuel NFL player prop when the project's prop model rates it clearly above the price.

Why this is a test rather than a pick list
------------------------------------------
On 2026-10-11 the NFL market blend went to the market's own price (get_market_blend):
legs the model chose by edge had graded 2-5 points below their prices, and closing-line
value has been negative throughout. On 2026-10-18 the model still rated 72 of 398 overs
inside the old 4-18% edge band, nearly all of them overs. That is either a soft spot in
FanDuel's over prices or the model's systematic lean, and the parlay logs cannot say
which because they price legs in probability points, not at a bet's real return.
This module stores the quoted over odds when a prop qualifies and grades the result, so
the record answers in money.

Expect it to lose: FanDuel loads extra margin onto overs (see form_signal), which is
the opposite side to the one this rule takes.

THE RULE IS FIXED (2026-10-18, before any row graded). Do not tune it on its own results.

  * FanDuel two-way NFL props for daily_parlay_gen.NFL_STAT_TYPES, OVER side only.
  * Model probability from daily_parlay_gen.score_legs (the board's own hit-rate model,
    calibrated), with at least _MIN_SAMPLE games behind it.
  * Edge = model - de-vigged fair probability, inside [0.04, 0.18] (weekly_picks' band:
    above 18% has historically been a stale role, not value).
  * Model probability >= the break-even of the quoted price, i.e. a positive-EV bet on the
    model's own numbers.
  * Depth chart order <= 2 when known (weekly_picks._MAX_DEPTH). Games under way skipped.
  * One entry per (player, stat, line, game), at the first run that sees it qualify.
  * Graded on the box score: over wins if actual > line; a push is void.

The sample floor of 8 comes from weekly_picks._MIN_RECEIVER_SAMPLE; it was chosen on
2026-10-18 after seeing that day's list (4-5 game samples near the top), not its results.

File: edge_log-NFL.json (written by the cloud runner).
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import statistics

import form_signal as _fs

_DIR = pathlib.Path(__file__).parent
_MIN_EDGE, _MAX_EDGE = 0.04, 0.18
_MIN_SAMPLE = 8


def _path(sport: str) -> pathlib.Path:
    return _DIR / f"edge_log-{sport.upper()}.json"


def _load(sport: str) -> dict:
    p = _path(sport)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(sport: str, data: dict) -> None:
    _path(sport).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def _breakeven(american: int) -> float:
    return 1 / _fs._decimal(american)


def log(sport: str = "nfl") -> int:
    """Record every FanDuel over that qualifies under the rule right now. Returns new rows."""
    import daily_parlay_gen as dg
    import weekly_picks as wp
    sport = sport.lower()
    if sport != "nfl":
        raise ValueError("edge_signal is NFL-only")
    raw = dg.fetch_fanduel("nfl")
    if raw is None or raw.empty:
        return 0
    legs = dg.score_legs(raw, dg.load_cal("NFL"), dg.NFL_STAT_TYPES, dg.nfl_hit_rate,
                         min_sample=dg.MIN_SAMPLE.get("NFL", 3))
    depths = wp._depth_lookup()
    data = _load(sport)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    added = 0
    for lg in legs:
        if str(lg.get("side", "over")).lower() != "over" or dg._has_started(lg.get("start_time")):
            continue
        model, fair = lg.get("hit_rate"), lg.get("implied_prob")
        if model is None or not fair or int(lg.get("sample_n") or 0) < _MIN_SAMPLE:
            continue
        model, fair, odds = float(model), float(fair), int(lg["american_odds"])
        edge = model - fair
        if not (_MIN_EDGE <= edge <= _MAX_EDGE) or model < _breakeven(odds):
            continue
        depth = depths.get(lg["player_name"])
        if depth is not None and depth > wp._MAX_DEPTH:
            continue
        day = _fs._game_day(lg.get("start_time"))
        if day is None:
            continue
        key = "|".join([_fs._norm(lg["player_name"]), lg["stat_type"], f"{float(lg['line_score']):g}",
                        str(lg.get("game_id") or lg.get("game_label"))])
        if key in data:
            continue
        data[key] = {"player": lg["player_name"], "stat": lg["stat_type"],
                     "line": float(lg["line_score"]), "side": "over", "american_odds": odds,
                     "model_prob": round(model, 4), "fair_prob": round(fair, 4),
                     "edge": round(edge, 4), "sample_n": int(lg["sample_n"]), "depth": depth,
                     "game": lg.get("game_label", ""), "start_time": lg.get("start_time", ""),
                     "game_day": day.isoformat(), "logged_at": now,
                     "outcome": None, "actual": None}
        added += 1
    if added:
        _save(sport, data)
    return added


def grade(sport: str = "nfl") -> int:
    """Fill in outcomes for logged rows whose game is in the box scores. Returns rows graded."""
    data = _load(sport)
    pending = {k: v for k, v in data.items() if v.get("outcome") is None}
    if not pending:
        return 0
    today = datetime.date.today()
    actuals, week_of_day = _fs._nfl_actuals()
    n = 0
    for row in pending.values():
        day = datetime.date.fromisoformat(row["game_day"])
        if day >= today:
            continue
        wk = week_of_day.get(row["game_day"])
        vals = actuals.get((_fs._norm(row["player"]), wk)) if wk else None
        actual = vals.get(row["stat"]) if vals else None
        if actual is None:
            # Did not play, or the stats are not in yet: void after three days, as form_signal.
            if (today - day).days >= 3:
                row["outcome"] = "void"
                n += 1
            continue
        row["actual"] = actual
        row["outcome"] = "void" if actual == row["line"] else ("win" if actual > row["line"] else "loss")
        n += 1
    if n:
        _save(sport, data)
    return n


def run(sport: str = "nfl") -> dict:
    """Grade what has finished, then log what qualifies now."""
    return {"graded": grade(sport), "logged": log(sport)}


def record(sport: str = "nfl") -> dict:
    """The test's running result, one unit per bet at the odds that were quoted."""
    data = _load(sport)
    rows = [r for r in data.values() if r.get("outcome") in ("win", "loss")]
    out = {"n": len(rows), "pending": sum(1 for r in data.values() if r.get("outcome") is None)}
    if not rows:
        return out
    rets = [(_fs._decimal(int(r["american_odds"])) - 1) if r["outcome"] == "win" else -1.0
            for r in rows]
    mean = sum(rets) / len(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    wins = sum(1 for r in rows if r["outcome"] == "win")
    out.update({"wins": wins, "win_pct": round(100 * wins / len(rows), 1),
                "breakeven_pct": round(100 * sum(_breakeven(int(r["american_odds"])) for r in rows)
                                       / len(rows), 1),
                "expected_pct": round(100 * sum(r["model_prob"] for r in rows) / len(rows), 1),
                "roi_pct": round(100 * mean, 1),
                "roi_se_pct": round(100 * sd / math.sqrt(len(rets)), 1),
                "units": round(sum(rets), 2)})
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("nfl", run("nfl"), record("nfl"))
