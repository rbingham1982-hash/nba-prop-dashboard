"""
nba_espn.py — the current NBA season's league game log, built from ESPN box scores.

stats.nba.com hangs for datacenter addresses, so until 2026-10-18 NBA ran on the home PC and
the hosted app read the logs it exported (nba_snapshot.py). ESPN's public site API answers
GitHub's runners (check_sources.py, 2026-10-18) and its box scores carry every column the
NBA model and graders read, so the cloud runner now writes the current season itself:

    python nba_espn.py          # refresh nba_data/gamelog-NBA-<season>-*.csv.gz

The files are LeagueGameLog-shaped and split by month exactly as nba_snapshot writes them,
so every reader (nba_snapshot.league_log/player_log, nba_grades.season_log, the dashboard)
works unchanged in snapshot mode. Earlier seasons stay as the home PC exported them; they
are final.

Player ids are NBA ids, so a player's rows join his earlier seasons. ESPN ids are mapped
through, in order: the saved map (nba_data/espn-nba-ids.csv, so an id never changes once
given), sportsdataverse's ESPN-to-NBA crosswalk, a name match against the saved PlayerIndex
and game logs, and last a synthetic id (900,000,000 + the ESPN id) for a player found in
none of them. Play-in games (ESPN season type 5) are not written: the snapshot has no
play-in file, so a play-in prop stays ungraded.
"""
from __future__ import annotations

import datetime
import time
import unicodedata

import pandas as pd
import requests

import nba_snapshot as ns

_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
_CROSSWALK = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
              "nba_crosswalk/nba_player_crosswalk_{year}.parquet")
_IDS = ns.DIR / "espn-nba-ids.csv"
_ESPN_TO_NBA = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}
_TYPES = {2: ("Regular Season", "2"), 3: ("Playoffs", "4")}     # ESPN type -> (snapshot, SEASON_ID prefix)
_SYNTHETIC = 900_000_000
_COLS = ["SEASON_ID", "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME",
         "GAME_ID", "GAME_DATE", "MATCHUP", "WL", "MIN", "FGM", "FGA", "FG_PCT", "FG3M", "FG3A",
         "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV",
         "PF", "PTS", "PLUS_MINUS", "FANTASY_PTS", "VIDEO_AVAILABLE"]


def _get(path: str, **params) -> dict:
    for attempt in range(3):
        try:
            r = requests.get(f"{_SITE}/{path}", params=params, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2.0 * (attempt + 1))


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "").replace(",", "").replace("-", " ").strip()
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return " ".join(s.split())


def _teams() -> dict:
    """NBA abbreviation -> (team id, full name), from nba_api's bundled table (no network)."""
    from nba_api.stats.static import teams
    return {t["abbreviation"]: (t["id"], t["full_name"]) for t in teams.get_teams()}


# ── player ids ────────────────────────────────────────────────────────────────────────────

class _Ids:
    def __init__(self, season: str):
        self.saved = {}
        if _IDS.exists():
            m = pd.read_csv(_IDS, dtype=str)
            self.saved = dict(zip(m["espn_id"], m["nba_id"].astype(int)))
        self.added = False
        self._season = season
        self._cross = self._names = None

    def _crosswalk(self) -> dict:
        if self._cross is None:
            self._cross = {}
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
            self._names = {_norm(n): i for n, i in ns.name_ids("00").items()}
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

    def save(self) -> bool:
        if not self.added:
            return False
        pd.DataFrame(sorted(self.saved.items()), columns=["espn_id", "nba_id"]).to_csv(
            _IDS, index=False, lineterminator="\n")
        return True


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


def game_rows(event_id: str, day: datetime.date, season: str, s_prefix: str,
              ids: _Ids, teams: dict) -> list:
    """LeagueGameLog rows for one finished game: every player who played."""
    s = _get("summary", event=event_id)
    comp = s["header"]["competitions"][0]
    side = {}
    for c in comp["competitors"]:
        abbr = _ESPN_TO_NBA.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
        side[c["team"]["id"]] = (abbr, c["homeAway"], bool(c.get("winner")))
    rows = []
    for t in s.get("boxscore", {}).get("players", []):
        abbr, home_away, won = side[t["team"]["id"]]
        opp = next(a for tid, (a, _h, _w) in side.items() if tid != t["team"]["id"])
        team_id, team_name = teams.get(abbr, (0, abbr))
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

def _existing(season: str, s_type: str) -> pd.DataFrame:
    return ns.league_log(season, s_type, "00")


def refresh(today: datetime.date | None = None, max_days: int = 45) -> list[str]:
    """
    Fetch every finished game since the last one saved (re-reading the last two days, which
    catches stat corrections and games that ended after the previous run) and write the
    month files that changed. Returns the changed file names.
    """
    import parlay_model as pm
    today = today or datetime.date.today()
    season = pm.nba_season_strings(today)[0]
    ids, teams = _Ids(season), _teams()
    have = pd.concat([_existing(season, st) for st, _p in _TYPES.values()], ignore_index=True)
    if have.empty:
        start = datetime.date(int(season[:4]), 10, 1)
    else:
        start = pd.to_datetime(have["GAME_DATE"]).max().date() - datetime.timedelta(days=2)
    start = max(start, today - datetime.timedelta(days=max_days))

    new = {st: [] for st, _p in _TYPES.values()}
    day = start
    while day <= today:
        board = _get("scoreboard", dates=day.strftime("%Y%m%d"))
        for e in board.get("events", []):
            kind = _TYPES.get(int(e.get("season", {}).get("type", 0)))
            if kind is None or not e["status"]["type"].get("completed"):
                continue                        # preseason, play-in, or not over yet
            try:
                new[kind[0]] += game_rows(e["id"], day, season, kind[1], ids, teams)
            except Exception as ex:
                print(f"  skip {e.get('shortName')} {day}: {type(ex).__name__}: {ex}")
        day += datetime.timedelta(days=1)

    changed = []
    for s_type, rows in new.items():
        if not rows:
            continue
        fresh = pd.DataFrame(rows, columns=_COLS)
        old = _existing(season, s_type)
        if not old.empty:
            old = old[~old["GAME_ID"].astype(str).isin(set(fresh["GAME_ID"]))]
        df = pd.concat([old, fresh], ignore_index=True)[_COLS]
        df = df.sort_values(["GAME_DATE", "GAME_ID", "PLAYER_ID"]).reset_index(drop=True)
        for month, part in df.groupby(df["GAME_DATE"].astype(str).str[:7]):
            ns._write(ns.DIR / f"{ns._stem('00', season, s_type)}-{month}.csv.gz", part, changed)
    if ids.save():
        changed.append(_IDS.name)
    ns._CACHE.clear()
    return changed


if __name__ == "__main__":
    names = refresh()
    print(f"ESPN NBA log: {len(names)} file(s) updated" + (": " + ", ".join(names) if names else ""))
