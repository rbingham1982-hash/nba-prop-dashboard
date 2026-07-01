"""
daily_parlay_gen.py
Runs at 2 pm daily via Windows Task Scheduler.
Fetches PrizePicks + Underdog lines for MLB, WNBA, and NBA (when in season),
scores each leg with the same historical hit-rate logic as the dashboard,
builds safe + value parlays, and logs them to parlay_log.json.
"""
import sys, time, os, requests, pandas as pd
from itertools import combinations
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8")

# parlay_tracker lives in the same directory
sys.path.insert(0, os.path.dirname(__file__))
import parlay_tracker

MLB_BASE   = "https://statsapi.mlb.com/api/v1"
MLB_SEASON = "2026"

PP_PAYOUTS      = {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0}
PP_ODDS_IMPLIED = {"goblin": 0.62, "standard": 0.50, "demon": 0.38}
PP_DEAD         = {"final", "postponed", "cancelled", "canceled", "suspended"}
PP_HEADERS      = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://app.prizepicks.com/",
}

PP_MLB_HIT_COL = {
    "Hits": "H", "Home Runs": "HR", "Stolen Bases": "SB",
    "Strikeouts": "K", "Hitter Strikeouts": "K",
    "Walks": "BB", "Runs Scored": "R", "Runs": "R",
    "Doubles": "2B", "Hits+Runs+RBIs": "H", "Plate Appearances": "AB",
}
PP_MLB_PIT_COL = {
    "Pitcher Strikeouts": "K", "Strikeouts": "K",
    "Earned Runs Allowed": "ER", "Walks Allowed": "BB",
    "Hits Allowed": "H", "Pitching Outs": "IP", "Pitches Thrown": "NP",
}
PP_PITCHER_TYPES = {
    "Pitcher Strikeouts", "Earned Runs Allowed", "Walks Allowed",
    "Hits Allowed", "Pitching Outs", "Pitches Thrown",
}
WNBA_COL_MAP = {
    "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
    "Steals": "STL", "Blocks": "BLK", "3-PT Made": "FG3M",
    "Pts+Rebs+Asts": "PRA", "Pts+Rebs": "PR", "Pts+Asts": "PA",
}
NBA_COL_MAP = {
    "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
    "Pts+Rebs+Asts": "PRA", "Pts+Asts": "PA", "Pts+Rebs": "PR",
    "3-PT Made": "FG3M", "Blocked Shots": "BLK", "Steals": "STL", "Turnovers": "TOV",
}

MLB_STAT_TYPES  = ["Hits", "Pitcher Strikeouts", "Home Runs", "Runs Scored"]
WNBA_STAT_TYPES = ["Points", "Rebounds", "Assists", "Pts+Rebs+Asts", "3-PT Made"]
NBA_STAT_TYPES  = ["Points", "Rebounds", "Assists", "Pts+Rebs+Asts", "3-PT Made"]

UD_MLB_STAT_MAP = {
    "Hits": "Hits", "Strikeouts": "Pitcher Strikeouts",
    "Pitcher Strikeouts": "Pitcher Strikeouts", "Runs": "Runs Scored",
    "Home Runs": "Home Runs", "Walks": "Walks",
    "Earned Runs": "Earned Runs Allowed", "Stolen Bases": "Stolen Bases",
}
UD_NBA_STAT_MAP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "3-Pointers Made": "3-PT Made", "Pts+Rebs+Asts": "Pts+Rebs+Asts",
    "Pts+Rebs": "Pts+Rebs", "Pts+Asts": "Pts+Asts",
    "Blocked Shots": "Blocked Shots", "Steals": "Steals", "Turnovers": "Turnovers",
}

# ── Calibration ────────────────────────────────────────────────────────────

def load_cal(sport):
    try:
        return parlay_tracker.get_calibration(sport=sport)
    except Exception:
        return {}

# ── PrizePicks API ─────────────────────────────────────────────────────────

def fetch_prizepicks(league_id: int) -> pd.DataFrame:
    dk_odds = {"goblin": -162, "standard": -100, "demon": 162}
    for attempt in range(3):
        try:
            if attempt:
                time.sleep(2)
            url = f"https://api.prizepicks.com/projections?league_id={league_id}&per_page=500&single_stat=true"
            resp = requests.get(url, headers=PP_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            payload = resp.json()
            player_map, game_map = {}, {}
            for item in payload.get("included", []):
                if item.get("type") == "new_player":
                    a = item["attributes"]
                    player_map[item["id"]] = {"name": a.get("display_name", ""), "team": a.get("team", a.get("team_name", ""))}
                elif item.get("type") == "game":
                    a = item["attributes"]
                    gteams = a.get("metadata", {}).get("game_info", {}).get("teams", {})
                    away = gteams.get("away", {}).get("abbreviation", "")
                    home = gteams.get("home", {}).get("abbreviation", "")
                    game_map[item["id"]] = {
                        "label": f"{away} @ {home}" if away and home else "",
                        "start_time": a.get("start_time", ""),
                    }
            rows = []
            for proj in payload.get("data", []):
                if proj.get("type") != "projection":
                    continue
                attrs = proj["attributes"]
                if attrs.get("status", "pre_game") in PP_DEAD:
                    continue
                rels = proj.get("relationships", {})
                pid = rels.get("new_player", {}).get("data", {}).get("id", "")
                gid = rels.get("game", {}).get("data", {}).get("id", "")
                ot  = attrs.get("odds_type", "standard")
                rows.append({
                    "player_name":  player_map.get(pid, {}).get("name", ""),
                    "team":         player_map.get(pid, {}).get("team", ""),
                    "stat_type":    attrs.get("stat_type", ""),
                    "line_score":   attrs.get("line_score"),
                    "odds_type":    ot,
                    "american_odds": dk_odds.get(ot, -100),
                    "implied_prob": PP_ODDS_IMPLIED.get(ot, 0.50),
                    "game_id":      gid,
                    "game_label":   game_map.get(gid, {}).get("label", ""),
                    "start_time":   game_map.get(gid, {}).get("start_time", attrs.get("start_time", "")),
                    "sportsbook":   "PrizePicks",
                })
            if rows:
                return pd.DataFrame(rows)
        except Exception as e:
            print(f"    PrizePicks attempt {attempt+1} failed: {e}")
    return pd.DataFrame()

# ── Underdog API ───────────────────────────────────────────────────────────

def fetch_underdog(sport: str) -> pd.DataFrame:
    sport_id = {"nba": "NBA", "wnba": "WNBA", "mlb": "MLB"}[sport]
    stat_map  = UD_MLB_STAT_MAP if sport == "mlb" else UD_NBA_STAT_MAP
    try:
        resp = requests.get(
            "https://api.underdogfantasy.com/beta/v5/over_under_lines",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return pd.DataFrame()
        d = resp.json()
        player_map     = {p["id"]: p for p in d.get("players", [])}
        appearance_map = {a["id"]: a for a in d.get("appearances", [])}
        game_map       = {g["id"]: g for g in d.get("games", [])}
        rows = []
        for line in d.get("over_under_lines", []):
            if line.get("status") != "active":
                continue
            ou       = line.get("over_under", {})
            app_stat = ou.get("appearance_stat", {})
            st_type  = stat_map.get(app_stat.get("display_stat", "").strip())
            if not st_type:
                continue
            app    = appearance_map.get(app_stat.get("appearance_id", ""), {})
            player = player_map.get(app.get("player_id", ""), {})
            if not player or player.get("sport_id") != sport_id:
                continue
            try:
                val = float(line.get("stat_value", 0))
            except Exception:
                continue
            game  = game_map.get(app.get("match_id"), {})
            name  = f"{player.get('first_name','')} {player.get('last_name','')}".strip()
            if not name:
                continue
            title = game.get("abbreviated_title", "")
            team  = ""
            if " @ " in title:
                away, home = title.split(" @ ", 1)
                team = away if app.get("team_id") == game.get("away_team_id") else home
            over_opt = next((o for o in line.get("options", []) if o.get("choice") == "higher"), None)
            if not over_opt:
                continue
            try:
                american = int(str(over_opt.get("american_price", "-110")).replace("+", ""))
            except Exception:
                american = -110
            rows.append({
                "player_name": name, "team": team, "stat_type": st_type,
                "line_score": val, "odds_type": "standard",
                "american_odds": american, "implied_prob": 0.50,
                "game_id": app.get("match_id", ""), "game_label": title,
                "start_time": game.get("scheduled_at", ""),
                "sportsbook": "Underdog",
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        print(f"    Underdog fetch failed: {e}")
        return pd.DataFrame()

# ── MLB player ID + game logs ──────────────────────────────────────────────

_mlb_name_map   = {}
_mlb_hit_cache  = {}
_mlb_pit_cache  = {}

def get_mlb_player_map():
    if _mlb_name_map:
        return _mlb_name_map
    for season in ("2025", "2026"):
        try:
            for p in requests.get(f"{MLB_BASE}/sports/1/players?season={season}", timeout=15).json().get("people", []):
                _mlb_name_map[p["fullName"].lower().strip()] = p["id"]
        except Exception:
            pass
    return _mlb_name_map

def mlb_player_id(name: str):
    nm  = get_mlb_player_map()
    key = name.lower().strip()
    if key in nm:
        return nm[key]
    parts = key.split()
    if len(parts) >= 2:
        first, last = parts[0].rstrip("."), parts[-1]
        for full, pid in nm.items():
            fp = full.split()
            if len(fp) >= 2 and fp[-1] == last and fp[0].startswith(first):
                return pid
    if parts:
        hits = [pid for full, pid in nm.items() if full.split()[-1] == parts[-1]]
        if len(hits) == 1:
            return hits[0]
    return None

def get_mlb_hitting_logs(pid):
    if pid in _mlb_hit_cache:
        return _mlb_hit_cache[pid]
    frames = []
    for season in ("2025", "2026"):
        try:
            url  = f"{MLB_BASE}/people/{pid}/stats?stats=gameLog&season={season}&group=hitting"
            data = requests.get(url, timeout=10).json().get("stats", [{}])[0].get("splits", [])
            rows = []
            for s in data:
                st = s.get("stat", {})
                rows.append({
                    "date": s.get("date", ""),
                    "H":  int(st.get("hits", 0) or 0),
                    "HR": int(st.get("homeRuns", 0) or 0),
                    "R":  int(st.get("runs", 0) or 0),
                    "SB": int(st.get("stolenBases", 0) or 0),
                    "BB": int(st.get("baseOnBalls", 0) or 0),
                    "AB": int(st.get("atBats", 0) or 0),
                    "K":  int(st.get("strikeOuts", 0) or 0),
                    "2B": int(st.get("doubles", 0) or 0),
                })
            if rows:
                frames.append(pd.DataFrame(rows))
        except Exception:
            pass
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    _mlb_hit_cache[pid] = df
    return df

def get_mlb_pitching_logs(pid):
    if pid in _mlb_pit_cache:
        return _mlb_pit_cache[pid]
    frames = []
    for season in ("2025", "2026"):
        try:
            url  = f"{MLB_BASE}/people/{pid}/stats?stats=gameLog&season={season}&group=pitching"
            data = requests.get(url, timeout=10).json().get("stats", [{}])[0].get("splits", [])
            rows = []
            for s in data:
                st = s.get("stat", {})
                ip_str = str(st.get("inningsPitched") or "0")
                parts  = ip_str.split(".")
                ip     = int(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)
                rows.append({
                    "date": s.get("date", ""),
                    "IP": round(ip, 1),
                    "H":  int(st.get("hits", 0) or 0),
                    "ER": int(st.get("earnedRuns", 0) or 0),
                    "BB": int(st.get("baseOnBalls", 0) or 0),
                    "K":  int(st.get("strikeOuts", 0) or 0),
                    "NP": int(st.get("numberOfPitches", 0) or 0),
                })
            if rows:
                frames.append(pd.DataFrame(rows))
        except Exception:
            pass
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    _mlb_pit_cache[pid] = df
    return df

# ── WNBA / NBA game logs (nba_api) ────────────────────────────────────────

_wnba_ids  = {}
_wnba_logs = {}
_nba_ids   = {}
_nba_logs  = {}

def _load_wnba_ids():
    if _wnba_ids:
        return _wnba_ids
    try:
        from nba_api.stats.endpoints import commonallplayers
        df = commonallplayers.CommonAllPlayers(is_only_current_season=0, league_id="10").get_data_frames()[0]
        _wnba_ids.update({r["DISPLAY_FIRST_LAST"].lower(): int(r["PERSON_ID"]) for _, r in df.iterrows()})
    except Exception:
        pass
    return _wnba_ids

def _load_nba_ids():
    if _nba_ids:
        return _nba_ids
    try:
        from nba_api.stats.static import players as nba_players
        _nba_ids.update({p["full_name"].lower(): p["id"] for p in nba_players.get_players()})
    except Exception:
        pass
    return _nba_ids

def get_wnba_gamelogs(pid):
    if pid in _wnba_logs:
        return _wnba_logs[pid]
    try:
        from nba_api.stats.endpoints import playergamelog
        frames = []
        for season in ("2025", "2024"):
            try:
                logs = playergamelog.PlayerGameLog(
                    player_id=pid, season=season,
                    season_type_all_star="Regular Season",
                    league_id_nullable="10", timeout=15,
                ).get_data_frames()[0]
                if not logs.empty:
                    frames.append(logs)
            except Exception:
                continue
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    except Exception:
        df = pd.DataFrame()
    _wnba_logs[pid] = df
    return df

def get_nba_gamelogs(pid):
    if pid in _nba_logs:
        return _nba_logs[pid]
    try:
        from nba_api.stats.endpoints import playergamelog
        frames = []
        for season in ("2025-26", "2024-25"):
            try:
                logs = playergamelog.PlayerGameLog(
                    player_id=pid, season=season,
                    season_type_all_star="Regular Season", timeout=15,
                ).get_data_frames()[0]
                if not logs.empty:
                    frames.append(logs)
            except Exception:
                continue
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    except Exception:
        df = pd.DataFrame()
    _nba_logs[pid] = df
    return df

# ── Hit rate calculators ───────────────────────────────────────────────────

def _weighted_rate(vals, line, is_pitcher=False):
    last20 = vals[-20:] if len(vals) >= 5 else vals
    last10 = vals[-10:] if len(vals) >= 10 else vals
    prev10 = vals[-20:-10] if len(vals) >= 20 else vals[:max(1, len(vals) // 2)]
    if len(last20) == 0:
        return 0.5, 0
    r20 = float((last20 > line).sum()) / len(last20)
    if is_pitcher:
        last3 = vals[-3:] if len(vals) >= 3 else vals
        r3    = float((last3  > line).sum()) / max(len(last3),  1)
        r10v  = float((last10 > line).sum()) / max(len(last10), 1) if len(last10) >= 3 else r20
        hist  = 0.50 * r3 + 0.30 * r10v + 0.20 * r20
    else:
        r10v = float((last10 > line).sum()) / len(last10) if len(last10) >= 5 else r20
        hist = 0.6 * r10v + 0.4 * r20
    if len(last10) >= 5 and len(prev10) >= 5:
        r_prev = float((prev10 > line).sum()) / len(prev10)
        r10c   = float((last10 > line).sum()) / len(last10)
        hist   = min(0.97, max(0.03, hist + (r10c - r_prev) * 0.1))
    return hist, len(last20)

def mlb_hit_rate(player_name, stat_type, line, odds_type="standard", implied=-1.0, cal=1.0):
    is_pit = stat_type in PP_PITCHER_TYPES
    col    = (PP_MLB_PIT_COL if is_pit else PP_MLB_HIT_COL).get(stat_type)
    if not col:
        return 0.5, 0
    pid = mlb_player_id(player_name)
    if not pid:
        return 0.5, 0
    df = get_mlb_pitching_logs(pid) if is_pit else get_mlb_hitting_logs(pid)
    if df.empty or col not in df.columns:
        return 0.5, 0
    hist, n = _weighted_rate(df[col].values, line, is_pitcher=is_pit)
    imp  = implied if implied >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    rate = (0.7 * hist + 0.3 * imp) * cal
    return round(min(0.97, max(0.03, rate)), 3), n

def wnba_hit_rate(player_name, stat_type, line, odds_type="standard", implied=-1.0, cal=1.0):
    col = WNBA_COL_MAP.get(stat_type)
    if not col:
        return 0.5, 0
    pid = _load_wnba_ids().get(player_name.strip().lower())
    if not pid:
        return 0.5, 0
    df = get_wnba_gamelogs(pid)
    if df.empty:
        return 0.5, 0
    if col in ("PRA", "PR", "PA"):
        df = df.copy()
        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        df["PR"]  = df["PTS"] + df["REB"]
        df["PA"]  = df["PTS"] + df["AST"]
    if col not in df.columns:
        return 0.5, 0
    hist, n = _weighted_rate(df[col].values, line)
    imp  = implied if implied >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    rate = (0.7 * hist + 0.3 * imp) * cal
    return round(min(0.97, max(0.03, rate)), 3), n

def nba_hit_rate(player_name, stat_type, line, odds_type="standard", implied=-1.0, cal=1.0):
    col = NBA_COL_MAP.get(stat_type)
    if not col:
        return 0.5, 0
    pid = _load_nba_ids().get(player_name.strip().lower())
    if not pid:
        return 0.5, 0
    df = get_nba_gamelogs(pid)
    if df.empty:
        return 0.5, 0
    if col in ("PRA", "PR", "PA"):
        df = df.copy()
        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        df["PR"]  = df["PTS"] + df["REB"]
        df["PA"]  = df["PTS"] + df["AST"]
    if col not in df.columns:
        return 0.5, 0
    hist, n = _weighted_rate(df[col].values, line)
    imp  = implied if implied >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    rate = (0.7 * hist + 0.3 * imp) * cal
    return round(min(0.97, max(0.03, rate)), 3), n

# ── Parlay builder (mirrors dashboard _build_parlays) ─────────────────────

def build_parlays(legs, min_legs=2, max_legs=4, top_n=50, pool_size=30):
    # Cap pool to avoid combinatorial explosion (C(pool_size,4) stays manageable)
    legs = sorted(legs, key=lambda x: x["hit_rate"], reverse=True)[:pool_size]
    results = []
    for n in range(min_legs, max_legs + 1):
        if n > len(legs):
            continue
        payout = PP_PAYOUTS.get(n, float(n) * 2.0)
        for combo in combinations(legs, n):
            if len({l["player_name"] for l in combo}) < n:
                continue
            prob = 1.0
            for leg in combo:
                prob *= leg["hit_rate"]
            results.append({
                "legs": list(combo), "n": n,
                "prob": round(prob, 4), "payout": payout,
                "ev": round(prob * payout - (1.0 - prob), 4),
            })

    def _top(pool, key_fn, exclude=None):
        seen, out = set(), []
        for p in sorted(pool, key=key_fn, reverse=True):
            k = frozenset(f"{l['player_name']}|{l['stat_type']}" for l in p["legs"])
            if k not in seen and (not exclude or k not in exclude):
                seen.add(k); out.append(p)
        return out

    safe  = _top(results, lambda x: x["prob"])[:top_n]
    sk    = {frozenset(f"{l['player_name']}|{l['stat_type']}" for l in p["legs"]) for p in safe}
    value = _top(sorted(results, key=lambda x: (x["n"], x["ev"]), reverse=True),
                 lambda x: x["ev"], exclude=sk)[:top_n]
    return safe, value

# ── Leg scorer ─────────────────────────────────────────────────────────────

def score_legs(df, cal, stat_types, rate_fn):
    df   = df[df["stat_type"].isin(stat_types)].copy()
    legs, seen = [], set()
    for _, row in df.iterrows():
        key = (row["player_name"], row["stat_type"])
        if key in seen:
            continue
        seen.add(key)
        try:
            line = float(row["line_score"])
        except Exception:
            continue
        rate, n = rate_fn(
            row["player_name"], row["stat_type"], line,
            odds_type=str(row.get("odds_type", "standard")),
            implied=float(row.get("implied_prob", -1.0)),
            cal=cal.get(row["stat_type"], 1.0),
        )
        if n < 3:
            continue
        legs.append({
            "player_name":   row["player_name"],
            "team":          str(row.get("team", "")),
            "stat_type":     row["stat_type"],
            "line_score":    line,
            "odds_type":     str(row.get("odds_type", "standard")),
            "american_odds": int(row.get("american_odds", -110)),
            "implied_prob":  float(row.get("implied_prob", 0.50)),
            "sportsbook":    str(row.get("sportsbook", "")),
            "game_id":       str(row.get("game_id", "")),
            "game_label":    str(row.get("game_label", "")),
            "start_time":    str(row.get("start_time", "")),
            "hit_rate":      rate,
            "sample_n":      n,
        })
        time.sleep(0.03)
    return legs

# ── Per-sport runner ───────────────────────────────────────────────────────

def run_sport(sport_key, sport_label, pp_league_id, stat_types, rate_fn):
    print(f"\n{'='*62}\n  {sport_label}\n{'='*62}")
    cal = load_cal(sport_label)
    if cal:
        print(f"  Calibration: { {k: round(v,3) for k,v in cal.items()} }")

    total = 0
    for sb, fetch_fn in [("PrizePicks", lambda: fetch_prizepicks(pp_league_id)),
                          ("Underdog",   lambda: fetch_underdog(sport_key))]:
        print(f"\n  [{sb}]")
        raw = fetch_fn()
        if raw.empty:
            print(f"    No lines — skipping.")
            continue
        print(f"    {len(raw)} lines fetched.")

        legs = score_legs(raw, cal, stat_types, rate_fn)
        print(f"    {len(legs)} legs scored.")
        if len(legs) < 2:
            continue

        safe, value = build_parlays(legs)
        s = parlay_tracker.log_parlays(safe,  sport_label, sb, kind="safe")
        v = parlay_tracker.log_parlays(value, sport_label, sb, kind="value")
        print(f"    Logged {s} safe + {v} value parlays.")
        total += s + v

    return total

# ── Entry point ────────────────────────────────────────────────────────────

def main():
    now   = datetime.now()
    month = now.month
    print(f"\nKonjure Analytics — Daily Parlay Generator")
    print(f"Run: {now.strftime('%Y-%m-%d %H:%M')}")

    total = 0
    total += run_sport("mlb",  "MLB",  2, MLB_STAT_TYPES,  mlb_hit_rate)

    if 5 <= month <= 9:
        total += run_sport("wnba", "WNBA", 6, WNBA_STAT_TYPES, wnba_hit_rate)
    else:
        print("\n  WNBA: off-season — skipping.")

    if month >= 10 or month <= 6:
        total += run_sport("nba",  "NBA",  7, NBA_STAT_TYPES,  nba_hit_rate)
    else:
        print("\n  NBA: off-season — skipping.")

    print(f"\n{'='*62}")
    print(f"  Total parlays logged: {total}")
    print(f"{'='*62}\n")

if __name__ == "__main__":
    main()
