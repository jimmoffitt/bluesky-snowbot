"""Tests for the SnowFormatter — both Open-Meteo and SnoCountry paths."""

import pytest

from snowbot.snow.formatter import SnowFormatter
from snowbot.snow.models import ResortOperations


@pytest.fixture
def report_with_snocountry(sample_snow_report):
    sample_snow_report.operations = ResortOperations(
        operating_status="Open",
        base_depth_min_cm=127.0,   # 50"
        base_depth_max_cm=152.4,   # 60"
        new_snow_24h_min_cm=15.2,  # 6"
        new_snow_24h_max_cm=20.3,  # 8"
        snow_48h_cm=30.5,          # 12"
        season_total_cm=457.2,     # 180"
        open_lifts=18,
        total_lifts=31,
        open_trails=95,
        total_trails=195,
        open_percent=49.0,
        surface_condition="Packed Powder",
        snow_comments="18 inches past 72 hours",
    )
    sample_snow_report.data_source = "SnoCountry+Open-Meteo"
    return sample_snow_report


# --- Open-Meteo path ---

def test_format_thread_returns_two_posts(sample_snow_report):
    assert len(SnowFormatter().format_thread(sample_snow_report)) == 2


def test_all_posts_within_limit(sample_snow_report):
    for post in SnowFormatter().format_thread(sample_snow_report):
        assert len(post) <= 300, f"Post too long: {len(post)}"


def test_post1_contains_resort_name(sample_snow_report):
    assert "Vail" in SnowFormatter().format_thread(sample_snow_report)[0]


def test_post1_contains_depth(sample_snow_report):
    post = SnowFormatter().format_thread(sample_snow_report)[0]
    assert "120" in post or "47" in post  # cm or approx inches


def test_post2_contains_temperature(sample_snow_report):
    assert "°F" in SnowFormatter().format_thread(sample_snow_report)[1]


def test_post2_contains_conditions(sample_snow_report):
    post = SnowFormatter().format_thread(sample_snow_report)[1]
    assert "snow" in post.lower() or "clear" in post.lower()


# --- SnoCountry path ---

def test_snocountry_path_two_posts(report_with_snocountry):
    assert len(SnowFormatter().format_thread(report_with_snocountry)) == 2


def test_snocountry_all_posts_within_limit(report_with_snocountry):
    for post in SnowFormatter().format_thread(report_with_snocountry):
        assert len(post) <= 300, f"Post too long: {len(post)}"


def test_snocountry_post1_contains_status(report_with_snocountry):
    post = SnowFormatter().format_thread(report_with_snocountry)[0]
    assert "Open" in post


def test_snocountry_post1_contains_base_depth(report_with_snocountry):
    post = SnowFormatter().format_thread(report_with_snocountry)[0]
    # Should show 50-60" range
    assert "50" in post or "127" in post


def test_snocountry_post2_contains_lifts(report_with_snocountry):
    post = SnowFormatter().format_thread(report_with_snocountry)[1]
    assert "18" in post and "31" in post   # 18/31 lifts


def test_snocountry_post2_contains_surface(report_with_snocountry):
    post = SnowFormatter().format_thread(report_with_snocountry)[1]
    assert "Packed Powder" in post
