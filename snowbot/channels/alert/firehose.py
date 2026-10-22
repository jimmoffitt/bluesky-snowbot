"""
FirehoseAlertChannel

Connects to the Bluesky AT Protocol firehose and streams all public posts
in real time. Posts that mention @snowbot.bsky.social are parsed for a
resort/location name and dispatched as AlertRequests.

Trigger detection:
  - Mention: post mentions @<bot_handle>

Location extraction: scans post text after the mention for a resort name.

Pattern examples the parser handles:
  "@snowbot.bsky.social Vail"
  "@snowbot.bsky.social Park City, UT"
  "@snowbot.bsky.social Mammoth Mountain"

Dependencies: pip install atproto
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from snowbot.channels.alert.base import AlertChannel, AlertRequest
from snowbot.config.settings import Settings

logger = logging.getLogger(__name__)

# Regex to capture location text after a mention trigger
_LOCATION_RE = re.compile(
    r"(?:@\S+)"                          # trigger: mention
    r"\s+"                               # whitespace
    r"([A-Za-z][^#@\n]{2,40}?)(?:\s*#|\s*@|$)",  # location token
    re.IGNORECASE,
)


class FirehoseAlertChannel(AlertChannel):
    """
    Streams the Bluesky public firehose and dispatches AlertRequests
    for posts that mention the bot handle.
    """

    CHANNEL_NAME = "firehose"

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._bot_handle = settings.bluesky_handle.lower().lstrip("@")
        self._bot_did: Optional[str] = self._resolve_bot_did(self._bot_handle)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen_uris: set[str] = set()
        self._seen_lock = threading.Lock()

    # ------------------------------------------------------------------
    # AlertChannel interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="FirehoseListener",
            daemon=True,
        )
        self._thread.start()
        logger.info("[firehose] Started — watching for @%s mentions", self._bot_handle)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[firehose] Stopped.")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            from atproto import (FirehoseSubscribeReposClient,
                                 parse_subscribe_repos_message, CAR,
                                 models as atproto_models)
            from atproto_firehose.models import MessageFrame
        except ImportError:
            import subprocess, sys
            logger.warning("[firehose] atproto not found — installing...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "atproto"],
                                  stdout=subprocess.DEVNULL)
            from atproto import (FirehoseSubscribeReposClient,
                                 parse_subscribe_repos_message, CAR,
                                 models as atproto_models)
            from atproto_firehose.models import MessageFrame

        client = FirehoseSubscribeReposClient()

        def on_message(message: MessageFrame) -> None:
            if self._stop_event.is_set():
                client.stop()
                return
            try:
                commit = parse_subscribe_repos_message(message)
                if not isinstance(commit, atproto_models.ComAtprotoSyncSubscribeRepos.Commit):
                    return
                if not commit.blocks:
                    return
                car = CAR.from_bytes(commit.blocks)
                if self._bot_did and commit.repo == self._bot_did:
                    return
                for op in commit.ops:
                    if op.action != "create":
                        continue
                    if not op.path.startswith("app.bsky.feed.post"):
                        continue
                    record_raw = car.blocks.get(op.cid)
                    if not record_raw:
                        continue
                    record = atproto_models.get_or_create(record_raw, strict=False)
                    if not isinstance(record, atproto_models.AppBskyFeedPost.Record):
                        continue
                    text = record.text or ""
                    if self._is_trigger(text):
                        request = self._build_request(text=text, repo=commit.repo, op=op)
                        if request:
                            if request.reply_to_uri:
                                with self._seen_lock:
                                    if request.reply_to_uri in self._seen_uris:
                                        logger.debug("[firehose] Dedup: %s", request.reply_to_uri)
                                        continue
                                    self._seen_uris.add(request.reply_to_uri)
                                    if len(self._seen_uris) > 10_000:
                                        keep = set(list(self._seen_uris)[5_000:])
                                        self._seen_uris.clear()
                                        self._seen_uris.update(keep)
                            self._dispatch(request)
            except Exception as exc:
                logger.debug("[firehose] Skipping malformed message: %s", exc)

        def on_error(error: Exception) -> None:
            if not self._stop_event.is_set():
                logger.warning("[firehose] Connection error: %s — will reconnect", error)

        while not self._stop_event.is_set():
            try:
                client.start(on_message, on_error)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                logger.warning("[firehose] Reconnecting after error: %s", exc)
                self._stop_event.wait(timeout=5)

    @staticmethod
    def _resolve_bot_did(handle: str) -> Optional[str]:
        import urllib.request as _ur
        import urllib.parse as _up
        import json as _json
        url = (
            "https://bsky.social/xrpc/com.atproto.identity.resolveHandle"
            f"?handle={_up.quote(handle)}"
        )
        try:
            with _ur.urlopen(url, timeout=10) as resp:
                did = _json.loads(resp.read()).get("did")
                logger.info("[firehose] Bot DID resolved: %s", did)
                return did
        except Exception as exc:
            logger.warning("[firehose] Could not resolve bot DID: %s", exc)
            return None

    def _is_trigger(self, text: str) -> bool:
        return f"@{self._bot_handle}" in text.lower()

    def _build_request(self, text: str, repo: str, op) -> Optional[AlertRequest]:
        raw_location = self._extract_location(text)

        rkey = getattr(op, "path", "").split("/")[-1] if hasattr(op, "path") else ""
        reply_uri = f"at://{repo}/app.bsky.feed.post/{rkey}" if rkey else None
        reply_cid = str(op.cid) if hasattr(op, "cid") and op.cid else None

        if raw_location is None:
            logger.debug("[firehose] Trigger from %s but no location found: %r", repo, text[:80])

        return AlertRequest(
            source_channel="firehose",
            requester_handle=repo,
            requester_did=repo,
            raw_location=raw_location,
            raw_content=text,
            reply_to_uri=reply_uri,
            reply_to_cid=reply_cid,
        )

    @staticmethod
    def _extract_location(text: str) -> Optional[str]:
        """Extracts resort/location text following a mention."""
        m = _LOCATION_RE.search(text)
        if m:
            candidate = m.group(1).strip()
            if candidate:
                return candidate
        return None
