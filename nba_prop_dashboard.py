# -*- coding: utf-8 -*-
"""
Konjure Analytics — NBA Prop Betting Dashboard
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
from nba_api.stats.static import teams, players
from nba_api.stats.endpoints import playergamelog, commonteamroster
from datetime import datetime

# ─── Page config (must be first Streamlit call) ────────────────────────────
st.set_page_config(
    page_title="Konjure Analytics",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── CSS ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
html, body, .stApp {
    background-color: #090909;
    color: #e0e0e0;
    font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif;
}
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1.5rem !important; max-width: 100% !important; }

/* ── Header ── */
.konjure-header {
    padding: 1rem 0 1.25rem 0;
    border-bottom: 1px solid #1c1c1c;
    margin-bottom: 0.5rem;
}
.konjure-title {
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #ffffff;
    margin: 0 0 0.15rem 0;
}
.konjure-sub {
    font-size: 0.72rem;
    color: #444;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin: 0;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    background-color: transparent !important;
    border-bottom: 1px solid #1c1c1c;
}
.stTabs [data-baseweb="tab"] {
    color: #444 !important;
    background-color: transparent !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    padding: 0.65rem 1.2rem !important;
    font-size: 0.72rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
}
.stTabs [aria-selected="true"] {
    color: #ffffff !important;
    border-bottom: 2px solid #ffffff !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.5rem; }

/* ── Selectbox / Input ── */
div[data-baseweb="select"] > div {
    background-color: #101010 !important;
    border: 1px solid #222 !important;
    border-radius: 4px !important;
    color: #e0e0e0 !important;
}
div[data-baseweb="select"] svg { fill: #555 !important; }
.stTextInput input, .stNumberInput input {
    background-color: #101010 !important;
    border: 1px solid #222 !important;
    color: #e0e0e0 !important;
    border-radius: 4px !important;
}
div[data-baseweb="popover"] { background-color: #111 !important; border: 1px solid #222 !important; }
li[role="option"] { background-color: #111 !important; color: #e0e0e0 !important; }
li[role="option"]:hover { background-color: #1e1e1e !important; }

/* ── Multiselect tags ── */
div[data-baseweb="tag"] {
    background-color: #1e1e1e !important;
    border: 1px solid #2e2e2e !important;
    color: #e0e0e0 !important;
}

/* ── Metrics ── */
[data-testid="metric-container"] {
    background-color: #0f0f0f;
    border: 1px solid #1c1c1c;
    border-radius: 6px;
    padding: 1rem 1.25rem !important;
}
[data-testid="metric-container"] label {
    color: #555 !important;
    font-size: 0.68rem !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
}
[data-testid="metric-container"] [data-testid="metric-value"] {
    color: #ffffff !important;
    font-size: 1.5rem !important;
    font-weight: 600 !important;
}

/* ── Player card ── */
.player-card {
    background-color: #0f0f0f;
    border: 1px solid #1c1c1c;
    border-radius: 8px;
    padding: 1rem 1.25rem;
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1.25rem;
}
.player-card img {
    width: 72px;
    height: 72px;
    object-fit: cover;
    border-radius: 50%;
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
}
.player-card-name {
    font-size: 1.05rem;
    font-weight: 600;
    color: #ffffff;
    margin: 0 0 0.2rem 0;
    letter-spacing: 0.03em;
}
.player-card-team {
    font-size: 0.68rem;
    color: #555;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin: 0;
}

/* ── Section heading ── */
.section-heading {
    font-size: 0.68rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #444;
    margin: 1.5rem 0 0.6rem 0;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid #1c1c1c;
}

/* ── Alerts ── */
.stAlert {
    background-color: #0f0f0f !important;
    border: 1px solid #222 !important;
    color: #888 !important;
    border-radius: 6px;
}

/* ── DataFrames ── */
.stDataFrame { border: 1px solid #1c1c1c !important; border-radius: 6px !important; }
[data-testid="stDataFrameResizable"] { background-color: #0f0f0f !important; }

/* ── Divider ── */
hr { border-color: #1c1c1c !important; }

/* ── Slider ── */
[data-testid="stSlider"] [role="slider"] { background-color: #fff !important; }

/* ── Home feature cards ── */
.feature-card {
    background-color: #0f0f0f;
    border: 1px solid #1c1c1c;
    border-radius: 8px;
    padding: 1.25rem 1.5rem;
    height: 100%;
}
.feature-card-icon { font-size: 1.4rem; margin-bottom: 0.5rem; }
.feature-card-title {
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #ffffff;
    margin: 0 0 0.4rem 0;
}
.feature-card-desc { font-size: 0.82rem; color: #555; margin: 0; line-height: 1.5; }
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

# ─── Data helpers ──────────────────────────────────────────────────────────
def get_team_abbreviation(team_name):
    team = next((t for t in teams.get_teams() if t["full_name"] == team_name), None)
    return team["abbreviation"].upper() if team else None

def get_team_id(team_abbr):
    team = next((t for t in teams.get_teams() if t["abbreviation"].upper() == team_abbr.upper()), None)
    return team["id"] if team else None

@st.cache_data(ttl=3600)
def get_team_players(team_abbr):
    team_id = get_team_id(team_abbr)
    if team_id:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id).get_data_frames()[0]
        return roster["PLAYER"].tolist()
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
def get_prizepicks_lines():
    try:
        url = "https://api.prizepicks.com/projections?league_id=7&per_page=250&single_stat=true"
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
        api_key = st.secrets.get("ODDS_API_KEY", "132d657e987feea06b1b91a21116d4a0")
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
    except Exception as e:
        st.warning(f"Could not fetch live line: {e}")
    return None

def simulate_bets(df):
    bet_result = df["HIT"].apply(lambda x: 1 if x else -1)
    cumulative = bet_result.cumsum()
    return pd.Series(cumulative.to_list(), index=df.index, name="CUMULATIVE_PROFIT")

@st.cache_data(ttl=3600)
def get_first_basket_data():
    url = "https://firstbasketstats.com/2024-2025-first-basket-stats-data"
    fallback_data = {
        "BOS": {"Games": 12, "First Basket": 7, "Tip Wins": 8},
        "DEN": {"Games": 13, "First Basket": 9, "Tip Wins": 10},
        "LAL": {"Games": 14, "First Basket": 6, "Tip Wins": 5}
    }
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table", {"id": "team-first-basket"})
        if not table:
            return fallback_data
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
                    "Tip Wins": int(cells[3].text.strip())
                }
            except ValueError:
                continue
        return data if data else fallback_data
    except Exception:
        return fallback_data

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
                "Matchup": cells[0].text.strip(),
                "Tip Winner": cells[1].text.strip(),
                "Likely Jumper": cells[2].text.strip(),
                "First Basket": cells[3].text.strip(),
                "Shot Type": cells[4].text.strip(),
                "Position": cells[5].text.strip()
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.warning(f"Failed to load today's first basket data: {e}")
        return pd.DataFrame()

# ─── Constants ─────────────────────────────────────────────────────────────
STAT_MAP = {
    "Points": "PTS",
    "Rebounds": "REB",
    "Assists": "AST",
    "PRA": "PRA",
    "3PM": "FG3M"
}
PP_STAT_MAP = {
    "Points": "Points",
    "Rebounds": "Rebounds",
    "Assists": "Assists",
    "PRA": "Pts+Rebs+Asts",
    "3PM": "3-PT Made",
}
SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]
CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="#0f0f0f",
    font_color="#888",
    title_font_color="#e0e0e0",
    xaxis=dict(gridcolor="#1c1c1c", linecolor="#1c1c1c", zerolinecolor="#1c1c1c"),
    yaxis=dict(gridcolor="#1c1c1c", linecolor="#1c1c1c", zerolinecolor="#1c1c1c"),
    legend=dict(bgcolor="rgba(0,0,0,0)", font_color="#888"),
    margin=dict(t=40, b=20, l=0, r=0),
)

# ─── UI helper ─────────────────────────────────────────────────────────────
def player_card(player_name, team_code):
    pid = get_player_id(player_name)
    headshot = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png" if pid else ""
    st.markdown(f"""
    <div class="player-card">
        <img src="{headshot}" onerror="this.style.display='none'" />
        <div>
            <p class="player-card-name">{player_name}</p>
            <p class="player-card-team">{team_code}</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

def section(label):
    st.markdown(f'<p class="section-heading">{label}</p>', unsafe_allow_html=True)

def dark_fig(fig):
    fig.update_layout(**CHART_LAYOUT)
    return fig

# ─── Header ────────────────────────────────────────────────────────────────
st.markdown("""
<div class="konjure-header">
    <p class="konjure-title">Konjure Analytics</p>
    <p class="konjure-sub">NBA Prop Intelligence &nbsp;·&nbsp; Powered by Data</p>
</div>
""", unsafe_allow_html=True)

# ─── Navigation tabs ───────────────────────────────────────────────────────
tab_home, tab_stats, tab_opp, tab_sim, tab_fb, tab_pp, tab_disc = st.tabs([
    "Home", "Player Stats", "Opponent Breakdown",
    "Bet Simulation", "First Basket", "PrizePicks", "Disclaimer"
])

# ═══════════════════════════════════════════════════════════════════════════
# HOME
# ═══════════════════════════════════════════════════════════════════════════
with tab_home:
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""
    <p style='font-size:0.78rem;letter-spacing:0.14em;text-transform:uppercase;color:#444;'>
        Welcome to Konjure Analytics
    </p>
    <h2 style='color:#fff;font-size:2rem;font-weight:700;margin:0.25rem 0 1rem 0;'>
        NBA Prop Intelligence,<br>Powered by Data.
    </h2>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4, c5 = st.columns(5)
    features = [
        ("📊", "Player Stats", "Hit rates, rolling averages, and next-opponent predictions for any player."),
        ("📈", "Opponent Breakdown", "See how a player performs split by every opponent they've faced."),
        ("🎯", "Bet Simulation", "Simulate flat-unit profit and loss across a full season."),
        ("🕒", "First Basket", "Team tip-off win rates and first basket frequency data."),
        ("🟣", "PrizePicks", "Today's live NBA prop lines pulled directly from PrizePicks."),
    ]
    for col, (icon, title, desc) in zip([c1, c2, c3, c4, c5], features):
        with col:
            st.markdown(f"""
            <div class="feature-card">
                <div class="feature-card-icon">{icon}</div>
                <p class="feature-card-title">{title}</p>
                <p class="feature-card-desc">{desc}</p>
            </div>
            """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════
# PLAYER STATS
# ═══════════════════════════════════════════════════════════════════════════
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
            player_card(player_name, team_code)

        section("Parameters")
        seasons = st.multiselect("Seasons", SEASONS, default=["2024-25"], key="ps_seasons")
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
                    st.warning("No game log data found for the selected seasons.")
                else:
                    if teammate_filter:
                        df = df[df["MATCHUP"].str.contains(teammate_filter, case=False, na=False)]

                    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
                    df["TARGET"] = df[STAT_MAP[prop_type]]
                    df["HIT"] = df["TARGET"] > line_value
                    df["MARGIN"] = df["TARGET"] - line_value
                    df["ROLLING_AVG"] = df["TARGET"].rolling(window=rolling_window).mean()

                    # Lines banner
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

                    section("Performance Summary")
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
                    m2.metric("Avg Margin", f"{df['MARGIN'].mean():.2f}")
                    m3.metric("Games", len(df))

                    section("Trend")
                    fig = px.line(
                        df.reset_index(),
                        y=["TARGET", "ROLLING_AVG"],
                        labels={"value": prop_type, "index": "Game"},
                        color_discrete_map={"TARGET": "#ffffff", "ROLLING_AVG": "#555555"},
                    )
                    fig.add_hline(y=line_value, line_dash="dot", line_color="#333",
                                  annotation_text=f"Line {line_value}",
                                  annotation_font_color="#555")
                    st.plotly_chart(dark_fig(fig), use_container_width=True)

                    section("Predictive Stat Line")
                    if len(df) >= rolling_window:
                        pred = {
                            "PTS": df["PTS"].rolling(window=rolling_window).mean().iloc[-1],
                            "REB": df["REB"].rolling(window=rolling_window).mean().iloc[-1],
                            "AST": df["AST"].rolling(window=rolling_window).mean().iloc[-1],
                            "FG3M": df["FG3M"].rolling(window=rolling_window).mean().iloc[-1],
                        }
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Points", f"{pred['PTS']:.1f}")
                        c2.metric("Rebounds", f"{pred['REB']:.1f}")
                        c3.metric("Assists", f"{pred['AST']:.1f}")
                        c4.metric("3PM", f"{pred['FG3M']:.1f}")
                    else:
                        st.warning(f"Not enough games ({len(df)}) for a rolling window of {rolling_window}.")

                    next_opp_code = get_next_opponent(team_code)
                    if next_opp_code:
                        section(f"vs {next_opp_code} (Next Opponent)")
                        df_opp = df[df["OPPONENT"].str.upper() == next_opp_code]
                        if not df_opp.empty:
                            o1, o2, o3 = st.columns(3)
                            o1.metric("Avg Points", f"{df_opp['PTS'].mean():.1f}")
                            o2.metric("Avg Rebounds", f"{df_opp['REB'].mean():.1f}")
                            o3.metric("Avg Assists", f"{df_opp['AST'].mean():.1f}")
                            st.caption(f"Based on {len(df_opp)} game(s) vs {next_opp_code}")
                        else:
                            st.info(f"No historical data vs {next_opp_code} in selected seasons.")

# ═══════════════════════════════════════════════════════════════════════════
# OPPONENT BREAKDOWN
# ═══════════════════════════════════════════════════════════════════════════
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
            player_card(player_name, team_code)

        section("Parameters")
        seasons = st.multiselect("Seasons", SEASONS, default=["2024-25"], key="ob_seasons")
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
                    fig = px.bar(
                        opp_stats.reset_index(),
                        x="Hit Rate", y="OPPONENT",
                        orientation="h",
                        color="Hit Rate",
                        color_continuous_scale=["#222", "#555", "#ffffff"],
                        text=opp_stats["Games"].astype(str).values + " G",
                    )
                    fig.add_vline(x=0.5, line_dash="dot", line_color="#333")
                    fig.update_coloraxes(showscale=False)
                    st.plotly_chart(dark_fig(fig), use_container_width=True)

                    section("Data Table")
                    st.dataframe(
                        opp_stats.style.format({
                            "Hit Rate": "{:.1%}",
                            "Avg Margin": "{:.2f}",
                            "Avg Stat": "{:.1f}",
                            "Games": "{:.0f}",
                        }),
                        use_container_width=True,
                    )

# ═══════════════════════════════════════════════════════════════════════════
# BET SIMULATION
# ═══════════════════════════════════════════════════════════════════════════
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
            player_card(player_name, team_code)

        section("Parameters")
        seasons = st.multiselect("Seasons", SEASONS, default=["2024-25"], key="sim_seasons")
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
                    fig = px.line(
                        df.reset_index(),
                        y="CUMULATIVE_PROFIT",
                        labels={"CUMULATIVE_PROFIT": "Units", "index": "Game"},
                        color_discrete_sequence=["#ffffff"],
                    )
                    fig.add_hline(y=0, line_dash="dot", line_color="#333")
                    st.plotly_chart(dark_fig(fig), use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# FIRST BASKET
# ═══════════════════════════════════════════════════════════════════════════
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
            player_card(selected_player, team_code)

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
            fig_scatter = px.scatter(
                df_team, x="Tip Win %", y="First Basket %",
                text=df_team.index, trendline="ols",
                color_discrete_sequence=["#ffffff"],
            )
            fig_scatter.update_traces(textfont_color="#666", marker_size=8)
            st.plotly_chart(dark_fig(fig_scatter), use_container_width=True)

            section("Full Team Table")
            df_display = df_team.sort_values("First Basket %", ascending=False)
            st.dataframe(
                df_display.style.format({"First Basket %": "{:.1%}", "Tip Win %": "{:.1%}"}),
                use_container_width=True,
            )

            section("Today's Matchups")
            with st.spinner("Loading..."):
                df_today = get_today_first_basket_stats()

            if not df_today.empty:
                extracted = df_today["Matchup"].str.extract(r'(\w+)\s+vs\s+(\w+)')
                teams_today = sorted(set(
                    extracted[0].dropna().tolist() + extracted[1].dropna().tolist()
                ))
                if teams_today:
                    sel_today = st.selectbox("Filter by Team", teams_today, key="fb_today")
                    team_df = df_today[df_today["Matchup"].str.contains(sel_today, na=False)]
                    st.dataframe(team_df, use_container_width=True)
                else:
                    st.info("Could not parse team names from today's matchups.")
            else:
                st.warning("No data available for today's matchups.")

            st.caption("Data: firstbasketstats.com")

# ═══════════════════════════════════════════════════════════════════════════
# PRIZEPICKS
# ═══════════════════════════════════════════════════════════════════════════
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
                "player_name": "Player",
                "stat_type": "Stat",
                "line_score": "Line",
                "status": "Status",
            }).sort_values("Player"),
            use_container_width=True,
            hide_index=True,
        )

# ═══════════════════════════════════════════════════════════════════════════
# DISCLAIMER
# ═══════════════════════════════════════════════════════════════════════════
with tab_disc:
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""
    <p style='font-size:0.68rem;letter-spacing:0.14em;text-transform:uppercase;color:#444;'>Disclaimer</p>
    <p style='color:#888;font-size:0.9rem;line-height:1.7;max-width:600px;'>
        This dashboard is for <strong style='color:#ccc;'>informational and entertainment purposes only.</strong>
        It does not constitute betting advice or guarantee outcomes.
        Use at your own discretion. Konjure Analytics is not responsible for any financial
        decisions made based on this data.
    </p>
    """, unsafe_allow_html=True)
