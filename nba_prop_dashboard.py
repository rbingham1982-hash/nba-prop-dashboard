# -*- coding: utf-8 -*-
"""
Konjure Analytics — NBA Prop Betting Dashboard
Fixed & Improved Version
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
from nba_api.stats.static import teams, players
from nba_api.stats.endpoints import playergamelog, commonteamroster
from datetime import datetime

# --- ESPN team slug map for schedule API ---
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

# --- Helper Functions ---
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
    """Fetch and combine game logs for a player across multiple seasons."""
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
    """Get next scheduled opponent using ESPN API."""
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
    """Fetch today's NBA prop lines from PrizePicks public API."""
    try:
        url = "https://api.prizepicks.com/projections?league_id=7&per_page=250&single_stat=true"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://app.prizepicks.com/",
        }
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
            player_id = rel.get("id", "")
            rows.append({
                "player_name": player_map.get(player_id, ""),
                "stat_type": attrs.get("stat_type", ""),
                "line_score": attrs.get("line_score"),
                "status": attrs.get("status", ""),
            })

        df = pd.DataFrame(rows)
        return df[df["player_name"] != ""] if not df.empty else df
    except Exception as e:
        st.warning(f"PrizePicks fetch failed: {e}")
        return pd.DataFrame()


PP_STAT_MAP = {
    "Points": "Points",
    "Rebounds": "Rebounds",
    "Assists": "Assists",
    "PRA": "Pts+Rebs+Asts",
    "3PM": "3-PT Made",
}


def get_real_time_line(player_name, market="points"):
    """Fetch real-time prop line from odds API."""
    try:
        # FIX: API key loaded from st.secrets (add to .streamlit/secrets.toml)
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
    """Simulate cumulative profit (+1 win, -1 loss) per game."""
    # FIX: use apply instead of .replace() on bool series
    bet_result = df["HIT"].apply(lambda x: 1 if x else -1)
    cumulative = bet_result.cumsum()
    return pd.Series(cumulative.to_list(), index=df.index, name="CUMULATIVE_PROFIT")

@st.cache_data(ttl=3600)
def get_first_basket_data():
    """Scrape season-level first basket stats with fallback data."""
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
    """Scrape today's first basket matchup stats."""
    url = "https://firstbasketstats.com/today-first-basket-stats"
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        # FIX: guard against missing table
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

# --- Shared constants ---
STAT_MAP = {
    "Points": "PTS",
    "Rebounds": "REB",
    "Assists": "AST",
    "PRA": "PRA",
    "3PM": "FG3M"
}
SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]

# --- UI Setup ---
st.set_page_config(page_title="NBA Prop Betting Dashboard", layout="centered")

st.image(
    "https://copilot.microsoft.com/th/id/BCO.402d6b29-c3e2-41e9-b818-3b556b92c0f2.png",
    width=120
)
st.markdown(
    "<h1 style='text-align: center; color:#E50914;'>Konjure Analytics</h1>",
    unsafe_allow_html=True
)
st.markdown(
    "<h4 style='text-align: center;'>NBA Prop Intelligence, Powered by Data</h4>",
    unsafe_allow_html=True
)

# --- Navigation ---
page = st.sidebar.radio("Navigate", [
    "🏠 Home",
    "📊 Player Stats",
    "📈 Opponent Breakdown",
    "🎯 Bet Simulation",
    "🕒 First Basket Breakdown",
    "🟣 PrizePicks Lines",
    "📜 Disclaimer"
])

# ─────────────────────────────────────────────
# 🏠 HOME
# ─────────────────────────────────────────────
if page == "🏠 Home":
    st.image(
        "https://copilot.microsoft.com/th/id/BCO.212291ea-d684-4612-81ca-a14039ffe56e.png",
        # FIX: use_column_width → use_container_width
        use_container_width=True
    )
    st.markdown("Welcome to **Konjure Analytics** — your hub for NBA prop insights.")
    st.markdown("---")
    st.markdown("""
    ### How to use this dashboard:
    1. **📊 Player Stats** — Pick a team, player, and prop type. View hit rates, rolling averages, and next-opponent predictions.
    2. **📈 Opponent Breakdown** — See how a player performs against each opponent.
    3. **🎯 Bet Simulation** — Simulate cumulative profit/loss over a season.
    4. **🕒 First Basket** — Team-level first basket and tip-off win rate data.
    """)

# ─────────────────────────────────────────────
# 📊 PLAYER STATS
# ─────────────────────────────────────────────
elif page == "📊 Player Stats":
    team_names = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team = st.selectbox("Select Team", team_names)
    team_code = get_team_abbreviation(selected_team)
    player_list = get_team_players(team_code)
    player_name = st.selectbox("Select Player", player_list)

    seasons = st.multiselect("Seasons", SEASONS, default=["2024-25"])
    prop_type = st.selectbox("Prop Type", list(STAT_MAP.keys()))
    line_value = st.number_input("Custom Prop Line", value=25.5, step=0.5)
    rolling_window = st.slider("Rolling Average Window", 1, 10, 5)
    teammate_filter = st.text_input("Filter games with matchup keyword (optional)")

    if player_name and seasons:
        player_id = get_player_id(player_name)
        if player_id:
            with st.spinner("Fetching game logs..."):
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

                # PrizePicks line
                pp_df = get_prizepicks_lines()
                pp_stat = PP_STAT_MAP.get(prop_type)
                if not pp_df.empty and pp_stat:
                    match = pp_df[
                        (pp_df["player_name"].str.lower() == player_name.lower()) &
                        (pp_df["stat_type"] == pp_stat)
                    ]
                    if not match.empty:
                        pp_line = match.iloc[0]["line_score"]
                        st.info(f"🟣 PrizePicks Line: **{pp_line}**")
                        df["PP_HIT"] = df["TARGET"] > pp_line
                        st.metric("Hit Rate vs PrizePicks Line", f"{df['PP_HIT'].mean():.1%}")

                # Live line
                live_line = get_real_time_line(player_name, market=prop_type.lower())
                if live_line:
                    st.info(f"📡 Real-Time Line: {live_line}")
                    df["LIVE_HIT"] = df["TARGET"] > live_line
                    st.metric("Hit Rate vs Live Line", f"{df['LIVE_HIT'].mean():.1%}")

                # Summary metrics
                st.subheader("📊 Prop Performance Summary")
                col1, col2, col3 = st.columns(3)
                col1.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
                col2.metric("Avg Margin", f"{df['MARGIN'].mean():.2f}")
                col3.metric("Games Analyzed", len(df))

                # IMPROVEMENT: Trend chart (was computed but never shown)
                st.subheader("📈 Performance Trend")
                fig = px.line(
                    df.reset_index(),
                    y=["TARGET", "ROLLING_AVG"],
                    title=f"{player_name} — {prop_type} Over Time",
                    labels={"value": prop_type, "index": "Game"},
                    color_discrete_map={"TARGET": "#636EFA", "ROLLING_AVG": "#E50914"}
                )
                fig.add_hline(y=line_value, line_dash="dash", line_color="gray",
                              annotation_text=f"Line: {line_value}")
                st.plotly_chart(fig, use_container_width=True)

                # Predictive stat line
                st.subheader("🔮 Predictive Stat Line (Rolling Avg)")
                # FIX: guard against window > games count
                if len(df) >= rolling_window:
                    pred = {
                        "PTS": df["PTS"].rolling(window=rolling_window).mean().iloc[-1],
                        "REB": df["REB"].rolling(window=rolling_window).mean().iloc[-1],
                        "AST": df["AST"].rolling(window=rolling_window).mean().iloc[-1],
                        "FG3M": df["FG3M"].rolling(window=rolling_window).mean().iloc[-1]
                    }
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Points", f"{pred['PTS']:.1f}")
                    c2.metric("Rebounds", f"{pred['REB']:.1f}")
                    c3.metric("Assists", f"{pred['AST']:.1f}")
                    c4.metric("3PM", f"{pred['FG3M']:.1f}")
                else:
                    st.warning(f"Not enough games ({len(df)}) for a rolling window of {rolling_window}.")

                # Next opponent prediction
                next_opp_code = get_next_opponent(team_code)
                if next_opp_code:
                    df_opp = df[df["OPPONENT"].str.upper() == next_opp_code]
                    st.subheader(f"📈 Prediction vs {next_opp_code}")
                    if not df_opp.empty:
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Avg Points", f"{df_opp['PTS'].mean():.1f}")
                        c2.metric("Avg Rebounds", f"{df_opp['REB'].mean():.1f}")
                        c3.metric("Avg Assists", f"{df_opp['AST'].mean():.1f}")
                        st.caption(f"Based on {len(df_opp)} game(s) vs {next_opp_code}")
                    else:
                        st.info(f"No historical data vs {next_opp_code} in selected seasons.")

# ─────────────────────────────────────────────
# 📈 OPPONENT BREAKDOWN
# ─────────────────────────────────────────────
elif page == "📈 Opponent Breakdown":
    st.subheader("🆚 Opponent Hit Rate Breakdown")

    team_names = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team = st.selectbox("Select Team", team_names)
    team_code = get_team_abbreviation(selected_team)
    player_list = get_team_players(team_code)
    player_name = st.selectbox("Select Player", player_list)

    seasons = st.multiselect("Seasons", SEASONS, default=["2024-25"])
    line_value = st.number_input("Prop Line for Breakdown", value=25.5, step=0.5)
    prop_type = st.selectbox("Stat Type", list(STAT_MAP.keys()))

    if player_name and seasons:
        player_id = get_player_id(player_name)
        if player_id:
            with st.spinner("Fetching game logs..."):
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

                # IMPROVEMENT: bar chart
                fig = px.bar(
                    opp_stats.reset_index(),
                    x="Hit Rate", y="OPPONENT",
                    orientation="h",
                    color="Hit Rate",
                    color_continuous_scale=["#ff4444", "#ffaa00", "#00cc44"],
                    title=f"{player_name} — {prop_type} Hit Rate by Opponent",
                    text=opp_stats["Games"].astype(str).values + " G"
                )
                fig.add_vline(x=0.5, line_dash="dash", line_color="gray")
                st.plotly_chart(fig, use_container_width=True)

                st.dataframe(
                    opp_stats.style.format({
                        "Hit Rate": "{:.1%}",
                        "Avg Margin": "{:.2f}",
                        "Avg Stat": "{:.1f}",
                        "Games": "{:.0f}"
                    }),
                    use_container_width=True
                )

# ─────────────────────────────────────────────
# 🎯 BET SIMULATION
# ─────────────────────────────────────────────
elif page == "🎯 Bet Simulation":
    st.subheader("🎯 Betting Strategy Simulation")

    team_names = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team = st.selectbox("Select Team", team_names)
    team_code = get_team_abbreviation(selected_team)
    player_list = get_team_players(team_code)
    player_name = st.selectbox("Select Player", player_list)

    seasons = st.multiselect("Seasons", SEASONS, default=["2024-25"])
    line_value = st.number_input("Simulated Prop Line", value=25.5, step=0.5)
    prop_type = st.selectbox("Stat Type", list(STAT_MAP.keys()))

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
                # FIX: use corrected simulate_bets
                df["CUMULATIVE_PROFIT"] = simulate_bets(df)

                col1, col2, col3 = st.columns(3)
                col1.metric("Total Profit", f"{df['CUMULATIVE_PROFIT'].iloc[-1]:.0f} units")
                col2.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
                col3.metric("Games Simulated", len(df))

                fig = px.line(
                    df.reset_index(),
                    y="CUMULATIVE_PROFIT",
                    title=f"{player_name} — Cumulative Profit (Line: {line_value})",
                    labels={"CUMULATIVE_PROFIT": "Units", "index": "Game"}
                )
                fig.add_hline(y=0, line_dash="dash", line_color="gray")
                st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────
# 🕒 FIRST BASKET BREAKDOWN
# ─────────────────────────────────────────────
elif page == "🕒 First Basket Breakdown":
    st.subheader("🕒 First Basket Breakdown")

    team_names_list = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team_full = st.selectbox("Select Team", team_names_list)
    team_code = get_team_abbreviation(selected_team_full)
    player_list = get_team_players(team_code)
    selected_player = st.selectbox("Select Player", player_list)

    # FIX: call get_first_basket_data() ONCE and reuse
    with st.spinner("Loading first basket data..."):
        team_stats = get_first_basket_data()

    df_team = pd.DataFrame.from_dict(team_stats, orient="index")
    required_cols = {"First Basket", "Games", "Tip Wins"}

    if required_cols.issubset(df_team.columns):
        df_team["First Basket %"] = df_team["First Basket"] / df_team["Games"]
        df_team["Tip Win %"] = df_team["Tip Wins"] / df_team["Games"]
    else:
        st.warning(f"Missing columns: {required_cols - set(df_team.columns)}")
        st.dataframe(df_team)
        st.stop()

    # Team-level metrics
    team_data = team_stats.get(team_code, {"Games": 0, "First Basket": 0, "Tip Wins": 0})
    team_fb_rate = team_data["First Basket"] / team_data["Games"] if team_data["Games"] else 0
    team_tip_rate = team_data["Tip Wins"] / team_data["Games"] if team_data["Games"] else 0

    st.markdown(f"### 📊 {team_code} Breakdown")
    c1, c2, c3 = st.columns(3)
    c1.metric("First Basket Rate", f"{team_fb_rate:.1%}")
    c2.metric("Tip-Off Win Rate", f"{team_tip_rate:.1%}")
    c3.metric("Games Played", team_data["Games"])

    st.markdown(f"### 🏀 {selected_player} Breakdown")
    st.info("Player-level first basket data coming soon.")

    # Scatter chart
    fig_scatter = px.scatter(
        df_team,
        x="Tip Win %", y="First Basket %",
        text=df_team.index,
        title="Tip Win % vs First Basket % — All Teams",
        trendline="ols"
    )
    st.plotly_chart(fig_scatter, use_container_width=True)

    # Full team table
    st.subheader("📋 Full Team First Basket Table")
    df_display = df_team.sort_values("First Basket %", ascending=False)
    st.dataframe(
        df_display.style.format({
            "First Basket %": "{:.1%}",
            "Tip Win %": "{:.1%}"
        }),
        use_container_width=True
    )

    # Today's games
    st.subheader("📅 Today's First Basket Stats")
    with st.spinner("Loading today's matchups..."):
        df_today = get_today_first_basket_stats()

    if not df_today.empty:
        # FIX: safe extraction of team codes from matchup column
        extracted = df_today["Matchup"].str.extract(r'(\w+)\s+vs\s+(\w+)')
        teams_today = sorted(set(
            extracted[0].dropna().tolist() + extracted[1].dropna().tolist()
        ))
        if teams_today:
            sel_today = st.selectbox("Select Team from Today's Games", teams_today)
            team_df = df_today[df_today["Matchup"].str.contains(sel_today, na=False)]
            st.dataframe(team_df, use_container_width=True)
        else:
            st.info("Could not parse team names from today's matchups.")
    else:
        st.warning("No data available for today's matchups.")

    st.markdown("🔍 Data sourced from [FirstBasketStats.com](https://firstbasketstats.com/2024-2025-first-basket-stats-data)")

# ─────────────────────────────────────────────
# 🟣 PRIZEPICKS LINES
# ─────────────────────────────────────────────
elif page == "🟣 PrizePicks Lines":
    st.subheader("🟣 Today's PrizePicks NBA Lines")

    with st.spinner("Loading PrizePicks projections..."):
        pp_df = get_prizepicks_lines()

    if pp_df.empty:
        st.warning("No PrizePicks data available right now.")
    else:
        stat_options = ["All"] + sorted(pp_df["stat_type"].dropna().unique().tolist())
        selected_stat = st.selectbox("Filter by Stat Type", stat_options)

        search = st.text_input("Search Player Name")

        filtered = pp_df.copy()
        if selected_stat != "All":
            filtered = filtered[filtered["stat_type"] == selected_stat]
        if search:
            filtered = filtered[filtered["player_name"].str.contains(search, case=False, na=False)]

        filtered = filtered.sort_values("player_name")
        st.dataframe(
            filtered[["player_name", "stat_type", "line_score", "status"]].rename(columns={
                "player_name": "Player",
                "stat_type": "Stat",
                "line_score": "Line",
                "status": "Status",
            }),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"{len(filtered)} projection(s) shown")

# ─────────────────────────────────────────────
# 📜 DISCLAIMER
# ─────────────────────────────────────────────
elif page == "📜 Disclaimer":
    st.subheader("📜 Disclaimer")
    st.markdown("""
    This dashboard is for **informational and entertainment purposes only.**  
    It does **not** constitute betting advice or guarantee outcomes.  
    Use at your own discretion. Konjure Analytics is not responsible for any financial
    decisions made based on this data.
    """)
