"""
games.py — two free daily games built on Konjure's own data. No prizes, so no contest law.

GUESS THE GRADE
  Five stat lines from the last week of NBA games, names hidden. Guess each line's grade,
  0-100: the same composite score the NBA Hub publishes (nba_grades). Each guess scores 100
  minus twice the miss, floored at 0, so a perfect game is 500. Every game is a fresh random
  draw, one line from each fifth of the grade range, so each has a star, a dud and three in
  between. Play as often as you like; the day's leaderboard keeps each player's best.

  A CHALLENGE is a fixed five everyone gets, for the podcast's on-air segment: the link
  ?challenge=YYYYMMDD draws from the week before that date with a seed from the date, so it
  needs no storage and plays the same next month. Its leaderboard is its own.

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
import functools
import hashlib
import random
from zoneinfo import ZoneInfo

import games_store as gs

GTG_ROUNDS = 5
BTM_PROPS = 5
BTM_STATS = ("Points", "Rebounds", "Assists", "Pts+Rebs+Asts", "3-PT Made")


def _rng(day: datetime.date, salt: str) -> random.Random:
    return random.Random(int(hashlib.sha256(f"{salt}:{day}".encode()).hexdigest()[:12], 16))


# ── Guess the Grade ───────────────────────────────────────────────────────────────────────

def gtg_points(guess: float, actual: float) -> int:
    return int(max(0, round(100 - 2 * abs(float(guess) - float(actual)))))


def gtg_pool(day: datetime.date):
    """Every graded player-game (15+ minutes) from the NBA nights in the week before `day`."""
    import nba_grades as ng
    import pandas as pd
    import parlay_model as pm
    season = pm.nba_season_strings(day)[0]
    try:
        log = ng.season_log(season)
    except Exception:
        return None
    week = log[(log["date"] < pd.Timestamp(day)) & (log["date"] >= pd.Timestamp(day - datetime.timedelta(days=7)))]
    if week.empty:
        return None
    g = ng.grade_games(week, ng._prev_season(season))
    return g[g["MIN"] >= 15].dropna(subset=["score"]).reset_index(drop=True)


def gtg_puzzle(pool, rng: random.Random | None = None) -> dict:
    """Five rounds drawn from `pool`, one per fifth of the grade range: {'rounds': [...]} or {}."""
    rng = rng or random.Random()
    rounds = []
    if pool is not None and not pool.empty:
        for lo in (0, 20, 40, 60, 80):
            band = pool[(pool["score"] >= lo) & (pool["score"] < lo + 20 + (1 if lo == 80 else 0))]
            if band.empty:
                continue
            r = band.iloc[rng.randrange(len(band))]
            ts = r["ts_pct"]
            rounds.append({
                "player": r["PLAYER_NAME"], "player_id": int(r["PLAYER_ID"]), "team": r["TEAM_ABBREVIATION"],
                "opponent": r["opponent"], "result": r["WL"], "minutes": int(r["MIN"]),
                "night": r["date"].strftime("%a %b %d"),
                "line": (f"{int(r['PTS'])} pts · {int(r['REB'])} reb · {int(r['AST'])} ast · "
                         f"{int(r['STL'])} stl · {int(r['BLK'])} blk · {int(r['TOV'])} TO"
                         + (f" · {ts:.0%} true shooting" if ts == ts else "")),
                "plus_minus": int(r["PLUS_MINUS"]), "score": float(r["score"]), "grade": r["grade"]})
        rng.shuffle(rounds)
    if len(rounds) < GTG_ROUNDS:
        return {}
    return {"sport": "NBA", "rounds": rounds}


CHALLENGE_URL = "https://konjureanalytics.com/app/?challenge={code}"


def challenge_code(day: datetime.date) -> str:
    return day.strftime("%Y%m%d")


def challenge_day(code) -> datetime.date | None:
    try:
        d = datetime.datetime.strptime(str(code).strip(), "%Y%m%d").date()
    except ValueError:
        return None
    return d if d <= datetime.date.today() else None


def gtg_challenge(day: datetime.date) -> dict:
    """The fixed five for `day`'s challenge, the same for everyone and on every visit."""
    pool = gtg_pool(day)
    if pool is not None and not pool.empty:
        pool = pool.sort_values(["date", "PLAYER_ID"]).reset_index(drop=True)
    return gtg_puzzle(pool, _rng(day, "challenge"))


def gtg_share(day: datetime.date, picks: list, rounds: list) -> str:
    sq = []
    for p, r in zip(picks, rounds):
        miss = abs(p - r["score"])
        sq.append("🟩" if miss <= 5 else "🟨" if miss <= 12 else "🟥")
    total = sum(gtg_points(p, r["score"]) for p, r in zip(picks, rounds))
    return (f"Konjure Guess the Grade · {day:%b %d}\n{''.join(sq)}  {total}/500\n"
            f"konjureanalytics.com")


def challenge_share(day: datetime.date, picks: list, rounds: list) -> str:
    sq = "".join("🟩" if abs(p - r["score"]) <= 5 else "🟨" if abs(p - r["score"]) <= 12 else "🟥"
                 for p, r in zip(picks, rounds))
    total = sum(gtg_points(p, r["score"]) for p, r in zip(picks, rounds))
    return (f"🎙️ Konjure podcast challenge · {day:%b %d}\n{sq}  {total}/500\n"
            f"Beat me: {CHALLENGE_URL.format(code=challenge_code(day))}")


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
#
# Built for the moment of finding out. Guess the Grade locks a guess, then reveals the player
# with both numbers on one track before the next round; Beat the Market shows, as the slider
# moves, what each prop would win or cost against the market, so the scoring rule is felt
# rather than read.

_GRADE_COLOR = {"A": "#34d399", "B": "#60a5fa", "C": "#fbbf24", "D": "#fb923c", "F": "#f87171"}
_SQUARE = {"🟩": "#34d399", "🟨": "#fbbf24", "🟥": "#f87171"}

_CSS = """<style>
.kg-hero{background:linear-gradient(135deg,#1e1b4b 0%,#312e81 45%,#0f172a 100%);border:1px solid #3730a3;
 border-radius:18px;padding:1.3rem 1.5rem;margin:.2rem 0 1rem;position:relative;overflow:hidden}
.kg-hero:after{content:"";position:absolute;right:-60px;top:-60px;width:220px;height:220px;border-radius:50%;
 background:radial-gradient(circle,rgba(129,140,248,.35),transparent 70%)}
.kg-hero .k{font-size:.66rem;letter-spacing:.2em;text-transform:uppercase;color:#a5b4fc;font-weight:800}
.kg-hero h2{color:#fff;font-size:1.7rem;margin:.2rem 0 .3rem;font-weight:800}
.kg-hero p{color:#c7d2fe;font-size:.9rem;margin:0;max-width:640px}
.kg-chips{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.8rem}
.kg-chip{background:rgba(15,23,42,.55);border:1px solid #4338ca;border-radius:999px;padding:.25rem .75rem;
 font-size:.78rem;color:#e0e7ff}
.kg-steps{display:flex;gap:.45rem;margin:.4rem 0 1rem;align-items:center}
.kg-step{flex:1;height:10px;border-radius:999px;background:#232838}
.kg-step.now{background:#818cf8;animation:kgpulse 1.4s ease-in-out infinite}
.kg-score{font-size:.8rem;color:#9ca3af;white-space:nowrap;margin-left:.6rem}
.kg-score b{color:#fff;font-size:1rem}
.kg-card{background:linear-gradient(160deg,#171b2b,#111522);border:1px solid #262c40;border-radius:18px;
 padding:1.3rem 1.4rem;margin-bottom:.8rem}
.kg-myst{display:flex;gap:1.1rem;align-items:center;margin-bottom:1rem}
.kg-q{width:76px;height:76px;border-radius:50%;display:flex;align-items:center;justify-content:center;
 font-size:2.2rem;font-weight:900;color:#a5b4fc;background:radial-gradient(circle at 35% 30%,#3730a3,#1e1b4b);
 border:2px dashed #6366f1;flex-shrink:0}
.kg-who{color:#fff;font-weight:800;font-size:1.15rem}
.kg-sub{color:#8b92a8;font-size:.82rem}
.kg-tiles{display:grid;grid-template-columns:repeat(7,1fr);gap:.5rem}
.kg-tile{background:#0d1019;border:1px solid #232838;border-radius:12px;padding:.55rem .3rem;text-align:center}
.kg-tile b{display:block;color:#fff;font-size:1.45rem;line-height:1.1;font-weight:800}
.kg-tile span{font-size:.62rem;letter-spacing:.12em;color:#7c8196;text-transform:uppercase}
.kg-call{display:flex;align-items:center;gap:.7rem;margin:.2rem 0 .5rem;color:#9ca3af;font-size:.85rem}
.kg-badge{display:inline-flex;align-items:center;justify-content:center;min-width:54px;height:54px;border-radius:14px;
 font-weight:900;font-size:1.5rem;color:#0b1020;padding:0 .4rem}
.kg-badge.sm{min-width:38px;height:38px;font-size:1.05rem;border-radius:10px}
.kg-rev{display:flex;gap:1.1rem;align-items:center}
.kg-shot{width:96px;height:96px;border-radius:50%;object-fit:cover;object-position:top;background:#1f2330;
 border:3px solid #818cf8;flex-shrink:0;animation:kgin .5s ease-out}
.kg-track{position:relative;height:14px;background:linear-gradient(90deg,#f87171,#fb923c 25%,#fbbf24 50%,#60a5fa 75%,#34d399);
 border-radius:999px;margin:2.3rem .4rem 2.1rem;opacity:.9}
.kg-gap{position:absolute;top:-4px;height:22px;background:rgba(255,255,255,.18);border:1px dashed #fff;border-radius:6px}
.kg-mark{position:absolute;top:-9px;width:4px;height:32px;border-radius:2px;transform:translateX(-2px)}
.kg-mark span{position:absolute;left:50%;transform:translateX(-50%);white-space:nowrap;font-size:.72rem;font-weight:800;
 padding:.1rem .45rem;border-radius:6px}
.kg-mark.you{background:#e0e7ff}.kg-mark.you span{top:-1.55rem;background:#e0e7ff;color:#1e1b4b}
.kg-mark.real{background:#fff;box-shadow:0 0 12px #fff}.kg-mark.real span{bottom:-1.55rem;background:#fff;color:#0b1020}
.kg-pts{font-size:2.6rem;font-weight:900;line-height:1;animation:kgpop .55s cubic-bezier(.2,1.6,.4,1)}
.kg-verdict{font-size:1rem;font-weight:700;color:#e5e7eb;margin-top:.2rem}
.kg-final{text-align:center;padding:1.8rem 1rem}
.kg-final .big{font-size:3.6rem;font-weight:900;color:#fff;line-height:1;animation:kgpop .7s cubic-bezier(.2,1.6,.4,1)}
.kg-final .big small{font-size:1.3rem;color:#7c8196}
.kg-final .tier{display:inline-block;margin-top:.6rem;padding:.3rem .9rem;border-radius:999px;font-weight:800;
 background:#312e81;color:#e0e7ff;letter-spacing:.06em}
.kg-sq{font-size:1.9rem;letter-spacing:.2rem;margin-top:.8rem}
.kg-row{display:flex;align-items:center;gap:.8rem;padding:.55rem .2rem;border-bottom:1px solid #1d2232}
.kg-row:last-child{border-bottom:none}
.kg-row img{width:40px;height:40px;border-radius:50%;object-fit:cover;object-position:top;background:#1f2330}
.kg-row .n{flex:1;color:#e5e7eb;font-size:.9rem}.kg-row .n div{color:#7c8196;font-size:.76rem}
.kg-row .p{font-weight:800;color:#fff;min-width:46px;text-align:right}
.kg-prop{display:flex;gap:.9rem;align-items:center}
.kg-prop img{width:64px;height:64px;border-radius:50%;object-fit:cover;object-position:top;background:#1f2330;
 border:2px solid #312e81;flex-shrink:0}
.kg-line{color:#fff;font-weight:900;font-size:1.25rem}
.kg-meter{position:relative;height:8px;border-radius:999px;background:#5b2333;margin:.55rem 0 .2rem;overflow:hidden}
.kg-meter span{position:absolute;left:0;top:0;bottom:0;background:#34d399;border-radius:999px}
.kg-meter i{position:absolute;top:0;width:2px;height:8px;background:#fff}
.kg-stakes{display:flex;gap:.5rem;font-size:.8rem;margin-top:.15rem;flex-wrap:wrap}
.kg-stakes span{padding:.2rem .55rem;border-radius:8px;font-weight:700}
.kg-win{background:rgba(52,211,153,.14);color:#34d399}.kg-lose{background:rgba(248,113,113,.14);color:#f87171}
.kg-board{background:#111522;border:1px solid #232838;border-radius:14px;padding:.4rem .9rem;margin:.4rem 0 1rem}
.kg-lb{display:flex;align-items:center;gap:.7rem;padding:.5rem 0;border-bottom:1px solid #1b2030;font-size:.9rem;color:#d1d5db}
.kg-lb:last-child{border-bottom:none}
.kg-lb .r{width:2rem;text-align:center;font-weight:800;color:#7c8196}
.kg-lb .n{flex:1}.kg-lb .s{font-weight:800;color:#fff}
.kg-lb.me{background:rgba(129,140,248,.12);border-radius:10px;padding-left:.4rem}
.kg-lb.bot .n{color:#a5b4fc}
.kg-res{display:inline-block;font-size:.7rem;font-weight:800;padding:.15rem .5rem;border-radius:6px;text-transform:uppercase}
@keyframes kgpop{0%{transform:scale(.3);opacity:0}100%{transform:scale(1);opacity:1}}
@keyframes kgin{0%{transform:scale(.6) rotate(-8deg);opacity:0}100%{transform:none;opacity:1}}
@keyframes kgpulse{0%,100%{opacity:1}50%{opacity:.45}}
@media (max-width:700px){.kg-tiles{grid-template-columns:repeat(4,1fr)}.kg-hero h2{font-size:1.35rem}
 .kg-shot{width:72px;height:72px}.kg-pts{font-size:2rem}}
</style>"""


def _esc(s) -> str:
    import html
    return html.escape(str(s))


def _email():
    import accounts as acct
    return acct.signed_in_email() or ("owner" if acct.backend() == "local" else None)


def _headshot(pid) -> str:
    return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{int(pid)}.png" if pid else ""


def _gcolor(grade) -> str:
    return _GRADE_COLOR.get(str(grade)[:1], "#818cf8")


def _letter(score) -> str:
    import nba_grades as ng
    return ng.letter(score)


def _square(miss: float) -> str:
    return "🟩" if miss <= 5 else "🟨" if miss <= 12 else "🟥"


def _verdict(miss: float) -> str:
    return ("🎯 Bullseye" if miss <= 2 else "🔥 Scout's eye" if miss <= 5 else "👍 Close" if miss <= 12
            else "😬 Off the mark" if miss <= 25 else "🧊 Ice cold")


def _tier(total: int) -> str:
    return ("Front-office legend" if total >= 450 else "Lead scout" if total >= 400 else "Scout" if total >= 325
            else "Analyst" if total >= 250 else "Rookie")


def _board_html(rows: list, me: str | None, fmt) -> str:
    """rows: [(name, value)] best first; the visitor's row and the house rows are marked."""
    medal = {0: "🥇", 1: "🥈", 2: "🥉"}
    out = "<div class='kg-board'>"
    for i, (n, v) in enumerate(rows):
        cls = "me" if me and n == me else "bot" if n.startswith(("🤖", "📈")) else ""
        out += (f"<div class='kg-lb {cls}'><div class='r'>{medal.get(i, i + 1)}</div>"
                f"<div class='n'>{_esc(n)}{' · you' if cls == 'me' else ''}</div><div class='s'>{fmt(v)}</div></div>")
    return out + "</div>"


def render() -> None:
    import streamlit as st
    st.markdown(_CSS, unsafe_allow_html=True)
    today = datetime.date.today()
    email = _email()
    played = gs.get(f"gtg_play:{today}:{email}") if email else None
    entered = gs.get(f"btm_entry:{today}:{email}") if email else None
    chips = [f"🧠 Guess the Grade: {'best today ' + str(int(played['score'])) + '/500' if played else 'not played yet'}",
             f"📈 Beat the Market: {'✅ entered' if entered else 'open'}",
             "🆓 Free · new games every day"]
    st.markdown("<div class='kg-hero'><div class='k'>Konjure Games · " + f"{today:%A %b %d}" + "</div>"
                "<h2>Think you can read the game?</h2><p>Two daily games built on the same data as the dashboard. "
                "Grade real stat lines blind, then take on the betting market with tonight's props. "
                "No prizes, just bragging rights and a leaderboard.</p><div class='kg-chips'>"
                + "".join(f"<span class='kg-chip'>{_esc(c)}</span>" for c in chips) + "</div></div>",
                unsafe_allow_html=True)
    t1, t2 = st.tabs(["🧠 Guess the Grade", "📈 Beat the Market"])
    with t1:
        _render_gtg()
    with t2:
        _render_btm()


# ── Guess the Grade ──

def _steps(picks: list, rounds: list, current: int | None) -> str:
    out = "<div class='kg-steps'>"
    for j in range(len(rounds)):
        if j < len(picks):
            out += f"<div class='kg-step' style='background:{_SQUARE[_square(abs(picks[j] - rounds[j]['score']))]}'></div>"
        else:
            out += f"<div class='kg-step{' now' if j == current else ''}'></div>"
    total = sum(gtg_points(p, r["score"]) for p, r in zip(picks, rounds))
    return out + f"<div class='kg-score'>Score <b>{total}</b> / {100 * len(rounds)}</div></div>"


def _mystery(r: dict, i: int, n: int) -> str:
    vals = {}
    for part in r["line"].split(" · "):          # "31 pts", "4 TO", "62% true shooting"
        bits = part.split(" ", 1)
        if len(bits) == 2:
            vals[bits[1]] = bits[0]
    order = [("pts", "PTS"), ("reb", "REB"), ("ast", "AST"), ("stl", "STL"), ("blk", "BLK"), ("TO", "TOV"),
             ("true shooting", "TS%")]
    tiles = "".join(f"<div class='kg-tile'><b>{_esc(vals[k])}</b><span>{lab}</span></div>"
                    for k, lab in order if k in vals)
    return (f"<div class='kg-card'><div class='kg-myst'><div class='kg-q'>?</div><div>"
            f"<div class='kg-sub'>ROUND {i + 1} OF {n} · MYSTERY PLAYER · {_esc(r.get('night', ''))}</div>"
            f"<div class='kg-who'>{_esc(r['team'])} vs {_esc(r['opponent'])} · "
            f"{'Won' if r['result'] == 'W' else 'Lost'}</div>"
            f"<div class='kg-sub'>{r['minutes']} minutes · {r['plus_minus']:+d} on the floor</div></div></div>"
            f"<div class='kg-tiles'>{tiles}</div></div>")


def _reveal(r: dict, guess: float) -> str:
    actual = r["score"]
    miss = abs(guess - actual)
    pts = gtg_points(guess, actual)
    lo, hi = sorted((guess, actual))
    pc = "#34d399" if pts >= 90 else "#fbbf24" if pts >= 70 else "#f87171"
    img = (f"<img class='kg-shot' src='{_esc(_headshot(r.get('player_id')))}'>" if r.get("player_id")
           else "<div class='kg-q'>★</div>")
    return (f"<div class='kg-card'><div class='kg-rev'>{img}<div style='flex:1'>"
            f"<div class='kg-sub'>IT WAS</div><div class='kg-who' style='font-size:1.4rem'>{_esc(r['player'])}</div>"
            f"<div class='kg-sub'>{_esc(r['line'])}</div></div>"
            f"<div style='text-align:center'><div class='kg-badge' style='background:{_gcolor(r['grade'])}'>"
            f"{_esc(r['grade'])}</div><div class='kg-sub' style='margin-top:.2rem'>{actual:.0f}</div></div></div>"
            f"<div class='kg-track'><div class='kg-gap' style='left:{lo}%;width:{max(hi - lo, 0.5)}%'></div>"
            f"<div class='kg-mark you' style='left:{guess}%'><span>You {guess:.0f}</span></div>"
            f"<div class='kg-mark real' style='left:{actual}%'><span>Actual {actual:.0f}</span></div></div>"
            f"<div style='text-align:center'><div class='kg-pts' style='color:{pc}'>+{pts}</div>"
            f"<div class='kg-verdict'>{_verdict(miss)} · off by {miss:.0f}</div></div></div>")


def _render_gtg() -> None:
    import streamlit as st

    @st.cache_data(ttl=3600, show_spinner=False)
    def _pool(day):
        return gtg_pool(day)

    today = datetime.date.today()
    with st.spinner("Loading the week's lines…"):
        pool = _pool(today)
    @st.cache_data(ttl=86400, show_spinner=False)
    def _challenge(day):
        return gtg_challenge(day)

    email = _email()
    cday = challenge_day(st.query_params.get("challenge", ""))
    state = st.session_state.get("gtg_game")
    want = f"challenge:{cday}" if cday else today.isoformat()
    if not state or state.get("day") != want:
        # A fresh random five for every game, or the challenge's fixed five; the draw lives
        # in the session so reruns keep it.
        pz = _challenge(cday) if cday else gtg_puzzle(pool)
        if not pz:
            st.info("No graded NBA games to play yet. Guess the Grade returns the morning after the next slate.")
            return
        n_game = (state or {}).get("n", 0) + 1
        state = st.session_state["gtg_game"] = {"day": want, "n": n_game, "rounds": pz["rounds"],
                                                "picks": [], "phase": "guess", "cheered": False}
    key = f"gtg_{today}_{state['n']}"
    rounds, picks = state["rounds"], state["picks"]
    if cday:
        st.markdown(f"<div class='kg-chips' style='margin:0 0 .4rem'><span class='kg-chip' "
                    f"style='border-color:#f59e0b;color:#fde68a'>🎙️ Podcast challenge · {cday:%b %d} · "
                    "everyone gets these same five</span></div>", unsafe_allow_html=True)
    n = len(rounds)

    if state["phase"] == "reveal":
        st.markdown(_steps(picks, rounds, None), unsafe_allow_html=True)
        st.markdown(_reveal(rounds[len(picks) - 1], picks[-1]), unsafe_allow_html=True)
        last = len(picks) == n
        if st.button("See my score 🏁" if last else "Next player →", type="primary", key=f"{key}_next",
                     width="stretch"):
            state["phase"] = "final" if last else "guess"
            st.rerun()
        return

    if len(picks) < n:
        i = len(picks)
        if i == 0:
            st.caption("Five real stat lines from the past week of NBA games, names hidden. Slide to the grade "
                       "you think each earned (0-100: where it ranks against every player-game of last season). "
                       "100 points a round, minus 2 for every point you miss by.")
        st.markdown(_steps(picks, rounds, i), unsafe_allow_html=True)
        st.markdown(_mystery(rounds[i], i, n), unsafe_allow_html=True)
        g = st.slider("Your grade", 0, 100, 50, key=f"{key}_s{i}")
        st.markdown(f"<div class='kg-call'>Your call <span class='kg-badge sm' style='background:{_gcolor(_letter(g))}'>"
                    f"{_letter(g)}</span> better than {g}% of last season's games</div>", unsafe_allow_html=True)
        if st.button("🔒 Lock it in & reveal", key=f"{key}_b{i}", type="primary", width="stretch"):
            picks.append(g)
            state["phase"] = "reveal"
            if len(picks) == n and email:
                # Each leaderboard keeps a player's best game: the day's, or the challenge's.
                total = sum(gtg_points(p, x["score"]) for p, x in zip(picks, rounds))
                kind, gday = ("gtg_challenge", cday) if cday else ("gtg_play", today)
                best = gs.get(f"{kind}:{gday}:{email}")
                if not best or total > (best.get("score") or 0):
                    gs.put(f"{kind}:{gday}:{email}", kind, gday.isoformat(),
                           {"picks": picks, "rounds": rounds}, email=email, score=total)
            st.rerun()
        return

    total = sum(gtg_points(p, r["score"]) for p, r in zip(picks, rounds))
    squares = "".join(_square(abs(p - r["score"])) for p, r in zip(picks, rounds))
    st.markdown(f"<div class='kg-card kg-final'><div class='kg-sub'>YOUR SCORE</div>"
                f"<div class='big'>{total}<small> / {100 * n}</small></div><div class='tier'>{_tier(total)}</div>"
                f"<div class='kg-sq'>{squares}</div></div>", unsafe_allow_html=True)
    if total >= 400 and not state["cheered"]:
        st.balloons()
    state["cheered"] = True
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("<div class='kg-card' style='padding:.6rem 1rem'>" + "".join(
            f"<div class='kg-row'><img src='{_esc(_headshot(r.get('player_id')))}'>"
            f"<div class='n'><b>{_esc(r['player'])}</b><div>{_esc(r['line'])}</div></div>"
            f"<span class='kg-badge sm' style='background:{_gcolor(r['grade'])}'>{_esc(r['grade'])}</span>"
            f"<div class='p'>{p:.0f}→{r['score']:.0f}<div class='kg-sub'>+{gtg_points(p, r['score'])}</div></div></div>"
            for p, r in zip(picks, rounds)) + "</div>", unsafe_allow_html=True)
    with c2:
        st.markdown("**Share it**")
        st.code(challenge_share(cday, picks, rounds) if cday else gtg_share(today, picks, rounds),
                language=None)
        if not email:
            st.caption("Sign in with the form at the bottom of this page to put your score on the leaderboard.")
        kind, gday = ("gtg_challenge", cday) if cday else ("gtg_play", today)
        board = sorted(((gs.handle(e["email"]), e["score"]) for e in gs.rows(kind, gday.isoformat())),
                       key=lambda x: -x[1])[:10]
        if board:
            st.markdown("**Challenge leaderboard**" if cday else "**Today's leaderboard**")
            st.markdown(_board_html(board, gs.handle(email) if email else None, lambda v: f"{int(v)}"),
                        unsafe_allow_html=True)
        st.caption("Your best game counts for the leaderboard.")
    if st.button("🔁 Play a random five" if cday else "🔁 Play again: new five", type="primary",
                 key=f"{key}_again", width="stretch"):
        st.query_params.pop("challenge", None)
        st.session_state["gtg_game"] = {"day": "", "n": state["n"]}
        st.rerun()


# ── Beat the Market ──

@functools.lru_cache(maxsize=1)
def _ids() -> dict:
    try:
        import nba_snapshot as ns
        return ns.name_ids()
    except Exception:
        return {}


def _stakes(side: str, conf: int, prop: dict) -> tuple:
    """(points if the pick is right, points if it is wrong) for one prop at this confidence."""
    p_over = conf / 100 if side == "over" else 1 - conf / 100
    right = prop_points(p_over, dict(prop, outcome=side))
    wrong = prop_points(p_over, dict(prop, outcome="under" if side == "over" else "over"))
    return right, wrong


def _prop_card(p: dict) -> str:
    pid = _ids().get(str(p["player"]).lower())
    img = (f"<img src='{_esc(_headshot(pid))}'>" if pid else "<div class='kg-q' style='width:64px;height:64px'>?</div>")
    m = p["p_over_market"]
    return (f"<div class='kg-prop'>{img}<div style='flex:1'><div class='kg-who'>{_esc(p['player'])}</div>"
            f"<div class='kg-line'>{_esc(p['stat'])} {p['line']:g}</div>"
            f"<div class='kg-sub'>{_esc(p['game'])}</div>"
            f"<div class='kg-meter'><span style='width:{100 * m:.0f}%'></span><i style='left:50%'></i></div>"
            f"<div class='kg-sub'>Market: <b style='color:#34d399'>{m:.0%} over</b> · "
            f"<b style='color:#f87171'>{1 - m:.0%} under</b></div></div></div>")


def _render_btm() -> None:
    import streamlit as st
    today = datetime.date.today()
    email = _email()
    try:
        settle(today - datetime.timedelta(days=1))
    except Exception:
        pass
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
        lock_t = datetime.datetime.fromisoformat(s["locks_at"].replace("Z", "+00:00"))
        left = lock_t - datetime.datetime.now(datetime.timezone.utc)
        mine = gs.get(f"btm_entry:{today}:{email}") if email else None
        prev = (mine or {}).get("payload", {}).get("picks") or []
        status = ("🔒 Entries closed: results post tomorrow morning" if closed else
                  f"⏳ Locks in {left.seconds // 3600}h {left.seconds % 3600 // 60}m, at the first tip "
                  f"({lock_t.astimezone(ZoneInfo('America/New_York')).strftime('%I:%M %p').lstrip('0')} ET)")
        st.markdown(f"<div class='kg-chips' style='margin:0 0 .6rem'><span class='kg-chip'>{status}</span></div>",
                    unsafe_allow_html=True)
        st.caption("Pick a side and how sure you are. Being more right than FanDuel's odds (margin removed) "
                   "earns points; being more wrong costs them. The market always scores 0, and the green and "
                   "red tags show what's at stake as you slide. Konjure's model plays too.")
        picks, best, worst = [], 0.0, 0.0
        for i, p in enumerate(s["props"]):
            d = prev[i] if i < len(prev) else {"side": "over", "confidence": 60}
            with st.container(border=True):
                c1, c2 = st.columns([3, 2])
                c1.markdown(_prop_card(p), unsafe_allow_html=True)
                with c2:
                    side = st.segmented_control("Side", ["Over", "Under"], default=d["side"].title(),
                                                key=f"btm_side_{today}_{i}", label_visibility="collapsed",
                                                disabled=closed) or d["side"].title()
                    side = side.lower()
                    conf = st.slider("How sure", 50, 95, int(d["confidence"]), 5, key=f"btm_conf_{today}_{i}",
                                     format="%d%% sure", label_visibility="collapsed", disabled=closed)
                    right, wrong = _stakes(side, conf, p)
                    best += right
                    worst += wrong
                    st.markdown(f"<div class='kg-stakes'><span class='kg-win'>{side.upper()} hits {right:+.1f}</span>"
                                f"<span class='kg-lose'>misses {wrong:+.1f}</span></div>", unsafe_allow_html=True)
            picks.append({"side": side, "confidence": conf})
        st.markdown(f"<div class='kg-card' style='display:flex;justify-content:space-around;text-align:center;padding:.9rem'>"
                    f"<div><div class='kg-sub'>ALL FIVE RIGHT</div><div class='kg-line' style='color:#34d399'>{best:+.1f}</div></div>"
                    f"<div><div class='kg-sub'>THE MARKET</div><div class='kg-line'>0.0</div></div>"
                    f"<div><div class='kg-sub'>ALL FIVE WRONG</div><div class='kg-line' style='color:#f87171'>{worst:+.1f}</div></div>"
                    f"</div>", unsafe_allow_html=True)
        if closed:
            st.caption("Entries are closed for tonight." + (" Your entry is in. ✅" if mine else ""))
        elif not email:
            st.info("Like your card? Create a free account with the form at the bottom of this page to enter "
                    "it and join the leaderboard.")
        elif st.button("🔄 Update my entry" if mine else "🚀 Submit my entry", type="primary", key=f"btm_go_{today}",
                       width="stretch"):
            if locked(s):
                st.error("Too late: the first game has tipped.")
            else:
                gs.put(f"btm_entry:{today}:{email}", "btm_entry", today.isoformat(), {"picks": picks}, email=email)
                st.success("Entry saved. You can change it until the first tip.")

    y = today - datetime.timedelta(days=1)
    board = leaderboard(y)
    if board:
        ys = gs.get(f"btm_slate:{y}")["payload"]
        ye = gs.get(f"btm_entry:{y}:{email}") if email else None
        yp = (ye or {}).get("payload", {}).get("picks") or []
        st.markdown(f"#### Last night ({y:%b %d})")
        rows = ""
        for i, p in enumerate(ys["props"]):
            out = p.get("outcome") or "pending"
            col = {"over": "#34d399", "under": "#f87171"}.get(out, "#7c8196")
            mine_pts = prop_points(entry_p_over(yp[i]), p) if i < len(yp) else None
            pid = _ids().get(str(p["player"]).lower())
            rows += (f"<div class='kg-row'><img src='{_esc(_headshot(pid))}'><div class='n'><b>{_esc(p['player'])}</b> "
                     f"{_esc(p['stat'])} {p['line']:g}<div>finished with {p.get('actual', '—')}</div></div>"
                     f"<span class='kg-res' style='background:{col}22;color:{col}'>{out}</span>"
                     f"<div class='p'>{'' if mine_pts is None else f'{mine_pts:+.1f}'}</div></div>")
        st.markdown(f"<div class='kg-card' style='padding:.6rem 1rem'>{rows}</div>", unsafe_allow_html=True)
        st.markdown(_board_html(board[:12], gs.handle(email) if email else None, lambda v: f"{v:+.1f}"),
                    unsafe_allow_html=True)
    season = season_table()
    if season:
        st.markdown("#### Season leaderboard")
        st.markdown(_board_html([(n, t) for n, t, _ in season[:15]], gs.handle(email) if email else None,
                                lambda v: f"{v:+.1f}"), unsafe_allow_html=True)
