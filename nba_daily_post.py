"""
nba_daily_post.py — the nightly NBA post for Discord: tonight's board, last night's results
and a "research behind the bets" card, built every afternoon and sent only when asked.

    python nba_daily_post.py             # build the cards (a dry run; nothing is posted)
    python nba_daily_post.py --publish   # build and post to Discord

Built by the cloud at 4:30 PM Chicago (.github/workflows/nba-daily.yml) as a dry run; a
human ticks "publish" to send it, the same rule as the weekly issue. A marker in
newsletter_out/ stops a second post on the same day.

The research card is the hand-built cards' method, automated: a leg needs the model at or
above the market, the player's own games this season AND last season behind it, and a
defense that has given his role at least the league average. Every leg carries a note
against it, and the price band prints the market's chance beside the model's, because the
model has not beaten the NBA market — none of this is a demonstrated edge.
"""
from __future__ import annotations

import datetime
import math
import pathlib

import publish

OUT = publish._OUT
_STATS = {"Points": ("PTS", "PTS", "points"), "Rebounds": ("REB", "REB", "rebounds"),
          "Assists": ("AST", "AST", "assists"), "3-PT Made": ("FG3M", "3PM", "threes"),
          "Pts+Rebs+Asts": ("PRA", "PRA", "PRA")}
_RESEARCH_LEGS = 4


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _dec(american: int) -> float:
    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def _american(dec: float) -> int:
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def _ct(start_time: str) -> str:
    """Tip time on the local clock, which is Chicago here and on the runner (TZ is set)."""
    t = datetime.datetime.fromisoformat(str(start_time).replace("Z", "+00:00")).astimezone()
    return t.strftime("%I:%M %p").lstrip("0") + " CT"


# ── research legs ───────────────────────────────────────────────────────────────────────

def research_legs(day: datetime.date, n: int = _RESEARCH_LEGS) -> list:
    """The night's best-supported legs, with notes for and against. [] if too few qualify."""
    import pandas as pd
    import nba_analysis as na
    import nba_matchup as nm
    import nba_scout as sc
    import nba_weekly_picks as nwp

    legs = nwp.slate_legs(day)
    if not legs:
        return []
    cur_s, prev_s = na.seasons(day)
    cur, prev = na.get_season(cur_s), na.get_season(prev_s)
    if cur.empty:
        return []
    for d in (cur, prev):
        if not d.empty:
            d["key"] = d["PLAYER_NAME"].map(sc._norm)
    rg = nm.role_games(cur_s)

    picked, players, per_game = [], set(), {}
    pool = sorted((l for l in legs if l["stat_type"] in _STATS
                   and l["model_prob"] >= 0.55 and l["implied_prob"] >= 0.5 and l["edge"] >= 0
                   and -200 <= l["american_odds"] <= 150), key=lambda l: -l["model_prob"])
    for l in pool:
        if l["player"] in players or per_game.get(l["game"], 0) >= 2:
            continue
        col, lab, word = _STATS[l["stat_type"]]
        k = sc._norm(l["player"])
        a = cur[cur["key"] == k]
        b = prev[prev["key"] == k] if not prev.empty else prev
        if len(a) < 3:
            continue
        over = l["side"] == "over"
        hit = (lambda v: v > l["line"]) if over else (lambda v: v < l["line"])
        rate_c = float(hit(a[col]).mean())
        rate_p = float(hit(b[col]).mean()) if len(b) >= 10 else None
        if rate_c < 0.5 or (rate_p is not None and rate_p < 0.5):
            continue
        team = str(a["TEAM_ABBREVIATION"].iloc[-1])
        away, home = [t.strip() for t in l["game"].split("@")]
        if team not in (away, home):
            continue
        opp = home if team == away else away
        pid = int(a["PLAYER_ID"].iloc[-1])
        role = (nm.player_role(rg, pid) or (None,))[0]
        dvr = nm.defense_vs_role(rg, role) if role else pd.DataFrame()
        if dvr.empty or opp not in dvr.index:
            continue
        lg = nm.league_avg(rg, role)
        allowed, avg = float(dvr.loc[opp, lab]), float(lg[lab])
        if (allowed - avg) * (1 if over else -1) < 0:
            continue                                # the defense works against the leg
        rank = int(dvr.loc[opp, lab + " rk"])       # 1 = allowed the fewest
        nth = len(dvr) - rank + 1 if over else rank
        role_word = role.lower().replace("starter", "starting").split()
        role_txt = f"{role_word[0]} {dict(g='guards', f='forwards', c='centers')[role_word[1]]}"
        notes = [("+", f"{opp} allow {allowed:.1f} {word} to {role_txt}, "
                       f"{_ordinal(nth)} {'most' if over else 'fewest'}")]
        vals = ", ".join(f"{v:g}" for v in a[col].tail(5))
        if len(b) >= 10:
            notes.append(("+", f"{vals} this season; {b[col].mean():.1f} a game last season ({len(b)} games)"))
        else:
            notes.append(("+", f"{vals} this season, {'over' if over else 'under'} in "
                               f"{int(hit(a[col]).sum())} of {len(a)}"))
        mins = list(a["MIN"].tail(3).round().astype(int))
        if rate_c < 0.67:
            risk = f"{'Over' if over else 'Under'} in only {int(hit(a[col]).sum())} of {len(a)} this season"
        elif mins and mins[-1] < a["MIN"].mean() - 4:
            risk = f"Minutes dipped last game: {', '.join(map(str, mins))}"
        elif len(b) < 20:
            risk = f"Only {len(b)} games last season. Small sample both ways"
        else:
            risk = f"Our model says {l['model_prob']:.0%}, the market {l['implied_prob']:.0%}: unproven"
        notes.append(("!", risk))
        side = "over" if over else "under"
        picked.append({**l, "pid": pid, "team": team, "opp": opp,
                       "title": f"{l['player']}  ·  {side} {l['line']:g} {word}",
                       "game_hdr": f"{l['game']}  ·  {_ct(l['start_time'])}", "notes": notes})
        players.add(l["player"])
        per_game[l["game"]] = per_game.get(l["game"], 0) + 1
        if len(picked) == n:
            break
    return picked if len(picked) >= 3 else []


# ── cards ───────────────────────────────────────────────────────────────────────────────

def _board_card(day: datetime.date, e: dict, stamp: str):
    import nba_daily_picks as ndp
    rows = []
    for g in [g for g in e.get("ats", []) if g.get("published")]:
        rows.append((g["pick_label"], f"{g['edge']:.1f} pts"))
    legs = e.get("parlay") or []
    if legs:
        rows.append(("", ""))
        for l in legs:
            rows.append((f"{l['player'].split()[-1]} {l['side'][0].upper()}{l['line']:g} "
                         f"{_STATS.get(l['stat_type'], (0, l['stat_type']))[1]}",
                         f"{int(l['american_odds']):+d}"))
    if not rows:
        return None
    p = e.get("parlay_price") or e.get("parlay_withheld_price") or {}
    foot2 = (f"Props parlay {p.get('american', 0):+d}: {p.get('blended_prob', 0):.1%} vs "
             f"{p.get('breakeven_prob', 0):.1%} to break even" if legs else
             (f"Props parlay held back: {p.get('blended_prob', 0):.1%} vs {p.get('breakeven_prob', 0):.1%} needed"
              if e.get("parlay_withheld") else "No props parlay tonight"))
    bt = ndp.BACKTEST
    return publish.render_card(
        "NBA Tonight", f"{day.strftime('%A, %b')} {day.day}  ·  spread picks, model vs line",
        rows, f"A tracked test: backtest {bt['ats_pct']}% ATS, {ndp.BREAKEVEN_ATS}% breaks even",
        OUT / f"{stamp}-nba-daily-board.jpg", footer2=foot2)


def _results_card(day: datetime.date, stamp: str):
    import nba_daily_picks as ndp
    y = (day - datetime.timedelta(days=1)).isoformat()
    e = ndp._load().get(y)
    if not e:
        return None
    pub = [g for g in e.get("ats", []) if g.get("published")]
    if not pub or any(g.get("outcome") is None for g in pub):
        return None
    rows = []
    for g in pub:
        sc = f" ({g['away_pts']}-{g['home_pts']})" if g.get("home_pts") is not None else ""
        rows.append((g["pick_label"] + sc, (g.get("outcome") or "").upper()))
    pl = e.get("parlay") or e.get("parlay_withheld") or []
    done = [l for l in pl if l.get("outcome") in ("win", "loss")]
    if done:
        hit = sum(l["outcome"] == "win" for l in done)
        label = "Props parlay" if e.get("parlay") else "Held-back parlay"
        rows.append((f"{label}: {hit} of {len(done)} legs", "HIT" if hit == len(done) else "MISS"))
    rec = ndp.record()
    a = rec["ats"]
    yd = datetime.date.fromisoformat(y)
    return publish.render_card(
        "Last Night", f"{yd.strftime('%A, %b')} {yd.day}  ·  graded",
        rows, f"Season: spread picks {a['record']} ({a['pct']}%)" if a.get("n") else "Season record starts here",
        OUT / f"{stamp}-nba-daily-results.jpg",
        footer2="Every pick is logged before tip and graded the next morning.")


def _research_card(day: datetime.date, legs: list, stamp: str):
    import nba_cards
    dec = math.prod(_dec(int(l["american_odds"])) for l in legs)
    mkt = math.prod(float(l["implied_prob"]) for l in legs)
    mdl = math.prod(float(l["model_prob"]) for l in legs)
    games = len({l["game"] for l in legs})
    panels = [{"game": l["game_hdr"], "odds": f"{int(l['american_odds']):+d}", "pid": l["pid"],
               "team": l["team"], "title": l["title"], "notes": l["notes"]} for l in legs]
    return nba_cards.render_research(
        panels, f"{day.strftime('%A, %b').upper()} {day.day}  ·  NBA RESEARCH CARD",
        f"{len(legs)} legs across {games} game{'s' if games != 1 else ''}, with the numbers for them and against them.",
        (f"{len(legs)}-leg parlay  ~{_american(dec):+d}", "at the listed prices",
         f"FanDuel's prices say about {mkt:.0%} to hit; our model says {mdl:.0%}, but it hasn't beaten the market."),
        "Stats: NBA box scores, last season and this. Graded after the games.",
        OUT / f"{stamp}-nba-daily-research.jpg")


def build(day: datetime.date | None = None) -> dict:
    """Every card for `day` that has something to say, plus the Discord headline."""
    import nba_daily_picks as ndp
    day = day or datetime.date.today()
    stamp = day.isoformat()
    e = ndp._load().get(stamp) or ndp.log_today(day)
    cards, notes = [], []
    board = _board_card(day, e, stamp) if e.get("ats") else None
    if board:
        cards.append(board)
    else:
        notes.append(f"no board card: {e.get('note') or 'no games on the board'}")
    res = _results_card(day, stamp)
    if res:
        cards.append(res)
    else:
        notes.append("no results card: last night not graded or no picks")
    try:
        legs = research_legs(day)
    except Exception as ex:
        legs = []
        notes.append(f"research card failed: {type(ex).__name__}: {ex}")
    if legs:
        cards.append(_research_card(day, legs, stamp))
    else:
        notes.append("no research card: fewer than 3 legs cleared every check")
    pub = [g["pick_label"] for g in e.get("ats", []) if g.get("published")]
    head = f"NBA tonight ({day.strftime('%b')} {day.day})"
    if pub:
        head += ": spread picks " + ", ".join(pub)
    if legs:
        head += ". Research card: " + ", ".join(l["title"].replace("  ·  ", " ") for l in legs)
    head += ". A tracked test, not a demonstrated edge."
    return {"day": stamp, "cards": [str(c) for c in cards], "headline": head, "notes": notes,
            "research": [{k: l[k] for k in ("player", "stat_type", "line", "side", "american_odds",
                                             "game", "start_time", "model_prob", "implied_prob")}
                         for l in legs]}


def marker(day: datetime.date) -> pathlib.Path:
    return OUT / f".published-{day.isoformat()}-nba-daily-discord"


def run(publish_now: bool = False, day: datetime.date | None = None) -> dict:
    day = day or datetime.date.today()
    r = build(day)
    r["posted"] = None
    if not publish_now:
        r["action"] = "DRY RUN: nothing posted"
        return r
    if marker(day).exists():
        r["action"] = "already posted today: skipped"
        return r
    if not r["cards"]:
        r["action"] = "nothing to post"
        return r
    res = publish.post_webhook_images(r["headline"], r["cards"], "discord")
    r["posted"] = {k: v for k, v in res.items() if k != "body"}
    if res.get("ok"):
        marker(day).write_text(datetime.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        r["action"] = "posted"
    else:
        r["action"] = "post failed"
    return r


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run("--publish" in sys.argv), indent=1, default=str))
