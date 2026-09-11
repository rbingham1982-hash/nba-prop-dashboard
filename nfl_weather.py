"""
nfl_weather.py — forecast wind for NFL games, and the honest re-test of the wind signal.

The wind column in nfldata's games.csv is populated only AFTER a game is played: 271
unplayed 2026 games carry no wind at all. So the historical wind result — outdoor unders
hitting 58-59% in the 10-15mph band — was built on information unavailable at bet time.
It is a look-ahead artifact until proven otherwise, in the same class as scoring a prop
against a game log that already contains the game.

This module supplies what a bettor could actually have known: the FORECAST. Open-Meteo
archives past forecasts as well as reanalysis actuals, so the same 2022-2025 games can be
re-tested on forecast wind rather than measured wind, without waiting a season.

The attenuation is real and visible in a single spot check — Buffalo, 17 Nov 2025, actual
winds 18/17/16/17 mph against a forecast of 14/14/15/16. A forecast that systematically
understates gusty days will push games out of the high-wind bucket, and the edge shrinks
with it.

Both endpoints are free and keyless:
    archive-api.open-meteo.com            reanalysis "what actually happened"
    historical-forecast-api.open-meteo.com archived forecasts, ~2022 onward
"""
from __future__ import annotations

_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_ARCHIVE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
_FORECAST = "https://api.open-meteo.com/v1/forecast"

# Home stadium coordinates by team. Only outdoor venues matter — a dome game has no wind
# effect and games.csv already carries roof, so indoor teams are here only for completeness
# and are filtered by roof at the call site.
STADIUM = {
    "BUF": (42.7738, -78.7870), "MIA": (25.9580, -80.2389), "NE": (42.0909, -71.2643),
    "NYJ": (40.8128, -74.0742), "NYG": (40.8128, -74.0742), "BAL": (39.2780, -76.6227),
    "CIN": (39.0955, -84.5161), "CLE": (41.5061, -81.6995), "PIT": (40.4468, -80.0158),
    "JAX": (30.3239, -81.6373), "TEN": (36.1665, -86.7713), "DEN": (39.7439, -105.0201),
    "KC": (39.0489, -94.4839), "PHI": (39.9008, -75.1675), "WAS": (38.9076, -76.8645),
    "CHI": (41.8623, -87.6167), "GB": (44.5013, -88.0622), "CAR": (35.2258, -80.8528),
    "TB": (27.9759, -82.5033), "SEA": (47.5952, -122.3316), "SF": (37.4033, -121.9694),
    "LA": (33.9535, -118.3392), "LAC": (33.9535, -118.3392), "LV": (36.0909, -115.1833),
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4009), "NO": (29.9511, -90.0812),
    "DET": (42.3400, -83.0456), "MIN": (44.9738, -93.2578), "IND": (39.7601, -86.1639),
    "HOU": (29.6847, -95.4107), "DAL": (32.7473, -97.0945),
}

_cache: dict = {}


def _wind_at(lat, lon, date_str, hour, url) -> float | None:
    """Wind speed in mph at a given local hour, from one of the Open-Meteo endpoints."""
    import requests
    key = (url, round(lat, 3), round(lon, 3), date_str)
    series = _cache.get(key)
    if series is None:
        try:
            r = requests.get(url, params={
                "latitude": lat, "longitude": lon,
                "start_date": date_str, "end_date": date_str,
                "hourly": "wind_speed_10m", "wind_speed_unit": "mph",
                "timezone": "America/New_York",
            }, timeout=30)
            if r.status_code != 200:
                _cache[key] = []
                return None
            series = r.json().get("hourly", {}).get("wind_speed_10m") or []
        except Exception:
            series = []
        _cache[key] = series
    if not series or hour is None or hour >= len(series):
        return None
    v = series[hour]
    return float(v) if v is not None else None


def forecast_wind(team: str, date_str: str, hour: int = 13) -> float | None:
    """Archived FORECAST wind for a past game — what was knowable before kickoff."""
    ll = STADIUM.get(str(team).upper())
    return _wind_at(ll[0], ll[1], date_str, hour, _FORECAST_ARCHIVE) if ll else None


def actual_wind(team: str, date_str: str, hour: int = 13) -> float | None:
    """Reanalysis wind — what actually happened. For validating the pipeline only."""
    ll = STADIUM.get(str(team).upper())
    return _wind_at(ll[0], ll[1], date_str, hour, _ARCHIVE) if ll else None


def upcoming_wind(team: str, date_str: str, hour: int = 13) -> float | None:
    """Live forecast for a game that has not happened yet."""
    ll = STADIUM.get(str(team).upper())
    return _wind_at(ll[0], ll[1], date_str, hour, _FORECAST) if ll else None


def _kick_hour(gametime) -> int:
    try:
        return int(str(gametime).split(":")[0])
    except Exception:
        return 13


def attach_wind(df, kind: str = "forecast", limit: int | None = None):
    """
    Add a wind column sourced from Open-Meteo to a games frame.

    One request per (stadium, date) thanks to the cache, so a season of outdoor games costs
    a few hundred calls rather than one per row.
    """
    fn = {"forecast": forecast_wind, "actual": actual_wind}[kind]
    out = df.copy()
    vals = []
    for i, (_, g) in enumerate(out.iterrows()):
        if limit is not None and i >= limit:
            vals.append(None)
            continue
        vals.append(fn(g["home_team"], str(g["gameday"])[:10], _kick_hour(g.get("gametime"))))
    out[f"wind_{kind}"] = vals
    return out
