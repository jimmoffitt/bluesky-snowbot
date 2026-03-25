"""
Database layer for bluesky_snowbot.

SQLite with WAL mode. Schema mirrors bluesky_weather_bot with snow-specific
cache table names. Shared tables (requests, responses, seen_dm_ids,
user_prefs, file_inbox_log) are structurally identical across both bots
to allow shared tooling and patterns.

Tables:
  requests        — every inbound alert/request, with latency timestamps
  responses       — every outbound notification, with delivery timestamps
  snow_cache      — TTL cache of SnowReport JSON blobs
  seen_dm_ids     — DM message IDs already processed (survives restarts)
  user_prefs      — per-user preferences set via DM
  file_inbox_log  — audit trail for file watcher activity

Views:
  latency_summary — per-request latency breakdown in seconds
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RETENTION_DAYS             = 90
SLOW_REQUEST_THRESHOLD_SEC = 30
DEFAULT_DB_PATH            = Path("data/snowbot.db")


class Database:

    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_schema()
        logger.info("Database connected: %s", self.path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        assert self._conn
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                source_channel          TEXT    NOT NULL,
                requester_handle        TEXT,
                raw_location            TEXT,
                resolved_location       TEXT,
                resolved_lat            REAL,
                resolved_lon            REAL,
                raw_content             TEXT    NOT NULL,
                status                  TEXT    NOT NULL DEFAULT 'pending',
                error_message           TEXT,
                source_created_at       TEXT,
                ingested_at             TEXT,
                processing_started_at   TEXT,
                formatting_started_at   TEXT,
                processing_finished_at  TEXT,
                is_slow                 INTEGER NOT NULL DEFAULT 0,
                source_uri              TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_requests_status   ON requests(status);
            CREATE INDEX IF NOT EXISTS idx_requests_ingested ON requests(ingested_at);
            CREATE INDEX IF NOT EXISTS idx_requests_handle   ON requests(requester_handle);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_requests_source_uri
                ON requests(source_uri)
                WHERE source_uri IS NOT NULL AND source_channel != 'dm';

            CREATE TABLE IF NOT EXISTS responses (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id           INTEGER NOT NULL REFERENCES requests(id),
                notify_channel       TEXT    NOT NULL,
                post_index           INTEGER NOT NULL DEFAULT 0,
                message_text         TEXT    NOT NULL,
                post_uri             TEXT,
                success              INTEGER NOT NULL DEFAULT 1,
                delivery_started_at  TEXT,
                delivery_finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_responses_request ON responses(request_id);

            CREATE TABLE IF NOT EXISTS snow_cache (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key     TEXT    NOT NULL UNIQUE,
                display_name  TEXT    NOT NULL,
                lat           REAL    NOT NULL,
                lon           REAL    NOT NULL,
                report_json   TEXT    NOT NULL,
                fetched_at    TEXT    NOT NULL,
                expires_at    TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snow_cache_key     ON snow_cache(cache_key);
            CREATE INDEX IF NOT EXISTS idx_snow_cache_expires ON snow_cache(expires_at);

            CREATE TABLE IF NOT EXISTS seen_dm_ids (
                message_id  TEXT PRIMARY KEY,
                convo_id    TEXT NOT NULL,
                seen_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_prefs (
                user_did      TEXT PRIMARY KEY,
                handle        TEXT,
                home_raw      TEXT,
                home_display  TEXT,
                home_lat      REAL,
                home_lon      REAL,
                home_state    TEXT,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dm_sessions (
                user_did    TEXT PRIMARY KEY,
                menu        TEXT NOT NULL,
                context     TEXT,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS file_inbox_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                filename      TEXT    NOT NULL,
                received_at   TEXT    NOT NULL,
                processed_at  TEXT,
                outcome       TEXT    NOT NULL,
                request_id    INTEGER REFERENCES requests(id),
                error_detail  TEXT
            );

            CREATE VIEW IF NOT EXISTS latency_summary AS
            SELECT
                r.id                    AS request_id,
                r.source_channel,
                r.requester_handle,
                r.resolved_location,
                r.status,
                r.is_slow,
                ROUND(
                    (JULIANDAY(r.ingested_at) - JULIANDAY(r.source_created_at))
                    * 86400.0, 3
                ) AS receive_latency_sec,
                ROUND(
                    (JULIANDAY(r.formatting_started_at) - JULIANDAY(r.processing_started_at))
                    * 86400.0, 3
                ) AS lookup_latency_sec,
                ROUND(
                    (JULIANDAY(r.processing_finished_at) - JULIANDAY(r.formatting_started_at))
                    * 86400.0, 3
                ) AS formatting_latency_sec,
                ROUND(
                    (JULIANDAY(resp.delivery_finished_at) - JULIANDAY(resp.delivery_started_at))
                    * 86400.0, 3
                ) AS delivery_latency_sec,
                ROUND(
                    (JULIANDAY(resp.delivery_finished_at) - JULIANDAY(r.source_created_at))
                    * 86400.0, 3
                ) AS end_to_end_latency_sec
            FROM requests r
            LEFT JOIN responses resp
                ON  resp.request_id = r.id
                AND resp.post_index  = 0;
        """)
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply additive migrations for existing databases."""
        assert self._conn
        # home_state column added after initial release
        try:
            self._conn.execute("ALTER TABLE user_prefs ADD COLUMN home_state TEXT")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # ------------------------------------------------------------------
    # requests
    # ------------------------------------------------------------------

    def save_request(
        self,
        source_channel: str,
        raw_content: str,
        requester_handle: Optional[str] = None,
        raw_location: Optional[str] = None,
        resolved_location: Optional[str] = None,
        resolved_lat: Optional[float] = None,
        resolved_lon: Optional[float] = None,
        source_created_at: Optional[str] = None,
        ingested_at: Optional[str] = None,
        status: str = "pending",
        source_uri: Optional[str] = None,
    ) -> Optional[int]:
        assert self._conn
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO requests (
                source_channel, requester_handle,
                raw_location, resolved_location, resolved_lat, resolved_lon,
                raw_content, status,
                source_created_at, ingested_at, source_uri
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_channel, requester_handle,
             raw_location, resolved_location, resolved_lat, resolved_lon,
             raw_content, status,
             source_created_at, ingested_at or _now(), source_uri),
        )
        self._conn.commit()
        if cur.lastrowid == 0 or cur.rowcount == 0:
            return None
        return cur.lastrowid

    def update_request_status(self, request_id: int, status: str,
                               error_message: Optional[str] = None) -> None:
        assert self._conn
        self._conn.execute(
            "UPDATE requests SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, request_id),
        )
        self._conn.commit()

    def update_request_processing_start(self, request_id: int) -> None:
        assert self._conn
        self._conn.execute(
            "UPDATE requests SET processing_started_at = ? WHERE id = ?", (_now(), request_id)
        )
        self._conn.commit()

    def update_request_formatting_start(self, request_id: int) -> None:
        assert self._conn
        self._conn.execute(
            "UPDATE requests SET formatting_started_at = ? WHERE id = ?", (_now(), request_id)
        )
        self._conn.commit()

    def update_request_processing_finish(self, request_id: int) -> None:
        assert self._conn
        self._conn.execute(
            "UPDATE requests SET processing_finished_at = ? WHERE id = ?", (_now(), request_id)
        )
        self._conn.commit()

    def update_request_mark_slow(self, request_id: int, delivery_finished_at: str,
                                  source_created_at: Optional[str]) -> bool:
        if source_created_at is None:
            return False
        try:
            elapsed = (
                datetime.fromisoformat(delivery_finished_at) -
                datetime.fromisoformat(source_created_at)
            ).total_seconds()
        except (ValueError, TypeError):
            return False
        if elapsed > SLOW_REQUEST_THRESHOLD_SEC:
            assert self._conn
            self._conn.execute(
                "UPDATE requests SET is_slow = 1 WHERE id = ?", (request_id,)
            )
            self._conn.commit()
            return True
        return False

    # ------------------------------------------------------------------
    # responses
    # ------------------------------------------------------------------

    def save_response(
        self,
        request_id: int,
        notify_channel: str,
        message_text: str,
        post_index: int = 0,
        post_uri: Optional[str] = None,
        success: bool = True,
        delivery_started_at: Optional[str] = None,
        delivery_finished_at: Optional[str] = None,
    ) -> int:
        assert self._conn
        cur = self._conn.execute(
            """
            INSERT INTO responses (
                request_id, notify_channel, post_index,
                message_text, post_uri, success,
                delivery_started_at, delivery_finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (request_id, notify_channel, post_index,
             message_text, post_uri, int(success),
             delivery_started_at, delivery_finished_at),
        )
        self._conn.commit()
        return cur.lastrowid

    # ------------------------------------------------------------------
    # snow_cache
    # ------------------------------------------------------------------

    def get_cached_snow(self, lat: float, lon: float) -> Optional[dict]:
        key = _cache_key(lat, lon)
        assert self._conn
        row = self._conn.execute(
            "SELECT report_json FROM snow_cache WHERE cache_key = ? AND expires_at > ?",
            (key, _now()),
        ).fetchone()
        return json.loads(row["report_json"]) if row else None

    def save_cached_snow(
        self, lat: float, lon: float, display_name: str,
        report_json: dict, ttl_minutes: int = 30,
    ) -> None:
        key    = _cache_key(lat, lon)
        now_dt = datetime.utcnow()
        expires = (now_dt + timedelta(minutes=ttl_minutes)).isoformat()
        assert self._conn
        self._conn.execute(
            """
            INSERT INTO snow_cache
                (cache_key, display_name, lat, lon, report_json, fetched_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                display_name = excluded.display_name,
                report_json  = excluded.report_json,
                fetched_at   = excluded.fetched_at,
                expires_at   = excluded.expires_at
            """,
            (key, display_name, round(lat, 3), round(lon, 3),
             json.dumps(report_json), now_dt.isoformat(), expires),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # seen_dm_ids
    # ------------------------------------------------------------------

    def is_dm_seen(self, message_id: str) -> bool:
        assert self._conn
        return self._conn.execute(
            "SELECT 1 FROM seen_dm_ids WHERE message_id = ?", (message_id,)
        ).fetchone() is not None

    def mark_dm_seen(self, message_id: str, convo_id: str) -> None:
        assert self._conn
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_dm_ids (message_id, convo_id, seen_at) VALUES (?,?,?)",
            (message_id, convo_id, _now()),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # user_prefs
    # ------------------------------------------------------------------

    def get_user_prefs(self, did: str) -> Optional[dict]:
        assert self._conn
        row = self._conn.execute(
            "SELECT * FROM user_prefs WHERE user_did = ?", (did,)
        ).fetchone()
        return dict(row) if row else None

    def set_user_prefs(
        self,
        did: str,
        handle: Optional[str] = None,
        home_raw: Optional[str] = None,
        home_display: Optional[str] = None,
        home_lat: Optional[float] = None,
        home_lon: Optional[float] = None,
        home_state: Optional[str] = None,
    ) -> None:
        assert self._conn
        existing = self.get_user_prefs(did)
        if existing is None:
            self._conn.execute(
                """INSERT INTO user_prefs
                   (user_did, handle, home_raw, home_display,
                    home_lat, home_lon, home_state, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (did, handle, home_raw, home_display, home_lat, home_lon, home_state, _now()),
            )
        else:
            updates: dict = {"updated_at": _now()}
            if handle       is not None: updates["handle"]       = handle
            if home_raw     is not None: updates["home_raw"]     = home_raw
            if home_display is not None: updates["home_display"] = home_display
            if home_lat     is not None: updates["home_lat"]     = home_lat
            if home_lon     is not None: updates["home_lon"]     = home_lon
            if home_state   is not None: updates["home_state"]   = home_state
            cols = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [did]
            self._conn.execute(
                f"UPDATE user_prefs SET {cols} WHERE user_did = ?", vals
            )
        self._conn.commit()

    def clear_home(self, did: str) -> None:
        assert self._conn
        self._conn.execute(
            """UPDATE user_prefs
               SET home_raw=NULL, home_display=NULL,
                   home_lat=NULL, home_lon=NULL, updated_at=?
               WHERE user_did = ?""",
            (_now(), did),
        )
        self._conn.commit()

    def set_home_state(self, did: str, state_code: str) -> None:
        assert self._conn
        existing = self.get_user_prefs(did)
        if existing is None:
            self._conn.execute(
                "INSERT INTO user_prefs (user_did, home_state, updated_at) VALUES (?, ?, ?)",
                (did, state_code.upper(), _now()),
            )
        else:
            self._conn.execute(
                "UPDATE user_prefs SET home_state = ?, updated_at = ? WHERE user_did = ?",
                (state_code.upper(), _now(), did),
            )
        self._conn.commit()

    def clear_home_state(self, did: str) -> None:
        assert self._conn
        self._conn.execute(
            "UPDATE user_prefs SET home_state = NULL, updated_at = ? WHERE user_did = ?",
            (_now(), did),
        )
        self._conn.commit()

    def reset_prefs(self, did: str) -> None:
        assert self._conn
        self._conn.execute("DELETE FROM user_prefs WHERE user_did = ?", (did,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # dm_sessions
    # ------------------------------------------------------------------

    def get_dm_session(self, did: str, ttl_minutes: int = 30) -> Optional[dict]:
        """Return active session for user, or None if absent/expired."""
        assert self._conn
        cutoff = (datetime.utcnow() - timedelta(minutes=ttl_minutes)).isoformat()
        row = self._conn.execute(
            "SELECT menu, context, updated_at FROM dm_sessions "
            "WHERE user_did = ? AND updated_at > ?",
            (did, cutoff),
        ).fetchone()
        if row is None:
            return None
        ctx = json.loads(row["context"]) if row["context"] else {}
        return {"menu": row["menu"], **ctx}

    def set_dm_session(self, did: str, menu: str, **context) -> None:
        assert self._conn
        ctx_json = json.dumps(context) if context else None
        self._conn.execute(
            """INSERT INTO dm_sessions (user_did, menu, context, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_did) DO UPDATE SET
                   menu = excluded.menu,
                   context = excluded.context,
                   updated_at = excluded.updated_at""",
            (did, menu, ctx_json, _now()),
        )
        self._conn.commit()

    def clear_dm_session(self, did: str) -> None:
        assert self._conn
        self._conn.execute("DELETE FROM dm_sessions WHERE user_did = ?", (did,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # file_inbox_log
    # ------------------------------------------------------------------

    def log_inbox_file(
        self,
        filename: str,
        outcome: str,
        request_id: Optional[int] = None,
        error_detail: Optional[str] = None,
    ) -> int:
        assert self._conn
        now = _now()
        cur = self._conn.execute(
            """INSERT INTO file_inbox_log
               (filename, received_at, processed_at, outcome, request_id, error_detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (filename, now, now, outcome, request_id, error_detail),
        )
        self._conn.commit()
        return cur.lastrowid

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune_old_records(self, retention_days: int = RETENTION_DAYS) -> dict[str, int]:
        assert self._conn
        cutoff  = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        results: dict[str, int] = {}

        cur = self._conn.execute(
            "DELETE FROM responses WHERE request_id IN "
            "(SELECT id FROM requests WHERE ingested_at < ?)", (cutoff,)
        )
        results["responses"] = cur.rowcount

        cur = self._conn.execute("DELETE FROM requests WHERE ingested_at < ?", (cutoff,))
        results["requests"] = cur.rowcount

        cur = self._conn.execute(
            "DELETE FROM file_inbox_log WHERE received_at < ?", (cutoff,)
        )
        results["file_inbox_log"] = cur.rowcount

        cur = self._conn.execute("DELETE FROM snow_cache WHERE expires_at <= ?", (_now(),))
        results["snow_cache"] = cur.rowcount

        self._conn.commit()
        return results

    def get_stats(self) -> dict:
        assert self._conn

        def count(table: str) -> int:
            return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        return {
            "requests_total":    count("requests"),
            "requests_pending":  self._conn.execute(
                "SELECT COUNT(*) FROM requests WHERE status = 'pending'"
            ).fetchone()[0],
            "requests_complete": self._conn.execute(
                "SELECT COUNT(*) FROM requests WHERE status = 'complete'"
            ).fetchone()[0],
            "responses_total":   count("responses"),
            "cache_entries":     count("snow_cache"),
            "seen_dm_ids":       count("seen_dm_ids"),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat()


def _cache_key(lat: float, lon: float) -> str:
    hour = datetime.utcnow().strftime("%Y%m%d%H")
    return f"{round(lat, 2):.2f}:{round(lon, 2):.2f}:{hour}"
