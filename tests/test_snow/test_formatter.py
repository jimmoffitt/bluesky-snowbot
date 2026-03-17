"""Tests for the SnowFormatter."""

import pytest

from bluesky_snowbot.snow.formatter import SnowFormatter


def test_format_thread_returns_two_posts(sample_snow_report):
    posts = SnowFormatter().format_thread(sample_snow_report)
    assert len(posts) == 2


def test_all_posts_within_limit(sample_snow_report):
    posts = SnowFormatter().format_thread(sample_snow_report)
    for post in posts:
        assert len(post) <= 300, f"Post exceeds 300 chars: {len(post)}"


def test_post1_contains_resort_name(sample_snow_report):
    post = SnowFormatter().format_thread(sample_snow_report)[0]
    assert "Vail" in post


def test_post1_contains_depth(sample_snow_report):
    post = SnowFormatter().format_thread(sample_snow_report)[0]
    assert "120" in post or "47" in post  # cm or inches


def test_post2_contains_temperature(sample_snow_report):
    post = SnowFormatter().format_thread(sample_snow_report)[1]
    assert "°F" in post or "°C" in post


def test_post2_contains_conditions(sample_snow_report):
    post = SnowFormatter().format_thread(sample_snow_report)[1]
    assert "snowfall" in post.lower() or "snow" in post.lower()
