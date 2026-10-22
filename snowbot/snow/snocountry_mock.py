"""
MockSnoCountryClient — returns realistic synthetic resort data.

Intended for development and testing while waiting for a real SnoCountry
API key. Data is deterministic: the same resort_id always produces the same
numbers, but different resort_ids produce plausibly different conditions.

The mock also accepts an optional override file (JSON or YAML) so you can
hand-craft specific scenarios without touching code — useful for testing
edge cases like a closed resort or zero-snow conditions.

Override file format (YAML example at inbox/snocountry_override.yaml):
    resort_id: 42
    operating_status: "Closed for Season"
    base_depth_min_cm: 0
    base_depth_max_cm: 0
    open_lifts: 0
    total_lifts: 18

Usage:
    from snowbot.snow.snocountry_mock import MockSnoCountryClient
    client = MockSnoCountryClient()
    ops = client.fetch_resort(resort_id=42)
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional

from snowbot.snow.models import ResortOperations

logger = logging.getLogger(__name__)

# Mid-season "good conditions" baseline values (all in cm unless noted)
_BASE_DEPTH_RANGE    = (90,  230)   # min/max base depth spread across resorts
_NEW_24H_RANGE       = (0,    35)   # new snow in last 24h
_SEASON_TOTAL_RANGE  = (200, 600)   # season snowfall total
_LIFT_RANGE          = (8,    35)   # total lifts at resort
_TRAIL_RANGE         = (60,  250)   # total trails at resort

_SURFACE_CONDITIONS = [
    "Packed Powder",
    "Groomed / Packed Powder",
    "Variable Conditions",
    "Powder",
    "Hard Packed",
    "Machine Groomed",
    "Wind Affected",
]

_OPERATING_STATUSES = [
    "Open",
    "Open",
    "Open",          # weighted 3:1 toward Open
    "Limited Operations",
]

_SNOW_COMMENTS_TEMPLATES = [
    "{new72}\" new snow in past 72 hours. Excellent conditions on all terrain.",
    "{new24}\" overnight. Grooming crews out early. Lifts open at 9am.",
    "Great skiing! {new48}\" in the past 48 hours with more snow in the forecast.",
    "{new72}\" of natural snow in the past 72 hours plus grooming.",
    "Midwinter storm cycle delivering consistent snowfall. Base: {base_lo}\"–{base_hi}\".",
    "Spring conditions in the afternoon. {base_lo}\"–{base_hi}\" base depth.",
]

_PREDICTION_TEMPLATES = [
    ("1-3\"", "3-6\"",  "6-9\""),
    ("2-4\"", "4-8\"",  "8-12\""),
    ("0-1\"", "1-3\"",  "3-5\""),
    ("4-8\"", "8-14\"", "14-18\""),
    ("0\"",   "0-1\"",  "1-2\""),
]


def _cm_to_in(cm: float) -> int:
    return int(round(cm / 2.54))


class MockSnoCountryClient:
    """
    Drop-in replacement for SnoCountryClient that generates synthetic data.
    No network calls, no API key required.
    """

    def __init__(self, override_path: Optional[Path] = None) -> None:
        self._override_path = override_path

    def fetch_resort(self, resort_id: int) -> Optional[ResortOperations]:
        logger.info("[snocountry-mock] Generating mock data for resort_id=%s", resort_id)
        overrides = self._load_overrides(resort_id)
        base = self._generate(resort_id)
        # Apply any field-level overrides
        for k, v in overrides.items():
            if hasattr(base, k):
                object.__setattr__(base, k, v)
        return base

    def fetch_resort_list(self) -> list[dict]:
        """Returns a small mock resort list for testing fetch_resort_list.py."""
        return [
            {"id": 1001, "resortName": "Mock Valley Ski Resort",
             "state": "CO", "country": "USA", "resortType": "NA_Alpine"},
            {"id": 1002, "resortName": "Demo Mountain",
             "state": "UT", "country": "USA", "resortType": "NA_Alpine"},
            {"id": 1003, "resortName": "Sample Peak",
             "state": "CA", "country": "USA", "resortType": "NA_Alpine"},
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _generate(self, resort_id: int) -> ResortOperations:
        rng = random.Random(resort_id)   # deterministic per resort_id

        # Base depth (min/max within plausible range, min ≤ max)
        base_lo = rng.randint(*_BASE_DEPTH_RANGE)
        spread  = rng.randint(15, 50)
        base_hi = base_lo + spread

        # New snow (24h min/max, 48h, 72h accumulating)
        new24_lo = rng.randint(*_NEW_24H_RANGE)
        new24_hi = new24_lo + rng.randint(0, 10)
        new48    = new24_hi + rng.randint(0, 25)
        new72    = new48    + rng.randint(0, 25)

        # Season total
        season = rng.randint(*_SEASON_TOTAL_RANGE)

        # Lifts
        total_lifts = rng.randint(*_LIFT_RANGE)
        pct_open    = rng.uniform(0.55, 0.98)
        open_lifts  = max(1, int(total_lifts * pct_open))

        # Trails
        total_trails = rng.randint(*_TRAIL_RANGE)
        open_trails  = max(1, int(total_trails * rng.uniform(0.45, 0.95)))
        open_pct     = round(open_trails / total_trails * 100)

        # Acres (rough approximation from trails)
        total_acres = round(total_trails * rng.uniform(4.0, 9.0), 1)
        open_acres  = round(open_trails  * rng.uniform(4.0, 8.0), 1)

        # Surface and status
        surface  = rng.choice(_SURFACE_CONDITIONS)
        status   = rng.choice(_OPERATING_STATUSES)

        # Snowfall comment
        comment_tmpl = rng.choice(_SNOW_COMMENTS_TEMPLATES)
        comment = comment_tmpl.format(
            new24=_cm_to_in(new24_hi),
            new48=_cm_to_in(new48),
            new72=_cm_to_in(new72),
            base_lo=_cm_to_in(base_lo),
            base_hi=_cm_to_in(base_hi),
        )

        # Snowfall predictions
        preds = rng.choice(_PREDICTION_TEMPLATES)

        # Hours
        weekday_hours  = f"M–F: {rng.choice(['8:30', '9:00'])}am – 4:00pm"
        weekend_hours  = f"Sat–Sun: 8:00am – 4:00pm"

        # Report datetime (static mock date)
        report_dt = "2026-03-17 06:00:00"

        return ResortOperations(
            operating_status    = status,
            resort_status_code  = 1 if status == "Open" else 3,

            base_depth_min_cm   = float(base_lo),
            base_depth_max_cm   = float(base_hi),
            new_snow_24h_min_cm = float(new24_lo),
            new_snow_24h_max_cm = float(new24_hi),
            snow_48h_cm         = float(new48),
            season_total_cm     = float(season),

            last_snowfall_date   = "Mar 16, 2026",
            last_snowfall_amount = f"{_cm_to_in(new24_lo)}–{_cm_to_in(new24_hi)} inches",
            prev_snowfall_date   = "Mar 14, 2026",
            prev_snowfall_amount = f"{_cm_to_in(rng.randint(5, 25))} inches",

            predicted_snow_24h  = preds[0],
            predicted_snow_48h  = preds[1],
            predicted_snow_72h  = preds[2],

            open_lifts    = open_lifts,
            total_lifts   = total_lifts,
            open_trails   = open_trails,
            total_trails  = total_trails,
            open_acres    = open_acres,
            total_acres   = total_acres,
            open_percent  = float(open_pct),

            surface_condition = surface,
            snow_comments     = comment,

            weekday_hours = weekday_hours,
            weekend_hours = weekend_hours,

            trail_map_url = "https://example.com/trailmap.jpg",
            webcam_url    = "https://example.com/webcam",
            website_url   = "https://example.com",

            report_datetime = report_dt,
        )

    def _load_overrides(self, resort_id: int) -> dict:
        """
        Load field overrides from a YAML/JSON file if present.
        Only applies when the file's resort_id matches.
        """
        if self._override_path is None or not self._override_path.exists():
            return {}
        try:
            import yaml
            with open(self._override_path) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return {}
            if data.get("resort_id") not in (None, resort_id):
                return {}
            return {k: v for k, v in data.items() if k != "resort_id"}
        except Exception as exc:
            logger.warning("[snocountry-mock] Could not load override file: %s", exc)
            return {}
