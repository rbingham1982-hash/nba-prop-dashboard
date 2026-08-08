"""
parlay_tracker.py — Persistent parlay logging, outcome resolution, and model calibration.

Log lives at parlay_log.json next to this file.
Outcomes are resolved automatically via the NBA API (playergamelog) and MLB Stats API.
Calibration factors are derived from resolved legs once CAL_MIN_SAMPLES is reached per stat.
"""

import json
import hashlib
import time as _time
import unicodedata
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import pandas as pd

LOG_PATH = Path(__file__).parent / "parlay_log.json"

# Which model produced a parlay's predicted_prob. Bump this whenever a change alters
# what predicted_prob *means*, so calibration stops grading a model that no longer
# exists.
#
# Parlay calibration is actual/predicted. Every parlay resolved before "devig" was
# predicted by a model that pinned the book's implied probability at a constant 0.50 —
# it never saw the market at all. Feeding it de-vigged prices made its leg
# probabilities markedly more honest, so applying the *old* model's overconfidence on
# top of the new one deflates twice, and the whole board reports as unprofitable.
#
# Those factors were also non-monotonic in pick count (WNBA 4-leg 0.179 but 5-leg
# 0.471). Overconfidence that compounds per leg cannot rise again at five; the factor
# was really encoding which builder produced the parlay — the daily generator capped at
# 4 and EV-ranked its worst legs, while 5-leg parlays came from the dashboard's better
# pool. A number that measures its own source is not a calibration.
#
# "blend": leg probabilities are shrunk toward the de-vigged market price with a
# weight fit from this log (see get_market_blend), and same-game combos carry a
# measured correlation penalty. Audit of 243 resolved devig legs showed the raw
# model was overconfident above 70% and its disagreements with the market were
# anti-predictive (pred>implied legs hit 45.5%, pred<=implied hit 63.3%), while
# same-game pairs hit at 0.72x what independence implied even after controlling
# for leg overconfidence. Both change what predicted numbers mean → new epoch.
_MODEL_EPOCH = "blend"

CAL_MIN_SAMPLES = 15          # minimum weighted resolved legs per stat before calibration kicks in
CAL_MAX_FACTOR = 1.35         # clamp calibration multiplier upper bound
CAL_MIN_FACTOR = 0.05         # allow deep deflation for stats like RBI/HR that rarely hit
CAL_HALF_LIFE_WEEKS = 3       # recency weighting: a week's legs count half as much every 3 weeks of age
CAL_RECENCY_WEEKS = 2         # a stat with no legs generated within this many weeks of the latest
                              # logged week is treated as RETIRED — the book stopped posting the line
                              # or the model stopped scoring it — so it emits neither a calibration
                              # factor nor a drift warning. Without this gate a dead market (e.g. Runs
                              # Scored, last seen weeks ago) keeps producing a factor and a scary drift
                              # warning from stale pre-epoch legs that nothing is betting anymore.

# ── NBA stat resolution: how to compute a value from a PlayerGameLog row ─────
# Format: "stat_type": ("single", col) or ("sum", [cols])
_NBA_RESOLVE = {
    "Points":         ("single", "PTS"),
    "Rebounds":       ("single", "REB"),
    "Assists":        ("single", "AST"),
    "Steals":         ("single", "STL"),
    "Blocked Shots":  ("single", "BLK"),
    "3-PT Made":      ("single", "FG3M"),
    "Turnovers":      ("single", "TOV"),
    "Pts+Rebs+Asts":  ("sum",    ["PTS", "REB", "AST"]),
    "Pts+Rebs":       ("sum",    ["PTS", "REB"]),
    "Pts+Asts":       ("sum",    ["PTS", "AST"]),
    "Rebs+Asts":      ("sum",    ["REB", "AST"]),
}

# ── MLB stat resolution: statsapi stat key names ──────────────────────────────
_MLB_BATTING_RESOLVE = {
    "Hits":              "hits",
    "Home Runs":         "homeRuns",
    "RBIs":              "rbi",
    "Runs":              "runs",
    # daily_parlay_gen emits "Runs Scored" (UD_MLB_STAT_MAP), and the dashboard emits
    # "Runs". Only "Runs" was mapped, so every "Runs Scored" leg fell through the
    # stat lookup, never resolved, and was retried against the API on every run.
    "Runs Scored":       "runs",
    "Stolen Bases":      "stolenBases",
    "Total Bases":       "totalBases",
    "Hitter Strikeouts": "strikeOuts",
    "Walks":             "baseOnBalls",
    "Doubles":           "doubles",
}
_MLB_PITCHING_RESOLVE = {
    "Pitcher Strikeouts": "strikeOuts",
    "Pitching Outs":      "outs",
    "Hits Allowed":       "hits",
    "Earned Runs Allowed": "earnedRuns",
    "Walks Allowed":      "baseOnBalls",
}
_MLB_PITCHER_TYPES = set(_MLB_PITCHING_RESOLVE)

# Nickname / city keyword → 3-letter abbrev, for matching a schedule game (which the
# MLB API returns as full names like "Los Angeles Dodgers") back to a leg's game_label
# (which is abbreviated, e.g. "LAD @ NYY"). "red sox"/"white sox" are keyed on the full
# two-word nickname so "sox" is never ambiguous.
_MLB_NAME_TO_ABBR = {
    "diamondbacks": "ARI", "braves": "ATL", "orioles": "BAL", "red sox": "BOS",
    "cubs": "CHC", "white sox": "CWS", "reds": "CIN", "guardians": "CLE",
    "rockies": "COL", "tigers": "DET", "astros": "HOU", "royals": "KC",
    "angels": "LAA", "dodgers": "LAD", "marlins": "MIA", "brewers": "MIL",
    "twins": "MIN", "mets": "NYM", "yankees": "NYY", "athletics": "OAK",
    "phillies": "PHI", "pirates": "PIT", "padres": "SD", "giants": "SF",
    "mariners": "SEA", "cardinals": "STL", "rays": "TB", "rangers": "TEX",
    "blue jays": "TOR", "nationals": "WSH",
}
# Abbreviation variants that different books use for the same club, so a game_label
# written "CHW @ TEX" still matches a schedule abbrev of CWS.
_MLB_ABBR_ALIASES = {
    "ARI": {"ARI", "AZ"}, "CWS": {"CWS", "CHW"}, "OAK": {"OAK", "ATH"},
    "WSH": {"WSH", "WSN", "WAS"},
}

def _mlb_abbrev_from_name(full_name: str) -> str | None:
    """Map an MLB full team name to its 3-letter abbrev, or None if unrecognized."""
    low = (full_name or "").lower()
    for keyword, abbr in _MLB_NAME_TO_ABBR.items():
        if keyword in low:
            return abbr
    return None

def _mlb_strip_name(s: str) -> str:
    """Normalized name with punctuation removed but spaces kept: 'C.J. Abrams' -> 'cj abrams'."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", _normalize_name(s)).strip()

def _mlb_match_player(box: dict, norm_name: str):
    """
    Find a player's boxscore entry, tolerant of name-form differences the raw normalized
    name misses: punctuation ('C.J.' vs 'CJ') and first-name variants ('Josh' vs 'Joshua',
    'Zach' vs 'Zac'). The first-name fallback fires only when exactly one player in the game
    shares the last name + first initial, so it can never resolve against the wrong player.
    Returns the player_data dict, or None.
    """
    target = _mlb_strip_name(norm_name)
    if not target:
        return None
    tparts = target.split()
    tlast, tinit = tparts[-1], tparts[0][:1]
    players = [pdd for side in ("home", "away")
               for pdd in box.get(side, {}).get("players", {}).values()]
    # 1) punctuation-insensitive substring match (parity with the old norm-substring test)
    for pdd in players:
        if target in _mlb_strip_name(pdd.get("person", {}).get("fullName", "")):
            return pdd
    # 2) unambiguous last-name + first-initial fallback
    cands = [pdd for pdd in players
             if (fp := _mlb_strip_name(pdd.get("person", {}).get("fullName", "")).split())
             and fp[-1] == tlast and fp[0][:1] == tinit]
    return cands[0] if len(cands) == 1 else None

def _mlb_game_matches_label(away_name: str, home_name: str, game_label: str) -> bool:
    """
    True if a schedule game (full team names) is the same matchup as a leg's abbreviated
    game_label. Requires *both* clubs present so a same-day postponement of a different
    game never voids the wrong leg.
    """
    gl = (game_label or "").upper()
    matched = 0
    for name in (away_name, home_name):
        abbr = _mlb_abbrev_from_name(name)
        if abbr and any(v in gl for v in _MLB_ABBR_ALIASES.get(abbr, {abbr})):
            matched += 1
    return matched == 2


def _mlb_derived_batting(stat_type: str, b: dict) -> float | None:
    """
    Compute batting props that the per-game boxscore has no single key for.

    boxscore_data carries raw counting stats only, so composites have to be derived.
    A composite the resolver cannot name never resolves at all: the leg is skipped,
    stays pending forever, and is retried against the API on every run. Returns None
    when stat_type is not a derived stat.
    """
    hits    = float(b.get("hits", 0) or 0)
    doubles = float(b.get("doubles", 0) or 0)
    triples = float(b.get("triples", 0) or 0)
    hr      = float(b.get("homeRuns", 0) or 0)
    if stat_type == "Total Bases":
        return hits + doubles + 2 * triples + 3 * hr
    if stat_type == "Singles":
        return hits - doubles - triples - hr
    if stat_type in ("Hits+Runs+RBIs", "Hits + Runs + RBIs"):
        return hits + float(b.get("runs", 0) or 0) + float(b.get("rbi", 0) or 0)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────────────────────

_CACHE: dict | None = None
_CACHE_MTIME: float = 0.0


def _load() -> dict:
    global _CACHE, _CACHE_MTIME
    try:
        mtime = LOG_PATH.stat().st_mtime
    except Exception:
        mtime = 0.0
    if _CACHE is not None and mtime == _CACHE_MTIME:
        return _CACHE
    if LOG_PATH.exists():
        try:
            _CACHE = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            _CACHE_MTIME = mtime
            return _CACHE
        except Exception:
            pass
    _CACHE = {"version": 1, "parlays": []}
    _CACHE_MTIME = 0.0
    return _CACHE


def _save(data: dict) -> None:
    global _CACHE, _CACHE_MTIME
    LOG_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    _CACHE = data
    try:
        _CACHE_MTIME = LOG_PATH.stat().st_mtime
    except Exception:
        _CACHE_MTIME = 0.0


def _parlay_id(parlay: dict, sport: str, sportsbook: str) -> str:
    """Stable hash: same legs + sport + sportsbook = same ID (prevents duplicate logging)."""
    leg_keys = sorted(
        f"{l['player_name']}|{l['stat_type']}|{l['line_score']}"
        for l in parlay["legs"]
    )
    raw = f"{sport}|{sportsbook}|{';;'.join(leg_keys)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# Public: log parlays
# ─────────────────────────────────────────────────────────────────────────────

EARLY_LEAD_HOURS = 8   # experiment threshold. A pick placed this many+ hours before first
                       # pitch is tagged "early". Lead time is the single biggest CLV lever
                       # found so far — early picks are ~break-even CLV and beat their implied
                       # hit rate, late picks bleed — so every pick is TAGGED, not filtered, to
                       # A/B the two cohorts prospectively (see get_lead_time_experiment).


def _lead_hours(start_time: str, ref_utc: datetime) -> float | None:
    """Hours from ref (UTC, taken at log time) to the game's start. None if unparseable."""
    if not start_time:
        return None
    try:
        s = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        return round((s - ref_utc).total_seconds() / 3600, 1)
    except Exception:
        return None


def log_parlays(
    parlays: list,
    sport: str,
    sportsbook: str,
    kind: str = "safe",
) -> int:
    """Append newly generated parlays to the log. Returns count of new entries added."""
    if not parlays:
        return 0
    data = _load()
    existing_ids = {p["id"] for p in data["parlays"]}
    now = datetime.now()
    now_utc = datetime.now(timezone.utc)   # for tz-correct lead-time stamping
    week = now.strftime("%G-W%V")
    added = 0

    for parlay in parlays:
        pid = _parlay_id(parlay, sport, sportsbook)
        if pid in existing_ids:
            continue
        entry = {
            "id":               pid,
            "sport":            sport,
            "sportsbook":       sportsbook,
            "kind":             kind,
            "generated_at":     now.isoformat(timespec="seconds"),
            "iso_week":         week,
            "predicted_prob":   round(parlay["prob"], 4),
            "payout":           parlay.get("payout"),
            # Which model produced predicted_prob. Calibration grades a model against
            # its own predictions; without this it grades whatever came before too.
            "model_epoch":      _MODEL_EPOCH,
            # EV on the calibrated probability, and whether it clears zero. Losers are
            # logged too — they are the training data — but only the recommended ones
            # are worth betting.
            "ev":               parlay.get("ev"),
            "recommended":      bool(parlay.get("recommended", True)),
            "parlay_hit":       None,
            "legs":             [],
        }
        _leads = []
        for leg in parlay["legs"]:
            _lh = _lead_hours(str(leg.get("start_time", "")), now_utc)
            _leads.append(_lh)
            entry["legs"].append({
                "player_name":       leg["player_name"],
                "stat_type":         leg["stat_type"],
                "line_score":        float(leg["line_score"]),
                "predicted_hit_rate": round(float(leg["hit_rate"]), 4),
                # Distinct scoring model, when a builder uses one that isn't the
                # standard game-log/Statcast scorer (e.g. "hr_power_picks"). Keeps
                # its calibration in its own bucket — see _cal_key.
                **({"source": leg["source"]} if leg.get("source") else {}),
                # Pre-blend model probability. get_market_blend refits the blend
                # weight against this, not the shipped (already-blended) number —
                # fitting against the blended value would shrink toward the market
                # twice.
                "model_hit_rate":    round(float(leg["model_hit_rate"]), 4)
                                     if leg.get("model_hit_rate") is not None else None,
                "american_odds":     leg.get("american_odds"),
                # The de-vigged book probability the model actually scored against.
                # Recomputing it later from american_odds gives a vig-inflated number.
                "implied_prob":      leg.get("implied_prob"),
                "game_id":           str(leg.get("game_id", "")),
                "game_label":        str(leg.get("game_label", "")),
                "start_time":        str(leg.get("start_time", "")),
                # Hours from log time to first pitch. Stamped, not filtered — the lead-time
                # experiment flag (see EARLY_LEAD_HOURS / get_lead_time_experiment).
                "lead_hours":        _lh,
                "outcome":           None,
            })
        # A parlay is only as "early" as its soonest game — that leg's line is already the
        # most shaped — so bucket the whole parlay on the minimum leg lead.
        _known = [x for x in _leads if x is not None]
        _min_lead = min(_known) if _known else None
        entry["min_lead_hours"] = _min_lead
        entry["experiment"] = ("early" if _min_lead is not None and _min_lead >= EARLY_LEAD_HOURS
                               else "late" if _min_lead is not None else "unknown")
        data["parlays"].append(entry)
        existing_ids.add(pid)
        added += 1

    if added:
        _save(data)
    return added


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: date parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_game_date(leg: dict, parlay_generated_at: str) -> date | None:
    """Return the expected game date for a leg."""
    start = leg.get("start_time", "")
    if start:
        try:
            # ISO format, possibly UTC
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            return dt.astimezone().date()
        except Exception:
            pass
    # Fall back to parlay generation date
    try:
        return datetime.fromisoformat(parlay_generated_at).date()
    except Exception:
        return None


RESOLVE_MAX_ATTEMPTS  = 5   # tries before a long-finished leg is presumed unresolvable
RESOLVE_GIVE_UP_DAYS  = 3   # ...and only once its game is this far in the past
# statsapi (MLB) and commonallplayers (WNBA) issue requests with no timeout, so one
# stalled connection freezes the whole resolve run indefinitely. A process-wide
# socket timeout during resolve turns that hang into a caught exception the per-call
# try/except already handles — the leg is skipped and retried next run.
RESOLVE_HTTP_TIMEOUT  = 15  # seconds; ceiling on any single resolve network read
# Wall-clock budget for a whole resolve pass. The daily job resolves BEFORE it generates
# today's slate, and the scheduled task has a hard execution-time limit — so a slow resolve
# (hundreds of pending legs, or the machine sleeping mid-run and the wall clock still
# counting) used to eat the entire window and the slate never got generated. Bounding
# resolve means the important work — generation — always runs; unresolved legs just retry
# next pass (attempt counters persist), so nothing is lost, only deferred.
RESOLVE_BUDGET_SECONDS = 600

_resolve_deadline: float | None = None   # monotonic clock deadline for the current pass


def _resolve_over_budget() -> bool:
    return _resolve_deadline is not None and _time.monotonic() >= _resolve_deadline


def _leg_is_abandoned(leg: dict, now: datetime | None = None) -> bool:
    """
    True once a leg has been tried enough times, long enough after its game, that it is
    never going to resolve — a player the boxscore doesn't name, a game the schedule
    doesn't list.

    Without this, a leg that cannot resolve is retried against the API on every run,
    forever. Resolution now runs before every daily generation, so that cost sits on
    the critical path and grows with every dead leg the log accumulates.

    Deliberately *not* an age cutoff alone: reset_outcomes() clears the counter, so a
    backfill after a resolver fix still re-tries everything. A bare age check would have
    silently refused to re-resolve the WNBA legs the wrong-game fix depended on.
    """
    if int(leg.get("resolve_attempts", 0)) < RESOLVE_MAX_ATTEMPTS:
        return False
    start = leg.get("start_time", "")
    if not start:
        return True   # no game date to wait on, and out of attempts
    try:
        game_start = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
    except Exception:
        return True
    now = now or datetime.now(timezone.utc).astimezone()
    return now >= game_start + timedelta(days=RESOLVE_GIVE_UP_DAYS)


def _leg_is_resolvable(leg: dict, generated_at: str) -> bool:
    """True if enough time has passed that the game should be finished."""
    if _leg_is_abandoned(leg):
        return False
    start = leg.get("start_time", "")
    if start:
        try:
            game_start = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
            # Wait until 4 hours after scheduled start
            return datetime.now(timezone.utc).astimezone() >= game_start + timedelta(hours=4)
        except Exception:
            pass
    # No start_time: always attempt — NBA API returns empty if game not yet posted
    return True


def _is_historical_leg(leg: dict) -> bool:
    """True if this leg was generated from historical fallback data (no real game to resolve)."""
    return leg.get("game_label", "").strip().lower() == "historical"


def _normalize_name(name: str) -> str:
    """Strip Unicode accents so 'Vásquez' matches 'Vasquez'."""
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()


def _team_label_to_abbrev(token: str) -> str:
    """
    Convert a team label token to a 3-letter abbreviation for MATCHUP matching.
    Handles both already-abbreviated tokens ('SAS') and full team names.
    Uses nba_api teams list as lookup; falls back to first 3 chars.
    """
    token = token.strip()
    if len(token) <= 4:
        return token.upper()[:3]  # already an abbreviation
    # Build a keyword → abbrev map from nba_api teams (cached in module)
    try:
        from nba_api.stats.static import teams as _nba_teams  # type: ignore
        all_teams = _nba_teams.get_teams()
        token_up = token.upper()
        for t in all_teams:
            abbrev = t["abbreviation"].upper()
            if (t["full_name"].upper() in token_up or
                    t["nickname"].upper() in token_up or
                    t["city"].upper() in token_up):
                return abbrev
    except Exception:
        pass
    return token.upper()[:3]


def _parse_log_date(raw: str) -> date | None:
    """Parse a PlayerGameLog GAME_DATE ('MMM DD, YYYY', or ISO) into a date."""
    try:
        return datetime.strptime(raw.title(), "%b %d, %Y").date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            return None


def _find_game_by_label(df: pd.DataFrame, game_label: str, generated_at: str) -> pd.Series | None:
    """
    Match a game log row using team names extracted from game_label (e.g. 'SAS @ OKC'
    or 'San Antonio Spurs @ Oklahoma City Thunder').
    Returns the game closest to generated_at that has both teams in the MATCHUP.
    """
    if df.empty or "MATCHUP" not in df.columns or not game_label:
        return None
    parts = [p.strip() for p in game_label.replace("@", " @ ").split("@")]
    if len(parts) < 2:
        return None
    abbrev_a = _team_label_to_abbrev(parts[0])
    abbrev_b = _team_label_to_abbrev(parts[1])

    gen_date = None
    try:
        gen_date = datetime.fromisoformat(generated_at).date()
    except Exception:
        pass

    best_row = None
    best_delta = timedelta(days=9999)
    for _, row in df.iterrows():
        matchup = str(row.get("MATCHUP", "")).upper()
        if abbrev_a not in matchup or abbrev_b not in matchup:
            continue
        gd = _parse_log_date(str(row.get("GAME_DATE", "")))
        if gd is None:
            continue
        if gen_date is not None:
            delta = abs((gd - gen_date).days)
            if delta < best_delta.days:
                best_delta = timedelta(days=delta)
                best_row = row
        else:
            best_row = row
            break
    return best_row


def _mark_parlay_outcomes(data: dict) -> int:
    """
    Set parlay_hit for any parlay whose real (non-historical) legs are all resolved.
    Historical and voided legs are excluded from both resolution check and hit/miss verdict.
    Voided legs (postponed/suspended games) are treated as no-action — parlay resolves on remaining legs.
    Returns the number of parlays newly marked.
    """
    marked = 0
    for parlay in data["parlays"]:
        if parlay["parlay_hit"] is not None:
            continue
        real_legs = [l for l in parlay["legs"] if not _is_historical_leg(l)]
        if real_legs:
            # Normal path: resolve based on live legs only
            active_legs = [l for l in real_legs if l["outcome"] != "void"]
            if not active_legs:
                continue
            outcomes = [l["outcome"] for l in active_legs]
            if all(o is not None for o in outcomes):
                parlay["parlay_hit"] = bool(all(outcomes))
                marked += 1
        else:
            # All-historical parlay: resolve on legs that have outcome data; ignore None legs
            scored_legs = [l for l in parlay["legs"] if l["outcome"] is not None and l["outcome"] != "void"]
            if scored_legs:
                parlay["parlay_hit"] = bool(all(l["outcome"] is True for l in scored_legs))
                marked += 1
    return marked


# ─────────────────────────────────────────────────────────────────────────────
# NBA resolution
# ─────────────────────────────────────────────────────────────────────────────

# Known nicknames / display-name aliases → official full names used in the NBA API
_NBA_PLAYER_ALIASES: dict[str, str] = {
    "deuce mcbride": "miles mcbride",
    "og anunoby":    "o.g. anunoby",
    "ky bowman":     "kendrick bowman",
}


def _get_nba_player_id(name: str):
    """Resolve a display name (including known nicknames) to an NBA API player ID."""
    try:
        from nba_api.stats.static import players as _nba_players
        # Alias substitution
        lookup = _NBA_PLAYER_ALIASES.get(name.strip().lower(), name)
        results = _nba_players.find_players_by_full_name(lookup)
        if results:
            return results[0]["id"]
        # Normalized partial match: ignore accents, check all name parts present
        norm_lookup = _normalize_name(lookup)
        for p in _nba_players.get_active_players():
            if all(part in _normalize_name(p["full_name"]) for part in norm_lookup.split()):
                return p["id"]
    except Exception:
        pass
    return None


def _stat_from_row(row, spec: tuple):
    """Extract stat value from a PlayerGameLog row using a spec tuple."""
    kind, cols = spec
    if kind == "single":
        return float(row.get(cols, 0) or 0)
    return sum(float(row.get(c, 0) or 0) for c in cols)


def _find_game_in_log(df: pd.DataFrame, target_date: date,
                      game_label: str = "") -> pd.Series | None:
    """
    Find the game log row for target_date, within +/-1 day.

    Picks the *closest* game and prefers an exact date match. Returning the first
    row inside the tolerance window — the previous behaviour — silently resolved a
    leg against the following day's game whenever a player played on back-to-back
    days, because nba_api returns game logs newest-first. When game_label names
    both teams, only rows whose MATCHUP contains them are eligible, which settles
    adjacent-day games outright; if that finds nothing (unmappable abbreviation),
    fall back to date-only matching rather than dropping the leg.
    """
    if df.empty or "GAME_DATE" not in df.columns:
        return None

    abbrevs: list[str] = []
    if game_label:
        parts = [p.strip() for p in game_label.replace("@", " @ ").split("@")]
        if len(parts) >= 2:
            abbrevs = [_team_label_to_abbrev(parts[0]), _team_label_to_abbrev(parts[1])]

    def _scan(require_teams: bool) -> pd.Series | None:
        best_row, best_delta = None, None
        for _, row in df.iterrows():
            if require_teams:
                matchup = str(row.get("MATCHUP", "")).upper()
                if any(a not in matchup for a in abbrevs):
                    continue
            gd = _parse_log_date(str(row.get("GAME_DATE", "")))
            if gd is None:
                continue
            delta = abs((gd - target_date).days)
            if delta > 1:
                continue
            if best_delta is None or delta < best_delta:
                best_row, best_delta = row, delta
                if delta == 0:
                    break
        return best_row

    if abbrevs:
        row = _scan(True)
        if row is not None:
            return row
    return _scan(False)


def _fetch_player_gamelog(player_id: int) -> pd.DataFrame:
    """Fetch game log for current NBA season, trying Playoffs then Regular Season."""
    from nba_api.stats.endpoints import playergamelog
    for season_type in ("Playoffs", "Regular Season"):
        try:
            _time.sleep(0.6)
            gl = playergamelog.PlayerGameLog(
                player_id=player_id,
                season="2025-26",
                season_type_all_star=season_type,
            )
            df = gl.get_data_frames()[0]
            if not df.empty:
                return df
        except Exception:
            continue
    return pd.DataFrame()


def resolve_nba_legs() -> int:
    """Auto-resolve pending NBA legs against NBA API box scores. Returns resolved count."""
    data = _load()
    player_legs: dict[str, list] = defaultdict(list)
    attempted = 0
    for parlay in data["parlays"]:
        if parlay["sport"] != "NBA":
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is not None:
                continue
            if _is_historical_leg(leg):
                continue  # fallback legs have no real game — skip permanently
            if not _leg_is_resolvable(leg, parlay["generated_at"]):
                continue
            # Count the try up front. A leg the API never returns is only ever seen
            # here, so this is the one place the give-up counter can advance.
            leg["resolve_attempts"] = int(leg.get("resolve_attempts", 0)) + 1
            attempted += 1
            player_legs[leg["player_name"]].append((parlay, leg))

    if not player_legs:
        _mark_parlay_outcomes(data)
        _save(data)
        return 0

    resolved_count = 0
    for player_name, entries in player_legs.items():
        if _resolve_over_budget():
            break   # out of wall-clock budget — remaining legs retry next pass
        pid = _get_nba_player_id(player_name)
        if not pid:
            continue
        df = _fetch_player_gamelog(pid)
        if df.empty:
            continue
        for parlay, leg in entries:
            spec = _NBA_RESOLVE.get(leg["stat_type"])
            if spec is None:
                continue
            # When start_time is missing, use game_label team matching (wider tolerance)
            # rather than relying on the generation date which predates the game.
            if not leg.get("start_time", "") and leg.get("game_label", ""):
                row = _find_game_by_label(df, leg["game_label"], parlay["generated_at"])
            else:
                target = _parse_game_date(leg, parlay["generated_at"])
                if target is None:
                    continue
                row = _find_game_in_log(df, target, leg.get("game_label", ""))
            if row is None:
                continue
            try:
                actual = _stat_from_row(row, spec)
                leg["outcome"] = bool(actual > leg["line_score"])
                resolved_count += 1
            except Exception:
                continue

    marked = _mark_parlay_outcomes(data)
    if resolved_count or marked or attempted:
        _save(data)   # attempt counters must persist, or a dead leg is retried forever
    return resolved_count


# ─────────────────────────────────────────────────────────────────────────────
# MLB resolution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_mlb_legs() -> int:
    """Auto-resolve pending MLB legs via MLB Stats API. Returns resolved count."""
    try:
        import statsapi  # type: ignore
    except ImportError:
        return 0

    data = _load()
    player_legs: dict[str, list] = defaultdict(list)
    attempted = 0
    for parlay in data["parlays"]:
        if parlay["sport"] != "MLB":
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is not None:
                continue
            if _is_historical_leg(leg):
                continue
            if not _leg_is_resolvable(leg, parlay["generated_at"]):
                continue
            # Count the try up front. A leg the API never returns is only ever seen
            # here, so this is the one place the give-up counter can advance.
            leg["resolve_attempts"] = int(leg.get("resolve_attempts", 0)) + 1
            attempted += 1
            player_legs[leg["player_name"]].append((parlay, leg))

    if not player_legs:
        _mark_parlay_outcomes(data)
        _save(data)
        return 0

    # One schedule per date and one boxscore per game, cached for the whole run.
    # The old code re-fetched both inside the per-player loop, so a slate of N
    # players sharing M games issued N×M boxscore calls (each with a 0.4s sleep) —
    # thousands of requests that made even a healthy run take 20+ minutes.
    _sched_cache: dict = {}
    _box_cache: dict = {}

    def _get_schedule(date_str):
        if date_str not in _sched_cache:
            try:
                _time.sleep(0.4)
                _sched_cache[date_str] = statsapi.schedule(date=date_str, sportId=1)
            except Exception:
                _sched_cache[date_str] = []
        return _sched_cache[date_str]

    def _get_box(game_pk):
        if game_pk not in _box_cache:
            try:
                _time.sleep(0.4)
                _box_cache[game_pk] = statsapi.boxscore_data(game_pk)
            except Exception:
                _box_cache[game_pk] = None
        return _box_cache[game_pk]

    resolved_count = 0
    for player_name, entries in player_legs.items():
        if _resolve_over_budget():
            break   # out of wall-clock budget — remaining legs retry next pass
        norm_name = _normalize_name(player_name)
        dates_needed: set[str] = set()
        for parlay, leg in entries:
            d = _parse_game_date(leg, parlay["generated_at"])
            if d:
                # ±1 day: a leg with no start_time dates off its parlay's generated_at,
                # which can land a day before the real game (games also occasionally
                # shift). The per-leg guard below keeps each leg pinned to the right game.
                for _off in (-1, 0, 1):
                    dates_needed.add((d + timedelta(days=_off)).strftime("%m/%d/%Y"))

        for date_str in dates_needed:
            schedule = _get_schedule(date_str)

            for game_info in schedule:
                game_pk = game_info.get("game_id")
                if not game_pk:
                    continue
                game_status = (game_info.get("status") or "").lower()
                # Void legs for postponed/suspended/cancelled games — parlay resolves on remaining legs
                if "postponed" in game_status or "suspended" in game_status or "cancelled" in game_status:
                    away_name = game_info.get("away_name") or ""
                    home_name = game_info.get("home_name") or ""
                    for parlay, leg in entries:
                        if leg["outcome"] is not None:
                            continue
                        # Match the postponed game to the leg by abbreviation, not by
                        # full-name prefix — game_labels are abbreviated ("LAD @ NYY"),
                        # so comparing "los"/"new" against them never matched and
                        # postponed games silently never voided.
                        if _mlb_game_matches_label(away_name, home_name, leg.get("game_label", "")):
                            leg["outcome"] = "void"
                            resolved_count += 1
                    continue
                # Only resolve Final games — skip in-progress/scheduled
                if game_status and "final" not in game_status and "completed" not in game_status and "over" not in game_status:
                    continue
                box = _get_box(game_pk)
                if box is None:
                    continue

                try:
                    game_date = datetime.strptime(date_str, "%m/%d/%Y").date()
                except Exception:
                    game_date = None
                away_name = game_info.get("away_name") or ""
                home_name = game_info.get("home_name") or ""

                player_data = _mlb_match_player(box, norm_name)
                if player_data is None:
                    continue
                stats = player_data.get("stats", {})
                bstats = stats.get("batting", {})
                pstats = stats.get("pitching", {})
                for parlay, leg in entries:
                    if leg["outcome"] is not None:
                        continue
                    # Pin this leg to the right game. A leg WITH a start_time has an
                    # accurate date, so only the exact-date game is valid — this also
                    # stops the ±1 window above from resolving it against an adjacent
                    # day's game. A leg WITHOUT one dates off generated_at, so allow ±1
                    # day but require the matchup to match, so a player's leg never
                    # resolves against the wrong game in a series.
                    leg_date = _parse_game_date(leg, parlay["generated_at"])
                    if leg.get("start_time"):
                        if game_date is not None and leg_date is not None and game_date != leg_date:
                            continue
                    else:
                        if game_date is None or leg_date is None or abs((game_date - leg_date).days) > 1:
                            continue
                        if not _mlb_game_matches_label(away_name, home_name, leg.get("game_label", "")):
                            continue
                    stat_type = leg["stat_type"]
                    is_pitching = stat_type in _MLB_PITCHER_TYPES
                    if is_pitching:
                        col = _MLB_PITCHING_RESOLVE.get(stat_type)
                        source = pstats
                        derived = None
                    else:
                        col = _MLB_BATTING_RESOLVE.get(stat_type)
                        source = bstats
                        # Composites (Total Bases, Singles, Hits+Runs+RBIs) have no
                        # single boxscore key and must be computed from the raw stats.
                        derived = _mlb_derived_batting(stat_type, bstats)
                    if derived is None and (col is None or source is None):
                        continue
                    try:
                        # Empty dict means player didn't appear; treat stat as 0
                        actual = (derived if derived is not None
                                  else float(source.get(col, 0) or 0))
                        leg["outcome"] = bool(actual > leg["line_score"])
                        resolved_count += 1
                    except Exception:
                        continue

    marked = _mark_parlay_outcomes(data)
    if resolved_count or marked or attempted:
        _save(data)   # attempt counters must persist, or a dead leg is retried forever
    return resolved_count


def _resolve_wnba_legs() -> int:
    """Auto-resolve pending WNBA legs via nba_api PlayerGameLog (league 10). Returns resolved count."""
    data = _load()
    player_legs: dict[str, list] = defaultdict(list)
    attempted = 0
    for parlay in data["parlays"]:
        if parlay["sport"] != "WNBA":
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is not None:
                continue
            if _is_historical_leg(leg):
                continue
            if not _leg_is_resolvable(leg, parlay["generated_at"]):
                continue
            # Count the try up front. A leg the API never returns is only ever seen
            # here, so this is the one place the give-up counter can advance.
            leg["resolve_attempts"] = int(leg.get("resolve_attempts", 0)) + 1
            attempted += 1
            player_legs[leg["player_name"]].append((parlay, leg))

    if not player_legs:
        _mark_parlay_outcomes(data)
        _save(data)
        return 0

    from nba_api.stats.endpoints import commonallplayers, playergamelog as _pgl  # type: ignore

    # Build WNBA name→id map
    try:
        _time.sleep(0.5)
        wnba_df = commonallplayers.CommonAllPlayers(
            is_only_current_season=0, league_id="10"
        ).get_data_frames()[0]
        wnba_id_map = {row["DISPLAY_FIRST_LAST"].lower(): int(row["PERSON_ID"])
                       for _, row in wnba_df.iterrows()}
    except Exception:
        wnba_id_map = {}

    resolved_count = 0
    for player_name, entries in player_legs.items():
        if _resolve_over_budget():
            break   # out of wall-clock budget — remaining legs retry next pass
        pid = wnba_id_map.get(player_name.strip().lower())
        if not pid:
            # Try normalized name match
            norm = _normalize_name(player_name)
            for k, v in wnba_id_map.items():
                if _normalize_name(k) == norm:
                    pid = v
                    break
        if not pid:
            continue

        # Fetch current WNBA season game log (try newest season first)
        df = pd.DataFrame()
        for season in ("2026", "2025"):
            try:
                _time.sleep(0.6)
                logs = _pgl.PlayerGameLog(
                    player_id=pid, season=season,
                    season_type_all_star="Regular Season",
                    league_id_nullable="10", timeout=15,
                ).get_data_frames()[0]
                if not logs.empty:
                    df = logs if df.empty else pd.concat([df, logs], ignore_index=True)
            except Exception:
                continue
        if df.empty:
            continue

        for parlay, leg in entries:
            spec = _NBA_RESOLVE.get(leg["stat_type"])
            if spec is None:
                continue
            if not leg.get("start_time", "") and leg.get("game_label", ""):
                row = _find_game_by_label(df, leg["game_label"], parlay["generated_at"])
            else:
                target = _parse_game_date(leg, parlay["generated_at"])
                if target is None:
                    continue
                row = _find_game_in_log(df, target, leg.get("game_label", ""))
            if row is None:
                continue
            try:
                actual = _stat_from_row(row, spec)
                leg["outcome"] = bool(actual > leg["line_score"])
                resolved_count += 1
            except Exception:
                continue

    marked = _mark_parlay_outcomes(data)
    if resolved_count or marked or attempted:
        _save(data)   # attempt counters must persist, or a dead leg is retried forever
    return resolved_count


def get_abandoned_legs(sport: str | None = None) -> list:
    """
    Legs the resolver has given up on, grouped by stat type. Silently dropping them is
    how "Runs Scored" went months without a single resolved leg; surfacing the count is
    what turns an unresolvable stat into something you can see and fix.
    """
    data = _load()
    counts: dict = defaultdict(lambda: {"n": 0, "players": set()})
    for parlay in data["parlays"]:
        if sport and parlay.get("sport") != sport:
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is not None or not _leg_is_abandoned(leg):
                continue
            key = (parlay.get("sport"), leg["stat_type"])
            counts[key]["n"] += 1
            counts[key]["players"].add(leg["player_name"])
    return sorted(
        ({"sport": s, "stat_type": st, "legs": d["n"], "players": len(d["players"])}
         for (s, st), d in counts.items()),
        key=lambda x: -x["legs"],
    )


def reset_outcomes(sport: str) -> int:
    """
    Clear resolved outcomes for a sport so they can be re-resolved from scratch.

    Needed after a resolver fix: outcomes are persisted, so a bug that scored legs
    against the wrong game stays baked into parlay_log.json (and into calibration)
    until the affected legs are re-run. Returns the number of legs cleared.
    """
    data = _load()
    cleared = 0
    for parlay in data["parlays"]:
        if parlay.get("sport") != sport:
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is not None:
                leg["outcome"] = None
                cleared += 1
            # Clearing the attempt counter is what makes a backfill possible: legs the
            # resolver had given up on become eligible again, which is the whole point
            # of re-running after fixing the resolver.
            leg.pop("resolve_attempts", None)
        parlay["parlay_hit"] = None
    if cleared:
        _save(data)
    return cleared


def _resolve_nfl_legs() -> int:
    """
    Auto-resolve pending NFL legs against nflverse weekly outcomes. Grades a prop by looking up
    the player's actual stat that week (matched by the leg's opponent), so it mirrors the
    WNBA/NBA game_label matching. Returns resolved count.

    NFL games are weekly and the season won't start until ~September, so in practice this no-ops
    until real NFL legs are logged and their games played — but it grades correctly against
    historical data today (see the module test).
    """
    data = _load()
    player_legs: dict[str, list] = defaultdict(list)
    attempted = 0
    for parlay in data["parlays"]:
        if parlay.get("sport") != "NFL":
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is not None:
                continue
            if _is_historical_leg(leg):
                continue
            if not _leg_is_resolvable(leg, parlay["generated_at"]):
                continue
            leg["resolve_attempts"] = int(leg.get("resolve_attempts", 0)) + 1
            attempted += 1
            player_legs[leg["player_name"]].append((parlay, leg))

    if not player_legs:
        _mark_parlay_outcomes(data)
        _save(data)
        return 0

    import nfl_analysis as _nfl
    _season_cache: dict = {}

    def _season_df(season):
        if season not in _season_cache:
            try:
                _, _season_cache[season] = _nfl.get_season(season)
            except Exception:
                _season_cache[season] = None
        return _season_cache[season]

    resolved_count = 0
    for player_name, entries in player_legs.items():
        if _resolve_over_budget():
            break
        for parlay, leg in entries:
            if leg["outcome"] is not None:
                continue
            d = _parse_game_date(leg, parlay["generated_at"])
            if d is None:
                continue
            df = _season_df(_nfl.season_for_date(d))
            if df is None or df.empty:
                continue
            # Opponents = the two teams in the leg's game_label ("AWAY @ HOME"); only the one
            # the player actually faced matches their weekly row.
            gl = (leg.get("game_label") or "").upper()
            opps = [t.strip() for t in gl.replace("@", " @ ").split("@") if t.strip()]
            try:
                actual = _nfl.stat_for_game(df, player_name, leg["stat_type"], opponents=opps)
            except Exception:
                actual = None
            if actual is None:
                continue
            leg["outcome"] = bool(actual > leg["line_score"])
            resolved_count += 1

    marked = _mark_parlay_outcomes(data)
    if resolved_count or marked or attempted:
        _save(data)
    return resolved_count


def resolve_all_legs(budget_seconds: float = RESOLVE_BUDGET_SECONDS) -> dict:
    """
    Resolve NBA, MLB, WNBA, and NFL pending legs. Returns {nba, mlb, wnba, nfl} counts.

    The sports share one wall-clock budget: whichever runs first can use the whole window, and
    once it's spent the remaining resolvers no-op on their first loop check. Bounding the pass is
    what keeps a slow resolve from starving the daily slate generation that runs after it.
    """
    global _resolve_deadline
    import socket
    _prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(RESOLVE_HTTP_TIMEOUT)
    _resolve_deadline = _time.monotonic() + budget_seconds if budget_seconds else None
    try:
        nba  = resolve_nba_legs()
        mlb  = _resolve_mlb_legs()
        wnba = _resolve_wnba_legs()
        nfl  = _resolve_nfl_legs()
    finally:
        socket.setdefaulttimeout(_prev_timeout)
        _resolve_deadline = None
    return {"nba": nba, "mlb": mlb, "wnba": wnba, "nfl": nfl}


# ─────────────────────────────────────────────────────────────────────────────
# Calibration
# ─────────────────────────────────────────────────────────────────────────────

def _prop_key(leg: dict) -> tuple:
    """
    Identity of the underlying prop (player + stat + line + game).

    The parlay builder reuses one leg across many parlays (up to max_leg_uses, and
    again in both the safe and value pools), so counting leg rows measures how
    often a prop was *bet*, not how often the model was *right* about it. Every
    per-stat accuracy number — and every calibration factor — has to dedupe on
    this key, or a single prop can satisfy CAL_MIN_SAMPLES on its own.
    """
    game = (leg.get("game_id") or leg.get("game_label")
            or str(leg.get("start_time", ""))[:10])
    return (
        _normalize_name(str(leg.get("player_name", ""))),
        str(leg.get("stat_type", "")),
        leg.get("line_score"),
        str(game),
    )


def _cal_key(leg: dict) -> str:
    """
    Calibration bucket for a leg — usually just its stat_type, but a leg tagged
    with a distinct model `source` gets its own bucket ("Home Runs::hr_power_picks").

    HR Power Picks scores home-run probability with a six-factor model (recent
    rate, BvP, pitcher HR/9, park, weather, platoon) that predicts nearly double
    what the game-log HR scorer does — 40.5% vs 20.9% over resolved legs — so
    pooling both into one "Home Runs" factor fits neither. Same rule the epoch
    comment states: a number that measures two different sources is not a
    calibration.
    """
    src = leg.get("source")
    stat = str(leg.get("stat_type", ""))
    return f"{stat}::{src}" if src else stat


def _cal_key_label(key: str) -> str:
    """Human-readable label for a calibration bucket key (for display tables)."""
    if "::" in key:
        stat, src = key.split("::", 1)
        return f"{stat} ({src.replace('_', ' ')})"
    return key


def _week_ordinal(iso_week: str) -> int | None:
    """Convert '2026-W27' to a comparable weekly ordinal (Monday's date // 7)."""
    try:
        year, wk = iso_week.split("-W")
        monday = datetime.strptime(f"{year}-W{int(wk):02d}-1", "%G-W%V-%u")
        return monday.toordinal() // 7
    except Exception:
        return None


def _latest_week_ordinal(data: dict) -> int | None:
    ords = [_week_ordinal(p.get("iso_week", "")) for p in data["parlays"] if p.get("iso_week")]
    ords = [o for o in ords if o is not None]
    return max(ords) if ords else None


def _recent_active_stats(data: dict, sport: str | None, weeks: int = CAL_RECENCY_WEEKS) -> set | None:
    """
    Cal-keys generated within `weeks` of the latest logged week — i.e. the markets still
    live. A stat absent from this set is retired: its only remaining legs are stale, so it
    should emit neither a calibration factor nor a drift warning. Returns None when recency
    can't be determined, in which case callers treat every stat as live (no gating).
    """
    latest = _latest_week_ordinal(data)
    if latest is None:
        return None
    live: set = set()
    for p in data["parlays"]:
        if sport and p.get("sport") != sport:
            continue
        wo = _week_ordinal(p.get("iso_week", ""))
        if wo is None or (latest - wo) >= weeks:
            continue
        for leg in p["legs"]:
            live.add(_cal_key(leg))
    return live


def get_calibration(sport: str | None = None) -> dict:
    """
    Return per-stat calibration factors from resolved legs, recency-weighted
    so a live drift in model accuracy is corrected within a few weeks instead
    of being diluted by months of older, since-resolved history.
    Each leg is weighted by 0.5 ** (weeks_ago / CAL_HALF_LIFE_WEEKS), where
    weeks_ago is measured against the most recent iso_week in the log.
    Pass sport='NBA', 'MLB', or 'WNBA' to get sport-specific factors;
    omit to blend all sports (legacy behaviour).
    factor > 1.0 → model underestimates; < 1.0 → overestimates.
    Only populated once a stat's weighted sample size reaches CAL_MIN_SAMPLES.
    """
    data = _load()
    # NOT epoch-filtered (unlike parlay calibration). This factor corrects the
    # base per-stat model — the game-log/Statcast (and six-factor HR) scorers —
    # which the market blend did not touch; the blend is a separate correction
    # layered on top afterward. Epoch-filtering here would throw away every
    # hard-won per-stat factor the moment the epoch is bumped, leaving known-
    # overconfident props (Home Runs, combo props) undeflated during the gap.
    parlays = [p for p in data["parlays"] if not sport or p.get("sport") == sport]
    if not parlays:
        return {}

    week_ords = [_week_ordinal(p["iso_week"]) for p in parlays if p.get("iso_week")]
    week_ords = [w for w in week_ords if w is not None]
    latest_ord = max(week_ords) if week_ords else None

    predicted: dict[str, float] = defaultdict(float)
    actual: dict[str, float] = defaultdict(float)
    weight_sum: dict[str, float] = defaultdict(float)
    seen_props: set = set()

    for parlay in parlays:
        w = 1.0
        if latest_ord is not None:
            wk_ord = _week_ordinal(parlay.get("iso_week", ""))
            if wk_ord is not None:
                weeks_ago = max(0, latest_ord - wk_ord)
                w = 0.5 ** (weeks_ago / CAL_HALF_LIFE_WEEKS)
        for leg in parlay["legs"]:
            if leg["outcome"] is None or leg["outcome"] == "void":
                continue
            key = _prop_key(leg)
            if key in seen_props:
                continue  # same prop, reused in another parlay — one observation
            seen_props.add(key)
            stat = _cal_key(leg)
            predicted[stat] += w * leg["predicted_hit_rate"]
            actual[stat] += w * (1.0 if leg["outcome"] is True else 0.0)
            weight_sum[stat] += w

    live = _recent_active_stats(data, sport)
    factors = {}
    for stat, wsum in weight_sum.items():
        if live is not None and stat not in live:
            continue  # retired market — don't carry a factor derived from stale legs
        if wsum < CAL_MIN_SAMPLES:
            continue
        p_mean = predicted[stat] / wsum
        a_mean = actual[stat] / wsum
        if p_mean > 0:
            raw = a_mean / p_mean
            factors[stat] = round(max(CAL_MIN_FACTOR, min(CAL_MAX_FACTOR, raw)), 4)
    return factors


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

def last_parlay_time(sport: str | None = None) -> datetime | None:
    """Timestamp of the most recently logged parlay, for detecting a missed daily run."""
    data = _load()
    stamps = []
    for p in data["parlays"]:
        if sport and p.get("sport") != sport:
            continue
        try:
            stamps.append(datetime.fromisoformat(p["generated_at"]))
        except Exception:
            continue
    return max(stamps) if stamps else None


def get_all_weeks() -> list:
    """Return ISO weeks that have logged parlays, most-recent first."""
    data = _load()
    return sorted({p["iso_week"] for p in data["parlays"]}, reverse=True)


def get_sport_weeks(sport: str) -> list:
    """Return ISO weeks that have logged parlays for a specific sport, most-recent first."""
    data = _load()
    return sorted(
        {p["iso_week"] for p in data["parlays"] if p.get("sport") == sport},
        reverse=True,
    )


def get_weekly_summary(week: str | None = None, sport: str | None = None) -> dict:
    """Compute accuracy metrics for a given ISO week (defaults to current week).
    Pass sport='NBA' or sport='MLB' to filter."""
    if week is None:
        week = datetime.now().strftime("%G-W%V")
    data = _load()
    week_parlays = [p for p in data["parlays"] if p.get("iso_week") == week]
    if sport:
        week_parlays = [p for p in week_parlays if p.get("sport") == sport]

    resolved_parlays  = [p for p in week_parlays if p["parlay_hit"] is not None]
    hit_parlays       = [p for p in resolved_parlays if p["parlay_hit"]]

    all_legs      = [l for p in week_parlays for l in p["legs"]]
    resolved_legs = [l for l in all_legs if l["outcome"] is not None and l["outcome"] != "void"]
    hit_legs      = [l for l in resolved_legs if l["outcome"] is True]

    # Per-stat accuracy is measured on unique props, not leg rows: the same prop is
    # reused across many parlays, which would otherwise multiply one right-or-wrong
    # call into a dozen and make a thin stat look statistically settled.
    stat_data: dict = defaultdict(lambda: {"predicted": [], "actual": []})
    seen_props: set = set()
    for leg in resolved_legs:
        key = _prop_key(leg)
        if key in seen_props:
            continue
        seen_props.add(key)
        s = leg["stat_type"]
        stat_data[s]["predicted"].append(leg["predicted_hit_rate"])
        stat_data[s]["actual"].append(1.0 if leg["outcome"] is True else 0.0)

    stat_breakdown = {}
    for stat, d in stat_data.items():
        n = len(d["predicted"])
        p_mean = sum(d["predicted"]) / n
        a_mean = sum(d["actual"]) / n
        stat_breakdown[stat] = {
            "n":                  n,
            "predicted_hit_rate": round(p_mean, 3),
            "actual_hit_rate":    round(a_mean, 3),
            "bias":               round(a_mean - p_mean, 3),
        }

    # By sportsbook
    sb_data: dict = defaultdict(lambda: {"total": 0, "hit": 0})
    for p in resolved_parlays:
        sb = p.get("sportsbook", "Unknown")
        sb_data[sb]["total"] += 1
        if p["parlay_hit"]:
            sb_data[sb]["hit"] += 1

    # Predicted vs actual EV
    avg_predicted_prob = (
        sum(p["predicted_prob"] for p in resolved_parlays) / len(resolved_parlays)
        if resolved_parlays else None
    )

    # Closing-line value for the week: closing implied minus entry implied (probability
    # points), deduped per prop and restricted to market-priced epochs. Positive CLV = the
    # market moved toward the pick — the sharpness signal that converges per leg instead of
    # waiting on win/loss variance, so it belongs next to ROI in every performance view.
    clv_diffs = []
    clv_seen: set = set()
    for p in week_parlays:
        if p.get("model_epoch") not in _MARKET_EPOCHS:
            continue
        for leg in p["legs"]:
            close = leg.get("closing_implied")
            entry = leg.get("implied_prob")
            if close is None or not entry:
                continue
            key = _prop_key(leg)
            if key in clv_seen:
                continue
            clv_seen.add(key)
            clv_diffs.append(float(close) - float(entry))
    clv = {
        "n":            len(clv_diffs),
        "avg_clv":      round(sum(clv_diffs) / len(clv_diffs), 4) if clv_diffs else None,
        "pct_positive": round(sum(1 for x in clv_diffs if x > 0) / len(clv_diffs), 4) if clv_diffs else None,
    }

    return {
        "week":              week,
        "total_parlays":     len(week_parlays),
        "resolved_parlays":  len(resolved_parlays),
        "parlay_hit_rate":   round(len(hit_parlays) / len(resolved_parlays), 3) if resolved_parlays else None,
        "avg_predicted_prob": round(avg_predicted_prob, 3) if avg_predicted_prob is not None else None,
        "total_legs":        len(all_legs),
        "resolved_legs":     len(resolved_legs),
        # Distinct props behind resolved_legs — the real accuracy sample size.
        "unique_resolved_props": len(seen_props),
        "leg_hit_rate":      round(len(hit_legs) / len(resolved_legs), 3) if resolved_legs else None,
        "stat_breakdown":    stat_breakdown,
        "sportsbook_breakdown": {sb: d for sb, d in sb_data.items()},
        "clv":               clv,
    }


def get_all_time_calibration_table(sport: str | None = None) -> list:
    """Return a list of dicts for the calibration summary table.
    Pass sport='NBA' or sport='MLB' to filter."""
    data = _load()
    predicted: dict[str, list] = defaultdict(list)
    actual: dict[str, list] = defaultdict(list)
    seen_props: set = set()

    for parlay in data["parlays"]:
        if sport and parlay.get("sport") != sport:
            continue
        for leg in parlay["legs"]:
            # "void" is a truthy string, so it has to be screened out explicitly or
            # a postponed game scores as a hit.
            if leg["outcome"] is None or leg["outcome"] == "void":
                continue
            key = _prop_key(leg)
            if key in seen_props:
                continue
            seen_props.add(key)
            stat = _cal_key(leg)
            predicted[stat].append(leg["predicted_hit_rate"])
            actual[stat].append(1.0 if leg["outcome"] is True else 0.0)

    live = _recent_active_stats(data, sport)
    rows = []
    for stat in sorted(predicted):
        n = len(predicted[stat])
        p_mean = sum(predicted[stat]) / n if n else 0
        a_mean = sum(actual[stat]) / n if n else 0
        # Retired markets keep their historical row (for transparency) but carry no factor:
        # the number is stale and is not applied to any current scoring.
        recent = live is None or stat in live
        factor = None
        if recent and n >= CAL_MIN_SAMPLES and p_mean > 0:
            raw = a_mean / p_mean
            factor = round(max(CAL_MIN_FACTOR, min(CAL_MAX_FACTOR, raw)), 3)
        rows.append({
            "stat_type":          _cal_key_label(stat),
            "samples":            n,
            "predicted_hit_rate": round(p_mean, 3),
            "actual_hit_rate":    round(a_mean, 3),
            "calibration_factor": factor,
            "active":             factor is not None,
            "recent":             recent,
        })
    return rows


PARLAY_CAL_MIN_SAMPLES = 30    # resolved parlays of a given size before its factor is trusted
PARLAY_CAL_MIN_FACTOR  = 0.05
# Parlay calibration only ever DEFLATES: correlated legs and leg-level overconfidence
# push a parlay's true joint probability below the independence-product, never above.
# A measured factor >1 is small-sample noise, and letting it inflate the probability
# manufactures fake positive EV (it was marking +150%-EV 5-leg parlays "recommended").
PARLAY_CAL_MAX_FACTOR  = 1.0


def get_parlay_calibration(sport: str | None = None) -> dict:
    """
    Per-leg-count calibration for whole-parlay probability. Returns {n_legs: factor};
    a factor below 1 means the model overestimates parlays of that size.

    A parlay's probability is the product of its legs', which assumes the legs are
    independent and that each leg's probability is honest. Neither holds. Leg-level
    overconfidence compounds multiplicatively, so the error grows with pick count:
    measured at 1.3x on 2-leg but 3.7x on MLB 4-leg and 5.3x on WNBA 4-leg. Leg
    calibration cannot see this — it is a property of the product, not of any one leg —
    which is how 4-leg parlays came to be booked at a 16.5% chance while hitting 4.4%,
    against a 10% break-even.

    Recency-weighted on the same half-life as leg calibration.
    """
    data = _load()
    parlays = [p for p in data["parlays"]
               if p["parlay_hit"] is not None
               and (not sport or p.get("sport") == sport)
               # Only grade predictions this model actually made. Every parlay resolved
               # before the de-vig fix was predicted by a model that never saw the
               # market; its overconfidence is not this model's overconfidence, and
               # applying it on top of corrected legs deflates twice — which is how the
               # board came to report that every parlay on it loses.
               and p.get("model_epoch") == _MODEL_EPOCH]
    if not parlays:
        return {}

    week_ords = [_week_ordinal(p.get("iso_week", "")) for p in parlays]
    week_ords = [w for w in week_ords if w is not None]
    latest = max(week_ords) if week_ords else None

    predicted: dict[int, float] = defaultdict(float)
    actual: dict[int, float] = defaultdict(float)
    weight: dict[int, float] = defaultdict(float)

    for p in parlays:
        w = 1.0
        if latest is not None:
            wk = _week_ordinal(p.get("iso_week", ""))
            if wk is not None:
                w = 0.5 ** (max(0, latest - wk) / CAL_HALF_LIFE_WEEKS)
        n = len(p["legs"])
        predicted[n] += w * float(p["predicted_prob"])
        actual[n]    += w * (1.0 if p["parlay_hit"] else 0.0)
        weight[n]    += w

    factors = {}
    for n, wsum in weight.items():
        if wsum < PARLAY_CAL_MIN_SAMPLES:
            continue
        p_mean = predicted[n] / wsum
        a_mean = actual[n] / wsum
        if p_mean > 0:
            raw = a_mean / p_mean
            factors[n] = round(max(PARLAY_CAL_MIN_FACTOR,
                                   min(PARLAY_CAL_MAX_FACTOR, raw)), 4)
    return factors


# ─────────────────────────────────────────────────────────────────────────────
# Market blend, same-game correlation, reliability, CLV
# ─────────────────────────────────────────────────────────────────────────────

# Epochs whose legs carry an honest de-vigged implied_prob — the pool the blend
# weight and correlation penalty are measured on. Pre-devig legs pinned implied
# at 0.50 and would poison both fits.
_MARKET_EPOCHS = ("devig", "blend")

MB_PRIOR_WEIGHT   = 0.35   # prior blend weight (model share) before data speaks
MB_PRIOR_STRENGTH = 150.0  # weighted legs the prior counts as — small samples move slowly
MB_MAX_WEIGHT     = 0.70   # never trust the model more than this over the market


def _market_legs(sport: str | None):
    """Resolved, deduped legs from market-aware epochs with (model_prob, implied, hit, w)."""
    data = _load()
    parlays = [p for p in data["parlays"]
               if p.get("model_epoch") in _MARKET_EPOCHS
               and (not sport or p.get("sport") == sport)]
    week_ords = [_week_ordinal(p["iso_week"]) for p in parlays if p.get("iso_week")]
    week_ords = [w for w in week_ords if w is not None]
    latest = max(week_ords) if week_ords else None
    seen: set = set()
    out = []
    for p in parlays:
        w = 1.0
        if latest is not None:
            wk = _week_ordinal(p.get("iso_week", ""))
            if wk is not None:
                w = 0.5 ** (max(0, latest - wk) / CAL_HALF_LIFE_WEEKS)
        for leg in p["legs"]:
            if leg.get("outcome") not in (True, False):
                continue
            implied = leg.get("implied_prob")
            if not implied:
                continue
            key = _prop_key(leg)
            if key in seen:
                continue
            seen.add(key)
            model_p = leg.get("model_hit_rate")
            if model_p is None:
                model_p = leg.get("predicted_hit_rate")
            out.append((float(model_p), float(implied),
                        1.0 if leg["outcome"] is True else 0.0, w))
    return out


def get_market_blend(sport: str | None = None) -> float:
    """
    Weight on the model's probability when blending with the de-vigged market:
    shipped_prob = w*model + (1-w)*implied.

    Fit by grid-searching the weighted Brier score over resolved legs, then
    shrunk toward MB_PRIOR_WEIGHT by MB_PRIOR_STRENGTH pseudo-legs so a thin
    sample can't slam the weight to an extreme. First audit (2026-07-19,
    n=243): WNBA fit 0.00 — the market alone beat every blend — MLB fit 0.25.
    """
    legs = _market_legs(sport)
    wsum = sum(w for *_, w in legs)
    if wsum <= 0:
        return MB_PRIOR_WEIGHT
    best_lam, best_brier = MB_PRIOR_WEIGHT, None
    for i in range(21):
        lam = i / 20.0
        brier = sum(w * (lam * mp + (1 - lam) * ip - y) ** 2
                    for mp, ip, y, w in legs) / wsum
        if best_brier is None or brier < best_brier:
            best_lam, best_brier = lam, brier
    lam = (wsum * best_lam + MB_PRIOR_STRENGTH * MB_PRIOR_WEIGHT) / (wsum + MB_PRIOR_STRENGTH)
    return round(min(MB_MAX_WEIGHT, max(0.0, lam)), 3)


SG_MIN_PAIRS   = 150   # same-game pairs before the measured penalty is trusted
SG_DEFAULT     = 0.85  # conservative fallback until the log can speak
SG_FLOOR       = 0.55


def get_same_game_penalty(sport: str | None = None) -> float:
    """
    DORMANT — measured but no longer applied to pricing (as of 2026-07-20). Kept
    as a monitored metric and for a future per-prop-type correlation model; the
    parlay builders default same_game_penalty to 1.0 and no call site passes this.

    Multiplier that would deflate a parlay's probability once per same-game leg
    pair, measured as the joint-hit ratio of same-game pairs normalized by the
    cross-game ratio (the normalization strips leg overconfidence, leaving pure
    correlation). Retired for two reasons the full-backlog resolution exposed:
      1. Unstable: measured 0.716 on ~524 pairs, then 1.34 (MLB) once the sample
         tripled — too high-variance to price against.
      2. Wrong shape: it is deflate-only (min(1.0, ...)), but the fuller data
         shows same-game legs hitting *more* than independence (positive
         correlation, the intuitive pace/game-script effect), which a deflate-only
         multiplier structurally cannot represent.
    Doing this right needs a per-prop-type model (pace props correlate positively;
    usage-competing props negatively), not one blended multiplier.

    Falls back to the all-sport measurement, then SG_DEFAULT, when thin.
    """
    def _pairs(sp):
        data = _load()
        sg = [0.0, 0.0, 0.0]; xg = [0.0, 0.0, 0.0]   # [n, joint_hits, indep_pred]
        for p in data["parlays"]:
            if p.get("model_epoch") not in _MARKET_EPOCHS or p.get("parlay_hit") is None:
                continue
            if sp and p.get("sport") != sp:
                continue
            legs = p["legs"]
            gids = [l.get("game_id") or l.get("game_label") for l in legs]
            for i in range(len(legs)):
                for j in range(i + 1, len(legs)):
                    li, lj = legs[i], legs[j]
                    if li.get("outcome") not in (True, False) or lj.get("outcome") not in (True, False):
                        continue
                    t = sg if gids[i] == gids[j] else xg
                    t[0] += 1
                    t[1] += 1 if (li["outcome"] is True and lj["outcome"] is True) else 0
                    t[2] += float(li["predicted_hit_rate"]) * float(lj["predicted_hit_rate"])
        return sg, xg

    for scope in (sport, None):
        sg, xg = _pairs(scope)
        if sg[0] >= SG_MIN_PAIRS and xg[0] >= SG_MIN_PAIRS and sg[2] > 0 and xg[2] > 0:
            sg_ratio = (sg[1] / sg[0]) / (sg[2] / sg[0])
            xg_ratio = (xg[1] / xg[0]) / (xg[2] / xg[0])
            if xg_ratio > 0:
                return round(min(1.0, max(SG_FLOOR, sg_ratio / xg_ratio)), 3)
        if scope is None:
            break
    return SG_DEFAULT


def get_reliability_curve(sport: str | None = None) -> dict:
    """
    Decile reliability of shipped leg probabilities (market-aware epochs,
    deduped, unweighted — the chart answers "how honest have the numbers
    been", not "what factor should we apply").

    Returns {"buckets": [{lo, hi, n, predicted, actual}], "brier": float,
    "n": int, "base_rate": float}.
    """
    data = _load()
    seen: set = set()
    buckets = {i: [0.0, 0.0, 0] for i in range(10)}   # i -> [pred_sum, hits, n]
    brier_sum, n_total, hits_total = 0.0, 0, 0.0
    for p in data["parlays"]:
        if p.get("model_epoch") not in _MARKET_EPOCHS:
            continue
        if sport and p.get("sport") != sport:
            continue
        for leg in p["legs"]:
            if leg.get("outcome") not in (True, False):
                continue
            key = _prop_key(leg)
            if key in seen:
                continue
            seen.add(key)
            pr = float(leg["predicted_hit_rate"])
            hit = 1.0 if leg["outcome"] is True else 0.0
            b = min(int(pr * 10), 9)
            buckets[b][0] += pr; buckets[b][1] += hit; buckets[b][2] += 1
            brier_sum += (pr - hit) ** 2
            n_total += 1; hits_total += hit
    rows = []
    for i in range(10):
        s, h, n = buckets[i]
        if n == 0:
            continue
        rows.append({"lo": i / 10, "hi": (i + 1) / 10, "n": n,
                     "predicted": round(s / n, 4), "actual": round(h / n, 4)})
    return {"buckets": rows,
            "brier": round(brier_sum / n_total, 4) if n_total else None,
            "n": n_total,
            "base_rate": round(hits_total / n_total, 4) if n_total else None}


def update_market_snapshots(props_df, sport: str) -> int:
    """
    Stamp pending legs with the latest market price for the same
    player+stat+line. Called after every FanDuel fetch; games stop being
    listed once they start, so the last snapshot to land approximates the
    closing line. Returns the number of legs updated.
    """
    try:
        if props_df is None or props_df.empty:
            return 0
    except Exception:
        return 0
    quotes = {}
    for _, r in props_df.iterrows():
        implied = r.get("implied_prob")
        if implied is None:
            continue
        key = (str(r.get("player_name", "")).lower(), str(r.get("stat_type", "")),
               float(r.get("line_score", 0)))
        quotes[key] = (float(implied), r.get("american_odds"))
    if not quotes:
        return 0
    data = _load()
    now_iso = datetime.now().isoformat(timespec="seconds")
    updated = 0
    for p in data["parlays"]:
        if p.get("sport") != sport:
            continue
        for leg in p["legs"]:
            if leg.get("outcome") is not None:
                continue   # game resolved — entry vs close is already frozen
            key = (str(leg.get("player_name", "")).lower(), str(leg.get("stat_type", "")),
                   float(leg.get("line_score", 0)))
            q = quotes.get(key)
            if q is None:
                continue
            implied, odds = q
            if leg.get("closing_implied") != implied or leg.get("closing_odds") != odds:
                leg["closing_implied"] = implied
                leg["closing_odds"] = odds
                leg["closing_seen_at"] = now_iso
                updated += 1
    if updated:
        _save(data)
    return updated


def get_clv_summary(sport: str | None = None) -> dict:
    """
    Closing-line value of logged picks: closing implied minus entry implied,
    in probability points. Positive CLV = the market moved toward the pick,
    the standard sharpness signal — it converges per leg instead of waiting
    on months of win/loss variance.
    """
    data = _load()
    seen: set = set()
    diffs = []
    for p in data["parlays"]:
        if p.get("model_epoch") not in _MARKET_EPOCHS:
            continue
        if sport and p.get("sport") != sport:
            continue
        for leg in p["legs"]:
            close = leg.get("closing_implied")
            entry = leg.get("implied_prob")
            if close is None or not entry:
                continue
            key = _prop_key(leg)
            if key in seen:
                continue
            seen.add(key)
            diffs.append(float(close) - float(entry))
    if not diffs:
        return {"n": 0, "avg_clv": None, "pct_positive": None}
    return {"n": len(diffs),
            "avg_clv": round(sum(diffs) / len(diffs), 4),
            "pct_positive": round(sum(1 for d in diffs if d > 0) / len(diffs), 4)}


def get_lead_time_experiment(sport: str | None = None,
                             threshold: float = EARLY_LEAD_HOURS) -> dict:
    """
    Prospective A/B of early vs late picks. Lead time is the single biggest CLV lever found:
    early picks are ~break-even CLV and beat their implied hit rate, late picks bleed. Every
    pick is logged and tagged (never filtered), so this compares the two cohorts on both CLV
    (leading) and realized hit-vs-implied (lagging), deduped per prop.

    Only legs carrying a stamped `lead_hours` are counted — i.e. those logged since the flag
    shipped — so the comparison is a clean forward experiment, not contaminated by history.
    """
    data = _load()
    b = {"early": {"clv": [], "res": []}, "late": {"clv": [], "res": []}}
    seen: set = set()
    for p in data["parlays"]:
        if sport and p.get("sport") != sport:
            continue
        if p.get("model_epoch") not in _MARKET_EPOCHS:
            continue
        for leg in p["legs"]:
            lh = leg.get("lead_hours")
            if lh is None:
                continue  # pre-experiment leg — excluded so the A/B stays forward-only
            entry = leg.get("implied_prob")
            if not entry:
                continue
            key = _prop_key(leg)
            if key in seen:
                continue
            seen.add(key)
            bucket = "early" if lh >= threshold else "late"
            close = leg.get("closing_implied")
            if close is not None:
                b[bucket]["clv"].append(float(close) - float(entry))
            if leg.get("outcome") in (True, False):
                b[bucket]["res"].append((1.0 if leg["outcome"] is True else 0.0, float(entry)))

    def _summ(v):
        clv, res = v["clv"], v["res"]
        return {
            "n_clv":        len(clv),
            "avg_clv":      round(sum(clv) / len(clv), 4) if clv else None,
            "pct_positive": round(sum(1 for x in clv if x > 0) / len(clv), 4) if clv else None,
            "n_resolved":   len(res),
            "hit_rate":     round(sum(h for h, _ in res) / len(res), 4) if res else None,
            "implied":      round(sum(i for _, i in res) / len(res), 4) if res else None,
        }
    return {"threshold_hours": threshold,
            "early": _summ(b["early"]), "late": _summ(b["late"])}


# Markets where the model has shown a realized/CLV edge over the book, isolated from the
# markets where it bleeds. The (sport, stat_type) pairs here are the "pocket strategy":
# MLB Pitcher Strikeouts is the only positive-CLV market and is break-even to the line, and
# WNBA rebounding/assist props beat their implied hit rate — while MLB Hits/Home Runs and
# WNBA scoring lose. Unlike lead time, pocket membership is static (no log-time stamp needed),
# so the readout works on history AND accrues forward.
POCKET_MARKETS = frozenset({
    ("MLB", "Pitcher Strikeouts"),
    ("WNBA", "Rebounds"),
    ("WNBA", "Assists"),
    ("WNBA", "Rebs+Asts"),
})


def _is_pocket(sport: str, stat_type: str) -> bool:
    return (sport, stat_type) in POCKET_MARKETS


def get_pocket_experiment(sport: str | None = None, forward_only: bool = False) -> dict:
    """
    A/B the "pocket strategy" (POCKET_MARKETS) against everything else on CLV (leading) and
    realized hit-vs-implied (lagging), deduped per prop. The pocket is the one segment that
    isn't losing to the line; this tracks whether that holds as data grows.

    forward_only=True counts only legs logged since the experiment infrastructure shipped
    (identified by a stamped `lead_hours`), for a clean prospective cut uncontaminated by the
    in-sample history the pocket was chosen on. Default False includes all market-epoch legs.
    """
    data = _load()
    b = {"pocket": {"clv": [], "res": []}, "rest": {"clv": [], "res": []}}
    seen: set = set()
    for p in data["parlays"]:
        if sport and p.get("sport") != sport:
            continue
        if p.get("model_epoch") not in _MARKET_EPOCHS:
            continue
        for leg in p["legs"]:
            if forward_only and leg.get("lead_hours") is None:
                continue
            entry = leg.get("implied_prob")
            if not entry:
                continue
            key = _prop_key(leg)
            if key in seen:
                continue
            seen.add(key)
            bucket = "pocket" if _is_pocket(p["sport"], leg.get("stat_type", "")) else "rest"
            close = leg.get("closing_implied")
            if close is not None:
                b[bucket]["clv"].append(float(close) - float(entry))
            if leg.get("outcome") in (True, False):
                b[bucket]["res"].append((1.0 if leg["outcome"] is True else 0.0, float(entry)))

    def _summ(v):
        clv, res = v["clv"], v["res"]
        return {
            "n_clv":        len(clv),
            "avg_clv":      round(sum(clv) / len(clv), 4) if clv else None,
            "pct_positive": round(sum(1 for x in clv if x > 0) / len(clv), 4) if clv else None,
            "n_resolved":   len(res),
            "hit_rate":     round(sum(h for h, _ in res) / len(res), 4) if res else None,
            "implied":      round(sum(i for _, i in res) / len(res), 4) if res else None,
        }
    return {"markets": sorted(f"{s}:{m}" for s, m in POCKET_MARKETS),
            "forward_only": forward_only,
            "pocket": _summ(b["pocket"]), "rest": _summ(b["rest"])}


# ─────────────────────────────────────────────────────────────────────────────
# Paper trading — the gate that turns "should I bet" into evidence
# ─────────────────────────────────────────────────────────────────────────────
# The goal is a real edge, and the only honest way to know is to bet on paper first:
# log each strategy's picks as hypothetical flat 1-unit STRAIGHT bets (never parlays — that
# was the variance trap), then track CLV (leads) and realized P&L (lags) with confidence
# bands until a verdict is earned. Picks generated on/after PAPER_TRACK_START are the live,
# out-of-sample record — the only window that counts, since the strategies were chosen on the
# history before it. Everything before is a backtest for reference only.
PAPER_TRACK_START = date(2026, 8, 2)
PAPER_MIN_BETS    = 100   # resolved bets before a realized verdict is trusted
PAPER_MIN_CLV     = 50    # priced bets before a CLV verdict is trusted


def _strat_pocket(sport, leg):
    return _is_pocket(sport, leg.get("stat_type", "")) and \
        float(leg.get("predicted_hit_rate") or 0) > float(leg.get("implied_prob") or 0)

def _strat_all_edge(sport, leg):
    return float(leg.get("predicted_hit_rate") or 0) > float(leg.get("implied_prob") or 0)

def _strat_all(sport, leg):
    return True

# key -> (label, leg predicate). Straight-bet strategies, compared side by side.
PAPER_STRATEGIES = {
    "pocket":   ("Pocket · K's + WNBA reb/ast, +edge", _strat_pocket),
    "all_edge": ("All markets, +model edge",           _strat_all_edge),
    "all":      ("Everything (baseline)",              _strat_all),
}


def _dec_from_odds(american) -> float:
    a = float(american)
    return 1 + (a / 100 if a > 0 else 100 / abs(a))


def _mean_ci(vals: list):
    """(mean, lo95, hi95, n) via normal approx; lo/hi None when n<2."""
    n = len(vals)
    if n == 0:
        return (None, None, None, 0)
    m = sum(vals) / n
    if n < 2:
        return (m, None, None, n)
    var = sum((x - m) ** 2 for x in vals) / (n - 1)
    se = (var / n) ** 0.5
    return (m, m - 1.96 * se, m + 1.96 * se, n)


def _paper_verdict(n_bets, n_clv, clv_lo, clv_hi, roi_lo):
    """
    CLV-first verdict — CLV is the leading indicator, so the market confirming or denying the
    edge outranks a realized P&L that at small n is mostly variance.
    """
    if n_bets < PAPER_MIN_BETS or n_clv < PAPER_MIN_CLV:
        return ("INSUFFICIENT_DATA",
                f"need ≥{PAPER_MIN_BETS} resolved bets and ≥{PAPER_MIN_CLV} priced bets — keep tracking")
    if clv_lo is not None and clv_lo > 0:
        return ("EDGE", "CLV positive with 95% confidence — the market confirms the edge")
    if clv_hi is not None and clv_hi < 0:
        return ("NO_EDGE", "CLV negative with 95% confidence — betting into the closing line")
    if roi_lo is not None and roi_lo > 0:
        return ("UNCONFIRMED_WIN", "profitable so far but CLV isn't positive — likely variance, keep tracking")
    return ("INCONCLUSIVE", "no significant CLV or ROI signal yet — keep tracking")


def get_paper_portfolio(strategy: str = "pocket", sport: str | None = None,
                        live_only: bool = False) -> dict:
    """
    Hypothetical flat 1-unit straight bets for a strategy, deduped per prop. live_only=True
    counts only picks generated on/after PAPER_TRACK_START (the out-of-sample record that
    actually gates real money); False is the full-history backtest for reference.
    """
    label, pred = PAPER_STRATEGIES.get(strategy, PAPER_STRATEGIES["pocket"])
    data = _load()
    seen: set = set()
    pnl: list = []
    clv: list = []
    hits = 0
    for p in data["parlays"]:
        if p.get("model_epoch") not in _MARKET_EPOCHS:
            continue
        if sport and p.get("sport") != sport:
            continue
        if live_only:
            try:
                if datetime.fromisoformat(p["generated_at"]).date() < PAPER_TRACK_START:
                    continue
            except Exception:
                continue
        for leg in p["legs"]:
            if not pred(p["sport"], leg):
                continue
            key = _prop_key(leg)
            if key in seen:
                continue
            seen.add(key)
            entry = leg.get("implied_prob")
            close = leg.get("closing_implied")
            if entry and close is not None:
                clv.append(float(close) - float(entry))
            oc = leg.get("outcome")
            if oc in (True, False):
                a = leg.get("american_odds")
                dec = _dec_from_odds(a) if a else (1 / float(entry) if entry else 2.0)
                pnl.append((dec - 1) if oc is True else -1.0)
                if oc is True:
                    hits += 1

    roi_m, roi_lo, roi_hi, n_bets = _mean_ci(pnl)
    clv_m, clv_lo, clv_hi, n_clv = _mean_ci(clv)
    verdict, reason = _paper_verdict(n_bets, n_clv, clv_lo, clv_hi, roi_lo)
    return {
        "strategy": strategy, "label": label, "live_only": live_only,
        "track_start": PAPER_TRACK_START.isoformat(),
        "n_bets": n_bets, "hits": hits,
        "hit_rate": round(hits / n_bets, 4) if n_bets else None,
        "roi": round(roi_m * 100, 2) if roi_m is not None else None,
        "roi_ci": [round(roi_lo * 100, 1), round(roi_hi * 100, 1)] if roi_lo is not None else None,
        "net_units": round(sum(pnl), 1),
        "n_clv": n_clv,
        "avg_clv": round(clv_m * 100, 2) if clv_m is not None else None,
        "clv_ci": [round(clv_lo * 100, 2), round(clv_hi * 100, 2)] if clv_lo is not None else None,
        "clv_pct_pos": round(sum(1 for x in clv if x > 0) / n_clv, 4) if n_clv else None,
        "verdict": verdict, "verdict_reason": reason,
    }


# ── Strong-edge pocket day alert ──────────────────────────────────────────────
# Most days the pocket board is thin (edges inside the ~1% noise floor at short odds), and
# forcing a bet on a dry day is how edges get given back. This flags the rare day that has a
# pocket play with a genuinely strong edge AND a worthwhile return — the only days worth acting
# on. Bet threshold, not vibes.
POCKET_ALERT_MIN_EDGE = 0.03    # model prob over the line — meaningfully above the noise floor
POCKET_ALERT_MIN_ODDS = -140    # odds floor so a strong-edge play also pays a worthwhile return


def get_pocket_alert(sport: str | None = None, on_date: str | None = None,
                     min_edge: float = POCKET_ALERT_MIN_EDGE,
                     min_odds: int = POCKET_ALERT_MIN_ODDS) -> dict:
    """
    Scan a day's logged pocket-market props and return the ones that clear BOTH a strong-edge
    bar and an odds floor — the plays worth a straight bet. on_date defaults to today (local,
    matching generated_at). qualifies=True means it's a day worth acting on.
    """
    data = _load()
    day = on_date or datetime.now().strftime("%Y-%m-%d")
    seen: dict = {}
    for p in data["parlays"]:
        if not str(p.get("generated_at", "")).startswith(day):
            continue
        if sport and p.get("sport") != sport:
            continue
        for leg in p["legs"]:
            if not _is_pocket(p["sport"], leg.get("stat_type", "")):
                continue
            pred, impl, odds = leg.get("predicted_hit_rate"), leg.get("implied_prob"), leg.get("american_odds")
            if pred is None or not impl or odds is None:
                continue
            key = _prop_key(leg)
            if key in seen:
                continue
            seen[key] = {
                "sport": p["sport"], "player": leg.get("player_name", ""),
                "stat": leg.get("stat_type", ""), "line": leg.get("line_score"),
                "odds": int(odds), "pred": round(float(pred), 4),
                "edge": round(float(pred) - float(impl), 4),
                "book": p.get("sportsbook", ""), "game": leg.get("game_label", ""),
            }
    plays = sorted((v for v in seen.values() if v["edge"] >= min_edge and v["odds"] >= min_odds),
                   key=lambda r: r["edge"], reverse=True)
    return {"date": day, "min_edge": min_edge, "min_odds": min_odds,
            "n_pocket": len(seen), "qualifies": bool(plays), "plays": plays}


DRIFT_MIN_PROPS  = 20    # unique props before a gap is worth calling drift rather than noise
DRIFT_BIAS_ALERT = 0.15  # actual vs predicted this far apart is real miscalibration


def get_drift_warnings(sport: str | None = None,
                       min_props: int = DRIFT_MIN_PROPS,
                       bias_alert: float = DRIFT_BIAS_ALERT,
                       weeks: int = CAL_RECENCY_WEEKS) -> list:
    """
    Stats whose actual hit rate has drifted materially from predicted *recently*, on a
    sample large enough to trust. Measured over the last `weeks` weeks only: an all-time
    window let a market that stopped being generated weeks ago (Runs Scored) keep screaming
    a -30% drift from stale pre-epoch legs, and let a live market's old, since-corrected
    history dilute or inflate its current bias. A retired market has no recent props and so
    drops out naturally; a live market with too few recent props (< min_props) is treated as
    unsettled rather than drifting.
    """
    data = _load()
    latest = _latest_week_ordinal(data)
    predicted: dict[str, list] = defaultdict(list)
    actual: dict[str, list] = defaultdict(list)
    seen_props: set = set()
    for parlay in data["parlays"]:
        if sport and parlay.get("sport") != sport:
            continue
        wo = _week_ordinal(parlay.get("iso_week", ""))
        if latest is not None and (wo is None or (latest - wo) >= weeks):
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is None or leg["outcome"] == "void":
                continue
            key = _prop_key(leg)
            if key in seen_props:
                continue
            seen_props.add(key)
            stat = _cal_key(leg)
            predicted[stat].append(leg["predicted_hit_rate"])
            actual[stat].append(1.0 if leg["outcome"] is True else 0.0)

    out = []
    for stat, preds in predicted.items():
        n = len(preds)
        if n < min_props:
            continue
        p_mean = sum(preds) / n
        a_mean = sum(actual[stat]) / n
        bias = round(a_mean - p_mean, 3)
        if abs(bias) >= bias_alert:
            out.append({
                "stat_type":          _cal_key_label(stat),
                "samples":            n,
                "predicted_hit_rate": round(p_mean, 3),
                "actual_hit_rate":    round(a_mean, 3),
                "bias":               bias,
            })
    return sorted(out, key=lambda x: abs(x["bias"]), reverse=True)


def export_csv(week: str | None = None, sport: str | None = None) -> str:
    """Return a CSV string of all logged legs for the given week and optional sport."""
    if week is None:
        week = datetime.now().strftime("%G-W%V")
    data = _load()
    rows = []
    for p in data["parlays"]:
        if p.get("iso_week") != week:
            continue
        if sport and p.get("sport") != sport:
            continue
        for leg in p["legs"]:
            rows.append({
                "week":                 week,
                "sport":                p["sport"],
                "sportsbook":           p["sportsbook"],
                "kind":                 p.get("kind", ""),
                "generated_at":         p["generated_at"],
                "predicted_parlay_prob": p["predicted_prob"],
                "parlay_hit":           p["parlay_hit"],
                "player_name":          leg["player_name"],
                "stat_type":            leg["stat_type"],
                "line_score":           leg["line_score"],
                "predicted_hit_rate":   leg["predicted_hit_rate"],
                "american_odds":        leg.get("american_odds", ""),
                "game_label":           leg.get("game_label", ""),
                "outcome":              leg["outcome"],
            })
    if not rows:
        cols = ["week","sport","sportsbook","kind","generated_at","predicted_parlay_prob",
                "parlay_hit","player_name","stat_type","line_score","predicted_hit_rate",
                "american_odds","game_label","outcome"]
        return ",".join(cols) + "\n"
    return pd.DataFrame(rows).to_csv(index=False)


def _implied_from_odds(american_odds) -> float:
    """Convert American odds to implied probability (vig-inclusive)."""
    try:
        odds = int(american_odds or 0)
        if odds > 0:
            return round(100 / (odds + 100), 4)
        elif odds < 0:
            return round(-odds / (-odds + 100), 4)
    except Exception:
        pass
    return 0.524  # default -110


def _leg_implied(leg: dict) -> float:
    """
    The book's probability for a leg: the de-vigged value stored at generation time
    when present, otherwise derived from the American price. Legs logged before the
    de-vig fix have no stored value, so they fall back to the vig-inflated number.
    """
    value = leg.get("implied_prob")
    try:
        value = float(value)
        if 0.0 <= value <= 1.0:
            return value
    except (TypeError, ValueError):
        pass
    return _implied_from_odds(leg.get("american_odds"))


def get_player_accuracy(sport: str | None = None) -> list[dict]:
    """Per-player, per-stat hit rate table from all resolved legs, sorted by sample count."""
    data = _load()
    buckets: dict = defaultdict(lambda: {"predicted": [], "actual": [], "implied": []})
    seen_props: set = set()
    for parlay in data["parlays"]:
        if sport and parlay.get("sport") != sport:
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is None or leg["outcome"] == "void":
                continue
            prop = _prop_key(leg)
            if prop in seen_props:
                continue  # one prop, reused across parlays — a single observation
            seen_props.add(prop)
            key = (leg["player_name"], leg["stat_type"])
            buckets[key]["predicted"].append(leg["predicted_hit_rate"])
            buckets[key]["actual"].append(1.0 if leg["outcome"] is True else 0.0)
            buckets[key]["implied"].append(_leg_implied(leg))
    rows = []
    for (player, stat), d in buckets.items():
        n = len(d["predicted"])
        p_mean = sum(d["predicted"]) / n
        a_mean = sum(d["actual"]) / n
        i_mean = sum(d["implied"]) / n
        rows.append({
            "player_name":        player,
            "stat_type":          stat,
            "n_legs":             n,
            "predicted_hit_pct":  round(p_mean * 100, 1),
            "actual_hit_pct":     round(a_mean * 100, 1),
            "bias_pct":           round((a_mean - p_mean) * 100, 1),
            "avg_edge_pct":       round((p_mean - i_mean) * 100, 1),
        })
    return sorted(rows, key=lambda x: -x["n_legs"])


KELLY_START_BANKROLL = 1000.0
KELLY_SLATE_CAP = 0.25   # most of the bankroll a single day's slate may risk


def get_roi_simulation(sport: str | None = None, flat_bet: float = 10.0,
                       slate_cap: float = KELLY_SLATE_CAP,
                       start_bankroll: float = KELLY_START_BANKROLL) -> dict:
    """
    Simulate flat-bet and Kelly ROI across all resolved parlays.

    `payout` is the gross multiplier the book advertises — a PrizePicks 2-pick pays
    3x the entry — so profit on a win is stake*(payout-1), not stake*payout. This
    used to credit the full gross as profit, handing back an extra stake on every
    win and inflating ROI by roughly 20 points. get_leg_count_breakdown already had
    it right, which is why the two disagreed.

    Kelly is staked per slate rather than per parlay. A day's parlays are placed at
    once and share legs, so compounding each one against a bankroll already updated
    by the previous one isn't a strategy anyone could run — chained across thousands
    of correlated bets it ran the bankroll into the billions. Stakes for a day are
    sized off the bankroll as it stood that morning, the slate's total exposure is
    capped at slate_cap of it, and the bankroll settles once at day's end.
    """
    data = _load()
    resolved = [p for p in data["parlays"]
                if p["parlay_hit"] is not None
                and (not sport or p.get("sport") == sport)]
    resolved.sort(key=lambda p: p["generated_at"])

    flat_pnl = 0.0
    total_wagered = 0.0
    flat_series: list[float] = []
    dates: list[str] = []

    for p in resolved:
        payout = float(p.get("payout") or 2.0)
        net = max(payout - 1.0, 0.0)          # profit per unit staked
        total_wagered += flat_bet
        flat_pnl += (flat_bet * net) if p["parlay_hit"] else -flat_bet
        flat_series.append(round(flat_pnl, 2))
        dates.append(p["generated_at"][:10])

    # ── Kelly, settled once per slate ────────────────────────────────────────
    slates: dict[str, list] = defaultdict(list)
    for p in resolved:
        slates[p["generated_at"][:10]].append(p)

    kelly_bankroll = start_bankroll
    kelly_series: list[float] = []
    kelly_staked = 0.0

    # Days ascend in the same order as `resolved`, and each slate keeps that order,
    # so kelly_series lines up one-to-one with flat_series and dates.
    for day in sorted(slates):
        slate = slates[day]
        day_open = kelly_bankroll

        fractions = []
        for p in slate:
            payout = float(p.get("payout") or 2.0)
            net = payout - 1.0
            prob = float(p["predicted_prob"])
            # Kelly on net odds b: f = (p*(b+1) - 1) / b, with b = payout - 1.
            f = ((prob * payout) - 1.0) / net if net > 0 else 0.0
            fractions.append(max(0.0, f))

        wanted = sum(fractions)
        scale = min(1.0, slate_cap / wanted) if wanted > slate_cap else 1.0

        day_pnl = 0.0
        for p, f in zip(slate, fractions):
            payout = float(p.get("payout") or 2.0)
            stake = day_open * f * scale
            kelly_staked += stake
            day_pnl += stake * (payout - 1.0) if p["parlay_hit"] else -stake

        kelly_bankroll = max(day_open + day_pnl, 0.01)
        kelly_series.extend([round(kelly_bankroll, 2)] * len(slate))

    return {
        "n_parlays":      len(resolved),
        "flat_pnl":       round(flat_pnl, 2),
        "total_wagered":  round(total_wagered, 2),
        "roi_pct":        round(flat_pnl / total_wagered * 100, 1) if total_wagered else 0.0,
        "flat_series":    flat_series,
        "kelly_bankroll": round(kelly_bankroll, 2),
        "kelly_staked":   round(kelly_staked, 2),
        "kelly_roi_pct":  round((kelly_bankroll - start_bankroll) / start_bankroll * 100, 1),
        "kelly_series":   kelly_series,
        "dates":          dates,
    }


def get_leg_count_breakdown(sport: str | None = None) -> list[dict]:
    """Hit rate, ROI, and average EV broken down by parlay leg count."""
    data = _load()
    buckets: dict = defaultdict(lambda: {"total": 0, "hits": 0, "bet": 0.0, "returns": 0.0, "ev": 0.0})
    for p in data["parlays"]:
        if p["parlay_hit"] is None:
            continue
        if sport and p.get("sport") != sport:
            continue
        n = len(p.get("legs", []))
        payout = float(p.get("payout") or 2.0)
        prob = float(p["predicted_prob"])
        buckets[n]["total"] += 1
        buckets[n]["bet"] += 10.0
        # payout is gross, so a win nets (payout - 1): EV = prob*(payout-1) - (1-prob),
        # which reduces to prob*payout - 1. Subtracting only (1-prob) double-counted the
        # returned stake and overstated EV by `prob` on every parlay.
        buckets[n]["ev"] += prob * payout - 1.0
        if p["parlay_hit"]:
            buckets[n]["hits"] += 1
            buckets[n]["returns"] += 10.0 * payout
    rows = []
    for n in sorted(buckets):
        d = buckets[n]
        t = d["total"]
        rows.append({
            "n_legs":       n,
            "total":        t,
            "hits":         d["hits"],
            "hit_rate_pct": round(d["hits"] / t * 100, 1) if t else 0.0,
            "avg_ev":       round(d["ev"] / t, 3) if t else 0.0,
            "roi_pct":      round((d["returns"] - d["bet"]) / d["bet"] * 100, 1) if d["bet"] else 0.0,
        })
    return rows


def get_kind_comparison(sport: str | None = None) -> dict:
    """Compare safe vs value parlay performance across all resolved data."""
    data = _load()
    out = {}
    for kind in ("safe", "value"):
        parlays = [p for p in data["parlays"]
                   if p["parlay_hit"] is not None
                   and p.get("kind") == kind
                   and (not sport or p.get("sport") == sport)]
        if not parlays:
            out[kind] = None
            continue
        hits = sum(1 for p in parlays if p["parlay_hit"])
        bet = len(parlays) * 10.0
        returns = sum(float(p.get("payout") or 2.0) * 10.0 for p in parlays if p["parlay_hit"])
        out[kind] = {
            "total":            len(parlays),
            "hits":             hits,
            "hit_rate_pct":     round(hits / len(parlays) * 100, 1),
            "roi_pct":          round((returns - bet) / bet * 100, 1) if bet else 0.0,
            "avg_predicted_pct": round(sum(p["predicted_prob"] for p in parlays) / len(parlays) * 100, 1),
        }
    return out


def get_sportsbook_comparison(sport: str | None = None) -> dict:
    """Per-sportsbook hit rate and flat-bet ROI."""
    data = _load()
    books: dict = defaultdict(lambda: {"total": 0, "hits": 0, "bet": 0.0, "returns": 0.0})
    for p in data["parlays"]:
        if p["parlay_hit"] is None:
            continue
        if sport and p.get("sport") != sport:
            continue
        sb = p.get("sportsbook", "Unknown")
        payout = float(p.get("payout") or 2.0)
        books[sb]["total"] += 1
        books[sb]["bet"] += 10.0
        if p["parlay_hit"]:
            books[sb]["hits"] += 1
            books[sb]["returns"] += 10.0 * payout
    result = {}
    for sb, d in books.items():
        result[sb] = {
            "total":        d["total"],
            "hits":         d["hits"],
            "hit_rate_pct": round(d["hits"] / d["total"] * 100, 1) if d["total"] else 0.0,
            "roi_pct":      round((d["returns"] - d["bet"]) / d["bet"] * 100, 1) if d["bet"] else 0.0,
        }
    return result


def get_monthly_trends(sport: str | None = None) -> list[dict]:
    """Month-by-month parlay and leg hit rates from all resolved parlays."""
    data = _load()
    months: dict = defaultdict(lambda: {
        "total": 0, "hits": 0,
        "leg_total": 0, "leg_hits": 0,
        "pred_sum": 0.0,
    })
    # Parlay counts are per-parlay, but the leg hit rate is per unique prop: the same
    # prop sits in many parlays, and counting it once per parlay would weight the rate
    # by how often the builder happened to reuse it.
    seen_props: set = set()
    for p in data["parlays"]:
        if p["parlay_hit"] is None:
            continue
        if sport and p.get("sport") != sport:
            continue
        month = p["generated_at"][:7]
        months[month]["total"] += 1
        months[month]["pred_sum"] += float(p["predicted_prob"])
        if p["parlay_hit"]:
            months[month]["hits"] += 1
        for leg in p["legs"]:
            if leg["outcome"] is None or leg["outcome"] == "void":
                continue
            prop = _prop_key(leg)
            if prop in seen_props:
                continue
            seen_props.add(prop)
            months[month]["leg_total"] += 1
            if leg["outcome"] is True:
                months[month]["leg_hits"] += 1
    rows = []
    for month in sorted(months):
        d = months[month]
        t, lt = d["total"], d["leg_total"]
        rows.append({
            "month":            month,
            "total":            t,
            "hits":             d["hits"],
            "hit_rate_pct":     round(d["hits"] / t * 100, 1) if t else 0.0,
            "leg_hit_rate_pct": round(d["leg_hits"] / lt * 100, 1) if lt else 0.0,
            "avg_pred_pct":     round(d["pred_sum"] / t * 100, 1) if t else 0.0,
        })
    return rows


def get_streak_info(sport: str | None = None) -> dict:
    """Current hit/miss streak and all-time longest streaks."""
    data = _load()
    resolved = [p for p in data["parlays"]
                if p["parlay_hit"] is not None
                and (not sport or p.get("sport") == sport)]
    resolved.sort(key=lambda p: p["generated_at"])
    if not resolved:
        return {"current_streak": 0, "current_type": None,
                "longest_hit": 0, "longest_miss": 0, "total_resolved": 0}

    last_result = resolved[-1]["parlay_hit"]
    current_type = "hit" if last_result else "miss"
    current = sum(1 for _ in __import__("itertools").takewhile(
        lambda p: p["parlay_hit"] == last_result, reversed(resolved)
    ))

    longest_hit = longest_miss = run_h = run_m = 0
    for p in resolved:
        if p["parlay_hit"]:
            run_h += 1; run_m = 0
            longest_hit = max(longest_hit, run_h)
        else:
            run_m += 1; run_h = 0
            longest_miss = max(longest_miss, run_m)

    return {
        "current_streak":  current,
        "current_type":    current_type,
        "longest_hit":     longest_hit,
        "longest_miss":    longest_miss,
        "total_resolved":  len(resolved),
    }


def get_best_worst_week(week: str, sport: str | None = None) -> dict:
    """Best (highest-payout hit) and worst (most-confident miss) parlay for a given week."""
    data = _load()
    wk = [p for p in data["parlays"]
          if p.get("iso_week") == week
          and p["parlay_hit"] is not None
          and (not sport or p.get("sport") == sport)]
    hits   = [p for p in wk if p["parlay_hit"]]
    misses = [p for p in wk if not p["parlay_hit"]]
    best  = max(hits,   key=lambda p: float(p.get("payout") or 0), default=None)
    worst = max(misses, key=lambda p: float(p["predicted_prob"]),   default=None)
    return {"best": best, "worst": worst}


def get_calibration_drift(sport: str | None = None, days: int = 30,
                           threshold: float = 0.15) -> list[dict]:
    """
    Flag stat types where rolling-N-day actual hit rate diverges > threshold
    from all-time actual hit rate (signals the model needs recalibration).
    """
    data = _load()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    alltime: dict = defaultdict(lambda: {"actual": []})
    recent:  dict = defaultdict(lambda: {"actual": []})
    seen_props: set = set()
    for parlay in data["parlays"]:
        if sport and parlay.get("sport") != sport:
            continue
        is_recent = parlay["generated_at"] >= cutoff
        for leg in parlay["legs"]:
            if leg["outcome"] is None or leg["outcome"] == "void":
                continue
            prop = _prop_key(leg)
            if prop in seen_props:
                continue
            seen_props.add(prop)
            a = 1.0 if leg["outcome"] is True else 0.0
            alltime[leg["stat_type"]]["actual"].append(a)
            if is_recent:
                recent[leg["stat_type"]]["actual"].append(a)
    alerts = []
    for stat, at in alltime.items():
        n_at = len(at["actual"])
        if n_at < CAL_MIN_SAMPLES:
            continue
        at_rate = sum(at["actual"]) / n_at
        rec = recent.get(stat, {"actual": []})
        n_rec = len(rec["actual"])
        if n_rec < 5:
            continue
        rec_rate = sum(rec["actual"]) / n_rec
        drift = rec_rate - at_rate
        if abs(drift) >= threshold:
            alerts.append({
                "stat_type":        stat,
                "alltime_hit_pct":  round(at_rate * 100, 1),
                "recent_hit_pct":   round(rec_rate * 100, 1),
                "drift_pct":        round(drift * 100, 1),
                "n_recent":         n_rec,
                "direction":        "hot" if drift > 0 else "cold",
            })
    return sorted(alerts, key=lambda x: -abs(x["drift_pct"]))


def get_line_value_analysis(sport: str | None = None) -> dict:
    """
    Split resolved legs into positive-edge (predicted > implied) and
    negative-edge groups, report hit rate for each.
    """
    data = _load()
    pos: list[dict] = []
    neg: list[dict] = []
    seen_props: set = set()
    for parlay in data["parlays"]:
        if sport and parlay.get("sport") != sport:
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is None or leg["outcome"] == "void":
                continue
            prop = _prop_key(leg)
            if prop in seen_props:
                continue
            seen_props.add(prop)
            implied = _leg_implied(leg)
            pred    = leg["predicted_hit_rate"]
            edge    = pred - implied
            entry   = {"edge": edge, "hit": leg["outcome"] is True,
                       "stat": leg["stat_type"], "implied": implied, "predicted": pred}
            (pos if edge > 0 else neg).append(entry)

    def _summary(legs: list) -> dict | None:
        if not legs:
            return None
        hits = sum(1 for l in legs if l["hit"])
        return {
            "n":            len(legs),
            "hit_rate_pct": round(hits / len(legs) * 100, 1),
            "avg_edge_pct": round(sum(l["edge"] for l in legs) / len(legs) * 100, 1),
        }

    return {
        "positive_edge": _summary(pos),
        "negative_edge": _summary(neg),
    }
