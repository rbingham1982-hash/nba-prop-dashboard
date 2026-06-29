import sys, requests, time, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

MLB_BASE = "https://statsapi.mlb.com/api/v1"
MLB_SEASON = "2026"

# ── Full park factor table from dashboard ──────────────────────────────────
PARK_FACTORS = {
    "Coors Field": 140, "Great American Ball Park": 125,
    "Camden Yards": 120, "Oriole Park at Camden Yards": 120,
    "Yankee Stadium": 118, "Globe Life Field": 115, "Citizens Bank Park": 112,
    "Rogers Centre": 112, "Chase Field": 110, "Fenway Park": 108,
    "American Family Field": 105, "Wrigley Field": 105, "Truist Park": 103,
    "Guaranteed Rate Field": 103, "Minute Maid Park": 102, "Nationals Park": 102,
    "Busch Stadium": 100, "Progressive Field": 100, "Angel Stadium": 99,
    "Dodger Stadium": 98, "Target Field": 98, "Citi Field": 96, "PNC Park": 96,
    "Tropicana Field": 95, "T-Mobile Park": 90, "loanDepot park": 90,
    "Kauffman Stadium": 88, "Comerica Park": 85, "Petco Park": 82, "Oracle Park": 76,
    "Sutter Health Park": 120, "Las Vegas Ballpark": 120,
}
DOMES = {
    "Chase Field", "Globe Life Field", "American Family Field",
    "Rogers Centre", "Minute Maid Park", "Tropicana Field", "loanDepot park",
}
VENUE_COORDS = {
    "Coors Field": (39.7559, -104.9942),
    "Great American Ball Park": (39.0979, -84.5082),
    "Camden Yards": (39.2838, -76.6217),
    "Oriole Park at Camden Yards": (39.2838, -76.6217),
    "Yankee Stadium": (40.8296, -73.9262),
    "Globe Life Field": (32.7473, -97.0829),
    "Fenway Park": (42.3467, -71.0972),
    "Chase Field": (33.4453, -112.0667),
    "American Family Field": (43.0280, -87.9712),
    "Wrigley Field": (41.9484, -87.6553),
    "Truist Park": (33.8908, -84.4678),
    "PNC Park": (40.4468, -80.0057),
    "Busch Stadium": (38.6226, -90.1928),
    "Guaranteed Rate Field": (41.8300, -87.6339),
    "Kauffman Stadium": (39.0514, -94.4803),
    "Angel Stadium": (33.8003, -117.8827),
    "Citi Field": (40.7571, -73.8458),
    "Citizens Bank Park": (39.9061, -75.1665),
    "Rogers Centre": (43.6414, -79.3894),
    "Minute Maid Park": (29.7572, -95.3555),
    "Dodger Stadium": (34.0739, -118.2400),
    "Oracle Park": (37.7786, -122.3893),
    "Petco Park": (32.7076, -117.1570),
    "Comerica Park": (42.3390, -83.0485),
    "T-Mobile Park": (47.5914, -122.3325),
    "Progressive Field": (41.4962, -81.6852),
    "loanDepot park": (25.7781, -80.2197),
    "Target Field": (44.9817, -93.2784),
    "Tropicana Field": (27.7683, -82.6534),
    "Nationals Park": (38.8730, -77.0074),
    "Sutter Health Park": (38.5800, -121.5142),
    "Las Vegas Ballpark": (36.1699, -115.1398),
}

# ── Games to analyze ───────────────────────────────────────────────────────
# Derived from 371 resolved MLB HR legs: avg predicted 35.3%, avg actual 17.0%
HR_CAL_FACTOR = 0.48

TARGET_GAMES = {
    ("BOS", "COL"),
    ("BAL", "LAA"),
    ("ATL", "SD"),
}

def get_weather(venue):
    coords = VENUE_COORDS.get(venue)
    if not coords or venue in DOMES:
        return {"temp_f": 75, "wind_mph": 0}
    try:
        lat, lon = coords
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               f"&current=temperature_2m,wind_speed_10m"
               f"&wind_speed_unit=mph&temperature_unit=fahrenheit&forecast_days=1")
        cur = requests.get(url, timeout=6).json().get("current", {})
        return {
            "temp_f": round(float(cur.get("temperature_2m", 75)), 1),
            "wind_mph": round(float(cur.get("wind_speed_10m", 5)), 1),
        }
    except:
        return {"temp_f": 75, "wind_mph": 5}

def context_boost(venue, weather):
    pf = PARK_FACTORS.get(venue, 100)
    park = (pf - 100) / 100 * 0.20
    temp = max(-0.06, min(0.08, (weather.get("temp_f", 70) - 70) / 5 * 0.01))
    wind = 0.0 if venue in DOMES else min(0.08, weather.get("wind_mph", 0) / 20 * 0.08)
    return 1.0 + park + temp + wind, pf

def pitcher_hr9(pid):
    if not pid:
        return 1.1
    try:
        url = f"{MLB_BASE}/people/{pid}/stats?stats=season&season={MLB_SEASON}&group=pitching"
        splits = requests.get(url, timeout=8).json().get("stats", [{}])[0].get("splits", [])
        if splits:
            s = splits[0]["stat"]
            ip_str = str(s.get("inningsPitched", "0") or "0")
            parts = ip_str.split(".")
            ip = int(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)
            hr = int(s.get("homeRuns", 0) or 0)
            if ip >= 20:
                return round(hr / ip * 9, 2)
    except:
        pass
    return 1.1

def get_roster(team_id):
    try:
        url = f"{MLB_BASE}/teams/{team_id}/roster?season={MLB_SEASON}&rosterType=active"
        resp = requests.get(url, timeout=10).json()
        hitters = []
        for p in resp.get("roster", []):
            pos = p.get("position", {}).get("abbreviation", "")
            if pos not in ("P", "RP", "SP"):
                hitters.append({"name": p["person"]["fullName"], "id": p["person"]["id"]})
        return hitters
    except:
        return []

def get_hitting_logs(pid):
    frames = []
    for season in ("2025", "2026"):
        try:
            url = f"{MLB_BASE}/people/{pid}/stats?stats=gameLog&season={season}&group=hitting"
            splits = requests.get(url, timeout=10).json().get("stats", [{}])[0].get("splits", [])
            rows = []
            for s in splits:
                st = s.get("stat", {})
                rows.append({
                    "HR": int(st.get("homeRuns", 0) or 0),
                    "AB": int(st.get("atBats", 0) or 0),
                    "SLG": float(st.get("slg", 0) or 0),
                })
            if rows:
                frames.append(pd.DataFrame(rows))
        except:
            pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

def get_player_hand(pid):
    try:
        resp = requests.get(f"{MLB_BASE}/people/{pid}", timeout=6).json()
        person = (resp.get("people") or [{}])[0]
        return person.get("batSide", {}).get("code", "R"), person.get("pitchHand", {}).get("code", "R")
    except:
        return "R", "R"

# ── Fetch today's schedule ─────────────────────────────────────────────────
today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
print(f"Fetching schedule for {today}…")
sched = requests.get(
    f"{MLB_BASE}/schedule?sportId=1&date={today}&hydrate=probablePitcher,team,venue",
    timeout=10
).json()

all_games = []
for de in sched.get("dates", []):
    for g in de.get("games", []):
        at = g["teams"]["away"]
        ht = g["teams"]["home"]
        ap = at.get("probablePitcher", {})
        hp = ht.get("probablePitcher", {})
        away_abbr = at["team"].get("abbreviation", "")
        home_abbr = ht["team"].get("abbreviation", "")
        all_games.append({
            "away_abbr": away_abbr,
            "home_abbr": home_abbr,
            "away_id": at["team"]["id"],
            "home_id": ht["team"]["id"],
            "away_pid": ap.get("id"),
            "away_pitcher": ap.get("fullName", "TBD"),
            "home_pid": hp.get("id"),
            "home_pitcher": hp.get("fullName", "TBD"),
            "venue": g.get("venue", {}).get("name", ""),
        })

# Filter to the 3 target games
games = [g for g in all_games
         if (g["away_abbr"], g["home_abbr"]) in TARGET_GAMES]

if not games:
    print("\nNo matching games found. Games on schedule today:")
    for g in all_games:
        print(f"  {g['away_abbr']} @ {g['home_abbr']}  — {g['venue']}")
    sys.exit(0)

# ── Score hitters ──────────────────────────────────────────────────────────
candidates = []

for g in games:
    venue = g["venue"]
    weather = get_weather(venue)
    mult, pf = context_boost(venue, weather)
    dome_str = " (dome)" if venue in DOMES else f" / {weather['wind_mph']:.0f} mph wind"
    print(f"\n{'─'*60}")
    print(f"  {g['away_abbr']} @ {g['home_abbr']}  |  {venue}  (PF {pf})")
    print(f"  Weather: {weather['temp_f']:.0f}°F{dome_str}")
    print(f"  Away SP: {g['away_pitcher']}  |  Home SP: {g['home_pitcher']}")

    for side, team_abbr, team_id, opp_pid, opp_name in [
        ("home", g["home_abbr"], g["home_id"], g["away_pid"], g["away_pitcher"]),
        ("away", g["away_abbr"], g["away_id"], g["home_pid"], g["home_pitcher"]),
    ]:
        p_hr9 = pitcher_hr9(opp_pid)
        pitcher_boost = min(0.12, max(-0.08, (p_hr9 - 1.1) / 1.0 * 0.10))
        p_hand = get_player_hand(opp_pid)[1] if opp_pid else "R"

        hitters = get_roster(team_id)
        print(f"  Scoring {len(hitters)} {team_abbr} hitters vs {opp_name} (HR/9={p_hr9:.2f})…")
        for h in hitters[:15]:
            pid = h["id"]
            df = get_hitting_logs(pid)
            if df.empty or "HR" not in df.columns or len(df) < 5:
                continue
            gp = len(df)
            last20 = df["HR"].values[-20:]
            last15 = df["HR"].values[-15:] if gp >= 15 else df["HR"].values
            last10 = df["HR"].values[-10:] if gp >= 10 else df["HR"].values
            last7  = df["HR"].values[-7:]  if gp >= 7  else df["HR"].values
            last5  = df["HR"].values[-5:]  if gp >= 5  else df["HR"].values

            # Mirror dashboard: skip players with 0 HR in last 15 (enough sample)
            if gp >= 15 and int((last15 > 0).sum()) == 0:
                continue

            r20 = float((last20 > 0).sum()) / len(last20)
            r10 = float((last10 > 0).sum()) / max(len(last10), 1)
            r5  = float((last5  > 0).sum()) / max(len(last5),  1)
            hist = 0.35 * r5 + 0.40 * r10 + 0.25 * r20
            if int((last7 > 0).sum()) == 0:
                hist *= 0.60

            b_hand = get_player_hand(pid)[0]
            platoon = 0.03 if (b_hand in ("L", "R") and b_hand != p_hand) else 0.0
            if b_hand == "S":
                platoon = 0.015

            raw = (hist + pitcher_boost + platoon) * mult
            score = round(min(0.97, max(0.01, raw * HR_CAL_FACTOR)), 3)
            slg = float(df["SLG"].iloc[-1]) if "SLG" in df.columns else 0

            candidates.append({
                "player": h["name"], "team": team_abbr,
                "opp_pitcher": opp_name, "pitcher_hr9": p_hr9,
                "venue": venue, "park_factor": pf,
                "temp_f": weather["temp_f"], "wind_mph": weather["wind_mph"],
                "last10_hr": int((last10 > 0).sum()), "last20_hr": int((last20 > 0).sum()),
                "hist_rate": round(hist, 3), "platoon": round(platoon, 3),
                "pitcher_boost": round(pitcher_boost, 3),
                "context_mult": round(mult, 3),
                "slg": slg, "score": score,
                "game": f"{g['away_abbr']} @ {g['home_abbr']}",
            })
            time.sleep(0.04)

# ── Results: top 3 per game + overall top 6 ───────────────────────────────
candidates.sort(key=lambda x: x["score"], reverse=True)

print(f"\n\n{'═'*65}")
print("TOP HR CANDIDATES — BOS@COL  |  BAL@LAA  |  ATL@SD")
print(f"{'═'*65}")

# Top 3 per game
for game_key in [("BOS","COL"), ("BAL","LAA"), ("ATL","SD")]:
    label = f"{game_key[0]} @ {game_key[1]}"
    game_picks = [c for c in candidates if c["game"] == label][:3]
    if not game_picks:
        print(f"\n  {label} — no data")
        continue
    venue = game_picks[0]["venue"] if game_picks else ""
    pf    = game_picks[0]["park_factor"] if game_picks else 100
    print(f"\n  {label}  |  {venue}  (PF {pf})")
    print(f"  {'─'*57}")
    for i, p in enumerate(game_picks, 1):
        dome = venue in DOMES
        weather_str = f"{p['temp_f']:.0f}°F (dome)" if dome else f"{p['temp_f']:.0f}°F / {p['wind_mph']:.0f} mph"
        platoon_str = "✓ platoon adv" if p["platoon"] > 0 else "same hand"
        print(f"  #{i}  {p['player']:<22} ({p['team']})  HR prob: {p['score']*100:.1f}%")
        print(f"       vs {p['opp_pitcher']:<25}  HR/9={p['pitcher_hr9']:.2f}")
        print(f"       Last 10g: {p['last10_hr']} HR  |  Last 20g: {p['last20_hr']} HR  |  {platoon_str}")
        print(f"       Weather: {weather_str}  |  Context mult: {p['context_mult']:.3f}")

print(f"\n{'═'*65}")
print("BEST BET ACROSS ALL 3 GAMES (no team duplication)")
print(f"{'═'*65}")
seen_teams = set()
top_diverse = []
for c in candidates:
    if c["team"] not in seen_teams:
        seen_teams.add(c["team"])
        top_diverse.append(c)
    if len(top_diverse) == 6:
        break

for i, p in enumerate(top_diverse, 1):
    dome = p["venue"] in DOMES
    weather_str = f"{p['temp_f']:.0f}°F (dome)" if dome else f"{p['temp_f']:.0f}°F / {p['wind_mph']:.0f} mph"
    print(f"\n#{i}  {p['player']} ({p['team']})  —  {p['game']}")
    print(f"    vs {p['opp_pitcher']}  |  Pitcher HR/9: {p['pitcher_hr9']:.2f}")
    print(f"    Venue: {p['venue']} (PF {p['park_factor']})")
    print(f"    Weather: {weather_str}")
    print(f"    Last 10g: {p['last10_hr']} HR  |  Last 20g: {p['last20_hr']} HR")
    print(f"    Platoon adv: {'yes' if p['platoon'] > 0 else 'no'}  |  HR prob: {p['score']*100:.1f}%")
