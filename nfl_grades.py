"""
nfl_grades.py — a weekly performance grade for every QB, RB, WR and TE.

One number, 0-100, for how well a player played THIS week, with a letter on top. Built
from nflverse weekly player stats, position by position, out of four components:

  efficiency  EPA per play, plus CPOE for quarterbacks. Shrunk toward zero on small
              samples, so one long catch on two targets cannot top the week.
  production  yards and touchdowns, a touchdown counted as _TD_YARDS yards.
  usage       share of the team's work. Not graded for quarterbacks, who all take ~100%.
  mistakes    interceptions and lost fumbles, and for quarterbacks a fraction of each sack.

Each component is a z-score against a REFERENCE season's player-weeks at the same
position, the components are blended with position weights, and the blend is expressed
as a percentile of that reference season's blends. So a score of 90 means "better than
90% of last season's player-weeks at his position". That means the same thing in Week 1
as in Week 17, and a week nobody played well does not force someone to an A+ the way
ranking within the week would.

This is a description of the week, not a projection. A great grade says he played well;
whether he does it again is what nfl_analysis is for.
"""
from __future__ import annotations

import bisect

_POS = ("QB", "RB", "WR", "TE")

# Minimum work to be graded at all: dropbacks for a QB, carries + targets for a RB,
# targets for a WR or TE. Below this a week is a cameo, and a percentile of a cameo is
# noise wearing a letter.
_MIN_WORK = {"QB": 15, "RB": 6, "WR": 3, "TE": 3}

# Pseudo-plays of zero EPA added before dividing, the same shrinkage idea as
# nfl_analysis._shrink. Without it the efficiency leader every week is a receiver who
# caught his only target for 40 yards.
_EPA_SHRINK = {"QB": 10, "RB": 8, "WR": 5, "TE": 5}

_TD_YARDS = 20          # a touchdown counts as 20 yards of production
_SACK_WEIGHT = 0.2      # a sack is partly the line's fault, so it is a fifth of a turnover
_QB_CPOE_SHARE = 0.25   # of a QB's efficiency z, how much is CPOE rather than EPA/play

_WEIGHTS = {
    "QB": {"efficiency": 0.45, "production": 0.35, "usage": 0.00, "mistakes": 0.20},
    "RB": {"efficiency": 0.30, "production": 0.40, "usage": 0.20, "mistakes": 0.10},
    "WR": {"efficiency": 0.35, "production": 0.40, "usage": 0.20, "mistakes": 0.05},
    "TE": {"efficiency": 0.35, "production": 0.40, "usage": 0.20, "mistakes": 0.05},
}
_COMPONENTS = ("efficiency", "production", "usage", "mistakes")

# Score floor for each letter. Scores are percentiles of the reference season, so a C is
# roughly a median week and an A+ is a top-3% week.
_LETTERS = [(97, "A+"), (90, "A"), (85, "A-"), (80, "B+"), (70, "B"), (65, "B-"),
            (60, "C+"), (45, "C"), (40, "C-"), (20, "D"), (0, "F")]

_REF: dict = {}

# The weekly frame, cached for an hour. Grading a season calls grade_week once per week,
# and each call re-downloading the same parquet made a 17-week season cost 17 downloads.
# An hour, not forever: a long-lived dashboard has to see a new week when it posts.
_FRAMES: dict = {}
_FRAME_TTL = 3600


def _frame(season: int):
    import time
    import nfl_analysis as nfl
    hit = _FRAMES.get(season)
    if hit and time.time() - hit[0] < _FRAME_TTL:
        return hit[1]
    df = nfl._load_weekly(season)
    _FRAMES[season] = (time.time(), df)
    return df


_TEAMS_URL = "https://github.com/nflverse/nflverse-data/releases/download/teams/teams_colors_logos.csv"
_TEAMS: dict = {}


def team_meta() -> dict:
    """team -> {name, nick, color, color2, logo}. {} on any failure; callers fall back."""
    if _TEAMS:
        return _TEAMS
    try:
        import pandas as pd
        t = pd.read_csv(_TEAMS_URL)
        for r in t.itertuples():
            _TEAMS[str(r.team_abbr)] = {"name": r.team_name, "nick": r.team_nick,
                                        "color": r.team_color, "color2": r.team_color2,
                                        "logo": r.team_logo_espn}
    except Exception:
        return {}
    return _TEAMS


def letter(score) -> str:
    if score is None or score != score:
        return "—"
    for floor, grade in _LETTERS:
        if score >= floor:
            return grade
    return "F"


def _col(d, c):
    import pandas as pd
    return d[c].fillna(0.0).astype(float) if c in d.columns else pd.Series(0.0, index=d.index)


def components(df):
    """Raw component values for every skill-position player-week, before any scaling."""
    d = df[df["position"].isin(_POS)].copy()
    team = (df.groupby(["team", "week"])[["carries", "targets"]].sum()
              .rename(columns={"carries": "team_carries", "targets": "team_targets"}))
    d = d.join(team, on=["team", "week"])

    att, sacks = _col(d, "attempts"), _col(d, "sacks_suffered")
    carries, targets = _col(d, "carries"), _col(d, "targets")
    plays = att + sacks + carries + targets
    epa = _col(d, "passing_epa") + _col(d, "rushing_epa") + _col(d, "receiving_epa")
    yards = _col(d, "passing_yards") + _col(d, "rushing_yards") + _col(d, "receiving_yards")
    tds = _col(d, "passing_tds") + _col(d, "rushing_tds") + _col(d, "receiving_tds")
    fum_lost = (_col(d, "sack_fumbles_lost") + _col(d, "rushing_fumbles_lost")
                + _col(d, "receiving_fumbles_lost"))
    qb = d["position"] == "QB"
    rb = d["position"] == "RB"

    d["efficiency_raw"] = epa / (plays + d["position"].map(_EPA_SHRINK))
    d["cpoe_raw"] = _col(d, "passing_cpoe")
    d["production_raw"] = yards + _TD_YARDS * tds
    team_opps = (d["team_carries"] + d["team_targets"]).where(lambda s: s > 0)
    d["usage_raw"] = ((carries + targets) / team_opps).where(rb, _col(d, "wopr")).fillna(0.0)
    d.loc[qb, "usage_raw"] = 0.0
    d["mistakes_raw"] = _col(d, "passing_interceptions") + fum_lost + (_SACK_WEIGHT * sacks).where(qb, 0.0)
    d["work"] = (att + sacks).where(qb, (carries + targets).where(rb, targets))
    d["qualified"] = d["work"] >= d["position"].map(_MIN_WORK)
    return d


def _zscores(d, ref: dict):
    """Per-component z columns, oriented so higher is always better."""
    import pandas as pd
    out = pd.DataFrame(index=d.index)
    for pos in _POS:
        m = d["position"] == pos
        if not m.any() or pos not in ref:
            continue
        st = ref[pos]["stats"]

        def z(col):
            mu, sd = st[col]
            return (d.loc[m, col] - mu) / sd if sd > 0 else 0.0

        eff = z("efficiency_raw")
        if pos == "QB":
            eff = (1 - _QB_CPOE_SHARE) * eff + _QB_CPOE_SHARE * z("cpoe_raw")
        out.loc[m, "efficiency"] = eff
        out.loc[m, "production"] = z("production_raw")
        out.loc[m, "usage"] = z("usage_raw") if pos != "QB" else 0.0
        out.loc[m, "mistakes"] = -z("mistakes_raw")
    return out


def _composite(zs, positions):
    import pandas as pd
    w = pd.DataFrame([_WEIGHTS[p] for p in positions], index=zs.index)
    return sum(zs[c].fillna(0.0) * w[c] for c in _COMPONENTS)


def reference(season: int) -> dict:
    """Means, spreads and sorted distributions for one season's qualified player-weeks."""
    if season in _REF:
        return _REF[season]
    d = components(_frame(season))
    d = d[d["qualified"]]
    ref = {}
    for pos in _POS:
        g = d[d["position"] == pos]
        if g.empty:
            continue
        ref[pos] = {"stats": {c: (float(g[c].mean()), float(g[c].std(ddof=0)))
                              for c in ("efficiency_raw", "cpoe_raw", "production_raw",
                                        "usage_raw", "mistakes_raw")}}
    zs = _zscores(d, ref)
    comp = _composite(zs, d["position"])
    for pos in _POS:
        if pos not in ref:
            continue
        m = d["position"] == pos
        ref[pos]["composite"] = sorted(comp[m].tolist())
        ref[pos]["dist"] = {c: sorted(zs.loc[m, c].fillna(0.0).tolist()) for c in _COMPONENTS}
    _REF[season] = ref
    return ref


def _pct(sorted_vals: list, x: float) -> float:
    """Share of reference weeks at or below x.

    At-or-below, not strictly-below. Mistakes are mostly zero, so ~98% of receiver weeks
    tie at the clean end, and strictly-below scored every clean week as the 2nd percentile.
    """
    if not sorted_vals or x != x:
        return float("nan")
    return 100.0 * bisect.bisect_right(sorted_vals, x) / len(sorted_vals)


def _stat_line(r) -> str:
    g = lambda c: int(round(float(r.get(c) or 0)))
    pos = r["position"]
    if pos == "QB":
        s = (f"{g('completions')}/{g('attempts')}, {g('passing_yards')} yds, "
             f"{g('passing_tds')} TD, {g('passing_interceptions')} INT")
        if g("rushing_yards") >= 15 or g("rushing_tds"):
            s += f" · {g('carries')}-{g('rushing_yards')} rush" + (f", {g('rushing_tds')} TD" if g("rushing_tds") else "")
        return s
    if pos == "RB":
        s = f"{g('carries')}-{g('rushing_yards')} rush"
        if g("rushing_tds"):
            s += f", {g('rushing_tds')} TD"
        if g("targets"):
            s += f" · {g('receptions')}/{g('targets')}, {g('receiving_yards')} rec"
            if g("receiving_tds"):
                s += f", {g('receiving_tds')} TD"
        return s
    s = f"{g('receptions')}/{g('targets')}, {g('receiving_yards')} yds"
    if g("receiving_tds"):
        s += f", {g('receiving_tds')} TD"
    return s


def grade_week(season: int, week: int, ref_season: int | None = None):
    """
    Every graded skill-position player for one week, best first.

    Columns: player, player_id, position, team, opponent, score (0-100), grade, the four
    component percentiles (mistakes: higher = cleaner), ppr, line, and the raw work count.
    Players below _MIN_WORK are left out rather than given a grade.
    """
    import pandas as pd
    ref_season = ref_season or season - 1
    ref = reference(ref_season)
    d = components(_frame(season))
    d = d[(d["week"] == week) & d["qualified"]]
    if d.empty:
        return pd.DataFrame()
    zs = _zscores(d, ref)
    comp = _composite(zs, d["position"])
    rows = []
    for i, r in d.iterrows():
        pos = r["position"]
        rp = ref.get(pos)
        if not rp:
            continue
        score = _pct(rp["composite"], float(comp[i]))
        row = {"player": r["player_display_name"], "player_id": r.get("player_id"),
               "position": pos, "team": r["team"], "opponent": r.get("opponent_team", ""),
               "score": round(score, 1), "grade": letter(score)}
        for c in _COMPONENTS:
            row[c] = (None if (pos == "QB" and c == "usage")
                      else round(_pct(rp["dist"][c], float(zs.at[i, c])), 0))
        row["ppr"] = round(float(r.get("fantasy_points_ppr") or 0), 1)
        row["line"] = _stat_line(r)
        row["work"] = int(r["work"])
        row["headshot"] = r.get("headshot_url") if isinstance(r.get("headshot_url"), str) else None
        rows.append(row)
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)


def weeks_available(season: int) -> list:
    try:
        return sorted(int(w) for w in _frame(season)["week"].unique())
    except Exception:
        return []


def grade_season(season: int, ref_season: int | None = None):
    """Every graded player-week of a season so far, with a `week` column."""
    import pandas as pd
    frames = []
    for w in weeks_available(season):
        g = grade_week(season, w, ref_season)
        if not g.empty:
            frames.append(g.assign(week=w))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
