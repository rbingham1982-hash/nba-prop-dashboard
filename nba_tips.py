"""
nba_tips.py — a running log of every NBA opening tip: who jumped, who won it.

The First Basket tab answers "how has THIS team done on the tip lately" by re-reading a
team's last N play-by-plays on every view. That is fine for one team and far too slow for
a league table, so this keeps a log instead: one row per game, keyed by ESPN event id,
fetched once and never again. update() only reads the play-by-play of games it has not
seen, so after the first backfill a nightly refresh is a handful of requests.

A row records the team that won the opening tip (ESPN's Jumpball play is credited to the
side that gained possession) and both jumpers, matched to their teams through the game's
own box-score rosters rather than by their order in the play text.
"""
from __future__ import annotations

import json
import pathlib
import time

LOG = pathlib.Path(__file__).with_name("tip_log.json")
_H = {"User-Agent": "Mozilla/5.0"}
_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
_PBP = "https://cdn.espn.com/core/nba/playbyplay"

# ESPN's abbreviations where they differ from the league's, so this table lines up with
# the rest of the NBA page.
_ABBR = {"NY": "NYK", "NO": "NOP", "GS": "GSW", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}


def _abbr(a) -> str:
    return _ABBR.get(str(a), str(a))


def _load() -> dict:
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    LOG.write_text(json.dumps(d, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def _get(url, **params):
    import requests
    return requests.get(url, params=params or None, headers=_H, timeout=12).json()


def current_year() -> int:
    """ESPN's season year (the year the season ENDS: 2026-27 is 2027)."""
    js = _get(f"{_SITE}/teams/BOS/schedule")
    return int((js.get("requestedSeason") or js.get("season") or {}).get("year"))


def _team_ids() -> list:
    js = _get(f"{_SITE}/teams")
    teams = js["sports"][0]["leagues"][0]["teams"]
    return [t["team"]["abbreviation"] for t in teams]


def completed_events(year: int) -> dict:
    """event id -> (date, home, away) for every completed regular-season game of a season."""
    out = {}
    for t in _team_ids():
        try:
            js = _get(f"{_SITE}/teams/{t}/schedule", season=year)
        except Exception:
            continue
        for e in js.get("events", []):
            comp = e.get("competitions", [{}])[0]
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue
            cs = comp.get("competitors", [])
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if home and away:
                out[str(e["id"])] = (e.get("date", "")[:10], _abbr(home["team"]["abbreviation"]),
                                     _abbr(away["team"]["abbreviation"]))
    return out


def parse_tip(event_id: str) -> dict | None:
    """The opening tip of one game, or None when the play-by-play has no jump ball."""
    js = _get(_PBP, gameId=event_id, xhr=1)
    gp = js.get("gamepackageJSON", {})
    teams = {}
    for c in gp.get("header", {}).get("competitions", [{}])[0].get("competitors", []):
        teams[str(c["team"]["id"])] = _abbr(c["team"]["abbreviation"])
    who = {}
    for side in gp.get("boxscore", {}).get("players", []):
        abbr = _abbr(side.get("team", {}).get("abbreviation"))
        for grp in side.get("statistics", []):
            for a in grp.get("athletes", []):
                ath = a.get("athlete", {})
                who[str(ath.get("id"))] = (ath.get("displayName"), abbr)
    for p in gp.get("plays", []):
        if (p.get("period") or {}).get("number") != 1:
            break
        if (p.get("type") or {}).get("text") != "Jumpball":
            continue
        winner = teams.get(str((p.get("team") or {}).get("id")))
        jumpers = {}
        for part in (p.get("participants") or [])[:2]:
            name, team = who.get(str(part.get("athlete", {}).get("id")), (None, None))
            if team:
                jumpers[team] = name
        return {"winner": winner, "jumpers": jumpers, "text": p.get("text", "")}
    return None


def update(years=None, budget_s: float = 900.0, verbose: bool = False, pace: float = 0.12) -> int:
    """
    Read every completed game not yet in the log. Returns how many were added.

    `pace` is the pause between games. A nightly refresh is a handful of games and runs
    fast; a season backfill needs ~1.5s or ESPN starts refusing after a few hundred.
    """
    log = _load()
    if years is None:
        cur = current_year()
        years = (cur - 1, cur)
    added, t0, fails = 0, time.time(), 0
    for y in years:
        for eid, (date, home, away) in sorted(completed_events(y).items(), key=lambda x: x[1][0]):
            if eid in log:
                continue
            if time.time() - t0 > budget_s:
                _save(log)
                return added
            try:
                tip = parse_tip(eid)
                fails = 0
            except Exception:
                # A failure here is almost always ESPN throttling, and every request after
                # it fails instantly — the first backfill skipped 900 straight games that
                # way and reported success. Back off, and after a run of failures stop and
                # keep what is logged; the next update resumes where this one ended.
                fails += 1
                if fails >= 10:
                    _save(log)
                    if verbose:
                        print(f"  stopping after {fails} failures in a row — resume later", flush=True)
                    return added
                time.sleep(min(60.0, 6.0 * fails))
                continue
            log[eid] = {"season": y, "date": date, "home": home, "away": away,
                        "winner": (tip or {}).get("winner"), "jumpers": (tip or {}).get("jumpers", {})}
            added += 1
            if verbose and added % 100 == 0:
                print(f"  {added} games logged", flush=True)
                _save(log)
            time.sleep(pace)
    _save(log)
    return added


def frame(season: int | None = None):
    """One row per team per game: did it win the tip, and who jumped for it."""
    import pandas as pd
    rows = []
    for eid, g in _load().items():
        if season is not None and g["season"] != season:
            continue
        if not g.get("winner"):
            continue
        for team, opp in ((g["home"], g["away"]), (g["away"], g["home"])):
            rows.append({"event": eid, "season": g["season"], "date": g["date"], "team": team,
                         "opp": opp, "won": g["winner"] == team,
                         "jumper": (g.get("jumpers") or {}).get(team),
                         "opp_jumper": (g.get("jumpers") or {}).get(opp)})
    return pd.DataFrame(rows).sort_values("date") if rows else pd.DataFrame()


def team_table(season: int, last_n: int = 10):
    """
    Each team's tip record for a season, its last-n record, and the man jumping for it now
    with his own record. The jumper matters more than the team: a tip is two players, and a
    team's rate mostly describes whoever its starting centre was.
    """
    import pandas as pd
    f = frame(season)
    if f.empty:
        return f
    out = []
    jrec = f.groupby("jumper")["won"].agg(["sum", "size"])
    for team, g in f.groupby("team"):
        g = g.sort_values("date")
        last = g.tail(last_n)
        # The usual jumper of late, not whoever jumped last: one rest night would otherwise
        # hand San Antonio's row to its backup centre.
        recent = g["jumper"].dropna().tail(5)
        jumper = recent.mode().iloc[-1] if not recent.empty else None
        if jumper is not None and (recent == recent.iloc[-1]).sum() == (recent == jumper).sum():
            jumper = recent.iloc[-1]          # a tie goes to the most recent
        jw, jn = (int(jrec.loc[jumper, "sum"]), int(jrec.loc[jumper, "size"])) if jumper in jrec.index else (0, 0)
        out.append({"Team": team, "Tips": f"{int(g['won'].sum())}-{int((~g['won']).sum())}",
                    "Win %": g["won"].mean(), f"Last {last_n}": f"{int(last['won'].sum())}-{int((~last['won']).sum())}",
                    "Jumper now": jumper or "—", "Jumper record": f"{jw}-{jn - jw}" if jn else "—",
                    "Jumper %": (jw / jn) if jn else None, "games": len(g)})
    return pd.DataFrame(out).sort_values("Win %", ascending=False).reset_index(drop=True)
