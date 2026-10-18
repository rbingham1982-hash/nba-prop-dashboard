"""
parlay_builder.py — build your own parlay from live FanDuel props, see the odds of it
hitting, and keep a graded record of the tickets you built.

The headline probability is the MARKET's: each leg's FanDuel price with the margin taken
out (de-vigged), multiplied across legs. The model's estimate sits beside it and is never
the headline, because the model has not beaten FanDuel's closing prices (negative CLV on
every sport measured) — showing its number as "the chance this hits" would advertise an
edge the record does not support.

Two honest caveats the page states rather than hides:

  * Multiplying legs assumes they are independent. Legs from one game are not quite, and
    FanDuel will only combine them as a same-game parlay, which it prices itself — so a
    same-game ticket's payout here is NOT what the book will offer.
  * A fair-odds probability always sits below the break-even of the posted price; the gap
    is the book's margin, compounded once per leg. That is the cost of a parlay, not a bug.

Saved tickets are a separate record from the model's tracked parlays (parlay_tracker), so
hand-built tickets never feed the calibration that prices the published boards.

Storage follows accounts.py: Supabase table `konjure_tickets` when the secrets are set
(create it with TICKETS_SQL below), SQLite at ~/.konjure/tickets.db on the home machine.
Grading covers NFL and NBA props, the two sports with a box-score source here
(form_signal's histories); other sports save but stay pending.
"""
from __future__ import annotations

import datetime
import json
import math
import pathlib
import sqlite3
import uuid

import accounts as _acct

_TABLE = "konjure_tickets"
_LOCAL_DB = pathlib.Path.home() / ".konjure" / "tickets.db"
SPORTS = ("nfl", "nba", "mlb", "wnba")
GRADED_SPORTS = ("nfl", "nba")

# Run once in the Supabase SQL editor. RLS on with no policies, like konjure_users: only
# the service key (server side) reads or writes it.
TICKETS_SQL = f"""
create table if not exists {_TABLE} (
  id text primary key,
  email text not null,
  created_at timestamptz not null default now(),
  legs jsonb not null,
  decimal double precision not null,
  american integer not null,
  market_prob double precision not null,
  model_prob double precision,
  result text,
  settled_at timestamptz
);
create index if not exists {_TABLE}_email on {_TABLE} (email);
alter table {_TABLE} enable row level security;
"""


# ── props ─────────────────────────────────────────────────────────────────────────────────

def _decimal(american: int) -> float:
    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def _american(dec: float) -> int:
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def load_props(sport: str) -> list:
    """
    Every FanDuel prop on the board for `sport`, both sides, at the market price.

    Built from the raw board rather than the scored legs so a prop the model skips (too few
    games, a rookie) can still go on a ticket. Not scored here: scoring a full NBA board
    pulls a game log per player and takes minutes, so the model prices only the legs that
    actually go on a ticket (model_probs).
    """
    import daily_parlay_gen as dg
    sport = sport.lower()
    raw = dg.fetch_fanduel(sport)
    if raw is None or raw.empty:
        return []
    out = []
    for _, r in raw.iterrows():
        if dg._has_started(r.get("start_time")):
            continue
        out.append({"sport": sport, "player_name": str(r["player_name"]),
                    "stat_type": str(r["stat_type"]), "line_score": float(r["line_score"]),
                    "side": str(r.get("side", "over")).lower(),
                    "american_odds": int(r["american_odds"]),
                    "fair_prob": round(float(r["implied_prob"]), 4),
                    "game_id": str(r.get("game_id", "")), "game_label": str(r.get("game_label", "")),
                    "team": str(r.get("team", "")), "start_time": str(r.get("start_time", "")),
                    "model_prob": None})
    return out


def model_probs(legs: list) -> dict:
    """{leg key: model probability} for these legs, from the same scorer the boards use."""
    import pandas as pd
    import daily_parlay_gen as dg
    out = {}
    for sport in {l["sport"] for l in legs}:
        rows = [{**l, "implied_prob": l["fair_prob"], "odds_type": "standard",
                 "sportsbook": "FanDuel"} for l in legs if l["sport"] == sport]
        stat_types = {"nfl": dg.NFL_STAT_TYPES, "nba": dg.NBA_STAT_TYPES,
                      "mlb": dg.MLB_STAT_TYPES, "wnba": dg.WNBA_STAT_TYPES}[sport]
        try:
            scored = dg.score_legs(pd.DataFrame(rows), dg.load_cal(sport.upper()), stat_types,
                                   getattr(dg, f"{sport}_hit_rate"),
                                   min_sample=dg.MIN_SAMPLE.get(sport.upper(), 3))
        except Exception:
            continue                 # the market price alone is still a complete ticket
        for lg in scored:
            out[_key({**lg, "sport": sport})] = round(float(lg["hit_rate"]), 4)
    return out


def _key(leg: dict) -> tuple:
    return (leg["player_name"], leg["stat_type"], float(leg["line_score"]),
            str(leg.get("side", "over")).lower(), str(leg.get("game_id", "")))


def label(leg: dict) -> str:
    side = "Over" if leg["side"] == "over" else "Under"
    odds = f"{leg['american_odds']:+d}"
    return f"{leg['player_name']} — {leg['stat_type']} {side} {leg['line_score']:g} ({odds}) · {leg['game_label']}"


# ── pricing ───────────────────────────────────────────────────────────────────────────────

def price(legs: list) -> dict:
    """The ticket's payout and both probabilities. Model probability only when every leg has one."""
    if not legs:
        return {}
    dec = math.prod(_decimal(int(l["american_odds"])) for l in legs)
    market = math.prod(float(l["fair_prob"]) for l in legs)
    have_model = all(l.get("model_prob") is not None for l in legs)
    model = math.prod(float(l["model_prob"]) for l in legs) if have_model else None
    games: dict = {}
    for l in legs:
        games.setdefault((l["sport"], l["game_id"] or l["game_label"]), []).append(l["player_name"])
    return {"decimal": round(dec, 3), "american": _american(dec),
            "market_prob": round(market, 5), "model_prob": round(model, 5) if model is not None else None,
            "breakeven": round(1 / dec, 5),
            "market_ev": round(market * dec - 1, 4),
            "model_ev": round(model * dec - 1, 4) if model is not None else None,
            "same_game": [names for names in games.values() if len(names) > 1],
            "conflicts": _conflicts(legs)}


def _conflicts(legs: list) -> list:
    """Both sides of one prop on one ticket: it cannot win (or is a guaranteed void)."""
    seen, out = {}, []
    for l in legs:
        k = (l["player_name"], l["stat_type"], l["line_score"], l["game_id"])
        if k in seen and seen[k] != l["side"]:
            out.append(f"{l['player_name']} {l['stat_type']} {l['line_score']:g}")
        seen[k] = l["side"]
    return out


# ── storage ───────────────────────────────────────────────────────────────────────────────

def backend() -> str:
    """'supabase', 'local' (SQLite, home machine) or 'closed' (cloud without secrets)."""
    return _acct.backend()


def _sb(method: str, params: dict | None = None, json_body=None, prefer: str | None = None):
    import requests
    url, key = _acct._supabase()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    if prefer:
        headers["Prefer"] = prefer
    r = requests.request(method, f"{url}/rest/v1/{_TABLE}", params=params, json=json_body,
                         headers=headers, timeout=15)
    r.raise_for_status()
    return r


def _local():
    _LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_LOCAL_DB)
    con.execute(f"create table if not exists {_TABLE} (id text primary key, email text not null, "
                "created_at text not null, legs text not null, decimal real not null, "
                "american integer not null, market_prob real not null, model_prob real, "
                "result text, settled_at text)")
    return con


def save_ticket(email: str, legs: list) -> str:
    """Store a ticket for `email` at the prices shown. Returns its id."""
    p = price(legs)
    row = {"id": uuid.uuid4().hex[:16], "email": _acct.normalize(email),
           "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "legs": [{**l, "outcome": None, "actual": None} for l in legs],
           "decimal": p["decimal"], "american": p["american"],
           "market_prob": p["market_prob"], "model_prob": p["model_prob"],
           "result": None, "settled_at": None}
    if backend() == "supabase":
        _sb("POST", json_body=row, prefer="return=minimal")
    else:
        with _local() as con:
            con.execute(f"insert into {_TABLE} values (?,?,?,?,?,?,?,?,?,?)",
                        (row["id"], row["email"], row["created_at"], json.dumps(row["legs"]),
                         row["decimal"], row["american"], row["market_prob"], row["model_prob"],
                         None, None))
    return row["id"]


def tickets(email: str) -> list:
    """This account's tickets, newest first."""
    email = _acct.normalize(email)
    if backend() == "supabase":
        rows = _sb("GET", params={"email": f"eq.{email}", "select": "*",
                                  "order": "created_at.desc"}).json()
    else:
        with _local() as con:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute(
                f"select * from {_TABLE} where email = ? order by created_at desc", (email,))]
        for r in rows:
            r["legs"] = json.loads(r["legs"])
    return rows


def delete_ticket(email: str, ticket_id: str) -> None:
    email = _acct.normalize(email)
    if backend() == "supabase":
        _sb("DELETE", params={"id": f"eq.{ticket_id}", "email": f"eq.{email}"})
    else:
        with _local() as con:
            con.execute(f"delete from {_TABLE} where id = ? and email = ?", (ticket_id, email))


def _update(ticket: dict) -> None:
    if backend() == "supabase":
        _sb("PATCH", params={"id": f"eq.{ticket['id']}"},
            json_body={"legs": ticket["legs"], "result": ticket["result"],
                       "settled_at": ticket["settled_at"]}, prefer="return=minimal")
    else:
        with _local() as con:
            con.execute(f"update {_TABLE} set legs = ?, result = ?, settled_at = ? where id = ?",
                        (json.dumps(ticket["legs"]), ticket["result"], ticket["settled_at"],
                         ticket["id"]))


# ── grading ───────────────────────────────────────────────────────────────────────────────

class _Box:
    """Box-score lookups, loaded once per settle() and only for the sports it needs."""

    def __init__(self):
        self._nfl = self._nba = None

    def actual(self, leg: dict):
        import form_signal as fs
        day = fs._game_day(leg["start_time"])
        if day is None:
            return None
        if leg["sport"] == "nfl":
            if self._nfl is None:
                self._nfl = fs._nfl_actuals()
            actuals, week_of_day = self._nfl
            wk = week_of_day.get(day.isoformat())
            vals = actuals.get((fs._norm(leg["player_name"]), wk)) if wk else None
            return vals.get(leg["stat_type"]) if vals else None
        if leg["sport"] == "nba":
            if self._nba is None:
                self._nba = fs._nba_history()
            games = [v for d, v in self._nba.get(fs._norm(leg["player_name"]), []) if d == day]
            return games[0].get(leg["stat_type"]) if games else None
        return None


def _ticket_result(legs: list):
    live = [l for l in legs if l.get("outcome") != "void"]
    outs = [l.get("outcome") for l in live]
    if any(o == "loss" for o in outs):
        return "loss"
    if not live:
        return "void"
    if all(o == "win" for o in outs):
        return "win"
    return None


def settle(email: str) -> int:
    """Grade this account's pending legs whose games are over. Returns tickets changed."""
    import form_signal as fs
    today = datetime.date.today()
    box, changed = _Box(), 0
    for t in tickets(email):
        if t.get("result"):
            continue
        dirty = False
        for l in t["legs"]:
            if l.get("outcome") is not None or l["sport"] not in GRADED_SPORTS:
                continue
            day = fs._game_day(l["start_time"])
            if day is None or day >= today:
                continue
            try:
                actual = box.actual(l)
            except Exception:
                return changed               # source down: try again on the next visit
            if actual is None:
                # No box-score row: did not play, or the stats are not in yet. FanDuel voids
                # a player prop when the player does not play; wait three days first.
                if (today - day).days >= 3:
                    l["outcome"], dirty = "void", True
                continue
            l["actual"] = actual
            if actual == l["line_score"]:
                l["outcome"] = "void"
            elif l["side"] == "under":
                l["outcome"] = "win" if actual < l["line_score"] else "loss"
            else:
                l["outcome"] = "win" if actual > l["line_score"] else "loss"
            dirty = True
        res = _ticket_result(t["legs"])
        if dirty or res:
            t["result"] = res
            t["settled_at"] = (datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
                               if res else None)
            _update(t)
            changed += 1
    return changed


def settled_payout(ticket: dict) -> float:
    """Decimal payout as settled: void legs drop out, the way FanDuel settles them."""
    live = [l for l in ticket["legs"] if l.get("outcome") != "void"]
    return math.prod(_decimal(int(l["american_odds"])) for l in live) if live else 1.0


def record(rows: list) -> dict:
    """Settled tickets in money, one unit each, and what the market expected of them."""
    done = [t for t in rows if t.get("result") in ("win", "loss")]
    out = {"tickets": len(rows), "settled": len(done),
           "pending": sum(1 for t in rows if not t.get("result"))}
    if not done:
        return out
    rets = [(settled_payout(t) - 1) if t["result"] == "win" else -1.0 for t in done]
    out.update({"wins": sum(1 for t in done if t["result"] == "win"),
                "units": round(sum(rets), 2), "roi_pct": round(100 * sum(rets) / len(rets), 1),
                "expected_wins": round(sum(float(t["market_prob"]) for t in done), 2)})
    return out


# ── the page ──────────────────────────────────────────────────────────────────────────────

MAX_LEGS = 10


def _pct(p) -> str:
    return "—" if p is None else f"{100 * float(p):.1f}%"


def _owner_email():
    """Who saves tickets: the signed-in account, or 'owner' on the home machine."""
    return _acct.signed_in_email() or ("owner" if backend() == "local" else None)


def render(sports: tuple = SPORTS, key: str = "pb") -> None:
    """
    The builder. `sports` limits which boards it offers: the standalone page passes every
    sport, each Edge Finder passes its own. `key` keeps the widgets of two copies apart.
    """
    import pandas as pd
    import streamlit as st

    @st.cache_data(ttl=300, show_spinner=False)
    def _props(sport: str) -> list:
        # Five minutes, like the Edge Finder boards: a ticket priced at a stale line is a
        # ticket the book will not honour at that payout.
        try:
            return load_props(sport)
        except Exception:
            return []

    @st.cache_data(ttl=300, show_spinner=False)
    def _model(_sig: tuple, _legs: list) -> dict:
        # Keyed on the ticket's labels (underscore args are not hashed); a label carries the
        # line and the odds, so a moved line is a new key.
        try:
            return model_probs(_legs)
        except Exception:
            return {}

    if len(sports) > 1:
        chosen = st.multiselect("Sports", [s.upper() for s in sports],
                                default=[s.upper() for s in sports], key=f"{key}_sports")
        chosen = [s.lower() for s in chosen]
    else:
        chosen = list(sports)
    pool = []
    with st.spinner("Loading FanDuel props…"):
        for s in chosen:
            pool += _props(s)
    if not pool:
        st.info("No FanDuel props are posted for these sports right now. Lines usually go up "
                "the day before games.")
        _render_my_tickets(key)
        return
    by_label = {label(l): l for l in pool}

    pick_key = f"{key}_pick"
    # Legs whose prop came off the board (game started, line moved) drop out here.
    picked = [lb for lb in st.session_state.get(pick_key, []) if lb in by_label]
    st.session_state[pick_key] = picked

    f1, f2, f3 = st.columns(3)
    games = sorted({l["game_label"] for l in pool if l["game_label"]})
    game = f1.selectbox("Game", ["All games"] + games, key=f"{key}_game")
    stat_f = f2.multiselect("Stat", sorted({l["stat_type"] for l in pool}), key=f"{key}_stat")
    who = f3.text_input("Player", placeholder="Search a name", key=f"{key}_who").strip().lower()

    shown = [lb for lb, l in by_label.items()
             if (game == "All games" or l["game_label"] == game)
             and (not stat_f or l["stat_type"] in stat_f)
             and (not who or who in l["player_name"].lower())]
    # Already-picked legs stay among the options whatever the filters show, or changing a
    # filter would drop them from the ticket.
    options = picked + [lb for lb in shown if lb not in picked]
    sel = st.multiselect(f"Legs (up to {MAX_LEGS})", options, max_selections=MAX_LEGS,
                         key=pick_key, placeholder="Pick props to add to your ticket")
    legs = [by_label[lb] for lb in sel]
    if legs:
        with st.spinner("Pricing with our model…"):
            mp = _model(tuple(sorted(sel)), legs)
        legs = [{**l, "model_prob": mp.get(_key(l))} for l in legs]

    if not legs:
        st.caption("Add legs above to price a ticket. Each leg carries FanDuel's odds; the ticket "
                   "shows what those odds say about its chance of hitting.")
    else:
        p = price(legs)
        st.dataframe(pd.DataFrame([{
            "Leg": f"{l['player_name']} — {l['stat_type']} {'O' if l['side'] == 'over' else 'U'} {l['line_score']:g}",
            "Game": l["game_label"], "Odds": f"{l['american_odds']:+d}",
            "Market %": _pct(l["fair_prob"]), "Model %": _pct(l.get("model_prob"))} for l in legs]),
            hide_index=True, width="stretch")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Chance it hits", _pct(p["market_prob"]),
                  help="FanDuel's own odds for each leg with the book's margin removed, multiplied "
                       "across the legs. This is the market's estimate.")
        m2.metric("Pays", f"{p['american']:+d}", f"{p['decimal']:.2f}x your stake", delta_color="off")
        m3.metric("Needs to hit", _pct(p["breakeven"]),
                  help="How often a ticket at this payout has to win to break even.")
        m4.metric("Our model", _pct(p["model_prob"]),
                  help="The project's prop model, for comparison. It has not beaten FanDuel's "
                       "closing prices, so read it as a second opinion, not an edge. Shown only "
                       "when the model can price every leg.")
        margin = p["breakeven"] - p["market_prob"]
        st.caption(f"The gap between the market's {_pct(p['market_prob'])} and the "
                   f"{_pct(p['breakeven'])} this payout needs is the book's margin across "
                   f"{len(legs)} leg{'s' if len(legs) > 1 else ''} ({100 * margin:.1f} points). "
                   f"Expected return at market odds: {100 * p['market_ev']:+.1f}% of stake.")
        for names in p["same_game"]:
            st.warning(f"Same game: {', '.join(names)}. FanDuel only combines these as a "
                       "same-game parlay, which it prices itself, so the payout here is an "
                       "estimate and the legs are not fully independent.")
        for c in p["conflicts"]:
            st.error(f"Both sides of {c} are on this ticket. It cannot win.")

        email = _owner_email()
        b1, b2 = st.columns([1, 4])
        if backend() == "closed":
            b2.caption("Saving tickets opens with accounts.")
        elif not email:
            b2.caption("Sign in to save tickets and track how they do.")
        elif b1.button("Save ticket", key=f"{key}_save", type="primary"):
            try:
                save_ticket(email, legs)
                st.session_state.pop(f"{key}_settled", None)
                st.success("Saved. It grades itself after the games (NFL and NBA props).")
            except Exception as e:
                st.error(f"Couldn't save the ticket: {e}")
        if b1.button("Clear", key=f"{key}_clear"):
            st.session_state.pop(pick_key, None)
            st.rerun()

    _render_my_tickets(key)


def _render_my_tickets(key: str) -> None:
    import streamlit as st
    email = _owner_email()
    if not email or backend() == "closed":
        return
    st.markdown("**My tickets**")
    try:
        if not st.session_state.get(f"{key}_settled"):
            settle(email)                     # once per visit: grading reads box scores
            st.session_state[f"{key}_settled"] = True
        rows = tickets(email)
    except Exception as e:
        st.caption(f"Tickets unavailable right now ({e}).")
        return
    if not rows:
        st.caption("No saved tickets yet.")
        return
    rec = record(rows)
    if rec.get("settled"):
        st.caption(f"{rec['wins']} of {rec['settled']} settled tickets hit (the market expected "
                   f"{rec['expected_wins']:.1f}) · {rec['units']:+.2f} units, "
                   f"{rec['roi_pct']:+.1f}% · {rec['pending']} pending")
    else:
        st.caption(f"{rec['pending']} pending")
    mark = {"win": "✅", "loss": "❌", "void": "➖", None: "⏳"}
    for t in rows[:25]:
        head = (f"{mark.get(t.get('result'))} {t['american']:+d} · {len(t['legs'])} legs · "
                f"market {_pct(t['market_prob'])} · {str(t['created_at'])[:10]}")
        with st.expander(head):
            for l in t["legs"]:
                act = "" if l.get("actual") is None else f" — actual {l['actual']:g}"
                st.write(f"{mark.get(l.get('outcome'))} {label(l)}{act}")
            if any(l["sport"] not in GRADED_SPORTS for l in t["legs"]):
                st.caption("Some legs are from a sport that isn't graded yet; they stay pending.")
            if st.button("Delete", key=f"{key}_del_{t['id']}"):
                delete_ticket(email, t["id"])
                st.rerun()
