"""
games.py — two free daily games built on Konjure's own data. No prizes, so no contest law.

GUESS THE GRADE
  Five stat lines from the last night of NBA games (the latest graded NFL week out of
  season), names hidden. Guess each line's grade, 0-100: the same composite score the NBA
  Hub publishes (nba_grades). Each guess scores 100 minus twice the miss, floored at 0, so a
  perfect day is 500. The five are drawn once per date, one from each fifth of the grade
  range, so every day has a star, a dud and three in between. Same puzzle for everyone.

BEAT THE MARKET
  Five of tonight's NBA props, frozen once for the day and the same for everyone. For each,
  pick over or under and how sure you are (50-95%). Scored against FanDuel's own price with
  the margin taken out: per prop, 100 x (market's squared error - yours), the Brier score
  difference. The market scores 0 by definition; beating it means being more right than
  the book, which is rare, and the leaderboard shows how rare. Konjure's model plays every
  day on the same terms. Entries close at the first tip-off; props grade from the box score
  the next morning. A player who does not play voids his prop for everyone.
"""
from __future__ import annotations

import datetime
import hashlib
import random

import games_store as gs

GTG_ROUNDS = 5
BTM_PROPS = 5
BTM_STATS = ("Points", "Rebounds", "Assists", "Pts+Rebs+Asts", "3-PT Made")


def _rng(day: datetime.date, salt: str) -> random.Random:
    return random.Random(int(hashlib.sha256(f"{salt}:{day}".encode()).hexdigest()[:12], 16))


# ── Guess the Grade ───────────────────────────────────────────────────────────────────────

def gtg_points(guess: float, actual: float) -> int:
    return int(max(0, round(100 - 2 * abs(float(guess) - float(actual)))))


def _nba_night(day: datetime.date):
    """(date, graded frame) for the latest NBA night before `day` with a real slate."""
    import nba_grades as ng
    import parlay_model as pm
    season = pm.nba_season_strings(day)[0]
    try:
        log = ng.season_log(season)
    except Exception:
        return None, None
    nights = sorted({d.date() for d in log["date"].unique() if d.date() < day}, reverse=True)
    for night in nights[:7]:
        g = ng.grade_night(season, night)
        if len(g) >= 20:
            return night, g
    return None, None


def gtg_puzzle(day: datetime.date) -> dict:
    """The day's five rounds: {'sport', 'night', 'rounds': [...]} or {} when there is nothing to grade."""
    night, g = _nba_night(day)
    rounds = []
    if g is not None:
        g = g[g["MIN"] >= 15].dropna(subset=["score"])
        rng = _rng(day, "gtg")
        for lo in (0, 20, 40, 60, 80):
            band = g[(g["score"] >= lo) & (g["score"] < lo + 20 + (1 if lo == 80 else 0))]
            if band.empty:
                continue
            r = band.iloc[rng.randrange(len(band))]
            ts = r["ts_pct"]
            rounds.append({
                "player": r["PLAYER_NAME"], "team": r["TEAM_ABBREVIATION"], "opponent": r["opponent"],
                "result": r["WL"], "minutes": int(r["MIN"]),
                "line": (f"{int(r['PTS'])} pts · {int(r['REB'])} reb · {int(r['AST'])} ast · "
                         f"{int(r['STL'])} stl · {int(r['BLK'])} blk · {int(r['TOV'])} TO"
                         + (f" · {ts:.0%} true shooting" if ts == ts else "")),
                "plus_minus": int(r["PLUS_MINUS"]), "score": float(r["score"]), "grade": r["grade"]})
        rng.shuffle(rounds)
    if len(rounds) < GTG_ROUNDS:
        return {}
    return {"sport": "NBA", "night": night.isoformat(), "rounds": rounds}


def gtg_share(day: datetime.date, picks: list, rounds: list) -> str:
    sq = []
    for p, r in zip(picks, rounds):
        miss = abs(p - r["score"])
        sq.append("🟩" if miss <= 5 else "🟨" if miss <= 12 else "🟥")
    total = sum(gtg_points(p, r["score"]) for p, r in zip(picks, rounds))
    return (f"Konjure Guess the Grade · {day:%b %d}\n{''.join(sq)}  {total}/500\n"
            f"konjureanalytics.com")


# ── Beat the Market ───────────────────────────────────────────────────────────────────────

def _decimal(american: int) -> float:
    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def btm_build_slate(day: datetime.date) -> dict:
    """Pick the day's five props from the live board: contestable lines on players who play big minutes."""
    import parlay_builder as pb
    import nba_fantasy as nf
    import nba_weekly_picks as nwp
    props = [p for p in pb.load_props("nba") if nwp._local_date(p["start_time"]) == day]
    if not props:
        return {}
    overs = {}
    for p in props:
        if p["stat_type"] not in BTM_STATS:
            continue
        k = (p["player_name"], p["stat_type"], p["line_score"], p["game_id"])
        overs.setdefault(k, {})[p["side"]] = p
    proj = nf.projections()
    minutes = {r["key"]: r["proj_min"] for _, r in proj.iterrows()}
    pool = []
    for (player, stat, line, gid), sides in overs.items():
        o, u = sides.get("over"), sides.get("under")
        if not o or not u:
            continue                              # need both sides to know the market's view
        p_over = float(o["fair_prob"])
        if not 0.38 <= p_over <= 0.62:
            continue                              # a 90% favourite is not a decision
        mins = minutes.get(nf._norm(player), 0)
        if mins < 27:
            continue
        pool.append({"player": player, "stat": stat, "line": float(line), "game": o["game_label"],
                     "game_id": gid, "start_time": o["start_time"], "p_over_market": round(p_over, 4),
                     "over_odds": int(o["american_odds"]), "under_odds": int(u["american_odds"]),
                     "minutes": mins})
    rng = _rng(day, "btm")
    rng.shuffle(pool)
    pool.sort(key=lambda r: -r["minutes"])
    chosen, games, stats = [], set(), {}
    for r in pool:                                # one per game, no stat more than twice
        if r["game"] in games or stats.get(r["stat"], 0) >= 2:
            continue
        chosen.append(r); games.add(r["game"]); stats[r["stat"]] = stats.get(r["stat"], 0) + 1
        if len(chosen) == BTM_PROPS:
            break
    if len(chosen) < BTM_PROPS:
        return {}
    # Konjure's own entry: the model's chance of the over, frozen with the slate.
    legs = [{"sport": "nba", "player_name": r["player"], "stat_type": r["stat"], "line_score": r["line"],
             "side": "over", "american_odds": r["over_odds"], "fair_prob": r["p_over_market"],
             "game_id": r["game_id"], "game_label": r["game"], "team": "", "start_time": r["start_time"]}
            for r in chosen]
    try:
        mp = pb.model_probs(legs)
    except Exception:
        mp = {}
    for r, l in zip(chosen, legs):
        r["p_over_model"] = mp.get(pb._key(l))
        r["outcome"] = None
        r["actual"] = None
    chosen.sort(key=lambda r: r["start_time"])
    return {"date": day.isoformat(), "props": chosen, "locks_at": chosen[0]["start_time"],
            "settled": False}


def btm_slate(day: datetime.date, create: bool = True) -> dict:
    """The day's slate, built and frozen by the first visitor after props post."""
    row = gs.get(f"btm_slate:{day}")
    if row:
        return row["payload"]
    if not create:
        return {}
    s = btm_build_slate(day)
    if s:
        gs.put(f"btm_slate:{day}", "btm_slate", day.isoformat(), s, insert_only=True)
        row = gs.get(f"btm_slate:{day}")             # someone else may have frozen it first
        return row["payload"] if row else s
    return {}


def locked(slate: dict, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    t = datetime.datetime.fromisoformat(slate["locks_at"].replace("Z", "+00:00"))
    return now >= t


def prop_points(p_over: float | None, prop: dict) -> float | None:
    """100 x (market Brier - this Brier) on one settled prop; None if void or no forecast."""
    if prop.get("outcome") not in ("over", "under") or p_over is None:
        return None
    y = 1.0 if prop["outcome"] == "over" else 0.0
    return 100.0 * ((prop["p_over_market"] - y) ** 2 - (float(p_over) - y) ** 2)


def entry_p_over(pick: dict) -> float:
    c = float(pick["confidence"]) / 100.0
    return c if pick["side"] == "over" else 1.0 - c


def entry_score(slate: dict, picks: list) -> float:
    pts = [prop_points(entry_p_over(pk), pr) for pk, pr in zip(picks, slate["props"])]
    return round(sum(p for p in pts if p is not None), 1)


def model_score(slate: dict) -> float | None:
    pts = [prop_points(pr.get("p_over_model"), pr) for pr in slate["props"]]
    pts = [p for p in pts if p is not None]
    return round(sum(pts), 1) if pts else None


def settle(day: datetime.date) -> bool:
    """Grade a finished day's props and score every entry. True if anything changed."""
    if day >= datetime.date.today():
        return False
    row = gs.get(f"btm_slate:{day}")
    if not row or row["payload"].get("settled"):
        return False
    import parlay_builder as pb
    s, box = row["payload"], pb._Box()
    for p in s["props"]:
        if p.get("outcome"):
            continue
        leg = {"sport": "nba", "player_name": p["player"], "stat_type": p["stat"], "start_time": p["start_time"]}
        try:
            actual = box.actual(leg)
        except Exception:
            return False
        if actual is None:
            # No row for him. If the night's box scores are in he did not play: void now,
            # rather than holding the whole day's leaderboard for the three-day fallback.
            night_in = bool(box._nba) and any(d == day for games_ in box._nba.values() for d, _ in games_)
            if night_in or (datetime.date.today() - day).days >= 3:
                p["outcome"] = "void"
            continue
        p["actual"] = actual
        p["outcome"] = "void" if actual == p["line"] else ("over" if actual > p["line"] else "under")
    if not all(p.get("outcome") for p in s["props"]):
        gs.put(row["id"], "btm_slate", day.isoformat(), s)
        return True
    s["settled"] = True
    gs.put(row["id"], "btm_slate", day.isoformat(), s)
    for e in gs.rows("btm_entry", day.isoformat()):
        gs.put(e["id"], "btm_entry", day.isoformat(), e["payload"], email=e["email"],
               score=entry_score(s, e["payload"]["picks"]))
    return True


def leaderboard(day: datetime.date) -> list:
    """[(name, score)] for a settled day, best first, with Konjure's model and the market."""
    row = gs.get(f"btm_slate:{day}")
    if not row or not row["payload"].get("settled"):
        return []
    s = row["payload"]
    out = [(gs.handle(e["email"]), e["score"]) for e in gs.rows("btm_entry", day.isoformat())
           if e.get("score") is not None]
    ms = model_score(s)
    if ms is not None:
        out.append(("🤖 Konjure model", ms))
    out.append(("📈 The market", 0.0))
    return sorted(out, key=lambda x: -x[1])


def season_table() -> list:
    """[(name, total, days)] over every settled day."""
    tot = {}
    for e in gs.rows("btm_entry"):
        if e.get("score") is None:
            continue
        n, t = tot.get(e["email"], (0, 0.0))
        tot[e["email"]] = (n + 1, t + e["score"])
    return sorted(((gs.handle(k), round(t, 1), n) for k, (n, t) in tot.items()), key=lambda x: -x[1])


# ── the page ──────────────────────────────────────────────────────────────────────────────

def _email():
    import accounts as acct
    return acct.signed_in_email() or ("owner" if acct.backend() == "local" else None)


def render() -> None:
    import streamlit as st
    st.markdown("#### 🎮 Konjure Games")
    st.caption("Free daily games built on the same data as the dashboard. No prizes, just a leaderboard.")
    t1, t2 = st.tabs(["Guess the Grade", "Beat the Market"])
    with t1:
        _render_gtg()
    with t2:
        _render_btm()


def _render_gtg() -> None:
    import streamlit as st

    @st.cache_data(ttl=3600, show_spinner=False)
    def _puzzle(day):
        return gtg_puzzle(day)

    today = datetime.date.today()
    with st.spinner("Loading last night's lines…"):
        pz = _puzzle(today)
    if not pz:
        st.info("No graded NBA night to play yet. Guess the Grade returns the morning after the next slate.")
        return
    st.caption(f"Five stat lines from the NBA night of {pz['night']}, names hidden. Guess each one's Konjure "
               "grade (0-100, a percentile against every player-game of last season). 100 points a round, "
               "minus 2 for every point you miss by.")
    email = _email()
    done = gs.get(f"gtg_play:{today}:{email}") if email else None
    key = f"gtg_{today}"
    state = st.session_state.setdefault(key, {"picks": [], "revealed": False})
    if done and not state["picks"]:
        state["picks"], state["revealed"] = done["payload"]["picks"], True
    rounds = pz["rounds"]
    i = len(state["picks"])

    for j, (p, r) in enumerate(zip(state["picks"], rounds)):
        st.markdown(f"**{j + 1}. {r['player']}** ({r['team']} vs {r['opponent']}, {r['result']}) — {r['line']}. "
                    f"Grade **{r['grade']} {r['score']:.0f}**, you said {p:.0f}: **+{gtg_points(p, r['score'])}**")

    if i < len(rounds):
        r = rounds[i]
        st.markdown(f"##### Round {i + 1} of {len(rounds)}")
        st.markdown(f"<div style='font-size:1.15rem;line-height:1.6'><b>{r['line']}</b><br>"
                    f"<span style='color:var(--text-muted)'>{r['minutes']} minutes · {r['team']} vs {r['opponent']} · "
                    f"{'won' if r['result'] == 'W' else 'lost'} · {r['plus_minus']:+d} on the floor</span></div>",
                    unsafe_allow_html=True)
        g = st.slider("Your grade", 0, 100, 50, key=f"{key}_s{i}")
        if st.button("Lock it in", key=f"{key}_b{i}", type="primary"):
            state["picks"].append(g)
            if len(state["picks"]) == len(rounds) and email:
                total = sum(gtg_points(p, x["score"]) for p, x in zip(state["picks"], rounds))
                gs.put(f"gtg_play:{today}:{email}", "gtg_play", today.isoformat(),
                       {"picks": state["picks"]}, email=email, score=total, insert_only=True)
            st.rerun()
        return

    total = sum(gtg_points(p, r["score"]) for p, r in zip(state["picks"], rounds))
    st.success(f"Today: **{total} / 500**")
    st.code(gtg_share(today, state["picks"], rounds), language=None)
    if not email:
        st.caption("Sign in (any Edge Finder tab) to keep your score on the leaderboard.")
    board = sorted(((gs.handle(e["email"]), e["score"]) for e in gs.rows("gtg_play", today.isoformat())),
                   key=lambda x: -x[1])[:10]
    if board:
        st.markdown("**Today's leaderboard**")
        st.table([{"Player": n, "Score": int(s)} for n, s in board])


def _render_btm() -> None:
    import streamlit as st
    today = datetime.date.today()
    email = _email()
    try:
        settle(today - datetime.timedelta(days=1))
    except Exception:
        pass
    st.caption("Five of tonight's NBA props. Pick over or under and how sure you are. You score against "
               "FanDuel's own odds with the margin removed: closer to what happens than the market earns points, "
               "further loses them. The market scores 0; beating it is rare. Konjure's model plays too.")
    with st.spinner("Loading tonight's slate…"):
        try:
            s = btm_slate(today)
        except Exception as e:
            s = {}
            st.caption(f"Slate unavailable ({e}).")
    if not s:
        st.info("No slate yet: it opens once tonight's NBA props are posted, usually by early afternoon.")
    else:
        closed = locked(s)
        mine = gs.get(f"btm_entry:{today}:{email}") if email else None
        prev = (mine or {}).get("payload", {}).get("picks") or []
        st.markdown(f"**Tonight's five** · entries close at the first tip "
                    f"({datetime.datetime.fromisoformat(s['locks_at'].replace('Z', '+00:00')).astimezone():%I:%M %p})")
        picks = []
        for i, p in enumerate(s["props"]):
            c1, c2, c3 = st.columns([3, 1.3, 2])
            c1.markdown(f"**{p['player']}** · {p['stat']} **{p['line']:g}**<br>"
                        f"<span style='color:var(--text-muted);font-size:.85rem'>{p['game']} · market "
                        f"{p['p_over_market']:.0%} over</span>", unsafe_allow_html=True)
            d = prev[i] if i < len(prev) else {"side": "over", "confidence": 60}
            side = c2.radio("Side", ["over", "under"], index=0 if d["side"] == "over" else 1,
                            key=f"btm_side_{today}_{i}", horizontal=True, label_visibility="collapsed",
                            disabled=closed)
            conf = c3.slider("How sure", 50, 95, int(d["confidence"]), 5, key=f"btm_conf_{today}_{i}",
                             format="%d%%", label_visibility="collapsed", disabled=closed)
            picks.append({"side": side, "confidence": conf})
        if closed:
            st.caption("Entries are closed for tonight. Results post tomorrow morning."
                       + (" Your entry is in." if mine else ""))
        elif not email:
            st.info("Create a free account (any Edge Finder tab) to enter.")
        elif st.button("Update my entry" if mine else "Submit my entry", type="primary", key=f"btm_go_{today}"):
            if locked(s):
                st.error("Too late: the first game has tipped.")
            else:
                gs.put(f"btm_entry:{today}:{email}", "btm_entry", today.isoformat(), {"picks": picks}, email=email)
                st.success("Entry saved. You can change it until the first tip.")

    y = today - datetime.timedelta(days=1)
    board = leaderboard(y)
    if board:
        st.markdown(f"**Last night's results ({y:%b %d})**")
        ys = gs.get(f"btm_slate:{y}")["payload"]
        st.caption(" · ".join(f"{p['player']} {p['stat']} {p['line']:g}: {p.get('actual', '—')} "
                              f"({p.get('outcome') or 'pending'})" for p in ys["props"]))
        st.table([{"Player": n, "Points": f"{v:+.1f}"} for n, v in board[:12]])
    season = season_table()
    if season:
        st.markdown("**Season leaderboard**")
        st.table([{"Player": n, "Points": f"{t:+.1f}", "Days": d} for n, t, d in season[:15]])
