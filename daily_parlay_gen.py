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
import sys, time, os, requests, pandas as pd
from datetime import datetime
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# parlay_tracker / parlay_model live in the same directory
sys.path.insert(0, os.path.dirname(__file__))
import parlay_tracker
import parlay_model as pm

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
WNBA_STAT_TYPES = ["Points", "Rebounds", "Assists", "Pts+Rebs+Asts", "3-PT Made"]
NBA_STAT_TYPES  = ["Points", "Rebounds", "Assists", "Pts+Rebs+Asts", "3-PT Made"]

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
                    away = gteams.get("away", {}).get("abbreviation", "")
                    home = gteams.get("home", {}).get("abbreviation", "")
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
            title = game.get("abbreviated_title", "")
            team  = ""
            if " @ " in title:
                away, home = title.split(" @ ", 1)
                team = away if app.get("team_id") == game.get("away_team_id") else home
            over_opt = next((o for o in line.get("options", []) if o.get("choice") == "higher"), None)
            if not over_opt:
                continue
            try:
                american = int(str(over_opt.get("american_price", "-110")).replace("+", ""))
            except Exception:
                american = -110
            rows.append({
                "player_name": name, "team": team, "stat_type": st_type,
                "line_score": val, "odds_type": "standard",
                "american_odds": american, "implied_prob": 0.50,
                "game_id": app.get("match_id", ""), "game_label": title,
                "start_time": game.get("scheduled_at", ""),
                "sportsbook": "Underdog",
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        print(f"    Underdog fetch failed: {e}")
        return pd.DataFrame()

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

def build_parlays(legs, min_legs=2, max_legs=4, top_n=50, pool_size=30, max_leg_uses=6):
    return pm._build_parlays(legs, min_legs=min_legs, max_legs=max_legs,
                              top_n=top_n, pool_size=pool_size, max_leg_uses=max_leg_uses)

# ── Leg scorer ─────────────────────────────────────────────────────────────

def score_legs(df, cal, stat_types, rate_fn):
    df   = df[df["stat_type"].isin(stat_types)].copy()
    legs, seen = [], set()
    for _, row in df.iterrows():
        key = (row["player_name"], row["stat_type"])
        if key in seen:
            continue
        seen.add(key)
        try:
            line = float(row["line_score"])
        except Exception:
            continue
        rate, n = rate_fn(
            row["player_name"], row["stat_type"], line,
            odds_type=str(row.get("odds_type", "standard")),
            implied=float(row.get("implied_prob", -1.0)),
            cal=cal.get(row["stat_type"], 1.0),
            team=str(row.get("team", "")),
        )
        if n < 3:
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
            "hit_rate":      rate,
            "sample_n":      n,
        })
        time.sleep(0.03)
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

    total = 0
    for sb, fetch_fn in [("PrizePicks", lambda: fetch_prizepicks(pp_league_id)),
                          ("Underdog",   lambda: fetch_underdog(sport_key))]:
        print(f"\n  [{sb}]")
        raw = fetch_fn()
        if raw.empty:
            print(f"    No lines — skipping.")
            continue
        print(f"    {len(raw)} lines fetched.")

        legs = score_legs(raw, cal, stat_types, rate_fn)
        print(f"    {len(legs)} legs scored.")
        if len(legs) < 2:
            continue

        safe, value = build_parlays(legs)
        s = parlay_tracker.log_parlays(safe,  sport_label, sb, kind="safe")
        v = parlay_tracker.log_parlays(value, sport_label, sb, kind="value")
        print(f"    Logged {s} safe + {v} value parlays.")
        total += s + v

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
        print(f"  Resolved — MLB {counts['mlb']}, WNBA {counts['wnba']}, NBA {counts['nba']}")
    except Exception as e:
        # A resolver outage must not block generation; today's factors just stay put.
        print(f"  Resolution failed ({e}) — continuing with existing calibration.")


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

def main():
    now   = datetime.now()
    month = now.month
    print(f"\nKonjure Analytics — Daily Parlay Generator")
    print(f"Run: {now.strftime('%Y-%m-%d %H:%M')}")

    warn_if_stale(now)
    resolve_pending()

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
