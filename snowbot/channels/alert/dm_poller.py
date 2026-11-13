"""
DMAlertChannel

Polls the Bluesky chat API to check for new Direct Messages addressed to
the bot. Polling is adaptive: every `poll_interval` seconds when idle, then
every `active_interval` seconds for `active_window` seconds after the last
incoming DM, so replies in a conversation come back quickly. Messages containing a resort name
or location are dispatched as AlertRequests with source_channel="dm".

Message deduplication is two-layered:
  1. In-memory set (fast, cleared on restart).
  2. DB seen_dm_ids table (survives restarts; requires db= parameter).

DM commands recognised:
  set home <resort>   — save home resort
  clear home          — remove saved home resort
  settings / prefs    — show current preferences
  reset               — clear all preferences
  help / ?            — show command list

Dependencies: pip install atproto
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from snowbot.channels.alert.base import AlertChannel, AlertRequest
from snowbot.config.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL   = 5.0    # idle
DEFAULT_ACTIVE_INTERVAL = 1.0    # while a conversation is active
DEFAULT_ACTIVE_WINDOW   = 120.0  # stay fast this long after the last incoming DM

_LOG_CREATE_MESSAGE = "chat.bsky.convo.defs#logCreateMessage"


class DMAlertChannel(AlertChannel):
    """
    Polls Bluesky Direct Messages and dispatches AlertRequests for
    messages that contain snow resort requests.
    """

    CHANNEL_NAME = "dm"

    def __init__(
        self,
        settings: Settings,
        db=None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        active_interval: float = DEFAULT_ACTIVE_INTERVAL,
        active_window: float = DEFAULT_ACTIVE_WINDOW,
    ) -> None:
        super().__init__()
        self._handle        = settings.bluesky_handle
        self._app_password  = settings.bluesky_app_password
        self._db            = db
        self._poll_interval = poll_interval
        self._active_interval = min(active_interval, poll_interval)
        self._active_window = active_window
        self._last_activity: Optional[float] = None  # monotonic time of last incoming DM
        self._stop_event    = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client        = None
        self._chat_client   = None
        self._own_did: str  = ""
        self._cursor: Optional[str] = None
        self._seen_ids: set[str] = set()
        self._handle_cache: dict[str, str] = {}  # DID → handle, persists for lifetime of process

    # ------------------------------------------------------------------
    # AlertChannel interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="DMPoller",
            daemon=True,
        )
        self._thread.start()
        logger.info("[dm] Started — polling every %.0fs (%.1fs for %.0fs after a DM)",
                    self._poll_interval, self._active_interval, self._active_window)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._poll_interval + 2)
        logger.info("[dm] Stopped.")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        self._login()
        was_active = False
        while not self._stop_event.is_set():
            try:
                if self._check_dms():
                    self._last_activity = time.monotonic()
            except Exception as exc:
                logger.warning("[dm] Error during poll: %s", exc)
                if "auth" in str(exc).lower() or "expired" in str(exc).lower():
                    self._login()
            interval = self._next_interval(time.monotonic())
            is_active = interval < self._poll_interval
            if is_active != was_active:
                logger.info("[dm] Polling every %.1fs (%s)", interval,
                            "conversation active" if is_active else "idle")
                was_active = is_active
            self._stop_event.wait(timeout=interval)

    def _next_interval(self, now: float) -> float:
        """Fast interval within the active window after the last incoming DM, else idle."""
        if self._last_activity is not None and now - self._last_activity <= self._active_window:
            return self._active_interval
        return self._poll_interval

    def _login(self) -> None:
        try:
            from atproto import Client
            self._client = Client()
            self._client.login(self._handle, self._app_password)
            self._own_did = self._client.me.did
            self._chat_client = self._client.with_bsky_chat_proxy()
            logger.info("[dm] Authenticated as %s (DID: %s)", self._handle, self._own_did)
        except ImportError:
            logger.error("[dm] atproto not installed: pip install atproto")
            self._stop_event.set()
        except Exception as exc:
            logger.error("[dm] Login failed: %s: %s", type(exc).__name__, exc or repr(exc))

    def _check_dms(self) -> int:
        """Fetch new log entries and dispatch them. Returns the number of incoming DMs."""
        if self._chat_client is None:
            return 0

        try:
            cursor, logs = self._fetch_log()
        except Exception as exc:
            logger.warning("[dm] get_log failed: %s", exc)
            return 0

        if cursor:
            self._cursor = cursor

        incoming = 0
        for log in logs:
            try:
                msg = log.message
                if getattr(getattr(msg, "sender", None), "did", "") == self._own_did:
                    continue

                msg_id = getattr(msg, "id", None)
                if not msg_id or self._is_seen(msg_id):
                    continue

                text = getattr(msg, "text", "") or ""
                command = self._extract_command(text)
                raw_location = None if command else _extract_location(text)

                self._mark_seen(msg_id, log.convo_id)

                sender_did    = getattr(getattr(msg, "sender", None), "did", "") or ""
                sender_handle = self._did_to_handle(sender_did) or sender_did

                request = AlertRequest(
                    source_channel="dm",
                    requester_handle=sender_handle,
                    requester_did=sender_did or None,
                    raw_location=raw_location,
                    raw_content=text,
                    reply_to_uri=log.convo_id,
                    command=command,
                    received_at=_parse_sent_at(getattr(msg, "sent_at", None)),
                )
                incoming += 1
                self._dispatch(request)

            except Exception as exc:
                logger.debug("[dm] Skipping malformed log entry: %s", exc)
        return incoming

    def _fetch_log(self) -> tuple[Optional[str], list]:
        """
        Fetch chat log entries since the cursor; return (cursor, LogCreateMessage models).

        The response is parsed per entry rather than as a whole: Bluesky adds new
        log types (e.g. logReadConvo) before the SDK knows them, and one unknown
        entry would otherwise fail the whole response and stall the cursor.
        """
        from atproto import models as atproto_models

        params = atproto_models.ChatBskyConvoGetLog.Params(cursor=self._cursor)
        resp = self._chat_client.invoke_query(
            "chat.bsky.convo.getLog", params=params, output_encoding="application/json",
        )
        content = resp.content if isinstance(resp.content, dict) else {}

        logs = []
        for entry in content.get("logs", []):
            if not isinstance(entry, dict) or entry.get("$type") != _LOG_CREATE_MESSAGE:
                continue  # read receipts, reactions, convo events, unknown future types
            try:
                logs.append(atproto_models.ChatBskyConvoDefs.LogCreateMessage.model_validate(entry))
            except Exception as exc:
                logger.warning("[dm] Skipping unparseable message log entry: %s", exc)
        return content.get("cursor"), logs

    # ------------------------------------------------------------------
    # Deduplication helpers
    # ------------------------------------------------------------------

    def _is_seen(self, msg_id: str) -> bool:
        if msg_id in self._seen_ids:
            return True
        if self._db is not None:
            try:
                return self._db.is_dm_seen(msg_id)
            except Exception:
                pass
        return False

    def _mark_seen(self, msg_id: str, convo_id: str) -> None:
        self._seen_ids.add(msg_id)
        if self._db is not None:
            try:
                self._db.mark_dm_seen(msg_id, convo_id)
            except Exception as exc:
                logger.debug("[dm] Could not persist seen DM %s: %s", msg_id, exc)

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _did_to_handle(self, did: str) -> Optional[str]:
        if not did or self._client is None:
            return None
        if did in self._handle_cache:
            return self._handle_cache[did]
        try:
            profile = self._client.app.bsky.actor.get_profile({"actor": did})
            handle = getattr(profile, "handle", None)
            if handle:
                self._handle_cache[did] = handle
            return handle
        except Exception:
            return None

    @staticmethod
    def _extract_command(text: str) -> Optional[str]:
        """Detects preference commands in DM text."""
        normalised = text.strip().lower()
        if normalised.startswith("set home"):
            return "set_home"
        if normalised.startswith(("set state", "set region")):
            return "set_state"
        if normalised in ("clear home", "clearhome", "remove home", "reset home"):
            return "clear_home"
        if normalised in ("clear state", "remove state", "reset state",
                          "clear region", "remove region", "reset region"):
            return "clear_state"
        if normalised in ("reset", "reset prefs", "reset preferences"):
            return "reset_prefs"
        if normalised in ("settings", "prefs", "preferences", "my settings"):
            return "settings"
        if normalised in ("help", "?", "commands"):
            return "help"
        return None


def _parse_sent_at(sent_at: Optional[str]) -> datetime:
    """Parse the message's sent_at ISO timestamp to a UTC-aware datetime.
    Falls back to utcnow() if absent or unparseable."""
    if sent_at:
        try:
            dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.utcnow()


def _extract_location(text: str) -> Optional[str]:
    """
    Extracts a resort/location name from DM text.
    Returns the trimmed text (3–60 chars) as the raw location for the resolver.
    """
    clean = text.strip()
    if 3 <= len(clean) <= 60:
        return clean
    return None
