"""
NFL season-long PPR fantasy draft board.

Data: nflverse release parquet read directly (the nfl_data_py package pins pandas<2 and
won't install on this Python 3.14 venv, but the underlying nflverse data reads fine via
pyarrow). Two modes, mirroring the NBA board:
  * get_ppr_rankings(season)     — value from a completed season's actual PPR production.
  * get_ppr_projections(target)  — projected value: recency-weighted blend + position-aware
                                   aging on current rosters, plus rookies from draft slot.

Cross-position value = VOR (value over replacement): a player's projected PPR points minus
the replacement-level player's at his position — the standard value-based-drafting metric,
so a QB and an RB are comparable despite very different raw point totals.
"""
from __future__ import annotations

_STATS = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_{y}.parquet"
_ROSTER = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{y}.parquet"

FANTASY_POS = ["QB", "RB", "WR", "TE"]
# Replacement rank per position for a 12-team league (starters + flex demand) — the VOR baseline.
REPLACEMENT = {"QB": 12, "RB": 30, "WR": 36, "TE": 12}


def _stats(year):
    import pandas as pd
    return pd.read_parquet(_STATS.format(y=year))


def _roster(year):
    import pandas as pd
    df = pd.read_parquet(_ROSTER.format(y=year))
    idc = "player_id" if "player_id" in df.columns else ("gsis_id" if "gsis_id" in df.columns else None)
    if idc:
        df = df.rename(columns={idc: "player_id"})
    return df


def _age_mult(age: float, pos: str) -> float:
    """Position-aware aging multiplier (age at target season). RBs cliff early; WR/QB/TE last."""
    if not age or age <= 0:
        return 1.0
    if pos == "RB":
        return (1.03 if age <= 23 else 1.00 if age <= 25 else 0.97 if age == 26 else
                0.93 if age == 27 else 0.87 if age == 28 else 0.80 if age == 29 else 0.70)
    if pos == "WR":
        return (1.05 if age <= 23 else 1.00 if age <= 27 else 0.98 if age == 28 else
                0.95 if age == 29 else 0.90 if age == 30 else 0.84 if age == 31 else 0.76)
    if pos == "TE":
        return (1.05 if age <= 24 else 1.00 if age <= 29 else 0.96 if age == 30 else
                0.92 if age == 31 else 0.85)
    if pos == "QB":
        return (1.03 if age <= 24 else 1.00 if age <= 34 else 0.96 if age <= 36 else 0.88)
    return 1.0


def _vor_rank_tier(rows, pool_size):
    """rows: list of dicts with position + proj_total. Adds VOR, overall rank, positional rank, tier."""
    import pandas as pd
    df = pd.DataFrame(rows)
    if df.empty:
        return {"n_pool": 0, "players": []}
    repl = {}
    for pos, rk in REPLACEMENT.items():
        pool = df[df["position"] == pos].sort_values("proj_total", ascending=False)
        repl[pos] = float(pool.iloc[rk - 1]["proj_total"]) if len(pool) >= rk else (
            float(pool["proj_total"].min()) if len(pool) else 0.0)
    df["vor"] = df.apply(lambda r: round(r["proj_total"] - repl.get(r["position"], 0.0), 1), axis=1)
    df = df.sort_values("vor", ascending=False).head(pool_size).reset_index(drop=True)
    # positional rank (e.g. RB1, WR5)
    df["pos_rank"] = df.groupby("position").cumcount() + 1
    tiers, tier = [], 1
    v = df["vor"].tolist()
    for i, x in enumerate(v):
        if i > 0 and (v[i - 1] - x) >= 12 and tier < 12:
            tier += 1
        tiers.append(tier)
    out = []
    for i, r in df.iterrows():
        out.append({
            "rank": i + 1, "tier": tiers[i], "player_id": r.get("player_id", ""),
            "name": r["name"], "pos": r["position"], "pos_rank": f"{r['position']}{int(r['pos_rank'])}",
            "team": r.get("team", ""), "note": r.get("note", ""),
            "vor": r["vor"], "proj_total": round(float(r["proj_total"]), 1),
            "ppg": round(float(r["ppg"]), 1), "games": int(round(float(r.get("games", 0)))),
            "pass_yd": round(float(r.get("pass_yd", 0))), "pass_td": round(float(r.get("pass_td", 0)), 1),
            "rush_yd": round(float(r.get("rush_yd", 0))), "rush_td": round(float(r.get("rush_td", 0)), 1),
            "rec": round(float(r.get("rec", 0)), 1), "rec_yd": round(float(r.get("rec_yd", 0))),
            "rec_td": round(float(r.get("rec_td", 0)), 1),
        })
    return {"n_pool": len(df), "players": out}


def _row_from_stats(r, name, team, pos, note=""):
    g = max(float(r.get("games", 0)) or 1, 1)
    total = float(r.get("fantasy_points_ppr", 0) or 0)
    return {
        "player_id": r.get("player_id", ""), "name": name, "position": pos, "team": team, "note": note,
        "proj_total": total, "ppg": total / g, "games": g,
        "pass_yd": float(r.get("passing_yards", 0) or 0), "pass_td": float(r.get("passing_tds", 0) or 0),
        "rush_yd": float(r.get("rushing_yards", 0) or 0), "rush_td": float(r.get("rushing_tds", 0) or 0),
        "rec": float(r.get("receptions", 0) or 0), "rec_yd": float(r.get("receiving_yards", 0) or 0),
        "rec_td": float(r.get("receiving_tds", 0) or 0),
    }


def get_ppr_rankings(season: int = 2025, pool_size: int = 250) -> dict:
    """VOR board from a completed season's actual PPR production."""
    s = _stats(season)
    s = s[s["position"].isin(FANTASY_POS) & (s["games"] >= 4)]
    rows = [_row_from_stats(r, r["player_display_name"], r.get("recent_team", ""), r["position"])
            for _, r in s.iterrows()]
    out = _vor_rank_tier(rows, pool_size)
    out["season"], out["mode"] = str(season), "actuals"
    return out


def get_ppr_projections(target: int = 2026, pool_size: int = 250) -> dict:
    import pandas as pd
    y1, y2 = target - 1, target - 2
    s1 = _stats(y1).set_index("player_id")
    try:
        s2 = _stats(y2).set_index("player_id")
    except Exception:
        s2 = s1.iloc[0:0]
    rost = _roster(target)
    rost = rost[rost["position"].isin(FANTASY_POS)]

    def _age(bd):
        try:
            return target - int(str(bd)[:4])
        except Exception:
            return 0

    rows, seen = [], set()
    for _, pr in rost.iterrows():
        pid = pr.get("player_id", "")
        if not pid or pid in seen:
            continue
        pos, team = pr["position"], pr.get("team", "")
        in1, in2 = pid in s1.index, pid in s2.index
        is_rookie = str(pr.get("rookie_year", "")).startswith(str(target)) or str(pr.get("entry_year", "")).startswith(str(target))
        if not (in1 or in2):
            continue  # rookies handled below
        seen.add(pid)
        r1 = s1.loc[pid] if in1 else None
        r2 = s2.loc[pid] if in2 else None
        g1 = float(r1["games"]) if in1 else 0.0
        g2 = float(r2["games"]) if in2 else 0.0
        w1 = 0.68 * (min(g1, 17) / 17) if in1 else 0.0
        w2 = 0.32 * (min(g2, 17) / 17) if in2 else 0.0
        if w1 + w2 == 0:
            continue
        def _pg(col):
            v1 = (float(r1[col]) / max(g1, 1)) if in1 else 0.0
            v2 = (float(r2[col]) / max(g2, 1)) if in2 else 0.0
            return (w1 * v1 + w2 * v2) / (w1 + w2)
        age = _age(pr.get("birth_date"))
        am = _age_mult(age, pos)
        ppr_pg = _pg("fantasy_points_ppr") * am
        proj_games = min(17, max(13, round((w1 * g1 + w2 * g2) / (w1 + w2)) or 16))
        note = []
        if age and ((pos == "RB" and age >= 28) or (pos in ("WR", "TE") and age >= 30) or (pos == "QB" and age >= 37)):
            note.append(f"age {age}")
        base = {
            "player_id": pid, "name": pr.get("full_name", ""), "position": pos, "team": team,
            "note": " · ".join(note), "ppg": ppr_pg, "proj_total": ppr_pg * proj_games, "games": proj_games,
            "pass_yd": _pg("passing_yards") * am, "pass_td": _pg("passing_tds") * am,
            "rush_yd": _pg("rushing_yards") * am, "rush_td": _pg("rushing_tds") * am,
            "rec": _pg("receptions") * am, "rec_yd": _pg("receiving_yards") * am,
            "rec_td": _pg("receiving_tds") * am,
        }
        # per-game -> season display for the stat line
        for c in ("pass_yd", "pass_td", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td"):
            base[c] *= proj_games
        rows.append(base)

    # Rookies: project per-game PPR from last rookie class by position + draft tier.
    try:
        rost_prev = _roster(y1)
        rk_prev = rost_prev[rost_prev.apply(
            lambda r: str(r.get("rookie_year", "")).startswith(str(y1)) or str(r.get("entry_year", "")).startswith(str(y1)), axis=1)]
        prev_stats = _stats(y1).set_index("player_id")
        def _tier(dn):
            try: p = float(dn)
            except Exception: return 4
            return 1 if p <= 15 else 2 if p <= 45 else 3 if p <= 100 else 4
        buckets = {}
        for _, rr in rk_prev.iterrows():
            pid = rr.get("player_id", "")
            if pid not in prev_stats.index or rr["position"] not in FANTASY_POS:
                continue
            st = prev_stats.loc[pid]
            g = max(float(st.get("games", 0)) or 1, 1)
            buckets.setdefault((rr["position"], _tier(rr.get("draft_number"))), []).append(
                float(st.get("fantasy_points_ppr", 0) or 0) / g)
        tier_pg = {k: (sum(v) / len(v)) for k, v in buckets.items() if v}
        incoming = rost[rost.apply(
            lambda r: str(r.get("rookie_year", "")).startswith(str(target)) or str(r.get("entry_year", "")).startswith(str(target)), axis=1)]
        for _, pr in incoming.iterrows():
            pg = tier_pg.get((pr["position"], _tier(pr.get("draft_number"))))
            if not pg:
                continue
            g = 16
            rows.append({"player_id": pr.get("player_id", ""), "name": pr.get("full_name", ""),
                         "position": pr["position"], "team": pr.get("team", ""), "note": "rookie",
                         "ppg": pg, "proj_total": pg * g, "games": g,
                         "pass_yd": 0, "pass_td": 0, "rush_yd": 0, "rush_td": 0, "rec": 0, "rec_yd": 0, "rec_td": 0})
    except Exception:
        pass

    out = _vor_rank_tier(rows, pool_size)
    out["season"], out["mode"] = str(target), "projection"
    out["blend"] = f"{y1} + {y2} per-game blend, position aging, current rosters"
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for fn, lbl in [(get_ppr_rankings, "ACTUALS 2025"), (get_ppr_projections, "PROJECTION 2026")]:
        o = fn()
        print(f"\n===== {lbl} — pool {o['n_pool']} =====")
        for p in o["players"][:18]:
            print(f"{p['rank']:>3} T{p['tier']:<2}{p['pos_rank']:>5} {p['name']:<24}{p['team']:>4} "
                  f"VOR {p['vor']:>6.0f}  {p['proj_total']:>5.0f}pts {p['ppg']:>4.1f}/g  {p.get('note',''):<10}")
