"""
nfl_matchup.py — how each defense has played against each offensive role: RB1, WR3, TE1.

"The Jets allow 106 rushing yards a game" says nothing about whether a team's RB2 will eat.
This splits every defense's season by the role of the player it faced, so a player's
matchup can be read against his own job.

ROLE IN A GAME is decided before it, among the players who actually played: first by work
per game in the team's earlier games (carries + targets for a back, targets for a receiver,
dropbacks for a quarterback), then by the depth chart as it stood before kickoff (nflverse's
daily ESPN snapshots, the ones nfl_analysis.depth_context uses), then by work that day.
When an RB1 sits, the next man up is that game's RB1, which is the job the defense faced.

Not the depth chart alone: ESPN's chart lags. In 2026 Week 4 it still listed Jaylen Wright
as Miami's RB1 while Ollie Gordon II had 20 carries to his 3 over the two games before. And
not work in the game itself, which would make "RB1" mean "whoever got the ball" and credit
every big day to the role after the fact.

The samples are a few games per defense per role, and a defense's numbers carry the
offenses it happened to face. The page prints the number of games beside every figure.
"""
from __future__ import annotations

import pandas as pd

ROLES = {"QB": 1, "RB": 2, "WR": 3, "TE": 2}       # how deep each position is tracked

# (label, column or ratio, higher-is-better-for-the-offense). Per game unless a ratio.
ROLE_STATS = {
    "QB": [("PPR", "fantasy_points_ppr"), ("Pass yds", "passing_yards"), ("Pass TD", "passing_tds"),
           ("INT", "passing_interceptions"), ("Sacks", "sacks_suffered"), ("Rush yds", "rushing_yards")],
    "RB": [("PPR", "fantasy_points_ppr"), ("Carries", "carries"), ("Rush yds", "rushing_yards"),
           ("Rush TD", "rushing_tds"), ("Targets", "targets"), ("Rec yds", "receiving_yards")],
    "WR": [("PPR", "fantasy_points_ppr"), ("Targets", "targets"), ("Rec", "receptions"),
           ("Rec yds", "receiving_yards"), ("Rec TD", "receiving_tds")],
    "TE": [("PPR", "fantasy_points_ppr"), ("Targets", "targets"), ("Rec", "receptions"),
           ("Rec yds", "receiving_yards"), ("Rec TD", "receiving_tds")],
}
_LOWER_IS_BETTER = {"INT", "Sacks"}     # for the offense; every other stat, more is better
_COLS = sorted({c for v in ROLE_STATS.values() for _, c in v}
               | {"rushing_epa", "receiving_epa", "passing_epa", "attempts", "completions"})


def role_games(season: int) -> pd.DataFrame:
    """One row per skill player per game, with his role in that game (RB1, WR3, ...)."""
    import nfl_analysis as nfl
    import nfl_game_model as gm
    wk = nfl._load_weekly(season)
    wk = wk[wk["position"].isin(list(ROLES))].copy()
    wk["player_id"] = wk["player_id"].astype(str)
    depth = nfl._depth_frame(season)
    snaps = {t: g for t, g in depth.groupby("team")}
    sch = gm.schedule()
    sch = sch[sch["season"] == season]
    kickoff = {}
    for w, day, h, a in zip(sch["week"], sch["gameday"], sch["home_team"], sch["away_team"]):
        kickoff[(h, int(w))] = kickoff[(a, int(w))] = pd.Timestamp(day, tz="UTC")
    # Work per game played BEFORE each week, for the same team (a trade starts him over).
    work = {"QB": wk["attempts"].fillna(0) + wk["sacks_suffered"].fillna(0),
            "RB": wk["carries"].fillna(0) + wk["targets"].fillna(0)}
    wk["_work"] = work["QB"].where(wk["position"] == "QB",
                                   work["RB"].where(wk["position"] == "RB", wk["targets"].fillna(0)))
    wk = wk.sort_values("week")
    grp = wk.groupby(["player_id", "team"])["_work"]
    wk["_prior"] = (grp.cumsum() - wk["_work"]) / grp.cumcount().replace(0, pd.NA)
    wk["_prior"] = pd.to_numeric(wk["_prior"], errors="coerce").fillna(-1)
    out = []
    for (team, week, pos), g in wk.groupby(["team", "week", "position"]):
        ko = kickoff.get((team, int(week)))
        snap = snaps.get(team)
        listed = {}
        if ko is not None and snap is not None:
            before = snap[(snap["dt"] < ko) & (snap["pos_abb"] == pos)]
            if not before.empty:
                last = before[before["dt"] == before["dt"].max()]
                listed = dict(zip(last["gsis_id"], last["pos_rank"]))
        g = g.assign(_listed=g["player_id"].map(listed).fillna(99))
        g = g.sort_values(["_prior", "_listed", "_work"], ascending=[False, True, False])
        for i, (_, r) in enumerate(g.iterrows(), 1):
            if i > ROLES[pos]:
                break
            row = {"season": season, "week": int(week), "team": team, "opponent": r["opponent_team"],
                   "player_id": r["player_id"], "player": r["player_display_name"], "position": pos,
                   "role": f"{pos}{i}", "listed": None if r["_listed"] == 99 else int(r["_listed"])}
            row.update({c: float(r.get(c) or 0) for c in _COLS})
            out.append(row)
    return pd.DataFrame(out)


def defense_vs_role(rg: pd.DataFrame, role: str) -> pd.DataFrame:
    """Every defense against one role: games, per-game averages, and a rank per stat.

    Rank 1 is the toughest defense: the fewest allowed, or for INT and sacks the most."""
    pos = role.rstrip("0123456789")
    d = rg[rg["role"] == role]
    if d.empty:
        return pd.DataFrame()
    stats = ROLE_STATS[pos]
    agg = d.groupby("opponent").agg(G=("week", "nunique"), **{lab: (col, "mean") for lab, col in stats})
    for lab, _ in stats:
        agg[f"{lab} rk"] = agg[lab].rank(ascending=lab not in _LOWER_IS_BETTER, method="min").astype(int)
    return agg.sort_values("PPR rk")


def league_avg(rg: pd.DataFrame, role: str) -> dict:
    pos = role.rstrip("0123456789")
    d = rg[rg["role"] == role]
    return {lab: float(d[col].mean()) for lab, col in ROLE_STATS[pos]} if not d.empty else {}


def position_summary(rg: pd.DataFrame, pos: str) -> pd.DataFrame:
    """A defense against a whole position group (every tracked role combined), per game,
    with the efficiency numbers a per-role split is too thin for. Rank 1 = toughest."""
    d = rg[rg["position"] == pos]
    if d.empty:
        return pd.DataFrame()
    g = d.groupby(["opponent", "week"]).sum(numeric_only=True).reset_index()
    t = g.groupby("opponent").agg(G=("week", "nunique"), **{c: (c, "sum") for c in _COLS})
    out = pd.DataFrame(index=t.index)
    out["G"] = t["G"]
    if pos == "RB":
        out["Rush yds/g"] = t["rushing_yards"] / t["G"]
        out["Yds/carry"] = t["rushing_yards"] / t["carries"]
        out["EPA/carry"] = t["rushing_epa"] / t["carries"]
        out["Rush TD/g"] = t["rushing_tds"] / t["G"]
        out["Rec yds/g"] = t["receiving_yards"] / t["G"]
    elif pos == "QB":
        out["Pass yds/g"] = t["passing_yards"] / t["G"]
        out["EPA/dropback"] = t["passing_epa"] / (t["attempts"] + t["sacks_suffered"])
        out["Pass TD/g"] = t["passing_tds"] / t["G"]
        out["INT/g"] = t["passing_interceptions"] / t["G"]
        out["Sacks/g"] = t["sacks_suffered"] / t["G"]
    else:
        out["Targets/g"] = t["targets"] / t["G"]
        out["Rec yds/g"] = t["receiving_yards"] / t["G"]
        out["Yds/target"] = t["receiving_yards"] / t["targets"]
        out["Catch %"] = 100 * t["receptions"] / t["targets"]
        out["EPA/target"] = t["receiving_epa"] / t["targets"]
        out["Rec TD/g"] = t["receiving_tds"] / t["G"]
    for c in [c for c in out.columns if c != "G"]:
        out[f"{c} rk"] = out[c].rank(ascending=c not in ("INT/g", "Sacks/g"), method="min").astype(int)
    return out


# A prop leg's matchup, for the Sunday board's veto (weekly_picks.parlay_legs).
#
# Backtest 2026-10-31: 1,251 graded FanDuel legs from Weeks 4-8, each defence's split built
# only from the games before that leg's week, 3+ games required. Legs whose defence allowed
# their role 25%+ less than league average (tilted against an over, or for an under) hit
# 45.1% at 49.1% implied (-4.0 +/- 2.7 points); legs tilted 25%+ their way hit 51.7% at
# 49.7% (+1.9 +/- 3.1). The direction held at a 2-game minimum (-2.8 vs +1.4) but neither
# split is significant, receptions ran the other way, and the legs kept still lost money at
# the price. So it only vetoes the worst matchups, it never ranks, and each leg's tilt is
# logged so the weeks ahead can confirm or retire it.
VETO_TILT, VETO_MIN_G = -0.25, 3
LEG_STATS = {"Passing Yards": ("Pass yds", "passing_yards"), "Passing TDs": ("Pass TD", "passing_tds"),
             "Rushing Yards": ("Rush yds", "rushing_yards"), "Receiving Yards": ("Rec yds", "receiving_yards"),
             "Receptions": ("Rec", "receptions"), "Rushing TDs": ("Rush TD", "rushing_tds"),
             "Receiving TDs": ("Rec TD", "receiving_tds")}


def leg_matchup(rg: pd.DataFrame, player: str, stat_type: str, side: str, game_label: str,
                min_games: int = VETO_MIN_G) -> dict | None:
    """
    How the leg's opponent has played the player's role on the leg's stat, against the
    league: tilt = (allowed - league average) / league average, signed so + favours the leg.
    The role is the one he held in his latest game. None when the player, the stat for his
    position, or min_games of that defence against the role can't be found.
    """
    import nfl_analysis as nfl
    if rg.empty or stat_type not in LEG_STATS or "@" not in str(game_label):
        return None
    if "_key" not in rg:
        rg["_key"] = rg["player"].map(nfl._name_key)
    me = rg[rg["_key"] == nfl._name_key(player)]
    if me.empty:
        return None
    me = me.sort_values("week").iloc[-1]
    away, home = [t.strip() for t in str(game_label).split("@")]
    if me["team"] not in (away, home):
        return None                                 # traded, or a namesake on another club
    opp = home if me["team"] == away else away
    lab, col = LEG_STATS[stat_type]
    pos = me["role"].rstrip("0123456789")
    if lab not in [x for x, _ in ROLE_STATS[pos]]:
        return None
    role = rg[rg["role"] == me["role"]]
    d = role[role["opponent"] == opp]
    if d["week"].nunique() < min_games:
        return None
    allowed, avg = float(d[col].mean()), float(role[col].mean())
    if not avg:
        return None
    tilt = (allowed - avg) / avg * (1 if side == "over" else -1)
    return {"role": me["role"], "opp": opp, "allowed": round(allowed, 2), "avg": round(avg, 2),
            "games": int(d["week"].nunique()), "tilt": round(tilt, 3)}


def upcoming_roles(season: int, teams=None, recent: int = 4, depth_slack: int = 1,
                   check_injuries: bool = True) -> dict:
    """
    Who holds each role (RB1, WR2, ...) in each team's NEXT game.

    Why: a board that reads roles off the last game hands a role to whoever filled it and
    misses the starter coming back. On the 2026 Week 11 board Kyle Monangai (16 touches a
    game, out Weeks 5-9) was back, but Chicago's RB2 rows were in Roschon Johnson's name,
    the fill-in. Carnell Tate, DeVonta Smith and Alvin Kamara were the same case. Injured
    players going the other way (Spears, Wan'Dale Robinson) are nfl_injuries' job; this
    function uses it too.

    Ranking matches role_games: work per game (carries + targets for a back, targets for a
    receiver, dropbacks for a QB), here over each player's last `recent` games for the team,
    then the newest depth chart. Eligible:
      * played the team's last game, or
      * RETURNING: missed it, played 2+ games for the team, and sits on the newest depth
        chart within ROLES[pos] + depth_slack at his position. The depth chart is what
        separates a starter back from injury from a player who was simply benched;
      * and, with check_injuries, not Out / Doubtful / IR (nfl_injuries; fails open).

    Returns {team: {role: {player, player_id, work, depth, returning, last_played}}}.
    """
    import nfl_analysis as nfl
    wk = nfl._load_weekly(season)
    wk = wk[wk["position"].isin(list(ROLES))].copy()
    wk["player_id"] = wk["player_id"].astype(str)
    wk["_work"] = (wk["attempts"].fillna(0) + wk["sacks_suffered"].fillna(0)).where(
        wk["position"] == "QB",
        (wk["carries"].fillna(0) + wk["targets"].fillna(0)).where(
            wk["position"] == "RB", wk["targets"].fillna(0)))
    # A player's current team is the one he played for most recently.
    latest = wk.sort_values("week").groupby("player_id").tail(1).set_index("player_id")["team"]
    wk = wk[wk["team"] == wk["player_id"].map(latest)]
    if teams is not None:
        wk = wk[wk["team"].isin(set(teams))]
    team_last = wk.groupby("team")["week"].max()

    depth = nfl._depth_frame(season)
    depth = depth[depth["dt"] == depth.groupby("team")["dt"].transform("max")]
    depth_rank = {(r.team, r.pos_abb, str(r.gsis_id)): int(r.pos_rank)
                  for r in depth.itertuples() if r.gsis_id}

    inj = None
    if check_injuries:
        try:
            import nfl_injuries as inj
            inj.status_map()
        except Exception:
            inj = None

    out = {}
    for team, tg in wk.groupby("team"):
        last_wk = int(team_last[team])
        roles = {}
        for pos, depth_n in ROLES.items():
            per = []
            for pid, pg in tg[tg["position"] == pos].sort_values("week").groupby("player_id"):
                name = pg["player_display_name"].iloc[-1]
                played_last = bool((pg["week"] == last_wk).any())
                dr = depth_rank.get((team, pos, pid))
                # A QB comes back only as the listed starter: a backup's old starts would
                # otherwise outrank the man who took the job (Tanner McKee over Joe Milton).
                slack = 0 if pos == "QB" else depth_slack
                returning = (not played_last and pg["week"].nunique() >= 2
                             and dr is not None and dr <= depth_n + slack)
                if not (played_last or returning):
                    continue
                if inj is not None and inj.is_out(name, team):
                    continue
                per.append({"player": name, "player_id": pid,
                            "work": round(float(pg["_work"].tail(recent).mean()), 2),
                            "depth": dr if dr is not None else 99,
                            "returning": bool(returning),
                            "last_played": int(pg["week"].max())})
            # QBs by the depth chart (one starter; work per game is a history of who was
            # healthy). Everyone else by recent work, depth chart as the tiebreak.
            per.sort(key=(lambda r: (r["depth"], -r["work"])) if pos == "QB"
                     else (lambda r: (-r["work"], r["depth"])))
            if pos == "QB" and not per:
                # A starter with no snaps for the team yet (Josh Johnson, ARI, 2026 Week 11
                # with Carson Beck out): the depth chart is all there is.
                listed = depth[(depth["team"] == team) & (depth["pos_abb"] == "QB")
                               ].sort_values("pos_rank")
                for r in listed.itertuples():
                    if inj is not None and inj.is_out(r.player_name, team):
                        continue
                    per.append({"player": r.player_name, "player_id": str(r.gsis_id),
                                "work": 0.0, "depth": int(r.pos_rank), "returning": False,
                                "last_played": None, "new": True})
                    break
            for i, r in enumerate(per[:depth_n], 1):
                roles[f"{pos}{i}"] = r
        out[team] = roles
    return out


def role_changes(rg: pd.DataFrame, upcoming: dict) -> list:
    """
    Every role whose holder differs between the team's last game (role_games) and its next
    one (upcoming_roles): [{team, role, was, now, why}], why in returning / injury / depth.

    A board built from last-game roles should rename `was` -> `now` for each row, since its
    figures are the defense against the ROLE, not the man, and drop `was` when `now` is None.

    Only changes of PERSONNEL are reported: a role whose last holder is out of the
    position's roles entirely, or whose new holder was not in them last game. Two players
    who both play swapping WR1 and WR2 on a few targets' difference is noise, not news.
    """
    try:
        import nfl_injuries as inj
    except Exception:
        inj = None
    out = []
    last_wk = rg.groupby("team")["week"].max()
    for team, roles in upcoming.items():
        if team not in last_wk:
            continue
        prev = rg[(rg["team"] == team) & (rg["week"] == last_wk[team])]
        prev_by_pos, now_by_pos = {}, {}
        for role, name in zip(prev["role"], prev["player"]):
            prev_by_pos.setdefault(role.rstrip("0123456789"), set()).add(name)
        for role, r in roles.items():
            now_by_pos.setdefault(role.rstrip("0123456789"), set()).add(r["player"])
        for role, was in zip(prev["role"], prev["player"]):
            pos = role.rstrip("0123456789")
            cur = roles.get(role)
            now = cur["player"] if cur else None
            if now == was:
                continue
            if was in now_by_pos.get(pos, set()) and (now is None or now in prev_by_pos.get(pos, set())):
                continue                                    # same people, different order
            if cur and cur.get("new"):
                why = "new starter"
            elif cur and cur["returning"]:
                why = "returning"
            elif inj is not None and inj.is_out(was, team):
                why = "injury"
            else:
                why = "depth"
            status = inj.label(now, team) if (inj is not None and now) else None
            out.append({"team": team, "role": role, "was": was, "now": now, "why": why,
                        "status": status})
    return out


def player_role(season: int, player_id) -> tuple | None:
    """(position, rank on the newest depth chart, team) for a player, or None if unlisted."""
    import nfl_analysis as nfl
    d = nfl._depth_frame(season)
    d = d[d["gsis_id"] == str(player_id)]
    if d.empty:
        return None
    d = d[d["dt"] == d["dt"].max()].sort_values("pos_rank").iloc[0]
    return d["pos_abb"], int(d["pos_rank"]), d["team"]


def next_game(season: int, team: str) -> dict | None:
    """The team's next unplayed game: {'week', 'opponent', 'home', 'gameday'}, or None."""
    import nfl_game_model as gm
    s = gm.schedule()
    s = s[(s["season"] == season) & s["home_score"].isna()
          & ((s["home_team"] == team) | (s["away_team"] == team))].sort_values("gameday")
    if s.empty:
        return None
    r = s.iloc[0]
    home = r["home_team"] == team
    return {"week": int(r["week"]), "opponent": r["away_team"] if home else r["home_team"],
            "home": bool(home), "gameday": str(r["gameday"])}


def role_log(rg: pd.DataFrame, defense: str, role: str) -> pd.DataFrame:
    """Who filled `role` against `defense` each week, and what he did."""
    pos = role.rstrip("0123456789")
    d = rg[(rg["opponent"] == defense) & (rg["role"] == role)].sort_values("week")
    cols = ["week", "team", "player"] + [c for _, c in ROLE_STATS[pos]]
    out = d[cols].rename(columns={"week": "Wk", "team": "Offense", "player": "Player",
                                  **{c: lab for lab, c in ROLE_STATS[pos]}})
    return out.reset_index(drop=True)
