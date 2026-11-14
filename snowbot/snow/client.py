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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

# Fail fast and retry: from the Pi, connections to Open-Meteo sometimes stall
# in the TCP/TLS handshake while the next attempt succeeds right away.
CONNECT_TIMEOUT = 4    # seconds to establish the connection
READ_TIMEOUT    = 8    # seconds to wait for the response
RETRIES         = 2    # extra attempts after the first (3 tries in all)
BACKOFF         = 0.5  # seconds; waits 0.5s, then 1s between tries


def _make_session() -> requests.Session:
    """A keep-alive session that retries stalled connects/reads and 5xx replies."""
    retry = Retry(
        total=RETRIES, connect=RETRIES, read=RETRIES, status=RETRIES,
        backoff_factor=BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class SnowClient:
    """
    Thin wrapper around Open-Meteo's forecast API.
    Returns raw dicts; SnowService is responsible for interpretation.
    """

    def __init__(self) -> None:
        self._session = _make_session()

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
        resp = self._session.get(BASE_URL, params=params,
                                 timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        resp.raise_for_status()
        data = resp.json()
        logger.debug("[client] Fetched Open-Meteo data for (%.4f, %.4f)", lat, lon)
        return data

    def fetch_many(self, coords: list[tuple[float, float]], past_days: int = 3,
                   timeout: tuple[float, float] = (6, 45)) -> list[dict]:
        """
        Fetch the same data as fetch() for several locations in one request.
        Returns one response dict per coordinate, in order. Used by the
        background refresh, which can afford a longer timeout than a user.
        """
        params = {
            "latitude":       ",".join(f"{lat:.4f}" for lat, _ in coords),
            "longitude":      ",".join(f"{lon:.4f}" for _, lon in coords),
            "hourly":         ",".join(HOURLY_VARS),
            "daily":          ",".join(DAILY_VARS),
            "past_days":      past_days,
            "forecast_days":  8,
            "timezone":       "auto",
        }
        resp = self._session.get(BASE_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        results = data if isinstance(data, list) else [data]
        if len(results) != len(coords):
            raise ValueError(f"Open-Meteo returned {len(results)} results for {len(coords)} locations")
        return results

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
