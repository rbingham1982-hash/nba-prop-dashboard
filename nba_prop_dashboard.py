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
from datetime import datetime

# --- Helper Functions ---
def get_player_id(player_name):
    match = players.find_players_by_full_name(player_name)
    return match[0]['id'] if match else None

def get_team_abbreviation(player_name):
    try:
        match = players.find_players_by_full_name(player_name)
        if match:
            player_id = match[0]['id']
            logs = playergamelog.PlayerGameLog(player_id=player_id, season="2023-24").get_data_frames()[0]
            if not logs.empty and "TEAM_ABBREVIATION" in logs.columns:
                team_abbr = logs.loc[0, "TEAM_ABBREVIATION"]
                return team_abbr.lower()
    except Exception as e:
        st.warning(f"Team detection failed: {e}")
    return "bos"  # fallback

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

def get_next_opponent(team_code):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_code}/schedule"
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
                    if team["team"]["abbreviation"].lower() != team_code:
                        return team["team"]["abbreviation"]
    return None

# --- UI ---
st.set_page_config(page_title="NBA Prop Betting Dashboard", layout="wide")
st.title("🏀 NBA Prop Betting Dashboard")

player_name = st.text_input("Player Name", "Jayson Tatum")
seasons = st.multiselect("Seasons", ["2022-23", "2023-24", "2024-25", "2025-26"], default=["2023-24"])
prop_type = st.selectbox("Prop Type", ["Points", "Rebounds", "Assists", "PRA", "3PM"])
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

        # Predictive stat line (rolling)
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

        # Predictive stat line vs next opponent
        team_code = get_team_abbreviation(player_name)
        next_opp_code = get_next_opponent(team_code)
        if next_opp_code:
            df_opp = df[df["OPPONENT"].str.upper() == next_opp_code.upper()]
            if not df_opp.empty:
                st.subheader(f"📈 Prediction vs {next_opp_code.upper()}")
                pred_stats = {
                    "PTS": df_opp["PTS"].mean(),
                    "REB": df_opp["REB"].mean(),
                    "AST": df_opp["AST"].mean(),
                    "FG3M": df_opp["FG3M"].mean()
                }
                st.metric("Points", f"{pred_stats['PTS']:.1f}")
                st.metric("Rebounds", f"{pred_stats['REB']:.1f}")
                st.metric("Assists", f"{pred_stats['AST']:.1f}")
                st.metric("3PM", f"{pred_stats['FG3M']:.1f}")
            else:
                st.warning(f"No past games found vs {next_opp_code.upper()}.")

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

        # Rolling averages chart
        st.subheader("📊 Rolling Averages")
        fig_pred = px.line(df, x="GAME_DATE", y=["PTS", "REB", "AST", "FG3M"], title="Rolling Averages")
        st.plotly_chart(fig_pred, use_container_width=True)

        # Betting simulation
        st.subheader("🎯 Betting Strategy Simulation")
        df["CUMULATIVE_PROFIT"] = simulate_bets(df)
        st.line_chart


