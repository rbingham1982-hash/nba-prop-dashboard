# -*- coding: utf-8 -*-
"""
Created on Fri Nov 14 10:06:21 2025

@author: rbing
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import requests
from bs4 import BeautifulSoup
from nba_api.stats.static import teams, players
from nba_api.stats.endpoints import playergamelog, commonteamroster
from datetime import datetime

# --- Helper Functions ---
def get_team_abbreviation(team_name):
    team = next((t for t in teams.get_teams() if t["full_name"] == team_name), None)
    return team["abbreviation"].upper() if team else None

def get_team_id(team_abbr):
    team = next((t for t in teams.get_teams() if t["abbreviation"].upper() == team_abbr.upper()), None)
    return team["id"] if team else None

def get_team_players(team_abbr):
    team_id = get_team_id(team_abbr)
    if team_id:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id).get_data_frames()[0]
        return roster["PLAYER"].tolist()
    return []

def get_player_id(player_name):
    match = players.find_players_by_full_name(player_name)
    return match[0]['id'] if match else None

def get_gamelogs(player_id, seasons):
    all_games = pd.DataFrame()
    for season in seasons:
        logs = playergamelog.PlayerGameLog(player_id=player_id, season=season).get_data_frames()[0]
        logs['SEASON'] = season
        logs['OPPONENT'] = logs['MATCHUP'].str.extract(r'@ (\w+)|vs. (\w+)').bfill(axis=1).iloc[:, 0]
        all_games = pd.concat([all_games, logs], ignore_index=True)
    return all_games

@st.cache_data(ttl=900)
def get_next_opponent(team_code):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_code.lower()}/schedule"
        response = requests.get(url)
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

def get_real_time_line(player_name, market="points"):
    url = "https://api.sportsgameodds.com/v2/events"
    headers = {"X-API-Key": "132d657e987feea06b1b91a21116d4a0"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        for event in data.get("events", []):
            for prop in event.get("playerProps", []):
                if player_name.lower() in prop.get("name", "").lower() and market in prop.get("market", "").lower():
                    return prop.get("line")
    return None

def simulate_bets(df):
    bet_result = df["HIT"].astype(int).replace({0: -1})
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
                games = int(cells[1].text.strip())
                first_baskets = int(cells[2].text.strip())
                tip_wins = int(cells[3].text.strip())
                data[team] = {
                    "Games": games,
                    "First Basket": first_baskets,
                    "Tip Wins": tip_wins
                }
            except ValueError:
                continue
        return data if data else fallback_data
    except Exception:
        return fallback_data

# --- UI ---
st.set_page_config(page_title="NBA Prop Betting Dashboard", layout="centered")

# Branding
st.image("https://copilot.microsoft.com/th/id/BCO.402d6b29-c3e2-41e9-b818-3b556b92c0f2.png", width=120)
st.markdown("<h1 style='text-align: center; color:#E50914;'>Konjure Analytics</h1>", unsafe_allow_html=True)
st.markdown("<h4 style='text-align: center;'>NBA Prop Intelligence, Powered by Data</h4>", unsafe_allow_html=True)

# Navigation
page = st.sidebar.radio("Navigate", [
    "🏠 Home",
    "📊 Player Stats",
    "📈 Opponent Breakdown",
    "🎯 Bet Simulation",
    "🕒 First Basket Breakdown",
    "📜 Disclaimer"
])

if page == "🏠 Home":
    st.image("https://copilot.microsoft.com/th/id/BCO.212291ea-d684-4612-81ca-a14039ffe56e.png", use_column_width=True)
    st.markdown("Welcome to Konjure Analytics — your hub for NBA prop insights.")
    st.markdown("---")

elif page == "📊 Player Stats":
    team_names = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team = st.selectbox("Select Team", team_names)
    team_code = get_team_abbreviation(selected_team)
    player_list = get_team_players(team_code)
    player_name = st.selectbox("Select Player", player_list)

    seasons = st.multiselect("Seasons", ["2022-23", "2023-24", "2024-25", "2025-26"], default=["2025-26"])
    prop_type = st.selectbox("Prop Type", ["Points", "Rebounds", "Assists", "PRA", "3PM"])
    line_value = st.number_input("Custom Prop Line", value=25.5)
    rolling_window = st.slider("Rolling Average Window", 1, 10, 5)
    teammate_filter = st.text_input("Filter games with teammate (optional)")

    if player_name and seasons:
        player_id = get_player_id(player_name)
        if player_id:
            df = get_gamelogs(player_id, seasons)
            if teammate_filter:
                df = df[df["MATCHUP"].str.contains(teammate_filter, case=False, na=False)]

            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
            stat_map = {
                "Points": "PTS",
                "Rebounds": "REB",
                "Assists": "AST",
                "PRA": "PRA",
                "3PM": "FG3M"
            }

            df["TARGET"] = df[stat_map[prop_type]]
            df["HIT"] = df["TARGET"] > line_value
            df["MARGIN"] = df["TARGET"] - line_value
            df["ROLLING_AVG"] = df["TARGET"].rolling(window=rolling_window).mean()

            live_line = get_real_time_line(player_name, market=prop_type.lower())
            if live_line:
                st.info(f"📡 Real-Time Line: {live_line}")
                df["LIVE_HIT"] = df["TARGET"] > live_line
                st.metric("Hit Rate vs Live Line", f"{df['LIVE_HIT'].mean():.1%}")

            st.subheader("📊 Prop Performance Summary")
            col1, col2 = st.columns(2)
            col1.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
            col2.metric("Avg Margin", f"{df['MARGIN'].mean():.2f}")

            st.subheader("🔮 Predictive Stat Line (Rolling Avg)")
            predictive_line = {
                "PTS": df["PTS"].rolling(window=rolling_window).mean().iloc[-1],
                "REB": df["REB"].rolling(window=rolling_window).mean().iloc[-1],
                "AST": df["AST"].rolling(window=rolling_window).mean().iloc[-1],
                "FG3M": df["FG3M"].rolling(window=rolling_window).mean().iloc[-1]
            }
            st.metric("Points", f"{predictive_line['PTS']:.1f}")
            st.metric("Rebounds", f"{predictive_line['REB']:.1f}")
            st.metric("Assists", f"{predictive_line['AST']:.1f}")
            st.metric("3PM", f"{predictive_line['FG3M']:.1f}")

            next_opp_code = get_next_opponent(team_code)
            if next_opp_code:
                df_opp = df[df["OPPONENT"].str.upper() == next_opp_code]
                st.subheader(f"📈 Prediction vs {next_opp_code}")
                if not df_opp.empty:
                    pred_stats = {
                        "PTS": df_opp["PTS"].mean(),
                        "REB": df_opp["REB"].mean(),
                        "AST": df_opp["AST"].mean(),
                        "FG3M": df_opp["FG3M"].mean()
                    }
                    st.metric("Points", f"{pred_stats['PTS']:.1f}")
                    st.metric("Rebounds", f"{pred_stats['REB']:.1f}")
                    st.metric("Assists", f"{pred_stats['AST']:.1f}")
                    
elif page == "📈 Opponent Breakdown":
    st.subheader("🆚 Opponent Hit Rate Breakdown")

    team_names = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team = st.selectbox("Select Team", team_names)
    team_code = get_team_abbreviation(selected_team)
    player_list = get_team_players(team_code)
    player_name = st.selectbox("Select Player", player_list)

    seasons = st.multiselect("Seasons", ["2022-23", "2023-24", "2024-25", "2025-26"], default=["2025-26"])
    line_value = st.number_input("Prop Line for Breakdown", value=25.5)
    prop_type = st.selectbox("Stat Type", ["Points", "Rebounds", "Assists", "PRA", "3PM"])

    if player_name and seasons:
        player_id = get_player_id(player_name)
        if player_id:
            df = get_gamelogs(player_id, seasons)
            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
            stat_map = {
                "Points": "PTS",
                "Rebounds": "REB",
                "Assists": "AST",
                "PRA": "PRA",
                "3PM": "FG3M"
            }
            df["TARGET"] = df[stat_map[prop_type]]
            df["HIT"] = df["TARGET"] > line_value
            df["MARGIN"] = df["TARGET"] - line_value

            opp_stats = df.groupby("OPPONENT")[["HIT", "MARGIN"]].mean().sort_values("HIT", ascending=False)
            st.dataframe(opp_stats.style.format({"HIT": "{:.1%}", "MARGIN": "{:.2f}"})) 
            
elif page == "🎯 Bet Simulation":
    st.subheader("🎯 Betting Strategy Simulation")

    team_names = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team = st.selectbox("Select Team", team_names)
    team_code = get_team_abbreviation(selected_team)
    player_list = get_team_players(team_code)
    player_name = st.selectbox("Select Player", player_list)

    seasons = st.multiselect("Seasons", ["2022-23", "2023-24", "2024-25", "2025-26"], default=["2025-26"])
    line_value = st.number_input("Simulated Prop Line", value=25.5)
    prop_type = st.selectbox("Stat Type", ["Points", "Rebounds", "Assists", "PRA", "3PM"])

    if player_name and seasons:
        player_id = get_player_id(player_name)
        if player_id:
            df = get_gamelogs(player_id, seasons)
            df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
            stat_map = {
                "Points": "PTS",
                "Rebounds": "REB",
                "Assists": "AST",
                "PRA": "PRA",
                "3PM": "FG3M"
            }
            df["TARGET"] = df[stat_map[prop_type]]
            df["HIT"] = df["TARGET"] > line_value
            df["CUMULATIVE_PROFIT"] = simulate_bets(df)

            st.line_chart(df[["CUMULATIVE_PROFIT"]])
            st.metric("Total Profit", f"{df['CUMULATIVE_PROFIT'].iloc[-1]:.0f} units")
            st.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
            
elif page == "🕒 First Basket Breakdown":
    st.subheader("🕒 First Basket Breakdown")

    team_names = sorted([t["full_name"] for t in teams.get_teams()])
    selected_team = st.selectbox("Select Team", team_names)
    team_code = get_team_abbreviation(selected_team)
    player_list = get_team_players(team_code)
    selected_player = st.selectbox("Select Player", player_list)

    # Load scraped data
    team_stats = get_first_basket_data()
    df_team = pd.DataFrame.from_dict(team_stats, orient="index")

    required_cols = {"First Basket", "Games", "Tip Wins"}
    if required_cols.issubset(df_team.columns):
        df_team["First Basket %"] = df_team["First Basket"] / df_team["Games"]
        df_team["Tip Win %"] = df_team["Tip Wins"] / df_team["Games"]
    else:
        st.warning(f"Missing columns: {required_cols - set(df_team.columns)}")
        st.dataframe(df_team)

    # Team breakdown
    team_data = team_stats.get(team_code, {"Games": 0, "First Basket": 0, "Tip Wins": 0})
    team_fb_rate = team_data["First Basket"] / team_data["Games"] if team_data["Games"] else 0
    team_tip_rate = team_data["Tip Wins"] / team_data["Games"] if team_data["Games"] else 0

    st.markdown(f"### 📊 {team_code} Breakdown")
    st.metric("First Basket Rate", f"{team_fb_rate:.1%}")
    st.metric("Tip-Off Win Rate", f"{team_tip_rate:.1%}")

    # Player breakdown (placeholder until player-level scrape is added)
    st.markdown(f"### 🏀 {selected_player} Breakdown")
    st.markdown("Player-level first basket data coming soon.")
    
    st.subheader("📊 Team First Basket Table")

    # Load team stats
    team_stats = get_first_basket_data()
    df_team = pd.DataFrame.from_dict(team_stats, orient="index")

    # Defensive check
    required_cols = {"Games", "First Basket", "Tip Wins"}
    if required_cols.issubset(df_team.columns):
        df_team["First Basket %"] = df_team["First Basket"] / df_team["Games"]
        df_team["Tip Win %"] = df_team["Tip Wins"] / df_team["Games"]
        df_team = df_team.sort_values("First Basket %", ascending=False)

        st.dataframe(df_team.style.format({
        "First Basket %": "{:.1%}",
        "Tip Win %": "{:.1%}"
    }))
    else:
        st.warning(f"Missing columns: {required_cols - set(df_team.columns)}")
        st.dataframe(df_team)

    team_stats = get_first_basket_data()
    team_codes = sorted(team_stats.keys())
    selected_team = st.selectbox("Select NBA Team", team_codes)

    team_data = team_stats[selected_team]
    fb_rate = team_data["First Basket"] / team_data["Games"]
    tip_rate = team_data["Tip Wins"] / team_data["Games"]

    st.markdown(f"### 📊 {selected_team} First Basket Stats")
    st.metric("Games Played", team_data["Games"])
    st.metric("First Baskets Made", team_data["First Basket"])
    st.metric("Tip-Off Wins", team_data["Tip Wins"])
    st.metric("First Basket Rate", f"{fb_rate:.1%}")
    st.metric("Tip-Off Win Rate", f"{tip_rate:.1%}")
    # Visuals
    df_team = pd.DataFrame.from_dict(team_stats, orient="index")
    df_team["First Basket %"] = df_team["First Basket"] / df_team["Games"]
    df_team["Tip Win %"] = df_team["Tip Wins"] / df_team["Games"]

    fig_team = px.scatter(
        df_team,
        x="Tip Win %",
        y="First Basket %",
        text=df_team.index,
        title="Tip Win % vs First Basket %",
        labels={"Tip Win %": "Tip-Off Win %", "First Basket %": "First Basket %"},
        trendline="ols"
    )
    st.plotly_chart(fig_team, use_container_width=True)
    df_team = pd.DataFrame.from_dict(team_stats, orient="index")
    df_team["First Basket %"] = df_team["First Basket"] / df_team["Games"]
    df_team["Tip Win %"] = df_team["Tip Wins"] / df_team["Games"]
    df_team = df_team.sort_values("First Basket %", ascending=False)

    st.subheader("📋 Full Team First Basket Table")
    st.dataframe(df_team.style.format({
    "First Basket %": "{:.1%}",
    "Tip Win %": "{:.1%}"
}))   

    st.markdown("🔍 Data sourced from [FirstBasketStats.com](https://firstbasketstats.com/2024-2025-first-basket-stats-data)")

elif page == "📜 Disclaimer":
    st.subheader("📜 Disclaimer")
    st.markdown("""
    This dashboard is for informational and entertainment purposes only.  
    It does not constitute betting advice or guarantee outcomes.  
    Use at your own discretion. Konjure Analytics is not responsible for any financial decisions made based on this data.
    """)                                             



