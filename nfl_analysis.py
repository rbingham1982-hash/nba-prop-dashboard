"""
NFL player analysis — the ANALYZE foundation (game logs, prop hit-rates, opponent splits,
next-game projections). This is also the projection layer the future NFL betting pipeline
will score props against.

Data: nflverse weekly player stats parquet, read directly (nfl_data_py won't install on this
py3.14 venv — see nfl_fantasy_rankings.py for the same pattern).
"""
from __future__ import annotations
import datetime

_WEEKLY = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{y}.parquet"

# Bettable stat -> (weekly column, display label). The markets a props pipeline will price.
PROP_STATS = {
    "Passing Yards":   ("passing_yards", "Pass Yds"),
    "Passing TDs":     ("passing_tds", "Pass TD"),
    "Completions":     ("completions", "Comp"),
    "Rushing Yards":   ("rushing_yards", "Rush Yds"),
    "Rushing TDs":     ("rushing_tds", "Rush TD"),
    "Carries":         ("carries", "Carries"),
    "Receptions":      ("receptions", "Rec"),
    "Receiving Yards": ("receiving_yards", "Rec Yds"),
    "Receiving TDs":   ("receiving_tds", "Rec TD"),
    "Fantasy (PPR)":   ("fantasy_points_ppr", "PPR"),
}
# Which stats matter by position (so the UI doesn't offer Passing Yards for a WR).
POS_STATS = {
    "QB": ["Passing Yards", "Passing TDs", "Completions", "Rushing Yards", "Fantasy (PPR)"],
    "RB": ["Rushing Yards", "Rushing TDs", "Carries", "Receptions", "Receiving Yards", "Fantasy (PPR)"],
    "WR": ["Receptions", "Receiving Yards", "Receiving TDs", "Rushing Yards", "Fantasy (PPR)"],
    "TE": ["Receptions", "Receiving Yards", "Receiving TDs", "Fantasy (PPR)"],
}


def _load_weekly(season: int):
    import pandas as pd
    df = pd.read_parquet(_WEEKLY.format(y=season))
    return df[df["season_type"] == "REG"].copy()


def latest_season_with_data(guess: int | None = None) -> int:
    """Most recent season that has weekly data (current season before Week 1 falls back a year)."""
    import pandas as pd
    y = guess or datetime.datetime.now().year
    for cand in (y, y - 1):
        try:
            pd.read_parquet(_WEEKLY.format(y=cand))
            return cand
        except Exception:
            continue
    return y - 1


def get_season(season: int | None = None):
    """Cached weekly frame for a season (resolved to the latest available if None)."""
    s = season or latest_season_with_data()
    return s, _load_weekly(s)


def teams_in(df) -> list:
    return sorted(t for t in df["team"].dropna().unique())


def players_on_team(df, team: str) -> list:
    """Fantasy-relevant players on a team, most-used first (by games played)."""
    sub = df[(df["team"] == team) & (df["position"].isin(["QB", "RB", "WR", "TE"]))]
    order = (sub.groupby(["player_display_name", "position"])
                .size().reset_index(name="g").sort_values("g", ascending=False))
    return [(r["player_display_name"], r["position"]) for _, r in order.iterrows()]


def game_log(df, player: str) -> list:
    """Week-by-week rows for a player, oldest→newest."""
    sub = df[df["player_display_name"] == player].sort_values("week")
    out = []
    for _, r in sub.iterrows():
        row = {"week": int(r["week"]), "opp": r.get("opponent_team", ""), "team": r.get("team", "")}
        for label, (col, _) in PROP_STATS.items():
            row[label] = float(r.get(col, 0) or 0)
        out.append(row)
    return out


def hit_rate(log: list, stat: str, line: float) -> dict:
    """Over-hit rate for a prop line across the game log, plus the per-game values."""
    vals = [g[stat] for g in log]
    n = len(vals)
    if n == 0:
        return {"n": 0, "hit_rate": None, "avg": None, "over": 0, "values": []}
    over = sum(1 for v in vals if v > line)
    return {"n": n, "hit_rate": round(over / n, 3), "avg": round(sum(vals) / n, 1),
            "over": over, "values": vals}


def project(log: list, stat: str, recent_n: int = 5) -> float | None:
    """
    Next-game projection: recency-weighted mean (last recent_n games weighted 2×). Simple and
    honest — a real matchup/pace model is a later layer; this is the baseline hit-rates use.
    """
    vals = [g[stat] for g in log]
    if not vals:
        return None
    recent = vals[-recent_n:]
    w_recent, w_all = 2.0, 1.0
    num = w_recent * (sum(recent) / len(recent)) + w_all * (sum(vals) / len(vals))
    return round(num / (w_recent + w_all), 1)


def season_for_date(d) -> int:
    """NFL season for a date. The season spans Sep→Feb, so Jan/Feb games belong to the prior year."""
    return d.year - 1 if d.month <= 2 else d.year


def stat_for_game(df, player: str, stat_label: str, opponents=None, week=None):
    """
    A player's actual value of a stat for one game — the resolution lookup. Identify the game
    by week when known, else by opponent (the leg's matchup); returns None if not found.
    """
    col = PROP_STATS.get(stat_label, (None,))[0]
    if not col or col not in df.columns:
        return None
    sub = df[df["player_display_name"] == player]
    if sub.empty:
        return None
    if week is not None:
        sub = sub[sub["week"] == int(week)]
    elif opponents:
        sub = sub[sub["opponent_team"].isin(list(opponents))]
    if sub.empty:
        return None
    return float(sub.iloc[0].get(col, 0) or 0)


def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    import math
    if sigma <= 0:
        return 1.0 if x < mu else 0.0
    return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def _implied_from_odds(american) -> float:
    a = float(american)
    return (100 / (a + 100)) if a > 0 else (abs(a) / (abs(a) + 100))


def score_prop(df, player: str, stat: str, line: float, american_odds=None,
               market_blend: float = 0.35, recent_n: int = 5) -> dict:
    """
    Model probability that `player` goes OVER `line` on `stat`, from a normal(projection,
    historical σ) — the projection is forward-looking (recency-weighted), σ carries the
    player's game-to-game variance. When market odds are given, blend toward the book's
    implied prob (market_blend = model's share) and report the edge — mirroring how the
    MLB/WNBA scorer trusts the market as the primary signal and the model as an edge nudge.

    Returns {} if the player has too little history to score.
    """
    import statistics as _st
    log = game_log(df, player)
    vals = [g[stat] for g in log]
    if len(vals) < 3:
        return {}
    mu = project(log, stat, recent_n=recent_n)
    sigma = _st.pstdev(vals) if len(vals) > 1 else max(mu * 0.5, 1.0)
    model_over = round(1 - _norm_cdf(line, mu, sigma), 4)

    out = {"player": player, "stat": stat, "line": float(line), "n": len(vals),
           "projection": mu, "sigma": round(sigma, 1),
           "model_over": model_over, "hit_rate_hist": round(sum(1 for v in vals if v > line) / len(vals), 3)}
    if american_odds is not None:
        implied = _implied_from_odds(american_odds)
        blended = round(market_blend * model_over + (1 - market_blend) * implied, 4)
        out.update({"american_odds": int(american_odds), "implied": round(implied, 4),
                    "blended_over": blended, "edge": round(blended - implied, 4),
                    "model_edge": round(model_over - implied, 4)})
    return out


def opponent_splits(df, player: str, stat: str) -> list:
    """Player's per-game average of a stat, grouped by opponent (min 1 game)."""
    col = PROP_STATS[stat][0]
    sub = df[df["player_display_name"] == player]
    rows = []
    for opp, g in sub.groupby("opponent_team"):
        rows.append({"opp": opp, "games": len(g), "avg": round(float(g[col].mean()), 1)})
    return sorted(rows, key=lambda r: r["avg"], reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Usage-share projection — the cold-start model for Weeks 1-2
# ─────────────────────────────────────────────────────────────────────────────
#
# project() above is a recency-weighted mean of a player's own raw per-game numbers.
# That is honest mid-season and actively misleading in Week 1, because NFL rosters turn
# over every offseason: a receiver who saw 8 targets a game on a 36-target offence and
# then signed with a 24-target offence does not carry his old volume with him.
#
# So project the two halves separately. USAGE (what share of his team's work he gets)
# travels with the player and stabilises within a few games; VOLUME (how much work the
# team generates) belongs to the new team. Multiply the player's share by the new team's
# per-game volume, apply EFFICIENCY (yards per opportunity), and the projection is
# rebased onto where he actually plays.
#
# Everything gets shrunk toward a position prior — see _shrink. Touchdown rates get the
# heaviest shrinkage of all: they are the noisiest thing on the board, and a leg priced
# off an unregressed TD rate is the same mistake that cost the MLB board 0-for-48 on
# home-run stacks in 2026-W34.

_ROSTER_URL = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{y}.parquet"

# The roster feed writes Arizona as AZ, the weekly stats feed as ARI. One club, and
# joining on the raw string silently drops it.
_TEAM_FIX = {"AZ": "ARI"}

# stat -> (opportunity column, per-opportunity rate column, shrink weight for that rate).
# The weight is a pseudo-count in OPPORTUNITIES: k=14 means a receiver needs 14 targets
# before his own TD rate outweighs his position's, k=4 that catch rate settles quickly.
_USAGE_MODEL = {
    "Passing Yards":   ("attempts", "passing_yards",   6),
    "Passing TDs":     ("attempts", "passing_tds",    14),
    "Completions":     ("attempts", "completions",     4),
    "Rushing Yards":   ("carries",  "rushing_yards",   6),
    "Rushing TDs":     ("carries",  "rushing_tds",    14),
    "Carries":         ("carries",  None,              0),
    "Receptions":      ("targets",  "receptions",      4),
    "Receiving Yards": ("targets",  "receiving_yards", 6),
    "Receiving TDs":   ("targets",  "receiving_tds",  14),
}
_VOLUME_COL = {"attempts": "attempts", "carries": "carries", "targets": "targets"}

_SHARE_MIN_GAMES = 4          # below this, a share is damped toward zero rather than trusted
_OPP_CLAMP = (0.88, 1.12)     # an opponent adjustment may move a projection ~12%, no more


def _shrink(value: float, prior: float, n: float, k: float) -> float:
    """Regress an observed rate toward a prior, weighted by how much of it we saw."""
    if k <= 0 or n <= 0:
        return value
    return (n * value + k * prior) / (n + k)


def current_teams(target_season: int) -> dict:
    """player_id -> team for the target season, from the nflverse roster feed."""
    import pandas as pd
    df = pd.read_parquet(_ROSTER_URL.format(y=target_season))
    idc = "player_id" if "player_id" in df.columns else "gsis_id"
    out = {}
    for pid, team in zip(df[idc], df["team"]):
        if pid and team:
            out[str(pid)] = _TEAM_FIX.get(str(team), str(team))
    return out


def team_volume(df) -> dict:
    """team -> mean per-game {attempts, targets, carries}. The denominator for every share."""
    g = df.groupby(["team", "week"])[["attempts", "targets", "carries"]].sum()
    m = g.groupby("team").mean()
    return {t: {c: float(r[c]) for c in ("attempts", "targets", "carries")}
            for t, r in m.iterrows()}


def position_priors(df) -> dict:
    """(position, opportunity, rate_col) -> league rate, the prior every player shrinks to."""
    priors: dict = {}
    for pos, g in df.groupby("position"):
        for _stat, (opp_col, rate_col, _k) in _USAGE_MODEL.items():
            if rate_col is None:
                continue
            denom = float(g[opp_col].sum())
            if denom > 0:
                priors[(pos, opp_col, rate_col)] = float(g[rate_col].sum()) / denom
    return priors


def player_index(df) -> dict:
    """
    player -> his rows, built once.

    Every scorer below starts by slicing one player out of the weekly frame, and
    `df[df[...] == player]` is a full scan of ~18.5k rows. Doing that several times per
    prop put a single scored prop at ~0.4s, which is 7 minutes for a 1,000-prop board and
    would have blown the resolve/generate budget outright. Grouping once makes the lookup
    a dict hit; pass the result through and a prop scores in ~1ms.
    """
    return {name: g.sort_values("week") for name, g in df.groupby("player_display_name")}


def _rows_for(df, player: str, idx: dict | None):
    if idx is not None:
        return idx.get(player)
    sub = df[df["player_display_name"] == player]
    return None if sub.empty else sub.sort_values("week")


def _log_from_rows(sub) -> list:
    """game_log() for rows already sliced — same shape, no rescan."""
    out = []
    for _, r in sub.iterrows():
        row = {"week": int(r["week"]), "opp": r.get("opponent_team", ""), "team": r.get("team", "")}
        for label, (col, _) in PROP_STATS.items():
            row[label] = float(r.get(col, 0) or 0)
        out.append(row)
    return out


def usage_profile(df, player: str, priors: dict | None = None, vol: dict | None = None,
                  idx: dict | None = None) -> dict:
    """
    A player's share of his team's work and his per-opportunity efficiency, both shrunk
    toward his position. Shares are what travel to a new team; raw per-game totals do not.
    """
    sub = _rows_for(df, player, idx)
    if sub is None or sub.empty:
        return {}
    n = len(sub)
    pos = str(sub.iloc[0].get("position", ""))
    team = str(sub.iloc[-1].get("team", ""))
    priors = priors if priors is not None else position_priors(df)
    vol = vol if vol is not None else team_volume(df)

    shares, rates = {}, {}
    for opp_col in ("attempts", "targets", "carries"):
        team_pg = (vol.get(team) or {}).get(opp_col, 0.0)
        player_pg = float(sub[opp_col].sum()) / n
        share = (player_pg / team_pg) if team_pg > 0 else 0.0
        # A share off one or two games is mostly noise, and the honest prior is not the
        # league median (most rostered players see none of the work) but zero — an
        # unproven player is assumed marginal until the sample says otherwise.
        shares[opp_col] = share if n >= _SHARE_MIN_GAMES else share * (n / _SHARE_MIN_GAMES)

    for _stat, (opp_col, rate_col, k) in _USAGE_MODEL.items():
        if rate_col is None:
            continue
        prior = priors.get((pos, opp_col, rate_col))
        if prior is None:
            continue
        opps = float(sub[opp_col].sum())
        observed = (float(sub[rate_col].sum()) / opps) if opps > 0 else prior
        rates[(opp_col, rate_col)] = _shrink(observed, prior, opps, k)

    return {"player": player, "position": pos, "team_prev": team, "games": n,
            "shares": shares, "rates": rates}


def defense_factor(df, opponent: str, position: str, stat: str) -> float:
    """
    How much this defence inflates or suppresses a stat for this position, as a multiplier
    on the league mean. Clamped: a 17-game sample cannot justify moving a projection more
    than ~12%, and an unclamped factor off a handful of games is how a model talks itself
    into a bad line.
    """
    col = PROP_STATS.get(stat, (None,))[0]
    if not col or col not in df.columns or not opponent:
        return 1.0
    pos_rows = df[df["position"] == position]
    if pos_rows.empty:
        return 1.0
    allowed = pos_rows.groupby(["opponent_team", "week"])[col].sum().groupby("opponent_team").mean()
    if opponent not in allowed.index or float(allowed.mean()) <= 0:
        return 1.0
    factor = float(allowed[opponent]) / float(allowed.mean())
    return max(_OPP_CLAMP[0], min(_OPP_CLAMP[1], factor))


def project_usage(df, player: str, stat: str, opponent: str | None = None,
                  teams: dict | None = None, priors: dict | None = None,
                  vol: dict | None = None, idx: dict | None = None,
                  dcache: dict | None = None) -> dict:
    """
    Rebased projection: the player's shrunk usage share x his CURRENT team's per-game
    volume x his shrunk efficiency, adjusted for the opponent.

    Returns {} when there is not enough history to say anything. `changed_team` marks a
    projection rebased onto a different offence than the history came from — those are
    the numbers to distrust first, so they are surfaced rather than folded in silently.
    """
    model = _USAGE_MODEL.get(stat)
    if model is None:
        return {}
    prof = usage_profile(df, player, priors=priors, vol=vol, idx=idx)
    if not prof or prof["games"] < 3:
        return {}
    opp_col, rate_col, _k = model

    sub = _rows_for(df, player, idx)
    pid = str(sub.iloc[0].get("player_id", ""))
    team_now = (teams or {}).get(pid) or prof["team_prev"]
    changed = bool(team_now and prof["team_prev"] and team_now != prof["team_prev"])

    vol = vol if vol is not None else team_volume(df)
    team_pg = (vol.get(team_now) or vol.get(prof["team_prev"]) or {}).get(_VOLUME_COL[opp_col], 0.0)
    opportunities = prof["shares"].get(opp_col, 0.0) * team_pg
    if opportunities <= 0:
        return {}

    mu = opportunities if rate_col is None else opportunities * prof["rates"].get((opp_col, rate_col), 0.0)
    if opponent:
        # Memoised: the factor is a full groupby over the position's rows and depends only
        # on (opponent, position, stat), so one slate of props would otherwise recompute
        # the same handful of numbers hundreds of times.
        ck = (opponent, prof["position"], stat)
        if dcache is not None and ck in dcache:
            dfac = dcache[ck]
        else:
            dfac = defense_factor(df, opponent, prof["position"], stat)
            if dcache is not None:
                dcache[ck] = dfac
    else:
        dfac = 1.0
    mu *= dfac

    return {"player": player, "stat": stat, "position": prof["position"],
            "team_prev": prof["team_prev"], "team_now": team_now, "changed_team": changed,
            "games": prof["games"], "opportunities": round(opportunities, 2),
            "def_factor": round(dfac, 3), "projection": round(mu, 2)}


def score_prop_usage(df, player: str, stat: str, line: float, american_odds=None,
                     opponent: str | None = None, market_blend: float = 0.35,
                     teams: dict | None = None, priors: dict | None = None,
                     vol: dict | None = None, idx: dict | None = None,
                     dcache: dict | None = None) -> dict:
    """
    score_prop, but off the rebased projection — the Week 1-2 scorer.

    Sigma comes from the player's own game-to-game spread, rescaled by however much the
    mean moved. Keeping the raw sigma after rebasing a mean down 30% would leave the
    distribution far too wide for the new role; preserving the coefficient of variation
    keeps the shape and moves the location, which is what actually changed. The floor
    stops a player with a flat history from pricing a real tail at ~0.
    """
    import statistics as _st
    proj = project_usage(df, player, stat, opponent=opponent, teams=teams,
                         priors=priors, vol=vol, idx=idx, dcache=dcache)
    if not proj:
        return {}
    sub = _rows_for(df, player, idx)
    if sub is None:
        return {}
    vals = [g[stat] for g in _log_from_rows(sub) if stat in g]
    if len(vals) < 3:
        return {}
    raw_mu = sum(vals) / len(vals)
    raw_sigma = _st.pstdev(vals) if len(vals) > 1 else max(raw_mu * 0.5, 1.0)
    mu = proj["projection"]
    sigma = (raw_sigma * (mu / raw_mu)) if raw_mu > 0 else raw_sigma
    sigma = max(sigma, 0.35 * max(mu, 0.5))

    model_over = round(1 - _norm_cdf(line, mu, sigma), 4)
    out = dict(proj)
    out.update({"line": float(line), "sigma": round(sigma, 2), "model_over": model_over,
                "hit_rate_hist": round(sum(1 for v in vals if v > line) / len(vals), 3),
                "n": len(vals)})
    if american_odds is not None:
        implied = _implied_from_odds(american_odds)
        blended = round(market_blend * model_over + (1 - market_blend) * implied, 4)
        out.update({"american_odds": int(american_odds), "implied": round(implied, 4),
                    "blended_over": blended, "edge": round(blended - implied, 4),
                    "model_edge": round(model_over - implied, 4)})
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    s, df = get_season()
    print(f"season {s} — {len(df)} REG player-weeks, {df['team'].nunique()} teams")
    log = game_log(df, "Puka Nacua")
    print(f"\nPuka Nacua game log ({len(log)} games):")
    for g in log[:6]:
        print(f"  Wk{g['week']:>2} vs {g['opp']:<3}  {g['Receptions']:.0f} rec, {g['Receiving Yards']:.0f} yds")
    hr = hit_rate(log, "Receiving Yards", 79.5)
    print(f"\nRec Yds o79.5: {hr['over']}/{hr['n']} = {hr['hit_rate']*100:.0f}% | avg {hr['avg']} | proj {project(log,'Receiving Yards')}")
