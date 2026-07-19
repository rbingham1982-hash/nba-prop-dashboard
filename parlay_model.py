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
import re
import io
import math
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


@_ttl_cache(86400)
def _mlb_team_abbr_map():
    """{team_id: abbreviation} for all MLB clubs. The gameLog `opponent` object
    carries an id and name but no abbreviation, so we resolve it ourselves."""
    try:
        resp = requests.get(f"{MLB_BASE}/teams?sportId=1&season={MLB_SEASON}", timeout=10)
        return {t["id"]: t.get("abbreviation", "")
                for t in resp.json().get("teams", []) if t.get("id")}
    except Exception:
        return {}


# ── Statcast (Baseball Savant) advanced metrics ─────────────────────────────
# Barrel rate is the best non-market predictor of a home run; xISO/xSLG measure
# true power, and the batted-ball mix (fly-ball%, pull%) tells you whether that
# power leaves the yard. Savant's custom leaderboard is a public CSV keyed by
# player_id == the MLBAM id we already use, so batter and pitcher rows join
# straight onto our roster IDs. Cached 6h — these are season-to-date rates.
SAVANT_URL = "https://baseballsavant.mlb.com/leaderboard/custom"
_SAVANT_BAT_SEL = ["pa", "barrel_batted_rate", "xiso", "xslg", "hard_hit_percent",
                   "flyballs_percent", "pull_percent", "exit_velocity_avg", "launch_angle_avg"]
_SAVANT_PIT_SEL = ["pa", "barrel_batted_rate", "flyballs_percent", "groundballs_percent",
                   "hard_hit_percent", "xslg"]

def _savant_fetch(kind, sels, season):
    out = {}
    url = (f"{SAVANT_URL}?year={season}&type={kind}&filter=&min=10"
           f"&selections={','.join(sels)}&chart=false&x={sels[1]}&y={sels[1]}&r=no"
           f"&chartType=beeswarm&sort={sels[1]}&sortDir=desc&csv=true")
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")))
    except Exception:
        return out
    for _, row in df.iterrows():
        try:
            pid = int(row["player_id"])
        except (TypeError, ValueError):
            continue
        def g(c):
            v = row.get(c)
            return float(v) if pd.notna(v) else None
        br = g("barrel_batted_rate")
        out[pid] = {
            "barrel": br / 100 if br is not None else None,   # fraction
            "fb": g("flyballs_percent"), "pull": g("pull_percent"),
            "gb": g("groundballs_percent"), "hardhit": g("hard_hit_percent"),
            "xiso": g("xiso"), "xslg": g("xslg"),
            "ev": g("exit_velocity_avg"), "la": g("launch_angle_avg"),
            "pa": int(row["pa"]) if pd.notna(row.get("pa")) else 0,
        }
    return out

@_ttl_cache(21600)
def savant_batter_stats(season=MLB_SEASON):
    """{mlbam_id: {barrel, xiso, xslg, fb, pull, hardhit, ev, la, pa}} for hitters."""
    return _savant_fetch("batter", _SAVANT_BAT_SEL, season)

@_ttl_cache(21600)
def savant_pitcher_stats(season=MLB_SEASON):
    """{mlbam_id: {barrel(allowed), fb, gb, hardhit, xslg, pa}} for pitchers."""
    return _savant_fetch("pitcher", _SAVANT_PIT_SEL, season)

_BARREL_TO_HR_GAME = 1.65   # folds batted-balls/game (~3) and HR-per-barrel (~0.55)

def barrel_hr_prob(batter_id, opp_pitcher_id=None):
    """Barrel-rate estimate of P(>=1 HR) in a game, optionally adjusted for the
    opposing pitcher's barrels-allowed via the log5 odds ratio. Returns None when
    the hitter has no Statcast data. Used to fold contact quality into the HR
    hit-rate that the recency model alone predicts poorly."""
    b = savant_batter_stats().get(batter_id) or {}
    barrel = b.get("barrel")
    if barrel is None:
        return None
    eff = barrel
    if opp_pitcher_id:
        pbrl = (savant_pitcher_stats().get(opp_pitcher_id) or {}).get("barrel")
        if pbrl:
            eff = barrel * pbrl / 0.065
    return max(0.01, min(0.60, 1 - math.exp(-eff * _BARREL_TO_HR_GAME)))


# ── Stat-type / column mappings ─────────────────────────────────────────────

# Payout ladders for DFS pick'em books, where an all-must-hit play pays a fixed
# multiplier by pick count. Traditional sportsbooks have no ladder — a parlay there
# pays the product of its legs' decimal odds — so they are deliberately absent, and
# parlay_payout() derives their payout from the odds instead.
PAYOUT_TABLES = {
    "PrizePicks": {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0},
    "Underdog":   {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0},   # UD's 3-pick pays 6x, not PP's 5x
}
PP_PAYOUTS = PAYOUT_TABLES["PrizePicks"]


def american_to_decimal(odds) -> float:
    """American odds -> gross decimal multiplier (-110 -> 1.909, +150 -> 2.5)."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return 2.0
    if o == 0:
        return 2.0
    return 1.0 + (o / 100.0 if o > 0 else 100.0 / -o)


def parlay_payout(sportsbook: str, legs: list) -> float:
    """
    Gross payout multiplier for a parlay.

    PrizePicks' ladder used to be applied to every book. A DraftKings 3-leg parlay was
    therefore booked at 5x when it actually pays the product of its legs' odds — a
    payout that was never on offer, which made its EV and ROI fiction. DFS books get
    their own ladder; traditional books get the product of decimal odds.
    """
    n = len(legs)
    table = PAYOUT_TABLES.get(sportsbook)
    if table is not None:
        return table.get(n, float(n) * 2.0)
    payout = 1.0
    for leg in legs:
        payout *= american_to_decimal(leg.get("american_odds"))
    return round(payout, 4)

# PrizePicks implied over-probability by odds_type (market signal)
# goblin = easier line (~62% implied), demon = harder line (~38% implied)
PP_ODDS_IMPLIED = {"goblin": 0.62, "standard": 0.50, "demon": 0.38}

NBA_STAT_COL = {
    "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
    "Pts+Rebs+Asts": "PRA", "Pts+Asts": "PA", "Pts+Rebs": "PR",
    "Rebs+Asts": "RA",
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
    "Rebs+Asts": "RA",
}
BVP_COL_MAP = {"H": "h", "HR": "hr", "TB": "tb", "K": "k", "BB": "bb", "RBI": "rbi"}
BVP_MIN_AB = 15  # minimum career AB vs pitcher to apply adjustment


def american_to_implied(odds) -> float:
    """American odds -> implied probability (vig included)."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return 0.50
    if o == 0:
        return 0.50
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def devig_two_way(implied_over: float, implied_under: float | None) -> float:
    """
    Strip the book's margin from a two-way market.

    The two sides' raw implied probabilities sum to more than 1; the excess is the
    vig. Normalising by that sum recovers the book's true view of the over. With no
    under price there is nothing to normalise against, so the raw number stands.
    """
    if implied_under is None:
        return implied_over
    total = implied_over + implied_under
    if total <= 0:
        return implied_over
    return implied_over / total


# ── FanDuel public sportsbook API (no key, unofficial) ──────────────────────
# FanDuel's own web client calls these endpoints with a public app key, so no
# signup or quota applies. Unlike Underdog it quotes *both* sides of every prop,
# which is what lets the de-vig below normalise against a real under price rather
# than falling back to the raw, vig-inflated over.
#
# DraftKings has no equivalent: its old eventgroup endpoint now serves HTML and
# the newer host answers 403, so DK is reachable only through a metered API.
FD_BASE     = "https://sbapi.va.sportsbook.fanduel.com/api"
FD_AK       = "FhMFpcPWXMeyZxOx"
FD_PAGE_ID  = {"mlb": "mlb", "wnba": "wnba", "nba": "nba"}
FD_HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0 Safari/537.36",
    "Accept": "application/json",
}

# Over/under props are typed <ROLE>_<letter>_TOTAL_<core>[_<LEAGUE>]; the letter is just
# a per-event index. Strip it and map the core exactly — substring matching would let
# POINTS swallow POINTS_+_REBOUNDS_+_ASSISTS.
#
# FanDuel is inconsistent across leagues: WNBA still tags markets with a trailing
# _WNBA, but NBA and MLB dropped the league suffix (PLAYER_A_TOTAL_POINTS), and MLB
# pitcher lines use a PITCHER_ role instead of PLAYER_ (PITCHER_C_TOTAL_STRIKEOUTS).
# So the role is an alternation, the suffix is optional, and <core> is non-greedy so
# the optional suffix is stripped rather than swallowed into the core.
_FD_MARKET_RE = re.compile(
    r"^(?:PLAYER|PITCHER|BATTER)_[A-Z]+_TOTAL_(?P<core>.+?)(?:_(?:WNBA|NBA|MLB))?$"
)

_FD_HOOPS_CORE = {
    "POINTS":                       "Points",
    "REBOUNDS":                     "Rebounds",
    "ASSISTS":                      "Assists",
    "MADE_3_POINT_FIELD_GOALS":     "3-PT Made",
    # WNBA spells combos out in full; NBA abbreviates them (POINTS_+_REB_+_AST). Both
    # forms map to the same stat — a league only ever emits one of them. The 3-way is
    # observed live; the two-way abbreviations follow the identical naming scheme.
    "POINTS_+_REBOUNDS_+_ASSISTS":  "Pts+Rebs+Asts",
    "POINTS_+_REBOUNDS":            "Pts+Rebs",
    "POINTS_+_ASSISTS":             "Pts+Asts",
    "REBOUNDS_+_ASSISTS":           "Rebs+Asts",
    "POINTS_+_REB_+_AST":           "Pts+Rebs+Asts",
    "POINTS_+_REB":                 "Pts+Rebs",
    "POINTS_+_AST":                 "Pts+Asts",
    "REB_+_AST":                    "Rebs+Asts",
}
# MLB is in the All-Star break as this ships, so these cores are inferred from FanDuel's
# naming scheme rather than observed. run_sport prints any core it cannot map, so the
# first in-season run names whatever is missing instead of silently fetching nothing.
_FD_MLB_CORE = {
    "HITS":              "Hits",
    "HOME_RUNS":         "Home Runs",
    "TOTAL_BASES":       "Total Bases",
    "RUNS_SCORED":       "Runs Scored",
    "RUNS":              "Runs Scored",
    "STRIKEOUTS":        "Pitcher Strikeouts",
    "PITCHER_STRIKEOUTS": "Pitcher Strikeouts",
    "EARNED_RUNS":       "Earned Runs Allowed",
    "HITS_ALLOWED":      "Hits Allowed",
    "WALKS":             "Walks",
}
_FD_CORE_MAP = {"wnba": _FD_HOOPS_CORE, "nba": _FD_HOOPS_CORE, "mlb": _FD_MLB_CORE}

# MLB batter props aren't <ROLE>_TOTAL_<core> over/under lines like the hoops
# leagues turned out to be — FanDuel ships them as milestone yes/no markets, one
# runner per player, with the threshold baked into the market type ("2+ Hits" =
# Over 1.5, "A Hit" = Over 0.5). They are single-sided, so there is no under
# price to de-vig against; the raw implied over-probability stands, the same way
# Underdog's one-sided lines are handled. Keys are matched after stripping an
# optional leading PLAYER_ (FanDuel is inconsistent about the prefix). RBIs is
# deliberately absent — resolved history shows it is a genuinely bad prop — and
# triples / novelty markets have no model column to score against.
_FD_MLB_MILESTONE = {
    "TO_RECORD_A_HIT":             ("Hits", 0.5),
    "TO_RECORD_2+_HITS":           ("Hits", 1.5),
    "TO_RECORD_3+_HITS":           ("Hits", 2.5),
    "TO_HIT_A_HOME_RUN":           ("Home Runs", 0.5),
    "TO_HIT_2+_HOME_RUNS":         ("Home Runs", 1.5),
    "TO_RECORD_1+_HITS+RUNS+RBIS": ("Hits+Runs+RBIs", 0.5),
    "TO_RECORD_2+_HITS+RUNS+RBIS": ("Hits+Runs+RBIs", 1.5),
    "TO_RECORD_3+_HITS+RUNS+RBIS": ("Hits+Runs+RBIs", 2.5),
    "TO_RECORD_2+_TOTAL_BASES":    ("Total Bases", 1.5),
    "TO_RECORD_3+_TOTAL_BASES":    ("Total Bases", 2.5),
    "TO_RECORD_4+_TOTAL_BASES":    ("Total Bases", 3.5),
    "TO_RECORD_5+_TOTAL_BASES":    ("Total Bases", 4.5),
    "TO_RECORD_A_RUN":             ("Runs Scored", 0.5),
    "TO_RECORD_2+_RUNS":           ("Runs Scored", 1.5),
    "TO_RECORD_A_STOLEN_BASE":     ("Stolen Bases", 0.5),
    "TO_RECORD_2+_STOLEN_BASES":   ("Stolen Bases", 1.5),
    "TO_HIT_A_SINGLE":             ("Singles", 0.5),
    "TO_HIT_A_DOUBLE":             ("Doubles", 0.5),
}

# The resolver matches WNBA games on the abbreviations nba_api uses in MATCHUP, and
# FanDuel names teams in full, so a leg built from "Phoenix Mercury @ Minnesota Lynx"
# resolves only if the label says "PHX @ MIN".
_FD_WNBA_ABBR = {
    "atlanta dream": "ATL", "chicago sky": "CHI", "connecticut sun": "CON",
    "dallas wings": "DAL", "golden state valkyries": "GSV", "indiana fever": "IND",
    "las vegas aces": "LVA", "los angeles sparks": "LAS", "minnesota lynx": "MIN",
    "new york liberty": "NYL", "phoenix mercury": "PHX", "portland fire": "PDX",
    "seattle storm": "SEA", "toronto tempo": "TOR", "washington mystics": "WAS",
}
_FD_UNMAPPED: set = set()   # cores seen but not mapped — reported once per run


# ── FanDuel API ────────────────────────────────────────────────────────────

def _fd_team_abbr(sport: str, full_name: str) -> str:
    """FanDuel's full team name -> the abbreviation the resolver matches games on."""
    key = full_name.strip().lower()
    if sport == "wnba":
        return _FD_WNBA_ABBR.get(key, full_name.strip().upper()[:3])
    if sport == "nba":
        try:
            from nba_api.stats.static import teams as _t  # type: ignore
            for t in _t.get_teams():
                if t["full_name"].lower() == key:
                    return t["abbreviation"]
        except Exception:
            pass
    return full_name.strip().upper()[:3]


def _fd_american(runner: dict):
    """American price off a FanDuel runner, or None when it isn't quoted."""
    try:
        return int(runner["winRunnerOdds"]["americanDisplayOdds"]["americanOddsInt"])
    except Exception:
        return None


def fetch_fanduel(sport: str) -> pd.DataFrame:
    """
    Player props from FanDuel's public web API. No key, no quota.

    Tabs are discovered from the event's own layout rather than hard-coded, so this
    keeps working when FanDuel renames or adds one — and so MLB, which is mid-All-Star
    break and unobservable right now, works off whatever tabs it actually ships.
    """
    page_id = FD_PAGE_ID.get(sport)
    core_map = _FD_CORE_MAP.get(sport, {})
    if not page_id:
        return pd.DataFrame()

    try:
        r = requests.get(f"{FD_BASE}/content-managed-page",
                         params={"page": "CUSTOM", "customPageId": page_id,
                                 "_ak": FD_AK, "timezone": "America/New_York"},
                         headers=FD_HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"    FanDuel page fetch failed: HTTP {r.status_code}")
            return pd.DataFrame()
        events = r.json().get("attachments", {}).get("events", {})
    except Exception as e:
        print(f"    FanDuel fetch failed: {e}")
        return pd.DataFrame()

    # Futures and specials share the page; only real matchups have an "A @ B" name.
    games = {i: e for i, e in events.items() if " @ " in (e.get("name") or "")}
    rows = []
    # MLB milestone markets offer the same player at several thresholds (2+/3+/4+
    # Total Bases). They are correlated, so keep just one line per player+stat —
    # the one closest to a coin flip, which is the most informative and avoids the
    # heavy chalk ("To Record A Hit" at -425) crowding out balanced lines.
    milestone_best: dict = {}

    for ev_id, ev in games.items():
        away_full, home_full = [s.strip() for s in ev["name"].split(" @ ", 1)]
        away = _fd_team_abbr(sport, away_full)
        home = _fd_team_abbr(sport, home_full)
        label = f"{away} @ {home}"
        start = ev.get("openDate", "")

        try:
            time.sleep(0.25)
            base = requests.get(f"{FD_BASE}/event-page",
                                params={"eventId": ev_id, "_ak": FD_AK},
                                headers=FD_HEADERS, timeout=25)
            tabs = base.json().get("layout", {}).get("tabs", {}) if base.status_code == 200 else {}
        except Exception:
            continue

        prop_tabs = [t["title"] for t in tabs.values()
                     if any(w in (t.get("title") or "").lower()
                            for w in ("player", "batter", "pitcher", "hitter"))]

        for title in prop_tabs:
            try:
                time.sleep(0.25)
                rt = requests.get(f"{FD_BASE}/event-page",
                                  params={"eventId": ev_id, "_ak": FD_AK,
                                          "tab": title.lower().replace(" ", "-")},
                                  headers=FD_HEADERS, timeout=25)
                if rt.status_code != 200:
                    continue
                markets = rt.json().get("attachments", {}).get("markets", {})
            except Exception:
                continue

            for m in markets.values():
                mtype = m.get("marketType", "")
                match = _FD_MARKET_RE.match(mtype)
                if not match:
                    # MLB batter props are milestone yes/no markets, not over/unders.
                    if sport == "mlb":
                        key = mtype[7:] if mtype.startswith("PLAYER_") else mtype
                        milestone = _FD_MLB_MILESTONE.get(key)
                        if milestone:
                            mstat, mline = milestone
                            for run in m.get("runners", []):
                                american = _fd_american(run)
                                if american is None:
                                    continue
                                player = (run.get("runnerName") or "").strip()
                                if not player:
                                    continue
                                # One-sided market — no under to de-vig, raw implied stands.
                                implied = round(american_to_implied(american), 4)
                                bk = (ev_id, player, mstat)
                                prev = milestone_best.get(bk)
                                if prev is None or abs(implied - 0.5) < abs(prev["implied_prob"] - 0.5):
                                    milestone_best[bk] = {
                                        "player_name": player, "team": "", "stat_type": mstat,
                                        "line_score": mline, "odds_type": "standard",
                                        "american_odds": american, "implied_prob": implied,
                                        "game_id": str(ev_id), "game_label": label,
                                        "start_time": start, "sportsbook": "FanDuel",
                                    }
                    continue                      # alt lines ("To Score 20+") aren't over/unders
                core = match.group("core")
                stat = core_map.get(core)
                if not stat:
                    _FD_UNMAPPED.add(core)
                    continue

                over = under = None
                for run in m.get("runners", []):
                    name = (run.get("runnerName") or "")
                    if name.endswith(" Over"):
                        over = run
                    elif name.endswith(" Under"):
                        under = run
                if over is None:
                    continue
                american = _fd_american(over)
                if american is None:
                    continue

                player = (over.get("runnerName") or "")[: -len(" Over")].strip()
                try:
                    line = float(over.get("handicap"))
                except (TypeError, ValueError):
                    continue

                # Both sides are quoted, so the vig can actually be stripped — the whole
                # reason FanDuel is a better price source for the model than Underdog.
                imp_over = american_to_implied(american)
                imp_under = None
                if under is not None:
                    au = _fd_american(under)
                    if au is not None:
                        imp_under = american_to_implied(au)

                rows.append({
                    "player_name": player, "team": "", "stat_type": stat,
                    "line_score": line, "odds_type": "standard",
                    "american_odds": american,
                    "implied_prob": round(devig_two_way(imp_over, imp_under), 4),
                    "game_id": str(ev_id), "game_label": label,
                    "start_time": start, "sportsbook": "FanDuel",
                })

    rows.extend(milestone_best.values())
    return pd.DataFrame(rows) if rows else pd.DataFrame()


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
    abbr_map = _mlb_team_abbr_map()
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
                    _opp = s.get("opponent", {}) or {}
                    rows.append({
                        "date": s.get("date", ""),
                        "season": season,
                        "opponent": _opp.get("abbreviation") or abbr_map.get(_opp.get("id"), ""),
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
    abbr_map = _mlb_team_abbr_map()
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
                    _k  = int(st_data.get("strikeOuts") or 0)
                    _er = int(st_data.get("earnedRuns") or 0)
                    _h  = int(st_data.get("hits") or 0)
                    _bb = int(st_data.get("baseOnBalls") or 0)
                    # The API's era/whip are season-to-date cumulative values, not
                    # this game's. Compute per-game rates so each row matches its box.
                    k9   = round((_k  / ip * 9), 2) if ip > 0 else 0
                    era  = round((_er / ip * 9), 2) if ip > 0 else 0.0
                    whip = round(((_h + _bb) / ip), 2) if ip > 0 else 0.0
                    _opp = s.get("opponent", {}) or {}
                    _opp_abbr = _opp.get("abbreviation") or abbr_map.get(_opp.get("id"), "")
                    rows.append({
                        "date": s.get("date", ""),
                        "season": season,
                        "opponent": _opp_abbr,
                        "IP": round(ip, 1),
                        "H": _h,
                        "ER": _er,
                        "BB": _bb,
                        "K": _k,
                        "HR": int(st_data.get("homeRuns") or 0),
                        "NP": int(st_data.get("numberOfPitches") or 0),
                        "ERA": era,
                        "WHIP": whip,
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
    if col in ("PRA", "PA", "PR", "RA", "FS"):
        df = df.copy()
        if col == "PRA":
            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        elif col == "PA":
            df["PA"] = df["PTS"] + df["AST"]
        elif col == "PR":
            df["PR"] = df["PTS"] + df["REB"]
        elif col == "RA":
            df["RA"] = df["REB"] + df["AST"]
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
    if col in ("PRA", "PR", "PA", "RA"):
        df = df.copy()
        if col == "PRA":
            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        elif col == "PR":
            df["PR"] = df["PTS"] + df["REB"]
        elif col == "PA":
            df["PA"] = df["PTS"] + df["AST"]
        elif col == "RA":
            df["RA"] = df["REB"] + df["AST"]
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

def _build_parlays(legs: list, min_legs: int = 2, max_legs: int = 5, top_n: int = 50,
                    pool_size: int = 30, max_leg_uses: int = 6,
                    sportsbook: str = "PrizePicks", parlay_cal: dict | None = None,
                    min_ev: float = 0.0):
    """
    Safe  — highest probability combos (most likely to hit).
    Value — highest EV combos that are NOT already in Safe.
             Because EV grows with payout and payout grows with pick count,
             higher-pick combos naturally rise here even at lower probability,
             so Safe and Value show genuinely different options.
    Same player never appears twice in one parlay.

    Probability is the product of the legs', which assumes independence and honest leg
    probabilities. Neither holds, and the error compounds with pick count — 4-leg
    parlays were booked at ~16% and hit ~4%, against a 10% break-even, a structural
    -58% ROI. parlay_cal (from parlay_tracker.get_parlay_calibration) deflates the
    product by the measured overestimate for that pick count. The calibrated probability
    is what gets returned and logged, so the next round of calibration measures the
    model we actually ship.

    EV decides what is *recommended*, not what is *built*. Every combo is still returned
    and logged, tagged recommended=(ev > min_ev). Dropping the losers at build time was
    the obvious move and the wrong one: the parlay log is the training data — only legs
    inside logged parlays ever get resolved — so filtering generation cut leg-resolution
    data by ~85% and starved the very calibration the filter depends on. Bet only the
    recommended ones; keep learning from all of them.

    pool_size caps the input to the top-N legs by hit_rate before generating
    combinations — Underdog/fallback data can return hundreds of legs, and
    C(800,4) = 17B combinations will hang the app.

    max_leg_uses caps how many output parlays any single player+stat leg can
    appear in, so top_n isn't just recombinations of the same handful of
    highest-confidence legs (e.g. all "Hits" props flooding the safe list).
    """
    legs = sorted(legs, key=lambda x: x["hit_rate"], reverse=True)[:pool_size]
    parlay_cal = parlay_cal or {}

    results = []
    for n in range(min_legs, max_legs + 1):
        if n > len(legs):
            continue
        factor = float(parlay_cal.get(n, parlay_cal.get(str(n), 1.0)))
        for combo in combinations(legs, n):
            if len({l["player_name"] for l in combo}) < n:
                continue
            raw = 1.0
            for leg in combo:
                raw *= leg["hit_rate"]
            prob = min(0.99, max(0.001, raw * factor))
            payout = parlay_payout(sportsbook, combo)
            # Payouts are gross (a 2-pick returns 3x the entry), so a win nets
            # payout-1 and EV = prob*(payout-1) - (1-prob) = prob*payout - 1.
            ev = round(prob * payout - 1.0, 4)
            results.append({
                "legs": list(combo), "n": n,
                "prob": round(prob, 4), "raw_prob": round(raw, 4),
                "payout": payout, "ev": ev,
                "recommended": bool(ev > min_ev),
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

    # Recommended (positive-EV) combos sort ahead of the rest in both lists, so the bets
    # worth making lead. The losers are still returned and logged — they are the training
    # data — they just never sit at the top of the board.
    safe_out = _top(results, lambda x: (x["recommended"], x["prob"]),
                    max_uses=max_leg_uses)[:top_n]
    safe_keys = {frozenset(f"{l['player_name']}|{l['stat_type']}" for l in p["legs"]) for p in safe_out}
    value_out = _top(results, lambda x: (x["recommended"], x["ev"]),
                     exclude_keys=safe_keys, max_uses=max_leg_uses)[:top_n]

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
