"""
teaser_signal.py — a live, pre-registered test of one rule: six-point teaser legs on NFL
underdogs of +1.5 to +2.5, teased through 3 and 7 to +7.5 to +8.5.

Why this rule, and why it is a test
-----------------------------------
On 2026-10-25 every season-to-date team stat was checked against the closing line on
nflverse 2010-2025 (3,066 games, discovery 2010-2019, holdout 2020-2025) and none predicted
the cover in both periods. The one thing near break-even works through key numbers rather
than prediction: 14.5% of NFL games end on exactly 3 and 8.5% on exactly 7, so six points
across both is worth more than six points anywhere else. At the closing spread:

    dogs +1.5 to +2.5 teased 6     2010-2019  75.0% of 272 legs    2020-2025  77.0% of 282
    favs -7.5 to -8.5 teased 6     2010-2019  76.4% of 144          2020-2025  71.5% of 137
    every other leg                           69.0%                            69.5%

A two-team teaser at -120 needs 73.9% a leg; at -130, 75.2%; at -140, 76.4%. The dog side
held up out of sample, but about 280 legs a period cannot tell 77% from 74%, and books have
raised teaser prices since these strategies became known. So the rule is recorded going
forward at FanDuel's real lines and the record settles it. Favourites are not in the rule:
their side fell below break-even in the holdout.

THE RULE IS FIXED. Do not tune it on its own results.

  * NFL regular season, FanDuel's game spread (MATCH_HANDICAP_(2-WAY)).
  * A leg is a team getting +1.5, +2 or +2.5 at the LAST line seen before kickoff. The
    first line seen is kept too, to show what betting early does.
  * The leg wins if its margin plus the line plus 6 is above zero; exactly zero (a +2 dog
    losing by 8) is a push and drops out. Graded from ESPN's final score.
  * Return is reported as a two-team teaser at -120 would pay: p^2 x 1.833 - 1 per unit at
    the leg win rate p, with its standard error. Pairing legs into tickets would add
    nothing but which-games-got-paired noise.

File: teaser_log-NFL.json (cloud runner).
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib

import favorite_signal as fs

_PATH = pathlib.Path(__file__).parent / "teaser_log-NFL.json"
TEASE = 6.0
DOG_LINES = (1.5, 2.0, 2.5)            # the rule
_TRACK = (1.0, 3.5)                    # tracked from here, so a move into the range is seen
TICKET_PAYOUT = 1 + 100 / 120          # two-team, six-point teaser at -120
BREAKEVEN = 1 / math.sqrt(TICKET_PAYOUT)


def _load() -> dict:
    if not _PATH.exists():
        return {}
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    _PATH.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def _spreads() -> list:
    """FanDuel NFL game spreads: one row per team with a positive (underdog) handicap."""
    import requests
    import parlay_model as pm
    r = requests.get(f"{pm.FD_BASE}/content-managed-page",
                     params={"page": "CUSTOM", "customPageId": pm.FD_PAGE_ID["nfl"], "_ak": pm.FD_AK,
                             "timezone": "America/New_York"}, headers=pm.FD_HEADERS, timeout=20)
    events = {i: e for i, e in r.json().get("attachments", {}).get("events", {}).items()
              if " @ " in (e.get("name") or "")}
    out = []
    for ev_id, ev in events.items():
        try:
            away, home = [s.strip() for s in ev["name"].split(" @ ", 1)]
            m = requests.get(f"{pm.FD_BASE}/event-page", params={"eventId": ev_id, "_ak": pm.FD_AK},
                             headers=pm.FD_HEADERS, timeout=25).json().get("attachments", {}).get("markets", {})
        except Exception:
            continue
        for mk in m.values():
            if mk.get("marketType") != "MATCH_HANDICAP_(2-WAY)":
                continue
            for run in mk.get("runners", []):
                h = run.get("handicap")
                name = (run.get("runnerName") or "").strip()
                odds = ((run.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt")
                if h is None or name not in (home, away) or float(h) <= 0:
                    continue
                out.append({"team": name, "home": name == home, "home_name": home, "away_name": away,
                            "line": float(h), "odds": odds, "start_time": ev.get("openDate", "")})
            break
    return out


def log() -> int:
    """Record or refresh every not-yet-started underdog line in the tracked range."""
    import daily_parlay_gen as dg
    data = _load()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    n = 0
    for s in _spreads():
        if dg._has_started(s["start_time"]):
            continue
        key = f"{s['away_name']} @ {s['home_name']}|{s['start_time'][:16]}"
        row = data.get(key)
        if row is None:
            if not _TRACK[0] <= s["line"] <= _TRACK[1]:
                continue
            row = {"dog": s["team"], "dog_home": s["home"], "home_name": s["home_name"],
                   "away_name": s["away_name"], "start_time": s["start_time"], "first_seen": now,
                   "first_line": s["line"], "outcome": None}
            data[key] = row
        if row.get("outcome") is not None:
            continue
        if s["team"] != row["dog"]:
            # The favourite flipped: the other side is now the underdog. Follow the market.
            row["dog"], row["dog_home"] = s["team"], s["home"]
        row["last_line"], row["last_odds"], row["last_seen"] = s["line"], s["odds"], now
        n += 1
    if n:
        _save(data)
    return n


def grade() -> int:
    data = _load()
    now = datetime.datetime.now(datetime.timezone.utc)
    by_day: dict = {}
    for k, row in data.items():
        if row.get("outcome") is not None:
            continue
        st = datetime.datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
        if now - st < datetime.timedelta(hours=4):
            continue
        by_day.setdefault((st - datetime.timedelta(hours=8)).date(), []).append((k, row, st))
    n = 0
    for day, rows in by_day.items():
        finals = fs._espn_finals("nfl", day)
        for k, row, st in rows:
            f = next((f for f in finals if f["home"] == fs._norm(row["home_name"])
                      and f["away"] == fs._norm(row["away_name"])), None)
            if f is None:
                if now - st > datetime.timedelta(days=3):
                    row["outcome"] = "void"
                    n += 1
                continue
            if f["season_type"] != 2:
                row["outcome"] = "not regular season"
                n += 1
                continue
            margin = (f["home_score"] - f["away_score"]) * (1 if row["dog_home"] else -1)
            row["margin"] = margin
            for which in ("last", "first"):
                line = row.get(f"{which}_line")
                if line is None:
                    continue
                c = margin + line + TEASE
                row[f"{which}_result"] = "push" if c == 0 else ("win" if c > 0 else "loss")
            row["outcome"] = "graded"
            n += 1
    if n:
        _save(data)
    return n


def run() -> dict:
    return {"graded": grade(), "tracked": log()}


def _summary(results: list) -> dict:
    w = sum(r == "win" for r in results)
    l = sum(r == "loss" for r in results)
    if not w + l:
        return {"n": 0}
    p = w / (w + l)
    se_p = math.sqrt(p * (1 - p) / (w + l))
    return {"n": w + l, "record": f"{w}-{l}", "win_pct": round(100 * p, 1),
            "breakeven_pct": round(100 * BREAKEVEN, 1),
            "roi_pct": round(100 * (p * p * TICKET_PAYOUT - 1), 1),
            "roi_se_pct": round(100 * 2 * p * TICKET_PAYOUT * se_p, 1)}


def record() -> dict:
    """Legs that qualified at the last line (+1.5 to +2.5), and the same games at the first line."""
    rows = [r for r in _load().values() if r.get("outcome") == "graded" and r.get("last_line") in DOG_LINES]
    pending = sum(1 for r in _load().values() if r.get("outcome") is None and r.get("last_line") in DOG_LINES)
    return {"at_last_line": _summary([r["last_result"] for r in rows]),
            "at_first_line": _summary([r.get("first_result") for r in rows if r.get("first_result")]),
            "pending": pending}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(run(), record())
