"""
nba_fantasy.py — the Fantasy tab's NBA tools: DFS value, a waiver board and start/sit.

All three run off one projection: DraftKings fantasy points per game, from the player's box
scores (the league game log, the same one the props model and graders read, which the
cloud keeps current from ESPN).

    projection = recent form, steadied by last season while this season is young

Recent form is the mean of the player's last 10 games this season. Early in a season that
is a handful of games, so it is blended with last season's average, weighted by games
played: (n * this_season + 8 * last_season) / (n + 8). A player with no NBA history at all
(a rookie before his debut) gets no projection rather than a made-up one.

DraftKings NBA classic scoring: point 1, made three +0.5, rebound 1.25, assist 1.5, steal 2,
block 2, turnover -0.5, double-double +1.5, triple-double +3.
"""
from __future__ import annotations

import datetime
import time
import unicodedata

import pandas as _pd

_RECENT = 10
_PRIOR_WEIGHT = 8
_SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nba"
_SLEEPER_TRENDING = "https://api.sleeper.app/v1/players/nba/trending/{kind}?lookback_hours={hours}&limit={limit}"
_UA = {"User-Agent": "Mozilla/5.0"}
_cache: dict = {}
_TTL = 900


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "").replace(",", "").replace("-", " ")
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return " ".join(s.split())


def dk_points(df: _pd.DataFrame) -> _pd.Series:
    """DraftKings classic points for each row of a LeagueGameLog-shaped frame."""
    cats = _pd.concat([df["PTS"] >= 10, df["REB"] >= 10, df["AST"] >= 10,
                       df["STL"] >= 10, df["BLK"] >= 10], axis=1).sum(axis=1)
    return (df["PTS"] + 0.5 * df["FG3M"] + 1.25 * df["REB"] + 1.5 * df["AST"] + 2 * df["STL"]
            + 2 * df["BLK"] - 0.5 * df["TOV"] + 1.5 * (cats >= 2) + 3 * (cats >= 3))


def projections(today: datetime.date | None = None) -> _pd.DataFrame:
    """One row per player: projected DK points and minutes per game, and what they rest on."""
    import nba_grades as ng
    import parlay_model as pm

    def _go():
        cur_s, prev_s = pm.nba_season_strings(today or datetime.date.today())
        frames = {}
        for s in (cur_s, prev_s):
            try:
                d = ng.season_log(s).copy()
            except Exception:
                d = _pd.DataFrame()
            if not d.empty:
                d = d[d["MIN"] > 0].copy()
                d["DK"] = dk_points(d)
            frames[s] = d
        cur, prev = frames[cur_s], frames[prev_s]
        rows = {}
        if not prev.empty:
            for (pid, name), g in prev.groupby(["PLAYER_ID", "PLAYER_NAME"]):
                rows[pid] = {"player": name, "team": g["TEAM_ABBREVIATION"].iloc[-1],
                             "prev_dk": g["DK"].mean(), "prev_min": g["MIN"].mean(), "prev_n": len(g),
                             "cur_dk": None, "cur_min": None, "cur_n": 0}
        if not cur.empty:
            for (pid, name), g in cur.sort_values("date").groupby(["PLAYER_ID", "PLAYER_NAME"]):
                r = rows.setdefault(pid, {"player": name, "prev_dk": None, "prev_min": None, "prev_n": 0})
                last = g.tail(_RECENT)
                r.update({"team": g["TEAM_ABBREVIATION"].iloc[-1], "cur_dk": last["DK"].mean(),
                          "cur_min": last["MIN"].mean(), "cur_n": len(g)})
        out = []
        for pid, r in rows.items():
            n = r["cur_n"]
            if n and r["prev_dk"] is not None:
                w = _PRIOR_WEIGHT
                dk = (n * r["cur_dk"] + w * r["prev_dk"]) / (n + w)
                mins = (n * r["cur_min"] + w * r["prev_min"]) / (n + w)
            elif n:
                dk, mins = r["cur_dk"], r["cur_min"]
            else:
                dk, mins = r["prev_dk"], r["prev_min"]
            out.append({"player_id": pid, "player": r["player"], "team": r.get("team"),
                        "proj_dk": round(float(dk), 1), "proj_min": round(float(mins), 1),
                        "games_this_season": n, "games_last_season": r["prev_n"],
                        "key": _norm(r["player"])})
        return _pd.DataFrame(out).sort_values("proj_dk", ascending=False).reset_index(drop=True)

    return _cached(f"proj_{today}", _go)


def _lookup(proj: _pd.DataFrame, name: str):
    hit = proj[proj["key"] == _norm(name)]
    return None if hit.empty else hit.iloc[0]


# ── DFS ───────────────────────────────────────────────────────────────────────────────────

def dfs_slate(draft_group_id: int | None = None) -> _pd.DataFrame:
    """Tonight's DraftKings NBA slate joined to the projection, ranked by points per $1,000."""
    import fantasy_tools as ft
    sal = ft.dk_salaries("NBA", draft_group_id)
    if sal is None or sal.empty:
        return _pd.DataFrame()
    proj = projections()
    rows = []
    for _, r in sal.iterrows():
        p = _lookup(proj, r["player"])
        if p is None:
            continue
        rows.append({**r.to_dict(), "proj_points": p["proj_dk"], "proj_min": p["proj_min"],
                     "games_this_season": int(p["games_this_season"])})
    if not rows:
        return _pd.DataFrame()
    out = _pd.DataFrame(rows)
    out["value"] = (out["proj_points"] / (out["salary"] / 1000.0)).round(2)
    return out.sort_values("value", ascending=False).reset_index(drop=True)


# ── Sleeper ───────────────────────────────────────────────────────────────────────────────

def sleeper_players() -> dict:
    """sleeper id -> {name, position, team, search_rank, injury_status}, NBA."""
    import requests

    def _go():
        r = requests.get(_SLEEPER_PLAYERS, timeout=60, headers=_UA)
        if r.status_code != 200:
            return {}
        out = {}
        for pid, p in (r.json() or {}).items():
            if not p.get("full_name") or not p.get("active"):
                continue
            out[pid] = {"name": p["full_name"], "position": p.get("position"), "team": p.get("team"),
                        "search_rank": p.get("search_rank"), "injury": p.get("injury_status")}
        return out

    return _cached("sleeper_nba_players", _go)


def sleeper_trending(kind: str = "add", hours: int = 168, limit: int = 200) -> dict:
    """normalized name -> adds (or drops) over the last `hours`, NBA."""
    import requests

    def _go():
        players = sleeper_players()
        r = requests.get(_SLEEPER_TRENDING.format(kind=kind, hours=hours, limit=limit), timeout=25, headers=_UA)
        if r.status_code != 200:
            return {}
        return {_norm(players[str(x["player_id"])]["name"]): int(x.get("count") or 0)
                for x in (r.json() or []) if str(x.get("player_id")) in players}

    return _cached(f"sleeper_nba_trend_{kind}_{hours}", _go)


# ── waiver board ──────────────────────────────────────────────────────────────────────────

def waiver_board(limit: int = 25, min_rank: int = 150, min_min: float = 18.0) -> list:
    """
    Players outside Sleeper's top `min_rank` (a free proxy for "probably on waivers") who
    project well, ranked by projected DK points per game. `quiet` means the crowd has not
    noticed (few adds this week); `hot` means it has.
    """
    proj = projections()
    sp = sleeper_players()
    adds = sleeper_trending("add")
    by_name = {_norm(p["name"]): p for p in sp.values()}
    out = []
    for _, r in proj.iterrows():
        s = by_name.get(r["key"])
        if not s or not s.get("team"):
            continue                              # not on an NBA roster now
        if str(s.get("injury") or "").lower() == "out":
            continue                              # not an add while he cannot play
        rank = s.get("search_rank") or 10_000
        if rank <= min_rank or r["proj_min"] < min_min:
            continue
        a = adds.get(r["key"], 0)
        out.append({"player": r["player"], "position": s.get("position"), "team": s.get("team"),
                    "proj_points": r["proj_dk"], "proj_min": r["proj_min"],
                    "search_rank": rank, "adds": a, "injury": s.get("injury") or "",
                    "crowd": "hot" if a >= 5000 else ("warm" if a >= 500 else "quiet")})
        if len(out) >= limit:
            break
    return out


# ── start / sit ───────────────────────────────────────────────────────────────────────────

def start_sit(names: list) -> list:
    """The listed players ranked by projected DK points per game; unknown names say so."""
    proj = projections()
    sp = {_norm(p["name"]): p for p in sleeper_players().values()}
    rows = []
    for n in names:
        p = _lookup(proj, n)
        s = sp.get(_norm(n), {})
        if p is None:
            rows.append({"player": n, "team": s.get("team", ""), "proj_points": None, "proj_min": None,
                         "note": "no NBA games on record yet"})
            continue
        note = []
        if s.get("injury"):
            note.append(f"injury: {s['injury']}")
        if int(p["games_this_season"]) < 5:
            note.append(f"{int(p['games_this_season'])} games this season, leaning on last season")
        rows.append({"player": p["player"], "team": s.get("team") or p["team"], "proj_points": p["proj_dk"],
                     "proj_min": p["proj_min"], "note": "; ".join(note)})
    return sorted(rows, key=lambda r: -(r["proj_points"] or -1))
