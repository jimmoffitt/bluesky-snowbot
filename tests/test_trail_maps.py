"""Trail-map links: 'map Vail', bare 'map', and the link at the end of DM reports."""

import pytest

from snowbot.content import load_trail_maps
from snowbot.menu import parse_map_request
from snowbot.snow.resolver import _RESORTS
from tests.test_admin_alerts import make_bot, _send  # noqa: F401  (fixture + helper)


@pytest.mark.parametrize("text, expected", [
    ("map", ""), ("Map!", ""), ("maps", ""), ("trail map", ""),
    ("map Vail", "Vail"), ("Map vail", "vail"), ("trail map for Alta", "Alta"),
    ("show me the map of Snowbird", "Snowbird"), ("piste map Zermatt", "Zermatt"),
    ("Vail trail map", "Vail"), ("Breckenridge's trail map", "Breckenridge"),
    ("mountain map Lutsen?", "Lutsen"),
])
def test_parse_map_request(text, expected):
    assert parse_map_request(text) == expected


@pytest.mark.parametrize("text", ["Vail", "Mammoth", "maple", "regions", "photos", "Colorado", "set home Vail"])
def test_not_map_requests(text):
    assert parse_map_request(text) is None


def test_every_resort_has_a_trail_map_or_site():
    maps = load_trail_maps()
    missing = [(e["display"], e["state"]) for e in _RESORTS if (e["display"], e["state"]) not in maps]
    assert missing == []
    assert all(tm.link.startswith("https://") for tm in maps.values())


def _dm(bot, text):
    _send(bot, text)
    return bot.dm.payloads[-1].post_thread[0]


def test_map_named_resort(make_bot):
    b = make_bot()
    reply = _dm(b, "map Vail")
    assert reply.startswith("🗺️ Vail trail map:")
    assert "vail.com/the-mountain/about-the-mountain/trail-map.aspx" in reply


def test_map_resort_without_direct_link_gives_website(make_bot):
    b = make_bot()
    reply = _dm(b, "trail map Alta")
    assert "don't have a direct trail-map link for Alta" in reply
    assert "https://" in reply


def test_map_unknown_resort(make_bot):
    b = make_bot()
    assert "couldn't find a resort called 'Narnia Peak'" in _dm(b, "map Narnia Peak")


def test_bare_map_without_context_asks_which(make_bot):
    b = make_bot()
    assert _dm(b, "map").startswith("Which resort's trail map?")


def test_bare_map_uses_last_report(make_bot, sample_snow_report):
    b = make_bot(lookup=lambda raw: sample_snow_report)   # a Vail report
    _send(b, "Vail")
    report = "\n\n".join(b.dm.payloads[-1].post_thread)
    assert "🗺️ Trail map: https://www.vail.com/" in report
    assert _dm(b, "map").startswith("🗺️ Vail trail map:")


def test_bare_map_uses_home_resort(make_bot):
    b = make_bot()
    b._db.set_user_prefs("did:plc:skier", handle="skier.bsky.social", home_raw="Lutsen",
                         home_display="Lutsen Mountains", home_lat=47.6, home_lon=-90.7)
    assert _dm(b, "map").startswith("🗺️ Lutsen Mountains trail map:")


def test_public_mention_report_has_no_map_line(make_bot, sample_snow_report):
    b = make_bot(lookup=lambda raw: sample_snow_report)
    _send(b, "Vail", channel="firehose")
    assert not any("Trail map" in t for p in b.post.payloads for t in p.post_thread)
