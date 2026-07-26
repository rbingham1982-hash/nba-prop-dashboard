"""
Season-long 9-category fantasy value rankings (draft board).

Two modes:
  * get_9cat_rankings(season)      — value from a completed season's actual per-game production.
  * get_9cat_projections(target)   — projected value for an upcoming season: recency-weighted
                                     multi-season blend + aging curve on current rosters, plus
                                     rookies projected from draft slot.

Value = sum of per-category z-scores over the fantasy pool (counting cats plain, turnovers
negative, FG%/FT% volume-weighted). Honest limit on the projection: minutes are carried from
recent production, not re-allocated from a depth-chart model — the largest simplification vs a
pro projection system.
"""
from __future__ import annotations
import time

CATS = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "FG_PCT", "FT_PCT", "TOV"]
NEG_CATS = {"TOV"}
# Per-game counting stats the aging curve scales (percentages age gently, handled separately).
COUNTING = ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV", "MIN", "FGA", "FTA", "FGM", "FTM"]


def _prev_season(season: str, back: int = 1) -> str:
    y = int(season[:4]) - back
    return f"{y}-{str(y + 1)[-2:]}"


def _fetch_stats(season: str):
    from nba_api.stats.endpoints import leaguedashplayerstats as L
    time.sleep(0.3)
    return L.LeagueDashPlayerStats(
        season=season, per_mode_detailed="PerGame",
        season_type_all_star="Regular Season", timeout=25,
    ).get_data_frames()[0]


def _fetch_index(season: str):
    """PlayerIndex for `season`: current rosters, positions, draft slot."""
    from nba_api.stats.endpoints import playerindex
    time.sleep(0.3)
    df = playerindex.PlayerIndex(season=season, timeout=25).get_data_frames()[0]
    pid = "PERSON_ID" if "PERSON_ID" in df.columns else "PLAYER_ID"
    df = df.rename(columns={pid: "PLAYER_ID"})
    df["NAME"] = (df["PLAYER_FIRST_NAME"].astype(str) + " " + df["PLAYER_LAST_NAME"].astype(str)).str.strip()
    return df


def _age_mult(age: float) -> float:
    """Aging multiplier for counting production (age = the player's age in the target season)."""
    if age <= 0:   return 1.00
    if age <= 23:  return 1.04
    if age <= 24:  return 1.02
    if age <= 28:  return 1.00
    if age == 29:  return 0.99
    if age == 30:  return 0.98
    if age == 31:  return 0.96
    if age == 32:  return 0.94
    if age == 33:  return 0.91
    if age == 34:  return 0.88
    if age == 35:  return 0.84
    return 0.78


def _rank_and_tier(df, pool_size: int, notes: dict | None = None,
                   positions: dict | None = None) -> dict:
    """
    Shared 9-cat valuation. `df` must carry per-game columns PTS/REB/AST/STL/BLK/FG3M/
    FG_PCT/FT_PCT/TOV plus FGM/FGA/FTM/FTA, GP, MIN, PLAYER_ID, PLAYER_NAME, TEAM_ABBREVIATION.
    """
    df = df.sort_values("MIN", ascending=False).head(pool_size).reset_index(drop=True)
    n = len(df)
    if n == 0:
        return {"n_pool": 0, "players": []}
    lg_fg = df["FGM"].sum() / df["FGA"].sum() if df["FGA"].sum() else 0.0
    lg_ft = df["FTM"].sum() / df["FTA"].sum() if df["FTA"].sum() else 0.0
    df["FG_IMPACT"] = (df["FG_PCT"] - lg_fg) * df["FGA"]
    df["FT_IMPACT"] = (df["FT_PCT"] - lg_ft) * df["FTA"]
    zsrc = {"PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK",
            "FG3M": "FG3M", "TOV": "TOV", "FG_PCT": "FG_IMPACT", "FT_PCT": "FT_IMPACT"}
    z = {}
    for cat, col in zsrc.items():
        mean = df[col].mean()
        std = df[col].std(ddof=0) or 1.0
        zc = (df[col] - mean) / std
        if cat in NEG_CATS:
            zc = -zc
        z[cat] = zc
    df["TOTAL"] = sum(z[c] for c in CATS)
    for c in CATS:
        df[f"z{c}"] = z[c]
    df = df.sort_values("TOTAL", ascending=False).reset_index(drop=True)

    tiers, tier = [], 1
    totals = df["TOTAL"].tolist()
    for i, t in enumerate(totals):
        if i > 0 and (totals[i - 1] - t) >= 0.6 and tier < 10:
            tier += 1
        tiers.append(tier)

    notes = notes or {}
    positions = positions or {}
    players = []
    for i, r in df.iterrows():
        pid = int(r["PLAYER_ID"])
        players.append({
            "rank": i + 1, "tier": tiers[i], "player_id": pid, "name": r["PLAYER_NAME"],
            "team": r["TEAM_ABBREVIATION"], "pos": positions.get(pid, r.get("POS", "")),
            "note": notes.get(pid, ""),
            "gp": int(r["GP"]), "mpg": round(float(r["MIN"]), 1),
            "pts": round(float(r["PTS"]), 1), "reb": round(float(r["REB"]), 1),
            "ast": round(float(r["AST"]), 1), "stl": round(float(r["STL"]), 1),
            "blk": round(float(r["BLK"]), 1), "fg3m": round(float(r["FG3M"]), 1),
            "fg_pct": round(float(r["FG_PCT"]), 3), "ft_pct": round(float(r["FT_PCT"]), 3),
            "tov": round(float(r["TOV"]), 1), "total": round(float(r["TOTAL"]), 2),
            **{f"z{c}": round(float(r[f"z{c}"]), 2) for c in CATS},
        })
    return {"n_pool": n, "players": players}


def get_9cat_rankings(season: str = "2025-26", min_gp: int = 25, min_mpg: float = 18.0,
                      pool_size: int = 180) -> dict:
    """9-cat value from a completed season's actual per-game production."""
    df = _fetch_stats(season)
    df = df[(df["GP"] >= min_gp) & (df["MIN"] >= min_mpg)].copy()
    positions = {int(r["PLAYER_ID"]): str(r.get("POSITION", "") or "")
                 for _, r in _fetch_index(season).iterrows()}
    out = _rank_and_tier(df, pool_size, positions=positions)
    out["season"] = season
    out["mode"] = "actuals"
    return out


def _rookie_lines_by_tier(prev_season: str, idx_prev):
    """
    Mean per-game line of last year's rookie class, bucketed by draft pick tier — the
    data-grounded basis for projecting incoming rookies. Rough by nature (one class, high
    variance), so it's a tiered average rather than a per-pick curve.
    """
    import pandas as pd
    rk_year = int(prev_season[:4])          # e.g. 2025 for prev_season 2025-26
    stats = _fetch_stats(prev_season)
    rookies = idx_prev[idx_prev["DRAFT_YEAR"].astype(str).str.startswith(str(rk_year))]
    m = stats.merge(rookies[["PLAYER_ID", "DRAFT_NUMBER"]], on="PLAYER_ID", how="inner")
    m = m[m["GP"] >= 20]
    def _tier(pick):
        try: p = float(pick)
        except Exception: return 5
        return 1 if p <= 3 else 2 if p <= 9 else 3 if p <= 20 else 4 if p <= 40 else 5
    m["PT"] = m["DRAFT_NUMBER"].map(_tier)
    cols = ["MIN", "PTS", "REB", "AST", "STL", "BLK", "FG3M", "FG_PCT", "FT_PCT", "TOV",
            "FGM", "FGA", "FTM", "FTA", "GP"]
    out = {}
    for pt, g in m.groupby("PT"):
        out[pt] = {c: float(g[c].mean()) for c in cols}
    return out, _tier


def get_9cat_projections(target_season: str = "2026-27", min_proj_mpg: float = 18.0,
                         pool_size: int = 200) -> dict:
    """
    Projected 9-cat value for `target_season`: returning players via a recency-weighted
    2-season blend + aging curve on current rosters, plus rookies projected from draft slot.
    """
    import pandas as pd
    s1_name, s2_name = _prev_season(target_season, 1), _prev_season(target_season, 2)
    idx = _fetch_index(target_season)
    s1 = _fetch_stats(s1_name).set_index("PLAYER_ID")
    s2 = _fetch_stats(s2_name).set_index("PLAYER_ID")

    stat_cols = ["MIN", "PTS", "REB", "AST", "STL", "BLK", "FG3M", "FG_PCT", "FT_PCT", "TOV",
                 "FGM", "FGA", "FTM", "FTA"]
    rows, notes = [], {}
    prev_team = {int(i): r["TEAM_ABBREVIATION"] for i, r in s1.iterrows()}

    for _, ir in idx.iterrows():
        pid = int(r_pid := ir["PLAYER_ID"])
        in1, in2 = pid in s1.index, pid in s2.index
        if not (in1 or in2):
            continue  # returning-player path; rookies handled below
        r1 = s1.loc[pid] if in1 else None
        r2 = s2.loc[pid] if in2 else None
        # recency weight, discounted by games played (a thin season counts less)
        w1 = 0.65 * (min(float(r1["GP"]), 50) / 50) if in1 else 0.0
        w2 = 0.35 * (min(float(r2["GP"]), 50) / 50) if in2 else 0.0
        if w1 + w2 == 0:
            continue
        blend = {}
        for c in stat_cols:
            v1 = float(r1[c]) if in1 else 0.0
            v2 = float(r2[c]) if in2 else 0.0
            blend[c] = (w1 * v1 + w2 * v2) / (w1 + w2)
        age = (float(r1["AGE"]) if in1 else float(r2["AGE"])) + (1 if in1 else 2)
        am = _age_mult(age)
        for c in ["PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV", "MIN", "FGM", "FGA", "FTM", "FTA"]:
            blend[c] *= am
        # percentages recomputed from aged makes/attempts so they stay self-consistent
        blend["FG_PCT"] = blend["FGM"] / blend["FGA"] if blend["FGA"] else float(r1["FG_PCT"] if in1 else r2["FG_PCT"])
        blend["FT_PCT"] = blend["FTM"] / blend["FTA"] if blend["FTA"] else float(r1["FT_PCT"] if in1 else r2["FT_PCT"])
        blend["GP"] = 70
        note = []
        if age >= 33: note.append(f"age {int(age)}")
        if prev_team.get(pid) and prev_team[pid] != ir["TEAM_ABBREVIATION"]:
            note.append(f"→ {ir['TEAM_ABBREVIATION']}")
        notes[pid] = " · ".join(note)
        rows.append({"PLAYER_ID": pid, "PLAYER_NAME": ir["NAME"],
                     "TEAM_ABBREVIATION": ir["TEAM_ABBREVIATION"],
                     "POS": str(ir.get("POSITION", "") or ""), **blend})

    # Rookies: project from draft-slot tier averages of last year's rookie class.
    try:
        tier_lines, tier_fn = _rookie_lines_by_tier(s1_name, idx)
        rk_year = int(target_season[:4])
        incoming = idx[idx["DRAFT_YEAR"].astype(str).str.startswith(str(rk_year))]
        for _, ir in incoming.iterrows():
            pid = int(ir["PLAYER_ID"])
            line = tier_lines.get(tier_fn(ir.get("DRAFT_NUMBER")))
            if not line:
                continue
            notes[pid] = "rookie"
            rows.append({"PLAYER_ID": pid, "PLAYER_NAME": ir["NAME"],
                         "TEAM_ABBREVIATION": ir["TEAM_ABBREVIATION"],
                         "POS": str(ir.get("POSITION", "") or ""), "GP": 65, **line})
    except Exception:
        pass

    df = pd.DataFrame(rows)
    df = df[df["MIN"] >= min_proj_mpg].copy()
    positions = {int(r["PLAYER_ID"]): str(r.get("POSITION", "") or "") for _, r in idx.iterrows()}
    out = _rank_and_tier(df, pool_size, notes=notes, positions=positions)
    out["season"] = target_season
    out["mode"] = "projection"
    out["blend"] = f"{s1_name} + {s2_name}, aged, current rosters"
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for fn, label in [(get_9cat_rankings, "ACTUALS 2025-26"), (get_9cat_projections, "PROJECTION 2026-27")]:
        out = fn()
        print(f"\n===== {label} — pool {out['n_pool']} =====")
        for p in out["players"][:15]:
            print(f"{p['rank']:>3} T{p['tier']} {p['name']:<22}{p['pos']:>4}{p['team']:>4}{p['total']:>6.2f}"
                  f"  {p['pts']:>4}/{p['reb']:>4}/{p['ast']:>4}  {p.get('note',''):<14}")
