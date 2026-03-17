"""
SnowBot — top-level orchestrator.

Wires alert channels → snow service → notification channels.

Data flow:
  AlertChannel (file / firehose / dm)
      → AlertRequest
          → SnowService.lookup(raw_location)
              → SnowReport
                  → SnowFormatter.format_thread(report)
                      → NotificationPayload
                          → NotificationChannel (bluesky_post / bluesky_dm)

Routing rules:
  - Requests from "dm" channel      → BlueskyDMNotifyChannel
  - Requests from "firehose"        → BlueskyPostNotifyChannel (public reply)
  - Requests from "file" channel    → BlueskyPostNotifyChannel (broadcast post)

Usage:
    from bluesky_snowbot.config.settings import Settings
    from bot import build_bot

    bot = build_bot(Settings.load())
    bot.start()   # blocks until SIGINT / SIGTERM
"""

from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime
from typing import Optional

from bluesky_snowbot.channels.alert.base import AlertChannel, AlertRequest
from bluesky_snowbot.channels.alert.dm_poller import DMAlertChannel
from bluesky_snowbot.channels.alert.file_watcher import FileWatcherAlertChannel
from bluesky_snowbot.channels.alert.firehose import FirehoseAlertChannel
from bluesky_snowbot.channels.notify.base import (
    NotificationChannel, NotificationPayload,
)
from bluesky_snowbot.channels.notify.bluesky_dm import BlueskyDMNotifyChannel
from bluesky_snowbot.channels.notify.bluesky_post import BlueskyPostNotifyChannel
from bluesky_snowbot.config.settings import Settings
from bluesky_snowbot.snow.formatter import SnowFormatter
from bluesky_snowbot.snow.service import SnowService
from bluesky_snowbot.storage.db import Database

logger = logging.getLogger(__name__)


class SnowBot:
    """
    Assembles and runs the full bot pipeline.

    Alert channels and notification channels are registered separately,
    so adding a new channel type requires only instantiating and registering it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings  = settings
        self._db        = Database(path=settings.db_path)
        self._formatter = SnowFormatter()
        self._snow      = SnowService(
            db=self._db,
            cache_ttl_minutes=settings.snow_cache_ttl_minutes,
        )

        self._alert_channels: list[AlertChannel] = []
        self._notify_channels: dict[str, NotificationChannel] = {}
        self._running = False
        self._lock    = threading.Lock()

    # ------------------------------------------------------------------
    # Channel registration
    # ------------------------------------------------------------------

    def register_alert_channel(self, channel: AlertChannel) -> None:
        channel.on_request(self._handle_request)
        self._alert_channels.append(channel)
        logger.info("Registered alert channel: %s", channel)

    def register_notify_channel(self, channel: NotificationChannel) -> None:
        self._notify_channels[channel.CHANNEL_NAME] = channel
        logger.info("Registered notify channel: %s", channel)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, block: bool = True) -> None:
        self._settings.ensure_directories()
        self._db.connect()
        self._running = True
        for ch in self._alert_channels:
            ch.start()
        logger.info("SnowBot running. %d alert channel(s), %d notify channel(s).",
                    len(self._alert_channels), len(self._notify_channels))
        if block:
            self._block_until_signal()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("SnowBot shutting down...")
        for ch in self._alert_channels:
            try:
                ch.stop()
            except Exception as exc:
                logger.warning("Error stopping %s: %s", ch, exc)
        self._db.close()
        logger.info("SnowBot stopped.")

    # ------------------------------------------------------------------
    # Request handler
    # ------------------------------------------------------------------

    def _handle_request(self, request: AlertRequest) -> None:
        logger.info(
            "[bot] Request from=%s channel=%s location=%r command=%r",
            request.requester_handle, request.source_channel,
            request.raw_location, request.command,
        )

        db_id = self._db.save_request(
            source_channel=request.source_channel,
            raw_content=request.raw_content,
            requester_handle=request.requester_handle,
            raw_location=request.raw_location,
            status="pending",
            ingested_at=request.received_at.isoformat(),
            source_uri=request.reply_to_uri if request.source_channel == "firehose" else None,
        )
        if db_id is None:
            logger.debug("[bot] Duplicate request dropped: channel=%s uri=%s",
                         request.source_channel, request.reply_to_uri)
            return
        request.db_id = db_id

        # DM preference commands
        if request.command:
            self._handle_dm_command(request)
            self._db.update_request_status(db_id, "complete")
            return

        # Home location fallback for DMs with no explicit location
        if not request.raw_location and request.source_channel == "dm" and request.requester_did:
            prefs = self._db.get_user_prefs(request.requester_did)
            if prefs and prefs.get("home_raw"):
                request.raw_location = prefs["home_raw"]
                logger.info("[bot] Using home location %r for %s",
                            request.raw_location, request.requester_handle)

        # No location
        if not request.raw_location:
            if request.source_channel == "file":
                self._broadcast_raw(request)
            elif request.source_channel == "dm":
                self._send_dm_reply(request, (
                    "Send me a resort or mountain name for snow conditions.\n"
                    "Examples: Vail  or  Park City  or  Mammoth\n\n"
                    "Set a home resort:\n"
                    "  set home Breckenridge\n\n"
                    "Send 'help' for all commands."
                ))
            else:
                self._send_help_reply(request)
            self._db.update_request_status(db_id, "complete")
            return

        # Snow lookup
        self._db.update_request_processing_start(db_id)
        try:
            report = self._snow.lookup(request.raw_location)
        except ValueError as exc:
            logger.warning("[bot] Location not resolved: %s", exc)
            self._send_error_reply(
                request, f"Sorry, I couldn't find snow conditions for {request.raw_location!r}."
            )
            self._db.update_request_status(db_id, "error")
            return
        except Exception as exc:
            logger.error("[bot] Snow lookup failed: %s", exc)
            self._send_error_reply(request, "Sorry, snow data is temporarily unavailable.")
            self._db.update_request_status(db_id, "error")
            return

        # Format
        self._db.update_request_formatting_start(db_id)
        thread_posts = self._formatter.format_thread(report)
        _append_latency_footer(thread_posts, request.received_at, self._settings.server_type)

        target_channel = self._route(request)
        payload = NotificationPayload(
            request_db_id=db_id,
            post_thread=thread_posts,
            reply_to_uri=request.reply_to_uri,
            reply_to_cid=request.reply_to_cid,
            recipient_handle=request.requester_handle,
            target_channel=target_channel,
        )

        delivery_started_at = _now()
        result = self._deliver(payload)
        delivery_finished_at = _now()

        for i, text in enumerate(thread_posts):
            self._db.save_response(
                request_id=db_id,
                notify_channel=result.channel,
                message_text=text,
                post_index=i,
                post_uri=result.post_uri if i == 0 else None,
                delivery_started_at=delivery_started_at,
                delivery_finished_at=delivery_finished_at,
            )

        self._db.update_request_processing_finish(db_id)
        self._db.update_request_mark_slow(
            db_id,
            delivery_finished_at=delivery_finished_at,
            source_created_at=None,
        )
        self._db.update_request_status(db_id, "complete")

    def _handle_dm_command(self, request: AlertRequest) -> None:
        cmd = request.command
        did = request.requester_did

        if cmd == "set_home":
            raw = request.raw_content.strip()
            for prefix in ("set home ", "set home"):
                if raw.lower().startswith(prefix):
                    raw = raw[len(prefix):].strip()
                    break
            if not raw:
                self._send_dm_reply(request, "Please provide a resort: set home Vail")
                return
            try:
                report = self._snow.lookup(raw)
                display = report.location.name
                self._db.set_user_prefs(did, handle=request.requester_handle,
                                        home_raw=raw, home_display=display,
                                        home_lat=report.location.lat,
                                        home_lon=report.location.lon)
                self._send_dm_reply(request, f"Home resort set to {display}. DM me anytime for snow conditions!")
            except Exception:
                self._send_dm_reply(request,
                    f"Couldn't find resort: {raw!r}. Try a known resort name like 'Vail' or 'Park City'.")

        elif cmd == "clear_home":
            if did:
                self._db.clear_home(did)
            self._send_dm_reply(request, "Home resort cleared.")

        elif cmd == "reset_prefs":
            if did:
                self._db.reset_prefs(did)
            self._send_dm_reply(request, "Preferences reset to defaults.")

        elif cmd == "settings":
            prefs = self._db.get_user_prefs(did) if did else None
            if prefs:
                home = prefs.get("home_display") or prefs.get("home_raw") or "not set"
            else:
                home = "not set"
            self._send_dm_reply(request, f"Your settings:\n  Home resort: {home}")

        elif cmd == "help":
            self._send_dm_reply(request, (
                "SnowBot commands:\n"
                "  Vail  or  Park City — get snow conditions\n"
                "  set home Breckenridge — save home resort\n"
                "  settings — view your preferences\n"
                "  reset — clear all preferences\n"
                "  clear home — remove home resort"
            ))

        else:
            logger.warning("[bot] Unknown DM command: %r", cmd)

    def _send_dm_reply(self, request: AlertRequest, message: str) -> None:
        payload = NotificationPayload(
            request_db_id=request.db_id,
            post_thread=[message],
            reply_to_uri=request.reply_to_uri,
            recipient_handle=request.requester_handle,
            target_channel="bluesky_dm",
        )
        self._deliver(payload)

    def _send_help_reply(self, request: AlertRequest) -> None:
        help_text = (
            "Hi! Mention me with a ski resort name to get snow conditions.\n"
            "Examples:\n"
            "  @snowbot.bsky.social Vail\n"
            "  @snowbot.bsky.social Park City\n"
            "  @snowbot.bsky.social Mammoth"
        )
        payload = NotificationPayload(
            request_db_id=request.db_id,
            post_thread=[help_text],
            reply_to_uri=request.reply_to_uri,
            reply_to_cid=request.reply_to_cid,
            recipient_handle=request.requester_handle,
            target_channel=self._route(request),
        )
        self._deliver(payload)

    def _broadcast_raw(self, request: AlertRequest) -> None:
        payload = NotificationPayload(
            request_db_id=request.db_id,
            post_thread=[request.raw_content],
            target_channel="bluesky_post",
        )
        self._deliver(payload)

    def _send_error_reply(self, request: AlertRequest, message: str) -> None:
        payload = NotificationPayload(
            request_db_id=request.db_id,
            post_thread=[message],
            reply_to_uri=request.reply_to_uri,
            reply_to_cid=request.reply_to_cid,
            recipient_handle=request.requester_handle,
            target_channel=self._route(request),
        )
        self._deliver(payload)

    # ------------------------------------------------------------------
    # Routing and delivery
    # ------------------------------------------------------------------

    def _route(self, request: AlertRequest) -> str:
        if request.source_channel == "dm":
            return "bluesky_dm"
        return "bluesky_post"

    def _deliver(self, payload: NotificationPayload):
        channel = self._notify_channels.get(payload.target_channel)
        if channel is None:
            channel = next(iter(self._notify_channels.values()), None)
        if channel is None:
            logger.error("[bot] No notification channel available!")
            from bluesky_snowbot.channels.notify.base import NotificationResult
            return NotificationResult(success=False, channel="none", error="No channel")
        return channel.send(payload)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _block_until_signal(self) -> None:
        stop_event = threading.Event()

        def _handler(sig, frame):
            logger.info("Signal %s received — stopping.", sig)
            stop_event.set()

        signal.signal(signal.SIGINT,  _handler)
        signal.signal(signal.SIGTERM, _handler)
        stop_event.wait()
        self.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat()


def _append_latency_footer(thread_posts: list[str], received_at: datetime,
                            server_type: str = "laptop") -> None:
    elapsed = (datetime.utcnow() - received_at).total_seconds()
    footer = f"\n\nResponded in {elapsed:.1f}s on a {server_type}."
    if len(thread_posts[-1]) + len(footer) <= 300:
        thread_posts[-1] += footer


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def build_bot(settings: Settings) -> SnowBot:
    """Constructs a fully wired SnowBot with all standard channels."""
    bot = SnowBot(settings)

    bot.register_alert_channel(FileWatcherAlertChannel(settings))
    bot.register_alert_channel(FirehoseAlertChannel(settings))
    bot.register_alert_channel(DMAlertChannel(settings, db=bot._db))

    bot.register_notify_channel(
        BlueskyPostNotifyChannel(settings.bluesky_handle, settings.bluesky_app_password)
    )
    bot.register_notify_channel(
        BlueskyDMNotifyChannel(settings.bluesky_handle, settings.bluesky_app_password)
    )

    return bot
