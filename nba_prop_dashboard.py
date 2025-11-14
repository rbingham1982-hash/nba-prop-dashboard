# -*- coding: utf-8 -*-
"""
Created on Fri Nov 14 10:06:21 2025

@author: rbing
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import requests
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

# --- UI ---
st.set_page_config(page_title="NBA Prop Betting Dashboard", layout="wide")
st.title("🏀 NBA Prop Betting Dashboard")

team_names = sorted([t["full_name"] for t in teams.get_teams()])
selected_team = st.selectbox("Select Team", team_names)
team_code = get_team_abbreviation(selected_team)
player_list = get_team_players(team_code)
player_name = st.selectbox("Select Player", player_list)

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
                st.metric("3PM", f"{pred_stats['FG3M']:.1f}")
            else:
                st.warning(f"No past games found vs {next_opp_code}.")

        st.subheader("🆚 Opponent Hit Rate Breakdown")
        opp_stats = df.groupby("OPPONENT")[["HIT", "MARGIN"]].mean().sort_values("HIT", ascending=False)
        st.dataframe(opp_stats.style.format({"HIT": "{:.1%}", "MARGIN": "{:.2f}"}))

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
        st.line_chart(df[["CUMULATIVE_PROFIT"]])
        
with st.expander("📜 Disclaimer"):
    st.markdown("""
    This dashboard is for informational and entertainment purposes only. 
    It does not constitute betting advice or guarantee outcomes. 
    Use at your own discretion.
    """)
