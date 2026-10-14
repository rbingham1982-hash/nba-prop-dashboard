"""
nba_snapshot.py — league game logs saved into the repository, for hosts stats.nba.com refuses.

stats.nba.com does not answer cloud addresses (GitHub Actions, Streamlit Community Cloud), so
on the hosted dashboard every NBA/WNBA game-log call timed out and the NBA Hub, Player
Analysis and Edge Finder came up empty. The home PC can reach it, so the home board run
exports one league-wide player game log per season (LeagueGameLog, every player-game) into
nba_data/, commits it with its other files, and the hosted app reads that instead.

One LeagueGameLog call replaces hundreds of PlayerGameLog calls: player_log() rebuilds a
PlayerGameLog-shaped frame for one player from it, so callers do not change.

Files are split by calendar month and gzipped with a fixed timestamp, so a nightly export
rewrites only the current month's file and an unchanged month makes no diff at all.

    python nba_snapshot.py          # export (home PC); prints what changed
"""
from __future__ import annotations

import datetime
import gzip
import io
import os
import pathlib
import time

import pandas as pd

DIR = pathlib.Path(__file__).resolve().parent / "nba_data"
_LEAGUE = {"00": "NBA", "10": "WNBA"}
_TYPE = {"Regular Season": "RS", "Playoffs": "PO"}
_CACHE: dict = {}
_TTL = 600


def use_snapshot() -> bool:
    """Read the saved logs instead of stats.nba.com: on Streamlit Cloud, or when asked to."""
    if os.environ.get("KONJURE_NBA_SNAPSHOT"):
        return True
    return pathlib.Path(__file__).resolve().as_posix().startswith("/mount/src")


def _stem(league: str, season: str, season_type: str) -> str:
    return f"gamelog-{_LEAGUE[league]}-{season}-{_TYPE[season_type]}"


# ── reading ─────────────────────────────────────────────────────────────────

def league_log(season: str, season_type: str = "Regular Season", league: str = "00") -> pd.DataFrame:
    """Every saved player-game for a season, in LeagueGameLog's columns, oldest first."""
    files = sorted(DIR.glob(_stem(league, season, season_type) + "-*.csv.gz"))
    key = (league, season, season_type)
    stamp = tuple((f.name, f.stat().st_mtime) for f in files)
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp and time.time() - hit[1] < _TTL:
        return hit[2]
    frames = [pd.read_csv(f, dtype={"GAME_ID": str, "SEASON_ID": str}) for f in files]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        df = df.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    _CACHE[key] = (stamp, time.time(), df)
    return df


def player_log(player_id, season: str, season_type: str = "Regular Season",
               league: str = "00") -> pd.DataFrame:
    """One player's games shaped like PlayerGameLog: Player_ID/Game_ID, "Oct 22, 2025" dates, newest first."""
    df = league_log(season, season_type, league)
    if df.empty:
        return df
    d = df[df["PLAYER_ID"].astype(str) == str(player_id)].copy()
    if d.empty:
        return pd.DataFrame()
    d["GAME_DATE"] = pd.to_datetime(d["GAME_DATE"]).dt.strftime("%b %d, %Y")
    for c in ("FG_PCT", "FG3_PCT", "FT_PCT"):        # PlayerGameLog reports 0-for-0 as 0, not blank
        if c in d.columns:
            d[c] = d[c].fillna(0.0)
    d = d.rename(columns={"PLAYER_ID": "Player_ID", "GAME_ID": "Game_ID"})
    d = d.drop(columns=[c for c in ("PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME",
                                    "FANTASY_PTS") if c in d.columns])
    return d.iloc[::-1].reset_index(drop=True)


def name_ids(league: str = "00") -> dict:
    """name.lower() -> player id for everyone in the saved logs (stands in for CommonAllPlayers)."""
    out = {}
    for f in sorted(DIR.glob(f"gamelog-{_LEAGUE[league]}-*.csv.gz")):   # later seasons overwrite
        df = pd.read_csv(f, usecols=["PLAYER_ID", "PLAYER_NAME"]).drop_duplicates()
        out.update({str(n).lower(): int(i) for i, n in zip(df["PLAYER_ID"], df["PLAYER_NAME"])})
    return out


def frame(name: str) -> pd.DataFrame:
    """A saved frame by name (see record()); empty if it was never saved."""
    path = DIR / f"{name}.csv.gz"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def record(name: str, df) -> None:
    """While export() runs, save a fetched frame so the hosted app can read it back with frame()."""
    if _RECORDING and df is not None and not df.empty:
        _write(DIR / f"{name}.csv.gz", df, _recorded)


# ── writing (home PC) ───────────────────────────────────────────────────────

_RECORDING = False      # export() turns this on so other modules' fetches are saved as they run
_recorded: list = []


def _write(path: pathlib.Path, df, changed: list) -> None:
    blob = _gz(df.to_csv(index=False, lineterminator="\n"))
    if path.exists() and path.read_bytes() == blob:      # unchanged rows: no rewrite, no diff
        return
    path.write_bytes(blob)
    changed.append(path.name)


def _targets(today=None):
    import parlay_model as pm
    d = today or datetime.date.today()
    cur, prev = pm.nba_season_strings(d)
    y = int(prev[:4]) - 1
    older = f"{y}-{str(y + 1)[-2:]}"   # nba_grades scores a season against the one before it
    out = [("00", s, t) for s in (cur, prev, older) for t in ("Regular Season", "Playoffs")]
    # The dashboard reads WNBA 2025 with a 2024 fallback; keep the current year too.
    out += [("10", str(y), "Regular Season") for y in (d.year, d.year - 1, d.year - 2)]
    return out


def _gz(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:   # fixed mtime: same rows, same bytes
        g.write(text.encode("utf-8"))
    return buf.getvalue()


def export(today=None) -> list[str]:
    """Fetch each target season from stats.nba.com and write changed month files. Returns their names."""
    from nba_api.stats.endpoints import leaguegamelog
    DIR.mkdir(exist_ok=True)
    changed = []
    for league, season, s_type in _targets(today):
        df = None
        for attempt in range(3):
            try:
                df = leaguegamelog.LeagueGameLog(season=season, league_id=league,
                                                 player_or_team_abbreviation="P",
                                                 season_type_all_star=s_type,
                                                 timeout=60).get_data_frames()[0]
                break
            except Exception as e:
                if attempt == 2:
                    print(f"skip {_LEAGUE[league]} {season} {s_type}: {type(e).__name__}")
                time.sleep(4.0 * (attempt + 1))
        if df is None or df.empty:
            continue
        df = df.sort_values(["GAME_DATE", "GAME_ID", "PLAYER_ID"]).reset_index(drop=True)
        for month, part in df.groupby(df["GAME_DATE"].str[:7]):
            _write(DIR / f"{_stem(league, season, s_type)}-{month}.csv.gz", part, changed)
        time.sleep(1.0)
    # The NBA draft board's inputs (LeagueDashPlayerStats, PlayerIndex): build both boards
    # with recording on, so whatever seasons they read are saved under their own names.
    global _RECORDING
    _RECORDING, _recorded[:] = True, []
    try:
        import fantasy_rankings as fr
        for build in (fr.get_9cat_rankings, fr.get_9cat_projections):
            try:
                build()
            except Exception as e:
                print(f"skip draft board {build.__name__}: {type(e).__name__}")
    finally:
        _RECORDING = False
    return changed + _recorded


if __name__ == "__main__":
    # Run through the imported module, not __main__: fantasy_rankings imports nba_snapshot,
    # and its record() calls must see the _RECORDING flag that export() sets.
    import nba_snapshot
    names = nba_snapshot.export()
    print(f"{len(names)} file(s) updated" + (": " + ", ".join(names) if names else ""))
