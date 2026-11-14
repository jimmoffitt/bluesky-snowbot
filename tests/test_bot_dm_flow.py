"""DM flow through Snowbot._handle_request with fake notify channels (no network)."""

import re

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
    b.snow_lookups_resolved = []

    def fake_lookup(raw):
        b.lookups.append(raw)
        loc = b._snow._resolver.match_known(raw)
        if loc:
            b.snow_lookups_resolved.append((loc.name, loc.state))
        raise ValueError(raw)
    monkeypatch.setattr(b._snow, "lookup", fake_lookup)
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
    assert "🎿 Colorado resorts" in bot.dm.sent[-1]


def test_home_region_opens_that_regions_resorts(bot):
    _dm(bot, "set region Japan")
    _dm(bot, "menu")
    _dm(bot, "1")
    assert "🎿 Japan resorts" in bot.dm.sent[-1]


@pytest.mark.parametrize("text", [
    "set region CO", "set state CO", "set region colorado", "set state Colorado",
])
def test_set_region_and_set_state_both_list_the_resorts(bot, text):
    _dm(bot, text)
    reply = bot.dm.sent[-1]
    assert reply.startswith("📍 Home region set to Colorado (CO).")
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


def test_regions_menu_without_home_explains_setting_one(bot):
    _dm(bot, "regions")
    reply = bot.dm.sent[-1]
    assert reply.startswith("🎿 Select a region")
    assert "Tip: save a home region" in reply and "set region Colorado" in reply
    assert "clear region" not in reply


def test_regions_menu_with_home_shows_it_and_how_to_clear(bot):
    _dm(bot, "set region Utah")
    _dm(bot, "regions")
    reply = bot.dm.sent[-1]
    assert "Your home region: Utah (UT)" in reply
    assert "Clear it: clear region" in reply
    assert "Tip: save a home region" not in reply


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
    assert "grandvalira" in bot.lookups[0].lower()


@pytest.mark.parametrize("text, header, expected", [
    ("Michigan",       "🎿 Michigan resorts",   "Boyne Mountain"),
    ("Wisconsin",      "🎿 Wisconsin resorts",  "Granite Peak"),
    ("Minnesota",      "🎿 Minnesota resorts",  "Lutsen Mountains"),
    ("MI",             "🎿 Michigan resorts",   "Nub's Nob"),
    ("midwest",        "🎿 US Midwest resorts", "Afton Alps"),
    ("great lakes",    "🎿 US Midwest resorts", "Trollhaugen"),
])
def test_midwest_areas_list_their_resorts(bot, text, header, expected):
    _dm(bot, text)
    assert bot.dm.sent[-1].startswith(header)
    assert expected in bot.dm.sent[-1]


def test_midwest_is_its_own_region_and_minnesota_left_us_east(bot):
    _dm(bot, "regions")
    assert "US Midwest" in bot.dm.sent[-1]
    _dm(bot, "US East")
    assert "Afton Alps" not in bot.dm.sent[-1]


def test_set_region_midwest(bot):
    _dm(bot, "set region Midwest")
    assert bot.dm.sent[-1].startswith("📍 Home region set to US Midwest.")


def test_home_region_list_says_why_and_how_to_clear(bot):
    _dm(bot, "set region Colorado")
    assert "Clear it: clear region" in bot.dm.sent[-1]
    _dm(bot, "menu")
    _dm(bot, "1")
    reply = bot.dm.sent[-1]
    assert reply.startswith("📍 Showing your home region: Colorado (CO)")
    assert "🎿 Colorado resorts" in reply
    assert "This list is your home region." in reply
    assert "Clear it: clear region" in reply


def test_large_home_region_still_fits_one_message(bot):
    _dm(bot, "set region US Rockies")
    _dm(bot, "menu")
    _dm(bot, "1")
    reply = bot.dm.sent[-1]
    assert reply.startswith("📍 Showing your home region: US Rockies")
    assert "Clear it: clear region" in reply


@pytest.mark.parametrize("steps", [["Colorado"], ["menu", "1", "1"]])
def test_lists_not_from_a_setting_have_no_home_note(bot, steps):
    for text in steps:
        _dm(bot, text)
    assert "🎿" in bot.dm.sent[-1]
    assert "📍" not in bot.dm.sent[-1]
    assert "clear region" not in bot.dm.sent[-1]


@pytest.mark.parametrize("steps", [["Colorado"], ["menu", "1", "1"], ["regions", "2"]])
def test_browsed_lists_without_home_suggest_setting_one(bot, steps):
    for text in steps:
        _dm(bot, text)
    reply = bot.dm.sent[-1]
    assert "🎿" in reply
    assert "Tip: save a home region" in reply


def test_browsing_another_region_with_home_set_has_no_tip(bot):
    _dm(bot, "set region Utah")
    _dm(bot, "Japan")
    reply = bot.dm.sent[-1]
    assert "🎿 Japan resorts" in reply
    assert "Tip: save a home region" not in reply
    assert "clear region" not in reply


def test_back_to_home_list_keeps_clear_hint(bot):
    _dm(bot, "set region Colorado")
    _dm(bot, "1")          # Arapahoe Basin report
    _dm(bot, "back")
    reply = bot.dm.sent[-1]
    assert "🎿 Colorado resorts" in reply
    assert "Clear it: clear region" in reply


def test_midwest_list_is_grouped_by_state_alphabetically(bot):
    _dm(bot, "midwest")
    lines = bot.dm.sent[-1].splitlines()
    # State labels on their own lines, in alphabetical order
    assert [l for l in lines if l in ("Michigan", "Minnesota", "Wisconsin")] == \
        ["Michigan", "Minnesota", "Wisconsin"]
    # Resorts indented beneath, "    N Name icon", numbered 1..21 straight through
    numbered = [l for l in lines if re.match(r"    \u2007?\d+ ", l)]
    assert [int(l.split()[0]) for l in numbered] == list(range(1, 23))
    mi = lines[lines.index("Michigan") + 1 : lines.index("Minnesota")]
    names = [re.sub(r"^    \u2007?\d+ | [❄≈○]$", "", l) for l in mi]
    assert names == sorted(names) and "Boyne Mountain" in names
    assert lines[lines.index("Minnesota") + 1] == "    \u20079 Afton Alps ○"
    assert ", MN" not in bot.dm.sent[-1]


def test_midwest_numbers_pick_the_resort_shown(bot):
    _dm(bot, "midwest")
    lines = bot.dm.sent[-1].splitlines()
    n = next(l.split()[0] for l in lines if "Lutsen Mountains" in l)
    _dm(bot, n)
    assert bot.snow_lookups_resolved[-1] == ("Lutsen Mountains", "MN")


@pytest.mark.parametrize("text", ["Colorado", "Michigan", "US Rockies"])
def test_other_lists_do_not_add_state(bot, text):
    _dm(bot, text)
    assert not re.search(r"\d+\. [^\n]+, [A-Z]{2} ", bot.dm.sent[-1])


def test_picking_michigan_crystal_mountain_gets_michigan(bot):
    _dm(bot, "Michigan")
    lines = bot.dm.sent[-1].splitlines()
    n = next(l.split(".")[0] for l in lines if "Crystal Mountain" in l)
    _dm(bot, n)
    assert bot.snow_lookups_resolved[-1] == ("Crystal Mountain", "MI")


@pytest.mark.parametrize("text, header", [
    ("region midwest",          "🎿 US Midwest resorts"),
    ("Region Midwest",          "🎿 US Midwest resorts"),
    ("midwest region",          "🎿 US Midwest resorts"),
    ("region great lakes",      "🎿 US Midwest resorts"),
    ("state michigan",          "🎿 Michigan resorts"),
    ("region MI",               "🎿 Michigan resorts"),
    ("show me Colorado resorts", "🎿 Colorado resorts"),
    ("resorts in Japan",        "🎿 Japan resorts"),
])
def test_region_phrases_list_resorts(bot, text, header):
    _dm(bot, text)
    assert bot.dm.sent[-1].startswith(header)
    assert bot.lookups == []


def test_resort_lists_pad_numbers_and_show_the_legend(bot):
    _dm(bot, "Colorado")                       # 15 resorts → two-digit numbers
    reply = bot.dm.sent[-1]
    assert "\u20071. Arapahoe Basin" in reply   # single digits padded
    assert "\n13. Vail" in reply                # double digits not padded
    assert "📊 Snow data:" in reply
    assert "SNOTEL station on the mountain" in reply and "Weather model only" in reply


def test_padded_number_still_picks_the_resort(bot):
    _dm(bot, "Colorado")
    _dm(bot, "\u20076")                       # as if copied from the list
    _dm(bot, "Colorado")
    _dm(bot, "4")
    assert bot.snow_lookups_resolved[-1] == ("Breckenridge", "CO")


@pytest.mark.parametrize("text", ["Mt. La Crosse", "mt lacrosse", "Mount La Crosse", "snow at mt la crosse?"])
def test_mt_la_crosse_resolves(bot, text):
    _dm(bot, text)
    assert bot.snow_lookups_resolved[-1] == ("Mt. La Crosse", "WI")


def test_mt_la_crosse_in_wisconsin_group(bot):
    _dm(bot, "midwest")
    lines = bot.dm.sent[-1].splitlines()
    wi = lines[lines.index("Wisconsin") + 1:]
    assert any("Mt. La Crosse" in l for l in wi[:8])
