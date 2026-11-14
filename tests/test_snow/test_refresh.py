"""Background refresh: batch Open-Meteo requests, cache every resort, serve users from cache."""

import pytest

from snowbot.snow.client import SnowClient
from snowbot.snow.resolver import _RESORTS, _entry_to_location
from snowbot.snow.service import SnowService


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_fetch_many_sends_one_request_for_all_locations():
    client = SnowClient()
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        n = len(params["latitude"].split(","))
        return _FakeResponse([{"i": i} for i in range(n)])
    client._session.get = fake_get
    out = client.fetch_many([(39.64, -106.37), (43.79, -110.96), (47.66, -90.73)])
    assert len(calls) == 1
    assert calls[0]["latitude"] == "39.6400,43.7900,47.6600"
    assert calls[0]["longitude"] == "-106.3700,-110.9600,-90.7300"
    assert [o["i"] for o in out] == [0, 1, 2]


def test_fetch_many_rejects_mismatched_results():
    client = SnowClient()
    client._session.get = lambda url, params, timeout: _FakeResponse([{"i": 0}])
    with pytest.raises(ValueError):
        client.fetch_many([(1, 1), (2, 2)])


@pytest.fixture
def svc(db, monkeypatch, sample_snow_report):
    s = SnowService(db)

    def fake_report(location, raw):
        sample_snow_report.location = location
        return sample_snow_report
    monkeypatch.setattr(s, "_report_from_raw", fake_report)
    return s


def test_refresh_all_caches_every_resort_in_batches(svc, db):
    batches = []
    svc._openmeteo.fetch_many = lambda coords: batches.append(len(coords)) or [{}] * len(coords)
    locations = [_entry_to_location(e) for e in _RESORTS]
    ok, failed = svc.refresh_all(locations, ttl_minutes=90, batch_size=25)
    assert (ok, failed) == (len(locations), 0)
    assert sum(batches) == len(locations) and max(batches) <= 25
    assert all(db.get_cached_snow(l.lat, l.lon) for l in locations)


def test_a_failed_batch_does_not_stop_the_rest(svc, db):
    calls = []

    def flaky(coords):
        calls.append(coords)
        if len(calls) == 1:
            raise ConnectionError("handshake timed out")
        return [{}] * len(coords)
    svc._openmeteo.fetch_many = flaky
    locations = [_entry_to_location(e) for e in _RESORTS[:30]]
    ok, failed = svc.refresh_all(locations, ttl_minutes=90, batch_size=10)
    assert (ok, failed) == (20, 10)


def test_cache_hit_survives_a_clock_hour_change(svc, db):
    loc = _entry_to_location(_RESORTS[0])
    svc._openmeteo.fetch_many = lambda coords: [{}] * len(coords)
    svc.refresh_all([loc], ttl_minutes=90)
    db._conn.execute("UPDATE snow_cache SET cache_key = 'some-earlier-hour'")
    db._conn.commit()
    assert db.get_cached_snow(loc.lat, loc.lon) is not None


def test_expired_cache_is_not_a_hit(svc, db):
    loc = _entry_to_location(_RESORTS[0])
    svc._openmeteo.fetch_many = lambda coords: [{}] * len(coords)
    svc.refresh_all([loc], ttl_minutes=90)
    db._conn.execute("UPDATE snow_cache SET expires_at = '2000-01-01T00:00:00'")
    db._conn.commit()
    assert db.get_cached_snow(loc.lat, loc.lon) is None


def test_user_lookup_is_served_from_refreshed_cache_without_live_call(svc, monkeypatch):
    svc._openmeteo.fetch_many = lambda coords: [{}] * len(coords)
    vail = next(e for e in _RESORTS if e["display"] == "Vail")
    svc.refresh_all([_entry_to_location(vail)], ttl_minutes=90)

    def no_live(location):
        raise AssertionError("live fetch should not happen")
    monkeypatch.setattr(svc, "_build_report", no_live)
    report = svc.lookup("Vail")
    assert report.location.name == "Vail"
    assert report.stale_fetched_at is None
