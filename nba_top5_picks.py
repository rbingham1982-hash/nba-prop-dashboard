"""
nba_top5_picks.py — tonight's five NBA picks for the daily "Top 5" Short, saved before tip-off
and graded from the box score the next morning, so every Short can open on the running record.

The five are nba_daily_post.research_legs: the night's best-supported props, where our model
and the market agree on the side, the player has cleared the line in at least half his games
this season (and last, with 10+ games), and the opponent's defence against his role does not
work against the leg, at most two per game, each with a note for and a note against.

That is a selection rule, not a demonstrated edge: the props model has not beaten the closing
line, and the Short says so. What it does guarantee is that the record is real — a day's five
are frozen the first time they are logged (before the first tip; never after a game starts),
and grading only fills in outcomes.

File: ~/.konjure/nba_top5_log.json (the video jobs run on the owner's PC).

    python nba_top5_picks.py log [YYYY-MM-DD]     # freeze tonight's five
    python nba_top5_picks.py grade                # grade every finished night
    python nba_top5_picks.py record
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys

LOG = pathlib.Path.home() / ".konjure" / "nba_top5_log.json"
N = 5


def _load() -> dict:
    return json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {}


def _save(d: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")


def log(day: datetime.date | None = None) -> list:
    """The day's five, frozen on first call. [] if the slate has too few or has started."""
    import daily_parlay_gen as dg
    import nba_daily_post as ndp
    day = day or datetime.date.today()
    d = _load()
    if day.isoformat() in d:
        return d[day.isoformat()]["picks"]
    legs = ndp.research_legs(day, N)
    if len(legs) < N or any(dg._has_started(l["start_time"]) for l in legs):
        return []
    picks = [{"player": l["player"], "stat_type": l["stat_type"], "line": float(l["line"]), "side": l["side"],
              "american_odds": int(l["american_odds"]), "game": l["game"], "start_time": l["start_time"],
              "team": l["team"], "opp": l["opp"], "model_prob": round(float(l["model_prob"]), 4),
              "market_prob": round(float(l["implied_prob"]), 4), "title": l["title"],
              "game_hdr": l["game_hdr"], "notes": l["notes"], "outcome": None, "actual": None}
             for l in sorted(legs, key=lambda l: -float(l["model_prob"]))]
    d[day.isoformat()] = {"logged_at": datetime.datetime.now().isoformat(timespec="seconds"), "picks": picks}
    _save(d)
    return picks


def grade(today: datetime.date | None = None) -> int:
    """Fill in outcomes for finished nights from the box score. Returns legs graded."""
    import parlay_builder as pb
    today = today or datetime.date.today()
    d, box, n = _load(), pb._Box(), 0
    for day, e in d.items():
        if datetime.date.fromisoformat(day) >= today:
            continue
        for p in e["picks"]:
            if p.get("outcome"):
                continue
            actual = box.actual({"sport": "nba", "player_name": p["player"], "stat_type": p["stat_type"],
                                 "start_time": p["start_time"]})
            if actual is None:
                if (today - datetime.date.fromisoformat(day)).days >= 3:
                    p["outcome"] = "void"                 # he did not play
                    n += 1
                continue
            p["actual"] = actual
            if actual == p["line"]:
                p["outcome"] = "push"
            else:
                p["outcome"] = "win" if (actual > p["line"]) == (p["side"] == "over") else "loss"
            n += 1
    if n:
        _save(d)
    return n


def record() -> dict:
    d = _load()
    legs = [p for e in d.values() for p in e["picks"] if p.get("outcome") in ("win", "loss")]
    w = sum(p["outcome"] == "win" for p in legs)
    last = None
    for day in sorted(d, reverse=True):
        done = [p for p in d[day]["picks"] if p.get("outcome") in ("win", "loss")]
        if done and len(done) == sum(1 for p in d[day]["picks"] if p.get("outcome") != "void"):
            last = (day, sum(p["outcome"] == "win" for p in done), len(done))
            break
    return {"wins": w, "losses": len(legs) - w, "n": len(legs),
            "pct": round(100 * w / len(legs), 1) if legs else None, "last": last}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "record"
    if cmd == "log":
        day = datetime.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None
        for p in log(day):
            print(p["title"], p["american_odds"])
    elif cmd == "grade":
        print("graded", grade())
    else:
        print(record())
