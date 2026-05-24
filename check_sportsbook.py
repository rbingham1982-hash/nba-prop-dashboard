"""Run exact same fetch paths as the Sportsbook tab."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8')
import requests, pandas as pd

ODDS_API_KEY = "3810f18fda845575c1185b2a0bc55405"

_PP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://app.prizepicks.com/",
    "Origin": "https://app.prizepicks.com",
}
_PP_DEAD_STATUSES = {"final","cancelled","failed","lost","won","scored","no_contest"}
_PP_ODDS_IMPLIED = {"goblin": 0.62, "standard": 0.50, "demon": 0.38}

def _american_to_implied(odds):
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)

# ── get_prizepicks_with_team ─────────────────────────────────────────────────
print("=== get_prizepicks_with_team (NBA, league_id=7) ===")
for attempt in range(3):
    try:
        url = "https://api.prizepicks.com/projections?league_id=7&per_page=500&single_stat=true"
        resp = requests.get(url, headers=_PP_HEADERS, timeout=15)
        print(f"Status: {resp.status_code}")
        if resp.status_code != 200:
            continue
        payload = resp.json()
        included = payload.get("included", [])
        data     = payload.get("data", [])
        print(f"data items: {len(data)}, included items: {len(included)}")

        player_map = {}
        for item in included:
            if item.get("type") == "new_player":
                a = item.get("attributes", {})
                player_map[item["id"]] = {
                    "name": a.get("display_name", ""),
                    "team": a.get("team", a.get("team_name", "")),
                }
        print(f"Players in map: {len(player_map)}")

        game_map = {}
        for item in included:
            if item.get("type") == "game":
                a = item.get("attributes", {})
                meta = a.get("metadata", {})
                gteams = meta.get("game_info", {}).get("teams", {})
                away = gteams.get("away", {}).get("abbreviation", "")
                home = gteams.get("home", {}).get("abbreviation", "")
                label = f"{away} @ {home}" if away and home else ""
                game_map[item["id"]] = {"label": label}
        print(f"Games in map: {len(game_map)}")

        rows = []
        skipped_dead = 0
        skipped_no_player = 0
        for proj in data:
            if proj.get("type") != "projection":
                continue
            attrs = proj.get("attributes", {})
            if attrs.get("status", "pre_game") in _PP_DEAD_STATUSES:
                skipped_dead += 1
                continue
            rels = proj.get("relationships", {})
            pid  = rels.get("new_player", {}).get("data", {}).get("id", "")
            gid  = rels.get("game", {}).get("data", {}).get("id", "")
            pinfo = player_map.get(pid, {})
            ginfo = game_map.get(gid, {})
            rows.append({
                "player_name": pinfo.get("name", ""),
                "team":        pinfo.get("team", ""),
                "stat_type":   attrs.get("stat_type", ""),
                "line_score":  attrs.get("line_score"),
                "odds_type":   attrs.get("odds_type", "standard"),
                "game_label":  ginfo.get("label", ""),
            })

        print(f"Rows built: {len(rows)}, skipped_dead: {skipped_dead}, skipped_no_player: {skipped_no_player}")
        df = pd.DataFrame(rows)
        if not df.empty:
            with_player = df[df["player_name"] != ""]
            print(f"Rows with player name: {len(with_player)}")
            print(f"Rows WITHOUT player name: {len(df) - len(with_player)}")
            print(f"Stat types: {sorted(df['stat_type'].unique())[:8]}")
            print(f"Sample:\n{with_player.head(3).to_string()}")
        else:
            print("DataFrame is EMPTY")
        break
    except Exception as e:
        print(f"Attempt {attempt} error: {e}")

# ── get_the_odds_api_props (FanDuel NBA) ────────────────────────────────────
print("\n=== get_the_odds_api_props (FanDuel, NBA) ===")
from datetime import datetime, timedelta, timezone
try:
    events_resp = requests.get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/events",
        params={"apiKey": ODDS_API_KEY}, timeout=15
    )
    print(f"Events status: {events_resp.status_code}, credits remaining: {events_resp.headers.get('x-requests-remaining','?')}")
    all_events = events_resp.json() if events_resp.ok else []
    cutoff = datetime.now(timezone.utc) + timedelta(hours=24)
    today_events = []
    for ev in all_events:
        ct = ev.get("commence_time", "")
        try:
            dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            if dt <= cutoff:
                today_events.append(ev)
        except Exception:
            today_events.append(ev)
    today_events = today_events[:12]
    print(f"Events within 24h: {len(today_events)}")

    markets = ["player_points", "player_rebounds", "player_assists", "player_threes", "player_points_rebounds_assists"]
    market_map = {
        "player_points": "Points", "player_rebounds": "Rebounds",
        "player_assists": "Assists", "player_threes": "3-PT Made",
        "player_points_rebounds_assists": "Pts+Rebs+Asts",
    }

    rows = []
    for event in today_events:
        ev_id = event.get("id", "")
        game_label = f"{event.get('away_team','')} @ {event.get('home_team','')}"
        try:
            props_resp = requests.get(
                f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{ev_id}/odds",
                params={"apiKey": ODDS_API_KEY, "regions": "us",
                        "markets": ",".join(markets), "bookmakers": "fanduel", "oddsFormat": "american"},
                timeout=15
            )
        except Exception as e:
            print(f"  Event {ev_id} error: {e}")
            continue
        print(f"  {game_label}: status={props_resp.status_code} credits_left={props_resp.headers.get('x-requests-remaining','?')}")
        if props_resp.status_code != 200:
            print(f"    body: {props_resp.text[:200]}")
            continue
        event_data = props_resp.json()
        for bm in event_data.get("bookmakers", []):
            if bm.get("key") != "fanduel":
                continue
            for mkt in bm.get("markets", []):
                stat_type = market_map.get(mkt.get("key"))
                if not stat_type:
                    continue
                for outcome in mkt.get("outcomes", []):
                    if outcome.get("name","").lower() != "over":
                        continue
                    rows.append({
                        "player_name": outcome.get("description",""),
                        "stat_type": stat_type,
                        "line_score": outcome.get("point"),
                        "american_odds": int(outcome.get("price", -110)),
                        "game_label": game_label,
                        "sportsbook": "FanDuel",
                    })
    print(f"\nTotal FanDuel rows: {len(rows)}")
    if rows:
        df = pd.DataFrame(rows)
        print(f"Stat types: {sorted(df['stat_type'].unique())}")
        print(f"Sample:\n{df.head(5).to_string()}")
    else:
        print("NO FANDUEL DATA RETURNED")
except Exception as e:
    print(f"ERROR: {e}")
