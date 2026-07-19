import sys, requests, time, unicodedata, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

import parlay_model as pm

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

# The FanDuel market price is the spine of this generator. Historically it is the
# single best predictor of a home run — sharper than any recency signal — so the
# board is ranked by the vig-stripped market probability. The park/weather/pitcher
# model rides alongside only as an EDGE signal (model% - market%): it flags where
# our read disagrees with the book, it does not overrule the book.
#
# On 65 resolved HR legs the recency model's high-confidence bucket (40-60%
# predicted) hit just 13.6% — worse than its low-confidence picks — so the model's
# own probability is deliberately down-weighted here and shown for context/edge only.
HR_CAL_FACTOR = 0.48        # raw recency model overshoots ~2x; calibrated down
MODEL_WEIGHT  = 0.30        # blended prob = 0.30*model + 0.70*market
TOP_TO_SCORE  = 60          # cap model API calls to the top market threats
MIN_MARKET    = 0.08        # ignore sub-8% market longshots

def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())

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
    except Exception:
        return {"temp_f": 75, "wind_mph": 5}

def context_boost(venue, weather):
    pf = PARK_FACTORS.get(venue, 100)
    park = (pf - 100) / 100 * 0.20
    temp = max(-0.06, min(0.08, (weather.get("temp_f", 70) - 70) / 5 * 0.01))
    wind = 0.0 if venue in DOMES else min(0.08, weather.get("wind_mph", 0) / 20 * 0.08)
    return 1.0 + park + temp + wind, pf

_hr9_cache = {}
def pitcher_hr9(pid):
    if not pid:
        return 1.1
    if pid in _hr9_cache:
        return _hr9_cache[pid]
    val = 1.1
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
                val = round(hr / ip * 9, 2)
    except Exception:
        pass
    _hr9_cache[pid] = val
    return val

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
    except Exception:
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
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

_hand_cache = {}
def get_player_hand(pid):
    if pid in _hand_cache:
        return _hand_cache[pid]
    res = ("R", "R")
    try:
        resp = requests.get(f"{MLB_BASE}/people/{pid}", timeout=6).json()
        person = (resp.get("people") or [{}])[0]
        res = (person.get("batSide", {}).get("code", "R"),
               person.get("pitchHand", {}).get("code", "R"))
    except Exception:
        pass
    _hand_cache[pid] = res
    return res

def model_hr_prob(pid, opp_pid, mult):
    """Recency + pitcher + platoon model, calibrated. Returns (score, hist, l10, l20, slg)."""
    df = get_hitting_logs(pid)
    if df.empty or "HR" not in df.columns or len(df) < 5:
        return None
    gp = len(df)
    last20 = df["HR"].values[-20:]
    last15 = df["HR"].values[-15:] if gp >= 15 else df["HR"].values
    last10 = df["HR"].values[-10:] if gp >= 10 else df["HR"].values
    last7  = df["HR"].values[-7:]  if gp >= 7  else df["HR"].values
    last5  = df["HR"].values[-5:]  if gp >= 5  else df["HR"].values
    r20 = float((last20 > 0).sum()) / len(last20)
    r10 = float((last10 > 0).sum()) / max(len(last10), 1)
    r5  = float((last5  > 0).sum()) / max(len(last5),  1)
    hist = 0.35 * r5 + 0.40 * r10 + 0.25 * r20
    if int((last7 > 0).sum()) == 0:
        hist *= 0.60
    p_hr9 = pitcher_hr9(opp_pid)
    pitcher_boost = min(0.12, max(-0.08, (p_hr9 - 1.1) / 1.0 * 0.10))
    b_hand = get_player_hand(pid)[0]
    p_hand = get_player_hand(opp_pid)[1] if opp_pid else "R"
    platoon = 0.03 if (b_hand in ("L", "R") and b_hand != p_hand) else 0.0
    if b_hand == "S":
        platoon = 0.015
    raw = (hist + pitcher_boost + platoon) * mult
    score = round(min(0.97, max(0.01, raw * HR_CAL_FACTOR)), 3)
    slg = float(df["SLG"].iloc[-1]) if "SLG" in df.columns else 0
    return score, round(hist, 3), int((last10 > 0).sum()), int((last20 > 0).sum()), slg

# ── FanDuel HR market (the spine) ──────────────────────────────────────────
print("Fetching FanDuel MLB home-run lines…")
fd = pm.fetch_fanduel("mlb")
hr_market = {}
if not fd.empty:
    hrs = fd[fd["stat_type"] == "Home Runs"]
    for _, r in hrs.iterrows():
        hr_market[norm_name(r["player_name"])] = {
            "player": r["player_name"],
            "implied": float(r["implied_prob"]),
            "odds": int(r["american_odds"]),
            "fd_game": r.get("game_label", ""),
        }
print(f"  {len(hr_market)} players with a FanDuel 'To Hit A Home Run' price.")
if not hr_market:
    print("No FanDuel HR lines available right now (lines usually post closer to game time).")
    sys.exit(0)

# ── Today's schedule → per-player context (venue, opposing pitcher) ─────────
today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
print(f"Fetching schedule + rosters for {today}…")
sched = requests.get(
    f"{MLB_BASE}/schedule?sportId=1&date={today}&hydrate=probablePitcher,team,venue",
    timeout=10,
).json()

player_ctx = {}   # norm(name) -> context dict
for de in sched.get("dates", []):
    for g in de.get("games", []):
        at, ht = g["teams"]["away"], g["teams"]["home"]
        venue = g.get("venue", {}).get("name", "")
        weather = get_weather(venue)
        mult, pf = context_boost(venue, weather)
        label = f"{at['team'].get('abbreviation','')} @ {ht['team'].get('abbreviation','')}"
        for side, team, opp in (("away", at, ht), ("home", ht, at)):
            opp_prob = opp.get("probablePitcher", {}) or {}
            for h in get_roster(team["team"]["id"]):
                player_ctx[norm_name(h["name"])] = {
                    "pid": h["id"], "team": team["team"].get("abbreviation", ""),
                    "opp_pid": opp_prob.get("id"),
                    "opp_pitcher": opp_prob.get("fullName", "TBD"),
                    "venue": venue, "pf": pf, "mult": mult,
                    "temp_f": weather["temp_f"], "wind_mph": weather["wind_mph"],
                    "game": label, "dome": venue in DOMES,
                }

# ── Join market ∩ slate, score the top market threats ──────────────────────
joined = []
for key, mk in hr_market.items():
    if mk["implied"] < MIN_MARKET:
        continue
    ctx = player_ctx.get(key)
    if ctx:
        joined.append((key, mk, ctx))
joined.sort(key=lambda x: x[1]["implied"], reverse=True)
to_score = joined[:TOP_TO_SCORE]
print(f"  {len(joined)} FanDuel HR players matched to today's rosters; "
      f"scoring the top {len(to_score)} by market price…\n")

picks = []
for key, mk, ctx in to_score:
    m = model_hr_prob(ctx["pid"], ctx["opp_pid"], ctx["mult"])
    model_p, hist, l10, l20, slg = (m if m else (None, None, None, None, None))
    market_p = mk["implied"]
    blended = round(MODEL_WEIGHT * model_p + (1 - MODEL_WEIGHT) * market_p, 3) if model_p is not None else market_p
    edge = round(model_p - market_p, 3) if model_p is not None else None
    picks.append({
        "player": mk["player"], "team": ctx["team"], "game": ctx["game"],
        "market": market_p, "odds": mk["odds"], "model": model_p,
        "blended": blended, "edge": edge, "pitcher": ctx["opp_pitcher"],
        "hr9": pitcher_hr9(ctx["opp_pid"]), "venue": ctx["venue"], "pf": ctx["pf"],
        "temp_f": ctx["temp_f"], "wind_mph": ctx["wind_mph"], "dome": ctx["dome"],
        "l10": l10, "l20": l20, "slg": slg,
    })
    time.sleep(0.03)

def odds_fmt(o):
    return f"+{o}" if o > 0 else str(o)

def wx(p):
    return f"{p['temp_f']:.0f}°F (dome)" if p["dome"] else f"{p['temp_f']:.0f}°F / {p['wind_mph']:.0f} mph"

# ── Board 1: most likely to homer (ranked by market) ───────────────────────
picks.sort(key=lambda x: x["market"], reverse=True)
print("═" * 70)
print(f"  MOST LIKELY TO HOMER TODAY — ranked by FanDuel market price")
print("═" * 70)
print(f"  {'Player':<22} {'Team':<4} {'Mkt':>5} {'Odds':>6} {'Model':>6} {'Edge':>6}  Matchup")
print(f"  {'-'*68}")
for p in picks[:15]:
    model_s = f"{p['model']*100:.0f}%" if p["model"] is not None else "  —"
    edge_s  = f"{p['edge']*100:+.0f}%" if p["edge"] is not None else "  —"
    print(f"  {p['player']:<22} {p['team']:<4} {p['market']*100:>4.0f}% {odds_fmt(p['odds']):>6} "
          f"{model_s:>6} {edge_s:>6}  {p['game']} · PF{p['pf']}")

# ── Board 2: value plays (model sees more than the market) ──────────────────
value = [p for p in picks if p["edge"] is not None and p["edge"] > 0.03]
value.sort(key=lambda x: x["edge"], reverse=True)
print("\n" + "═" * 70)
print("  MODEL VALUE PLAYS — model HR% exceeds the market (treat as lean, not gospel)")
print("═" * 70)
if not value:
    print("  None today — the model doesn't beat the market on any priced hitter.")
for i, p in enumerate(value[:8], 1):
    print(f"\n  #{i}  {p['player']} ({p['team']}) — {p['game']}")
    print(f"      Market {p['market']*100:.0f}% ({odds_fmt(p['odds'])})  |  Model {p['model']*100:.0f}%  |  Edge {p['edge']*100:+.0f}%  |  Blended {p['blended']*100:.0f}%")
    print(f"      vs {p['pitcher']} (HR/9 {p['hr9']:.2f})  |  {p['venue']} (PF {p['pf']})  |  {wx(p)}")
    print(f"      Recent: {p['l10']} HR L10 · {p['l20']} HR L20  |  SLG {p['slg']:.3f}")

print(f"\n{'─'*70}")
print("Market price is the primary signal; the model is a secondary edge read.")
print("Recent-HR streaks are noisy — don't chase a hot bat the market has already priced.")
