"""
BlueskyPostNotifyChannel

Sends a snow report response as a public Bluesky post thread.
Index 0 of payload.post_thread is the root post (or reply to the
original request post if reply_to_uri is set). Subsequent posts
are threaded as replies to the preceding post.

Dependencies: pip install atproto
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from bluesky_snowbot.channels.notify.base import (
    NotificationChannel,
    NotificationPayload,
    NotificationResult,
)

logger = logging.getLogger(__name__)

INTER_POST_DELAY = 0.5  # seconds between posts in a thread


class BlueskyPostNotifyChannel(NotificationChannel):
    """
    Sends formatted snow reports as public Bluesky post threads.

    post_thread[0] → root post (replies to the original request if uri provided)
    post_thread[1] → reply to post_thread[0]
    post_thread[2] → reply to post_thread[1]
    """

    CHANNEL_NAME = "bluesky_post"

    def __init__(self, handle: str, app_password: str) -> None:
        self._handle       = handle
        self._app_password = app_password
        self._client       = None
        self._login()

    # ------------------------------------------------------------------
    # NotificationChannel interface
    # ------------------------------------------------------------------

    def send(self, payload: NotificationPayload) -> NotificationResult:
        if not payload.post_thread:
            return NotificationResult(
                success=False, channel=self.CHANNEL_NAME,
                error="Empty post_thread in payload",
            )

        if self._client is None:
            ok = self._login()
            if not ok:
                return NotificationResult(
                    success=False, channel=self.CHANNEL_NAME,
                    error="Not authenticated",
                )

        root_uri: Optional[str] = None
        root_cid: Optional[str] = None
        parent_uri: Optional[str] = None
        parent_cid: Optional[str] = None

        reply_to_uri = payload.reply_to_uri
        reply_to_cid = payload.reply_to_cid

        for i, text in enumerate(payload.post_thread):
            try:
                if i == 0:
                    post_ref = self._post(
                        text=text,
                        reply_uri=reply_to_uri,
                        reply_cid=reply_to_cid,
                        root_uri=reply_to_uri,
                        root_cid=reply_to_cid,
                    )
                else:
                    post_ref = self._post(
                        text=text,
                        reply_uri=parent_uri,
                        reply_cid=parent_cid,
                        root_uri=root_uri,
                        root_cid=root_cid,
                    )

                if post_ref is None:
                    raise RuntimeError("post() returned None")

                uri = getattr(post_ref, "uri", None) or str(post_ref)
                cid = getattr(post_ref, "cid", None)

                if i == 0:
                    root_uri = reply_to_uri or uri
                    root_cid = reply_to_cid if reply_to_uri else (str(cid) if cid else None)
                parent_uri = uri
                parent_cid = str(cid) if cid else None

                logger.info("[bluesky_post] Posted %d/%d: %s", i + 1, len(payload.post_thread), uri)

                if i < len(payload.post_thread) - 1:
                    time.sleep(INTER_POST_DELAY)

            except Exception as exc:
                logger.error("[bluesky_post] Failed on post %d: %s", i, exc)
                return NotificationResult(
                    success=False, channel=self.CHANNEL_NAME,
                    post_uri=root_uri, error=str(exc),
                )

        return NotificationResult(success=True, channel=self.CHANNEL_NAME, post_uri=root_uri)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _login(self) -> bool:
        try:
            from atproto import Client
            self._client = Client()
            self._client.login(self._handle, self._app_password)
            logger.info("[bluesky_post] Authenticated as %s", self._handle)
            return True
        except ImportError:
            logger.error("[bluesky_post] atproto not installed: pip install atproto")
            return False
        except Exception as exc:
            logger.error("[bluesky_post] Login failed: %s", exc)
            return False

    def _post(
        self,
        text: str,
        reply_uri: Optional[str] = None,
        reply_cid: Optional[str] = None,
        root_uri: Optional[str] = None,
        root_cid: Optional[str] = None,
    ):
        if self._client is None:
            raise RuntimeError("Not authenticated")

        reply_ref = None
        if reply_uri:
            from atproto import models as atproto_models
            root   = atproto_models.ComAtprotoRepoStrongRef.Main(
                uri=root_uri or reply_uri, cid=root_cid or reply_cid or "",
            )
            parent = atproto_models.ComAtprotoRepoStrongRef.Main(
                uri=reply_uri, cid=reply_cid or "",
            )
            reply_ref = atproto_models.AppBskyFeedPost.ReplyRef(root=root, parent=parent)

        return self._client.send_post(text=text, reply_to=reply_ref)

    def post_photo(self, photo_path: str, caption: str,
                   mention_handle: str = "", mention_did: str = "") -> Optional[str]:
        """Post a photo publicly, optionally mentioning a user. Returns the post URI."""
        if self._client is None:
            raise RuntimeError("Not authenticated")
        from atproto import models as atproto_models
        with open(photo_path, "rb") as f:
            img_data = f.read()

        # Build text: "@handle 📸 caption #Snowbot"
        hashtag  = "#Snowbot"
        body     = f"📸 {caption}"
        facets   = []

        if mention_handle and mention_did:
            mention_str  = f"@{mention_handle}"   # no trailing space in facet range
            text         = f"{mention_str} {body} {hashtag}"
            mention_end  = len(mention_str.encode("utf-8"))
            facets.append(
                atproto_models.AppBskyRichtextFacet.Main(
                    index=atproto_models.AppBskyRichtextFacet.ByteSlice(
                        byte_start=0, byte_end=mention_end,
                    ),
                    features=[atproto_models.AppBskyRichtextFacet.Mention(did=mention_did)],
                )
            )
        else:
            text = f"{body} {hashtag}"

        # Tag facet for #Snowbot
        tag_start = len(text.encode("utf-8")) - len(hashtag.encode("utf-8"))
        tag_end   = len(text.encode("utf-8"))
        facets.append(
            atproto_models.AppBskyRichtextFacet.Main(
                index=atproto_models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=tag_start, byte_end=tag_end,
                ),
                features=[atproto_models.AppBskyRichtextFacet.Tag(tag="Snowbot")],
            )
        )

        resp = self._client.send_image(
            text=text[:300],
            image=img_data,
            image_alt=caption,
            facets=facets,
        )
        return getattr(resp, "uri", None)
