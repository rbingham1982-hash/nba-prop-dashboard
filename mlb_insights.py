"""
mlb_insights.py — player-level MLB analysis on top of the projection engine.

The betting board asks one question of a player ("is this line beatable?") and throws away
everything else it computed. This is the other view: what does the data actually say about
this hitter, and who on today's slate is most likely to do a given thing.

Built on the same pieces the board uses — the plate-appearance model in parlay_model, the
posted lineup, Statcast rates — plus MLB Stats API situational splits, which the board
never touches:

    vs Left / vs Right      the platoon matchup against tonight's starter
    Home / Away             park and travel
    Day / Night             lighting and, in practice, lineup quality
    RISP, First Inning      context the frequency estimator cannot see

Two entry points:

    hit_board(date)         every hitter in a posted lineup, ranked by P(1+ hit)
    player_profile(name)    the deep dive on one hitter
"""
from __future__ import annotations

import time

_STATSAPI_PEOPLE = "https://statsapi.mlb.com/api/v1/people/{pid}"
_STATSAPI_SPLITS = ("https://statsapi.mlb.com/api/v1/people/{pid}/stats"
                    "?stats=statSplits&sitCodes={codes}&season={season}&group={group}")
_SPLIT_CODES = "vl,vr,h,a,d,n,risp"

# Plate appearances of a split before it outweighs the player's own overall rate. Platoon
# samples are small — a right-handed regular may see only 50-60 at-bats against lefties in
# a season — and an unshrunk .246-vs-.310 split is mostly noise. 120 is deliberately heavy.
_SPLIT_SHRINK_AB = 120

_cache: dict = {}
_TTL = 3600


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def player_meta(pid: int) -> dict:
    """Handedness and bio for a player id."""
    import requests

    def _go():
        try:
            r = requests.get(_STATSAPI_PEOPLE.format(pid=pid), timeout=20)
            p = (r.json().get("people") or [{}])[0]
            return {"name": p.get("fullName"),
                    "bats": (p.get("batSide") or {}).get("code"),
                    "throws": (p.get("pitchHand") or {}).get("code"),
                    "position": (p.get("primaryPosition") or {}).get("abbreviation")}
        except Exception:
            return {}

    return _cached(f"meta_{pid}", _go)


def player_splits(pid: int, season: int, group: str = "hitting") -> dict:
    """
    split code -> stat dict, for the situational splits MLB publishes.

    Returns {} rather than raising when a player has no rows for the season, which is the
    normal case early on and for callups.
    """
    import requests

    def _go():
        try:
            u = _STATSAPI_SPLITS.format(pid=pid, codes=_SPLIT_CODES, season=season, group=group)
            r = requests.get(u, timeout=25)
            out = {}
            for s in r.json().get("stats", []) or []:
                for sp in s.get("splits", []) or []:
                    code = (sp.get("split") or {}).get("code")
                    if code:
                        out[code] = sp.get("stat", {}) or {}
            return out
        except Exception:
            return {}

    return _cached(f"splits_{pid}_{season}_{group}", _go)


def _f(d: dict, key: str, default=0.0) -> float:
    try:
        return float(d.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def platoon_factor(pid: int, season: int, opp_hand: str) -> dict:
    """
    How this hitter's hit rate per at-bat changes against a given pitcher hand, as a
    multiplier on his overall rate.

    Shrunk hard toward 1.0 by at-bats — see _SPLIT_SHRINK_AB. An unshrunk platoon split is
    one of the most over-read numbers in baseball: 57 at-bats carries a standard error on
    batting average of roughly 60 points, which is larger than the effect being measured.

    DO NOT WIRE THIS INTO THE PROPS SCORER on the strength of it looking sensible. Tested
    against 731 resolved MLB Hits legs whose opposing starter's hand was recovered from the
    boxscore, it predicts BACKWARDS:

        unfavourable platoon (<0.99)   n=298   hit 62.1%
        favourable   platoon (>1.01)   n=343   hit 53.9%
        controlling for the model's own prediction: t = -3.0

    The likely cause is not that platoon splits are useless but that THIS measurement is
    contaminated: the split comes from full-season numbers that include the very game being
    predicted, so a hitter with a high vs-RHP factor is partly a hitter who happened to run
    hot against righties in that sample, and mean reversion does the rest. A clean test
    needs point-in-time splits — the player's rate against that hand using only games
    BEFORE the one in question, which means recovering the starter faced in each historical
    game.

    So this is honest analysis output for the hit board and the profile view, where a human
    reads it in context. It is not a prediction input, and the contaminated test is not
    evidence that it would be a good one.
    """
    code = "vl" if str(opp_hand).upper().startswith("L") else "vr"
    sp = player_splits(pid, season)
    side, vl, vr = sp.get(code, {}), sp.get("vl", {}), sp.get("vr", {})
    ab_side, h_side = _f(side, "atBats"), _f(side, "hits")
    ab_all = _f(vl, "atBats") + _f(vr, "atBats")
    h_all = _f(vl, "hits") + _f(vr, "hits")
    if ab_all <= 0 or ab_side <= 0:
        return {"factor": 1.0, "ab": 0, "avg_split": None, "avg_overall": None, "hand": code}
    r_side, r_all = h_side / ab_side, h_all / ab_all
    if r_all <= 0:
        return {"factor": 1.0, "ab": int(ab_side), "avg_split": r_side,
                "avg_overall": r_all, "hand": code}
    raw = r_side / r_all
    w = ab_side / (ab_side + _SPLIT_SHRINK_AB)
    return {"factor": round(1.0 + w * (raw - 1.0), 4), "ab": int(ab_side),
            "avg_split": round(r_side, 3), "avg_overall": round(r_all, 3),
            "raw_factor": round(raw, 3), "hand": code}


# ── Today's board ───────────────────────────────────────────────────────────

def todays_matchups(date_str: str) -> dict:
    """
    normalised hitter name -> {slot, team, opp_starter, opp_hand} for every posted lineup.

    Lineup and opposing starter are resolved together in one pass over the schedule,
    because they have to agree. The first cut looked the starter up by a team abbreviation
    derived from the hitter's most recent game log — which carries the OPPONENT, not his
    own club — so every lookup missed and the platoon adjustment silently applied 1.0 to
    the entire board. Reading both off the same boxscore removes the join entirely.
    """
    out = {}
    try:
        import statsapi
        import parlay_model as pm
    except ImportError:
        return out
    try:
        games = statsapi.schedule(date=date_str, sportId=1)
    except Exception:
        return out
    hands: dict = {}
    for g in games:
        try:
            box = statsapi.boxscore_data(g["game_id"])
        except Exception:
            continue
        for side, opp_side in (("home", "away"), ("away", "home")):
            sp_name = g.get(f"{opp_side}_probable_pitcher") or ""
            hand = None
            if sp_name:
                if sp_name not in hands:
                    try:
                        spid = pm.mlb_player_id(sp_name)
                        hands[sp_name] = player_meta(spid).get("throws") if spid else None
                    except Exception:
                        hands[sp_name] = None
                hand = hands[sp_name]
            for pdd in box.get(side, {}).get("players", {}).values():
                bo = str(pdd.get("battingOrder") or "")
                if not bo.isdigit() or int(bo) % 100 != 0:
                    continue      # starters only
                nm = (pdd.get("person") or {}).get("fullName", "")
                if not nm:
                    continue
                out[pm._norm_mlb_name(nm)] = {
                    "slot": int(bo) // 100,
                    "team": g.get(f"{side}_name"),
                    "opp_starter": sp_name or None,
                    "opp_hand": hand,
                }
    return out


def hit_board(date_str: str | None = None, season: int | None = None, limit: int = 40) -> list:
    """
    Every hitter in a posted lineup today, ranked by P(at least one hit).

    This is the question the prop board cannot answer directly, because it only ever scores
    the lines a book happens to post. Here the probability is built from the pieces:

        P(1+ hit) = 1 - (1 - p_hit_per_PA * platoon) ^ expected_PA

    expected_PA comes from the posted batting slot (_MLB_SLOT_PA), p_hit_per_PA from the
    player's own recent games, and platoon from his shrunk split against the handedness of
    tonight's actual starter. Nothing here is a betting line — it is what the model thinks
    before a price is involved.
    """
    import datetime
    import parlay_model as pm
    date_str = date_str or datetime.date.today().strftime("%m/%d/%Y")
    season = season or datetime.date.today().year

    matchups = todays_matchups(date_str)
    if not matchups:
        return []

    rows = []
    for norm_name, mu in matchups.items():
        slot = mu["slot"]
        try:
            pid = pm.mlb_player_id(norm_name)
            if not pid:
                continue
            df = pm.get_mlb_hitting_logs(pid, (str(season - 1), str(season)))
            if df is None or df.empty:
                continue
            sub = df.tail(pm._MLB_PA_LOOKBACK)
            tot_pa = float(sum(int(a or 0) + int(b or 0) for a, b in zip(sub["AB"], sub["BB"])))
            if tot_pa <= 0:
                continue
            p_hit = float(sub["H"].sum()) / tot_pa
        except Exception:
            continue

        exp_pa = pm._MLB_SLOT_PA.get(int(slot), 4.0)
        meta = player_meta(pid)
        hand = mu.get("opp_hand")
        pf = platoon_factor(pid, season, hand) if hand else {"factor": 1.0, "ab": 0}
        p_adj = min(0.95, max(0.01, p_hit * pf["factor"]))
        p_1h = 1.0 - (1.0 - p_adj) ** exp_pa

        rows.append({
            "player": meta.get("name") or norm_name,
            "bats": meta.get("bats"), "slot": int(slot),
            "exp_pa": round(exp_pa, 2),
            "hit_per_pa": round(p_hit, 4),
            "team": mu.get("team"),
            "vs_hand": hand, "starter": mu.get("opp_starter"),
            "platoon_x": pf.get("factor"), "platoon_ab": pf.get("ab"),
            "p_hit": round(p_1h, 4),
        })
    rows.sort(key=lambda r: -r["p_hit"])
    return rows[:limit]


# ── One player, everything ──────────────────────────────────────────────────

def player_profile(name: str, season: int | None = None) -> dict:
    """
    Everything the system knows about one hitter, in one object.

    Deliberately assembled rather than modelled: recent form, situational splits, Statcast
    contact quality, and the plate-appearance profile that drives every prop he appears in.
    The point is to see WHY a projection is what it is, which the board never shows.
    """
    import datetime
    import parlay_model as pm
    season = season or datetime.date.today().year
    pid = pm.mlb_player_id(name)
    if not pid:
        return {}
    out = {"player": name, "id": pid, **player_meta(pid)}

    df = pm.get_mlb_hitting_logs(pid, (str(season - 1), str(season)))
    if df is not None and not df.empty:
        for label, n in (("last_10", 10), ("last_30", 30)):
            sub = df.tail(n)
            pa = float(sum(int(a or 0) + int(b or 0) for a, b in zip(sub["AB"], sub["BB"])))
            out[label] = {
                "games": len(sub), "pa": pa,
                "avg": round(float(sub["H"].sum()) / max(float(sub["AB"].sum()), 1), 3),
                "hit_per_pa": round(float(sub["H"].sum()) / pa, 3) if pa else None,
                "hr": int(sub["HR"].sum()), "tb_per_game": round(float(sub["TB"].sum()) / len(sub), 2),
                "k_rate": round(float(sub["K"].sum()) / pa, 3) if pa else None,
            }
        out["pa_profile"] = {
            "mean_pa": round(pm._mlb_mean_opportunity(df, False) or 0, 2),
            "games": len(df),
        }

    sp = player_splits(pid, season)
    out["splits"] = {k: {"ab": _f(v, "atBats"), "avg": v.get("avg"), "ops": v.get("ops"),
                         "hr": _f(v, "homeRuns"), "k": _f(v, "strikeOuts")}
                     for k, v in sp.items()}
    for hand in ("L", "R"):
        out[f"platoon_vs_{hand}"] = platoon_factor(pid, season, hand)

    try:
        sc = pm.savant_batter_stats().get(pid) or {}
        out["statcast"] = {k: sc.get(k) for k in
                           ("xba", "xslg", "xiso", "barrel", "hardhit", "ev", "la", "k_pct", "bb_pct")}
    except Exception:
        out["statcast"] = {}
    return out
