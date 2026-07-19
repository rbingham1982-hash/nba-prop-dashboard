# -*- coding: utf-8 -*-
"""
Konjure Analytics — Multi-Sport Prop & Predictive Dashboard
"""

import os
import re
import time
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
from nba_api.stats.static import teams, players
from nba_api.stats.endpoints import playergamelog, commonteamroster, leaguegamefinder, playbyplayv3, commonallplayers
from datetime import datetime, timedelta
import parlay_tracker
import parlay_model as _pm

# Shared prop-prediction model (hit-rate calculators, BvP, game-log fetchers,
# parlay builder) — see parlay_model.py. Kept under this file's original
# names so no call sites below need to change; daily_parlay_gen.py imports
# the same module so the two can no longer drift apart.
PP_PAYOUTS            = _pm.PP_PAYOUTS
_PP_ODDS_IMPLIED       = _pm.PP_ODDS_IMPLIED
_PP_NBA_STAT_COL       = _pm.NBA_STAT_COL
_PP_MLB_HIT_COL        = _pm.MLB_HIT_COL
_PP_MLB_PIT_COL        = _pm.MLB_PIT_COL
_PP_PITCHER_TYPES      = _pm.MLB_PITCHER_TYPES
_WNBA_PARLAY_COL_MAP   = _pm.WNBA_STAT_COL
_BVP_COL_MAP           = _pm.BVP_COL_MAP
_BVP_MIN_AB            = _pm.BVP_MIN_AB

get_player_id          = _pm.get_player_id
get_gamelogs           = _pm.get_gamelogs
_get_mlb_player_map    = _pm.get_mlb_player_map
_mlb_player_id_by_name = _pm.mlb_player_id
get_mlb_hitting_logs   = _pm.get_mlb_hitting_logs
get_mlb_pitching_logs  = _pm.get_mlb_pitching_logs
_mlb_bvp_stats         = _pm.mlb_bvp_stats
_mlb_today_pitcher_lookup = _pm.mlb_today_pitcher_lookup
get_wnba_player_id     = _pm.get_wnba_player_id
get_wnba_gamelogs      = _pm.get_wnba_gamelogs

_nba_hit_rate  = _pm._nba_hit_rate
_wnba_hit_rate = _pm._wnba_hit_rate
_mlb_hit_rate  = _pm._mlb_hit_rate
_build_parlays = _pm._build_parlays
_build_sgp     = _pm._build_sgp

# True when running on a local machine; False in Codespaces / deployed environments
_IS_LOCAL = not bool(os.environ.get("CODESPACE_NAME") or os.environ.get("GITPOD_WORKSPACE_ID"))


def _safe_rerun():
    """st.rerun() was added in 1.27; fall back to experimental for older versions."""
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

#MainMenu, footer, header { visibility: hidden; }
[data-testid="stSidebar"] { display: none; }
.block-container { padding: 1.1rem 1.5rem 2rem !important; max-width: 100% !important; }
html, body, .stApp { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }

::-webkit-scrollbar { width: 3px; height: 3px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

/* ── Header ── */
.konjure-header { padding: 0.5rem 0 0.85rem 0; border-bottom: 1px solid var(--border); margin-bottom: 0.25rem; }
.konjure-title {
    font-size: 1.3rem; font-weight: 800; letter-spacing: 0.18em;
    text-transform: uppercase; margin: 0 0 0.06rem 0;
    background: var(--title-gradient); -webkit-background-clip: text;
    -webkit-text-fill-color: transparent; background-clip: text;
}
.konjure-sub { font-size: 0.6rem; color: var(--text-muted); letter-spacing: 0.22em; text-transform: uppercase; margin: 0; }

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0; background: transparent !important;
    border-bottom: 1px solid var(--border);
}
.stTabs [data-baseweb="tab"] {
    color: var(--text-muted) !important; background: transparent !important;
    border: none !important; border-bottom: 2px solid transparent !important;
    padding: 0.6rem 1.1rem !important; font-size: 0.66rem !important;
    font-weight: 600 !important; letter-spacing: 0.14em !important;
    text-transform: uppercase !important; transition: color 0.15s !important;
}
.stTabs [data-baseweb="tab"]:hover { color: var(--text-primary) !important; }
.stTabs [aria-selected="true"] { color: var(--accent) !important; border-bottom: 2px solid var(--accent) !important; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.25rem; }

/* ── Section heading ── */
.section-heading {
    font-size: 0.58rem; font-weight: 700; letter-spacing: 0.22em; text-transform: uppercase;
    color: var(--text-muted); margin: 1.4rem 0 0.7rem 0;
    padding-bottom: 0.38rem; border-bottom: 1px solid var(--border);
}

/* ── Metric cards ── */
[data-testid="metric-container"] {
    background: var(--card-bg) !important;
    border: 1px solid var(--border) !important;
    border-radius: 12px !important; padding: 1rem 1.15rem !important;
    position: relative; overflow: hidden;
}
[data-testid="metric-container"]::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0;
    height: 2px; background: var(--accent-gradient);
}
[data-testid="metric-container"] label {
    color: var(--text-muted) !important; font-size: 0.59rem !important;
    font-weight: 600 !important; letter-spacing: 0.16em !important; text-transform: uppercase !important;
}
[data-testid="metric-container"] [data-testid="metric-value"] {
    color: var(--text-primary) !important; font-size: 1.45rem !important; font-weight: 700 !important; line-height: 1.2 !important;
}
[data-testid="stMetricDelta"] { font-size: 0.68rem !important; font-weight: 500 !important; }

/* ── DataFrames ── */
.stDataFrame { border: 1px solid var(--border) !important; border-radius: 12px !important; overflow: hidden !important; }

/* ── Alerts ── */
.stAlert { background: var(--card-bg) !important; border: 1px solid var(--border) !important; color: var(--text-muted) !important; border-radius: 10px !important; font-size: 0.8rem !important; }

/* ── Divider ── */
hr { border-color: var(--border) !important; margin: 1rem 0 !important; }

/* ── Player card (NBA) ── */
.player-card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 14px; padding: 1rem 1.2rem; display: flex;
    align-items: center; gap: 1rem; margin-bottom: 1rem;
}
.player-card img {
    width: 62px; height: 62px; object-fit: cover;
    border-radius: 50%; border: 2px solid var(--border); background: var(--card-bg);
}
.player-card-name { font-size: 0.95rem; font-weight: 700; color: var(--text-primary); margin: 0 0 0.12rem 0; }
.player-card-team { font-size: 0.58rem; color: var(--text-muted); letter-spacing: 0.16em; text-transform: uppercase; margin: 0; }

/* ── Feature cards ── */
.feature-card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 12px; padding: 1.15rem 1.25rem; height: 100%;
}
.feature-card-icon { font-size: 1.35rem; margin-bottom: 0.45rem; }
.feature-card-title { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-primary); margin: 0 0 0.35rem 0; }
.feature-card-desc { font-size: 0.78rem; color: var(--text-muted); margin: 0; line-height: 1.6; }

/* ── MLB player card ── */
.mlb-player-card {
    background: #fff; border: 1px solid var(--mlb-border);
    border-radius: 14px; padding: 1.15rem 1.3rem; display: flex;
    align-items: center; gap: 1.2rem; margin-bottom: 1rem;
    box-shadow: 0 4px 20px rgba(0,32,96,0.08);
}
.mlb-player-card img {
    width: 76px; height: 76px; object-fit: cover; object-position: top;
    border-radius: 10px; background: #e8edf4; border: 2px solid var(--mlb-navy);
}
.mlb-player-name { font-size: 1.05rem; font-weight: 800; color: var(--mlb-navy); margin: 0 0 0.12rem 0; }
.mlb-player-pos { font-size: 0.6rem; color: var(--mlb-red); font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; margin: 0 0 0.06rem 0; }
.mlb-player-team { font-size: 0.6rem; color: #8899aa; letter-spacing: 0.1em; text-transform: uppercase; margin: 0; }

/* ── Stat pill ── */
.stat-pill {
    display: inline-block; background: var(--accent-dim); color: var(--text-primary);
    font-size: 0.65rem; font-weight: 600; letter-spacing: 0.08em;
    padding: 0.18rem 0.65rem; border-radius: 20px; margin-right: 0.35rem; margin-bottom: 0.25rem;
}

/* ── Sport selector ── */
div[data-testid="stSelectbox"] label { display: none; }

/* ── Control panel wrapper ── */
.ctrl-panel {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 14px; padding: 1.1rem 1.15rem; margin-bottom: 0.5rem;
}

/* ── Inputs / selects ── */
.stTextInput input, .stNumberInput input {
    border-radius: 8px !important; font-size: 0.82rem !important;
}
div[data-baseweb="select"] > div { border-radius: 8px !important; }

/* ── Buttons ── */
.stButton > button {
    border-radius: 8px !important; font-size: 0.7rem !important;
    font-weight: 600 !important; letter-spacing: 0.1em !important;
    text-transform: uppercase !important; transition: all 0.15s !important;
}

/* ── Score ticker ── */
.score-ticker {
    display: flex; align-items: stretch; overflow-x: auto;
    white-space: nowrap; background: var(--card-bg);
    border: 1px solid var(--border); border-radius: 10px;
    margin-bottom: 1rem; scrollbar-width: none;
}
.score-ticker::-webkit-scrollbar { display: none; }
.sg-label {
    flex-shrink: 0; display: flex; align-items: center;
    padding: 0 1rem; font-size: 0.52rem; font-weight: 800;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--accent); border-right: 1px solid var(--border);
}
.sg-item {
    flex-shrink: 0; padding: 0.55rem 1.1rem;
    border-right: 1px solid var(--border);
    text-align: center; min-width: 110px;
    transition: background 0.15s;
}
.sg-link {
    text-decoration: none; color: inherit; display: block;
    cursor: pointer; transition: background 0.15s;
}
.sg-link:hover { background: rgba(129,140,248,0.10); }
.sg-teams { font-size: 0.7rem; font-weight: 700; color: var(--text-primary); }
.sg-score { font-size: 0.88rem; font-weight: 800; color: var(--text-primary); margin: 0.12rem 0; letter-spacing: 0.04em; }
.sg-status { font-size: 0.52rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-muted); }
.sg-live { color: #4ade80 !important; }

/* ── Sport hero ── */
.sport-hero {
    position: relative; border-radius: 14px; overflow: hidden;
    padding: 2rem 2.5rem; margin-bottom: 1.25rem;
}
.sport-hero-watermark {
    position: absolute; right: 2rem; top: 50%; transform: translateY(-50%);
    font-size: 9rem; line-height: 1; opacity: 0.06;
    user-select: none; pointer-events: none;
}
.sport-hero-content { position: relative; z-index: 1; }
.sport-hero-label {
    font-size: 0.56rem; letter-spacing: 0.24em; text-transform: uppercase;
    color: var(--text-muted); margin: 0 0 0.4rem 0;
}
.sport-hero-title {
    font-size: 2rem; font-weight: 800; line-height: 1.1; margin: 0 0 0.5rem 0;
    background: var(--title-gradient); -webkit-background-clip: text;
    -webkit-text-fill-color: transparent; background-clip: text;
}
.sport-hero-sub { font-size: 0.8rem; color: var(--text-muted); margin: 0; line-height: 1.6; }
.sport-hero-stats {
    display: flex; gap: 2rem; margin-top: 1.2rem;
}
.sport-hero-stat-val { font-size: 1.25rem; font-weight: 800; color: var(--text-primary); }
.sport-hero-stat-lbl { font-size: 0.52rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--text-muted); }

/* ── Daily Blog ── */
.blog-wrap { max-width: 740px; margin: 0 auto; padding: 0.5rem 0 2rem; }
.blog-kicker {
    font-size: 0.56rem; font-weight: 800; letter-spacing: 0.24em;
    text-transform: uppercase; color: var(--accent); margin: 0 0 0.55rem;
}
.blog-title {
    font-size: 1.7rem; font-weight: 800; line-height: 1.22;
    color: var(--text-primary); margin: 0 0 0.8rem;
}
.blog-meta {
    font-size: 0.6rem; color: var(--text-muted); margin: 0 0 1.6rem;
    padding-bottom: 0.8rem; border-bottom: 1px solid var(--border);
    display: flex; gap: 1.2rem; letter-spacing: 0.08em; text-transform: uppercase;
}
.blog-lead {
    font-size: 1.0rem; color: var(--text-primary); line-height: 1.75;
    font-weight: 500; margin: 0 0 1.6rem;
}
.blog-body { font-size: 0.9rem; color: #9294a8; line-height: 1.82; margin: 0 0 1.25rem; }
.blog-h2 {
    font-size: 0.65rem; font-weight: 800; letter-spacing: 0.22em;
    text-transform: uppercase; color: var(--accent);
    margin: 2.2rem 0 1.1rem; padding-bottom: 0.45rem;
    border-bottom: 1px solid var(--border);
}
.blog-game-card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 12px; padding: 1.1rem 1.3rem; margin-bottom: 0.85rem;
    position: relative; overflow: hidden;
}
.blog-game-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0;
    height: 2px; background: var(--accent-gradient);
}
.blog-game-vs { font-size: 0.72rem; font-weight: 800; color: var(--text-primary); margin: 0 0 0.22rem; }
.blog-game-rec { font-size: 0.6rem; color: var(--text-muted); margin: 0 0 0.55rem; letter-spacing: 0.06em; }
.blog-game-body { font-size: 0.84rem; color: #9294a8; line-height: 1.72; margin: 0; }
.blog-player-card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-left: 3px solid var(--accent); border-radius: 10px;
    padding: 0.95rem 1.15rem; margin-bottom: 0.75rem;
}
.blog-player-name { font-size: 0.88rem; font-weight: 800; color: var(--text-primary); margin: 0 0 0.18rem; }
.blog-player-role { font-size: 0.6rem; color: var(--accent); font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; margin: 0 0 0.38rem; }
.blog-player-body { font-size: 0.82rem; color: #9294a8; line-height: 1.7; margin: 0; }
.blog-callout {
    background: rgba(129,140,248,0.07); border: 1px solid rgba(129,140,248,0.18);
    border-radius: 10px; padding: 1rem 1.2rem; margin: 1.5rem 0;
    font-size: 0.9rem; color: var(--text-primary); line-height: 1.75;
}
.blog-news-row {
    padding: 0.65rem 0; border-bottom: 1px solid var(--border);
    display: flex; gap: 0.75rem; align-items: flex-start;
}
.blog-news-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent); flex-shrink: 0; margin-top: 0.35rem;
}
.blog-news-text { font-size: 0.82rem; color: var(--text-primary); line-height: 1.5; }
.blog-news-date { font-size: 0.56rem; color: var(--text-muted); margin-top: 0.15rem; }

/* ── News cards ── */
.news-card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 10px; padding: 0.85rem 1rem; margin-bottom: 0.55rem;
    transition: border-color 0.15s, box-shadow 0.15s; display: block;
}
.news-card:hover { border-color: var(--accent); box-shadow: 0 2px 12px rgba(0,0,0,0.12); }
.news-headline {
    font-size: 0.8rem; font-weight: 600; color: var(--text-primary);
    line-height: 1.45; margin: 0 0 0.28rem 0;
}
.news-desc { font-size: 0.73rem; color: var(--text-muted); margin: 0 0 0.28rem 0; line-height: 1.5; }
.news-meta { font-size: 0.56rem; color: var(--text-muted); letter-spacing: 0.1em; text-transform: uppercase; margin: 0; }
.news-source { font-size: 0.56rem; font-weight: 700; color: var(--accent); letter-spacing: 0.1em; text-transform: uppercase; margin: 0 0 0.28rem 0; }

/* ── Players to Watch ── */
.ptw-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 0.5rem; margin-bottom: 0.5rem; }
.ptw-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 0.7rem 0.85rem; transition: border-color 0.18s; }
.ptw-card:hover { border-color: var(--accent); }
.ptw-player-name { font-size: 0.8rem; font-weight: 700; color: var(--text-primary); margin: 0 0 0.2rem 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ptw-team { font-size: 0.62rem; color: var(--text-muted); margin: 0 0 0.3rem 0; letter-spacing: 0.08em; text-transform: uppercase; }
.ptw-line { font-size: 1.05rem; font-weight: 700; color: var(--accent); margin: 0 0 0.35rem 0; }
.ptw-badge { display: inline-block; font-size: 0.55rem; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase; padding: 0.14rem 0.4rem; border-radius: 3px; }
.ptw-badge-normal { background: rgba(129,140,248,0.12); color: var(--accent); }
.ptw-badge-goblin { background: rgba(167,139,250,0.15); color: #a78bfa; }
.ptw-badge-demon  { background: rgba(248,113,113,0.15); color: #f87171; }

/* ── Mock Draft ── */
.draft-header { padding: 1rem 0 0.5rem; border-bottom: 1px solid var(--border); margin-bottom: 1.25rem; }
.draft-kicker { font-size: 0.58rem; font-weight: 700; letter-spacing: 0.22em; text-transform: uppercase; color: #818cf8; margin-bottom: 0.35rem; }
.draft-countdown { font-size: 0.78rem; color: var(--text-muted); margin: 0; }
.draft-prospect-card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-top: 3px solid #818cf8; border-radius: 12px;
    padding: 1rem 1.1rem; margin-bottom: 0.75rem; position: relative;
}
.draft-pick-badge {
    display: inline-flex; align-items: center; justify-content: center;
    width: 2rem; height: 2rem; border-radius: 50%;
    background: rgba(129,140,248,0.15); border: 1px solid rgba(129,140,248,0.3);
    font-size: 0.72rem; font-weight: 800; color: #818cf8;
    margin-bottom: 0.5rem;
}
.draft-player-name { font-size: 1rem; font-weight: 800; color: var(--text-primary); margin: 0 0 0.1rem 0; }
.draft-player-meta { font-size: 0.68rem; color: var(--text-muted); letter-spacing: 0.08em; text-transform: uppercase; margin: 0 0 0.6rem 0; }
.draft-grade {
    position: absolute; top: 1rem; right: 1rem;
    font-size: 1.1rem; font-weight: 800; color: #818cf8;
}
.draft-strength { font-size: 0.75rem; color: #86efac; padding: 0.08rem 0; line-height: 1.55; }
.draft-weakness { font-size: 0.75rem; color: #fca5a5; padding: 0.08rem 0; line-height: 1.55; }
.draft-comp { font-size: 0.7rem; color: var(--text-muted); margin: 0.5rem 0 0 0; }
.draft-comp strong { color: #f59e0b; }
.draft-summary { font-size: 0.78rem; color: #c8cad4; line-height: 1.65; margin: 0.5rem 0 0 0; font-style: italic; }
.draft-board-row { display: flex; align-items: center; padding: 0.45rem 0; border-bottom: 1px solid rgba(255,255,255,0.04); gap: 0.75rem; font-size: 0.78rem; }
.draft-board-pick { font-size: 0.7rem; font-weight: 700; color: #818cf8; width: 1.8rem; flex-shrink: 0; }
.draft-board-team { color: var(--text-muted); font-size: 0.7rem; width: 7rem; flex-shrink: 0; }
.draft-board-player { font-weight: 600; color: var(--text-primary); flex: 1; }
.draft-board-pos { font-size: 0.65rem; color: #818cf8; background: rgba(129,140,248,0.1); border-radius: 4px; padding: 0.1rem 0.35rem; }
.draft-board-school { font-size: 0.68rem; color: var(--text-muted); }

/* ══ MOBILE RESPONSIVE ══════════════════════════════════════════════════════ */
@media (max-width: 640px) {
    /* ── Layout ── */
    .block-container { padding: 0.6rem 0.5rem 1.5rem !important; }

    /* Stack ALL column groups by default */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0 !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 100% !important;
        min-width: 100% !important;
        max-width: 100% !important;
    }

    /* Re-allow 2-up grid for rows that contain metric cards or feature cards */
    [data-testid="stHorizontalBlock"]:has([data-testid="metric-container"]) > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"]:has(.feature-card) > [data-testid="stColumn"] {
        flex: 1 1 calc(50% - 0.25rem) !important;
        min-width: calc(50% - 0.25rem) !important;
        max-width: 50% !important;
    }

    /* ── Header ── */
    .konjure-title { font-size: 1rem !important; letter-spacing: 0.12em !important; }
    .konjure-sub { font-size: 0.54rem !important; }

    /* ── Tabs: horizontal scroll, no wrap ── */
    .stTabs [data-baseweb="tab-list"] {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
        -webkit-overflow-scrolling: touch !important;
        scrollbar-width: none !important;
    }
    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar { display: none !important; }
    .stTabs [data-baseweb="tab"] {
        padding: 0.5rem 0.65rem !important;
        font-size: 0.6rem !important;
        flex-shrink: 0 !important;
    }

    /* ── Metric cards ── */
    [data-testid="metric-container"] {
        padding: 0.65rem 0.75rem !important;
        border-radius: 10px !important;
    }
    [data-testid="metric-container"] [data-testid="metric-value"] {
        font-size: 1.1rem !important;
    }
    [data-testid="metric-container"] label {
        font-size: 0.7rem !important;
    }

    /* ── Player cards ── */
    .player-card { padding: 0.75rem !important; gap: 0.75rem !important; }
    .player-card img { width: 46px !important; height: 46px !important; }
    .mlb-player-card { padding: 0.75rem !important; gap: 0.75rem !important; }
    .mlb-player-card img { width: 54px !important; height: 54px !important; }

    /* ── Hero banner ── */
    .sport-hero {
        padding: 1.2rem 1.1rem !important;
        border-radius: 10px !important;
    }
    .sport-hero-title { font-size: 1.35rem !important; }
    .sport-hero-sub { font-size: 0.74rem !important; }
    .sport-hero-watermark { display: none !important; }

    /* ── Score ticker ── */
    .sg-item { min-width: 88px !important; padding: 0.4rem 0.65rem !important; }
    .sg-score { font-size: 0.8rem !important; }
    .sg-teams { font-size: 0.64rem !important; }

    /* ── News cards ── */
    .news-card { padding: 0.75rem 0.85rem !important; }
    .news-headline { font-size: 0.76rem !important; }

    /* ── Control panel ── */
    .ctrl-panel { padding: 0.85rem !important; }

    /* ── Charts: allow horizontal scroll ── */
    .js-plotly-plot { overflow-x: auto !important; }

    /* ── DataFrames ── */
    .stDataFrame { font-size: 0.75rem !important; }

    /* ── Section headings ── */
    .section-heading { margin-top: 1rem !important; font-size: 0.55rem !important; }

    /* ── Feature card text ── */
    .feature-card-title { font-size: 0.62rem !important; }
    .feature-card-desc { font-size: 0.72rem !important; }
}

/* ── Parlay Cards ── */
.pl-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 0.75rem; position: relative; overflow: hidden; }
.pl-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; }
.pl-card-safe::before { background: linear-gradient(90deg,#22c55e,#4ade80); }
.pl-card-value::before { background: linear-gradient(90deg,#f59e0b,#fbbf24); }
.pl-header { display: flex; align-items: center; gap: 0.85rem; margin-bottom: 0.65rem; flex-wrap: wrap; }
.pl-prob { font-size: 1.15rem; font-weight: 800; color: var(--text-primary); }
.pl-tag { font-size: 0.68rem; font-weight: 700; background: rgba(129,140,248,0.12); color: var(--accent); padding: 0.18rem 0.55rem; border-radius: 4px; letter-spacing: 0.06em; white-space: nowrap; }
.pl-ev { font-size: 0.65rem; color: var(--text-muted); margin-left: auto; }
/* ── Verdict strip (Accuracy tab) ── */
.verdict { border: 1px solid var(--border); border-left-width: 3px; border-radius: 8px; padding: 0.6rem 0.8rem; background: var(--card-bg); display: flex; flex-direction: column; gap: 0.15rem; height: 100%; }
.verdict-k { font-size: 0.95rem; font-weight: 800; color: var(--text-primary); }
.verdict-v { font-size: 0.68rem; color: var(--text-muted); }
.verdict-good { border-left-color: #0ca30c; }
.verdict-good .verdict-k { color: #4ade80; }
.verdict-warn { border-left-color: #fab219; }
.verdict-warn .verdict-k { color: #fbbf24; }
.verdict-bad  { border-left-color: #d03b3b; }
.verdict-bad .verdict-k { color: #f87171; }

.pl-summary { font-size: 0.8rem; margin: 0.2rem 0 0.6rem; }
.pl-summary-good { color: #4ade80; }
.pl-summary-none { color: #f59e0b; }
.pl-rec { font-size: 0.62rem; font-weight: 800; background: rgba(34,197,94,0.14); color: #4ade80; padding: 0.18rem 0.5rem; border-radius: 4px; letter-spacing: 0.05em; white-space: nowrap; }
.pl-norec { font-size: 0.62rem; font-weight: 700; background: rgba(148,163,184,0.10); color: #94a3b8; padding: 0.18rem 0.5rem; border-radius: 4px; letter-spacing: 0.04em; white-space: nowrap; }
/* A negative-EV parlay is logged for calibration, not offered as a bet — mute it. */
.pl-card-norec { opacity: 0.62; }
.pl-card-norec:hover { opacity: 1; }
.pl-leg { display: flex; justify-content: space-between; align-items: center; padding: 0.3rem 0; border-bottom: 1px solid rgba(255,255,255,0.04); gap: 0.4rem; }
.pl-leg:last-child { border-bottom: none; }
.pl-name { font-size: 0.82rem; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 130px; flex-shrink: 0; }
.pl-stat { font-size: 0.72rem; color: var(--text-muted); flex: 1; text-align: center; }
.pl-rate { font-size: 0.72rem; font-weight: 700; white-space: nowrap; }
.pl-rate-hi { color: #0ca30c; }   /* VIZ_HIT */
.pl-rate-mid { color: #fab219; }  /* VIZ_PENDING */
.pl-rate-lo { color: #d03b3b; }   /* VIZ_MISS */
.pl-rate-none { color: var(--text-muted); font-weight: 400; }
.pl-section-label { font-size: 0.58rem; font-weight: 700; letter-spacing: 0.2em; text-transform: uppercase; color: var(--text-muted); margin: 0 0 0.35rem 0; }
</style>
""", unsafe_allow_html=True)

# ─── PWA + mobile meta tags ────────────────────────────────────────────────
st.markdown("""
<link rel="manifest" href='data:application/manifest+json,{"name":"Konjure Analytics","short_name":"Konjure","description":"Multi-Sport Prop Intelligence","start_url":"/","display":"standalone","background_color":"%23111318","theme_color":"%23111318","orientation":"portrait-primary","icons":[{"src":"https://cdn.jsdelivr.net/npm/twemoji@14/72x72/1f3c6.png","sizes":"72x72","type":"image/png"},{"src":"https://cdn.jsdelivr.net/npm/twemoji@14/72x72/1f3c6.png","sizes":"192x192","type":"image/png"}]}'>
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Konjure">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#111318">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes">
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

# ESPN scoreboard returns shorter abbreviations for some teams; map them to nba_api format
_ESPN_TO_NBA_ABBR = {
    "NY":   "NYK",
    "SA":   "SAS",
    "GS":   "GSW",
    "NO":   "NOP",
    "WSH":  "WAS",
    "PHO":  "PHX",
    "UTAH": "UTA",
}

def _resolve_nba_abbr(espn_abbr: str) -> str:
    return _ESPN_TO_NBA_ABBR.get(espn_abbr.upper(), espn_abbr.upper())

@st.cache_data(ttl=3600)
def get_team_players(team_abbr):
    team_id = get_team_id(team_abbr)
    if not team_id:
        return []
    try:
        roster = commonteamroster.CommonTeamRoster(team_id=team_id).get_data_frames()[0]
        return roster["PLAYER"].tolist()
    except Exception:
        return []

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

_PP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://app.prizepicks.com/",
}
_PP_DEAD_STATUSES = {"final", "cancelled", "failed", "lost", "won", "scored", "no_contest"}

_pp_lite_cache: dict = {}
_pp_lite_cache_ts: dict = {}
_PP_LITE_CACHE_TTL = 300  # 5 min; never cache empty results

def get_prizepicks_lines(league_id=7):
    now = time.time()
    cached = _pp_lite_cache.get(league_id)
    if cached is not None and not cached.empty and now - _pp_lite_cache_ts.get(league_id, 0) < _PP_LITE_CACHE_TTL:
        return cached
    try:
        url = f"https://api.prizepicks.com/projections?league_id={league_id}&per_page=250&single_stat=true"
        resp = requests.get(url, headers=_PP_HEADERS, timeout=12)
        if resp.status_code != 200:
            return _pp_lite_cache.get(league_id, pd.DataFrame())
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
            if attrs.get("status", "pre_game") in _PP_DEAD_STATUSES:
                continue
            rel = proj.get("relationships", {}).get("new_player", {}).get("data", {})
            pid = rel.get("id", "")
            rows.append({
                "player_name": player_map.get(pid, ""),
                "stat_type": attrs.get("stat_type", ""),
                "line_score": attrs.get("line_score"),
                "odds_type": attrs.get("odds_type", "standard"),
            })
        df = pd.DataFrame(rows)
        result = df[df["player_name"] != ""] if not df.empty else df
        if not result.empty:
            _pp_lite_cache[league_id] = result
            _pp_lite_cache_ts[league_id] = now
        return result if not result.empty else _pp_lite_cache.get(league_id, pd.DataFrame())
    except Exception:
        return _pp_lite_cache.get(league_id, pd.DataFrame())

# ─── 2026 NBA Mock Draft ──────────────────────────────────────────────────────
NBA_DRAFT_DATE = "June 23, 2026"
NBA_DRAFT_VENUE = "Barclays Center, Brooklyn, NY"

# ── 2026 NBA Free Agent Tracker ────────────────────────────────────────────
# Last manually reviewed: 2026-06-29. Predictions update daily via timestamp.
_NBA_FA_LAST_UPDATED = "July 1, 2026 — live updates"

_NBA_FREE_AGENTS = [
    # ── Tier 1: Franchise Cornerstones ────────────────────────────────────
    {
        "name": "Stephen Curry",
        "pos": "PG", "age": 38, "from_team": "Golden State Warriors",
        "tier": "Supermax",
        "predicted_dest": "Golden State Warriors",
        "confidence": 85,
        "contract_projection": "2–3 yr / $120–140M",
        "interest_teams": ["Warriors", "Suns", "Knicks"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 26.6, "rpg": 3.6, "apg": 4.7, "fg_pct": 46.2, "ts_pct": 63.1, "per": 22.6},
        "blurb": (
            "The greatest shooter in NBA history enters free agency for the first time in his career, "
            "coming off a season that ranks among the best ever by a player 37+. Golden State will clear "
            "every cap obstacle to retain him, and Curry has given no indication he wants to leave. The "
            "real question is length — two years keeps options open, three locks in his jersey retirement. "
            "His return is the prerequisite the Warriors need before chasing LeBron and restructuring "
            "the roster around one final championship run."
        ),
    },
    {
        "name": "LeBron James",
        "pos": "SF/PF", "age": 41, "from_team": "Los Angeles Lakers",
        "tier": "Max",
        "predicted_dest": "Golden State Warriors",
        "confidence": 55,
        "contract_projection": "1–2 yr / $50–90M",
        "interest_teams": ["Warriors", "Heat", "Cavaliers"],
        "status": "Unrestricted FA — leaving Lakers",
        "stats": {"ppg": 20.9, "rpg": 6.1, "apg": 7.2, "fg_pct": 51.5, "ts_pct": 59.5, "per": 20.8},
        "blurb": (
            "LeBron has officially decided not to return to the Lakers, ending his eight-year run in Los "
            "Angeles. The Warriors are the heavy favorite — Golden State cleared cap space by releasing "
            "Draymond Green's $27.7M option specifically to create room and Kristaps Porzingis has already "
            "signed (2yr/$40M) as a complementary piece. The Heat are also in the mix with a potential "
            "Giannis/Bam/LeBron superteam scenario; Cleveland would be a sentimental homecoming. Expect "
            "a decision within days — this is the defining move of the 2026 offseason."
        ),
    },
    {
        "name": "Kawhi Leonard",
        "pos": "SF", "age": 35, "from_team": "Los Angeles Clippers",
        "tier": "Near-Max",
        "predicted_dest": "Toronto Raptors",
        "confidence": 100,
        "contract_projection": "3–4 yr / $120–160M",
        "interest_teams": ["Raptors"],
        "status": "Traded — Toronto Raptors",
        "stats": {"ppg": 27.9, "rpg": 6.4, "apg": 3.6, "fg_pct": 52.1, "ts_pct": 61.8, "per": 24.2},
        "blurb": (
            "Kawhi is a Raptor again. The Clippers traded Leonard to Toronto for Brandon Ingram, Gradey Dick, "
            "unprotected 2031 and 2033 first-round picks, second-round picks in 2030 and 2033, and a 2027 "
            "first-round pick swap — a massive haul for a team that received next to nothing when Kawhi left "
            "the first time. For Toronto, this is a bet-the-franchise move: Leonard returns to the city "
            "where he won his 2019 title and reportedly made clear it was the only destination he'd accept. "
            "Extension talks are expected to commence immediately."
        ),
    },
    {
        "name": "Jimmy Butler",
        "pos": "SF", "age": 36, "from_team": "Golden State Warriors",
        "tier": "Near-Max",
        "predicted_dest": "Washington Wizards (sign-and-trade)",
        "confidence": 40,
        "contract_projection": "2 yr / $113M (remaining) or buyout",
        "interest_teams": ["Wizards", "Warriors retain", "Heat"],
        "status": "Unrestricted FA — recovering from torn ACL",
        "stats": {"ppg": 20.0, "rpg": 5.6, "apg": 4.9, "fg_pct": 47.8, "ts_pct": 57.2, "per": 19.4},
        "blurb": (
            "Butler's 2025-26 Warriors tenure ended January 19 when he tore his right ACL in a win over "
            "the Heat, after posting 20.0 PPG in 38 games. He has since said he wants to retire as a "
            "Warrior, and his $56.8M salary is reportedly central to the Warriors' Anthony Davis trade "
            "pursuit — Golden State would ship Butler to Washington with draft capital to absorb Davis's "
            "$58.4M. Butler's ACL recovery timeline puts his availability near the 2026-27 season start "
            "at best, dramatically depressing his standalone market value."
        ),
    },
    # ── Tier 2: Quality Stars ──────────────────────────────────────────────
    {
        "name": "Draymond Green",
        "pos": "PF", "age": 36, "from_team": "Golden State Warriors",
        "tier": "Starter",
        "predicted_dest": "Golden State Warriors",
        "confidence": 60,
        "contract_projection": "2 yr / $40–52M",
        "interest_teams": ["Warriors", "Lakers", "Celtics"],
        "status": "Opted out of $27.7M — Unrestricted FA",
        "stats": {"ppg": 8.4, "rpg": 5.5, "apg": 5.5, "fg_pct": 41.8, "ts_pct": 54.0, "per": 14.2},
        "blurb": (
            "Draymond declined his $27.7M player option, creating the cap space the Warriors need to "
            "chase LeBron and/or Anthony Davis. This is a calculated sacrifice — if the Warriors land "
            "their targets, Draymond re-signs at a negotiated rate as part of the rebuilt core. If the "
            "LeBron pursuit fails, he returns on a shorter deal regardless. His defensive IQ and "
            "playmaking are still elite; the opt-out is roster architecture, not a departure signal. "
            "Expect a resolution within days of Curry's own decision."
        ),
    },
    {
        "name": "OG Anunoby",
        "pos": "SF", "age": 28, "from_team": "New York Knicks",
        "tier": "Max",
        "predicted_dest": "New York Knicks",
        "confidence": 100,
        "contract_projection": "4 yr / $180–200M",
        "interest_teams": ["Knicks"],
        "status": "Re-signed — New York Knicks",
        "stats": {"ppg": 16.7, "rpg": 5.2, "apg": 2.2, "fg_pct": 48.4, "ts_pct": 62.0, "per": 15.8},
        "blurb": (
            "The cornerstone stays. Anunoby re-signed with New York as part of the core group — Brunson, "
            "Towns, Anunoby, Bridges, Hart, and McBride — that won the 2025-26 NBA championship. At 28 and "
            "entering his absolute prime, OG is now one of the five best two-way wings in the league: elite "
            "perimeter defense, expanding shot creation (48.4 FG%, 62 TS%), and the championship IQ that "
            "makes him the defensive anchor of a dynasty-track Knicks team. This one was never in doubt."
        ),
    },
    {
        "name": "Klay Thompson",
        "pos": "SG", "age": 36, "from_team": "Dallas Mavericks",
        "tier": "Starter",
        "predicted_dest": "Los Angeles Lakers",
        "confidence": 38,
        "contract_projection": "1–2 yr / $14–22M",
        "interest_teams": ["Lakers", "Warriors", "Heat", "Suns"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 11.7, "rpg": 2.1, "apg": 1.4, "fg_pct": 39.3, "ts_pct": 53.5, "per": 10.8},
        "blurb": (
            "Klay's Dallas stint was a career-worst season — 11.7 PPG on 39.3% shooting with career lows "
            "in nearly every category and just 8 starts. The Mavericks experiment exposed real "
            "decline from the two-way terror of his Warriors peak. However, his name still carries playoff "
            "weight and a contender willing to carry his reputation on a short, incentive-laden deal could "
            "unlock a bounce-back. A Lakers fit alongside LeBron (if LeBron stays) has narrative appeal. "
            "Warriors reunion talk will surface but GSW won't overpay given his age trajectory."
        ),
    },
    {
        "name": "DeMar DeRozan",
        "pos": "SG/SF", "age": 37, "from_team": "Sacramento Kings",
        "tier": "Starter",
        "predicted_dest": "Chicago Bulls",
        "confidence": 46,
        "contract_projection": "1–2 yr / $18–26M",
        "interest_teams": ["Bulls", "Heat", "Knicks", "Clippers"],
        "status": "Unrestricted FA — Kings may waive ($25M partial guarantee)",
        "stats": {"ppg": 18.4, "rpg": 2.9, "apg": 4.1, "fg_pct": 47.1, "ts_pct": 56.8, "per": 16.3},
        "blurb": (
            "DeRozan's midrange artistry remains elite even at 37 — 18.4 PPG in Sacramento proves the "
            "scoring touch is intact, though it was his first sub-20 PPG season since 2012-13. Sacramento "
            "is reportedly exploring waiving him given the partially guaranteed $25M on his deal. If he "
            "hits the market clean, Chicago is the sentimental choice — he's never emotionally left that "
            "city. The Heat are also an intriguing fit: DeRozan's late-clock creation ability is exactly "
            "what Miami needs in crunch time."
        ),
    },
    {
        "name": "Bradley Beal",
        "pos": "SG", "age": 33, "from_team": "LA Clippers",
        "tier": "MLE",
        "predicted_dest": "Eastern Conference lottery team",
        "confidence": 25,
        "contract_projection": "Prove-it 1 yr / $8–12M",
        "interest_teams": ["Pistons", "Wizards", "Spurs"],
        "status": "Declining $5.6M option — Unrestricted FA (hip fracture)",
        "stats": {"ppg": 8.2, "rpg": 0.8, "apg": 1.7, "fg_pct": 37.5, "ts_pct": 44.1, "per": 6.3},
        "blurb": (
            "Beal's Clippers tenure lasted exactly six games before a left hip fracture ended his season "
            "in November. He's now declining his $5.6M player option and entering the open market coming "
            "off major hip surgery. The durability concerns are severe — he's played a full season just "
            "twice in the last six years. A playoff contender won't risk cap on Beal; his market is "
            "lottery teams willing to gamble on upside. If fully healthy, the offensive toolkit is still "
            "legitimate, but 'if fully healthy' has been the Beal caveat for four straight years."
        ),
    },
    {
        "name": "Norman Powell",
        "pos": "SG/SF", "age": 33, "from_team": "Los Angeles Clippers",
        "tier": "Starter",
        "predicted_dest": "Chicago Bulls",
        "confidence": 100,
        "contract_projection": "3–4 yr / $90–105M",
        "interest_teams": ["Bulls"],
        "status": "Signed — Chicago Bulls",
        "stats": {"ppg": 24.8, "rpg": 3.5, "apg": 2.8, "fg_pct": 51.5, "ts_pct": 62.1, "per": 17.2},
        "blurb": (
            "One of the most efficient volume scorers in the league — Powell averaged 24.8 PPG on 51.5% "
            "shooting while serving as the Clippers' primary offensive option in a lost season. His ability "
            "to score from all three levels without creating shot quality issues makes him a plug-and-play "
            "starter on any contender. Chicago identified him as the scoring wing they've needed since "
            "DeMar DeRozan's departure era, and the deal was reportedly agreed upon within hours of the "
            "moratorium lifting. Powell gives the Bulls a genuine 25-point threat for the first time in years."
        ),
    },
    {
        "name": "Walker Kessler",
        "pos": "C", "age": 23, "from_team": "Utah Jazz",
        "tier": "Starter",
        "predicted_dest": "Los Angeles Lakers",
        "confidence": 100,
        "contract_projection": "4 yr / $130M",
        "interest_teams": ["Lakers"],
        "status": "Signed — Los Angeles Lakers (sign-and-trade)",
        "stats": {"ppg": 12.8, "rpg": 10.2, "apg": 1.4, "fg_pct": 62.1, "ts_pct": 66.5, "per": 18.3},
        "blurb": (
            "The Jazz cashed in their best remaining asset. Utah sign-and-traded Kessler to the Lakers "
            "for a package including unprotected 2031 and 2033 first-round picks plus 2028 and 2030 "
            "pick swaps — a significant haul for a rebuild. For LA, it's a bet on getting elite rim "
            "protection (2.9 BPG, 66.5 TS%) around whoever the Lakers pair alongside him this summer. "
            "At 23, Kessler is entering his prime years with a franchise that needs his defensive anchor "
            "regardless of which star they land. A generational shot-blocker locked in for four years."
        ),
    },
    # ── Tier 3: Quality Starters ───────────────────────────────────────────
    {
        "name": "Anfernee Simons",
        "pos": "PG/SG", "age": 27, "from_team": "Portland Trail Blazers",
        "tier": "Starter",
        "predicted_dest": "Indiana Pacers",
        "confidence": 44,
        "contract_projection": "4 yr / $120–140M",
        "interest_teams": ["Pacers", "Nets", "Knicks", "Heat"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 21.4, "rpg": 3.2, "apg": 5.5, "fg_pct": 46.3, "ts_pct": 59.7, "per": 15.6},
        "blurb": (
            "Simons has developed into a legitimate starting-caliber point guard — 21.4 PPG and 5.5 APG "
            "on a rebuilding Portland team that gave him every opportunity to show his ceiling. His "
            "elite pull-up three-point shooting (42.1% from deep on high volume) is the exact weapon "
            "contenders covet off the bench or as a third option. Portland won't be able to offer a "
            "max, clearing the path for an Eastern contender to acquire him. Indiana's pace-and-space "
            "system around Tyrese Haliburton is a natural fit for a player of his skill set."
        ),
    },
    {
        "name": "Chris Paul",
        "pos": "PG", "age": 41, "from_team": "San Antonio Spurs",
        "tier": "Veteran Min",
        "predicted_dest": "Retirement / Advisory Role",
        "confidence": 62,
        "contract_projection": "Veteran minimum or retirement",
        "interest_teams": ["Spurs", "Warriors", "Celtics"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 6.8, "rpg": 3.2, "apg": 7.1, "fg_pct": 44.9, "ts_pct": 56.2, "per": 12.1},
        "blurb": (
            "The Point God's playing future hinges entirely on health and desire. His San Antonio "
            "role as mentor to Wembanyama produced genuine chemistry and intangible value that "
            "doesn't appear on a stat sheet. If he plays again, returning to the Spurs for one final "
            "season as a veteran sage is the most graceful exit. A Warriors reunion with Curry would be "
            "poetic. Retirement before training camp is a real outcome — his legacy is secure and his "
            "business interests are thriving."
        ),
    },
    {
        "name": "Tobias Harris",
        "pos": "PF", "age": 33, "from_team": "Detroit Pistons",
        "tier": "MLE",
        "predicted_dest": "Detroit Pistons",
        "confidence": 55,
        "contract_projection": "2–3 yr / $22–32M",
        "interest_teams": ["Pistons", "Celtics", "Nuggets", "Lakers"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 13.3, "rpg": 5.1, "apg": 2.5, "fg_pct": 46.9, "ts_pct": 58.4, "per": 13.7},
        "blurb": (
            "Harris had a quietly strong season in Detroit — 13.3 PPG on 46.9% FG in 63 games as the "
            "Pistons emerged as a legitimate playoff team. Reports say Detroit is 'determined' to re-sign "
            "him, and the fit is genuinely good: he provides the veteran three-and-D presence that "
            "complements Cade Cunningham's star development perfectly. Other contenders will call, but "
            "Harris has built roots in Detroit and the market probably doesn't push to a max offer elsewhere."
        ),
    },
    {
        "name": "Kyle Lowry",
        "pos": "PG", "age": 40, "from_team": "Philadelphia 76ers",
        "tier": "Veteran Min",
        "predicted_dest": "Toronto Raptors",
        "confidence": 55,
        "contract_projection": "1 yr veteran minimum",
        "interest_teams": ["Raptors", "Heat", "Warriors"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 5.1, "rpg": 2.8, "apg": 4.9, "fg_pct": 40.2, "ts_pct": 51.3, "per": 9.4},
        "blurb": (
            "Lowry's career has come full circle and Toronto remains the only destination that "
            "makes emotional sense for a final chapter. The Raptors are rebuilding around Scottie Barnes "
            "and Lowry's mentorship value is legitimate — he's been here before and knows what winning "
            "culture looks like. His on-court contribution is limited to spot minutes and voice, "
            "but that's enough at the minimum. A Heat return for one more South Beach chapter "
            "is the main alternative."
        ),
    },
    # ── Tier 4: Impactful Role Players ────────────────────────────────────
    {
        "name": "Caris LeVert",
        "pos": "SG/SF", "age": 31, "from_team": "Indiana Pacers",
        "tier": "MLE",
        "predicted_dest": "Miami Heat",
        "confidence": 35,
        "contract_projection": "2 yr / $22–28M",
        "interest_teams": ["Heat", "Clippers", "Knicks", "Warriors"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 14.2, "rpg": 3.8, "apg": 4.3, "fg_pct": 44.6, "ts_pct": 55.9, "per": 13.4},
        "blurb": (
            "LeVert quietly evolved into one of the better backup scorers in the Eastern Conference — "
            "a player who can initiate offense, create for others, and knock down open threes without "
            "demanding heavy usage. Miami's system of turning multi-skilled guards into contributors is "
            "tailor-made for his skill set. He's the type of player that looks average on paper and "
            "plays above his contract in the right environment. Expect Pat Riley to make a call."
        ),
    },
    {
        "name": "Gary Trent Jr.",
        "pos": "SG", "age": 27, "from_team": "Milwaukee Bucks",
        "tier": "MLE",
        "predicted_dest": "Boston Celtics",
        "confidence": 40,
        "contract_projection": "2–3 yr / $24–36M",
        "interest_teams": ["Celtics", "Thunder", "Clippers", "Suns"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 17.2, "rpg": 2.9, "apg": 2.1, "fg_pct": 44.6, "ts_pct": 58.3, "per": 12.8},
        "blurb": (
            "GTJ is the archetype contenders hunt in free agency: a clean-catch shooter who can defend "
            "one through three and doesn't need the ball to make an impact. He shot 40.2% from three "
            "on nearly seven attempts per game in Milwaukee's motion offense. Boston's need for wing "
            "shooting depth after last year's championship run is well-documented, and Trent fits the "
            "Celtics' system perfectly — catch-and-shoot threes, switchable defense, no wasted possessions. "
            "He'll attract multiple offers, but landing with a contender is his stated priority."
        ),
    },
    {
        "name": "Marcus Smart",
        "pos": "PG", "age": 32, "from_team": "Memphis Grizzlies",
        "tier": "MLE",
        "predicted_dest": "Houston Rockets",
        "confidence": 100,
        "contract_projection": "2 yr / $13M",
        "interest_teams": ["Rockets"],
        "status": "Signed — Houston Rockets",
        "stats": {"ppg": 9.4, "rpg": 4.5, "apg": 7.2, "fg_pct": 38.1, "ts_pct": 50.8, "per": 11.9},
        "blurb": (
            "A reunion with Ime Udoka. Smart reunites with his former Boston coach in Houston on a "
            "2yr/$13M deal — a low-risk, high-upside signing for a Rockets team that needs defensive "
            "identity and veteran leadership to complement their young core. Smart's 7.2 APG, elite "
            "on-ball defense, and playoff experience are exactly what a contender-track Houston team "
            "needs as they make their next push. The Celtic homecoming narrative was always media "
            "storyline; the Udoka connection was always the real draw."
        ),
    },
    {
        "name": "Dorian Finney-Smith",
        "pos": "SF/PF", "age": 32, "from_team": "Brooklyn Nets",
        "tier": "MLE",
        "predicted_dest": "Golden State Warriors",
        "confidence": 42,
        "contract_projection": "2–3 yr / $18–28M",
        "interest_teams": ["Warriors", "Celtics", "Nuggets", "Clippers"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 9.4, "rpg": 4.2, "apg": 2.1, "fg_pct": 43.6, "ts_pct": 55.2, "per": 10.6},
        "blurb": (
            "DFS is the consummate '3-and-D' wing that every contender wants but few acknowledge "
            "they covet until they lose him. His value is systemic: elite off-ball movement, "
            "switchable defense that holds up on both guards and stretch fours, and the shooting "
            "gravity to open driving lanes for stars. Golden State's pursuit of star acquisitions "
            "this summer means they'll need cost-effective depth pieces — Finney-Smith at the MLE "
            "is exactly the kind of mid-tier signing that quietly becomes essential by April."
        ),
    },
    # ── Tier 5: Veterans ──────────────────────────────────────────────────
    {
        "name": "Jonas Valanciunas",
        "pos": "C", "age": 34, "from_team": "New Orleans Pelicans",
        "tier": "Veteran Min",
        "predicted_dest": "Milwaukee Bucks",
        "confidence": 36,
        "contract_projection": "1–2 yr / $7–12M",
        "interest_teams": ["Bucks", "Cavaliers", "Nuggets", "Knicks"],
        "status": "Unrestricted FA",
        "stats": {"ppg": 13.6, "rpg": 10.8, "apg": 2.2, "fg_pct": 55.9, "ts_pct": 59.7, "per": 16.4},
        "blurb": (
            "Valanciunas remains one of the most reliable traditional bigs in the league at 34 — "
            "a double-double machine (13.6/10.8) who can score efficiently in the post and stretch "
            "to the mid-range without asking for a star's salary. New Orleans' rebuild cost him a "
            "playoff push, and at this stage of his career JV wants a legitimate championship run. "
            "Milwaukee's frontcourt void after the Giannis trade makes this a logical fit — "
            "Valanciunas as a veteran anchor who frees Damian Lillard to run pick-and-roll is "
            "underrated upside at the minimum."
        ),
    },
]

# ── NBA API: live advanced analytics for FA tracker ───────────────────────────
# Maps display name → nba_api PLAYER_NAME (handles suffixes like "III")
_FA_NAME_MAP = {
    "Jimmy Butler": "Jimmy Butler III",
    "OG Anunoby":   "OG Anunoby",
    "DeMar DeRozan": "DeMar DeRozan",
    "LeBron James": "LeBron James",
}

# 2025-26 season-end advanced stats — fallback when nba_api is unavailable (off-season)
# Values: pie/usg_pct/ast_pct/efg_pct in percentage points; ratings in points-per-100
_NBA_FA_ADVANCED_STATS: dict = {
    "Stephen Curry":        {"pie": 15.2, "usg_pct": 30.1, "net_rating":  5.4, "off_rating": 121.8, "def_rating": 116.4, "ast_pct": 18.6, "efg_pct": 57.9},
    "LeBron James":         {"pie": 14.6, "usg_pct": 27.3, "net_rating":  3.8, "off_rating": 118.2, "def_rating": 114.4, "ast_pct": 32.4, "efg_pct": 55.2},
    "Kawhi Leonard":        {"pie": 17.2, "usg_pct": 29.5, "net_rating":  6.1, "off_rating": 122.4, "def_rating": 116.3, "ast_pct": 13.8, "efg_pct": 57.6},
    "Jimmy Butler":         {"pie": 14.2, "usg_pct": 26.8, "net_rating":  2.9, "off_rating": 117.6, "def_rating": 114.7, "ast_pct": 21.5, "efg_pct": 52.2},
    "Draymond Green":       {"pie": 10.2, "usg_pct": 14.2, "net_rating":  4.8, "off_rating": 119.6, "def_rating": 114.8, "ast_pct": 30.4, "efg_pct": 46.9},
    "OG Anunoby":           {"pie": 12.8, "usg_pct": 20.6, "net_rating":  7.4, "off_rating": 122.1, "def_rating": 114.7, "ast_pct":  9.2, "efg_pct": 56.6},
    "Klay Thompson":        {"pie":  8.9, "usg_pct": 18.4, "net_rating": -1.2, "off_rating": 114.3, "def_rating": 115.5, "ast_pct":  5.8, "efg_pct": 47.2},
    "DeMar DeRozan":        {"pie": 12.6, "usg_pct": 24.8, "net_rating": -2.1, "off_rating": 113.9, "def_rating": 116.0, "ast_pct": 18.6, "efg_pct": 50.3},
    "Bradley Beal":         {"pie":  5.9, "usg_pct": 22.1, "net_rating": -4.8, "off_rating": 110.2, "def_rating": 115.0, "ast_pct":  8.3, "efg_pct": 41.8},
    "Norman Powell":        {"pie": 13.8, "usg_pct": 27.9, "net_rating": -3.6, "off_rating": 114.2, "def_rating": 117.8, "ast_pct": 10.1, "efg_pct": 59.2},
    "Walker Kessler":       {"pie": 11.8, "usg_pct": 13.4, "net_rating": -5.2, "off_rating": 109.3, "def_rating": 114.5, "ast_pct":  5.6, "efg_pct": 71.8},
    "Anfernee Simons":      {"pie": 11.9, "usg_pct": 28.1, "net_rating": -6.4, "off_rating": 111.4, "def_rating": 117.8, "ast_pct": 20.8, "efg_pct": 53.8},
    "Chris Paul":           {"pie":  9.4, "usg_pct": 14.6, "net_rating":  1.2, "off_rating": 115.2, "def_rating": 114.0, "ast_pct": 39.8, "efg_pct": 48.4},
    "Tobias Harris":        {"pie": 11.1, "usg_pct": 18.9, "net_rating":  4.2, "off_rating": 118.6, "def_rating": 114.4, "ast_pct": 11.4, "efg_pct": 53.8},
    "Kyle Lowry":           {"pie":  7.2, "usg_pct": 10.8, "net_rating": -0.8, "off_rating": 113.4, "def_rating": 114.2, "ast_pct": 28.6, "efg_pct": 44.6},
    "Caris LeVert":         {"pie": 10.4, "usg_pct": 20.3, "net_rating":  3.1, "off_rating": 116.8, "def_rating": 113.7, "ast_pct": 19.8, "efg_pct": 50.8},
    "Gary Trent Jr.":       {"pie": 10.6, "usg_pct": 22.4, "net_rating":  2.6, "off_rating": 117.2, "def_rating": 114.6, "ast_pct":  7.8, "efg_pct": 53.4},
    "Marcus Smart":         {"pie":  9.1, "usg_pct": 16.2, "net_rating": -3.8, "off_rating": 111.8, "def_rating": 115.6, "ast_pct": 34.2, "efg_pct": 43.8},
    "Dorian Finney-Smith":  {"pie":  8.2, "usg_pct": 12.8, "net_rating": -4.1, "off_rating": 110.6, "def_rating": 114.7, "ast_pct":  9.4, "efg_pct": 49.8},
    "Jonas Valanciunas":    {"pie": 12.2, "usg_pct": 20.6, "net_rating": -5.9, "off_rating": 109.8, "def_rating": 115.7, "ast_pct": 11.2, "efg_pct": 62.1},
}

@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_nba_fa_analytics(season: str = "2025-26") -> dict:
    """
    Returns {player_name: {pie, usg_pct, net_rating, off_rating, def_rating, ast_pct, ts_pct, efg_pct}}
    using nba_api LeagueDashPlayerStats (Advanced measure) + PlayerEstimatedMetrics.
    Keyed by the FA display names in _NBA_FREE_AGENTS.
    """
    import time
    result: dict = {}
    try:
        from nba_api.stats.endpoints import LeagueDashPlayerStats, PlayerEstimatedMetrics
        time.sleep(0.6)
        adv_df = LeagueDashPlayerStats(
            season=season,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
        ).get_data_frames()[0]
        time.sleep(0.6)
        est_df = PlayerEstimatedMetrics(season=season).get_data_frames()[0]

        # Build lookups keyed by lowercase name
        adv_lu = {r["PLAYER_NAME"].lower(): r for _, r in adv_df.iterrows()}
        est_lu = {r["PLAYER_NAME"].lower(): r for _, r in est_df.iterrows()}

        target_names = [fa["name"] for fa in _NBA_FREE_AGENTS]
        for name in target_names:
            api_name = _FA_NAME_MAP.get(name, name)
            key = api_name.lower()
            adv = adv_lu.get(key)   # None if not found (avoid Series bool ambiguity)
            est = est_lu.get(key)
            if adv is None and est is None:
                continue
            def _s(series, field, scale=1):
                if series is None:
                    return 0.0
                v = series.get(field, 0)
                return round(float(v or 0) * scale, 1)
            result[name] = {
                "pie":        _s(adv, "PIE", 100),
                "usg_pct":    _s(adv, "USG_PCT", 100),
                "net_rating": _s(adv, "NET_RATING"),
                "off_rating": _s(adv, "OFF_RATING"),
                "def_rating": _s(adv, "DEF_RATING"),
                "ast_pct":    _s(adv, "AST_PCT", 100),
                "ts_pct":     _s(adv, "TS_PCT", 100),
                "efg_pct":    _s(adv, "EFG_PCT", 100),
                "e_net":      _s(est, "E_NET_RATING"),
            }
    except Exception:
        pass
    return result

_NBA_MOCK_DRAFT = [
    {"pick": 1,  "team": "Washington Wizards",    "player": "AJ Dybantsa",        "pos": "SF",    "school": "BYU",                    "country": "USA"},
    {"pick": 2,  "team": "Utah Jazz",             "player": "Darryn Peterson",     "pos": "PG/SG", "school": "Kansas",                  "country": "USA"},
    {"pick": 3,  "team": "Memphis Grizzlies",     "player": "Cameron Boozer",      "pos": "PF",    "school": "Duke",                    "country": "USA"},
    {"pick": 4,  "team": "Chicago Bulls",         "player": "Caleb Wilson",        "pos": "PF/SF", "school": "North Carolina",          "country": "USA"},
    {"pick": 5,  "team": "LA Clippers",           "player": "Keaton Wagler",       "pos": "PG/SG", "school": "Illinois",                "country": "USA"},
    {"pick": 6,  "team": "Brooklyn Nets",         "player": "Darius Acuff Jr.",    "pos": "PG",    "school": "Arkansas",                "country": "USA"},
    {"pick": 7,  "team": "Sacramento Kings",      "player": "Kingston Flemings",   "pos": "PG",    "school": "Houston",                 "country": "USA"},
    {"pick": 8,  "team": "Atlanta Hawks",         "player": "Mikel Brown Jr.",     "pos": "PG/SG", "school": "Louisville",              "country": "USA"},
    {"pick": 9,  "team": "Dallas Mavericks",      "player": "Brayden Burries",     "pos": "SG",    "school": "Arizona",                 "country": "USA"},
    {"pick": 10, "team": "Milwaukee Bucks",       "player": "Nate Ament",          "pos": "SF",    "school": "Tennessee",               "country": "USA"},
    {"pick": 11, "team": "Golden State Warriors", "player": "Karim Lopez",         "pos": "PF/SF", "school": "NZ Breakers (NBL)",       "country": "Mexico"},
    {"pick": 12, "team": "Oklahoma City Thunder", "player": "Aday Mara",           "pos": "C",     "school": "Michigan",                "country": "Spain"},
    {"pick": 13, "team": "Miami Heat",            "player": "Yaxel Lendeborg",     "pos": "PF",    "school": "Michigan",                "country": "USA"},
    {"pick": 14, "team": "Charlotte Hornets",     "player": "Labaron Philon Jr.",  "pos": "PG",    "school": "Alabama",                 "country": "USA"},
    {"pick": 15, "team": "Chicago Bulls",         "player": "Cameron Carr",        "pos": "SG",    "school": "Baylor",                  "country": "USA"},
    {"pick": 16, "team": "Memphis Grizzlies",     "player": "Ebuka Okorie",        "pos": "PG",    "school": "Stanford",                "country": "USA"},
    {"pick": 17, "team": "Oklahoma City Thunder", "player": "Morez Johnson Jr.",   "pos": "PF/C",  "school": "Michigan",                "country": "USA"},
    {"pick": 18, "team": "Charlotte Hornets",     "player": "Hannes Steinbach",    "pos": "C",     "school": "Washington",              "country": "Germany"},
    {"pick": 19, "team": "Toronto Raptors",       "player": "Christian Anderson",  "pos": "PG",    "school": "Texas Tech",              "country": "USA/Germany"},
    {"pick": 20, "team": "San Antonio Spurs",     "player": "Allen Graves",        "pos": "PF",    "school": "Santa Clara",             "country": "USA"},
    {"pick": 21, "team": "Detroit Pistons",       "player": "Bennett Stirtz",      "pos": "PG",    "school": "Iowa",                    "country": "USA"},
    {"pick": 22, "team": "Philadelphia 76ers",    "player": "Chris Cenac Jr.",     "pos": "PF/C",  "school": "Houston",                 "country": "USA"},
    {"pick": 23, "team": "Atlanta Hawks",         "player": "Jayden Quaintance",   "pos": "PF",    "school": "Kentucky",                "country": "USA"},
    {"pick": 24, "team": "New York Knicks",       "player": "Meleek Thomas",       "pos": "PG/SG", "school": "Arkansas",                "country": "USA"},
    {"pick": 25, "team": "Los Angeles Lakers",    "player": "Dailyn Swain",        "pos": "PG/SG", "school": "Texas",                   "country": "USA"},
    {"pick": 26, "team": "Denver Nuggets",        "player": "Luigi Suigo",         "pos": "C",     "school": "Mega (Serbia)",           "country": "Italy"},
    {"pick": 27, "team": "Boston Celtics",        "player": "Koa Peat",            "pos": "PF",    "school": "Arizona",                 "country": "USA"},
    {"pick": 28, "team": "Minnesota Timberwolves","player": "Isaiah Evans",        "pos": "SF",    "school": "Duke",                    "country": "USA"},
    {"pick": 29, "team": "Cleveland Cavaliers",   "player": "Sergio de Larrea",    "pos": "SG",    "school": "Valencia (Spain)",        "country": "Spain"},
    {"pick": 30, "team": "Dallas Mavericks",      "player": "Henri Veesaar",       "pos": "C",     "school": "North Carolina",          "country": "Estonia"},
]

_NBA_DRAFT_SCOUTS = {
    "AJ Dybantsa": {
        "measurements": "6'8.5\" / 217 lbs / 7'0.5\" wingspan",
        "stats": "25.5 PPG, 6.8 RPG, 3.7 APG — BYU (Freshman)",
        "strengths": [
            "Elite three-level scorer with burst, body control, and Paul George-caliber frame — best isolation player in the class",
            "Switches 1-through-4 defensively; 6'9\" length with weak-side shot-blocking instincts",
            "Advanced step-back and footwork for a 19-year-old; self-creation at NBA size",
        ],
        "weaknesses": [
            "Three-point shot inconsistent; can force contested looks rather than manufacturing easier opportunities",
            "Right-hand dominant — opponents shade left; lapses off-ball defensively",
        ],
        "comp": "Paul George / Jaylen Brown",
        "grade": "A",
        "summary": "The most tooled-up prospect in the class — franchise cornerstone potential with three-point reliability as the key developmental unlock.",
    },
    "Darryn Peterson": {
        "measurements": "6'6\" / 205 lbs / 6'10\" wingspan",
        "stats": "20.0 PPG, 4.2 RPG, 1.6 APG, 1.4 SPG — Kansas (Freshman, 24 GP)",
        "strengths": [
            "True 6'6\" shot creator with three-level scoring — pull-ups, C&S threes, drives at NBA size",
            "Legitimate two-way defender (6'10\" wingspan) capable of guarding 1-through-3",
            "Advanced basketball IQ and maturity rare for a 19-year-old freshman",
        ],
        "weaknesses": [
            "Durability concern: missed 11 games with a full-body cramping condition — medical workup is critical",
            "Passing functional but not elite; shot creation can stall inside the arc vs. longer athletes",
        ],
        "comp": "Anthony Edwards",
        "grade": "A",
        "summary": "Physically imposing two-way guard with star-level size and scoring tools — off-court medical clearance is the swing factor separating him from the #1 pick.",
    },
    "Cameron Boozer": {
        "measurements": "6'8.25\" / 253 lbs / 7'1.5\" wingspan / 9'0\" standing reach",
        "stats": "22.5 PPG, 10.0 RPG, 4.0 APG — Duke (57.7% FG, 40.2% 3PT, 29 GP)",
        "strengths": [
            "Historically efficient college scorer — elite post footwork, patience, and finishing through contact",
            "Stretched big with legitimate 40%+ three-point shooting and face-up creation off the dribble",
            "Exceptional IQ, high-motor rebounder, communicative defender — plays like a 10-year veteran",
        ],
        "weaknesses": [
            "Not an elite athlete — limited burst/vertical explosiveness could restrict separation vs. long shot blockers",
            "Not a back-line rim protector; block rates solid but won't anchor interior defense",
        ],
        "comp": "Kevin Love / Pascal Siakam (ceiling)",
        "grade": "A",
        "summary": "The class's safest high-floor bet — a historically efficient, polished frontcourt scorer with elite feel and one of the highest floors of any prospect in years.",
    },
    "Caleb Wilson": {
        "measurements": "6'9.25\" / 211 lbs / 7'0.25\" wingspan / 9'0\" standing reach",
        "stats": "19.8 PPG, 9.4 RPG, 1.5 SPG, 1.4 BPG — UNC (57.8% FG, 24 GP)",
        "strengths": [
            "Rare length + vertical athleticism + fluidity at 6'10\" — plays above the rim and runs like a guard",
            "Elite two-way impact: dominant rim protector, high-activity rebounder, guards 2-through-5",
            "Ball-handler with shot-creation flashes and strong passing instincts from the high post",
        ],
        "weaknesses": [
            "Three-point shooting not yet a weapon (25.9%) — spacing limitations could constrain team offenses",
            "Slight 211 lb frame; missed time with injury in 2025-26",
        ],
        "comp": "Kevin Garnett / Giannis (ceiling)",
        "grade": "A",
        "summary": "Two-way unicorn who plays above the rim at 6'10\" with KG-level defensive versatility — developing a consistent three is the only remaining ceiling question.",
    },
    "Keaton Wagler": {
        "measurements": "6'6\" / ~185 lbs",
        "stats": "17.9 PPG, 5.1 RPG, 4.2 APG — Illinois (44.5% FG, 39.7% 3PT)",
        "strengths": [
            "Premier half-court creator — generates quality shots through deception, pace, and tight ball-handling",
            "Elite shooting versatility: pull-ups, C&S threes, floaters — best shot-maker in class at his size",
            "High basketball IQ and composure in late-game situations; already plays a pro-style game",
        ],
        "weaknesses": [
            "Not an elite athlete — limited burst and vertical explosion constrains driving/finishing ceiling",
            "Defensive ceiling is a solid minus-one, not an anchor",
        ],
        "comp": "Jamal Murray / CJ McCollum",
        "grade": "B+",
        "summary": "The class's best pure skill guard — polished half-court scorer and playmaker already playing like a competent NBA lead guard.",
    },
    "Darius Acuff Jr.": {
        "measurements": "6'2\" / 186 lbs / 6'7\" wingspan / 36.5\" vertical",
        "stats": "23.5 PPG (3rd in DI), 6.4 APG — Arkansas (48.4% FG, 44.0% 3PT, 1st-Team All-American)",
        "strengths": [
            "Historically efficient freshman scorer — slippery, quick-trigger; elite off-screen shooter in the Lillard mold",
            "6.4 APG with 44% three-point shooting as a freshman — historically rare combination of vision and accuracy",
            "36.5\" max vertical — above-the-rim finisher and transition threat despite size",
        ],
        "weaknesses": [
            "Size is a real concern at 6'2\" barefoot — limited defensive frame, struggles navigating screens",
            "Must be elite shooting to survive at his size; NBA rim finishing against length remains unproven",
        ],
        "comp": "Damian Lillard",
        "grade": "B+",
        "summary": "Generational freshman scorer with near-identical Lillard combine measurements — the bucket-getting gift is rare at any height, but defensive limitations are a real lottery risk.",
    },
    "Kingston Flemings": {
        "measurements": "6'2.5\" / 183 lbs / 6'3.5\" wingspan / 40.5\" vertical",
        "stats": "16.0 PPG, 5.2 APG, 4.1 RPG — Houston (47.6% FG, 38.7% 3PT, 37 GP)",
        "strengths": [
            "Elite athleticism for a guard (40.5\" vertical, best 3PT drill at combine 19/25); gets above the rim",
            "Best two-way effort at his size — elite defensive intensity under Kelvin Sampson's demanding system",
            "47.6% FG / 38.7% 3PT / 84.5% FT — true three-level threat despite undersized frame",
        ],
        "weaknesses": [
            "Wingspan barely longer than height (6'3.5\") — limited defensive length exploitable vs. bigger guards",
            "Not a primary playmaker — passing and creation must scale to lead NBA initiator role",
        ],
        "comp": "De'Aaron Fox",
        "grade": "B+",
        "summary": "Explosive two-way guard who maximized his freshman season at one of college basketball's most demanding programs — wingspan concerns are the only lottery debate.",
    },
    "Mikel Brown Jr.": {
        "measurements": "6'3.5\" / 190 lbs / 6'7.5\" wingspan / 39.5\" vertical",
        "stats": "18.2 PPG, 4.7 APG — Louisville (41/34/84 splits, 21 GP — scored 45 vs NC State)",
        "strengths": [
            "Best ball-handler in the class — fluid, rapid movement with advanced counters at all three levels",
            "Excellent combine athleticism (39.5\" vertical, 8'9.5\" standing reach) plus strong length for his position",
            "Dynamic shot-creator with natural mechanics and advanced feel for contact",
        ],
        "weaknesses": [
            "Significant durability concern: missed 14 of 35 games with back injury — back ailments are the most serious long-term red flag",
            "34% from three needs improvement to be a reliable spacer at the NBA level",
        ],
        "comp": "Coby White",
        "grade": "B+",
        "summary": "Dynamic scoring guard with the highest single-game ceiling in the class — back health is the critical swing factor between steal and bust.",
    },
    "Brayden Burries": {
        "measurements": "6'3.75\" / 215 lbs / 6'6\" wingspan / 38.5\" vertical",
        "stats": "16.1 PPG, 4.9 RPG, 2.4 APG — Arizona (49.1% FG, 39.1% 3PT, 39 GP)",
        "strengths": [
            "High-efficiency two-way wing: 49.1% FG / 39.1% 3PT — among the best overall efficiency profiles in the class",
            "Elite defensive tools and competitive intensity; lateral quickness and physicality to guard 1-through-3",
            "Effective on and off the ball; strong interior finisher and transition scorer",
        ],
        "weaknesses": [
            "Undersized wingspan (6'6\") for SG — defensive ceiling vs. bigger wings is limited",
            "Not a primary creator; projects as tertiary scorer rather than featured option",
        ],
        "comp": "Derrick White",
        "grade": "B+",
        "summary": "The class's best two-way efficiency play — physically mature, elite shooting and defensive effort, won't wow with highlights but makes immediate impact.",
    },
    "Nate Ament": {
        "measurements": "6'10\" / 207 lbs / 7'1\" wingspan",
        "stats": "16.7 PPG, 6.3 RPG, 2.3 APG — Tennessee (39.9% FG, Freshman)",
        "strengths": [
            "Elite physical profile at 6'10\" / 7'1\" wingspan — projects as a 3-and-D wing guarding 3-through-5",
            "Fluidity unusual for his size; strong defensive positioning and length",
            "Shooting mechanics and release suggest meaningful improvement from 39.9% college FG%",
        ],
        "weaknesses": [
            "Raw offensively — struggled to create efficiently vs. SEC competition",
            "Wide gap between current play level and lottery expectations; requires significant patience",
        ],
        "comp": "Jabari Smith Jr.",
        "grade": "B",
        "summary": "A 6'10\" physical specimen whose tools justify the lottery pick — the raw offensive game makes him a high-risk, high-upside patience play.",
    },
    "Karim Lopez": {
        "measurements": "6'8.25\" / 222 lbs / 6'11.5\" wingspan",
        "stats": "11.9 PPG, 6.1 RPG — NZ Breakers NBL (49.0% FG, 32.2% 3PT) — NBL Next Stars scoring record",
        "strengths": [
            "Historic NBL Next Stars production — more points than LaMelo Ball, Josh Giddey, and Alex Sarr in that pipeline",
            "Drives downhill and finishes with authority; elite positional size for a modern forward",
            "Strong defensive projection with length and physicality to guard multiple positions",
        ],
        "weaknesses": [
            "32.2% from three is not yet reliable — the swing skill that determines his ceiling",
            "Limited competition sample vs. elite college programs; NBL is a tier below top DI",
        ],
        "comp": "Franz Wagner",
        "grade": "B",
        "summary": "Would be the first Mexican-born first-rounder — physically imposing wing with elite NBL pedigree whose three-point development defines his NBA ceiling.",
    },
    "Aday Mara": {
        "measurements": "7'3\" / 255 lbs / 7'6.75\" wingspan",
        "stats": "12.1 PPG, 6.8 RPG, 2.4 APG — Michigan (100th-percentile rim deterrence in Big Ten)",
        "strengths": [
            "Most dominant rim protector in the class — size and timing create a near-impassable barrier",
            "Uncommon passing instincts for a 7'3\" (2.4 APG, 18.5% assist rate) — initiates from elbow/short roll",
            "Rare 12.0 block% and 19.0 assist% — elite on both ends of the two-way big spectrum",
        ],
        "weaknesses": [
            "Limited athleticism (28\" max vertical) — not a lob threat, can be exposed in PnR coverage",
            "Post scoring and midrange still developing; offensive reliability outside finishes is a work in progress",
        ],
        "comp": "Andrew Bogut",
        "grade": "B",
        "summary": "A generational defensive center whose size and timing make him a true anchor — worth the lottery pick on defense alone, with passing as an underrated bonus.",
    },
    "Yaxel Lendeborg": {
        "measurements": "6'8.5\" / 240 lbs / 7'4\" wingspan",
        "stats": "15.1 PPG, 6.8 RPG, 3.2 APG — Michigan (52/37/82, Big Ten POY, NCAA Champion)",
        "strengths": [
            "Premier two-way connector: 52/37/82 splits + elite defensive metrics (1.2 BPG, 1.1 SPG) + 7'4\" wingspan",
            "Morey-ball shot profile (87% rim/three), efficient playmaking, switches onto guards — ideal modern big",
            "Proven winner: Big Ten POY, Big Ten champion, and NCAA champion at Michigan in 2025-26",
        ],
        "weaknesses": [
            "At 23, significantly older than draft peers — shorter development runway and peak arrives sooner",
            "Projects as high-upside role player, not a first/second scoring option on a contender",
        ],
        "comp": "Aaron Gordon",
        "grade": "B",
        "summary": "The most immediately ready player in the draft — championship-tested, two-way forward with elite length and efficiency held back only by age.",
    },
    "Labaron Philon Jr.": {
        "measurements": "6'2.5\" / 176 lbs / 6'6.25\" wingspan / 35.0\" vertical",
        "stats": "22.0 PPG, 5.0 APG, 3.5 RPG — Alabama (50.1% FG, 39.9% 3PT — only DI player 22/5/50%)",
        "strengths": [
            "Historically efficient: only Division I player in 2025-26 averaging 22+ PPG, 5+ APG, 50%+ FG simultaneously",
            "Herky-jerky SGA-style attack gets into the paint at will; legitimate three-level scorer and playmaker",
            "Creative finisher with advanced feel for contact and midrange scoring off the bounce",
        ],
        "weaknesses": [
            "6'2.5\" barefoot and 176 lbs with barely plus-four wingspan — limited defensive impact at NBA level",
            "Floor may plateau as complementary scorer rather than true lead guard due to size constraints",
        ],
        "comp": "Dejounte Murray / Shai Gilgeous-Alexander (dream comp)",
        "grade": "B-",
        "summary": "Historically efficient statistically — gifted scoring creator whose real-game production demands first-round grade even if size defines his role ceiling.",
    },
    "Cameron Carr": {
        "measurements": "6'4.5\" / 184 lbs / 7'0.75\" wingspan / 8'8\" standing reach",
        "stats": "18.9 PPG, 5.8 RPG, 2.6 APG, 1.3 BPG — Baylor (49.4% FG, 37.0% 3PT, 34 GP)",
        "strengths": [
            "Elite athleticism with a 42.5\" max vertical and +8.25\" wingspan-to-height differential — one of the most explosive dunkers in the class",
            "3-and-D wing profile with shot-making range, the ability to attack closeouts, and instincts to get in passing lanes (1.3 BPG from the wing)",
            "62.2% true shooting and 9.2 BPM — elite efficiency marks for a wing with secondary creation upside",
        ],
        "weaknesses": [
            "Must add functional strength — can be moved off his line defensively and gets outbodied in physical interior situations",
            "Shot selection discipline: tendency to pull trigger on contested off-the-dribble looks early in the shot clock",
        ],
        "comp": "OG Anunoby",
        "grade": "B-",
        "summary": "A supremely athletic 3-and-D wing whose combine measurements and explosiveness vaulted him into the late lottery conversation — strength development is the key unlock.",
    },
    "Ebuka Okorie": {
        "measurements": "6'1.25\" / 185 lbs / 6'7.75\" wingspan",
        "stats": "23.2 PPG, ~3.0 RPG, ~4.0 APG — Stanford (47.0% FG, 50% rim rate, led ACC in scoring)",
        "strengths": [
            "Elite burst and change-of-pace in short spaces — generates high-value rim attempts (50% of FGs at the rim) with a sub-elite turnover rate at 31%+ usage",
            "Crafty scorer with advanced pull-up game, live-dribble vision, and rare feel for contact manipulation at his size",
            "Intelligent passer who makes the simple read ahead of the defense and shows real vision in live-dribble situations",
        ],
        "weaknesses": [
            "Standing 6'1.25\" barefoot — limited defensive frame; can be bullied by bigger guards and struggles navigating screens",
            "Tendency to hunt tough pull-ups early in the shot clock; must refine decision-making to sustain NBA efficiency",
        ],
        "comp": "Tyrese Maxey",
        "grade": "B-",
        "summary": "A jitterbug freshman scorer who led the ACC with historic efficiency at 31%+ usage — small stature limits his defensive ceiling but elite feel and burst give him a clear NBA scoring niche.",
    },
    "Morez Johnson Jr.": {
        "measurements": "6'10\" / 251 lbs / 7'3.5\" wingspan",
        "stats": "13.1 PPG, 7.3 RPG, 1.1 BPG — Michigan (62.3% FG, 67.7% TS, 40 GP)",
        "strengths": [
            "Among the best defenders in the entire class — elite length and athleticism to switch 1-through-5; top-tier combine lane agility and sprint times for a big",
            "Brutally efficient interior scorer (62.3% FG) who converts 65.8% of his 7.0 two-point attempts via physicality and positioning",
            "NBA-ready body at 251 lbs with 7'3.5\" wingspan — the trench work (rebounding, screening, hustle) is immediately translatable",
        ],
        "weaknesses": [
            "Limited perimeter shooting — projects as a non-shooter, reducing spacing utility in modern offensive systems",
            "Offensive role is narrowly defined as a roll man and finisher; creation upside is ceiling-capping",
        ],
        "comp": "Jarrett Allen",
        "grade": "B-",
        "summary": "A defensive standout with elite physical tools and immediate NBA strength — the two-way impact is real, but a non-existent perimeter game caps his offensive ceiling.",
    },
    "Hannes Steinbach": {
        "measurements": "6'10.25\" / 220 lbs / 7'2.25\" wingspan",
        "stats": "18.5 PPG, 11.8 RPG, 1.6 APG, 1.2 BPG — Washington (57.7% FG, 34.0% 3PT, 30 GP)",
        "strengths": [
            "Led all of college basketball in rebounding at 11.8 per game (4.2 offensive, 7.6 defensive) — elite combination of positioning, effort, and hands",
            "Mobile big who attacks downhill, finishes with physicality, and processes pick-and-roll actions at a high level",
            "57.7% FG with developing perimeter range (34% from three) — blend of interior dominance and stretch upside",
        ],
        "weaknesses": [
            "Perimeter shooting (34%) needs consistency to unlock full spacing value at the NBA level",
            "At 220 lbs, frame will need NFL-style weight room investment to hold up against NBA interior physicality",
        ],
        "comp": "Brook Lopez",
        "grade": "B-",
        "summary": "The best rebounder in college basketball in 2025-26 — a skilled German big with elite hands, positioning, and a developing outside shot that could make him a key modern center.",
    },
    "Christian Anderson": {
        "measurements": "6'2\" / 178 lbs",
        "stats": "18.6 PPG, 7.5 APG, 3.6 RPG, 1.6 SPG — Texas Tech (55.8% 2PT, 41.5% 3PT, 80.5% FT, 244 assists — TTU single-season record)",
        "strengths": [
            "Elite floor general with a Texas Tech single-season record 244 assists — advanced pick-and-roll feel and multi-level reads",
            "Pristine shooting splits (41.5% 3PT / 80.5% FT) with deep range and a confident pull-up game; legitimate gravity as a shooter",
            "Competitive defensive instincts (1.6 SPG) and high basketball IQ — plays a pro-style game already",
        ],
        "weaknesses": [
            "Average NBA athlete — lacks elite first-step burst and vertical pop to create separation against longer guards",
            "Undersized at 6'2\" / 178 lbs with modest wingspan; defensive upside is limited against bigger backcourt players",
        ],
        "comp": "Tyus Jones / Mike Conley",
        "grade": "B-",
        "summary": "A historic playmaker and elite shooter whose feel for the game is years ahead of his age — pedestrian athleticism is the only thing keeping him from a higher grade.",
    },
    "Allen Graves": {
        "measurements": "6'7.75\" / 225 lbs / 7'0\" wingspan / 8'10.5\" standing reach",
        "stats": "11.6 PPG, 6.5 RPG, 1.8 APG, 1.9 SPG — Santa Clara (51.7% FG, 41.6% 3PT, 35 GP, 13.4 BPM)",
        "strengths": [
            "Elite shooting efficiency from three (41.6%) with a proven shot off the catch and on the move — immediate floor-spacing value",
            "Versatile two-way forward with 1.9 SPG and instincts to impact play without the ball; screens, rebounds, and competes every possession",
            "13.4 BPM is among the best in the class — outstanding overall impact metrics relative to competition level",
        ],
        "weaknesses": [
            "WCC competition level is a legitimate concern — production against top-50 opponents was less dominant",
            "Not a primary creator; scoring upside as a featured option at the NBA level is a question mark",
        ],
        "comp": "Draymond Green (better shooter)",
        "grade": "C+",
        "summary": "A mature, under-the-radar two-way forward from a mid-major with elite efficiency, elite three-point shooting, and strong defensive instincts — a legitimate steal candidate in the late first.",
    },
    "Bennett Stirtz": {
        "measurements": "6'2.5\" / 186 lbs / 6'6\" wingspan / 8'2.5\" standing reach",
        "stats": "19.8 PPG, 4.4 APG, 2.6 RPG, 1.4 SPG — Iowa (36.0% 3PT, 60.7% TS, 10.2 BPM, 37 GP)",
        "strengths": [
            "Exceptional feel for the game — operates the pick-and-roll at a high level and can score or distribute out of every action",
            "Positional size and physicality for a lead guard at 6'4\" in shoes with 186 lbs; handles contact well and plays with IQ over athleticism",
            "Versatile two-way contributor: scores off the ball as a floor-spacer, initiates as a lead guard, and applies defensive pressure (1.4 SPG)",
        ],
        "weaknesses": [
            "Somewhat measured 6'6\" wingspan limits defensive ceiling vs. longer guards; wingspan-to-height ratio is modest",
            "Not a lottery-level athlete — limited burst and vertical will require elite decision-making to compensate at the next level",
        ],
        "comp": "Tyus Jones / Jordan Nwora mold",
        "grade": "C+",
        "summary": "A polished, high-IQ lead guard whose feel and skill translate well to the NBA game — average athleticism limits upside but the floor is a reliable backup-starter with immediate playmaking value.",
    },
    "Chris Cenac Jr.": {
        "measurements": "6'10.25\" / 240 lbs / 7'5\" wingspan / 9'0\" standing reach",
        "stats": "9.5 PPG, 7.9 RPG, 0.5 BPG — Houston (48.5% FG, 33.3% 3PT, 37 GP, 24.8 MPG)",
        "strengths": [
            "Elite rebounder with a 7'5\" wingspan and strong motor — 7.9 RPG in under 25 minutes is elite per-minute production",
            "Versatile stretch big concept with a developing mid-range game and 2.4 three-point attempts per game — spacing upside in a modern NBA system",
            "Mobile for his size; finishes with authority at the rim and has enough touch to exploit mismatches in short-roll situations",
        ],
        "weaknesses": [
            "Inconsistent motor — had games where he was outworked by bigger opponents despite superior physical tools",
            "Frame and strength still developing at 240 lbs; can be overpowered by more established NBA centers in the post",
        ],
        "comp": "Mitchell Robinson",
        "grade": "C+",
        "summary": "A long-armed, mobile freshman big with elite rebounding instincts and stretch upside — motor consistency and frame development will determine whether he sticks as a rotation center.",
    },
    "Jayden Quaintance": {
        "measurements": "6'10.5\" / 255 lbs / 7'3\"+ wingspan",
        "stats": "8.8 PPG, 7.5 RPG, 2.4 BPG, 1.0 SPG — Kentucky (53.0% FG, 45.2% FT, 28 GP)",
        "strengths": [
            "Arguably the best interior defender in the entire draft — elite timing, instincts, and 7'3\"+ wingspan that alters everything within 8 feet of the basket",
            "Physically ready body at 255 lbs with natural vertical athleticism and explosive lateral quickness unusual for his size",
            "Combo of 2.4 BPG and 1.0 SPG at 6'10.5\" signals a generational defensive archetype — projects as a switchable anchor",
        ],
        "weaknesses": [
            "45.2% FT shooting is a serious liability — can be hacked off the floor in late-game situations and limits offensive deployment",
            "Recurring knee injuries (torn ACL/meniscus as a freshman, limited availability as a sophomore) — medical clearance is the top concern",
        ],
        "comp": "Jaren Jackson Jr.",
        "grade": "C+",
        "summary": "The draft's best interior defender and most physically imposing big when healthy — catastrophic free-throw shooting and injury history make him a high-risk, high-reward bet at pick 23.",
    },
    "Meleek Thomas": {
        "measurements": "6'3\" / 189 lbs / 6'6.75\" wingspan",
        "stats": "15.6 PPG, 3.8 RPG, 2.5 APG, 1.5 SPG — Arkansas (43.0% FG, 41.6% 3PT, 84.3% FT, 30.5 MPG)",
        "strengths": [
            "Elite shooter with a quick, high release — 41.6% from three on heavy SEC volume; one of the best movement shooters in the freshman class",
            "Assertive, energy-forward scorer who plays with great competitive intensity and can generate offense off movement",
            "Strong defensive instincts (1.5 SPG) and positive IQ — efficient, low-waste offensive profile with 43% FG",
        ],
        "weaknesses": [
            "Thin 189 lb frame with modest wingspan — contact knocks him off his line around the rim, and NBA physicality will demand strength gains",
            "Not an elite athlete; limited burst makes self-creation in isolation situations inconsistent at the next level",
        ],
        "comp": "Jordan Poole",
        "grade": "C+",
        "summary": "A proven SEC shooter with elite three-point accuracy and SEC-tested efficiency — physical limitations cap his defensive ceiling but the shooting translates immediately as a bench scoring punch.",
    },
    "Dailyn Swain": {
        "measurements": "6'8\" / 225 lbs",
        "stats": "17.3 PPG, 7.5 RPG, 3.6 APG, 1.6 SPG — Texas (63.6% TS, 34.4% 3PT, 10.5 BPM, 36 GP — led team in all five categories)",
        "strengths": [
            "Elite slasher at wing size — generated 10.5 rim attempts per 100 possessions (92nd percentile) with 64.5% finishing at the rim, almost all unassisted",
            "Versatile two-way wing who led his team in scoring, rebounding, assists, steals, and minutes — rare multi-dimensional impact at 6'8\"",
            "Strong defensive tools with length, positional size, and IQ to guard 1-through-4; SEC Newcomer of the Year recognition",
        ],
        "weaknesses": [
            "Three-point shooting (34.4%) is a significant concern for a player who primarily operates from the perimeter — defenses will sag and take away drives if he doesn't improve",
            "Turnover-prone as a playmaker; pass decisions need refinement before he can be trusted as a primary initiator in NBA half-court offense",
        ],
        "comp": "Josh Hart",
        "grade": "C+",
        "summary": "A relentless, physically imposing multi-tool wing who impacts every phase of the game — the lack of a reliable three-point shot is the one thing standing between him and top-20 value.",
    },
    "Luigi Suigo": {
        "measurements": "7'2.75\" / 289 lbs / 7'5.5\" wingspan / 9'6\" standing reach",
        "stats": "8.5 PPG, 5.3 RPG, 1.0 BPG — Mega Basket Serbia (56.0% FG, 30.0% 3PT, 18.1 MPG, 32 GP)",
        "strengths": [
            "Rare 7'3\" frame with a 7'5.5\" wingspan and 9'6\" standing reach — freakish size with developing touch gives him a uniquely high ceiling as a modern stretch center",
            "Promising shooting mechanics for his size — confident pick-and-pop jumper and clean release with positive trajectory in 3PT attempts",
            "Rim protection upside is elite at full development — shot alteration and space-eating presence comparable to Kristaps Porzingis archetype",
        ],
        "weaknesses": [
            "Very raw — limited to 18 minutes/game in the Adriatic League as a teenager; still years away from reliable NBA rotation minutes",
            "30% three-point shooting and modest per-game production at a young level underscore how much refinement remains ahead",
        ],
        "comp": "Kristaps Porzingis",
        "grade": "C+",
        "summary": "A 19-year-old Italian giant with Porzingis-level physical tools and the upside of a transcendent stretch center — an extreme raw talent play requiring significant patience.",
    },
    "Koa Peat": {
        "measurements": "6'7\" / 245 lbs / 6'11.25\" wingspan / 8'8\" standing reach",
        "stats": "14.1 PPG, 5.6 RPG, 2.6 APG — Arizona (55.7% TS, 8.8 BPM, 36 GP)",
        "strengths": [
            "Physically dominant at 245 lbs with explosive athleticism (37.5\" max vertical) — too quick for slower bigs, too strong for smaller defenders",
            "Reliable mid-range pull-up and turnaround jumper; finishes through contact with authority and draws fouls at a high rate",
            "High-motor forward who impacts the game with hustle, positioning, and competitive intensity on every possession",
        ],
        "weaknesses": [
            "Three-point shooting is a critical concern — took only 20 attempts all season (35%) and went 6-of-25 in the NBA combine spot-up drill, among the worst combine shooting performances in recent history",
            "Without a reliable perimeter game, NBA defenses can sag into the paint and limit his driving lanes — spacing limitations reduce his offensive ceiling significantly",
        ],
        "comp": "Zion Williamson (minus the elite scoring burst)",
        "grade": "C+",
        "summary": "A physically imposing, explosive forward with power and athleticism that projects well — the near-total absence of three-point shooting is a legitimate scheme concern that slid him to the back end of the first.",
    },
    "Isaiah Evans": {
        "measurements": "6'6\" / 180 lbs / 6'10\" wingspan",
        "stats": "15.0 PPG, 3.2 RPG, 1.3 APG, 0.7 BPG — Duke (43.3% FG, 36.1% 3PT, 59.0% TS, 9.8 BPM)",
        "strengths": [
            "Smooth, natural shooter with a quick, high release point over defenders — ranked top-5 in ACC with 2.7 threes per game made; excellent shooting versatility off movement and the catch",
            "6'10\" wingspan gives him legitimate two-way wing projection — length to contest on defense and shoot over smaller guards on offense",
            "ACC-proven scoring production at 15 PPG with elite efficiency markers — polished offensive skill set for a freshman",
        ],
        "weaknesses": [
            "Only 180 lbs — will need significant strength development to avoid being bullied by physical NBA wings on the perimeter and in transition",
            "Scoring impact wanes in stretches when the shot is off; limited secondary skills (creation, playmaking) to contribute when shooting is cold",
        ],
        "comp": "Cam Johnson",
        "grade": "C+",
        "summary": "A natural shooter with elite length and proven ACC production — frame development is the only thing preventing an immediate NBA scoring role as a 3-and-D wing.",
    },
    "Sergio de Larrea": {
        "measurements": "6'6\" / 204 lbs / 6'9\" wingspan / 8'4.25\" standing reach",
        "stats": "9.3 PPG, 3.6 APG — Valencia ACB (43.0% 3PT, 17 MPG); 5.4 PPG, 4.4 APG — 2025 EuroBasket (Spain NT)",
        "strengths": [
            "Elite perimeter shooter who connects at 43% from three in the ACB — one of the best shooters in this draft at 20 years old with NBA-ready mechanics",
            "6'6\" with a 6'9\" wingspan and exceptional IQ — fits perfectly in modern positionless offenses as a shot-making playmaker who reads defenses early",
            "EuroBasket-tested international pedigree and pick-and-roll feel — 4.4 APG for the Spanish national team signals legitimate vision and passing instincts",
        ],
        "weaknesses": [
            "Below-average athlete — limited horizontal and vertical explosiveness makes separation off the dribble unreliable and creates significant defensive issues on and off the ball",
            "Low usage role in Valencia (17 MPG) means durability and performance under heavy NBA minutes remains unproven",
        ],
        "comp": "Austin Reaves",
        "grade": "C+",
        "summary": "A polished 20-year-old Spanish shooter and playmaker with elite three-point marksmanship and real passing vision — pedestrian athleticism is the swing factor between solid role player and fringe rotation piece.",
    },
    "Henri Veesaar": {
        "measurements": "6'11.25\" / 227 lbs / 7'2\" wingspan / 9'3\" standing reach",
        "stats": "17.0 PPG, 8.7 RPG, 2.1 APG, 1.2 BPG — North Carolina (61.0% FG, 43.0% 3PT, 31.3 MPG)",
        "strengths": [
            "Elite interior-plus-perimeter combination — 61% FG and 43% from three is an exceptionally rare shooting profile for a 6'11\" center",
            "Skilled post scorer with a reliable hook shot, soft touch, and ability to shoot over defenders at his height; also flashes passing instincts out of the mid-post",
            "Fits the modern floor-spacing center template with size, rebounding (8.7 RPG), and shot-making that every front office is hunting",
        ],
        "weaknesses": [
            "Limited athleticism and modest defensive metrics — block rate and mobility suggest he will be a drop-coverage specialist rather than a switchable anchor",
            "227 lbs is a thin frame for a seven-footer; will need mass to hold position against elite NBA bigs",
        ],
        "comp": "Kelly Olynyk",
        "grade": "C+",
        "summary": "The draft's best shooting center at 43% from three with size, skill, and feel for the game — an Estonian big with an immediately translatable offensive skill set who profiles as a long-term starter.",
    },
}

# ─── Parlay builder ───────────────────────────────────────────────────────────
# PP_PAYOUTS, _PP_ODDS_IMPLIED, _PP_NBA_STAT_COL, _PP_MLB_HIT_COL,
# _PP_MLB_PIT_COL, _PP_PITCHER_TYPES now come from parlay_model (see import
# block near the top of this file).

_pp_cache: dict = {}
_pp_cache_ts: dict = {}
_pp_last_error: dict = {}
_PP_CACHE_TTL = 300  # 5 minutes; only store non-empty results

def get_prizepicks_with_team(league_id: int = 7) -> pd.DataFrame:
    """PrizePicks fetch capturing team, status, and game label for SGP grouping.
    Uses module-level cache so empty results are never cached."""
    now = time.time()
    cached = _pp_cache.get(league_id)
    if cached is not None and not cached.empty and now - _pp_cache_ts.get(league_id, 0) < _PP_CACHE_TTL:
        return cached

    for attempt in range(3):
        try:
            if attempt:
                time.sleep(1.5)
            url = f"https://api.prizepicks.com/projections?league_id={league_id}&per_page=500&single_stat=true"
            resp = requests.get(url, headers=_PP_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            payload = resp.json()

            # Build player map
            player_map = {}
            for item in payload.get("included", []):
                if item.get("type") == "new_player":
                    a = item.get("attributes", {})
                    player_map[item["id"]] = {
                        "name": a.get("display_name", ""),
                        "team": a.get("team", a.get("team_name", "")),
                    }

            # Build game map from 'game' included items
            game_map = {}
            for item in payload.get("included", []):
                if item.get("type") == "game":
                    a    = item.get("attributes", {})
                    meta = a.get("metadata", {})
                    gteams = meta.get("game_info", {}).get("teams", {})
                    away   = gteams.get("away", {}).get("abbreviation", "")
                    home   = gteams.get("home", {}).get("abbreviation", "")
                    label  = f"{away} @ {home}" if away and home else ""
                    game_map[item["id"]] = {
                        "label":      label,
                        "start_time": a.get("start_time", ""),
                    }

            rows = []
            for proj in payload.get("data", []):
                if proj.get("type") != "projection":
                    continue
                attrs  = proj.get("attributes", {})
                if attrs.get("status", "pre_game") in _PP_DEAD_STATUSES:
                    continue
                rels   = proj.get("relationships", {})
                pid    = rels.get("new_player", {}).get("data", {}).get("id", "")
                gid    = rels.get("game", {}).get("data", {}).get("id", "")
                pinfo  = player_map.get(pid, {})
                ginfo  = game_map.get(gid, {})
                rows.append({
                    "player_name": pinfo.get("name", ""),
                    "team":        pinfo.get("team", ""),
                    "stat_type":   attrs.get("stat_type", ""),
                    "line_score":  attrs.get("line_score"),
                    "odds_type":   attrs.get("odds_type", "standard"),
                    "game_id":     gid,
                    "game_label":  ginfo.get("label", ""),
                    "start_time":  ginfo.get("start_time", attrs.get("start_time", "")),
                })
            df = pd.DataFrame(rows)
            result = df[df["player_name"] != ""] if not df.empty else df
            if not result.empty:
                _pp_cache[league_id] = result
                _pp_cache_ts[league_id] = now
                return result
        except Exception as _e:
            _pp_last_error[league_id] = str(_e)
            continue
    # Return stale cache if available
    stale = _pp_cache.get(league_id)
    if stale is not None:
        return stale
    # Last resort: fall back to the lighter cached endpoint (no team/game columns)
    try:
        fb = get_prizepicks_lines(league_id=league_id)
        if not fb.empty:
            fb = fb.copy()
            for _col in ("team", "game_label", "game_id", "start_time"):
                if _col not in fb.columns:
                    fb[_col] = ""
            return fb
    except Exception:
        pass
    return pd.DataFrame()

# ─── Sportsbook helpers ──────────────────────────────────────────────────────

def _american_to_implied(odds: int) -> float:
    """Convert American odds integer to implied over-probability (0–1)."""
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def _american_to_decimal(odds: int) -> float:
    """American odds to decimal (European) odds."""
    if odds >= 0:
        return (odds / 100.0) + 1.0
    return (100.0 / abs(odds)) + 1.0


def _decimal_to_american(dec: float) -> int:
    """Decimal odds to American odds."""
    if dec >= 2.0:
        return int(round((dec - 1.0) * 100))
    if dec > 1.0:
        return int(round(-100.0 / (dec - 1.0)))
    return -9999

_DK_NBA_STAT_MAP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "Threes": "3-PT Made", "3-Point Field Goals Made": "3-PT Made",
    "Blocked Shots": "Blocked Shots", "Steals": "Steals", "Turnovers": "Turnovers",
    "Pts + Rebs + Asts": "Pts+Rebs+Asts", "Points + Rebounds + Assists": "Pts+Rebs+Asts",
    "Pts + Rebs": "Pts+Rebs", "Points + Rebounds": "Pts+Rebs",
    "Pts + Asts": "Pts+Asts", "Points + Assists": "Pts+Asts",
}
_DK_MLB_STAT_MAP = {
    "Hits": "Hits", "Home Runs": "Home Runs", "RBIs": "RBIs",
    "Total Bases": "Total Bases", "Runs": "Runs Scored",
    "Stolen Bases": "Stolen Bases", "Strikeouts": "Strikeouts",
    "Walks": "Walks", "Pitcher Strikeouts": "Pitcher Strikeouts",
    "Pitcher Walks": "Walks Allowed", "Earned Runs Allowed": "Earned Runs Allowed",
    "Hits Allowed": "Hits Allowed",
}
_FD_NBA_STAT_MAP = {
    "Player Points": "Points", "Player Rebounds": "Rebounds",
    "Player Assists": "Assists", "Player Threes": "3-PT Made",
    "Player Blocks": "Blocked Shots", "Player Steals": "Steals",
    "Player Blocks + Steals": "Blocked Shots",
    "Player Pts + Rebs + Asts": "Pts+Rebs+Asts",
    "Player Pts + Rebs": "Pts+Rebs", "Player Pts + Asts": "Pts+Asts",
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
}
_FD_MLB_STAT_MAP = {
    "Batter Hits": "Hits", "Batter Home Runs": "Home Runs",
    "Batter RBIs": "RBIs", "Batter Total Bases": "Total Bases",
    "Batter Runs": "Runs Scored", "Batter Stolen Bases": "Stolen Bases",
    "Batter Strikeouts": "Strikeouts", "Batter Walks": "Walks",
    "Pitcher Strikeouts": "Pitcher Strikeouts",
    "Pitcher Walks Allowed": "Walks Allowed",
    "Pitcher Earned Runs Allowed": "Earned Runs Allowed",
}

# ── Underdog Fantasy stat maps ───────────────────────────────────────────────
_UD_NBA_STAT_MAP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "3-Pointers Made": "3-PT Made", "Blocks": "Blocked Shots",
    "Steals": "Steals", "Turnovers": "Turnovers",
    "Pts + Rebs + Asts": "Pts+Rebs+Asts",
    "Points + Rebounds": "Pts+Rebs", "Points + Assists": "Pts+Asts",
}
_UD_MLB_STAT_MAP = {
    "Hits": "Hits", "Home Runs": "Home Runs", "RBIs": "RBIs",
    "Runs": "Runs", "Total Bases": "Total Bases",
    "Stolen Bases": "Stolen Bases", "Singles": "Singles", "Doubles": "Doubles",
    "Hits + Runs + RBIs": "Hits+Runs+RBIs",
    "Batter Strikeouts": "Hitter Strikeouts", "Batter Walks": "Walks",
    "Strikeouts": "Pitcher Strikeouts", "Hits Allowed": "Hits Allowed",
    "Earned Runs Allowed": "Earned Runs Allowed",
    "Walks Allowed": "Walks Allowed", "Pitching Outs": "Pitching Outs",
}

# Dashboard stat selector → Underdog stat_type (as returned by get_underdog_props)
_UD_NBA_PROP_LOOKUP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "PRA": "Pts+Rebs+Asts", "3PM": "3-PT Made",
    "Steals": "Steals", "Blocks": "Blocked Shots", "Turnovers": "Turnovers",
}
_UD_MLB_HITTER_LOOKUP = {
    "H": "Hits", "HR": "Home Runs", "RBI": "RBIs",
    "K": "Hitter Strikeouts", "BB": "Walks",
}
_UD_MLB_PITCHER_LOOKUP = {
    "K": "Pitcher Strikeouts",
    "ER": "Earned Runs Allowed",
    "BB": "Walks Allowed",
    "H": "Hits Allowed",
}

_ud_cache: dict = {}
_ud_cache_ts: dict = {}
_UD_CACHE_TTL = 300

def get_underdog_props(sport: str = "nba") -> pd.DataFrame:
    """Fetch player props from Underdog Fantasy (free, no auth required)."""
    now = time.time()
    cached = _ud_cache.get(sport)
    if cached is not None and not cached.empty and now - _ud_cache_ts.get(sport, 0) < _UD_CACHE_TTL:
        return cached

    if sport == "nba":
        sport_id, stat_map = "NBA", _UD_NBA_STAT_MAP
    elif sport == "wnba":
        sport_id, stat_map = "WNBA", _UD_NBA_STAT_MAP  # WNBA uses same stat names
    else:
        sport_id, stat_map = "MLB", _UD_MLB_STAT_MAP
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Accept": "application/json",
        }
        resp = requests.get(
            "https://api.underdogfantasy.com/beta/v5/over_under_lines",
            headers=headers, timeout=15,
        )
        if resp.status_code != 200:
            return _ud_cache.get(sport, pd.DataFrame())
        d = resp.json()

        player_map     = {p["id"]: p for p in d.get("players", [])}
        appearance_map = {a["id"]: a for a in d.get("appearances", [])}
        game_map       = {g["id"]: g for g in d.get("games", [])}

        rows = []
        for line in d.get("over_under_lines", []):
            if line.get("status") != "active":
                continue
            ou        = line.get("over_under", {})
            app_stat  = ou.get("appearance_stat", {})
            app_id    = app_stat.get("appearance_id", "")
            stat_type = stat_map.get(app_stat.get("display_stat", "").strip())
            if stat_type is None:
                continue
            app = appearance_map.get(app_id, {})
            if not app:
                continue
            player = player_map.get(app.get("player_id", ""), {})
            if not player or player.get("sport_id") != sport_id:
                continue
            try:
                stat_value = float(line.get("stat_value", 0))
            except Exception:
                continue
            game       = game_map.get(app.get("match_id"), {})
            name       = f"{player.get('first_name','')} {player.get('last_name','')}".strip()
            if not name:
                continue
            game_title = game.get("abbreviated_title", "")
            team = ""
            if " @ " in game_title:
                away_abbr, home_abbr = game_title.split(" @ ", 1)
                team = away_abbr if app.get("team_id") == game.get("away_team_id") else home_abbr
            _opts = line.get("options", [])
            over_opt  = next((o for o in _opts if o.get("choice") == "higher"), None)
            under_opt = next((o for o in _opts if o.get("choice") == "lower"), None)
            if not over_opt:
                continue
            try:
                american = int(str(over_opt.get("american_price", "-110")).replace("+", ""))
            except Exception:
                american = -110
            # De-vig against the under price: the raw over probability carries the book's
            # margin and runs high, which biases the model toward the over.
            _imp_over = _american_to_implied(american)
            _imp_under = None
            if under_opt is not None:
                try:
                    _imp_under = _american_to_implied(
                        int(str(under_opt.get("american_price", "-110")).replace("+", ""))
                    )
                except Exception:
                    _imp_under = None
            rows.append({
                "player_name": name, "team": team,
                "stat_type": stat_type, "line_score": stat_value,
                "odds_type": "standard", "american_odds": american,
                "implied_prob": round(_pm.devig_two_way(_imp_over, _imp_under), 3),
                "game_id": str(app.get("match_id", "")),
                "game_label": game_title,
                "start_time": game.get("scheduled_at", ""),
                "sportsbook": "Underdog",
            })
        df = pd.DataFrame(rows)
        if df.empty:
            return _ud_cache.get(sport, pd.DataFrame())
        result = df[df["player_name"] != ""].drop_duplicates(["player_name", "stat_type"])
        _ud_cache[sport] = result
        _ud_cache_ts[sport] = now
        return result
    except Exception:
        return _ud_cache.get(sport, pd.DataFrame())

# ── DraftKings public sportsbook endpoint (no auth, unofficial) ─────────────
_DK_MLB_EVENT_GROUP = 84240
_DK_NBA_EVENT_GROUP = 42648
_DK_CACHE_TTL = 300  # 5 minutes

_DK_MLB_MARKET_MAP = {
    "Batter Hits": "Hits",
    "Batter Home Runs": "Home Runs",
    "Batter RBIs": "RBIs",
    "Batter Total Bases": "Total Bases",
    "Batter Stolen Bases": "Stolen Bases",
    "Batter Walks": "Walks",
    "Pitcher Strikeouts": "Pitcher Strikeouts",
    "Pitcher Hits Allowed": "Hits Allowed",
    "Pitcher Earned Runs Allowed": "Earned Runs Allowed",
    "Pitcher Walks Allowed": "Walks Allowed",
    "Pitcher Outs Recorded": "Pitching Outs",
    # Aliases DK sometimes uses
    "Hits": "Hits",
    "Home Runs": "Home Runs",
    "RBIs": "RBIs",
    "Total Bases": "Total Bases",
    "Stolen Bases": "Stolen Bases",
    "Strikeouts": "Pitcher Strikeouts",
}

_dk_cache: dict = {}
_dk_cache_ts: dict = {}


def get_draftkings_props(sport: str = "mlb") -> pd.DataFrame:
    """Fetch MLB/NBA player props from DraftKings' public (unofficial) sportsbook API.
    No API key required. Falls back through NJ → IL → PA state endpoints."""
    now = time.time()
    cached = _dk_cache.get(sport)
    if cached is not None and not cached.empty and now - _dk_cache_ts.get(sport, 0) < _DK_CACHE_TTL:
        return cached

    group_id = _DK_MLB_EVENT_GROUP if sport == "mlb" else _DK_NBA_EVENT_GROUP
    market_map = _DK_MLB_MARKET_MAP
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    resp = None
    for state in ("US-NJ-SB", "US-IL-SB", "US-PA-SB"):
        try:
            url = f"https://sportsbook.draftkings.com/sites/{state}/api/v1/eventgroup/{group_id}/full"
            r = requests.get(url, params={"format": "json"}, headers=headers, timeout=20)
            if r.status_code == 200:
                resp = r
                break
        except Exception:
            continue

    if resp is None:
        return _dk_cache.get(sport, pd.DataFrame())

    # DK returns HTML when their API is inaccessible from server-side requests
    ct = resp.headers.get("Content-Type", "")
    if "html" in ct or resp.text.lstrip().startswith("<!"):
        return _dk_cache.get(sport, pd.DataFrame())

    try:
        data = resp.json()
    except Exception:
        return _dk_cache.get(sport, pd.DataFrame())

    eg = data.get("eventGroup", {})

    # Build event id → metadata map
    event_map = {}
    for ev in eg.get("events", []):
        eid = str(ev.get("eventId", ""))
        event_map[eid] = {
            "game_label": ev.get("name", ""),
            "start_time": ev.get("startDate", ""),
        }

    rows = []
    for cat in eg.get("offerCategories", []):
        for sub in cat.get("offerSubcategories", []):
            stat_type = market_map.get(sub.get("name", ""))
            if not stat_type:
                continue

            # offers can be a list of lists (one inner list per event) or flat
            flat_offers = []
            for item in sub.get("offers", []):
                if isinstance(item, list):
                    flat_offers.extend(item)
                elif isinstance(item, dict):
                    flat_offers.append(item)

            for offer in flat_offers:
                ev_id = str(offer.get("eventId", ""))
                ev_info = event_map.get(ev_id, {})
                game_label = ev_info.get("game_label", "")
                start_time = ev_info.get("start_time", "")

                for outcome in offer.get("outcomes", []):
                    if outcome.get("label", "").lower() != "over":
                        continue
                    player_name = outcome.get("participant", "") or outcome.get("metadata", {}).get("player", "")
                    line_val = outcome.get("line")
                    odds_str = str(outcome.get("oddsAmerican", "-110"))
                    if not player_name or line_val is None:
                        continue
                    try:
                        american = int(odds_str.replace("+", ""))
                    except Exception:
                        american = -110
                    try:
                        line_score = float(line_val)
                    except Exception:
                        continue
                    rows.append({
                        "player_name": player_name,
                        "team": "",
                        "stat_type": stat_type,
                        "line_score": line_score,
                        "odds_type": "standard",
                        "american_odds": american,
                        "implied_prob": round(_american_to_implied(american), 3),
                        "game_id": ev_id,
                        "game_label": game_label,
                        "start_time": start_time,
                        "sportsbook": "DraftKings",
                    })

    df = pd.DataFrame(rows)
    if df.empty:
        return _dk_cache.get(sport, pd.DataFrame())
    result = df[df["player_name"] != ""].drop_duplicates(["player_name", "stat_type"])
    _dk_cache[sport] = result
    _dk_cache_ts[sport] = now
    return result


# ── SharpAPI (free tier: DK + FD, no credit card, 12 req/min) ───────────────
# Sign up at sharpapi.io — free tier gives DraftKings + FanDuel player props.
# Add your key to .streamlit/secrets.toml as SHARP_API_KEY = "sk_live_xxx"

_SHARP_API_BASE = "https://api.sharpapi.io/api/v1"
_SHARP_CACHE_TTL = 300  # 5 minutes

# SharpAPI market_type → our normalized stat_type
_SHARP_MLB_MARKET_MAP = {
    "pitcher_strikeouts":      "Pitcher Strikeouts",
    "pitcher_walks_allowed":   "Walks Allowed",
    "pitcher_earned_runs":     "Earned Runs Allowed",
    "hitting_hits":            "Hits",
    "hitting_home_runs":       "Home Runs",
    "hitting_rbis":            "RBIs",
    "hitting_total_bases":     "Total Bases",
    "hitting_doubles":         "Doubles",
    "hitting_runs_scored":     "Runs",
    # Alternate label spellings SharpAPI may use
    "batter_hits":             "Hits",
    "batter_home_runs":        "Home Runs",
    "batter_rbis":             "RBIs",
    "batter_total_bases":      "Total Bases",
}

_sharp_cache: dict = {}
_sharp_cache_ts: dict = {}


def _get_sharp_api_key() -> str:
    try:
        k = st.secrets.get("SHARP_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("SHARP_API_KEY", "")


def get_sharpapi_props(sport: str = "mlb", sportsbook: str = "DraftKings") -> pd.DataFrame:
    """Fetch MLB/NBA player props from SharpAPI (free tier: DK + FD, no CC required).
    Register at sharpapi.io and add SHARP_API_KEY to .streamlit/secrets.toml."""
    api_key = _get_sharp_api_key()
    if not api_key:
        return pd.DataFrame()

    _SB_KEY_MAP = {
        "DraftKings": "draftkings",
        "FanDuel":    "fanduel",
        "BetMGM":     "betmgm",
        "Caesars":    "caesars",
    }
    sb_key = _SB_KEY_MAP.get(sportsbook, sportsbook.lower())
    league = "mlb" if sport == "mlb" else "nba"
    cache_key = f"{league}_{sb_key}"

    now = time.time()
    cached = _sharp_cache.get(cache_key)
    if cached is not None and not cached.empty and now - _sharp_cache_ts.get(cache_key, 0) < _SHARP_CACHE_TTL:
        return cached

    market_map = _SHARP_MLB_MARKET_MAP

    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        rows = []
        offset = 0
        limit = 200
        while True:
            resp = requests.get(
                f"{_SHARP_API_BASE}/odds",
                headers=headers,
                params={
                    "league":      league,
                    "sportsbook":  sb_key,
                    "market":      "player_props",
                    "limit":       limit,
                    "offset":      offset,
                },
                timeout=15,
            )
            if resp.status_code == 401:
                _sharp_cache[f"_err_{cache_key}"] = "invalid_key"
                break
            if resp.status_code == 429:
                # Rate limited — return whatever we have so far
                break
            if resp.status_code != 200:
                break

            data = resp.json()
            items = data.get("data", [])
            if not items:
                break

            for item in items:
                mtype = item.get("market_type", "")
                stat_type = market_map.get(mtype)
                if not stat_type:
                    continue
                player_name = item.get("selection", "")
                if not player_name:
                    continue
                # SharpAPI returns the line embedded in selection or a separate field
                line_val = item.get("line") or item.get("point") or item.get("handicap")
                if line_val is None:
                    continue
                try:
                    line_score = float(line_val)
                except Exception:
                    continue
                american = item.get("odds_american", -110)
                try:
                    american = int(american)
                except Exception:
                    american = -110
                implied = item.get("odds_probability")
                if implied is None:
                    implied = round(_american_to_implied(american), 3)
                else:
                    implied = round(float(implied), 3)

                home = item.get("home_team", "")
                away = item.get("away_team", "")
                game_label = f"{away} @ {home}" if away and home else ""
                rows.append({
                    "player_name":  player_name,
                    "team":         "",
                    "stat_type":    stat_type,
                    "line_score":   line_score,
                    "odds_type":    "standard",
                    "american_odds": american,
                    "implied_prob": implied,
                    "game_id":      str(item.get("event_id", "")),
                    "game_label":   game_label,
                    "start_time":   str(item.get("updated_at", "")),
                    "sportsbook":   sportsbook,
                })

            pagination = data.get("pagination", {})
            if not pagination.get("has_more", False):
                break
            offset += limit
            time.sleep(0.1)  # stay well under 12 req/min

        if not rows:
            return _sharp_cache.get(cache_key, pd.DataFrame())

        df = pd.DataFrame(rows)
        result = df[df["player_name"] != ""].drop_duplicates(["player_name", "stat_type"])
        _sharp_cache[cache_key] = result
        _sharp_cache_ts[cache_key] = now
        return result
    except Exception:
        return _sharp_cache.get(cache_key, pd.DataFrame())


# ── The Odds API (aggregates DK + FD player props) ──────────────────────────
# Free tier: 500 credits/month. Credits = bookmakers × markets per event call.
# We use 1 bookmaker × 5 NBA markets or 4 MLB markets = 5-4 credits per event.
# Events list is free. Cache 30 min and filter to today's games to stay within limit.
_toa_cache: dict = {}
_toa_cache_ts: dict = {}
_TOA_CACHE_TTL = 1800  # 30 minutes — conserve free-tier credits

# Slim market lists: 1 credit per market per event call
_TOA_NBA_MARKETS = [
    "player_points", "player_rebounds", "player_assists",
    "player_threes", "player_points_rebounds_assists",
]
_TOA_MLB_MARKETS = [
    "batter_hits", "batter_home_runs", "pitcher_strikeouts", "batter_total_bases",
]
_TOA_NBA_MARKET_MAP = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "3-PT Made",
    "player_blocks": "Blocked Shots",
    "player_steals": "Steals",
    "player_turnovers": "Turnovers",
    "player_points_rebounds_assists": "Pts+Rebs+Asts",
    "player_points_rebounds": "Pts+Rebs",
    "player_points_assists": "Pts+Asts",
}
_TOA_MLB_MARKET_MAP = {
    "batter_hits": "Hits",
    "batter_home_runs": "Home Runs",
    "batter_rbis": "RBIs",
    "batter_total_bases": "Total Bases",
    "batter_stolen_bases": "Stolen Bases",
    "batter_walks": "Walks",
    "pitcher_strikeouts": "Pitcher Strikeouts",
    "pitcher_walks": "Walks Allowed",
    "pitcher_earned_runs": "Earned Runs Allowed",
}

_ODDS_API_KEY_DEFAULT = "3810f18fda845575c1185b2a0bc55405"

def _get_odds_api_key() -> str:
    try:
        k = st.secrets.get("ODDS_API_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("ODDS_API_KEY", _ODDS_API_KEY_DEFAULT)

_TOA_CREDITS_REMAINING: dict = {}  # tracks x-requests-remaining per api key

def get_the_odds_api_props(sport: str = "nba", sportsbook: str = "DraftKings") -> pd.DataFrame:
    """Fetch DK / FD / Bet365 player props via The Odds API (the-odds-api.com).
    Free tier: 500 credits/month. Credits = markets × bookmakers per call."""
    api_key = _get_odds_api_key()
    if not api_key:
        return pd.DataFrame()

    cache_key = f"{sport}_{sportsbook}"
    now = time.time()
    cached = _toa_cache.get(cache_key)
    if cached is not None and not cached.empty and now - _toa_cache_ts.get(cache_key, 0) < _TOA_CACHE_TTL:
        return cached

    _BK_KEY_MAP = {"DraftKings": "draftkings", "FanDuel": "fanduel", "Bet365": "bet365"}
    bk_key = _BK_KEY_MAP.get(sportsbook, "fanduel")
    sport_key = "basketball_nba" if sport == "nba" else "baseball_mlb"
    markets_list = _TOA_NBA_MARKETS if sport == "nba" else _TOA_MLB_MARKETS
    market_map = _TOA_NBA_MARKET_MAP if sport == "nba" else _TOA_MLB_MARKET_MAP

    try:
        # Step 1: get today's events (free — no credit cost)
        events_resp = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/events",
            params={"apiKey": api_key},
            timeout=15,
        )
        if events_resp.status_code == 401:
            _toa_cache[f"_err_{cache_key}"] = "invalid_key"
            return _toa_cache.get(cache_key, pd.DataFrame())
        if events_resp.status_code == 422:
            _toa_cache[f"_err_{cache_key}"] = "quota_exceeded"
            return _toa_cache.get(cache_key, pd.DataFrame())
        if events_resp.status_code != 200:
            return _toa_cache.get(cache_key, pd.DataFrame())
        rem = events_resp.headers.get("x-requests-remaining")
        if rem is not None:
            _TOA_CREDITS_REMAINING[api_key] = int(rem)
        all_events = events_resp.json()

        # Filter to games starting within next 24 hours to limit credit spend
        from datetime import timezone
        cutoff = datetime.now(timezone.utc) + timedelta(hours=24)
        today_events = []
        for ev in all_events:
            ct = ev.get("commence_time", "")
            try:
                dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                if dt <= cutoff:
                    today_events.append(ev)
            except Exception:
                today_events.append(ev)
        # Safety cap: never fetch more than 12 events per call
        today_events = today_events[:12]

        rows = []
        for event in today_events:
            ev_id = event.get("id", "")
            game_label = f"{event.get('away_team', '')} @ {event.get('home_team', '')}"
            start_time = event.get("commence_time", "")

            try:
                props_resp = requests.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{ev_id}/odds",
                    params={
                        "apiKey": api_key,
                        "regions": "us",
                        "markets": ",".join(markets_list),
                        "bookmakers": bk_key,
                        "oddsFormat": "american",
                    },
                    timeout=15,
                )
            except Exception:
                continue
            if props_resp.status_code != 200:
                continue

            event_data = props_resp.json()
            for bm in event_data.get("bookmakers", []):
                if bm.get("key") != bk_key:
                    continue
                for mkt in bm.get("markets", []):
                    stat_type = market_map.get(mkt.get("key"))
                    if not stat_type:
                        continue
                    for outcome in mkt.get("outcomes", []):
                        if outcome.get("name", "").lower() != "over":
                            continue
                        player_name = outcome.get("description", "")
                        point = outcome.get("point")
                        price = outcome.get("price")
                        if not player_name or point is None or price is None:
                            continue
                        try:
                            american = int(price)
                        except Exception:
                            american = -110
                        rows.append({
                            "player_name": player_name,
                            "team": "",
                            "stat_type": stat_type,
                            "line_score": float(point),
                            "odds_type": "standard",
                            "american_odds": american,
                            "implied_prob": round(_american_to_implied(american), 3),
                            "game_id": ev_id,
                            "game_label": game_label,
                            "start_time": start_time,
                            "sportsbook": sportsbook,
                        })
        df = pd.DataFrame(rows)
        if df.empty:
            return _toa_cache.get(cache_key, pd.DataFrame())
        result = df[df["player_name"] != ""].drop_duplicates(["player_name", "stat_type"])
        _toa_cache[cache_key] = result
        _toa_cache_ts[cache_key] = now
        return result
    except Exception:
        return _toa_cache.get(cache_key, pd.DataFrame())

def get_sportsbook_props(sport: str = "nba", sportsbook: str = "PrizePicks") -> pd.DataFrame:
    """Unified prop fetch for any supported sportsbook. Returns normalized DataFrame."""
    league_id = 7 if sport == "nba" else 2
    if sportsbook == "PrizePicks":
        df = get_prizepicks_with_team(league_id=league_id)
        if not df.empty:
            df = df.copy()
            if "american_odds" not in df.columns:
                dk_equiv = {"goblin": -162, "standard": -100, "demon": 162}
                df["american_odds"] = df["odds_type"].map(dk_equiv).fillna(-100).astype(int)
                df["implied_prob"] = df["odds_type"].map(_PP_ODDS_IMPLIED).fillna(0.50)
            if "sportsbook" not in df.columns:
                df["sportsbook"] = "PrizePicks"
        return df
    elif sportsbook == "Underdog":
        return get_underdog_props(sport)
    elif sportsbook == "DraftKings":
        # Try SharpAPI first (free, no CC); fall back to direct endpoint (currently blocked)
        if _get_sharp_api_key():
            df = get_sharpapi_props(sport, "DraftKings")
            if not df.empty:
                return df
        return get_draftkings_props(sport)
    elif sportsbook == "FanDuel":
        # FanDuel's own public web API: no key, no quota, and it quotes both sides of
        # every prop so the de-vig has a real under price to work with. Shared with
        # daily_parlay_gen via parlay_model so the two can't drift.
        df = _pm.fetch_fanduel(sport)
        if not df.empty:
            return df
        # Metered fallbacks only if the free path yields nothing.
        if _get_sharp_api_key():
            df = get_sharpapi_props(sport, "FanDuel")
            if not df.empty:
                return df
        return get_the_odds_api_props(sport, "FanDuel")
    elif sportsbook == "Bet365":
        return get_the_odds_api_props(sport, "Bet365")
    return pd.DataFrame()

@st.cache_data(ttl=3600)
def _load_calibration(sport: str | None = None) -> dict:
    """Load sport-specific per-stat calibration factors from the parlay tracker (cached 1h)."""
    try:
        return parlay_tracker.get_calibration(sport=sport)
    except Exception:
        return {}



def _fallback_nba_legs(stat_types: list = None) -> list:
    """Generate parlay legs from top NBA players using historical averages as lines."""
    if stat_types is None:
        stat_types = ["Points", "Rebounds", "Assists", "3-PT Made"]
    COL_MAP = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "3-PT Made": "FG3M",
               "Blocked Shots": "BLK", "Steals": "STL", "Turnovers": "TOV"}
    TOP = [
        "LeBron James", "Stephen Curry", "Kevin Durant", "Giannis Antetokounmpo",
        "Jayson Tatum", "Joel Embiid", "Nikola Jokic", "Luka Doncic",
        "Damian Lillard", "Anthony Davis", "Devin Booker", "Shai Gilgeous-Alexander",
        "Donovan Mitchell", "Jaylen Brown", "Anthony Edwards", "Jalen Brunson",
        "Paolo Banchero", "Tyrese Haliburton", "Bam Adebayo", "Trae Young",
    ]
    legs = []

    # Pre-warm 2024-25 gamelogs in parallel (complete season, fast, no rate-limit risk).
    # We fetch 2024-25 only here — hit rate is computed inline using the same df,
    # so we never trigger a slow 2025-26 API call in the fallback path.
    _fb_ids = [(p, get_player_id(p)) for p in TOP]
    _fb_ids = [(p, pid) for p, pid in _fb_ids if pid]
    with ThreadPoolExecutor(max_workers=5) as _fb_ex:
        _fb_futs = [_fb_ex.submit(get_gamelogs, pid, ("2024-25",)) for _, pid in _fb_ids]
        for _ff in _fb_futs:
            try:
                _ff.result(timeout=30)
            except Exception:
                pass

    for player in TOP:
        pid = get_player_id(player)
        if not pid:
            continue
        # Use 2024-25 directly — it's pre-warmed and complete. Avoids slow 2025-26 API call.
        df = get_gamelogs(pid, ("2024-25",))
        if df.empty:
            continue
        for stat in stat_types:
            col = COL_MAP.get(stat)
            if not col or col not in df.columns:
                continue
            vals = df[col].values[-20:]
            if len(vals) < 3:
                continue
            line = round(float(vals.mean()) * 0.88, 1)
            # Inline hit rate using the same df — avoids _nba_hit_rate's internal get_gamelogs call
            last30 = vals[-30:] if len(vals) >= 5 else vals
            last10 = vals[-10:] if len(vals) >= 10 else vals
            n = len(last30)
            if n < 3:
                continue
            r30 = float((last30 > line).sum()) / len(last30)
            r10 = float((last10 > line).sum()) / len(last10) if len(last10) >= 5 else r30
            rate = round(min(0.97, max(0.03, 0.7 * (0.6 * r10 + 0.4 * r30) + 0.3 * 0.5)), 3)
            legs.append({
                "player_name": player, "team": "", "stat_type": stat,
                "line_score": line, "odds_type": "standard", "american_odds": -110,
                "game_id": "", "game_label": "Historical", "hit_rate": rate, "sample_n": n,
            })
    return legs

def _fallback_mlb_legs(stat_types: list = None, cal: dict = None) -> list:
    """Generate parlay legs from top MLB players using historical averages as lines."""
    if stat_types is None:
        stat_types = ["Hits", "Pitcher Strikeouts"]
    if cal is None:
        cal = {}

    hitter_stats = [s for s in stat_types if s not in _PP_PITCHER_TYPES]
    pitcher_stats = [s for s in stat_types if s in _PP_PITCHER_TYPES]

    # Collect all players from rosters first (cached after first run)
    teams_list = get_mlb_teams()[:20]
    all_hitters: list = []  # [(player_dict, team_abbr)]
    all_pitchers: list = []
    for team in teams_list:
        try:
            hitters, pitchers = get_mlb_roster(team["id"])
            all_hitters.extend((h, team["abbr"]) for h in hitters[:4])
            all_pitchers.extend((p, team["abbr"]) for p in pitchers[:2])
        except Exception:
            continue

    # Pre-warm game logs in parallel so the processing loop below hits cache
    _fb_warm: list = []
    if hitter_stats:
        _fb_warm.extend((h["id"], False) for h, _ in all_hitters)
    if pitcher_stats:
        _fb_warm.extend((p["id"], True) for p, _ in all_pitchers)

    if _fb_warm:
        with ThreadPoolExecutor(max_workers=8) as _fbex:
            _fbfuts = {
                _fbex.submit(
                    get_mlb_pitching_logs if _isp else get_mlb_hitting_logs,
                    _pid, ("2025", "2026")
                ): _pid
                for _pid, _isp in _fb_warm
            }
            try:
                for _ in as_completed(_fbfuts, timeout=60):
                    pass
            except Exception:
                pass

    # Process legs — all log fetches are now cache hits
    legs = []
    for h, abbr in all_hitters:
        for stat in hitter_stats:
            col = _PP_MLB_HIT_COL.get(stat)
            if not col:
                continue
            try:
                df_h = get_mlb_hitting_logs(h["id"], ("2025", "2026"))
                if df_h.empty or col not in df_h.columns:
                    continue
                vals = df_h[col].values[-20:]
                if len(vals) < 1:
                    continue
                line = round(float(vals.mean()) * 0.85, 1)
                rate, n = _mlb_hit_rate(h["name"], stat, line, odds_type="standard",
                                        cal_factor=cal.get(stat, 1.0))
                if n == 0:
                    rate, n = 0.55, 1
                legs.append({
                    "player_name": h["name"], "team": abbr,
                    "stat_type": stat, "line_score": line,
                    "odds_type": "standard", "american_odds": -110,
                    "game_id": "", "game_label": "Historical", "hit_rate": rate, "sample_n": n,
                })
            except Exception:
                continue

    for p, abbr in all_pitchers:
        for stat in pitcher_stats:
            col = _PP_MLB_PIT_COL.get(stat)
            if not col:
                continue
            try:
                df_p = get_mlb_pitching_logs(p["id"], ("2025", "2026"))
                if df_p.empty or col not in df_p.columns:
                    continue
                vals = df_p[col].values[-10:]
                if len(vals) < 1:
                    continue
                line = round(float(vals.mean()) * 0.85, 1)
                rate, n = _mlb_hit_rate(p["name"], stat, line, odds_type="standard",
                                        cal_factor=cal.get(stat, 1.0))
                if n == 0:
                    rate, n = 0.52, 1
                legs.append({
                    "player_name": p["name"], "team": abbr,
                    "stat_type": stat, "line_score": line,
                    "odds_type": "standard", "american_odds": -110,
                    "game_id": "", "game_label": "Historical", "hit_rate": rate, "sample_n": n,
                })
            except Exception:
                continue

    return legs


@st.cache_data(ttl=3600, show_spinner=False)
def _compute_todays_best_plays_mlb() -> list:
    """Score today's Underdog MLB props; return top 5 plays (2 pitchers + 3 hitters)."""
    try:
        ud = get_underdog_props("mlb")
        if ud.empty:
            return []
        FD_STATS = {"Hits", "Home Runs", "Pitcher Strikeouts", "Hits Allowed"}
        df = ud[ud["stat_type"].isin(FD_STATS)].copy()
        df = df.drop_duplicates(["player_name", "stat_type"]).reset_index(drop=True)
        scored = []
        for _, row in df.iterrows():
            try:
                rate, n = _mlb_hit_rate(
                    row["player_name"], row["stat_type"], float(row["line_score"] or 0),
                    odds_type=str(row.get("odds_type", "standard") or "standard"),
                    implied_override=float(
                        row.get("implied_prob", -1.0) if row.get("implied_prob") is not None else -1.0
                    ),
                )
                if n < 5:
                    continue
                direction = "OVER" if rate >= 0.5 else "UNDER"
                edge = max(rate, 1.0 - rate)
                scored.append({
                    "player": row["player_name"],
                    "team": str(row.get("team", "") or ""),
                    "stat": row["stat_type"],
                    "line": float(row["line_score"]),
                    "hit_rate": rate,
                    "edge": edge,
                    "direction": direction,
                    "n": n,
                    "game": str(row.get("game_label", "") or ""),
                    "is_pitcher": row["stat_type"] in _PP_PITCHER_TYPES,
                })
            except Exception:
                continue
        if not scored:
            return []
        pitchers = sorted([s for s in scored if s["is_pitcher"]], key=lambda x: x["edge"], reverse=True)
        hitters  = sorted([s for s in scored if not s["is_pitcher"]], key=lambda x: x["edge"], reverse=True)
        best = pitchers[:2] + hitters[:3]
        best.sort(key=lambda x: x["edge"], reverse=True)
        return best[:5]
    except Exception:
        return []


_ODDS_BADGE_HTML = {
    "goblin":   "<span style='font-size:0.62rem;font-weight:700;letter-spacing:0.08em;"
                "color:#22c55e;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.3);"
                "border-radius:4px;padding:1px 5px;margin-left:5px;'>GOBLIN</span>",
    "demon":    "<span style='font-size:0.62rem;font-weight:700;letter-spacing:0.08em;"
                "color:#f87171;background:rgba(248,113,113,0.12);border:1px solid rgba(248,113,113,0.3);"
                "border-radius:4px;padding:1px 5px;margin-left:5px;'>DEMON</span>",
    "standard": "",
}


def _parlay_card_html(parlay: dict, kind: str = "safe") -> str:
    """Render a parlay dict as an HTML card."""
    legs_html = ""
    for leg in parlay["legs"]:
        r  = leg["hit_rate"]
        n  = leg["sample_n"]
        ot = leg.get("odds_type", "standard")
        if n < 3:
            rcls, rlbl = "pl-rate-none", "—"
        else:
            rcls = "pl-rate-hi" if r >= 0.60 else ("pl-rate-mid" if r >= 0.45 else "pl-rate-lo")
            rlbl = f"{r*100:.0f}% ({n}g)"
        american = leg.get("american_odds")
        imp_prob = leg.get("implied_prob", -1.0)
        sb = leg.get("sportsbook", "PrizePicks")
        if sb in ("DraftKings", "FanDuel", "Underdog") and american is not None and imp_prob > 0:
            # Real sportsbook: show American odds + implied%
            sb_lbl = "UD" if sb == "Underdog" else sb[:2].upper()
            odds_disp = f"+{american}" if american > 0 else str(american)
            odds_suffix = f"<span style='color:#6b7280;font-size:0.7rem;margin-left:6px;'>{odds_disp} &nbsp;{sb_lbl}&nbsp;{int(imp_prob*100)}%</span>"
            odds_badge = ""
        else:
            implied_pct = int(_PP_ODDS_IMPLIED.get(ot, 0.50) * 100)
            odds_badge  = _ODDS_BADGE_HTML.get(ot, "")
            odds_suffix = f"<span style='color:#6b7280;font-size:0.7rem;margin-left:6px;'>PP&nbsp;{implied_pct}%</span>"
        legs_html += (
            f"<div class='pl-leg'>"
            f"<span class='pl-name'>{leg['player_name']}{odds_badge}</span>"
            f"<span class='pl-stat'>{leg['stat_type']} &gt; {leg['line_score']}"
            f"{odds_suffix}</span>"
            f"<span class='pl-rate {rcls}'>{rlbl}</span>"
            f"</div>"
        )
    # Header: combined true odds for real-sportsbook parlays, PP multiplier otherwise
    sb_first = parlay["legs"][0].get("sportsbook", "PrizePicks") if parlay["legs"] else "PrizePicks"
    if sb_first != "PrizePicks":
        dec_combined = 1.0
        for leg in parlay["legs"]:
            dec_combined *= _american_to_decimal(leg.get("american_odds", -110))
        combined_am = _decimal_to_american(dec_combined)
        odds_tag = f"+{combined_am}" if combined_am >= 0 else str(combined_am)
        payout_span = f"<span class='pl-tag'>{parlay['n']}-Pick &nbsp;·&nbsp; {odds_tag}</span>"
    else:
        payout_span = f"<span class='pl-tag'>{parlay['n']}-Pick &nbsp;·&nbsp; {parlay['payout']}x</span>"
    ev_str = f"+{parlay['ev']:.2f}" if parlay['ev'] >= 0 else f"{parlay['ev']:.2f}"
    # EV here is on the calibrated probability, so a negative number means the model
    # itself expects this bet to lose. Say so on the card rather than leaving the reader
    # to notice the minus sign.
    recommended = parlay.get("recommended", parlay["ev"] > 0)
    if recommended:
        rec_badge = "<span class='pl-rec'>✓ +EV</span>"
        card_extra = " pl-card-rec"
    else:
        rec_badge = "<span class='pl-norec'>−EV · not recommended</span>"
        card_extra = " pl-card-norec"
    return (
        f"<div class='pl-card pl-card-{kind}{card_extra}'>"
        f"<div class='pl-header'>"
        f"<span class='pl-prob'>{parlay['prob']*100:.1f}%</span>"
        f"{payout_span}"
        f"<span class='pl-ev'>EV {ev_str}</span>"
        f"{rec_badge}"
        f"</div>"
        f"{legs_html}"
        f"</div>"
    )


def _grouped_tabs(groups: list):
    """
    Build two-level tabs but hand back a flat iterator in declaration order.

    Eleven top-level tabs is past the point anyone scans them. Nesting them cuts the top
    row to five without touching a single page body: callers still pull tabs off an
    iterator in the same order, so `with tab_parlays:` keeps working unchanged.

    A group with no children renders as a plain leaf tab, which keeps Home on the top
    row rather than burying the landing page one click deep.

    groups: [(group_name, [child_name, ...]), ...]
    """
    outer = st.tabs([name for name, _children in groups])
    flat = []
    for container, (_name, children) in zip(outer, groups):
        if not children:
            flat.append(container)
            continue
        with container:
            flat.extend(st.tabs(children))
    return iter(flat)


def _render_parlay_pair(safe: list, value: list, key: str, sport: str | None = None,
                        safe_label: str = "Safe Parlays — Most Likely to Hit",
                        value_label: str = "Value Parlays — Best Payout Potential",
                        safe_empty: str = "Not enough legs with sufficient historical data. Try adding more stat types.",
                        value_empty: str = "No additional value parlays found beyond safe parlays."):
    """
    Render the safe/value columns behind one recommended-only toggle.

    The +EV badge alone left a recommendation findable only by eye — with a hundred
    parlays on the board you scroll and squint at badges. The toggle is what turns the
    flag into a decision, and it is the only control on this page that changes what you
    would actually bet, so it defaults to on whenever there is anything to recommend.
    """
    rec_total = sum(1 for p in safe + value if p.get("recommended"))
    total = len(safe) + len(value)

    # Say which correction is actually behind the EV. Parlay-level calibration only
    # counts parlays this model predicted, so right after a model change there is none,
    # and EV rests on leg calibration and the real payout alone. Claiming "the
    # calibrated model" in that state overstates what stands behind the number.
    _pcal_live = bool(parlay_tracker.get_parlay_calibration(sport=sport)) if sport else False

    _hc1, _hc2 = st.columns([3, 1])
    with _hc1:
        if total:
            tone = "pl-summary-good" if rec_total else "pl-summary-none"
            basis = ("calibrated model" if _pcal_live
                     else "leg-calibrated model (no parlay-level calibration yet)")
            msg = (f"<strong>{rec_total}</strong> of {total} parlays are positive-EV "
                   f"on the {basis}"
                   if rec_total else
                   f"None of {total} parlays clear break-even on the {basis}")
            st.markdown(f"<p class='pl-summary {tone}'>{msg}</p>", unsafe_allow_html=True)
    with _hc2:
        only_rec = st.toggle("Recommended only", value=bool(rec_total),
                             key=f"rec_only_{key}",
                             help="Hide parlays the calibrated model expects to lose.")

    if only_rec:
        safe = [p for p in safe if p.get("recommended")]
        value = [p for p in value if p.get("recommended")]

    _cs, _cv = st.columns(2)
    with _cs:
        st.markdown(f"<p class='pl-section-label'>{safe_label}</p>", unsafe_allow_html=True)
        if safe:
            for _p in safe:
                st.markdown(_parlay_card_html(_p, "safe"), unsafe_allow_html=True)
        else:
            st.caption("No recommended safe parlays — untick the filter to see the rest."
                       if only_rec else safe_empty)
    with _cv:
        st.markdown(f"<p class='pl-section-label'>{value_label}</p>", unsafe_allow_html=True)
        if value:
            for _p in value:
                st.markdown(_parlay_card_html(_p, "value"), unsafe_allow_html=True)
        else:
            st.caption("No recommended value parlays — untick the filter to see the rest."
                       if only_rec else value_empty)


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

@st.cache_data(ttl=7200)
def get_team_first_basket_history(team_abbr, num_games=20):
    """Pull first basket data via ESPN play-by-play (no nba_api PBP calls needed)."""
    _HDR = {"User-Agent": "Mozilla/5.0"}
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_abbr}/schedule"
        events = requests.get(url, timeout=8, headers=_HDR).json().get("events", [])
        completed = [
            e for e in events
            if e.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed", False)
        ]
        completed = sorted(completed, key=lambda e: e.get("date", ""), reverse=True)[:num_games]
    except Exception:
        return pd.DataFrame()

    results = []
    for event in completed:
        try:
            event_id = event["id"]
            comp = event["competitions"][0]
            comps = comp["competitors"]
            home_c = next((c for c in comps if c["homeAway"] == "home"), comps[0])
            away_c = next((c for c in comps if c["homeAway"] == "away"), comps[-1])
            home_abbr = home_c["team"]["abbreviation"]
            away_abbr = away_c["team"]["abbreviation"]
            is_home = home_abbr.upper() == team_abbr.upper()
            matchup_fmt = f"{team_abbr} vs. {away_abbr}" if is_home else f"{team_abbr} @ {home_abbr}"
            def _sc(raw):
                if isinstance(raw, dict):
                    return float(raw.get("value", 0) or 0)
                return float(raw or 0)
            home_score_final = _sc(home_c.get("score", 0))
            away_score_final = _sc(away_c.get("score", 0))
            wl = "W" if (
                (is_home and home_score_final > away_score_final) or
                (not is_home and away_score_final > home_score_final)
            ) else "L"
            game_date = event.get("date", "")[:10]

            pbp_data = requests.get(
                f"https://cdn.espn.com/core/nba/playbyplay?gameId={event_id}&xhr=1",
                timeout=10, headers=_HDR,
            ).json()
            plays = pbp_data.get("gamepackageJSON", {}).get("plays", [])

            tip_team = first_scorer = shot_type = team_scored_first = None
            prev_home = prev_away = 0

            for play in plays:
                ptype = (play.get("type", {}) or {}).get("text", "")
                period_n = (play.get("period", {}) or {}).get("number", 0)
                play_team_id = (play.get("team", {}) or {}).get("id", "")
                text = play.get("text", "") or ""
                h_score = int(play.get("homeScore", prev_home) or prev_home)
                a_score = int(play.get("awayScore", prev_away) or prev_away)
                score_val = play.get("scoreValue", 0) or 0

                # Tip-off: first play with a known team ID in period 1
                if tip_team is None and period_n == 1 and play_team_id:
                    tip_team = home_abbr if play_team_id == home_c["team"].get("id", "") else away_abbr

                # First scoring event: detect via homeScore/awayScore change
                if first_scorer is None and score_val > 0:
                    scorer_is_home = h_score > prev_home
                    team_scored_first = scorer_is_home == is_home  # True if selected team scored first
                    tip_scorer_abbr = home_abbr if scorer_is_home else away_abbr
                    # Extract player name from text: "Name makes ..."
                    name_part = text.split(" makes ")[0].split(" misses ")[0].strip() if text else "Unknown"
                    first_scorer = name_part or "Unknown"
                    if "Free Throw" in ptype or "free throw" in text.lower():
                        shot_type = "Free Throw"
                    elif score_val == 3:
                        shot_type = "3-Pointer"
                    else:
                        shot_type = "2-Pointer"

                prev_home, prev_away = h_score, a_score
                if first_scorer is not None and tip_team is not None:
                    break

            results.append({
                "Game Date": game_date,
                "Matchup": matchup_fmt,
                "W/L": wl,
                "Tip Winner": tip_team or "—",
                "Tip Won": tip_team.upper() == team_abbr.upper() if tip_team else None,
                "First Scorer": first_scorer or "—",
                "Shot Type": shot_type or "—",
                "Team Scored First": team_scored_first,
            })
            time.sleep(0.15)
        except Exception:
            continue

    return pd.DataFrame(results)

# ══════════════════════════════════════════════════════════════════════════════
# MLB DATA FUNCTIONS  (MLB Stats API — free, no key required)
# ══════════════════════════════════════════════════════════════════════════════
MLB_SEASON = "2026"
MLB_SEASONS = ["2024", "2025", "2026"]
MLB_BASE = "https://statsapi.mlb.com/api/v1"

# Park HR factors relative to 100 (neutral). Source: 2024-2026 season estimates.
_MLB_PARK_HR_FACTORS: dict = {
    "Coors Field": 140, "Great American Ball Park": 125,
    "Camden Yards": 120, "Oriole Park at Camden Yards": 120,
    "Yankee Stadium": 118, "Globe Life Field": 115, "Citizens Bank Park": 112,
    "Rogers Centre": 112, "Chase Field": 110, "Fenway Park": 108,
    "American Family Field": 105, "Wrigley Field": 105, "Truist Park": 103,
    "Guaranteed Rate Field": 103, "Minute Maid Park": 102, "Nationals Park": 102,
    "Busch Stadium": 100, "Progressive Field": 100, "Angel Stadium": 99,
    "Dodger Stadium": 98, "Target Field": 98, "Citi Field": 96, "PNC Park": 96,
    "Tropicana Field": 95, "T-Mobile Park": 90, "loanDepot park": 90,
    "Kauffman Stadium": 88, "Comerica Park": 85, "Petco Park": 82, "Oracle Park": 76,
    "Sutter Health Park": 120, "Las Vegas Ballpark": 120,
}

# Lat/lon for Open-Meteo weather lookup (retractable dome venues get no wind boost)
_MLB_VENUE_COORDS: dict = {
    "Coors Field": (39.7559, -104.9942), "Great American Ball Park": (39.0979, -84.5082),
    "Camden Yards": (39.2838, -76.6217), "Oriole Park at Camden Yards": (39.2838, -76.6217),
    "Yankee Stadium": (40.8296, -73.9262),
    "Globe Life Field": (32.7473, -97.0829), "Fenway Park": (42.3467, -71.0972),
    "Chase Field": (33.4453, -112.0667), "American Family Field": (43.0280, -87.9712),
    "Wrigley Field": (41.9484, -87.6553), "Truist Park": (33.8908, -84.4678),
    "PNC Park": (40.4468, -80.0057), "Busch Stadium": (38.6226, -90.1928),
    "Guaranteed Rate Field": (41.8300, -87.6339), "Kauffman Stadium": (39.0514, -94.4803),
    "Angel Stadium": (33.8003, -117.8827), "Citi Field": (40.7571, -73.8458),
    "Citizens Bank Park": (39.9061, -75.1665), "Rogers Centre": (43.6414, -79.3894),
    "Minute Maid Park": (29.7572, -95.3555), "Dodger Stadium": (34.0739, -118.2400),
    "Oracle Park": (37.7786, -122.3893), "Petco Park": (32.7076, -117.1570),
    "Comerica Park": (42.3390, -83.0485), "T-Mobile Park": (47.5914, -122.3325),
    "Progressive Field": (41.4962, -81.6852), "loanDepot park": (25.7781, -80.2197),
    "Target Field": (44.9817, -93.2784), "Tropicana Field": (27.7683, -82.6534),
    "Nationals Park": (38.8730, -77.0074),
    "Sutter Health Park": (38.5800, -121.5142),
    "Las Vegas Ballpark": (36.1699, -115.1398),
}
_MLB_DOME_VENUES = {
    "Chase Field", "Globe Life Field", "American Family Field",
    "Rogers Centre", "Minute Maid Park", "Tropicana Field", "loanDepot park",
}

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


@st.cache_data(ttl=1800, show_spinner=False)
def _get_venue_weather(venue: str) -> dict:
    """Current weather for an MLB venue via Open-Meteo (free, no key)."""
    coords = _MLB_VENUE_COORDS.get(venue)
    if not coords:
        return {}
    lat, lon = coords
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m"
            f"&wind_speed_unit=mph&temperature_unit=fahrenheit&forecast_days=1"
        )
        cur = requests.get(url, timeout=8).json().get("current", {})
        return {
            "temp_f":   round(float(cur.get("temperature_2m", 70)), 1),
            "wind_mph": round(float(cur.get("wind_speed_10m", 0)), 1),
            "wind_deg": int(cur.get("wind_direction_10m", 0)),
            "humidity": int(cur.get("relative_humidity_2m", 50)),
        }
    except Exception:
        return {}


def _hr_context_boost(venue: str, weather: dict) -> float:
    """
    Multiplicative boost to HR probability from park factor + weather. 1.0 = neutral.
    Park: ±20% from factor vs 100 baseline.
    Temp: ±1% per 5°F vs 70°F baseline.
    Wind: up to +8% at 20 mph (suppressed for dome venues).
    """
    park_f = _MLB_PARK_HR_FACTORS.get(venue, 100)
    park_boost = (park_f - 100) / 100 * 0.20
    temp_f = weather.get("temp_f", 70)
    wind_mph = weather.get("wind_mph", 0)
    temp_boost = max(-0.06, min(0.08, (temp_f - 70) / 5 * 0.01))
    wind_boost = 0.0 if venue in _MLB_DOME_VENUES else min(0.08, wind_mph / 20 * 0.08)
    return 1.0 + park_boost + temp_boost + wind_boost


@st.cache_data(ttl=3600, show_spinner=False)
def _pitcher_hr_per9(pitcher_id: int) -> float:
    """HR/9 for a pitcher this season. Returns MLB average (1.1) if unavailable."""
    try:
        stats = get_mlb_pitcher_season_stats(pitcher_id)
        ip = float(stats.get("inningsPitched", 0) or 0)
        hr = int(stats.get("homeRuns", 0) or 0)
        if ip >= 20:
            return round(hr / ip * 9, 2)
    except Exception:
        pass
    return 1.1


@st.cache_data(ttl=3600, show_spinner=False)
def _get_player_hand(player_id: int, side: str = "bat") -> str:
    """Returns 'L', 'R', or 'S' for switch hitter. side='bat' or 'pitch'."""
    try:
        resp = requests.get(f"{MLB_BASE}/people/{player_id}", timeout=8)
        person = (resp.json().get("people") or [{}])[0]
        if side == "bat":
            return person.get("batSide", {}).get("code", "R")
        return person.get("pitchHand", {}).get("code", "R")
    except Exception:
        return "R"


@st.cache_data(ttl=900, show_spinner=False)
def _score_hitter_for_hr(
    player_id: int,
    opp_pitcher_id,
    venue: str,
    weather: dict,
) -> dict:
    """
    Composite HR probability for a hitter in today's game context.
    Combines HR rate from logs, BvP HR history, pitcher HR/9,
    park factor, weather, and handedness platoon split.
    """
    try:
        df = get_mlb_hitting_logs(player_id, ("2025", "2026"))
    except Exception:
        return {}
    if df.empty or "HR" not in df.columns or len(df) < 5:
        return {}

    games_played = len(df)
    last20 = df["HR"].values[-20:]
    last15 = df["HR"].values[-15:] if games_played >= 15 else df["HR"].values
    last10 = df["HR"].values[-10:] if games_played >= 10 else df["HR"].values
    last7  = df["HR"].values[-7:]  if games_played >= 7  else df["HR"].values
    last5  = df["HR"].values[-5:]  if games_played >= 5  else df["HR"].values
    total_hrs = int(df["HR"].sum())

    # Skip non-HR hitters: 0 HRs in last 15 games (given enough sample)
    if games_played >= 15 and int((last15 > 0).sum()) == 0:
        return {}

    # Fraction of games with ≥1 HR — weight very recent form most heavily
    r20 = float((last20 > 0).sum()) / len(last20)
    r10 = float((last10 > 0).sum()) / max(len(last10), 1)
    r5  = float((last5  > 0).sum()) / max(len(last5),  1)
    hist_rate = 0.35 * r5 + 0.40 * r10 + 0.25 * r20

    # Cold streak penalty: 0 HRs in last 7 games → significantly reduce score
    if int((last7 > 0).sum()) == 0:
        hist_rate *= 0.60

    # BvP HR adjustment
    bvp_boost = 0.0
    bvp_info = {}
    if opp_pitcher_id:
        try:
            bvp = _mlb_bvp_stats(player_id, int(opp_pitcher_id))
            ab = bvp.get("ab", 0)
            if ab >= 10:
                bvp_hr_ab = bvp.get("hr", 0) / ab
                season_ab = float(df["AB"].sum()) if "AB" in df.columns else games_played * 4
                season_hr_ab = total_hrs / max(season_ab, 1)
                if season_hr_ab > 0:
                    factor = bvp_hr_ab / season_hr_ab
                    bvp_boost = min(0.08, max(-0.06, (factor - 1.0) * 0.05))
                bvp_info = {"ab": ab, "hr": bvp.get("hr", 0)}
        except Exception:
            pass

    # Pitcher HR vulnerability
    p_hr9 = 1.1
    if opp_pitcher_id:
        p_hr9 = _pitcher_hr_per9(int(opp_pitcher_id))
    pitcher_boost = min(0.12, max(-0.08, (p_hr9 - 1.1) / 1.0 * 0.10))

    # Handedness platoon bonus
    platoon_boost = 0.0
    try:
        b_hand = _get_player_hand(player_id, "bat")
        if opp_pitcher_id:
            p_hand = _get_player_hand(int(opp_pitcher_id), "pitch")
            if (b_hand in ("L", "R")) and b_hand != p_hand:
                platoon_boost = 0.03
            elif b_hand == "S":
                platoon_boost = 0.015
    except Exception:
        pass

    context_mult = _hr_context_boost(venue, weather)
    raw = (hist_rate + bvp_boost + pitcher_boost + platoon_boost) * context_mult
    score = round(min(0.97, max(0.01, raw)), 3)

    return {
        "score": score, "hist_rate": round(hist_rate, 3),
        "last10_hr": int((last10 > 0).sum()), "last20_hr": int((last20 > 0).sum()),
        "total_hrs": total_hrs, "games_played": games_played,
        "bvp_boost": round(bvp_boost, 3), "bvp_info": bvp_info,
        "pitcher_hr9": p_hr9, "pitcher_boost": round(pitcher_boost, 3),
        "park_factor": _MLB_PARK_HR_FACTORS.get(venue, 100),
        "temp_f": weather.get("temp_f", 70), "wind_mph": weather.get("wind_mph", 0),
        "platoon_boost": round(platoon_boost, 3), "context_mult": round(context_mult, 3),
    }


@st.cache_data(ttl=900, show_spinner=False)
def _build_hr_power_picks(top_n: int = 6, cal_factor: float = 1.0) -> list:
    """
    Top HR candidates for today's games. Scores every active hitter using
    park factor, weather, pitcher HR/9, BvP HR history, and platoon splits.
    cal_factor is applied to all raw scores to correct for model overconfidence
    (derived from resolved parlay legs via get_calibration).
    """
    try:
        games = get_mlb_today_with_pitchers()
    except Exception:
        return []
    if not games:
        return []

    mlb_teams = get_mlb_teams()
    team_id_map = {t["abbr"]: t["id"] for t in mlb_teams}

    candidates = []
    for game in games:
        venue = game.get("venue", "")
        weather = _get_venue_weather(venue)
        for side in ("home", "away"):
            opp = "away" if side == "home" else "home"
            team_abbr = game.get(f"{side}_abbr", "")
            opp_pitcher_id = game.get(f"{opp}_p_id")
            opp_pitcher_name = game.get(f"{opp}_pitcher", "TBD")
            tid = team_id_map.get(team_abbr)
            if not tid:
                continue
            try:
                hitters, _ = get_mlb_roster(tid)
            except Exception:
                continue
            for h in hitters[:15]:
                pid = _mlb_player_id_by_name(h["name"])
                if not pid:
                    continue
                result = _score_hitter_for_hr(int(pid), opp_pitcher_id, venue, weather)
                if not result:
                    continue
                calibrated = round(min(0.97, max(0.01, result["score"] * cal_factor)), 3)
                candidates.append({
                    "player_name": h["name"], "team": team_abbr, "venue": venue,
                    "opp_pitcher": opp_pitcher_name, "opp_pitcher_id": opp_pitcher_id,
                    "game_label": f"{game.get('away_abbr','')} @ {game.get('home_abbr','')}",
                    "weather": weather,
                    **result,
                    "score": calibrated,
                })

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Enforce diversity: max 1 pick per team so a single favorable
    # matchup (park + weak pitcher) can't fill all top slots.
    seen_teams: set = set()
    diverse = []
    for c in candidates:
        if c["team"] not in seen_teams:
            seen_teams.add(c["team"])
            diverse.append(c)
        if len(diverse) == top_n:
            break
    return diverse


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

def _scoreboard_date():
    """Current game-day date in CST (UTC-6). Day rolls at 2am CST."""
    from datetime import timezone, timedelta as _td
    cst = datetime.now(timezone(_td(hours=-6)))
    if cst.hour < 2:
        cst = cst - _td(days=1)
    return cst.strftime("%Y%m%d")

@st.cache_data(ttl=60)
def get_nba_scoreboard(game_date: str):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={game_date}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        games = []
        for ev in resp.json().get("events", []):
            comp = ev["competitions"][0]
            comps = comp["competitors"]
            home = next((c for c in comps if c["homeAway"] == "home"), comps[0])
            away = next((c for c in comps if c["homeAway"] == "away"), comps[-1])
            st_type = ev["status"]["type"]
            games.append({
                "away": away["team"]["abbreviation"],
                "home": home["team"]["abbreviation"],
                "away_score": away.get("score", ""),
                "home_score": home.get("score", ""),
                "status": st_type.get("shortDetail", st_type.get("description", "")),
                "live": st_type.get("state", "") == "in",
                "completed": st_type.get("completed", False),
            })
        return games
    except Exception:
        return []

@st.cache_data(ttl=60)
def get_mlb_scoreboard(game_date: str):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={game_date}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        games = []
        for ev in resp.json().get("events", []):
            comp = ev["competitions"][0]
            comps = comp["competitors"]
            home = next((c for c in comps if c["homeAway"] == "home"), comps[0])
            away = next((c for c in comps if c["homeAway"] == "away"), comps[-1])
            st_type = ev["status"]["type"]
            games.append({
                "away": away["team"]["abbreviation"],
                "home": home["team"]["abbreviation"],
                "away_score": away.get("score", ""),
                "home_score": home.get("score", ""),
                "status": st_type.get("shortDetail", st_type.get("description", "")),
                "live": st_type.get("state", "") == "in",
                "completed": st_type.get("completed", False),
            })
        return games
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_nba_scoreboard_full(game_date: str):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={game_date}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        out = []
        for ev in resp.json().get("events", []):
            comp = ev["competitions"][0]
            comps = comp["competitors"]
            home = next((c for c in comps if c["homeAway"] == "home"), comps[0])
            away = next((c for c in comps if c["homeAway"] == "away"), comps[-1])
            out.append({
                "name": ev.get("shortName", ev.get("name", "")),
                "away": away["team"]["displayName"],
                "away_abbr": away["team"]["abbreviation"],
                "away_record": ((away.get("records") or [{}])[0].get("summary", "")),
                "home": home["team"]["displayName"],
                "home_abbr": home["team"]["abbreviation"],
                "home_record": ((home.get("records") or [{}])[0].get("summary", "")),
                "status": ev["status"]["type"].get("shortDetail", ""),
                "venue": comp.get("venue", {}).get("fullName", ""),
            })
        return out
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_mlb_today_with_pitchers():
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = f"{MLB_BASE}/schedule?sportId=1&date={today}&hydrate=probablePitcher,team,record,linescore"
        resp = requests.get(url, timeout=10)
        out = []
        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                at = game["teams"]["away"]
                ht = game["teams"]["home"]
                ap = at.get("probablePitcher", {})
                hp = ht.get("probablePitcher", {})
                ar = at.get("leagueRecord", {})
                hr = ht.get("leagueRecord", {})
                out.append({
                    "away": at["team"]["name"],
                    "away_abbr": at["team"].get("abbreviation", ""),
                    "away_record": f"{ar.get('wins',0)}-{ar.get('losses',0)}",
                    "home": ht["team"]["name"],
                    "home_abbr": ht["team"].get("abbreviation", ""),
                    "home_record": f"{hr.get('wins',0)}-{hr.get('losses',0)}",
                    "away_pitcher": ap.get("fullName", "TBD"),
                    "away_p_id": ap.get("id"),
                    "home_pitcher": hp.get("fullName", "TBD"),
                    "home_p_id": hp.get("id"),
                    "venue": game.get("venue", {}).get("name", ""),
                })
        for g in out:
            g["away_p_stats"] = get_mlb_pitcher_season_stats(g["away_p_id"]) if g["away_p_id"] else {}
            g["home_p_stats"] = get_mlb_pitcher_season_stats(g["home_p_id"]) if g["home_p_id"] else {}
        return out
    except Exception:
        return []

@st.cache_data(ttl=900)
def get_sport_news(sport="nba"):
    import re as _re
    # ESPN JSON news API — works from cloud servers (no scraping)
    _ESPN_SPORT = {"nba": "basketball/nba", "mlb": "baseball/mlb", "wnba": "basketball/wnba"}
    sport_path = _ESPN_SPORT.get(sport, f"basketball/{sport}")
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/news?limit=10"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("articles", [])
        news = []
        for a in articles[:8]:
            title = a.get("headline", "").strip()
            if not title:
                continue
            desc = BeautifulSoup(a.get("description", ""), "html.parser").get_text()[:130]
            link = a.get("links", {}).get("web", {}).get("href", "#")
            published = a.get("published", "")[:22]
            news.append({"title": title, "desc": desc, "link": link, "date": published})
        if news:
            return news
    except Exception:
        pass
    # Fallback: ESPN RSS (works locally but may be blocked on cloud)
    try:
        url = f"https://www.espn.com/espn/rss/{sport}/news"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.find_all("item")[:8]
        news = []
        for item in items:
            raw = str(item)
            title_tag = item.find("title")
            title_text = _re.sub(r"<!\[CDATA\[|\]\]>", "", title_tag.get_text(strip=True)) if title_tag else ""
            link_match = _re.search(r"<link/>\s*<!\[CDATA\[(https?://[^\]]+)\]\]>", raw)
            link_url = link_match.group(1).strip() if link_match else "#"
            desc_tag = item.find("description")
            desc_text = BeautifulSoup(desc_tag.get_text(strip=True), "html.parser").get_text()[:130] if desc_tag else ""
            date_tag = item.find("pubdate") or item.find("pubDate")
            date_text = date_tag.get_text(strip=True)[:22] if date_tag else ""
            news.append({"title": title_text, "desc": desc_text, "link": link_url, "date": date_text})
        return [n for n in news if n["title"]]
    except Exception:
        return []

# ══════════════════════════════════════════════════════════════════════════════
# SCOUT REPORT GENERATORS
# ══════════════════════════════════════════════════════════════════════════════
def _scout_card(text):
    return (
        "<div style='background:#191c23;border:1px solid #252a35;"
        "border-left:3px solid #818cf8;"
        "border-radius:10px;padding:1.25rem 1.4rem;margin-bottom:1.25rem;'>"
        "<p style='font-size:0.58rem;letter-spacing:0.18em;text-transform:uppercase;"
        "color:#818cf8;margin:0 0 0.55rem 0;font-weight:700;'>Scout Report</p>"
        f"<p style='font-size:0.86rem;color:#c8cad4;line-height:1.85;margin:0;'>{text}</p></div>"
    )

# Aliases kept for compatibility with existing call sites
def _scout_card_nba(text): return _scout_card(text)
def _scout_card_mlb(text): return _scout_card(text)

def nba_scout_report(player_name, team_code, df, next_opp, prop_type, rolling_window,
                     line=None, ud_line=None, ud_odds=None, ud_implied=None):
    if df.empty or not next_opp:
        return None
    stat_col = STAT_MAP.get(prop_type, "PTS")
    season_stat = df[stat_col].mean() if stat_col in df.columns else 0
    season_pts  = df["PTS"].mean()
    season_reb  = df["REB"].mean()
    season_ast  = df["AST"].mean()
    last10 = df.tail(10)
    recent_stat = last10[stat_col].mean() if stat_col in last10.columns else season_stat
    delta = recent_stat - season_stat
    thresh = max(season_stat * 0.08, 1.0)
    if delta > thresh:
        trend = f"trending upward at {recent_stat:.1f} {prop_type}/G over the last 10 ({delta:+.1f} vs season avg)"
    elif delta < -thresh:
        trend = f"trending downward at {recent_stat:.1f} {prop_type}/G over the last 10 ({delta:+.1f} vs season avg)"
    else:
        trend = f"consistent at {recent_stat:.1f} {prop_type}/G over the last 10 games"
    # Opponent history for the selected prop
    opp_df = df[df["OPPONENT"].str.upper() == next_opp.upper()]
    if not opp_df.empty:
        op_stat = opp_df[stat_col].mean() if stat_col in opp_df.columns else 0
        n = len(opp_df)
        quality = ("favorable" if op_stat > season_stat * 1.08
                   else "difficult" if op_stat < season_stat * 0.92 else "neutral")
        opp_str = (f"In {n} game(s) vs {next_opp}, {player_name} averaged "
                   f"{op_stat:.1f} {prop_type} — a historically {quality} matchup for this prop.")
    else:
        opp_str = f"No prior matchup data vs {next_opp} in the selected seasons."
    # Streak and hit rate vs line
    streak_str = hit_rate_str = ""
    if line is not None and stat_col in df.columns:
        over_series = (df[stat_col] > line).tolist()
        streak_over = 0
        for v in reversed(over_series):
            if v: streak_over += 1
            else: break
        streak_under = 0
        for v in reversed(over_series):
            if not v: streak_under += 1
            else: break
        last10_hit = (last10[stat_col] > line).mean() if stat_col in last10.columns else 0
        hit_rate_str = f"Hit rate vs {line} over the last 10 games: {last10_hit:.0%}."
        if streak_over >= 3:
            streak_str = f" {player_name} has cleared {line} in {streak_over} straight games — a hot streak."
        elif streak_under >= 3:
            streak_str = f" Warning: {player_name} has missed {line} in {streak_under} consecutive games."
    # Home/away split for the prop
    ha_str = ""
    if "IS_HOME" in df.columns and stat_col in df.columns:
        home_avg = df.loc[df["IS_HOME"], stat_col].mean()
        away_avg = df.loc[~df["IS_HOME"], stat_col].mean()
        if pd.notna(home_avg) and pd.notna(away_avg):
            ha_str = f"Home/away split: {home_avg:.1f} at home vs {away_avg:.1f} on the road."
    # Form last 5
    last5_stat = df.tail(5)[stat_col].mean() if stat_col in df.columns else season_stat
    if last5_stat > season_stat * 1.1:
        form = f"Running hot over the last 5 games ({last5_stat:.1f} {prop_type}/G)."
    elif last5_stat < season_stat * 0.9:
        form = f"Cooled over the last 5 ({last5_stat:.1f} {prop_type}/G vs {season_stat:.1f} season avg)."
    else:
        form = f"Steady form over the last 5 games ({last5_stat:.1f} {prop_type}/G)."
    # Rolling model prediction
    pred_str = ""
    if len(df) >= rolling_window and stat_col in df.columns:
        pred_val = df[stat_col].rolling(rolling_window).mean().iloc[-1]
        pred_pts = df["PTS"].rolling(rolling_window).mean().iloc[-1]
        pred_reb = df["REB"].rolling(rolling_window).mean().iloc[-1]
        pred_ast = df["AST"].rolling(rolling_window).mean().iloc[-1]
        pred_str = (f"A {rolling_window}-game rolling model projects {pred_val:.1f} {prop_type} "
                    f"({pred_pts:.1f} pts / {pred_reb:.1f} reb / {pred_ast:.1f} ast).")
    # Underdog market signal
    ud_str = ""
    if ud_line is not None and ud_odds is not None and ud_implied is not None:
        odds_fmt = f"+{ud_odds}" if ud_odds > 0 else str(ud_odds)
        lean = "leaning OVER" if ud_implied < 0.50 else "implying slight UNDER value"
        ud_str = (f"Underdog Fantasy has this prop at {ud_line} ({odds_fmt}, {ud_implied:.0%} implied) "
                  f"— the market is {lean}.")
    parts = [
        f"{player_name} ({team_code}) heads into the matchup vs {next_opp} averaging {season_stat:.1f} {prop_type}/G "
        f"(context: {season_pts:.1f} PPG / {season_reb:.1f} RPG / {season_ast:.1f} APG) this season. "
        f"Scoring is {trend}.",
        opp_str, hit_rate_str, streak_str, ha_str, form, pred_str, ud_str,
    ]
    return " ".join(p for p in parts if p)

def mlb_hitter_scout_report(hitter_name, team_abbr, df, team_id,
                             line=None, prop_stat=None, ud_line=None, ud_odds=None, ud_implied=None,
                             player_id=None):
    if df.empty:
        return None
    _H_STAT_LABELS = {"H": "Hits", "HR": "Home Runs", "RBI": "RBIs", "K": "Strikeouts", "BB": "Walks"}
    # Statcast quality-of-contact line (barrel rate is the sharpest HR signal)
    _sc_pid = player_id or _pm.mlb_player_id(hitter_name)
    _sc = _pm.savant_batter_stats().get(_sc_pid) if _sc_pid else None
    sc_txt = ""
    if _sc and _sc.get("barrel") is not None:
        _bits = [f"{_sc['barrel']*100:.1f}% barrel rate"]
        if _sc.get("xiso") is not None:
            _bits.append(f".{int(_sc['xiso']*1000):03d} xISO")
        if _sc.get("hardhit") is not None:
            _bits.append(f"{_sc['hardhit']:.0f}% hard-hit")
        if _sc.get("fb") is not None and _sc.get("pull") is not None:
            _bits.append(f"{_sc['fb']:.0f}% FB / {_sc['pull']:.0f}% pull")
        sc_txt = "Statcast: " + ", ".join(_bits) + "."
    stat_col   = prop_stat or "H"
    stat_label = _H_STAT_LABELS.get(stat_col, stat_col)
    avg = df["AVG"].iloc[-1] if len(df) > 0 else 0.0
    obp = df["OBP"].iloc[-1] if len(df) > 0 else 0.0
    slg = df["SLG"].iloc[-1] if len(df) > 0 else 0.0
    ops = obp + slg
    season_stat = df[stat_col].mean() if stat_col in df.columns else 0
    last10 = df.tail(10)
    recent_stat = last10[stat_col].mean() if stat_col in last10.columns else season_stat
    delta = recent_stat - season_stat
    thresh = max(season_stat * 0.15, 0.1)
    trend = (f"trending {'up' if delta > 0 else 'down'} at {recent_stat:.2f} {stat_label}/G "
             f"({delta:+.2f} vs season avg) over the last 10 games"
             if abs(delta) > thresh
             else f"steady at {recent_stat:.2f} {stat_label}/G over the last 10 games")
    # Streak and hit rate vs line
    streak_str = hit_rate_str = ""
    if line is not None and stat_col in df.columns:
        over_series = (df[stat_col] > line).tolist()
        streak_over = 0
        for v in reversed(over_series):
            if v: streak_over += 1
            else: break
        streak_under = 0
        for v in reversed(over_series):
            if not v: streak_under += 1
            else: break
        last10_hit = (last10[stat_col] > line).mean() if stat_col in last10.columns else 0
        hit_rate_str = f"Hit rate vs {line} {stat_label} over the last 10 games: {last10_hit:.0%}."
        if streak_over >= 3:
            streak_str = f" {hitter_name} has cleared {line} in {streak_over} consecutive games."
        elif streak_under >= 3:
            streak_str = f" Warning: {hitter_name} has missed {line} in {streak_under} straight starts."
    # Today's game context
    game = get_mlb_today_game_for_team(team_id)
    pitcher_txt = opp_hist_txt = ""
    if game:
        is_away = game["teams"]["away"]["team"]["id"] == team_id
        opp_side = "home" if is_away else "away"
        opp_team = game["teams"][opp_side]["team"]
        opp_prob = game["teams"][opp_side].get("probablePitcher", {})
        opp_name = opp_team.get("name", "the opponent")
        opp_abbr_g = opp_team.get("abbreviation", "")
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
                    pitcher_txt += f"{p_name}: {p_era:.2f} ERA / {p_whip:.2f} WHIP / {p_k9:.1f} K/9. "
                    if p_era < 3.50:
                        pitcher_txt += "Tough draw — downside risk on hitting props. "
                    elif p_era > 5.00:
                        pitcher_txt += "Favorable matchup — the numbers lean OVER. "
                    else:
                        pitcher_txt += "Neutral matchup on paper. "
        else:
            pitcher_txt = f"Today's opponent is the {opp_name}; no probable starter posted yet. "
        if opp_abbr_g and stat_col in df.columns:
            vs = df[df["opponent"].str.upper() == opp_abbr_g.upper()]
            if not vs.empty:
                vs_stat_avg = vs[stat_col].mean()
                vs_h_avg = vs["H"].sum() / max(vs["AB"].sum(), 1)
                opp_hist_txt = (f"vs {opp_name} this season: {hitter_name} averages "
                                f"{vs_stat_avg:.2f} {stat_label}/G across {len(vs)} game(s) "
                                f"(.{int(vs_h_avg*1000):03d} AVG). ")
    # Last 5 form
    last5 = df.tail(5)
    last5_stat = last5[stat_col].mean() if stat_col in last5.columns else season_stat
    if last5_stat > season_stat * 1.2:
        form = f"Running hot over the last 5 games ({last5_stat:.2f} {stat_label}/G vs {season_stat:.2f} season avg)."
    elif last5_stat < season_stat * 0.8:
        form = f"Cooled recently ({last5_stat:.2f} {stat_label}/G vs {season_stat:.2f} season avg over the last 5)."
    else:
        form = f"Consistent form over the last 5 games ({last5_stat:.2f} {stat_label}/G)."
    # Rolling projection
    pred_h   = df["H"].rolling(10).mean().iloc[-1]   if len(df) >= 10 else df["H"].mean()
    pred_hr  = df["HR"].rolling(10).mean().iloc[-1]  if len(df) >= 10 else df["HR"].mean()
    pred_rbi = df["RBI"].rolling(10).mean().iloc[-1] if len(df) >= 10 else df["RBI"].mean()
    # Underdog market signal
    ud_str = ""
    if ud_line is not None and ud_odds is not None and ud_implied is not None:
        odds_fmt = f"+{ud_odds}" if ud_odds > 0 else str(ud_odds)
        lean = "leaning OVER" if ud_implied < 0.50 else "implying slight UNDER value"
        ud_str = (f"Underdog has this prop at {ud_line} {stat_label} "
                  f"({odds_fmt}, {ud_implied:.0%} implied) — market is {lean}.")
    q = "elite" if avg > 0.290 else ("above-average" if avg > 0.260 else ("solid" if avg > 0.230 else "below-average"))
    parts = [
        f"{hitter_name} ({team_abbr}) brings a {q} slash line "
        f".{int(avg*1000):03d}/.{int(obp*1000):03d}/.{int(slg*1000):03d} (OPS {ops:.3f}), "
        f"averaging {season_stat:.2f} {stat_label}/G this season — {trend}.",
        sc_txt,
        pitcher_txt, opp_hist_txt,
        hit_rate_str, streak_str, form,
        f"10-game rolling projection: {pred_h:.1f} H / {pred_hr:.2f} HR / {pred_rbi:.1f} RBI.",
        ud_str,
    ]
    return " ".join(p for p in parts if p)

def mlb_pitcher_scout_report(pitcher_name, team_abbr, df, team_id,
                              line=None, prop_stat=None, ud_line=None, ud_odds=None, ud_implied=None,
                              player_id=None):
    if df.empty:
        return None
    _P_STAT_LABELS = {"K": "Strikeouts", "IP": "Innings", "ER": "Earned Runs",
                      "BB": "Walks", "H": "Hits Allowed", "HR": "HR Allowed"}
    # Statcast contact-allowed line (barrel/flyball tendencies drive HR risk)
    _sc_pid = player_id or _pm.mlb_player_id(pitcher_name)
    _sc = _pm.savant_pitcher_stats().get(_sc_pid) if _sc_pid else None
    sc_txt = ""
    if _sc and _sc.get("barrel") is not None:
        _bits = [f"allows a {_sc['barrel']*100:.1f}% barrel rate"]
        if _sc.get("hardhit") is not None:
            _bits.append(f"{_sc['hardhit']:.0f}% hard contact")
        if _sc.get("fb") is not None:
            _bits.append(f"{_sc['fb']:.0f}% fly balls")
        sc_txt = "Statcast: " + ", ".join(_bits) + "."
    stat_col   = prop_stat or "K"
    stat_label = _P_STAT_LABELS.get(stat_col, stat_col)
    tip  = df["IP"].sum()
    # Season rates from totals — averaging per-game ERA/WHIP would let short
    # outings dominate.
    era  = (df["ER"].sum() / tip * 9) if tip > 0 else 0.0
    whip = ((df["H"].sum() + df["BB"].sum()) / tip) if tip > 0 else 0.0
    k9   = (df["K"].sum() / tip * 9) if tip > 0 else 0.0
    bb9  = df["BB"].sum() / tip * 9 if tip > 0 else 0
    kbb  = df["K"].sum() / max(df["BB"].sum(), 1)
    qs   = ((df["IP"] >= 6) & (df["ER"] <= 3)).mean() * 100
    season_stat = df[stat_col].mean() if stat_col in df.columns else 0
    # Recent form for selected prop
    last5 = df.tail(5)
    last10 = df.tail(10)
    recent_stat = last5[stat_col].mean() if stat_col in last5.columns else season_stat
    # Streak and hit rate vs line
    streak_str = hit_rate_str = ""
    if line is not None and stat_col in df.columns:
        over_series = (df[stat_col] > line).tolist()
        streak_over = 0
        for v in reversed(over_series):
            if v: streak_over += 1
            else: break
        streak_under = 0
        for v in reversed(over_series):
            if not v: streak_under += 1
            else: break
        last10_hit = (last10[stat_col] > line).mean() if stat_col in last10.columns else 0
        hit_rate_str = f"Hit rate vs {line} {stat_label} over the last 10 starts: {last10_hit:.0%}."
        if streak_over >= 3:
            streak_str = f" {pitcher_name} has cleared {line} in {streak_over} consecutive starts."
        elif streak_under >= 3:
            streak_str = f" Warning: {pitcher_name} has missed {line} in {streak_under} straight starts."
    # Today's game context
    game = get_mlb_today_game_for_team(team_id)
    opp_txt = bat_txt = ""
    if game:
        is_away = game["teams"]["away"]["team"]["id"] == team_id
        opp_side = "home" if is_away else "away"
        opp_team = game["teams"][opp_side]["team"]
        opp_name = opp_team.get("name", "the opponent")
        opp_abbr = opp_team.get("abbreviation", "")
        opp_id   = opp_team.get("id")
        opp_txt = f"Today, {pitcher_name} takes the hill against the {opp_name}. "
        vs = df[df["opponent"].str.upper() == opp_abbr.upper()] if opp_abbr else pd.DataFrame()
        if not vs.empty:
            vs_stat_avg = vs[stat_col].mean() if stat_col in vs.columns else 0
            opp_txt += (f"In {len(vs)} prior start(s) vs {opp_name}, {pitcher_name} averaged "
                        f"{vs_stat_avg:.1f} {stat_label} with a {vs['ERA'].mean():.2f} ERA. ")
        if opp_id:
            bat = get_mlb_team_batting_stats(opp_id)
            if bat:
                t_avg = float(bat.get("avg") or 0)
                t_ops = float(bat.get("ops") or 0)
                if t_avg > 0.265:
                    bat_txt = (f"The {opp_name} offense is dangerous ({t_avg:.3f} AVG / {t_ops:.3f} OPS) "
                               f"— expect a competitive at-bat environment. ")
                elif t_avg < 0.240:
                    bat_txt = (f"The {opp_name} lineup has struggled ({t_avg:.3f} AVG / {t_ops:.3f} OPS), "
                               f"a favorable strikeout setup. ")
                else:
                    bat_txt = f"The {opp_name} lineup is an average unit ({t_avg:.3f} AVG / {t_ops:.3f} OPS). "
    # Form and trend
    sk = df["K"].mean()
    rk = last5["K"].mean()
    if recent_stat > season_stat * 1.15 and stat_col != "ERA":
        form = f"Over the last 5 starts, {pitcher_name} is running hot with {recent_stat:.1f} {stat_label}/start."
    elif recent_stat < season_stat * 0.85 and stat_col not in ("ERA", "WHIP"):
        form = f"Output has dipped over the last 5 starts ({recent_stat:.1f} {stat_label} vs {season_stat:.1f} season avg)."
    else:
        form = f"Production has been steady at {recent_stat:.1f} {stat_label}/start over the last 5 outings."
    # Rolling projection
    pred_k  = df["K"].rolling(5).mean().iloc[-1]  if len(df) >= 5 else sk
    pred_ip = df["IP"].rolling(5).mean().iloc[-1] if len(df) >= 5 else df["IP"].mean()
    # Underdog market signal
    ud_str = ""
    if ud_line is not None and ud_odds is not None and ud_implied is not None:
        odds_fmt = f"+{ud_odds}" if ud_odds > 0 else str(ud_odds)
        lean = "leaning OVER" if ud_implied < 0.50 else "implying slight UNDER value"
        ud_str = (f"Underdog has this prop at {ud_line} {stat_label} "
                  f"({odds_fmt}, {ud_implied:.0%} implied) — market is {lean}.")
    tier = "elite" if era < 3.00 else ("solid" if era < 4.00 else ("middling" if era < 5.00 else "struggling"))
    parts = [
        f"{pitcher_name} ({team_abbr}) is a {tier} arm: {era:.2f} ERA / {whip:.2f} WHIP / "
        f"{k9:.1f} K/9 / {bb9:.1f} BB/9 / {kbb:.2f} K/BB / {qs:.0f}% QS rate. "
        f"Season average: {season_stat:.1f} {stat_label}/start.",
        sc_txt,
        opp_txt, bat_txt,
        hit_rate_str, streak_str, form,
        f"5-start rolling projection: {pred_k:.1f} K / {pred_ip:.1f} IP.",
        ud_str,
    ]
    return " ".join(p for p in parts if p)

def wnba_scout_report(player_name, team_code, df, next_opp, prop_type, rolling_window, line=None):
    """Scout report for WNBA vs-opponent tab. Expects df to have a TARGET column already set."""
    if df.empty or not next_opp:
        return None
    stat_col = "TARGET" if "TARGET" in df.columns else _WNBA_STAT_MAP.get(prop_type, "PTS")
    season_stat = df[stat_col].mean() if stat_col in df.columns else 0
    season_pts  = df["PTS"].mean() if "PTS" in df.columns else 0
    season_reb  = df["REB"].mean() if "REB" in df.columns else 0
    season_ast  = df["AST"].mean() if "AST" in df.columns else 0
    last10 = df.tail(10)
    recent_stat = last10[stat_col].mean() if stat_col in last10.columns else season_stat
    delta = recent_stat - season_stat
    thresh = max(season_stat * 0.08, 1.0)
    if delta > thresh:
        trend = f"trending upward at {recent_stat:.1f} {prop_type}/G over the last 10 ({delta:+.1f} vs season avg)"
    elif delta < -thresh:
        trend = f"trending downward at {recent_stat:.1f} {prop_type}/G over the last 10 ({delta:+.1f} vs season avg)"
    else:
        trend = f"consistent at {recent_stat:.1f} {prop_type}/G over the last 10 games"
    opp_df = df[df["OPPONENT"].str.upper() == next_opp.upper()] if "OPPONENT" in df.columns else pd.DataFrame()
    if not opp_df.empty:
        op_stat = opp_df[stat_col].mean() if stat_col in opp_df.columns else 0
        n = len(opp_df)
        quality = ("favorable" if op_stat > season_stat * 1.08
                   else "difficult" if op_stat < season_stat * 0.92 else "neutral")
        opp_str = (f"In {n} game(s) vs {next_opp}, {player_name} averaged "
                   f"{op_stat:.1f} {prop_type} — a historically {quality} matchup.")
    else:
        opp_str = f"No prior matchup data vs {next_opp} in the selected seasons."
    streak_str = hit_rate_str = ""
    if line is not None and stat_col in df.columns:
        over_series = (df[stat_col] > line).tolist()
        streak_over = streak_under = 0
        for v in reversed(over_series):
            if v: streak_over += 1
            else: break
        for v in reversed(over_series):
            if not v: streak_under += 1
            else: break
        last10_hit = (last10[stat_col] > line).mean() if stat_col in last10.columns else 0
        hit_rate_str = f"Hit rate vs {line} over the last 10 games: {last10_hit:.0%}."
        if streak_over >= 3:
            streak_str = f" {player_name} has cleared {line} in {streak_over} straight games — a hot streak."
        elif streak_under >= 3:
            streak_str = f" Warning: {player_name} has missed {line} in {streak_under} consecutive games."
    last5_stat = df.tail(5)[stat_col].mean() if stat_col in df.columns else season_stat
    if last5_stat > season_stat * 1.1:
        form = f"Running hot over the last 5 games ({last5_stat:.1f} {prop_type}/G)."
    elif last5_stat < season_stat * 0.9:
        form = f"Cooled over the last 5 ({last5_stat:.1f} {prop_type}/G vs {season_stat:.1f} season avg)."
    else:
        form = f"Steady form over the last 5 games ({last5_stat:.1f} {prop_type}/G)."
    pred_str = ""
    if len(df) >= rolling_window and stat_col in df.columns:
        pred_val = df[stat_col].rolling(rolling_window).mean().iloc[-1]
        pred_str = (f"A {rolling_window}-game rolling model projects {pred_val:.1f} {prop_type} "
                    f"({season_pts:.1f} pts / {season_reb:.1f} reb / {season_ast:.1f} ast season avg).")
    parts = [
        f"{player_name} ({team_code}) heads into the matchup vs {next_opp} averaging {season_stat:.1f} {prop_type}/G "
        f"(context: {season_pts:.1f} PPG / {season_reb:.1f} RPG / {season_ast:.1f} APG) this season. "
        f"Output is {trend}.",
        opp_str, hit_rate_str, streak_str, form, pred_str,
    ]
    return " ".join(p for p in parts if p)

# ══════════════════════════════════════════════════════════════════════════════
# DAILY BLOG GENERATORS
# ══════════════════════════════════════════════════════════════════════════════

def _win_pct(record_str):
    try:
        w, l = record_str.split("-")
        t = int(w) + int(l)
        return int(w) / t if t > 0 else 0.5
    except Exception:
        return 0.5

def _pitcher_desc(stats):
    if not stats:
        return ""
    era = float(stats.get("era") or 0)
    whip = float(stats.get("whip") or 0)
    k9 = float(stats.get("strikeoutsPer9Inn") or 0)
    tier = ("one of the game's elite arms" if era < 3.00
            else "a bonafide ace" if era < 3.50
            else "a solid mid-rotation starter" if era < 4.25
            else "a back-end starter fighting for consistency")
    k_str = (f" a strikeout machine ({k9:.1f} K/9)" if k9 > 10
              else f" high strikeout upside ({k9:.1f} K/9)" if k9 > 8
              else "")
    ctrl_str = (" with elite control" if whip < 1.05
                else " with sharp control" if whip < 1.20
                else " who has battled command issues at times" if whip > 1.40
                else "")
    return f"{tier}{k_str}{ctrl_str} ({era:.2f} ERA / {whip:.2f} WHIP)"

def _nba_game_html(g):
    aw = g.get("away", "")
    hw = g.get("home", "")
    ar = g.get("away_record", "") or ""
    hr = g.get("home_record", "") or ""
    ap, hp = _win_pct(ar), _win_pct(hr)
    rec_line = f"{ar} · {hr}" if ar and hr else ""
    if ap > 0.60 and hp > 0.60:
        ctx = "a marquee clash between two of the league's elite teams"
    elif ap < 0.38 and hp < 0.38:
        ctx = "a bottom-of-the-standings battle with lottery implications"
    elif abs(ap - hp) > 0.22:
        fav = hw if hp > ap else aw
        dog = aw if hp > ap else hw
        ctx = f"a lopsided test as the {fav} host the struggling {dog}"
    else:
        ctx = "a tight, evenly-matched contest"
    venue = f" at {g['venue']}" if g.get("venue") else ""
    ar_str = f" ({ar})" if ar else ""
    hr_str = f" ({hr})" if hr else ""
    body = (f"The {aw}{ar_str} travel to face the {hw}{hr_str}{venue} in {ctx}. "
            f"Expect both teams' backcourt depth and three-point shooting to be decisive factors, "
            f"as the game-time status of any injured stars bears watching before tip-off.")
    return (f"<div class='blog-game-card'>"
            f"<div class='blog-game-vs'>{aw} @ {hw}</div>"
            f"{'<div class=\"blog-game-rec\">' + rec_line + '</div>' if rec_line else ''}"
            f"<div class='blog-game-body'>{body}</div>"
            f"</div>")

def _mlb_game_html(g):
    aw = g.get("away", "")
    hw = g.get("home", "")
    ar = g.get("away_record", "") or ""
    hr = g.get("home_record", "") or ""
    ap_name = g.get("away_pitcher", "TBD") or "TBD"
    hp_name = g.get("home_pitcher", "TBD") or "TBD"
    ap_desc = _pitcher_desc(g.get("away_p_stats", {}))
    hp_desc = _pitcher_desc(g.get("home_p_stats", {}))
    rec_line = f"{ar} · {hr}" if ar and hr else ""
    venue = f" at {g['venue']}" if g.get("venue") else ""

    ar_str = f" ({ar})" if ar else ""
    hr_str = f" ({hr})" if hr else ""
    body = f"The {aw}{ar_str} visit the {hw}{hr_str}{venue}. "
    if ap_name != "TBD" and ap_desc:
        body += f"{ap_name} gets the ball for {aw} — {ap_desc}. "
    elif ap_name != "TBD":
        body += f"{ap_name} takes the hill for {aw}. "
    if hp_name != "TBD" and hp_desc:
        body += f"He'll be opposed by {hp_name}, {hp_desc}. "
    elif hp_name != "TBD":
        body += f"He'll be opposed by {hp_name}. "
    if ap_name == "TBD" and hp_name == "TBD":
        body += "Probable pitchers are yet to be posted — check back closer to first pitch. "

    ap_era = float((g.get("away_p_stats") or {}).get("era") or 0)
    hp_era = float((g.get("home_p_stats") or {}).get("era") or 0)
    if ap_era > 0 and hp_era > 0:
        if abs(ap_era - hp_era) > 1.2:
            adv = hp_name if hp_era < ap_era else ap_name
            body += f"On paper, {adv} holds a clear ERA advantage in this pitching matchup."
        else:
            body += "The pitching matchup looks competitive on the surface — bullpen depth may be the deciding factor."

    return (f"<div class='blog-game-card'>"
            f"<div class='blog-game-vs'>{aw} @ {hw}</div>"
            f"{'<div class=\"blog-game-rec\">' + rec_line + '</div>' if rec_line else ''}"
            f"<div class='blog-game-body'>{body}</div>"
            f"</div>")

def _news_rows_html(news, n=5):
    if not news:
        return "<p class='blog-body'>No recent stories available.</p>"
    rows = ""
    for item in news[:n]:
        link = item.get("link", "#")
        title = item.get("title", "")
        date = item.get("date", "")
        rows += (f"<a href='{link}' target='_blank' style='text-decoration:none;color:inherit;'>"
                 f"<div class='blog-news-row'>"
                 f"<div class='blog-news-dot'></div>"
                 f"<div><div class='blog-news-text'>{title}</div>"
                 f"<div class='blog-news-date'>{date}</div></div>"
                 f"</div></a>")
    return rows

@st.cache_data(ttl=3600)
def generate_nba_blog():
    today = datetime.now()
    m, d = today.month, today.day
    date_str = today.strftime("%B %d, %Y")
    weekday = today.strftime("%A")
    # NBA offseason: ~late June through mid-October
    is_offseason = (m == 6 and d >= 22) or (m in [7, 8]) or (m == 9) or (m == 10 and d <= 21)
    games = get_nba_scoreboard_full(_scoreboard_date())
    news = get_sport_news("nba")

    if is_offseason or not games:
        # ── Offseason article ─────────────────────────────────────────────────
        headline = "NBA Offseason Watch: Draft, Free Agency & What Every Team Needs"
        top_news = news[0]["title"] if news else "the league continues to evolve"
        lead = (f"The NBA season may be over, but the offseason never sleeps. "
                f"From lottery picks to blockbuster trades and free-agent courtships, "
                f"the moves made between now and training camp will define next year's contenders. "
                f"The latest from around the league: {top_news.lower().rstrip('.')}.")
        offseason_body = (
            "The NBA Draft Lottery sets the stage for rebuilding franchises. "
            "Teams at the bottom of the standings are jockeying for positioning, "
            "with the top prospects commanding intense pre-draft workouts and measurables "
            "scrutiny. Expect front offices to be aggressive on draft night — both moving up "
            "and consolidating picks for near-term help. "
            "On the free-agent front, the summer market is shaping up to feature a mix of "
            "max-contract stars and high-value role players. Teams with cap space will be "
            "targeting perimeter shooting, versatile defenders, and reliable shot creation — "
            "the premium commodities in today's pace-and-space NBA. "
            "The trade market is equally active. Contenders will be looking to add proven "
            "playoff contributors, while rebuilders are signaling openness to moving veteran "
            "pieces for draft capital. The Western Conference arms race shows no sign of slowing."
        )
        closing = (f"Konjure will be tracking every significant roster move, trade rumor, and "
                   f"draft development throughout the offseason. Check back daily as the "
                   f"NBA landscape reshapes itself for {today.year + 1}.")
        html = (f"<div class='blog-wrap'>"
                f"<div class='blog-kicker'>NBA Offseason Brief · {date_str}</div>"
                f"<h1 class='blog-title'>{headline}</h1>"
                f"<div class='blog-meta'><span>{date_str}</span><span>Konjure Analytics</span></div>"
                f"<p class='blog-lead'>{lead}</p>"
                f"<h2 class='blog-h2'>Draft & Free Agency Landscape</h2>"
                f"<p class='blog-body'>{offseason_body}</p>"
                f"<h2 class='blog-h2'>Around the League</h2>"
                f"{_news_rows_html(news, 6)}"
                f"<div class='blog-callout'>{closing}</div>"
                f"</div>")
        return html

    # ── Game Day article ───────────────────────────────────────────────────────
    n = len(games)
    g0 = games[0]
    if n == 1:
        headline = f"{g0['away_abbr']} vs. {g0['home_abbr']}: Breaking Down Tonight's Lone NBA Showdown"
    elif n <= 3:
        headline = f"{g0['away_abbr']}–{g0['home_abbr']} Headline a {n}-Game {weekday} NBA Slate"
    else:
        headline = f"{n} Games Tonight: Konjure's Breakdown of {weekday}'s Full NBA Slate"

    lead = (f"{weekday}'s NBA calendar delivers {n} {'game' if n == 1 else 'games'} for fans across the league. "
            f"{'The marquee matchup' if n > 1 else 'Tonight'} features the {g0['away']} "
            f"{'(' + g0['away_record'] + ') ' if g0['away_record'] else ''}"
            f"taking on the {g0['home']} "
            f"{'(' + g0['home_record'] + ') ' if g0['home_record'] else ''}. "
            f"Here's Konjure's data-driven breakdown of the night's action, "
            f"the matchups that matter, and the players worth putting on your radar.")

    games_html = "".join(_nba_game_html(g) for g in games)

    watch_body = (
        f"With {n} games on the docket, prop bettors and fantasy players alike should zero in on "
        f"volume scorers who face weaker perimeter defenses. Players who have posted back-to-back "
        f"double-digit performances and are playing at home deserve extra attention — rest advantage "
        f"and crowd energy remain underrated factors. Monitor late-breaking injury reports for guards "
        f"and wings, as their absence can elevate usage rates for teammates in significant ways. "
        f"The Konjure rolling model favors high-usage players who have exceeded their prop lines in "
        f"four or more of their last seven outings."
    )

    closing = (f"Tonight shapes up as a {'packed' if n >= 6 else 'focused'} slate. "
               f"Konjure's projections are live on the Player Stats tab — select any player "
               f"currently on tonight's schedule to pull their rolling model and matchup history.")

    html = (f"<div class='blog-wrap'>"
            f"<div class='blog-kicker'>NBA Daily Brief · {date_str}</div>"
            f"<h1 class='blog-title'>{headline}</h1>"
            f"<div class='blog-meta'><span>{date_str}</span><span>Konjure Analytics</span></div>"
            f"<p class='blog-lead'>{lead}</p>"
            f"<h2 class='blog-h2'>Tonight's Matchups</h2>"
            f"{games_html}"
            f"<h2 class='blog-h2'>Players to Watch</h2>"
            f"<p class='blog-body'>{watch_body}</p>"
            f"<h2 class='blog-h2'>Around the League</h2>"
            f"{_news_rows_html(news, 5)}"
            f"<div class='blog-callout'>{closing}</div>"
            f"</div>")
    return html


def generate_mlb_blog():
    today = datetime.now()
    m, d = today.month, today.day
    date_str = today.strftime("%B %d, %Y")
    weekday = today.strftime("%A")
    # MLB offseason: ~Nov 1 through Feb 14
    is_offseason = m in [11, 12, 1] or (m == 2 and d <= 14)
    games = get_mlb_today_with_pitchers()
    news = get_sport_news("mlb")

    if is_offseason or not games:
        # ── Offseason article ─────────────────────────────────────────────────
        headline = "MLB Hot Stove: Free Agency, Trades & the Winter Moves Reshaping Rosters"
        top_news = news[0]["title"] if news else "the offseason market is heating up"
        lead = (f"The Hot Stove is burning. With the {today.year - 1} season in the books, "
                f"front offices are working overtime — free-agent targets are being pursued, "
                f"trade packages are being assembled, and minor-league depth charts are being "
                f"reshuffled. The latest: {top_news.lower().rstrip('.')}.")
        offseason_body = (
            "The free-agent market typically breaks open after the World Series, with top "
            "starting pitchers and corner outfielders commanding the largest guarantees. "
            "Teams with rotation holes — always a majority of the league — are competing "
            "aggressively for arms with sub-4.00 ERAs and elite strikeout rates. On offense, "
            "power bats with on-base skills remain the most coveted commodity. "
            "The trade market is equally dynamic. Clubs sitting outside playoff contention "
            "are fielding offers for veterans with one or two years of control remaining, "
            "seeking prospects and draft capital to accelerate their rebuilds. Deadline "
            "rentals from last year are now extension targets or trade chips depending on "
            "each organization's competitive window. "
            "The Rule 5 Draft and offseason waiver wire rounds out the roster-building "
            "calendar — a hunting ground for savvy front offices looking for undervalued talent."
        )
        closing = (f"Konjure's MLB coverage will track every significant signing, trade, and "
                   f"prospect development through the winter. The {today.year} roster projections "
                   f"will update as deals are completed — check the Hitter and Pitcher tabs for live stats.")
        html = (f"<div class='blog-wrap'>"
                f"<div class='blog-kicker'>MLB Hot Stove · {date_str}</div>"
                f"<h1 class='blog-title'>{headline}</h1>"
                f"<div class='blog-meta'><span>{date_str}</span><span>Konjure Analytics</span></div>"
                f"<p class='blog-lead'>{lead}</p>"
                f"<h2 class='blog-h2'>Free Agency & Trade Market</h2>"
                f"<p class='blog-body'>{offseason_body}</p>"
                f"<h2 class='blog-h2'>Around the League</h2>"
                f"{_news_rows_html(news, 6)}"
                f"<div class='blog-callout'>{closing}</div>"
                f"</div>")
        return html

    # ── Game Day article ───────────────────────────────────────────────────────
    n = len(games)
    g0 = games[0]
    if n >= 15:
        headline = f"Full {n}-Game MLB Slate: The Pitching Matchups and Hot Bats to Target Today"
    elif n >= 8:
        headline = f"{n} Games on Tap: Konjure's MLB Pitching Preview for {weekday}"
    else:
        headline = (f"{g0['away_abbr']} vs. {g0['home_abbr']} Among {n} MLB Games — "
                    f"Today's Pitching Matchup Analysis")

    _ar = g0.get('away_record', '') or ''
    _hr = g0.get('home_record', '') or ''
    _ap = g0.get('away_pitcher', 'TBD') or 'TBD'
    _hp = g0.get('home_pitcher', 'TBD') or 'TBD'
    lead = (f"{weekday}'s MLB schedule features {n} {'game' if n == 1 else 'games'} across the league. "
            f"{'The featured matchup' if n > 1 else 'The day'} has the {g0.get('away', '')} "
            f"{f'({_ar}) ' if _ar else ''}visiting the {g0.get('home', '')}{f' ({_hr})' if _hr else ''}, "
            f"with {_ap} squaring off against {_hp}. "
            f"Here's Konjure's game-by-game pitching breakdown and the bats worth tracking today.")

    games_html = "".join(_mlb_game_html(g) for g in games[:8])
    if n > 8:
        games_html += (f"<p class='blog-body' style='margin-top:0.5rem;'>"
                       f"Plus {n - 8} additional game{'s' if n - 8 > 1 else ''} rounding out the full {n}-game slate.</p>")

    hitter_body = (
        "With a full slate comes a deep pool of hitting props to explore. "
        "Target hitters facing starters with ERAs above 4.50 and WHIPs north of 1.30 — "
        "these arms tend to allow hard contact and can inflate hit totals. "
        "Left-handed bats against right-handed pitchers with below-average platoon splits "
        "represent some of the best edges in today's market. "
        "Pay close attention to lineup position: cleanup and three-hole hitters facing "
        "vulnerable arms have historically hit over their average lines at a 58%+ clip "
        "in Konjure's database. Power props (HR) carry more variance but reward on full "
        "counts and hitter-friendly park factors."
    )

    # ── HR Power Pick of the Day ───────────────────────────────────────────────
    # Use pre-computed picks from session state (populated by Parlays tab button).
    # Avoids fetching all roster/hitting data on every blog load.
    hr_pick_html = ""
    _cached_picks = st.session_state.get("hr_power_picks_cache", [])
    if not _cached_picks:
        hr_pick_html = (
            "<h2 class='blog-h2'>HR Power Pick of the Day</h2>"
            "<p class='blog-body' style='color:var(--text-muted);font-style:italic;'>"
            "Click <strong>Build HR Power Picks</strong> on the Parlays tab to populate today's top pick here."
            "</p>"
        )
    else:
        try:
            p = _cached_picks[0]
            pf = p.get("park_factor", 100)
            pf_label = "HR-friendly" if pf >= 110 else ("neutral" if pf >= 95 else "pitcher-friendly")
            p9 = p.get("pitcher_hr9", 1.1)
            p9_label = "highly vulnerable" if p9 >= 1.5 else ("above-average vulnerability" if p9 >= 1.1 else "stingy")
            plat = "with platoon advantage" if p.get("platoon_boost", 0) > 0 else "same-hand matchup"
            bvp = p.get("bvp_info", {})
            bvp_str = (f"{bvp['hr']} HR in {bvp['ab']} career AB vs today's starter"
                       if bvp.get("ab", 0) >= 10 else "limited career history vs today's starter")
            l10 = p.get("last10_hr", 0)
            l20 = p.get("last20_hr", 0)
            score_pct = int(p["score"] * 100)
            venue = p.get("venue", "")
            weather = p.get("weather", {})
            temp_f = weather.get("temp_f", 70)
            wind_mph = weather.get("wind_mph", 0)
            weather_str = (f"{temp_f:.0f}°F, {wind_mph:.0f} mph wind"
                           if venue not in _MLB_DOME_VENUES else f"{temp_f:.0f}°F (dome)")

            hr_pick_blurb = (
                f"{p['player_name']} ({p['team']}) is Konjure's top HR candidate today at {score_pct}% probability, "
                f"facing {p['opp_pitcher']} in the {p['game_label']} matchup. "
                f"The model flags {p['player_name'].split()[-1]} on {l10} home runs over his last 10 games "
                f"and {l20} over his last 20 — one of the hottest power streaks on today's slate. "
                f"The opposing starter carries a {p9:.2f} HR/9 ({p9_label}), "
                f"and the game is played at {venue or 'today’s venue'} "
                f"(park factor {pf}, {pf_label}) under {weather_str}. "
                f"Handedness: {plat}. BvP: {bvp_str}. "
                f"Find this pick — and the full HR Power Picks board — on the Parlays tab."
            )
            hr_pick_html = (
                f"<h2 class='blog-h2'>HR Power Pick of the Day</h2>"
                f"<div class='blog-callout' style='border-left-color:#f59e0b;'>"
                f"<span style='font-size:0.62rem;font-weight:700;letter-spacing:0.16em;"
                f"text-transform:uppercase;color:#f59e0b;display:block;margin-bottom:0.4rem;'>"
                f"#{1} Pick &nbsp;&middot;&nbsp; {score_pct}% HR Probability</span>"
                f"<strong style='font-size:1.05rem;color:#f1f5f9;'>{p['player_name']} ({p['team']})</strong>"
                f"<span style='color:#9294a8;font-size:0.82rem;margin-left:0.6rem;'>"
                f"vs {p['opp_pitcher']} &middot; {p['game_label']}</span>"
                f"<p style='margin:0.6rem 0 0;font-size:0.86rem;color:#c8cad4;line-height:1.75;'>"
                f"{hr_pick_blurb}</p>"
                f"</div>"
            )
        except Exception as _blog_e:
            hr_pick_html = (
                f"<h2 class='blog-h2'>HR Power Pick of the Day</h2>"
                f"<p class='blog-body'>DEBUG: cached={len(_cached_picks)} err={_blog_e}</p>"
            )

    closing = (f"Today's full slate is live. Head to the Hitter Analysis and Pitcher Analysis "
               f"tabs to pull any player's rolling model, matchup history, and current prop line comparison. "
               f"Good luck on {weekday}'s card.")

    html = (f"<div class='blog-wrap'>"
            f"<div class='blog-kicker'>MLB Daily Brief · {date_str}</div>"
            f"<h1 class='blog-title'>{headline}</h1>"
            f"<div class='blog-meta'><span>{date_str}</span><span>Konjure Analytics</span></div>"
            f"<p class='blog-lead'>{lead}</p>"
            f"<h2 class='blog-h2'>Today's Pitching Matchups</h2>"
            f"{games_html}"
            f"<h2 class='blog-h2'>Hitters to Target</h2>"
            f"<p class='blog-body'>{hitter_body}</p>"
            f"{hr_pick_html}"
            f"<h2 class='blog-h2'>Around the League</h2>"
            f"{_news_rows_html(news, 5)}"
            f"<div class='blog-callout'>{closing}</div>"
            f"</div>")
    return html

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS & CHART HELPERS
# ══════════════════════════════════════════════════════════════════════════════
STAT_MAP = {"Points": "PTS", "Rebounds": "REB", "Assists": "AST", "PRA": "PRA", "3PM": "FG3M"}
PP_STAT_MAP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "PRA": "Pts+Rebs+Asts", "3PM": "3-PT Made",
}
SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]

# ── Chart palette ───────────────────────────────────────────────────────────
# Validated against this dashboard's own dark chart surface (#191c23), not a generic
# one — contrast and lightness only mean anything against the surface a chart actually
# renders on. Worst adjacent CVD ΔE is 35.9 (target ≥12), all slots inside the dark
# lightness band and ≥3:1 on the surface.
#
# Slot 1 is the brand indigo snapped one step darker: the accent itself (#818cf8) sits
# at L 0.68, just outside the 0.48–0.67 band for a dark surface, so it was too light to
# hold as a data mark. Same hue, one step down, passes.
#
# The order is fixed and never cycled — a fifth series folds into "Other" rather than
# inventing a hue, and colour follows the entity, never its rank, so filtering a series
# out must never repaint the survivors.
#
# No red here on purpose. Red means "miss", and nothing that isn't a miss is allowed to
# borrow it: a status colour must never impersonate a series. Dropping it also raised
# the worst adjacent CVD separation from ΔE 35.9 to 41.3.
VIZ_SERIES = ["#6366f1", "#199e70", "#c98500", "#3987e5"]

# One hue, monotone lightness, low value nearest the surface. There were three
# different ad-hoc ramps doing this job, each starting at the *gridline* colour, so the
# low end of every magnitude scale dissolved into the grid.
VIZ_SEQUENTIAL = ["#184f95", "#2a78d6", "#86b6ef"]

# Reserved. Never reused as a series colour, and never the only signal — every use is
# paired with an icon or a label, because colour alone is not a channel everyone has.
VIZ_HIT     = "#0ca30c"   # good
VIZ_PENDING = "#fab219"   # warning
VIZ_MISS    = "#d03b3b"   # critical

_SHARED_CHART = dict(
    colorway=VIZ_SERIES,
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#191c23",
    font_color="#5c6272", title_font_color="#dfe1ea", title_font_size=13, title_text="",
    font=dict(family="Inter, sans-serif", size=11),
    xaxis=dict(gridcolor="#252a35", linecolor="#252a35", zerolinecolor="#252a35",
               tickfont=dict(size=10, color="#5c6272"), showspikes=True,
               spikecolor="#2e3341", spikethickness=1, spikemode="across"),
    yaxis=dict(gridcolor="#252a35", linecolor="#252a35", zerolinecolor="#252a35",
               tickfont=dict(size=10, color="#5c6272")),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#5c6272", size=10),
                bordercolor="rgba(0,0,0,0)", orientation="h", yanchor="bottom",
                y=1.02, xanchor="right", x=1),
    margin=dict(t=44, b=24, l=4, r=4),
    hovermode="x unified",
    hoverlabel=dict(bgcolor="#1f2330", bordercolor="#2e3341",
                    font=dict(color="#dfe1ea", size=11)),
)
# Keep aliases so existing code compiles without change
NBA_CHART = _SHARED_CHART
MLB_CHART = _SHARED_CHART
_CHART_CFG = {"displaylogo": False, "modeBarButtonsToRemove": ["select2d","lasso2d","autoScale2d"]}

def nba_fig(fig):
    fig.update_layout(**_SHARED_CHART)
    return fig

def mlb_fig(fig):
    fig.update_layout(**_SHARED_CHART)
    return fig

# ══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def nba_player_card(player_name, team_code):
    pid = get_player_id(player_name)
    headshot = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png" if pid else ""
    logo = f"https://a.espncdn.com/i/teamlogos/nba/500/{team_code.lower()}.png"
    st.markdown(f"""
    <div class="player-card">
        <img src="{headshot}" />
        <div style="flex:1;">
            <p class="player-card-name">{player_name}</p>
            <div style="display:flex;align-items:center;gap:0.45rem;margin-top:0.2rem;">
                <img src="{logo}" style="width:18px;height:18px;object-fit:contain;border:none;border-radius:0;background:transparent;" />
                <p class="player-card-team" style="margin:0;">{team_code}</p>
            </div>
        </div>
    </div>""", unsafe_allow_html=True)

def mlb_player_card(name, pos, team, player_id):
    photo = mlb_headshot(player_id)
    logo = f"https://a.espncdn.com/i/teamlogos/mlb/500/{team.lower()}.png"
    st.markdown(f"""
    <div class="mlb-player-card">
        <img src="{photo}" />
        <div style="flex:1;">
            <p class="mlb-player-name">{name}</p>
            <p class="mlb-player-pos">{pos}</p>
            <div style="display:flex;align-items:center;gap:0.45rem;margin-top:0.18rem;">
                <img src="{logo}" style="width:20px;height:20px;object-fit:contain;background:transparent;" />
                <p class="mlb-player-team" style="margin:0;">{team}</p>
            </div>
        </div>
    </div>""", unsafe_allow_html=True)

def section(label):
    st.markdown(f'<p class="section-heading">{label}</p>', unsafe_allow_html=True)

def mlb_section(label):
    st.markdown(f'<p class="section-heading">{label}</p>', unsafe_allow_html=True)

def render_score_ticker(games, sport: str = "NBA"):
    if not games:
        st.markdown(
            "<div class='score-ticker'><span class='sg-label'>TODAY</span>"
            "<span style='padding:0 1.5rem;font-size:0.75rem;color:var(--text-muted);'>No games scheduled today</span></div>",
            unsafe_allow_html=True)
        return
    from urllib.parse import urlencode
    items_html = ""
    for g in games:
        is_pregame = not g["live"] and not g.get("completed", False)
        status_txt = g["status"]
        if is_pregame:
            # Strip the date prefix ("M/D - ") and show just the gametime
            score = status_txt.split(" - ", 1)[-1] if " - " in status_txt else status_txt
            status_label = "PRE"
        elif g["away_score"] != "" or g["home_score"] != "":
            score = f"{g['away_score']} – {g['home_score']}"
            status_label = status_txt
        else:
            score = "–"
            status_label = status_txt
        status_cls = "sg-live" if g["live"] else ""
        game_key = f"{g['away']} @ {g['home']}"
        qs = urlencode({"ticker_game": game_key, "ticker_sport": sport})
        items_html += (
            f"<a class='sg-item sg-link' href='?{qs}' "
            f"title='View {game_key} in vs. Opponent tab'>"
            f"<div class='sg-teams'>{g['away']} @ {g['home']}</div>"
            f"<div class='sg-score'>{score}</div>"
            f"<div class='sg-status {status_cls}'>{status_label}</div>"
            f"</a>"
        )
    st.markdown(
        f"<div class='score-ticker'><span class='sg-label'>TODAY</span>{items_html}</div>",
        unsafe_allow_html=True)

def render_news_panel(news):
    if not news:
        st.caption("No news available right now.")
        return
    for item in news:
        st.markdown(
            f"<a href='{item['link']}' target='_blank' style='text-decoration:none;'>"
            f"<div class='news-card'>"
            f"<p class='news-source'>AP / ESPN</p>"
            f"<p class='news-headline'>{item['title']}</p>"
            f"<p class='news-desc'>{item['desc']}</p>"
            f"<p class='news-meta'>{item['date']}</p>"
            f"</div></a>",
            unsafe_allow_html=True)

def rolling_projection(df, col, window):
    if len(df) >= window:
        return df[col].rolling(window).mean().iloc[-1]
    return df[col].mean() if not df.empty else 0

# ══════════════════════════════════════════════════════════════════════════════
# HEADER + SPORT SELECTOR
# ══════════════════════════════════════════════════════════════════════════════

# Restore sport from ticker link query param so the correct sport handler fires
_qs_ticker_sport = st.query_params.get("ticker_sport", "")
if _qs_ticker_sport and "sport_selector" not in st.session_state:
    _sport_map = {"NBA": "🏀 NBA", "WNBA": "🏀 WNBA", "MLB": "⚾ MLB"}
    if _qs_ticker_sport in _sport_map:
        st.session_state["sport_selector"] = _sport_map[_qs_ticker_sport]

hdr_col, sport_col = st.columns([5, 1])
with hdr_col:
    st.markdown("""
    <div class="konjure-header">
        <p class="konjure-title">Konjure Analytics</p>
        <p class="konjure-sub">Multi-Sport Prop Intelligence &nbsp;&middot;&nbsp; Powered by Data</p>
    </div>""", unsafe_allow_html=True)
with sport_col:
    sport = st.selectbox("Sport", ["🏀 NBA", "🏀 WNBA", "⚾ MLB"], key="sport_selector")

# ══════════════════════════════════════════════════════════════════════════════
# SPORT-SPECIFIC CSS INJECTION
# ══════════════════════════════════════════════════════════════════════════════
_SHARED_CSS = """
<style>
html, body, .stApp {
    background-color: #111318 !important; color: #d8dae4 !important;
    --text-primary: #dfe1ea; --text-muted: #5c6272; --border: #252a35;
    --surface: #191c23; --card-bg: #191c23; --accent: #818cf8;
    --accent-dim: rgba(129,140,248,0.12);
    --accent-gradient: linear-gradient(90deg,#818cf8,#a78bfa);
    --title-gradient: linear-gradient(135deg,#dfe1ea 0%,#a5b0ff 100%);
    --mlb-navy: #a5b0ff; --mlb-red: #a78bfa; --mlb-surface: #191c23; --mlb-border: #252a35;
}
div[data-baseweb="select"] > div {
    background-color: #191c23 !important; border: 1px solid #2e3341 !important;
    color: #d8dae4 !important; border-radius: 8px !important;
}
div[data-baseweb="select"] svg { fill: #5c6272 !important; }
.stTextInput input, .stNumberInput input {
    background-color: #191c23 !important; border: 1px solid #2e3341 !important; color: #d8dae4 !important;
}
div[data-baseweb="popover"] { background-color: #1f2330 !important; border: 1px solid #2e3341 !important; border-radius: 10px !important; }
li[role="option"] { background-color: #1f2330 !important; color: #d8dae4 !important; font-size: 0.82rem !important; }
li[role="option"]:hover { background-color: #252a35 !important; }
div[data-baseweb="tag"] { background-color: rgba(129,140,248,0.14) !important; border: 1px solid rgba(129,140,248,0.3) !important; color: #a5b0ff !important; border-radius: 6px !important; }
[data-testid="stSlider"] > div > div > div { background: linear-gradient(90deg,#818cf8,#a78bfa) !important; }
[data-testid="stSlider"] [role="slider"] { background-color: #818cf8 !important; box-shadow: 0 0 0 4px rgba(129,140,248,0.2) !important; }
.stMultiSelect [data-baseweb="select"] > div { background-color: #191c23 !important; border: 1px solid #2e3341 !important; }
</style>"""
st.markdown(_SHARED_CSS, unsafe_allow_html=True)


def _render_accuracy_tab(sport_filter: str) -> None:
    """Render the Accuracy tab content, filtered to a single sport."""
    _cur_week = datetime.now().strftime("%G-W%V")
    _c1, _c2 = st.columns([3, 1])
    with _c2:
        if st.button("🔄 Resolve Outcomes", type="secondary", key=f"acc_resolve_{sport_filter}"):
            with st.spinner("Fetching game results…"):
                try:
                    if sport_filter == "NBA":
                        _cnt = parlay_tracker.resolve_nba_legs()
                        st.success(f"Resolved {_cnt} NBA leg(s).")
                    elif sport_filter == "MLB":
                        _cnt = parlay_tracker._resolve_mlb_legs()
                        st.success(f"Resolved {_cnt} MLB leg(s).")
                    elif sport_filter == "WNBA":
                        _cnt = parlay_tracker._resolve_wnba_legs()
                        st.success(f"Resolved {_cnt} WNBA leg(s).")
                    _load_calibration.clear()
                except Exception as _e:
                    st.error(f"Resolution error: {_e}")
    with _c1:
        _all_weeks_raw = parlay_tracker.get_all_weeks()
        _sport_weeks   = parlay_tracker.get_sport_weeks(sport_filter)
        _all_weeks     = sorted(set([_cur_week] + _all_weeks_raw), reverse=True)
        _default_week  = _sport_weeks[0] if _sport_weeks else _cur_week
        _default_idx   = _all_weeks.index(_default_week) if _default_week in _all_weeks else 0
        _sel_week = st.selectbox(
            "Week", _all_weeks, index=_default_idx,
            format_func=lambda w: f"{w}  {'← current' if w == _cur_week else ''}",
            key=f"acc_week_{sport_filter}",
        )
    st.divider()

    # ── Verdict strip ─────────────────────────────────────────────────────────
    # Seven tables follow, and the two facts that actually change a decision — is any
    # stat drifting, and is this sport making money — were buried among them. Answer
    # both before the tables, not after.
    _drift = parlay_tracker.get_drift_warnings(sport=sport_filter)
    _roi   = parlay_tracker.get_roi_simulation(sport=sport_filter)
    _pcal  = parlay_tracker.get_parlay_calibration(sport=sport_filter)

    _v1, _v2, _v3 = st.columns(3)
    with _v1:
        if _drift:
            _worst = _drift[0]
            st.markdown(
                f"<div class='verdict verdict-bad'><span class='verdict-k'>{len(_drift)} stat"
                f"{'s' if len(_drift) > 1 else ''} drifting</span>"
                f"<span class='verdict-v'>worst: {_worst['stat_type']} "
                f"{_worst['bias']:+.0%} over {_worst['samples']} props</span></div>",
                unsafe_allow_html=True)
        else:
            st.markdown("<div class='verdict verdict-good'><span class='verdict-k'>No drift</span>"
                        "<span class='verdict-v'>every stat within tolerance</span></div>",
                        unsafe_allow_html=True)
    with _v2:
        _r = _roi["roi_pct"]
        _cls = "verdict-good" if _r > 0 else "verdict-bad"
        st.markdown(
            f"<div class='verdict {_cls}'><span class='verdict-k'>{_r:+.1f}% ROI</span>"
            f"<span class='verdict-v'>flat $10 over {_roi['n_parlays']:,} resolved parlays</span></div>",
            unsafe_allow_html=True)
    with _v3:
        if _pcal:
            _worst_n = min(_pcal, key=lambda k: _pcal[k])
            st.markdown(
                f"<div class='verdict verdict-warn'><span class='verdict-k'>"
                f"{_worst_n}-leg overrated {1/_pcal[_worst_n]:.1f}x</span>"
                f"<span class='verdict-v'>parlay calibration deflates it automatically</span></div>",
                unsafe_allow_html=True)
        else:
            st.markdown("<div class='verdict verdict-warn'><span class='verdict-k'>No parlay "
                        "calibration yet</span><span class='verdict-v'>needs more resolved "
                        "parlays per pick count</span></div>", unsafe_allow_html=True)
    st.write("")

    # ── Weekly KPIs ───────────────────────────────────────────────────────────
    _wsum = parlay_tracker.get_weekly_summary(_sel_week, sport=sport_filter)
    _mc1, _mc2, _mc3, _mc4, _mc5 = st.columns(5)
    _mc1.metric("Parlays Generated", _wsum["total_parlays"])
    _mc2.metric("Resolved", _wsum["resolved_parlays"])
    _phit  = f"{_wsum['parlay_hit_rate']*100:.1f}%" if _wsum["parlay_hit_rate"] is not None else "—"
    _ppred = f"pred {_wsum['avg_predicted_prob']*100:.1f}%" if _wsum["avg_predicted_prob"] is not None else ""
    _mc3.metric("Parlay Hit Rate", _phit, delta=_ppred or None)
    _lhit  = f"{_wsum['leg_hit_rate']*100:.1f}%" if _wsum["leg_hit_rate"] is not None else "—"
    _mc4.metric("Leg Hit Rate", _lhit)
    _mc5.metric("Legs Resolved", f"{_wsum['resolved_legs']} / {_wsum['total_legs']}")

    # ── Streak ticker ─────────────────────────────────────────────────────────
    _streak = parlay_tracker.get_streak_info(sport=sport_filter)
    if _streak["total_resolved"] > 0:
        _stype  = _streak["current_type"]
        _sn     = _streak["current_streak"]
        _scolor = "#22c55e" if _stype == "hit" else "#f87171"
        _slabel = f"{'🔥' if _stype == 'hit' else '❄️'} {_sn}-in-a-row {'hit' if _stype == 'hit' else 'miss'}"
        st.markdown(
            f"<div style='display:flex;gap:1.5rem;align-items:center;margin:0.4rem 0 0;'>"
            f"<span style='font-size:0.8rem;font-weight:700;color:{_scolor};'>{_slabel}</span>"
            f"<span style='font-size:0.7rem;color:var(--text-muted);'>longest hit streak: "
            f"<strong style='color:var(--text-primary);'>{_streak['longest_hit']}</strong> &nbsp;·&nbsp; "
            f"longest miss streak: <strong style='color:var(--text-primary);'>{_streak['longest_miss']}</strong>"
            f"</span></div>",
            unsafe_allow_html=True,
        )
    st.divider()

    # ── Best & worst parlay of the week ───────────────────────────────────────
    _bw = parlay_tracker.get_best_worst_week(_sel_week, sport=sport_filter)
    if _bw["best"] or _bw["worst"]:
        st.markdown("#### Best & Worst of the Week")
        _bw_c1, _bw_c2 = st.columns(2)
        with _bw_c1:
            if _bw["best"]:
                _b = _bw["best"]
                st.markdown(
                    f"<div style='background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.2);"
                    f"border-radius:10px;padding:0.8rem 1rem;'>"
                    f"<div style='font-size:0.6rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;"
                    f"color:#22c55e;margin-bottom:0.3rem;'>✅ Best Hit</div>"
                    f"<div style='font-size:0.85rem;font-weight:700;color:var(--text-primary);'>"
                    f"{_b.get('sportsbook','?')} · {len(_b['legs'])}-leg · {_b['predicted_prob']*100:.1f}% predicted</div>"
                    f"<div style='font-size:0.72rem;color:#22c55e;font-weight:700;'>"
                    f"Payout: {_b.get('payout','?')}×</div>"
                    f"<div style='font-size:0.68rem;color:var(--text-muted);margin-top:0.3rem;'>"
                    + " · ".join(f"{l['player_name']} {l['stat_type']} >{l['line_score']}" for l in _b["legs"])
                    + "</div></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No hits this week yet.")
        with _bw_c2:
            if _bw["worst"]:
                _w = _bw["worst"]
                st.markdown(
                    f"<div style='background:rgba(248,113,113,0.06);border:1px solid rgba(248,113,113,0.2);"
                    f"border-radius:10px;padding:0.8rem 1rem;'>"
                    f"<div style='font-size:0.6rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;"
                    f"color:#f87171;margin-bottom:0.3rem;'>❌ Worst Miss</div>"
                    f"<div style='font-size:0.85rem;font-weight:700;color:var(--text-primary);'>"
                    f"{_w.get('sportsbook','?')} · {len(_w['legs'])}-leg · {_w['predicted_prob']*100:.1f}% predicted</div>"
                    f"<div style='font-size:0.72rem;color:#f87171;font-weight:700;'>Most confident miss</div>"
                    f"<div style='font-size:0.68rem;color:var(--text-muted);margin-top:0.3rem;'>"
                    + " · ".join(
                        f"{'✅' if l['outcome'] is True else '❌'} {l['player_name']} {l['stat_type']} >{l['line_score']}"
                        for l in _w["legs"]
                    )
                    + "</div></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No misses this week yet.")
        st.divider()

    # ── Safe vs Value + Sportsbook comparison ─────────────────────────────────
    st.markdown("#### Performance Breakdown")
    _pb_c1, _pb_c2 = st.columns(2)

    with _pb_c1:
        st.markdown("**Safe vs Value**")
        _kc = parlay_tracker.get_kind_comparison(sport=sport_filter)
        _kc_rows = []
        for _kname in ("safe", "value"):
            _kd = _kc.get(_kname)
            if _kd:
                _kc_rows.append({
                    "Kind":       _kname.title(),
                    "Parlays":    _kd["total"],
                    "Hits":       _kd["hits"],
                    "Hit Rate":   f"{_kd['hit_rate_pct']}%",
                    "ROI":        f"{_kd['roi_pct']:+.1f}%",
                    "Avg Pred":   f"{_kd['avg_predicted_pct']}%",
                })
        if _kc_rows:
            st.dataframe(pd.DataFrame(_kc_rows), hide_index=True, width="stretch")
        else:
            st.caption("No resolved data yet.")

    with _pb_c2:
        st.markdown("**By Sportsbook**")
        _sbc = parlay_tracker.get_sportsbook_comparison(sport=sport_filter)
        _sb_rows = [
            {"Sportsbook": sb, "Parlays": d["total"], "Hits": d["hits"],
             "Hit Rate": f"{d['hit_rate_pct']}%", "ROI": f"{d['roi_pct']:+.1f}%"}
            for sb, d in _sbc.items()
        ]
        if _sb_rows:
            st.dataframe(pd.DataFrame(_sb_rows), hide_index=True, width="stretch")
        else:
            st.caption("No resolved data yet.")

    # ── Leg count breakdown ───────────────────────────────────────────────────
    st.markdown("**By Leg Count**")
    _lcd = parlay_tracker.get_leg_count_breakdown(sport=sport_filter)
    if _lcd:
        _lcd_df = pd.DataFrame(_lcd)
        _lcd_df.columns = ["Legs", "Total", "Hits", "Hit Rate %", "Avg EV", "ROI %"]
        st.dataframe(_lcd_df, hide_index=True, width="stretch")
    else:
        st.caption("No resolved data yet.")
    st.divider()

    # ── ROI simulation ────────────────────────────────────────────────────────
    st.markdown("#### ROI Simulation")
    st.caption(
        "Simulates a $10 flat bet on every resolved parlay in historical order. "
        "Payouts are gross (a 2-pick returns 3x the entry), so a win profits payout − 1. "
        "Kelly is staked per slate — a day's parlays are placed at once and share legs, "
        "so stakes are sized off that morning's bankroll and the day's total exposure is "
        "capped at 25% of it. Kelly sizes off the model's predicted probability, which "
        "still runs optimistic, so treat its growth as an upper bound."
    )
    _roi = parlay_tracker.get_roi_simulation(sport=sport_filter)
    if _roi["n_parlays"] > 0:
        _r1, _r2, _r3, _r4 = st.columns(4)
        _r1.metric("Parlays Simulated", _roi["n_parlays"])
        _roi_color = "normal" if _roi["roi_pct"] >= 0 else "inverse"
        _r2.metric("Flat-Bet P&L", f"${_roi['flat_pnl']:+,.2f}", delta=f"{_roi['roi_pct']:+.1f}% ROI")
        _r3.metric("Total Wagered", f"${_roi['total_wagered']:,.0f}")
        _r4.metric("Kelly Bankroll", f"${_roi['kelly_bankroll']:,.2f}",
                   delta=f"${_roi['kelly_bankroll']-1000:+,.2f} from $1,000 start")

        if len(_roi["flat_series"]) > 1:
            _chart_data = pd.DataFrame({
                "Flat P&L ($)":      _roi["flat_series"],
                "Kelly Bankroll ($)": _roi["kelly_series"],
            }, index=_roi["dates"])
            _tabs_roi = st.tabs(["Flat Bet P&L", "Kelly Bankroll"])
            with _tabs_roi[0]:
                st.line_chart(_chart_data[["Flat P&L ($)"]], height=200)
            with _tabs_roi[1]:
                st.line_chart(_chart_data[["Kelly Bankroll ($)"]], height=200)
    else:
        st.caption("No resolved parlays yet — resolve outcomes to populate simulation.")
    st.divider()

    # ── Line value analysis ───────────────────────────────────────────────────
    st.markdown("#### Line Value Analysis")
    st.caption("Do legs where the model sees positive edge (predicted > implied odds) actually hit more?")
    _lva = parlay_tracker.get_line_value_analysis(sport=sport_filter)
    _lv_c1, _lv_c2 = st.columns(2)
    with _lv_c1:
        _pos = _lva.get("positive_edge")
        if _pos:
            st.markdown(
                f"<div style='background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.2);"
                f"border-radius:10px;padding:0.75rem 1rem;text-align:center;'>"
                f"<div style='font-size:0.6rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;"
                f"color:#22c55e;'>Positive Edge Legs</div>"
                f"<div style='font-size:1.6rem;font-weight:800;color:var(--text-primary);'>{_pos['hit_rate_pct']}%</div>"
                f"<div style='font-size:0.7rem;color:var(--text-muted);'>actual hit rate · {_pos['n']} legs</div>"
                f"<div style='font-size:0.72rem;color:#22c55e;font-weight:700;margin-top:0.2rem;'>"
                f"avg edge: +{_pos['avg_edge_pct']:.1f}%</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("No positive-edge legs resolved yet.")
    with _lv_c2:
        _neg = _lva.get("negative_edge")
        if _neg:
            st.markdown(
                f"<div style='background:rgba(248,113,113,0.06);border:1px solid rgba(248,113,113,0.2);"
                f"border-radius:10px;padding:0.75rem 1rem;text-align:center;'>"
                f"<div style='font-size:0.6rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;"
                f"color:#f87171;'>Negative Edge Legs</div>"
                f"<div style='font-size:1.6rem;font-weight:800;color:var(--text-primary);'>{_neg['hit_rate_pct']}%</div>"
                f"<div style='font-size:0.7rem;color:var(--text-muted);'>actual hit rate · {_neg['n']} legs</div>"
                f"<div style='font-size:0.72rem;color:#f87171;font-weight:700;margin-top:0.2rem;'>"
                f"avg edge: {_neg['avg_edge_pct']:.1f}%</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("No negative-edge legs resolved yet.")
    st.divider()

    # ── Calibration drift alerts ──────────────────────────────────────────────
    st.markdown("#### Calibration Drift Alerts")
    st.caption("Flags stat types where the rolling 30-day actual hit rate has shifted ≥15 ppts from the all-time baseline.")
    _drift = parlay_tracker.get_calibration_drift(sport=sport_filter)
    if _drift:
        for _d in _drift:
            _dcol = "#22c55e" if _d["direction"] == "hot" else "#f87171"
            _darrow = "↑" if _d["direction"] == "hot" else "↓"
            st.markdown(
                f"<div style='background:rgba(248,113,113,0.05);border-left:3px solid {_dcol};"
                f"border-radius:0 8px 8px 0;padding:0.5rem 0.8rem;margin-bottom:0.4rem;'>"
                f"<span style='font-weight:700;color:var(--text-primary);'>{_d['stat_type']}</span>"
                f"<span style='color:var(--text-muted);font-size:0.8rem;'> — all-time: "
                f"{_d['alltime_hit_pct']}% → recent 30d: "
                f"<strong style='color:{_dcol};'>{_d['recent_hit_pct']}% {_darrow}</strong>"
                f" ({_d['drift_pct']:+.1f} ppts, n={_d['n_recent']})</span></div>",
                unsafe_allow_html=True,
            )
    else:
        st.success("No significant calibration drift detected (30-day window).", icon="✅")

    # ── All-time calibration ──────────────────────────────────────────────────
    st.markdown("#### All-Time Model Calibration")
    st.caption(
        f"Active once a stat reaches ≥{parlay_tracker.CAL_MIN_SAMPLES} resolved legs. "
        "Factor > 1.0 = model underestimates; < 1.0 = overestimates."
    )
    _cal_rows = parlay_tracker.get_all_time_calibration_table(sport=sport_filter)
    if _cal_rows:
        _cdf = pd.DataFrame(_cal_rows)
        _cdf["Calibration Factor"] = _cdf["calibration_factor"].apply(
            lambda x: (f"{x:.3f}  ✓" if x is not None
                       else f"— (need {parlay_tracker.CAL_MIN_SAMPLES}+ samples)")
        )
        _cdf["Predicted Hit%"] = (_cdf["predicted_hit_rate"] * 100).round(1).astype(str) + "%"
        _cdf["Actual Hit%"]    = (_cdf["actual_hit_rate"]    * 100).round(1).astype(str) + "%"
        st.dataframe(
            _cdf[["stat_type", "samples", "Predicted Hit%", "Actual Hit%", "Calibration Factor"]]
            .rename(columns={"stat_type": "Stat Type", "samples": "Props"}),
            hide_index=True, width="stretch",
        )
    else:
        st.info("No resolved leg data yet.")
    st.divider()

    # ── Per-stat breakdown (weekly) ───────────────────────────────────────────
    if _wsum["stat_breakdown"]:
        st.markdown(f"#### Per-Stat Breakdown — {_sel_week}")
        _sb_rows = []
        for stat, d in sorted(_wsum["stat_breakdown"].items()):
            b = d["bias"]
            _sb_rows.append({
                "Stat Type":      stat,
                "Props":          d["n"],
                "Predicted Hit%": f"{d['predicted_hit_rate']*100:.1f}%",
                "Actual Hit%":    f"{d['actual_hit_rate']*100:.1f}%",
                "Bias":           f"{'+' if b >= 0 else ''}{b*100:.1f}%",
            })
        st.dataframe(pd.DataFrame(_sb_rows), hide_index=True, width="stretch")
    elif _wsum["total_legs"] > 0:
        _pending = _wsum["total_legs"] - _wsum["resolved_legs"]
        st.info(
            f"**{_pending} leg(s) pending resolution** for {_sel_week}. "
            "Click **🔄 Resolve Outcomes** above to fetch game results."
        )
    else:
        st.info(
            f"No {sport_filter} parlays logged for this week. "
            "Build parlays on the **Parlays** tab to start tracking accuracy."
        )

    # ── Player accuracy table ─────────────────────────────────────────────────
    _pa = parlay_tracker.get_player_accuracy(sport=sport_filter)
    if _pa:
        with st.expander(f"Player Accuracy Table ({len(_pa)} player/stat combos)", expanded=False):
            st.caption("Sorted by sample count. Edge = predicted hit rate − sportsbook implied probability.")
            _pa_df = pd.DataFrame(_pa)
            _pa_df.columns = ["Player", "Stat", "Props", "Pred Hit%", "Actual Hit%", "Bias (ppts)", "Avg Edge (ppts)"]
            st.dataframe(_pa_df, hide_index=True, width="stretch")
    st.divider()

    # ── Monthly trends ────────────────────────────────────────────────────────
    _mt = parlay_tracker.get_monthly_trends(sport=sport_filter)
    if len(_mt) >= 2:
        st.markdown("#### Monthly Trends")
        _mt_df = pd.DataFrame(_mt)
        _chart_mt = _mt_df.set_index("month")[["hit_rate_pct", "leg_hit_rate_pct", "avg_pred_pct"]]
        _chart_mt.columns = ["Parlay Hit %", "Leg Hit %", "Avg Predicted %"]
        st.line_chart(_chart_mt, height=220)
        _mt_tbl = _mt_df.rename(columns={
            "month": "Month", "total": "Parlays", "hits": "Hits",
            "hit_rate_pct": "Hit Rate %", "leg_hit_rate_pct": "Leg Hit %", "avg_pred_pct": "Avg Pred %"
        })[["Month","Parlays","Hits","Hit Rate %","Leg Hit %","Avg Pred %"]]
        st.dataframe(_mt_tbl, hide_index=True, width="stretch")
        st.divider()

    # ── Full parlay log ───────────────────────────────────────────────────────
    st.markdown(f"#### Parlay Log — {_sel_week}")
    _log_data   = parlay_tracker._load()
    _wk_parlays = [p for p in _log_data["parlays"]
                   if p.get("iso_week") == _sel_week and p.get("sport") == sport_filter]
    if _wk_parlays:
        for _wp in _wk_parlays:
            _hit_icon = ("✅ Hit" if _wp["parlay_hit"] is True
                         else ("❌ Miss" if _wp["parlay_hit"] is False else "⏳ Pending"))
            _label = (f"{_wp.get('sportsbook','?')} · {_wp.get('kind','safe').title()} · "
                      f"{_wp['predicted_prob']*100:.1f}% predicted · {_hit_icon}")
            with st.expander(_label):
                for _wl in _wp["legs"]:
                    _icon = ("✅" if _wl["outcome"] is True
                             else ("❌" if _wl["outcome"] is False else "⏳"))
                    _odds_s = ""
                    if _wl.get("american_odds"):
                        _a = _wl["american_odds"]
                        _odds_s = f"  |  {'+' if _a > 0 else ''}{_a}"
                    st.markdown(
                        f"{_icon} **{_wl['player_name']}** — "
                        f"{_wl['stat_type']} > {_wl['line_score']}"
                        f"{_odds_s}  |  "
                        f"predicted {_wl['predicted_hit_rate']*100:.0f}%  |  "
                        f"game: {_wl.get('game_label','—')}"
                    )
    else:
        st.caption("No parlays logged for this week yet.")
    st.divider()

    # ── CSV export ────────────────────────────────────────────────────────────
    st.markdown("#### Export")
    _csv_data = parlay_tracker.export_csv(_sel_week, sport=sport_filter)
    st.download_button(
        label=f"📥 Download {sport_filter} {_sel_week} CSV",
        data=_csv_data,
        file_name=f"konjure_{sport_filter.lower()}_parlays_{_sel_week}.csv",
        mime="text/csv",
        type="secondary",
        key=f"acc_dl_{sport_filter}",
    )


# ══════════════════════════════════════════════════════════════════════════════
# WNBA DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

_WNBA_SEASONS = ["2022", "2023", "2024", "2025"]

_WNBA_STAT_MAP = {
    "Points":        "PTS",
    "Rebounds":      "REB",
    "Assists":       "AST",
    "Steals":        "STL",
    "Blocks":        "BLK",
    "3-PT Made":     "FG3M",
    "Pts+Rebs+Asts": "PTS",   # computed below
    "Pts+Rebs":      "PTS",
    "Pts+Asts":      "PTS",
}

_WNBA_PP_STAT_MAP = {
    "Points": "Points", "Rebounds": "Rebounds", "Assists": "Assists",
    "Steals": "Steals", "Blocks": "Blocked Shots", "3-PT Made": "3-Pointers Made",
    "Pts+Rebs+Asts": "Pts+Rebs+Asts", "Pts+Rebs": "Pts+Rebs", "Pts+Asts": "Pts+Asts",
}

# Static WNBA team table — nba_api team IDs for CommonTeamRoster
_WNBA_TEAMS = [
    {"full_name": "Atlanta Dream",          "abbreviation": "ATL", "id": 1611661328},
    {"full_name": "Chicago Sky",            "abbreviation": "CHI", "id": 1611661320},
    {"full_name": "Connecticut Sun",        "abbreviation": "CON", "id": 1611661319},
    {"full_name": "Dallas Wings",           "abbreviation": "DAL", "id": 1611661323},
    {"full_name": "Golden State Valkyries", "abbreviation": "GSV", "id": 1611661396},
    {"full_name": "Indiana Fever",          "abbreviation": "IND", "id": 1611661325},
    {"full_name": "Las Vegas Aces",         "abbreviation": "LVA", "id": 1611661330},
    {"full_name": "Los Angeles Sparks",     "abbreviation": "LA",  "id": 1611661316},
    {"full_name": "Minnesota Lynx",         "abbreviation": "MIN", "id": 1611661322},
    {"full_name": "New York Liberty",       "abbreviation": "NYL", "id": 1611661317},
    {"full_name": "Phoenix Mercury",        "abbreviation": "PHX", "id": 1611661324},
    {"full_name": "Seattle Storm",          "abbreviation": "SEA", "id": 1611661329},
    {"full_name": "Washington Mystics",     "abbreviation": "WAS", "id": 1611661321},
]
_WNBA_ABBR_TO_FULL = {t["abbreviation"].upper(): t["full_name"] for t in _WNBA_TEAMS}
_WNBA_FULL_TO_ABBR = {t["full_name"]: t["abbreviation"] for t in _WNBA_TEAMS}
_WNBA_ABBR_TO_ID   = {t["abbreviation"].upper(): t["id"]        for t in _WNBA_TEAMS}

# ESPN may return short abbreviations; map to our canonical ones
_ESPN_TO_WNBA_ABBR = {
    "LV":  "LVA",
    "NY":  "NYL",
    "LA":  "LA",
    "GS":  "GSV",
    "CON": "CON",
    "WSH": "WAS",
}

def _resolve_wnba_abbr(espn_abbr: str) -> str:
    return _ESPN_TO_WNBA_ABBR.get(espn_abbr.upper(), espn_abbr.upper())

def get_wnba_team_abbreviation(team_name: str) -> str | None:
    return _WNBA_FULL_TO_ABBR.get(team_name)

def get_wnba_team_id(abbr: str) -> int | None:
    return _WNBA_ABBR_TO_ID.get(abbr.upper())

# ESPN WNBA roster slugs differ from our canonical abbreviations for a few teams
_WNBA_ABBR_TO_ESPN_SLUG = {
    "LVA": "lv",
    "NYL": "ny",
    "GSV": "gs",
}

def _wnba_espn_slug(abbr: str) -> str:
    return _WNBA_ABBR_TO_ESPN_SLUG.get(abbr.upper(), abbr.lower())

@st.cache_data(ttl=3600)
def _get_wnba_team_roster_map(team_abbr: str) -> dict:
    """Returns {player_name: espn_id} for a team. Cached so both names and IDs are stored."""
    slug = _wnba_espn_slug(team_abbr)
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/{slug}/roster"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            result = {}
            for a in resp.json().get("athletes", []):
                name = a.get("displayName") or a.get("fullName", "")
                espn_id = str(a.get("id", ""))
                if name and espn_id:
                    result[name] = espn_id
            return result
    except Exception:
        pass
    return {}

def get_wnba_team_players(team_abbr: str):
    """Return list of player names for a WNBA team."""
    return list(_get_wnba_team_roster_map(team_abbr).keys())

@st.cache_data(ttl=60)
def get_wnba_scoreboard(game_date: str):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={game_date}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        games = []
        for ev in resp.json().get("events", []):
            comp = ev["competitions"][0]
            comps = comp["competitors"]
            home = next((c for c in comps if c["homeAway"] == "home"), comps[0])
            away = next((c for c in comps if c["homeAway"] == "away"), comps[-1])
            st_type = ev["status"]["type"]
            games.append({
                "away": away["team"]["abbreviation"],
                "home": home["team"]["abbreviation"],
                "away_score": away.get("score", ""),
                "home_score": home.get("score", ""),
                "status": st_type.get("shortDetail", st_type.get("description", "")),
                "live": st_type.get("state", "") == "in",
                "completed": st_type.get("completed", False),
            })
        return games
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_wnba_scoreboard_full(game_date: str):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={game_date}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        out = []
        for ev in resp.json().get("events", []):
            comp = ev["competitions"][0]
            comps = comp["competitors"]
            home = next((c for c in comps if c["homeAway"] == "home"), comps[0])
            away = next((c for c in comps if c["homeAway"] == "away"), comps[-1])
            out.append({
                "name":       ev.get("shortName", ev.get("name", "")),
                "away":       away["team"]["displayName"],
                "away_abbr":  away["team"]["abbreviation"],
                "away_record": ((away.get("records") or [{}])[0].get("summary", "")),
                "home":       home["team"]["displayName"],
                "home_abbr":  home["team"]["abbreviation"],
                "home_record": ((home.get("records") or [{}])[0].get("summary", "")),
                "status":     ev["status"]["type"].get("shortDetail", ""),
                "venue":      comp.get("venue", {}).get("fullName", ""),
            })
        return out
    except Exception:
        return []

def wnba_player_card(player_name: str, team_code: str):
    pid = get_wnba_player_id(player_name)
    headshot = f"https://cdn.nba.com/headshots/wnba/latest/1040x760/{pid}.png" if pid else ""
    logo = f"https://a.espncdn.com/i/teamlogos/wnba/500/{team_code.lower()}.png"
    st.markdown(f"""
    <div class="player-card">
        <div class="player-card-left">
            <img src="{headshot}" class="player-headshot">
        </div>
        <div class="player-card-right">
            <p class="player-card-name">{player_name}</p>
            <p class="player-card-team">
                <img src="{logo}" class="team-logo-inline">
                {team_code}
            </p>
        </div>
    </div>""", unsafe_allow_html=True)

def _wnba_scoreboard_date():
    from datetime import timezone, timedelta as _td
    cst = datetime.now(timezone(_td(hours=-6)))
    if cst.hour < 2:
        cst = cst - _td(days=1)
    return cst.strftime("%Y%m%d")



def _fallback_wnba_legs(stat_types: list = None, cal: dict = None) -> list:
    """Build WNBA parlay legs from top players using ESPN historical averages as lines."""
    if stat_types is None:
        stat_types = ["Pts+Rebs+Asts"]
    if cal is None:
        cal = {}
    TOP_WNBA = [
        "A'ja Wilson", "Caitlin Clark", "Breanna Stewart", "Sabrina Ionescu",
        "Alyssa Thomas", "Kelsey Plum", "Jewell Loyd", "Rhyne Howard",
        "Napheesa Collier", "Jonquel Jones", "DeWanna Bonner", "Nneka Ogwumike",
        "Jackie Young", "Dearica Hamby", "Kahleah Copper",
    ]
    _fw_ids = [(p, get_wnba_player_id(p)) for p in TOP_WNBA]
    _fw_ids = [(p, pid) for p, pid in _fw_ids if pid]
    with ThreadPoolExecutor(max_workers=4) as _fex:
        _ffuts = [_fex.submit(get_wnba_gamelogs, pid, ("2025",)) for _, pid in _fw_ids]
        for _ff in _ffuts:
            try:
                _ff.result(timeout=30)
            except Exception:
                pass
    legs = []
    for player in TOP_WNBA:
        pid = get_wnba_player_id(player)
        if not pid:
            continue
        df = get_wnba_gamelogs(pid, ("2025",))
        if df.empty:
            df = get_wnba_gamelogs(pid, ("2024",))
        if df.empty:
            continue
        for stat in stat_types:
            col = _WNBA_PARLAY_COL_MAP.get(stat)
            if not col:
                continue
            if col in ("PRA", "PR", "PA"):
                df = df.copy()
                if col == "PRA":
                    df["PRA"] = df["PTS"] + df["REB"] + df["AST"]
                elif col == "PR":
                    df["PR"] = df["PTS"] + df["REB"]
                elif col == "PA":
                    df["PA"] = df["PTS"] + df["AST"]
            if col not in df.columns:
                continue
            vals = df[col].values[-20:]
            if len(vals) < 3:
                continue
            line = round(float(vals.mean()) * 0.88, 1)
            last30 = vals[-30:] if len(vals) >= 5 else vals
            last10 = vals[-10:] if len(vals) >= 10 else vals
            n = len(last30)
            if n < 3:
                continue
            r30 = float((last30 > line).sum()) / len(last30)
            r10 = float((last10 > line).sum()) / len(last10) if len(last10) >= 5 else r30
            cal_f = cal.get(stat, 1.0)
            rate = round(min(0.97, max(0.03, (0.7 * (0.6 * r10 + 0.4 * r30) + 0.3 * 0.5) * cal_f)), 3)
            legs.append({
                "player_name": player, "team": "", "stat_type": stat,
                "line_score": line, "odds_type": "standard", "american_odds": -110,
                "implied_prob": rate, "game_id": "", "game_label": "Historical",
                "hit_rate": rate, "sample_n": n, "sportsbook": "Historical",
            })
    return legs


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════  NBA  ════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
if sport == "🏀 NBA":

    # ── NBA score ticker (shown on all NBA tabs) ───────────────────────────────
    with st.spinner(""):
        _nba_scores = get_nba_scoreboard(_scoreboard_date())
    render_score_ticker(_nba_scores, "NBA")

    # ── Ticker click: set game + flag, then rerun to let home tab fire nav JS ──
    if (st.query_params.get("ticker_sport") == "NBA"
            and st.query_params.get("ticker_game")):
        st.session_state["nvo_game"] = st.query_params["ticker_game"]
        st.session_state["_nav_vs_nba"] = True
        del st.query_params["ticker_game"]
        del st.query_params["ticker_sport"]
        st.rerun()

    _nba_tabs_iter = _grouped_tabs([
        ("Home",    []),
        ("Analyze", ["Player Stats", "Opponent Breakdown", "vs. Opponent"]),
        ("Bet",     ["Bet Simulation", "First Basket", "Sportsbook", "Parlays"]),
        *( [("Track", ["Accuracy"])] if _IS_LOCAL else [] ),
        ("About",   ["Daily Blog", "Disclaimer"]),
    ])
    tab_home        = next(_nba_tabs_iter)
    tab_stats       = next(_nba_tabs_iter)
    tab_opp         = next(_nba_tabs_iter)
    tab_vs_opp_nba  = next(_nba_tabs_iter)
    tab_sim         = next(_nba_tabs_iter)
    tab_fb          = next(_nba_tabs_iter)
    tab_pp          = next(_nba_tabs_iter)
    tab_parlays     = next(_nba_tabs_iter)
    tab_accuracy_nba = next(_nba_tabs_iter) if _IS_LOCAL else None
    tab_blog        = next(_nba_tabs_iter)
    tab_disc        = next(_nba_tabs_iter)

    # ── HOME ──────────────────────────────────────────────────────────────────
    with tab_home:
        if st.session_state.pop("_nba_ptw_nav", False):
            components.html(
                "<script>setTimeout(function(){"
                "var t=window.parent.document.querySelectorAll('[role=\"tab\"]');"
                "if(t.length>1)t[1].click();"
                "},150);</script>",
                height=0,
            )
        if st.session_state.pop("_nav_vs_nba", False):
            components.html(
                "<script>setTimeout(function(){"
                "var t=window.parent.document.querySelectorAll('[role=\"tab\"]');"
                "if(t[3])t[3].click();"
                "},300);</script>",
                height=0,
            )
        # ── 2026 NBA Free Agent Tracker ───────────────────────────────────────
        _fa_today = datetime.now().strftime("%B %d, %Y")
        st.markdown(
            f"<div class='draft-header'>"
            f"<div class='draft-kicker'>2026 NBA Free Agency &nbsp;·&nbsp; Konjure Projections</div>"
            f"<div class='draft-countdown'>Moratorium opens July 1 &nbsp;·&nbsp; Signings official July 6 &nbsp;·&nbsp; "
            f"<strong style='color:var(--text-primary);'>Updated {_fa_today}</strong></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # ── Tier colour + confidence bar helpers ──────────────────────────────
        def _tier_color(t):
            return {
                "Supermax": "#f59e0b", "Max": "#818cf8",
                "Near-Max": "#60a5fa", "Starter": "#34d399",
                "MLE": "#94a3b8", "Starter / MLE": "#34d399",
                "MLE / Veteran": "#94a3b8", "Veteran Min": "#6b7280",
                "Veteran minimum": "#6b7280",
            }.get(t, "#6b7280")

        def _conf_color(c):
            if c >= 65:  return "#22c55e"
            if c >= 45:  return "#f59e0b"
            return "#f87171"

        def _conf_bar(c):
            color = _conf_color(c)
            return (
                f"<div style='background:rgba(255,255,255,0.07);border-radius:4px;"
                f"height:5px;margin:0.4rem 0 0.6rem;'>"
                f"<div style='width:{c}%;height:5px;border-radius:4px;"
                f"background:{color};'></div></div>"
            )

        # ── Interest teams chips ───────────────────────────────────────────────
        def _interest_chips(teams_list):
            chips = " ".join(
                f"<span style='display:inline-block;background:rgba(255,255,255,0.07);"
                f"border:1px solid rgba(255,255,255,0.1);border-radius:99px;"
                f"font-size:0.6rem;padding:0.15rem 0.5rem;margin:0.1rem 0.15rem 0 0;"
                f"color:var(--text-muted);'>{t}</span>"
                for t in teams_list
            )
            return f"<div style='margin-bottom:0.5rem;'>{chips}</div>"

        # ── Stats pill row helper ─────────────────────────────────────────────
        def _stats_row(stats: dict) -> str:
            if not stats:
                return ""
            items = [
                ("PPG", stats.get("ppg", "—")),
                ("RPG", stats.get("rpg", "—")),
                ("APG", stats.get("apg", "—")),
                ("FG%", f"{stats['fg_pct']:.1f}" if "fg_pct" in stats else "—"),
                ("TS%", f"{stats['ts_pct']:.1f}" if "ts_pct" in stats else "—"),
                ("PER", stats.get("per", "—")),
            ]
            pills = "".join(
                f"<span style='display:inline-flex;flex-direction:column;align-items:center;"
                f"background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);"
                f"border-radius:6px;padding:0.2rem 0.45rem;margin:0 0.2rem 0 0;'>"
                f"<span style='font-size:0.72rem;font-weight:700;color:var(--text-primary);'>{v}</span>"
                f"<span style='font-size:0.55rem;color:var(--text-muted);letter-spacing:0.06em;'>{k}</span>"
                f"</span>"
                for k, v in items
            )
            return (
                f"<div style='display:flex;flex-wrap:wrap;gap:0;margin:0.5rem 0 0.55rem;"
                f"padding:0.4rem 0;border-top:1px solid rgba(255,255,255,0.05);"
                f"border-bottom:1px solid rgba(255,255,255,0.05);'>{pills}</div>"
                f"<div style='font-size:0.58rem;color:var(--text-muted);margin-bottom:0.3rem;'>"
                f"2025–26 regular season</div>"
            )

        # ── Live analytics row (from nba_api) ─────────────────────────────────
        def _analytics_row(anl: dict) -> str:
            if not anl:
                return ""
            net = anl.get("net_rating", 0)
            net_color = "#22c55e" if net > 2 else ("#f59e0b" if net > -2 else "#f87171")
            items = [
                ("USG%",    f"{anl.get('usg_pct', 0):.1f}"),
                ("NET RTG", f"{'+' if net >= 0 else ''}{net:.1f}"),
                ("PIE",     f"{anl.get('pie', 0):.1f}"),
                ("OFF RTG", f"{anl.get('off_rating', 0):.1f}"),
                ("DEF RTG", f"{anl.get('def_rating', 0):.1f}"),
                ("AST%",    f"{anl.get('ast_pct', 0):.1f}"),
                ("eFG%",    f"{anl.get('efg_pct', 0):.1f}"),
            ]
            pills = "".join(
                f"<span style='display:inline-flex;flex-direction:column;align-items:center;"
                f"background:rgba(99,102,241,0.08);border:1px solid rgba(99,102,241,0.18);"
                f"border-radius:6px;padding:0.2rem 0.4rem;margin:0 0.18rem 0 0;'>"
                f"<span style='font-size:0.7rem;font-weight:700;"
                f"color:{'var(--text-primary)' if k != 'NET RTG' else net_color};'>{v}</span>"
                f"<span style='font-size:0.52rem;color:var(--text-muted);letter-spacing:0.05em;'>{k}</span>"
                f"</span>"
                for k, v in items
            )
            return (
                f"<div style='display:flex;flex-wrap:wrap;gap:0;margin:0.45rem 0 0.2rem;"
                f"padding:0.35rem 0;border-top:1px solid rgba(99,102,241,0.12);'>{pills}</div>"
                f"<div style='font-size:0.57rem;color:rgba(99,102,241,0.6);margin-bottom:0.4rem;'>"
                f"2025–26 season advanced stats</div>"
            )

        # ── FA card renderer ───────────────────────────────────────────────────
        def _render_fa_card(fa, analytics: dict | None = None):
            tc  = _tier_color(fa["tier"])
            cc  = _conf_color(fa["confidence"])
            bar = _conf_bar(fa["confidence"])
            interest_html = _interest_chips(fa.get("interest_teams", []))
            stats_html = _stats_row(fa.get("stats", {}))
            anl_html = _analytics_row(analytics or {})
            _st_lower = fa["status"].lower()
            status_color = ("#22c55e" if "signed" in _st_lower
                            else "#f59e0b" if "opt" in _st_lower
                            else "#60a5fa")
            return (
                f"<div style='background:var(--card-bg);border:1px solid var(--border);"
                f"border-top:3px solid {tc};border-radius:12px;"
                f"padding:1.1rem 1.25rem;margin-bottom:1rem;'>"
                # Header row
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.4rem;'>"
                f"<div>"
                f"<span style='font-size:1.0rem;font-weight:700;color:var(--text-primary);'>{fa['name']}</span>"
                f"<span style='font-size:0.72rem;color:var(--text-muted);margin-left:0.5rem;'>"
                f"{fa['pos']} &nbsp;·&nbsp; Age {fa['age']}</span>"
                f"</div>"
                f"<span style='font-size:0.6rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;"
                f"color:{tc};white-space:nowrap;margin-left:0.5rem;'>{fa['tier']}</span>"
                f"</div>"
                # From team + status
                f"<div style='font-size:0.7rem;color:var(--text-muted);margin-bottom:0.55rem;'>"
                f"<span style='color:var(--text-primary);font-weight:600;'>{fa['from_team']}</span>"
                f" &nbsp;·&nbsp; "
                f"<span style='color:{status_color};'>{fa['status']}</span>"
                f"</div>"
                # Basic stats row (manual / reference)
                f"{stats_html}"
                # Live advanced analytics row (nba_api)
                f"{anl_html}"
                # Predicted destination
                f"<div style='display:flex;align-items:center;gap:0.6rem;margin-bottom:0.2rem;'>"
                f"<span style='font-size:0.6rem;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;"
                f"color:var(--text-muted);'>Predicted destination</span>"
                f"</div>"
                f"<div style='font-size:0.95rem;font-weight:700;color:{cc};margin-bottom:0rem;'>"
                f"{fa['predicted_dest']}</div>"
                f"{bar}"
                f"<div style='display:flex;justify-content:space-between;font-size:0.67rem;"
                f"color:var(--text-muted);margin-bottom:0.55rem;'>"
                f"<span>Confidence: <strong style='color:{cc};'>{fa['confidence']}%</strong></span>"
                f"<span>{fa['contract_projection']}</span>"
                f"</div>"
                # Interest chips
                f"<div style='font-size:0.6rem;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;"
                f"color:var(--text-muted);margin-bottom:0.2rem;'>Also watching</div>"
                f"{interest_html}"
                # Blurb
                f"<div style='font-size:0.76rem;color:var(--text-muted);line-height:1.65;"
                f"margin-top:0.6rem;border-top:1px solid rgba(255,255,255,0.05);padding-top:0.6rem;'>"
                f"{fa['blurb']}</div>"
                f"</div>"
            )

        # ── Load live advanced analytics (nba_api, cached 24h) ───────────────
        with st.spinner("Loading live NBA advanced analytics…"):
            _fa_analytics = _fetch_nba_fa_analytics("2025-26")

        # ── Tier grouping ──────────────────────────────────────────────────────
        _tier_order = ["Supermax", "Max", "Near-Max", "Starter", "Starter / MLE",
                       "MLE", "MLE / Veteran", "Veteran Min", "Veteran minimum"]
        _tier_groups: dict = {}
        for _fa in _NBA_FREE_AGENTS:
            _tier_groups.setdefault(_fa["tier"], []).append(_fa)

        _fa_col1, _fa_col2 = st.columns(2)
        _fa_col_idx = 0
        for _tier_name in _tier_order:
            _group = _tier_groups.get(_tier_name, [])
            if not _group:
                continue
            _tc = _tier_color(_tier_name)
            _tier_header = (
                f"<div style='font-size:0.58rem;font-weight:700;letter-spacing:0.18em;"
                f"text-transform:uppercase;color:{_tc};margin:1.1rem 0 0.5rem;"
                f"padding-bottom:0.3rem;border-bottom:1px solid rgba(255,255,255,0.06);'>"
                f"{_tier_name} Free Agents</div>"
            )
            # Alternate header between columns
            if _fa_col_idx % 2 == 0:
                _fa_col1.markdown(_tier_header, unsafe_allow_html=True)
                _fa_col2.markdown(_tier_header, unsafe_allow_html=True)
            for _fi, _fa in enumerate(_group):
                _target_col = _fa_col1 if _fi % 2 == 0 else _fa_col2
                _target_col.markdown(
                    _render_fa_card(
                        _fa,
                        _fa_analytics.get(_fa["name"]) or _NBA_FA_ADVANCED_STATS.get(_fa["name"], {}),
                    ),
                    unsafe_allow_html=True,
                )
            _fa_col_idx += 1

        # ── News panel below cards ─────────────────────────────────────────────
        st.markdown("<hr style='margin:1.5rem 0;border-color:rgba(255,255,255,0.06);'>", unsafe_allow_html=True)
        _fa_news_col, _fa_feat_col = st.columns([1.2, 1])
        with _fa_news_col:
            section("NBA Free Agency News")
            with st.spinner("Loading news..."):
                _nba_news = get_sport_news("nba")
            render_news_panel(_nba_news)
        with _fa_feat_col:
            section("Platform Features")
            _fc1, _fc2 = st.columns(2)
            _features = [
                (_fc1, "📊", "Player Stats", "Hit rates, rolling averages, next-opponent predictions."),
                (_fc2, "📈", "Opponent Breakdown", "Performance split by every opponent faced."),
                (_fc1, "🎯", "Bet Simulation", "Simulate flat-unit P&L across a season."),
                (_fc2, "🕒", "First Basket", "Tip-off win rates and first basket frequency."),
                (_fc1, "🟣", "PrizePicks", "Today's live NBA prop lines from PrizePicks."),
            ]
            for _col, _icon, _title, _desc in _features:
                with _col:
                    st.markdown(
                        f"<div class='feature-card'>"
                        f"<div class='feature-card-icon'>{_icon}</div>"
                        f"<p class='feature-card-title'>{_title}</p>"
                        f"<p class='feature-card-desc'>{_desc}</p>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

    # ── PLAYER STATS ──────────────────────────────────────────────────────────
    with tab_stats:
        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            section("Select Player")
            team_names = sorted([t["full_name"] for t in teams.get_teams()])
            selected_team = st.selectbox("Team", team_names, key="ps_team")
            team_code = get_team_abbreviation(selected_team)
            player_list = get_team_players(team_code)
            _ps_hint = st.session_state.pop("_ps_player_hint", None)
            if _ps_hint and _ps_hint in player_list:
                st.session_state["ps_player"] = _ps_hint
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
            if not player_name or not seasons:
                st.info("Select a player and season from the sidebar to view stats.")
            elif player_name and seasons:
                player_id = get_player_id(player_name)
                if not player_id:
                    st.warning(f"Could not find player ID for **{player_name}**. Try searching by full name.")
                else:
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

                        pp_df = get_prizepicks_with_team()
                        pp_stat = PP_STAT_MAP.get(prop_type)
                        ud_df_nba = get_underdog_props("nba")
                        ud_stat_nba = _UD_NBA_PROP_LOOKUP.get(prop_type)

                        # ── Market Lines ──────────────────────────────────
                        section("Market Lines")
                        _pp_line_val = None
                        if not pp_df.empty and pp_stat:
                            _pp_match = pp_df[
                                (pp_df["player_name"].str.lower() == player_name.lower()) &
                                (pp_df["stat_type"] == pp_stat)
                            ]
                            if not _pp_match.empty:
                                _pp_line_val = _pp_match.iloc[0]["line_score"]
                                df["PP_HIT"] = df["TARGET"] > _pp_line_val
                        _ud_line_val = _ud_odds_val = _ud_implied_val = None
                        if not ud_df_nba.empty and ud_stat_nba:
                            _ud_match = ud_df_nba[
                                (ud_df_nba["player_name"].str.lower() == player_name.lower()) &
                                (ud_df_nba["stat_type"] == ud_stat_nba)
                            ]
                            if not _ud_match.empty:
                                _ud_row = _ud_match.iloc[0]
                                _ud_line_val   = float(_ud_row["line_score"])
                                _ud_odds_val   = int(_ud_row["american_odds"])
                                _ud_implied_val = float(_ud_row["implied_prob"])
                                df["UD_HIT"] = df["TARGET"] > _ud_line_val
                        def _fmt_odds(o): return f"+{o}" if o is not None and o > 0 else (str(o) if o is not None else "—")
                        def _fmt_hr(col): return f"{df[col].mean():.1%}" if col in df.columns else "—"

                        _ud_odds_disp = _fmt_odds(_ud_odds_val)
                        _bcs = st.columns(6)
                        _bcs[0].metric("PrizePicks Line", _pp_line_val if _pp_line_val is not None else "—")
                        _bcs[1].metric("Hit Rate vs PP",  f"{df['PP_HIT'].mean():.1%}" if _pp_line_val is not None else "—")
                        _bcs[2].metric("Underdog Line",   _ud_line_val if _ud_line_val is not None else "—")
                        _bcs[3].metric("UD Odds",         _ud_odds_disp)
                        _bcs[4].metric("UD Implied",      f"{_ud_implied_val:.0%}" if _ud_implied_val is not None else "—")
                        _bcs[5].metric("Hit Rate vs UD",  _fmt_hr("UD_HIT") if _ud_line_val is not None else "—")

                        # ── Model Prediction ────────────────────────────────
                        _nba_tab_cal_f = _load_calibration("NBA").get(prop_type, 1.0)
                        _nba_tab_imp   = _ud_implied_val if _ud_implied_val is not None else -1.0
                        _model_rate, _ = _nba_hit_rate(
                            player_name, prop_type, line_value,
                            odds_type="standard", implied_override=_nba_tab_imp,
                            cal_factor=_nba_tab_cal_f,
                        )
                        _raw_rate = df["HIT"].mean()
                        _edge = _model_rate - _ud_implied_val if _ud_implied_val is not None else None
                        _bcs2 = st.columns(4)
                        _bcs2[0].metric("Model Hit Rate",      f"{_model_rate:.1%}", help="Calibrated blend: 70% historical + 30% market implied")
                        _bcs2[1].metric("Raw Historical",      f"{_raw_rate:.1%}",   help="Simple binary hit rate from game log")
                        _bcs2[2].metric("Calibration Factor",  f"{_nba_tab_cal_f:.3f}", help="Accuracy adjustment for this stat type based on resolved parlays")
                        _bcs2[3].metric("Edge vs Market",      f"{_edge:+.1%}" if _edge is not None else "—", help="Model rate minus Underdog implied. Positive = model sees value.")

                        # ── Scout Report ──────────────────────────────────
                        df["IS_HOME"] = df["MATCHUP"].str.contains(r"vs\.", na=False)
                        _nxt = get_next_opponent(team_code)
                        _report = nba_scout_report(player_name, team_code, df, _nxt, prop_type, rolling_window,
                                                   line=line_value, ud_line=_ud_line_val,
                                                   ud_odds=_ud_odds_val, ud_implied=_ud_implied_val)
                        if _report:
                            section("Scout Report")
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
                                      color_discrete_map={"TARGET": "#818cf8", "ROLLING_AVG": "#a78bfa"})
                        fig.add_hline(y=line_value, line_dash="dot", line_color="#3a4055",
                                      annotation_text=f"Line {line_value}", annotation_font_color="#5c6272")
                        st.plotly_chart(nba_fig(fig), width="stretch", config=_CHART_CFG)

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
                                     color_continuous_scale=VIZ_SEQUENTIAL,
                                     text=opp_stats["Games"].astype(str).values + " G")
                        fig.add_vline(x=0.5, line_dash="dot", line_color="#3a4055")
                        fig.update_coloraxes(showscale=False)
                        st.plotly_chart(nba_fig(fig), width="stretch", config=_CHART_CFG)
                        section("Data Table")
                        st.dataframe(opp_stats.style.format(
                            {"Hit Rate": "{:.1%}", "Avg Margin": "{:.2f}", "Avg Stat": "{:.1f}", "Games": "{:.0f}"}
                        ), width="stretch")

    # ── VS. OPPONENT ──────────────────────────────────────────────────────────
    with tab_vs_opp_nba:
        with st.spinner("Loading today's games..."):
            _nba_today_sched = get_nba_scoreboard(_scoreboard_date())

        if not _nba_today_sched:
            st.info("No NBA games scheduled for today.")
        else:
            nvo_ctrl, nvo_main = st.columns([1, 2.8])
            with nvo_ctrl:
                section("Today's Games")
                nvo_seasons = st.multiselect("Seasons", SEASONS, default=["2025-26"], key="nvo_seasons")
                game_opts = [f"{g['away']} @ {g['home']}" for g in _nba_today_sched]
                nvo_game_label = st.selectbox("Select Game", game_opts, key="nvo_game")
                nvo_game = next(g for g in _nba_today_sched if f"{g['away']} @ {g['home']}" == nvo_game_label)
                nvo_team_code = st.radio("Analyze Team",
                    [nvo_game["away"], nvo_game["home"]], key="nvo_team_side", horizontal=True)
                nvo_opp_code = nvo_game["home"] if nvo_team_code == nvo_game["away"] else nvo_game["away"]
                # Resolve ESPN abbreviations (e.g. "NY"→"NYK", "SA"→"SAS") for nba_api calls
                nvo_team_code = _resolve_nba_abbr(nvo_team_code)
                nvo_opp_code = _resolve_nba_abbr(nvo_opp_code)

                section("Player")
                nvo_players = get_team_players(nvo_team_code)
                if not nvo_players:
                    st.warning(f"Could not load roster for {nvo_team_code}.")
                else:
                    nvo_player = st.selectbox("Player", nvo_players, key="nvo_player")
                    nba_player_card(nvo_player, nvo_team_code)
                    section("Parameters")
                    nvo_prop = st.selectbox("Stat Type", list(STAT_MAP.keys()), key="nvo_prop")
                    nvo_line = st.number_input("Prop Line", value=25.5, step=0.5, key="nvo_line")
                    nvo_window = st.slider("Rolling Window", 3, 20, 10, key="nvo_window")

            with nvo_main:
                if nvo_players and nvo_seasons:
                    nvo_pid = get_player_id(nvo_player)
                    if nvo_pid:
                        with st.spinner("Loading game logs..."):
                            nvo_df = get_gamelogs(nvo_pid, tuple(nvo_seasons))
                        if nvo_df.empty:
                            st.warning("No stats found for this player.")
                        else:
                            nvo_df = nvo_df.copy()
                            nvo_df["PRA"] = nvo_df["PTS"] + nvo_df["REB"] + nvo_df["AST"]
                            nvo_col = STAT_MAP[nvo_prop]
                            nvo_df["ROLLING"] = nvo_df[nvo_col].rolling(nvo_window).mean()
                            nvo_df["IS_HOME"] = nvo_df["MATCHUP"].str.contains(r"vs\.", na=False)
                            opp_mask = nvo_df["OPPONENT"].str.upper() == nvo_opp_code.upper()
                            vs_opp_df = nvo_df[opp_mask]
                            proj_all = rolling_projection(nvo_df, nvo_col, nvo_window)
                            hit_all = (nvo_df[nvo_col] > nvo_line).mean()

                            # ── Scout Report ──────────────────────────────────
                            _nvo_report = nba_scout_report(
                                nvo_player, nvo_team_code, nvo_df, nvo_opp_code,
                                nvo_prop, nvo_window, line=nvo_line,
                            )
                            if _nvo_report:
                                section("Scout Report")
                                st.markdown(_scout_card_nba(_nvo_report), unsafe_allow_html=True)

                            section(f"Projection vs {nvo_opp_code} — Today")
                            c1, c2, c3, c4 = st.columns(4)
                            c1.metric("Season Rolling Avg", f"{proj_all:.1f}")
                            c2.metric("Season Hit Rate", f"{hit_all:.1%}")
                            if not vs_opp_df.empty:
                                avg_vs = vs_opp_df[nvo_col].mean()
                                hit_vs = (vs_opp_df[nvo_col] > nvo_line).mean()
                                c3.metric(f"Avg vs {nvo_opp_code}",
                                          f"{avg_vs:.1f}",
                                          delta=f"{avg_vs - proj_all:+.1f} vs season",
                                          delta_color="normal")
                                c4.metric(f"Hit Rate vs {nvo_opp_code}",
                                          f"{hit_vs:.1%}",
                                          delta=f"{hit_vs - hit_all:+.1%} vs season",
                                          delta_color="normal")
                            else:
                                c3.metric(f"vs {nvo_opp_code}", "—", help="No historical matchup data")
                                c4.metric("Games vs opp", "0")

                            section(f"{nvo_prop} Trend — {nvo_opp_code} Games Highlighted")
                            fig_nvo = px.line(nvo_df.reset_index(), y=nvo_col,
                                              labels={nvo_col: nvo_prop, "index": "Game"},
                                              color_discrete_sequence=["#3a4055"])
                            fig_nvo.add_scatter(x=nvo_df.reset_index().index,
                                                y=nvo_df["ROLLING"],
                                                mode="lines", name="Rolling Avg",
                                                line=dict(color="#818cf8", width=2))
                            if opp_mask.any():
                                opp_idx = nvo_df.reset_index().index[opp_mask.values]
                                fig_nvo.add_scatter(x=opp_idx,
                                                    y=vs_opp_df[nvo_col].values,
                                                    mode="markers",
                                                    name=f"vs {nvo_opp_code}",
                                                    marker=dict(color="#a78bfa", size=10, symbol="diamond"))
                            fig_nvo.add_hline(y=nvo_line, line_dash="dot", line_color="#3a4055",
                                              annotation_text=f"Line {nvo_line}",
                                              annotation_font_color="#5c6272")
                            st.plotly_chart(nba_fig(fig_nvo), width="stretch", config=_CHART_CFG)

                            if not vs_opp_df.empty:
                                section(f"Game Log vs {nvo_opp_code}")
                                st.dataframe(
                                    vs_opp_df[["GAME_DATE", "MATCHUP", nvo_col]].rename(columns={
                                        "GAME_DATE": "Date", "MATCHUP": "Matchup", nvo_col: nvo_prop
                                    }),
                                    width="stretch", hide_index=True,
                                )

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
                                      color_discrete_sequence=[VIZ_SERIES[0]])
                        fig.add_hline(y=0, line_dash="dot", line_color="#3a4055")
                        st.plotly_chart(nba_fig(fig), width="stretch", config=_CHART_CFG)

    # ── FIRST BASKET ──────────────────────────────────────────────────────────
    with tab_fb:
        ctrl_col, main_col = st.columns([1, 2.8])
        with ctrl_col:
            section("Select Team & Player")
            team_names_list = sorted([t["full_name"] for t in teams.get_teams()])
            selected_team_full = st.selectbox("Team", team_names_list, key="fb_team")
            team_code = get_team_abbreviation(selected_team_full)
            player_list = get_team_players(team_code)
            selected_player = st.selectbox("Player", ["(All Players)"] + player_list, key="fb_player")
            if selected_player and selected_player != "(All Players)":
                nba_player_card(selected_player, team_code)
            st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
            num_games = st.slider("Games to analyze", min_value=10, max_value=30, value=20, step=5, key="fb_ngames")
            _fb_load = st.button("Load First Basket Data", type="primary", key="fb_load_btn")
            if _fb_load:
                get_team_first_basket_history.clear()
                st.session_state["fb_loaded"] = team_code

        with main_col:
            _fb_ready = st.session_state.get("fb_loaded") is not None
            fb_df = pd.DataFrame()
            if not _fb_ready:
                st.info("Select a team and click **Load First Basket Data** to pull play-by-play history.")
            else:
                with st.spinner(f"Pulling last {num_games} games for {team_code}…"):
                    fb_df = get_team_first_basket_history(team_code, num_games)
                if fb_df.empty:
                    st.warning("Could not load play-by-play data. Try refreshing or selecting another team.")

            if _fb_ready and not fb_df.empty:
                total = len(fb_df)
                # fillna(False) so None tip/scorer values don't produce NaN sums
                tip_wins = int(fb_df["Tip Won"].fillna(False).sum()) if "Tip Won" in fb_df.columns else 0
                team_fb_count = int(fb_df["Team Scored First"].fillna(False).sum()) if "Team Scored First" in fb_df.columns else 0
                tip_pct = tip_wins / total if total else 0
                fb_pct = team_fb_count / total if total else 0

                # Top scorer of first baskets
                scorer_counts = fb_df[fb_df["First Scorer"] != "—"]["First Scorer"].value_counts()
                top_scorer = scorer_counts.index[0] if not scorer_counts.empty else "—"

                section(f"{team_code} — Last {total} Games")
                c1, c2, c3 = st.columns(3)
                c1.metric("Tip-Off Win %", f"{tip_pct:.1%}")
                c2.metric("Team First Basket %", f"{fb_pct:.1%}")
                c3.metric("Top First Scorer", top_scorer)

                # ── First Basket Scorers bar chart ──
                section("First Basket Scorers — Frequency")
                if not scorer_counts.empty:
                    df_scorers = scorer_counts.reset_index()
                    df_scorers.columns = ["Player", "First Baskets"]
                    fig_bar = px.bar(
                        df_scorers.head(12), x="First Baskets", y="Player",
                        orientation="h",
                        color="First Baskets",
                        color_continuous_scale=VIZ_SEQUENTIAL,
                        text="First Baskets",
                    )
                    fig_bar.update_traces(textposition="outside")
                    fig_bar.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False)
                    st.plotly_chart(nba_fig(fig_bar), width="stretch", config=_CHART_CFG)

                # ── Shot type + Tip-off charts (independent columns) ──
                shot_counts = fb_df[fb_df["Shot Type"] != "—"]["Shot Type"].value_counts().reset_index()
                shot_counts.columns = ["Shot Type", "Count"]
                left_c, right_c = st.columns(2)
                with left_c:
                    section("First Basket Shot Type")
                    if not shot_counts.empty:
                        fig_pie = px.pie(
                            shot_counts, names="Shot Type", values="Count",
                            color_discrete_sequence=["#818cf8", "#a5b4fc", "#4f46e5"],
                            hole=0.55,
                        )
                        fig_pie.update_traces(textfont_size=11)
                        st.plotly_chart(nba_fig(fig_pie), width="stretch", config=_CHART_CFG)
                    else:
                        st.info("No shot type data available.")
                with right_c:
                    section("Tip-Off Outcomes")
                    tip_data = pd.DataFrame({
                        "Result": ["Won Tip", "Lost Tip"],
                        "Count": [tip_wins, total - tip_wins],
                    })
                    fig_tip = px.bar(
                        tip_data, x="Result", y="Count",
                        color="Result",
                        color_discrete_map={"Won Tip": "#818cf8", "Lost Tip": "#3a4055"},
                        text="Count",
                    )
                    fig_tip.update_traces(textposition="outside")
                    fig_tip.update_layout(showlegend=False)
                    st.plotly_chart(nba_fig(fig_tip), width="stretch", config=_CHART_CFG)

                # ── Player spotlight ──
                if selected_player and selected_player != "(All Players)":
                    player_games = fb_df[fb_df["First Scorer"] == selected_player]
                    section(f"Player Spotlight — {selected_player}")
                    if player_games.empty:
                        st.info(f"{selected_player} has not scored the first basket in the last {total} games.")
                    else:
                        p_count = len(player_games)
                        p_pct = p_count / total
                        p1, p2 = st.columns(2)
                        p1.metric("First Basket Games", p_count)
                        p2.metric("First Basket Rate", f"{p_pct:.1%}")
                        p_shots = player_games["Shot Type"].value_counts().reset_index()
                        p_shots.columns = ["Shot Type", "Count"]
                        fig_p = px.bar(
                            p_shots, x="Shot Type", y="Count",
                            color="Shot Type",
                            color_discrete_sequence=["#818cf8", "#a5b4fc", "#4f46e5"],
                            text="Count",
                        )
                        fig_p.update_traces(textposition="outside")
                        fig_p.update_layout(showlegend=False)
                        st.plotly_chart(nba_fig(fig_p), width="stretch", config=_CHART_CFG)

                # ── Prediction: Tonight's First Basket ───────────────────────
                section("Tonight's First Basket Prediction")
                # Determine tonight's matchup for this team
                _fb_today = get_nba_scoreboard(_scoreboard_date())
                _fb_game = next(
                    (g for g in _fb_today
                     if g["away"].upper() == team_code.upper() or g["home"].upper() == team_code.upper()),
                    None
                )

                # Build per-player first basket frequency from history
                _fb_team_games = fb_df[fb_df["Team Scored First"].fillna(False)]
                _all_scorers = fb_df[fb_df["First Scorer"] != "—"]
                _team_scorers = _all_scorers[
                    _all_scorers.apply(
                        lambda r: any(
                            pl.lower() in r["First Scorer"].lower()
                            for pl in (get_team_players(team_code) or [])
                        ),
                        axis=1,
                    )
                ] if get_team_players(team_code) else _fb_team_games[_fb_team_games["First Scorer"] != "—"]

                # Extract opponent from Matchup column (e.g. "ATL vs. NY" → "NY")
                def _fb_opp(matchup, team):
                    for part in matchup.replace("vs.", "@").split("@"):
                        p = part.strip()
                        if p.upper() != team.upper() and p:
                            return p.upper()
                    return ""
                fb_df["_opp"] = fb_df["Matchup"].apply(lambda m: _fb_opp(m, team_code))

                # Compute per-player stats including opponent-specific rate
                _fb_player_stats = []
                for _fbp, _fbp_df in _all_scorers.groupby("First Scorer"):
                    _fbp_total = len(_fbp_df)
                    _fbp_rate = _fbp_total / len(fb_df)
                    _shot_breakdown = _fbp_df["Shot Type"].value_counts().to_dict()
                    _top_shot = max(_shot_breakdown, key=_shot_breakdown.get) if _shot_breakdown else "—"
                    # Home/away split
                    _home_games = fb_df[fb_df["Matchup"].str.contains(r"vs\.", na=False)]
                    _away_games = fb_df[~fb_df["Matchup"].str.contains(r"vs\.", na=False)]
                    _fbp_home = len(_fbp_df[_fbp_df.index.isin(_home_games.index)]) / max(len(_home_games), 1)
                    _fbp_away = len(_fbp_df[_fbp_df.index.isin(_away_games.index)]) / max(len(_away_games), 1)
                    # Per-opponent rate (games vs this specific opponent)
                    _opp_rates: dict = {}
                    for _opp_code, _opp_group in fb_df.groupby("_opp"):
                        _opp_scorer_games = len(_fbp_df[_fbp_df.index.isin(_opp_group.index)])
                        _opp_rates[_opp_code] = _opp_scorer_games / max(len(_opp_group), 1)
                    _fb_player_stats.append({
                        "player": _fbp, "games": _fbp_total, "rate": _fbp_rate,
                        "top_shot": _top_shot, "home_rate": _fbp_home, "away_rate": _fbp_away,
                        "opp_rates": _opp_rates,
                    })

                _fb_player_stats.sort(key=lambda x: -x["rate"])

                if _fb_game:
                    _is_home = _fb_game["home"].upper() == team_code.upper()
                    _opp = (_fb_game["away"] if _is_home else _fb_game["home"]).upper()
                    _loc = "Home" if _is_home else "Away"
                    st.markdown(
                        f"<p style='font-size:0.78rem;color:#818cf8;font-weight:600;margin:0 0 0.75rem;'>"
                        f"Tonight: {team_code} vs {_opp} ({_loc})</p>",
                        unsafe_allow_html=True,
                    )
                    # Blend: 50% opponent-specific (if available) + 50% location-adjusted
                    for _ps in _fb_player_stats:
                        _loc_rate = _ps["home_rate"] if _is_home else _ps["away_rate"]
                        _opp_rate = _ps["opp_rates"].get(_opp)
                        if _opp_rate is not None:
                            # Weighted blend: opponent data available
                            _ps["tonight_rate"] = 0.5 * _opp_rate + 0.5 * _loc_rate
                            _ps["opp_label"] = f"vs {_opp}: {_opp_rate:.0%}"
                        else:
                            # No opponent history — use location rate only
                            _ps["tonight_rate"] = _loc_rate or _ps["rate"]
                            _ps["opp_label"] = f"No prior data vs {_opp}"
                else:
                    st.caption(f"No {team_code} game found on today's schedule — showing season-average rates.")
                    for _ps in _fb_player_stats:
                        _ps["tonight_rate"] = _ps["rate"]
                        _ps["opp_label"] = ""

                # Display top candidates
                _top_candidates = [p for p in _fb_player_stats if p["games"] >= 1][:8]
                if _top_candidates:
                    _pred_cols = st.columns(min(4, len(_top_candidates)))
                    for _ci, _ps in enumerate(_top_candidates[:4]):
                        with _pred_cols[_ci]:
                            _pct = _ps["tonight_rate"]
                            _bar_w = int(_pct * 100)
                            st.markdown(
                                f"<div style='background:#191c23;border:1px solid #252a35;border-radius:10px;"
                                f"padding:0.75rem 1rem;text-align:center;'>"
                                f"<p style='font-size:0.7rem;font-weight:700;color:#dfe1ea;margin:0 0 0.25rem;'>{_ps['player']}</p>"
                                f"<p style='font-size:1.5rem;font-weight:800;color:#818cf8;margin:0;'>{_pct:.0%}</p>"
                                f"<p style='font-size:0.58rem;color:#5c6272;margin:0.15rem 0 0.4rem;letter-spacing:0.08em;text-transform:uppercase;'>First Basket Rate</p>"
                                f"<div style='background:#252a35;border-radius:4px;height:4px;'>"
                                f"<div style='background:#818cf8;width:{_bar_w}%;height:4px;border-radius:4px;'></div></div>"
                                f"<p style='font-size:0.6rem;color:#5c6272;margin:0.4rem 0 0;'>{_ps['top_shot']} · {_ps.get('opp_label', str(_ps['games'])+' game(s)')}</p>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                    if len(_top_candidates) > 4:
                        _pred_cols2 = st.columns(min(4, len(_top_candidates) - 4))
                        for _ci, _ps in enumerate(_top_candidates[4:]):
                            with _pred_cols2[_ci]:
                                _pct = _ps["tonight_rate"]
                                _bar_w = int(_pct * 100)
                                st.markdown(
                                    f"<div style='background:#191c23;border:1px solid #252a35;border-radius:10px;"
                                    f"padding:0.75rem 1rem;text-align:center;'>"
                                    f"<p style='font-size:0.7rem;font-weight:700;color:#dfe1ea;margin:0 0 0.25rem;'>{_ps['player']}</p>"
                                    f"<p style='font-size:1.5rem;font-weight:800;color:#818cf8;margin:0;'>{_pct:.0%}</p>"
                                    f"<p style='font-size:0.58rem;color:#5c6272;margin:0.15rem 0 0.4rem;letter-spacing:0.08em;text-transform:uppercase;'>First Basket Rate</p>"
                                    f"<div style='background:#252a35;border-radius:4px;height:4px;'>"
                                    f"<div style='background:#818cf8;width:{_bar_w}%;height:4px;border-radius:4px;'></div></div>"
                                    f"<p style='font-size:0.6rem;color:#5c6272;margin:0.4rem 0 0;'>{_ps['top_shot']} · {_ps.get('opp_label', str(_ps['games'])+' game(s)')}</p>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )
                    # Scout-style insight
                    _best = _top_candidates[0]
                    _opp_hist = _best.get("opp_label", "")
                    _insight = (
                        f"{_best['player']} leads all first basket scorers with a "
                        f"{_best['rate']:.0%} overall rate ({_best['games']} game(s)), "
                        f"typically via a {_best['top_shot'].lower()}. "
                    )
                    if _fb_game:
                        _loc_rate = _best["tonight_rate"]
                        _opp_rate_val = _best["opp_rates"].get(_opp)
                        if _opp_rate_val is not None:
                            _insight += (
                                f"Against {_opp} specifically they score the first basket "
                                f"{_opp_rate_val:.0%} of the time — "
                                f"{'above' if _opp_rate_val > _best['rate'] else 'below'} their season average. "
                            )
                        _insight += (
                            f"{'At home' if _is_home else 'On the road'}, "
                            f"the blended prediction lands at {_loc_rate:.0%} — "
                            f"the top first-basket target tonight."
                        )
                    st.markdown(
                        f"<div style='background:#191c23;border:1px solid #252a35;border-left:3px solid #818cf8;"
                        f"border-radius:10px;padding:1rem 1.25rem;margin-top:0.75rem;'>"
                        f"<p style='font-size:0.58rem;letter-spacing:0.18em;text-transform:uppercase;"
                        f"color:#818cf8;margin:0 0 0.4rem;font-weight:700;'>Model Insight</p>"
                        f"<p style='font-size:0.84rem;color:#c8cad4;line-height:1.75;margin:0;'>{_insight}</p></div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.info("No first basket data available to build predictions.")

                # ── Game log table ──
                section("Game Log")
                display_df = fb_df[["Game Date", "Matchup", "W/L", "Tip Winner", "First Scorer", "Shot Type"]].copy()
                st.dataframe(display_df, width="stretch", hide_index=True)
                st.caption("Data: NBA Stats API (nba_api) — play-by-play")

    # ── SPORTSBOOK ────────────────────────────────────────────────────────────
    with tab_pp:
        _sb_nba_col1, _sb_nba_col2 = st.columns([4, 1])
        with _sb_nba_col1:
            _sb_nba = st.radio(
                "Sportsbook",
                ["FanDuel", "Underdog", "PrizePicks"],
                horizontal=True,
                key="sb_nba_select",
            )
        with _sb_nba_col2:
            if st.button("🔄 Refresh Lines", key="nba_sb_refresh"):
                if _sb_nba == "PrizePicks":
                    _pp_cache.clear(); _pp_cache_ts.clear()
                    _pp_lite_cache.clear(); _pp_lite_cache_ts.clear()
                elif _sb_nba == "Underdog":
                    _ud_cache.pop("nba", None); _ud_cache_ts.pop("nba", None)
                else:
                    _toa_cache.pop(f"nba_{_sb_nba}", None); _toa_cache_ts.pop(f"nba_{_sb_nba}", None)
                _safe_rerun()
        st.session_state["nba_sportsbook"] = _sb_nba
        with st.spinner(f"Loading {_sb_nba} NBA projections..."):
            pp_df = get_sportsbook_props("nba", _sb_nba)
        _toa_err = _toa_cache.get(f"_err_nba_{_sb_nba}", "")
        _toa_rem = _TOA_CREDITS_REMAINING.get(_get_odds_api_key())
        if _sb_nba in ("FanDuel", "DraftKings", "Bet365") and _toa_rem is not None:
            st.caption(f"Odds API credits remaining this month: **{_toa_rem}**")
        if pp_df.empty:
            if _toa_err == "quota_exceeded":
                st.error(f"**{_sb_nba} API quota exhausted.** The Odds API free tier allows 500 credits/month. Add a new `ODDS_API_KEY` in Secrets to restore access.")
            elif _toa_err == "invalid_key":
                st.error(f"**{_sb_nba} API key is invalid.** Update `ODDS_API_KEY` in Streamlit Secrets.")
            elif _sb_nba in ("FanDuel", "DraftKings", "Bet365") and not _get_odds_api_key():
                st.error(f"**{_sb_nba} requires an Odds API key.** Add `ODDS_API_KEY` to Streamlit Secrets.")
            else:
                st.warning(f"No {_sb_nba} NBA lines available right now. Lines are typically posted on game days.")
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
            filtered = filtered.copy()
            if _sb_nba == "PrizePicks":
                _ot_label = {"goblin": "Goblin", "demon": "Demon", "standard": "Standard"}
                filtered["Odds"] = filtered["odds_type"].map(_ot_label).fillna("Standard")
                filtered["Implied %"] = filtered["odds_type"].map(
                    lambda x: f"{int(_PP_ODDS_IMPLIED.get(x, 0.50)*100)}%"
                )
            else:
                filtered["Odds"] = filtered["american_odds"].apply(
                    lambda x: f"+{x}" if x > 0 else str(x)
                ) if "american_odds" in filtered.columns else "-"
                filtered["Implied %"] = filtered["implied_prob"].apply(
                    lambda x: f"{int(x*100)}%"
                ) if "implied_prob" in filtered.columns else "50%"
            section(f"{len(filtered)} Line(s) — {_sb_nba}")
            show_cols = ["player_name", "team", "stat_type", "line_score", "Odds", "Implied %", "game_label"]
            show_cols = [c for c in show_cols if c in filtered.columns]
            st.dataframe(
                filtered[show_cols].rename(columns={
                    "player_name": "Player", "team": "Team", "stat_type": "Stat",
                    "line_score": "Line", "game_label": "Game",
                }).sort_values("Player"),
                width="stretch", hide_index=True,
            )

    # ── PARLAYS ───────────────────────────────────────────────────────────────
    with tab_parlays:
        section("NBA Parlay Builder")
        st.markdown("""
        <p style='color:var(--text-muted);font-size:0.82rem;line-height:1.6;max-width:680px;margin-bottom:1rem;'>
        Builds optimized PrizePicks parlays using today's NBA lines and historical hit rates
        (last 30 games, 2024-25 &amp; 2025-26 seasons). <strong style='color:var(--text-primary)'>Safe Parlays</strong>
        maximize probability of hitting. <strong style='color:#f59e0b;'>Value Parlays</strong>
        maximize expected value (probability × payout).
        </p>
        <div style='background:rgba(248,113,113,0.07);border:1px solid rgba(248,113,113,0.2);border-radius:10px;padding:0.75rem 1rem;max-width:680px;margin-bottom:1.25rem;'>
            <p style='font-size:0.6rem;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#f87171;margin:0 0 0.3rem 0;'>Disclaimer</p>
            <p style='font-size:0.78rem;color:#9294a8;line-height:1.6;margin:0;'>
                Parlay suggestions are generated from historical statistics and are for
                <strong style='color:#ccc;'>informational and entertainment purposes only.</strong>
                Past performance does not guarantee future results. This is not betting advice.
                Always gamble responsibly and within your means.
            </p>
        </div>""", unsafe_allow_html=True)

        _pc1, _pc2, _pc3 = st.columns([1, 1, 2])
        with _pc1:
            st.markdown("**Minimum Picks**")
            _par_min = st.selectbox("Min Picks", [2, 3], index=0, key="nba_par_min", label_visibility="collapsed")
        with _pc2:
            st.markdown("**Maximum Picks**")
            _par_max = st.selectbox("Max Picks", [2, 3, 4, 5], index=2, key="nba_par_max", label_visibility="collapsed")
        with _pc3:
            st.markdown("**Stat Types to Include**")
            _par_stats = st.multiselect(
                "Stat Types",
                options=list(_PP_NBA_STAT_COL.keys()),
                default=["Pts+Rebs+Asts", "Assists"],
                key="nba_par_stats",
                label_visibility="collapsed",
            )

        if st.button("Build NBA Parlays", type="primary", key="nba_build_parlays"):
            st.session_state["nba_parlays_built"] = True
            st.session_state["nba_par_min_val"] = _par_min
            st.session_state["nba_par_max_val"] = _par_max
            st.session_state["nba_par_stats_val"] = _par_stats

        if st.session_state.get("nba_parlays_built"):
            _b_min = st.session_state.get("nba_par_min_val", _par_min)
            _b_max = st.session_state.get("nba_par_max_val", _par_max)
            _b_stats = st.session_state.get("nba_par_stats_val", _par_stats) or list(_PP_NBA_STAT_COL.keys())

            _nba_sb = st.session_state.get("nba_sportsbook", "Underdog")
            _SB_OPTS = ["FanDuel", "Underdog", "PrizePicks"]
            if _nba_sb not in _SB_OPTS:
                _nba_sb = "Underdog"
            _par_sb_col1, _par_sb_col2 = st.columns([4, 1])
            with _par_sb_col1:
                _sb_choice_nba = st.radio(
                    "Sportsbook for Parlays",
                    _SB_OPTS,
                    horizontal=True,
                    index=_SB_OPTS.index(_nba_sb),
                    key="nba_par_sb",
                )
            with _par_sb_col2:
                if st.button("🔄 Refresh", key="nba_par_refresh"):
                    if _sb_choice_nba == "PrizePicks":
                        _pp_cache.clear(); _pp_cache_ts.clear()
                    elif _sb_choice_nba == "Underdog":
                        _ud_cache.pop("nba", None); _ud_cache_ts.pop("nba", None)
                    elif _sb_choice_nba == "DraftKings":
                        _sharp_cache.pop("nba_draftkings", None); _sharp_cache_ts.pop("nba_draftkings", None)
                    elif _sb_choice_nba == "FanDuel":
                        _sharp_cache.pop("nba_fanduel", None); _sharp_cache_ts.pop("nba_fanduel", None)
                        _toa_cache.pop("nba_FanDuel", None); _toa_cache_ts.pop("nba_FanDuel", None)
                    else:
                        _toa_cache.pop(f"nba_{_sb_choice_nba}", None)
                        _toa_cache_ts.pop(f"nba_{_sb_choice_nba}", None)
                    _safe_rerun()
            with st.spinner(f"Fetching {_sb_choice_nba} NBA lines…"):
                _pp_raw = get_sportsbook_props("nba", _sb_choice_nba)

            _using_fallback_nba = False
            if _pp_raw.empty:
                if _sb_choice_nba in ("DraftKings", "FanDuel") and not _get_sharp_api_key():
                    st.warning(
                        f"**{_sb_choice_nba} requires a SharpAPI key** (free, no credit card). "
                        "Register at [sharpapi.io](https://sharpapi.io) then add "
                        "`SHARP_API_KEY = \"sk_live_xxx\"` to `.streamlit/secrets.toml`."
                    )
                st.info(f"No live {_sb_choice_nba} NBA lines found — building parlays from historical data.")
                _using_fallback_nba = True
            else:
                _pp_filt = _pp_raw[_pp_raw["stat_type"].isin(_b_stats)].copy()
                if _pp_filt.empty:
                    st.info("No active lines for the selected stat types — switching to historical mode.")
                    _using_fallback_nba = True

            _nba_cal = _load_calibration("NBA")

            # Warn for stats with poor or insufficient calibration
            _nba_weak = [s for s in _b_stats if _nba_cal.get(s, 1.0) < 0.3]
            if _nba_weak:
                st.warning(
                    f"**Low-accuracy stat type(s): {', '.join(_nba_weak)}** — "
                    "historical data shows these props almost never hit at the predicted rate. "
                    "Consider removing them from the selection above.",
                    icon="⚠️",
                )

            if _using_fallback_nba:
                with st.spinner("Loading historical data…"):
                    _legs_nba_data = _fallback_nba_legs(_b_stats)
                if _legs_nba_data:
                    st.caption("Using historical averages as prop lines (no live sportsbook data). Lines set at ~88% of rolling average.")
                _legs_nba_data = [l for l in _legs_nba_data if l["hit_rate"] >= 0.35]
                _safe_p, _value_p = _build_parlays(
                    _legs_nba_data, min_legs=_b_min, max_legs=_b_max,
                    sportsbook=_sb_choice_nba,
                    parlay_cal=parlay_tracker.get_parlay_calibration("NBA"),
                )
            else:
                _pp_filt = _pp_filt.sort_values("implied_prob" if "implied_prob" in _pp_filt.columns else "line_score", ascending=False).head(50)
                _legs_nba = []

                # Pre-warm gamelogs in parallel — 3 workers avoids NBA API rate-limiting
                _warm_ids = [(r["player_name"], get_player_id(r["player_name"])) for _, r in _pp_filt.iterrows()]
                _warm_ids = [(n, p) for n, p in _warm_ids if p]
                _warm_prog = st.progress(0, text=f"Loading {len(_warm_ids)} player histories…")
                with ThreadPoolExecutor(max_workers=3) as _wex:
                    _wfutures = {_wex.submit(get_gamelogs, pid, ("2025-26",)): name for name, pid in _warm_ids}
                    _wdone = 0
                    for _wf in as_completed(_wfutures, timeout=120):
                        _wdone += 1
                        _warm_prog.progress(
                            _wdone / max(1, len(_warm_ids)),
                            text=f"Loading histories… ({_wdone}/{len(_warm_ids)})",
                        )
                _warm_prog.empty()

                _prog = st.progress(0, text="Calculating hit rates…")
                for _i, (_idx, _row) in enumerate(_pp_filt.iterrows()):
                    _ot = _row.get("odds_type", "standard") or "standard"
                    _imp = float(_row.get("implied_prob", -1.0) if "implied_prob" in _row.index else -1.0)
                    _cal_f = _nba_cal.get(_row["stat_type"], 1.0)
                    _rate, _n = _nba_hit_rate(
                        _row["player_name"], _row["stat_type"],
                        float(_row["line_score"] or 0),
                        odds_type=_ot, implied_override=_imp,
                        cal_factor=_cal_f,
                    )
                    # When player history is unavailable, fall back to implied odds
                    if _n == 0:
                        _eff_imp = _imp if _imp >= 0 else _PP_ODDS_IMPLIED.get(_ot, 0.50)
                        _rate = round(min(0.97, max(0.03, _eff_imp)), 3)
                        _n = 1
                    _legs_nba.append({
                        "player_name": _row["player_name"],
                        "team":        _row.get("team", ""),
                        "stat_type":   _row["stat_type"],
                        "line_score":  _row["line_score"],
                        "odds_type":   _ot,
                        "american_odds": int(_row.get("american_odds", -110) if "american_odds" in _row.index else -110),
                        "implied_prob": _imp if _imp >= 0 else _PP_ODDS_IMPLIED.get(_ot, 0.50),
                        "sportsbook":  str(_row.get("sportsbook", "PrizePicks") if "sportsbook" in _row.index else _sb_choice_nba),
                        "game_id":     _row.get("game_id", ""),
                        "game_label":  _row.get("game_label", ""),
                        "hit_rate":    _rate,
                        "sample_n":    _n,
                    })
                    _prog.progress((_i + 1) / len(_pp_filt),
                                   text=f"Analyzing {_row['player_name']}…")
                _prog.empty()

                _legs_nba_data = [l for l in _legs_nba if l["sample_n"] >= 1 and l["hit_rate"] >= 0.35]
                _safe_p, _value_p = _build_parlays(
                    _legs_nba_data, min_legs=_b_min, max_legs=_b_max,
                    sportsbook=_sb_choice_nba,
                    parlay_cal=parlay_tracker.get_parlay_calibration("NBA"),
                )

            # Log generated parlays for accuracy tracking
            try:
                _logged = parlay_tracker.log_parlays(_safe_p, "NBA", _sb_choice_nba, kind="safe")
                _logged += parlay_tracker.log_parlays(_value_p, "NBA", _sb_choice_nba, kind="value")
                if _logged:
                    st.caption(f"Logged {_logged} new parlay(s) to accuracy tracker.")
            except Exception:
                pass

            # --- DISPLAY (always runs) ---
            st.markdown(
                f"<p style='font-size:0.72rem;color:var(--text-muted);margin:0.4rem 0 1.1rem;'>"
                f"Analyzed <strong>{len(_legs_nba_data)}</strong> legs with data &nbsp;·&nbsp; "
                f"<strong>{len(_safe_p)}</strong> safe parlays &nbsp;·&nbsp; "
                f"<strong>{len(_value_p)}</strong> value parlays</p>",
                unsafe_allow_html=True,
            )

            _render_parlay_pair(_safe_p, _value_p, key="nba", sport="NBA")

            # ── Same-Game Parlays ─────────────────────────────────────────────
            st.markdown("<hr style='margin:1.5rem 0;border-color:rgba(255,255,255,0.07);'>",
                        unsafe_allow_html=True)
            st.markdown("<p class='pl-section-label'>Same-Game Parlays — Best Picks Per Game</p>",
                        unsafe_allow_html=True)
            _sgp_results = _build_sgp(_legs_nba_data, min_legs=5, max_legs=5)
            if _sgp_results:
                for _sgp in _sgp_results:
                    st.markdown(
                        f"<p style='font-size:0.78rem;font-weight:600;color:#818cf8;"
                        f"margin:1rem 0 0.4rem;letter-spacing:0.06em;'>"
                        f"{_sgp['game_label']}</p>",
                        unsafe_allow_html=True,
                    )
                    for _sp in _sgp["parlays"]:
                        st.markdown(_parlay_card_html(_sp, "safe"), unsafe_allow_html=True)
            else:
                st.caption("Same-game parlay data requires game grouping from live sportsbook. "
                           "No games with enough legs were found today.")
        else:
            st.info("Select your options above and click **Build NBA Parlays** to generate suggestions. "
                    "First build may take 1-2 minutes while historical data is fetched; subsequent builds are instant.")

    # ── DAILY BLOG ────────────────────────────────────────────────────────────
    with tab_blog:
        with st.spinner("Generating today's NBA brief..."):
            blog_html = generate_nba_blog()
        st.markdown(blog_html, unsafe_allow_html=True)

    # ── NBA ACCURACY ──────────────────────────────────────────────────────────
    if _IS_LOCAL and tab_accuracy_nba is not None:
        with tab_accuracy_nba:
            _render_accuracy_tab("NBA")

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
# ══════════════  WNBA  ═══════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
elif sport == "🏀 WNBA":

    with st.spinner(""):
        _wnba_scores = get_wnba_scoreboard(_wnba_scoreboard_date())
    render_score_ticker(_wnba_scores, "WNBA")

    # ── Ticker click: set game + flag, then rerun to let home tab fire nav JS ──
    if (st.query_params.get("ticker_sport") == "WNBA"
            and st.query_params.get("ticker_game")):
        st.session_state["wvo_game"] = st.query_params["ticker_game"]
        st.session_state["_nav_vs_wnba"] = True
        del st.query_params["ticker_game"]
        del st.query_params["ticker_sport"]
        st.rerun()

    _wnba_tabs_iter = _grouped_tabs([
        ("Home",    []),
        ("Analyze", ["Player Stats", "Opponent Breakdown", "vs. Opponent"]),
        ("Bet",     ["Bet Simulation", "Sportsbook", "Parlays"]),
        *( [("Track", ["Accuracy"])] if _IS_LOCAL else [] ),
        ("About",   ["Daily Blog", "Disclaimer"]),
    ])
    tab_w_home     = next(_wnba_tabs_iter)
    tab_w_stats    = next(_wnba_tabs_iter)
    tab_w_opp      = next(_wnba_tabs_iter)
    tab_w_vs       = next(_wnba_tabs_iter)
    tab_w_sim      = next(_wnba_tabs_iter)
    tab_w_pp       = next(_wnba_tabs_iter)
    tab_w_parlays  = next(_wnba_tabs_iter)
    tab_w_accuracy = next(_wnba_tabs_iter) if _IS_LOCAL else None
    tab_w_blog     = next(_wnba_tabs_iter)
    tab_w_disc     = next(_wnba_tabs_iter)

    # ── HOME ──────────────────────────────────────────────────────────────────
    with tab_w_home:
        if st.session_state.pop("_nav_vs_wnba", False):
            components.html(
                "<script>setTimeout(function(){"
                "var t=window.parent.document.querySelectorAll('[role=\"tab\"]');"
                "if(t[3])t[3].click();"
                "},300);</script>",
                height=0,
            )
        section("Players to Watch")
        with st.spinner(""):
            _wptw_frames = []
            _wud = get_underdog_props("wnba")
            if not _wud.empty:
                _wud2 = _wud.copy(); _wud2["_source"] = "Underdog"
                _wptw_frames.append(_wud2)
            _wpp = get_prizepicks_with_team(league_id=6)
            if not _wpp.empty:
                _wpp2 = _wpp.copy(); _wpp2["_source"] = "PrizePicks"
                if "implied_prob" not in _wpp2.columns:
                    _wpp2["implied_prob"] = 0.5
                _wptw_frames.append(_wpp2)
        if _wptw_frames:
            _wptw_all = pd.concat(_wptw_frames, ignore_index=True)
            _wptw_stats = ["Points", "Rebounds", "Assists", "Pts+Rebs+Asts", "Steals", "Blocks", "3-PT Made"]
            _wptw_all = _wptw_all[_wptw_all["stat_type"].isin(_wptw_stats)].copy()
            if "implied_prob" not in _wptw_all.columns:
                _wptw_all["implied_prob"] = 0.5
            _wptw_all["implied_prob"] = pd.to_numeric(_wptw_all["implied_prob"], errors="coerce").fillna(0.5)
            _wptw_all = _wptw_all.sort_values("implied_prob", ascending=False)
            _wptw_all = _wptw_all.drop_duplicates(["player_name", "stat_type"]).drop_duplicates("player_name")
            _wptw = _wptw_all.head(10).reset_index(drop=True)
            for _wrs in range(0, len(_wptw), 5):
                _wchunk = list(_wptw.iloc[_wrs:_wrs + 5].iterrows())
                _wtcols = st.columns(len(_wchunk))
                for _wci, (_, _wr) in enumerate(_wchunk):
                    with _wtcols[_wci]:
                        _wotype = str(_wr.get("odds_type") or "standard").lower()
                        _wbcls = "ptw-badge-goblin" if _wotype == "goblin" else ("ptw-badge-demon" if _wotype == "demon" else "ptw-badge-normal")
                        _wblbl = _wotype.capitalize() if _wotype in ("goblin", "demon") else "Standard"
                        _wimp = int(float(_wr.get("implied_prob", 0.5)) * 100)
                        _wsrc = str(_wr.get("_source", ""))
                        st.markdown(f"""
                        <div class='ptw-card'>
                            <p class='ptw-player-name'>{_wr['player_name']}</p>
                            <p class='ptw-team'>{_wr.get('team', '')} &nbsp;·&nbsp; {_wr['stat_type']}</p>
                            <p class='ptw-line'>{_wr['line_score']}</p>
                            <span class='ptw-badge {_wbcls}'>{_wblbl} &nbsp;{_wimp}%</span>
                            <p style='font-size:0.68rem;color:var(--text-muted);margin:0.25rem 0 0.5rem;'>{_wsrc}</p>
                        </div>""", unsafe_allow_html=True)
                        _wbtn_key = f"wnba_ptw_{''.join(c for c in _wr['player_name'] if c.isalnum())}"
                        if st.button("→ Profile", key=_wbtn_key, width="stretch"):
                            _wabbr = str(_wr.get("team", "")).upper()
                            _wfull = _WNBA_ABBR_TO_FULL.get(_wabbr, "")
                            if _wfull:
                                st.session_state["wps_team"] = _wfull
                            st.session_state["_wps_player_hint"] = _wr["player_name"]
                            st.session_state["_wnba_ptw_nav"] = True
                            _safe_rerun()
        else:
            st.caption("Sportsbook lines unavailable right now. Check back closer to game time.")

        st.markdown("""
        <div class="sport-hero" style="background:linear-gradient(135deg,#111318 0%,#1d1528 55%,#111318 100%);">
            <div class="sport-hero-watermark">🏀</div>
            <div class="sport-hero-content">
                <p class="sport-hero-label">Konjure Analytics &nbsp;·&nbsp; WNBA Edition</p>
                <h2 class="sport-hero-title">WNBA Prop Intelligence</h2>
                <p class="sport-hero-sub">
                    Real-time player tracking &nbsp;·&nbsp; Rolling predictive models &nbsp;·&nbsp;
                    PrizePicks integration &nbsp;·&nbsp; Scout reports
                </p>
            </div>
        </div>""", unsafe_allow_html=True)

        feat_col_w, news_col_w = st.columns([1.5, 1])
        with feat_col_w:
            section("Platform Features")
            wfc1, wfc2 = st.columns(2)
            wfeatures = [
                (wfc1, "📊", "Player Stats", "Hit rates, rolling averages, and next-opponent predictions."),
                (wfc2, "📈", "Opponent Breakdown", "Player performance split by every opponent faced."),
                (wfc1, "🎯", "Bet Simulation", "Simulate flat-unit profit and loss across a season."),
                (wfc2, "🟣", "PrizePicks", "Today's live WNBA prop lines from PrizePicks."),
                (wfc1, "🃏", "Parlays", "Build optimized WNBA parlays from today's lines."),
            ]
            for col, icon, title, desc in wfeatures:
                with col:
                    st.markdown(f"""
                    <div class="feature-card">
                        <div class="feature-card-icon">{icon}</div>
                        <p class="feature-card-title">{title}</p>
                        <p class="feature-card-desc">{desc}</p>
                    </div>""", unsafe_allow_html=True)
        with news_col_w:
            section("WNBA News")
            with st.spinner("Loading news..."):
                _wnba_news = get_sport_news("wnba")
            render_news_panel(_wnba_news)

    # ── PLAYER STATS ──────────────────────────────────────────────────────────
    with tab_w_stats:
        if st.session_state.pop("_wnba_ptw_nav", False):
            components.html(
                "<script>setTimeout(function(){"
                "var t=window.parent.document.querySelectorAll('[role=\"tab\"]');"
                "if(t.length>1)t[1].click();"
                "},150);</script>",
                height=0,
            )
        wctrl, wmain = st.columns([1, 2.8])
        with wctrl:
            section("Select Player")
            wteam_names = sorted([t["full_name"] for t in _WNBA_TEAMS])
            wsel_team = st.selectbox("Team", wteam_names, key="wps_team")
            wteam_code = get_wnba_team_abbreviation(wsel_team)
            wplayer_list = get_wnba_team_players(wteam_code) if wteam_code else []
            _wps_hint = st.session_state.pop("_wps_player_hint", None)
            if _wps_hint and _wps_hint in wplayer_list:
                st.session_state["wps_player"] = _wps_hint
            wplayer_name = st.selectbox("Player", wplayer_list, key="wps_player")
            if wplayer_name:
                wnba_player_card(wplayer_name, wteam_code or "")
            section("Parameters")
            wseasons = st.multiselect("Seasons", _WNBA_SEASONS, default=["2025"], key="wps_seasons")
            wprop_type = st.selectbox("Prop Type", list(_WNBA_STAT_MAP.keys()), key="wps_prop")
            wline_value = st.number_input("Prop Line", value=15.5, step=0.5, key="wps_line")
            wrolling_window = st.slider("Rolling Window", 1, 10, 5, key="wps_roll")

        with wmain:
            if not wplayer_name or not wseasons:
                st.info("Select a player and season to view stats.")
            else:
                wpid = get_wnba_player_id(wplayer_name)
                if not wpid:
                    st.warning(f"Could not find player ID for **{wplayer_name}**.")
                else:
                    with st.spinner("Loading..."):
                        wdf = get_wnba_gamelogs(wpid, tuple(wseasons))
                    if wdf.empty:
                        st.warning("No game log data found.")
                    else:
                        wdf = wdf.copy()
                        wdf["PRA"] = wdf["PTS"] + wdf["REB"] + wdf["AST"]
                        wcol_key = _WNBA_STAT_MAP.get(wprop_type, "PTS")
                        if wprop_type == "Pts+Rebs+Asts":
                            wdf["TARGET"] = wdf["PRA"]
                        elif wprop_type == "Pts+Rebs":
                            wdf["TARGET"] = wdf["PTS"] + wdf["REB"]
                        elif wprop_type == "Pts+Asts":
                            wdf["TARGET"] = wdf["PTS"] + wdf["AST"]
                        else:
                            wdf["TARGET"] = wdf[wcol_key]
                        wdf["HIT"] = wdf["TARGET"] > wline_value
                        wdf["MARGIN"] = wdf["TARGET"] - wline_value
                        wdf["ROLLING_AVG"] = wdf["TARGET"].rolling(window=wrolling_window).mean()

                        # Market lines from PrizePicks + Underdog with hit rate comparison
                        section("Market Lines")
                        _wpp_live = get_prizepicks_with_team(league_id=6)
                        _wpp_stat = _WNBA_PP_STAT_MAP.get(wprop_type)
                        _wud_live = get_underdog_props("wnba")
                        _wud_stat = wprop_type
                        _wud_imp_val = None
                        wline_cols = st.columns(6)
                        wlines_shown = 0
                        for _wsb, _wsb_df, _wsb_stat in [
                            ("PrizePicks", _wpp_live, _wpp_stat),
                            ("Underdog", _wud_live, _wud_stat),
                        ]:
                            if _wsb_df is not None and not _wsb_df.empty and _wsb_stat:
                                _wm = _wsb_df[
                                    (_wsb_df["player_name"].str.lower() == wplayer_name.lower()) &
                                    (_wsb_df["stat_type"] == _wsb_stat)
                                ]
                                if not _wm.empty:
                                    _wr0 = _wm.iloc[0]
                                    _wl = float(_wr0["line_score"])
                                    _wi = float(_wr0.get("implied_prob", 0.5))
                                    _whr = (wdf["TARGET"] > _wl).mean()
                                    wline_cols[wlines_shown * 3].metric(f"{_wsb} Line", f"{_wl}")
                                    wline_cols[wlines_shown * 3 + 1].metric("Implied", f"{_wi:.0%}")
                                    wline_cols[wlines_shown * 3 + 2].metric(f"Hit Rate vs {_wsb}", f"{_whr:.1%}")
                                    if _wsb == "Underdog":
                                        _wud_imp_val = _wi
                                    wlines_shown += 1
                        if wlines_shown == 0:
                            st.info("No live lines found for this player/prop.")

                        # ── Model Prediction ──────────────────────────────
                        _w_cal = _load_calibration("WNBA")
                        _w_cal_f = _w_cal.get(wprop_type, 1.0)
                        _w_imp_override = _wud_imp_val if _wud_imp_val is not None else -1.0
                        _w_model_rate, _ = _wnba_hit_rate(
                            wplayer_name, wprop_type, wline_value,
                            odds_type="standard", implied_override=_w_imp_override,
                            cal_factor=_w_cal_f,
                        )
                        _w_raw_rate = wdf["HIT"].mean()
                        _w_edge = _w_model_rate - _wud_imp_val if _wud_imp_val is not None else None
                        section("Model Prediction")
                        _wm_cols = st.columns(4)
                        _wm_cols[0].metric("Model Hit Rate",     f"{_w_model_rate:.1%}", help="Calibrated blend of historical rate + market implied odds")
                        _wm_cols[1].metric("Raw Historical",     f"{_w_raw_rate:.1%}",   help="Simple hit rate from game log")
                        _wm_cols[2].metric("Calibration Factor", f"{_w_cal_f:.3f}",      help=f"Accuracy factor for {wprop_type} — WNBA Pts+Rebs+Asts consistently outperforms prediction (cal 1.30)")
                        _wm_cols[3].metric("Edge vs Market",     f"{_w_edge:+.1%}" if _w_edge is not None else "—", help="Model rate minus Underdog implied. Positive = model sees value.")

                        section("Performance Chart")
                        wfig = px.line(wdf.reset_index(), x=wdf.index, y=["TARGET", "ROLLING_AVG"],
                                      labels={"value": wprop_type, "index": "Game"},
                                      color_discrete_map={"TARGET": "#818cf8", "ROLLING_AVG": "#a78bfa"})
                        wfig.add_hline(y=wline_value, line_dash="dot", line_color="#ef4444",
                                      annotation_text=f"Line: {wline_value}")
                        st.plotly_chart(nba_fig(wfig), width="stretch", config=_CHART_CFG)

                        section("Summary Stats")
                        ws1, ws2, ws3, ws4 = st.columns(4)
                        ws1.metric("Hit Rate", f"{wdf['HIT'].mean():.1%}")
                        ws2.metric("Avg", f"{wdf['TARGET'].mean():.1f}")
                        ws3.metric("Last 5 Avg", f"{wdf['TARGET'].head(5).mean():.1f}")
                        ws4.metric("Games", len(wdf))

                        section("Game Log")
                        wlog_cols = ["GAME_DATE", "MATCHUP", "WL", "TARGET", "HIT", "MARGIN"]
                        wlog_cols = [c for c in wlog_cols if c in wdf.columns]
                        st.dataframe(wdf[wlog_cols].style.format(
                            {c: "{:.1f}" for c in ["TARGET", "MARGIN"] if c in wlog_cols}
                        ), width="stretch", hide_index=True)

    # ── OPPONENT BREAKDOWN ────────────────────────────────────────────────────
    with tab_w_opp:
        wobctrl, wobmain = st.columns([1, 2.8])
        with wobctrl:
            section("Select Player")
            wobteam_names = sorted([t["full_name"] for t in _WNBA_TEAMS])
            wobsel_team = st.selectbox("Team", wobteam_names, key="wob_team")
            wobteam_code = get_wnba_team_abbreviation(wobsel_team)
            wobplayer_list = get_wnba_team_players(wobteam_code) if wobteam_code else []
            wobplayer_name = st.selectbox("Player", wobplayer_list, key="wob_player")
            if wobplayer_name:
                wnba_player_card(wobplayer_name, wobteam_code or "")
            section("Parameters")
            wobseasons = st.multiselect("Seasons", _WNBA_SEASONS, default=["2025"], key="wob_seasons")
            wobprop = st.selectbox("Prop Type", list(_WNBA_STAT_MAP.keys()), key="wob_prop")
            wobline = st.number_input("Prop Line", value=15.5, step=0.5, key="wob_line")

        with wobmain:
            if wobplayer_name and wobseasons:
                wobpid = get_wnba_player_id(wobplayer_name)
                if not wobpid:
                    st.warning(f"Could not find player ID for **{wobplayer_name}**.")
                else:
                    with st.spinner("Loading..."):
                        wobdf = get_wnba_gamelogs(wobpid, tuple(wobseasons))
                    if wobdf.empty:
                        st.warning("No game log data found.")
                    else:
                        wobdf = wobdf.copy()
                        wobdf["PRA"] = wobdf["PTS"] + wobdf["REB"] + wobdf["AST"]
                        wobcol = _WNBA_STAT_MAP.get(wobprop, "PTS")
                        if wobprop == "Pts+Rebs+Asts":
                            wobdf["TARGET"] = wobdf["PRA"]
                        elif wobprop == "Pts+Rebs":
                            wobdf["TARGET"] = wobdf["PTS"] + wobdf["REB"]
                        elif wobprop == "Pts+Asts":
                            wobdf["TARGET"] = wobdf["PTS"] + wobdf["AST"]
                        else:
                            wobdf["TARGET"] = wobdf[wobcol]
                        wobdf["HIT"] = wobdf["TARGET"] > wobline
                        if "OPPONENT" not in wobdf.columns or wobdf["OPPONENT"].isna().all():
                            st.info("Opponent data not available for this player's logs.")
                        else:
                            wob_grouped = wobdf.groupby("OPPONENT").agg(
                                Hit_Rate=("HIT", "mean"),
                                Avg_Margin=("MARGIN", "mean") if "MARGIN" in wobdf.columns else ("TARGET", "mean"),
                                Avg_Stat=("TARGET", "mean"),
                                Games=("TARGET", "count"),
                            )
                            wob_grouped = wob_grouped.sort_values("Hit_Rate", ascending=False)
                            section(f"Hit Rate by Opponent — {wobprop}")
                            wob_fig = px.bar(wob_grouped.reset_index(), x="Hit_Rate", y="OPPONENT",
                                            orientation="h", color="Hit_Rate",
                                            color_continuous_scale=VIZ_SEQUENTIAL,
                                            text=wob_grouped["Games"].astype(str).values + " G")
                            wob_fig.add_vline(x=0.5, line_dash="dot", line_color="#3a4055")
                            wob_fig.update_coloraxes(showscale=False)
                            st.plotly_chart(nba_fig(wob_fig), width="stretch", config=_CHART_CFG)
                            section("Data Table")
                            st.dataframe(wob_grouped.style.format(
                                {"Hit_Rate": "{:.1%}", "Avg_Margin": "{:.2f}", "Avg_Stat": "{:.1f}"}
                            ), width="stretch")

    # ── VS. OPPONENT ──────────────────────────────────────────────────────────
    with tab_w_vs:
        with st.spinner("Loading today's games..."):
            _wnba_today = get_wnba_scoreboard(_wnba_scoreboard_date())
        if not _wnba_today:
            st.info("No WNBA games scheduled for today.")
        else:
            wvo_ctrl, wvo_main = st.columns([1, 2.8])
            with wvo_ctrl:
                section("Today's Games")
                wvo_seasons = st.multiselect("Seasons", _WNBA_SEASONS, default=["2025"], key="wvo_seasons")
                wvo_opts = [f"{g['away']} @ {g['home']}" for g in _wnba_today]
                wvo_label = st.selectbox("Select Game", wvo_opts, key="wvo_game")
                wvo_game = next(g for g in _wnba_today if f"{g['away']} @ {g['home']}" == wvo_label)
                wvo_team_raw = st.radio("Analyze Team",
                    [wvo_game["away"], wvo_game["home"]], key="wvo_team_side", horizontal=True)
                wvo_opp_raw = wvo_game["home"] if wvo_team_raw == wvo_game["away"] else wvo_game["away"]
                wvo_team_code = _resolve_wnba_abbr(wvo_team_raw)
                wvo_opp_code  = _resolve_wnba_abbr(wvo_opp_raw)

                section("Player")
                wvo_players = get_wnba_team_players(wvo_team_code)
                if not wvo_players:
                    st.warning(f"Could not load roster for {wvo_team_code}.")
                else:
                    wvo_player = st.selectbox("Player", wvo_players, key="wvo_player")
                    wnba_player_card(wvo_player, wvo_team_code)
                    section("Parameters")
                    wvo_prop = st.selectbox("Stat Type", list(_WNBA_STAT_MAP.keys()), key="wvo_prop")
                    wvo_line = st.number_input("Prop Line", value=15.5, step=0.5, key="wvo_line")
                    wvo_window = st.slider("Rolling Window", 3, 20, 10, key="wvo_window")

            with wvo_main:
                if wvo_players and wvo_seasons:
                    wvo_pid = get_wnba_player_id(wvo_player)
                    if wvo_pid:
                        with st.spinner("Loading game logs..."):
                            wvo_df = get_wnba_gamelogs(wvo_pid, tuple(wvo_seasons))
                        if wvo_df.empty:
                            st.warning("No stats found for this player.")
                        else:
                            wvo_df = wvo_df.copy()
                            wvo_df["PRA"] = wvo_df["PTS"] + wvo_df["REB"] + wvo_df["AST"]
                            wvo_col = _WNBA_STAT_MAP.get(wvo_prop, "PTS")
                            if wvo_prop == "Pts+Rebs+Asts":
                                wvo_df["TARGET"] = wvo_df["PRA"]
                            elif wvo_prop == "Pts+Rebs":
                                wvo_df["TARGET"] = wvo_df["PTS"] + wvo_df["REB"]
                            elif wvo_prop == "Pts+Asts":
                                wvo_df["TARGET"] = wvo_df["PTS"] + wvo_df["AST"]
                            else:
                                wvo_df["TARGET"] = wvo_df[wvo_col]
                            wvo_df["ROLLING"] = wvo_df["TARGET"].rolling(wvo_window).mean()
                            opp_mask = wvo_df["OPPONENT"].str.upper() == wvo_opp_code.upper() if "OPPONENT" in wvo_df.columns else pd.Series([False] * len(wvo_df))
                            vs_opp_df = wvo_df[opp_mask]
                            proj_all = wvo_df["TARGET"].rolling(wvo_window).mean().iloc[-1] if len(wvo_df) >= wvo_window else wvo_df["TARGET"].mean()
                            hit_all = (wvo_df["TARGET"] > wvo_line).mean()

                            # ── Scout Report ──────────────────────────────────
                            _wvo_report = wnba_scout_report(
                                wvo_player, wvo_team_code, wvo_df, wvo_opp_code,
                                wvo_prop, wvo_window, line=wvo_line,
                            )
                            if _wvo_report:
                                section("Scout Report")
                                st.markdown(_scout_card(_wvo_report), unsafe_allow_html=True)

                            section(f"Projection vs {wvo_opp_code} — Today")
                            wc1, wc2, wc3, wc4 = st.columns(4)
                            wc1.metric("Season Rolling Avg", f"{proj_all:.1f}")
                            wc2.metric("Season Hit Rate", f"{hit_all:.1%}")
                            if not vs_opp_df.empty:
                                avg_vs = vs_opp_df["TARGET"].mean()
                                hit_vs = (vs_opp_df["TARGET"] > wvo_line).mean()
                                wc3.metric(f"Avg vs {wvo_opp_code}", f"{avg_vs:.1f}",
                                          delta=f"{avg_vs - proj_all:+.1f} vs season", delta_color="normal")
                                wc4.metric(f"Hit Rate vs {wvo_opp_code}", f"{hit_vs:.1%}",
                                          delta=f"{hit_vs - hit_all:+.1%} vs season", delta_color="normal")
                            else:
                                wc3.metric(f"Avg vs {wvo_opp_code}", "—")
                                wc4.metric(f"Hit Rate vs {wvo_opp_code}", "—")

                            section("Rolling Performance")
                            wvo_fig = px.line(wvo_df.reset_index(), x=wvo_df.index, y=["TARGET", "ROLLING"],
                                             color_discrete_map={"TARGET": "#818cf8", "ROLLING": "#a78bfa"})
                            wvo_fig.add_hline(y=wvo_line, line_dash="dot", line_color="#ef4444")
                            st.plotly_chart(nba_fig(wvo_fig), width="stretch", config=_CHART_CFG)
                    else:
                        st.warning(f"Could not find player ID for {wvo_player}.")

    # ── BET SIMULATION ────────────────────────────────────────────────────────
    with tab_w_sim:
        wsim_ctrl, wsim_main = st.columns([1, 2.8])
        with wsim_ctrl:
            section("Select Player")
            wsim_teams = sorted([t["full_name"] for t in _WNBA_TEAMS])
            wsim_team = st.selectbox("Team", wsim_teams, key="wsim_team")
            wsim_tcode = get_wnba_team_abbreviation(wsim_team)
            wsim_players = get_wnba_team_players(wsim_tcode) if wsim_tcode else []
            wsim_player = st.selectbox("Player", wsim_players, key="wsim_player")
            if wsim_player:
                wnba_player_card(wsim_player, wsim_tcode or "")
            section("Parameters")
            wsim_seasons = st.multiselect("Seasons", _WNBA_SEASONS, default=["2025"], key="wsim_seasons")
            wsim_line = st.number_input("Prop Line", value=15.5, step=0.5, key="wsim_line")
            wsim_prop = st.selectbox("Prop Type", list(_WNBA_STAT_MAP.keys()), key="wsim_prop")

        with wsim_main:
            if wsim_player and wsim_seasons:
                wsim_pid = get_wnba_player_id(wsim_player)
                if not wsim_pid:
                    st.warning(f"Could not find player ID for **{wsim_player}**.")
                else:
                    with st.spinner("Loading..."):
                        wsim_df = get_wnba_gamelogs(wsim_pid, tuple(wsim_seasons))
                    if wsim_df.empty:
                        st.warning("No game log data found.")
                    else:
                        wsim_df = wsim_df.copy()
                        wsim_df["PRA"] = wsim_df["PTS"] + wsim_df["REB"] + wsim_df["AST"]
                        wsim_col = _WNBA_STAT_MAP.get(wsim_prop, "PTS")
                        if wsim_prop == "Pts+Rebs+Asts":
                            wsim_df["TARGET"] = wsim_df["PRA"]
                        elif wsim_prop == "Pts+Rebs":
                            wsim_df["TARGET"] = wsim_df["PTS"] + wsim_df["REB"]
                        elif wsim_prop == "Pts+Asts":
                            wsim_df["TARGET"] = wsim_df["PTS"] + wsim_df["AST"]
                        else:
                            wsim_df["TARGET"] = wsim_df[wsim_col]
                        wsim_df["HIT"] = wsim_df["TARGET"] > wsim_line
                        wsim_df["PROFIT"] = wsim_df["HIT"].apply(lambda x: 1.0 if x else -1.0)
                        wsim_df["CUMULATIVE"] = wsim_df["PROFIT"].cumsum()
                        section("Cumulative P&L")
                        wsim_fig = px.line(wsim_df.reset_index(), x=wsim_df.index, y="CUMULATIVE",
                                          color_discrete_sequence=[VIZ_SERIES[0]])
                        wsim_fig.add_hline(y=0, line_dash="dot", line_color="#3a4055")
                        st.plotly_chart(nba_fig(wsim_fig), width="stretch", config=_CHART_CFG)
                        ws1, ws2, ws3, ws4 = st.columns(4)
                        ws1.metric("Hit Rate", f"{wsim_df['HIT'].mean():.1%}")
                        ws2.metric("Net Units", f"{wsim_df['CUMULATIVE'].iloc[-1]:+.1f}")
                        ws3.metric("Games", len(wsim_df))
                        ws4.metric("Avg", f"{wsim_df['TARGET'].mean():.1f}")

    # ── SPORTSBOOK ────────────────────────────────────────────────────────────
    with tab_w_pp:
        _wsb_col1, _wsb_col2 = st.columns([4, 1])
        with _wsb_col1:
            _wsb_choice = st.radio("Sportsbook", ["FanDuel", "Underdog", "PrizePicks"], key="wsb_choice", horizontal=True)
        with _wsb_col2:
            if st.button("⟳ Refresh", key="wsb_refresh"):
                _ud_cache.pop("wnba", None)
        with st.spinner("Loading WNBA lines..."):
            if _wsb_choice == "PrizePicks":
                _wsb_df = get_prizepicks_with_team(league_id=6)
            else:
                _wsb_df = get_underdog_props("wnba")
        if _wsb_df is None or _wsb_df.empty:
            st.info(f"No WNBA lines available on {_wsb_choice} right now. Lines are typically posted closer to game time.")
        else:
            _wsb_df2 = _wsb_df.copy()
            _wsb_teams = sorted(_wsb_df2["team"].dropna().unique().tolist()) if "team" in _wsb_df2.columns else []
            _wsb_stats = sorted(_wsb_df2["stat_type"].dropna().unique().tolist())
            wfilt1, wfilt2 = st.columns(2)
            with wfilt1:
                _wsb_stat_sel = st.multiselect("Stat Type", _wsb_stats, default=_wsb_stats[:3] if len(_wsb_stats) >= 3 else _wsb_stats, key="wsb_stat_filter")
            with wfilt2:
                _wsb_team_sel = st.multiselect("Team", _wsb_teams, key="wsb_team_filter")
            if _wsb_stat_sel:
                _wsb_df2 = _wsb_df2[_wsb_df2["stat_type"].isin(_wsb_stat_sel)]
            if _wsb_team_sel and "team" in _wsb_df2.columns:
                _wsb_df2 = _wsb_df2[_wsb_df2["team"].isin(_wsb_team_sel)]
            _wsb_display_cols = ["player_name", "team", "stat_type", "line_score"]
            if "implied_prob" in _wsb_df2.columns:
                _wsb_display_cols.append("implied_prob")
            _wsb_display_cols = [c for c in _wsb_display_cols if c in _wsb_df2.columns]
            st.dataframe(
                _wsb_df2[_wsb_display_cols].sort_values("implied_prob", ascending=False) if "implied_prob" in _wsb_df2.columns else _wsb_df2[_wsb_display_cols],
                width="stretch", hide_index=True
            )

    # ── PARLAYS ───────────────────────────────────────────────────────────────
    with tab_w_parlays:
        section("WNBA Parlay Builder")
        st.markdown("""
        <p style='color:var(--text-muted);font-size:0.82rem;line-height:1.6;max-width:680px;margin-bottom:1rem;'>
        Builds optimized PrizePicks parlays using today's WNBA lines and historical hit rates
        (last 30 games, 2024 &amp; 2025 seasons). <strong style='color:var(--text-primary)'>Safe Parlays</strong>
        maximize probability of hitting. <strong style='color:#f59e0b;'>Value Parlays</strong>
        maximize expected value (probability &times; payout).
        </p>
        <div style='background:rgba(248,113,113,0.07);border:1px solid rgba(248,113,113,0.2);border-radius:10px;padding:0.75rem 1rem;max-width:680px;margin-bottom:1.25rem;'>
            <p style='font-size:0.6rem;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#f87171;margin:0 0 0.3rem 0;'>Disclaimer</p>
            <p style='font-size:0.78rem;color:#9294a8;line-height:1.6;margin:0;'>
                Parlay suggestions are generated from historical statistics and are for
                <strong style='color:#ccc;'>informational and entertainment purposes only.</strong>
                Past performance does not guarantee future results. This is not betting advice.
                Always gamble responsibly and within your means.
            </p>
        </div>""", unsafe_allow_html=True)

        _wpc1, _wpc2, _wpc3 = st.columns([1, 1, 2])
        with _wpc1:
            st.markdown("**Minimum Picks**")
            _wpar_min = st.selectbox("Min", [2, 3], index=0, key="wpar_min", label_visibility="collapsed")
        with _wpc2:
            st.markdown("**Maximum Picks**")
            _wpar_max = st.selectbox("Max", [2, 3, 4, 5], index=2, key="wpar_max", label_visibility="collapsed")
        with _wpc3:
            st.markdown("**Stat Types to Include**")
            _wpar_stats = st.multiselect(
                "Stats", options=list(_WNBA_PARLAY_COL_MAP.keys()),
                default=["Pts+Rebs+Asts"],
                key="wpar_stats", label_visibility="collapsed",
            )

        _wpar_sb_col1, _wpar_sb_col2 = st.columns([4, 1])
        with _wpar_sb_col1:
            _wsb_par = st.radio("Sportsbook", ["FanDuel", "Underdog", "PrizePicks"],
                                horizontal=True, key="wpar_sb")
        with _wpar_sb_col2:
            if st.button("Refresh", key="wpar_refresh"):
                if _wsb_par == "PrizePicks":
                    _pp_cache.clear(); _pp_cache_ts.clear()
                else:
                    _ud_cache.pop("wnba", None); _ud_cache_ts.pop("wnba", None)
                _safe_rerun()

        if st.button("Build WNBA Parlays", type="primary", key="wpar_build"):
            st.session_state["wnba_parlays_built"] = True
            st.session_state["wpar_min_val"]   = _wpar_min
            st.session_state["wpar_max_val"]   = _wpar_max
            st.session_state["wpar_stats_val"] = _wpar_stats
            st.session_state["wpar_sb_val"]    = _wsb_par

        if st.session_state.get("wnba_parlays_built"):
            _wb_min   = st.session_state.get("wpar_min_val", _wpar_min)
            _wb_max   = st.session_state.get("wpar_max_val", _wpar_max)
            _wb_stats = st.session_state.get("wpar_stats_val", _wpar_stats) or list(_WNBA_PARLAY_COL_MAP.keys())
            _wb_sb    = st.session_state.get("wpar_sb_val", _wsb_par)

            with st.spinner(f"Fetching {_wb_sb} WNBA lines..."):
                if _wb_sb == "PrizePicks":
                    _wraw = get_prizepicks_with_team(league_id=6)
                else:
                    _wraw = get_underdog_props("wnba")

            _using_fallback_wnba = False
            if _wraw is None or _wraw.empty:
                st.info(f"No live {_wb_sb} WNBA lines found — building parlays from historical data.")
                _using_fallback_wnba = True
            else:
                _wfilt = _wraw[_wraw["stat_type"].isin(_wb_stats)].copy()
                if _wfilt.empty:
                    st.info("No active lines for the selected stat types — switching to historical mode.")
                    _using_fallback_wnba = True

            _wnba_cal = _load_calibration("WNBA")

            # Warn for stats with poor calibration
            _wnba_weak = [s for s in _wb_stats if _wnba_cal.get(s, 1.0) < 0.3]
            if _wnba_weak:
                st.warning(
                    f"**Low-accuracy stat type(s): {', '.join(_wnba_weak)}** — "
                    "historical data shows these props almost never hit at the predicted rate. "
                    "Consider removing them from the selection above.",
                    icon="⚠️",
                )

            if _using_fallback_wnba:
                with st.spinner("Loading historical WNBA data..."):
                    _wlegs_data = _fallback_wnba_legs(_wb_stats, cal=_wnba_cal)
                if _wlegs_data:
                    st.caption("Using historical averages as prop lines (no live sportsbook data).")
                _wlegs_data = [l for l in _wlegs_data if l["hit_rate"] >= 0.35]
                _wsafe_p, _wvalue_p = _build_parlays(
                    _wlegs_data, min_legs=_wb_min, max_legs=_wb_max,
                    sportsbook=_wb_sb,
                    parlay_cal=parlay_tracker.get_parlay_calibration("WNBA"),
                )
            else:
                _wfilt = _wfilt.sort_values("implied_prob" if "implied_prob" in _wfilt.columns else "line_score", ascending=False).head(40)
                _wlegs = []
                # Pre-warm ESPN gamelogs in parallel
                _wwarm = [(r["player_name"], get_wnba_player_id(r["player_name"])) for _, r in _wfilt.iterrows()]
                _wwarm = [(n, p) for n, p in _wwarm if p]
                _wprog = st.progress(0, text=f"Loading {len(_wwarm)} player histories...")
                with ThreadPoolExecutor(max_workers=4) as _wex:
                    _wfuts = {_wex.submit(get_wnba_gamelogs, pid, ("2025",)): nm for nm, pid in _wwarm}
                    _wdone = 0
                    for _wf in as_completed(_wfuts, timeout=120):
                        _wdone += 1
                        _wprog.progress(_wdone / max(1, len(_wwarm)),
                                        text=f"Loading histories... ({_wdone}/{len(_wwarm)})")
                _wprog.empty()

                _wprog2 = st.progress(0, text="Calculating hit rates...")
                for _wi, (_widx, _wrow) in enumerate(_wfilt.iterrows()):
                    _wot  = _wrow.get("odds_type", "standard") or "standard"
                    _wimp = float(_wrow.get("implied_prob", -1.0) if "implied_prob" in _wrow.index else -1.0)
                    _wcal_f = _wnba_cal.get(_wrow["stat_type"], 1.0)
                    _wrate, _wn = _wnba_hit_rate(
                        _wrow["player_name"], _wrow["stat_type"],
                        float(_wrow["line_score"] or 0),
                        odds_type=_wot, implied_override=_wimp,
                        cal_factor=_wcal_f,
                    )
                    if _wn == 0:
                        _weff = _wimp if _wimp >= 0 else _PP_ODDS_IMPLIED.get(_wot, 0.50)
                        _wrate = round(min(0.97, max(0.03, _weff)), 3)
                        _wn = 1
                    _wlegs.append({
                        "player_name":  _wrow["player_name"],
                        "team":         _wrow.get("team", ""),
                        "stat_type":    _wrow["stat_type"],
                        "line_score":   _wrow["line_score"],
                        "odds_type":    _wot,
                        "american_odds": int(_wrow.get("american_odds", -110) if "american_odds" in _wrow.index else -110),
                        "implied_prob": _wimp if _wimp >= 0 else _PP_ODDS_IMPLIED.get(_wot, 0.50),
                        "sportsbook":   str(_wrow.get("sportsbook", _wb_sb) if "sportsbook" in _wrow.index else _wb_sb),
                        "game_id":      _wrow.get("game_id", ""),
                        "game_label":   _wrow.get("game_label", ""),
                        "hit_rate":     _wrate,
                        "sample_n":     _wn,
                    })
                    _wprog2.progress((_wi + 1) / len(_wfilt),
                                     text=f"Analyzing {_wrow['player_name']}...")
                _wprog2.empty()

                _wlegs_data = [l for l in _wlegs if l["sample_n"] >= 1 and l["hit_rate"] >= 0.35]
                _wsafe_p, _wvalue_p = _build_parlays(
                    _wlegs_data, min_legs=_wb_min, max_legs=_wb_max,
                    sportsbook=_wb_sb,
                    parlay_cal=parlay_tracker.get_parlay_calibration("WNBA"),
                )

            # Log to accuracy tracker
            try:
                _wlogged = parlay_tracker.log_parlays(_wsafe_p, "WNBA", _wb_sb, kind="safe")
                _wlogged += parlay_tracker.log_parlays(_wvalue_p, "WNBA", _wb_sb, kind="value")
                if _wlogged:
                    st.caption(f"Logged {_wlogged} new parlay(s) to accuracy tracker.")
            except Exception:
                pass

            st.markdown(
                f"<p style='font-size:0.72rem;color:var(--text-muted);margin:0.4rem 0 1.1rem;'>"
                f"Analyzed <strong>{len(_wlegs_data)}</strong> legs &nbsp;·&nbsp; "
                f"<strong>{len(_wsafe_p)}</strong> safe parlays &nbsp;·&nbsp; "
                f"<strong>{len(_wvalue_p)}</strong> value parlays</p>",
                unsafe_allow_html=True,
            )

            _render_parlay_pair(
                _wsafe_p, _wvalue_p, key="wnba", sport="WNBA",
                safe_empty="Not enough legs with data. Try adding more stat types or use historical mode.",
            )

            # Same-Game Parlays
            st.markdown("<hr style='margin:1.5rem 0;border-color:rgba(255,255,255,0.07);'>",
                        unsafe_allow_html=True)
            st.markdown("<p class='pl-section-label'>Same-Game Parlays — Best Picks Per Game</p>",
                        unsafe_allow_html=True)
            _wsgp = _build_sgp(_wlegs_data, min_legs=3, max_legs=5)
            if _wsgp:
                for _wg in _wsgp[:4]:
                    st.markdown(f"<p style='font-size:0.7rem;color:var(--text-muted);margin:0.6rem 0 0.2rem;'>"
                                f"{_wg['game_label']}</p>", unsafe_allow_html=True)
                    for _wp in _wg["parlays"][:2]:
                        st.markdown(_parlay_card_html(_wp, "safe"), unsafe_allow_html=True)
            else:
                st.caption("Not enough per-game legs for SGP suggestions.")
        else:
            st.info("Select your options above and click **Build WNBA Parlays** to generate suggestions. "
                    "First build may take 1-2 minutes while player histories are fetched.")

    # ── ACCURACY ──────────────────────────────────────────────────────────────
    if _IS_LOCAL and tab_w_accuracy is not None:
        with tab_w_accuracy:
            _render_accuracy_tab("WNBA")

    # ── DAILY BLOG ────────────────────────────────────────────────────────────
    with tab_w_blog:
        with st.spinner("Generating today's WNBA preview..."):
            _wnba_blog_games = get_wnba_scoreboard_full(_wnba_scoreboard_date())
            _wnba_blog_news  = get_sport_news("wnba")
        today = datetime.now()
        date_str = today.strftime("%B %d, %Y")
        n = len(_wnba_blog_games)
        if n == 0:
            st.info("No WNBA games today.")
        else:
            g0 = _wnba_blog_games[0]
            if n == 1:
                headline = f"{g0['away_abbr']} vs. {g0['home_abbr']}: Breaking Down Tonight's WNBA Showdown"
            else:
                headline = f"{n} Games Tonight: Konjure's Breakdown of Today's WNBA Slate"
            st.markdown(f"""
            <p style="font-size:0.65rem;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;
               color:var(--accent);margin:0 0 0.5rem 0;">WNBA Daily Brief &nbsp;·&nbsp; {date_str}</p>
            <h1 style="font-size:2rem;font-weight:800;color:var(--text-primary);margin:0 0 0.3rem 0;
               line-height:1.2;">{headline}</h1>
            <p style="font-size:0.72rem;color:var(--text-muted);margin:0 0 1.5rem 0;">
                {date_str} &nbsp;|&nbsp; Konjure Analytics</p>
            <hr style="border:none;border-top:1px solid var(--border);margin-bottom:1.5rem;">
            """, unsafe_allow_html=True)
            st.markdown('<h2 style="font-size:1.1rem;font-weight:700;color:var(--text-primary);">TODAY\'S MATCHUPS</h2>', unsafe_allow_html=True)
            for g in _wnba_blog_games:
                st.markdown(f"""
                <div style="background:var(--card-bg);border:1px solid var(--border);border-radius:10px;
                     padding:1rem 1.25rem;margin-bottom:0.75rem;">
                    <p style="font-weight:700;color:var(--text-primary);margin:0 0 0.2rem 0;">
                        {g.get('away','')} @ {g.get('home','')}
                    </p>
                    <p style="font-size:0.72rem;color:var(--text-muted);margin:0 0 0.5rem 0;">
                        {g.get('away_record','')} · {g.get('home_record','')}
                    </p>
                    <p style="font-size:0.8rem;color:var(--text-muted);margin:0;">
                        {g.get('venue','')}
                    </p>
                </div>""", unsafe_allow_html=True)
        section("WNBA News")
        render_news_panel(_wnba_blog_news)

    # ── DISCLAIMER ────────────────────────────────────────────────────────────
    with tab_w_disc:
        st.markdown("""
        <h3 style="color:var(--text-primary);">Disclaimer</h3>
        <p style="color:var(--text-muted);font-size:0.85rem;line-height:1.7;">
            Konjure Analytics provides data-driven insights for informational and entertainment purposes only.
            All statistics, projections, and prop suggestions are based on historical data and publicly available
            information. Nothing on this platform constitutes financial, legal, or betting advice.
            Users are solely responsible for their own decisions.
            It does not constitute betting advice or guarantee outcomes.
            Konjure Analytics is not responsible for any financial decisions made based on this data.
        </p>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════  MLB  ════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
elif sport == "⚾ MLB":

    # ── MLB score ticker (shown on all MLB tabs) ───────────────────────────────
    with st.spinner(""):
        _mlb_scores = get_mlb_scoreboard(_scoreboard_date())
    render_score_ticker(_mlb_scores, "MLB")

    # ── Ticker click: navigate to vs. Opponent tab ─────────────────────────────
    if (st.query_params.get("ticker_sport") == "MLB"
            and st.query_params.get("ticker_game")):
        _tnav_abbr = st.query_params["ticker_game"]  # e.g. "CLE @ TEX"
        _tnav_parts = _tnav_abbr.split(" @ ")
        if len(_tnav_parts) == 2:
            _tnav_away_abbr, _tnav_home_abbr = _tnav_parts
            # ESPN and MLB Stats API use different abbreviations for some teams
            _ESPN_TO_MLB = {"CHW": "CWS", "ARI": "AZ", "WSH": "WSH"}
            _tnav_away_abbr = _ESPN_TO_MLB.get(_tnav_away_abbr, _tnav_away_abbr)
            _tnav_home_abbr = _ESPN_TO_MLB.get(_tnav_home_abbr, _tnav_home_abbr)
            # Convert abbreviations → full-name label used by the vs. tab selectbox
            _tnav_all_games = get_today_mlb_games()
            for _tg in _tnav_all_games:
                if (_tg.get("away_abbr", "").upper() == _tnav_away_abbr.upper() and
                        _tg.get("home_abbr", "").upper() == _tnav_home_abbr.upper()):
                    st.session_state["vo_game"] = _tg["label"]
                    break
        st.session_state["_nav_vs_mlb"] = True
        del st.query_params["ticker_game"]
        del st.query_params["ticker_sport"]
        st.rerun()
    _mlb_tabs_iter  = _grouped_tabs([
        ("Home",    []),
        ("Analyze", ["Hitter Analysis", "Pitcher Analysis", "vs Opponent"]),
        ("Bet",     ["Bet Simulation", "Sportsbook", "Parlays"]),
        *( [("Track", ["Accuracy"])] if _IS_LOCAL else [] ),
        ("About",   ["Daily Blog", "Disclaimer"]),
    ])
    tab_mlb_home    = next(_mlb_tabs_iter)
    tab_hitter      = next(_mlb_tabs_iter)
    tab_pitcher     = next(_mlb_tabs_iter)
    tab_vs_opp      = next(_mlb_tabs_iter)
    tab_sim_mlb     = next(_mlb_tabs_iter)
    tab_pp_mlb      = next(_mlb_tabs_iter)
    tab_parlays_mlb = next(_mlb_tabs_iter)
    tab_accuracy_mlb = next(_mlb_tabs_iter) if _IS_LOCAL else None
    tab_blog_mlb    = next(_mlb_tabs_iter)
    tab_disc_mlb    = next(_mlb_tabs_iter)

    # ── MLB HOME ──────────────────────────────────────────────────────────────
    with tab_mlb_home:
        if st.session_state.pop("_nav_vs_mlb", False):
            components.html(
                "<script>setTimeout(function(){"
                "var t=window.parent.document.querySelectorAll('[role=\"tab\"]');"
                "if(t[3])t[3].click();"
                "},300);</script>",
                height=0,
            )
        _mlb_ptw_nav = st.session_state.pop("_mlb_ptw_nav", None)
        if _mlb_ptw_nav == "hitter":
            components.html(
                "<script>setTimeout(function(){"
                "var t=window.parent.document.querySelectorAll('[role=\"tab\"]');"
                "if(t.length>1)t[1].click();"
                "},150);</script>",
                height=0,
            )
        elif _mlb_ptw_nav == "pitcher":
            components.html(
                "<script>setTimeout(function(){"
                "var t=window.parent.document.querySelectorAll('[role=\"tab\"]');"
                "if(t.length>2)t[2].click();"
                "},150);</script>",
                height=0,
            )
        section("Players to Watch")
        with st.spinner(""):
            _mlb_ptw_games = get_mlb_today_with_pitchers()
            _mlb_all_teams = get_mlb_teams()
            _mlb_abbr_to_name = {t["abbr"].upper(): t["name"] for t in _mlb_all_teams}
            _mlb_h_frames = []
            # Underdog is primary source
            _mlb_ud = get_sportsbook_props("mlb", "Underdog")
            if not _mlb_ud.empty:
                _mlb_ud = _mlb_ud.copy(); _mlb_ud["_source"] = "Underdog"
                _mlb_h_frames.append(_mlb_ud)
            else:
                # Fall back to PrizePicks if Underdog returned nothing
                _mlb_pp = get_sportsbook_props("mlb", "PrizePicks")
                if not _mlb_pp.empty:
                    _mlb_pp = _mlb_pp.copy(); _mlb_pp["_source"] = "PrizePicks"
                    _mlb_h_frames.append(_mlb_pp)
            # FanDuel: include if already cached
            _mlb_fd_cached = _toa_cache.get("mlb_FanDuel")
            if _mlb_fd_cached is not None and not _mlb_fd_cached.empty:
                _mlb_fd_c = _mlb_fd_cached.copy(); _mlb_fd_c["_source"] = "FanDuel"
                _mlb_h_frames.append(_mlb_fd_c)

        # Build opponent ERA lookup: team_abbr -> opponent_pitcher_era
        _opp_era_lookup = {}
        for _g in _mlb_ptw_games:
            _a = str(_g.get("away_abbr", "")).upper()
            _h = str(_g.get("home_abbr", "")).upper()
            _h_era = float(_g.get("home_p_stats", {}).get("era", 0) or 0)
            _a_era = float(_g.get("away_p_stats", {}).get("era", 0) or 0)
            if _a:
                _opp_era_lookup[_a] = _h_era
            if _h:
                _opp_era_lookup[_h] = _a_era

        # Build pitcher tiles from today's games, ranked by K/9
        _mlb_pitcher_tiles = []
        for _g in _mlb_ptw_games[:6]:
            for _side in ("away", "home"):
                _pname = _g.get(f"{_side}_pitcher", "TBD")
                if _pname == "TBD":
                    continue
                _pteam = str(_g.get(f"{_side}_abbr", ""))
                _pstats = _g.get(f"{_side}_p_stats") or {}
                _era = _pstats.get("era", "—")
                _so = int(_pstats.get("strikeOuts", 0) or 0)
                try:
                    _ip = float(_pstats.get("inningsPitched", 0) or 0)
                except Exception:
                    _ip = 0
                _k9 = round((_so / _ip * 9) if _ip > 0 else 0, 1)
                _mlb_pitcher_tiles.append({
                    "player_name": _pname, "team": _pteam,
                    "era": _era, "_k9": _k9,
                })
        _mlb_pitcher_tiles.sort(key=lambda x: x["_k9"], reverse=True)

        # Build hitter tiles from all sportsbooks, scored by implied_prob + opp ERA
        _mlb_h_stats = ["Hits", "Home Runs", "RBIs", "Hits+Runs+RBIs", "Total Bases", "Stolen Bases", "RBI"]
        _top_hitters = []
        if _mlb_h_frames:
            _all_mlb_h = pd.concat(_mlb_h_frames, ignore_index=True)
            _all_mlb_h = _all_mlb_h[_all_mlb_h["stat_type"].isin(_mlb_h_stats)].copy()
            if "implied_prob" not in _all_mlb_h.columns:
                _all_mlb_h["implied_prob"] = 0.5
            _all_mlb_h["implied_prob"] = pd.to_numeric(_all_mlb_h["implied_prob"], errors="coerce").fillna(0.5)
            _all_mlb_h["opp_era"] = _all_mlb_h["team"].str.upper().map(_opp_era_lookup).fillna(0)
            _all_mlb_h["_ptw_score"] = _all_mlb_h["implied_prob"] + _all_mlb_h["opp_era"] / 20.0
            _all_mlb_h = _all_mlb_h.sort_values("_ptw_score", ascending=False)
            _all_mlb_h = _all_mlb_h.drop_duplicates(["player_name", "stat_type"]).drop_duplicates("player_name")
            _top_hitters = _all_mlb_h.head(6).to_dict("records")

        if _mlb_pitcher_tiles:
            mlb_section("Starting Pitchers")
            for _rs in range(0, len(_mlb_pitcher_tiles), 5):
                _chunk = _mlb_pitcher_tiles[_rs:_rs + 5]
                _tcols = st.columns(len(_chunk))
                for _ci, _r in enumerate(_chunk):
                    with _tcols[_ci]:
                        st.markdown(f"""
                        <div class='ptw-card'>
                            <p class='ptw-player-name'>{_r['player_name']}</p>
                            <p class='ptw-team'>{_r['team']} &nbsp;·&nbsp; SP</p>
                            <p class='ptw-line' style='font-size:0.85rem'>ERA {_r['era']}</p>
                            <span class='ptw-badge ptw-badge-normal'>K/9: {_r['_k9']}</span>
                        </div>""", unsafe_allow_html=True)
                        _p_btn_key = f"mlb_ptw_p_{''.join(c for c in _r['player_name'] if c.isalnum())}"
                        if st.button("→ Profile", key=_p_btn_key, width="stretch"):
                            _mlb_full = _mlb_abbr_to_name.get(_r["team"].upper(), "")
                            if _mlb_full:
                                st.session_state["p_team"] = _mlb_full
                            st.session_state["_mlb_p_player_hint"] = _r["player_name"]
                            st.session_state["_mlb_ptw_nav"] = "pitcher"
                            _safe_rerun()

        if _top_hitters:
            mlb_section("Hitters to Watch")
            for _rs in range(0, len(_top_hitters), 5):
                _chunk = _top_hitters[_rs:_rs + 5]
                _tcols = st.columns(len(_chunk))
                for _ci, _r in enumerate(_chunk):
                    with _tcols[_ci]:
                        _otype = str(_r.get("odds_type") or "standard").lower()
                        _bcls = "ptw-badge-goblin" if _otype == "goblin" else ("ptw-badge-demon" if _otype == "demon" else "ptw-badge-normal")
                        _blbl = _otype.capitalize() if _otype in ("goblin", "demon") else "Standard"
                        _opp_e = float(_r.get("opp_era", 0))
                        _era_note = f"Opp ERA {_opp_e:.2f}" if _opp_e > 0 else ""
                        st.markdown(f"""
                        <div class='ptw-card'>
                            <p class='ptw-player-name'>{_r['player_name']}</p>
                            <p class='ptw-team'>{_r.get('team', '')} &nbsp;·&nbsp; {_r['stat_type']}</p>
                            <p class='ptw-line'>{_r['line_score']}</p>
                            <span class='ptw-badge {_bcls}'>{_blbl}</span>
                            {f"<p style='font-size:0.68rem;color:var(--text-muted);margin:0.25rem 0 0.5rem;'>{_era_note}</p>" if _era_note else ""}
                        </div>""", unsafe_allow_html=True)
                        _h_btn_key = f"mlb_ptw_h_{''.join(c for c in _r['player_name'] if c.isalnum())}"
                        if st.button("→ Profile", key=_h_btn_key, width="stretch"):
                            _mlb_full = _mlb_abbr_to_name.get(str(_r.get("team", "")).upper(), "")
                            if _mlb_full:
                                st.session_state["h_team"] = _mlb_full
                            st.session_state["_mlb_h_player_hint"] = _r["player_name"]
                            st.session_state["_mlb_ptw_nav"] = "hitter"
                            _safe_rerun()

        if not _mlb_pitcher_tiles and not _top_hitters:
            st.caption("Player data unavailable right now.")

        # ── Today's Best Plays ────────────────────────────────────────────────
        with st.spinner("Analyzing today's best plays…"):
            _tbp = _compute_todays_best_plays_mlb()
        if _tbp:
            mlb_section("Today's Best Plays")
            st.markdown(
                "<p style='font-size:0.75rem;color:var(--text-muted);margin:-0.25rem 0 0.9rem;'>"
                "Top-edge props from Underdog Fantasy lines · 2 pitchers + 3 hitters · refreshed hourly</p>",
                unsafe_allow_html=True,
            )
            _tbp_cols = st.columns(len(_tbp))
            for _tcol, _tp in zip(_tbp_cols, _tbp):
                with _tcol:
                    _is_over = _tp["direction"] == "OVER"
                    _dir_color = "#22c55e" if _is_over else "#f87171"
                    _dir_bg    = "rgba(34,197,94,0.1)" if _is_over else "rgba(248,113,113,0.1)"
                    _conf_pct  = int(_tp["edge"] * 100)
                    _role      = "SP" if _tp["is_pitcher"] else "Hitter"
                    st.markdown(f"""
                    <div class='ptw-card' style='border-color:{_dir_color}44;'>
                        <p class='ptw-player-name'>{_tp['player']}</p>
                        <p class='ptw-team'>{_tp['team']} &nbsp;·&nbsp; {_role}</p>
                        <p class='ptw-line' style='font-size:0.92rem;margin-bottom:0.3rem;'>
                            {_tp['stat']} &nbsp;<span style='color:var(--text-muted);font-size:0.82rem;font-weight:500;'>{_tp['line']}</span>
                        </p>
                        <span style='display:inline-block;font-size:0.6rem;font-weight:700;letter-spacing:0.09em;
                                     text-transform:uppercase;padding:0.15rem 0.5rem;border-radius:4px;
                                     color:{_dir_color};background:{_dir_bg};border:1px solid {_dir_color}40;'>
                            {_tp['direction']}
                        </span>
                        <p style='font-size:0.64rem;color:var(--text-muted);margin:0.35rem 0 0;'>
                            {_conf_pct}% confidence &nbsp;·&nbsp; {_tp['n']}G sample
                        </p>
                    </div>""", unsafe_allow_html=True)

        st.markdown("""
        <div class="sport-hero" style="background:linear-gradient(135deg,#111318 0%,#181d2e 55%,#111318 100%);">
            <div class="sport-hero-watermark">⚾</div>
            <div class="sport-hero-content">
                <p class="sport-hero-label">Konjure Analytics &nbsp;·&nbsp; MLB Edition</p>
                <h2 class="sport-hero-title">MLB Predictive Analytics</h2>
                <p class="sport-hero-sub">
                    Hitter &amp; pitcher projections &nbsp;·&nbsp; Batter vs. pitcher matchups &nbsp;·&nbsp;
                    PrizePicks integration &nbsp;·&nbsp; Official MLB Stats API
                </p>
            </div>
        </div>""", unsafe_allow_html=True)

        feat_col, news_col = st.columns([1.5, 1])
        with feat_col:
            section("Platform Features")
            fc1, fc2 = st.columns(2)
            mlb_features = [
                (fc1, "⚾", "Hitter Analysis",
                 "Rolling averages, opponent splits, and next-game projections for H, HR, RBI."),
                (fc2, "🎯", "Pitcher Analysis",
                 "Per-start K, IP, ERA, WHIP trends and opponent breakdowns."),
                (fc1, "📊", "vs Opponent",
                 "Head-to-head batter vs. pitcher and pitcher vs. lineup previews."),
                (fc2, "🟣", "PrizePicks",
                 "Today's live MLB prop lines from PrizePicks."),
            ]
            for col, icon, title, desc in mlb_features:
                with col:
                    st.markdown(f"""
                    <div class="feature-card">
                        <div class="feature-card-icon">{icon}</div>
                        <p class="feature-card-title">{title}</p>
                        <p class="feature-card-desc">{desc}</p>
                    </div>""", unsafe_allow_html=True)
        with news_col:
            section("MLB News")
            with st.spinner("Loading news..."):
                _mlb_news = get_sport_news("mlb")
            render_news_panel(_mlb_news)

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
                    _h_hint = st.session_state.pop("_mlb_h_player_hint", None)
                    _h_names = [p["name"] for p in h_roster]
                    if _h_hint and _h_hint in _h_names:
                        st.session_state["h_player"] = _h_hint
                    sel_h_name = st.selectbox("Hitter", _h_names, key="h_player")
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
                    # ── Underdog Market ───────────────────────────────────
                    _ud_mlb_h_df = get_underdog_props("mlb")
                    _ud_h_stat   = _UD_MLB_HITTER_LOOKUP.get(h_stat)
                    _ud_h_line = _ud_h_odds = _ud_h_implied = None
                    if not _ud_mlb_h_df.empty and _ud_h_stat:
                        _ud_h_match = _ud_mlb_h_df[
                            (_ud_mlb_h_df["player_name"].str.lower() == sel_hitter["name"].lower()) &
                            (_ud_mlb_h_df["stat_type"] == _ud_h_stat)
                        ]
                        if not _ud_h_match.empty:
                            _ud_h_row      = _ud_h_match.iloc[0]
                            _ud_h_line     = float(_ud_h_row["line_score"])
                            _ud_h_odds     = int(_ud_h_row["american_odds"])
                            _ud_h_implied  = float(_ud_h_row["implied_prob"])
                    if _ud_h_line is not None:
                        mlb_section("Underdog Market")
                        _udh_cols = st.columns(4)
                        _ud_h_odds_disp = f"+{_ud_h_odds}" if _ud_h_odds > 0 else str(_ud_h_odds)
                        _udh_cols[0].metric(f"UD Line ({h_stat})", _ud_h_line)
                        _udh_cols[1].metric("UD Odds",  _ud_h_odds_disp)
                        _udh_cols[2].metric("Implied",  f"{_ud_h_implied:.0%}")
                        h_df["UD_HIT"] = h_df[h_stat] > _ud_h_line
                        _udh_cols[3].metric("Hit Rate vs UD", f"{h_df['UD_HIT'].mean():.1%}")

                    # ── Model Prediction ─────────────────────────────────
                    _H_COL_STAT = {"H": "Hits", "HR": "Home Runs", "RBI": "RBIs",
                                   "K": "Hitter Strikeouts", "BB": "Walks",
                                   "SB": "Stolen Bases", "TB": "Total Bases"}
                    _h_stat_type = _H_COL_STAT.get(h_stat)
                    if _h_stat_type:
                        _h_cal = _load_calibration("MLB")
                        _h_cal_f = _h_cal.get(_h_stat_type, 1.0)
                        _h_imp_override = _ud_h_implied if _ud_h_implied is not None else -1.0
                        # Get opposing pitcher if available for BvP
                        _h_today_game = get_mlb_today_game_for_team(sel_team["id"])
                        _h_opp_pid = None
                        if _h_today_game:
                            _h_at  = _h_today_game["teams"]["away"]
                            _h_ht  = _h_today_game["teams"]["home"]
                            _is_away = _h_at["team"]["id"] == sel_team["id"]
                            _opp_p  = _h_ht.get("probablePitcher") if _is_away else _h_at.get("probablePitcher")
                            _h_opp_pid = (_opp_p or {}).get("id")
                        _h_model_rate, _ = _mlb_hit_rate(
                            sel_hitter["name"], _h_stat_type, h_line,
                            odds_type="standard", implied_override=_h_imp_override,
                            cal_factor=_h_cal_f, opp_pitcher_id=_h_opp_pid,
                        )
                        # For HR props the recency hit-rate is a weak predictor; blend in
                        # the Statcast barrel model (best non-market HR signal) 50/50.
                        _h_hr_help = "Calibrated: 65% historical + BvP adjustment + 25% market implied"
                        if _h_stat_type == "Home Runs" and h_line <= 0.5:
                            _h_bhr = _pm.barrel_hr_prob(sel_hitter["id"], _h_opp_pid)
                            if _h_bhr is not None:
                                _h_model_rate = round(0.5 * _h_model_rate + 0.5 * _h_bhr, 3)
                                _h_hr_help = (f"HR blend: 50% recency/market + 50% Statcast barrel model "
                                              f"({_h_bhr:.0%} from barrel rate)")
                        _h_raw_rate = (h_df[h_stat] > h_line).mean()
                        _h_edge = _h_model_rate - _ud_h_implied if _ud_h_implied is not None else None
                        mlb_section("Model Prediction")
                        _hm_cols = st.columns(4)
                        _hm_cols[0].metric("Model Hit Rate",     f"{_h_model_rate:.1%}", help=_h_hr_help)
                        _hm_cols[1].metric("Raw Historical",     f"{_h_raw_rate:.1%}",  help="Simple hit rate from game log vs this line")
                        _hm_cols[2].metric("Calibration Factor", f"{_h_cal_f:.3f}",     help=f"Accuracy factor for {_h_stat_type} props based on resolved parlay history")
                        _hm_cols[3].metric("Edge vs Market",     f"{_h_edge:+.1%}" if _h_edge is not None else "—", help="Model rate minus UD implied. Positive = model sees value.")

                    # ── Scout Report ──────────────────────────────────────
                    _h_report = mlb_hitter_scout_report(
                        sel_hitter["name"], sel_team["abbr"], h_df, sel_team["id"],
                        line=h_line, prop_stat=h_stat,
                        ud_line=_ud_h_line, ud_odds=_ud_h_odds, ud_implied=_ud_h_implied,
                        player_id=sel_hitter["id"])
                    if _h_report:
                        mlb_section("Scout Report")
                        st.markdown(_scout_card_mlb(_h_report), unsafe_allow_html=True)

                    # ── Analytical Breakdown ──────────────────────────────
                    mlb_section("Analytical Breakdown")

                    _h_avg = h_df["AVG"].iloc[-1] if not h_df.empty else 0.0
                    _h_obp = h_df["OBP"].iloc[-1] if not h_df.empty else 0.0
                    _h_slg = h_df["SLG"].iloc[-1] if not h_df.empty else 0.0
                    _h_ops = _h_obp + _h_slg
                    _h_iso = _h_slg - _h_avg
                    sl_c = st.columns(5)
                    sl_c[0].metric("AVG", f"{_h_avg:.3f}")
                    sl_c[1].metric("OBP", f"{_h_obp:.3f}")
                    sl_c[2].metric("SLG", f"{_h_slg:.3f}")
                    sl_c[3].metric("OPS", f"{_h_ops:.3f}")
                    sl_c[4].metric("ISO", f"{_h_iso:.3f}")

                    # ── Statcast — Quality of Contact ─────────────────────
                    _h_sc = _pm.savant_batter_stats().get(sel_hitter["id"])
                    if _h_sc and _h_sc.get("barrel") is not None:
                        mlb_section("Statcast — Quality of Contact")
                        _scc = st.columns(5)
                        _scc[0].metric("Barrel%", f"{_h_sc['barrel']*100:.1f}%",
                                       help="Barrels per batted ball — the best non-market HR predictor. League ~7–8%.")
                        _scc[1].metric("xISO", f"{_h_sc['xiso']:.3f}" if _h_sc.get("xiso") is not None else "—",
                                       help="Expected isolated power from contact quality (strips batted-ball luck).")
                        _scc[2].metric("Hard-Hit%", f"{_h_sc['hardhit']:.0f}%" if _h_sc.get("hardhit") is not None else "—",
                                       help="Share of batted balls hit 95+ mph. League ~38%.")
                        _scc[3].metric("Avg EV", f"{_h_sc['ev']:.1f}" if _h_sc.get("ev") is not None else "—",
                                       help="Average exit velocity (mph).")
                        _scc[4].metric("FB% / Pull%",
                                       f"{_h_sc['fb']:.0f}/{_h_sc['pull']:.0f}" if _h_sc.get("fb") is not None else "—",
                                       help="Fly-ball% and pull% — whether that power actually leaves the yard.")

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
                        color_discrete_map={h_stat: "#818cf8", "ROLLING": "#a78bfa"},
                    )
                    fig_h.add_hline(y=h_line, line_dash="dot", line_color="#3a4055",
                                    annotation_text=f"Line {h_line}", annotation_font_color="#5c6272")
                    st.plotly_chart(mlb_fig(fig_h), width="stretch", config=_CHART_CFG)

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
                            color_continuous_scale=VIZ_SEQUENTIAL,
                            text=opp_h["Games"].astype(str).values + "G",
                        )
                        fig_opp.add_vline(x=h_df[h_stat].mean(), line_dash="dot", line_color="#a78bfa")
                        fig_opp.update_coloraxes(showscale=False)
                        st.plotly_chart(mlb_fig(fig_opp), width="stretch", config=_CHART_CFG)

                    # Recent game log table
                    mlb_section("Recent Game Log")
                    display_cols = ["date", "opponent", "AB", "H", "HR", "RBI", "BB", "K", "AVG", "OBP", "SLG"]
                    st.dataframe(
                        h_df[display_cols].tail(20).sort_values("date", ascending=False).style.format({
                            "date": lambda x: x.strftime("%b %d"),
                            "AVG": "{:.3f}", "OBP": "{:.3f}", "SLG": "{:.3f}",
                        }),
                        width="stretch", hide_index=True,
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
                    _p_hint = st.session_state.pop("_mlb_p_player_hint", None)
                    _p_names = [p["name"] for p in p_roster]
                    if _p_hint and _p_hint in _p_names:
                        st.session_state["p_player"] = _p_hint
                    sel_p_name = st.selectbox("Pitcher", _p_names, key="p_player")
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
                    # ── Underdog Market ───────────────────────────────────
                    _ud_mlb_p_df = get_underdog_props("mlb")
                    _ud_p_stat   = _UD_MLB_PITCHER_LOOKUP.get(p_stat)
                    _ud_p_line = _ud_p_odds = _ud_p_implied = None
                    if not _ud_mlb_p_df.empty and _ud_p_stat:
                        _ud_p_match = _ud_mlb_p_df[
                            (_ud_mlb_p_df["player_name"].str.lower() == sel_pitcher["name"].lower()) &
                            (_ud_mlb_p_df["stat_type"] == _ud_p_stat)
                        ]
                        if not _ud_p_match.empty:
                            _ud_p_row      = _ud_p_match.iloc[0]
                            _ud_p_line     = float(_ud_p_row["line_score"])
                            _ud_p_odds     = int(_ud_p_row["american_odds"])
                            _ud_p_implied  = float(_ud_p_row["implied_prob"])
                    if _ud_p_line is not None:
                        mlb_section("Underdog Market")
                        _udp_cols = st.columns(4)
                        _ud_p_odds_disp = f"+{_ud_p_odds}" if _ud_p_odds > 0 else str(_ud_p_odds)
                        _udp_cols[0].metric(f"UD Line ({p_stat})", _ud_p_line)
                        _udp_cols[1].metric("UD Odds",  _ud_p_odds_disp)
                        _udp_cols[2].metric("Implied",  f"{_ud_p_implied:.0%}")
                        p_df["UD_HIT"] = p_df[p_stat] > _ud_p_line
                        _udp_cols[3].metric("Hit Rate vs UD", f"{p_df['UD_HIT'].mean():.1%}")

                    # ── Model Prediction ─────────────────────────────────
                    _P_COL_STAT = {"K": "Pitcher Strikeouts", "ER": "Earned Runs Allowed",
                                   "BB": "Walks Allowed", "H": "Hits Allowed"}
                    _p_stat_type = _P_COL_STAT.get(p_stat)
                    if _p_stat_type:
                        _p_cal = _load_calibration("MLB")
                        _p_cal_f = _p_cal.get(_p_stat_type, 1.0)
                        _p_imp_override = _ud_p_implied if _ud_p_implied is not None else -1.0
                        _p_model_rate, _ = _mlb_hit_rate(
                            sel_pitcher["name"], _p_stat_type, p_line,
                            odds_type="standard", implied_override=_p_imp_override,
                            cal_factor=_p_cal_f,
                        )
                        _p_raw_rate = (p_df[p_stat] > p_line).mean()
                        _p_edge = _p_model_rate - _ud_p_implied if _ud_p_implied is not None else None
                        mlb_section("Model Prediction")
                        _pm_cols = st.columns(4)
                        _pm_cols[0].metric("Model Hit Rate",     f"{_p_model_rate:.1%}", help="Calibrated: 50% last-3 starts + 30% last-10 + 20% last-20 + 30% market implied")
                        _pm_cols[1].metric("Raw Historical",     f"{_p_raw_rate:.1%}",  help="Simple hit rate from game log vs this line")
                        _pm_cols[2].metric("Calibration Factor", f"{_p_cal_f:.3f}",     help=f"Accuracy factor for {_p_stat_type} props based on resolved parlay history")
                        _pm_cols[3].metric("Edge vs Market",     f"{_p_edge:+.1%}" if _p_edge is not None else "—", help="Model rate minus UD implied. Positive = model sees value.")

                    # ── Scout Report ──────────────────────────────────────
                    _p_report = mlb_pitcher_scout_report(
                        sel_pitcher["name"], sel_team_p["abbr"], p_df, sel_team_p["id"],
                        line=p_line, prop_stat=p_stat,
                        ud_line=_ud_p_line, ud_odds=_ud_p_odds, ud_implied=_ud_p_implied,
                        player_id=sel_pitcher["id"])
                    if _p_report:
                        mlb_section("Scout Report")
                        st.markdown(_scout_card_mlb(_p_report), unsafe_allow_html=True)

                    # ── Analytical Breakdown ──────────────────────────────
                    mlb_section("Analytical Breakdown")

                    _p_ip = p_df["IP"].sum()
                    _p_k = p_df["K"].sum()
                    _p_bb = p_df["BB"].sum()
                    # Season ERA/WHIP must come from totals — averaging per-game
                    # rates lets short blowup outings distort the number.
                    _p_era = (p_df["ER"].sum() / _p_ip * 9) if _p_ip > 0 else 0.0
                    _p_whip = ((p_df["H"].sum() + _p_bb) / _p_ip) if _p_ip > 0 else 0.0
                    _p_k9 = _p_k / _p_ip * 9 if _p_ip > 0 else 0
                    _p_bb9 = _p_bb / _p_ip * 9 if _p_ip > 0 else 0
                    _p_kbb = _p_k / _p_bb if _p_bb > 0 else 0
                    pm_c = st.columns(5)
                    pm_c[0].metric("ERA", f"{_p_era:.2f}")
                    pm_c[1].metric("WHIP", f"{_p_whip:.2f}")
                    pm_c[2].metric("K/9", f"{_p_k9:.1f}")
                    pm_c[3].metric("BB/9", f"{_p_bb9:.1f}")
                    pm_c[4].metric("K/BB", f"{_p_kbb:.2f}")

                    # ── Statcast — Contact Allowed ────────────────────────
                    _p_sc = _pm.savant_pitcher_stats().get(sel_pitcher["id"])
                    if _p_sc and _p_sc.get("barrel") is not None:
                        mlb_section("Statcast — Contact Allowed")
                        _psc = st.columns(5)
                        _psc[0].metric("Barrel% Allowed", f"{_p_sc['barrel']*100:.1f}%",
                                       help="Barrels allowed per batted ball — a flyball/barrel-prone arm gives up more HR. Lower is better; league ~7–8%.")
                        _psc[1].metric("Hard-Hit% Allowed", f"{_p_sc['hardhit']:.0f}%" if _p_sc.get("hardhit") is not None else "—",
                                       help="Share of batted balls allowed at 95+ mph.")
                        _psc[2].metric("FB% Allowed", f"{_p_sc['fb']:.0f}%" if _p_sc.get("fb") is not None else "—",
                                       help="Fly-ball rate allowed — flyball pitchers surrender more home runs.")
                        _psc[3].metric("GB% Allowed", f"{_p_sc['gb']:.0f}%" if _p_sc.get("gb") is not None else "—",
                                       help="Ground-ball rate allowed — groundball pitchers suppress HR.")
                        _psc[4].metric("xSLG Allowed", f"{_p_sc['xslg']:.3f}" if _p_sc.get("xslg") is not None else "—",
                                       help="Expected slugging allowed from contact quality.")

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

                    proj_p = {c: rolling_projection(p_df, c, p_window) for c in ["K", "IP", "ER", "BB", "K9"]}
                    # ERA/WHIP over the window come from summed totals, not an
                    # average of per-game rates (which short starts would skew).
                    _pw = p_df.tail(p_window) if len(p_df) >= p_window else p_df
                    _pw_ip = _pw["IP"].sum()
                    proj_p["ERA"] = (_pw["ER"].sum() / _pw_ip * 9) if _pw_ip > 0 else 0.0
                    proj_p["WHIP"] = ((_pw["H"].sum() + _pw["BB"].sum()) / _pw_ip) if _pw_ip > 0 else 0.0

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
                        color_discrete_map={p_stat: "#818cf8", "ROLLING": "#a78bfa"},
                    )
                    fig_p.add_hline(y=p_line, line_dash="dot", line_color="#3a4055",
                                    annotation_text=f"Line {p_line}", annotation_font_color="#5c6272")
                    st.plotly_chart(mlb_fig(fig_p), width="stretch", config=_CHART_CFG)

                    # Strikeout distribution
                    mlb_section("K Distribution")
                    fig_hist = px.histogram(
                        p_df, x="K", nbins=12,
                        color_discrete_sequence=[VIZ_SERIES[0]],
                    )
                    fig_hist.add_vline(x=p_line, line_dash="dot", line_color="#a78bfa",
                                       annotation_text=f"Line {p_line}", annotation_font_color="#a78bfa")
                    st.plotly_chart(mlb_fig(fig_hist), width="stretch", config=_CHART_CFG)

                    # Opponent breakdown
                    if "opponent" in p_df.columns and p_df["opponent"].notna().any():
                        mlb_section("Stats by Opponent")
                        opp_p = (p_df.groupby("opponent")[["K", "IP", "ER", "ERA"]]
                                 .mean().round(2).sort_values("K", ascending=False))
                        st.dataframe(opp_p.style.format("{:.2f}"), width="stretch")

                    # Recent starts table
                    mlb_section("Recent Starts")
                    pcols = ["date", "opponent", "IP", "H", "ER", "BB", "K", "HR", "ERA", "WHIP"]
                    st.dataframe(
                        p_df[pcols].tail(15).sort_values("date", ascending=False).style.format({
                            "date": lambda x: x.strftime("%b %d"),
                            "ERA": "{:.2f}", "WHIP": "{:.2f}", "IP": "{:.1f}",
                        }),
                        width="stretch", hide_index=True,
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

                        # ── Scout Report ──────────────────────────────────────
                        if vo_player_type == "Hitter":
                            _vo_report = mlb_hitter_scout_report(
                                vo_player["name"], vo_team["abbr"], vo_df, vo_team["id"],
                            )
                        else:
                            _vo_report = mlb_pitcher_scout_report(
                                vo_player["name"], vo_team["abbr"], vo_df, vo_team["id"],
                            )
                        if _vo_report:
                            mlb_section("Scout Report")
                            st.markdown(_scout_card_mlb(_vo_report), unsafe_allow_html=True)

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
                                         color_discrete_sequence=["#3a4055"])
                        fig_vo.add_scatter(x=vo_df["date"], y=vo_df["ROLLING"],
                                           mode="lines", name="Rolling Avg",
                                           line=dict(color="#818cf8", width=2))
                        if opp_mask.any():
                            fig_vo.add_scatter(
                                x=vo_df.loc[opp_mask, "date"],
                                y=vo_df.loc[opp_mask, primary],
                                mode="markers", name=f"vs {opp_display}",
                                marker=dict(color="#a78bfa", size=10, symbol="diamond"),
                            )
                        st.plotly_chart(mlb_fig(fig_vo), width="stretch", config=_CHART_CFG)

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
                            width="stretch", hide_index=True,
                        )

    # ── MLB BET SIMULATION ───────────────────────────────────────────────────
    with tab_sim_mlb:
        _MLB_HIT_SIM_MAP = {
            "Hits": "H", "Home Runs": "HR", "RBIs": "RBI",
            "Strikeouts": "K", "Walks": "BB", "Total Bases": "TB", "Stolen Bases": "SB",
        }
        _MLB_PIT_SIM_MAP = {
            "Strikeouts": "K", "Earned Runs": "ER", "Hits Allowed": "H", "Walks Allowed": "BB",
        }
        mlb_teams_sim = get_mlb_teams()
        sim_ctrl, sim_main = st.columns([1, 2.8])
        with sim_ctrl:
            mlb_section("Select Player")
            if not mlb_teams_sim:
                st.warning("Could not load MLB teams.")
            else:
                sim_team_name = st.selectbox("Team", [t["name"] for t in mlb_teams_sim], key="mlbs_team")
                sim_team = next(t for t in mlb_teams_sim if t["name"] == sim_team_name)
                sim_ptype = st.radio("Player Type", ["Hitter", "Pitcher"], key="mlbs_ptype", horizontal=True)
                with st.spinner("Loading roster..."):
                    sim_h_roster, sim_p_roster = get_mlb_roster(sim_team["id"])
                sim_roster = sim_h_roster if sim_ptype == "Hitter" else sim_p_roster
                if not sim_roster:
                    st.warning("No players found.")
                else:
                    sim_player_name = st.selectbox("Player", [p["name"] for p in sim_roster], key="mlbs_player")
                    sim_player = next(p for p in sim_roster if p["name"] == sim_player_name)
                    mlb_player_card(sim_player["name"], sim_player["pos"], sim_team["abbr"], sim_player["id"])
                    mlb_section("Parameters")
                    sim_stat_map = _MLB_HIT_SIM_MAP if sim_ptype == "Hitter" else _MLB_PIT_SIM_MAP
                    sim_stat = st.selectbox("Stat Type", list(sim_stat_map.keys()), key="mlbs_stat")
                    sim_line = st.number_input("Prop Line", value=0.5, step=0.5, key="mlbs_line")
                    sim_seasons = st.multiselect("Seasons", MLB_SEASONS, default=[MLB_SEASON], key="mlbs_seasons")

        with sim_main:
            if mlb_teams_sim and sim_roster and sim_seasons:
                _sim_col = sim_stat_map[sim_stat]
                with st.spinner("Running simulation..."):
                    _sim_seasons = tuple(sim_seasons) if sim_seasons else (MLB_SEASON,)
                    if sim_ptype == "Pitcher":
                        sim_df = get_mlb_pitching_logs(sim_player["id"], _sim_seasons)
                    else:
                        sim_df = get_mlb_hitting_logs(sim_player["id"], _sim_seasons)
                if sim_df.empty or _sim_col not in sim_df.columns:
                    st.warning("No data found for this player and stat.")
                else:
                    sim_df = sim_df.copy()
                    sim_df["HIT"] = sim_df[_sim_col] > sim_line
                    sim_df["CUMULATIVE_PROFIT"] = simulate_bets(sim_df)
                    mlb_section("Results")
                    rc1, rc2, rc3 = st.columns(3)
                    rc1.metric("Total Profit", f"{sim_df['CUMULATIVE_PROFIT'].iloc[-1]:.0f} units")
                    rc2.metric("Hit Rate", f"{sim_df['HIT'].mean():.1%}")
                    rc3.metric("Games", len(sim_df))
                    mlb_section("Cumulative P&L")
                    fig_sim = px.line(sim_df.reset_index(), y="CUMULATIVE_PROFIT",
                                      labels={"CUMULATIVE_PROFIT": "Units", "index": "Game"},
                                      color_discrete_sequence=[VIZ_SERIES[0]])
                    fig_sim.add_hline(y=0, line_dash="dot", line_color="#1e2d3d")
                    st.plotly_chart(mlb_fig(fig_sim), width="stretch", config=_CHART_CFG)
                    mlb_section("Game Log")
                    st.dataframe(
                        sim_df[["date", "opponent", _sim_col, "HIT"]].rename(columns={
                            "date": "Date", "opponent": "Opponent",
                            _sim_col: sim_stat, "HIT": "Hit",
                        }),
                        width="stretch", hide_index=True,
                    )

    # ── MLB PRIZEPICKS ────────────────────────────────────────────────────────
    with tab_pp_mlb:
        _sb_mlb_col1, _sb_mlb_col2 = st.columns([4, 1])
        with _sb_mlb_col1:
            _sb_mlb = st.radio(
                "Sportsbook",
                ["FanDuel", "Underdog", "PrizePicks"],
                horizontal=True,
                key="sb_mlb_select",
            )
        with _sb_mlb_col2:
            if st.button("🔄 Refresh Lines", key="mlb_sb_refresh"):
                if _sb_mlb == "PrizePicks":
                    _pp_cache.clear(); _pp_cache_ts.clear()
                    _pp_lite_cache.clear(); _pp_lite_cache_ts.clear()
                elif _sb_mlb == "Underdog":
                    _ud_cache.pop("mlb", None); _ud_cache_ts.pop("mlb", None)
                else:
                    _toa_cache.pop(f"mlb_{_sb_mlb}", None); _toa_cache_ts.pop(f"mlb_{_sb_mlb}", None)
                _safe_rerun()
        st.session_state["mlb_sportsbook"] = _sb_mlb
        with st.spinner(f"Loading {_sb_mlb} MLB projections..."):
            mlb_pp_df = get_sportsbook_props("mlb", _sb_mlb)
        _mlb_toa_err = _toa_cache.get(f"_err_mlb_{_sb_mlb}", "")
        _mlb_toa_rem = _TOA_CREDITS_REMAINING.get(_get_odds_api_key())
        if _sb_mlb in ("FanDuel", "DraftKings", "Bet365") and _mlb_toa_rem is not None:
            st.caption(f"Odds API credits remaining this month: **{_mlb_toa_rem}**")
        if mlb_pp_df.empty:
            if _mlb_toa_err == "quota_exceeded":
                st.error(f"**{_sb_mlb} API quota exhausted.** The Odds API free tier allows 500 credits/month. Add a new `ODDS_API_KEY` in Secrets.")
            elif _mlb_toa_err == "invalid_key":
                st.error(f"**{_sb_mlb} API key is invalid.** Update `ODDS_API_KEY` in Streamlit Secrets.")
            elif _sb_mlb in ("FanDuel", "DraftKings", "Bet365") and not _get_odds_api_key():
                st.error(f"**{_sb_mlb} requires an Odds API key.** Add `ODDS_API_KEY` to Streamlit Secrets.")
            else:
                st.info(f"No {_sb_mlb} MLB lines available right now. Lines are typically posted on game days.")
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

            filt = filt.copy()
            if _sb_mlb == "PrizePicks":
                _ot_label_mlb = {"goblin": "Goblin", "demon": "Demon", "standard": "Standard"}
                filt["Odds"] = filt["odds_type"].map(_ot_label_mlb).fillna("Standard")
                filt["Implied %"] = filt["odds_type"].map(
                    lambda x: f"{int(_PP_ODDS_IMPLIED.get(x, 0.50)*100)}%"
                )
            else:
                filt["Odds"] = filt["american_odds"].apply(
                    lambda x: f"+{x}" if x > 0 else str(x)
                ) if "american_odds" in filt.columns else "-"
                filt["Implied %"] = filt["implied_prob"].apply(
                    lambda x: f"{int(x*100)}%"
                ) if "implied_prob" in filt.columns else "50%"
            mlb_section(f"{len(filt)} Line(s) — {_sb_mlb}")
            show_cols_mlb = ["player_name", "team", "stat_type", "line_score", "Odds", "Implied %", "game_label"]
            show_cols_mlb = [c for c in show_cols_mlb if c in filt.columns]
            st.dataframe(
                filt[show_cols_mlb].rename(columns={
                    "player_name": "Player", "team": "Team", "stat_type": "Stat",
                    "line_score": "Line", "game_label": "Game",
                }).sort_values("Player"),
                width="stretch", hide_index=True,
            )

    # ── MLB PARLAYS ───────────────────────────────────────────────────────────
    with tab_parlays_mlb:
        section("MLB Parlay Builder")
        st.markdown("""
        <p style='color:var(--text-muted);font-size:0.82rem;line-height:1.6;max-width:680px;margin-bottom:1rem;'>
        Builds optimized PrizePicks parlays using today's MLB lines and historical hit rates
        (last 20 games). Hitter and pitcher legs are analyzed separately using official MLB Stats API data.
        <strong style='color:var(--text-primary)'>Safe Parlays</strong> maximize probability.
        <strong style='color:#f59e0b;'>Value Parlays</strong> maximize expected value.
        </p>
        <div style='background:rgba(248,113,113,0.07);border:1px solid rgba(248,113,113,0.2);border-radius:10px;padding:0.75rem 1rem;max-width:680px;margin-bottom:1.25rem;'>
            <p style='font-size:0.6rem;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:#f87171;margin:0 0 0.3rem 0;'>Disclaimer</p>
            <p style='font-size:0.78rem;color:#9294a8;line-height:1.6;margin:0;'>
                Parlay suggestions are generated from historical statistics and are for
                <strong style='color:#ccc;'>informational and entertainment purposes only.</strong>
                Past performance does not guarantee future results. This is not betting advice.
                Always gamble responsibly and within your means.
            </p>
        </div>""", unsafe_allow_html=True)

        _mp1, _mp2, _mp3 = st.columns([1, 1, 2])
        with _mp1:
            st.markdown("**Minimum Picks**")
            _mlb_par_min = st.selectbox("Min Picks", [2, 3], index=0, key="mlb_par_min", label_visibility="collapsed")
        with _mp2:
            st.markdown("**Maximum Picks**")
            _mlb_par_max = st.selectbox("Max Picks", [2, 3, 4, 5], index=1, key="mlb_par_max", label_visibility="collapsed")
        with _mp3:
            st.markdown("**Stat Types to Include**")
            _all_mlb_stat_types = list(_PP_MLB_HIT_COL.keys()) + list(_PP_MLB_PIT_COL.keys())
            _mlb_par_stats = st.multiselect(
                "Stat Types",
                options=sorted(set(_all_mlb_stat_types)),
                default=["Hits", "Pitcher Strikeouts"],
                key="mlb_par_stats",
                label_visibility="collapsed",
            )
            # Read the hit rates out of the tracker instead of hard-coding them. The
            # sentence that lived here quoted 72.5% for Total Bases and 3.7% for RBIs;
            # both came from the pre-dedupe, pre-resolver-fix log and are now wrong
            # (52.6% and 25.0%). A caption asserting a number it doesn't measure is a
            # caption that goes stale the moment the data moves.
            _mlb_worst = [
                r for r in parlay_tracker.get_all_time_calibration_table(sport="MLB")
                if r["samples"] >= 5 and r["actual_hit_rate"] < 0.40
            ]
            if _mlb_worst:
                _w = ", ".join(
                    f"{r['stat_type']} {r['actual_hit_rate']:.0%} ({r['samples']} props)"
                    for r in sorted(_mlb_worst, key=lambda x: x["actual_hit_rate"])[:3]
                )
                st.caption(f"Lowest-hitting MLB props on resolved data: {_w}. "
                           f"Calibration deflates these automatically — see the Accuracy tab.")
            else:
                st.caption("Per-stat hit rates and calibration factors are on the Accuracy tab.")

        if st.button("Build MLB Parlays", type="primary", key="mlb_build_parlays"):
            st.session_state["mlb_parlays_built"] = True
            st.session_state["mlb_par_min_val"] = _mlb_par_min
            st.session_state["mlb_par_max_val"] = _mlb_par_max
            st.session_state["mlb_par_stats_val"] = _mlb_par_stats

        if st.session_state.get("mlb_parlays_built"):
            _mb_min = st.session_state.get("mlb_par_min_val", _mlb_par_min)
            _mb_max = st.session_state.get("mlb_par_max_val", _mlb_par_max)
            _mb_stats = st.session_state.get("mlb_par_stats_val", _mlb_par_stats) or list(_PP_MLB_HIT_COL.keys())

            _mlb_sb = st.session_state.get("mlb_sportsbook", "Underdog")
            _MLB_SB_OPTS = ["FanDuel", "Underdog", "PrizePicks"]
            if _mlb_sb not in _MLB_SB_OPTS:
                _mlb_sb = "Underdog"
            _mlb_par_sb_col1, _mlb_par_sb_col2 = st.columns([4, 1])
            with _mlb_par_sb_col1:
                _sb_choice_mlb = st.radio(
                    "Sportsbook for Parlays",
                    _MLB_SB_OPTS,
                    horizontal=True,
                    index=_MLB_SB_OPTS.index(_mlb_sb),
                    key="mlb_par_sb",
                )
            with _mlb_par_sb_col2:
                if st.button("🔄 Refresh", key="mlb_par_refresh"):
                    if _sb_choice_mlb == "PrizePicks":
                        _pp_cache.clear(); _pp_cache_ts.clear()
                    elif _sb_choice_mlb == "Underdog":
                        _ud_cache.pop("mlb", None); _ud_cache_ts.pop("mlb", None)
                    elif _sb_choice_mlb == "DraftKings":
                        _dk_cache.pop("mlb", None); _dk_cache_ts.pop("mlb", None)
                        _sharp_cache.pop("mlb_draftkings", None); _sharp_cache_ts.pop("mlb_draftkings", None)
                    elif _sb_choice_mlb == "FanDuel":
                        _sharp_cache.pop("mlb_fanduel", None); _sharp_cache_ts.pop("mlb_fanduel", None)
                        _toa_cache.pop("mlb_FanDuel", None); _toa_cache_ts.pop("mlb_FanDuel", None)
                    else:
                        _toa_cache.pop(f"mlb_{_sb_choice_mlb}", None)
                        _toa_cache_ts.pop(f"mlb_{_sb_choice_mlb}", None)
                    _safe_rerun()
            with st.spinner(f"Fetching {_sb_choice_mlb} MLB lines…"):
                _mlb_pp_raw = get_sportsbook_props("mlb", _sb_choice_mlb)

            _using_fallback_mlb = False
            if _mlb_pp_raw.empty:
                if _sb_choice_mlb in ("DraftKings", "FanDuel") and not _get_sharp_api_key():
                    st.warning(
                        f"**{_sb_choice_mlb} requires a SharpAPI key** (free, no credit card). "
                        "Register at [sharpapi.io](https://sharpapi.io) then add "
                        "`SHARP_API_KEY = \"sk_live_xxx\"` to `.streamlit/secrets.toml`."
                    )
                st.info(f"No live {_sb_choice_mlb} MLB lines found — building parlays from historical data.")
                _using_fallback_mlb = True
            else:
                _mlb_filt = _mlb_pp_raw[_mlb_pp_raw["stat_type"].isin(_mb_stats)].copy()
                if _mlb_filt.empty:
                    st.info("No active MLB lines for the selected stat types — switching to historical mode.")
                    _using_fallback_mlb = True

            _mlb_cal = _load_calibration("MLB")

            # Warn if any selected stat type has a very poor calibration factor
            _weak_stats = [s for s in _mb_stats if _mlb_cal.get(s, 1.0) < 0.65]
            if _weak_stats:
                st.warning(
                    f"**Low-accuracy stat type(s) detected: {', '.join(_weak_stats)}** — "
                    "calibration data shows the model significantly overestimates these hit rates. "
                    "Consider removing them from the selection above.",
                    icon="⚠️",
                )

            if _using_fallback_mlb:
                with st.spinner("Loading historical MLB data…"):
                    _legs_mlb_data = _fallback_mlb_legs(_mb_stats, cal=_mlb_cal)
                if _legs_mlb_data:
                    st.caption("Using historical averages as prop lines (no live sportsbook data).")
                # Drop legs whose calibrated hit rate is below threshold (< 35%)
                _legs_mlb_data = [l for l in _legs_mlb_data if l["hit_rate"] >= 0.35]
                _safe_m, _value_m = _build_parlays(
                    _legs_mlb_data, min_legs=_mb_min, max_legs=_mb_max,
                    sportsbook=_sb_choice_mlb,
                    parlay_cal=parlay_tracker.get_parlay_calibration("MLB"),
                )
            else:
                _mlb_filt = _mlb_filt.sort_values("implied_prob" if "implied_prob" in _mlb_filt.columns else "line_score", ascending=False).head(50)
                _legs_mlb = []
                # Pre-fetch today's probable pitchers for BvP matching
                try:
                    _pitcher_lookup = _mlb_today_pitcher_lookup()
                except Exception:
                    _pitcher_lookup = {}

                # Pre-warm gamelogs in parallel — avoids serial API calls in the loop below
                _mlb_warm = []
                for _, _mrow in _mlb_filt.iterrows():
                    _is_p = _mrow["stat_type"] in _PP_PITCHER_TYPES
                    _pid = _mlb_player_id_by_name(_mrow["player_name"])
                    if _pid:
                        _mlb_warm.append((_pid, _is_p))
                _mwarm_prog = st.progress(0, text=f"Loading {len(_mlb_warm)} player histories…")
                with ThreadPoolExecutor(max_workers=5) as _mwex:
                    _mwfuts = {
                        _mwex.submit(
                            get_mlb_pitching_logs if _isp else get_mlb_hitting_logs,
                            _pid, ("2025", "2026")
                        ): _pid
                        for _pid, _isp in _mlb_warm
                    }
                    _mwdone = 0
                    try:
                        for _mwf in as_completed(_mwfuts, timeout=120):
                            _mwdone += 1
                            _mwarm_prog.progress(_mwdone / max(1, len(_mlb_warm)),
                                                 text=f"Loading histories… ({_mwdone}/{len(_mlb_warm)})")
                    except Exception:
                        pass
                _mwarm_prog.empty()

                _mlb_prog = st.progress(0, text="Calculating MLB hit rates…")
                for _mi, (_mix, _mrow) in enumerate(_mlb_filt.iterrows()):
                    _mot = _mrow.get("odds_type", "standard") or "standard"
                    _mimp = float(_mrow.get("implied_prob", -1.0) if "implied_prob" in _mrow.index else -1.0)
                    _mcal_f = _mlb_cal.get(_mrow["stat_type"], 1.0)
                    _mteam = _mrow.get("team", "") or ""
                    _opp_pid = _pitcher_lookup.get(_mteam) if _mrow["stat_type"] not in _PP_PITCHER_TYPES else None
                    _mrate, _mn = _mlb_hit_rate(
                        _mrow["player_name"], _mrow["stat_type"],
                        float(_mrow["line_score"] or 0),
                        odds_type=_mot, implied_override=_mimp,
                        cal_factor=_mcal_f, opp_pitcher_id=_opp_pid,
                    )
                    # When player history is unavailable, fall back to implied odds
                    if _mn == 0:
                        _eff_imp = _mimp if _mimp >= 0 else _PP_ODDS_IMPLIED.get(_mot, 0.50)
                        _mrate = round(min(0.97, max(0.03, _eff_imp)), 3)
                        _mn = 1
                    _legs_mlb.append({
                        "player_name": _mrow["player_name"],
                        "team":        _mrow.get("team", ""),
                        "stat_type":   _mrow["stat_type"],
                        "line_score":  _mrow["line_score"],
                        "odds_type":   _mot,
                        "american_odds": int(_mrow.get("american_odds", -110) if "american_odds" in _mrow.index else -110),
                        "implied_prob": _mimp if _mimp >= 0 else _PP_ODDS_IMPLIED.get(_mot, 0.50),
                        "sportsbook":  str(_mrow.get("sportsbook", "PrizePicks") if "sportsbook" in _mrow.index else _sb_choice_mlb),
                        "game_id":     _mrow.get("game_id", ""),
                        "game_label":  _mrow.get("game_label", ""),
                        "hit_rate":    _mrate,
                        "sample_n":    _mn,
                    })
                    _mlb_prog.progress((_mi + 1) / len(_mlb_filt),
                                       text=f"Analyzing {_mrow['player_name']}…")
                _mlb_prog.empty()

                _legs_mlb_data = [l for l in _legs_mlb if l["sample_n"] >= 1]
                # If live data is still too sparse, supplement with historical legs
                if len(_legs_mlb_data) < max(_mb_min, 3):
                    with st.spinner("Supplementing with historical MLB data…"):
                        _hist_legs_m = _fallback_mlb_legs(_mb_stats, cal=_mlb_cal)
                    _legs_mlb_data = _legs_mlb_data + _hist_legs_m
                # Drop legs whose calibrated hit rate is below threshold (< 35%)
                _legs_mlb_data = [l for l in _legs_mlb_data if l["hit_rate"] >= 0.35]
                _safe_m, _value_m = _build_parlays(
                    _legs_mlb_data, min_legs=_mb_min, max_legs=_mb_max,
                    sportsbook=_sb_choice_mlb,
                    parlay_cal=parlay_tracker.get_parlay_calibration("MLB"),
                )

            # Log generated parlays for accuracy tracking
            try:
                _mlogged = parlay_tracker.log_parlays(_safe_m, "MLB", _sb_choice_mlb, kind="safe")
                _mlogged += parlay_tracker.log_parlays(_value_m, "MLB", _sb_choice_mlb, kind="value")
                if _mlogged:
                    st.caption(f"Logged {_mlogged} new parlay(s) to accuracy tracker.")
            except Exception:
                pass

            # --- DISPLAY (always runs) ---
            st.markdown(
                f"<p style='font-size:0.72rem;color:var(--text-muted);margin:0.4rem 0 1.1rem;'>"
                f"Analyzed <strong>{len(_legs_mlb_data)}</strong> legs with data &nbsp;·&nbsp; "
                f"<strong>{len(_safe_m)}</strong> safe parlays &nbsp;·&nbsp; "
                f"<strong>{len(_value_m)}</strong> value parlays</p>",
                unsafe_allow_html=True,
            )

            _render_parlay_pair(_safe_m, _value_m, key="mlb", sport="MLB")

            # ── Same-Game Parlays ─────────────────────────────────────────────
            st.markdown("<hr style='margin:1.5rem 0;border-color:rgba(255,255,255,0.07);'>",
                        unsafe_allow_html=True)
            st.markdown("<p class='pl-section-label'>Same-Game Parlays — Best Picks Per Game</p>",
                        unsafe_allow_html=True)
            _mlb_sgp = _build_sgp(_legs_mlb_data, min_legs=3, max_legs=5)
            if _mlb_sgp:
                for _sgpm in _mlb_sgp:
                    st.markdown(
                        f"<p style='font-size:0.78rem;font-weight:600;color:#3b82f6;"
                        f"margin:1rem 0 0.4rem;letter-spacing:0.06em;'>"
                        f"{_sgpm['game_label']}</p>",
                        unsafe_allow_html=True,
                    )
                    for _spm in _sgpm["parlays"]:
                        st.markdown(_parlay_card_html(_spm, "safe"), unsafe_allow_html=True)
            else:
                st.caption("Same-game parlay data requires game grouping from live sportsbook. "
                           "No games with enough legs were found today.")
        else:
            st.info("Select your options above and click **Build MLB Parlays** to generate suggestions. "
                    "First build may take 1-2 minutes while player data is fetched; subsequent builds are instant.")

        # ── HR POWER PICKS ────────────────────────────────────────────────────
        st.markdown(
            "<hr style='margin:2rem 0 1.5rem;border-color:rgba(255,255,255,0.07);'>",
            unsafe_allow_html=True,
        )
        section("HR Power Picks")
        st.markdown("""
        <p style='color:var(--text-muted);font-size:0.82rem;line-height:1.6;max-width:680px;margin-bottom:1rem;'>
        Ranks today's hitters by home run probability using <strong style='color:var(--text-primary)'>six combined factors</strong>:
        recent HR rate, batter vs. pitcher career history, starting pitcher HR/9,
        park HR factor, weather (temperature + wind), and handedness platoon split.
        </p>""", unsafe_allow_html=True)

        if st.button("Build HR Power Picks", type="primary", key="mlb_hr_picks_btn"):
            st.session_state["mlb_hr_picks_built"] = True
            _build_hr_power_picks.clear()
            _score_hitter_for_hr.clear()

        if st.session_state.get("mlb_hr_picks_built"):
            with st.spinner("Scoring today's hitters for HR probability…"):
                _hr_cal_factor = _load_calibration("MLB").get("Home Runs", 0.48)
                _hr_picks = _build_hr_power_picks(top_n=6, cal_factor=_hr_cal_factor)

            # Cache in session state so the blog tab can read the top pick instantly
            if _hr_picks:
                st.session_state["hr_power_picks_cache"] = _hr_picks

            if not _hr_picks:
                st.warning("No games found for today or player data unavailable. Try again closer to game time.")
            else:
                # ── Top 4 pick cards ──────────────────────────────────────────
                _rank_labels = ["#1", "#2", "#3", "#4"]
                _hr_cols = st.columns(2)
                for _ri, _pick in enumerate(_hr_picks[:4]):
                    _col = _hr_cols[_ri % 2]
                    _pf = _pick["park_factor"]
                    _pf_color = "#22c55e" if _pf >= 110 else ("#f59e0b" if _pf >= 100 else "#f87171")
                    _p9 = _pick["pitcher_hr9"]
                    _p9_color = "#f87171" if _p9 >= 1.4 else ("#f59e0b" if _p9 >= 1.0 else "#22c55e")
                    _tmp = _pick.get("temp_f", 70)
                    _wnd = _pick.get("wind_mph", 0)
                    _weather_str = (f"{_tmp:.0f}°F · {_wnd:.0f} mph wind"
                                    if _pick.get("venue") not in _MLB_DOME_VENUES
                                    else f"{_tmp:.0f}°F (dome)")
                    _bvp = _pick.get("bvp_info", {})
                    _bvp_str = (f"{_bvp.get('hr', 0)} HR in {_bvp.get('ab', 0)} AB vs pitcher"
                                if _bvp.get("ab", 0) >= 10 else "No BvP data (< 10 AB)")
                    _l10 = _pick.get("last10_hr", 0)
                    _l20 = _pick.get("last20_hr", 0)
                    _score_pct = int(_pick["score"] * 100)
                    _plat = _pick.get("platoon_boost", 0)
                    _plat_str = "✓ Platoon adv." if _plat > 0 else "Same hand"
                    _col.markdown(
                        f"<div style='background:var(--card-bg);border:1px solid var(--border);"
                        f"border-top:3px solid #f59e0b;border-radius:12px;"
                        f"padding:1.1rem 1.2rem;margin-bottom:1rem;'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.6rem;'>"
                        f"<div>"
                        f"<span style='font-size:0.58rem;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;"
                        f"color:#f59e0b;display:block;margin-bottom:0.2rem;'>{_rank_labels[_ri]} · HR Power Pick</span>"
                        f"<span style='font-size:1.05rem;font-weight:700;color:var(--text-primary);'>{_pick['player_name']}</span>"
                        f"<span style='font-size:0.75rem;color:var(--text-muted);margin-left:0.5rem;'>{_pick['team']}</span>"
                        f"</div>"
                        f"<div style='text-align:right;'>"
                        f"<span style='font-size:1.5rem;font-weight:800;color:#f59e0b;'>{_score_pct}%</span>"
                        f"<span style='font-size:0.6rem;color:var(--text-muted);display:block;'>HR prob. (cal.)</span>"
                        f"</div></div>"
                        f"<div style='font-size:0.72rem;color:var(--text-muted);margin-bottom:0.55rem;'>"
                        f"vs <strong style='color:var(--text-primary);'>{_pick['opp_pitcher']}</strong>"
                        f" &nbsp;·&nbsp; {_pick['game_label']}"
                        f"</div>"
                        f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:0.35rem 1rem;font-size:0.72rem;'>"
                        f"<div><span style='color:var(--text-muted);'>HR last 10g</span> "
                        f"<strong style='color:var(--text-primary);'>{_l10} HR</strong></div>"
                        f"<div><span style='color:var(--text-muted);'>HR last 20g</span> "
                        f"<strong style='color:var(--text-primary);'>{_l20} HR</strong></div>"
                        f"<div><span style='color:var(--text-muted);'>Pitcher HR/9</span> "
                        f"<strong style='color:{_p9_color};'>{_p9:.2f}</strong></div>"
                        f"<div><span style='color:var(--text-muted);'>Park factor</span> "
                        f"<strong style='color:{_pf_color};'>{_pf}</strong></div>"
                        f"<div><span style='color:var(--text-muted);'>Weather</span> "
                        f"<strong style='color:var(--text-primary);'>{_weather_str}</strong></div>"
                        f"<div><span style='color:var(--text-muted);'>Matchup</span> "
                        f"<strong style='color:var(--text-primary);'>{_plat_str}</strong></div>"
                        f"<div style='grid-column:1/-1;'><span style='color:var(--text-muted);'>BvP</span> "
                        f"<strong style='color:var(--text-primary);'>{_bvp_str}</strong></div>"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )

                # ── Auto-build HR parlay from top picks ───────────────────────
                st.markdown("<p class='pl-section-label' style='margin-top:1.25rem;'>HR Parlay from Top Picks</p>",
                            unsafe_allow_html=True)
                _hr_legs = []
                for _pick in _hr_picks[:4]:
                    _hr_legs.append({
                        "player_name": _pick["player_name"],
                        "team":        _pick["team"],
                        "stat_type":   "Home Runs",
                        "line_score":  0.5,
                        "odds_type":   "standard",
                        "american_odds": -110,
                        "implied_prob": _PP_ODDS_IMPLIED["standard"],
                        "sportsbook":  "PrizePicks",
                        "game_id":     _pick.get("game_label", ""),
                        "game_label":  _pick.get("game_label", ""),
                        "hit_rate":    _pick["score"],
                        "sample_n":    _pick.get("games_played", 10),
                    })
                _hr_safe, _hr_value = _build_parlays(
                    _hr_legs, min_legs=2, max_legs=4, sportsbook="PrizePicks",
                    parlay_cal=parlay_tracker.get_parlay_calibration("MLB"),
                )
                _hc1, _hc2 = st.columns(2)
                with _hc1:
                    st.markdown("<p class='pl-section-label'>Safe HR Parlays</p>", unsafe_allow_html=True)
                    if _hr_safe:
                        for _hp in _hr_safe[:3]:
                            st.markdown(_parlay_card_html(_hp, "safe"), unsafe_allow_html=True)
                    else:
                        st.caption("Not enough data for HR parlays today.")
                with _hc2:
                    st.markdown("<p class='pl-section-label'>Value HR Parlays</p>", unsafe_allow_html=True)
                    if _hr_value:
                        for _hp in _hr_value[:3]:
                            st.markdown(_parlay_card_html(_hp, "value"), unsafe_allow_html=True)
                    else:
                        st.caption("No additional value parlays.")

                try:
                    _hr_logged = parlay_tracker.log_parlays(_hr_safe, "MLB", "PrizePicks", kind="safe")
                    _hr_logged += parlay_tracker.log_parlays(_hr_value, "MLB", "PrizePicks", kind="value")
                    if _hr_logged:
                        st.caption(f"Logged {_hr_logged} HR parlay(s) to accuracy tracker.")
                except Exception:
                    pass

    # ── MLB DAILY BLOG ────────────────────────────────────────────────────────
    with tab_blog_mlb:
        with st.spinner("Generating today's MLB brief..."):
            blog_html_mlb = generate_mlb_blog()
        st.markdown(blog_html_mlb, unsafe_allow_html=True)

    # ── MLB ACCURACY ──────────────────────────────────────────────────────────
    if _IS_LOCAL and tab_accuracy_mlb is not None:
        with tab_accuracy_mlb:
            try:
                _render_accuracy_tab("MLB")
            except Exception as _acc_err:
                import traceback
                st.error(f"Accuracy tab error: {_acc_err}")
                st.code(traceback.format_exc())

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
# END OF FILE — orphaned block removed
