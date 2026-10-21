"""
Snowbot — top-level orchestrator.

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
from bluesky_snowbot.content import ContentLibrary
from bluesky_snowbot.menu import (
    classify_menu_input, format_help, format_learn_menu,
    format_link_detail, format_main_menu,
    format_photo_caption,
    format_playlist_detail, format_playlist_menu, format_regions_menu,
    find_menu_keyword, format_resorts_menu, format_small_talk_reply, is_menu_trigger,
    is_small_talk,
    STATE_NAMES,
)
from bluesky_snowbot.snow.resolver import REGION_GROUPS, _RESORTS_BY_REGION
from bluesky_snowbot.snow.formatter import SnowFormatter
from bluesky_snowbot.snow.service import SnowService
from bluesky_snowbot.storage.db import Database

logger = logging.getLogger(__name__)


class Snowbot:
    """
    Assembles and runs the full bot pipeline.

    Alert channels and notification channels are registered separately,
    so adding a new channel type requires only instantiating and registering it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings  = settings
        self._db        = Database(path=settings.db_path)
        self._formatter = SnowFormatter()
        self._content   = ContentLibrary.load(photos_dir=settings.photos_path)
        self._snow      = SnowService(
            db=self._db,
            cache_ttl_minutes=settings.snow_cache_ttl_minutes,
            snocountry_key=settings.snocountry_key,
            snocountry_mock=settings.snocountry_mock,
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
        logger.info("Snowbot running. %d alert channel(s), %d notify channel(s).",
                    len(self._alert_channels), len(self._notify_channels))
        if block:
            self._block_until_signal()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("Snowbot shutting down...")
        for ch in self._alert_channels:
            try:
                ch.stop()
            except Exception as exc:
                logger.warning("Error stopping %s: %s", ch, exc)
        self._db.close()
        logger.info("Snowbot stopped.")

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

        # Menu / session navigation (DM only)
        resort_selected = False
        if request.source_channel == "dm" and not request.command:
            raw = request.raw_content.strip()

            # Numeric selection within active session (takes priority over menu triggers)
            if raw.isdigit() and request.requester_did:
                session = self._db.get_dm_session(
                    request.requester_did, self._settings.dm_session_ttl_minutes
                )
                if session and self._handle_session_selection(request, session, int(raw)):
                    self._db.update_request_status(db_id, "complete")
                    return
                # A resort pick sets raw_location and falls through to the snow lookup
                resort_selected = session is not None and session.get("menu") == "resorts"

            # Small talk → short nudge instead of a geocoded "location"
            if not resort_selected and is_small_talk(raw):
                self._send_dm_reply(request, format_small_talk_reply())
                self._db.update_request_status(db_id, "complete")
                return

            # Named keyword → menu branch; failing that, a keyword inside a longer
            # message ("show me a photo"), unless the message names a resort
            branch = None
            if not resort_selected:
                branch = classify_menu_input(raw)
                if branch is None and not self._snow.names_known_resort(raw):
                    branch = find_menu_keyword(raw)
            if branch:
                self._handle_menu_branch(request, branch)
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
                # Public mention with no resort name — stay quiet rather than reply to chatter
                logger.info("[bot] Mention without a location — not replying: %r",
                            request.raw_content[:80])
            self._db.update_request_status(db_id, "complete")
            return

        # Snow lookup
        self._db.update_request_processing_start(db_id)
        try:
            report = self._snow.lookup(request.raw_location)
        except ValueError as exc:
            logger.warning("[bot] Location not resolved: %s", exc)
            if request.source_channel == "firehose":
                # Likely chatter, not a resort — don't post a public "couldn't find" reply
                self._db.update_request_status(db_id, "ignored")
                return
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
        if resort_selected:
            _append_if_fits(thread_posts, "\n\nReply 'back' for the resort list.")
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

        elif cmd == "set_state":
            raw = request.raw_content.strip()
            for prefix in ("set state ", "set state"):
                if raw.lower().startswith(prefix):
                    raw = raw[len(prefix):].strip()
                    break
            code = raw.upper()
            if code in STATE_NAMES:
                if did:
                    self._db.set_home_state(did, code)
                self._send_dm_reply(request,
                    f"Home region set to {STATE_NAMES[code]} ({code}). "
                    f"Next time you ask for snow reports I'll start there.")
            else:
                valid = ", ".join(sorted(STATE_NAMES.keys()))
                self._send_dm_reply(request,
                    f"Unknown region: {raw!r}\nValid codes: {valid}")

        elif cmd == "clear_state":
            if did:
                self._db.clear_home_state(did)
            self._send_dm_reply(request, "Home region cleared.")

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
                state_code = prefs.get("home_state")
                home_state = (
                    f"{STATE_NAMES.get(state_code, state_code)} ({state_code})"
                    if state_code else "not set"
                )
            else:
                home  = "not set"
                home_state = "not set"
            self._send_dm_reply(request,
                f"Your settings:\n  Home resort: {home}\n  Home region: {home_state}"
            )

        elif cmd == "help":
            self._send_dm_reply(request, format_help())

        else:
            logger.warning("[bot] Unknown DM command: %r", cmd)

    def _handle_menu_branch(self, request: AlertRequest, branch: str) -> None:
        did = request.requester_did

        if branch == "menu":
            if did:
                self._db.clear_dm_session(did)
            self._send_dm_reply(request, format_main_menu())

        elif branch == "snow_report":
            # If user has a home state saved, jump straight to the resorts in its region
            prefs = self._db.get_user_prefs(did) if did else None
            home_state = prefs.get("home_state") if prefs else None
            home_region = next(
                (r for r, states in REGION_GROUPS.items() if home_state in states), None
            )
            if home_region and home_region in _RESORTS_BY_REGION:
                resorts = _RESORTS_BY_REGION[home_region]
                if did:
                    self._db.set_dm_session(did, "resorts", state=home_region)
                for _msg in format_resorts_menu(home_region, resorts):
                    self._send_dm_reply(request, _msg)
            else:
                if did:
                    self._db.set_dm_session(did, "regions")
                for _msg in format_regions_menu(_RESORTS_BY_REGION):
                    self._send_dm_reply(request, _msg)

        elif branch == "back":
            self._handle_back(request)

        elif branch == "change_state":
            if did:
                self._db.set_dm_session(did, "regions")
            for _msg in format_regions_menu(_RESORTS_BY_REGION):
                self._send_dm_reply(request, _msg)

        elif branch == "learn":
            if did:
                self._db.set_dm_session(did, "learn")
            for msg in format_learn_menu(self._content):
                self._send_dm_reply(request, msg)

        elif branch == "photos":
            # Photos posted publicly (DM image embeds not supported by Bluesky).
            # When DM image support is added, replace _post_public_photo with _send_photo_dm.
            photo = self._content.random_photo()
            if photo:
                self._post_public_photo(request, photo)
            else:
                self._send_dm_reply(request, "No photos available right now.")

        elif branch == "playlist":
            if did:
                self._db.set_dm_session(did, "playlist")
            for msg in format_playlist_menu(self._content):
                self._send_dm_reply(request, msg)

        elif branch == "help":
            self._send_dm_reply(request, format_help())

    def _handle_back(self, request: AlertRequest) -> None:
        """Navigate one level up based on current session context."""
        did = request.requester_did
        session = self._db.get_dm_session(did, self._settings.dm_session_ttl_minutes) if did else None

        if session is None:
            # No session — "back" from anywhere unknown goes to main menu
            if did:
                self._db.clear_dm_session(did)
            self._send_dm_reply(request, format_main_menu())

        elif session["menu"] == "report":
            # Back from a report picked off a resort list → that resort list
            region = session.get("state", "")
            resorts = _RESORTS_BY_REGION.get(region, [])
            if did:
                self._db.set_dm_session(did, "resorts", state=region)
            for _msg in format_resorts_menu(region, resorts):
                self._send_dm_reply(request, _msg)

        elif session["menu"] == "resorts":
            # Back from resort list → states menu
            if did:
                self._db.set_dm_session(did, "regions")
            for _msg in format_regions_menu(_RESORTS_BY_REGION):
                self._send_dm_reply(request, _msg)

        elif session["menu"] == "regions":
            # Back from states menu → main menu
            if did:
                self._db.clear_dm_session(did)
            self._send_dm_reply(request, format_main_menu())

        else:
            # Back from learn / playlist / any other leaf → main menu
            if did:
                self._db.clear_dm_session(did)
            self._send_dm_reply(request, format_main_menu())

    def _handle_session_selection(self, request: AlertRequest, session: dict, index: int) -> bool:
        """
        Handle a numeric selection within an active session.
        Returns True if handled, False if the number is out of range or session unknown.
        """
        did  = request.requester_did
        menu = session.get("menu")

        if menu == "regions":
            regions = list(_RESORTS_BY_REGION.keys())
            if not 1 <= index <= len(regions):
                self._send_dm_reply(request,
                    f"Please reply with a number between 1 and {len(regions)}.")
                return True
            region = regions[index - 1]
            resorts = _RESORTS_BY_REGION[region]
            if did:
                self._db.set_dm_session(did, "resorts", state=region)
            for _msg in format_resorts_menu(region, resorts):
                self._send_dm_reply(request, _msg)
            return True

        elif menu == "resorts":
            region = session.get("state", "")
            resorts = _RESORTS_BY_REGION.get(region, [])
            if not 1 <= index <= len(resorts):
                self._send_dm_reply(request,
                    f"Please reply with a number between 1 and {len(resorts)}.")
                return True
            resort_entry = resorts[index - 1]
            # Drop the numbered list so later bare numbers mean the main menu again,
            # but remember the region so "back" can reopen it
            if did:
                self._db.set_dm_session(did, "report", state=region)
            request.raw_location = resort_entry["display"]
            return False  # fall through to normal snow lookup

        elif menu == "learn":
            self._handle_learn_selection(request, index)
            return True

        elif menu == "playlist":
            self._handle_playlist_selection(request, index)
            return True

        return False

    def _handle_learn_selection(self, request: AlertRequest, index: int) -> None:
        self._send_dm_reply(request, format_link_detail(self._content, index))

    def _handle_playlist_selection(self, request: AlertRequest, index: int) -> None:
        self._send_dm_reply(request, format_playlist_detail(self._content, index))

    # _send_photo_dm disabled — Bluesky DMs do not support image embeds.
    # Preserved for when the feature becomes available.
    # def _send_photo_dm(self, request: AlertRequest, photo) -> None:
    #     self._send_dm_reply(request, format_photo_caption(photo))

    def _post_public_photo(self, request: AlertRequest, photo) -> None:
        """Post a random photo publicly, mentioning the requester."""
        channel = self._notify_channels.get("bluesky_post")
        if channel is None:
            logger.warning("[bot] No bluesky_post channel available for photo post")
            self._send_dm_reply(request, "Sorry, photo posting is unavailable right now.")
            return
        try:
            uri = channel.post_photo(
                photo_path=str(photo.path),
                caption=photo.caption,
                mention_handle=request.requester_handle or "",
                mention_did=request.requester_did or "",
            )
            logger.info("[bot] Posted photo publicly: %s", uri)
            # The post lives on the bot's own profile, not the requester's
            post_url = _uri_to_url(uri, self._settings.bluesky_handle)
            msg = f"📸 Just posted a snow photo for you!\n\n{post_url}" if post_url else "📸 Just posted a snow photo for you!"
            self._send_dm_reply(request, msg)
        except Exception as exc:
            logger.error("[bot] Public photo post failed: %s", exc)
            self._send_dm_reply(request, "Sorry, couldn't post a photo right now.")

    def _send_dm_reply(self, request: AlertRequest, message: str) -> None:
        elapsed = (datetime.utcnow() - request.received_at).total_seconds()
        footer = f"\n\n({elapsed:.1f}s on {self._settings.server_type})"
        if len(message) + len(footer) <= 300:
            message = message + footer
        payload = NotificationPayload(
            request_db_id=request.db_id,
            post_thread=[message],
            reply_to_uri=request.reply_to_uri,
            recipient_handle=request.requester_handle,
            target_channel="bluesky_dm",
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


def _uri_to_url(uri: Optional[str], handle: Optional[str]) -> Optional[str]:
    """Convert an AT URI (at://did/collection/rkey) to a bsky.app post URL."""
    if not uri or not handle:
        return None
    try:
        # at://did:plc:xxx/app.bsky.feed.post/rkey
        rkey = uri.rsplit("/", 1)[-1]
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    except Exception:
        return None


def _append_if_fits(thread_posts: list[str], text: str) -> None:
    if len(thread_posts[-1]) + len(text) <= 300:
        thread_posts[-1] += text


def _append_latency_footer(thread_posts: list[str], received_at: datetime,
                            server_type: str = "laptop") -> None:
    elapsed = (datetime.utcnow() - received_at).total_seconds()
    footer = f"\n\nResponded in {elapsed:.1f}s on a {server_type}."
    if len(thread_posts[-1]) + len(footer) <= 300:
        thread_posts[-1] += footer


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def build_bot(settings: Settings) -> Snowbot:
    """Constructs a fully wired Snowbot with all standard channels."""
    bot = Snowbot(settings)

    bot.register_alert_channel(FileWatcherAlertChannel(settings))
    bot.register_alert_channel(FirehoseAlertChannel(settings))
    bot.register_alert_channel(DMAlertChannel(
        settings, db=bot._db,
        poll_interval=settings.dm_poll_interval_sec,
        active_interval=settings.dm_poll_active_interval_sec,
        active_window=settings.dm_poll_active_window_sec,
    ))

    bot.register_notify_channel(
        BlueskyPostNotifyChannel(settings.bluesky_handle, settings.bluesky_app_password)
    )
    bot.register_notify_channel(
        BlueskyDMNotifyChannel(settings.bluesky_handle, settings.bluesky_app_password)
    )

    return bot
