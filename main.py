"""Entry point for the Snowbot."""

import logging
import sys

from bluesky_snowbot.config.settings import Settings, ConfigError
from bot import build_bot


def _configure_logging(settings: Settings) -> None:
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(settings.log_path))
    except OSError as exc:
        print(f"Warning: could not open log file {settings.log_path}: {exc}", file=sys.stderr)

    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO),
                        format=fmt, handlers=handlers)


def main() -> None:
    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print("Copy .local.env.example to .local.env and fill in your credentials.",
              file=sys.stderr)
        sys.exit(1)

    _configure_logging(settings)
    logger = logging.getLogger(__name__)
    logger.info("Starting Snowbot as %s on %s", settings.bluesky_handle, settings.server_type)

    bot = build_bot(settings)
    bot.start()   # blocks until SIGINT / SIGTERM


if __name__ == "__main__":
    main()
