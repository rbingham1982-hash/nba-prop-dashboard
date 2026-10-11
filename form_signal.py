"""
form_signal.py — a live, pre-registered test of one betting rule: take the UNDER on a
FanDuel player prop when the player's recent form sits below the line.

Why this rule, and why it is a test rather than a pick list
-----------------------------------------------------------
By 2026-10-11 every model in this project had been measured against FanDuel's lines and
none beat them (parlay_tracker.get_market_blend, nfl_prop_eval). Two findings pointed at
one place the price might be soft:

  * FanDuel loads extra margin onto overs: tracking legs the model never chose hit 2.5
    points below their de-vigged over price in WNBA (n=3,799) and NFL (n=748).
  * A plain recent-form rule beat the de-vigged price on the under side in two sports.
    NFL, 785 props: median of the last 5 games below the line -> unders hit 56.6% against
    51.4% (+5.2 +/- 2.4), positive in weeks 1-3 and again in 4-5. WNBA, a sport the rule
    was never fit on, 1,983 props: 55.1% against 51.3% (+3.7 +/- 1.1). The over half did
    not hold up out of sample (WNBA -1.1), so it is not part of the rule.

What the logs could NOT say is whether it pays at the odds actually offered: most of those
props were logged as overs, so the under's own price was never stored, and beating a
de-vigged probability by 2-4 points is roughly what the margin takes back. This module
stores the real under odds at the moment a prop qualifies and grades the result, so the
record answers that question in money, not in probability points.

THE RULE IS FIXED. Do not tune it on its own results — a threshold picked after looking
is how an earlier wind finding went from 64.5% to 56.7% out of sample.

  * FanDuel two-way props for the sport's stat list; the under must be quoted.
  * Median of the player's last 5 games (both seasons counted, most recent first) < line.
  * At least 5 prior games. Games already under way are skipped.
  * One entry per (player, stat, line, game), at the first run that sees it qualify.
  * Graded on the box score: under wins if actual < line; a push (actual == line) is void.

Files: form_log-NFL.json (written by the cloud runner), form_log-NBA.json (home runner).
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import statistics
import unicodedata

_DIR = pathlib.Path(__file__).parent
_MIN_GAMES = 5
_SUFFIXES = (" jr", " sr", " ii", " iii", " iv", " v")

_NBA_STATS = {"Points": ("PTS",), "Rebounds": ("REB",), "Assists": ("AST",), "3-PT Made": ("FG3M",),
              "Pts+Rebs+Asts": ("PTS", "REB", "AST"), "Pts+Rebs": ("PTS", "REB"),
              "Pts+Asts": ("PTS", "AST"), "Rebs+Asts": ("REB", "AST")}


def _path(sport: str) -> pathlib.Path:
    return _DIR / f"form_log-{sport.upper()}.json"


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


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "").replace(",", "").strip()
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s


def _game_day(start_time: str):
    """Local game date from a UTC start time (US evening games land on the next UTC day)."""
    try:
        t = datetime.datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
    except Exception:
        return None
    return (t - datetime.timedelta(hours=8)).date()


# ── per-sport history: {normalized player: [(date or week, {stat: value}), ...]} ──────────

def _nfl_history():
    import nfl_analysis as nfl
    play = nfl.season_for_date(datetime.date.today())
    hist = nfl.history_frame(play)
    if hist is None:
        _, hist = nfl.get_season(play - 1)
    cols = {lab: c for lab, (c, _) in nfl.PROP_STATS.items()}
    out: dict = {}
    for _, r in hist.sort_values("week").iterrows():
        key = _norm(r.get("player_display_name", ""))
        if not key:
            continue
        out.setdefault(key, []).append(
            (int(r["week"]), int(r.get("season") or play),
             {lab: float(r.get(c, 0) or 0) for lab, c in cols.items()}))
    return out, play


def _nba_history():
    import pandas as pd
    import nba_grades
    import parlay_model as pm
    frames = []
    for season in reversed(pm.nba_season_strings()):          # previous season first
        for st in ("Regular Season", "Playoffs"):
            try:
                frames.append(nba_grades.season_log(season, st))
            except Exception:
                continue
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True).sort_values("date")
    out: dict = {}
    for _, r in df.iterrows():
        vals = {lab: float(sum(float(r.get(c, 0) or 0) for c in cs)) for lab, cs in _NBA_STATS.items()}
        out.setdefault(_norm(r["PLAYER_NAME"]), []).append((r["date"].date(), vals))
    return out


def _prior_values(sport: str, hist, player: str, stat: str, day) -> list:
    rows = hist.get(_norm(player)) or []
    if sport == "nba":
        return [v[stat] for d, v in rows if d < day and stat in v]
    # NFL rows are (week, season, vals); every game already in the frame was played
    # before a prop that is still on the board, since unplayed weeks have no stats.
    return [v[stat] for _w, _s, v in rows if stat in v]


# ── logging ───────────────────────────────────────────────────────────────────────────────

def log(sport: str) -> int:
    """Record every FanDuel prop that qualifies under the rule right now. Returns new rows."""
    import parlay_model as pm
    import daily_parlay_gen as dg
    sport = sport.lower()
    raw = pm.fetch_fanduel(sport)
    if raw is None or raw.empty:
        return 0
    if sport == "nfl":
        stats = set(dg.NFL_STAT_TYPES)
        hist, _play = _nfl_history()
    elif sport == "nba":
        stats = set(_NBA_STATS)
        hist = _nba_history()
    else:
        raise ValueError(f"form_signal has no history source for {sport}")
    unders = raw[(raw["side"] == "under") & raw["stat_type"].isin(stats)]
    data = _load(sport)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    added = 0
    for _, r in unders.iterrows():
        if dg._has_started(r.get("start_time")):
            continue
        day = _game_day(r.get("start_time"))
        if day is None:
            continue
        key = "|".join([_norm(r["player_name"]), r["stat_type"], f"{float(r['line_score']):g}",
                        str(r.get("game_id") or r.get("game_label"))])
        if key in data:
            continue
        vals = _prior_values(sport, hist, r["player_name"], r["stat_type"], day)
        if len(vals) < _MIN_GAMES:
            continue
        med5 = statistics.median(vals[-5:])
        line = float(r["line_score"])
        if not med5 < line:
            continue
        data[key] = {"player": r["player_name"], "stat": r["stat_type"], "line": line,
                     "side": "under", "american_odds": int(r["american_odds"]),
                     "fair_prob": round(float(r["implied_prob"]), 4), "med5": round(med5, 2),
                     "last5": [round(v, 1) for v in vals[-5:]], "game": str(r.get("game_label", "")),
                     "start_time": str(r.get("start_time", "")), "game_day": day.isoformat(),
                     "logged_at": now, "outcome": None, "actual": None}
        added += 1
    if added:
        _save(sport, data)
    return added


# ── grading ───────────────────────────────────────────────────────────────────────────────

def _nfl_actuals():
    import nfl_analysis as nfl
    import nfl_game_model as gm
    play = nfl.season_for_date(datetime.date.today())
    cur = nfl._load_weekly(play)
    g = gm.schedule()
    g = g[g["season"] == play]
    week_of_day = {str(x["gameday"])[:10]: int(x["week"]) for _, x in g.iterrows()}
    cols = {lab: c for lab, (c, _) in nfl.PROP_STATS.items()}
    out = {}
    for _, r in cur.iterrows():
        out[(_norm(r.get("player_display_name", "")), int(r["week"]))] = \
            {lab: float(r.get(c, 0) or 0) for lab, c in cols.items()}
    return out, week_of_day


def grade(sport: str) -> int:
    """Fill in outcomes for logged rows whose game is in the box scores. Returns rows graded."""
    sport = sport.lower()
    data = _load(sport)
    pending = {k: v for k, v in data.items() if v.get("outcome") is None}
    if not pending:
        return 0
    today = datetime.date.today()
    if sport == "nfl":
        actuals, week_of_day = _nfl_actuals()
    else:
        hist = _nba_history()
    n = 0
    for k, row in pending.items():
        day = datetime.date.fromisoformat(row["game_day"])
        if day >= today:
            continue
        if sport == "nfl":
            wk = week_of_day.get(row["game_day"])
            vals = actuals.get((_norm(row["player"]), wk)) if wk else None
            actual = vals.get(row["stat"]) if vals else None
        else:
            games = [v for d, v in hist.get(_norm(row["player"]), []) if d == day]
            actual = games[0].get(row["stat"]) if games else None
        if actual is None:
            # No box-score row: did not play, or the stats are not in yet. Void after
            # three days rather than counting a game he never played as a loss.
            if (today - day).days >= 3:
                row["outcome"] = "void"
                n += 1
            continue
        row["actual"] = actual
        row["outcome"] = "void" if actual == row["line"] else ("win" if actual < row["line"] else "loss")
        n += 1
    if n:
        _save(sport, data)
    return n


def run(sport: str) -> dict:
    """Grade what has finished, then log what qualifies now."""
    return {"graded": grade(sport), "logged": log(sport)}


def record(sport: str) -> dict:
    """The test's running result, in money at the odds that were quoted."""
    rows = [r for r in _load(sport).values() if r.get("outcome") in ("win", "loss")]
    out = {"n": len(rows), "pending": sum(1 for r in _load(sport).values() if r.get("outcome") is None)}
    if not rows:
        return out
    rets = []
    for r in rows:
        a = int(r["american_odds"])
        dec = 1 + (a / 100 if a > 0 else 100 / abs(a))
        rets.append(dec - 1 if r["outcome"] == "win" else -1.0)
    mean = sum(rets) / len(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    out.update({"wins": sum(1 for r in rows if r["outcome"] == "win"),
                "win_pct": round(100 * sum(1 for r in rows if r["outcome"] == "win") / len(rows), 1),
                "breakeven_pct": round(100 * sum(1 / (1 + (int(r["american_odds"]) / 100 if int(r["american_odds"]) > 0
                                                            else 100 / abs(int(r["american_odds"]))))
                                                 for r in rows) / len(rows), 1),
                "roi_pct": round(100 * mean, 1),
                "roi_se_pct": round(100 * sd / math.sqrt(len(rets)), 1)})
    return out


# ── the parlay ────────────────────────────────────────────────────────────────────────────
#
# Built from the logged legs by a rule fixed on 2026-10-11, before any of them graded:
#
#   * One parlay per sport per game day, PARLAY_LEGS legs.
#   * The legs are that day's logged form-unders with the highest de-vigged probability,
#     at most one per game (FanDuel does not combine props from one game outside a
#     same-game parlay, and two legs from one game are not independent).
#   * Only legs logged before the earliest selected game starts, so the ticket could
#     actually have been placed at the prices recorded.
#   * Payout is the product of the legs' decimal odds. A void leg drops out and the rest
#     pay on their own, which is how FanDuel settles a parlay with a void leg.
#
# Derived, never stored: the parlay is a pure function of form_log, so it cannot drift from
# the legs it is made of, and re-deriving it after a fix cannot rewrite its history.

PARLAY_LEGS = 3


def _decimal(american: int) -> float:
    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def _start(row):
    try:
        return datetime.datetime.fromisoformat(str(row["start_time"]).replace("Z", "+00:00"))
    except Exception:
        return None


def _logged_utc(row):
    t = datetime.datetime.fromisoformat(row["logged_at"])
    # logged_at is the runner's local wall clock (America/Chicago on both runners).
    return t.astimezone(datetime.timezone.utc) if t.tzinfo else t.astimezone().astimezone(datetime.timezone.utc)


def build_parlay(rows: list) -> list:
    """The rule's legs, from one day's logged rows. [] when the day cannot fill a ticket."""
    pool = sorted((r for r in rows if _start(r) is not None),
                  key=lambda r: (-float(r["fair_prob"]), r["logged_at"]))
    picked, games = [], set()
    for r in pool:
        if r["game"] in games:
            continue
        trial = picked + [r]
        first = min(_start(x) for x in trial)
        if any(_logged_utc(x) >= first for x in trial):
            continue                       # could not have been on one ticket in time
        picked, games = trial, games | {r["game"]}
        if len(picked) == PARLAY_LEGS:
            return picked
    return []


def parlays(sport: str) -> list:
    """Every day's rule parlay with its price and, once settled, its result."""
    by_day: dict = {}
    for r in _load(sport).values():
        by_day.setdefault(r["game_day"], []).append(r)
    out = []
    for day in sorted(by_day):
        legs = build_parlay(by_day[day])
        if not legs:
            continue
        live = [l for l in legs if l.get("outcome") != "void"]
        price = math.prod(_decimal(int(l["american_odds"])) for l in live) if live else 1.0
        fair = math.prod(float(l["fair_prob"]) for l in live) if live else 1.0
        outs = [l.get("outcome") for l in live]
        if any(o == "loss" for o in outs):
            result = "loss"
        elif not live:
            result = "void"
        elif all(o == "win" for o in outs):
            result = "win"
        else:
            result = None                  # still pending
        out.append({"day": day, "legs": legs, "decimal": round(price, 3),
                    "american": round((price - 1) * 100) if price >= 2 else round(-100 / (price - 1)),
                    "fair_prob": round(fair, 4), "breakeven": round(1 / price, 4),
                    "result": result})
    return out


def parlay_record(sport: str) -> dict:
    """The rule parlay's running result in money: one unit per ticket."""
    ps = parlays(sport)
    done = [p for p in ps if p["result"] in ("win", "loss")]
    out = {"tickets": len(done), "pending": sum(1 for p in ps if p["result"] is None)}
    if not done:
        return out
    rets = [(p["decimal"] - 1) if p["result"] == "win" else -1.0 for p in done]
    mean = sum(rets) / len(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    out.update({"hits": sum(1 for p in done if p["result"] == "win"),
                "roi_pct": round(100 * mean, 1),
                "roi_se_pct": round(100 * sd / math.sqrt(len(rets)), 1),
                "units": round(sum(rets), 2)})
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for sp in (sys.argv[1:] or ["nfl"]):
        print(sp, run(sp), record(sp))
