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


# ── NBA alternatives to stats.nba.com (2026-10-18) ───────────────────────────────────────
# stats.nba.com hangs for datacenter IPs, which is why NBA/WNBA stay on the home PC. These
# probe the candidate replacements. None is required: the question is which ones answer.

_UA = {"User-Agent": "Mozilla/5.0"}


@probe("ESPN NBA box score (game summary)", required=False)
def _espn_nba_box():
    sb = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                      params={"dates": "20260301"}, timeout=15).json()
    eid = sb["events"][0]["id"]
    s = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
                     params={"event": eid}, timeout=15).json()
    teams = s.get("boxscore", {}).get("players", [])
    n = sum(len(st.get("athletes", [])) for t in teams for st in t.get("statistics", []))
    keys = teams[0]["statistics"][0].get("keys", []) if teams else []
    return n > 0, f"{n} player rows; fields {keys[:14]}"


@probe("ESPN core API athlete season stats", required=False)
def _espn_core():
    # Nikola Jokic, 2025-26 regular season. The core API carries ESPN's own derived stats.
    r = requests.get("https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/"
                     "seasons/2026/types/2/athletes/3112335/statistics", timeout=15)
    cats = r.json().get("splits", {}).get("categories", []) if r.ok else []
    names = [s["name"] for c in cats for s in c.get("stats", [])]
    adv = [n for n in names if any(k in n.lower() for k in ("per", "rating", "usage", "true",
                                                            "efficiency", "estimated"))]
    return r.ok and bool(names), f"HTTP {r.status_code}, {len(names)} stats; advanced-ish {adv[:10]}"


@probe("cdn.nba.com box score (live-data JSON)", required=False)
def _nba_cdn_box():
    r = requests.get("https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022500900.json",
                     headers={**_UA, "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com"},
                     timeout=15)
    if not r.ok:
        return False, f"HTTP {r.status_code}"
    g = r.json()["game"]
    p = g["homeTeam"]["players"][0]["statistics"]
    return True, f"HTTP 200; player fields {list(p)[:12]}"


@probe("cdn.nba.com season schedule", required=False)
def _nba_cdn_sched():
    r = requests.get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
                     headers=_UA, timeout=20)
    if not r.ok:
        return False, f"HTTP {r.status_code}"
    d = r.json()["leagueSchedule"]
    return True, f"HTTP 200, season {d.get('seasonYear')}, {len(d.get('gameDates', []))} dates"


@probe("pbpstats API player totals", required=False)
def _pbpstats():
    r = requests.get("https://api.pbpstats.com/get-totals/nba",
                     params={"Season": "2025-26", "SeasonType": "Regular Season", "Type": "Player"},
                     headers=_UA, timeout=30)
    if not r.ok:
        return False, f"HTTP {r.status_code}: {r.text[:100]!r}"
    rows = r.json().get("multi_row_table_data", [])
    return bool(rows), f"{len(rows)} players; fields {sorted(rows[0])[:12] if rows else []}"


@probe("sportsdataverse NBA release (hoopR bulk data)", required=False)
def _sdv():
    import pandas as pd
    df = pd.read_parquet("https://github.com/sportsdataverse/sportsdataverse-data/releases/"
                         "download/espn_nba_player_boxscores/player_box_2026.parquet")
    return len(df) > 0, f"{len(df)} player-game rows, last game {df['game_date'].max()}"


@probe("Basketball-Reference advanced page (scrape only)", required=False)
def _bref():
    r = requests.get("https://www.basketball-reference.com/leagues/NBA_2026_advanced.html",
                     headers=_UA, timeout=20)
    return r.ok and "advanced" in r.text, f"HTTP {r.status_code}, {len(r.text) // 1024} KB"


failed = [n for n, req, ok, _ in results if req and not ok]
print("\n" + ("All required sources reachable." if not failed
              else "BLOCKED: " + ", ".join(failed)))
sys.exit(1 if failed else 0)
