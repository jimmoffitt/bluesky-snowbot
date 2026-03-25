"""
SNOTEL data client — fetches snow depth and SWE from the NRCS AWDB REST API.

Fetches two key variables for a given AWDB station triplet:
  SNWD  — Snow depth (inches)
  WTEQ  — Snow water equivalent (inches)

The AWDB API is free with no key required.

Usage:
    from bluesky_snowbot.snow.snotel_client import SnotelClient
    data = SnotelClient().fetch("780:CO:SNTL")
    # → SnotelReport or None if station has no data
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

AWDB_BASE = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
TIMEOUT = 15


@dataclass
class SnotelReport:
    """Current-day SNOTEL readings for a station."""
    triplet: str              # e.g. "780:CO:SNTL"
    station_name: str
    elevation_ft: int
    snow_depth_in: Optional[float]    # SNWD — inches on ground
    swe_in: Optional[float]           # WTEQ — snow water equivalent
    # cm conversions for convenience
    snow_depth_cm: Optional[float]
    swe_cm: Optional[float]


class SnotelClient:
    """
    Thin wrapper around the NRCS AWDB data REST API.
    Fetches current SNWD and WTEQ for a station triplet.
    """

    def fetch(self, triplet: str) -> Optional[SnotelReport]:
        """
        Fetch the most recent SNWD and WTEQ for the given station triplet.
        Returns None if the station has no data or the request fails.

        Daily SNOTEL data typically lags by one day (yesterday is the latest
        available reading). We look back up to 3 days to get the most recent
        non-null value.

        triplet format: "{stationId}:{stateCode}:{networkCode}"
        e.g. "780:CO:SNTL" or "MHP:CA:MSNT"
        """
        station_info = self._fetch_station_meta(triplet)
        if station_info is None:
            return None

        snwd, wteq = self._fetch_elements(triplet)

        snwd_cm = round(snwd * 2.54, 1) if snwd is not None else None
        wteq_cm = round(wteq * 2.54, 1) if wteq is not None else None

        return SnotelReport(
            triplet=triplet,
            station_name=station_info.get("name", triplet),
            elevation_ft=int(station_info.get("elevation", 0)),
            snow_depth_in=snwd,
            swe_in=wteq,
            snow_depth_cm=snwd_cm,
            swe_cm=wteq_cm,
        )

    def _fetch_station_meta(self, triplet: str) -> Optional[dict]:
        """Fetch station metadata (name, elevation) via the stations endpoint."""
        url = f"{AWDB_BASE}/stations"
        params = {"stationTriplets": triplet, "returnObjects": "true"}
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data[0] if data else None
            return data
        except Exception as exc:
            logger.warning("[snotel] Failed to fetch metadata for %s: %s", triplet, exc)
            return None

    def _fetch_elements(self, triplet: str) -> tuple[Optional[float], Optional[float]]:
        """
        Fetch SNWD and WTEQ in one request, looking back up to 3 days.
        Returns (snow_depth_in, swe_in). Either may be None.
        """
        from datetime import date, timedelta
        end_date = date.today()
        begin_date = end_date - timedelta(days=3)
        url = f"{AWDB_BASE}/data"
        params = {
            "stationTriplets": triplet,
            "elements":        "SNWD,WTEQ",
            "beginDate":       begin_date.isoformat(),
            "endDate":         end_date.isoformat(),
        }
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            results = resp.json()
            if not results:
                return None, None

            station_result = results[0]
            snwd = self._latest_value(station_result, "SNWD")
            wteq = self._latest_value(station_result, "WTEQ")
            return snwd, wteq
        except Exception as exc:
            logger.warning("[snotel] Failed to fetch elements for %s: %s", triplet, exc)
            return None, None

    @staticmethod
    def _latest_value(station_result: dict, element_code: str) -> Optional[float]:
        """Extract the most recent non-null value for an element from a data result."""
        for element_data in station_result.get("data", []):
            meta = element_data.get("stationElement", {})
            if meta.get("elementCode") != element_code:
                continue
            # Walk values newest-first to find the latest non-null
            for entry in reversed(element_data.get("values", [])):
                val = entry.get("value")
                if val is not None:
                    return float(val)
        return None
