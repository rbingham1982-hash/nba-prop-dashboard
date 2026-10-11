"""
nba_weekly_picks.py — the weekly NBA board, logged before tip-off and graded after.

The NBA counterpart of weekly_picks, with the lessons that board paid for built in from the
start rather than learned again:

  * One night's slate, locked on the first Friday-Sunday run of the ISO week, so the prices
    are close to tip-off. A week of NBA nights would mean logging Tuesday's picks against
    Tuesday-morning lines and grading them as if they were Friday's.
  * The props parlay is ranked by BLENDED probability, one leg per stat category and per
    player, and published only if it clears the break-even its payout implies. Below that
    it is logged and graded as `parlay_withheld`, so the gate is tested, not trusted.
  * Probabilities are blended against the book at the project's NBA weight. The raw model
    number is kept beside it, never published on its own.
  * The scorer board is the slate's highest points lines with the blended chance of the
    over — a description of who the market expects to score, graded against what happened.

Grading reads the league game log the morning after. A player with no row that night is
"dnp", not a loss: he did not lose a prediction about a game he did not play.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import unicodedata

LOG = pathlib.Path(__file__).with_name("nba_weekly_picks.json")

# Same reasoning and bounds as weekly_picks: a leg must disagree with the book enough to
# be interesting, and a very large disagreement has historically been a stale role or a
# player who was not going to play rather than an edge.
_MIN_EDGE, _MAX_EDGE = 0.04, 0.18
_LOCK_WEEKDAY = 4                 # Friday, Monday=0
_PARLAY_LEGS = 5
_SCORERS = 10
_STATS = ("Points", "Rebounds", "Assists", "3-PT Made", "Pts+Rebs+Asts")
_NBA_COL = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3-PT Made": "FG3M",
            "Pts+Rebs+Asts": "PRA"}


# ── keys and dates ──────────────────────────────────────────────────────────

def _today() -> datetime.date:
    return datetime.date.today()


def week_key(day: datetime.date | None = None) -> str:
    y, w, _ = (day or _today()).isocalendar()
    return f"{y}-W{w:02d}"


def locked_today(today=None) -> bool:
    return (today or _today()).weekday() >= _LOCK_WEEKDAY


# ESPN season types that count: regular season, postseason, play-in. Preseason props post
# on FanDuel too, and a Friday-Sunday run in early October would otherwise have locked a
# week of exhibition games — rotations no book or model can price, graded as if real.
_LIVE_SEASON_TYPES = {2, 3, 5}


def season_type() -> int | None:
    """ESPN's current NBA season type (1 preseason, 2 regular, 3 postseason, 5 play-in)."""
    try:
        import requests
        js = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        return int(js["leagues"][0]["season"]["type"]["type"])
    except Exception:
        return None


def _local_date(start_time: str):
    try:
        dt = datetime.datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        return dt.astimezone().date()
    except Exception:
        return None


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _load() -> dict:
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    LOG.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")


# ── pricing ─────────────────────────────────────────────────────────────────

def _market_weight() -> float:
    """The weight on the model in the published blend; 0.11 until NBA calibration exists."""
    try:
        import parlay_tracker
        w = parlay_tracker.get_market_blend(sport="NBA")
        if w is not None and parlay_tracker.get_calibration(sport="NBA"):
            return float(w)
    except Exception:
        pass
    return 0.11


def slate_legs(slate: datetime.date) -> list:
    """
    Every FanDuel NBA prop on one night's slate, priced: raw model, book, blended.

    The scorer returns 0.7 x model + 0.3 x implied. It is called with implied 0.5 and no
    calibration so the model term comes back exactly — (rate - 0.15) / 0.7, the inversion
    nba_eval uses — and the blend is then applied once, here, at the published weight.
    """
    import daily_parlay_gen as dg
    import parlay_model as pm
    raw = pm.fetch_fanduel("nba")
    if raw is None or raw.empty:
        return []
    raw = raw[raw["stat_type"].isin(_STATS)]
    raw = raw[raw["start_time"].map(_local_date) == slate]
    raw = raw[~raw["start_time"].map(dg._has_started)]
    slate_legs.games = set(raw["game_label"].astype(str))
    w = _market_weight()
    out, cache = [], {}
    for _, r in raw.iterrows():
        side = "under" if str(r.get("side", "over")).lower() == "under" else "over"
        key = (r["player_name"], r["stat_type"], float(r["line_score"]))
        if key not in cache:
            rate, n = pm._nba_hit_rate(r["player_name"], r["stat_type"], float(r["line_score"]),
                                       implied_override=0.5)
            cache[key] = ((rate - 0.15) / 0.7, n) if n else (None, 0)
        p_over, n = cache[key]
        if p_over is None or n < 3:
            continue
        model = p_over if side == "over" else 1.0 - p_over
        imp = float(r.get("implied_prob") or 0)
        if not imp:
            continue
        out.append({"player": r["player_name"], "stat_type": r["stat_type"],
                    "line": float(r["line_score"]), "side": side,
                    "american_odds": int(r.get("american_odds", -110)),
                    "game": str(r.get("game_label", "")), "start_time": str(r.get("start_time", "")),
                    "model_prob": round(model, 4), "implied_prob": round(imp, 4),
                    "blended_prob": round(w * model + (1 - w) * imp, 4),
                    "edge": round(model - imp, 4), "sample_n": int(n)})
    return out


def parlay_legs(legs: list, n: int = _PARLAY_LEGS) -> list:
    """Most likely first, one per category and per player, inside the edge band."""
    pool = sorted((l for l in legs if _MIN_EDGE <= l["edge"] <= _MAX_EDGE),
                  key=lambda l: -l["blended_prob"])
    out, cats, players = [], set(), set()
    for l in pool:
        if l["stat_type"] in cats or l["player"] in players:
            continue
        out.append(l); cats.add(l["stat_type"]); players.add(l["player"])
        if len(out) == n:
            break
    return out


def parlay_price(legs: list) -> dict:
    import weekly_picks as wp
    return wp.parlay_price(legs)


def clears_breakeven(price: dict) -> bool:
    import weekly_picks as wp
    return wp.clears_breakeven(price)


def scorer_board(legs: list, n: int = _SCORERS) -> list:
    """The slate's highest points lines, with the blended chance of the over."""
    pts = [l for l in legs if l["stat_type"] == "Points" and l["side"] == "over"]
    best = {}
    for l in pts:                       # one line per player: his main (highest-odds-near-even) line
        cur = best.get(l["player"])
        if cur is None or abs(l["implied_prob"] - 0.5) < abs(cur["implied_prob"] - 0.5):
            best[l["player"]] = l
    return sorted(best.values(), key=lambda l: -l["line"])[:n]


# ── logging ─────────────────────────────────────────────────────────────────

def _row(l: dict) -> dict:
    return {k: l.get(k) for k in ("player", "stat_type", "line", "side", "american_odds", "game",
                                  "start_time", "model_prob", "implied_prob", "blended_prob",
                                  "edge")} | {"outcome": None, "actual": None}


def log_picks(slate: datetime.date | None = None, legs: list | None = None,
              overwrite: bool = False, force: bool = False) -> dict:
    """
    Record this week's board, once, from the first Friday-Sunday run with games.

    Refuses to overwrite a logged week: a board rebuilt midweek against moved lines would
    be a different prediction while the record kept the old one.
    """
    slate = slate or _today()
    data = _load()
    k = week_key(slate)
    if k in data and not overwrite:
        return data[k]
    if not (force or locked_today(slate)):
        return {"week": k, "parlay": [], "scorers": [], "locked": False, "locks_on": "Friday"}
    if not force and season_type() not in _LIVE_SEASON_TYPES:
        return {"week": k, "parlay": [], "scorers": [], "locked": False,
                "note": "preseason — the board starts with the regular season"}
    if legs is None:
        legs = slate_legs(slate)
        # A player whose game log does not load is dropped from the pool without a word,
        # so a stats.nba.com blip at lock time can take a whole game off the board (a
        # preview on 2026-10-11 lost OKC @ SAS that way, then had it back a minute later).
        # A board missing a game is a different prediction, so do not save it; the next
        # run inside the Friday-Sunday window tries again.
        missing = sorted(getattr(slate_legs, "games", set()) - {l["game"] for l in legs})
        if missing and not force:
            return {"week": k, "parlay": [], "scorers": [], "locked": False,
                    "note": f"no priced legs for {', '.join(missing)} — not saved, retried next run"}
    if not legs:
        return {"week": k, "parlay": [], "scorers": [], "locked": False, "note": "no games on this slate"}
    parlay = parlay_legs(legs)
    price = parlay_price(parlay) if parlay else {}
    withheld = bool(parlay) and not clears_breakeven(price)
    entry = {"week": k, "slate": slate.isoformat(),
             "generated": datetime.datetime.now().isoformat(timespec="seconds"),
             "model_weight": _market_weight(),
             "parlay": [] if withheld else [_row(l) for l in parlay],
             "scorers": [_row(l) for l in scorer_board(legs)]}
    if withheld:
        entry["parlay_withheld"] = [_row(l) for l in parlay]
        entry["parlay_withheld_price"] = price
    elif parlay:
        entry["parlay_price"] = price
    data[k] = entry
    _save(data)
    return entry


# ── grading ─────────────────────────────────────────────────────────────────

def _grade_rows(rows: list, box: dict) -> int:
    filled = 0
    for r in rows:
        if r.get("outcome"):
            continue
        g = box.get(_norm(r["player"]))
        if g is None:
            r["outcome"] = "dnp"
            filled += 1
            continue
        actual = float(g[_NBA_COL[r["stat_type"]]])
        line = float(r["line"])
        if actual == line:
            r["outcome"] = "push"
        elif r["side"] == "under":
            r["outcome"] = "win" if actual < line else "loss"
        else:
            r["outcome"] = "win" if actual > line else "loss"
        r["actual"] = actual
        filled += 1
    return filled


def grade() -> int:
    """Grade every logged board whose slate has been played. Safe to run repeatedly."""
    import pandas as pd
    import nba_grades as ng
    import parlay_model as pm
    data, filled = _load(), 0
    for e in data.values():
        slate = e.get("slate")
        if not slate or datetime.date.fromisoformat(slate) >= _today():
            continue
        rows = e.get("parlay", []) + e.get("parlay_withheld", []) + e.get("scorers", [])
        if all(r.get("outcome") for r in rows):
            continue
        season = pm.nba_season_strings(datetime.date.fromisoformat(slate))[0]
        try:
            log = ng.season_log(season)
        except Exception:
            continue
        night = log[log["date"] == pd.Timestamp(slate)]
        # Only grade once the night is in the log; an empty night means the data lags.
        if night.empty:
            continue
        night = night.assign(PRA=night["PTS"] + night["REB"] + night["AST"])
        box = {_norm(r.PLAYER_NAME): r._asdict() for r in night.itertuples()}
        filled += _grade_rows(rows, box)
    if filled:
        _save(data)
    return filled


def _parlay_result(legs: list):
    if not legs or not all(r.get("outcome") for r in legs):
        return None
    if any(r["outcome"] == "loss" for r in legs):
        return "miss"
    return "hit" if any(r["outcome"] == "win" for r in legs) else None


def record() -> dict:
    """Season totals, each against the probability we published for it."""
    data = _load()
    legs_w = legs_l = sc_w = sc_l = 0
    sc_exp = 0.0
    par_hit = par_done = wh_hit = wh_done = 0
    for e in data.values():
        for r in e.get("parlay", []):
            legs_w += r.get("outcome") == "win"; legs_l += r.get("outcome") == "loss"
        for r in e.get("scorers", []):
            if r.get("outcome") in ("win", "loss"):
                sc_w += r["outcome"] == "win"; sc_l += r["outcome"] == "loss"
                sc_exp += float(r.get("blended_prob") or 0)
        pr = _parlay_result(e.get("parlay", []))
        if pr:
            par_done += 1; par_hit += pr == "hit"
        wr = _parlay_result(e.get("parlay_withheld", []))
        if wr:
            wh_done += 1; wh_hit += wr == "hit"
    n = sc_w + sc_l
    return {"leg_record": f"{legs_w}-{legs_l}", "parlays_hit": par_hit, "parlays_settled": par_done,
            "withheld_hit": wh_hit, "withheld_settled": wh_done,
            "scorer_record": f"{sc_w}-{sc_l}", "scorer_n": n,
            "scorer_expected_pct": round(100 * sc_exp / n, 1) if n else None,
            "scorer_pct": round(100 * sc_w / n, 1) if n else None}


# ── the hub section ─────────────────────────────────────────────────────────

def render_hub_section() -> None:
    """This week's board (or last week's, graded) for nba_hub."""
    import streamlit as st
    import nfl_hub as nh
    data = _load()
    if not data:
        st.caption("The weekly NBA board starts with the 2026-27 season: a break-even-gated props "
                   "parlay and a scorer board, locked on the first Friday slate of each week and "
                   "graded here the morning after.")
        return
    e = data[sorted(data)[-1]]
    chip = {"win": "nh-win", "loss": "nh-loss", "push": "nh-push", "dnp": "nh-dnp"}

    def pick(r, label):
        res = r.get("outcome")
        tag = (f"<span class='res {chip.get(res, 'nh-pend')}'>{(res or 'pending').upper()}"
               + (f" · {r['actual']:g}" if r.get("actual") is not None else "") + "</span>")
        return (f"<div class='nh-pick'><div><b>{nh._esc(r['player'])}</b> · {nh._esc(label)}"
                f"<div class='sub'>{nh._esc(r.get('game', ''))} · {r['blended_prob']:.0%} · "
                f"{int(r['american_odds']):+d}</div></div>{tag}</div>")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"<div class='nh-colhead'>PROPS PARLAY · {nh._esc(e.get('slate', ''))}</div>",
                    unsafe_allow_html=True)
        if e.get("parlay"):
            st.markdown("".join(pick(r, f"{r['side'].upper()} {r['line']:g} {r['stat_type']}")
                                for r in e["parlay"]), unsafe_allow_html=True)
            p = e.get("parlay_price") or {}
            st.caption(f"{p.get('american', 0):+d} · {p.get('blended_prob', 0):.2%} against "
                       f"{p.get('breakeven_prob', 0):.2%} to break even.")
        elif e.get("parlay_withheld"):
            p = e.get("parlay_withheld_price") or {}
            st.caption(f"Held back: the best {len(e['parlay_withheld'])} legs priced at "
                       f"{p.get('blended_prob', 0):.2%} "
                       f"against {p.get('breakeven_prob', 0):.2%} needed. Logged and graded anyway.")
    with c2:
        st.markdown("<div class='nh-colhead'>SCORER BOARD · points lines</div>", unsafe_allow_html=True)
        st.markdown("".join(pick(r, f"OVER {r['line']:g} pts") for r in e.get("scorers", [])),
                    unsafe_allow_html=True)
    rec = record()
    if rec["scorer_n"] or rec["parlays_settled"] or rec["withheld_settled"]:
        st.caption(f"Season: scorer board {rec['scorer_record']}"
                   + (f" against {rec['scorer_expected_pct']}% expected" if rec["scorer_n"] else "")
                   + f" · parlays {rec['parlays_hit']}/{rec['parlays_settled']}"
                   + f" · held back {rec['withheld_hit']}/{rec['withheld_settled']} would have hit.")
