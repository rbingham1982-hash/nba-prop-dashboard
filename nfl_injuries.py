"""
nfl_injuries.py — who is unavailable right now, for every NFL board.

Why
---
The boards dropped anyone who missed his team's last game, and that was not enough. On the
Week 11 matchup board (2026-11-22) the filter passed Tyjae Spears and Wan'Dale Robinson —
both played Week 10, then went on IR (Spears Saturday, Robinson Tuesday) — and Spears was
the board's single most favorable RB2 row. Dalton Kincaid (Doubtful) and Carson Beck (Out)
were on it too. The nflverse weekly report (nfl_analysis.injury_context) cannot fix this:
it is the Friday practice report, posts late, and never shows an IR placement.

Sources, read live
------------------
  * ESPN's league injury list: statuses including "Injured Reserve", updated within hours,
    with a one-line note. Primary.
  * Sleeper's player file: injury_status ("Out", "IR", "PUP", "Sus", ...) plus roster
    status ("Inactive"). Fills gaps when ESPN's feed is missing a player or fails.

What counts as unavailable
--------------------------
Out, Doubtful, IR, PUP, NFI, suspended. Doubtful is treated as out because, in 2025's report
rows, doubtful skill players played 0 of 29 times (nfl_analysis._INJURY_PLAY_RATE).
Questionable is NOT removed: those players play about 55% of the time. Boards flag them.

Limits
------
Game-day inactives that are not injuries (a healthy scratch) are not in either feed until
Sleeper catches up. Rerun after inactives post: 90 minutes before each kickoff.
Every function fails open: if both feeds are down it returns {} and the boards behave as
they did before, with a warning on stderr.
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata

_ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
_SLEEPER_URL = "https://api.sleeper.app/v1/players/nfl"
_POSITIONS = {"QB", "RB", "WR", "TE", "FB"}

# Normalised status -> unavailable?
_OUT = {"out", "doubtful", "injured reserve", "ir", "pup", "physically unable to perform",
        "nfi", "non-football injury", "suspended", "sus", "suspension", "cov",
        "injured reserve", "non football injury"}
_SLEEPER_ROSTER_OUT = {"Injured Reserve", "Physically Unable to Perform", "Suspended",
                       "Non Football Injury"}
_QUESTIONABLE = {"questionable", "q", "game-time decision", "gtd"}

_CACHE: dict = {"t": 0.0, "map": None}
_TTL = 15 * 60          # a board run reads this many times; a rerun 15 minutes later refetches


def name_key(name: str) -> str:
    """Accents, punctuation and Jr./III removed: 'Travis Etienne Jr.' == 'Travis Etienne'."""
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", n)
    return re.sub(r"[^a-z]", "", n)


def _norm_status(s) -> str:
    return str(s or "").strip().lower()


def _espn() -> dict:
    import requests
    j = requests.get(_ESPN_URL, timeout=30).json()
    out = {}
    for team in j.get("injuries", []):
        for i in team.get("injuries", []):
            a = i.get("athlete") or {}
            nm = a.get("displayName")
            st = i.get("status")
            if not nm or not st:
                continue
            pos = ((a.get("position") or {}).get("abbreviation") or "").upper()
            if pos and pos not in _POSITIONS:
                continue
            tm = ((a.get("team") or {}).get("abbreviation")
                  or (team.get("team") or {}).get("abbreviation") or "")
            out[name_key(nm)] = {"player": nm, "team": tm, "status": st,
                                 "note": (i.get("shortComment") or "").strip(),
                                 "date": str(i.get("date") or "")[:10], "source": "ESPN"}
    return out


def _sleeper() -> dict:
    import requests
    j = requests.get(_SLEEPER_URL, timeout=60).json()
    out = {}
    for p in j.values():
        if p.get("position") not in _POSITIONS or not p.get("full_name") or not p.get("team"):
            continue
        inj = p.get("injury_status")
        roster = p.get("status")
        # Roster status only for the long absences. Plain "Inactive" also covers the practice
        # squad, which would remove a player elevated for the game (Michael Carter, TEN,
        # Week 11 2026).
        st = inj or (roster if roster in _SLEEPER_ROSTER_OUT else None)
        if not st:
            continue
        out[name_key(p["full_name"])] = {"player": p["full_name"], "team": p["team"],
                                         "status": st, "note": p.get("injury_body_part") or "",
                                         "date": "", "source": "Sleeper"}
    return out


def status_map(refresh: bool = False) -> dict:
    """name_key -> {player, team, status, note, date, source}. ESPN first, Sleeper fills gaps."""
    if not refresh and _CACHE["map"] is not None and time.time() - _CACHE["t"] < _TTL:
        return _CACHE["map"]
    merged: dict = {}
    for label, fn in (("Sleeper", _sleeper), ("ESPN", _espn)):     # ESPN last: it wins
        try:
            merged.update(fn())
        except Exception as e:                                      # fail open
            print(f"nfl_injuries: {label} feed failed ({e}); continuing without it",
                  file=sys.stderr)
    _CACHE.update(t=time.time(), map=merged)
    return merged


def status(player: str, team: str | None = None) -> dict | None:
    """The current entry for a player, or None if he is on no injury list.

    `team`, when given, guards against two players with one name: an entry for a different
    team is ignored (Sleeper lists free agents with no team, which never match a board row).
    """
    e = status_map().get(name_key(player))
    if e and team and e.get("team") and e["team"].upper() != str(team).upper():
        return None
    return e


def is_out(player: str, team: str | None = None) -> bool:
    e = status(player, team)
    return bool(e) and _norm_status(e["status"]) in _OUT


def is_questionable(player: str, team: str | None = None) -> bool:
    e = status(player, team)
    return bool(e) and _norm_status(e["status"]) in _QUESTIONABLE


def label(player: str, team: str | None = None) -> str | None:
    """'Out', 'IR', 'Doubtful', 'Q' or None — short enough for a table cell."""
    e = status(player, team)
    if not e:
        return None
    s = _norm_status(e["status"])
    if s in ("injured reserve", "ir"):
        return "IR"
    if s in _QUESTIONABLE:
        return "Q"
    if s in _OUT:
        return e["status"].title() if len(e["status"]) <= 10 else "Out"
    return None


def check(players) -> dict:
    """
    Sort a board's players into {"out": [...], "questionable": [...]}.

    `players` is an iterable of names or (name, team) pairs. Each result row is the status
    entry plus the name as the board spelled it, so a page can say what it removed and why.
    """
    out, q = [], []
    seen = set()
    for p in players:
        name, team = (p if isinstance(p, (tuple, list)) else (p, None))
        k = name_key(name)
        if k in seen:
            continue
        seen.add(k)
        e = status(name, team)
        if not e:
            continue
        row = {**e, "board_name": name, "label": label(name, team)}
        if _norm_status(e["status"]) in _OUT:
            out.append(row)
        elif _norm_status(e["status"]) in _QUESTIONABLE:
            q.append(row)
    return {"out": out, "questionable": q}


if __name__ == "__main__":
    m = status_map(refresh=True)
    outs = sorted((e for e in m.values() if _norm_status(e["status"]) in _OUT),
                  key=lambda e: (e["team"], e["player"]))
    print(f"{len(m)} skill players on a list; {len(outs)} unavailable")
    for e in outs:
        print(f"  {e['team']:4s} {e['player']:26s} {e['status']:16s} {e['source']}")
