"""
daily_parlay_gen.py
Runs at 2 pm daily via Windows Task Scheduler.
Fetches PrizePicks + Underdog lines for MLB, WNBA, and NBA (when in season),
scores each leg with the same historical hit-rate logic as the dashboard,
builds safe + value parlays, and logs them to parlay_log.json.

The prediction model itself (hit-rate calculators, BvP, game-log fetchers,
parlay builder) lives in parlay_model.py, shared with nba_prop_dashboard.py,
so the two stop drifting apart.
"""
import sys, time, os, re, requests, pandas as pd
from datetime import datetime
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# parlay_tracker / parlay_model live in the same directory
sys.path.insert(0, os.path.dirname(__file__))
import parlay_tracker
import parlay_model as pm
import game_tracker

LOG_PATH = Path(__file__).parent / "logs" / "daily_parlay_gen.log"


class _Tee:
    """
    Mirror a stream to the run log.

    The log used to be written by the shell redirect in run_daily_parlay_gen.bat,
    so any run started another way left no trace — the log undercounted real
    output and showed phantom gaps. Owning the log here means every invocation is
    recorded however it was launched.
    """

    def __init__(self, stream, sink):
        self._stream = stream
        self._sink = sink

    def write(self, text):
        self._stream.write(text)
        self._sink.write(text)
        self._sink.flush()
        return len(text)

    def flush(self):
        self._stream.flush()
        self._sink.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)

PP_PAYOUTS       = pm.PP_PAYOUTS
PP_ODDS_IMPLIED  = pm.PP_ODDS_IMPLIED
PP_DEAD          = {"final", "postponed", "cancelled", "canceled", "suspended"}
PP_HEADERS       = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://app.prizepicks.com/",
}

MLB_STAT_TYPES  = ["Hits", "Pitcher Strikeouts", "Home Runs", "Runs Scored", "Total Bases"]
# FanDuel prices the combo props too, and the resolver already knows all of them
# (_NBA_RESOLVE), so leaving them out just discarded half the board it hands us.
WNBA_STAT_TYPES = ["Points", "Rebounds", "Assists", "3-PT Made",
                   "Pts+Rebs+Asts", "Pts+Rebs", "Pts+Asts", "Rebs+Asts"]
NBA_STAT_TYPES  = ["Points", "Rebounds", "Assists", "3-PT Made",
                   "Pts+Rebs+Asts", "Pts+Rebs", "Pts+Asts", "Rebs+Asts"]
# Every market _FD_NFL_CORE maps and nfl_analysis._USAGE_MODEL can project. Deliberately
# spread across passing, rushing and receiving: _build_parlays quotas the pool per market
# (_market_diverse_pool), so the breadth of this list is what decides whether a board can
# hold a genuinely mixed parlay or just five receiving-yards legs.
NFL_STAT_TYPES  = ["Passing Yards", "Passing TDs", "Completions",
                   "Rushing Yards", "Rushing TDs", "Carries",
                   "Receptions", "Receiving Yards", "Receiving TDs"]

# Legs of one market allowed in a single parlay, per sport. Only NFL is constrained:
# without it the first NFL slate returned 5-leg parlays that were five Passing TDs, one
# market at 0.20 distinct-markets-per-leg — one bet wearing five names, and the same shape
# as the MLB home-run stacks that went 0-for-48 in 2026-W34. A cap of 2 forces a 5-leg to
# span at least three of passing/rushing/receiving.
#
# MLB and WNBA are left unconstrained on purpose: this changes which parlays a board emits,
# and retuning a live board is a separate decision from standing up a new one. MLB is the
# obvious next candidate.
MAX_SAME_MARKET = {"NFL": 2, "MLB": 2}

# The coarser cut: what KIND of outcome a market resolves on. The market cap alone still
# produced a 5-leg of Passing TDs / Rushing TDs / Receiving TDs — three markets by name,
# one event repeated, and every leg drawn from the most heavily regressed corner of the
# model. Volume props resolve on usage, yardage on usage x efficiency, touchdowns on a
# rare event; capping per family is what makes a parlay span genuinely different outcomes.
NFL_STAT_FAMILY = {
    "Completions": "volume", "Carries": "volume", "Receptions": "volume",
    "Passing Yards": "yards", "Rushing Yards": "yards", "Receiving Yards": "yards",
    "Passing TDs": "td", "Rushing TDs": "td", "Receiving TDs": "td",
}
# MLB, added after the 2026-W34 board went 0-for-48 on its recommended parlays at a median
# 51.5x payout and 2.0% model probability — 47 of 48 paid 20x or more, and they were built
# by stacking the same longshot market. Home Runs is the one MLB market that is a rare
# event, and it is also the worst-calibrated (factor 0.796, hr_power_picks 0.733), so a
# parlay made of them multiplies the model's least reliable numbers together.
#
# BUT THE DATA DOES NOT YET BACK THIS UP, and that is worth knowing before trusting it.
# Backtested over the logged board:
#
#   ROI          W33  -55.0% all vs -41.2% capped   (cap helps)
#                W34  -35.6% all vs -50.9% capped   (cap hurts)
#   calibration  3wk, actual/predicted: 0.90 diverse vs 0.87 concentrated — no real gap,
#                and by pick count it flips (3-leg 1.11 vs 0.78, 4-leg 0.49 vs 0.89)
#
# So the correlation argument — that same-market legs are one bet and the independence
# product overprices them — is sound reasoning that this sample cannot confirm. It also
# costs about half the board (593 of 1231 W34 parlays, and 95 of 165 winners). Kept
# because a 5-leg of one longshot market is a concentration risk regardless of what three
# weeks of variance says, but it is a judgement call, not a measured improvement: delete
# the "MLB" entries here and in MAX_SAME_FAMILY to revert it.
#
# Total Bases sits with Hits rather than with Home Runs even though a homer is four bases:
# the line books post on it (1.5, 2.5) resolves on contact, not on power, and grouping it
# with HR would leave "contact" too thin to fill a leg on most slates.
MLB_STAT_FAMILY = {
    "Home Runs": "power",
    "Hits": "contact", "Total Bases": "contact",
    "Runs Scored": "runs",
    "Pitcher Strikeouts": "pitching",
}
STAT_FAMILY = {"NFL": NFL_STAT_FAMILY, "MLB": MLB_STAT_FAMILY}
MAX_SAME_FAMILY = {"NFL": 2, "MLB": 2}

# Games of history a leg needs before it is scored, per sport. See score_legs.
MIN_SAMPLE = {"NFL": 0}

UD_MLB_STAT_MAP = {
    "Hits": "Hits", "Strikeouts": "Pitcher Strikeouts",
    "Pitcher Strikeouts": "Pitcher Strikeouts", "Runs": "Runs Scored",
    "Home Runs": "Home Runs", "Walks": "Walks",
    "Earned Runs": "Earned Runs Allowed", "Stolen Bases": "Stolen Bases",
    "Total Bases": "Total Bases",
}
UD_NBA_STAT_MAP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "3-Pointers Made": "3-PT Made", "Pts+Rebs+Asts": "Pts+Rebs+Asts",
    "Pts+Rebs": "Pts+Rebs", "Pts+Asts": "Pts+Asts",
    "Blocked Shots": "Blocked Shots", "Steals": "Steals", "Turnovers": "Turnovers",
}

# ── Calibration ────────────────────────────────────────────────────────────

def load_cal(sport):
    try:
        return parlay_tracker.get_calibration(sport=sport)
    except Exception:
        return {}

# ── PrizePicks API ─────────────────────────────────────────────────────────

# run_sport passes the PrizePicks league id, not the sport, and canonical_abbr needs the
# sport to know which alias table applies.
_PP_LEAGUE_SPORT = {2: "mlb", 6: "wnba", 7: "nba", 9: "nfl"}


def fetch_prizepicks(league_id: int) -> pd.DataFrame:
    dk_odds = {"goblin": -162, "standard": -100, "demon": 162}
    for attempt in range(3):
        try:
            if attempt:
                time.sleep(2)
            url = f"https://api.prizepicks.com/projections?league_id={league_id}&per_page=500&single_stat=true"
            resp = requests.get(url, headers=PP_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            payload = resp.json()
            player_map, game_map = {}, {}
            for item in payload.get("included", []):
                if item.get("type") == "new_player":
                    a = item["attributes"]
                    player_map[item["id"]] = {"name": a.get("display_name", ""), "team": a.get("team", a.get("team_name", ""))}
                elif item.get("type") == "game":
                    a = item["attributes"]
                    gteams = a.get("metadata", {}).get("game_info", {}).get("teams", {})
                    # Normalised for the same reason as the Underdog branch: PrizePicks
                    # writes AZ/CWS where FanDuel writes ARI/CHW.
                    sport_key = _PP_LEAGUE_SPORT.get(league_id, "")
                    away = pm.canonical_abbr(sport_key, gteams.get("away", {}).get("abbreviation", ""))
                    home = pm.canonical_abbr(sport_key, gteams.get("home", {}).get("abbreviation", ""))
                    game_map[item["id"]] = {
                        "label": f"{away} @ {home}" if away and home else "",
                        "start_time": a.get("start_time", ""),
                    }
            rows = []
            for proj in payload.get("data", []):
                if proj.get("type") != "projection":
                    continue
                attrs = proj["attributes"]
                if attrs.get("status", "pre_game") in PP_DEAD:
                    continue
                rels = proj.get("relationships", {})
                pid = rels.get("new_player", {}).get("data", {}).get("id", "")
                gid = rels.get("game", {}).get("data", {}).get("id", "")
                ot  = attrs.get("odds_type", "standard")
                rows.append({
                    "player_name":  player_map.get(pid, {}).get("name", ""),
                    "team":         player_map.get(pid, {}).get("team", ""),
                    "stat_type":    attrs.get("stat_type", ""),
                    "line_score":   attrs.get("line_score"),
                    "odds_type":    ot,
                    "american_odds": dk_odds.get(ot, -100),
                    "implied_prob": PP_ODDS_IMPLIED.get(ot, 0.50),
                    "game_id":      gid,
                    "game_label":   game_map.get(gid, {}).get("label", ""),
                    "start_time":   game_map.get(gid, {}).get("start_time", attrs.get("start_time", "")),
                    "sportsbook":   "PrizePicks",
                })
            if rows:
                return pd.DataFrame(rows)
        except Exception as e:
            print(f"    PrizePicks attempt {attempt+1} failed: {e}")
    return pd.DataFrame()

# ── Underdog API ───────────────────────────────────────────────────────────

def fetch_underdog(sport: str) -> pd.DataFrame:
    sport_id = {"nba": "NBA", "wnba": "WNBA", "mlb": "MLB"}[sport]
    stat_map  = UD_MLB_STAT_MAP if sport == "mlb" else UD_NBA_STAT_MAP
    try:
        resp = requests.get(
            "https://api.underdogfantasy.com/beta/v5/over_under_lines",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return pd.DataFrame()
        d = resp.json()
        player_map     = {p["id"]: p for p in d.get("players", [])}
        appearance_map = {a["id"]: a for a in d.get("appearances", [])}
        game_map       = {g["id"]: g for g in d.get("games", [])}
        rows = []
        for line in d.get("over_under_lines", []):
            if line.get("status") != "active":
                continue
            ou       = line.get("over_under", {})
            app_stat = ou.get("appearance_stat", {})
            st_type  = stat_map.get(app_stat.get("display_stat", "").strip())
            if not st_type:
                continue
            app    = appearance_map.get(app_stat.get("appearance_id", ""), {})
            player = player_map.get(app.get("player_id", ""), {})
            if not player or player.get("sport_id") != sport_id:
                continue
            try:
                val = float(line.get("stat_value", 0))
            except Exception:
                continue
            game  = game_map.get(app.get("match_id"), {})
            name  = f"{player.get('first_name','')} {player.get('last_name','')}".strip()
            if not name:
                continue
            # Normalise both halves so the label matches what the other books write —
            # Underdog says AZ/CWS/POR where FanDuel says ARI/CHW/PDX, and marks
            # doubleheader halves inside the abbreviation ("CLE (Game 1)").
            title = game.get("abbreviated_title", "")
            team  = ""
            if " @ " in title:
                away, home = [pm.canonical_abbr(sport, x) for x in title.split(" @ ", 1)]
                title = f"{away} @ {home}"
                team = away if app.get("team_id") == game.get("away_team_id") else home
            opts = line.get("options", [])
            over_opt  = next((o for o in opts if o.get("choice") == "higher"), None)
            under_opt = next((o for o in opts if o.get("choice") == "lower"), None)
            if not over_opt:
                continue
            try:
                american = int(str(over_opt.get("american_price", "-110")).replace("+", ""))
            except Exception:
                american = -110

            # The price is the sharpest signal on the board and this used to throw it
            # away, pinning implied_prob at 0.50 for every Underdog leg. That left the
            # model's "30% market" term a constant, so predictions barely moved with the
            # line: where the book priced a WNBA leg at 14% it still predicted 26% (those
            # legs hit 0%), and where the book said 81% it predicted 76% (they hit 84%).
            # Model-vs-book edge was therefore inverted for WNBA — the legs it liked most
            # hit least. De-vigged book probability restores the signal.
            implied_over = pm.american_to_implied(american)
            implied_under = None
            if under_opt is not None:
                try:
                    under_american = int(str(under_opt.get("american_price", "-110")).replace("+", ""))
                    implied_under = pm.american_to_implied(under_american)
                except Exception:
                    implied_under = None
            implied = round(pm.devig_two_way(implied_over, implied_under), 4)

            rows.append({
                "player_name": name, "team": team, "stat_type": st_type,
                "line_score": val, "odds_type": "standard",
                "american_odds": american, "implied_prob": implied,
                "game_id": app.get("match_id", ""), "game_label": title,
                "start_time": game.get("scheduled_at", ""),
                "sportsbook": "Underdog",
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        print(f"    Underdog fetch failed: {e}")
        return pd.DataFrame()

# FanDuel lives in parlay_model so the dashboard and this script cannot drift
# apart on it — the same mistake that produced two divergent copies of the model.
fetch_fanduel = pm.fetch_fanduel

# ── Hit-rate adapters ───────────────────────────────────────────────────────
# Thin shims translating this script's (implied=, cal=, team=) calling
# convention into parlay_model's canonical (implied_override=, cal_factor=,
# opp_pitcher_id=) signatures, so score_legs/run_sport/main below are
# unchanged from before the model logic moved into the shared module.

def mlb_hit_rate(player_name, stat_type, line, odds_type="standard", implied=-1.0, cal=1.0, team=""):
    return pm._mlb_hit_rate(player_name, stat_type, line, odds_type=odds_type,
                             implied_override=implied, cal_factor=cal, team=team)

def wnba_hit_rate(player_name, stat_type, line, odds_type="standard", implied=-1.0, cal=1.0, team=""):
    return pm._wnba_hit_rate(player_name, stat_type, line, odds_type=odds_type,
                              implied_override=implied, cal_factor=cal)

def nba_hit_rate(player_name, stat_type, line, odds_type="standard", implied=-1.0, cal=1.0, team=""):
    return pm._nba_hit_rate(player_name, stat_type, line, odds_type=odds_type,
                             implied_override=implied, cal_factor=cal)

# NFL scoring is expensive to set up and cheap to reuse: the weekly frame is ~18.5k rows
# and team_volume/position_priors are full-table groupbys. score_legs calls the rate fn
# once per prop, so building them per call would redo that work a thousand times a run.
# Cached per (process, season) and rebuilt when the season rolls over.
_NFL_CTX: dict = {}

def _nfl_context():
    import nfl_analysis as nfl
    # Resolve the season ONCE per process. latest_season_with_data() probes the nflverse
    # release by actually reading the parquet, so calling it per prop was a remote round
    # trip per prop — 419ms each, which is where a 1,000-prop board's seven minutes went.
    # The season cannot change mid-run, and the daily job is a fresh process every time.
    if not _NFL_CTX:
        season = nfl.latest_season_with_data()
        _, df = nfl.get_season(season)
        _NFL_CTX.update({
            "season": season, "df": df,
            "priors": nfl.position_priors(df),
            "vol": nfl.team_volume(df),
            "idx": nfl.player_index(df),
            "dcache": {},
            # Fantasy-board projections cover the players the usage model cannot see at
            # all — rookies, who have no game log and are exactly who books post Week 1
            # props on. Built once; the board itself is a couple of parquet reads.
            "board": nfl.board_projections(season + 1),
            "rates": nfl.league_rates(df),
            "cvcache": {},
            # Rosters for the season being PLAYED, which is the season after the stats
            # season while the new one has no games yet. That mapping is the entire point
            # of the usage model — it is what rebases a mover onto his new offence.
            "teams": nfl.current_teams(season + 1),
        })
    return _NFL_CTX

def nfl_hit_rate(player_name, stat_type, line, odds_type="standard", implied=-1.0, cal=1.0, team=""):
    """
    Model probability for an NFL prop, RAW — the market blend is applied downstream by
    _apply_market_blend, same as the other sports, so returning a blended number here
    would shrink toward the market twice and make calibration measure a model that was
    never shipped.

    `team` is the leg's game_label, not a club, so the opponent is left unset: a defence
    adjustment needs to know which side the player is on, and guessing it from a label
    would silently apply the wrong defence half the time. Opponent-aware scoring is wired
    in the dashboard builder, where the matchup is known.
    """
    import nfl_analysis as nfl
    ctx = _nfl_context()
    try:
        s = nfl.score_prop_nfl(ctx["df"], player_name, stat_type, line,
                               teams=ctx["teams"], priors=ctx["priors"], vol=ctx["vol"],
                               idx=ctx["idx"], dcache=ctx["dcache"], board=ctx["board"],
                               rates=ctx["rates"], cvcache=ctx["cvcache"])
    except Exception:
        return 0.0, 0
    if not s:
        return 0.0, 0
    rate = max(0.01, min(0.99, float(s["model_over"]) * float(cal)))
    return rate, int(s.get("n", 0))

def build_parlays(legs, min_legs=2, max_legs=5, top_n=50, pool_size=30, max_leg_uses=6,
                  sportsbook="PrizePicks", parlay_cal=None,
                  market_blend=None, same_game_penalty=1.0, max_same_market=None,
                  stat_family=None, max_same_family=None):
    # max_legs was 4, so the daily job could never emit a 5-leg parlay — the only size
    # that has actually been profitable (+100% ROI on MLB, vs -58% for the 4-leg it did
    # emit). The EV filter decides which sizes survive; the cap no longer prejudges it.
    return pm._build_parlays(legs, min_legs=min_legs, max_legs=max_legs,
                              top_n=top_n, pool_size=pool_size, max_leg_uses=max_leg_uses,
                              sportsbook=sportsbook, parlay_cal=parlay_cal,
                              market_blend=market_blend,
                              same_game_penalty=same_game_penalty,
                              max_same_market=max_same_market,
                              stat_family=stat_family,
                              max_same_family=max_same_family)

# ── Leg scorer ─────────────────────────────────────────────────────────────

def _has_started(start_time) -> bool:
    """
    True if first pitch/tip is already in the past.

    Unparseable or missing start times return False — books drop props once a game is
    live, so an absent timestamp is far more likely to be a feed quirk than a started
    game, and refusing to score those would silently empty the board.
    """
    s = str(start_time or "").strip()
    if not s:
        return False
    try:
        from datetime import timezone
        st = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= st
    except Exception:
        return False


def score_legs(df, cal, stat_types, rate_fn, min_sample=3):
    df   = df[df["stat_type"].isin(stat_types)].copy()
    legs, seen = [], set()
    started = 0
    for _, row in df.iterrows():
        # Side is part of the identity now: over and under of one line are two different
        # bets, and keying without it would let the first side seen suppress the other.
        # Normalised defensively: a DataFrame row missing the column yields NaN, not
        # None, so a bare str(...) default would produce the literal "nan".
        side = "under" if str(row.get("side", "over")).lower() == "under" else "over"
        key = (row["player_name"], row["stat_type"], side)
        if key in seen:
            continue
        seen.add(key)
        # A prop on a game already under way is not a prediction. The 2026-08-15 late
        # run fired at 17:12 against a 17:10 first pitch and logged 9 parlays / 38 legs
        # at negative lead; those resolve and enter calibration as if they had been
        # forecast, when the price by then reflects what has already happened on the
        # field. Cheap to drop, and impossible to distinguish after the fact.
        if _has_started(row.get("start_time", "")):
            started += 1
            continue
        try:
            line = float(row["line_score"])
        except Exception:
            continue
        # Every scorer reasons in OVER-space: it estimates P(stat > line) and blends that
        # against the market's over price. So an under row is flipped into over-space on the
        # way in and back out again on the way out. devig_two_way normalises both sides by
        # the same total, so 1 - implied_under is exactly implied_over — no information is
        # lost in the round trip, and no scorer needs to learn about sides.
        implied_in = float(row.get("implied_prob", -1.0))
        if side == "under" and implied_in >= 0:
            implied_in = 1.0 - implied_in
        rate, n = rate_fn(
            row["player_name"], row["stat_type"], line,
            odds_type=str(row.get("odds_type", "standard")),
            implied=implied_in,
            cal=cal.get(row["stat_type"], 1.0),
            team=str(row.get("team", "")),
        )
        if side == "under":
            rate = 1.0 - rate
        # min_sample is the games-of-history floor. NFL passes 0 because a leg can be
        # priced off the fantasy board's draft-cohort projection, which is a real
        # projection with n=0 games behind it — the scorer already refuses anything it
        # cannot price, so the floor would only be discarding rookies. Other sports keep
        # 3, where n really is a game-log count and a 1-game sample is noise.
        if n < min_sample:
            continue
        legs.append({
            "player_name":   row["player_name"],
            "team":          str(row.get("team", "")),
            "stat_type":     row["stat_type"],
            "line_score":    line,
            "odds_type":     str(row.get("odds_type", "standard")),
            "american_odds": int(row.get("american_odds", -110)),
            "implied_prob":  float(row.get("implied_prob", 0.50)),
            "sportsbook":    str(row.get("sportsbook", "")),
            "game_id":       str(row.get("game_id", "")),
            "game_label":    str(row.get("game_label", "")),
            "start_time":    str(row.get("start_time", "")),
            "side":          side,
            "hit_rate":      rate,
            "sample_n":      n,
        })
        time.sleep(0.03)
    if started:
        print(f"    {started} prop(s) skipped — game already under way.")
    return legs

# ── Per-sport runner ───────────────────────────────────────────────────────

def run_sport(sport_key, sport_label, pp_league_id, stat_types, rate_fn):
    print(f"\n{'='*62}\n  {sport_label}\n{'='*62}")
    cal = load_cal(sport_label)
    if cal:
        print(f"  Calibration: { {k: round(v,3) for k,v in cal.items()} }")

    try:
        for w in parlay_tracker.get_drift_warnings(sport=sport_label):
            print(f"  ! DRIFT {w['stat_type']}: predicted {w['predicted_hit_rate']:.1%} "
                  f"vs actual {w['actual_hit_rate']:.1%} ({w['bias']:+.1%}) "
                  f"over {w['samples']} props")
    except Exception:
        pass

    p_cal = {}
    try:
        p_cal = parlay_tracker.get_parlay_calibration(sport=sport_label)
        if p_cal:
            print(f"  Parlay calibration by pick count: "
                  f"{ {k: round(v, 3) for k, v in sorted(p_cal.items())} }")
    except Exception:
        pass

    # Market blend, fit from the log. (Same-game correlation is still measured by
    # parlay_tracker.get_same_game_penalty but no longer applied to pricing — the
    # estimator was unstable and deflate-only; see that function's docstring.)
    mkt_w = None
    try:
        mkt_w = parlay_tracker.get_market_blend(sport=sport_label)
        print(f"  Market blend weight (model share): {mkt_w}")
    except Exception:
        pass

    total = 0
    # FanDuel leads: it is the only book here that quotes both sides of a prop, so it is
    # the only one the de-vig can fully use. Underdog and PrizePicks still run — a book
    # returning nothing (off-season, no slate) must not take the others down with it.
    for sb, fetch_fn in [("FanDuel",    lambda: fetch_fanduel(sport_key)),
                          ("Underdog",   lambda: fetch_underdog(sport_key)),
                          ("PrizePicks", lambda: fetch_prizepicks(pp_league_id))]:
        print(f"\n  [{sb}]")
        raw = fetch_fn()
        if raw.empty:
            print(f"    No lines — skipping.")
            continue
        print(f"    {len(raw)} lines fetched.")

        if sb == "FanDuel":
            # CLV: stamp pending logged legs with the latest price. Games drop off
            # the feed once they start, so the last stamp ≈ the closing line.
            try:
                n_clv = parlay_tracker.update_market_snapshots(raw, sport_label)
                if n_clv:
                    print(f"    CLV snapshots updated on {n_clv} pending legs.")
            except Exception:
                pass

        legs = score_legs(raw, cal, stat_types, rate_fn,
                          min_sample=MIN_SAMPLE.get(sport_label, 3))
        print(f"    {len(legs)} legs scored.")

        # Price the legs BEFORE anything logs them. _build_parlays used to apply the
        # market blend to its own copy, so safe/value legs were logged at the shipped
        # (blended) probability while the tracking legs written just below kept the raw
        # scorer output — one field, predicted_hit_rate, meaning two different things
        # depending on which kind of row you read.
        #
        # That fed straight into the pocket alert, whose edge is predicted minus implied:
        # on unblended rows it measured the raw model's disagreement with the market with
        # none of the shrinkage that exists precisely because that disagreement measured
        # anti-predictive. Today it produced a +26.6% "edge" on a -112 pitcher strikeout
        # line, against a best of +7.0% three days earlier. It also left calibration
        # grading a mixture: 482 props appear both ways, median gap 0.051, and the
        # deduper keeps whichever it happens to see first.
        #
        # Blending here means every logged leg carries model_hit_rate (raw) and
        # predicted_hit_rate (shipped) alike. Re-blending inside _build_parlays is a
        # documented no-op, so passing the same list on is safe.
        if mkt_w is not None:
            legs = pm._apply_market_blend(legs, mkt_w)

        # Track EVERY scored prop, not just the ones the builder bets. The builder optimizes
        # for EV and overwhelmingly picks the high-probability market (MLB Hits), so Home Runs,
        # Total Bases, Runs Scored and Pitcher Strikeouts were scored and discarded — never
        # resolved, never calibrated. These single-leg tracking records (recommended=False)
        # fix that: a market can only calibrate on data it accumulates.
        try:
            t = parlay_tracker.log_tracking_legs(legs, sport_label, sb)
            if t:
                import collections as _c
                _mk = _c.Counter(l["stat_type"] for l in legs)
                print(f"    Tracked {t} props for calibration ({dict(_mk)}).")
        except Exception as _e:
            print(f"    (tracking-log skipped: {_e})")

        if len(legs) < 2:
            continue

        safe, value = build_parlays(legs, sportsbook=sb, parlay_cal=p_cal,
                                    market_blend=mkt_w,
                                    max_same_market=MAX_SAME_MARKET.get(sport_label),
                                    stat_family=STAT_FAMILY.get(sport_label),
                                    max_same_family=MAX_SAME_FAMILY.get(sport_label))
        if not safe and not value:
            continue
        s = parlay_tracker.log_parlays(safe,  sport_label, sb, kind="safe")
        v = parlay_tracker.log_parlays(value, sport_label, sb, kind="value")

        # The whole slate is logged — it is the training data. Only the positive-EV ones
        # are worth betting, so say which those are.
        rec = [p for p in safe + value if p.get("recommended")]
        sizes = sorted({p["n"] for p in rec})
        print(f"    Logged {s} safe + {v} value parlays.")
        if rec:
            best = max(rec, key=lambda p: p["ev"])
            print(f"    RECOMMENDED (positive EV): {len(rec)} of {len(safe) + len(value)} "
                  f"— pick counts {sizes}, best EV {best['ev']:+.3f} on a {best['n']}-leg.")
        else:
            print(f"    RECOMMENDED: none — no positive-EV parlay on the board today.")
        total += s + v

    if pm._FD_UNMAPPED:
        # A FanDuel market we can't name is a prop we silently never bet — the same
        # failure mode as the stat types that went months without resolving.
        print(f"\n  FanDuel markets with no stat mapping: {sorted(pm._FD_UNMAPPED)}")
        pm._FD_UNMAPPED.clear()

    return total

# ── Pre-run housekeeping ───────────────────────────────────────────────────

STALE_RUN_HOURS = 36   # a daily job that hasn't logged a parlay in this long has missed a day


def resolve_pending():
    """
    Settle finished games before scoring today's slate.

    Calibration is derived from resolved legs, so running this first is what makes
    today's factors reflect last night's results. Nothing else calls it — leaving
    it to a manual invocation is how the model ended up generating a full week of
    parlays against stale factors.
    """
    print(f"\n{'='*62}\n  Resolving pending legs\n{'='*62}")
    try:
        counts = parlay_tracker.resolve_all_legs()
        print(f"  Resolved — MLB {counts['mlb']}, WNBA {counts['wnba']}, "
              f"NBA {counts['nba']}, NFL {counts.get('nfl', 0)}")
    except Exception as e:
        # A resolver outage must not block generation; today's factors just stay put.
        print(f"  Resolution failed ({e}) — continuing with existing calibration.")
        return

    try:
        abandoned = parlay_tracker.get_abandoned_legs()
        if abandoned:
            total = sum(a["legs"] for a in abandoned)
            print(f"\n  Gave up on {total} leg(s) after "
                  f"{parlay_tracker.RESOLVE_MAX_ATTEMPTS} tries — they stop costing API calls:")
            for a in abandoned[:6]:
                print(f"    {a['sport']:5s} {a['stat_type']:22s} {a['legs']:4d} legs, "
                      f"{a['players']} player(s)")
            print("    (a whole stat type here means the resolver can't name it — fix the "
                  "mapping. Legs spread across many stat types for the same few players "
                  "usually means those players did not play. Either way "
                  "retry_abandoned_legs() reopens them without discarding graded legs.)")
    except Exception:
        pass


def warn_if_stale(now):
    """Flag a missed run: if the last logged parlay predates STALE_RUN_HOURS, a day was skipped."""
    try:
        last = parlay_tracker.last_parlay_time()
    except Exception:
        return
    if last is None:
        return
    gap_h = (now - last).total_seconds() / 3600
    if gap_h >= STALE_RUN_HOURS:
        print(f"\n  ** MISSED RUN ** last parlay logged {gap_h:.1f}h ago "
              f"({last:%Y-%m-%d %H:%M}) — expected a run within {STALE_RUN_HOURS}h.")


# ── Entry point ────────────────────────────────────────────────────────────

def log_game_markets():
    """
    Snapshot the game-level moneyline and settle finished games.

    Runs every time the generator does, including forced second runs, because the extra
    snapshots ARE the point — repeated observations through the day are what turn the
    log into a record of line movement rather than a single opening price. It is the
    benchmark any future winner model gets graded against, so it starts accumulating
    now, before that model exists; there is no way to backfill a closing line.

    Wrapped so a failure here can never take down parlay generation — this is
    measurement, not something the daily board depends on.
    """
    print(f"\n{'='*62}\n  Game markets\n{'='*62}")
    month = datetime.now().month
    # Same season gates the parlay side uses. Belt and braces with the price sanity
    # check in fetch_moneylines: out of season the page is all futures and specials.
    in_season = {"mlb": 3 <= month <= 11,
                 "wnba": 5 <= month <= 9,
                 "nba": month >= 10 or month <= 6}
    for sport_key, label in (("mlb", "MLB"), ("wnba", "WNBA"), ("nba", "NBA")):
        if not in_season[sport_key]:
            print(f"  {label}: off-season — skipping.")
            continue
        try:
            df = pm.fetch_moneylines(sport_key)
            if df is None or df.empty:
                print(f"  {label}: no moneylines posted.")
                continue
            r = game_tracker.log_market(df, label)
            print(f"  {label}: {len(df)} games — {r['new']} new, {r['snapshots']} snapshot(s) "
                  f"(mean vig {100*(df['overround'].mean()-1):.1f}%).")
        except Exception as e:
            print(f"  {label}: market logging skipped — {e}")
    try:
        counts = game_tracker.resolve_all()
        print(f"  Resolved — {', '.join(f'{k} {v}' for k, v in counts.items())}")
    except Exception as e:
        print(f"  Game resolution skipped — {e}")

    # Predictions are LOGGED, not bet. A 56% winner model is indistinguishable from
    # picking favourites until it can be scored against the de-vigged closing line, and
    # the only way to get that comparison is to record a prediction before the close and
    # wait. Nothing here reaches the dashboard or the board.
    if in_season["mlb"]:
        try:
            log_mlb_predictions()
        except Exception as e:
            print(f"  Game predictions skipped — {e}")


def log_mlb_predictions():
    """Rate every finished game this season, then predict the ones not yet played."""
    import statsapi
    import game_model as gmod
    season = f"{datetime.now().year}"
    raw = statsapi.schedule(start_date=f"03/01/{season}", end_date=datetime.now().strftime("%m/%d/%Y"),
                            sportId=1)
    hist = [{"game_id": g["game_id"], "date": g["game_date"], "home": g["home_name"],
             "away": g["away_name"], "home_p": g.get("home_probable_pitcher") or "?",
             "away_p": g.get("away_probable_pitcher") or "?",
             "home_score": g["home_score"], "away_score": g["away_score"]}
            for g in raw if g.get("status") == "Final"
            and g.get("home_probable_pitcher") and g.get("away_probable_pitcher")]
    if len(hist) < 200:
        print(f"  Game predictions: only {len(hist)} finished games — too early to rate.")
        return
    model = gmod.build_from_history(hist)

    upcoming = [g for g in raw if g.get("status") != "Final"
                and g.get("home_probable_pitcher") and g.get("away_probable_pitcher")]
    slate = [{"game_label": f"{pm.canonical_abbr('mlb', pm._fd_team_abbr('mlb', g['away_name']))}"
                            f" @ {pm.canonical_abbr('mlb', pm._fd_team_abbr('mlb', g['home_name']))}",
              "home": g["home_name"], "away": g["away_name"],
              "home_p": g["home_probable_pitcher"], "away_p": g["away_probable_pitcher"]}
             for g in upcoming]
    preds = gmod.predict_slate(model, slate)
    n = game_tracker.log_predictions(preds, "MLB")
    print(f"  Game predictions: rated {len(hist)} finished games, "
          f"predicted {len(preds)}, matched {n} to logged markets.")


def already_ran_today(now) -> bool:
    """True if a parlay was already logged today.

    The scheduled task repeats hourly through the day so a 2 PM run missed while
    the machine was hibernated/in modern-standby is caught the next time it's
    awake. Only the first success each day should generate; every later firing
    exits here instead of producing a duplicate slate.
    """
    try:
        last = parlay_tracker.last_parlay_time()
    except Exception:
        return False
    return last is not None and last.date() == now.date()


def snapshot_only_pass(month: int) -> int:
    """
    Re-price today's still-pending legs against the current market. Returns legs stamped.

    This is what the hourly catch-up firings do now instead of returning immediately, and
    it exists because CLV was close to unmeasurable: only 27.8% of logged props ever
    received a closing snapshot, and on a one-board day it was 10%. A leg can only be
    stamped while its game is still quoted, so with a single working pass a day most legs
    got exactly one chance — at the moment they were logged, at their own entry price,
    which records a CLV of zero rather than a closing line.

    Firing hourly through the evening catches prices near kickoff, which is the number
    CLV is supposed to compare against. Deliberately snapshot-only: no resolve, no
    generation, no logging. One FanDuel fetch per in-season sport is cheap enough to run
    every hour, where a resolve pass carries a 600s budget and would put the expensive
    work back on a schedule that has no board to protect.
    """
    total = 0
    for sport_key, sport_label, in_season in (
        ("mlb",  "MLB",  True),
        ("wnba", "WNBA", 5 <= month <= 9),
        ("nba",  "NBA",  month >= 10 or month <= 6),
        ("nfl",  "NFL",  month >= 9 or month <= 2),
    ):
        if not in_season:
            continue
        try:
            raw = fetch_fanduel(sport_key)
            if raw is None or raw.empty:
                continue
            n = parlay_tracker.update_market_snapshots(raw, sport_label)
            total += n
            if n:
                print(f"    {sport_label}: {n} leg(s) re-priced.")
        except Exception as e:
            print(f"    {sport_label}: snapshot failed ({e}).")
    if not total:
        print("    No pending legs still on the board.")

    # Lineups post ~3-4h before first pitch, so the hourly firing is the only thing in the
    # system positioned to see them. This is where the batter-prop drift comes from.
    try:
        lc = parlay_tracker.apply_lineup_check()
        if lc.get("scanned"):
            print(f"    Lineups: {lc['in']} in ({lc['repriced']} re-priced), "
                  f"{lc['out']} not in a posted lineup, {lc['no_lineup']} awaiting cards "
                  f"(of {lc['scanned']} legs starting soon).")
    except Exception as e:
        print(f"    Lineup check failed ({e}).")
    return total


def main():
    now   = datetime.now()
    month = now.month
    # --force runs even when today already has a board. The guard exists to stop the
    # hourly catch-up firing from duplicating a slate, which is right for the default
    # once-a-day job — but a split slate is a real case it blocks. On 2026-08-15 five
    # MLB games started 12:10-15:10 and ten more from 17:10: one board cannot price
    # both clusters at a sane lead, because whatever time you pick is hours late for
    # the early games or hours early for the late ones. A forced second run near the
    # late first pitch prices those off current lines instead. Books drop props once a
    # game is under way, so the split is self-enforcing: the later run only ever sees
    # games that have not started. Identical leg sets dedupe on _parlay_id, so a prop
    # unchanged between runs is not logged twice.
    force = "--force" in sys.argv
    print(f"\nKonjure Analytics — Daily Parlay Generator")
    print(f"Run: {now.strftime('%Y-%m-%d %H:%M')}{'  (forced)' if force else ''}")

    if already_ran_today(now) and not force:
        print("  Already generated today — catch-up firing, snapshotting prices only.")
        snapshot_only_pass(month)
        return

    warn_if_stale(now)
    resolve_pending()
    log_game_markets()

    total = 0
    total += run_sport("mlb",  "MLB",  2, MLB_STAT_TYPES,  mlb_hit_rate)

    if 5 <= month <= 9:
        total += run_sport("wnba", "WNBA", 6, WNBA_STAT_TYPES, wnba_hit_rate)
    else:
        print("\n  WNBA: off-season — skipping.")

    if month >= 10 or month <= 6:
        total += run_sport("nba",  "NBA",  7, NBA_STAT_TYPES,  nba_hit_rate)
    else:
        print("\n  NBA: off-season — skipping.")

    # Sep-Feb covers Week 1 through the Super Bowl. The empty-fetch guard in run_sport
    # would skip an out-of-season NFL run anyway, but the month gate saves three book
    # fetches and the ~2s nflverse load on every run for the other six months.
    if month >= 9 or month <= 2:
        total += run_sport("nfl",  "NFL",  9, NFL_STAT_TYPES,  nfl_hit_rate)
    else:
        print("\n  NFL: off-season — skipping.")

    print(f"\n{'='*62}")
    print(f"  Total parlays logged: {total}")
    print(f"{'='*62}\n")

if __name__ == "__main__":
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _real_stdout, _real_stderr = sys.stdout, sys.stderr
    with open(LOG_PATH, "a", encoding="utf-8") as _log:
        sys.stdout = _Tee(_real_stdout, _log)
        sys.stderr = _Tee(_real_stderr, _log)   # tracebacks belong in the log too
        try:
            main()
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        finally:
            # Restore before the log file closes: leaving sys.stdout wrapped around a
            # closed file makes the interpreter's shutdown flush raise, which exits
            # non-zero (120) even on a clean run and reads as a failed scheduled task.
            sys.stdout, sys.stderr = _real_stdout, _real_stderr
