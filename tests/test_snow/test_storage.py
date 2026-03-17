"""Tests for the Database layer."""


def test_save_and_get_request(db):
    rid = db.save_request(
        source_channel="dm",
        raw_content="Vail",
        requester_handle="user.bsky.social",
        raw_location="Vail",
    )
    assert rid is not None
    row = db.get_stats()
    assert row["requests_total"] == 1


def test_duplicate_firehose_request_dropped(db):
    uri = "at://did:plc:abc/app.bsky.feed.post/xyz"
    r1 = db.save_request(source_channel="firehose", raw_content="test",
                          source_uri=uri)
    r2 = db.save_request(source_channel="firehose", raw_content="test",
                          source_uri=uri)
    assert r1 is not None
    assert r2 is None   # duplicate


def test_snow_cache_roundtrip(db):
    report = {"depth_cm": 100.0, "new_24h_cm": 10.0, "new_48h_cm": 20.0,
              "new_72h_cm": 25.0, "temp_c": -5.0, "wind_speed_kmh": 20.0,
              "wind_gusts_kmh": 40.0, "weather_code": 71, "weather_desc": "Slight snowfall",
              "visibility_km": None, "fetched_at": "2025-12-15T10:00:00"}
    db.save_cached_snow(39.64, -106.38, "Vail", report, ttl_minutes=60)
    cached = db.get_cached_snow(39.64, -106.38)
    assert cached is not None
    assert cached["depth_cm"] == 100.0


def test_user_prefs_upsert(db):
    db.set_user_prefs("did:plc:abc123", handle="user.bsky.social",
                      home_raw="Vail", home_display="Vail")
    prefs = db.get_user_prefs("did:plc:abc123")
    assert prefs is not None
    assert prefs["home_raw"] == "Vail"

    # Update just the handle
    db.set_user_prefs("did:plc:abc123", handle="newhandle.bsky.social")
    prefs = db.get_user_prefs("did:plc:abc123")
    assert prefs["handle"] == "newhandle.bsky.social"
    assert prefs["home_raw"] == "Vail"   # unchanged


def test_clear_home(db):
    db.set_user_prefs("did:plc:abc", home_raw="Vail", home_display="Vail")
    db.clear_home("did:plc:abc")
    prefs = db.get_user_prefs("did:plc:abc")
    assert prefs["home_raw"] is None


def test_seen_dm_dedup(db):
    db.mark_dm_seen("msg-001", "convo-xyz")
    assert db.is_dm_seen("msg-001") is True
    assert db.is_dm_seen("msg-999") is False
