"""Failed requests DM the admin (ADMIN_HANDLE) with the error details."""

import pytest
import requests

import bot as bot_module
from bot import Snowbot
from snowbot.channels.alert.base import AlertRequest
from snowbot.channels.notify.base import NotificationResult

ADMIN = "snowman.bsky.social"


class RecordingChannel:
    def __init__(self, name):
        self.CHANNEL_NAME = name
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return NotificationResult(success=True, channel=self.CHANNEL_NAME)

    def post_photo(self, **kwargs):
        raise RuntimeError("upload failed: blob too large")


def _timeout():
    """A requests timeout with a real cause chain, raised from a function."""
    try:
        raise TimeoutError("timed out")
    except TimeoutError as inner:
        raise requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='api.open-meteo.com', port=443): Read timed out. (read timeout=15)"
        ) from inner


@pytest.fixture
def make_bot(tmp_path, monkeypatch):
    bots = []

    def _make(admin=ADMIN, lookup=None):
        from snowbot.config.settings import Settings
        settings = Settings.load()
        settings.db_path = str(tmp_path / f"test{len(bots)}.db")
        settings.admin_handle = admin
        b = Snowbot(settings)
        b._db.connect()
        monkeypatch.setattr(b._snow, "lookup", lookup or (lambda raw: _timeout()))
        b.dm, b.post = RecordingChannel("bluesky_dm"), RecordingChannel("bluesky_post")
        b.register_notify_channel(b.dm)
        b.register_notify_channel(b.post)
        bots.append(b)
        return b

    yield _make
    for b in bots:
        b._db.close()


def _send(bot, text, channel="dm", n=[0]):
    n[0] += 1
    bot._handle_request(AlertRequest(
        source_channel=channel, requester_handle="skier.bsky.social", requester_did="did:plc:skier",
        raw_location=text, raw_content=text, reply_to_uri=f"convo-{n[0]}",
    ))


def _admin_dms(bot):
    return [p for p in bot.dm.payloads if p.recipient_handle == ADMIN]


def test_lookup_failure_dms_admin_with_error_details(make_bot):
    b = make_bot()
    _send(b, "Lutsen")
    # The user still gets the friendly message
    assert "temporarily unavailable" in b.dm.payloads[0].post_thread[0]
    alerts = _admin_dms(b)
    assert len(alerts) == 1
    text = alerts[0].post_thread[0]
    assert "Snow lookup failed" in text
    assert "@skier.bsky.social via dm" in text
    assert "'Lutsen'" in text
    assert "ReadTimeout" in text and "api.open-meteo.com" in text
    assert "Caused by: TimeoutError: timed out" in text
    assert "At: tests/test_admin_alerts.py" in text and "_timeout()" in text
    assert alerts[0].reply_to_uri is None          # a new convo with the admin, not the user's
    assert b.post.payloads == []                   # never posted publicly


def test_alerts_are_throttled_per_error_type(make_bot, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(bot_module.time, "monotonic", lambda: clock[0])
    b = make_bot()
    _send(b, "Vail")
    _send(b, "Alta")
    _send(b, "Taos")
    assert len(_admin_dms(b)) == 1
    clock[0] += bot_module.ADMIN_ALERT_COOLDOWN_SEC + 1
    _send(b, "Lutsen")
    alerts = _admin_dms(b)
    assert len(alerts) == 2
    assert "+2 more like this" in alerts[1].post_thread[0]


def test_public_mention_failure_also_alerts_admin(make_bot):
    b = make_bot()
    _send(b, "Vail", channel="firehose")
    assert len(_admin_dms(b)) == 1


def test_no_admin_handle_means_no_alert(make_bot):
    b = make_bot(admin=None)
    _send(b, "Vail")
    assert len(b.dm.payloads) == 1   # only the user's "sorry"


def test_unknown_resort_is_not_an_alert(make_bot):
    def not_found(raw):
        raise ValueError(f"Could not resolve location: {raw!r}")
    b = make_bot(lookup=not_found)
    _send(b, "Narnia Peak")
    assert _admin_dms(b) == []


def test_photo_post_failure_alerts_admin(make_bot):
    b = make_bot()
    if not b._content.all_photos():
        pytest.skip("no local photos")
    b._handle_request(AlertRequest(
        source_channel="dm", requester_handle="skier.bsky.social", requester_did="did:plc:skier",
        raw_location=None, raw_content="photos", reply_to_uri="convo-photo",
    ))
    alerts = _admin_dms(b)
    assert len(alerts) == 1
    assert "Photo post failed" in alerts[0].post_thread[0]
    assert "blob too large" in alerts[0].post_thread[0]


def test_no_dm_channel_never_falls_back_to_public_post(make_bot):
    b = make_bot()
    del b._notify_channels["bluesky_dm"]
    _send(b, "Vail")
    # The user's error reply may route elsewhere, but no admin alert is posted publicly
    assert not any(ADMIN in (p.recipient_handle or "") for p in b.post.payloads)
    assert not any("Snowbot: Snow lookup failed" in p.post_thread[0] for p in b.post.payloads)


def test_stale_report_is_labeled_and_admin_alerted(make_bot, sample_snow_report):
    from datetime import datetime, timedelta
    sample_snow_report.stale_fetched_at = (datetime.utcnow() - timedelta(minutes=190)).isoformat()
    try:
        _timeout()
    except Exception as exc:
        sample_snow_report.stale_error = exc
    b = make_bot(lookup=lambda raw: sample_snow_report)
    _send(b, "Vail")
    user_dm = b.dm.payloads[0].post_thread
    assert user_dm[0].startswith("⚠️ Live data is unavailable.")
    assert "3 hr 10 min ago" in user_dm[0]
    assert any("Vail" in p for p in user_dm[1:])            # the report itself follows
    alerts = _admin_dms(b)
    assert len(alerts) == 1
    assert "Live snow data failed (sent a cached report)" in alerts[0].post_thread[0]
    assert "ReadTimeout" in alerts[0].post_thread[0]


def test_fresh_report_has_no_stale_note(make_bot, sample_snow_report):
    b = make_bot(lookup=lambda raw: sample_snow_report)
    _send(b, "Vail")
    assert "Live snow data" not in b.dm.payloads[0].post_thread[0]
    assert _admin_dms(b) == []
