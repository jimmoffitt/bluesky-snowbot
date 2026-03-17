"""
SnoCountry API client.

Fetches resort-reported snow conditions from feeds.snocountry.net.
All returned depth/snowfall values are converted from inches to cm.
String fields (snowfall amounts, surface condition, comments) are
kept as-is since they may contain ranges like "8 - 12".

API overview:
  Base URL: http://feeds.snocountry.net/conditions.php
  Auth:     ?apiKey=YOUR_KEY
  Single resort:  &ids=RESORT_ID
  Resort list:    &action=getResortList
  Response:       {"items": [{...resort fields...}]}

All measurements in the API are imperial (inches, °F, feet, acres).

Usage:
    from bluesky_snowbot.snow.snocountry_client import SnoCountryClient
    client = SnoCountryClient(api_key="YOUR_KEY")
    ops = client.fetch_resort(resort_id=12345)   # → ResortOperations
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from bluesky_snowbot.snow.models import ResortOperations

logger = logging.getLogger(__name__)

BASE_URL = "http://feeds.snocountry.net/conditions.php"
REQUEST_TIMEOUT = 15


def _in_to_cm(inches) -> Optional[float]:
    """Convert inches (int, float, or None) to cm. Returns None if input is None."""
    if inches is None:
        return None
    try:
        return round(float(inches) * 2.54, 1)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class SnoCountryClient:

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch_resort(self, resort_id: int) -> Optional[ResortOperations]:
        """
        Fetch conditions for a single resort by SnoCountry ID.
        Returns a ResortOperations, or None if the resort is not found
        or the API returns an error.
        """
        params = {"apiKey": self._api_key, "ids": resort_id}
        try:
            resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("[snocountry] HTTP error for resort %s: %s", resort_id, exc)
            return None
        except ValueError as exc:
            logger.warning("[snocountry] JSON parse error for resort %s: %s", resort_id, exc)
            return None

        items = data.get("items", [])
        if not items:
            logger.warning("[snocountry] No items in response for resort %s", resort_id)
            return None

        return _parse_resort(items[0])

    def fetch_resort_list(self) -> list[dict]:
        """
        Fetch the full list of resorts available with this API key.
        Returns a list of dicts with keys: id, resortName, state, country, resortType.
        Useful for populating snocountry_id values in resolver.py.
        Run via: python scripts/fetch_resort_list.py
        """
        params = {"apiKey": self._api_key, "action": "getResortList"}
        resp = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])


def _parse_resort(r: dict) -> ResortOperations:
    """Parse a single SnoCountry resort dict into a ResortOperations."""

    # Base depth: API gives min/max in inches
    base_min_cm = _in_to_cm(_safe_float(r.get("avgBaseDepthMin")))
    base_max_cm = _in_to_cm(_safe_float(r.get("avgBaseDepthMax")))

    # New snow 24h: min/max in inches
    new24_min_cm = _in_to_cm(_safe_float(r.get("newSnowMin")))
    new24_max_cm = _in_to_cm(_safe_float(r.get("newSnowMax")))

    # 48h snowfall in inches
    snow48_cm = _in_to_cm(_safe_float(r.get("snowLast48Hours")))

    # Season total — may be a string like "142 inches" or a number
    season_total_cm = None
    season_raw = r.get("seasonTotal")
    if season_raw is not None:
        try:
            season_total_cm = _in_to_cm(float(str(season_raw).split()[0]))
        except (ValueError, IndexError):
            pass

    return ResortOperations(
        operating_status=str(r.get("operatingStatus") or "Unknown"),
        resort_status_code=_safe_int(r.get("resortStatus")),

        base_depth_min_cm=base_min_cm,
        base_depth_max_cm=base_max_cm,
        new_snow_24h_min_cm=new24_min_cm,
        new_snow_24h_max_cm=new24_max_cm,
        snow_48h_cm=snow48_cm,
        season_total_cm=season_total_cm,

        last_snowfall_date=r.get("lastSnowFallDate"),
        last_snowfall_amount=r.get("lastSnowFallAmount"),
        prev_snowfall_date=r.get("prevSnowFallDate"),
        prev_snowfall_amount=r.get("prevSnowFallAmount"),

        predicted_snow_24h=r.get("predictedSnowFall_24Hours"),
        predicted_snow_48h=r.get("predictedSnowFall_48Hours"),
        predicted_snow_72h=r.get("predictedSnowFall_72Hours"),

        open_lifts=_safe_int(r.get("openDownHillLifts")),
        total_lifts=_safe_int(r.get("maxOpenDownHillLifts")),
        open_trails=_safe_int(r.get("openDownHillTrails")),
        total_trails=_safe_int(r.get("maxOpenDownHillTrails")),
        open_acres=_safe_float(r.get("openDownHillAcres")),
        total_acres=_safe_float(r.get("maxOpenDownHillAcres")),
        open_percent=_safe_float(r.get("openDownHillPercent")),

        surface_condition=r.get("primarySurfaceCondition"),
        snow_comments=r.get("snowComments"),

        weekday_hours=r.get("weekdayHours"),
        weekend_hours=r.get("weekendHours"),

        trail_map_url=r.get("lgTrailMapURL"),
        webcam_url=r.get("webCamLink"),
        website_url=r.get("webSiteLink"),

        report_datetime=r.get("reportDateTime"),
    )
