"""
td_market.py — every FanDuel NFL anytime-TD price, saved and graded, so the TD board can be
tested against the market it quotes.

Why
---
On 2026-10-31 the defence-vs-role split was tested as a veto on the Sunday parlay board,
against 1,251 graded legs at their FanDuel prices. The TD board could not be tested that
way: only its own 92 picks had ever been saved. A check without prices (824 RB/WR/TE games,
Weeks 4-8, prior weeks only) found the defence's TD rate against a role adds a little to the
player's own rate (Brier +0.3%, coefficient 0.10, 90% interval 0.01 to 0.18) and nothing at
the veto's -25% cut (scored 21.3% vs 22.2% expected, +/- 2.1). Whether that little survives
the price is the question this log is for.

What is saved
-------------
  * One row per player per game: the first price seen and the last before kickoff.
  * Graded from nflverse weekly stats once the week is in: scored = a rushing or receiving
    TD. FanDuel also pays return and fumble-recovery scores, which this misses; for skill
    players they are rare. A player with no stat row once his game's week is in is void.

File: td_market_log-NFL.json (cloud runner).
"""
from __future__ import annotations

import datetime
import json
import pathlib

_PATH = pathlib.Path(__file__).parent / "td_market_log-NFL.json"


def _load() -> dict:
    if not _PATH.exists():
        return {}
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    _PATH.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def log() -> int:
    """Record the current price of every anytime-TD runner whose game has not started."""
    import daily_parlay_gen as dg
    import nfl_analysis as nfl
    import parlay_model as pm

    raw = pm.fetch_fanduel_anytime_td()
    if raw is None or raw.empty:
        return 0
    data, now, n = _load(), datetime.datetime.now().isoformat(timespec="seconds"), 0
    for _, r in raw.iterrows():
        if dg._has_started(r["start_time"]) or not r.get("american_odds"):
            continue
        k = f"{nfl._name_key(r['player_name'])}|{r['game_id']}"
        row = data.get(k)
        price = {"american_odds": int(r["american_odds"]), "implied_prob": round(float(r["implied_prob"]), 4),
                 "seen_at": now}
        if row is None:
            row = data[k] = {"player": r["player_name"], "game": r["game_label"], "game_id": str(r["game_id"]),
                             "start_time": r["start_time"], "first": price,
                             "outcome": None, "actual": None}
        row["last"] = price
        row["start_time"] = r["start_time"]
        n += 1
    _save(data)
    return n


def grade() -> int:
    """Grade rows whose game's week is in nflverse's weekly stats."""
    import nfl_analysis as nfl
    import nfl_game_model as gm
    import pandas as pd

    data = _load()
    todo = {k: v for k, v in data.items() if v.get("outcome") is None}
    if not todo:
        return 0
    now = pd.Timestamp.now(tz="UTC")
    todo = {k: v for k, v in todo.items()
            if pd.Timestamp(v["start_time"]) < now - pd.Timedelta(hours=6)}
    if not todo:
        return 0
    sch = gm.schedule()
    done = 0
    for season in sorted({pd.Timestamp(v["start_time"]).year if pd.Timestamp(v["start_time"]).month > 2
                          else pd.Timestamp(v["start_time"]).year - 1 for v in todo.values()}):
        wk = nfl._load_weekly(season)
        if wk is None or wk.empty:
            continue
        s = sch[sch["season"] == season]
        day = {}
        for w, d, h, a in zip(s["week"], s["gameday"], s["home_team"], s["away_team"]):
            day[(h, int(w))] = day[(a, int(w))] = str(d)
        wk = wk.assign(_day=[day.get((t, int(w))) for t, w in zip(wk["team"], wk["week"])],
                       _key=wk["player_display_name"].map(nfl._name_key))
        days_in = set(wk["_day"].dropna())
        for k, v in todo.items():
            # gameday is the US date; a night game's UTC start falls on the next day
            local = str((pd.Timestamp(v["start_time"]) - pd.Timedelta(hours=5)).date())
            if local not in days_in:
                continue                        # that week's stats aren't in yet
            me = wk[(wk["_key"] == k.split("|")[0]) & (wk["_day"] == local)]
            if me.empty:
                v["outcome"] = "void"
            else:
                tds = int(me["rushing_tds"].fillna(0).sum() + me["receiving_tds"].fillna(0).sum())
                v["actual"], v["outcome"] = tds, "win" if tds >= 1 else "loss"
            done += 1
    if done:
        _save(data)
    return done


def run() -> dict:
    graded = grade()
    return {"graded": graded, "logged": log()}


def record() -> dict:
    rows = [v for v in _load().values() if v.get("outcome") in ("win", "loss")]
    if not rows:
        return {"n": 0, "pending": sum(1 for v in _load().values() if v.get("outcome") is None)}
    hit = sum(v["outcome"] == "win" for v in rows) / len(rows)
    imp = sum(v["last"]["implied_prob"] for v in rows) / len(rows)
    return {"n": len(rows), "scored_pct": round(100 * hit, 1), "implied_pct": round(100 * imp, 1),
            "pending": sum(1 for v in _load().values() if v.get("outcome") is None)}


if __name__ == "__main__":
    print(run(), record())
