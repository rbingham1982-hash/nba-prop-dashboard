import requests, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')

ODDS_API_KEY = "3810f18fda845575c1185b2a0bc55405"

# ── PrizePicks NBA ────────────────────────────────────────────────────────────
print("=== PrizePicks NBA (league_id=7) ===")
try:
    r = requests.get(
        "https://api.prizepicks.com/projections?league_id=7&per_page=500&single_stat=true",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://app.prizepicks.com"},
        timeout=15
    )
    print(f"Status: {r.status_code}")
    if r.ok:
        d = r.json()
        projs = [x for x in d.get("data", []) if x.get("type") == "projection"]
        print(f"Projections: {len(projs)}")
        if projs:
            a = projs[0]["attributes"]
            print(f"Sample: stat={a.get('stat_type')} line={a.get('line_score')} status={a.get('status')} odds={a.get('odds_type')}")
    else:
        print(f"Body: {r.text[:300]}")
except Exception as e:
    print(f"ERROR: {e}")

# ── PrizePicks MLB ────────────────────────────────────────────────────────────
print("\n=== PrizePicks MLB (league_id=2) ===")
try:
    r = requests.get(
        "https://api.prizepicks.com/projections?league_id=2&per_page=500&single_stat=true",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://app.prizepicks.com"},
        timeout=15
    )
    print(f"Status: {r.status_code}")
    if r.ok:
        d = r.json()
        projs = [x for x in d.get("data", []) if x.get("type") == "projection"]
        print(f"Projections: {len(projs)}")
        if projs:
            a = projs[0]["attributes"]
            print(f"Sample: stat={a.get('stat_type')} line={a.get('line_score')} status={a.get('status')}")
    else:
        print(f"Body: {r.text[:300]}")
except Exception as e:
    print(f"ERROR: {e}")

# ── The Odds API – events list (no credit cost) ───────────────────────────────
print("\n=== The Odds API – NBA events ===")
try:
    r = requests.get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/events",
        params={"apiKey": ODDS_API_KEY},
        timeout=15
    )
    print(f"Status: {r.status_code}")
    print(f"Remaining credits: {r.headers.get('x-requests-remaining','?')}")
    if r.ok:
        events = r.json()
        print(f"Events: {len(events)}")
        for ev in events[:3]:
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}  commence={ev.get('commence_time','')[:16]}")
    else:
        print(f"Body: {r.text[:300]}")
except Exception as e:
    print(f"ERROR: {e}")

# ── The Odds API – FanDuel player props for first event ───────────────────────
print("\n=== The Odds API – FanDuel NBA player props (first event) ===")
try:
    r = requests.get(
        "https://api.the-odds-api.com/v4/sports/basketball_nba/events",
        params={"apiKey": ODDS_API_KEY},
        timeout=15
    )
    events = r.json() if r.ok else []
    if events:
        ev_id = events[0]["id"]
        label = f"{events[0].get('away_team')} @ {events[0].get('home_team')}"
        print(f"Event: {label}  id={ev_id}")
        r2 = requests.get(
            f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{ev_id}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "player_points,player_rebounds,player_assists",
                "bookmakers": "fanduel",
                "oddsFormat": "american",
            },
            timeout=15
        )
        print(f"Props status: {r2.status_code}")
        print(f"Remaining credits after: {r2.headers.get('x-requests-remaining','?')}")
        if r2.ok:
            data = r2.json()
            bms = data.get("bookmakers", [])
            print(f"Bookmakers returned: {[b.get('key') for b in bms]}")
            for bm in bms:
                for mkt in bm.get("markets", []):
                    outcomes = mkt.get("outcomes", [])
                    overs = [o for o in outcomes if o.get("name","").lower()=="over"]
                    print(f"  {mkt.get('key')}: {len(overs)} over lines  sample={overs[0] if overs else 'none'}")
        else:
            print(f"Body: {r2.text[:400]}")
    else:
        print("No events found")
except Exception as e:
    print(f"ERROR: {e}")

# ── The Odds API – MLB events ─────────────────────────────────────────────────
print("\n=== The Odds API – MLB events ===")
try:
    r = requests.get(
        "https://api.the-odds-api.com/v4/sports/baseball_mlb/events",
        params={"apiKey": ODDS_API_KEY},
        timeout=15
    )
    print(f"Status: {r.status_code}")
    if r.ok:
        events = r.json()
        print(f"Events: {len(events)}")
        for ev in events[:3]:
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}  commence={ev.get('commence_time','')[:16]}")
    else:
        print(f"Body: {r.text[:300]}")
except Exception as e:
    print(f"ERROR: {e}")
