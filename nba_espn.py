"""
nba_espn.py — the current NBA and WNBA seasons' league game logs, plus the NBA roster and
season-stat files the draft board reads, all built from ESPN.

stats.nba.com hangs for datacenter addresses, so until 2026-10-18 NBA and WNBA ran on the home
PC and the hosted app read the logs it exported (nba_snapshot.py). ESPN's public site API
answers GitHub's runners (check_sources.py, 2026-10-18) and its box scores carry every column
the models and graders read, so the cloud runner writes these itself:

    python nba_espn.py          # refresh both leagues' logs and the NBA roster files

Game logs are LeagueGameLog-shaped and split by month exactly as nba_snapshot writes them,
so every reader (nba_snapshot.league_log/player_log, nba_grades.season_log, the dashboard)
works unchanged in snapshot mode. Earlier seasons stay as the home PC exported them; they are
final. Checked against stats.nba.com on three nights of 2025-26: 545 of 547 player-games
matched with every stat identical (the two misses were a 1- and a 3-minute cameo).

Player ids are stats.nba.com ids, so a player's rows join his earlier seasons. ESPN ids are
mapped through, in order: the saved map (nba_data/espn-<league>-ids.csv, so an id never
changes once given), sportsdataverse's ESPN-to-NBA crosswalk (NBA only), a name match
against the saved PlayerIndex and game logs, and last a synthetic id (900,000,000 + the ESPN
id) for a player found in none of them. Team codes come from the saved logs, matched on the
team's name, so they stay stats.nba.com's (NYK, LVA, PHX), not ESPN's (NY, LV). Play-in
games (ESPN season type 5) are not written: the snapshot has no play-in file.
"""
from __future__ import annotations

import datetime
import time
import unicodedata

import pandas as pd
import requests

import nba_snapshot as ns

_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/{path}"
# The same API on a second host. From 2026-10-28 the runner's scoreboard calls to the first
# started coming back as empty 200s (JSONDecodeError) while every local call worked, which
# stopped the NBA log for two days; trying the other host gets the same JSON.
_SITE_ALT = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/{path}"
_CROSSWALK = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
              "nba_crosswalk/nba_player_crosswalk_{year}.parquet")
_LEAGUES = {"00": "nba", "10": "wnba"}
_TYPES = {2: ("Regular Season", "2"), 3: ("Playoffs", "4")}     # ESPN type -> (snapshot, SEASON_ID prefix)
# Used only when a team's name is not in the saved logs (a franchise renamed or new).
_ESPN_ABBR = {"00": {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"},
              "10": {"GS": "GSV", "LV": "LVA", "LA": "LAS", "NY": "NYL", "WSH": "WAS", "POR": "PDX"}}
_SYNTHETIC = 900_000_000
_COLS = ["SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME",
         "GAME_ID", "GAME_DATE", "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M", "FG3A",
         "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV",
         "PF", "PTS", "PLUS_MINUS", "FANTASY_PTS", "VIDEO_AVAILABLE"]


def _get(league: str, path: str, **params) -> dict:
    hosts = (_SITE, _SITE_ALT, _SITE, _SITE_ALT)
    for attempt, host in enumerate(hosts):
        # The empty 200s hit some dates and not others, on both hosts, run after run: a bad
        # copy cached at ESPN's edge. Retries add a throwaway parameter so they miss that copy.
        q = dict(params, _=int(time.time() * 1000)) if attempt else params
        r = None
        try:
            r = requests.get(host.format(path=f"{_LEAGUES[league]}/{path}"), params=q,
                             timeout=20, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"})
            r.raise_for_status()
            return r.json()
        except Exception as ex:
            if r is not None:
                print(f"  ESPN {path} {params} try {attempt + 1}: {type(ex).__name__}, HTTP {r.status_code}, "
                      f"{len(r.content)} bytes, {r.headers.get('content-type')}")
            if attempt == len(hosts) - 1:
                raise
            time.sleep(2.0 * (attempt + 1))


_CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/{lg}/events"


def _day_events(league: str, day: datetime.date) -> list:
    """
    The day's games as scoreboard events (id, shortName, season.type, status.type.completed).
    From 2026-10-28 the runner gets 0-byte 200s for some dates' scoreboards on every try and
    every host, while summaries still come through; then the ids come from ESPN's core API
    and each game's season type and status from its summary.
    """
    try:
        return _get(league, "scoreboard", dates=day.strftime("%Y%m%d")).get("events", [])
    except Exception as ex:
        print(f"  scoreboard {day} unreadable ({type(ex).__name__}); listing games from the core API")
    r = requests.get(_CORE.format(lg=_LEAGUES[league]), params={"dates": day.strftime("%Y%m%d"), "limit": 100},
                     timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    events = []
    for item in r.json().get("items", []):
        eid = item["$ref"].split("/events/")[1].split("?")[0]
        h = _get(league, "summary", event=eid)["header"]
        comp = h["competitions"][0]
        events.append({"id": eid, "season": {"type": h.get("season", {}).get("type", 0)},
                       "status": comp["status"],
                       "shortName": " @ ".join(c["team"]["abbreviation"] for c in
                                               sorted(comp["competitors"], key=lambda c: c["homeAway"] == "home"))})
    return events


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "").replace(",", "").replace("-", " ").strip()
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return " ".join(s.split())


def season_for(league: str, today: datetime.date) -> str:
    """The season a date belongs to: '2026-27' for the NBA, '2026' for the WNBA."""
    if league == "00":
        import parlay_model as pm
        return pm.nba_season_strings(today)[0]
    return str(today.year if today.month >= 4 else today.year - 1)


def _season_start(league: str, season: str) -> datetime.date:
    return datetime.date(int(season[:4]), 10, 1) if league == "00" else datetime.date(int(season), 5, 1)


# ── teams ─────────────────────────────────────────────────────────────────────────────────

class _Teams:
    """ESPN team -> (stats.nba.com abbreviation, team id, team name)."""

    def __init__(self, league: str):
        self.league, self.by_name, self.by_abbr = league, {}, {}
        for f in sorted(ns.DIR.glob(f"gamelog-{ns._LEAGUE[league]}-*.csv.gz")):  # newest last
            df = pd.read_csv(f, usecols=["TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME"]).drop_duplicates()
            for i, a, n in zip(df["TEAM_ID"], df["TEAM_ABBREVIATION"], df["TEAM_NAME"]):
                self.by_name[_norm(n)] = (a, int(i), n)
                self.by_abbr[a] = (a, int(i), n)
        if league == "00":                       # LeagueGameLog spells it "LA Clippers"
            self.by_name.setdefault(_norm("Los Angeles Clippers"), self.by_name.get(_norm("LA Clippers")))

    def get(self, team: dict) -> tuple:
        hit = self.by_name.get(_norm(team.get("displayName", "")))
        if hit:
            return hit
        abbr = _ESPN_ABBR[self.league].get(team["abbreviation"], team["abbreviation"])
        return self.by_abbr.get(abbr, (abbr, 0, team.get("displayName", abbr)))


# ── player ids ────────────────────────────────────────────────────────────────────────────

class _Ids:
    def __init__(self, league: str, season: str):
        self.league, self._season = league, season
        self.path = ns.DIR / f"espn-{_LEAGUES[league]}-ids.csv"
        self.saved = {}
        if self.path.exists():
            m = pd.read_csv(self.path, dtype=str)
            self.saved = dict(zip(m["espn_id"], m["nba_id"].astype(int)))
        self.added = False
        self._cross = self._names = None

    def _crosswalk(self) -> dict:
        if self._cross is None:
            self._cross = {}
            if self.league != "00":
                return self._cross
            year = int(self._season[:4]) + 1
            for y in (year, year - 1):                  # the new season's file appears late
                try:
                    x = pd.read_parquet(_CROSSWALK.format(year=y))
                except Exception:
                    continue
                x = x.dropna(subset=["espn_athlete_id", "nba_player_id"])
                for e, n in zip(x["espn_athlete_id"], x["nba_player_id"]):
                    self._cross.setdefault(str(int(float(e))), int(float(n)))
        return self._cross

    def _by_name(self) -> dict:
        if self._names is None:
            self._names = {_norm(n): i for n, i in ns.name_ids(self.league).items()}
            if self.league == "00":
                idx = ns.frame(f"index-NBA-{self._season}")
                if not idx.empty:
                    for f, l, i in zip(idx["PLAYER_FIRST_NAME"], idx["PLAYER_LAST_NAME"], idx["PERSON_ID"]):
                        self._names[_norm(f"{f} {l}")] = int(i)
        return self._names

    def get(self, espn_id: str, name: str) -> int:
        if espn_id in self.saved:
            return self.saved[espn_id]
        nba = self._crosswalk().get(espn_id) or self._by_name().get(_norm(name))
        nba = int(nba) if nba else _SYNTHETIC + int(espn_id)
        self.saved[espn_id], self.added = nba, True
        return nba

    def save(self, changed: list) -> None:
        if self.added:
            pd.DataFrame(sorted(self.saved.items()), columns=["espn_id", "nba_id"]).to_csv(
                self.path, index=False, lineterminator="\n")
            changed.append(self.path.name)


# ── box scores ────────────────────────────────────────────────────────────────────────────

def _split(s: str) -> tuple[int, int]:
    a, b = (s or "0-0").split("-")
    return int(a or 0), int(b or 0)


def _int(s) -> int:
    try:
        return int(str(s).replace("+", "") or 0)
    except ValueError:
        return 0


def _pct(m: int, a: int):
    return round(m / a, 3) if a else None


def game_rows(league: str, event_id: str, day: datetime.date, season: str, s_prefix: str,
              ids: _Ids, teams: _Teams) -> list:
    """LeagueGameLog rows for one finished game: every player who played."""
    s = _get(league, "summary", event=event_id)
    comp = s["header"]["competitions"][0]
    side = {c["team"]["id"]: (teams.get(c["team"]), c["homeAway"], bool(c.get("winner")))
            for c in comp["competitors"]}
    rows = []
    for t in s.get("boxscore", {}).get("players", []):
        (abbr, team_id, team_name), home_away, won = side[t["team"]["id"]]
        opp = next(v[0][0] for tid, v in side.items() if tid != t["team"]["id"])
        block = t["statistics"][0]
        keys = block["keys"]
        for a in block["athletes"]:
            if a.get("didNotPlay") or not a.get("stats"):
                continue
            v = dict(zip(keys, a["stats"]))
            mins = _int(v.get("minutes"))
            if mins <= 0:
                continue
            fgm, fga = _split(v.get("fieldGoalsMade-fieldGoalsAttempted"))
            f3m, f3a = _split(v.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
            ftm, fta = _split(v.get("freeThrowsMade-freeThrowsAttempted"))
            reb, ast, stl, blk, tov = (_int(v.get(k)) for k in
                                       ("rebounds", "assists", "steals", "blocks", "turnovers"))
            pts = _int(v.get("points"))
            name = a["athlete"]["displayName"]
            rows.append({
                "SEASON_ID": f"{s_prefix}{season[:4]}", "PLAYER_ID": ids.get(str(a["athlete"]["id"]), name),
                "PLAYER_NAME": name, "TEAM_ID": team_id, "TEAM_ABBREVIATION": abbr,
                "TEAM_NAME": team_name, "GAME_ID": str(event_id), "GAME_DATE": day.isoformat(),
                "MATCHUP": f"{abbr} vs. {opp}" if home_away == "home" else f"{abbr} @ {opp}",
                "WL": "W" if won else "L", "MIN": mins,
                "FGM": fgm, "FGA": fga, "FG_PCT": _pct(fgm, fga),
                "FG3M": f3m, "FG3A": f3a, "FG3_PCT": _pct(f3m, f3a),
                "FTM": ftm, "FTA": fta, "FT_PCT": _pct(ftm, fta),
                "OREB": _int(v.get("offensiveRebounds")), "DREB": _int(v.get("defensiveRebounds")),
                "REB": reb, "AST": ast, "STL": stl, "BLK": blk, "TOV": tov,
                "PF": _int(v.get("fouls")), "PTS": pts, "PLUS_MINUS": _int(v.get("plusMinus")),
                # NBA.com's fantasy formula, which LeagueGameLog reports.
                "FANTASY_PTS": round(pts + 1.2 * reb + 1.5 * ast + 3 * stl + 3 * blk - tov, 1),
                "VIDEO_AVAILABLE": 0})
    return rows


# ── refresh ───────────────────────────────────────────────────────────────────────────────

def refresh(league: str = "00", today: datetime.date | None = None, max_days: int = 45,
            changed: list | None = None) -> list[str]:
    """
    Fetch every finished game since the last one saved (re-reading the last two days, which
    catches stat corrections and games that ended after the previous run) and write the
    month files that changed. Returns the changed file names.
    """
    today = today or datetime.date.today()
    changed = [] if changed is None else changed
    season = season_for(league, today)
    ids, teams = _Ids(league, season), _Teams(league)
    have = pd.concat([ns.league_log(season, st, league) for st, _p in _TYPES.values()],
                     ignore_index=True)
    if have.empty:
        start = _season_start(league, season)
    else:
        start = pd.to_datetime(have["GAME_DATE"]).max().date() - datetime.timedelta(days=2)
    start = max(start, today - datetime.timedelta(days=max_days))

    new = {st: [] for st, _p in _TYPES.values()}
    day = start
    while day <= today:
        try:
            events = _day_events(league, day)
        except Exception as ex:
            # Stop here rather than skip the day: the next run re-reads from two days before
            # the newest saved game, so a hole left behind a later day would never be filled.
            # Everything before this day is still written below.
            print(f"  games on {day} unreadable ({type(ex).__name__}); stopping at {day}")
            break
        for e in events:
            kind = _TYPES.get(int(e.get("season", {}).get("type", 0)))
            if kind is None or not e["status"]["type"].get("completed"):
                continue                        # preseason, play-in, all-star, or not over yet
            try:
                new[kind[0]] += game_rows(league, e["id"], day, season, kind[1], ids, teams)
            except Exception as ex:
                print(f"  skip {e.get('shortName')} {day}: {type(ex).__name__}: {ex}")
        day += datetime.timedelta(days=1)

    for s_type, rows in new.items():
        if not rows:
            continue
        fresh = pd.DataFrame(rows, columns=_COLS)
        old = ns.league_log(season, s_type, league)
        if not old.empty:
            # Replace by (date, team), not by GAME_ID: rows the home PC exported from
            # stats.nba.com carry NBA game ids, so matching on ESPN's ids would keep both
            # copies of the same game on the night the two sources meet.
            done = set(zip(fresh["GAME_DATE"].astype(str), fresh["TEAM_ABBREVIATION"]))
            old = old[[(str(d), a) not in done
                       for d, a in zip(old["GAME_DATE"], old["TEAM_ABBREVIATION"])]]
        df = pd.concat([old, fresh], ignore_index=True)[_COLS]
        df = df.sort_values(["GAME_DATE", "GAME_ID", "PLAYER_ID"]).reset_index(drop=True)
        for month, part in df.groupby(df["GAME_DATE"].astype(str).str[:7]):
            ns._write(ns.DIR / f"{ns._stem(league, season, s_type)}-{month}.csv.gz", part, changed)
    ids.save(changed)
    ns._CACHE.clear()
    return changed


# ── NBA rosters and season stats (the draft board's inputs) ──────────────────────────────

def refresh_rosters(today: datetime.date | None = None, changed: list | None = None) -> list[str]:
    """
    Keep index-NBA-<season> (PlayerIndex) current from ESPN's team rosters: a traded player's
    team, and players signed since the last export. Columns ESPN does not carry (draft year
    and slot, college) are kept from the saved file; a player ESPN lists on no roster keeps
    his last saved team rather than being dropped.
    """
    today = today or datetime.date.today()
    changed = [] if changed is None else changed
    season = season_for("00", today)
    ids, teams = _Ids("00", season), _Teams("00")
    # Read as text: values this run does not touch are written back exactly as they were
    # ("243", not "243.0"), so only real roster moves show up as changes.
    idx = pd.DataFrame()
    for s_name in (season, _prev(season)):
        path = ns.DIR / f"index-NBA-{s_name}.csv.gz"
        if path.exists():
            idx = pd.read_csv(path, dtype=str, keep_default_na=False)
            break
    rows = {int(float(r["PERSON_ID"])): dict(r) for _, r in idx.iterrows()} if not idx.empty else {}
    seen = 0
    for t in _get("00", "teams")["sports"][0]["leagues"][0]["teams"]:
        abbr, team_id, team_name = teams.get(t["team"])
        roster = _get("00", f"teams/{t['team']['id']}/roster").get("athletes", [])
        for a in roster:
            pid = ids.get(str(a["id"]), a.get("displayName", ""))
            r = rows.get(pid)
            if r is None:
                # New to the file: ESPN is the only source, so it fills the name and a
                # position ("G"/"F"/"C", coarser than PlayerIndex's "G-F").
                r = {"PERSON_ID": str(pid), "ROSTER_STATUS": "1.0", "PLAYER_FIRST_NAME": a.get("firstName", ""),
                     "PLAYER_LAST_NAME": a.get("lastName", ""),
                     "POSITION": (a.get("position") or {}).get("abbreviation", "")}
            # A known player keeps his saved name spelling and position (ESPN adds accents
            # and drops the hybrid positions); only the club and number follow ESPN.
            r.update({"TEAM_ID": str(team_id), "TEAM_ABBREVIATION": abbr,
                      # PlayerIndex splits the club into city and nickname, as ESPN does.
                      "TEAM_NAME": t["team"].get("name", ""), "TEAM_CITY": t["team"].get("location", ""),
                      "JERSEY_NUMBER": str(a.get("jersey", r.get("JERSEY_NUMBER", "")))})
            rows[pid] = r
            seen += 1
    if seen < 300:                               # a partial ESPN answer must not rewrite rosters
        print(f"  rosters: only {seen} players returned — not saved")
        return changed
    out = pd.DataFrame(list(rows.values())).fillna("")
    cols = list(idx.columns) if not idx.empty else list(out.columns)
    out = out[cols + [c for c in out.columns if c not in cols]]
    if not idx.empty:                           # saved order first, new players after
        order = {p: i for i, p in enumerate(idx["PERSON_ID"])}
        out = out.assign(_o=out["PERSON_ID"].map(order).fillna(len(order))).sort_values(
            ["_o", "PLAYER_LAST_NAME"]).drop(columns="_o")
    ns._write(ns.DIR / f"index-NBA-{season}.csv.gz", out.reset_index(drop=True), changed)
    ids.save(changed)
    return changed


def refresh_dash(today: datetime.date | None = None, changed: list | None = None) -> list[str]:
    """
    dash-NBA-<season> (LeagueDashPlayerStats, per game, regular season) aggregated from the
    saved game log, for the draft board's current-season numbers. AGE carries from last
    season's file plus one; a player with no earlier file gets 0, which the aging curve
    treats as no adjustment.
    """
    today = today or datetime.date.today()
    changed = [] if changed is None else changed
    season = season_for("00", today)
    log = ns.league_log(season, "Regular Season", "00")
    if log.empty:
        return changed
    stats = ["MIN", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "REB", "AST",
             "TOV", "STL", "BLK", "PF", "PTS", "PLUS_MINUS", "FANTASY_PTS"]
    g = log.sort_values("GAME_DATE").groupby("PLAYER_ID")
    df = g[stats].mean().round(1)
    tot = g[["FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA"]].sum()
    df["GP"] = g.size()
    df["W"] = g["WL"].apply(lambda s: int((s == "W").sum()))
    df["L"] = df["GP"] - df["W"]
    df["W_PCT"] = (df["W"] / df["GP"]).round(3)
    for m, a, c in (("FGM", "FGA", "FG_PCT"), ("FG3M", "FG3A", "FG3_PCT"), ("FTM", "FTA", "FT_PCT")):
        df[c] = (tot[m] / tot[a].where(tot[a] > 0)).round(3).fillna(0.0)
    last = g.last()
    df["PLAYER_NAME"], df["TEAM_ID"], df["TEAM_ABBREVIATION"] = (
        last["PLAYER_NAME"], last["TEAM_ID"], last["TEAM_ABBREVIATION"])
    prev = ns.frame(f"dash-NBA-{_prev(season)}")
    ages = dict(zip(prev["PLAYER_ID"], prev["AGE"])) if not prev.empty else {}
    df["AGE"] = [float(ages[p]) + 1 if p in ages else 0.0 for p in df.index]
    df = df.rename(columns={"FANTASY_PTS": "NBA_FANTASY_PTS"}).reset_index()
    ns._write(ns.DIR / f"dash-NBA-{season}.csv.gz", df, changed)
    return changed


def _prev(season: str) -> str:
    y = int(season[:4]) - 1
    return f"{y}-{str(y + 1)[-2:]}"


def refresh_all(today: datetime.date | None = None) -> list[str]:
    """Everything the cloud runner keeps current. Each part fails alone."""
    changed: list = []
    for name, fn in (("NBA log", lambda: refresh("00", today, changed=changed)),
                     ("WNBA log", lambda: refresh("10", today, changed=changed)),
                     ("NBA rosters", lambda: refresh_rosters(today, changed)),
                     ("NBA season stats", lambda: refresh_dash(today, changed))):
        try:
            fn()
        except Exception as e:
            print(f"  {name} refresh failed ({type(e).__name__}: {e}) — retried next run")
    return changed


if __name__ == "__main__":
    names = refresh_all()
    print(f"ESPN refresh: {len(names)} file(s) updated" + (": " + ", ".join(names) if names else ""))
