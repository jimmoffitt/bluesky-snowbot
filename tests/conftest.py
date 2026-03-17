"""Shared pytest fixtures for bluesky_snowbot tests."""

import pytest

from bluesky_snowbot.storage.db import Database


@pytest.fixture
def db():
    """In-memory SQLite database, connected and closed per test."""
    with Database(":memory:") as _db:
        yield _db


@pytest.fixture
def sample_resort_location():
    from bluesky_snowbot.snow.models import ResortLocation
    return ResortLocation(name="Vail", lat=39.6433, lon=-106.3781, state="CO", elevation_m=3527)


@pytest.fixture
def sample_snow_report(sample_resort_location):
    from bluesky_snowbot.snow.models import SnowDepthReport, ConditionsReport, SnowReport
    return SnowReport(
        location=sample_resort_location,
        snow=SnowDepthReport(
            depth_cm=120.0,
            new_24h_cm=15.0,
            new_48h_cm=25.0,
            new_72h_cm=30.0,
        ),
        conditions=ConditionsReport(
            temp_c=-8.0,
            wind_speed_kmh=25.0,
            wind_gusts_kmh=45.0,
            weather_code=73,
            weather_desc="Moderate snowfall",
            visibility_km=5.0,
        ),
    )
