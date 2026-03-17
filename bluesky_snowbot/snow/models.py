"""
Data models for the SnowBot snow domain.

SnowReport is the canonical output of SnowService.lookup().
It is the only object passed to SnowFormatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ResortLocation:
    """A resolved ski resort or mountain location."""
    name: str           # Display name, e.g. "Vail Ski Resort"
    lat: float
    lon: float
    state: str          # Two-letter state/province code, e.g. "CO"
    country: str = "US"
    elevation_m: Optional[float] = None


@dataclass
class SnowDepthReport:
    """Current and recent snowfall data."""
    depth_cm: float           # Current total snow depth on ground
    new_24h_cm: float         # Snowfall in the last 24 hours
    new_48h_cm: float         # Snowfall in the last 48 hours
    new_72h_cm: float         # Snowfall in the last 72 hours


@dataclass
class ConditionsReport:
    """Current weather conditions at the resort."""
    temp_c: float
    wind_speed_kmh: float
    wind_gusts_kmh: float
    weather_code: int         # WMO weather interpretation code
    weather_desc: str         # Human-readable description
    visibility_km: Optional[float] = None


@dataclass
class SnowReport:
    """
    Complete snow conditions report for a single resort.
    This is the canonical data object passed to SnowFormatter.
    """
    location: ResortLocation
    snow: SnowDepthReport
    conditions: ConditionsReport
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    data_source: str = "Open-Meteo"


# ---------------------------------------------------------------------------
# WMO weather code → human-readable description
# Reference: https://open-meteo.com/en/docs
# ---------------------------------------------------------------------------

WMO_DESCRIPTIONS: dict[int, str] = {
    0:  "Clear sky",
    1:  "Mostly clear",
    2:  "Partly cloudy",
    3:  "Overcast",
    45: "Foggy",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm w/ slight hail",
    99: "Thunderstorm w/ heavy hail",
}


def wmo_description(code: int) -> str:
    return WMO_DESCRIPTIONS.get(code, f"Unknown (WMO {code})")
