"""
favorite_signal.py — a live, pre-registered test of one betting rule: back the moneyline
favourite when FanDuel prices it at 70% or more.

Why this rule, and why it is a test
-----------------------------------
A scan of the game markets on 2026-10-11 found one pattern that recurred across sports:
heavy favourites won more often than their price said (the favourite-longshot bias, in
which long shots are overpriced). At the odds FanDuel actually quoted, from the game log:

    MLB 2026,  favourites at 70%+:  n=25, won 96%, ROI +18.9% +/- 5.5
    WNBA 2026, favourites at 70%+:  n=33, won 94%, ROI +10.4% +/- 5.3

But the NFL, at closing moneylines, does not agree year to year: 80%+ favourites returned
+7.5% (2024) and +3.2% (2025) yet -3.5% to -7.5% in 2021-2023, and 70%+ lost money in every
season 2023-2025 and so far in 2026. The samples are small and the window is one half of
two seasons. So this module records the rule going forward at real prices and lets the
record settle it.

THE RULE IS FIXED. Do not tune it on its own results.

  * FanDuel game moneylines, NFL / NBA / MLB / WNBA.
  * The favourite's de-vigged probability, at the LAST price seen before the start, is
    0.70 or more. That price is the bet (the 2026 numbers above were measured there).
  * The first price seen is kept as a secondary record, to show what betting early costs.
  * One unit per qualifying game. Graded from ESPN's final score; a tie is void.
  * Regular season and playoffs only: a preseason game is set aside, not counted.

Files: favorite_log-{NFL,MLB}.json (cloud runner), favorite_log-{NBA,WNBA}.json (home).
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import statistics
import unicodedata

_DIR = pathlib.Path(__file__).parent
QUALIFY = 0.70        # the rule
_TRACK = 0.65         # games are tracked from here so a late move to 70% is still seen
_ESPN = {"nfl": "football/nfl", "nba": "basketball/nba", "mlb": "baseball/mlb",
         "wnba": "basketball/wnba"}


def _path(sport: str) -> pathlib.Path:
    return _DIR / f"favorite_log-{sport.upper()}.json"


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
    return " ".join(s.replace(".", "").split())


def _decimal(american: int) -> float:
    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def _price(row) -> dict:
    p_home = float(row["market_home_prob"])
    home_fav = p_home >= 0.5
    return {"fav": "home" if home_fav else "away",
            "fav_prob": round(p_home if home_fav else 1 - p_home, 4),
            "odds": int(row["home_odds"] if home_fav else row["away_odds"]),
            "home_odds": int(row["home_odds"]), "away_odds": int(row["away_odds"])}


def log(sport: str) -> int:
    """Record or refresh every not-yet-started game whose favourite is at 65%+. Returns
    the number of games written."""
    import parlay_model as pm
    import daily_parlay_gen as dg
    sport = sport.lower()
    df = pm.fetch_moneylines(sport)
    if df is None or df.empty:
        return 0
    data = _load(sport)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    n = 0
    for _, r in df.iterrows():
        if dg._has_started(r.get("start_time")):
            continue                     # live odds are not a pre-game price
        pr = _price(r)
        key = f"{r['game_label']}|{str(r['start_time'])[:16]}"
        row = data.get(key)
        if row is None:
            if pr["fav_prob"] < _TRACK:
                continue
            row = {"label": r["game_label"], "home_name": r["home_name"], "away_name": r["away_name"],
                   "start_time": str(r["start_time"]), "first_seen": now, "first": pr,
                   "outcome": None, "winner": None}
            data[key] = row
        if row.get("outcome") is not None:
            continue
        row["last"], row["last_seen"] = pr, now
        n += 1
    if n:
        _save(sport, data)
    return n


def _espn_finals(sport: str, day: datetime.date) -> list:
    import requests
    url = f"https://site.api.espn.com/apis/site/v2/sports/{_ESPN[sport]}/scoreboard"
    try:
        r = requests.get(url, params={"dates": day.strftime("%Y%m%d"), "limit": 300}, timeout=20)
        events = r.json().get("events", []) if r.status_code == 200 else []
    except Exception:
        return []
    out = []
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
            continue
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            continue
        try:
            hs, as_ = float(teams["home"]["score"]), float(teams["away"]["score"])
        except Exception:
            continue
        out.append({"season_type": int((ev.get("season") or {}).get("type") or 0),
                    "home": _norm(teams["home"]["team"].get("displayName", "")),
                    "away": _norm(teams["away"]["team"].get("displayName", "")),
                    "start": ev.get("date", ""), "home_score": hs, "away_score": as_})
    return out


def grade(sport: str) -> int:
    """Settle finished games from ESPN. Returns games graded."""
    sport = sport.lower()
    data = _load(sport)
    now = datetime.datetime.now(datetime.timezone.utc)
    by_day: dict = {}
    for k, row in data.items():
        if row.get("outcome") is not None:
            continue
        try:
            st = datetime.datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        if now - st < datetime.timedelta(hours=4):
            continue                     # not over yet
        by_day.setdefault((st - datetime.timedelta(hours=8)).date(), []).append((k, row, st))
    n = 0
    for day, rows in by_day.items():
        finals = _espn_finals(sport, day)
        for k, row, st in rows:
            cands = [f for f in finals if f["home"] == _norm(row["home_name"]) and f["away"] == _norm(row["away_name"])]
            if not cands:
                if now - st > datetime.timedelta(days=3):
                    row["outcome"] = "void"          # postponed, or a name we cannot match
                    n += 1
                continue
            # A doubleheader lists the same teams twice; the nearest start time is this one.
            f = min(cands, key=lambda c: abs((datetime.datetime.fromisoformat(c["start"].replace("Z", "+00:00")) - st).total_seconds())
                    if c["start"] else 0)
            if f["season_type"] == 1:
                row["outcome"] = "preseason"         # outside the rule: regular season and playoffs only
            elif f["home_score"] == f["away_score"]:
                row["outcome"] = "void"
            else:
                row["winner"] = "home" if f["home_score"] > f["away_score"] else "away"
                row["outcome"] = "graded"
            n += 1
    if n:
        _save(sport, data)
    return n


def run(sport: str) -> dict:
    return {"graded": grade(sport), "tracked": log(sport)}


def _summary(bets: list) -> dict:
    if not bets:
        return {"n": 0}
    rets = [(_decimal(o) - 1) if won else -1.0 for o, won in bets]
    mean = sum(rets) / len(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    return {"n": len(rets), "won_pct": round(100 * sum(w for _, w in bets) / len(bets), 1),
            "breakeven_pct": round(100 * sum(1 / _decimal(o) for o, _ in bets) / len(bets), 1),
            "roi_pct": round(100 * mean, 1), "roi_se_pct": round(100 * sd / math.sqrt(len(rets)), 1),
            "units": round(sum(rets), 2)}


def record(sport: str) -> dict:
    """The rule's result: primary at the last pre-game price, secondary at the first."""
    rows = [r for r in _load(sport).values() if r.get("outcome") == "graded" and r.get("last")]
    last, first = [], []
    for r in rows:
        if r["last"]["fav_prob"] < QUALIFY:
            continue
        won = r["winner"] == r["last"]["fav"]
        last.append((r["last"]["odds"], won))
        f = r["first"]
        f_odds = f["home_odds"] if r["last"]["fav"] == "home" else f["away_odds"]
        first.append((f_odds, won))
    pending = sum(1 for r in _load(sport).values() if r.get("outcome") is None
                  and (r.get("last") or {}).get("fav_prob", 0) >= QUALIFY)
    return {"at_last_price": _summary(last), "at_first_price": _summary(first), "pending": pending}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for sp in (sys.argv[1:] or ["nfl"]):
        print(sp, run(sp), record(sp))
