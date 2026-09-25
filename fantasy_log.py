"""
fantasy_log.py — what the fantasy boards recommended, and what those players then did.

The betting side has had a prediction log since July. The fantasy boards publish every
week, are the part of this project with a plausible edge — no bookmaker's hold, and the
competition is consensus — and had no record at all. This is that record, in the same shape
as weekly_picks: written once per week before the games, graded after, never rewritten.

Graded two ways, because they answer different questions:

    actual PPR    what the player scored that week
    beat the bar  whether he cleared the startable line at his position that week — the
                  Nth-best actual score, N being a 12-team league's starters. A waiver add
                  is only useful if it was startable, and "scored 9.4" means nothing
                  without the week's bar next to it.

The bar is the week's OWN, not a fixed number: 14 points is a good week for a tight end and
a poor one for a quarterback, and a week where everyone scored is not a week the board got
right.
"""
from __future__ import annotations

import datetime
import json
import pathlib

LOG = pathlib.Path(__file__).parent / "fantasy_log.json"

# A 12-team league's starters at each position: the line a recommendation has to clear to
# have been worth acting on.
_STARTERS = {"QB": 12, "RB": 24, "WR": 24, "TE": 12}


def _load() -> dict:
    if not LOG.exists():
        return {}
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    LOG.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _key(season: int, week: int) -> str:
    return f"{season}-{week:02d}"


def log_week(season: int, week: int, waivers=None, sleepers=None, overwrite: bool = False) -> dict:
    """
    Record one week's recommendations, once.

    Refuses to overwrite a week that already has rows, for the same reason weekly_picks
    does: a board regenerated on Friday is a different recommendation from the one that
    went out on Tuesday, and grading the second against the first week's outcome would be
    describing advice nobody was given.
    """
    data = _load()
    k = _key(season, week)
    if k in data and not overwrite:
        return data[k]
    recs = []
    for kind, rows in (("waiver", waivers or []), ("sleeper", sleepers or [])):
        for r in rows:
            recs.append({"kind": kind, "player": r.get("player"), "position": r.get("position"),
                         "team": r.get("team"), "proj": r.get("proj_points"),
                         "actual": None, "bar": None, "beat_bar": None, "outcome": None})
    if not recs:
        return {"season": season, "week": week, "recs": [], "graded": False}
    data[k] = {"season": season, "week": week,
               "generated": datetime.datetime.now().isoformat(timespec="seconds"),
               "recs": recs, "graded": False}
    _save(data)
    return data[k]


def _week_actuals(season: int, week: int):
    """(name key -> PPR for the week, position -> that week's startable bar), or (None, None)."""
    import nfl_analysis as nfl
    import nfl_grades as ng
    if week not in ng.completed_weeks(season):
        return None, None
    df = ng._frame(season)
    wk = df[df["week"] == week]
    if wk.empty:
        return None, None
    ppr = {}
    for name, pts in zip(wk["player_display_name"], wk["fantasy_points_ppr"].fillna(0.0)):
        if name:
            ppr[nfl._name_key(name)] = float(pts)
    bars = {}
    for pos, n in _STARTERS.items():
        vals = sorted(wk.loc[wk["position"] == pos, "fantasy_points_ppr"].fillna(0.0),
                      reverse=True)
        if len(vals) >= n:
            bars[pos] = float(vals[n - 1])
    return ppr, bars


def grade(season: int | None = None) -> dict:
    """Fill in outcomes for every logged week whose games are finished. Safe to re-run."""
    import nfl_analysis as nfl
    data = _load()
    filled = weeks = 0
    for k, entry in data.items():
        if season is not None and entry.get("season") != season:
            continue
        pending = [r for r in entry.get("recs", []) if not r.get("outcome")]
        if not pending:
            continue
        ppr, bars = _week_actuals(entry["season"], entry["week"])
        if ppr is None:
            continue
        for r in pending:
            key = nfl._name_key(r.get("player") or "")
            # Absent from the week's stats means he did not play — not a zero he earned, but
            # still a recommendation that returned nothing, so it grades as a miss with the
            # reason attached rather than as an average.
            if key not in ppr:
                r.update({"actual": None, "outcome": "dnp", "beat_bar": False})
            else:
                bar = bars.get(r.get("position"))
                r.update({"actual": round(ppr[key], 1), "bar": None if bar is None else round(bar, 1),
                          "beat_bar": None if bar is None else bool(ppr[key] >= bar),
                          "outcome": "graded"})
            filled += 1
        entry["graded"] = all(x.get("outcome") for x in entry.get("recs", []))
        weeks += 1
    if filled:
        _save(data)
    return {"weeks": weeks, "filled": filled}


def record(season: int | None = None) -> dict:
    """
    The running record of the boards: how often a recommendation was startable, and whether
    the projection behind it was honest about how much he would score.
    """
    data = _load()
    rows, weeks = [], []
    for k in sorted(data):
        e = data[k]
        if season is not None and e.get("season") != season:
            continue
        graded = [r for r in e.get("recs", []) if r.get("outcome")]
        if not graded:
            continue
        beat = sum(1 for r in graded if r.get("beat_bar"))
        dnp = sum(1 for r in graded if r.get("outcome") == "dnp")
        weeks.append({"week": k, "n": len(graded), "startable": f"{beat}/{len(graded)}",
                      "dnp": dnp})
        rows.extend(graded)
    if not rows:
        return {"n": 0, "weeks": []}
    played = [r for r in rows if r.get("actual") is not None]
    beat = sum(1 for r in rows if r.get("beat_bar"))
    proj = [float(r["proj"]) for r in played if r.get("proj") is not None]
    act = [float(r["actual"]) for r in played if r.get("proj") is not None]
    err = [a - p for p, a in zip(proj, act)]
    out = {
        "n": len(rows), "weeks": weeks,
        "startable_rate": round(beat / len(rows) * 100, 1),
        "startable": f"{beat}/{len(rows)}",
        "dnp": sum(1 for r in rows if r.get("outcome") == "dnp"),
        # Projection against outcome, on the players who played. A board that recommends
        # startable players while projecting them 4 points high is two different results,
        # and one number cannot carry both.
        "mean_proj": round(sum(proj) / len(proj), 2) if proj else None,
        "mean_actual": round(sum(act) / len(act), 2) if act else None,
        "mean_error": round(sum(err) / len(err), 2) if err else None,
        "mae": round(sum(abs(e) for e in err) / len(err), 2) if err else None,
    }
    for kind in ("waiver", "sleeper"):
        sub = [r for r in rows if r.get("kind") == kind]
        if sub:
            b = sum(1 for r in sub if r.get("beat_bar"))
            out[f"{kind}_startable"] = f"{b}/{len(sub)}"
            out[f"{kind}_rate"] = round(b / len(sub) * 100, 1)
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("grade():", grade())
    print(json.dumps(record(), indent=1, default=str))
