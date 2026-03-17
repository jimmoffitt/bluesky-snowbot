"""
SnowService — facade that wires resolver → SnoCountry + Open-Meteo → models.

Provider strategy:
  1. Open-Meteo is always called for weather conditions (temp, wind, sky, snow depth).
  2. SnoCountry is called when SNOCOUNTRY_KEY is set and the resolved resort has a
     known snocountry_id. If SnoCountry fails (network error, maintenance) the bot
     continues with Open-Meteo data and logs a warning.

The formatter decides how to present the data depending on which fields are populated
in the returned SnowReport.

Usage:
    from bluesky_snowbot.snow.service import SnowService
    from bluesky_snowbot.storage.db import Database

    with Database("data/snowbot.db") as db:
        svc = SnowService(db, snocountry_key="YOUR_KEY")
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
from bluesky_snowbot.snow.snocountry_client import SnoCountryClient

logger = logging.getLogger(__name__)


class SnowService:

    def __init__(
        self,
        db,
        cache_ttl_minutes: int = 30,
        snocountry_key: Optional[str] = None,
    ) -> None:
        self._db            = db
        self._cache_ttl     = cache_ttl_minutes
        self._resolver      = SnowResolver()
        self._openmeteo     = SnowClient()
        self._snocountry    = SnoCountryClient(snocountry_key) if snocountry_key else None

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
        cached = self._db.get_cached_snow(location.lat, location.lon)
        if cached:
            logger.debug("[service] Cache hit for %s", location.name)
            return _deserialize_report(cached, location)

        report = self._build_report(location)

        self._db.save_cached_snow(
            lat=location.lat,
            lon=location.lon,
            display_name=location.name,
            report_json=_serialize_report(report),
            ttl_minutes=self._cache_ttl,
        )
        return report

    def _build_report(self, location: ResortLocation) -> SnowReport:
        # Always fetch Open-Meteo for weather conditions
        logger.info("[service] Fetching Open-Meteo data for %s", location.name)
        raw = self._openmeteo.fetch(location.lat, location.lon)
        snow, conditions = _parse_open_meteo(raw, location)

        # Attempt SnoCountry if configured and resort has a known ID
        operations = None
        if self._snocountry and location.snocountry_id:
            logger.info("[service] Fetching SnoCountry data for %s (id=%s)",
                        location.name, location.snocountry_id)
            operations = self._snocountry.fetch_resort(location.snocountry_id)
            if operations is None:
                logger.warning("[service] SnoCountry returned no data for %s — using Open-Meteo only",
                               location.name)
        elif self._snocountry and not location.snocountry_id:
            logger.debug("[service] No SnoCountry ID for %s — Open-Meteo only", location.name)

        data_source = "SnoCountry+Open-Meteo" if operations else "Open-Meteo"
        return SnowReport(
            location=location,
            snow=snow,
            conditions=conditions,
            operations=operations,
            data_source=data_source,
        )


# ---------------------------------------------------------------------------
# Open-Meteo response parsing
# ---------------------------------------------------------------------------

def _parse_open_meteo(data: dict, location: ResortLocation) -> tuple[SnowDepthReport, ConditionsReport]:
    hourly = data.get("hourly", {})
    idx    = SnowClient.extract_current_index(data)

    def _get(key: str, default=0.0):
        vals = hourly.get(key, [])
        v = vals[idx] if idx < len(vals) else None
        return v if v is not None else default

    snow = SnowDepthReport(
        depth_cm   = _get("snow_depth", 0) * 100,   # metres → cm
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

    return snow, conditions


# ---------------------------------------------------------------------------
# Cache serialization
# ---------------------------------------------------------------------------

def _serialize_report(report: SnowReport) -> dict:
    ops = report.operations
    return {
        # Open-Meteo snow
        "depth_cm":       report.snow.depth_cm,
        "new_24h_cm":     report.snow.new_24h_cm,
        "new_48h_cm":     report.snow.new_48h_cm,
        "new_72h_cm":     report.snow.new_72h_cm,
        # Open-Meteo conditions
        "temp_c":         report.conditions.temp_c,
        "wind_speed_kmh": report.conditions.wind_speed_kmh,
        "wind_gusts_kmh": report.conditions.wind_gusts_kmh,
        "weather_code":   report.conditions.weather_code,
        "weather_desc":   report.conditions.weather_desc,
        "visibility_km":  report.conditions.visibility_km,
        # SnoCountry operations (None when not available)
        "operations": {
            "operating_status":      ops.operating_status,
            "resort_status_code":    ops.resort_status_code,
            "base_depth_min_cm":     ops.base_depth_min_cm,
            "base_depth_max_cm":     ops.base_depth_max_cm,
            "new_snow_24h_min_cm":   ops.new_snow_24h_min_cm,
            "new_snow_24h_max_cm":   ops.new_snow_24h_max_cm,
            "snow_48h_cm":           ops.snow_48h_cm,
            "season_total_cm":       ops.season_total_cm,
            "last_snowfall_date":    ops.last_snowfall_date,
            "last_snowfall_amount":  ops.last_snowfall_amount,
            "prev_snowfall_date":    ops.prev_snowfall_date,
            "prev_snowfall_amount":  ops.prev_snowfall_amount,
            "predicted_snow_24h":    ops.predicted_snow_24h,
            "predicted_snow_48h":    ops.predicted_snow_48h,
            "predicted_snow_72h":    ops.predicted_snow_72h,
            "open_lifts":            ops.open_lifts,
            "total_lifts":           ops.total_lifts,
            "open_trails":           ops.open_trails,
            "total_trails":          ops.total_trails,
            "open_acres":            ops.open_acres,
            "total_acres":           ops.total_acres,
            "open_percent":          ops.open_percent,
            "surface_condition":     ops.surface_condition,
            "snow_comments":         ops.snow_comments,
            "weekday_hours":         ops.weekday_hours,
            "weekend_hours":         ops.weekend_hours,
            "trail_map_url":         ops.trail_map_url,
            "webcam_url":            ops.webcam_url,
            "website_url":           ops.website_url,
            "report_datetime":       ops.report_datetime,
        } if ops else None,
        "data_source": report.data_source,
        "fetched_at":  report.fetched_at.isoformat(),
    }


def _deserialize_report(data: dict, location: ResortLocation) -> SnowReport:
    from bluesky_snowbot.snow.models import ResortOperations
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

    ops_data = data.get("operations")
    operations = None
    if ops_data:
        operations = ResortOperations(**ops_data)

    fetched_at = datetime.fromisoformat(data["fetched_at"]) if "fetched_at" in data else datetime.utcnow()
    return SnowReport(
        location=location,
        snow=snow,
        conditions=conditions,
        operations=operations,
        fetched_at=fetched_at,
        data_source=data.get("data_source", "Open-Meteo"),
    )
