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
from concurrent.futures import ThreadPoolExecutor
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
_SAVANT_BAT_SEL = ["pa", "barrel_batted_rate", "xiso", "xslg", "xba", "k_percent",
                   "bb_percent", "hard_hit_percent", "flyballs_percent", "pull_percent",
                   "exit_velocity_avg", "launch_angle_avg"]
_SAVANT_PIT_SEL = ["pa", "barrel_batted_rate", "xslg", "xba", "k_percent", "bb_percent",
                   "flyballs_percent", "groundballs_percent", "hard_hit_percent"]

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
            "xiso": g("xiso"), "xslg": g("xslg"), "xba": g("xba"),
            "k_pct": g("k_percent"), "bb_pct": g("bb_percent"),
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


# ── Statcast expected-stat model — P(over line) for any supported prop ───────
# The same idea as barrels→HR, generalised: each counting prop has a Statcast
# rate that predicts it better than recency (xBA→hits, xSLG→total bases, K%→
# strikeouts, BB%→walks). We turn that rate into a per-game count distribution
# and read off P(over the line). Where a hitter faces a specific pitcher, the
# rate is combined with the pitcher's rate-against via the log5 odds ratio.
_AB_PER_GAME = 4      # at-bats per game for a regular (binomial trials)
_PA_PER_GAME = 4      # plate appearances per game
_BF_START    = 22     # batters faced by a starter (~5.2 IP)

@_ttl_cache(21600)
def _savant_league():
    reg = [v for v in savant_batter_stats().values() if v.get("pa", 0) >= 200]
    def m(k, dflt):
        xs = [v[k] for v in reg if v.get(k) is not None]
        return sum(xs) / len(xs) if xs else dflt
    return {"xba": m("xba", 0.245), "k": m("k_pct", 22.5),
            "bb": m("bb_pct", 8.5), "xslg": m("xslg", 0.400)}

def _over_int(line):
    """Smallest integer strictly greater than the line (o0.5 -> 1, o1.5 -> 2)."""
    return int(math.floor(line)) + 1

def _binom_ge(k, n, p):
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    p = min(max(p, 0.0), 1.0)
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1)))

def _pois_ge(k, lam):
    if k <= 0:
        return 1.0
    cdf = sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(0, k))
    return max(0.0, min(1.0, 1 - cdf))

def _matchup(rate_b, rate_p, lg):
    """log5 odds ratio of a batter rate with the pitcher's rate-against."""
    if rate_b is None:
        return None
    if rate_p is not None and lg:
        return rate_b * rate_p / lg
    return rate_b

# The other batted-ball fields we already fetch — hardhit, fb, pull, ev, la — were tested
# as extra inputs and REJECTED. Against 786 resolved Home Runs legs, barrel alone explains
# R^2 0.031 of the actual outcome and adding all five gets 0.035. `pull` correlates +0.058
# with whether the homer happened, which is nothing.
#
# They look powerful against xISO (barrel R^2 0.88, all six 0.90) but that test is circular:
# xISO is itself derived from batted-ball data. Predicting a Statcast aggregate from
# Statcast inputs proves only that the aggregate is computed from them. Judge these against
# resolved outcomes or not at all.
def statcast_over_prob(pid, stat_type, line, is_pitcher, opp_pitcher_id=None,
                       opportunity=None):
    """
    P(stat > line) from Statcast expected rates, or None when unsupported.

    `opportunity` is this player's own expected batters faced (pitchers) or plate
    appearances (batters). It defaults to the league constants below, and that default is
    the third place the same bug lived: a Statcast rate is per-batter, so multiplying an
    excellent K% by a starter's 22 batters faced prices an opener as a starter. Sean
    Newcomb faces ~5.8 batters an outing and this component alone had his o1.5 strikeouts
    at 91%, which then carried 0.35 weight into the shipped price.

    Passing the measured opportunity makes the rate and the workload independent inputs
    here too, so all three components of the blend agree about how much of the game the
    player is actually going to see.
    """
    lg = _savant_league()
    k_over = _over_int(line)
    _bf = float(opportunity) if opportunity else _BF_START
    _ab = float(opportunity) if opportunity else _AB_PER_GAME
    _pa = float(opportunity) if opportunity else _PA_PER_GAME
    if is_pitcher:
        p = savant_pitcher_stats().get(pid) or {}
        if stat_type in ("Pitcher Strikeouts", "Strikeouts"):
            kp = p.get("k_pct")
            return _pois_ge(k_over, (kp / 100) * _bf) if kp is not None else None
        if stat_type == "Walks Allowed":
            bb = p.get("bb_pct")
            return _pois_ge(k_over, (bb / 100) * _bf) if bb is not None else None
        if stat_type == "Hits Allowed":
            xba = p.get("xba")
            return _pois_ge(k_over, xba * _bf) if xba is not None else None
        return None
    b = savant_batter_stats().get(pid) or {}
    opp = savant_pitcher_stats().get(opp_pitcher_id) if opp_pitcher_id else None
    if stat_type in ("Hits", "Singles"):
        r = _matchup(b.get("xba"), opp.get("xba") if opp else None, lg["xba"])
        if r is None:
            return None
        if stat_type == "Singles":
            r *= 0.66                      # singles are ~2/3 of all hits
        return _binom_ge(k_over, int(round(_ab)), min(0.65, max(0.05, r)))
    if stat_type == "Home Runs":
        return barrel_hr_prob(pid, opp_pitcher_id)
    if stat_type == "Total Bases":
        xslg = b.get("xslg")
        if xslg is None:
            return None
        if opp and opp.get("xslg") and lg["xslg"]:
            xslg = xslg * opp["xslg"] / lg["xslg"]
        return _pois_ge(k_over, xslg * _ab)
    if stat_type in ("Hitter Strikeouts", "Strikeouts"):
        r = _matchup(b.get("k_pct"), opp.get("k_pct") if opp else None, lg["k"])
        return _binom_ge(k_over, int(round(_pa)), min(0.70, max(0.05, r / 100))) if r is not None else None
    if stat_type == "Walks":
        r = _matchup(b.get("bb_pct"), opp.get("bb_pct") if opp else None, lg["bb"])
        return _binom_ge(k_over, int(round(_pa)), min(0.50, max(0.02, r / 100))) if r is not None else None
    return None


# ── Stat-type / column mappings ─────────────────────────────────────────────

# Payout ladders for DFS pick'em books, where an all-must-hit play pays a fixed
# multiplier by pick count. Traditional sportsbooks have no ladder — a parlay there
# pays the product of its legs' decimal odds — so they are deliberately absent, and
# parlay_payout() derives their payout from the odds instead.
#
# Underdog USED to sit here on a {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0} ladder, and it was
# the DraftKings mistake below repeated on a book that hides it better. The flat ladder
# is real, but it prices Underdog's standard pick'em lines, which sit near even money.
# The legs this model actually selects do not: their quoted american_price runs a median
# of -239 across 20,634 logged MLB legs, deciles -271 to -137. Underdog does not pay 20x
# on a 5-pick of -239 favourites, so the ladder was crediting a payout never on offer:
#
#     leg count   true product of quoted odds   ladder credited   overstated by
#     2                   2.01x                      3.0x             1.49x
#     3                   2.85x                      6.0x             2.10x
#     5                   5.74x                     20.0x             3.48x
#
# It made 9,178 resolved Underdog parlays read +27.1% ROI against -29.6% on the real
# prices, and marked 111 of 194 of one day's Underdog parlays "recommended" where the
# honest payout recommends 0. FanDuel, already on the product, was unaffected at 6.
#
# PrizePicks genuinely belongs here: its ladder IS the product on offer, and the
# american_odds carried on its legs are synthetic constants this repo assigns by
# odds_type (see PP_ODDS_IMPLIED), not quotes. Underdog quotes real, varying prices,
# so it is priced like any other book that does.
PAYOUT_TABLES = {
    "PrizePicks": {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0},
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
    payout that was never on offer, which made its EV and ROI fiction. A book gets a
    ladder only if the ladder is what it actually pays on the legs being selected;
    everything else gets the product of decimal odds. Underdog failed that test and was
    moved to the product — see the note above PAYOUT_TABLES.

    A leg with no usable american_odds falls back to 2.0 via american_to_decimal, so a
    book that stops quoting prices degrades to even money per leg rather than silently
    inheriting somebody else's ladder.
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


# FanDuel's MLB batter props are one-sided milestone markets ("To Record A Hit"), one
# runner per player with a Yes price and no No side, so devig_two_way has nothing to
# normalise against and the raw, vig-inflated number used to stand. That covers Hits,
# Home Runs, Total Bases and Runs Scored — every MLB market except Pitcher Strikeouts,
# which is a true over/under and does get de-vigged.
#
# The overround was measured against the de-vigged book: 247 props priced by both
# FanDuel and Underdog on the same day, same player, stat and line, gave a mean
# FD/UD ratio of 1.0485. The control holds — FanDuel's two-way Pitcher Strikeouts
# market, which IS de-vigged, came in at 0.9851 against the same book, i.e. no gap.
#
# It is a flat estimate and honest about being one: 226 of those 247 pairs sit between
# 0.50 and 0.70 implied, so that is where it is fitted. The >=0.70 bucket measured 1.086
# (n=26) and books normally hold MORE on long shots, so this likely UNDER-corrects the
# tails — Home Runs especially, where the overlap sample was 3 props. It is still
# strictly better than shipping the raw number. Re-fit it when the tails have data.
FD_ONE_SIDED_OVERROUND = 1.0586

# Re-fit 2026-08-16 on 345 props priced by both books the same day, comparing FanDuel's
# RAW price against Underdog's de-vigged one. Selection cancels in that comparison —
# whatever the model chose to log, both books quoted the same prop.
#
# The headline is a negative result. One-sided milestone markets hold 1.0586 against a
# two-way control (Pitcher Strikeouts, n=97) at 1.0511 — a difference of 0.0075, about
# 1.8 standard errors. The worry that prompted this, that a market quoting no second side
# hides a much fatter margin, is not supported: FanDuel charges roughly the same either
# way, and the original flat 1.0485 was already close.
#
# Price dependence is real but small and NOT the monotonic shape reported earlier in the
# week. Measured band means run 0.976 / 0.993 / 1.079 / 1.099 / 1.055 across ascending
# price — an inverted U, not a favourite-longshot ramp. Two bands sit 4+ standard errors
# off pooled, so it is not all noise, but the tails carry n=8 and n=21 and the shape
# contradicts how books usually price, so the curve below is shrunk hard toward pooled
# (a band needs ~30 pairs to move halfway). That keeps the fit from chasing eight props.
#
# An earlier attempt estimated the hold from resolved outcomes instead — raw implied
# against the actual hit rate. It produced ratios of 1.14 to 1.60 and failed its own
# control: the two-way market, which should read ~1.00, came out at 1.135. The estimator
# was measuring the model's own anti-predictive selection, not the book's margin. It is
# recorded here because it looks like the more obvious approach and is worse.
_FD_OVERROUND_CURVE = [
    (0.100, 1.0411),
    (0.275, 1.0316),
    (0.425, 1.0667),
    (0.575, 1.0852),
    (0.725, 1.0557),
]


# Refit 2026-08-23 against 2,350 resolved one-sided MLB props, and the old curve was
# stripping about half the margin it should. Method: bucket legs by RAW implied (the
# curve's input, straight off the American odds), take the realized hit rate per bucket,
# and the divisor that makes de-vigged equal reality is mean_raw / realized. Buckets are
# shrunk toward the pooled divisor (1.1361) with a 120-leg pseudo-count so a thin band
# cannot fit its own noise.
#
#   raw band     n     raw     realized   fit     shrunk
#   0.05-0.15   151   11.0%      9.9%    1.105   1.1187
#   0.15-0.25   156   19.1%     13.5%    1.416   1.2944   <- least certain, see below
#   0.25-0.35   146   31.3%     27.4%    1.144   1.1403
#   0.35-0.45   322   40.1%     34.5%    1.163   1.1559
#   0.45-0.55   283   49.7%     41.0%    1.212   1.1891
#   0.55-0.65   321   61.3%     55.1%    1.112   1.1183
#   0.65-0.75   965   68.9%     61.7%    1.117   1.1188
#
# So FanDuel's yes/no milestone markets carry roughly a 12-19% overround, which is normal
# for alt markets and far above the 3-9% the old curve assumed. Every batter prop was
# being de-vigged to a probability about 11% too high, which inflated EV — and then
# per-stat calibration (0.95-0.98) and parlay calibration (0.89-0.90) deflated it back.
# Three corrections stacked on a mis-specified root.
#
# The 0.15-0.25 point is the one to distrust: n=156, realized 13.5% has a standard error
# near 2.7 points, and the unshrunk fit of 1.42 would mean a 42% hold. Shrinkage pulls it
# to 1.29. A higher divisor in that band is at least theoretically expected — it is the
# +300 to +500 longshot range where books apply the most margin — but refresh it before
# leaning on it.
_FD_OVERROUND_CURVE_MLB = [
    (0.110, 1.1187),
    (0.191, 1.2944),
    (0.313, 1.1403),
    (0.401, 1.1559),
    (0.497, 1.1891),
    (0.613, 1.1183),
    (0.689, 1.1188),
]
# Keyed by sport because the fit is MLB-only. WNBA carries 2,820 FanDuel legs of its own
# and nothing here shows the same margin applies to them, so everything else keeps the
# original curve until it has been measured the same way.
_FD_OVERROUND_CURVES = {"mlb": _FD_OVERROUND_CURVE_MLB}


def devig_one_sided(implied: float, sport: str | None = None) -> float:
    """
    Strip an estimated margin from a market that quotes only one side.

    Same multiplicative form as devig_two_way — that divides by the observed overround
    (p_over + p_under); this divides by a fitted one, because the second side does not
    exist to observe. The divisor varies with price, interpolated between the fitted
    band midpoints and held flat outside them rather than extrapolated, since the
    outermost bands are the thinnest.
    """
    if implied <= 0:
        return implied
    pts = _FD_OVERROUND_CURVES.get(str(sport or "").lower(), _FD_OVERROUND_CURVE)
    if implied <= pts[0][0]:
        div = pts[0][1]
    elif implied >= pts[-1][0]:
        div = pts[-1][1]
    else:
        div = pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= implied <= x1:
                div = y0 + (y1 - y0) * (implied - x0) / (x1 - x0)
                break
    return min(0.99, implied / div)


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
FD_PAGE_ID  = {"mlb": "mlb", "wnba": "wnba", "nba": "nba", "nfl": "nfl"}
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
    r"^(?:PLAYER|PITCHER|BATTER)_[A-Z]+_TOTAL_(?P<core>.+?)(?:_(?:WNBA|NBA|MLB|NFL))?$"
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
# NFL is pre-season as this ships, so — like MLB during the All-Star break above — these cores
# are inferred from FanDuel's naming scheme, not observed. _fd_parse_event prints any core it
# can't map, so the first in-season run names whatever is missing instead of fetching nothing.
# Labels match nfl_analysis.PROP_STATS so the scorer lines up.
_FD_NFL_CORE = {
    "PASSING_YARDS":         "Passing Yards",
    "PASSING_TOUCHDOWNS":    "Passing TDs",
    "PASS_COMPLETIONS":      "Completions",
    "COMPLETIONS":           "Completions",
    "RUSHING_YARDS":         "Rushing Yards",
    "RUSHING_TOUCHDOWNS":    "Rushing TDs",
    "RUSH_ATTEMPTS":         "Carries",
    "CARRIES":               "Carries",
    "RECEIVING_YARDS":       "Receiving Yards",
    "RECEIVING_TOUCHDOWNS":  "Receiving TDs",
    "RECEPTIONS":            "Receptions",
}
_FD_CORE_MAP = {"wnba": _FD_HOOPS_CORE, "nba": _FD_HOOPS_CORE, "mlb": _FD_MLB_CORE,
                "nfl": _FD_NFL_CORE}

# FanDuel full team name -> abbreviation, matching nflverse team codes so the scorer/resolver
# line up. 32 teams; used by _fd_team_abbr for sport == "nfl".
_FD_NFL_ABBR = {
    "arizona cardinals": "ARI", "atlanta falcons": "ATL", "baltimore ravens": "BAL",
    "buffalo bills": "BUF", "carolina panthers": "CAR", "chicago bears": "CHI",
    "cincinnati bengals": "CIN", "cleveland browns": "CLE", "dallas cowboys": "DAL",
    "denver broncos": "DEN", "detroit lions": "DET", "green bay packers": "GB",
    "houston texans": "HOU", "indianapolis colts": "IND", "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC", "las vegas raiders": "LV", "los angeles chargers": "LAC",
    "los angeles rams": "LA", "miami dolphins": "MIA", "minnesota vikings": "MIN",
    "new england patriots": "NE", "new orleans saints": "NO", "new york giants": "NYG",
    "new york jets": "NYJ", "philadelphia eagles": "PHI", "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF", "seattle seahawks": "SEA", "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN", "washington commanders": "WAS",
}

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
# FanDuel names MLB teams in full; map to the ESPN-style abbreviations the rest
# of the app displays (ticker, blog). First-3-letters fallback produced labels
# like "ST. @ ARI" for St. Louis.
_FD_MLB_ABBR = {
    "arizona diamondbacks": "ARI", "athletics": "ATH", "atlanta braves": "ATL",
    "baltimore orioles": "BAL", "boston red sox": "BOS", "chicago cubs": "CHC",
    "chicago white sox": "CHW", "cincinnati reds": "CIN", "cleveland guardians": "CLE",
    "colorado rockies": "COL", "detroit tigers": "DET", "houston astros": "HOU",
    "kansas city royals": "KC", "los angeles angels": "LAA", "los angeles dodgers": "LAD",
    "miami marlins": "MIA", "milwaukee brewers": "MIL", "minnesota twins": "MIN",
    "new york mets": "NYM", "new york yankees": "NYY", "philadelphia phillies": "PHI",
    "pittsburgh pirates": "PIT", "san diego padres": "SD", "san francisco giants": "SF",
    "seattle mariners": "SEA", "st. louis cardinals": "STL", "tampa bay rays": "TB",
    "texas rangers": "TEX", "toronto blue jays": "TOR", "washington nationals": "WSH",
    # Legacy name still appears in some FanDuel feeds
    "oakland athletics": "ATH",
}
_FD_UNMAPPED: set = set()   # cores seen but not mapped — reported once per run


# ── FanDuel API ────────────────────────────────────────────────────────────

# Books spell the same club differently, so a game_label is not a portable key until it
# is normalised: on 2026-08-15 FanDuel wrote "ARI @ ATL" and "CHW @ DET" for fixtures
# Underdog and PrizePicks wrote as "AZ @ ATL" and "CWS @ DET", and WNBA Portland is PDX
# on FanDuel and POR on Underdog. Anything joining across books on the label silently
# treats those as different games — which is exactly what the pocket alert's cross-book
# dedupe does, and what game_tracker's sport|label|start_time identity would do the
# moment a second book is added.
#
# Canonical forms are taken from parlay_tracker._MLB_ABBR_ALIASES so labels agree with
# the resolver rather than introducing a third spelling. ATH/OAK is deliberately left
# alone: every book writes ATH today, and the resolver's alias map already matches it.
#
# ESPN is a third vocabulary again, and it is the one the game resolver reads: it writes
# NY/LA/WSH/LV/GS where the books write NYL/LAS/WAS/LVA/GSV. Every WNBA game in the
# ledger failed to settle because of it — resolve_espn matched book abbreviations against
# ESPN's and never found a single one. Books are canonical here since they are what gets
# logged; ESPN's spellings fold into them.
_ABBR_CANONICAL = {
    "mlb":  {"AZ": "ARI", "CHW": "CWS", "WAS": "WSH", "WSN": "WSH"},
    "wnba": {"PDX": "POR", "NY": "NYL", "LA": "LAS", "WSH": "WAS",
             "LV": "LVA", "GS": "GSV"},
    "nba":  {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP",
             "UTAH": "UTA", "WSH": "WAS"},
}
_ABBR_UNMAPPED: set = set()


def canonical_abbr(sport: str, abbr: str) -> str:
    """One spelling per club, whatever the book called it."""
    a = str(abbr or "").strip().upper()
    # Underdog marks doubleheader halves in the abbreviation itself ("CLE (Game 1)").
    # The half is already carried by game_id and start_time; keeping it here would fork
    # the label and defeat the normalisation.
    a = re.sub(r"\s*\(GAME\s*\d+\)\s*$", "", a).strip()
    return _ABBR_CANONICAL.get(str(sport).lower(), {}).get(a, a)


def _fd_team_abbr(sport: str, full_name: str) -> str:
    """FanDuel's full team name -> the abbreviation the resolver matches games on."""
    # MLB event names embed the probable pitcher — "Los Angeles Dodgers (W Klein)" —
    # so strip any trailing parenthetical before the lookup.
    full_name = re.sub(r"\s*\([^)]*\)\s*$", "", full_name.strip())
    key = full_name.lower()
    table = {"wnba": _FD_WNBA_ABBR, "mlb": _FD_MLB_ABBR, "nfl": _FD_NFL_ABBR}.get(sport)
    if table is not None:
        hit = table.get(key)
        if hit:
            return canonical_abbr(sport, hit)
    elif sport == "nba":
        # Import outside the try on purpose: a missing or renamed module is a coding
        # error and must fail loudly, because the fallback below INVENTS an abbreviation
        # from the first three characters — the mechanism that produced "LOS" for both
        # Los Angeles clubs and left 90 legs permanently unresolvable.
        from nba_api.stats.static import teams as _t  # type: ignore
        try:
            for t in _t.get_teams():
                if t["full_name"].lower() == key:
                    return canonical_abbr(sport, t["abbreviation"])
        except Exception:
            pass
    # The old fallback was full_name[:3], which does not fail — it quietly invents an
    # abbreviation, and for MLB it invented AMBIGUOUS ones: "LOS" for both Los Angeles
    # clubs, "NEW" for both New York, "SAN" for San Diego and San Francisco, plus "ST.",
    # "KAN", "TAM". 2,925 legs carry those from 2026-07, and any two teams sharing a
    # prefix were merged into one label. Record the miss so it surfaces the way an
    # unmapped market already does, rather than being absorbed silently.
    _ABBR_UNMAPPED.add(f"{sport}:{full_name}")
    return canonical_abbr(sport, full_name.strip().upper()[:3])


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

    # Each event is independent (own id, own tabs), so fetch them concurrently — the
    # serial walk with per-request sleeps was the whole reason a build took ~30-40s.
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fd_parse_event, sport, core_map, ev_id, ev)
                   for ev_id, ev in games.items()]
        for fut in futures:
            try:
                rows.extend(fut.result())
            except Exception:
                continue
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_moneylines(sport: str) -> pd.DataFrame:
    """
    Game-level moneylines from the same FanDuel page the prop fetcher already walks.

    Both sides are quoted, so unlike the MLB batter props (one-sided milestone markets
    needing the estimated FD_ONE_SIDED_OVERROUND) this de-vigs honestly against a real
    opposite price. That matters more here than anywhere else in the repo: a de-vigged
    moneyline is the benchmark a winner model has to beat. Without it "accuracy" is
    meaningless — home teams win 52.4% and favourites ~57%, so a model can look strong
    and carry no edge at all. Grading props against nothing is how four separate defects
    inflated EV for months; this exists so the same thing cannot happen to game picks.

    Returns one row per game: both teams, both prices, and the vig-free home win
    probability. Empty frame when the page has no games.
    """
    page_id = FD_PAGE_ID.get(sport)
    if not page_id:
        return pd.DataFrame()
    try:
        r = requests.get(f"{FD_BASE}/content-managed-page",
                         params={"page": "CUSTOM", "customPageId": page_id,
                                 "_ak": FD_AK, "timezone": "America/New_York"},
                         headers=FD_HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"    FanDuel moneyline page failed: HTTP {r.status_code}")
            return pd.DataFrame()
        events = r.json().get("attachments", {}).get("events", {})
    except Exception as e:
        print(f"    FanDuel moneyline fetch failed: {e}")
        return pd.DataFrame()

    games = {i: e for i, e in events.items() if " @ " in (e.get("name") or "")}
    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in [ex.submit(_fd_parse_moneyline, sport, i, e) for i, e in games.items()]:
            try:
                row = fut.result()
            except Exception:
                continue
            if row:
                rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _fd_parse_moneyline(sport, ev_id, ev):
    """One event's MONEY_LINE market. Returns a row dict, or None if not quoted yet."""
    name = ev.get("name") or ""
    try:
        away_full, home_full = [s.strip() for s in name.split(" @ ", 1)]
    except ValueError:
        return None
    # MLB event names carry the probable starter: "Kansas City Royals (R Dobnak)".
    def _strip(t):
        return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()
    away_full, home_full = _strip(away_full), _strip(home_full)

    try:
        rt = requests.get(f"{FD_BASE}/event-page", params={"eventId": ev_id, "_ak": FD_AK},
                          headers=FD_HEADERS, timeout=25)
        if rt.status_code != 200:
            return None
        markets = rt.json().get("attachments", {}).get("markets", {})
    except Exception:
        return None

    for m in markets.values():
        if m.get("marketType") != "MONEY_LINE":
            continue
        prices = {}
        for run in m.get("runners", []):
            odds = _fd_american(run)
            if odds is None:
                continue
            prices[_strip(run.get("runnerName") or "")] = odds
        home_odds, away_odds = prices.get(home_full), prices.get(away_full)
        if home_odds is None or away_odds is None:
            continue
        # Out of season the page still lists futures, specials and simulated matchups,
        # and their names contain " @ " so they survive the event filter. The prop
        # fetcher never noticed because they carry no player markets; this one finds a
        # MONEY_LINE on them and would log 21 fictional NBA games in August, priced at
        # -100000. A real two-way game price is neither locked nor wildly overround.
        if max(abs(home_odds), abs(away_odds)) >= 10000:
            continue
        overround = american_to_implied(home_odds) + american_to_implied(away_odds)
        if not (1.0 <= overround <= 1.20):
            continue
        p_home = devig_two_way(american_to_implied(home_odds), american_to_implied(away_odds))
        return {
            "game_id": str(ev_id),
            "sport": sport.upper(),
            "away_team": _fd_team_abbr(sport, away_full),
            "home_team": _fd_team_abbr(sport, home_full),
            "away_name": away_full,
            "home_name": home_full,
            "game_label": f"{_fd_team_abbr(sport, away_full)} @ {_fd_team_abbr(sport, home_full)}",
            "start_time": ev.get("openDate", ""),
            "home_odds": int(home_odds),
            "away_odds": int(away_odds),
            "home_implied_raw": round(american_to_implied(home_odds), 4),
            "away_implied_raw": round(american_to_implied(away_odds), 4),
            "market_home_prob": round(p_home, 4),
            "overround": round(overround, 4),
            "sportsbook": "FanDuel",
        }
    return None


def _fd_parse_event(sport, core_map, ev_id, ev):
    """Fetch and parse one FanDuel event's player-prop tabs. Returns a list of leg
    rows (over/under + MLB milestone). Runs in a worker thread, so it owns all its
    state and shares nothing but the read-only maps."""
    try:
        away_full, home_full = [s.strip() for s in ev["name"].split(" @ ", 1)]
    except (KeyError, ValueError):
        return []
    label = f"{_fd_team_abbr(sport, away_full)} @ {_fd_team_abbr(sport, home_full)}"
    start = ev.get("openDate", "")

    try:
        base = requests.get(f"{FD_BASE}/event-page",
                            params={"eventId": ev_id, "_ak": FD_AK},
                            headers=FD_HEADERS, timeout=25)
        tabs = base.json().get("layout", {}).get("tabs", {}) if base.status_code == 200 else {}
    except Exception:
        return []

    prop_tabs = [t["title"] for t in tabs.values()
                 if any(w in (t.get("title") or "").lower()
                        for w in ("player", "batter", "pitcher", "hitter",
                                  "passing", "rushing", "receiving"))]

    rows = []
    # MLB milestone markets offer the same player at several thresholds (2+/3+/4+
    # Total Bases). They are correlated, so keep just one line per player+stat —
    # the one closest to a coin flip, which is the most informative and avoids the
    # heavy chalk ("To Record A Hit" at -425) crowding out balanced lines.
    milestone_best: dict = {}
    for title in prop_tabs:
        try:
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
                            # One-sided market — no under price exists, so the margin
                            # comes off with a measured overround instead of an observed
                            # one (see FD_ONE_SIDED_OVERROUND). Leaving it raw overstated
                            # every MLB batter prop by ~4.9% and, because MLB's blend
                            # weight is 0.258, that inflated number was ~74% of the
                            # shipped hit_rate.
                            implied = round(devig_one_sided(american_to_implied(american), sport), 4)
                            bk = (player, mstat)
                            prev = milestone_best.get(bk)
                            if prev is None or abs(implied - 0.5) < abs(prev["implied_prob"] - 0.5):
                                milestone_best[bk] = {
                                    "player_name": player, "team": "", "stat_type": mstat,
                                    "line_score": mline, "odds_type": "standard",
                                    "american_odds": american, "implied_prob": implied,
                                    "game_id": str(ev_id), "game_label": label,
                                    "start_time": start, "sportsbook": "FanDuel",
                                    # Milestone markets are yes/no with no second side to
                                    # bet, so they are always the "over" of their threshold.
                                    "side": "over",
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
                "side": "over",
            })

            # The under, when the book actually quotes one. Only two-way markets get this:
            # the milestone path below is a yes/no market with no second side to bet, so
            # emitting an under there would invent a price.
            #
            # Worth having because the board only ever bet overs, and on the one MLB market
            # where both sides exist the over is the losing side. Over 320 resolved
            # two-way pitcher-strikeout legs from starters: over -10.3% ROI, under +0.5%.
            # Against a 5.8% vig a fair market puts both near -2.9%, so the over runs 7.4
            # points worse than fair and the under 3.4 better — the signature of a shaded
            # over. The under is NOT a proven edge (+0.5% carries a standard error near
            # 5.6%); it is a side the system could not previously even consider.
            if imp_under is not None and au is not None:
                rows.append({
                    "player_name": player, "team": "", "stat_type": stat,
                    "line_score": line, "odds_type": "standard",
                    "american_odds": au,
                    # devig_two_way normalises by the same total either way, so the two
                    # sides sum to exactly 1 and the model can work purely in over-space.
                    "implied_prob": round(devig_two_way(imp_under, imp_over), 4),
                    "game_id": str(ev_id), "game_label": label,
                    "start_time": start, "sportsbook": "FanDuel",
                    "side": "under",
                })

    rows.extend(milestone_best.values())
    return rows


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

    # Fold in the minutes model. Same weighting as the MLB opportunity models: it is the
    # principled version of the same signal — identical games, but minutes and per-minute
    # rate kept apart instead of fused into a threshold frequency — so it carries the
    # larger share, with the frequency term retained as a hedge until calibration has
    # graded it.
    try:
        opp_p = _hoops_min_over_prob(df, stat_type, col, line)
    except Exception:
        opp_p = None
    if opp_p is not None:
        hist = min(0.97, max(0.03, 0.65 * opp_p + 0.35 * hist))
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

    # Fold in the minutes model. Same weighting as the MLB opportunity models: it is the
    # principled version of the same signal — identical games, but minutes and per-minute
    # rate kept apart instead of fused into a threshold frequency — so it carries the
    # larger share, with the frequency term retained as a hedge until calibration has
    # graded it.
    try:
        opp_p = _hoops_min_over_prob(df, stat_type, col, line)
    except Exception:
        opp_p = None
    if opp_p is not None:
        hist = min(0.97, max(0.03, 0.65 * opp_p + 0.35 * hist))
    implied = implied_override if implied_override >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    rate = 0.7 * hist + 0.3 * implied
    rate = rate * cal_factor
    return round(min(0.97, max(0.03, rate)), 3), n


# ── Plate-appearance model for batter props ─────────────────────────────────
#
# The frequency estimator below computes P(over) as the share of a batter's last 20 games
# in which he cleared the line. That silently conditions on a normal workload, because the
# only games in the log are ones he played — and the bet does not get that guarantee.
# Measured over 2,718 resolved batter legs matched to their box scores:
#
#   AB >= 4   59.9% of legs   over hits 55.9%
#   AB 1-3    35.1% of legs   over hits 38.3%
#   AB 0       5.0% of legs   over hits  2.2%   (now voided in the resolver)
#
# 40% of legs resolve on a short day. If every leg had a full workload the rate would be
# 55.9%; it is actually 47.1%, and that 8.8-point gap matches the market error measured
# independently per market (-7.4 Hits, -8.1 Runs Scored, -7.0 Total Bases). Pitcher
# Strikeouts, the one market where workload is announced in advance, is off by 1.1.
#
# So decompose it the way the NFL scorer decomposes usage from volume:
#
#   P(over) = SUM_k  P(PA = k) * P(stat > line | k plate appearances)
#
# Both halves come from the player's own game log, which means the PA distribution
# already encodes his lineup slot, his rest pattern and his platoon usage without needing
# a lineup fetch — lineups are not posted when the 2 PM board is built.
#
# Conditioned on PA >= 1, because a zero-PA game is a void rather than a loss.

_MLB_PA_LOOKBACK = 30      # games behind the PA distribution and the per-PA rates
_MLB_PA_MIN_GAMES = 12     # below this the distribution is too lumpy to marginalise over


def _binom_sf(line: float, n: int, p: float) -> float:
    """P(X > line) for X ~ Binomial(n, p)."""
    import math
    k = int(math.floor(line))
    if k < 0:
        return 1.0
    if k >= n:
        return 0.0
    p = min(max(p, 0.0), 1.0)
    cdf = 0.0
    for i in range(0, k + 1):
        cdf += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return max(0.0, min(1.0, 1.0 - cdf))


def _tb_sf(line: float, n: int, probs: dict) -> float:
    """
    P(total bases > line) over n plate appearances.

    Total bases is not binomial — one PA yields 0, 1, 2, 3 or 4 — so convolve the per-PA
    distribution n times. The line is 1.5 or 2.5 in practice, so only the low tail of the
    convolution is needed and this stays cheap.
    """
    import math
    cap = int(math.floor(line)) + 1
    dist = {0: 1.0}
    for _ in range(n):
        nxt: dict = {}
        for total, w in dist.items():
            if total > cap:
                nxt[cap + 1] = nxt.get(cap + 1, 0.0) + w
                continue
            for bases, pb in probs.items():
                t = min(total + bases, cap + 1)
                nxt[t] = nxt.get(t, 0.0) + w * pb
        dist = nxt
    return max(0.0, min(1.0, sum(w for t, w in dist.items() if t > line)))


def _mlb_pa_over_prob(df, stat_type: str, line: float, slot: int | None = None):
    """
    P(stat > line) marginalised over the batter's own plate-appearance distribution.
    Returns None when the market or the sample cannot support it.

    `slot` is his posted batting-order position when the lineup is out. His own PA history
    is a proxy for workload that averages over every slot he has hit in and every day he
    was rested; once the card is posted, the slot is the fact. The distribution is RESCALED
    to the slot's expected PA rather than replaced by it, which keeps his own shape — a
    catcher and a leadoff man batting second do not have the same spread — and moves only
    the location, the same treatment sigma gets in the NFL scorer.
    """
    need = {"AB", "BB"}
    if df is None or df.empty or not need.issubset(df.columns):
        return None
    sub = df.tail(_MLB_PA_LOOKBACK)
    pa_series = [int(a or 0) + int(b or 0) for a, b in zip(sub["AB"], sub["BB"])]
    pa_series = [k for k in pa_series if k >= 1]      # 0 PA is a void, not a data point
    if len(pa_series) < _MLB_PA_MIN_GAMES:
        return None
    pa_total = float(sum(pa_series))
    if pa_total <= 0:
        return None
    weights: dict = {}
    scale = 1.0
    if slot and slot in _MLB_SLOT_PA:
        own = sum(pa_series) / len(pa_series)
        if own > 0:
            scale = _MLB_SLOT_PA[slot] / own
    for k in pa_series:
        # Plate appearances are integers, so a scaled count lands between two of them.
        # Rounding to the nearest throws away most of the slot signal — the whole spread
        # from leadoff to ninth is 1.16 PA, and rounding collapsed slots 1, 3 and 5 onto
        # an identical answer. Splitting the weight between the neighbours keeps the
        # distribution continuous in the slot.
        import math
        v = max(1.0, k * scale)
        lo, hi = int(math.floor(v)), int(math.ceil(v))
        frac = v - lo
        w = 1.0 / len(pa_series)
        if lo == hi:
            weights[lo] = weights.get(lo, 0.0) + w
        else:
            weights[lo] = weights.get(lo, 0.0) + w * (1 - frac)
            weights[hi] = weights.get(hi, 0.0) + w * frac

    def _rate(col):
        return float(sub[col].sum()) / pa_total if col in sub.columns else None

    if stat_type in ("Hits", "Singles"):
        p = _rate("H")
        f = lambda k: _binom_sf(line, k, p)
    elif stat_type == "Home Runs":
        p = _rate("HR")
        f = lambda k: _binom_sf(line, k, p)
    elif stat_type in ("Runs Scored", "Runs"):
        p = _rate("R")
        f = lambda k: _binom_sf(line, k, p)
    elif stat_type == "Walks":
        p = _rate("BB")
        f = lambda k: _binom_sf(line, k, p)
    elif stat_type == "Total Bases":
        h, hr = _rate("H"), _rate("HR")
        d2, d3 = _rate("2B"), _rate("3B")
        if None in (h, hr, d2, d3):
            return None
        singles = max(0.0, h - d2 - d3 - hr)
        probs = {1: singles, 2: d2, 3: d3, 4: hr}
        probs[0] = max(0.0, 1.0 - sum(probs.values()))
        f = lambda k: _tb_sf(line, k, probs)
    else:
        return None
    if stat_type != "Total Bases" and (p is None or p <= 0):
        return None
    return round(sum(w * f(k) for k, w in weights.items()), 4)


# ── Batters-faced model for pitcher props ───────────────────────────────────
#
# Third instance of the same structural gap: the scorer has a good RATE and no model of
# the OPPORTUNITY it applies over. For batters that was plate appearances; here it is
# batters faced, and it is what separates a starter from an opener.
#
# The frequency estimator weights a pitcher's last 3 outings at 50%, which is reasonable
# form-tracking for someone who throws 5-6 innings and nonsense for someone who throws
# one. On 2026-08-23 it priced Sean Newcomb's o1.5 strikeouts at 64.6% off a 2-for-3
# recent streak, against a 3-for-10 record over ten straight outings of 2.3 innings or
# fewer. The pocket alert now filters those, but filtering only guards the alert — the
# leg still prices at 64.6% inside a parlay or the BET tab. This fixes the price.
#
#   P(over) = SUM_b  P(BF = b) * P(stat > line | b batters faced)
#
# Both halves come from the pitcher's own log, so the BF distribution carries his role and
# his leash without needing to know whether tonight is a start or an opener appearance.
#
# BF is not in the feed and is reconstructed as outs + hits + walks. IP here is thirds of
# an inning written as .0/.3/.7 (never baseball's .1/.2 — verified across five pitchers'
# logs), so outs = round(IP * 3) is exact rather than approximate.

_MLB_BF_LOOKBACK = 12      # recent outings behind the BF distribution and the per-BF rate
_MLB_BF_MIN_GAMES = 5

# Markets that are a per-batter Bernoulli trial, so binomial in batters faced. Earned runs
# are excluded on purpose: runs cluster within an inning rather than arriving independently
# per batter, so a binomial would understate the tail that actually decides those props.
_MLB_BF_RATE_COL = {
    "Pitcher Strikeouts": "K",
    "Strikeouts":         "K",
    "Walks Allowed":      "BB",
    "Hits Allowed":       "H",
}


# Expected plate appearances by batting-order slot, measured over 216 games (2026-08-01 to
# 08-16, starters only — battingOrder ending in 00; 101/102 are substitutes):
#
#   slot 1  4.43 PA   P(PA>=4) 0.92        slot 6  3.84 PA   P(PA>=4) 0.74
#   slot 2  4.32       0.91                slot 7  3.63       0.62
#   slot 3  4.24       0.89                slot 8  3.50       0.53
#   slot 4  4.06       0.86                slot 9  3.27       0.42
#   slot 5  4.00       0.82
#
# A 1.16 PA spread top to bottom, and P(PA>=4) more than halves. This is the information
# the market has at lineup posting and the 2 PM board does not — measured at 1.3 CLV points
# on batter props, against +0.13 for pitcher strikeouts where the workload is announced.
_MLB_SLOT_PA = {1: 4.43, 2: 4.32, 3: 4.24, 4: 4.06, 5: 4.00,
                6: 3.84, 7: 3.63, 8: 3.50, 9: 3.27}


def mlb_posted_lineups(date_str: str) -> dict:
    """
    Normalised player name -> batting-order slot for every lineup posted on a date.

    Empty until lineups go up (roughly 3-4 hours before first pitch), which is the whole
    point: this is late information and calling it early gets you nothing.
    """
    out = {}
    try:
        import statsapi
    except ImportError:
        return out
    try:
        games = statsapi.schedule(date=date_str, sportId=1)
    except Exception:
        return out
    for g in games:
        try:
            box = statsapi.boxscore_data(g["game_id"])
        except Exception:
            continue
        for side in ("home", "away"):
            for pdd in box.get(side, {}).get("players", {}).values():
                bo = str(pdd.get("battingOrder") or "")
                # Only starters. 101/102 are substitutes who inherit a slot mid-game and
                # never see that slot's full plate-appearance count.
                if not bo.isdigit() or int(bo) % 100 != 0:
                    continue
                name = (pdd.get("person") or {}).get("fullName", "")
                if name:
                    out[_norm_mlb_name(name)] = int(bo) // 100
    return out


def _norm_mlb_name(s: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def _mlb_mean_opportunity(df, is_pitcher: bool):
    """
    Mean batters faced (pitcher) or plate appearances (batter) over recent games, or None.

    This is the workload the Statcast rates get applied over, replacing the league
    constants. Zero-opportunity games are excluded — those are voids, and averaging them
    in would drag every projection toward a scenario the bet never pays out on.
    """
    if df is None or df.empty:
        return None
    if is_pitcher:
        if not {"IP", "H", "BB"}.issubset(df.columns):
            return None
        sub = df.tail(_MLB_BF_LOOKBACK)
        vals = []
        for ip, h, bb in zip(sub["IP"], sub["H"], sub["BB"]):
            try:
                bf = round(float(ip or 0) * 3) + int(h or 0) + int(bb or 0)
            except (TypeError, ValueError):
                continue
            if bf > 0:
                vals.append(bf)
        need = _MLB_BF_MIN_GAMES
    else:
        if not {"AB", "BB"}.issubset(df.columns):
            return None
        sub = df.tail(_MLB_PA_LOOKBACK)
        vals = [int(a or 0) + int(b or 0) for a, b in zip(sub["AB"], sub["BB"])]
        vals = [v for v in vals if v > 0]
        need = _MLB_PA_MIN_GAMES
    if len(vals) < need:
        return None
    return sum(vals) / len(vals)


def _mlb_bf_over_prob(df, stat_type: str, line: float):
    """
    P(stat > line) marginalised over the pitcher's own batters-faced distribution.
    Returns None when the market or the sample cannot support it.
    """
    col = _MLB_BF_RATE_COL.get(stat_type)
    if col is None or df is None or df.empty:
        return None
    if not {"IP", "H", "BB", col}.issubset(df.columns):
        return None
    sub = df.tail(_MLB_BF_LOOKBACK)
    bf_series, stat_total, bf_total = [], 0.0, 0.0
    for ip, h, bb, sv in zip(sub["IP"], sub["H"], sub["BB"], sub[col]):
        try:
            outs = round(float(ip or 0) * 3)
        except (TypeError, ValueError):
            continue
        bf = outs + int(h or 0) + int(bb or 0)
        if bf <= 0:
            continue        # did not pitch — a void, not a data point
        bf_series.append(bf)
        bf_total += bf
        stat_total += float(sv or 0)
    if len(bf_series) < _MLB_BF_MIN_GAMES or bf_total <= 0:
        return None
    rate = stat_total / bf_total
    if rate <= 0:
        return None
    weights: dict = {}
    for b in bf_series:
        weights[b] = weights.get(b, 0.0) + 1.0 / len(bf_series)
    return round(sum(w * _binom_sf(line, b, rate) for b, w in weights.items()), 4)


# ── Minutes model for basketball props ──────────────────────────────────────
#
# Third sport, same structural gap: a rate applied over an unmodelled opportunity. The
# hoops scorer is "70% historical (60/40 last-10/30 + trend) + 30% implied" — a frequency
# of games above the line, with nothing about how long the player was on the floor.
#
# It is the worst case of the three. Measured over 3,976 resolved WNBA legs matched to the
# minutes actually played:
#
#   30+ min    55.7% of legs   over hits 53.5%
#   24-30      27.2%                     41.5%
#   18-24      10.2%                     32.4%
#   under 18    6.9%                      9.5%
#
# 44% of legs resolve on a short night. A full-minutes board hits 53.5%; the real one hits
# 45.0%, an 8.5-point gap that matches the 8.8 points plate appearances cost in baseball.
# And basketball minutes swing far wider than plate appearances — 25 to 43 in five games
# for one starter — with load management, back-to-backs and blowouts on top.
#
# Both halves come from the player's own log, so the minutes distribution already carries
# his role, his rest pattern and his blowout exposure.

_HOOPS_MIN_LOOKBACK = 15
_HOOPS_MIN_GAMES = 6
# Discrete low counts where the market posts lines on the half point and the normal fit is
# poor near zero — the same reasoning as NFL touchdowns. Points, and every combo built on
# points, accumulate over many possessions and stay normal-ish.
_HOOPS_COUNT_STATS = {"3-PT Made", "Steals", "Blocks", "Blocked Shots", "Turnovers"}


def _hoops_min_over_prob(df, stat_type: str, col: str, line: float):
    """
    P(stat > line) marginalised over the player's own minutes distribution.
    Returns None when the sample cannot support it.
    """
    import statistics as _st
    if df is None or df.empty or "MIN" not in df.columns or col not in df.columns:
        return None
    sub = df.tail(_HOOPS_MIN_LOOKBACK)
    pairs = []
    for m, v in zip(sub["MIN"], sub[col]):
        try:
            mm = float(m or 0)
        except (TypeError, ValueError):
            continue
        # A zero-minute game is a DNP, which books void rather than settle, so it is not a
        # data point about how the player performs.
        if mm > 0:
            pairs.append((mm, float(v or 0)))
    if len(pairs) < _HOOPS_MIN_GAMES:
        return None
    tot_min = sum(m for m, _ in pairs)
    tot_val = sum(v for _, v in pairs)
    if tot_min <= 0 or tot_val <= 0:
        return None
    per_min = tot_val / tot_min
    mean_min = tot_min / len(pairs)
    vals = [v for _, v in pairs]
    own_sd = _st.pstdev(vals) if len(vals) > 1 else max(tot_val / len(pairs) * 0.5, 1.0)
    own_mean = tot_val / len(pairs)

    total = 0.0
    for m, _ in pairs:
        mu = per_min * m
        if stat_type in _HOOPS_COUNT_STATS:
            p = _poisson_sf_hoops(line, mu)
        else:
            # Scale the player's own spread by how far this game's minutes sit from his
            # average, preserving the shape and moving only the location — the same
            # treatment the NFL scorer gives sigma.
            sd = (own_sd * (mu / own_mean)) if own_mean > 0 else own_sd
            sd = max(sd, 0.30 * max(mu, 0.5))
            p = 1.0 - _norm_cdf_hoops(line, mu, sd)
        total += p / len(pairs)
    return round(min(0.99, max(0.01, total)), 4)


def _poisson_sf_hoops(line: float, mu: float) -> float:
    import math
    if mu <= 0:
        return 0.0
    k = int(math.floor(line))
    if k < 0:
        return 1.0
    term = cdf = math.exp(-mu)
    for i in range(1, k + 1):
        term *= mu / i
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


def _norm_cdf_hoops(x: float, mu: float, sigma: float) -> float:
    import math
    if sigma <= 0:
        return 1.0 if x < mu else 0.0
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _mlb_hit_rate(player_name: str, stat_type: str, line: float,
                   odds_type: str = "standard", implied_override: float = -1.0,
                   cal_factor: float = 1.0, opp_pitcher_id: int | None = None,
                   team: str | None = None, slot: int | None = None):
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

    # Fold in the plate-appearance model for batters. It is the principled version of the
    # same signal — same games, but P(PA) and the per-PA rate kept apart instead of fused
    # into one threshold frequency — so it carries the larger share. The frequency term is
    # retained at 0.35 as a hedge: the PA model adds its own assumptions (binomial hits,
    # a convolved per-PA distribution for total bases) that have not been measured against
    # this log yet, and the calibration loop needs a few weeks to grade them.
    try:
        opp_p = (_mlb_bf_over_prob(df, stat_type, line) if is_pitcher
                 else _mlb_pa_over_prob(df, stat_type, line, slot=slot))
    except Exception:
        opp_p = None
    if opp_p is not None:
        hist = min(0.97, max(0.03, 0.65 * opp_p + 0.35 * hist))

    implied = implied_override if implied_override >= 0 else PP_ODDS_IMPLIED.get(odds_type, 0.50)
    # Statcast expected-stat model, when the prop has one (xBA→hits, xSLG→total
    # bases, K%→strikeouts, barrel→HR, …). It predicts these far better than the
    # game-log recency rate, so give it real weight when available.
    sc = None
    try:
        sc = statcast_over_prob(pid, stat_type, line, is_pitcher, opp_pitcher_id,
                                opportunity=_mlb_mean_opportunity(df, is_pitcher))
    except Exception:
        sc = None
    if sc is not None:
        rate = 0.40 * hist + 0.35 * sc + 0.25 * implied
    else:
        rate = 0.7 * hist + 0.3 * implied
    rate = rate * cal_factor
    return round(min(0.97, max(0.03, rate)), 3), n


# ── Parlay builder ───────────────────────────────────────────────────────────

def _apply_market_blend(legs: list, weight: float) -> list:
    """
    Shrink each leg's probability toward the de-vigged market price:
    hit_rate = weight*model + (1-weight)*implied.

    The raw model's disagreements with the market measured anti-predictive
    (audit 2026-07-19: pred>implied legs hit 45.5%, pred<=implied hit 63.3%),
    so the shipped probability leans market. The pre-blend number is kept on
    the leg as model_hit_rate — it is what future blend-weight fits grade —
    and re-blending is a no-op, so shared leg lists can pass through both the
    parlay builder and the SGP builder safely.
    """
    out = []
    for leg in legs:
        implied = leg.get("implied_prob")
        if implied is None:
            out.append(leg)
            continue
        leg = dict(leg)
        model_p = leg.get("model_hit_rate")
        if model_p is None:
            model_p = leg["hit_rate"]
            leg["model_hit_rate"] = model_p
        leg["hit_rate"] = round(
            min(0.97, max(0.03, weight * float(model_p) + (1 - weight) * float(implied))), 4)
        out.append(leg)
    return out


def _same_game_pairs(combo) -> int:
    """Number of leg pairs sharing a game — the unit the correlation penalty prices."""
    counts: dict = {}
    for leg in combo:
        gid = leg.get("game_id") or leg.get("game_label") or ""
        if gid:
            counts[gid] = counts.get(gid, 0) + 1
    return sum(c * (c - 1) // 2 for c in counts.values())


def _market_diverse_pool(legs: list, pool_size: int) -> list:
    """
    Cut the scored slate down to pool_size, giving each market an even quota first.

    A plain `sorted(legs, key=hit_rate)[:pool_size]` ranks every prop by probability,
    and since _apply_market_blend leans the probability toward the de-vigged price,
    that is really "keep the 30 shortest-priced props on the board". In MLB one market
    wins that sort outright — over 0.5 Hits is a genuine ~65-70% proposition and a full
    slate has well past 30 of them, so the pool came back 29/30 Hits (2026-08-12) and
    Home Runs, Total Bases, Runs Scored and Pitcher Strikeouts were never poolable.
    They weren't rejected on merit; they just never cleared a cutoff meant only to stop
    C(800,4) from hanging the app. WNBA escaped this by accident — no WNBA market has
    30 props above the pool floor, so its top 30 had to mix.

    Each market gets pool_size // n_markets slots, taken best-first. Markets with fewer
    legs than their quota leave slack, and any unfilled slots go to the global hit_rate
    ranking — so a thin slate still fills the pool and the strongest legs still get in.
    max_leg_uses caps how often one *leg* is reused across output parlays; this caps how
    much of the pool one *market* can own.
    """
    if len(legs) <= pool_size:
        return sorted(legs, key=lambda x: x["hit_rate"], reverse=True)

    by_market: dict = defaultdict(list)
    for leg in legs:
        by_market[leg.get("stat_type", "")].append(leg)
    for group in by_market.values():
        group.sort(key=lambda x: x["hit_rate"], reverse=True)

    quota = max(1, pool_size // len(by_market))
    pool, taken = [], set()
    for market in sorted(by_market):            # sorted() so the cut is deterministic
        for leg in by_market[market][:quota]:
            pool.append(leg)
            taken.add(id(leg))

    for leg in sorted(legs, key=lambda x: x["hit_rate"], reverse=True):
        if len(pool) >= pool_size:
            break
        if id(leg) not in taken:
            pool.append(leg)
            taken.add(id(leg))

    pool.sort(key=lambda x: x["hit_rate"], reverse=True)
    return pool[:pool_size]


def _within_caps(combo, max_same_market, stat_family, max_same_family) -> bool:
    """
    True if a combo respects the per-market and per-family diversity caps.

    Two levels because they catch different things. The market cap stops five legs of the
    same stat_type. The FAMILY cap stops five legs of the same kind of outcome: with only
    the market cap, the top NFL 5-leg came back Passing TDs x2 / Rushing TDs x2 /
    Receiving TDs x1 — three markets by name, but one event repeated, since every leg
    needs someone to reach the end zone. Touchdown props are also the most heavily
    regressed thing on the board, so a parlay made of them stacks the model's least
    confident numbers and calls the result diversification.
    """
    for key, cap in ((lambda l: l.get("stat_type", ""), max_same_market),
                     (lambda l: (stat_family or {}).get(l.get("stat_type", ""), ""),
                      max_same_family if stat_family else None)):
        if cap is None:
            continue
        counts: dict = {}
        for l in combo:
            k = key(l)
            counts[k] = counts.get(k, 0) + 1
            if counts[k] > cap:
                return False
    return True


def _build_parlays(legs: list, min_legs: int = 2, max_legs: int = 5, top_n: int = 50,
                    pool_size: int = 30, max_leg_uses: int = 6,
                    max_player_uses: int | None = None,
                    sportsbook: str = "PrizePicks", parlay_cal: dict | None = None,
                    min_ev: float = 0.0, market_blend: float | None = None,
                    same_game_penalty: float = 1.0,
                    max_same_market: int | None = None,
                    stat_family: dict | None = None,
                    max_same_family: int | None = None):
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

    pool_size caps the input legs before generating combinations — Underdog/fallback
    data can return hundreds of legs, and C(800,4) = 17B combinations will hang the
    app. The cut is per-market quota'd (see _market_diverse_pool), so one market
    cannot own the whole pool and lock every other market out of the board.

    max_leg_uses caps how many output parlays any single player+stat leg can
    appear in, so top_n isn't just recombinations of the same handful of
    highest-confidence legs (e.g. all "Hits" props flooding the safe list).

    max_player_uses caps the same thing per PLAYER, across every market they appear
    in. max_leg_uses alone does not bound player exposure: once the pool became
    market-diverse, one player could sit in it under several markets and collect
    max_leg_uses parlays in each. On the 2026-08-12 MLB slate that put a single
    player in 36 of 100 parlays where the pre-quota board had him in 12 — those
    parlays all lose together on his bad night, which is exactly the concentration
    the per-leg cap was meant to prevent.

    It defaults to max_leg_uses: there is no reason a player should get a larger budget
    than any one of their legs, and that value lands top-player exposure back on the 12
    the pre-quota board had. It costs board size — a market-diverse pool holds ~22
    distinct players where the old single-market pool held 30, so the same top_n cannot
    be filled without concentrating. 100 parlays/book/day becomes ~61-75. That is a
    cheap trade now: calibration dedupes repeated legs on _prop_key anyway, and the
    single-leg tracking records (log_tracking_legs) are what actually feed it.

    Both budgets are per list (safe and value each get their own), so a player's real
    exposure across the board is up to twice max_player_uses. They are not shared on
    purpose: safe is built first and would spend the whole budget, leaving value empty.

    max_same_market caps how many legs of one stat_type may appear in a single parlay.
    _market_diverse_pool quotas the INPUT pool, which is a different guarantee and not
    the one that matters here: a pool spanning nine markets still yields a top parlay of
    five Passing TDs, because the combos are ranked by probability and the highest
    probabilities cluster in whichever market sits closest to its line. On the first NFL
    slate every 5-leg came back single-market at 0.20 distinct-markets-per-leg.

    Those are not five bets, they are one bet with five names on it — the identical shape
    as the MLB home-run stacks that went 0-for-48 in 2026-W34 at a median 51.5x. The cap
    is what makes "wide variance of outcomes" structural rather than incidental.

    stat_family + max_same_family apply the same idea one level coarser — see
    _within_caps for why the market cap alone is not enough.

    Both default to None (no constraint) so MLB/WNBA boards are unchanged; NFL passes 2
    and 2, which forces a 5-leg to span at least three markets across all three of
    volume, yardage and touchdowns.

    market_blend (parlay_tracker.get_market_blend) shrinks leg probabilities
    toward the de-vigged market before combining.

    same_game_penalty would multiply the product once per same-game leg pair,
    but it is DORMANT — it defaults to 1.0 and no caller passes a live value
    (the correlation estimator proved unreliable; see
    parlay_tracker.get_same_game_penalty). The parameter and _same_game_pairs
    plumbing are retained so a future per-prop-type correlation model can slot in.
    """
    if market_blend is not None:
        legs = _apply_market_blend(legs, market_blend)
    legs = _market_diverse_pool(legs, pool_size)
    parlay_cal = parlay_cal or {}
    if max_player_uses is None:
        max_player_uses = max_leg_uses

    results = []
    for n in range(min_legs, max_legs + 1):
        if n > len(legs):
            continue
        factor = float(parlay_cal.get(n, parlay_cal.get(str(n), 1.0)))
        for combo in combinations(legs, n):
            if len({l["player_name"] for l in combo}) < n:
                continue
            if not _within_caps(combo, max_same_market, stat_family, max_same_family):
                continue
            raw = 1.0
            for leg in combo:
                raw *= leg["hit_rate"]
            sg_pairs = _same_game_pairs(combo) if same_game_penalty < 1.0 else 0
            corr = same_game_penalty ** sg_pairs if sg_pairs else 1.0
            # The floor here used to be 0.001, which SILENTLY INFLATED long shots. DFS
            # ladders cap the payout at 20x so a floored parlay still prices at -0.98 and
            # the inflation never showed. FanDuel pays the product of decimal odds, which
            # has no cap: a 5-leg of +500 home runs is a 7776x payout, and floating a
            # 7.6e-05 probability up to 0.001 booked it at EV +6.78 against a true -0.41.
            # On the real 2026-08-12 FanDuel slate this marked two all-Home-Runs 5-legs
            # "recommended" at EV +0.61 whose honest EV is -0.62. Any floor that RAISES a
            # probability manufactures EV, so the floor is now numerical-only — it exists
            # to keep a zeroed leg from producing a degenerate 0, not to prop up a price.
            prob = min(0.99, max(1e-12, raw * corr * factor))
            payout = parlay_payout(sportsbook, combo)
            # Payouts are gross (a 2-pick returns 3x the entry), so a win nets
            # payout-1 and EV = prob*(payout-1) - (1-prob) = prob*payout - 1.
            ev = round(prob * payout - 1.0, 4)
            results.append({
                # 6dp, not 4: a long shot is now allowed to be genuinely small, and
                # rounding it to 0.0 would hide the number the EV was computed from.
                "legs": list(combo), "n": n,
                "prob": round(prob, 6), "raw_prob": round(raw, 6),
                "payout": payout, "ev": ev,
                "recommended": bool(ev > min_ev),
            })

    def _lk(p):
        # line_score is part of the identity: without it two alt lines on the same
        # player+stat ("2+ Total Bases" and "3+ Total Bases") collapse to one key, so
        # the use cap counts them as one leg and the dedupe treats two different
        # parlays as identical. Nothing feeds alt lines in today (score_legs dedupes on
        # player+stat and milestone_best keeps one line per player+stat), which is
        # exactly why this would have failed silently the day something did.
        return [f"{l['player_name']}|{l['stat_type']}|{l.get('line_score')}"
                for l in p["legs"]]

    def _top(pool, key_fn, exclude_keys=None, max_uses=None, max_player=None):
        # Guarantee every requested pick count is represented instead of letting the
        # global sort fill the board with small parlays. Larger parlays are selected
        # FIRST so they get fresh legs — otherwise 2- and 3-leg combos exhaust the
        # per-leg use budget and 5-leg parlays never make the cut (a 5-leg was
        # effectively ungeneratable). Each count gets an even share of the slots, then
        # any remaining slots are filled by the global ranking.
        #
        # Budgets are per call, so a leg may be used up to max_uses times in safe AND
        # again in value. That is deliberate — the two lists are alternative views of
        # the same slate, and sharing one budget would let safe spend it all and leave
        # value empty — but it does mean real exposure is twice the per-list cap.
        counts = sorted({p["n"] for p in pool}, reverse=True)
        per = max(1, top_n // max(1, len(counts)))
        seen: set = set()
        excl = exclude_keys or set()
        leg_uses = defaultdict(int)
        player_uses = defaultdict(int)
        out = []

        def _try_add(p):
            leg_keys = _lk(p)
            k = frozenset(leg_keys)
            if k in seen or k in excl:
                return False
            if max_uses is not None and any(leg_uses[lk] >= max_uses for lk in leg_keys):
                return False
            players = {l["player_name"] for l in p["legs"]}
            if max_player is not None and any(player_uses[pl] >= max_player for pl in players):
                return False
            seen.add(k)
            out.append(p)
            for lk in leg_keys:
                leg_uses[lk] += 1
            for pl in players:
                player_uses[pl] += 1
            return True

        for n in counts:                       # large parlays first, even share each
            added = 0
            for p in sorted((x for x in pool if x["n"] == n), key=key_fn, reverse=True):
                if added >= per or len(out) >= top_n:
                    break
                if _try_add(p):
                    added += 1
        for p in sorted(pool, key=key_fn, reverse=True):   # fill remainder globally
            if len(out) >= top_n:
                break
            _try_add(p)

        out.sort(key=key_fn, reverse=True)     # display best-first
        return out

    # Recommended (positive-EV) combos sort ahead of the rest in both lists, so the bets
    # worth making lead. The losers are still returned and logged — they are the training
    # data — they just never sit at the top of the board.
    safe_out = _top(results, lambda x: (x["recommended"], x["prob"]),
                    max_uses=max_leg_uses, max_player=max_player_uses)
    safe_keys = {frozenset(_lk(p)) for p in safe_out}
    value_out = _top(results, lambda x: (x["recommended"], x["ev"]),
                     exclude_keys=safe_keys, max_uses=max_leg_uses,
                     max_player=max_player_uses)

    return safe_out, value_out


def _build_sgp(legs: list, min_legs: int = 2, max_legs: int = 5,
               market_blend: float | None = None,
               same_game_penalty: float = 1.0) -> list:
    """Group legs by game, return best parlay(s) per game sorted by probability.

    Every combo here is all-same-game, so the correlation penalty applies to
    each pair — SGP boards price honestly instead of riding independence."""
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
        safe, _ = _build_parlays(game_legs, min_legs=min_legs, max_legs=cap, top_n=3,
                                 market_blend=market_blend,
                                 same_game_penalty=same_game_penalty)
        if safe:
            sgp_results.append({"game_label": glabel, "game_id": gid, "parlays": safe[:3]})
    sgp_results.sort(key=lambda x: x["parlays"][0]["prob"] if x["parlays"] else 0, reverse=True)
    return sgp_results
