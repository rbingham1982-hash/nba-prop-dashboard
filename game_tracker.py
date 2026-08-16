"""
game_tracker.py — persist the game-level market and the result it settled to.

This is the benchmark ledger for whole-game prediction. It exists before any winner
model does, on purpose: the prop side of this repo ran for months on an edge that turned
out to be four separate defects, and the reason none of them surfaced is that nothing
compared a prediction to the market it was supposedly beating. A model graded only on
accuracy cannot be falsified — home teams win 52.4% of MLB games and favourites about
57%, so almost anything scores "well".

So the ledger records, per game:
  * every market snapshot seen, not just the first — line movement is the signal that
    converges fastest, long before win rate means anything
  * the de-vigged home win probability at open and at close
  * the final score and who won

A model can then be scored against `closing_home_prob` on games it predicted BEFORE
that close, which is the only comparison that answers "is there edge".

Deliberately a separate file from parlay_log.json: that one is already 37 MB, is rewritten
whole on every save, and has a different lifecycle.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from parlay_model import canonical_abbr

LOG_PATH = Path(__file__).with_name("game_log.json")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
ESPN_PATHS = {"WNBA": "basketball/wnba", "NBA": "basketball/nba"}
# A game is gradeable once it has had time to finish. Both resolvers check a real
# "completed" flag as well, so this only avoids pointless API calls.
RESOLVE_AFTER_HOURS = 4

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
    _CACHE = {"version": 1, "games": []}
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


def _game_id(sport: str, label: str, start_time: str) -> str:
    """
    Stable identity for one fixture.

    Keyed on start time rather than the book's event id, because the book's id is not
    portable — the pocket alert had to be fixed after FanDuel, Underdog and PrizePicks
    each gave the same fixture a different one. Start time also separates the two halves
    of a doubleheader, which a team-and-date key silently merges.
    """
    raw = f"{sport}|{label}|{str(start_time)[:16]}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _parse_dt(s: str):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def log_market(df, sport: str) -> dict:
    """
    Record a market observation for every game in a fetch_moneylines() frame.

    Appends a snapshot rather than overwriting: repeated runs through the day build the
    movement history. `opening_home_prob` is fixed on first sight and `closing_home_prob`
    tracks the latest, so whichever run happens to be last still leaves an honest close.
    """
    if df is None or getattr(df, "empty", True):
        return {"new": 0, "snapshots": 0}
    data = _load()
    by_id = {g["id"]: g for g in data["games"]}
    now = datetime.now().isoformat(timespec="seconds")
    new = snaps = 0

    for _, r in df.iterrows():
        gid = _game_id(sport, r["game_label"], r["start_time"])
        snap = {
            "seen_at": now,
            "sportsbook": r.get("sportsbook", "FanDuel"),
            "home_odds": int(r["home_odds"]),
            "away_odds": int(r["away_odds"]),
            "home_prob": float(r["market_home_prob"]),
            "overround": float(r["overround"]),
        }
        g = by_id.get(gid)
        if g is None:
            g = {
                "id": gid, "sport": sport,
                "game_label": r["game_label"],
                "home_team": r["home_team"], "away_team": r["away_team"],
                "home_name": r.get("home_name", ""), "away_name": r.get("away_name", ""),
                "start_time": r["start_time"],
                "first_seen": now,
                "opening_home_prob": float(r["market_home_prob"]),
                "closing_home_prob": float(r["market_home_prob"]),
                "market": [], "home_score": None, "away_score": None,
                "home_won": None, "resolved_at": None,
            }
            data["games"].append(g)
            by_id[gid] = g
            new += 1
        g["market"].append(snap)
        g["closing_home_prob"] = float(r["market_home_prob"])
        snaps += 1

    _save(data)
    return {"new": new, "snapshots": snaps}


def log_predictions(preds: list, sport: str) -> int:
    """
    Attach model win probabilities to games already in the ledger.

    Written next to the market rather than instead of it, and only onto games that were
    logged BEFORE the prediction was made, so the eventual comparison is model-vs-close
    on the same fixture. `model_first_seen` freezes the first prediction: later runs
    refresh `model_home_prob` as ratings move, but the original stays readable so a
    prediction cannot quietly drift toward the market it is meant to be tested against.
    """
    if not preds:
        return 0
    data = _load()
    by_label = {(g["sport"], g["game_label"]): g for g in data["games"]}
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for p in preds:
        g = by_label.get((sport, p.get("game_label", "")))
        if g is None:
            continue
        if g.get("model_first_seen") is None:
            g["model_first_seen"] = now
            g["model_opening_prob"] = float(p["model_home_prob"])
        g["model_home_prob"] = float(p["model_home_prob"])
        g["model_seen_at"] = now
        n += 1
    if n:
        _save(data)
    return n


def model_vs_market(sport: str | None = None) -> dict:
    """
    The verdict that matters, once there is enough of it: model against the de-vigged
    closing line on resolved games.

    Reports both log losses and the count of disagreements the model got right. A model
    that agrees with the market everywhere has no edge to measure; one that disagrees and
    is wrong has negative edge. Only disagreeing AND being right counts.
    """
    import math
    data = _load()
    rows = [g for g in data["games"]
            if g.get("home_won") is not None
            and g.get("model_home_prob") is not None
            and g.get("closing_home_prob") is not None
            and (not sport or g.get("sport") == sport)]
    if not rows:
        return {"n": 0, "sport": sport or "ALL"}
    eps = 1e-12

    def ll(key):
        return -sum(math.log(max(g[key] if g["home_won"] else 1 - g[key], eps))
                    for g in rows) / len(rows)

    dis = [g for g in rows
           if (g["model_home_prob"] >= 0.5) != (g["closing_home_prob"] >= 0.5)]
    return {
        "n": len(rows), "sport": sport or "ALL",
        "model_log_loss": round(ll("model_home_prob"), 4),
        "market_log_loss": round(ll("closing_home_prob"), 4),
        "edge": round(ll("closing_home_prob") - ll("model_home_prob"), 4),  # +ve = model better
        "model_accuracy": round(sum(1 for g in rows
                                    if (g["model_home_prob"] >= 0.5) == g["home_won"]) / len(rows), 4),
        "market_accuracy": round(sum(1 for g in rows
                                     if (g["closing_home_prob"] >= 0.5) == g["home_won"]) / len(rows), 4),
        "disagreements": len(dis),
        "disagreements_model_right": sum(1 for g in dis
                                         if (g["model_home_prob"] >= 0.5) == g["home_won"]),
    }


def _pending(data: dict, sport: str) -> list:
    """Unresolved games whose start is far enough in the past to have finished."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RESOLVE_AFTER_HOURS)
    out = []
    for g in data["games"]:
        if g.get("sport") != sport or g.get("home_won") is not None:
            continue
        st = _parse_dt(g.get("start_time", ""))
        if st is None or st <= cutoff:
            out.append(g)
    return out


def resolve_mlb() -> int:
    """Settle pending MLB games from the MLB Stats API schedule."""
    import statsapi
    data = _load()
    pending = _pending(data, "MLB")
    if not pending:
        return 0

    wanted = defaultdict(list)
    for g in pending:
        st = _parse_dt(g.get("start_time", ""))
        if st is None:
            continue
        # ±1 day covers a game whose UTC start falls on the next calendar date.
        for off in (-1, 0, 1):
            wanted[(st + timedelta(days=off)).strftime("%m/%d/%Y")].append(g)

    resolved = 0
    for date_str, games in wanted.items():
        try:
            sched = statsapi.schedule(date=date_str, sportId=1)
        except Exception:
            continue
        for g in games:
            if g.get("home_won") is not None:
                continue
            st = _parse_dt(g.get("start_time", ""))
            best, best_gap = None, None
            for s in sched:
                if s.get("status") != "Final":
                    continue
                s_dt = _parse_dt(s.get("game_datetime", ""))
                if s_dt is None or st is None:
                    continue
                # Match on start time, not just the teams: a doubleheader plays the same
                # fixture twice in a day and a team-and-date match would grade both halves
                # against whichever the API listed first.
                gap = abs((s_dt - st).total_seconds())
                if gap > 6 * 3600:
                    continue
                if _team_match(g, s) and (best_gap is None or gap < best_gap):
                    best, best_gap = s, gap
            if best is None:
                continue
            g["home_score"] = int(best["home_score"])
            g["away_score"] = int(best["away_score"])
            g["home_won"] = bool(g["home_score"] > g["away_score"])
            g["resolved_at"] = datetime.now().isoformat(timespec="seconds")
            g["source_game_id"] = str(best.get("game_id", ""))
            resolved += 1
    if resolved:
        _save(data)
    return resolved


def _team_match(g: dict, s: dict) -> bool:
    """Loose name match: FanDuel full names vs MLB Stats API full names."""
    def norm(x):
        return "".join(ch for ch in str(x).lower() if ch.isalnum())
    home_ok = norm(g.get("home_name")) == norm(s.get("home_name")) or \
              norm(g.get("home_name"))[-6:] in norm(s.get("home_name"))
    away_ok = norm(g.get("away_name")) == norm(s.get("away_name")) or \
              norm(g.get("away_name"))[-6:] in norm(s.get("away_name"))
    return home_ok and away_ok


def resolve_espn(sport: str) -> int:
    """Settle pending WNBA/NBA games from the ESPN scoreboard."""
    path = ESPN_PATHS.get(sport)
    if not path:
        return 0
    data = _load()
    pending = _pending(data, sport)
    if not pending:
        return 0

    dates = set()
    for g in pending:
        st = _parse_dt(g.get("start_time", ""))
        if st is None:
            continue
        for off in (-1, 0):
            dates.add((st + timedelta(days=off)).strftime("%Y%m%d"))

    board = {}
    for d in sorted(dates):
        try:
            r = requests.get(ESPN_SCOREBOARD.format(path=path), params={"dates": d}, timeout=20)
            if r.status_code != 200:
                continue
            for e in r.json().get("events", []):
                comp = e["competitions"][0]
                if not comp["status"]["type"].get("completed"):
                    continue
                sides = {c["homeAway"]: c for c in comp["competitors"]}
                if "home" not in sides or "away" not in sides:
                    continue
                # Fold ESPN's spellings into the book vocabulary the ledger is written
                # in — it says NY/LA/WSH/LV/GS for NYL/LAS/WAS/LVA/GSV, which matched
                # nothing at all until this was added.
                board[(canonical_abbr(sport, sides["away"]["team"]["abbreviation"]),
                       canonical_abbr(sport, sides["home"]["team"]["abbreviation"]),
                       str(e.get("date", ""))[:10])] = (
                    int(sides["home"].get("score") or 0), int(sides["away"].get("score") or 0))
        except Exception:
            continue

    resolved = 0
    for g in pending:
        st = _parse_dt(g.get("start_time", ""))
        if st is None:
            continue
        for off in (0, -1):
            # Normalise the stored side as well: rows logged before the abbreviation
            # fix carry the old spelling (PDX) and would otherwise never match.
            key = (canonical_abbr(g.get("sport", ""), g["away_team"]),
                   canonical_abbr(g.get("sport", ""), g["home_team"]),
                   (st + timedelta(days=off)).strftime("%Y-%m-%d"))
            if key in board:
                hs, as_ = board[key]
                g["home_score"], g["away_score"] = hs, as_
                g["home_won"] = bool(hs > as_)
                g["resolved_at"] = datetime.now().isoformat(timespec="seconds")
                resolved += 1
                break
    if resolved:
        _save(data)
    return resolved


def resolve_all() -> dict:
    """Settle every pending game across sports. Returns per-sport counts."""
    out = {}
    try:
        out["MLB"] = resolve_mlb()
    except Exception as e:
        out["MLB"] = f"error: {e}"
    for s in ("WNBA", "NBA"):
        try:
            out[s] = resolve_espn(s)
        except Exception as e:
            out[s] = f"error: {e}"
    return out


def market_calibration(sport: str | None = None) -> dict:
    """
    How well the closing market predicted, which is the number a model has to beat.

    Also reports movement — mean |close - open| — because a market that barely moves
    leaves no room for a model to be early, and one that moves a lot says the opening
    number was soft.
    """
    import math
    data = _load()
    rows = [g for g in data["games"]
            if g.get("home_won") is not None
            and (not sport or g.get("sport") == sport)
            and g.get("closing_home_prob") is not None]
    if not rows:
        return {"n": 0}
    eps = 1e-12
    n = len(rows)
    acc = sum(1 for g in rows if (g["closing_home_prob"] >= 0.5) == g["home_won"]) / n
    ll = -sum(math.log(max(g["closing_home_prob"] if g["home_won"] else 1 - g["closing_home_prob"], eps))
              for g in rows) / n
    brier = sum((g["closing_home_prob"] - (1.0 if g["home_won"] else 0.0)) ** 2 for g in rows) / n
    moved = [abs(g["closing_home_prob"] - g["opening_home_prob"]) for g in rows
             if g.get("opening_home_prob") is not None]
    return {
        "n": n, "sport": sport or "ALL",
        "market_accuracy": round(acc, 4),
        "market_log_loss": round(ll, 4),
        "market_brier": round(brier, 4),
        "home_win_rate": round(sum(1 for g in rows if g["home_won"]) / n, 4),
        "mean_abs_move": round(sum(moved) / len(moved), 4) if moved else None,
        "snapshots_per_game": round(sum(len(g.get("market", [])) for g in rows) / n, 2),
    }
