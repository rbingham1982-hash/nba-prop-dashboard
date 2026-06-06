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
CAL_MIN_SAMPLES = 15          # minimum resolved legs per stat before calibration kicks in
CAL_MAX_FACTOR = 1.35         # clamp calibration multiplier upper bound
CAL_MIN_FACTOR = 0.05         # allow deep deflation for stats like RBI/HR that rarely hit

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


# ─────────────────────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    if LOG_PATH.exists():
        try:
            return json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": 1, "parlays": []}


def _save(data: dict) -> None:
    LOG_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


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
            "parlay_hit":       None,
            "legs":             [],
        }
        for leg in parlay["legs"]:
            entry["legs"].append({
                "player_name":       leg["player_name"],
                "stat_type":         leg["stat_type"],
                "line_score":        float(leg["line_score"]),
                "predicted_hit_rate": round(float(leg["hit_rate"]), 4),
                "american_odds":     leg.get("american_odds"),
                "game_id":           str(leg.get("game_id", "")),
                "game_label":        str(leg.get("game_label", "")),
                "start_time":        str(leg.get("start_time", "")),
                "outcome":           None,
            })
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


def _leg_is_resolvable(leg: dict, generated_at: str) -> bool:
    """True if enough time has passed that the game should be finished."""
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
        raw = str(row.get("GAME_DATE", ""))
        try:
            gd = datetime.strptime(raw.title(), "%b %d, %Y").date()
        except ValueError:
            try:
                gd = date.fromisoformat(raw[:10])
            except Exception:
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


def _mark_parlay_outcomes(data: dict) -> None:
    """
    Set parlay_hit for any parlay whose real (non-historical) legs are all resolved.
    Historical legs are excluded from both the resolution check and the hit/miss verdict.
    """
    for parlay in data["parlays"]:
        if parlay["parlay_hit"] is not None:
            continue
        real_legs = [l for l in parlay["legs"] if not _is_historical_leg(l)]
        if not real_legs:
            # All legs are historical — parlay can never be resolved; leave parlay_hit=None
            continue
        outcomes = [l["outcome"] for l in real_legs]
        if all(o is not None for o in outcomes):
            parlay["parlay_hit"] = bool(all(outcomes))


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


def _find_game_in_log(df: pd.DataFrame, target_date: date) -> pd.Series | None:
    """Find a game log row matching target_date (±1 day tolerance)."""
    if df.empty or "GAME_DATE" not in df.columns:
        return None
    for _, row in df.iterrows():
        raw = str(row["GAME_DATE"])
        try:
            # NBA API returns "MMM DD, YYYY" e.g. "MAY 18, 2026"
            gd = datetime.strptime(raw.title(), "%b %d, %Y").date()
        except ValueError:
            try:
                gd = date.fromisoformat(raw[:10])
            except Exception:
                continue
        if abs((gd - target_date).days) <= 1:
            return row
    return None


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
            player_legs[leg["player_name"]].append((parlay, leg))

    if not player_legs:
        _mark_parlay_outcomes(data)
        _save(data)
        return 0

    resolved_count = 0
    for player_name, entries in player_legs.items():
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
                row = _find_game_in_log(df, target)
            if row is None:
                continue
            try:
                actual = _stat_from_row(row, spec)
                leg["outcome"] = bool(actual > leg["line_score"])
                resolved_count += 1
            except Exception:
                continue

    _mark_parlay_outcomes(data)
    if resolved_count:
        _save(data)
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
            player_legs[leg["player_name"]].append((parlay, leg))

    if not player_legs:
        _mark_parlay_outcomes(data)
        _save(data)
        return 0

    resolved_count = 0
    for player_name, entries in player_legs.items():
        norm_name = _normalize_name(player_name)
        dates_needed: set[str] = set()
        for parlay, leg in entries:
            d = _parse_game_date(leg, parlay["generated_at"])
            if d:
                dates_needed.add(d.strftime("%m/%d/%Y"))

        for date_str in dates_needed:
            try:
                _time.sleep(0.4)
                schedule = statsapi.schedule(date=date_str, sportId=1)
            except Exception:
                continue

            for game_info in schedule:
                game_pk = game_info.get("game_id")
                if not game_pk:
                    continue
                try:
                    _time.sleep(0.4)
                    box = statsapi.boxscore_data(game_pk)
                except Exception:
                    continue

                for side in ("home", "away"):
                    team_data = box.get(side, {})
                    for player_data in team_data.get("players", {}).values():
                        full_name = player_data.get("person", {}).get("fullName", "")
                        # Normalize both sides to handle accented characters (e.g. Vásquez)
                        if norm_name not in _normalize_name(full_name):
                            continue
                        stats = player_data.get("stats", {})
                        bstats = stats.get("batting", {})
                        pstats = stats.get("pitching", {})
                        for parlay, leg in entries:
                            if leg["outcome"] is not None:
                                continue
                            stat_type = leg["stat_type"]
                            if stat_type in _MLB_PITCHER_TYPES:
                                col = _MLB_PITCHING_RESOLVE.get(stat_type)
                                source = pstats
                            else:
                                col = _MLB_BATTING_RESOLVE.get(stat_type)
                                source = bstats
                            if col is None or source is None:
                                continue
                            try:
                                # Empty dict means player didn't appear; treat stat as 0
                                actual = float(source.get(col, 0) or 0)
                                leg["outcome"] = bool(actual > leg["line_score"])
                                resolved_count += 1
                            except Exception:
                                continue

    _mark_parlay_outcomes(data)
    if resolved_count:
        _save(data)
    return resolved_count


def _resolve_wnba_legs() -> int:
    """Auto-resolve pending WNBA legs via nba_api PlayerGameLog (league 10). Returns resolved count."""
    data = _load()
    player_legs: dict[str, list] = defaultdict(list)
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
                row = _find_game_in_log(df, target)
            if row is None:
                continue
            try:
                actual = _stat_from_row(row, spec)
                leg["outcome"] = bool(actual > leg["line_score"])
                resolved_count += 1
            except Exception:
                continue

    _mark_parlay_outcomes(data)
    if resolved_count:
        _save(data)
    return resolved_count


def resolve_all_legs() -> dict:
    """Resolve NBA, MLB, and WNBA pending legs. Returns {nba: count, mlb: count, wnba: count}."""
    nba  = resolve_nba_legs()
    mlb  = _resolve_mlb_legs()
    wnba = _resolve_wnba_legs()
    return {"nba": nba, "mlb": mlb, "wnba": wnba}


# ─────────────────────────────────────────────────────────────────────────────
# Calibration
# ─────────────────────────────────────────────────────────────────────────────

def get_calibration() -> dict:
    """
    Return per-stat calibration factors from all resolved legs.
    factor > 1.0 → model underestimates hit rate; < 1.0 → overestimates.
    Only populated when a stat has >= CAL_MIN_SAMPLES resolved legs.
    """
    data = _load()
    predicted: dict[str, list] = defaultdict(list)
    actual: dict[str, list] = defaultdict(list)

    for parlay in data["parlays"]:
        for leg in parlay["legs"]:
            if leg["outcome"] is None:
                continue
            stat = leg["stat_type"]
            predicted[stat].append(leg["predicted_hit_rate"])
            actual[stat].append(1.0 if leg["outcome"] else 0.0)

    factors = {}
    for stat in predicted:
        n = len(predicted[stat])
        if n < CAL_MIN_SAMPLES:
            continue
        p_mean = sum(predicted[stat]) / n
        a_mean = sum(actual[stat]) / n
        if p_mean > 0:
            raw = a_mean / p_mean
            factors[stat] = round(max(CAL_MIN_FACTOR, min(CAL_MAX_FACTOR, raw)), 4)
    return factors


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

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
    resolved_legs = [l for l in all_legs if l["outcome"] is not None]
    hit_legs      = [l for l in resolved_legs if l["outcome"]]

    stat_data: dict = defaultdict(lambda: {"predicted": [], "actual": []})
    for leg in resolved_legs:
        s = leg["stat_type"]
        stat_data[s]["predicted"].append(leg["predicted_hit_rate"])
        stat_data[s]["actual"].append(1.0 if leg["outcome"] else 0.0)

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

    return {
        "week":              week,
        "total_parlays":     len(week_parlays),
        "resolved_parlays":  len(resolved_parlays),
        "parlay_hit_rate":   round(len(hit_parlays) / len(resolved_parlays), 3) if resolved_parlays else None,
        "avg_predicted_prob": round(avg_predicted_prob, 3) if avg_predicted_prob is not None else None,
        "total_legs":        len(all_legs),
        "resolved_legs":     len(resolved_legs),
        "leg_hit_rate":      round(len(hit_legs) / len(resolved_legs), 3) if resolved_legs else None,
        "stat_breakdown":    stat_breakdown,
        "sportsbook_breakdown": {sb: d for sb, d in sb_data.items()},
    }


def get_all_time_calibration_table(sport: str | None = None) -> list:
    """Return a list of dicts for the calibration summary table.
    Pass sport='NBA' or sport='MLB' to filter."""
    data = _load()
    predicted: dict[str, list] = defaultdict(list)
    actual: dict[str, list] = defaultdict(list)

    for parlay in data["parlays"]:
        if sport and parlay.get("sport") != sport:
            continue
        for leg in parlay["legs"]:
            if leg["outcome"] is None:
                continue
            stat = leg["stat_type"]
            predicted[stat].append(leg["predicted_hit_rate"])
            actual[stat].append(1.0 if leg["outcome"] else 0.0)

    rows = []
    for stat in sorted(predicted):
        n = len(predicted[stat])
        p_mean = sum(predicted[stat]) / n if n else 0
        a_mean = sum(actual[stat]) / n if n else 0
        factor = None
        if n >= CAL_MIN_SAMPLES and p_mean > 0:
            raw = a_mean / p_mean
            factor = round(max(CAL_MIN_FACTOR, min(CAL_MAX_FACTOR, raw)), 3)
        rows.append({
            "stat_type":          stat,
            "samples":            n,
            "predicted_hit_rate": round(p_mean, 3),
            "actual_hit_rate":    round(a_mean, 3),
            "calibration_factor": factor,
            "active":             factor is not None,
        })
    return rows


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
