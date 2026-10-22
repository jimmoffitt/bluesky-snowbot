"""
BlueskyDMNotifyChannel

Sends a snow report response as a Bluesky Direct Message.

For DM responses the full thread is concatenated into a single message.
If the joined text exceeds Bluesky's DM character limit, it is split into
sequential messages in the same conversation.

The convo_id is passed in payload.reply_to_uri (set by DMAlertChannel).

Dependencies: pip install atproto
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from snowbot.channels.notify.base import (
    NotificationChannel,
    NotificationPayload,
    NotificationResult,
)

logger = logging.getLogger(__name__)

DM_MAX_CHARS    = 1000
INTER_MSG_DELAY = 0.3


class BlueskyDMNotifyChannel(NotificationChannel):
    """
    Sends formatted snow reports as Bluesky Direct Messages.

    payload.reply_to_uri should contain the convo_id from the originating DM.
    payload.recipient_handle is used to start a new convo if no convo_id.
    """

    CHANNEL_NAME = "bluesky_dm"

    def __init__(self, handle: str, app_password: str) -> None:
        self._handle       = handle
        self._app_password = app_password
        self._client       = None
        self._chat_client  = None
        self._login()

    # ------------------------------------------------------------------
    # NotificationChannel interface
    # ------------------------------------------------------------------

    def send(self, payload: NotificationPayload) -> NotificationResult:
        if not payload.post_thread:
            return NotificationResult(
                success=False, channel=self.CHANNEL_NAME, error="Empty post_thread",
            )

        if self._chat_client is None:
            ok = self._login()
            if not ok:
                return NotificationResult(
                    success=False, channel=self.CHANNEL_NAME, error="Not authenticated",
                )

        full_text = "\n\n".join(payload.post_thread)
        messages  = self._split_message(full_text)

        convo_id = payload.reply_to_uri
        if not convo_id and payload.recipient_handle:
            convo_id = self._get_or_create_convo(payload.recipient_handle)
        if not convo_id:
            return NotificationResult(
                success=False, channel=self.CHANNEL_NAME,
                error="No convo_id and no recipient_handle",
            )

        sent_any = False
        for i, text in enumerate(messages):
            try:
                self._send_message(convo_id, text)
                sent_any = True
                logger.info("[bluesky_dm] Sent message %d/%d to convo %s",
                            i + 1, len(messages), convo_id)
            except Exception as exc:
                logger.error("[bluesky_dm] Failed on message %d: %s", i, exc)
                return NotificationResult(
                    success=sent_any, channel=self.CHANNEL_NAME, error=str(exc),
                )
            if i < len(messages) - 1:
                time.sleep(INTER_MSG_DELAY)

        return NotificationResult(success=True, channel=self.CHANNEL_NAME)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _login(self) -> bool:
        try:
            from atproto import Client
            self._client = Client()
            self._client.login(self._handle, self._app_password)
            self._chat_client = self._client.with_bsky_chat_proxy()
            logger.info("[bluesky_dm] Authenticated as %s", self._handle)
            return True
        except ImportError:
            logger.error("[bluesky_dm] atproto not installed: pip install atproto")
            return False
        except Exception as exc:
            logger.error("[bluesky_dm] Login failed: %s: %s", type(exc).__name__, exc or repr(exc))
            return False

    def _get_or_create_convo(self, handle: str) -> Optional[str]:
        try:
            from atproto import models as atproto_models
            resolved = self._client.resolve_handle(handle=handle)
            resp = self._chat_client.chat.bsky.convo.get_convo_for_members(
                atproto_models.ChatBskyConvoGetConvoForMembers.Params(members=[resolved.did])
            )
            return getattr(getattr(resp, "convo", None), "id", None)
        except Exception as exc:
            logger.error("[bluesky_dm] Could not get/create convo with %s: %s", handle, exc)
            return None

    def _send_message(self, convo_id: str, text: str) -> None:
        if self._chat_client is None:
            raise RuntimeError("Not authenticated")
        from atproto import models as atproto_models
        facets = _build_link_facets(text)
        self._chat_client.chat.bsky.convo.send_message(
            atproto_models.ChatBskyConvoSendMessage.Data(
                convo_id=convo_id,
                message=atproto_models.ChatBskyConvoDefs.MessageInput(
                    text=text,
                    facets=facets if facets else None,
                ),
            )
        )

    @staticmethod
    def _split_message(text: str, max_len: int = DM_MAX_CHARS) -> list[str]:
        if len(text) <= max_len:
            return [text]
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            candidate = (current + "\n\n" + para).lstrip("\n") if current else para
            if len(candidate) <= max_len:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                if len(para) > max_len:
                    for i in range(0, len(para), max_len):
                        chunks.append(para[i:i + max_len])
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current)
        return chunks


_URL_RE = re.compile(r'https?://\S+')


def _build_link_facets(text: str) -> list:
    """Return a list of AppBskyRichtextFacet.Main for every URL found in text."""
    try:
        from atproto import models as atproto_models
    except ImportError:
        return []
    facets = []
    text_bytes = text.encode("utf-8")
    for match in _URL_RE.finditer(text):
        byte_start = len(text[:match.start()].encode("utf-8"))
        byte_end   = len(text[:match.end()].encode("utf-8"))
        facets.append(
            atproto_models.AppBskyRichtextFacet.Main(
                index=atproto_models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=byte_start,
                    byte_end=byte_end,
                ),
                features=[atproto_models.AppBskyRichtextFacet.Link(uri=match.group())],
            )
        )
    return facets
