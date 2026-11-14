"""Open-Meteo hiccups: quick retries, then a recent cached report flagged as stale."""

from datetime import datetime, timedelta

import pytest
import requests

from snowbot.snow import client as client_module
from snowbot.snow.service import SnowService, _serialize_report


def test_client_fails_fast_and_retries():
    session = client_module.SnowClient()._session
    retry = session.get_adapter("https://api.open-meteo.com").max_retries
    assert retry.connect == 2 and retry.read == 2
    assert client_module.CONNECT_TIMEOUT <= 5
    assert "GET" in retry.allowed_methods


def _cache(db, report, minutes_ago):
    loc = report.location
    db.save_cached_snow(loc.lat, loc.lon, loc.name, _serialize_report(report), ttl_minutes=30)
    then = (datetime.utcnow() - timedelta(minutes=minutes_ago)).isoformat()
    db._conn.execute("UPDATE snow_cache SET fetched_at = ?, expires_at = ?", (then, then))
    db._conn.commit()


@pytest.fixture
def service(db, monkeypatch, sample_snow_report):
    svc = SnowService(db)
    monkeypatch.setattr(svc._resolver, "resolve", lambda raw: sample_snow_report.location)

    def offline(location):
        raise requests.exceptions.ConnectTimeout("api.open-meteo.com: connect timed out")
    monkeypatch.setattr(svc, "_build_report", offline)
    return svc


def test_failure_falls_back_to_recent_cached_report(service, db, sample_snow_report):
    _cache(db, sample_snow_report, minutes_ago=180)
    report = service.lookup("Vail")
    assert report.location.name == "Vail"
    assert report.snow.depth_cm == sample_snow_report.snow.depth_cm
    assert report.stale_fetched_at is not None
    assert isinstance(report.stale_error, requests.exceptions.ConnectTimeout)


def test_cached_report_too_old_is_not_used(service, db, sample_snow_report):
    _cache(db, sample_snow_report, minutes_ago=13 * 60)
    with pytest.raises(requests.exceptions.ConnectTimeout):
        service.lookup("Vail")


def test_no_cached_report_raises(service):
    with pytest.raises(requests.exceptions.ConnectTimeout):
        service.lookup("Vail")
