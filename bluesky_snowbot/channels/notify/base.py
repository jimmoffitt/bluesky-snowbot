"""
Abstract base class for all notification channels.

A NotificationChannel is any output destination that can receive a
formatted snow report response. Current implementations: BlueskyPostNotifyChannel,
BlueskyDMNotifyChannel.

The channel receives a NotificationPayload (already formatted text) and
is responsible only for delivery — it does not format, look up snow data,
or know anything about the request that triggered it.

Usage pattern (in bot.py):
    channel = BlueskyPostNotifyChannel(handle, app_password)
    channel.send(payload)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NotificationPayload:
    """
    A fully formatted, ready-to-send notification.

    For Bluesky this is a list of post strings forming a thread.
    """
    # Back-reference to the originating request (for DB logging)
    request_db_id: Optional[int]

    # The formatted post thread. Index 0 is the root post; 1+ are replies.
    post_thread: list[str]

    # For social reply channels: the post to reply to
    reply_to_uri: Optional[str] = None
    reply_to_cid: Optional[str] = None

    # For DM channels: the recipient handle
    recipient_handle: Optional[str] = None

    # Channel hint — lets the orchestrator route to the right channel
    # "bluesky_post" | "bluesky_dm"
    target_channel: str = "bluesky_post"

    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class NotificationResult:
    """Result returned by NotificationChannel.send()."""
    success: bool
    channel: str
    # AT-URI of the root post sent (Bluesky channels); None for others
    post_uri: Optional[str] = None
    error: Optional[str] = None


class NotificationChannel(ABC):
    """
    Abstract base for all notification output channels.

    Subclasses must implement:
      - send(payload) → NotificationResult
    """

    CHANNEL_NAME: str = "base"   # Override in each subclass

    @abstractmethod
    def send(self, payload: NotificationPayload) -> NotificationResult:
        """
        Deliver the notification.
        Must return a NotificationResult regardless of success/failure
        (do not raise — log and return result with success=False instead).
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} channel={self.CHANNEL_NAME!r}>"
