"""DM flow through Snowbot._handle_request with fake notify channels (no network)."""

import pytest

from snowbot.channels.alert.base import AlertRequest
from snowbot.channels.notify.base import NotificationResult
from bot import Snowbot


class FakeChannel:
    def __init__(self, name):
        self.CHANNEL_NAME = name
        self.sent = []
        self.photos = []

    def send(self, payload):
        self.sent.extend(payload.post_thread)
        return NotificationResult(success=True, channel=self.CHANNEL_NAME)

    def post_photo(self, **kwargs):
        self.photos.append(kwargs)
        return "at://did:plc:bot/app.bsky.feed.post/abc"


@pytest.fixture
def bot(tmp_path, monkeypatch):
    from snowbot.config.settings import Settings
    settings = Settings.load()
    settings.db_path = str(tmp_path / "test.db")
    b = Snowbot(settings)
    b._db.connect()
    b.lookups = []
    monkeypatch.setattr(b._snow, "lookup", lambda raw: b.lookups.append(raw) or (_ for _ in ()).throw(ValueError(raw)))
    b.dm, b.post = FakeChannel("bluesky_dm"), FakeChannel("bluesky_post")
    b.register_notify_channel(b.dm)
    b.register_notify_channel(b.post)
    yield b
    b._db.close()


def _dm(bot, text, n=[0]):
    from snowbot.channels.alert.dm_poller import DMAlertChannel, _extract_location
    n[0] += 1
    cmd = DMAlertChannel._extract_command(text)
    bot._handle_request(AlertRequest(
        source_channel="dm", requester_handle="tester.bsky.social", requester_did="did:plc:tester",
        raw_location=None if cmd else _extract_location(text), raw_content=text,
        reply_to_uri=f"convo-{n[0]}", command=cmd,
    ))


def test_resort_number_that_matches_main_menu_number_looks_up_resort(bot):
    from snowbot.snow.resolver import _RESORTS_BY_REGION
    _dm(bot, "menu")
    _dm(bot, "1")   # regions
    _dm(bot, "1")   # first region's resorts
    _dm(bot, "3")   # third resort — must not trigger the "photos" branch
    first_region = next(iter(_RESORTS_BY_REGION.values()))
    assert bot.lookups == [first_region[2]["display"]]
    assert bot.post.photos == []


def test_photos_from_main_menu(bot):
    if not bot._content.random_photo():
        pytest.skip("no local photos")
    _dm(bot, "menu")
    _dm(bot, "3")
    assert len(bot.post.photos) == 1
    # Link must point at the bot's profile, where the post lives
    assert f"https://bsky.app/profile/{bot._settings.bluesky_handle}/post/abc" in bot.dm.sent[-1]


def test_photo_requests_do_not_repeat(bot):
    n = len(bot._content.all_photos())
    if n < 2:
        pytest.skip("need at least 2 local photos")
    for _ in range(min(n, 5)):
        _dm(bot, "photos")
    paths = [p["photo_path"] for p in bot.post.photos]
    assert len(paths) == len(set(paths))
    assert len(bot._db.get_photo_last_posted("did:plc:tester")) == len(paths)


def test_set_state_and_settings(bot):
    _dm(bot, "set state CO")
    assert "Colorado" in bot.dm.sent[-1]
    _dm(bot, "settings")
    assert "Colorado (CO)" in bot.dm.sent[-1]


def test_set_region_alias_and_clear(bot):
    _dm(bot, "set region co")
    assert "Colorado (CO)" in bot.dm.sent[-1]
    _dm(bot, "clear region")
    assert bot.dm.sent[-1].startswith("Home region cleared.")
    _dm(bot, "settings")
    assert "Home region: not set" in bot.dm.sent[-1]


@pytest.mark.parametrize("text", ["thanks!", "ok", "cool", "hello"])
def test_small_talk_is_not_a_location(bot, text):
    _dm(bot, text)
    assert bot.lookups == []
    assert "resort name" in bot.dm.sent[-1]


def test_home_state_opens_that_states_resorts(bot):
    _dm(bot, "set state CO")
    _dm(bot, "menu")
    _dm(bot, "1")
    assert bot.dm.sent[-1].startswith("🎿 Colorado resorts")


def test_home_region_opens_that_regions_resorts(bot):
    _dm(bot, "set region Japan")
    _dm(bot, "menu")
    _dm(bot, "1")
    assert bot.dm.sent[-1].startswith("🎿 Japan resorts")


@pytest.mark.parametrize("text", [
    "set region CO", "set state CO", "set region colorado", "set state Colorado",
])
def test_set_region_and_set_state_both_list_the_resorts(bot, text):
    _dm(bot, text)
    reply = bot.dm.sent[-1]
    assert reply.startswith("Home region set to Colorado (CO).")
    assert "🎿 Colorado resorts" in reply
    assert "Vail" in reply and "Park City" not in reply
    # The list is live: a number picks from it
    _dm(bot, "1")
    assert bot.lookups == ["Arapahoe Basin"]


@pytest.mark.parametrize("text, header, setting", [
    ("set region US Rockies", "🎿 US Rockies resorts", "Home region: US Rockies"),
    ("set state japan",       "🎿 Japan resorts",      "Home region: Japan"),
    ("set region west coast", "🎿 US West Coast resorts", "Home region: US West Coast"),
    ("set region Québec",     "🎿 Québec resorts",     "Home region: Québec (QC)"),
    ("set region quebec",     "🎿 Québec resorts",     "Home region: Québec (QC)"),
])
def test_set_region_accepts_names_and_regions(bot, text, header, setting):
    _dm(bot, text)
    assert header in bot.dm.sent[-1]
    _dm(bot, "settings")
    assert setting in bot.dm.sent[-1]


@pytest.mark.parametrize("text", ["set region Narnia", "set region", "set state AZ"])
def test_set_region_unknown_or_empty_gives_examples(bot, text):
    _dm(bot, text)
    assert "set region Colorado" in bot.dm.sent[-1]
    _dm(bot, "settings")
    assert "Home region: not set" in bot.dm.sent[-1]


@pytest.mark.parametrize("text", ["regions", "back"])
def test_other_regions_reachable_with_home_state(bot, text):
    _dm(bot, "set state CO")
    _dm(bot, "menu")
    _dm(bot, "1")      # snow reports → Colorado resorts
    _dm(bot, text)
    assert bot.dm.sent[-1].startswith("🎿 Select a region")


@pytest.mark.parametrize("text", [
    "loving this bot @snowbot.bsky.social",   # no location after the mention
    "@snowbot.bsky.social you rock",          # text that doesn't resolve to a resort
])
def test_public_mention_chatter_gets_no_reply(bot, text):
    from snowbot.channels.alert.firehose import FirehoseAlertChannel
    bot._handle_request(AlertRequest(
        source_channel="firehose", requester_handle="did:plc:tester", requester_did="did:plc:tester",
        raw_location=FirehoseAlertChannel._extract_location(text), raw_content=text,
        reply_to_uri=f"at://did:plc:tester/app.bsky.feed.post/{abs(hash(text))}", reply_to_cid="cid",
    ))
    assert bot.post.sent == [] and bot.dm.sent == []


def test_resort_list_dropped_after_report(bot):
    _dm(bot, "menu")
    _dm(bot, "1")   # regions
    _dm(bot, "2")   # second region's resorts
    _dm(bot, "3")   # resort pick → lookup
    _dm(bot, "1")   # now means main-menu "Snow reports", not resort #1
    assert len(bot.lookups) == 1
    assert any(m.startswith("🎿 Select a region") for m in bot.dm.sent[-2:])


def test_back_after_report_reopens_resort_list(bot):
    from snowbot.snow.resolver import _RESORTS_BY_REGION
    region = list(_RESORTS_BY_REGION)[1]
    for text in ("menu", "1", "2", "3", "back"):
        _dm(bot, text)
    assert any(m.startswith(f"🎿 {region} resorts") for m in bot.dm.sent[-3:])


@pytest.mark.parametrize("text", ["Photo!", "photos please", "show me a photo", "send a pic"])
def test_photo_keyword_variants(bot, text):
    if not bot._content.random_photo():
        pytest.skip("no local photos")
    _dm(bot, text)
    assert len(bot.post.photos) == 1
    assert bot.lookups == []


@pytest.mark.parametrize("text", ["learn", "Learn!", "learn more", "I want to learn about snow"])
def test_learn_keyword_variants(bot, text):
    _dm(bot, text)
    assert bot.lookups == []
    assert any("Learn about snow" in m for m in bot.dm.sent)


def test_resort_name_beats_keyword(bot):
    _dm(bot, "photos from vail")   # names a resort → snow report, not a photo
    assert bot.lookups == ["photos from vail"]
    assert bot.post.photos == []


@pytest.mark.parametrize("text", ["learn", "music", "menu"])
def test_menus_arrive_as_one_dm(bot, text):
    _dm(bot, text)
    assert len(bot.dm.sent) == 1


def test_large_resort_list_is_one_dm(bot):
    for text in ("menu", "1", "1"):   # US Rockies, the longest resort list
        _dm(bot, text)
    assert bot.dm.sent[-1].startswith("🎿 US Rockies resorts")
    assert len(bot.dm.sent[-1]) <= 1000


def test_regions_menu_explains_default_region(bot):
    _dm(bot, "regions")
    reply = bot.dm.sent[-1]
    assert reply.startswith("🎿 Select a region")
    assert "set region Colorado" in reply
    assert "clear region" in reply


@pytest.mark.parametrize("text, header", [
    ("Colorado",           "🎿 Colorado resorts"),
    ("colorado",           "🎿 Colorado resorts"),
    ("CO",                 "🎿 Colorado resorts"),
    ("Utah resorts",       "🎿 Utah resorts"),
    ("Japan",              "🎿 Japan resorts"),
    ("us rockies",         "🎿 US Rockies resorts"),
    ("British Columbia!",  "🎿 British Columbia resorts"),
])
def test_bare_area_name_lists_its_resorts(bot, text, header):
    _dm(bot, text)
    assert bot.lookups == []
    assert bot.dm.sent[-1].startswith(header)
    # Doesn't change the saved home region
    _dm(bot, "settings")
    assert "Home region: not set" in bot.dm.sent[-1]


def test_bare_area_list_is_live(bot):
    _dm(bot, "Colorado")
    _dm(bot, "13")
    assert bot.lookups == ["Vail"]


@pytest.mark.parametrize("text", ["me", "it", "or", "At"])
def test_lowercase_two_letter_words_are_not_areas(bot, text):
    _dm(bot, text)
    assert not any(m.startswith("🎿") for m in bot.dm.sent)


def test_single_resort_area_goes_straight_to_report(bot):
    _dm(bot, "Andorra")
    assert len(bot.lookups) == 1
    assert "Grandvalira" in bot.lookups[0]
