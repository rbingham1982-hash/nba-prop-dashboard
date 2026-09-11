"""
weekly_picks.py — the week's published NFL board, and the record it builds over time.

Two boards go out each week: team predictions against the spread, and a five-leg player
parlay spanning five different stat categories. This module generates both, writes them to
a log BEFORE the games are played, and grades them afterwards against the same public
sources the rest of the project uses.

The writing-before is the whole point. A prediction recorded after kickoff is not a
prediction, and a published record that can be quietly revised is not a record. Every pick
lands in weekly_picks.json keyed by season and week with outcome null, and grading only
ever fills that null in. Nothing regenerates a week that already has picks — see
log_picks() — because the cheapest way to manufacture a good record is to rebuild last
week's board knowing how it turned out.

What the ATS number actually is, stated here so no caller has to guess: the spread model
hits 49.25% against the closing line over 1,359 out-of-sample games, ROI -6%, where
break-even at -110 is 52.38%. It has no demonstrated edge. It is published as the places
where the model and the market disagree most, which is a genuinely interesting thing to
read every week and is not a betting recommendation. If the tracked record ever separates
from that backtest, this log is how we would find out.
"""
from __future__ import annotations

import datetime
import json
import pathlib

LOG = pathlib.Path(__file__).with_name("weekly_picks.json")

# Seasons of history the spread model fits scale and home-field on. Five is enough for a
# stable fit and recent enough that the league it describes still exists.
_FIT_SEASONS = 5

# Cached fit, so a weekly run does not refit from seven seasons of play-by-play every time.
_PARAMS_CACHE = pathlib.Path(__file__).with_name(".nfl_ats_params.json")

# A leg has to disagree with the book enough to be interesting, but a large disagreement on
# an NFL prop has never once been real — it has always been a stale role, a player who is
# not playing, or a market the scorer should not have priced. The upper bound is the more
# important of the two. It is what keeps a Bhayshul-Tuten-at-+52-points row off the card.
_MIN_EDGE, _MAX_EDGE = 0.04, 0.18

# Day of week a week's board locks, Monday=0. Friday, so the picks are made against lines
# close to kickoff rather than against whatever was posted the moment the week became
# "current" — early-week numbers move a lot, and a prediction logged on Tuesday is being
# graded against a market it never saw.
#
# Friday through Sunday all qualify, so a missed Friday run is not a lost week. It cannot
# reach back further than that: by Monday the week's games have been played, and upcoming()
# will have excluded them anyway.
_LOCK_WEEKDAY = 4

# Depth-chart slot beyond which a player's projected role is not trustworthy enough to
# publish. Third-stringers carry last season's usage into a role they no longer have.
_MAX_DEPTH = 2


def _today() -> datetime.date:
    return datetime.date.today()


# ── the week ───────────────────────────────────────────────────────────────

def current_week() -> tuple[int, int]:
    """
    The season and week being predicted: the earliest one with an unplayed priced game.

    Deliberately not "the week containing today". A Tuesday sits between two weeks and a
    date-based answer flips at an arbitrary hour; what makes a week the current one is that
    its games have not happened yet.
    """
    import pandas as pd
    import nfl_game_model as gm
    d = pd.read_csv(gm._GAMES_URL)
    up = d[(d["game_type"] == "REG") & d["home_score"].isna() & d["spread_line"].notna()]
    if up.empty:
        raise RuntimeError("no unplayed regular-season games carry a spread")
    first = up.sort_values(["season", "week"]).iloc[0]
    return int(first["season"]), int(first["week"])


def upcoming(season: int, week: int) -> list:
    """Priced, unplayed games for one week."""
    import pandas as pd
    import nfl_game_model as gm
    d = pd.read_csv(gm._GAMES_URL)
    # Unplayed only. This filter is what makes the Friday lock safe: the Thursday night
    # game has already finished by then, and without this it would be picked up as an
    # upcoming game and logged as a prediction after the result was known. A record that
    # contains one retro-graded game is not a record at all.
    g = d[(d["season"] == season) & (d["week"] == week) & (d["game_type"] == "REG")
          & d["spread_line"].notna() & d["home_score"].isna()]
    return [{"away_team": r["away_team"], "home_team": r["home_team"],
             "spread_line": float(r["spread_line"]),
             "total_line": (float(r["total_line"]) if pd.notna(r.get("total_line")) else None),
             "gameday": str(r["gameday"])[:10], "roof": str(r.get("roof", "")),
             "game": f"{r['away_team']} @ {r['home_team']}"}
            for _, r in g.iterrows()]


def ats_params(season: int, refit: bool = False) -> dict:
    """Fitted scale and home-field, cached on disk and keyed by the seasons fitted on."""
    import nfl_game_model as gm
    train = list(range(season - _FIT_SEASONS, season))
    key = f"{train[0]}-{train[-1]}@{gm._CARRYOVER_WEIGHT}"
    if not refit and _PARAMS_CACHE.exists():
        try:
            cached = json.loads(_PARAMS_CACHE.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return cached["params"]
        except Exception:
            pass
    params = gm.fit(train)
    try:
        _PARAMS_CACHE.write_text(json.dumps({"key": key, "params": params}, indent=1),
                                 encoding="utf-8")
    except Exception:
        pass
    return params


# ── the ATS board ──────────────────────────────────────────────────────────

def ats_board(season: int | None = None, week: int | None = None, top_n: int = 5) -> list:
    """
    The games where the model and the closing line disagree most.

    `edge` is the disagreement in points, not an expected profit. A positive edge on the
    home side means the model projects the home team to beat the posted number; whether
    that is worth betting is a different question, and the backtest says it is not.
    """
    if season is None or week is None:
        season, week = current_week()
    import nfl_game_model as gm
    params = ats_params(season)
    rows = gm.predict_slate(season, week, params, upcoming=upcoming(season, week))
    out = []
    for r in rows:
        if r.get("pred_margin") is None or r.get("edge") is None:
            continue
        sp = r["spread_line"]
        home_fav = sp > 0
        # The side the model prefers, written the way a reader expects to see it.
        #
        # nfldata's spread_line is the HOME team's expected margin, in the same units as
        # `result` — positive means the home team is favoured by that many. Betting notation
        # runs the other way: a home team favoured by 6 is "PHI -6.0". So the displayed
        # number is the negative of spread_line for a home pick and spread_line itself for
        # an away pick. Getting this backwards prints a dog as a favourite on a published
        # card, which is the kind of error a reader spots instantly and never forgets.
        if r["pick"] == "home":
            label = f"{r['home_team']} {-sp:+.1f}"
        else:
            label = f"{r['away_team']} {sp:+.1f}"
        out.append({"game": r["game"], "away_team": r["away_team"],
                    "home_team": r["home_team"], "spread_line": sp,
                    "pred_margin": r["pred_margin"], "edge": r["edge"],
                    "pick": r["pick"], "pick_label": label,
                    "home_favored": bool(home_fav), "gameday": r.get("gameday", "")})
    out.sort(key=lambda r: -abs(r["edge"]))
    return out[:top_n]


# ── the parlay board ───────────────────────────────────────────────────────

def parlay_legs(n: int = 5) -> list:
    """
    Five player legs in five different stat categories.

    One category per leg is a constraint on the BOARD, not a preference about variety: five
    passing-TD legs are close to the same bet five times, and a parlay of them prices as
    though they were independent when they are not. Spanning categories is the only part of
    a five-leg parlay that is defensible on its own terms.

    Probabilities come out blended against the book at the project's standing weight, not
    raw. The raw model number is the one that produced 75-of-75 positive-EV parlays on
    opening weekend, which was a selection artifact; the blend is what the board is allowed
    to publish.
    """
    import daily_parlay_gen as dg
    import parlay_tracker

    raw = dg.fetch_fanduel("nfl")
    if raw is None or raw.empty:
        return []

    cal = dg.load_cal("NFL")
    legs = dg.score_legs(raw, cal, dg.NFL_STAT_TYPES, dg.nfl_hit_rate,
                         min_sample=dg.MIN_SAMPLE.get("NFL", 3))
    if not legs:
        return []

    # The blend weight this project is allowed to use for a sport with no resolved history.
    mkt_w = 0.11
    try:
        fitted = parlay_tracker.get_market_blend(sport="NFL")
        if fitted is not None and parlay_tracker.get_calibration(sport="NFL"):
            mkt_w = float(fitted)
    except Exception:
        pass

    depths = _depth_lookup()
    scored = []
    for lg in legs:
        model = lg.get("hit_rate")
        imp = lg.get("implied_prob")
        if model is None or not imp:
            continue
        blended = mkt_w * float(model) + (1.0 - mkt_w) * float(imp)
        edge = float(model) - float(imp)
        if not (_MIN_EDGE <= edge <= _MAX_EDGE):
            continue
        d = depths.get(lg["player_name"])
        if d is not None and d > _MAX_DEPTH:
            continue
        scored.append({**lg, "model_prob": round(float(model), 4),
                       "blended_prob": round(blended, 4),
                       "edge": round(edge, 4), "depth": d})

    # One leg per category and one per player, most LIKELY first — not most disagreed-with.
    #
    # Ranking by edge descending picks the legs pressed hardest against _MAX_EDGE, which is
    # to say the legs where the model contradicts the book most. With no demonstrated edge
    # that is a selection of the model's largest errors, and it is how opening weekend
    # produced 75 positive-EV parlays out of 75. Ranking by blended probability instead
    # selects what we actually believe is most likely to happen, which is both the honest
    # reading of "best prediction" and the only version whose tracked hit rate will mean
    # anything. The edge band stays as a filter: a leg the book likes more than we do is
    # not our pick, and a leg we like far more than the book is a stale role.
    scored.sort(key=lambda r: -r["blended_prob"])
    out, used_cat, used_player = [], set(), set()
    for r in scored:
        cat = r["stat_type"]
        if cat in used_cat or r["player_name"] in used_player:
            continue
        used_cat.add(cat)
        used_player.add(r["player_name"])
        out.append(r)
        if len(out) >= n:
            break
    return out


def _depth_lookup() -> dict:
    """player name -> depth_chart_order, position-aware, with loose-name fallback."""
    try:
        import fantasy_tools as ft
        import nfl_analysis as nfl
        sp = ft.sleeper_profiles()
        out = {}
        for (name, _pos), pl in sp.items():
            o = pl.get("depth_chart_order")
            if o is None or not name:
                continue
            # Keep the shallowest slot a name maps to, so a namesake deep on another
            # roster cannot filter out a starter.
            cur = out.get(name)
            if cur is None or o < cur:
                out[name] = o
            k = nfl._name_key(name)
            if k and (k not in out or o < out[k]):
                out[k] = o
        return out
    except Exception:
        return {}


def parlay_price(legs: list) -> dict:
    """Combined American odds and both probabilities for a set of legs."""
    dec, model, blended = 1.0, 1.0, 1.0
    for lg in legs:
        o = int(lg.get("american_odds", -110))
        dec *= (1 + o / 100.0) if o > 0 else (1 + 100.0 / abs(o))
        model *= float(lg.get("model_prob", 0))
        blended *= float(lg.get("blended_prob", 0))
    american = (round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))) if dec > 1 else 0
    return {"decimal": round(dec, 3), "american": int(american),
            "model_prob": round(model, 5), "blended_prob": round(blended, 5),
            "breakeven_prob": round(1.0 / dec, 5) if dec else None}


# ── the log ────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not LOG.exists():
        return {}
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    LOG.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def _key(season: int, week: int) -> str:
    return f"{season}-{week:02d}"


def locked_today(today=None) -> bool:
    """True once the week is close enough to kickoff to commit to a board."""
    return (today or _today()).weekday() >= _LOCK_WEEKDAY


def log_picks(season: int | None = None, week: int | None = None,
              ats: list | None = None, parlay: list | None = None,
              overwrite: bool = False, force: bool = False) -> dict:
    """
    Record a week's picks, once, and not before Friday.

    Refuses to overwrite a week that already has picks unless asked explicitly. A board
    regenerated mid-week would silently become a different prediction — the model's ratings
    move as games finish, and the lines move too — and the record would describe picks that
    were never published. `overwrite` exists for a genuine mistake, not for a second run.

    Before the lock day it returns an empty board rather than raising. A newsletter that
    runs on a Tuesday should simply go out without the NFL section; treating "too early to
    commit" as a source failure would block the whole issue over a working system behaving
    correctly.
    """
    if season is None or week is None:
        season, week = current_week()
    data = _load()
    k = _key(season, week)
    if k in data and not overwrite:
        return data[k]
    if not (force or locked_today()):
        return {"season": season, "week": week, "ats": [], "parlay": [],
                "locked": False, "locks_on": "Friday"}

    ats = ats if ats is not None else ats_board(season, week)
    parlay = parlay if parlay is not None else parlay_legs()

    entry = {
        "season": season, "week": week,
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "ats": [{"game": r["game"], "away_team": r["away_team"], "home_team": r["home_team"],
                 "spread_line": r["spread_line"], "pred_margin": r["pred_margin"],
                 "edge": r["edge"], "pick": r["pick"], "pick_label": r["pick_label"],
                 "outcome": None, "result": None} for r in ats],
        "parlay": [{"player": r["player_name"], "stat_type": r["stat_type"],
                    "line": r["line_score"], "side": r["side"],
                    "american_odds": r.get("american_odds"),
                    "model_prob": r.get("model_prob"), "blended_prob": r.get("blended_prob"),
                    "implied_prob": r.get("implied_prob"), "team": r.get("team", ""),
                    "outcome": None, "actual": None} for r in parlay],
    }
    if entry["parlay"]:
        entry["parlay_price"] = parlay_price(parlay)
    data[k] = entry
    _save(data)
    return entry


# ── grading ────────────────────────────────────────────────────────────────

def _grade_ats(entry: dict, sched) -> int:
    """Fill in ATS outcomes from final scores. Pushes are pushes, not half-wins."""
    filled = 0
    for r in entry.get("ats", []):
        if r.get("outcome"):
            continue
        m = sched[(sched["season"] == entry["season"]) & (sched["week"] == entry["week"])
                  & (sched["home_team"] == r["home_team"])
                  & (sched["away_team"] == r["away_team"])]
        if m.empty:
            continue
        g = m.iloc[0]
        result = float(g["result"])
        # The spread the pick was made against, not the one in the file today. A line that
        # moved after publication does not retroactively change what was predicted.
        spread = float(r["spread_line"])
        if result == spread:
            r["outcome"] = "push"
        else:
            picked_home = r["pick"] == "home"
            r["outcome"] = "win" if (result > spread) == picked_home else "loss"
        r["result"] = result
        filled += 1
    return filled


def _grade_parlay(entry: dict) -> int:
    """
    Fill in parlay leg outcomes from the weekly stat frame.

    A leg with no row for that week is marked "dnp" rather than a loss. A player who did
    not play did not lose a prediction about his production, and counting it as a loss
    would make the record look worse than the model actually performed — the mirror image
    of the more usual error.
    """
    import nfl_analysis as nfl
    filled = 0
    pending = [r for r in entry.get("parlay", []) if not r.get("outcome")]
    if not pending:
        return 0
    try:
        _, wk = nfl.get_season(entry["season"])
    except Exception:
        return 0
    wk = wk[wk["week"] == entry["week"]]
    if wk.empty:
        return 0
    by_name = {}
    for _, row in wk.iterrows():
        nm = row.get("player_display_name")
        if not nm:
            continue
        by_name[nm] = row
        by_name.setdefault(nfl._name_key(nm), row)

    for r in pending:
        # Explicit None checks, never `a or b`. These values are pandas Series, and a Series
        # in a boolean context raises rather than being falsy — the same trap that once made
        # every name lookup in nfl_analysis fail inside a bare except, scoring 38 props out
        # of 736 while looking like it worked.
        row = by_name.get(r["player"])
        if row is None:
            row = by_name.get(nfl._name_key(r["player"]))
        if row is None:
            r["outcome"] = "dnp"
            filled += 1
            continue
        opp, res, *_ = nfl._USAGE_MODEL.get(r["stat_type"], (None, None, 0))
        col = res or opp
        if not col or col not in row.index:
            continue
        val = row.get(col)
        if val is None or (isinstance(val, float) and val != val):
            val = 0.0
        actual = float(val)
        line = float(r["line"])
        if actual == line:
            r["outcome"] = "push"
        elif str(r["side"]).lower() == "under":
            r["outcome"] = "win" if actual < line else "loss"
        else:
            r["outcome"] = "win" if actual > line else "loss"
        r["actual"] = actual
        filled += 1
    return filled


def grade(season: int | None = None) -> dict:
    """Grade every ungraded week that has finished. Safe to run repeatedly."""
    import nfl_game_model as gm
    data = _load()
    if not data:
        return {"weeks": 0, "ats_filled": 0, "parlay_filled": 0}
    sched = gm.games()
    sched = sched[sched["result"].notna()]
    ats_filled = parlay_filled = weeks = 0
    for k, entry in data.items():
        if season is not None and entry.get("season") != season:
            continue
        a = _grade_ats(entry, sched)
        p = _grade_parlay(entry)
        if a or p:
            weeks += 1
        ats_filled += a
        parlay_filled += p
        entry["graded"] = all(r.get("outcome") for r in entry.get("ats", [])) and \
                          all(r.get("outcome") for r in entry.get("parlay", []))
    if ats_filled or parlay_filled:
        _save(data)
    return {"weeks": weeks, "ats_filled": ats_filled, "parlay_filled": parlay_filled}


def record(season: int | None = None) -> dict:
    """
    The running record, which is the reason any of this is logged.

    ATS is reported against 52.38% because that is the number that decides whether the
    board would have made money, and against 50% because that is the number that decides
    whether the model knows anything at all. Those are different questions and a single
    percentage answers neither on its own.
    """
    data = _load()
    ats_w = ats_l = ats_p = 0
    leg_w = leg_l = leg_p = leg_dnp = 0
    parlays_hit = parlays_done = 0
    weeks = []
    for k in sorted(data):
        e = data[k]
        if season is not None and e.get("season") != season:
            continue
        aw = sum(1 for r in e.get("ats", []) if r.get("outcome") == "win")
        al = sum(1 for r in e.get("ats", []) if r.get("outcome") == "loss")
        ap = sum(1 for r in e.get("ats", []) if r.get("outcome") == "push")
        lw = sum(1 for r in e.get("parlay", []) if r.get("outcome") == "win")
        ll = sum(1 for r in e.get("parlay", []) if r.get("outcome") == "loss")
        lp = sum(1 for r in e.get("parlay", []) if r.get("outcome") == "push")
        ld = sum(1 for r in e.get("parlay", []) if r.get("outcome") == "dnp")
        ats_w += aw; ats_l += al; ats_p += ap
        leg_w += lw; leg_l += ll; leg_p += lp; leg_dnp += ld
        legs = e.get("parlay", [])
        if legs and all(r.get("outcome") for r in legs):
            parlays_done += 1
            if all(r.get("outcome") in ("win", "push") for r in legs):
                parlays_hit += 1
        weeks.append({"week": k, "ats": f"{aw}-{al}" + (f"-{ap}" if ap else ""),
                      "legs": f"{lw}-{ll}" + (f" ({ld} dnp)" if ld else "")})
    ats_dec = ats_w + ats_l
    leg_dec = leg_w + leg_l
    return {
        "ats_record": f"{ats_w}-{ats_l}" + (f"-{ats_p}" if ats_p else ""),
        "ats_pct": round(ats_w / ats_dec * 100, 2) if ats_dec else None,
        "ats_breakeven": 52.38,
        "ats_vs_coinflip": round(ats_w / ats_dec * 100 - 50, 2) if ats_dec else None,
        "ats_n": ats_dec,
        "leg_record": f"{leg_w}-{leg_l}",
        "leg_pct": round(leg_w / leg_dec * 100, 2) if leg_dec else None,
        "leg_n": leg_dec, "legs_dnp": leg_dnp,
        "parlays_hit": parlays_hit, "parlays_settled": parlays_done,
        "weeks": weeks,
        # The backtest this record is meant to confirm or contradict.
        "ats_backtest_pct": 49.25,
    }
