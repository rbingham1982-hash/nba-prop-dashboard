import sys, os, json, math, requests, time, io, unicodedata
from collections import defaultdict
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

import parlay_model as pm

MLB_BASE = "https://statsapi.mlb.com/api/v1"
MLB_SEASON = "2026"
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hr_market_log.json")

# ── Full park factor table from dashboard ──────────────────────────────────
PARK_FACTORS = {
    "Coors Field": 140, "Great American Ball Park": 125,
    "Camden Yards": 120, "Oriole Park at Camden Yards": 120,
    "Yankee Stadium": 118, "Globe Life Field": 115, "Citizens Bank Park": 112,
    "Rogers Centre": 112, "Chase Field": 110, "Fenway Park": 108,
    "American Family Field": 105, "Wrigley Field": 105, "Truist Park": 103,
    "Guaranteed Rate Field": 103, "Minute Maid Park": 102, "Daikin Park": 102,
    "Nationals Park": 102,
    "Busch Stadium": 100, "Progressive Field": 100, "Angel Stadium": 99,
    "Dodger Stadium": 98, "Target Field": 98, "Citi Field": 96, "PNC Park": 96,
    "Tropicana Field": 95, "T-Mobile Park": 90, "loanDepot park": 90,
    "Kauffman Stadium": 88, "Comerica Park": 85, "Petco Park": 82, "Oracle Park": 76,
    "Sutter Health Park": 120, "Las Vegas Ballpark": 120,
}
DOMES = {
    "Chase Field", "Globe Life Field", "American Family Field",
    "Rogers Centre", "Minute Maid Park", "Daikin Park", "Tropicana Field", "loanDepot park",
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
    "Daikin Park": (29.7572, -95.3555),
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

# The FanDuel market price is the spine — historically the single best predictor
# of a home run. The board is ranked by the vig market probability; the model
# rides alongside only as an EDGE read (model% - market%).
#
# The model is now built on Statcast BARREL RATE, not recent-HR streaks. On 65
# resolved legs the old recency model's high-confidence bucket hit just 13.6% —
# it had no discrimination. Barrel rate (barrels per batted ball) is the best
# *non-market* HR predictor: it measures the quality of contact that actually
# produces home runs, and it stabilises far faster than HR counts. The per-game
# HR rate is modelled as a Poisson mean:
#
#   lambda = barrel_rate * BARREL_TO_HR_GAME * park/weather * pitcher * platoon
#   P(>=1 HR) = 1 - exp(-lambda)
#
# BARREL_TO_HR_GAME folds "batted balls per game" (~3) and "HR per barrel" (~0.55)
# into one coefficient, tuned so the model lands on the same scale as the market.
BARREL_TO_HR_GAME = 1.65
LEAGUE_BARREL     = 0.065     # fallback barrel rate for players Savant hasn't qualified
MODEL_WEIGHT      = 0.35      # blended prob = 0.35*model + 0.65*market
TOP_TO_SCORE      = 60        # cap per-run scoring to the top market threats
MIN_MARKET        = 0.08      # ignore sub-8% market longshots
VALUE_EDGE        = 0.03      # model must beat market by this to be a "value play"

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

# ── Statcast barrel-rate leaderboards (one fetch each, keyed by MLBAM id) ───
# Batter barrel rate = quality of contact a hitter makes; pitcher barrel rate =
# quality of contact a pitcher allows. Both come from the same Savant leaderboard
# with type=batter / type=pitcher, and both use the MLBAM player_id we already key on.
_savant = {"batter": {}, "pitcher": {}}
def savant_barrels(kind):
    if _savant[kind]:
        return _savant[kind]
    url = ("https://baseballsavant.mlb.com/leaderboard/custom"
           f"?year={MLB_SEASON}&type={kind}&filter=&min=10"
           "&selections=pa,barrel_batted_rate,xslg&chart=false"
           "&x=barrel_batted_rate&y=barrel_batted_rate&r=no&chartType=beeswarm"
           "&sort=barrel_batted_rate&sortDir=desc&csv=true")
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")))
        for _, row in df.iterrows():
            try:
                pid = int(row["player_id"])
            except (TypeError, ValueError):
                continue
            br = row.get("barrel_batted_rate")
            _savant[kind][pid] = {
                "barrel": float(br) / 100 if pd.notna(br) else None,
                "xslg": float(row["xslg"]) if pd.notna(row.get("xslg")) else None,
                "pa": int(row["pa"]) if pd.notna(row.get("pa")) else 0,
            }
    except Exception as e:
        print(f"  Savant {kind} barrel fetch failed ({e}); using fallbacks.")
    return _savant[kind]

def model_hr_prob(pid, opp_pid, mult):
    """Barrel-rate Poisson HR model. Returns (prob, batter_barrel%, xslg, is_fallback, pitcher_barrel%)."""
    info = savant_barrels("batter").get(pid) or {}
    barrel = info.get("barrel")
    is_fallback = barrel is None
    if barrel is None:
        barrel = LEAGUE_BARREL
    # Pitcher signal: prefer barrel-allowed via the log5 odds ratio
    # (batter x pitcher / league), which is the standard way to combine two rate
    # stats. A pitcher who allows more barrels than league lifts the matchup rate;
    # a stingy one suppresses it. HR/9 stays as a light residual (it carries the
    # pitcher's HR-per-barrel tendency that barrel rate alone misses). When the
    # pitcher has no Statcast data, fall back to the HR/9 factor alone.
    pinfo = savant_barrels("pitcher").get(opp_pid) or {}
    pbrl = pinfo.get("barrel")
    p_hr9 = pitcher_hr9(opp_pid)
    if pbrl and pbrl > 0:
        eff_barrel = barrel * pbrl / LEAGUE_BARREL
        pitcher_factor = 1 + 0.4 * (min(1.4, max(0.7, p_hr9 / 1.1)) - 1)
        pbrl_pct = round(pbrl * 100, 1)
    else:
        eff_barrel = barrel
        pitcher_factor = min(1.4, max(0.7, p_hr9 / 1.1))
        pbrl_pct = None
    b_hand = get_player_hand(pid)[0]
    p_hand = get_player_hand(opp_pid)[1] if opp_pid else "R"
    platoon = 0.03 if (b_hand in ("L", "R") and b_hand != p_hand) else (0.015 if b_hand == "S" else 0.0)
    lam = eff_barrel * BARREL_TO_HR_GAME * mult * pitcher_factor * (1 + platoon)
    prob = round(min(0.60, max(0.01, 1 - math.exp(-lam))), 3)
    return prob, round(barrel * 100, 1), info.get("xslg"), is_fallback, pbrl_pct

# ── Persistent HR market log + backfill resolver ───────────────────────────
def load_log():
    try:
        return json.load(open(LOG_PATH, encoding="utf-8")) if os.path.exists(LOG_PATH) else []
    except Exception:
        return []

def resolve_log(entries, today):
    """Fill outcome (1/0) for past unresolved entries from each player's game log."""
    pending = [e for e in entries if e.get("outcome") is None
               and e.get("date", "") < today and e.get("player_id")]
    if not pending:
        return 0
    by_pid = defaultdict(list)
    for e in pending:
        by_pid[e["player_id"]].append(e)
    print(f"Resolving {len(pending)} past HR log entr(ies) across {len(by_pid)} player(s)…")
    resolved = 0
    for pid, es in by_pid.items():
        hr_by_date = {}
        for season in ("2025", "2026"):
            try:
                url = f"{MLB_BASE}/people/{pid}/stats?stats=gameLog&season={season}&group=hitting"
                splits = requests.get(url, timeout=8).json().get("stats", [{}])[0].get("splits", [])
                for s in splits:
                    d = s.get("date")
                    if d:
                        hr_by_date[d] = hr_by_date.get(d, 0) + int(s.get("stat", {}).get("homeRuns", 0) or 0)
            except Exception:
                pass
        for e in es:
            if e["date"] in hr_by_date:
                e["outcome"] = 1 if hr_by_date[e["date"]] > 0 else 0
                resolved += 1
        time.sleep(0.03)
    return resolved

def calibration_report(entries):
    done = [e for e in entries if e.get("outcome") in (0, 1)]
    if len(done) < 10:
        return
    n = len(done)
    hit = sum(e["outcome"] for e in done)
    mkt = sum(e.get("market_implied") or 0 for e in done) / n
    mdl = [e for e in done if isinstance(e.get("model"), (int, float))]
    print(f"\n  Log calibration on {n} resolved picks: actual {hit/n:.1%}  |  "
          f"market avg {mkt:.1%}" +
          (f"  |  model avg {sum(e['model'] for e in mdl)/len(mdl):.1%}" if mdl else ""))

# ── FanDuel HR market (the spine) ──────────────────────────────────────────
today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
log_entries = load_log()
try:
    done = resolve_log(log_entries, today)
    if done:
        json.dump(log_entries, open(LOG_PATH, "w", encoding="utf-8"), indent=1)
        print(f"  resolved {done} outcome(s).")
except Exception as e:
    print(f"  (resolver skipped: {e})")

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
        }
print(f"  {len(hr_market)} players with a FanDuel 'To Hit A Home Run' price.")
if not hr_market:
    print("No FanDuel HR lines available right now (lines usually post closer to game time).")
    sys.exit(0)

print("Loading Statcast barrel-rate leaderboards (batter + pitcher)…")
savant_barrels("batter")
savant_barrels("pitcher")
print(f"  {len(_savant['batter'])} batters, {len(_savant['pitcher'])} pitchers with barrel data.")

# ── Today's schedule → per-player context (venue, opposing pitcher) ─────────
print(f"Fetching schedule + rosters for {today}…")
sched = requests.get(
    f"{MLB_BASE}/schedule?sportId=1&date={today}&hydrate=probablePitcher,team,venue",
    timeout=10,
).json()

player_ctx = {}
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
        joined.append((mk, ctx))
joined.sort(key=lambda x: x[0]["implied"], reverse=True)
to_score = joined[:TOP_TO_SCORE]
print(f"  {len(joined)} FanDuel HR players matched to rosters; scoring the top {len(to_score)}…\n")

picks = []
for mk, ctx in to_score:
    model_p, barrel, xslg, fb, pbrl = model_hr_prob(ctx["pid"], ctx["opp_pid"], ctx["mult"])
    market_p = mk["implied"]
    blended = round(MODEL_WEIGHT * model_p + (1 - MODEL_WEIGHT) * market_p, 3)
    edge = round(model_p - market_p, 3)
    picks.append({
        "player": mk["player"], "pid": ctx["pid"], "team": ctx["team"], "game": ctx["game"],
        "market": market_p, "odds": mk["odds"], "model": model_p, "blended": blended,
        "edge": edge, "barrel": barrel, "xslg": xslg, "fallback": fb, "pbrl": pbrl,
        "pitcher": ctx["opp_pitcher"], "hr9": pitcher_hr9(ctx["opp_pid"]),
        "venue": ctx["venue"], "pf": ctx["pf"], "temp_f": ctx["temp_f"],
        "wind_mph": ctx["wind_mph"], "dome": ctx["dome"],
    })

def odds_fmt(o):
    return f"+{o}" if o > 0 else str(o)

def wx(p):
    return f"{p['temp_f']:.0f}°F (dome)" if p["dome"] else f"{p['temp_f']:.0f}°F / {p['wind_mph']:.0f} mph"

# ── Persist today's board (market price + model, outcome to be resolved) ────
log_entries = [e for e in log_entries if e.get("date") != today]
for p in picks:
    log_entries.append({
        "date": today, "player": p["player"], "player_id": p["pid"], "team": p["team"],
        "game": p["game"], "line": 0.5, "market_implied": p["market"],
        "american_odds": p["odds"], "model": p["model"], "blended": p["blended"],
        "edge": p["edge"], "barrel_pct": p["barrel"], "pitcher_barrel_pct": p["pbrl"],
        "venue": p["venue"], "pf": p["pf"], "outcome": None,
    })
try:
    json.dump(log_entries, open(LOG_PATH, "w", encoding="utf-8"), indent=1)
    print(f"Logged {len(picks)} HR market prices → {os.path.basename(LOG_PATH)}")
except Exception as e:
    print(f"(could not write log: {e})")

# ── Board 1: most likely to homer (ranked by market) ───────────────────────
picks.sort(key=lambda x: x["market"], reverse=True)
print("\n" + "═" * 74)
print("  MOST LIKELY TO HOMER TODAY — ranked by FanDuel market price")
print("═" * 74)
print(f"  {'Player':<22} {'Tm':<4} {'Mkt':>5} {'Odds':>6} {'Model':>6} {'Edge':>6} {'Brl%':>5}  Matchup")
print(f"  {'-'*72}")
for p in picks[:15]:
    brl = f"{p['barrel']:.1f}" + ("*" if p["fallback"] else "")
    print(f"  {p['player']:<22} {p['team']:<4} {p['market']*100:>4.0f}% {odds_fmt(p['odds']):>6} "
          f"{p['model']*100:>5.0f}% {p['edge']*100:>+5.0f}% {brl:>5}  {p['game']} · PF{p['pf']}")
print("  * = no Statcast barrel data (league-average fallback)")

# ── Board 2: value plays (model beats the market) ──────────────────────────
value = [p for p in picks if p["edge"] > VALUE_EDGE and not p["fallback"]]
value.sort(key=lambda x: x["edge"], reverse=True)
print("\n" + "═" * 74)
print("  MODEL VALUE PLAYS — barrel model beats the market (lean, not gospel)")
print("═" * 74)
if not value:
    print("  None today — the barrel model doesn't beat the market on any priced hitter.")
for i, p in enumerate(value[:8], 1):
    xslg_s = f"xSLG {p['xslg']:.3f}" if p["xslg"] is not None else "xSLG —"
    pbrl_s = f"allows {p['pbrl']:.1f}% barrels" if p.get("pbrl") is not None else f"HR/9 {p['hr9']:.2f}"
    print(f"\n  #{i}  {p['player']} ({p['team']}) — {p['game']}")
    print(f"      Market {p['market']*100:.0f}% ({odds_fmt(p['odds'])})  |  Model {p['model']*100:.0f}%  |  "
          f"Edge {p['edge']*100:+.0f}%  |  Blended {p['blended']*100:.0f}%")
    print(f"      Batter barrel {p['barrel']:.1f}%  |  {xslg_s}  |  vs {p['pitcher']} ({pbrl_s})")
    print(f"      {p['venue']} (PF {p['pf']})  |  {wx(p)}")

calibration_report(log_entries)
print(f"\n{'─'*74}")
print("Market price is the primary signal; the barrel model is a secondary edge read.")
print("Outcomes backfill automatically on the next run so edges can be validated over time.")
