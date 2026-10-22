"""
Post a random snow photo to the @snowbot.bsky.social Bluesky account.

Usage:
    python scripts/post_random_photo.py
    python scripts/post_random_photo.py --dry-run      # preview without posting
    python scripts/post_random_photo.py --photo corduroy.jpg  # specific photo

Photos are loaded from PHOTOS_PATH (or snowbot/data/photos by default).
Credentials are read from .local.env.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from snowbot.config.settings import Settings
from snowbot.content import ContentLibrary


def post_photo(photo, dry_run: bool = False) -> None:
    settings = Settings.load()

    print(f"Photo : {photo.filename}")
    print(f"Caption: {photo.caption}")
    print(f"Path  : {photo.path}")

    if dry_run:
        print("\n[dry-run] Would post to @snowbot.bsky.social — skipping.")
        return

    from atproto import Client

    client = Client()
    client.login(settings.bluesky_handle, settings.bluesky_app_password)

    with open(photo.path, "rb") as f:
        img_data = f.read()

    # Detect MIME type from extension
    ext = photo.path.suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

    client.send_image(
        text=photo.caption,
        image=img_data,
        image_alt=photo.caption,
    )
    print("\nPosted successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Post a random snow photo to Bluesky")
    parser.add_argument("--dry-run", action="store_true", help="Preview without posting")
    parser.add_argument("--photo", metavar="FILENAME", help="Post a specific photo by filename")
    args = parser.parse_args()

    settings = Settings.load()
    lib = ContentLibrary.load(photos_dir=settings.photos_path)
    photos = lib.all_photos()

    if not photos:
        print(f"No photos found in {settings.photos_path}")
        sys.exit(1)

    if args.photo:
        matches = [p for p in photos if p.filename == args.photo]
        if not matches:
            print(f"Photo {args.photo!r} not found in photos.csv or file missing.")
            print(f"Available: {[p.filename for p in photos]}")
            sys.exit(1)
        photo = matches[0]
    else:
        photo = random.choice(photos)

    post_photo(photo, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
