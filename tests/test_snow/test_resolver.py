"""Tests for the resort resolver."""

import pytest

from bluesky_snowbot.snow.resolver import SnowResolver


@pytest.fixture
def resolver():
    return SnowResolver()


def test_exact_match(resolver):
    loc = resolver.resolve("vail")
    assert loc.name == "Vail"
    assert loc.state == "CO"
    assert abs(loc.lat - 39.6433) < 0.01


def test_display_name_match(resolver):
    loc = resolver.resolve("Vail Ski Resort")
    assert loc.name == "Vail"


def test_alias_match(resolver):
    loc = resolver.resolve("breck")
    assert loc.name == "Breckenridge"


def test_case_insensitive(resolver):
    loc = resolver.resolve("MAMMOTH MOUNTAIN")
    assert loc.name == "Mammoth Mountain"


def test_partial_match(resolver):
    loc = resolver.resolve("park city")
    assert "Park City" in loc.name


def test_unknown_raises(resolver):
    with pytest.raises(ValueError):
        resolver.resolve("Zzz Nonexistent Ski Area Xyz")


def test_elevation_set(resolver):
    loc = resolver.resolve("vail")
    assert loc.elevation_m is not None
    assert loc.elevation_m > 3000


@pytest.mark.parametrize("text,expected", [
    ("what's the snow like at vail?", "Vail"),
    ("snow report for jackson hole please", "Jackson Hole"),
    ("telluride today?", "Telluride"),
])
def test_resort_named_in_sentence(resolver, text, expected):
    assert expected in resolver.resolve(text).name


@pytest.mark.parametrize("text", ["ok", "yes"])
def test_short_words_do_not_match_inside_resort_names(resolver, monkeypatch, text):
    monkeypatch.setattr(SnowResolver, "_geocode_fallback", staticmethod(lambda raw: None))
    with pytest.raises(ValueError):
        resolver.resolve(text)
