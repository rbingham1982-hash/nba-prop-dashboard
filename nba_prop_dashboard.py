# -*- coding: utf-8 -*-
"""
Konjure Analytics — Multi-Sport Prop & Predictive Dashboard
"""

import os
import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
from nba_api.stats.static import teams, players
from nba_api.stats.endpoints import playergamelog, commonteamroster
from datetime import datetime, timedelta

# ─── Page config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Konjure Analytics",
    page_icon="🏆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Base CSS (shared) ─────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1.5rem !important; max-width: 100% !important; }
html, body, .stApp {
    font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif;
}

/* ── Header ── */
.konjure-header {
    padding: 0.75rem 0 1rem 0;
    border-bottom: 1px solid var(--border);
    margin-bottom: 0.5rem;
}
.konjure-title {
    font-size: 1.4rem; font-weight: 700; letter-spacing: 0.14em;
    text-transform: uppercase; color: var(--text-primary); margin: 0 0 0.1rem 0;
}
.konjure-sub {
    font-size: 0.68rem; color: var(--text-muted); letter-spacing: 0.18em;
    text-transform: uppercase; margin: 0;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0; background-color: transparent !important;
    border-bottom: 1px solid var(--border);
}
.stTabs [data-baseweb="tab"] {
    color: var(--text-muted) !important; background-color: transparent !important;
    border: none !important; border-bottom: 2px solid transparent !important;
    padding: 0.65rem 1.2rem !important; font-size: 0.72rem !important;
    font-weight: 500 !important; letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
}
.stTabs [aria-selected="true"] {
    color: var(--accent) !important;
    border-bottom: 2px solid var(--accent) !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.5rem; }

/* ── Section heading ── */
.section-heading {
    font-size: 0.65rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--text-muted); margin: 1.25rem 0 0.5rem 0;
    padding-bottom: 0.35rem; border-bottom: 1px solid var(--border);
}

/* ── Metric cards ── */
[data-testid="metric-container"] {
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px; padding: 0.9rem 1.1rem !important;
}
[data-testid="metric-container"] label {
    color: var(--text-muted) !important; font-size: 0.65rem !important;
    letter-spacing: 0.12em !important; text-transform: uppercase !important;
}
[data-testid="metric-container"] [data-testid="metric-value"] {
    color: var(--text-primary) !important;
    font-size: 1.4rem !important; font-weight: 600 !important;
}

/* ── DataFrames ── */
.stDataFrame { border: 1px solid var(--border) !important; border-radius: 6px !important; }

/* ── Alerts ── */
.stAlert {
    background-color: var(--surface) !important; border: 1px solid var(--border) !important;
    color: var(--text-muted) !important; border-radius: 6px;
}

/* ── Divider ── */
hr { border-color: var(--border) !important; }

/* ── Player card (NBA dark) ── */
.player-card {
    background-color: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 1rem 1.25rem; display: flex;
    align-items: center; gap: 1rem; margin-bottom: 1.25rem;
}
.player-card img {
    width: 68px; height: 68px; object-fit: cover;
    border-radius: 50%; background: var(--surface); border: 1px solid var(--border);
}
.player-card-name { font-size: 1rem; font-weight: 600; color: var(--text-primary); margin: 0 0 0.15rem 0; }
.player-card-team { font-size: 0.65rem; color: var(--text-muted); letter-spacing: 0.12em; text-transform: uppercase; margin: 0; }

/* ── Feature cards (home) ── */
.feature-card {
    background-color: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.1rem 1.25rem; height: 100%;
}
.feature-card-icon { font-size: 1.3rem; margin-bottom: 0.4rem; }
.feature-card-title {
    font-size: 0.74rem; font-weight: 600; letter-spacing: 0.1em;
    text-transform: uppercase; color: var(--text-primary); margin: 0 0 0.35rem 0;
}
.feature-card-desc { font-size: 0.8rem; color: var(--text-muted); margin: 0; line-height: 1.5; }

/* ── MLB player photo card ── */
.mlb-player-card {
    background: var(--mlb-surface); border: 1px solid var(--mlb-border);
    border-radius: 12px; padding: 1.25rem; display: flex;
    align-items: center; gap: 1.25rem; margin-bottom: 1.25rem;
    box-shadow: 0 2px 8px rgba(0,45,114,0.08);
}
.mlb-player-card img {
    width: 90px; height: 90px; object-fit: cover; object-position: top;
    border-radius: 8px; background: #e8edf4;
    border: 2px solid var(--mlb-navy);
}
.mlb-player-name { font-size: 1.15rem; font-weight: 700; color: var(--mlb-navy); margin: 0 0 0.2rem 0; }
.mlb-player-pos { font-size: 0.68rem; color: var(--mlb-red); font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; margin: 0 0 0.1rem 0; }
.mlb-player-team { font-size: 0.68rem; color: #666; letter-spacing: 0.1em; text-transform: uppercase; margin: 0; }

/* ── MLB section heading ── */
.mlb-section {
    font-size: 0.65rem; font-weight: 700; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--mlb-navy);
    margin: 1.5rem 0 0.6rem 0; padding-bottom: 0.4rem;
    border-bottom: 2px solid var(--mlb-red);
}

/* ── MLB stat badge ── */
.mlb-badge {
    display: inline-block; background: var(--mlb-navy); color: #fff;
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.06em;
    padding: 0.2rem 0.6rem; border-radius: 20px; margin-right: 0.4rem;
}

/* ── Prediction chip ── */
.pred-chip {
    display: inline-block; padding: 0.3rem 0.8rem; border-radius: 20px;
    font-size: 0.75rem; font-weight: 600; letter-spacing: 0.04em;
    background: var(--mlb-red); color: #fff; margin-top: 0.3rem;
}

/* ── Sport selector ── */
div[data-testid="stSelectbox"] label { display: none; }
</style>
""", unsafe_allow_html=True)

# ─── ESPN slug map ─────────────────────────────────────────────────────────
ESPN_SLUG_MAP = {
    "ATL": "atlanta-hawks", "BOS": "boston-celtics", "BKN": "brooklyn-nets",
    "CHA": "charlotte-hornets", "CHI": "chicago-bulls", "CLE": "cleveland-cavaliers",
    "DAL": "dallas-mavericks", "DEN": "denver-nuggets", "DET": "detroit-pistons",
    "GSW": "golden-state-warriors", "HOU": "houston-rockets", "IND": "indiana-pacers",
    "LAC": "la-clippers", "LAL": "los-angeles-lakers", "MEM": "memphis-grizzlies",
    "MIA": "miami-heat", "MIL": "milwaukee-bucks", "MIN": "minnesota-timberwolves",
    "NOP": "new-orleans-pelicans", "NYK": "new-york-knicks", "OKC": "oklahoma-city-thunder",
    "ORL": "orlando-magic", "PHI": "philadelphia-76ers", "PHX": "phoenix-suns",
    "POR": "portland-trail-blazers", "SAC": "sacramento-kings", "SAS": "san-antonio-spurs",
    "TOR": "toronto-raptors", "UTA": "utah-jazz", "WAS": "washington-wizards"
}

# ══════════════════════════════════════════════════════════════════════════════
# NBA DATA FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════
def get_team_abbreviation(team_name):
    team = next((t for t in teams.get_teams() if t["full_name"] == team_name), None)
    return team["abbreviation"].upper() if team else None

def get_team_id(team_abbr):
    team = next((t for t in teams.get_teams() if t["abbreviation"].upper() == team_abbr.upper()), None)
    return team["id"] if team else None

@st.cache_data(ttl=3600)
def get_team_players(team_abbr):
    team_id = get_team_id(team_abbr)
    if not team_id:
        return []
    try:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id).get_data_frames()[0]
        return roster["PLAYER"].tolist()
    except Exception as e:
        st.warning(f"Could not load roster for {team_abbr}: {e}")
        return []

def get_player_id(player_name):
    match = players.find_players_by_full_name(player_name)
    return match[0]['id'] if match else None

@st.cache_data(ttl=3600)
def get_gamelogs(player_id, seasons):
    frames = []
    for season in seasons:
        try:
            logs = playergamelog.PlayerGameLog(
                player_id=player_id, season=season
            ).get_data_frames()[0]
            logs['SEASON'] = season
            extracted = logs['MATCHUP'].str.extract(r'@ (\w+)|vs\. (\w+)')
            logs['OPPONENT'] = extracted[0].fillna(extracted[1])
            frames.append(logs)
        except Exception as e:
            st.warning(f"Could not load {season} data: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

@st.cache_data(ttl=900)
def get_next_opponent(team_code):
    try:
        slug = ESPN_SLUG_MAP.get(team_code.upper(), team_code.lower())
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{slug}/schedule"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            now = datetime.now()
            for event in data.get("events", []):
                date_str = event.get("date")
                game_date = datetime.fromisoformat(date_str.replace("Z", ""))
                if game_date > now:
                    competitors = event["competitions"][0]["competitors"]
                    for team in competitors:
                        abbrev = team["team"]["abbreviation"].upper()
                        if abbrev != team_code.upper():
                            return abbrev
    except Exception as e:
        st.warning(f"Opponent detection failed: {e}")
    return None

@st.cache_data(ttl=900)
def get_prizepicks_lines(league_id=7):
    try:
        url = f"https://api.prizepicks.com/projections?league_id={league_id}&per_page=250&single_stat=true"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://app.prizepicks.com/"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return pd.DataFrame()
        payload = resp.json()
        player_map = {
            item["id"]: item["attributes"].get("display_name", "")
            for item in payload.get("included", [])
            if item.get("type") == "new_player"
        }
        rows = []
        for proj in payload.get("data", []):
            if proj.get("type") != "projection":
                continue
            attrs = proj.get("attributes", {})
            rel = proj.get("relationships", {}).get("new_player", {}).get("data", {})
            pid = rel.get("id", "")
            rows.append({
                "player_name": player_map.get(pid, ""),
                "stat_type": attrs.get("stat_type", ""),
                "line_score": attrs.get("line_score"),
                "status": attrs.get("status", ""),
            })
        df = pd.DataFrame(rows)
        return df[df["player_name"] != ""] if not df.empty else df
    except Exception as e:
        st.warning(f"PrizePicks fetch failed: {e}")
        return pd.DataFrame()

def get_real_time_line(player_name, market="points"):
    try:
        api_key = st.secrets["ODDS_API_KEY"]
    except Exception:
        api_key = os.environ.get("ODDS_API_KEY", "132d657e987feea06b1b91a21116d4a0")
    try:
        url = "https://api.sportsgameodds.com/v2/events"
        headers = {"X-API-Key": api_key}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for event in data.get("events", []):
                for prop in event.get("playerProps", []):
                    if (player_name.lower() in prop.get("name", "").lower()
                            and market in prop.get("market", "").lower()):
                        return prop.get("line")
    except Exception:
        pass
    return None

def simulate_bets(df):
    bet_result = df["HIT"].apply(lambda x: 1 if x else -1)
    return pd.Series(bet_result.cumsum().to_list(), index=df.index, name="CUMULATIVE_PROFIT")

@st.cache_data(ttl=3600)
def get_first_basket_data():
    url = "https://firstbasketstats.com/2024-2025-first-basket-stats-data"
    fallback = {
        "BOS": {"Games": 12, "First Basket": 7, "Tip Wins": 8},
        "DEN": {"Games": 13, "First Basket": 9, "Tip Wins": 10},
        "LAL": {"Games": 14, "First Basket": 6, "Tip Wins": 5},
    }
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table", {"id": "team-first-basket"})
        if not table:
            return fallback
        rows = table.find_all("tr")[1:]
        data = {}
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            team = cells[0].text.strip().upper()
            try:
                data[team] = {
                    "Games": int(cells[1].text.strip()),
                    "First Basket": int(cells[2].text.strip()),
                    "Tip Wins": int(cells[3].text.strip()),
                }
            except ValueError:
                continue
        return data if data else fallback
    except Exception:
        return fallback

@st.cache_data(ttl=1800)
def get_today_first_basket_stats():
    url = "https://firstbasketstats.com/today-first-basket-stats"
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if not table:
            return pd.DataFrame()
        rows = table.find_all("tr")[1:]
        data = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 6:
                continue
            data.append({
                "Matchup": cells[0].text.strip(), "Tip Winner": cells[1].text.strip(),
                "Likely Jumper": cells[2].text.strip(), "First Basket": cells[3].text.strip(),
                "Shot Type": cells[4].text.strip(), "Position": cells[5].text.strip(),
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.warning(f"Failed to load today's first basket data: {e}")
        return pd.DataFrame()

# ══════════════════════════════════════════════════════════════════════════════
# MLB DATA FUNCTIONS  (MLB Stats API — free, no key required)
# ══════════════════════════════════════════════════════════════════════════════
MLB_SEASON = "2026"
MLB_SEASONS = ["2024", "2025", "2026"]
MLB_BASE = "https://statsapi.mlb.com/api/v1"

@st.cache_data(ttl=3600)
def get_mlb_teams():
    try:
        resp = requests.get(f"{MLB_BASE}/teams?sportId=1&season={MLB_SEASON}", timeout=10)
        data = resp.json().get("teams", [])
        return sorted(
            [{"id": t["id"], "name": t["name"], "abbr": t.get("abbreviation", "")}
             for t in data if t.get("active", True) and t.get("sport", {}).get("id") == 1],
            key=lambda x: x["name"],
        )
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_mlb_roster(team_id):
    try:
        url = f"{MLB_BASE}/teams/{team_id}/roster?season={MLB_SEASON}&rosterType=active"
        resp = requests.get(url, timeout=10)
        roster = resp.json().get("roster", [])
        hitters, pitchers = [], []
        for p in roster:
            pos = p.get("position", {})
            pos_abbr = pos.get("abbreviation", "")
            pos_type = pos.get("type", "")
            name = p.get("person", {}).get("fullName", "")
            pid = p.get("person", {}).get("id")
            entry = {"name": name, "id": pid, "pos": pos_abbr}
            if pos_abbr == "P" or pos_type == "Pitcher":
                pitchers.append(entry)
            else:
                hitters.append(entry)
        return hitters, pitchers
    except Exception as e:
        st.warning(f"Could not load MLB roster: {e}")
        return [], []

@st.cache_data(ttl=3600)
def get_mlb_hitting_logs(player_id, seasons=(MLB_SEASON,)):
    frames = []
    for season in seasons:
        try:
            url = f"{MLB_BASE}/people/{player_id}/stats?stats=gameLog&season={season}&group=hitting"
            resp = requests.get(url, timeout=10)
            splits = resp.json().get("stats", [{}])[0].get("splits", [])
            rows = []
            for s in splits:
                st_data = s.get("stat", {})
                rows.append({
                    "date": s.get("date", ""),
                    "season": season,
                    "opponent": s.get("opponent", {}).get("abbreviation", ""),
                    "AB": int(st_data.get("atBats") or 0),
                    "H":  int(st_data.get("hits") or 0),
                    "HR": int(st_data.get("homeRuns") or 0),
                    "RBI": int(st_data.get("rbi") or 0),
                    "BB": int(st_data.get("baseOnBalls") or 0),
                    "K":  int(st_data.get("strikeOuts") or 0),
                    "AVG": float(st_data.get("avg") or 0),
                    "OBP": float(st_data.get("obp") or 0),
                    "SLG": float(st_data.get("slg") or 0),
                })
            if rows:
                frames.append(pd.DataFrame(rows))
        except Exception as e:
            st.warning(f"Could not load hitting logs for {season}: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)

@st.cache_data(ttl=3600)
def get_mlb_pitching_logs(player_id, seasons=(MLB_SEASON,)):
    frames = []
    for season in seasons:
        try:
            url = f"{MLB_BASE}/people/{player_id}/stats?stats=gameLog&season={season}&group=pitching"
            resp = requests.get(url, timeout=10)
            splits = resp.json().get("stats", [{}])[0].get("splits", [])
            rows = []
            for s in splits:
                st_data = s.get("stat", {})
                ip_str = str(st_data.get("inningsPitched") or "0")
                try:
                    parts = ip_str.split(".")
                    ip = int(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)
                except Exception:
                    ip = 0.0
                k9 = round((int(st_data.get("strikeOuts") or 0) / ip * 9), 2) if ip > 0 else 0
                rows.append({
                    "date": s.get("date", ""),
                    "season": season,
                    "opponent": s.get("opponent", {}).get("abbreviation", ""),
                    "IP":  round(ip, 1),
                    "H":  int(st_data.get("hits") or 0),
                    "ER": int(st_data.get("earnedRuns") or 0),
                    "BB": int(st_data.get("baseOnBalls") or 0),
                    "K":  int(st_data.get("strikeOuts") or 0),
                    "HR": int(st_data.get("homeRuns") or 0),
                    "ERA": float(st_data.get("era") or 0),
                    "WHIP": float(st_data.get("whip") or 0),
                    "K9": k9,
                })
            if rows:
                frames.append(pd.DataFrame(rows))
        except Exception as e:
            st.warning(f"Could not load pitching logs for {season}: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)

@st.cache_data(ttl=900)
def get_mlb_next_opponent(team_id):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
        url = f"{MLB_BASE}/schedule?sportId=1&teamId={team_id}&startDate={today}&endDate={future}"
        resp = requests.get(url, timeout=10)
        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                away = game["teams"]["away"]["team"]
                home = game["teams"]["home"]["team"]
                if away["id"] == team_id:
                    return home.get("abbreviation", ""), home.get("id")
                else:
                    return away.get("abbreviation", ""), away.get("id")
    except Exception:
        pass
    return None, None

def mlb_headshot(player_id):
    return (
        f"https://img.mlbstatic.com/mlb-photos/image/upload/"
        f"d_people:generic:headshot:67:current.png/w_213,q_auto:best/"
        f"v1/people/{player_id}/headshot/67/current"
    )

@st.cache_data(ttl=900)
def get_mlb_today_game_for_team(team_id):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = (f"{MLB_BASE}/schedule?sportId=1&teamId={team_id}"
               f"&date={today}&hydrate=probablePitcher,team")
        resp = requests.get(url, timeout=10)
        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                return game
    except Exception:
        pass
    return None

@st.cache_data(ttl=3600)
def get_mlb_pitcher_season_stats(pitcher_id):
    try:
        url = (f"{MLB_BASE}/people/{pitcher_id}/stats"
               f"?stats=season&season={MLB_SEASON}&group=pitching")
        resp = requests.get(url, timeout=10)
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        return splits[0].get("stat", {}) if splits else {}
    except Exception:
        return {}

@st.cache_data(ttl=3600)
def get_mlb_team_batting_stats(team_id):
    try:
        url = (f"{MLB_BASE}/teams/{team_id}/stats"
               f"?stats=season&season={MLB_SEASON}&group=hitting")
        resp = requests.get(url, timeout=10)
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
        return splits[0].get("stat", {}) if splits else {}
    except Exception:
        return {}

@st.cache_data(ttl=900)
def get_today_mlb_games():
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = f"{MLB_BASE}/schedule?sportId=1&date={today}&hydrate=team"
        resp = requests.get(url, timeout=10)
        games = []
        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                away = game["teams"]["away"]["team"]
                home = game["teams"]["home"]["team"]
                games.append({
                    "label": f"{away['name']} @ {home['name']}",
                    "away_id": away["id"], "away_name": away["name"],
                    "away_abbr": away.get("abbreviation", ""),
                    "home_id": home["id"], "home_name": home["name"],
                    "home_abbr": home.get("abbreviation", ""),
                })
        return games
    except Exception:
        return []

# ══════════════════════════════════════════════════════════════════════════════
# SCOUT REPORT GENERATORS
# ══════════════════════════════════════════════════════════════════════════════
def _scout_card_nba(text):
    return (
        "<div style='background:#111;border:1px solid #1c1c1c;border-left:3px solid #fff;"
        "border-radius:8px;padding:1.25rem 1.4rem;margin-bottom:1.25rem;'>"
        "<p style='font-size:0.6rem;letter-spacing:0.18em;text-transform:uppercase;"
        "color:#444;margin:0 0 0.55rem 0;'>Scout Report</p>"
        f"<p style='font-size:0.88rem;color:#ccc;line-height:1.8;margin:0;'>{text}</p></div>"
    )

def _scout_card_mlb(text):
    return (
        "<div style='background:#fff;border:1px solid #dde6f0;border-left:4px solid #002D72;"
        "border-radius:8px;padding:1.25rem 1.4rem;margin-bottom:1.25rem;"
        "box-shadow:0 2px 8px rgba(0,45,114,0.07);'>"
        "<p style='font-size:0.6rem;letter-spacing:0.18em;text-transform:uppercase;"
        "color:#6b7c9e;margin:0 0 0.55rem 0;'>Scout Report</p>"
        f"<p style='font-size:0.88rem;color:#1a1a2e;line-height:1.8;margin:0;'>{text}</p></div>"
    )

def nba_scout_report(player_name, team_code, df, next_opp, prop_type, rolling_window):
    if df.empty or not next_opp:
        return None
    season_pts = df["PTS"].mean()
    season_reb = df["REB"].mean()
    season_ast = df["AST"].mean()
    last10 = df.tail(10)
    recent_pts = last10["PTS"].mean()
    delta = recent_pts - season_pts
    if delta > 2:
        trend = f"trending upward at {recent_pts:.1f} PPG over the last 10 games (+{delta:.1f} vs season avg)"
    elif delta < -2:
        trend = f"trending downward at {recent_pts:.1f} PPG over the last 10 games ({delta:.1f} vs season avg)"
    else:
        trend = f"consistent at {recent_pts:.1f} PPG over the last 10 games"
    opp_df = df[df["OPPONENT"].str.upper() == next_opp.upper()]
    if not opp_df.empty:
        op = opp_df["PTS"].mean()
        or_ = opp_df["REB"].mean()
        oa = opp_df["AST"].mean()
        n = len(opp_df)
        quality = "favorable" if op - season_pts > 3 else ("difficult" if season_pts - op > 3 else "neutral")
        opp_str = (f"Against {next_opp} in {n} game(s) from the selected seasons, {player_name} averaged "
                   f"{op:.1f} PPG / {or_:.1f} RPG / {oa:.1f} APG — a historically {quality} matchup.")
    else:
        opp_str = f"No prior matchup data vs {next_opp} exists in the selected seasons, making this an uncharted assignment."
    pred_pts = df["PTS"].rolling(rolling_window).mean().iloc[-1] if len(df) >= rolling_window else season_pts
    pred_reb = df["REB"].rolling(rolling_window).mean().iloc[-1] if len(df) >= rolling_window else season_reb
    pred_ast = df["AST"].rolling(rolling_window).mean().iloc[-1] if len(df) >= rolling_window else season_ast
    last5_pts = df.tail(5)["PTS"].mean()
    if last5_pts > season_pts * 1.1:
        form = f"Momentum is on their side — {player_name} is running hot over the last 5 games ({last5_pts:.1f} PPG)."
    elif last5_pts < season_pts * 0.9:
        form = f"A note of caution: {player_name} has cooled over the last 5 games ({last5_pts:.1f} PPG)."
    else:
        form = f"Recent 5-game form is steady ({last5_pts:.1f} PPG), lending reliability to the projection."
    return (
        f"{player_name} ({team_code}) heads into the upcoming matchup against {next_opp} averaging "
        f"{season_pts:.1f} PPG, {season_reb:.1f} RPG, and {season_ast:.1f} APG this season. "
        f"Scoring is {trend}. {opp_str} "
        f"A {rolling_window}-game rolling model projects approximately "
        f"{pred_pts:.1f} pts / {pred_reb:.1f} reb / {pred_ast:.1f} ast. {form}"
    )

def mlb_hitter_scout_report(hitter_name, team_abbr, df, team_id):
    if df.empty:
        return None
    avg = df["AVG"].mean()
    obp = df["OBP"].mean()
    slg = df["SLG"].mean()
    ops = obp + slg
    game = get_mlb_today_game_for_team(team_id)
    pitcher_txt = opp_hist_txt = ""
    if game:
        is_away = game["teams"]["away"]["team"]["id"] == team_id
        opp_side = "home" if is_away else "away"
        opp_team = game["teams"][opp_side]["team"]
        opp_prob = game["teams"][opp_side].get("probablePitcher", {})
        opp_name = opp_team.get("name", "the opponent")
        opp_abbr = opp_team.get("abbreviation", "")
        opp_id = opp_team.get("id")
        if opp_prob:
            p_name = opp_prob.get("fullName", "the starter")
            p_id = opp_prob.get("id")
            pitcher_txt = f"Today, {hitter_name} faces {p_name} of the {opp_name}. "
            if p_id:
                ps = get_mlb_pitcher_season_stats(p_id)
                if ps:
                    p_era = float(ps.get("era") or 0)
                    p_whip = float(ps.get("whip") or 0)
                    p_k9 = float(ps.get("strikeoutsPer9Inn") or 0)
                    pitcher_txt += f"{p_name} carries a {p_era:.2f} ERA, {p_whip:.2f} WHIP, and {p_k9:.1f} K/9. "
                    if p_era < 3.50:
                        pitcher_txt += "This is a tough draw at the plate. "
                    elif p_era > 5.00:
                        pitcher_txt += "The numbers favor the hitter — this is an exploitable assignment. "
                    else:
                        pitcher_txt += "The matchup is neutral on paper. "
        else:
            pitcher_txt = f"Today's opponent is the {opp_name}; no probable starter has been posted yet. "
        if opp_abbr:
            vs = df[df["opponent"].str.upper() == opp_abbr.upper()]
            if not vs.empty:
                vs_avg = vs["H"].sum() / max(vs["AB"].sum(), 1)
                opp_hist_txt = (f"Against the {opp_name} this season, {hitter_name} is batting "
                                f".{int(vs_avg*1000):03d} ({vs['H'].sum()}-for-{vs['AB'].sum()}) "
                                f"with {vs['HR'].sum()} HR. ")
        if opp_id:
            bat = get_mlb_team_batting_stats(opp_id)
            if bat:
                t_avg = float(bat.get("avg") or 0)
                t_ops = float(bat.get("ops") or 0)
    recent10 = df.tail(10)
    rh = recent10["H"].mean()
    sh = df["H"].mean()
    form = (f"Over the last 10 games, {hitter_name} is hot ({rh:.2f} H/G, up from {sh:.2f} season avg)."
            if rh > sh * 1.2
            else f"Over the last 10 games, {hitter_name} has cooled ({rh:.2f} H/G vs {sh:.2f} season avg)."
            if rh < sh * 0.8
            else f"Recent form is consistent ({rh:.2f} H/G over the last 10 games).")
    pred_h = df["H"].rolling(10).mean().iloc[-1] if len(df) >= 10 else sh
    pred_hr = df["HR"].rolling(10).mean().iloc[-1] if len(df) >= 10 else df["HR"].mean()
    pred_rbi = df["RBI"].rolling(10).mean().iloc[-1] if len(df) >= 10 else df["RBI"].mean()
    q = "elite" if avg > 0.290 else ("above-average" if avg > 0.260 else ("solid" if avg > 0.230 else "below-average"))
    return (
        f"{hitter_name} ({team_abbr}) brings an {q} slash line of "
        f".{int(avg*1000):03d}/.{int(obp*1000):03d}/.{int(slg*1000):03d} (OPS {ops:.3f}) into today's game. "
        f"{pitcher_txt}{opp_hist_txt}{form} "
        f"10-game rolling projection: {pred_h:.1f} H / {pred_hr:.2f} HR / {pred_rbi:.1f} RBI."
    )

def mlb_pitcher_scout_report(pitcher_name, team_abbr, df, team_id):
    if df.empty:
        return None
    era = df["ERA"].mean()
    whip = df["WHIP"].mean()
    k9 = df["K9"].mean()
    tip = df["IP"].sum()
    bb9 = df["BB"].sum() / tip * 9 if tip > 0 else 0
    kbb = df["K"].sum() / max(df["BB"].sum(), 1)
    qs = ((df["IP"] >= 6) & (df["ER"] <= 3)).mean() * 100
    game = get_mlb_today_game_for_team(team_id)
    opp_txt = bat_txt = ""
    if game:
        is_away = game["teams"]["away"]["team"]["id"] == team_id
        opp_side = "home" if is_away else "away"
        opp_team = game["teams"][opp_side]["team"]
        opp_name = opp_team.get("name", "the opponent")
        opp_abbr = opp_team.get("abbreviation", "")
        opp_id = opp_team.get("id")
        opp_txt = f"Today, {pitcher_name} takes the hill against the {opp_name}. "
        vs = df[df["opponent"].str.upper() == opp_abbr.upper()] if opp_abbr else pd.DataFrame()
        if not vs.empty:
            opp_txt += (f"In {len(vs)} prior start(s) vs the {opp_name} this season, "
                        f"{pitcher_name} posted a {vs['ERA'].mean():.2f} ERA with {vs['K'].mean():.1f} K/start. ")
        if opp_id:
            bat = get_mlb_team_batting_stats(opp_id)
            if bat:
                t_avg = float(bat.get("avg") or 0)
                t_ops = float(bat.get("ops") or 0)
                if t_avg > 0.265:
                    bat_txt = (f"The {opp_name} offense is dangerous ({t_avg:.3f} team AVG / {t_ops:.3f} OPS) "
                               f"— precision will be essential. ")
                elif t_avg < 0.240:
                    bat_txt = (f"The {opp_name} lineup has struggled offensively ({t_avg:.3f} AVG / {t_ops:.3f} OPS), "
                               f"setting up a favorable strikeout environment. ")
                else:
                    bat_txt = f"The {opp_name} lineup is an average offensive unit ({t_avg:.3f} AVG / {t_ops:.3f} OPS). "
    last5 = df.tail(5)
    rk = last5["K"].mean()
    sk = df["K"].mean()
    form = (f"Over the last 5 starts, {pitcher_name} has been dealing — {rk:.1f} K/start (up from {sk:.1f} season avg)."
            if rk > sk * 1.15
            else f"Strikeout output has dipped over the last 5 starts ({rk:.1f} vs {sk:.1f} season avg), worth monitoring."
            if rk < sk * 0.85
            else f"Strikeout production has been steady at {rk:.1f}/start over the last 5 outings.")
    pred_k = df["K"].rolling(5).mean().iloc[-1] if len(df) >= 5 else sk
    pred_ip = df["IP"].rolling(5).mean().iloc[-1] if len(df) >= 5 else df["IP"].mean()
    tier = "elite" if era < 3.00 else ("solid" if era < 4.00 else ("middling" if era < 5.00 else "struggling"))
    return (
        f"{pitcher_name} ({team_abbr}) is a {tier} arm carrying a {era:.2f} ERA, "
        f"{whip:.2f} WHIP, {k9:.1f} K/9, {bb9:.1f} BB/9, {kbb:.2f} K/BB ratio, "
        f"and a {qs:.0f}% quality-start rate into today's outing. "
        f"{opp_txt}{bat_txt}{form} "
        f"5-start rolling projection: {pred_k:.1f} K / {pred_ip:.1f} IP."
    )

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS & CHART HELPERS
# ══════════════════════════════════════════════════════════════════════════════
STAT_MAP = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "PRA": "PRA", "3PM": "FG3M"}
PP_STAT_MAP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "PRA": "Pts+Rebs+Asts", "3PM": "3-PT Made",
}
SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]

NBA_CHART = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0f0f0f", font_color="#888",
    title_font_color="#e0e0e0",
    xaxis=dict(gridcolor="#1c1c1c", linecolor="#1c1c1c", zerolinecolor="#1c1c1c"),
    yaxis=dict(gridcolor="#1c1c1c", linecolor="#1c1c1c", zerolinecolor="#1c1c1c"),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#888")),
    margin=dict(t=40, b=20, l=0, r=0),
)
MLB_CHART = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#f8fbff", font_color="#555",
    title_font_color="#002D72",
    xaxis=dict(gridcolor="#dde6f0", linecolor="#dde6f0", zerolinecolor="#dde6f0"),
    yaxis=dict(gridcolor="#dde6f0", linecolor="#dde6f0", zerolinecolor="#dde6f0"),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#555")),
    margin=dict(t=40, b=20, l=0, r=0),
)

def nba_fig(fig):
    fig.update_layout(**NBA_CHART)
    return fig

def mlb_fig(fig):
    fig.update_layout(**MLB_CHART)
    return fig

# ══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def nba_player_card(player_name, team_code):
    pid = get_player_id(player_name)
    headshot = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png" if pid else ""
    st.markdown(f"""
    <div class="player-card">
        <img src="{headshot}" />
        <div>
            <p class="player-card-name">{player_name}</p>
            <p class="player-card-team">{team_code}</p>
        </div>
    </div>""", unsafe_allow_html=True)

def mlb_player_card(name, pos, team, player_id):
    photo = mlb_headshot(player_id)
    st.markdown(f"""
    <div class="mlb-player-card">
        <img src="{photo}" />
        <div>
            <p class="mlb-player-name">{name}</p>
            <p class="mlb-player-pos">{pos}</p>
            <p class="mlb-player-team">{team}</p>
        </div>
    </div>""", unsafe_allow_html=True)

def section(label):
    st.markdown(f'<p class="section-heading">{label}</p>', unsafe_allow_html=True)

def mlb_section(label):
    st.markdown(f'<p class="mlb-section">{label}</p>', unsafe_allow_html=True)

def rolling_projection(df, col, window):
    if len(df) >= window:
        return df[col].rolling(window).mean().iloc[-1]
    return df[col].mean() if not df.empty else 0

# ══════════════════════════════════════════════════════════════════════════════
# HEADER + SPORT SELECTOR
# ══════════════════════════════════════════════════════════════════════════════
hdr_col, sport_col = st.columns([5, 1])
with hdr_col:
    st.markdown("""
    <div class="konjure-header">
        <p class="konjure-title">Konjure Analytics</p>
        <p class="konjure-sub">Multi-Sport Prop Intelligence &nbsp;&middot;&nbsp; Powered by Data</p>
    </div>""", unsafe_allow_html=True)
with sport_col:
    sport = st.selectbox("Sport", ["🏀 NBA", "⚾ MLB"], key="sport_selector")

# ══════════════════════════════════════════════════════════════════════════════
# SPORT-SPECIFIC CSS INJECTION
# ══════════════════════════════════════════════════════════════════════════════
if sport == "🏀 NBA":
    st.markdown("""
    <style>
    html, body, .stApp {
        background-color: #090909 !important; color: #e0e0e0 !important;
        --text-primary: #ffffff; --text-muted: #444; --border: #1c1c1c;
        --surface: #0f0f0f; --accent: #ffffff;
        --mlb-navy: #fff; --mlb-red: #fff; --mlb-surface: #111; --mlb-border: #1c1c1c;
    }
    div[data-baseweb="select"] > div {
        background-color: #101010 !important; border: 1px solid #222 !important; color: #e0e0e0 !important;
    }
    div[data-baseweb="select"] svg { fill: #555 !important; }
    .stTextInput input, .stNumberInput input {
        background-color: #101010 !important; border: 1px solid #222 !important; color: #e0e0e0 !important;
    }
    div[data-baseweb="popover"] { background-color: #111 !important; border: 1px solid #222 !important; }
    li[role="option"] { background-color: #111 !important; color: #e0e0e0 !important; }
    li[role="option"]:hover { background-color: #1e1e1e !important; }
    div[data-baseweb="tag"] { background-color: #1e1e1e !important; border: 1px solid #2e2e2e !important; color: #e0e0e0 !important; }
    [data-testid="stSlider"] [role="slider"] { background-color: #fff !important; }
    </style>""", unsafe_allow_html=True)
else:
    st.markdown("""
    <style>
    html, body, .stApp {
        background-color: #f0f5fc !important; color: #1a1a2e !important;
        --text-primary: #002D72; --text-muted: #6b7c9e; --border: #dde6f0;
        --surface: #ffffff; --accent: #002D72;
        --mlb-navy: #002D72; --mlb-red: #D50032; --mlb-surface: #ffffff; --mlb-border: #dde6f0;
    }
    div[data-baseweb="select"] > div {
        background-color: #ffffff !important; border: 1px solid #c8d8ea !important; color: #002D72 !important;
    }
    div[data-baseweb="select"] svg { fill: #002D72 !important; }
    .stTextInput input, .stNumberInput input {
        background-color: #ffffff !important; border: 1px solid #c8d8ea !important; color: #002D72 !important;
    }
    div[data-baseweb="popover"] { background-color: #fff !important; border: 1px solid #c8d8ea !important; }
    li[role="option"] { background-color: #fff !important; color: #002D72 !important; }
    li[role="option"]:hover { background-color: #f0f5fc !important; }
    div[data-baseweb="tag"] { background-color: #e0ecf8 !important; border: 1px solid #c8d8ea !important; color: #002D72 !important; }
    [data-testid="stSlider"] [role="slider"] { background-color: #002D72 !important; }
    .stTabs [aria-selected="true"] { color: #D50032 !important; border-bottom: 2px solid #D50032 !important; }
    .stTabs [data-baseweb="tab-list"] { border-bottom: 1px solid #dde6f0 !important; }
    </style>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# ══════════════  NBA  ════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
if sport == "🏀 NBA":

    tab_home, tab_stats, tab_opp, tab_sim, tab_fb, tab_pp, tab_disc = st.tabs([
        "Home", "Player Stats", "Opponent Breakdown",
        "Bet Simulation", "First Basket", "PrizePicks", "Disclaimer"
    ])

    # ── HOME ──────────────────────────────────────────────────────────────────
    with tab_home:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
        <p style='font-size:0.78rem;letter-spacing:0.14em;text-transform:uppercase;color:#444;'>
            Welcome to Konjure Analytics
        </p>
        <h2 style='color:#fff;font-size:2rem;font-weight:700;margin:0.25rem 0 1rem 0;'>
            NBA Prop Intelligence,<br>Powered by Data.
        </h2>""", unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns(5)
        features = [
            ("📊", "Player Stats", "Hit rates, rolling averages, and next-opponent predictions."),
            ("📈", "Opponent Breakdown", "Player performance split by every opponent faced."),
            ("🎯", "Bet Simulation", "Simulate flat-unit profit and loss across a season."),
            ("🕒", "First Basket", "Tip-off win rates and first basket frequency data."),
            ("🟣", "PrizePicks", "Today's live NBA prop lines from PrizePicks."),
        ]
        for col, (icon, title, desc) in zip([c1, c2, c3, c4, c5], features):
            with col:
                st.markdown(f"""
                <div class="feature-card">
                    <div class="feature-card-icon">{icon}</div>
                    <p class="feature-card-title">{title}</p>
                    <p class="feature-card-desc">{desc}</p>
                </div>""", unsafe_allow_html=True)

    # ── PLAYER STATS ──────────────────────────────────────────────────────────
    with tab_stats:
        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            section("Select Player")
            team_names = sorted([t["full_name"] for t in teams.get_teams()])
            selected_team = st.selectbox("Team", team_names, key="ps_team")
            team_code = get_team_abbreviation(selected_team)
            player_list = get_team_players(team_code)
            player_name = st.selectbox("Player", player_list, key="ps_player")
            if player_name:
                nba_player_card(player_name, team_code)
            section("Parameters")
            seasons = st.multiselect("Seasons", SEASONS, default=["2025-26"], key="ps_seasons")
            prop_type = st.selectbox("Prop Type", list(STAT_MAP.keys()), key="ps_prop")
            line_value = st.number_input("Prop Line", value=25.5, step=0.5, key="ps_line")
            rolling_window = st.slider("Rolling Window", 1, 10, 5, key="ps_roll")
            teammate_filter = st.text_input("Matchup Filter (optional)", key="ps_filter")

        with main_col:
            if player_name and seasons:
                player_id = get_player_id(player_name)
                if player_id:
                    with st.spinner("Loading..."):
                        df = get_gamelogs(player_id, tuple(seasons))
                    if df.empty:
                        st.warning("No game log data found.")
                    else:
                        if teammate_filter:
                            df = df[df["MATCHUP"].str.contains(teammate_filter, case=False, na=False)]
                        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
                        df["TARGET"] = df[STAT_MAP[prop_type]]
                        df["HIT"] = df["TARGET"] > line_value
                        df["MARGIN"] = df["TARGET"] - line_value
                        df["ROLLING_AVG"] = df["TARGET"].rolling(window=rolling_window).mean()

                        pp_df = get_prizepicks_lines()
                        pp_stat = PP_STAT_MAP.get(prop_type)
                        banner_cols = st.columns(3)
                        if not pp_df.empty and pp_stat:
                            match = pp_df[
                                (pp_df["player_name"].str.lower() == player_name.lower()) &
                                (pp_df["stat_type"] == pp_stat)
                            ]
                            if not match.empty:
                                pp_line = match.iloc[0]["line_score"]
                                df["PP_HIT"] = df["TARGET"] > pp_line
                                banner_cols[0].metric("PrizePicks Line", pp_line)
                                banner_cols[1].metric("Hit Rate vs PP", f"{df['PP_HIT'].mean():.1%}")
                        live_line = get_real_time_line(player_name, market=prop_type.lower())
                        if live_line:
                            df["LIVE_HIT"] = df["TARGET"] > live_line
                            banner_cols[2].metric("Live Line", live_line)

                        # ── Scout Report ──────────────────────────────────
                        _nxt = get_next_opponent(team_code)
                        _report = nba_scout_report(player_name, team_code, df,
                                                   _nxt, prop_type, rolling_window)
                        if _report:
                            section("Game Preview")
                            st.markdown(_scout_card_nba(_report), unsafe_allow_html=True)

                        # ── Analytical Breakdown ──────────────────────────
                        section("Analytical Breakdown")
                        ab_c = st.columns(5)
                        for _c, _s, _l in zip(ab_c,
                                ["PTS", "REB", "AST", "FG3M", "STL"],
                                ["PPG", "RPG", "APG", "3PM", "SPG"]):
                            if _s in df.columns:
                                _c.metric(_l, f"{df[_s].mean():.1f}")

                        section("Recent Form — Last 10 Games vs Season Avg")
                        _last10 = df.tail(10)
                        rf_c = st.columns(4)
                        for _c, _s, _l in zip(rf_c,
                                ["PTS", "REB", "AST", "FG3M"],
                                ["Points", "Rebounds", "Assists", "3PM"]):
                            if _s in df.columns:
                                _sa = df[_s].mean()
                                _ra = _last10[_s].mean()
                                _c.metric(_l, f"{_ra:.1f}", delta=f"{_ra - _sa:+.1f}")

                        section("Splits & Consistency")
                        df["IS_HOME"] = df["MATCHUP"].str.contains(r"vs\.", na=False)
                        _tgt_s = STAT_MAP[prop_type]
                        _home_a = df.loc[df["IS_HOME"], _tgt_s].mean()
                        _away_a = df.loc[~df["IS_HOME"], _tgt_s].mean()
                        _cv = (df[_tgt_s].std() / df[_tgt_s].mean() * 100
                               if df[_tgt_s].mean() > 0 else 0)
                        _cons = max(0, 100 - _cv)
                        _last5 = df.tail(5)
                        _above = (_last5[_tgt_s] > df[_tgt_s].mean()).sum()
                        _form = ("Hot (4-5 over avg)" if _above >= 4
                                 else "Cold (0-1 over avg)" if _above <= 1
                                 else "Neutral")
                        sp_c = st.columns(4)
                        sp_c[0].metric(f"Home {prop_type}", f"{_home_a:.1f}" if pd.notna(_home_a) else "—")
                        sp_c[1].metric(f"Away {prop_type}", f"{_away_a:.1f}" if pd.notna(_away_a) else "—")
                        sp_c[2].metric("Consistency", f"{_cons:.0f}/100")
                        sp_c[3].metric("Form (L5)", _form)

                        section("Performance Summary")
                        m1, m2, m3 = st.columns(3)
                        m1.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
                        m2.metric("Avg Margin", f"{df['MARGIN'].mean():.2f}")
                        m3.metric("Games", len(df))

                        section("Trend")
                        fig = px.line(df.reset_index(), y=["TARGET", "ROLLING_AVG"],
                                      labels={"value": prop_type, "index": "Game"},
                                      color_discrete_map={"TARGET": "#ffffff", "ROLLING_AVG": "#555"})
                        fig.add_hline(y=line_value, line_dash="dot", line_color="#333",
                                      annotation_text=f"Line {line_value}", annotation_font_color="#555")
                        st.plotly_chart(nba_fig(fig), use_container_width=True)

                        section("Predictive Stat Line")
                        if len(df) >= rolling_window:
                            pred = {c: df[c].rolling(rolling_window).mean().iloc[-1]
                                    for c in ["PTS", "REB", "AST", "FG3M"]}
                            c1, c2, c3, c4 = st.columns(4)
                            c1.metric("Points", f"{pred['PTS']:.1f}")
                            c2.metric("Rebounds", f"{pred['REB']:.1f}")
                            c3.metric("Assists", f"{pred['AST']:.1f}")
                            c4.metric("3PM", f"{pred['FG3M']:.1f}")
                        else:
                            st.warning(f"Not enough games for rolling window of {rolling_window}.")

                        next_opp_code = get_next_opponent(team_code)
                        if next_opp_code:
                            section(f"vs {next_opp_code} (Next Opponent)")
                            df_opp = df[df["OPPONENT"].str.upper() == next_opp_code]
                            if not df_opp.empty:
                                o1, o2, o3 = st.columns(3)
                                o1.metric("Avg Points", f"{df_opp['PTS'].mean():.1f}")
                                o2.metric("Avg Rebounds", f"{df_opp['REB'].mean():.1f}")
                                o3.metric("Avg Assists", f"{df_opp['AST'].mean():.1f}")
                                st.caption(f"Based on {len(df_opp)} game(s)")
                            else:
                                st.info(f"No historical data vs {next_opp_code}.")

    # ── OPPONENT BREAKDOWN ────────────────────────────────────────────────────
    with tab_opp:
        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            section("Select Player")
            team_names = sorted([t["full_name"] for t in teams.get_teams()])
            selected_team = st.selectbox("Team", team_names, key="ob_team")
            team_code = get_team_abbreviation(selected_team)
            player_list = get_team_players(team_code)
            player_name = st.selectbox("Player", player_list, key="ob_player")
            if player_name:
                nba_player_card(player_name, team_code)
            section("Parameters")
            seasons = st.multiselect("Seasons", SEASONS, default=["2025-26"], key="ob_seasons")
            line_value = st.number_input("Prop Line", value=25.5, step=0.5, key="ob_line")
            prop_type = st.selectbox("Stat Type", list(STAT_MAP.keys()), key="ob_prop")

        with main_col:
            if player_name and seasons:
                player_id = get_player_id(player_name)
                if player_id:
                    with st.spinner("Loading..."):
                        df = get_gamelogs(player_id, tuple(seasons))
                    if df.empty:
                        st.warning("No data found.")
                    else:
                        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
                        df["TARGET"] = df[STAT_MAP[prop_type]]
                        df["HIT"] = df["TARGET"] > line_value
                        df["MARGIN"] = df["TARGET"] - line_value
                        opp_stats = (
                            df.groupby("OPPONENT")[["HIT", "MARGIN", "TARGET"]]
                            .agg({"HIT": "mean", "MARGIN": "mean", "TARGET": ["mean", "count"]})
                        )
                        opp_stats.columns = ["Hit Rate", "Avg Margin", "Avg Stat", "Games"]
                        opp_stats = opp_stats.sort_values("Hit Rate", ascending=False)
                        section("Hit Rate by Opponent")
                        fig = px.bar(opp_stats.reset_index(), x="Hit Rate", y="OPPONENT",
                                     orientation="h", color="Hit Rate",
                                     color_continuous_scale=["#222", "#555", "#ffffff"],
                                     text=opp_stats["Games"].astype(str).values + " G")
                        fig.add_vline(x=0.5, line_dash="dot", line_color="#333")
                        fig.update_coloraxes(showscale=False)
                        st.plotly_chart(nba_fig(fig), use_container_width=True)
                        section("Data Table")
                        st.dataframe(opp_stats.style.format(
                            {"Hit Rate": "{:.1%}", "Avg Margin": "{:.2f}", "Avg Stat": "{:.1f}", "Games": "{:.0f}"}
                        ), use_container_width=True)

    # ── BET SIMULATION ────────────────────────────────────────────────────────
    with tab_sim:
        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            section("Select Player")
            team_names = sorted([t["full_name"] for t in teams.get_teams()])
            selected_team = st.selectbox("Team", team_names, key="sim_team")
            team_code = get_team_abbreviation(selected_team)
            player_list = get_team_players(team_code)
            player_name = st.selectbox("Player", player_list, key="sim_player")
            if player_name:
                nba_player_card(player_name, team_code)
            section("Parameters")
            seasons = st.multiselect("Seasons", SEASONS, default=["2025-26"], key="sim_seasons")
            line_value = st.number_input("Prop Line", value=25.5, step=0.5, key="sim_line")
            prop_type = st.selectbox("Stat Type", list(STAT_MAP.keys()), key="sim_prop")

        with main_col:
            if player_name and seasons:
                player_id = get_player_id(player_name)
                if player_id:
                    with st.spinner("Running simulation..."):
                        df = get_gamelogs(player_id, tuple(seasons))
                    if df.empty:
                        st.warning("No data found.")
                    else:
                        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
                        df["TARGET"] = df[STAT_MAP[prop_type]]
                        df["HIT"] = df["TARGET"] > line_value
                        df["CUMULATIVE_PROFIT"] = simulate_bets(df)
                        section("Results")
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Total Profit", f"{df['CUMULATIVE_PROFIT'].iloc[-1]:.0f} units")
                        col2.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
                        col3.metric("Games", len(df))
                        section("Cumulative P&L")
                        fig = px.line(df.reset_index(), y="CUMULATIVE_PROFIT",
                                      labels={"CUMULATIVE_PROFIT": "Units", "index": "Game"},
                                      color_discrete_sequence=["#ffffff"])
                        fig.add_hline(y=0, line_dash="dot", line_color="#333")
                        st.plotly_chart(nba_fig(fig), use_container_width=True)

    # ── FIRST BASKET ──────────────────────────────────────────────────────────
    with tab_fb:
        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            section("Select Team & Player")
            team_names_list = sorted([t["full_name"] for t in teams.get_teams()])
            selected_team_full = st.selectbox("Team", team_names_list, key="fb_team")
            team_code = get_team_abbreviation(selected_team_full)
            player_list = get_team_players(team_code)
            selected_player = st.selectbox("Player", player_list, key="fb_player")
            if selected_player:
                nba_player_card(selected_player, team_code)

        with main_col:
            with st.spinner("Loading first basket data..."):
                team_stats = get_first_basket_data()
            df_team = pd.DataFrame.from_dict(team_stats, orient="index")
            required_cols = {"First Basket", "Games", "Tip Wins"}
            if not required_cols.issubset(df_team.columns):
                st.warning(f"Missing columns: {required_cols - set(df_team.columns)}")
                st.dataframe(df_team)
            else:
                df_team["First Basket %"] = df_team["First Basket"] / df_team["Games"]
                df_team["Tip Win %"] = df_team["Tip Wins"] / df_team["Games"]
                team_data = team_stats.get(team_code, {"Games": 0, "First Basket": 0, "Tip Wins": 0})
                team_fb_rate = team_data["First Basket"] / team_data["Games"] if team_data["Games"] else 0
                team_tip_rate = team_data["Tip Wins"] / team_data["Games"] if team_data["Games"] else 0
                section(f"{team_code} Metrics")
                c1, c2, c3 = st.columns(3)
                c1.metric("First Basket Rate", f"{team_fb_rate:.1%}")
                c2.metric("Tip-Off Win Rate", f"{team_tip_rate:.1%}")
                c3.metric("Games Played", team_data["Games"])
                section("Tip Win % vs First Basket % — All Teams")
                fig_scatter = px.scatter(df_team, x="Tip Win %", y="First Basket %",
                                         text=df_team.index, trendline="ols",
                                         color_discrete_sequence=["#ffffff"])
                fig_scatter.update_traces(textfont_color="#666", marker_size=8)
                st.plotly_chart(nba_fig(fig_scatter), use_container_width=True)
                section("Full Team Table")
                st.dataframe(df_team.sort_values("First Basket %", ascending=False).style.format(
                    {"First Basket %": "{:.1%}", "Tip Win %": "{:.1%}"}), use_container_width=True)
                section("Today's Matchups")
                with st.spinner("Loading..."):
                    df_today = get_today_first_basket_stats()
                if not df_today.empty:
                    extracted = df_today["Matchup"].str.extract(r'(\w+)\s+vs\s+(\w+)')
                    teams_today = sorted(set(extracted[0].dropna().tolist() + extracted[1].dropna().tolist()))
                    if teams_today:
                        sel_today = st.selectbox("Filter by Team", teams_today, key="fb_today")
                        st.dataframe(df_today[df_today["Matchup"].str.contains(sel_today, na=False)],
                                     use_container_width=True)
                    else:
                        st.info("Could not parse team names from today's matchups.")
                else:
                    st.warning("No data available for today's matchups.")
                st.caption("Data: firstbasketstats.com")

    # ── PRIZEPICKS ────────────────────────────────────────────────────────────
    with tab_pp:
        with st.spinner("Loading PrizePicks projections..."):
            pp_df = get_prizepicks_lines()
        if pp_df.empty:
            st.warning("No PrizePicks data available right now.")
        else:
            f1, f2 = st.columns([1, 2])
            with f1:
                stat_options = ["All"] + sorted(pp_df["stat_type"].dropna().unique().tolist())
                selected_stat = st.selectbox("Stat Type", stat_options, key="pp_stat")
            with f2:
                search = st.text_input("Search Player", key="pp_search")
            filtered = pp_df.copy()
            if selected_stat != "All":
                filtered = filtered[filtered["stat_type"] == selected_stat]
            if search:
                filtered = filtered[filtered["player_name"].str.contains(search, case=False, na=False)]
            section(f"{len(filtered)} Projection(s)")
            st.dataframe(
                filtered[["player_name", "stat_type", "line_score", "status"]].rename(columns={
                    "player_name": "Player", "stat_type": "Stat", "line_score": "Line", "status": "Status"
                }).sort_values("Player"), use_container_width=True, hide_index=True)

    # ── DISCLAIMER ────────────────────────────────────────────────────────────
    with tab_disc:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
        <p style='font-size:0.68rem;letter-spacing:0.14em;text-transform:uppercase;color:#444;'>Disclaimer</p>
        <p style='color:#888;font-size:0.9rem;line-height:1.7;max-width:600px;'>
            This dashboard is for <strong style='color:#ccc;'>informational and entertainment purposes only.</strong>
            It does not constitute betting advice or guarantee outcomes.
            Konjure Analytics is not responsible for any financial decisions made based on this data.
        </p>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════  MLB  ════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
else:

    tab_mlb_home, tab_hitter, tab_pitcher, tab_vs_opp, tab_pp_mlb, tab_disc_mlb = st.tabs([
        "Home", "Hitter Analysis", "Pitcher Analysis", "vs Opponent", "PrizePicks", "Disclaimer"
    ])

    # ── MLB HOME ──────────────────────────────────────────────────────────────
    with tab_mlb_home:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
        <p style='font-size:0.75rem;letter-spacing:0.14em;text-transform:uppercase;color:#6b7c9e;'>
            Welcome to Konjure Analytics — MLB Edition
        </p>
        <h2 style='color:#002D72;font-size:2rem;font-weight:700;margin:0.25rem 0 0.5rem 0;'>
            MLB Predictive Analytics
        </h2>
        <p style='color:#6b7c9e;font-size:0.9rem;margin:0 0 1.5rem 0;'>
            Hitter and pitcher projections powered by the official MLB Stats API.
        </p>""", unsafe_allow_html=True)

        hc1, hc2, hc3 = st.columns(3)
        mlb_features = [
            ("⚾", "#D50032", "Hitter Analysis",
             "Rolling averages, opponent splits, and next-game projections for H, HR, RBI, K, BB."),
            ("🎯", "#002D72", "Pitcher Analysis",
             "Per-start K, IP, ERA, WHIP trends and opponent breakdowns for every pitcher."),
            ("📸", "#1a7d3e", "Player Photos",
             "Official MLB headshots pulled live from MLB's photo CDN for every active player."),
        ]
        for col, (icon, color, title, desc) in zip([hc1, hc2, hc3], mlb_features):
            with col:
                st.markdown(f"""
                <div style='background:#fff;border:1px solid #dde6f0;border-radius:10px;
                            padding:1.3rem 1.4rem;border-top:3px solid {color};'>
                    <div style='font-size:1.5rem;margin-bottom:0.5rem;'>{icon}</div>
                    <p style='font-size:0.76rem;font-weight:700;letter-spacing:0.1em;
                              text-transform:uppercase;color:{color};margin:0 0 0.4rem 0;'>{title}</p>
                    <p style='font-size:0.82rem;color:#6b7c9e;margin:0;line-height:1.5;'>{desc}</p>
                </div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
        <p style='font-size:0.68rem;color:#aab;letter-spacing:0.1em;text-transform:uppercase;'>
            Data Source: MLB Stats API (statsapi.mlb.com) &nbsp;&middot;&nbsp; Free &nbsp;&middot;&nbsp; No API Key Required
        </p>""", unsafe_allow_html=True)

    # ── HITTER ANALYSIS ───────────────────────────────────────────────────────
    with tab_hitter:
        mlb_teams = get_mlb_teams()
        h_roster, sel_hitter, sel_team = [], None, None
        h_seasons, h_window, h_stat, h_line = [MLB_SEASON], 10, "H", 0.5

        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            mlb_section("Select Hitter")
            if not mlb_teams:
                st.warning("Could not load MLB teams.")
            else:
                sel_team_name = st.selectbox("Team", [t["name"] for t in mlb_teams], key="h_team")
                sel_team = next(t for t in mlb_teams if t["name"] == sel_team_name)
                with st.spinner("Loading roster..."):
                    h_roster, _ = get_mlb_roster(sel_team["id"])
                if not h_roster:
                    st.warning("No hitters found.")
                else:
                    sel_h_name = st.selectbox("Hitter", [p["name"] for p in h_roster], key="h_player")
                    sel_hitter = next(p for p in h_roster if p["name"] == sel_h_name)
                    mlb_player_card(sel_hitter["name"], sel_hitter["pos"], sel_team["abbr"], sel_hitter["id"])
                    mlb_section("Parameters")
                    h_seasons = st.multiselect("Seasons", MLB_SEASONS, default=[MLB_SEASON], key="h_seasons")
                    h_window = st.slider("Rolling Window (games)", 3, 20, 10, key="h_roll")
                    h_stat = st.selectbox("Primary Stat", ["H", "HR", "RBI", "K", "BB"], key="h_stat")
                    h_line = st.number_input("Prop Line", value=0.5, step=0.5, key="h_line")

        with main_col:
            if mlb_teams and h_roster and sel_hitter:
                with st.spinner("Loading hitting logs..."):
                    h_df = get_mlb_hitting_logs(sel_hitter["id"], tuple(h_seasons) if h_seasons else (MLB_SEASON,))

                if h_df.empty:
                    st.warning("No hitting data found for this player this season.")
                else:
                    # ── Scout Report ──────────────────────────────────────
                    _h_report = mlb_hitter_scout_report(
                        sel_hitter["name"], sel_team["abbr"], h_df, sel_team["id"])
                    if _h_report:
                        mlb_section("Game Preview")
                        st.markdown(_scout_card_mlb(_h_report), unsafe_allow_html=True)

                    # ── Analytical Breakdown ──────────────────────────────
                    mlb_section("Analytical Breakdown")

                    _h_avg = h_df["AVG"].mean() if not h_df.empty else 0
                    _h_obp = h_df["OBP"].mean() if not h_df.empty else 0
                    _h_slg = h_df["SLG"].mean() if not h_df.empty else 0
                    _h_ops = _h_obp + _h_slg
                    _h_iso = _h_slg - _h_avg
                    sl_c = st.columns(5)
                    sl_c[0].metric("AVG", f"{_h_avg:.3f}")
                    sl_c[1].metric("OBP", f"{_h_obp:.3f}")
                    sl_c[2].metric("SLG", f"{_h_slg:.3f}")
                    sl_c[3].metric("OPS", f"{_h_ops:.3f}")
                    sl_c[4].metric("ISO", f"{_h_iso:.3f}")

                    mlb_section("Recent Form — Last 10 Games vs Season Avg")
                    _last10h = h_df.tail(10)
                    rfh_c = st.columns(4)
                    for _c, _s, _l, _inv in zip(rfh_c,
                            ["H", "HR", "RBI", "K"],
                            ["Hits", "HR", "RBI", "K"],
                            [False, False, False, True]):
                        _sa = h_df[_s].mean()
                        _ra = _last10h[_s].mean()
                        _c.metric(_l, f"{_ra:.2f}", delta=f"{_ra - _sa:+.2f}",
                                  delta_color="inverse" if _inv else "normal")

                    mlb_section("Plate Discipline & Consistency")
                    _h_pa = h_df["AB"].sum() + h_df["BB"].sum()
                    _h_kpct = h_df["K"].sum() / _h_pa * 100 if _h_pa > 0 else 0
                    _h_bbpct = h_df["BB"].sum() / _h_pa * 100 if _h_pa > 0 else 0
                    _h_cv = (h_df["H"].std() / h_df["H"].mean() * 100
                             if h_df["H"].mean() > 0 else 0)
                    _h_cons = max(0, 100 - _h_cv)
                    _last5h = h_df.tail(5)
                    _h_above = (_last5h["H"] > h_df["H"].mean()).sum()
                    _h_form = ("Hot (4-5 over avg)" if _h_above >= 4
                               else "Cold (0-1 over avg)" if _h_above <= 1
                               else "Neutral")
                    pd_c = st.columns(4)
                    pd_c[0].metric("K%", f"{_h_kpct:.1f}%")
                    pd_c[1].metric("BB%", f"{_h_bbpct:.1f}%")
                    pd_c[2].metric("Consistency", f"{_h_cons:.0f}/100")
                    pd_c[3].metric("Form (L5)", _h_form)

                    # Projections
                    proj = {c: rolling_projection(h_df, c, h_window) for c in ["H", "HR", "RBI", "K", "BB"]}

                    mlb_section("Season Projections (Rolling Avg)")
                    p1, p2, p3, p4, p5 = st.columns(5)
                    p1.metric("Hits / Game", f"{proj['H']:.2f}")
                    p2.metric("HR / Game", f"{proj['HR']:.2f}")
                    p3.metric("RBI / Game", f"{proj['RBI']:.2f}")
                    p4.metric("K / Game", f"{proj['K']:.2f}")
                    p5.metric("BB / Game", f"{proj['BB']:.2f}")

                    # Hit rate vs line
                    h_df["HIT_PROP"] = h_df[h_stat] > h_line
                    hit_rate = h_df["HIT_PROP"].mean()
                    mlb_section(f"Hit Rate — {h_stat} Over {h_line}")
                    r1, r2, r3 = st.columns(3)
                    r1.metric("Hit Rate", f"{hit_rate:.1%}")
                    r2.metric(f"Avg {h_stat}", f"{h_df[h_stat].mean():.2f}")
                    r3.metric("Games", len(h_df))

                    # Rolling trend chart
                    h_df["ROLLING"] = h_df[h_stat].rolling(h_window).mean()
                    mlb_section(f"{h_stat} Trend — Last {len(h_df)} Games")
                    fig_h = px.line(
                        h_df.reset_index(),
                        x="date", y=[h_stat, "ROLLING"],
                        labels={"value": h_stat, "date": "Date"},
                        color_discrete_map={h_stat: "#002D72", "ROLLING": "#D50032"},
                    )
                    fig_h.add_hline(y=h_line, line_dash="dot", line_color="#bbb",
                                    annotation_text=f"Line {h_line}", annotation_font_color="#aaa")
                    st.plotly_chart(mlb_fig(fig_h), use_container_width=True)

                    # Opponent breakdown
                    if "opponent" in h_df.columns and h_df["opponent"].notna().any():
                        mlb_section("Hit Rate by Opponent")
                        opp_h = (h_df.groupby("opponent")[[h_stat]]
                                 .agg(mean=(h_stat, "mean"), count=(h_stat, "count"))
                                 .rename(columns={"mean": f"Avg {h_stat}", "count": "Games"})
                                 .sort_values(f"Avg {h_stat}", ascending=False))
                        fig_opp = px.bar(
                            opp_h.reset_index(),
                            x=f"Avg {h_stat}", y="opponent", orientation="h",
                            color=f"Avg {h_stat}",
                            color_continuous_scale=["#e8f0fa", "#002D72"],
                            text=opp_h["Games"].astype(str).values + "G",
                        )
                        fig_opp.add_vline(x=h_df[h_stat].mean(), line_dash="dot", line_color="#D50032")
                        fig_opp.update_coloraxes(showscale=False)
                        st.plotly_chart(mlb_fig(fig_opp), use_container_width=True)

                    # Recent game log table
                    mlb_section("Recent Game Log")
                    display_cols = ["date", "opponent", "AB", "H", "HR", "RBI", "BB", "K", "AVG", "OBP", "SLG"]
                    st.dataframe(
                        h_df[display_cols].tail(20).sort_values("date", ascending=False).style.format({
                            "date": lambda x: x.strftime("%b %d"),
                            "AVG": "{:.3f}", "OBP": "{:.3f}", "SLG": "{:.3f}",
                        }),
                        use_container_width=True, hide_index=True,
                    )

    # ── PITCHER ANALYSIS ──────────────────────────────────────────────────────
    with tab_pitcher:
        mlb_teams_p = get_mlb_teams()
        p_roster, sel_pitcher, sel_team_p = [], None, None
        p_seasons, p_window, p_stat, p_line = [MLB_SEASON], 5, "K", 5.5

        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            mlb_section("Select Pitcher")
            if not mlb_teams_p:
                st.warning("Could not load MLB teams.")
            else:
                sel_team_name_p = st.selectbox("Team", [t["name"] for t in mlb_teams_p], key="p_team")
                sel_team_p = next(t for t in mlb_teams_p if t["name"] == sel_team_name_p)
                with st.spinner("Loading roster..."):
                    _, p_roster = get_mlb_roster(sel_team_p["id"])
                if not p_roster:
                    st.warning("No pitchers found.")
                else:
                    sel_p_name = st.selectbox("Pitcher", [p["name"] for p in p_roster], key="p_player")
                    sel_pitcher = next(p for p in p_roster if p["name"] == sel_p_name)
                    mlb_player_card(sel_pitcher["name"], sel_pitcher["pos"], sel_team_p["abbr"], sel_pitcher["id"])
                    mlb_section("Parameters")
                    p_seasons = st.multiselect("Seasons", MLB_SEASONS, default=[MLB_SEASON], key="p_seasons")
                    p_window = st.slider("Rolling Window (starts)", 3, 15, 5, key="p_roll")
                    p_stat = st.selectbox("Primary Stat", ["K", "IP", "ER", "BB", "H", "HR"], key="p_stat")
                    p_line = st.number_input("Prop Line", value=5.5, step=0.5, key="p_line")

        with main_col:
            if mlb_teams_p and p_roster and sel_pitcher:
                with st.spinner("Loading pitching logs..."):
                    p_df = get_mlb_pitching_logs(sel_pitcher["id"], tuple(p_seasons) if p_seasons else (MLB_SEASON,))

                if p_df.empty:
                    st.warning("No pitching data found for this player this season.")
                else:
                    # ── Scout Report ──────────────────────────────────────
                    _p_report = mlb_pitcher_scout_report(
                        sel_pitcher["name"], sel_team_p["abbr"], p_df, sel_team_p["id"])
                    if _p_report:
                        mlb_section("Game Preview")
                        st.markdown(_scout_card_mlb(_p_report), unsafe_allow_html=True)

                    # ── Analytical Breakdown ──────────────────────────────
                    mlb_section("Analytical Breakdown")

                    _p_ip = p_df["IP"].sum()
                    _p_k = p_df["K"].sum()
                    _p_bb = p_df["BB"].sum()
                    _p_era = p_df["ERA"].mean()
                    _p_whip = p_df["WHIP"].mean()
                    _p_k9 = _p_k / _p_ip * 9 if _p_ip > 0 else 0
                    _p_bb9 = _p_bb / _p_ip * 9 if _p_ip > 0 else 0
                    _p_kbb = _p_k / _p_bb if _p_bb > 0 else 0
                    pm_c = st.columns(5)
                    pm_c[0].metric("ERA", f"{_p_era:.2f}")
                    pm_c[1].metric("WHIP", f"{_p_whip:.2f}")
                    pm_c[2].metric("K/9", f"{_p_k9:.1f}")
                    pm_c[3].metric("BB/9", f"{_p_bb9:.1f}")
                    pm_c[4].metric("K/BB", f"{_p_kbb:.2f}")

                    mlb_section("Recent Form — Last 5 Starts vs Season Avg")
                    _last5p = p_df.tail(5)
                    rfp_c = st.columns(4)
                    for _c, _s, _l, _inv in zip(rfp_c,
                            ["K", "IP", "ER", "BB"],
                            ["K", "IP", "ER", "BB"],
                            [False, False, True, True]):
                        _sa = p_df[_s].mean()
                        _ra = _last5p[_s].mean()
                        _c.metric(_l, f"{_ra:.1f}", delta=f"{_ra - _sa:+.1f}",
                                  delta_color="inverse" if _inv else "normal")

                    mlb_section("Quality & Consistency")
                    _p_qs = ((p_df["IP"] >= 6) & (p_df["ER"] <= 3)).mean() * 100
                    _p_cv = (p_df["K"].std() / p_df["K"].mean() * 100
                             if p_df["K"].mean() > 0 else 0)
                    _p_cons = max(0, 100 - _p_cv)
                    _p_above = (_last5p["K"] > p_df["K"].mean()).sum()
                    _p_form = ("Hot (4-5 over avg)" if _p_above >= 4
                               else "Cold (0-1 over avg)" if _p_above <= 1
                               else "Neutral")
                    qc_c = st.columns(4)
                    qc_c[0].metric("Quality Start %", f"{_p_qs:.0f}%")
                    qc_c[1].metric("Avg IP/Start", f"{p_df['IP'].mean():.1f}")
                    qc_c[2].metric("K Consistency", f"{_p_cons:.0f}/100")
                    qc_c[3].metric("Form (L5)", _p_form)

                    proj_p = {c: rolling_projection(p_df, c, p_window) for c in ["K", "IP", "ER", "BB", "ERA", "WHIP", "K9"]}

                    mlb_section("Season Projections (Rolling Avg)")
                    pp1, pp2, pp3, pp4 = st.columns(4)
                    pp1.metric("K / Start", f"{proj_p['K']:.1f}")
                    pp2.metric("IP / Start", f"{proj_p['IP']:.1f}")
                    pp3.metric("ERA", f"{proj_p['ERA']:.2f}")
                    pp4.metric("WHIP", f"{proj_p['WHIP']:.2f}")
                    pp5, pp6 = st.columns(2)
                    pp5.metric("K/9", f"{proj_p['K9']:.1f}")
                    pp6.metric("BB / Start", f"{proj_p['BB']:.1f}")

                    # Hit rate vs line
                    p_df["HIT_PROP"] = p_df[p_stat] > p_line
                    p_hit_rate = p_df["HIT_PROP"].mean()
                    mlb_section(f"Hit Rate — {p_stat} Over {p_line}")
                    pr1, pr2, pr3 = st.columns(3)
                    pr1.metric("Hit Rate", f"{p_hit_rate:.1%}")
                    pr2.metric(f"Avg {p_stat}", f"{p_df[p_stat].mean():.2f}")
                    pr3.metric("Starts", len(p_df))

                    # Rolling trend
                    p_df["ROLLING"] = p_df[p_stat].rolling(p_window).mean()
                    mlb_section(f"{p_stat} Trend — Last {len(p_df)} Starts")
                    fig_p = px.line(
                        p_df.reset_index(),
                        x="date", y=[p_stat, "ROLLING"],
                        labels={"value": p_stat, "date": "Date"},
                        color_discrete_map={p_stat: "#002D72", "ROLLING": "#D50032"},
                    )
                    fig_p.add_hline(y=p_line, line_dash="dot", line_color="#bbb",
                                    annotation_text=f"Line {p_line}", annotation_font_color="#aaa")
                    st.plotly_chart(mlb_fig(fig_p), use_container_width=True)

                    # Strikeout distribution
                    mlb_section("K Distribution")
                    fig_hist = px.histogram(
                        p_df, x="K", nbins=12,
                        color_discrete_sequence=["#002D72"],
                    )
                    fig_hist.add_vline(x=p_line, line_dash="dot", line_color="#D50032",
                                       annotation_text=f"Line {p_line}", annotation_font_color="#D50032")
                    st.plotly_chart(mlb_fig(fig_hist), use_container_width=True)

                    # Opponent breakdown
                    if "opponent" in p_df.columns and p_df["opponent"].notna().any():
                        mlb_section("Stats by Opponent")
                        opp_p = (p_df.groupby("opponent")[["K", "IP", "ER", "ERA"]]
                                 .mean().round(2).sort_values("K", ascending=False))
                        st.dataframe(opp_p.style.format("{:.2f}"), use_container_width=True)

                    # Recent starts table
                    mlb_section("Recent Starts")
                    pcols = ["date", "opponent", "IP", "H", "ER", "BB", "K", "HR", "ERA", "WHIP"]
                    st.dataframe(
                        p_df[pcols].tail(15).sort_values("date", ascending=False).style.format({
                            "date": lambda x: x.strftime("%b %d"),
                            "ERA": "{:.2f}", "WHIP": "{:.2f}", "IP": "{:.1f}",
                        }),
                        use_container_width=True, hide_index=True,
                    )

    # ── VS OPPONENT ───────────────────────────────────────────────────────────
    with tab_vs_opp:
        with st.spinner("Loading today's games..."):
            today_games = get_today_mlb_games()

        if not today_games:
            st.info("No MLB games scheduled for today.")
        else:
            vo_ctrl, vo_main = st.columns([1, 2.8])
            vo_roster, vo_player, vo_team, vo_opp_abbr = [], None, None, ""
            vo_player_type = "Hitter"

            with vo_ctrl:
                mlb_section("Today's Games")
                vo_seasons = st.multiselect("Seasons", MLB_SEASONS, default=[MLB_SEASON], key="vo_seasons")
                game_labels = [g["label"] for g in today_games]
                sel_game_label = st.selectbox("Select Game", game_labels, key="vo_game")
                sel_game = next(g for g in today_games if g["label"] == sel_game_label)

                team_choice = st.radio(
                    "Analyze Team",
                    [sel_game["away_name"], sel_game["home_name"]],
                    key="vo_team_side",
                )
                if team_choice == sel_game["away_name"]:
                    vo_team = {"id": sel_game["away_id"], "name": sel_game["away_name"], "abbr": sel_game["away_abbr"]}
                    vo_opp_abbr = sel_game["home_abbr"]
                else:
                    vo_team = {"id": sel_game["home_id"], "name": sel_game["home_name"], "abbr": sel_game["home_abbr"]}
                    vo_opp_abbr = sel_game["away_abbr"]

                mlb_section("Player")
                vo_player_type = st.radio("Type", ["Hitter", "Pitcher"], key="vo_ptype", horizontal=True)
                with st.spinner("Loading roster..."):
                    h_r, p_r = get_mlb_roster(vo_team["id"])
                vo_roster = h_r if vo_player_type == "Hitter" else p_r

                if not vo_roster:
                    st.warning("No players found.")
                else:
                    vo_name = st.selectbox("Player", [p["name"] for p in vo_roster], key="vo_player")
                    vo_player = next(p for p in vo_roster if p["name"] == vo_name)
                    mlb_player_card(vo_player["name"], vo_player["pos"], vo_team["abbr"], vo_player["id"])
                    mlb_section("Parameters")
                    vo_window = st.slider("Rolling Window", 3, 20, 10, key="vo_window")

            with vo_main:
                if vo_player and vo_team:
                    _vo_seasons = tuple(vo_seasons) if vo_seasons else (MLB_SEASON,)
                    with st.spinner("Loading stats..."):
                        if vo_player_type == "Hitter":
                            vo_df = get_mlb_hitting_logs(vo_player["id"], _vo_seasons)
                        else:
                            vo_df = get_mlb_pitching_logs(vo_player["id"], _vo_seasons)

                    if vo_df.empty:
                        st.warning("No stats found for this player this season.")
                    else:
                        opp_display = vo_opp_abbr or "opponent"

                        # ── Prediction card ──────────────────────────────────
                        mlb_section(f"Prediction vs {opp_display} — Today")
                        if vo_player_type == "Hitter":
                            stat_keys = ["H", "HR", "RBI", "K", "BB"]
                        else:
                            stat_keys = ["K", "IP", "ER", "BB", "ERA"]

                        proj = {c: rolling_projection(vo_df, c, vo_window) for c in stat_keys}
                        vs_opp_df = vo_df[vo_df["opponent"].str.upper() == vo_opp_abbr.upper()] if vo_opp_abbr else pd.DataFrame()

                        # Season projection row
                        st.markdown("""
                        <p style='font-size:0.68rem;color:#6b7c9e;letter-spacing:0.1em;
                                  text-transform:uppercase;margin:0 0 0.5rem 0;'>
                            Season Rolling Projection
                        </p>""", unsafe_allow_html=True)
                        pcols = st.columns(len(stat_keys))
                        for col, key in zip(pcols, stat_keys):
                            fmt = ".2f" if key in ("ERA", "WHIP") else ".1f"
                            col.metric(key, f"{proj[key]:{fmt}}")

                        # vs this opponent row
                        if not vs_opp_df.empty:
                            st.markdown(f"""
                            <p style='font-size:0.68rem;color:#D50032;letter-spacing:0.1em;
                                      text-transform:uppercase;margin:1rem 0 0.5rem 0;'>
                                Historical vs {opp_display} ({len(vs_opp_df)} game(s))
                            </p>""", unsafe_allow_html=True)
                            vcols = st.columns(len(stat_keys))
                            for col, key in zip(vcols, stat_keys):
                                if key in vs_opp_df.columns:
                                    avg = vs_opp_df[key].mean()
                                    season_avg = vo_df[key].mean()
                                    delta = avg - season_avg
                                    fmt = ".2f" if key in ("ERA", "WHIP") else ".1f"
                                    col.metric(f"vs {opp_display}", f"{avg:{fmt}}",
                                               delta=f"{delta:+.2f} vs season avg",
                                               delta_color="inverse" if key in ("K", "ER", "ERA", "WHIP", "BB") else "normal")
                        else:
                            st.info(f"No historical data vs {opp_display} in {MLB_SEASON}.")

                        # ── Trend vs opponent chart ───────────────────────────
                        primary = "H" if vo_player_type == "Hitter" else "K"
                        vo_df["ROLLING"] = vo_df[primary].rolling(vo_window).mean()
                        opp_mask = vo_df["opponent"].str.upper() == vo_opp_abbr.upper() if vo_opp_abbr else pd.Series(False, index=vo_df.index)

                        mlb_section(f"{primary} Trend with {opp_display} Games Highlighted")
                        fig_vo = px.line(vo_df.reset_index(), x="date", y=primary,
                                         labels={primary: primary, "date": "Date"},
                                         color_discrete_sequence=["#ccd8ea"])
                        fig_vo.add_scatter(x=vo_df["date"], y=vo_df["ROLLING"],
                                           mode="lines", name="Rolling Avg",
                                           line=dict(color="#002D72", width=2))
                        if opp_mask.any():
                            fig_vo.add_scatter(
                                x=vo_df.loc[opp_mask, "date"],
                                y=vo_df.loc[opp_mask, primary],
                                mode="markers", name=f"vs {opp_display}",
                                marker=dict(color="#D50032", size=10, symbol="diamond"),
                            )
                        st.plotly_chart(mlb_fig(fig_vo), use_container_width=True)

                        # ── Recent game log ────────────────────────────────────
                        mlb_section("Recent Game Log")
                        log_cols = (["date", "opponent", "AB", "H", "HR", "RBI", "BB", "K", "AVG"]
                                    if vo_player_type == "Hitter"
                                    else ["date", "opponent", "IP", "H", "ER", "BB", "K", "ERA", "WHIP"])
                        available = [c for c in log_cols if c in vo_df.columns]
                        fmt_map = {"AVG": "{:.3f}", "ERA": "{:.2f}", "WHIP": "{:.2f}", "IP": "{:.1f}"}
                        display_fmt = {k: v for k, v in fmt_map.items() if k in available}
                        st.dataframe(
                            vo_df[available].tail(15).sort_values("date", ascending=False)
                            .style.format({"date": lambda x: x.strftime("%b %d"), **display_fmt}),
                            use_container_width=True, hide_index=True,
                        )

    # ── MLB PRIZEPICKS ────────────────────────────────────────────────────────
    with tab_pp_mlb:
        with st.spinner("Loading PrizePicks MLB projections..."):
            mlb_pp_df = get_prizepicks_lines(league_id=2)

        if mlb_pp_df.empty:
            st.info("No MLB PrizePicks lines available right now. Lines are typically posted on game days.")
        else:
            pf1, pf2 = st.columns([1, 2])
            with pf1:
                stat_opts = ["All"] + sorted(mlb_pp_df["stat_type"].dropna().unique().tolist())
                sel_stat = st.selectbox("Stat Type", stat_opts, key="mlb_pp_stat")
            with pf2:
                search_mlb = st.text_input("Search Player", key="mlb_pp_search")

            filt = mlb_pp_df.copy()
            if sel_stat != "All":
                filt = filt[filt["stat_type"] == sel_stat]
            if search_mlb:
                filt = filt[filt["player_name"].str.contains(search_mlb, case=False, na=False)]

            mlb_section(f"{len(filt)} Projection(s)")
            st.dataframe(
                filt[["player_name", "stat_type", "line_score", "status"]].rename(columns={
                    "player_name": "Player", "stat_type": "Stat",
                    "line_score": "Line", "status": "Status",
                }).sort_values("Player"),
                use_container_width=True, hide_index=True,
            )

    # ── MLB DISCLAIMER ────────────────────────────────────────────────────────
    with tab_disc_mlb:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
        <p style='font-size:0.68rem;letter-spacing:0.14em;text-transform:uppercase;color:#6b7c9e;'>Disclaimer</p>
        <p style='color:#6b7c9e;font-size:0.9rem;line-height:1.7;max-width:600px;'>
            This dashboard is for <strong style='color:#002D72;'>informational and entertainment purposes only.</strong>
            It does not constitute betting advice or guarantee outcomes.
            Konjure Analytics is not responsible for any financial decisions made based on this data.
            MLB data is sourced from the official MLB Stats API.
        </p>""", unsafe_allow_html=True)
