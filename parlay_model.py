"""
parlay_model.py — Shared prop-prediction model for MLB/NBA/WNBA.

Single source of truth for hit-rate calculators, batter-vs-pitcher (BvP)
matchup adjustment, game-log fetchers, player-ID resolution, and the parlay
combination builder. Both nba_prop_dashboard.py (interactive Streamlit app)
and daily_parlay_gen.py (headless cron generator) import from here instead
of keeping their own copies.

This module has no Streamlit dependency (daily_parlay_gen.py is a plain
script). Caching uses a small TTL decorator (_ttl_cache) instead of
st.cache_data — same per-argument/expiry semantics, no Streamlit runtime
required.

History: these two files independently reimplemented this model and drifted
apart twice — a BvP adjustment existed only in the dashboard, and a
combinatorial-explosion pool-size cap existed only in the generator — each
causing a real bug. This module exists so that can't happen again.
"""
import time
import requests
import pandas as pd
from functools import wraps
from itertools import combinations
from collections import defaultdict
from datetime import datetime

from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog, commonallplayers

MLB_BASE = "https://statsapi.mlb.com/api/v1"
MLB_SEASON = "2026"


def _ttl_cache(ttl_seconds):
    """Per-argument cache with time-based expiry — a Streamlit-free stand-in
    for @st.cache_data(ttl=...) so this module works in both the dashboard
    and the headless generator."""
    def decorator(fn):
        cache = {}

        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.time()
            hit = cache.get(key)
            if hit is not None and now - hit[1] < ttl_seconds:
                return hit[0]
            value = fn(*args, **kwargs)
            cache[key] = (value, now)
            return value

        wrapper.clear = cache.clear
        return wrapper
    return decorator


# ── Stat-type / column mappings ─────────────────────────────────────────────

PP_PAYOUTS = {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0}

# PrizePicks implied over-probability by odds_type (market signal)
# goblin = easier line (~62% implied), demon = harder line (~38% implied)
PP_ODDS_IMPLIED = {"goblin": 0.62, "standard": 0.50, "demon": 0.38}

NBA_STAT_COL = {
    "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
    "Pts+Rebs+Asts": "PRA", "Pts+Asts": "PA", "Pts+Rebs": "PR",
    "3-PT Made": "FG3M", "Blocked Shots": "BLK", "Steals": "STL",
    "Turnovers": "TOV", "Fantasy Score": "FS", "Spread": None,
}
MLB_HIT_COL = {
    "Hits": "H", "Home Runs": "HR",
    "Stolen Bases": "SB", "Strikeouts": "K", "Hitter Strikeouts": "K",
    "Walks": "BB",
    "Runs Scored": "R", "Runs": "R",
    "Doubles": "2B", "Singles": "H", "Total Bases": "TB",
    "Hits+Runs+RBIs": "H", "Plate Appearances": "AB",
}
# Total Bases previously showed a 0.0% actual hit rate across 91 resolved legs and
# was removed as "unplayable" — that was a resolver bug, not reality: MLB Stats
# API's per-game boxscore has no 'totalBases' key, so it silently resolved to 0
# every time (fixed in parlay_tracker._resolve_mlb_legs, which now derives it from
# H+2B+2*3B+3*HR). Re-resolving those 91 legs with the fix gives 72.5% actual vs.
# 61.3% predicted — a genuinely good prop. Re-enabled.
#
# RBIs (3.7% actual hit rate across 54 resolved legs, correctly resolved — 'rbi' is
# a real boxscore key, no bug found) stays excluded: the data says it really is a
# bad prop, not a resolution artifact.
MLB_PIT_COL = {
    "Pitcher Strikeouts": "K", "Strikeouts": "K",
    "Earned Runs Allowed": "ER", "Walks Allowed": "BB", "Hits Allowed": "H",
    "Pitching Outs": "IP", "Pitches Thrown": "NP",
}
MLB_PITCHER_TYPES = {
    "Pitcher Strikeouts", "Earned Runs Allowed", "Walks Allowed",
    "Hits Allowed", "Pitching Outs", "Pitches Thrown",
}
WNBA_STAT_COL = {
    "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
    "Steals": "STL", "Blocks": "BLK", "3-PT Made": "FG3M",
    "Pts+Rebs+Asts": "PRA", "Pts+Rebs": "PR", "Pts+Asts": "PA",
}
BVP_COL_MAP = {"H": "h", "HR": "hr", "TB": "tb", "K": "k", "BB": "bb", "RBI": "rbi"}
BVP_MIN_AB = 15  # minimum career AB vs pitcher to apply adjustment


# ── MLB player ID + game logs ────────────────────────────────────────────────

@_ttl_cache(86400)
def get_mlb_player_map():
    """Fetch MLB players across 2025 and 2026 seasons and return a name->id dict."""
    combined = {}
    for season in ("2025", "2026"):
        try:
            resp = requests.get(f"{MLB_BASE}/sports/1/players?season={season}", timeout=15)
            for p in resp.json().get("people", []):
                combined[p["fullName"].lower().strip()] = p["id"]
        except Exception:
            pass
    return combined


def mlb_player_id(name: str):
    """Resolve a player name to an MLB Stats API ID using the cached season roster."""
    name_map = get_mlb_player_map()
    if not name_map:
        return None
    key = name.lower().strip()
    if key in name_map:
        return name_map[key]
    parts = key.split()
    if len(parts) >= 2:
        first, last = parts[0].rstrip("."), parts[-1]
        for full_name, pid in name_map.items():
            fp = full_name.split()
            if len(fp) >= 2 and fp[-1] == last and fp[0].startswith(first):
                return pid
    if parts:
        last = parts[-1]
        hits = [pid for full_name, pid in name_map.items() if full_name.split()[-1] == last]
        if len(hits) == 1:
            return hits[0]
    return None


@_ttl_cache(3600)
def get_mlb_hitting_logs(player_id, seasons=(MLB_SEASON,)):
    frames = []
    for season in seasons:
        for attempt in range(2):
            try:
                url = f"{MLB_BASE}/people/{player_id}/stats?stats=gameLog&season={season}&group=hitting"
                resp = requests.get(url, timeout=15)
                _stats = resp.json().get("stats", [])
                splits = _stats[0].get("splits", []) if _stats else []
                rows = []
                for s in splits:
                    st_data = s.get("stat", {})
                    _h = int(st_data.get("hits") or 0)
                    _hr = int(st_data.get("homeRuns") or 0)
                    _2b = int(st_data.get("doubles") or 0)
                    _3b = int(st_data.get("triples") or 0)
                    rows.append({
                        "date": s.get("date", ""),
                        "season": season,
                        "opponent": s.get("opponent", {}).get("abbreviation", ""),
                        "AB": int(st_data.get("atBats") or 0),
                        "H": _h, "HR": _hr, "2B": _2b, "3B": _3b,
                        "RBI": int(st_data.get("rbi") or 0),
                        "BB": int(st_data.get("baseOnBalls") or 0),
                        "K": int(st_data.get("strikeOuts") or 0),
                        "SB": int(st_data.get("stolenBases") or 0),
                        "R": int(st_data.get("runs") or 0),
                        "TB": int(st_data.get("totalBases") or (_h + _2b + 2 * _3b + 3 * _hr)),
                        "AVG": float(st_data.get("avg") or 0),
                        "OBP": float(st_data.get("obp") or 0),
                        "SLG": float(st_data.get("slg") or 0),
                    })
                if rows:
                    frames.append(pd.DataFrame(rows))
                break
            except Exception:
                if attempt == 0:
                    time.sleep(1)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


@_ttl_cache(3600)
def get_mlb_pitching_logs(player_id, seasons=(MLB_SEASON,)):
    frames = []
    for season in seasons:
        for attempt in range(2):
            try:
                url = f"{MLB_BASE}/people/{player_id}/stats?stats=gameLog&season={season}&group=pitching"
                resp = requests.get(url, timeout=15)
                _stats = resp.json().get("stats", [])
                splits = _stats[0].get("splits", []) if _stats else []
                rows = []
                for s in splits:
                    st_data = s.get("stat", {})
                    ip_str = str(st_data.get("inningsPitched") or "0")
                    try:
                        parts = ip_str.split(".")
                        ip = int(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)
                    except Exception:
                        ip = 0.0
                    k9 = round((int(st_data.get("strikeOuts") or 0) / ip * 9), 2) if ip > 0 else 0
                    rows.append({
                        "date": s.get("date", ""),
                        "season": season,
                        "opponent": s.get("opponent", {}).get("abbreviation", ""),
                        "IP": round(ip, 1),
                        "H": int(st_data.get("hits") or 0),
                        "ER": int(st_data.get("earnedRuns") or 0),
                        "BB": int(st_data.get("baseOnBalls") or 0),
                        "K": int(st_data.get("strikeOuts") or 0),
                        "HR": int(st_data.get("homeRuns") or 0),
                        "NP": int(st_data.get("numberOfPitches") or 0),
                        "ERA": float(st_data.get("era") or 0),
                        "WHIP": float(st_data.get("whip") or 0),
                        "K9": k9,
                    })
                if rows:
                    frames.append(pd.DataFrame(rows))
                break
            except Exception:
                if attempt == 0:
                    time.sleep(1)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ── Batter vs. pitcher matchup ───────────────────────────────────────────────

@_ttl_cache(86400)
def mlb_bvp_stats(batter_id: int, pitcher_id: int) -> dict:
    """Career batting stats for batter_id against pitcher_id. Returns {} if no data."""
    try:
        url = (f"{MLB_BASE}/people/{batter_id}/stats"
               f"?stats=vsPlayer&group=hitting&opposingPlayerId={pitcher_id}&sportId=1")
        resp = requests.get(url, timeout=10)
        splits = (resp.json().get("stats") or [{}])[0].get("splits", [])
        if splits:
            st_data = splits[0].get("stat", {})
            return {
                "ab": int(st_data.get("atBats") or 0),
                "h": int(st_data.get("hits") or 0),
                "hr": int(st_data.get("homeRuns") or 0),
                "tb": int(st_data.get("totalBases") or 0),
                "k": int(st_data.get("strikeOuts") or 0),
                "bb": int(st_data.get("baseOnBalls") or 0),
                "rbi": int(st_data.get("rbi") or 0),
            }
    except Exception:
        pass
    return {}


@_ttl_cache(3600)
def mlb_today_pitcher_lookup() -> dict:
    """Returns {team_abbr: opp_pitcher_id} for today's MLB games."""
    lookup = {}
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = f"{MLB_BASE}/schedule?sportId=1&date={today}&hydrate=probablePitcher,team"
        resp = requests.get(url, timeout=10)
        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                at, ht = game["teams"]["away"], game["teams"]["home"]
                away_abbr = at["team"].get("abbreviation", "")
                home_abbr = ht["team"].get("abbreviation", "")
                away_pid = at.get("probablePitcher", {}).get("id")
                home_pid = ht.get("probablePitcher", {}).get("id")
                if away_abbr and home_pid:
                    lookup[away_abbr] = home_pid   # away batters face home pitcher
                if home_abbr and away_pid:
                    lookup[home_abbr] = away_pid   # home batters face away pitcher
    except Exception:
        pass
    return lookup


# ── NBA player ID + game logs ────────────────────────────────────────────────

@_ttl_cache(86400)
def _current_season_nba_player_ids() -> dict:
    """Live fallback: name->id map from CommonAllPlayers for players missing from the static db."""
    try:
        df = commonallplayers.CommonAllPlayers(
            is_only_current_season=1, league_id="00", season="2025-26"
        ).get_data_frames()[0]
        return {row["DISPLAY_FIRST_LAST"].lower(): int(row["PERSON_ID"]) for _, row in df.iterrows()}
    except Exception:
        return {}


def get_player_id(player_name):
    match = players.find_players_by_full_name(player_name)
    if match:
        return match[0]["id"]
    parts = player_name.strip().split()
    if len(parts) >= 2:
        last = parts[-1]
        candidates = players.find_players_by_last_name(last)
        first_init = parts[0][0].lower()
        filtered = [p for p in candidates if p["first_name"].lower().startswith(first_init)]
        if len(filtered) == 1:
            return filtered[0]["id"]
        if not filtered and len(candidates) == 1:
            return candidates[0]["id"]
    live_map = _current_season_nba_player_ids()
    return live_map.get(player_name.strip().lower())


def _sort_by_game_date(df: pd.DataFrame) -> pd.DataFrame:
    """PlayerGameLog returns rows newest-first; the hit-rate math slices
    vals[-N:] expecting oldest-first so that 'last N games' really means the
    most recent N. Parse GAME_DATE ('MMM DD, YYYY') and sort ascending."""
    if df.empty or "GAME_DATE" not in df.columns:
        return df
    parsed = pd.to_datetime(df["GAME_DATE"], format="%b %d, %Y", errors="coerce")
    return df.assign(_game_date=parsed).sort_values("_game_date").drop(columns="_game_date").reset_index(drop=True)


@_ttl_cache(3600)
def get_gamelogs(player_id, seasons):
    frames = []
    for season in seasons:
        for s_type in ("Regular Season", "Playoffs"):
            try:
                logs = playergamelog.PlayerGameLog(
                    player_id=player_id, season=season,
                    season_type_all_star=s_type, timeout=10,
                ).get_data_frames()[0]
                if logs.empty:
                    continue
                logs["SEASON"] = season
                logs["SEASON_TYPE"] = s_type
                extracted = logs["MATCHUP"].str.extract(r"@ (\w+)|vs\. (\w+)")
                logs["OPPONENT"] = extracted[0].fillna(extracted[1])
                frames.append(logs)
            except Exception:
                pass
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _sort_by_game_date(df)


# ── WNBA player ID + game logs ───────────────────────────────────────────────

@_ttl_cache(86400)
def _wnba_nba_api_player_ids() -> dict:
    """name.lower() -> nba_api person_id for all WNBA players ever (1200+ players)."""
    try:
        df = commonallplayers.CommonAllPlayers(is_only_current_season=0, league_id="10").get_data_frames()[0]
        if not df.empty:
            return {row["DISPLAY_FIRST_LAST"].lower(): int(row["PERSON_ID"]) for _, row in df.iterrows()}
    except Exception:
        pass
    return {}


def get_wnba_player_id(player_name: str):
    """Return nba_api player ID for a WNBA player (used with PlayerGameLog)."""
    return _wnba_nba_api_player_ids().get(player_name.strip().lower())


@_ttl_cache(3600)
def get_wnba_gamelogs(player_id, seasons):
    """Fetch WNBA game logs via nba_api PlayerGameLog with league_id_nullable='10'."""
    if not player_id:
        return pd.DataFrame()
    frames = []
    for season in seasons:
        try:
            logs = playergamelog.PlayerGameLog(
                player_id=player_id, season=season,
                season_type_all_star="Regular Season",
                league_id_nullable="10", timeout=15,
            ).get_data_frames()[0]
            if not logs.empty:
                frames.append(logs)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "MATCHUP" in df.columns:
        df["OPPONENT"] = df["MATCHUP"].str.extract(r"(?:vs\.|@)\s*([A-Z]+)")
    for col in ["PTS", "REB", "AST", "STL", "BLK", "FG3M"]:
        if col not in df.columns:
            df[col] = 0.0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
    return _sort_by_game_date(df)


# ── Hit rate calculators ─────────────────────────────────────────────────────
# All three follow the same shape: 70% historical (60/40 last-10/last-30ish,
# with a +/-10%-of-trend nudge from last-10-vs-prior-10 momentum) blended with
# 30% sportsbook implied odds, then multiplied by a per-stat calibration factor
# (see parlay_tracker.get_calibration). MLB batters additionally get a BvP nudge.

def _nba_hit_rate(player_name: str, stat_type: str, line: float, odds_type: str = "standard",
                   implied_override: float = -1.0, cal_factor: float = 1.0):
    """Weighted hit rate: 70% historical (60/40 last-10/30 + trend) + 30% sportsbook implied odds."""
    col = NBA_STAT_COL.get(stat_type)
    if col is None:
        return 0.5, 0
    pid = get_player_id(player_name)
    if not pid:
        return 0.5, 0
    df = get_gamelogs(pid, ("2025-26",))
    if df.empty:
        df = get_gamelogs(pid, ("2024-25",))
    if df.empty:
        return 0.5, 0
    if col in ("PRA", "PA", "PR", "FS"):
        df = df.copy()
        if col == "PRA":
            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        elif col == "PA":
            df["PA"] = df["PTS"] + df["AST"]
        elif col == "PR":
            df["PR"] = df["PTS"] + df["REB"]
        elif col == "FS":
            df["FS"] = (df["PTS"]
                        + 1.2 * df.get("REB", 0)
                        + 1.5 * df.get("AST", 0)
                        + 3.0 * df.get("STL", 0)
                        + 3.0 * df.get("BLK", 0)
                        - df.get("TOV", 0))
    if col not in df.columns:
        return 0.5, 0
    vals = df[col].values
    last30 = vals[-30:] if len(vals) >= 5 else vals
    last10 = vals[-10:] if len(vals) >= 10 else vals
    prev10 = vals[-20:-10] if len(vals) >= 20 else vals[:max(1, len(vals) // 2)]
    n = len(last30)
    if n == 0:
        return 0.5, 0
    r30 = float((last30 > line).sum()) / len(last30)
    if len(last10) >= 5:
        r10 = float((last10 > line).sum()) / len(last10)
        hist = 0.6 * r10 + 0.4 * r30
    else:
        hist = r30
        r10 = hist
    if len(last10) >= 5 and len(prev10) >= 5:
        r_prev = float((prev10 > line).sum()) / len(prev10)
        hist = min(0.97, max(0.03, hist + (r10 - r_prev) * 0.1))
    implied = implied_override if implied_override >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    rate = 0.7 * hist + 0.3 * implied
    rate = rate * cal_factor
    return round(min(0.97, max(0.03, rate)), 3), n


def _wnba_hit_rate(player_name: str, stat_type: str, line: float, odds_type: str = "standard",
                    implied_override: float = -1.0, cal_factor: float = 1.0):
    """Weighted WNBA hit rate: 70% game log history + 30% sportsbook implied."""
    col = WNBA_STAT_COL.get(stat_type)
    if not col:
        return 0.5, 0
    pid = get_wnba_player_id(player_name)
    if not pid:
        return 0.5, 0
    # 2026 is the current season — try it first, fall back to prior seasons
    # if a player hasn't logged games yet this year (rookies, recent injury returns).
    df = get_wnba_gamelogs(pid, ("2026",))
    if df.empty:
        df = get_wnba_gamelogs(pid, ("2025",))
    if df.empty:
        df = get_wnba_gamelogs(pid, ("2024",))
    if df.empty:
        return 0.5, 0
    if col in ("PRA", "PR", "PA"):
        df = df.copy()
        if col == "PRA":
            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        elif col == "PR":
            df["PR"] = df["PTS"] + df["REB"]
        elif col == "PA":
            df["PA"] = df["PTS"] + df["AST"]
    if col not in df.columns:
        return 0.5, 0
    vals = df[col].values
    last30 = vals[-30:] if len(vals) >= 5 else vals
    last10 = vals[-10:] if len(vals) >= 10 else vals
    prev10 = vals[-20:-10] if len(vals) >= 20 else vals[:max(1, len(vals) // 2)]
    n = len(last30)
    if n == 0:
        return 0.5, 0
    r30 = float((last30 > line).sum()) / len(last30)
    if len(last10) >= 5:
        r10 = float((last10 > line).sum()) / len(last10)
        hist = 0.6 * r10 + 0.4 * r30
    else:
        hist = r30
        r10 = hist
    if len(last10) >= 5 and len(prev10) >= 5:
        r_prev = float((prev10 > line).sum()) / len(prev10)
        hist = min(0.97, max(0.03, hist + (r10 - r_prev) * 0.1))
    implied = implied_override if implied_override >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    rate = 0.7 * hist + 0.3 * implied
    rate = rate * cal_factor
    return round(min(0.97, max(0.03, rate)), 3), n


def _mlb_hit_rate(player_name: str, stat_type: str, line: float,
                   odds_type: str = "standard", implied_override: float = -1.0,
                   cal_factor: float = 1.0, opp_pitcher_id: int | None = None,
                   team: str | None = None):
    """
    Weighted hit rate for MLB props.

    Batters:  60/40 last-10/last-20 + optional BvP adjustment + trend nudge,
              then 70% historical + 30% implied, x calibration.
              When batter has >= 15 career AB vs today's pitcher, their career
              rate against that pitcher nudges hist up or down by up to +/-40%.

    Pitchers: 50/30/20 last-3/last-10/last-20 (recency-heavy) + trend nudge,
              then 70% historical + 30% implied, x calibration. Recent form
              dominates because pitchers can run hot/cold start-by-start.

    opp_pitcher_id can be passed directly (dashboard call sites that pre-fetch
    it in a loop), or resolved automatically from `team` via
    mlb_today_pitcher_lookup() if opp_pitcher_id is omitted.
    """
    is_pitcher = stat_type in MLB_PITCHER_TYPES
    col = (MLB_PIT_COL if is_pitcher else MLB_HIT_COL).get(stat_type)
    if col is None:
        return 0.5, 0
    pid = mlb_player_id(player_name)
    if not pid:
        return 0.5, 0
    seasons = ("2025", "2026")
    try:
        df = get_mlb_pitching_logs(pid, seasons) if is_pitcher else get_mlb_hitting_logs(pid, seasons)
    except Exception:
        return 0.5, 0
    if df.empty or col not in df.columns:
        return 0.5, 0
    vals = df[col].values
    last20 = vals[-20:] if len(vals) >= 5 else vals
    last10 = vals[-10:] if len(vals) >= 10 else vals
    prev10 = vals[-20:-10] if len(vals) >= 20 else vals[:max(1, len(vals) // 2)]
    n = len(last20)
    if n == 0:
        return 0.5, 0

    r20 = float((last20 > line).sum()) / len(last20)

    if is_pitcher:
        last3 = vals[-3:] if len(vals) >= 3 else vals
        r3 = float((last3 > line).sum()) / max(len(last3), 1)
        r10 = float((last10 > line).sum()) / max(len(last10), 1) if len(last10) >= 3 else r20
        hist = 0.50 * r3 + 0.30 * r10 + 0.20 * r20
    else:
        if len(last10) >= 5:
            r10 = float((last10 > line).sum()) / len(last10)
            hist = 0.6 * r10 + 0.4 * r20
        else:
            r10 = hist = r20

        if opp_pitcher_id is None and team:
            opp_pitcher_id = mlb_today_pitcher_lookup().get(team)

        if opp_pitcher_id and pid and col in BVP_COL_MAP:
            try:
                bvp = mlb_bvp_stats(int(pid), int(opp_pitcher_id))
                if bvp.get("ab", 0) >= BVP_MIN_AB:
                    bvp_key = BVP_COL_MAP[col]
                    season_ab = float(df["AB"].sum()) if "AB" in df.columns else max(len(vals) * 4, 1)
                    season_stat = float(df[col].sum())
                    season_per_ab = season_stat / max(season_ab, 1)
                    bvp_per_ab = bvp.get(bvp_key, 0) / max(bvp["ab"], 1)
                    if season_per_ab > 0.001:
                        bvp_factor = min(1.4, max(0.60, bvp_per_ab / season_per_ab))
                        hist = min(0.97, max(0.03, hist * (0.85 + 0.15 * bvp_factor)))
            except Exception:
                pass

    if len(last10) >= 5 and len(prev10) >= 5:
        r_prev = float((prev10 > line).sum()) / len(prev10)
        r10_cur = float((last10 > line).sum()) / len(last10)
        hist = min(0.97, max(0.03, hist + (r10_cur - r_prev) * 0.1))

    implied = implied_override if implied_override >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    rate = 0.7 * hist + 0.3 * implied
    rate = rate * cal_factor
    return round(min(0.97, max(0.03, rate)), 3), n


# ── Parlay builder ───────────────────────────────────────────────────────────

def _build_parlays(legs: list, min_legs: int = 2, max_legs: int = 4, top_n: int = 50,
                    pool_size: int = 30, max_leg_uses: int = 6):
    """
    Safe  — highest probability combos (most likely to hit).
    Value — highest EV combos that are NOT already in Safe.
             Because EV grows with payout and payout grows with pick count,
             higher-pick combos naturally rise here even at lower probability,
             so Safe and Value show genuinely different options.
    Same player never appears twice in one parlay.

    pool_size caps the input to the top-N legs by hit_rate before generating
    combinations — Underdog/fallback data can return hundreds of legs, and
    C(800,4) = 17B combinations will hang the app.

    max_leg_uses caps how many output parlays any single player+stat leg can
    appear in, so top_n isn't just recombinations of the same handful of
    highest-confidence legs (e.g. all "Hits" props flooding the safe list).
    """
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
            # PP_PAYOUTS are gross multipliers (a 2-pick returns 3x the entry), so a
            # win nets payout-1: EV = prob*(payout-1) - (1-prob) = prob*payout - 1.
            # Subtracting only (1-prob) treated the returned stake as profit and
            # overstated EV by `prob`. Selection is unaffected — value_pool sorts by
            # (n, ev) and within a fixed n both forms rank identically in prob — but
            # the stored ev is now the real per-dollar edge.
            ev = round(prob * payout - 1.0, 4)
            results.append({
                "legs": list(combo), "n": n,
                "prob": round(prob, 4), "payout": payout, "ev": ev,
            })

    def _top(pool, key_fn, exclude_keys=None, max_uses=None):
        seen: set = set()
        out = []
        leg_uses = defaultdict(int)
        for p in sorted(pool, key=key_fn, reverse=True):
            leg_keys = [f"{l['player_name']}|{l['stat_type']}" for l in p["legs"]]
            k = frozenset(leg_keys)
            if k in seen or (exclude_keys and k in exclude_keys):
                continue
            if max_uses is not None and any(leg_uses[lk] >= max_uses for lk in leg_keys):
                continue
            seen.add(k)
            out.append(p)
            for lk in leg_keys:
                leg_uses[lk] += 1
        return out

    safe_out = _top(results, lambda x: x["prob"], max_uses=max_leg_uses)[:top_n]
    safe_keys = {frozenset(f"{l['player_name']}|{l['stat_type']}" for l in p["legs"]) for p in safe_out}
    value_pool = sorted(results, key=lambda x: (x["n"], x["ev"]), reverse=True)
    value_out = _top(value_pool, lambda x: x["ev"], exclude_keys=safe_keys, max_uses=max_leg_uses)[:top_n]

    return safe_out, value_out


def _build_sgp(legs: list, min_legs: int = 2, max_legs: int = 5) -> list:
    """Group legs by game, return best parlay(s) per game sorted by probability."""
    game_groups: dict = defaultdict(list)
    for leg in legs:
        gid = leg.get("game_id", "")
        glabel = leg.get("game_label", leg.get("game_desc", "Unknown Game"))
        if gid:
            game_groups[(gid, glabel)].append(leg)
    sgp_results = []
    for (gid, glabel), game_legs in game_groups.items():
        if len(game_legs) < min_legs:
            continue
        cap = min(max_legs, len(game_legs))
        safe, _ = _build_parlays(game_legs, min_legs=min_legs, max_legs=cap, top_n=3)
        if safe:
            sgp_results.append({"game_label": glabel, "game_id": gid, "parlays": safe[:3]})
    sgp_results.sort(key=lambda x: x["parlays"][0]["prob"] if x["parlays"] else 0, reverse=True)
    return sgp_results
