# bluesky_snowbot

A Bluesky bot that responds to ski resort snow condition requests from public
posts, direct messages, and file-based alerts.

Bluesky account: @snowbot.bsky.social

## What this project does
Users mention `@snowbot.bsky.social Vail` on Bluesky (or send a DM, or drop
a YAML file in the inbox) and receive a 2-post snow report thread:
snow depth + recent snowfall, plus current temperature, wind, and sky conditions
— all in dual units (°F/°C, mph/km/h, in/cm).

## Architecture in one diagram
```
Alert channels (inputs)               Notify channels (outputs)
───────────────────────               ─────────────────────────
FileWatcherAlertChannel   ──┐
FirehoseAlertChannel      ──┼──▶  SnowBot (bot.py)  ──▶  BlueskyPostNotifyChannel
DMAlertChannel            ──┘           │            ──▶  BlueskyDMNotifyChannel
                                        ▼
                                   SnowService
                                (Open-Meteo API, free)
                                        ▼
                                     Database
                                    (SQLite WAL)
```

## Package structure
```
bluesky_snowbot/        ← importable package
  channels/
    alert/
      base.py               ← AlertChannel ABC + AlertRequest dataclass
      file_watcher.py       ← polls inbox/ for YAML files every 5s
      firehose.py           ← Bluesky public post firehose listener
      dm_poller.py          ← Bluesky DM poller (15s interval)
    notify/
      base.py               ← NotificationChannel ABC + payload/result
      bluesky_post.py       ← posts 2-post public reply thread
      bluesky_dm.py         ← sends DM reply
  snow/
    models.py               ← dataclasses: SnowReport, ResortLocation, etc.
    resolver.py             ← resort name → lat/lon (30 hardcoded + Nominatim fallback)
    client.py               ← Open-Meteo API client (no key required)
    formatter.py            ← SnowReport → list of Bluesky post strings (≤300 chars each)
    service.py              ← SnowService facade: raw location → SnowReport
  storage/
    db.py                   ← SQLite: requests, responses, snow_cache, seen_dm_ids,
                              user_prefs, file_inbox_log
  config/
    settings.py             ← loads .local.env → Settings dataclass
bot.py                      ← SnowBot orchestrator + build_bot() factory
main.py                     ← entry point
```

## Shared patterns with bluesky-weather-bot
These are intentionally identical between both bots (copy/update imports only):
- Channel architecture: alert/notify split, AlertRequest, NotificationPayload
- Settings: .local.env → Settings dataclass
- DB schema: requests, responses, seen_dm_ids, user_prefs, file_inbox_log tables
- Channel implementations: file_watcher, firehose, dm_poller, bluesky_post, bluesky_dm
- Latency instrumentation (6 timestamps per request)
- systemd service file pattern for Pi deployment

## Key design decisions
- **Alert channels** are inputs; **notify channels** are outputs. Never talk
  to each other — everything routes through SnowBot in bot.py.
- **Resort resolution**: 30 hardcoded US/Canada resorts + Nominatim fallback.
  Add new resorts to `_RESORTS` in `snow/resolver.py`.
- **Snow data**: Open-Meteo (free, no API key). Variables: snow_depth,
  snowfall, temperature_2m, wind_speed_10m, wind_gusts_10m, weather_code.
- **Post format**: 2-post thread. Post 1 = snow depth + recent totals.
  Post 2 = temperature + wind + conditions.
- **User preferences**: home resort stored in DB user_prefs table via DM.
- Bluesky post limit is 300 chars. Formatter always truncates to fit.

## DM commands
```
Vail                    — get snow conditions for Vail
set home Breckenridge   — save home resort
settings                — view current preferences
reset                   — clear all preferences
clear home              — remove home resort
help                    — list commands
```

## Database schema (SQLite)
Six tables: requests, responses, snow_cache, seen_dm_ids, user_prefs, file_inbox_log.
One view: latency_summary.
Cache TTL: configurable (default 30 min). Retention: 90 days for requests/responses.

## Configuration
All settings loaded from .local.env (gitignored). Copy .local.env.example to get started.
Required: BSKY_HANDLE, BSKY_APP_PASSWORD.
Key optional: INBOX_PATH, DB_PATH, LOG_PATH, SNOW_CACHE_TTL_MINUTES, SERVER_TYPE.

## Snow data (Open-Meteo)
Provider: Open-Meteo (free, no API key, global coverage)
- Current: snow_depth (m → cm), temperature, wind speed/gusts, weather code
- Historical 72h: snowfall accumulation summed from hourly data
- Resort resolution: 30 hardcoded resorts + Nominatim geocoding fallback

## Inbox YAML format (file alert channel)
```yaml
full_message: "Fresh 12\" at Vail overnight ❄️ #SnowBot"
message: "Fresh 12\" at Vail overnight"
created_at: "2025-12-15T08:00:00-07:00"
host: Test
tags: [SnowBot, PowderAlert]
mentions:
```

## Reference project
Sister project: `../bluesky-weather-bot/` — shares channel architecture.
When implementing or debugging channel code, compare with the weather bot's
equivalent implementations (structurally identical, different imports).

## Running / testing
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Validate snow core (no Bluesky credentials needed):
python -c "
from bluesky_snowbot.storage.db import Database
from bluesky_snowbot.snow.service import SnowService
from bluesky_snowbot.snow.formatter import SnowFormatter
with Database(':memory:') as db:
    svc = SnowService(db)
    report = svc.lookup('Vail')
    for post in SnowFormatter().format_thread(report):
        print(post); print('---')
"

# Run tests:
pytest

# Run the bot:
python main.py
```

## What's complete vs. needs work
- `snow/models.py`             ✅ complete
- `snow/resolver.py`           ✅ complete (30 resorts + Nominatim)
- `snow/client.py`             ✅ complete
- `snow/formatter.py`          ✅ complete
- `snow/service.py`            ✅ complete
- `storage/db.py`              ✅ complete
- `config/settings.py`         ✅ complete
- `bot.py`                     ✅ complete (needs integration testing)
- `main.py`                    ✅ complete
- `channels/alert/base.py`     ✅ complete
- `channels/notify/base.py`    ✅ complete
- `channels/alert/file_watcher.py`  ✅ complete
- `channels/alert/firehose.py`      ✅ complete
- `channels/alert/dm_poller.py`     ✅ complete
- `channels/notify/bluesky_post.py` ✅ complete
- `channels/notify/bluesky_dm.py`   ✅ complete

## Next steps
1. Set up .venv: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Copy `.local.env.example` → `.local.env`, fill in @snowbot.bsky.social credentials
3. Run `pytest` to validate snow core
4. Run the bot and test with a DM: "Vail"
5. Consider adding photo capabilities (original SnowBotDev fetched ski photos)
6. Consider music playlist feature from original bot
