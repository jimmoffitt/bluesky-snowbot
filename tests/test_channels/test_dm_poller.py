"""Adaptive DM polling interval."""

import pytest

from snowbot.channels.alert.dm_poller import DMAlertChannel


@pytest.fixture
def poller():
    from snowbot.config.settings import Settings
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
    from snowbot.config.settings import Settings
    p = DMAlertChannel(Settings.load(), poll_interval=2.0, active_interval=10.0)
    p._last_activity = 0.0
    assert p._next_interval(now=1.0) == 2.0


class _FakeChatClient:
    def __init__(self, content):
        self.content = content

    def invoke_query(self, nsid, **kwargs):
        from types import SimpleNamespace
        assert nsid == "chat.bsky.convo.getLog"
        return SimpleNamespace(content=self.content)


def _message_entry(msg_id, text, sender="did:plc:user"):
    return {
        "$type": "chat.bsky.convo.defs#logCreateMessage",
        "convoId": "convo1", "rev": "2",
        "message": {
            "$type": "chat.bsky.convo.defs#messageView",
            "id": msg_id, "rev": "2", "text": text,
            "sender": {"did": sender}, "sentAt": "2026-10-21T21:00:00.000Z",
        },
    }


def test_unknown_log_types_are_skipped_not_fatal(poller):
    poller._chat_client = _FakeChatClient({
        "cursor": "c2",
        "logs": [
            {"$type": "chat.bsky.convo.defs#logReadConvo", "convoId": "convo1", "rev": "1"},
            {"$type": "chat.bsky.convo.defs#logSomethingNew", "whatever": True},
            _message_entry("m1", "Vail"),
            {"$type": "chat.bsky.convo.defs#logCreateMessage", "convoId": "convo1"},  # malformed
        ],
    })
    poller._own_did = "did:plc:bot"
    received = []
    poller.on_request(received.append)

    assert poller._check_dms() == 1
    assert [r.raw_location for r in received] == ["Vail"]
    assert poller._cursor == "c2"


def test_own_messages_not_counted(poller):
    poller._chat_client = _FakeChatClient({"cursor": "c3", "logs": [_message_entry("m2", "hi", sender="did:plc:bot")]})
    poller._own_did = "did:plc:bot"
    assert poller._check_dms() == 0
    assert poller._cursor == "c3"
