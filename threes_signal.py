"""
threes_signal.py — a live, pre-registered test: does the NBA moneyline overreact to
three-point luck? Back the team that has been COLDER from three when the gap is large.

Why
---
Three-point variance moves results more than anything else in the box score: over
2023-24 to 2025-26 a team won 63% of the games where it shot above its own usual 3P% and
37% of the rest, and a game's swing in made threes tracked its swing in points at 0.53.
But a hot stretch is luck: across 2,310 team-games of 2025-26, a team's three-point excess
over its previous five games predicted its next game's excess at corr +0.033 (SE 0.021).
If prices still lean on recent shooting, the side that has been cold is underpriced.

THE RULE IS FIXED (2026-10-11, before the season). Do not tune it on its own results.

  * luck(team) = (3PM - 3PA x baseline) / 5 over the team's last five regular-season games,
    where baseline is its 3P% over everything before that window (last season + this
    season), so the window never grades itself. Needs five games this season.
  * gap = luck(home) - luck(away). A game qualifies when |gap| >= 2.5 made threes per game —
    fixed from FREQUENCY alone (about 20% of 2025-26 games cleared it), never from results.
  * Bet one unit on the colder team's moneyline at its LAST FanDuel price before tip-off;
    the first price seen is kept as a secondary record.
  * Regular season and playoffs; graded from ESPN's final score.

File: threes_log-NBA.json (cloud runner since 2026-10-18; team totals come from the saved
league log there, since stats.nba.com does not answer the cloud).
"""
from __future__ import annotations

import datetime
import json
import pathlib

import favorite_signal as _fav

_DIR = pathlib.Path(__file__).parent
GAP = 2.5
WINDOW = 5
_PATH = _DIR / "threes_log-NBA.json"
# stats.nba.com and FanDuel spell one franchise differently.
_ALIAS = {"la clippers": "los angeles clippers"}


def _load() -> dict:
    if not _PATH.exists():
        return {}
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    _PATH.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def _team(name: str) -> str:
    n = _fav._norm(name)
    return _ALIAS.get(n, n)


def _team_logs() -> dict:
    """{team: [(date, season, fg3m, fg3a), ...]} for last season and this one, oldest first."""
    import pandas as pd
    import parlay_model as pm
    import nba_snapshot as ns
    from nba_api.stats.endpoints import leaguegamelog
    frames = []
    for season in reversed(pm.nba_season_strings()):
        for st in ("Regular Season", "Playoffs"):
            try:
                if ns.use_snapshot():
                    # Cloud runner: team totals are the sum of the saved player rows.
                    p = ns.league_log(season, st)
                    if p.empty:
                        continue
                    g = (p.groupby(["GAME_ID", "GAME_DATE", "TEAM_NAME"], as_index=False)
                          [["FG3M", "FG3A"]].sum())
                else:
                    g = leaguegamelog.LeagueGameLog(season=season, player_or_team_abbreviation="T",
                                                    season_type_all_star=st,
                                                    timeout=60).get_data_frames()[0]
            except Exception:
                continue
            if g.empty:
                continue
            g["season"], g["stype"] = season, st
            frames.append(g)
    if not frames:
        return {}
    df = pd.concat(frames)
    df["date"] = pd.to_datetime(df["GAME_DATE"]).dt.date
    out: dict = {}
    for _, r in df.sort_values("date").iterrows():
        out.setdefault(_team(r["TEAM_NAME"]), []).append(
            (r["date"], r["season"], r["stype"], float(r["FG3M"]), float(r["FG3A"])))
    return out


def luck(rows: list, day, current: str):
    """Made threes per game above the team's baseline over its last WINDOW games before day."""
    before = [r for r in rows if r[0] < day]
    this = [r for r in before if r[1] == current and r[2] == "Regular Season"]
    if len(this) < WINDOW:
        return None
    win, base_rows = before[-WINDOW:], before[:-WINDOW]
    att = sum(r[4] for r in base_rows)
    if att <= 0:
        return None
    base = sum(r[3] for r in base_rows) / att
    return (sum(r[3] for r in win) - sum(r[4] for r in win) * base) / WINDOW


def log() -> int:
    """Record or refresh every not-yet-started NBA game that qualifies. Returns rows written."""
    import parlay_model as pm
    import daily_parlay_gen as dg
    df = pm.fetch_moneylines("nba")
    if df is None or df.empty:
        return 0
    data = _load()
    teams = None
    current = pm.nba_season_strings()[0]
    now = datetime.datetime.now().isoformat(timespec="seconds")
    n = 0
    for _, r in df.iterrows():
        if dg._has_started(r.get("start_time")):
            continue
        key = f"{r['game_label']}|{str(r['start_time'])[:16]}"
        row = data.get(key)
        price = {"home_odds": int(r["home_odds"]), "away_odds": int(r["away_odds"]),
                 "home_prob": float(r["market_home_prob"])}
        if row is None:
            if teams is None:
                teams = _team_logs()
            try:
                st = datetime.datetime.fromisoformat(str(r["start_time"]).replace("Z", "+00:00"))
            except Exception:
                continue
            day = (st - datetime.timedelta(hours=8)).date()
            lh = luck(teams.get(_team(r["home_name"]), []), day, current)
            la = luck(teams.get(_team(r["away_name"]), []), day, current)
            if lh is None or la is None or abs(lh - la) < GAP:
                continue
            row = {"label": r["game_label"], "home_name": r["home_name"], "away_name": r["away_name"],
                   "start_time": str(r["start_time"]), "luck_home": round(lh, 2), "luck_away": round(la, 2),
                   "bet": "home" if lh < la else "away", "first_seen": now, "first": price,
                   "outcome": None, "winner": None}
            data[key] = row
        if row.get("outcome") is not None:
            continue
        row["last"], row["last_seen"] = price, now
        n += 1
    if n:
        _save(data)
    return n


def grade() -> int:
    """Settle finished games from ESPN, with favorite_signal's matcher. Returns rows graded."""
    data = _load()
    pending = {k: v for k, v in data.items() if v.get("outcome") is None}
    if not pending:
        return 0
    # Same matching and preseason handling as favorite_signal: run its grader on a copy.
    copy = {k: dict(v) for k, v in pending.items()}
    loaded, saved = _fav._load, _fav._save
    try:
        _fav._load = lambda sport: copy
        _fav._save = lambda sport, d: None
        n = _fav.grade("nba")
    finally:
        _fav._load, _fav._save = loaded, saved
    for k, v in copy.items():
        data[k].update({"outcome": v.get("outcome"), "winner": v.get("winner")})
    if n:
        _save(data)
    return n


def run() -> dict:
    return {"graded": grade(), "tracked": log()}


def record() -> dict:
    rows = [r for r in _load().values() if r.get("outcome") == "graded" and r.get("last")]
    last, first = [], []
    for r in rows:
        won = r["winner"] == r["bet"]
        last.append((r["last"][f"{r['bet']}_odds"], won))
        first.append((r["first"][f"{r['bet']}_odds"], won))
    return {"at_last_price": _fav._summary(last), "at_first_price": _fav._summary(first),
            "pending": sum(1 for r in _load().values() if r.get("outcome") is None)}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(run(), record())
