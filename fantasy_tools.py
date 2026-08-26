"""
fantasy_tools.py — fantasy football and daily-fantasy tooling built on the projection
engine in nfl_analysis / parlay_model.

Why this exists as its own surface rather than more betting features: the props model has
to clear a book's hold (12-19% on MLB milestone markets, 5.8% on two-way) with a market
blend weight of ~0.11, which means it needs a ~50%+ relative disagreement with the market
before a bet is even playable. Fantasy has no pricing market. The opponent is other
managers working off consensus rankings, and DFS salaries are set days ahead and do not
move on lineup news the way a prop line does. The same projections are worth more here.

Three tools:

  dfs_slate()      DraftKings salaries joined to our projections -> points per $1000
  waiver_board()   our projection vs what the crowd is adding on Sleeper
  start_sit()      head-to-head weekly projection for players you already roster

Data sources, all public and unauthenticated:
  DraftKings  lobby + draftgroups API  (salaries, live slates)
  Sleeper     /v1/players, /v1/players/nfl/trending  (the crowd)
"""
from __future__ import annotations

import time

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0"}
_DK_LOBBY = "https://www.draftkings.com/lobby/getcontests?sport={sport}"
_DK_DRAFTABLES = "https://api.draftkings.com/draftgroups/v1/draftgroups/{gid}/draftables"
_SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"
_SLEEPER_TRENDING = "https://api.sleeper.app/v1/players/nfl/trending/{kind}?lookback_hours={hours}&limit={limit}"

_cache: dict = {}
_CACHE_TTL = 900   # seconds; slates and trending both move slowly enough


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ── DraftKings ──────────────────────────────────────────────────────────────

def dk_draft_groups(sport: str = "MLB") -> list:
    """Live draft groups for a sport, soonest first."""
    import requests

    def _go():
        r = requests.get(_DK_LOBBY.format(sport=sport.upper()), timeout=25, headers=_UA)
        if r.status_code != 200:
            return []
        gs = r.json().get("DraftGroups", []) or []
        return sorted(gs, key=lambda g: str(g.get("StartDateEst") or ""))

    return _cached(f"dk_groups_{sport}", _go)


def dk_salaries(sport: str = "MLB", draft_group_id: int | None = None):
    """
    Salaries for a slate as a DataFrame: player, position, team, salary, start.

    A draft group is only populated once DraftKings posts the slate, so future groups come
    back empty — the default walks the groups in start order and takes the first that has
    players, which is the slate you would actually be entering.
    """
    import pandas as pd
    import requests

    def _go():
        groups = ([{"DraftGroupId": draft_group_id}] if draft_group_id
                  else dk_draft_groups(sport))
        for g in groups:
            gid = g.get("DraftGroupId")
            if not gid:
                continue
            try:
                r = requests.get(_DK_DRAFTABLES.format(gid=gid), timeout=25, headers=_UA)
                if r.status_code != 200:
                    continue
                d = r.json().get("draftables", []) or []
            except Exception:
                continue
            if not d:
                continue
            rows, seen = [], set()
            for x in d:
                name = x.get("displayName")
                # One player appears once per roster slot they are eligible for; the
                # salary is the same, so keep the first and drop the duplicates.
                if not name or name in seen:
                    continue
                seen.add(name)
                rows.append({
                    "player": name,
                    "position": x.get("position"),
                    "team": x.get("teamAbbreviation"),
                    "salary": x.get("salary"),
                    "draft_group": gid,
                    "start": g.get("StartDateEst"),
                })
            if rows:
                return pd.DataFrame(rows)
        return pd.DataFrame()

    return _cached(f"dk_sal_{sport}_{draft_group_id}", _go)


# ── DraftKings scoring ──────────────────────────────────────────────────────
# Classic DK scoring. Kept explicit rather than approximated, because value is points per
# dollar and a scoring shortcut moves every ranking.

DK_MLB_HITTER = {"single": 3, "double": 5, "triple": 8, "hr": 10,
                 "rbi": 2, "run": 2, "bb": 2, "sb": 5}
DK_NFL = {"pass_yd": 0.04, "pass_td": 4, "rush_yd": 0.1, "rush_td": 6,
          "rec": 1.0, "rec_yd": 0.1, "rec_td": 6}


def dk_points_mlb_hitter(per_game: dict) -> float:
    """Expected DK points from projected per-game batting counts."""
    singles = max(0.0, per_game.get("H", 0) - per_game.get("2B", 0)
                  - per_game.get("3B", 0) - per_game.get("HR", 0))
    return round(
        singles * DK_MLB_HITTER["single"]
        + per_game.get("2B", 0) * DK_MLB_HITTER["double"]
        + per_game.get("3B", 0) * DK_MLB_HITTER["triple"]
        + per_game.get("HR", 0) * DK_MLB_HITTER["hr"]
        + per_game.get("RBI", 0) * DK_MLB_HITTER["rbi"]
        + per_game.get("R", 0) * DK_MLB_HITTER["run"]
        + per_game.get("BB", 0) * DK_MLB_HITTER["bb"]
        + per_game.get("SB", 0) * DK_MLB_HITTER["sb"], 2)


def dk_points_nfl(proj: dict) -> float:
    """Expected DK points from projected per-game NFL stats."""
    return round(
        proj.get("Passing Yards", 0) * DK_NFL["pass_yd"]
        + proj.get("Passing TDs", 0) * DK_NFL["pass_td"]
        + proj.get("Rushing Yards", 0) * DK_NFL["rush_yd"]
        + proj.get("Rushing TDs", 0) * DK_NFL["rush_td"]
        + proj.get("Receptions", 0) * DK_NFL["rec"]
        + proj.get("Receiving Yards", 0) * DK_NFL["rec_yd"]
        + proj.get("Receiving TDs", 0) * DK_NFL["rec_td"], 2)


# ── Sleeper: the crowd ──────────────────────────────────────────────────────

def sleeper_players() -> dict:
    """sleeper player_id -> {name, position, team}."""
    import requests

    def _go():
        try:
            r = requests.get(_SLEEPER_PLAYERS, timeout=40, headers=_UA)
            if r.status_code != 200:
                return {}
            out = {}
            for pid, p in (r.json() or {}).items():
                nm = p.get("full_name") or p.get("last_name")
                if not nm:
                    continue
                out[pid] = {"name": nm, "position": p.get("position"),
                            "team": p.get("team"),
                            # Which slot he holds. The single most important field here:
                            # a QB2 is not a waiver target, he is a backup.
                            "depth_chart_order": p.get("depth_chart_order"),
                            # Sleeper's popularity rank: Josh Allen is 3, a deep-league
                            # dart is in the thousands. The closest free proxy for
                            # "already rostered" — see waiver_board.
                            "search_rank": p.get("search_rank")}
            return out
        except Exception:
            return {}

    return _cached("sleeper_players", _go)


def sleeper_trending(kind: str = "add", hours: int = 168, limit: int = 50) -> list:
    """
    [{name, position, team, count}] — what the crowd is adding or dropping.

    This is the closest thing fantasy has to a market price, and it is a far softer one:
    it lags rather than anticipates, which is exactly why a projection can get in front
    of it.
    """
    import requests
    players = sleeper_players()

    def _go():
        try:
            r = requests.get(_SLEEPER_TRENDING.format(kind=kind, hours=hours, limit=limit),
                             timeout=25, headers=_UA)
            if r.status_code != 200:
                return []
            out = []
            for row in r.json() or []:
                p = players.get(str(row.get("player_id")))
                if not p:
                    continue
                out.append({**p, "count": int(row.get("count") or 0)})
            return out
        except Exception:
            return []

    return _cached(f"sleeper_trend_{kind}_{hours}_{limit}", _go)


# ── Tool 1: DFS value board ─────────────────────────────────────────────────

def dfs_slate(sport: str = "MLB", draft_group_id: int | None = None):
    """
    Today's DraftKings slate joined to our projections, ranked by points per $1,000.

    Value, not raw points, is the DFS question: a roster is a salary-cap problem, so the
    player who scores most is rarely the player you want. Projections come from the same
    engines the betting board uses — the MLB plate-appearance model and the NFL usage-share
    model — which is the whole argument for doing this at all: the projections already
    exist and DFS does not charge a hold to use them.

    Returns a DataFrame with proj_points, value and the inputs behind them; empty when no
    slate is posted.
    """
    import pandas as pd
    sal = dk_salaries(sport, draft_group_id)
    if sal is None or sal.empty:
        return pd.DataFrame()
    rows = []
    if sport.upper() == "MLB":
        import parlay_model as pm
        for _, r in sal.iterrows():
            pos = str(r.get("position") or "")
            if pos in ("SP", "RP", "P"):
                continue          # pitcher DFS scoring needs an innings model, see below
            try:
                pid = pm.mlb_player_id(r["player"])
                if not pid:
                    continue
                df = pm.get_mlb_hitting_logs(pid, ("2025", "2026"))
                if df is None or df.empty:
                    continue
                pa = pm._mlb_mean_opportunity(df, False)
                if not pa:
                    continue
                sub = df.tail(pm._MLB_PA_LOOKBACK)
                tot_pa = float(sum(int(a or 0) + int(b or 0)
                                   for a, b in zip(sub["AB"], sub["BB"])))
                if tot_pa <= 0:
                    continue
                per_game = {c: (float(sub[c].sum()) / tot_pa) * pa
                            for c in ("H", "2B", "3B", "HR", "R", "BB", "RBI", "SB")
                            if c in sub.columns}
                pts = dk_points_mlb_hitter(per_game)
            except Exception:
                continue
            rows.append({**r.to_dict(), "proj_points": pts, "proj_pa": round(pa, 2)})
    else:
        import nfl_analysis as nfl
        season = nfl.latest_season_with_data()
        _, df = nfl.get_season(season)
        idx = nfl.player_index(df)
        priors, vol = nfl.position_priors(df), nfl.team_volume(df)
        teams = nfl.current_teams(season + 1)
        board = nfl.board_projections(season + 1)
        rates, cv = nfl.league_rates(df), {}
        for _, r in sal.iterrows():
            proj = {}
            for stat in nfl._USAGE_MODEL:
                try:
                    s = nfl.score_prop_nfl(df, r["player"], stat, 0.5, teams=teams,
                                           priors=priors, vol=vol, idx=idx,
                                           board=board, rates=rates, cvcache=cv)
                except Exception:
                    s = None
                if s:
                    proj[stat] = s.get("projection", 0)
            if not proj:
                continue
            rows.append({**r.to_dict(), "proj_points": dk_points_nfl(proj)})
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["value"] = (out["proj_points"] / (out["salary"] / 1000.0)).round(2)
    return out.sort_values("value", ascending=False).reset_index(drop=True)


# ── Tool 2: waiver board ────────────────────────────────────────────────────

# Share of a position's snaps a player at each depth-chart slot actually sees. This is the
# fourth instance of the same structural gap in this project: the projection engine models
# a RATE and has no concept of whether the player takes the field at all.
#
# It is most extreme at quarterback, and it produced a wrong answer that a domain expert
# spotted instantly and no automated check caught. The first waiver board led with Tyrod
# Taylor at 12.2 projected points — along with Jake Browning, Davis Mills and Kirk Cousins,
# every one of them depth_chart_order 2. usage_profile computes share-of-team-attempts from
# games the player ACTUALLY PLAYED, and when a backup quarterback plays he plays a full
# game, so his per-game usage reads exactly like a starter's. The board then surfaced them
# precisely BECAUSE they were unrostered, which is the same fact as "he does not play".
#
# Quarterback is nearly binary — one man takes essentially every snap — while the other
# positions rotate, which is why the curve is so much steeper for QB.
_DEPTH_SHARE = {
    "QB": {1: 1.00, 2: 0.12, 3: 0.03},
    "RB": {1: 1.00, 2: 0.75, 3: 0.35, 4: 0.12},
    "WR": {1: 1.00, 2: 1.00, 3: 0.80, 4: 0.45, 5: 0.15},
    "TE": {1: 1.00, 2: 0.35, 3: 0.10},
}
_DEPTH_FLOOR = {"QB": 0.03, "RB": 0.10, "WR": 0.12, "TE": 0.08}

# Minimum projected points for a player to be worth naming as a waiver add, per position.
# Applying the depth discount above without this just produces a politely-ranked list of
# people nobody should add: the first depth-aware board led with quarterbacks projecting
# 1.6 points. A board that recommends a 1.6-point quarterback is worse than an empty one,
# because it teaches the reader the numbers do not mean anything.
#
# Empty is a legitimate answer and the newsletter gate treats it as one. In late August
# everyone with a real role is rostered, so "nothing worth adding this week" is simply
# true — and saying so is the thing that makes the weeks with a real name credible.
_WAIVER_FLOOR = {"QB": 11.0, "RB": 7.0, "WR": 7.0, "TE": 5.0}


def depth_multiplier(position: str, order) -> float:
    """
    How much of a full workload to expect from a player at this depth-chart slot.

    Unknown depth returns 1.0 — no information is not evidence of a bench role, and
    discounting on a missing field would quietly bury every player Sleeper has not charted.
    """
    if order is None:
        return 1.0
    try:
        o = int(order)
    except (TypeError, ValueError):
        return 1.0
    table = _DEPTH_SHARE.get(str(position).upper())
    if not table:
        return 1.0
    return table.get(o, _DEPTH_FLOOR.get(str(position).upper(), 0.1))


def waiver_board(limit: int = 40, min_rank: int = 150, with_stats: bool = False):
    """
    Players we project well who are probably still available.

    The gap is the point. Sleeper's trending list lags — it reacts to last week's box
    score — so a player the projection likes who is NOT being added is the actionable
    case, and one being added in six figures is already priced into your league.

    min_rank is what makes this a waiver board rather than a list of the best players
    alive. Ranked on projection alone it returned Josh Allen, Puka Nacua and Christian
    McCaffrey — all correct, all rostered in every league, all useless. Sleeper's
    search_rank is the free proxy for that: roughly the top 150 are gone in a 12-team
    league, so the board starts below them.

    A caution that belongs in the docstring rather than the README: usage CHANGE is a weak
    predictor. Tested over 1,799 player-weeks, the correlation between a shift in target
    share and the next three weeks of production is 0.058, and the buckets are not even
    monotonic. So this ranks on projected LEVEL against crowd attention, never on "his
    snaps spiked last week".
    """
    import nfl_analysis as nfl
    season = nfl.latest_season_with_data()
    _, df = nfl.get_season(season)
    idx = nfl.player_index(df)
    priors, vol = nfl.position_priors(df), nfl.team_volume(df)
    teams = nfl.current_teams(season + 1)
    board = nfl.board_projections(season + 1)
    rates, cv = nfl.league_rates(df), {}

    adds = {p["name"]: p["count"] for p in sleeper_trending("add", limit=200)}
    drops = {p["name"]: p["count"] for p in sleeper_trending("drop", limit=200)}
    _sp = sleeper_players()
    ranks = {p["name"]: (p.get("search_rank") or 10 ** 7) for p in _sp.values()}
    depths = {p["name"]: p.get("depth_chart_order") for p in _sp.values()}

    out = []
    _scored = _after_rank = 0
    for name, sub in idx.items():
        pos = str(sub.iloc[0].get("position", ""))
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        _scored += 1
        proj = {}
        for stat in nfl._USAGE_MODEL:
            try:
                s = nfl.score_prop_nfl(df, name, stat, 0.5, teams=teams, priors=priors,
                                       vol=vol, idx=idx, board=board, rates=rates, cvcache=cv)
            except Exception:
                s = None
            if s:
                proj[stat] = s.get("projection", 0)
        if not proj:
            continue
        pts = dk_points_nfl(proj)
        if pts <= 0:
            continue
        # Discount to the workload his depth-chart slot actually implies.
        order = depths.get(name)
        mult = depth_multiplier(pos, order)
        pts = round(pts * mult, 2)
        if pts < _WAIVER_FLOOR.get(pos, 5.0):
            continue
        rank = ranks.get(name, 10 ** 7)
        if rank < min_rank:
            continue          # rostered everywhere; not a waiver decision
        _after_rank += 1
        out.append({"player": name, "position": pos,
                    "team": (teams.get(str(sub.iloc[0].get("player_id", ""))) or ""),
                    "proj_points": pts, "search_rank": rank,
                    "depth": order, "depth_mult": mult,
                    "adds": adds.get(name, 0), "drops": drops.get(name, 0),
                    # "quiet" is the one you want: the projection likes him and the
                    # crowd has not moved yet. "hot" means your league already knows.
                    "crowd": "hot" if adds.get(name, 0) > 20000 else
                             ("cooling" if drops.get(name, 0) > 20000 else "quiet")})
    # Sleeper's search_rank is not reliable enough to be the only availability test. It
    # put Lamar Jackson at 1059 and Marvin Harrison Jr. at 10,000,000, so the first build
    # of this board recommended both as waiver pickups — tolerable in an internal tool,
    # fatal in anything published, because one obviously wrong name discredits the whole
    # list.
    #
    # So a second gate that does not depend on their data: anyone our OWN projection ranks
    # inside the startable tier for his position is rostered by definition, whatever
    # search_rank claims. Roughly a 12-team league's starters plus a bench.
    startable = {"QB": 14, "RB": 30, "WR": 36, "TE": 14}
    by_pos: dict = {}
    for r in sorted(out, key=lambda r: -r["proj_points"]):
        by_pos.setdefault(r["position"], []).append(r)
    elite = set()
    for pos, rows_ in by_pos.items():
        for r in rows_[:startable.get(pos, 20)]:
            elite.add((r["player"], r["position"]))
    out = [r for r in out if (r["player"], r["position"]) not in elite]

    # Per position, not overall. DK scoring pays a quarterback roughly twice what it pays
    # a receiver for an equivalent week, so an overall ranking returned fourteen
    # quarterbacks and nothing else — true, and useless, since you start one. Top N per
    # position is the shape a waiver decision actually takes.
    # Diagnostics so a caller can tell "the feed is down" from "nobody qualified", which
    # look identical from an empty list and are opposite situations: one is a bug, the
    # other is a true and publishable answer.
    stats = {"scored": _scored, "after_rank": _after_rank,
             "after_floor": len(out), "returned": 0}

    out.sort(key=lambda r: -r["proj_points"])
    per_pos, kept = {}, []
    cap = max(1, limit // 4)
    for r in out:
        pos = r["position"]
        if per_pos.get(pos, 0) >= cap:
            continue
        per_pos[pos] = per_pos.get(pos, 0) + 1
        kept.append(r)
    stats["returned"] = len(kept)
    return (kept, stats) if with_stats else kept


# ── Tool 3: start / sit ─────────────────────────────────────────────────────

def start_sit(players: list) -> list:
    """
    Rank a set of players you already roster by projected DK points, highest first.

    Deliberately the same projection the DFS board and the waiver board use. A start/sit
    call that disagrees with your own waiver ranking means one of them is wrong, and
    keeping a single engine behind all three is what stops that happening quietly.
    """
    import nfl_analysis as nfl
    season = nfl.latest_season_with_data()
    _, df = nfl.get_season(season)
    idx = nfl.player_index(df)
    priors, vol = nfl.position_priors(df), nfl.team_volume(df)
    teams = nfl.current_teams(season + 1)
    board = nfl.board_projections(season + 1)
    rates, cv = nfl.league_rates(df), {}

    out = []
    for name in players:
        proj, src = {}, None
        for stat in nfl._USAGE_MODEL:
            try:
                s = nfl.score_prop_nfl(df, name, stat, 0.5, teams=teams, priors=priors,
                                       vol=vol, idx=idx, board=board, rates=rates, cvcache=cv)
            except Exception:
                s = None
            if s:
                proj[stat] = s.get("projection", 0)
                src = s.get("source", src)
                changed = s.get("changed_team")
        if not proj:
            out.append({"player": name, "proj_points": None, "note": "no projection"})
            continue
        out.append({"player": name, "proj_points": dk_points_nfl(proj), "source": src,
                    "note": "new team — projection rebased" if locals().get("changed") else ""})
    out.sort(key=lambda r: -(r["proj_points"] or -1))
    return out


# ── Sleepers: projection versus consensus ───────────────────────────────────

def sleepers(limit: int = 24, min_rank: int = 60, max_rank: int = 400) -> list:
    """
    Players our projection likes far more than consensus does — the pre-season question,
    where a waiver board has nothing to say because nobody has been dropped yet.

    A sleeper is a GAP, not a good player. Both ranks are computed within position, so the
    number means "we have him N spots higher at his position than the crowd does". Ranking
    on raw projection instead would just return the best players alive, who are nobody's
    sleeper.

    Sleeper's search_rank stands in for ADP. It is not a true draft position, but it is a
    consensus popularity ordering and it is free, which is the trade being made — nflverse
    publishes no ADP feed (ff_rankings 404s).

    Three guards, each of which exists because its absence produced a wrong answer:

      depth_multiplier   a backup is not a sleeper, he is a backup. Without this the board
                         led with Tyrod Taylor, a QB2 projected like a starter because his
                         usage was measured over the games he actually played.
      _WAIVER_FLOOR      a player projecting 1.6 points is not a sleeper at any ADP.
      rank bounds        min_rank skips the top of the draft, where being 5 spots higher
                         than consensus is an opinion rather than a find. max_rank excludes
                         the unranked, whose sentinel value (10,000,000) would otherwise
                         make every uncharted player look like the steal of the century —
                         which is exactly how Marvin Harrison Jr. once turned up as a
                         waiver target.
    """
    import nfl_analysis as nfl
    season = nfl.latest_season_with_data()
    _, df = nfl.get_season(season)
    idx = nfl.player_index(df)
    priors, vol = nfl.position_priors(df), nfl.team_volume(df)
    teams = nfl.current_teams(season + 1)
    board = nfl.board_projections(season + 1)
    rates, cv = nfl.league_rates(df), {}

    sp = sleeper_players()
    ranks = {p["name"]: p.get("search_rank") for p in sp.values()}
    depths = {p["name"]: p.get("depth_chart_order") for p in sp.values()}
    cur_team = {p["name"]: p.get("team") for p in sp.values()}

    rows = []
    for name, sub in idx.items():
        pos = str(sub.iloc[0].get("position", ""))
        if pos not in ("QB", "RB", "WR", "TE"):
            continue
        # Fifth instance of the availability gap, and the bluntest: an unsigned free agent
        # has no team, so project_usage falls back to team_prev and scores him on LAST
        # year's offence as though he were still starting there. The first sleepers list
        # led with Tyreek Hill, Kareem Hunt and Zach Ertz — all unsigned, none on a 2026
        # roster, all projected like starters.
        #
        # Sleeper's team field is the check rather than nflverse's roster, because the two
        # disagree and Sleeper is the fresher of the pair in late August: it has Stefon
        # Diggs on Washington and Keenan Allen on Indianapolis, both of whom the nflverse
        # 2026 roster has not picked up yet.
        if not cur_team.get(name):
            continue
        rank = ranks.get(name)
        if not rank or not (min_rank <= int(rank) <= max_rank):
            continue
        proj = {}
        for stat in nfl._USAGE_MODEL:
            try:
                s = nfl.score_prop_nfl(df, name, stat, 0.5, teams=teams, priors=priors,
                                       vol=vol, idx=idx, board=board, rates=rates, cvcache=cv)
            except Exception:
                s = None
            if s:
                proj[stat] = s.get("projection", 0)
        if not proj:
            continue
        order = depths.get(name)
        pts = round(dk_points_nfl(proj) * depth_multiplier(pos, order), 2)
        if pts < _WAIVER_FLOOR.get(pos, 5.0):
            continue
        rows.append({"player": name, "position": pos,
                     # Sleeper's team, not nflverse's — see the free-agent guard above.
                     "team": cur_team.get(name) or teams.get(str(sub.iloc[0].get("player_id", ""))) or "",
                     "proj_points": pts, "consensus_rank": int(rank),
                     "depth": order, "depth_mult": depth_multiplier(pos, order)})

    # Rank within position on each axis, then take the gap.
    by_pos: dict = {}
    for r in rows:
        by_pos.setdefault(r["position"], []).append(r)
    out = []
    for pos, group in by_pos.items():
        ours = sorted(group, key=lambda r: -r["proj_points"])
        theirs = sorted(group, key=lambda r: r["consensus_rank"])
        our_rank = {r["player"]: i + 1 for i, r in enumerate(ours)}
        their_rank = {r["player"]: i + 1 for i, r in enumerate(theirs)}
        for r in group:
            r["our_pos_rank"] = our_rank[r["player"]]
            r["consensus_pos_rank"] = their_rank[r["player"]]
            r["gap"] = their_rank[r["player"]] - our_rank[r["player"]]
            if r["gap"] > 0:
                out.append(r)
    out.sort(key=lambda r: -r["gap"])
    return out[:limit]
