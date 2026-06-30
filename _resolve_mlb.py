"""
Resolves outstanding MLB parlay legs.
Looks up real MLB gamePks from the schedule using game_label + date,
then fetches box scores to score each leg.
"""
import sys, json, time, requests
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# Abbreviation -> MLB team ID (2026 rosters)
ABBR_TO_ID = {
    "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112,
    "CWS": 145, "CIN": 113, "CLE": 114, "COL": 115, "DET": 116,
    "HOU": 117, "KC":  118, "LAA": 108, "LAD": 119, "MIA": 146,
    "MIL": 158, "MIN": 142, "NYM": 121, "NYY": 147, "OAK": 133,
    "ATH": 133, "PHI": 143, "PIT": 134, "SD":  135, "SF":  137,
    "SEA": 136, "STL": 138, "TB":  139, "TEX": 140, "TOR": 141,
    "WSH": 120, "WSN": 120,
}

ALIASES = {
    "AZ": "ARI",   # Arizona
    "ARI": "AZ",
    "OAK": "ATH",  # Oakland/Athletics
    "ATH": "OAK",
    "WSH": "WSN",
    "WSN": "WSH",
}

def _abbr_variants(abbr):
    out = {abbr}
    if abbr in ALIASES:
        out.add(ALIASES[abbr])
    return out

# ── Load log ───────────────────────────────────────────────────────────────────
with open("parlay_log.json", encoding="utf-8") as f:
    data = json.load(f)
parlays = data.get("parlays", [])

mlb_unresolved = [
    (i, p) for i, p in enumerate(parlays)
    if p.get("sport") == "MLB" and p.get("parlay_hit") is None
]
print(f"Unresolved MLB parlays: {len(mlb_unresolved)}")

# ── Build (date, away_abbr, home_abbr) -> gamePk lookup from schedule ─────────
# Collect unique (date, game_label) combos we need
needed_games = set()
for _, p in mlb_unresolved:
    date = p.get("generated_at", "")[:10]
    if not date:
        continue
    for leg in p.get("legs", []):
        gl = leg.get("game_label", "")
        if gl and "@" in gl:
            away, home = [t.strip() for t in gl.split("@", 1)]
            needed_games.add((date, away, home))

print(f"Unique (date, game) combos: {len(needed_games)}")

# Expand dates by ±1 to catch games scheduled the day before/after parlay generation
from datetime import datetime, timedelta
raw_dates = sorted(set(d for d, _, _ in needed_games))
dates_needed = sorted(set(
    (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")
    for d in raw_dates
    for delta in (-1, 0, 1)
))
print(f"Fetching schedules for {len(dates_needed)} dates (±1 day buffer)...")

ID_TO_ABBR = {v: k for k, v in ABBR_TO_ID.items()}

gamepk_cache = {}   # (date, away_abbr, home_abbr) -> gamePk
status_cache = {}   # gamePk -> status str

for date in dates_needed:
    try:
        r = requests.get(
            f"{MLB_BASE}/schedule?sportId=1&date={date}&hydrate=team",
            timeout=10
        ).json()
        for day in r.get("dates", []):
            for game in day.get("games", []):
                gpk    = game["gamePk"]
                status = game.get("status", {}).get("abstractGameState", "unknown")
                away_id = game["teams"]["away"]["team"].get("id", 0)
                home_id = game["teams"]["home"]["team"].get("id", 0)
                away_a  = game["teams"]["away"]["team"].get("abbreviation") \
                          or ID_TO_ABBR.get(away_id, "")
                home_a  = game["teams"]["home"]["team"].get("abbreviation") \
                          or ID_TO_ABBR.get(home_id, "")
                # store both the primary key and handle AZ/ARI alias
                for away_key in _abbr_variants(away_a):
                    for home_key in _abbr_variants(home_a):
                        gamepk_cache[(date, away_key, home_key)] = gpk
                status_cache[gpk] = status
                print(f"  {date}: {away_a} @ {home_a} → {gpk} [{status}]")
        time.sleep(0.2)
    except Exception as e:
        print(f"  WARN: schedule fetch failed for {date}: {e}")

print(f"Cached {len(gamepk_cache)} game-date mappings")

# ── Fetch box scores for needed gamePks ───────────────────────────────────────
# Use ±1 expansion to catch games generated night-before
needed_pks = set()
for (date, away, home) in needed_games:
    for away_v in _abbr_variants(away):
        for home_v in _abbr_variants(home):
            for delta in (0, 1, -1):
                chk = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")
                gpk = gamepk_cache.get((chk, away_v, home_v))
                if gpk:
                    needed_pks.add(gpk)
                    break

print(f"Fetching {len(needed_pks)} box scores...")
boxscore_cache = {}   # gamePk -> player_stats dict

for gpk in sorted(needed_pks):
    status = status_cache.get(gpk, "unknown")
    if status not in ("Final", "Game Over"):
        print(f"  {gpk}: {status} — skipping")
        continue
    try:
        bs = requests.get(f"{MLB_BASE}/game/{gpk}/boxscore", timeout=10).json()
        players = {}
        for side in ("away", "home"):
            for pid, pdata in bs.get("teams", {}).get(side, {}).get("players", {}).items():
                name = pdata.get("person", {}).get("fullName", "").lower().strip()
                if not name:
                    continue
                bat = pdata.get("stats", {}).get("batting",  {})
                pit = pdata.get("stats", {}).get("pitching", {})
                players[name] = {
                    "H":      int(bat.get("hits",         0) or 0),
                    "HR":     int(bat.get("homeRuns",      0) or 0),
                    "R":      int(bat.get("runs",          0) or 0),
                    "RBI":    int(bat.get("rbi",           0) or 0),
                    "SB":     int(bat.get("stolenBases",   0) or 0),
                    "BB_bat": int(bat.get("baseOnBalls",   0) or 0),
                    "K_bat":  int(bat.get("strikeOuts",    0) or 0),
                    "K_pit":  int(pit.get("strikeOuts",    0) or 0),
                    "ER":     int(pit.get("earnedRuns",    0) or 0),
                    "H_pit":  int(pit.get("hits",          0) or 0),
                    "BB_pit": int(pit.get("baseOnBalls",   0) or 0),
                }
        boxscore_cache[gpk] = players
        print(f"  {gpk}: Final ({len(players)} players)")
        time.sleep(0.2)
    except Exception as e:
        print(f"  WARN: boxscore {gpk} failed: {e}")

# ── Stat lookup helper ─────────────────────────────────────────────────────────
def player_stat(name_lower, stat_type, players):
    pdata = players.get(name_lower)
    if pdata is None:
        # fuzzy: match on last name only if unambiguous
        last = name_lower.split()[-1]
        matches = [(k, v) for k, v in players.items() if k.split()[-1] == last]
        if len(matches) == 1:
            pdata = matches[0][1]
    if pdata is None:
        return None, False
    st = stat_type
    if st == "Hits":
        return pdata["H"], True
    if st == "Home Runs":
        return pdata["HR"], True
    if st in ("Hits+Runs+RBIs", "Hits + Runs + RBIs"):
        return pdata["H"] + pdata["R"] + pdata["RBI"], True
    if st in ("Runs Scored", "Runs"):
        return pdata["R"], True
    if st == "Stolen Bases":
        return pdata["SB"], True
    if st in ("Pitcher Strikeouts", "Strikeouts"):
        return (pdata["K_pit"] if pdata["K_pit"] else pdata["K_bat"]), True
    if st == "Hitter Strikeouts":
        return pdata["K_bat"], True
    if st == "Earned Runs Allowed":
        return pdata["ER"], True
    if st == "Hits Allowed":
        return pdata["H_pit"], True
    if st in ("Walks", "Walks Allowed"):
        return (pdata["BB_pit"] or pdata["BB_bat"]), True
    return None, False

# ── Resolve ────────────────────────────────────────────────────────────────────
resolved_count = 0
leg_hits = leg_misses = leg_no_data = leg_not_final = 0
not_found_names = defaultdict(int)

for idx, p in mlb_unresolved:
    date = p.get("generated_at", "")[:10]
    legs = p.get("legs", [])
    all_resolved = True
    all_hit = True

    for leg in legs:
        gl   = leg.get("game_label", "")
        name = leg.get("player_name", "").lower().strip()
        stat = leg.get("stat_type", "")
        line = float(leg.get("line_score", 0))

        if not gl or "@" not in gl:
            leg["outcome"] = None
            all_resolved = False
            leg_no_data += 1
            continue

        away, home = [t.strip() for t in gl.split("@", 1)]
        # Try parlay date first, then ±1 day (generator runs night-before)
        gpk = None
        for away_v in _abbr_variants(away):
            for home_v in _abbr_variants(home):
                for delta in (0, 1, -1):
                    from datetime import datetime, timedelta
                    chk = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")
                    gpk = gamepk_cache.get((chk, away_v, home_v))
                    if gpk:
                        break
                if gpk:
                    break
            if gpk:
                break

        if not gpk:
            leg["outcome"] = None
            all_resolved = False
            leg_not_final += 1
            continue

        status = status_cache.get(gpk, "unknown")
        if status not in ("Final", "Game Over"):
            leg["outcome"] = None
            all_resolved = False
            leg_not_final += 1
            continue

        players = boxscore_cache.get(gpk, {})
        if not players:
            leg["outcome"] = None
            all_resolved = False
            leg_no_data += 1
            continue

        val, found = player_stat(name, stat, players)
        if not found or val is None:
            not_found_names[leg["player_name"]] += 1
            leg["outcome"] = None
            all_resolved = False
            leg_no_data += 1
            continue

        hit = val > line
        leg["outcome"]       = "hit" if hit else "miss"
        leg["actual_value"]  = val
        if hit:
            leg_hits += 1
        else:
            leg_misses += 1
            all_hit = False

    if all_resolved:
        p["parlay_hit"] = all_hit
        resolved_count += 1

# ── Results ────────────────────────────────────────────────────────────────────
print(f"\n{'='*62}")
print(f"  Parlays fully resolved: {resolved_count}")
print(f"  Leg hits:               {leg_hits}")
print(f"  Leg misses:             {leg_misses}")
print(f"  Legs no data/not final: {leg_no_data + leg_not_final}")
if not_found_names:
    print(f"\n  Players not found in boxscore ({len(not_found_names)} unique):")
    for name, cnt in sorted(not_found_names.items(), key=lambda x: -x[1])[:15]:
        print(f"    {name} ({cnt}x)")

mlb_done = [p for p in parlays if p.get("sport") == "MLB" and p.get("parlay_hit") is not None]
hits   = sum(1 for p in mlb_done if p["parlay_hit"] is True)
misses = sum(1 for p in mlb_done if p["parlay_hit"] is False)
print(f"\n  All-time MLB resolved: {len(mlb_done)}  ({hits} hit / {misses} miss = {hits/len(mlb_done)*100:.1f}% hit rate)")

by_n = defaultdict(lambda: [0, 0])
for p in mlb_done:
    n = len(p.get("legs", []))
    by_n[n][0 if p["parlay_hit"] else 1] += 1
for n in sorted(by_n):
    h, m = by_n[n]
    total = h + m
    print(f"    {n}-leg: {h}/{total}  ({h/total*100:.0f}% hit rate)")

# ── Save ───────────────────────────────────────────────────────────────────────
with open("parlay_log.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(f"\nSaved.")
