"""
SnowService — facade that wires resolver → client → models.

This is the single entry point for snow lookups.

Usage:
    from bluesky_snowbot.snow.service import SnowService
    from bluesky_snowbot.storage.db import Database

    with Database("data/snowbot.db") as db:
        svc = SnowService(db)
        report = svc.lookup("Vail")   # → SnowReport
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from bluesky_snowbot.snow.client import SnowClient
from bluesky_snowbot.snow.models import (
    ConditionsReport, ResortLocation, SnowDepthReport, SnowReport, wmo_description,
)
from bluesky_snowbot.snow.resolver import SnowResolver

logger = logging.getLogger(__name__)


class SnowService:

    def __init__(self, db, cache_ttl_minutes: int = 30) -> None:
        self._db               = db
        self._cache_ttl        = cache_ttl_minutes
        self._resolver         = SnowResolver()
        self._client           = SnowClient()

    def lookup(self, raw_location: str) -> SnowReport:
        """
        Main entry point: raw location string → SnowReport.
        Raises ValueError if the location cannot be resolved.
        """
        location = self._resolver.resolve(raw_location)
        return self._fetch_report(location)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_report(self, location: ResortLocation) -> SnowReport:
        # Check DB cache first
        cached = self._db.get_cached_snow(location.lat, location.lon)
        if cached:
            logger.debug("[service] Cache hit for %s", location.name)
            return _deserialize_report(cached, location)

        # Fetch from Open-Meteo
        logger.info("[service] Fetching snow data for %s (%.4f, %.4f)",
                    location.name, location.lat, location.lon)
        raw = self._client.fetch(location.lat, location.lon)
        report = _parse_open_meteo(raw, location)

        # Cache the result
        self._db.save_cached_snow(
            lat=location.lat,
            lon=location.lon,
            display_name=location.name,
            report_json=_serialize_report(report),
            ttl_minutes=self._cache_ttl,
        )

        return report


# ---------------------------------------------------------------------------
# Open-Meteo response parsing
# ---------------------------------------------------------------------------

def _parse_open_meteo(data: dict, location: ResortLocation) -> SnowReport:
    hourly = data.get("hourly", {})
    idx    = SnowClient.extract_current_index(data)

    def _get(key: str, default=0.0):
        vals = hourly.get(key, [])
        v = vals[idx] if idx < len(vals) else None
        return v if v is not None else default

    snow = SnowDepthReport(
        depth_cm   = _get("snow_depth", 0) * 100,   # API returns metres → cm
        new_24h_cm = SnowClient.sum_snowfall_hours(data, idx, 24),
        new_48h_cm = SnowClient.sum_snowfall_hours(data, idx, 48),
        new_72h_cm = SnowClient.sum_snowfall_hours(data, idx, 72),
    )

    weather_code = int(_get("weather_code", 0))
    vis_raw = hourly.get("visibility", [])
    vis_km  = (vis_raw[idx] / 1000.0) if idx < len(vis_raw) and vis_raw[idx] is not None else None

    conditions = ConditionsReport(
        temp_c         = _get("temperature_2m"),
        wind_speed_kmh = _get("wind_speed_10m"),
        wind_gusts_kmh = _get("wind_gusts_10m"),
        weather_code   = weather_code,
        weather_desc   = wmo_description(weather_code),
        visibility_km  = vis_km,
    )

    return SnowReport(location=location, snow=snow, conditions=conditions)


# ---------------------------------------------------------------------------
# Cache serialization helpers
# ---------------------------------------------------------------------------

def _serialize_report(report: SnowReport) -> dict:
    return {
        "depth_cm":       report.snow.depth_cm,
        "new_24h_cm":     report.snow.new_24h_cm,
        "new_48h_cm":     report.snow.new_48h_cm,
        "new_72h_cm":     report.snow.new_72h_cm,
        "temp_c":         report.conditions.temp_c,
        "wind_speed_kmh": report.conditions.wind_speed_kmh,
        "wind_gusts_kmh": report.conditions.wind_gusts_kmh,
        "weather_code":   report.conditions.weather_code,
        "weather_desc":   report.conditions.weather_desc,
        "visibility_km":  report.conditions.visibility_km,
        "fetched_at":     report.fetched_at.isoformat(),
    }


def _deserialize_report(data: dict, location: ResortLocation) -> SnowReport:
    snow = SnowDepthReport(
        depth_cm   = data["depth_cm"],
        new_24h_cm = data["new_24h_cm"],
        new_48h_cm = data["new_48h_cm"],
        new_72h_cm = data["new_72h_cm"],
    )
    conditions = ConditionsReport(
        temp_c         = data["temp_c"],
        wind_speed_kmh = data["wind_speed_kmh"],
        wind_gusts_kmh = data["wind_gusts_kmh"],
        weather_code   = data["weather_code"],
        weather_desc   = data["weather_desc"],
        visibility_km  = data.get("visibility_km"),
    )
    fetched_at = datetime.fromisoformat(data["fetched_at"]) if "fetched_at" in data else datetime.utcnow()
    return SnowReport(location=location, snow=snow, conditions=conditions, fetched_at=fetched_at)
