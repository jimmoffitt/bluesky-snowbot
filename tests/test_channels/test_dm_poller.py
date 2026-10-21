"""Adaptive DM polling interval."""

import pytest

from bluesky_snowbot.channels.alert.dm_poller import DMAlertChannel


@pytest.fixture
def poller():
    from bluesky_snowbot.config.settings import Settings
    return DMAlertChannel(Settings.load(), poll_interval=5.0, active_interval=1.0, active_window=120.0)


def test_idle_before_any_dm(poller):
    assert poller._next_interval(now=1000.0) == 5.0


def test_fast_within_window_after_dm(poller):
    poller._last_activity = 1000.0
    assert poller._next_interval(now=1000.0) == 1.0
    assert poller._next_interval(now=1119.0) == 1.0


def test_back_to_idle_after_window(poller):
    poller._last_activity = 1000.0
    assert poller._next_interval(now=1121.0) == 5.0


def test_active_interval_never_slower_than_idle():
    from bluesky_snowbot.config.settings import Settings
    p = DMAlertChannel(Settings.load(), poll_interval=2.0, active_interval=10.0)
    p._last_activity = 0.0
    assert p._next_interval(now=1.0) == 2.0
