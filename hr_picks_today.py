import sys, os, json, math, requests, time, io, unicodedata
from collections import defaultdict
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")

import parlay_model as pm

MLB_BASE = "https://statsapi.mlb.com/api/v1"
MLB_SEASON = "2026"
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hr_market_log.json")

# ── Park factors + venue coords ─────────────────────────────────────────────
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
    "Coors Field": (39.7559, -104.9942), "Great American Ball Park": (39.0979, -84.5082),
    "Camden Yards": (39.2838, -76.6217), "Oriole Park at Camden Yards": (39.2838, -76.6217),
    "Yankee Stadium": (40.8296, -73.9262), "Globe Life Field": (32.7473, -97.0829),
    "Fenway Park": (42.3467, -71.0972), "Chase Field": (33.4453, -112.0667),
    "American Family Field": (43.0280, -87.9712), "Wrigley Field": (41.9484, -87.6553),
    "Truist Park": (33.8908, -84.4678), "PNC Park": (40.4468, -80.0057),
    "Busch Stadium": (38.6226, -90.1928), "Guaranteed Rate Field": (41.8300, -87.6339),
    "Kauffman Stadium": (39.0514, -94.4803), "Angel Stadium": (33.8003, -117.8827),
    "Citi Field": (40.7571, -73.8458), "Citizens Bank Park": (39.9061, -75.1665),
    "Rogers Centre": (43.6414, -79.3894), "Minute Maid Park": (29.7572, -95.3555),
    "Daikin Park": (29.7572, -95.3555), "Dodger Stadium": (34.0739, -118.2400),
    "Oracle Park": (37.7786, -122.3893), "Petco Park": (32.7076, -117.1570),
    "Comerica Park": (42.3390, -83.0485), "T-Mobile Park": (47.5914, -122.3325),
    "Progressive Field": (41.4962, -81.6852), "loanDepot park": (25.7781, -80.2197),
    "Target Field": (44.9817, -93.2784), "Tropicana Field": (27.7683, -82.6534),
    "Nationals Park": (38.8730, -77.0074), "Sutter Health Park": (38.5800, -121.5142),
    "Las Vegas Ballpark": (36.1699, -115.1398),
}
# Approximate compass bearing (deg from N) from home plate toward center field, used
# to turn wind DIRECTION into a signed out/in HR effect. These are approximations;
# the wind-direction term is capped small (±6%) precisely because the bearings are
# not surveyed. Parks not listed fall back to a magnitude-only tailwind assumption.
PARK_CF_BEARING = {
    "Wrigley Field": 24, "Fenway Park": 47, "Yankee Stadium": 22, "Coors Field": 5,
    "Dodger Stadium": 24, "Citi Field": 38, "Citizens Bank Park": 15,
    "Great American Ball Park": 60, "Camden Yards": 32, "Oriole Park at Camden Yards": 32,
    "Petco Park": 2, "Truist Park": 24, "PNC Park": 115, "Nationals Park": 30,
    "Busch Stadium": 62, "Comerica Park": 150, "Kauffman Stadium": 45, "Angel Stadium": 45,
    "Target Field": 60, "Progressive Field": 2, "T-Mobile Park": 60,
    "Guaranteed Rate Field": 130, "Oracle Park": 92,
}

# ── Model configuration ─────────────────────────────────────────────────────
# The FanDuel market price is the spine (best single HR predictor). The model is a
# secondary EDGE read built from Statcast advanced metrics and combined as a Poisson
# mean, then its overall scale is auto-calibrated to the market each run so the added
# factors change the RANKING/spread, not the global level — edges stay meaningful and
# the coefficient never has to be hand-tuned.
#
#   hr_index = eff_barrel * air_pull * pit_air * hardhit * platoon * pa_slot * context
#   lambda   = k * hr_index          (k fit so mean model ≈ mean market over the slate)
#   P(>=1 HR)= 1 - exp(-lambda)
#
# Factors (each a ratio to league, clamped, mild exponents to avoid double-counting
# the barrel rate, which already encodes contact quality):
#   eff_barrel : batter barrel × pitcher barrel-allowed / league   (log5 matchup)
#   air_pull   : batter fly-ball% and pull% — does a barrel become a HR (Tier 1)
#   pit_air    : pitcher fly-ball% — a flyball arm allows more HR at equal barrel (Tier 1)
#   hardhit    : batter hard-hit% — mild power stabiliser (Tier 2)
#   platoon    : batter ISO vs the starter's hand / overall ISO (Tier 2, rate-level)
#   pa_slot    : expected plate appearances by lineup slot — more chances (Tier 1)
#   context    : park factor, temperature, and signed wind DIRECTION (Tier 1)
LEAGUE_BARREL = 0.065
MODEL_WEIGHT  = 0.35
TOP_TO_SCORE  = 60
MIN_MARKET    = 0.08
VALUE_EDGE    = 0.03
PA_BY_SLOT = {1: 4.65, 2: 4.55, 3: 4.44, 4: 4.34, 5: 4.23, 6: 4.13, 7: 4.02, 8: 3.92, 9: 3.81}
PA_BASE = 4.23

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())

# ── Weather (temp + wind speed & direction) ─────────────────────────────────
def get_weather(venue):
    coords = VENUE_COORDS.get(venue)
    if not coords or venue in DOMES:
        return {"temp_f": 75, "wind_mph": 0, "wind_dir": None}
    try:
        lat, lon = coords
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               f"&current=temperature_2m,wind_speed_10m,wind_direction_10m"
               f"&wind_speed_unit=mph&temperature_unit=fahrenheit&forecast_days=1")
        cur = requests.get(url, timeout=6).json().get("current", {})
        return {
            "temp_f": round(float(cur.get("temperature_2m", 75)), 1),
            "wind_mph": round(float(cur.get("wind_speed_10m", 5)), 1),
            "wind_dir": cur.get("wind_direction_10m"),
        }
    except Exception:
        return {"temp_f": 75, "wind_mph": 5, "wind_dir": None}

def context_boost(venue, weather):
    pf = PARK_FACTORS.get(venue, 100)
    park = (pf - 100) / 100 * 0.20
    temp = clamp((weather.get("temp_f", 70) - 70) / 5 * 0.01, -0.06, 0.08)
    if venue in DOMES:
        wind = 0.0
    else:
        wmph = weather.get("wind_mph", 0) or 0
        wdir = weather.get("wind_dir")
        cf = PARK_CF_BEARING.get(venue)
        if wdir is not None and cf is not None:
            # Wind vector points toward (wind_dir + 180); positive when it blows out to CF.
            out_comp = math.cos(math.radians((wdir + 180) - cf))
            wind = clamp(out_comp * (wmph / 20) * 0.10, -0.06, 0.08)
        else:
            wind = min(0.06, wmph / 20 * 0.06)   # unknown orientation → mild tailwind assumption
    return 1.0 + park + temp + wind, pf

# ── Pitcher HR/9 (residual + fallback) ──────────────────────────────────────
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
        return [{"name": p["person"]["fullName"], "id": p["person"]["id"]}
                for p in resp.get("roster", [])
                if p.get("position", {}).get("abbreviation", "") not in ("P", "RP", "SP")]
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

# ── Platoon splits: batter ISO vs LHP / RHP (Tier 2, rate-level) ─────────────
_split_cache = {}
def batter_iso_split(pid):
    """Returns {'L': iso_vs_lhp, 'R': iso_vs_rhp, 'all': iso_overall} or {}."""
    if pid in _split_cache:
        return _split_cache[pid]
    out = {}
    try:
        url = (f"{MLB_BASE}/people/{pid}/stats?stats=statSplits&group=hitting"
               f"&season={MLB_SEASON}&sitCodes=vr,vl")
        for st in requests.get(url, timeout=8).json().get("stats", []):
            for sp in st.get("splits", []):
                d = sp.get("stat", {})
                try:
                    iso = float(d.get("slg", 0)) - float(d.get("avg", 0))
                    ab = int(d.get("atBats", 0) or 0)
                except (TypeError, ValueError):
                    continue
                code = sp.get("split", {}).get("code")
                if code == "vl" and ab >= 20:
                    out["L"] = iso
                elif code == "vr" and ab >= 20:
                    out["R"] = iso
    except Exception:
        pass
    _split_cache[pid] = out
    return out

# ── Statcast leaderboards (batter + pitcher), one fetch each ────────────────
_savant = {"batter": {}, "pitcher": {}}
_league = {}
def savant_load(kind):
    if _savant[kind]:
        return _savant[kind]
    sels = (["pa", "barrel_batted_rate", "xslg", "xiso", "hard_hit_percent",
             "flyballs_percent", "pull_percent"] if kind == "batter"
            else ["pa", "barrel_batted_rate", "flyballs_percent", "groundballs_percent",
                  "hard_hit_percent", "xslg"])
    url = ("https://baseballsavant.mlb.com/leaderboard/custom"
           f"?year={MLB_SEASON}&type={kind}&filter=&min=10"
           f"&selections={','.join(sels)}&chart=false&x={sels[1]}&y={sels[1]}&r=no"
           f"&chartType=beeswarm&sort={sels[1]}&sortDir=desc&csv=true")
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")))
        for _, row in df.iterrows():
            try:
                pid = int(row["player_id"])
            except (TypeError, ValueError):
                continue
            g = lambda c: (float(row[c]) if pd.notna(row.get(c)) else None)
            br = g("barrel_batted_rate")
            _savant[kind][pid] = {
                "barrel": br / 100 if br is not None else None,
                "fb": g("flyballs_percent"), "pull": g("pull_percent"),
                "gb": g("groundballs_percent"), "hardhit": g("hard_hit_percent"),
                "xiso": g("xiso"), "xslg": g("xslg"),
                "pa": int(row["pa"]) if pd.notna(row.get("pa")) else 0,
            }
        # league baselines over the more-stable regulars (pa>=100)
        reg = df[df["pa"] >= 100] if "pa" in df.columns else df
        _league[kind] = {
            "fb": float(reg["flyballs_percent"].mean()) if "flyballs_percent" in reg else 26.5,
            "pull": float(reg["pull_percent"].mean()) if "pull_percent" in reg else 39.7,
            "hardhit": float(reg["hard_hit_percent"].mean()) if "hard_hit_percent" in reg else 38.3,
        }
    except Exception as e:
        print(f"  Savant {kind} fetch failed ({e}); using fallbacks.")
        _league[kind] = {"fb": 26.5, "pull": 39.7, "hardhit": 38.3}
    return _savant[kind]

# ── HR index (proportional to the Poisson mean; k calibrated separately) ────
def hr_index(pid, opp_pid, mult, slot=None, use_platoon=False):
    b = savant_load("batter").get(pid) or {}
    barrel = b.get("barrel")
    if barrel is None:
        return None, {}          # no Statcast batter data → skip (fallback handled by caller)
    lgb = _league["batter"]
    p = savant_load("pitcher").get(opp_pid) or {}

    # log5 matchup barrel
    pbrl = p.get("barrel")
    eff_barrel = barrel * (pbrl / LEAGUE_BARREL if pbrl else 1.0)

    # batter air/pull conversion
    air_pull = 1.0
    if b.get("fb") is not None:
        air_pull *= clamp((b["fb"] / lgb["fb"]) ** 0.5, 0.75, 1.35)
    if b.get("pull") is not None:
        air_pull *= clamp((b["pull"] / lgb["pull"]) ** 0.3, 0.85, 1.20)

    # pitcher fly-ball tendency
    pit_air = clamp((p["fb"] / _league["pitcher"]["fb"]) ** 0.5, 0.80, 1.30) if p.get("fb") else 1.0

    # hard-hit stabiliser (mild)
    hardhit = clamp((b["hardhit"] / lgb["hardhit"]) ** 0.25, 0.90, 1.15) if b.get("hardhit") else 1.0

    # HR/9 residual (light, and the sole pitcher signal when Statcast is missing)
    p_hr9 = pitcher_hr9(opp_pid)
    if pbrl:
        hr9_f = 1 + 0.4 * (clamp(p_hr9 / 1.1, 0.7, 1.4) - 1)
    else:
        hr9_f = clamp(p_hr9 / 1.1, 0.7, 1.4)

    # platoon (rate-level ISO vs hand, else flat hand bonus). Only computed for the
    # detailed pass — the fit pass leaves it neutral to avoid 260 handedness calls.
    platoon = 1.0
    if use_platoon:
        b_hand = get_player_hand(pid)[0]
        p_hand = get_player_hand(opp_pid)[1] if opp_pid else "R"
        spl = batter_iso_split(pid)
        iso_all = b.get("xiso")
        iso_vs = spl.get(p_hand)
        if iso_vs is not None and iso_all and iso_all > 0:
            platoon = clamp(iso_vs / iso_all, 0.80, 1.30)
        elif b_hand == "S":
            platoon = 1.015
        elif b_hand in ("L", "R") and b_hand != p_hand:
            platoon = 1.03

    # lineup slot → expected PAs
    pa_f = (PA_BY_SLOT.get(slot, PA_BASE) / PA_BASE) if slot else 1.0

    idx = eff_barrel * air_pull * pit_air * hardhit * hr9_f * platoon * pa_f * mult
    detail = {
        "barrel": round(barrel * 100, 1), "pbrl": round(pbrl * 100, 1) if pbrl else None,
        "air_pull": round(air_pull, 3), "pit_air": round(pit_air, 3),
        "hardhit": round(hardhit, 3), "platoon": round(platoon, 3),
        "pa_f": round(pa_f, 3), "slot": slot, "xiso": b.get("xiso"),
        "xslg": b.get("xslg"), "fb": b.get("fb"), "pull": b.get("pull"),
        "pit_fb": p.get("fb"), "hr9": p_hr9,
    }
    return idx, detail

def fit_k(pairs):
    """Solve k so mean(1-exp(-k*idx)) matches mean(market) across the slate."""
    if len(pairs) < 5:
        return 1.7
    target = sum(m for _, m in pairs) / len(pairs)
    lo, hi = 0.05, 8.0
    for _ in range(40):
        k = (lo + hi) / 2
        avg = sum(1 - math.exp(-k * i) for i, _ in pairs) / len(pairs)
        if avg < target:
            lo = k
        else:
            hi = k
    return (lo + hi) / 2

# ── Persistent log + backfill resolver ──────────────────────────────────────
def load_log():
    try:
        return json.load(open(LOG_PATH, encoding="utf-8")) if os.path.exists(LOG_PATH) else []
    except Exception:
        return []

def resolve_log(entries, today):
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
    line = (f"\n  Log calibration on {n} resolved picks: actual {hit/n:.1%}  |  market avg {mkt:.1%}")
    if mdl:
        line += f"  |  model avg {sum(e['model'] for e in mdl)/len(mdl):.1%}"
    print(line)

# ══════════════════════════════════════════════════════════════════════════
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
    for _, r in fd[fd["stat_type"] == "Home Runs"].iterrows():
        hr_market[norm_name(r["player_name"])] = {
            "player": r["player_name"], "implied": float(r["implied_prob"]),
            "odds": int(r["american_odds"]),
        }
print(f"  {len(hr_market)} players with a FanDuel 'To Hit A Home Run' price.")
if not hr_market:
    print("No FanDuel HR lines available right now (lines usually post closer to game time).")
    sys.exit(0)

print("Loading Statcast leaderboards (batter + pitcher barrels, batted-ball, hard-hit)…")
savant_load("batter"); savant_load("pitcher")
print(f"  {len(_savant['batter'])} batters, {len(_savant['pitcher'])} pitchers.")

print(f"Fetching schedule + lineups + rosters for {today}…")
sched = requests.get(
    f"{MLB_BASE}/schedule?sportId=1&date={today}&hydrate=probablePitcher,team,venue,lineups",
    timeout=12,
).json()

player_ctx = {}
lineup_slot = {}         # pid -> batting order slot (1-9) when posted
for de in sched.get("dates", []):
    for g in de.get("games", []):
        at, ht = g["teams"]["away"], g["teams"]["home"]
        venue = g.get("venue", {}).get("name", "")
        weather = get_weather(venue)
        mult, pf = context_boost(venue, weather)
        label = f"{at['team'].get('abbreviation','')} @ {ht['team'].get('abbreviation','')}"
        lu = g.get("lineups", {}) or {}
        for arr_key in ("homePlayers", "awayPlayers"):
            for i, pl in enumerate(lu.get(arr_key, []) or [], 1):
                if pl.get("id"):
                    lineup_slot[pl["id"]] = i
        for side, team, opp in (("away", at, ht), ("home", ht, at)):
            opp_prob = opp.get("probablePitcher", {}) or {}
            for h in get_roster(team["team"]["id"]):
                player_ctx[norm_name(h["name"])] = {
                    "pid": h["id"], "team": team["team"].get("abbreviation", ""),
                    "opp_pid": opp_prob.get("id"), "opp_pitcher": opp_prob.get("fullName", "TBD"),
                    "venue": venue, "pf": pf, "mult": mult,
                    "temp_f": weather["temp_f"], "wind_mph": weather["wind_mph"],
                    "wind_dir": weather.get("wind_dir"), "game": label, "dome": venue in DOMES,
                }

# ── Pass A: fit k on the full priced slate (cheap index, no platoon calls) ──
joined = []
for key, mk in hr_market.items():
    if mk["implied"] < MIN_MARKET:
        continue
    ctx = player_ctx.get(key)
    if ctx:
        joined.append((mk, ctx))
joined.sort(key=lambda x: x[0]["implied"], reverse=True)

fit_pairs = []
for mk, ctx in joined:
    idx, _ = hr_index(ctx["pid"], ctx["opp_pid"], ctx["mult"],
                      slot=lineup_slot.get(ctx["pid"]), use_platoon=False)
    if idx is not None:
        fit_pairs.append((idx, mk["implied"]))
K = fit_k(fit_pairs)
n_lineups = sum(1 for _, ctx in joined if ctx["pid"] in lineup_slot)
print(f"  matched {len(joined)} priced hitters ({n_lineups} with posted lineups); "
      f"calibrated k={K:.2f} to market. Scoring top {min(TOP_TO_SCORE, len(joined))}…\n")

# ── Pass B: score the top market threats in full (adds rate-level platoon) ──
picks = []
for mk, ctx in joined[:TOP_TO_SCORE]:
    idx, det = hr_index(ctx["pid"], ctx["opp_pid"], ctx["mult"],
                        slot=lineup_slot.get(ctx["pid"]), use_platoon=True)
    if idx is None:
        continue
    model_p = round(min(0.60, max(0.01, 1 - math.exp(-K * idx))), 3)
    market_p = mk["implied"]
    picks.append({
        "player": mk["player"], "pid": ctx["pid"], "team": ctx["team"], "game": ctx["game"],
        "market": market_p, "odds": mk["odds"], "model": model_p,
        "blended": round(MODEL_WEIGHT * model_p + (1 - MODEL_WEIGHT) * market_p, 3),
        "edge": round(model_p - market_p, 3),
        "pitcher": ctx["opp_pitcher"], "venue": ctx["venue"], "pf": ctx["pf"],
        "temp_f": ctx["temp_f"], "wind_mph": ctx["wind_mph"], "dome": ctx["dome"], **det,
    })

def odds_fmt(o):
    return f"+{o}" if o > 0 else str(o)

def wx(p):
    return f"{p['temp_f']:.0f}°F (dome)" if p["dome"] else f"{p['temp_f']:.0f}°F / {p['wind_mph']:.0f} mph"

# ── Persist today's board ──────────────────────────────────────────────────
log_entries = [e for e in log_entries if e.get("date") != today]
for p in picks:
    log_entries.append({
        "date": today, "player": p["player"], "player_id": p["pid"], "team": p["team"],
        "game": p["game"], "line": 0.5, "market_implied": p["market"], "american_odds": p["odds"],
        "model": p["model"], "blended": p["blended"], "edge": p["edge"], "k": round(K, 3),
        "barrel_pct": p["barrel"], "pitcher_barrel_pct": p["pbrl"], "slot": p["slot"],
        "factors": {"air_pull": p["air_pull"], "pit_air": p["pit_air"], "hardhit": p["hardhit"],
                    "platoon": p["platoon"], "pa_f": p["pa_f"]},
        "venue": p["venue"], "pf": p["pf"], "outcome": None,
    })
try:
    json.dump(log_entries, open(LOG_PATH, "w", encoding="utf-8"), indent=1)
    print(f"Logged {len(picks)} HR market prices → {os.path.basename(LOG_PATH)}")
except Exception as e:
    print(f"(could not write log: {e})")

# ── Board 1: most likely to homer (ranked by market) ───────────────────────
picks.sort(key=lambda x: x["market"], reverse=True)
print("\n" + "═" * 78)
print("  MOST LIKELY TO HOMER TODAY — ranked by FanDuel market price")
print("═" * 78)
print(f"  {'Player':<21} {'Tm':<4} {'Mkt':>5} {'Odds':>6} {'Model':>6} {'Edge':>6} {'Brl':>4} {'Slot':>4}  Matchup")
print(f"  {'-'*76}")
for p in picks[:15]:
    slot = str(p["slot"]) if p["slot"] else "—"
    print(f"  {p['player']:<21} {p['team']:<4} {p['market']*100:>4.0f}% {odds_fmt(p['odds']):>6} "
          f"{p['model']*100:>5.0f}% {p['edge']*100:>+5.0f}% {p['barrel']:>4.1f} {slot:>4}  {p['game']} · PF{p['pf']}")

# ── Board 2: value plays (model beats market) ──────────────────────────────
value = [p for p in picks if p["edge"] > VALUE_EDGE]
value.sort(key=lambda x: x["edge"], reverse=True)
print("\n" + "═" * 78)
print("  MODEL VALUE PLAYS — advanced-metric model beats the market (lean, not gospel)")
print("═" * 78)
if not value:
    print("  None today — the model doesn't beat the market on any priced hitter.")
for i, p in enumerate(value[:8], 1):
    pbrl_s = f"allows {p['pbrl']:.1f}% barrels" if p.get("pbrl") is not None else f"HR/9 {p['hr9']:.2f}"
    xiso_s = f"xISO {p['xiso']:.3f}" if p.get("xiso") is not None else "xISO —"
    slot_s = f"bats {p['slot']}" if p["slot"] else "slot TBD"
    print(f"\n  #{i}  {p['player']} ({p['team']}) — {p['game']}  [{slot_s}]")
    print(f"      Market {p['market']*100:.0f}% ({odds_fmt(p['odds'])})  |  Model {p['model']*100:.0f}%  |  "
          f"Edge {p['edge']*100:+.0f}%  |  Blended {p['blended']*100:.0f}%")
    print(f"      Barrel {p['barrel']:.1f}% · {xiso_s} · FB {p['fb']:.0f}%/Pull {p['pull']:.0f}%  |  vs {p['pitcher']} ({pbrl_s}, FB {p['pit_fb'] or 0:.0f}%)")
    print(f"      factors: air/pull ×{p['air_pull']:.2f} · pit-air ×{p['pit_air']:.2f} · "
          f"platoon ×{p['platoon']:.2f} · PA ×{p['pa_f']:.2f}  |  {p['venue']} (PF {p['pf']}) · {wx(p)}")

calibration_report(log_entries)
print(f"\n{'─'*78}")
print(f"Market price is the primary signal; the model (k={K:.2f}, auto-fit to market) is a secondary edge read.")
print("Outcomes backfill automatically on the next run so each factor can be validated over time.")
