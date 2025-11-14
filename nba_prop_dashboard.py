# -*- coding: utf-8 -*-
"""
Created on Fri Nov 14 10:06:21 2025

@author: rbing
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog

# --- Helper Functions ---
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

# --- UI ---
st.set_page_config(page_title="NBA Prop Betting Dashboard", layout="wide")
st.title("🏀 NBA Prop Betting Dashboard")

player_name = st.text_input("Player Name", "Jayson Tatum")
seasons = st.multiselect("Seasons", ["2022-23", "2023-24", "2024-25", "2025-26"], default=["2023-24"])
prop_type = st.selectbox("Prop Type", ["Points", "Rebounds", "Assists", "PRA"])
line_value = st.number_input("Custom Prop Line", value=25.5)
rolling_window = st.slider("Rolling Average Window", 1, 10, 5)
teammate_filter = st.text_input("Filter games with teammate (optional)")

# --- Main Logic ---
if player_name and seasons:
    player_id = get_player_id(player_name)
    if player_id:
        df = get_gamelogs(player_id, seasons)
        if teammate_filter:
            df = df[df["MATCHUP"].str.contains(teammate_filter, case=False, na=False)]

        stat_map = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "PRA": "PRA"}
        df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
        df["TARGET"] = df[stat_map[prop_type]]
        df["HIT"] = df["TARGET"] > line_value
        df["MARGIN"] = df["TARGET"] - line_value
        df["ROLLING_AVG"] = df["TARGET"].rolling(window=rolling_window).mean()

        # Real-time odds
        live_line = get_real_time_line(player_name, market=prop_type.lower())
        if live_line:
            st.info(f"📡 Real-Time Line: {live_line}")
            df["LIVE_HIT"] = df["TARGET"] > live_line
            st.metric("Hit Rate vs Live Line", f"{df['LIVE_HIT'].mean():.1%}")

        # Summary
        st.subheader("📊 Prop Performance Summary")
        col1, col2 = st.columns(2)
        col1.metric("Hit Rate", f"{df['HIT'].mean():.1%}")
        col2.metric("Avg Margin", f"{df['MARGIN'].mean():.2f}")

        # Opponent breakdown
        st.subheader("🆚 Opponent Hit Rate Breakdown")
        opp_stats = df.groupby("OPPONENT")[["HIT", "MARGIN"]].mean().sort_values("HIT", ascending=False)
        st.dataframe(opp_stats.style.format({"HIT": "{:.1%}", "MARGIN": "{:.2f}"}))

        # Visuals
        st.subheader("📈 Game-by-Game Performance")
        fig_line = px.line(df, x="GAME_DATE", y=["TARGET", "ROLLING_AVG"], title="Performance vs Prop Line")
        fig_line.add_hline(y=line_value, line_dash="dash", line_color="red", annotation_text=f"Line: {line_value}")
        st.plotly_chart(fig_line, use_container_width=True)

        fig_scatter = px.scatter(df, x="GAME_DATE", y="TARGET", color="HIT", title="Hit/Miss Distribution")
        fig_scatter.add_hline(y=line_value, line_dash="dash", line_color="red")
        st.plotly_chart(fig_scatter, use_container_width=True)

        # Betting simulation
        st.subheader("🎯 Betting Strategy Simulation")
        df["CUMULATIVE_PROFIT"] = simulate_bets(df)
        st.line_chart(df["CUMULATIVE_PROFIT"])

        # Export
        st.subheader("📥 Export Results")
        st.download_button("Download CSV", df.to_csv(index=False), file_name="prop_results.csv")
    else:
        st.warning("Player not found. Please check the spelling.")