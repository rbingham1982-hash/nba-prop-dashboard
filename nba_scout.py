"""
nba_scout.py — scouting write-ups for every NBA player with a prop line tonight, rebuilt on
each cloud board run and shown on the NBA Hub ("Tonight's scouting").

Every number names the season it comes from. The old Scout Report averaged whatever seasons
were ticked and called the result "this season", so in October a player's 2025-26 year read
as his current form and "3 games vs BOS" could be two from last season and one from last
week. Here the current season and last season are separate sentences, the matchup history
is dated game by game, and a sample that spans both seasons says so.

Regular season only: playoff games are a different opponent mix and minutes load.

    python nba_scout.py         # rebuild tonight's write-ups into nba_scout.json

Facts, not picks. The book's price sits beside each line so the reader sees what the market
says; nothing here claims an edge (see nba_daily_picks for the tracked tests).
"""
from __future__ import annotations

import datetime
import json
import pathlib
import unicodedata

import pandas as pd

OUT = pathlib.Path(__file__).with_name("nba_scout.json")
STATS = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3-PT Made": "FG3M",
         "Pts+Rebs+Asts": "PRA"}
_SHORT = {"Points": "pts", "Rebounds": "reb", "Assists": "ast", "3-PT Made": "3PM",
          "Pts+Rebs+Asts": "PRA"}
_RECENT = 5


def seasons(today: datetime.date | None = None) -> tuple[str, str]:
    import parlay_model as pm
    return pm.nba_season_strings(today)


def season_word(season: str, cur: str) -> str:
    """'This season (2026-27)', 'Last season (2025-26)', or just '2024-25'."""
    if season == cur:
        return f"This season ({season})"
    y = int(cur[:4]) - 1
    if season == f"{y}-{str(y + 1)[-2:]}":
        return f"Last season ({season})"
    return season


def span(seasons_col: pd.Series, cur: str) -> str:
    """Which seasons a run of games came from: 'all 2026-27' or '3 from 2026-27, 7 from 2025-26'."""
    counts = seasons_col.value_counts()
    if len(counts) == 1:
        return f"all {counts.index[0]}"
    order = sorted(counts.index, reverse=True)
    return ", ".join(f"{int(counts[s])} from {s}" for s in order)


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "").replace(",", "").replace("-", " ").strip()
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return " ".join(s.split())


def _day(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.strftime('%b')} {d.day}"


def _odds(v) -> str:
    return f"{int(v):+d}" if v is not None else "—"


def _fmt(v: float) -> str:
    return f"{v:.1f}"


# ── the write-up ────────────────────────────────────────────────────────────

def write_up(name: str, team: str, opp: str, home: bool, cur_df: pd.DataFrame,
             prev_df: pd.DataFrame, props: list, cur: str, prev: str,
             when: str = "tonight") -> list[str]:
    """
    Paragraphs for one player. cur_df / prev_df are his regular-season games, oldest first,
    with PRA added. props: [{stat, line, over, under}] from the book.
    """
    out = []
    where = "hosts" if home else "visits"
    n, m = len(cur_df), len(prev_df)

    # 1. Who he is this season, then last season, each named.
    lead = f"{name} ({team}) {where} {opp} {when}."
    if n:
        lead += (f" This season ({cur}): {_fmt(cur_df['PTS'].mean())} pts, {_fmt(cur_df['REB'].mean())} reb, "
                 f"{_fmt(cur_df['AST'].mean())} ast in {_fmt(cur_df['MIN'].mean())} min over {n} "
                 f"game{'s' if n != 1 else ''}.")
    else:
        lead += f" He has not played a {cur} game yet, so everything below is last season."
    if m:
        prev_team = str(prev_df["TEAM_ABBREVIATION"].iloc[-1])
        moved = f", with {prev_team}" if prev_team != team else ""
        lead += (f" Last season ({prev}{moved}): {_fmt(prev_df['PTS'].mean())} pts, "
                 f"{_fmt(prev_df['REB'].mean())} reb, {_fmt(prev_df['AST'].mean())} ast over {m} games.")
        if n:
            d = cur_df["PTS"].mean() - prev_df["PTS"].mean()
            if abs(d) >= 2:
                lead += (f" That is {abs(d):.1f} points a game {'up on' if d > 0 else 'down from'} last season, "
                         f"on {n} game{'s' if n != 1 else ''}" + (" — too few to call a change." if n < 5 else "."))
    else:
        lead += f" No {prev} NBA games to compare against."
    out.append(lead)

    # 2. Each line, against this season and last season separately.
    for p in props:
        col = STATS[p["stat"]]
        s = (f"{p['stat']} {p['line']:g} (FanDuel over {_odds(p.get('over'))}"
             + (f", under {_odds(p.get('under'))}" if p.get("under") is not None else "") + "): ")
        bits = []
        if n:
            v = cur_df[col]
            last = ", ".join(f"{x:g}" for x in v.tail(_RECENT)[::-1])
            bits.append(f"over in {int((v > p['line']).sum())} of {n} this season "
                        f"(latest first: {last})")
        if m:
            v = prev_df[col]
            bits.append(f"{int((v > p['line']).sum())} of {m} last season "
                        f"({(v > p['line']).mean():.0%}, avg {_fmt(v.mean())})")
        out.append(s + ("; ".join(bits) if bits else "no games to measure it against") + ".")

    # 3. Matchup history against tonight's opponent, dated, by season.
    lead_stat = props[0]["stat"] if props else "Points"
    col, short = STATS[lead_stat], _SHORT[lead_stat]
    parts = []
    for label, df in ((f"this season ({cur})", cur_df), (f"last season ({prev})", prev_df)):
        vs = df[df["OPPONENT"] == opp]
        if vs.empty:
            parts.append(f"{label}: none")
            continue
        games = ", ".join(f"{_day(r['GAME_DATE'])}: {r[col]:g}" for _, r in vs.iterrows())
        parts.append(f"{label}: {len(vs)} game{'s' if len(vs) != 1 else ''}, "
                     f"{_fmt(vs[col].mean())} {short} ({games})")
    out.append(f"Against {opp} — " + "; ".join(parts) + ".")
    return out


# ── tonight's slate ─────────────────────────────────────────────────────────

def _prep(log: pd.DataFrame) -> pd.DataFrame:
    if log.empty:
        return log
    d = log.copy()
    d["PRA"] = d["PTS"] + d["REB"] + d["AST"]
    d["MIN"] = pd.to_numeric(d["MIN"], errors="coerce").fillna(0.0)
    opp = d["MATCHUP"].str.extract(r"@ (\w+)|vs\. (\w+)")
    d["OPPONENT"] = opp[0].fillna(opp[1])
    d["key"] = d["PLAYER_NAME"].map(_norm)
    return d.sort_values(["GAME_DATE", "GAME_ID"])


def _props(raw: pd.DataFrame) -> dict:
    """(player, game) -> [{stat, line, over, under}], one main line per stat (closest to even)."""
    out: dict = {}
    for (player, game, stat), g in raw.groupby(["player_name", "game_label", "stat_type"]):
        overs = g[g["side"].str.lower() == "over"]
        if overs.empty:
            continue
        best = overs.iloc[(overs["implied_prob"].astype(float) - 0.5).abs().argsort().iloc[0]]
        under = g[(g["side"].str.lower() == "under") & (g["line_score"] == best["line_score"])]
        out.setdefault((player, game), []).append({
            "stat": stat, "line": float(best["line_score"]), "over": int(best["american_odds"]),
            "under": int(under["american_odds"].iloc[0]) if not under.empty else None,
            "start_time": str(best["start_time"])})
    order = list(STATS)
    for v in out.values():
        v.sort(key=lambda p: order.index(p["stat"]))
    return out


def build(day: datetime.date | None = None) -> dict:
    """Rebuild `day`'s write-ups from the book's current lines and the saved game logs."""
    import daily_parlay_gen as dg
    import nba_snapshot as ns
    import nba_weekly_picks as nwp
    import parlay_model as pm

    day = day or datetime.date.today()
    cur, prev = seasons(day)
    old = load()
    keep = {}
    if old.get("date") == day.isoformat():
        # Books pull a game's props at tip-off; keep what was written before it started.
        keep = {p["player"] + "|" + p["game"]: p for p in old.get("players", [])
                if dg._has_started(p.get("start_time"))}

    raw = pm.fetch_fanduel("nba")
    if raw is None or raw.empty:
        raw = pd.DataFrame(columns=["player_name", "stat_type", "game_label", "start_time"])
    raw = raw[raw["stat_type"].isin(STATS)]
    raw = raw[raw["start_time"].map(nwp._local_date) == day]
    raw = raw[~raw["start_time"].map(dg._has_started)]

    cur_log = _prep(ns.league_log(cur))
    prev_log = _prep(ns.league_log(prev))
    players = dict(keep)
    for (player, game), props in _props(raw).items():
        k = _norm(player)
        mine_c = cur_log[cur_log["key"] == k] if not cur_log.empty else cur_log
        mine_p = prev_log[prev_log["key"] == k] if not prev_log.empty else prev_log
        away, home = [t.strip() for t in game.split("@")]
        if not mine_c.empty:
            team = str(mine_c["TEAM_ABBREVIATION"].iloc[-1])
        elif not mine_p.empty:
            team = str(mine_p["TEAM_ABBREVIATION"].iloc[-1])
        else:
            continue                    # no NBA games anywhere: nothing to write
        if team not in (away, home):
            # Moved since his last logged game: the book knows his side, the log does not,
            # and a write-up with the wrong opponent is worse than none.
            continue
        opp = home if team == away else away
        paras = write_up(player, team, opp, team == home, mine_c, mine_p, props, cur, prev)
        players[player + "|" + game] = {
            "player": player, "team": team, "opp": opp, "game": game,
            "start_time": props[0]["start_time"],
            "games_cur": len(mine_c), "games_prev": len(mine_p),
            "props": [{k2: p[k2] for k2 in ("stat", "line", "over", "under")} for p in props],
            "text": paras}

    rows = sorted(players.values(), key=lambda p: (p["start_time"], p["game"], p["team"],
                  -next((x["line"] for x in p["props"] if x["stat"] == "Points"), 0)))
    data = {"date": day.isoformat(), "season": cur, "prev": prev,
            "updated": datetime.datetime.now().isoformat(timespec="minutes"), "players": rows}
    if rows or not old:
        OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def load() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── the Hub section ─────────────────────────────────────────────────────────

def render_hub_section() -> None:
    import streamlit as st
    import nfl_hub as nh
    data = load()
    today = datetime.date.today().isoformat()
    if not data.get("players"):
        st.caption("Write-ups appear here on game days, built from FanDuel's lines once they post.")
        return
    stale = data.get("date") != today
    st.caption(f"{'Last slate: ' + data['date'] + '. ' if stale else ''}"
               f"Updated {data.get('updated', '').replace('T', ' ')} · regular season only · "
               f"\"this season\" is {data['season']}, \"last season\" is {data['prev']}. "
               "Facts and the book's price, not picks.")
    games = list(dict.fromkeys(p["game"] for p in data["players"]))
    c1, c2 = st.columns([1, 1.4])
    with c1:
        game = st.selectbox("Game", games, key="nba_scout_game", label_visibility="collapsed")
    rows = [p for p in data["players"] if p["game"] == game]
    with c2:
        q = st.text_input("Find a player", key="nba_scout_q", label_visibility="collapsed",
                          placeholder="Find a player")
    if q:
        rows = [p for p in data["players"] if q.lower() in p["player"].lower()]
    for p in rows:
        new = " · first season" if not p["games_prev"] else ""
        with st.expander(f"{p['player']} · {p['team']} vs {p['opp']} · "
                         f"{p['games_cur']} games {data['season']}{new}"):
            st.markdown("".join(f"<p style='font-size:0.86rem;color:#c8cad4;line-height:1.75;"
                                f"margin:0 0 0.55rem;'>{nh._esc(t)}</p>" for t in p["text"]),
                        unsafe_allow_html=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    d = build()
    print(f"{d['date']}: {len(d['players'])} write-ups ({d['season']} vs {d['prev']})")
    if d["players"]:
        print("\n".join(d["players"][0]["text"]))
