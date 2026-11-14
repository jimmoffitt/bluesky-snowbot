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
    """A resolved snow resort or mountain location."""
    name: str           # Display name, e.g. "Vail Ski Resort"
    lat: float
    lon: float
    state: str          # Two-letter state/province/region code, e.g. "CO"
    country: str = "US"
    elevation_m: Optional[float] = None
    postal_code: Optional[str] = None     # ZIP / postal code for the resort area
    snocountry_id: Optional[int] = None   # SnoCountry numeric resort ID
    snotel_triplet: Optional[str] = None  # AWDB station triplet, e.g. "842:CO:SNTL"
    snotel_dist_mi: Optional[float] = None  # Distance from resort to SNOTEL station


@dataclass
class SnowDepthReport:
    """
    Snow depth and accumulation data from Open-Meteo.
    Used as fallback when SnoCountry data is unavailable.
    All values in cm.
    """
    depth_cm: float           # Current total snow depth on ground
    new_24h_cm: float         # Snowfall in the last 24 hours
    new_48h_cm: float         # Snowfall in the last 48 hours
    new_72h_cm: float         # Snowfall in the last 72 hours


@dataclass
class ResortOperations:
    """
    Resort-reported conditions from SnoCountry.
    All depth/snowfall values stored in cm (converted from inches).
    String fields (lastSnowFallAmount, etc.) may contain ranges like "8 - 12"
    and are kept as-is for display.
    """
    operating_status: str                    # "Open", "Closed", "Opening Soon", etc.
    resort_status_code: Optional[int] = None

    # Base depth (resort-reported, cm)
    base_depth_min_cm: Optional[float] = None
    base_depth_max_cm: Optional[float] = None

    # New snow (resort-reported, cm)
    new_snow_24h_min_cm: Optional[float] = None
    new_snow_24h_max_cm: Optional[float] = None
    snow_48h_cm: Optional[float] = None
    season_total_cm: Optional[float] = None

    # Last reported snowfall (kept as strings — may be ranges)
    last_snowfall_date: Optional[str] = None
    last_snowfall_amount: Optional[str] = None   # e.g. "8 - 12 inches"
    prev_snowfall_date: Optional[str] = None
    prev_snowfall_amount: Optional[str] = None

    # Snowfall forecast (resort-provided strings, inches)
    predicted_snow_24h: Optional[str] = None
    predicted_snow_48h: Optional[str] = None
    predicted_snow_72h: Optional[str] = None

    # Lifts and trails
    open_lifts: Optional[int] = None
    total_lifts: Optional[int] = None
    open_trails: Optional[int] = None
    total_trails: Optional[int] = None
    open_acres: Optional[float] = None
    total_acres: Optional[float] = None
    open_percent: Optional[float] = None

    # Surface
    surface_condition: Optional[str] = None  # e.g. "Packed Powder"
    snow_comments: Optional[str] = None      # free-text resort comment

    # Hours
    weekday_hours: Optional[str] = None
    weekend_hours: Optional[str] = None

    # Media
    trail_map_url: Optional[str] = None
    webcam_url: Optional[str] = None
    website_url: Optional[str] = None

    # Report timestamp
    report_datetime: Optional[str] = None


@dataclass
class DailyForecast:
    """
    One day of forecast (or history) data from Open-Meteo daily variables.
    All temperatures in °C, snowfall/precip in cm, wind in km/h.
    """
    date: str               # ISO date string e.g. "2026-03-20"
    snowfall_cm: float
    temp_max_c: float
    temp_min_c: float
    weather_code: int
    weather_desc: str
    precip_cm: float
    wind_max_kmh: float
    is_forecast: bool = True   # False for past days, True for today and future


@dataclass
class ConditionsReport:
    """Current weather conditions from Open-Meteo."""
    temp_c: float
    wind_speed_kmh: float
    wind_gusts_kmh: float
    weather_code: int         # WMO weather interpretation code
    weather_desc: str         # Human-readable description
    visibility_km: Optional[float] = None


@dataclass
class SnotelReading:
    """
    Current SNOTEL station readings (snow depth + SWE) from the NRCS AWDB API.
    Values stored in both inches (source units) and cm (display).
    """
    triplet: str                          # e.g. "780:CO:SNTL"
    station_name: str
    elevation_ft: int
    snow_depth_in: Optional[float] = None  # SNWD — inches on ground
    swe_in: Optional[float] = None         # WTEQ — snow water equivalent
    snow_depth_cm: Optional[float] = None
    swe_cm: Optional[float] = None


@dataclass
class SnowReport:
    """
    Complete snow conditions report for a single resort.
    This is the canonical data object passed to SnowFormatter.

    operations is populated when a SnoCountry key is configured and
    the resort has a known snocountry_id. When present it takes
    precedence over snow (depth/accumulation) in the formatter.

    snotel is populated when the resort has a known snotel_triplet.
    It provides observed snow depth and SWE from the nearest SNOTEL station.
    """
    location: ResortLocation
    snow: SnowDepthReport              # Open-Meteo (fallback / weather supplement)
    conditions: ConditionsReport       # Open-Meteo (temp, wind, sky)
    operations: Optional[ResortOperations] = None   # SnoCountry (resort-reported)
    snotel: Optional[SnotelReading] = None          # NRCS AWDB (observed snow depth/SWE)

    # Set when live data couldn't be fetched and a recent cached report was used
    stale_fetched_at: Optional[str] = None                     # ISO UTC time of the cached fetch
    stale_error: Optional[BaseException] = field(default=None, repr=False, compare=False)
    forecast: list = field(default_factory=list)    # list[DailyForecast] — 7-day outlook
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


def wmo_emoji(code: int) -> str:
    """Single emoji summarising a WMO weather code — used in compact forecast rows."""
    if code == 0:                    return "☀️"
    if code in (1, 2):               return "⛅"
    if code == 3:                    return "☁️"
    if code in (45, 48):             return "🌫️"
    if 51 <= code <= 67:             return "🌧️"
    if code in (71, 73, 75, 77):     return "❄️"
    if code in (80, 81, 82):         return "🌧️"
    if code in (85, 86):             return "🌨️"
    if code in (95, 96, 99):         return "⛈️"
    return "🌤️"
