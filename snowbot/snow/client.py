"""
Open-Meteo API client for snow data.

Fetches snowfall, snow depth, temperature, and wind data for a given
lat/lon coordinate. Uses the free Open-Meteo API (no key required).

API docs: https://open-meteo.com/en/docs

Usage:
    from snowbot.snow.client import SnowClient
    data = SnowClient().fetch(lat=39.6433, lon=-106.3781)
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Hourly variables — current conditions + 72h accumulation history
HOURLY_VARS = [
    "temperature_2m",
    "snow_depth",
    "snowfall",
    "wind_speed_10m",
    "wind_gusts_10m",
    "weather_code",
    "visibility",
]

# Daily variables — 3-day history + 7-day forecast
DAILY_VARS = [
    "snowfall_sum",
    "temperature_2m_max",
    "temperature_2m_min",
    "weather_code",
    "precipitation_sum",
    "wind_speed_10m_max",
]

REQUEST_TIMEOUT = 15  # seconds


class SnowClient:
    """
    Thin wrapper around Open-Meteo's forecast API.
    Returns raw dicts; SnowService is responsible for interpretation.
    """

    def fetch(self, lat: float, lon: float, past_days: int = 3) -> dict:
        """
        Fetch current conditions + past 3 days of hourly data for lat/lon.

        Returns the raw Open-Meteo JSON response dict.
        Raises requests.RequestException on network errors.
        """
        params = {
            "latitude":       round(lat, 4),
            "longitude":      round(lon, 4),
            "hourly":         ",".join(HOURLY_VARS),
            "daily":          ",".join(DAILY_VARS),
            "past_days":      past_days,
            "forecast_days":  8,   # today + 7 days ahead
            "timezone":       "auto",
        }
        resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("[client] Fetched Open-Meteo data for (%.4f, %.4f)", lat, lon)
        return data

    @staticmethod
    def extract_current_index(data: dict) -> int:
        """
        Returns the index into the hourly arrays that corresponds to the
        most recent completed hour.
        """
        from datetime import datetime, timezone
        times = data.get("hourly", {}).get("time", [])
        if not times:
            return 0
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        # Find the last entry at or before now
        for i in range(len(times) - 1, -1, -1):
            if times[i] <= now_str:
                return i
        return 0

    @staticmethod
    def sum_snowfall_hours(data: dict, current_idx: int, hours_back: int) -> float:
        """Sum snowfall (cm) over the past N hours ending at current_idx."""
        snowfall = data.get("hourly", {}).get("snowfall", [])
        start = max(0, current_idx - hours_back + 1)
        values = [v for v in snowfall[start:current_idx + 1] if v is not None]
        return round(sum(values), 1)
