"""
check_sources.py — can this machine reach the data the generator depends on?

Built to decide whether the daily run can leave the home PC. FanDuel and stats.nba.com
both treat datacenter addresses differently from residential ones, so the same code that
works at home can come back empty on a cloud runner. Each probe reports status, latency
and how much data came back; nothing is written to any log.

Run it at home first for a baseline, then on the candidate host, and compare.
Exit code is 1 if any required source fails, so a CI run shows red.
"""
from __future__ import annotations

import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

results = []          # (name, required, ok, detail)


def probe(name: str, required: bool = True):
    def wrap(fn):
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {str(e)[:200]}"
        detail = f"{detail}  ({time.time() - t0:.1f}s)"
        results.append((name, required, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
        return fn
    return wrap


@probe("where am I", required=False)
def _ip():
    d = requests.get("https://ipinfo.io/json", timeout=10).json()
    return True, f"{d.get('ip')} — {d.get('org')}, {d.get('city')}, {d.get('region')}"


def _fd_page(sport: str):
    import parlay_model as pm
    r = requests.get(f"{pm.FD_BASE}/content-managed-page",
                     params={"page": "CUSTOM", "customPageId": sport, "_ak": pm.FD_AK,
                             "timezone": "America/New_York"},
                     headers=pm.FD_HEADERS, timeout=20)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:120]!r}"
    events = r.json().get("attachments", {}).get("events", {})
    games = sum(1 for e in events.values() if " @ " in (e.get("name") or ""))
    return True, f"HTTP 200, {games} games listed"


for _s in ("nfl", "nba", "mlb", "wnba"):
    probe(f"FanDuel page [{_s}]")(lambda s=_s: _fd_page(s))


@probe("FanDuel props [nfl] (the generator's real fetch)")
def _fd_props():
    import parlay_model as pm
    df = pm.fetch_fanduel("nfl")
    return len(df) > 0, f"{len(df)} prop rows"


@probe("stats.nba.com league game log")
def _nba():
    # One attempt, generous timeout: the question is whether it answers at all, and
    # nba_grades' retry loop would only hide a block behind three slow failures.
    from nba_api.stats.endpoints import leaguegamelog
    df = leaguegamelog.LeagueGameLog(season="2025-26", player_or_team_abbreviation="P",
                                     season_type_all_star="Regular Season",
                                     timeout=60).get_data_frames()[0]
    return len(df) > 0, f"{len(df)} player-game rows"


@probe("stats.nba.com WNBA players (league 10)")
def _wnba():
    # WNBA grading and game logs go through the same host as the NBA, via league_id 10.
    from nba_api.stats.endpoints import commonallplayers
    df = commonallplayers.CommonAllPlayers(is_only_current_season=1, league_id="10",
                                           timeout=60).get_data_frames()[0]
    return len(df) > 0, f"{len(df)} players"


@probe("MLB statsapi schedule")
def _mlb():
    import statsapi
    games = statsapi.schedule(start_date="09/01/2026", end_date="09/07/2026", sportId=1)
    return len(games) > 0, f"{len(games)} games"


@probe("MLB statsapi boxscore", required=False)
def _mlb_box():
    r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1, "date": "2026-09-05"}, timeout=20)
    gid = r.json()["dates"][0]["games"][0]["gamePk"]
    b = requests.get(f"https://statsapi.mlb.com/api/v1/game/{gid}/boxscore", timeout=20)
    return b.status_code == 200, f"HTTP {b.status_code}"


@probe("ESPN scoreboard (game grading)")
def _espn():
    r = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
                     timeout=15)
    return r.status_code == 200, f"HTTP {r.status_code}, {len(r.json().get('events', []))} events"


@probe("cdn.nba.com scoreboard (a possible NBA fallback)", required=False)
def _nba_cdn():
    r = requests.get("https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/",
                              "Origin": "https://www.nba.com"},
                     timeout=15)
    return r.status_code == 200, f"HTTP {r.status_code}"


@probe("nflverse weekly parquet")
def _nflverse():
    import pandas as pd
    df = pd.read_parquet("https://github.com/nflverse/nflverse-data/releases/download/"
                         "stats_player/stats_player_week_2026.parquet")
    return len(df) > 0, f"{len(df)} rows"


@probe("Sleeper trending adds", required=False)
def _sleeper():
    r = requests.get("https://api.sleeper.app/v1/players/nfl/trending/add",
                     params={"limit": 5}, timeout=15)
    return r.status_code == 200, f"HTTP {r.status_code}, {len(r.json()) if r.ok else 0} players"


failed = [n for n, req, ok, _ in results if req and not ok]
print("\n" + ("All required sources reachable." if not failed
              else "BLOCKED: " + ", ".join(failed)))
sys.exit(1 if failed else 0)
