"""
nba_shot_eval.py — what a shot is worth by zone once you count what happens after it.

Points per shot from zone data counts the make and nothing else. This rebuilds every
possession from stats.nba.com play-by-play (PlayByPlayV3) and scores each field-goal attempt
by team A as

    own  = A's points on that possession: the shot if made (plus any and-one), or the
           second-chance points after an offensive rebound;
    opp  = B's points on the NEXT possession (after A's make, or after B's rebound);
    net  = own - opp,

with offensive-rebound rates, second-chance points per miss, the opponent's points after a
defensive rebound, and how often that rebound turns into a shot inside seven seconds.

    venv/Scripts/python.exe nba_shot_eval.py                 # 2025-26, 600 games
    venv/Scripts/python.exe nba_shot_eval.py 2026-27 400     # another season / sample

Play-by-play is cached in pbp_cache/<season>/ (git-ignored), so a rerun only downloads
games it has not seen. stats.nba.com does not answer cloud runners: run it at home.

First run, 2025-26, 599 games, 103,425 attempts (seed 7):

    zone             FG%   pts/shot  OREB%  opp after DREB  transition   NET
    Rim (0-4 ft)     67.5   1.349    37.7      1.237          43.5%     +0.403 +/- 0.009
    Corner 3         39.0   1.171    26.6      1.191          29.5%     +0.228 +/- 0.018
    Above-break 3    35.7   1.071    25.6      1.178          25.8%     +0.121 +/- 0.011
    Paint (non-rim)  44.5   0.889    31.7      1.170          36.4%     -0.005 +/- 0.011
    Mid-range        42.0   0.840    24.7      1.197          30.3%     -0.118 +/- 0.016

    opponent scores 1.131 after a made shot, 1.189 after a defensive rebound.

What the misses change: nothing about the ranking. Mid-range gets no help from its misses
(lowest OREB%), and its gap to an above-break three is about the same counted either way
(-0.23 on makes, -0.24 net). Rim misses are the most valuable misses (0.49 second-chance
points each) and the most dangerous (43.5% become opponent transition), which roughly
cancels. The one zone that moves is the short paint, about +0.06 a shot better than its
FG% says, from offensive rebounds and a quiet opponent possession after. Long threes do
NOT feed the most fast breaks: above-break misses produce the fewest (25.8%).

Not captured: a shooting foul on a MISSED shot is not logged as a field-goal attempt, so
those free-throw trips are invisible here (they mostly belong to rim attacks, whose real
value is therefore higher). Shot selection is not controlled either: this is the value of
shots as teams actually take them.
"""
from __future__ import annotations

import math
import pathlib
import random
import re
import sys
import time

import numpy as np
import pandas as pd

ZONES = ["Rim (0-4 ft)", "Paint (non-rim)", "Mid-range", "Corner 3", "Above-break 3"]
_CACHE = pathlib.Path(__file__).with_name("pbp_cache")
_SEED = 7
_TRANSITION_S = 7.0


# ── download ──────────────────────────────────────────────────────────────────────────────

def fetch(season: str, n_games: int) -> list:
    """Cache play-by-play for a reproducible spread of the season's games; return the files."""
    from nba_api.stats.endpoints import leaguegamelog, playbyplayv3
    out = _CACHE / season
    out.mkdir(parents=True, exist_ok=True)
    g = leaguegamelog.LeagueGameLog(season=season, player_or_team_abbreviation="T",
                                    season_type_all_star="Regular Season", timeout=60).get_data_frames()[0]
    ids = sorted(g["GAME_ID"].unique())
    random.Random(_SEED).shuffle(ids)
    files = []
    for i, gid in enumerate(ids[:n_games]):
        f = out / f"{gid}.parquet"
        if not f.exists():
            for attempt in range(3):
                try:
                    playbyplayv3.PlayByPlayV3(game_id=gid, timeout=45).get_data_frames()[0].to_parquet(f, index=False)
                    break
                except Exception:
                    time.sleep(4 * (attempt + 1))
            time.sleep(0.7)
        if f.exists():
            files.append(f)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{min(n_games, len(ids))} games", flush=True)
    return files


# ── possessions ───────────────────────────────────────────────────────────────────────────

def _secs(clock):
    m = re.match(r"PT(\d+)M([\d.]+)S", str(clock))
    return int(m.group(1)) * 60 + float(m.group(2)) if m else None


def zone(r) -> str:
    """Zone from the shot's court position (tenths of a foot, hoop at the origin)."""
    x, y = float(r["xLegacy"]), float(r["yLegacy"])
    if int(r["shotValue"] or 0) == 3:
        return "Corner 3" if (y <= 92 and abs(x) >= 220) else "Above-break 3"
    dist = math.hypot(x, y) / 10.0
    if dist <= 4:
        return "Rim (0-4 ft)"
    if abs(x) <= 80 and y <= 142.5:
        return "Paint (non-rim)"
    return "Mid-range"


def _nicknames() -> dict:
    from nba_api.stats.static import teams
    return {t["id"]: t["nickname"].lower() for t in teams.get_teams()}


def _last_ft(e) -> bool:
    m = re.search(r"(\d) of (\d)", str(e["subType"]))
    return bool(m) and m.group(1) == m.group(2)


def _window(p, k, O, D, per, n):
    """Walk O's possession from index k. Returns (end_index_exclusive, how): 'dreb' (D
    rebounded at the end index), 'score' (O's make, or a made last free throw), 'to'
    (turnover), 'dshot' (D shot with no hand-over), or (None, None) at the period end."""
    while k < n and p.at[k, "period"] == per:
        tid, at = p.at[k, "teamId"], p.at[k, "actionType"]
        if at == "Rebound" and tid == D:
            return k, "dreb"
        if tid == D and int(p.at[k, "isFieldGoal"]) == 1:
            return k, "dshot"
        if at == "Turnover" and tid in (O, D):
            return k + 1, "to"
        if tid == O and at == "Made Shot":
            t = p.at[k, "t"]
            k += 1
            while (k < n and p.at[k, "t"] == t
                   and p.at[k, "actionType"] not in ("Made Shot", "Missed Shot", "Turnover", "Rebound")):
                k += 1                               # the and-one free throw, at the same clock
            return k, "score"
        if (tid == O and at == "Free Throw" and _last_ft(p.iloc[k])
                and "MISS" not in str(p.at[k, "description"])):
            return k + 1, "score"
        k += 1
    return None, None


def game_rows(p, nick: dict) -> list:
    """One row per field-goal attempt in a game: zone, make, rebounds, own/opp points."""
    p = p.reset_index(drop=True).copy()
    p["t"] = p["clock"].map(_secs)
    for c in ("scoreHome", "scoreAway"):
        p[c] = pd.to_numeric(p[c].replace("", np.nan), errors="coerce")
    p[["scoreHome", "scoreAway"]] = p[["scoreHome", "scoreAway"]].ffill().fillna(0)
    loc = p[p["teamId"].astype(float) > 0].groupby("teamId")["location"].agg(
        lambda s: s.mode().iat[0] if len(s.mode()) else "")
    home = {tid for tid, l in loc.items() if l == "h"}
    teams = list(loc.index)
    if len(teams) != 2 or len(home) != 1:
        return []
    # Team rebounds carry teamId 0 and the nickname in the description.
    for idx in p.index[(p["actionType"] == "Rebound") & (p["teamId"].astype(float) == 0)]:
        d0 = str(p.at[idx, "description"]).lower()
        for tid in teams:
            if d0.startswith(nick.get(int(tid), "~")):
                p.at[idx, "teamId"] = tid
    sc = lambda i, tid: (p.at[i, "scoreHome"] if tid in home else p.at[i, "scoreAway"]) if i >= 0 else 0
    n, out = len(p), []
    for i in np.where(p["isFieldGoal"].astype(int) == 1)[0]:
        r = p.iloc[i]
        A, per = r["teamId"], r["period"]
        # End-of-period heaves and shots from beyond 35 feet are not shot choices.
        if r["t"] is None or r["t"] < 3 or math.hypot(float(r["xLegacy"]), float(r["yLegacy"])) / 10 > 35:
            continue
        B = teams[1] if teams[0] == A else teams[0]
        made = r["shotResult"] == "Made"
        start_a = sc(i - 1, A)
        j, how = _window(p, i, A, B, per, n)
        if j is None or how == "dshot":
            continue                                 # period end, or no clean hand-over
        own = sc(j - 1, A) - start_a
        oreb = (not made) and any(p.at[x, "actionType"] == "Rebound" and p.at[x, "teamId"] == A
                                  for x in range(i + 1, j))
        dreb = (not made) and how == "dreb" and not oreb
        t0 = p.at[j, "t"] if j < n else None
        b0 = j + 1 if how == "dreb" else j
        start_b = sc(b0 - 1, B)
        k, _how_b = _window(p, b0, B, A, per, n)
        if k is None:
            continue
        opp = sc(k - 1, B) - start_b
        first = next((p.at[x, "t"] for x in range(b0, k)
                      if p.at[x, "teamId"] == B and int(p.at[x, "isFieldGoal"]) == 1), None)
        trans = dreb and first is not None and t0 is not None and (t0 - first) <= _TRANSITION_S
        out.append({"zone": zone(r), "made": made, "value": int(r["shotValue"]), "oreb": oreb,
                    "dreb": dreb, "own": own, "opp": opp, "transition": trans})
    return out


# ── report ────────────────────────────────────────────────────────────────────────────────

def evaluate(files: list) -> pd.DataFrame:
    nick = _nicknames()
    rows = []
    for f in files:
        try:
            rows += game_rows(pd.read_parquet(f), nick)
        except Exception as e:
            print(f"  skipped {pathlib.Path(f).name}: {e}")
    return pd.DataFrame(rows)


def summarize(d: pd.DataFrame) -> pd.DataFrame:
    out = []
    for z in ZONES:
        g = d[d["zone"] == z]
        if g.empty:
            continue
        ms = g[~g["made"]]
        dr = ms[ms["dreb"]]
        net = g["own"] - g["opp"]
        out.append({"zone": z, "fga": len(g), "fg_pct": g["made"].mean(),
                    "pts_per_shot": (g["made"] * g["value"]).mean(),
                    "oreb_pct": ms["oreb"].mean(), "second_chance_per_miss": ms["own"].mean(),
                    "opp_after_dreb": dr["opp"].mean(), "transition_pct": dr["transition"].mean(),
                    "own": g["own"].mean(), "opp": g["opp"].mean(),
                    "net": net.mean(), "net_se": net.std() / math.sqrt(len(g))})
    return pd.DataFrame(out)


def main(season: str = "2025-26", n_games: int = 600) -> pd.DataFrame:
    print(f"{season}: fetching up to {n_games} games (cached in {_CACHE / season})")
    files = fetch(season, n_games)
    d = evaluate(files)
    print(f"games: {len(files)}  field-goal attempts analysed: {len(d)}\n")
    s = summarize(d)
    print(f"{'zone':16s} {'FGA':>6s} {'FG%':>6s} {'pts/shot':>8s} | {'OREB%':>6s} {'2nd/miss':>8s} | "
          f"{'opp/DREB':>8s} {'trans%':>7s} | {'own':>6s} {'opp':>6s} {'NET':>15s}")
    for r in s.itertuples():
        print(f"{r.zone:16s} {r.fga:6d} {100*r.fg_pct:5.1f}% {r.pts_per_shot:8.3f} | "
              f"{100*r.oreb_pct:5.1f}% {r.second_chance_per_miss:8.3f} | {r.opp_after_dreb:8.3f} "
              f"{100*r.transition_pct:6.1f}% | {r.own:6.3f} {r.opp:6.3f} {r.net:+7.3f} +/- {r.net_se:.3f}")
    after_make = d.loc[d["made"], "opp"].mean()
    after_dreb = d.loc[~d["made"] & d["dreb"], "opp"].mean()
    print(f"\nopponent scores {after_make:.3f} after a made shot, {after_dreb:.3f} after a defensive "
          f"rebound (a live-ball rebound is worth {after_dreb - after_make:+.3f} to the defence)")
    return s


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    main(args[0] if args else "2025-26", int(args[1]) if len(args) > 1 else 600)
