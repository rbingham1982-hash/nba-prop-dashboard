"""
nba_daily_picks.py — the daily NBA board: spread picks and a props parlay, logged before
tip-off and graded the next day, with a running record on the NBA Hub and the website.

The weekly NBA board (nba_weekly_picks) is one Friday slate for the newsletter; this is every
night. Both are saved once and never edited: a board rebuilt after lines move would be a
different prediction from the one on the record.

  * Saved by the cloud board run, on its first firing from noon Chicago (the hour the boards
    are built), from the games not yet started. A game that tipped before then has no pick.
  * Spread picks (nba_spreads): every game's model side is logged and graded, so the whole
    model is measured; the published picks are the games where the model and the posted
    line disagree by EDGE_MIN to EDGE_MAX points. Above EDGE_MAX the disagreement is more
    often a roster change the ratings have not seen than a mistake in the line.
  * The props parlay is the weekly board's: ranked by blended probability, one leg per
    category and per player, published only when it clears the break-even its payout
    implies. Below that it is logged and graded as `parlay_withheld`.

Graded from ESPN's final scores (spreads) and the league game log (props).

THE SPREAD MODEL HAS NO DEMONSTRATED EDGE. Walk-forward over 983 games of 2025-26 against
ESPN's posted (DraftKings) lines it went 48.1% against the spread, 46.7% on edges of 2+
points and 46.3% from January on, and its margin error (14.9 points RMSE) was worse than the
line's own (14.1). Break-even at -110 is 52.4%. The picks are published as a tracked test,
with that backtest printed beside them, the way the NFL spread board is; nothing here was
tuned on it, and a better-looking threshold found on the same games would not be evidence.
"""
from __future__ import annotations

import datetime
import json
import pathlib

LOG = pathlib.Path(__file__).with_name("nba_daily_picks.json")
EDGE_MIN, EDGE_MAX = 2.0, 6.0
MAX_PUBLISHED = 3
LOCK_HOUR = 12
BREAKEVEN_ATS = 52.38            # -110 on both sides
BACKTEST = {"games": 983, "ats_pct": 48.1, "edge2_pct": 46.7, "season": "2025-26"}


def _load() -> dict:
    if not LOG.exists():
        return {}
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    LOG.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")


def log_today(day: datetime.date | None = None, now: datetime.datetime | None = None,
              force: bool = False) -> dict:
    """Save `day`'s board once, from the first run at or after LOCK_HOUR. Returns the entry."""
    import nba_spreads as ns
    import nba_weekly_picks as nwp
    now = now or datetime.datetime.now()
    day = day or now.date()
    key = day.isoformat()
    data = _load()
    if key in data and not force:
        return data[key]
    if not force and now.hour < LOCK_HOUR:
        return {"date": key, "note": f"saves from {LOCK_HOUR}:00"}
    if not force and nwp.season_type() not in nwp._LIVE_SEASON_TYPES:
        return {"date": key, "note": "no regular-season or playoff games"}

    try:
        games = ns.picks(day)
    except Exception as e:
        games, ats_err = [], str(e)
    else:
        ats_err = None
    published = [g for g in games if EDGE_MIN <= g["edge"] <= EDGE_MAX][:MAX_PUBLISHED]
    pub_ids = {g["event"] for g in published}

    try:
        legs = nwp.slate_legs(day)
    except Exception:
        legs = []
    parlay = nwp.parlay_legs(legs) if legs else []
    price = nwp.parlay_price(parlay) if parlay else {}
    withheld = bool(parlay) and not nwp.clears_breakeven(price)
    if not games and not parlay:
        return {"date": key, "note": "nothing on the board yet" + (f" ({ats_err})" if ats_err else "")}

    entry = {"date": key, "generated": now.isoformat(timespec="seconds"),
             "ats": [dict(g, published=g["event"] in pub_ids) for g in games],
             "ats_rule": f"published when the model and the line differ by {EDGE_MIN:g}-{EDGE_MAX:g} points",
             "parlay": [] if withheld else [nwp._row(l) for l in parlay],
             "parlay_rule": getattr(nwp.parlay_legs, "rule", "market")}
    if withheld:
        entry["parlay_withheld"] = [nwp._row(l) for l in parlay]
        entry["parlay_withheld_price"] = price
    elif parlay:
        entry["parlay_price"] = price
    data[key] = entry
    _save(data)
    return entry


# ── grading ───────────────────────────────────────────────────────────────────────────────

def _finals(day: datetime.date) -> dict:
    """ESPN event id -> (home_pts, away_pts) for that day's finished games."""
    import requests
    import nba_spreads as ns
    js = requests.get(f"{ns._SITE}/scoreboard", params={"dates": day.strftime("%Y%m%d")}, timeout=20,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
    out = {}
    for e in js.get("events", []):
        if not e["status"]["type"].get("completed"):
            continue
        sc = {x["homeAway"]: int(x.get("score") or 0) for x in e["competitions"][0]["competitors"]}
        out[e["id"]] = (sc["home"], sc["away"])
    return out


def grade(today: datetime.date | None = None) -> int:
    """Grade every saved day before `today`. Safe to repeat. Returns rows graded."""
    import pandas as pd
    import nba_grades as ng
    import nba_weekly_picks as nwp
    import parlay_model as pm
    today = today or datetime.date.today()
    data, filled = _load(), 0
    for key, e in data.items():
        day = datetime.date.fromisoformat(key)
        if day >= today:
            continue
        pending = [g for g in e.get("ats", []) if not g.get("outcome")]
        if pending:
            try:
                fin = _finals(day)
            except Exception:
                fin = {}
            for g in pending:
                if g["event"] not in fin:
                    continue
                hp, ap = fin[g["event"]]
                cover = (hp - ap) + g["home_spread"]
                g["home_pts"], g["away_pts"] = hp, ap
                g["outcome"] = "push" if cover == 0 else (
                    "win" if (cover > 0) == (g["pick"] == "home") else "loss")
                filled += 1
        rows = e.get("parlay", []) + e.get("parlay_withheld", [])
        if rows and not all(r.get("outcome") for r in rows):
            try:
                log = ng.season_log(pm.nba_season_strings(day)[0])
                night = log[log["date"] == pd.Timestamp(day)]
            except Exception:
                night = None
            if night is not None and not night.empty:
                night = night.assign(PRA=night["PTS"] + night["REB"] + night["AST"])
                box = {nwp._norm(r.PLAYER_NAME): r._asdict() for r in night.itertuples()}
                filled += nwp._grade_rows(rows, box)
    if filled:
        _save(data)
    return filled


# ── record ────────────────────────────────────────────────────────────────────────────────

def _ats(rows: list) -> dict:
    w = sum(r.get("outcome") == "win" for r in rows)
    l = sum(r.get("outcome") == "loss" for r in rows)
    p = sum(r.get("outcome") == "push" for r in rows)
    return {"record": f"{w}-{l}" + (f"-{p}" if p else ""), "n": w + l,
            "pct": round(100 * w / (w + l), 1) if w + l else None}


def record() -> dict:
    data = _load()
    pub = [g for e in data.values() for g in e.get("ats", []) if g.get("published")]
    allg = [g for e in data.values() for g in e.get("ats", [])]
    legs_w = legs_l = par_hit = par_done = wh_hit = wh_done = 0
    import nba_weekly_picks as nwp
    for e in data.values():
        for r in e.get("parlay", []):
            legs_w += r.get("outcome") == "win"; legs_l += r.get("outcome") == "loss"
        pr = nwp._parlay_result(e.get("parlay", []))
        if pr:
            par_done += 1; par_hit += pr == "hit"
        wr = nwp._parlay_result(e.get("parlay_withheld", []))
        if wr:
            wh_done += 1; wh_hit += wr == "hit"
    return {"days": len(data), "ats": _ats(pub), "ats_all_games": _ats(allg), "breakeven": BREAKEVEN_ATS,
            "leg_record": f"{legs_w}-{legs_l}", "parlays_hit": par_hit, "parlays_settled": par_done,
            "withheld_hit": wh_hit, "withheld_settled": wh_done}


# ── the hub section ───────────────────────────────────────────────────────────────────────

def render_hub_section() -> None:
    """Today's board (or the latest, graded) and the season record, for nba_hub."""
    import streamlit as st
    import nfl_hub as nh
    data = _load()
    if not data:
        st.caption("The daily NBA board saves each afternoon from the first regular-season slate: "
                   "spread picks and a break-even-gated props parlay, graded here the next day.")
        return
    key = sorted(data)[-1]
    e = data[key]
    chip = {"win": "nh-win", "loss": "nh-loss", "push": "nh-push", "dnp": "nh-dnp"}

    def tag(res, extra=""):
        return (f"<span class='res {chip.get(res, 'nh-pend')}'>{(res or 'pending').upper()}{extra}</span>")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"<div class='nh-colhead'>SPREAD PICKS · {nh._esc(key)}</div>", unsafe_allow_html=True)
        pub = [g for g in e.get("ats", []) if g.get("published")]
        if pub:
            html = ""
            for g in pub:
                score = (f" · {g['away_pts']}-{g['home_pts']}" if g.get("home_pts") is not None else "")
                html += (f"<div class='nh-pick'><div><b>{nh._esc(g['pick_label'])}</b>"
                         f"<div class='sub'>{nh._esc(g['game'])} · model {g['pred_margin']:+.1f} for the home side · "
                         f"{g['edge']:.1f} pts off the line</div></div>{tag(g.get('outcome'), score)}</div>")
            st.markdown(html, unsafe_allow_html=True)
        else:
            st.caption("No game cleared the publishing rule today; every game is still logged and graded.")
    with c2:
        st.markdown("<div class='nh-colhead'>PROPS PARLAY</div>", unsafe_allow_html=True)
        legs = e.get("parlay") or []
        if legs:
            st.markdown("".join(
                f"<div class='nh-pick'><div><b>{nh._esc(r['player'])}</b> · {r['side'].upper()} {r['line']:g} "
                f"{nh._esc(r['stat_type'])}<div class='sub'>{nh._esc(r.get('game', ''))} · "
                f"{r['blended_prob']:.0%} · {int(r['american_odds']):+d}</div></div>"
                f"{tag(r.get('outcome'), (' · %g' % r['actual']) if r.get('actual') is not None else '')}</div>"
                for r in legs), unsafe_allow_html=True)
            p = e.get("parlay_price") or {}
            st.caption(f"{p.get('american', 0):+d} · {p.get('blended_prob', 0):.2%} against "
                       f"{p.get('breakeven_prob', 0):.2%} to break even.")
        elif e.get("parlay_withheld"):
            p = e.get("parlay_withheld_price") or {}
            st.caption(f"Held back: the best {len(e['parlay_withheld'])} legs priced at {p.get('blended_prob', 0):.2%} "
                       f"against {p.get('breakeven_prob', 0):.2%} needed. Logged and graded anyway.")
        else:
            st.caption("No props parlay today.")
    rec = record()
    a, al = rec["ats"], rec["ats_all_games"]
    bits = []
    if a["n"]:
        bits.append(f"Spread picks {a['record']} ({a['pct']}%, {BREAKEVEN_ATS}% breaks even)")
    if al["n"]:
        bits.append(f"model on every game {al['record']} ({al['pct']}%)")
    if rec["parlays_settled"] or rec["withheld_settled"]:
        bits.append(f"parlays {rec['parlays_hit']}/{rec['parlays_settled']} · held back "
                    f"{rec['withheld_hit']}/{rec['withheld_settled']} would have hit")
    if bits:
        st.caption("Season: " + " · ".join(bits) + ".")
    st.caption(f"The spread model is a tracked test, not an edge: walk-forward over {BACKTEST['games']} games of "
               f"{BACKTEST['season']} it went {BACKTEST['ats_pct']}% against the posted line "
               f"({BACKTEST['edge2_pct']}% on its 2+ point edges), below the {BREAKEVEN_ATS}% that breaks even.")
