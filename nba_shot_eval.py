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
    venv/Scripts/python.exe nba_shot_eval.py 2025-26 1300    # the whole season
    venv/Scripts/python.exe nba_shot_eval.py 2026-27 400     # another season / sample
    venv/Scripts/python.exe nba_shot_eval.py --no-clock      # skip the shot-clock section

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

Shot-clock section (clock reconstructed from possessions; see clock_rows), full 2025-26
season (`2025-26 1300`), 1,230 games, 212,865 attempts, points per shot:

    zone            24-18   18-15   15-7    7-4    4-0
    Rim (0-4 ft)    1.464   1.355   1.287   1.289  1.204
    Corner 3        1.169   1.177   1.168   1.126  1.102
    Above-break 3   1.061   1.091   1.071   1.031  0.927
    Paint (non-rim) 0.934   0.893   0.902   0.863  0.810
    Mid-range       0.932   0.905   0.852   0.780  0.707
    all shots       1.212   1.113   1.085   1.012  0.920

Mid-range is a late-clock shot (20.1% of mid-range attempts come with under 7 seconds
left, against 11.7% of all shots) and the zone that falls furthest as the clock runs
down. Corner threes barely fall at all. Elite mid-range shooters (top 12 on shots with 7+
seconds left, 1.010 there) dropped to 0.837 +/- 0.040 on their late-clock mid-range: below
an average late-clock shot (0.951) and their own other late shots (1.031), though well
above everyone else's late mid-range (0.720). Exceptions on small samples:
Gilgeous-Alexander 1.17 late, Cunningham 1.07, Brunson 0.98, and DeRozan, whose late
mid-range (0.89) beats his other late shots (0.75).
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


# ── shot clock ────────────────────────────────────────────────────────────────────────────
#
# PlayByPlayV3 has no shot clock, so it is rebuilt from possession changes: it starts at 24
# when a team gains the ball (opponent's make or made last free throw, a defensive
# rebound, an opponent turnover, the period start), at 14 on an offensive rebound, tops up
# to 14 on a defensive foul or kicked ball when below, and runs with the game clock. After
# a made basket or free throw it starts _INBOUND_S later, when the inbound is touched.
# That delay was set by matching the official shot-clock DISTRIBUTION (LeagueDashTeamPtShot),
# never results: at 6 s the shares were 18.7/18.4/47.9/8.9/6.2% against the official
# 17.8/17.2/47.8/9.3/8.0%, and the eFG it was not tuned on matched too (60.6, 55.7, 54.3,
# 50.5, 46.0 against 61.0, 55.9, 54.6, 50.5, 43.9). The last four seconds stay a little
# light, so the very-late cell is the least reliable.

_INBOUND_S = 6.0
CLOCK_BINS = [(24, 18, "24-18"), (18, 15, "18-15"), (15, 7, "15-7"), (7, 4, "7-4"), (4, -1, "4-0")]
_OFFICIAL = {"24-22": "24-18", "22-18 Very Early": "24-18", "18-15 Early": "18-15",
             "15-7 Average": "15-7", "7-4 Late": "7-4", "4-0 Very Late": "4-0"}
_LATE_S = 7.0          # "late clock" for the elite split
_ELITE_N = 12


def clock_bucket(sc: float):
    for hi, lo, name in CLOCK_BINS:
        if lo < sc <= hi:
            return name
    return None


def clock_rows(p, nick: dict) -> list:
    """One row per field-goal attempt with its reconstructed shot clock, zone and shooter."""
    p = p.reset_index(drop=True).copy()
    p["t"] = p["clock"].map(_secs)
    loc = p[p["teamId"].astype(float) > 0].groupby("teamId")["location"].agg(
        lambda s: s.mode().iat[0] if len(s.mode()) else "")
    teams = list(loc.index)
    if len(teams) != 2:
        return []
    for idx in p.index[(p["actionType"] == "Rebound") & (p["teamId"].astype(float) == 0)]:
        d0 = str(p.at[idx, "description"]).lower()
        for tid in teams:
            if d0.startswith(nick.get(int(tid), "~")):
                p.at[idx, "teamId"] = tid
    other = lambda x: teams[1] if teams[0] == x else teams[0]
    out = []
    poss, start, reset, per, last_shooter = None, None, 24.0, None, None
    for i in range(len(p)):
        e = p.iloc[i]
        t, at, tid = e["t"], e["actionType"], e["teamId"]
        if e["period"] != per:
            per, poss, start, reset = e["period"], None, t, 24.0
        if t is None:
            continue
        if int(e["isFieldGoal"]) == 1:
            if poss is None:                             # first possession of the period
                poss, start, reset = tid, start if start is not None else t, 24.0
            if tid == poss and start is not None:
                sc = reset - (start - t)
                if (-1 <= sc <= 24 and t >= 3
                        and math.hypot(float(e["xLegacy"]), float(e["yLegacy"])) / 10 <= 35):
                    out.append({"zone": zone(e), "made": e["shotResult"] == "Made",
                                "value": int(e["shotValue"]), "clock": max(sc, 0.0),
                                "player": e["playerName"]})
            last_shooter = tid
            if e["shotResult"] == "Made":
                poss, start, reset = other(tid), t - _INBOUND_S, 24.0
        elif at == "Rebound" and tid in teams:
            if tid == last_shooter and poss == tid:
                start, reset = t, 14.0                   # offensive rebound: 14-second reset
            else:
                poss, start, reset = tid, t, 24.0
        elif at == "Turnover" and tid in teams:
            poss, start, reset = other(tid), t, 24.0
        elif (at == "Free Throw" and tid in teams and _last_ft(e)
              and "MISS" not in str(e["description"])):
            poss, start, reset = other(tid), t - _INBOUND_S, 24.0
        elif ((at == "Foul" and tid in teams and tid != poss and "Shooting" not in str(e["subType"])
               and "Offensive" not in str(e["subType"]))
              or (at == "Violation" and "kick" in str(e["subType"]).lower())):
            if poss is not None and start is not None and reset - (start - t) < 14:
                start, reset = t, 14.0                   # defensive foul / kicked ball
    return out


def official_clock_splits(season: str) -> dict:
    """{bucket: (share of FGA, eFG)} from stats.nba.com's own shot-clock splits."""
    from nba_api.stats.endpoints import leaguedashteamptshot
    acc: dict = {}
    for name, b in _OFFICIAL.items():
        try:
            t = leaguedashteamptshot.LeagueDashTeamPtShot(
                season=season, shot_clock_range_nullable=name, timeout=60).get_data_frames()[0]
        except Exception:
            continue
        a = acc.setdefault(b, [0.0, 0.0, 0.0])
        a[0] += float(t["FGA"].sum()); a[1] += float(t["FGM"].sum()); a[2] += float(t["FG3M"].sum())
        time.sleep(0.7)
    tot = sum(v[0] for v in acc.values()) or 1.0
    return {b: (v[0] / tot, (v[1] + 0.5 * v[2]) / v[0]) for b, v in acc.items() if v[0]}


def clock_report(files: list, season: str) -> pd.DataFrame:
    nick = _nicknames()
    rows = []
    for f in files:
        try:
            rows += clock_rows(pd.read_parquet(f), nick)
        except Exception as e:
            print(f"  skipped {pathlib.Path(f).name}: {e}")
    d = pd.DataFrame(rows)
    d["bucket"] = d["clock"].map(clock_bucket)
    d["pts"] = d["made"] * d["value"]
    print(f"\n── shot clock ──  shots with a reconstructed clock: {len(d)}")
    off = official_clock_splits(season)
    print(f"{'clock':7s} {'official share':>15s} {'rebuilt share':>14s} {'official eFG':>13s} {'rebuilt eFG':>12s}")
    for _, _, b in CLOCK_BINS:
        g = d[d["bucket"] == b]
        o = off.get(b)
        print(f"{b:7s} {(f'{100*o[0]:.1f}%' if o else '—'):>15s} {100*len(g)/len(d):13.1f}% "
              f"{(f'{100*o[1]:.1f}%' if o else '—'):>13s} {100*g['pts'].sum()/2/max(1,len(g)):11.1f}%")
    print("\npoints per shot by zone and shot clock (zone's share of that bucket's shots)")
    print(f"{'zone':16s}" + "".join(f"{b:>18s}" for _, _, b in CLOCK_BINS))
    for z in ZONES:
        cells = []
        for _, _, b in CLOCK_BINS:
            g = d[(d["zone"] == z) & (d["bucket"] == b)]
            cells.append(f"{g['pts'].mean():.3f} ({100*len(g)/max(1,(d['bucket'] == b).sum()):4.1f}%)")
        print(f"{z:16s}" + "".join(f"{c:>18s}" for c in cells))
    print(f"{'ALL SHOTS':16s}" + "".join(f"{d.loc[d['bucket'] == b, 'pts'].mean():>18.3f}" for _, _, b in CLOCK_BINS))

    # Elite mid-range shooters: chosen on shots with _LATE_S+ seconds left, measured on the
    # separate late-clock shots, so the selection cannot grade itself.
    d["late"] = d["clock"] < _LATE_S
    mr = d[d["zone"] == "Mid-range"]
    p = mr.groupby("player").agg(n_early=("late", lambda s: int((~s).sum())), n_late=("late", "sum"))
    p = p.join(mr[~mr["late"]].groupby("player")["pts"].mean().rename("early"))
    p = p.join(mr[mr["late"]].groupby("player")["pts"].mean().rename("late_mr"))
    o = d[d["late"] & (d["zone"] != "Mid-range")].groupby("player")["pts"].agg(["mean", "size"])
    p = p.join(o.rename(columns={"mean": "other_late", "size": "n_other_late"}))
    q = p[(p["n_early"] >= 100) & (p["n_late"] >= 25)]
    if len(q) <= _ELITE_N:
        print("\n(not enough mid-range shooters for the elite split yet)")
        return d
    elite = q.sort_values("early", ascending=False).head(_ELITE_N)
    rest = q.drop(elite.index)
    w = lambda g, c, n: float((g[c] * g[n]).sum() / g[n].sum())
    el = mr[mr["late"] & mr["player"].isin(elite.index)]["pts"]
    print(f"\nlate clock (<{_LATE_S:g} s), league: every shot {d.loc[d['late'], 'pts'].mean():.3f}, "
          f"mid-range {mr.loc[mr['late'], 'pts'].mean():.3f}; mid-range attempts taken late "
          f"{100*mr['late'].mean():.1f}% (all shots {100*d['late'].mean():.1f}%)")
    for lab, g in ((f"elite {_ELITE_N} (by mid-range with {_LATE_S:g}+ s)", elite), ("everyone else", rest)):
        print(f"  {lab:34s} n={len(g):2d}  early MR {w(g, 'early', 'n_early'):.3f}  late MR "
              f"{w(g, 'late_mr', 'n_late'):.3f} on {int(g['n_late'].sum())}  | their other late shots "
              f"{w(g.dropna(subset=['other_late']), 'other_late', 'n_other_late'):.3f}")
    print(f"  elite late mid-range {el.mean():.3f} +/- {el.std() / math.sqrt(len(el)):.3f}")
    print(elite[["n_early", "early", "n_late", "late_mr", "other_late"]].round(3).to_string())
    return d


def main(season: str = "2025-26", n_games: int = 600, clock: bool = True) -> pd.DataFrame:
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
    if clock:
        clock_report(files, season)
    return s


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "2025-26", int(args[1]) if len(args) > 1 else 600,
         clock="--no-clock" not in flags)
