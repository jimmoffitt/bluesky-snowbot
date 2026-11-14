"""
Content loader — reads the curated CSV data files into memory at startup.

Data files (in snowbot/data/):
  links.csv      — snow science / research organizations
  playlists.csv  — curated Spotify playlists
  photos.csv     — photo filenames + captions
  trail_maps.csv — each resort's official trail-map page (or its website)

All loaders return plain lists of dicts so callers don't need to know
about CSV internals.
"""

from __future__ import annotations

import csv
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SnowLink:
    short_name: str
    full_name: str
    url: str
    description: str


@dataclass
class Playlist:
    name: str
    description: str
    url: str


@dataclass
class Photo:
    filename: str
    caption: str
    photos_dir: Path = _DATA_DIR / "photos"

    @property
    def path(self) -> Path:
        return self.photos_dir / self.filename


@dataclass
class TrailMap:
    display: str
    state: str
    url: str    # the resort's own trail-map page ("" if not found)
    site: str   # the resort's website (fallback)

    @property
    def link(self) -> str:
        return self.url or self.site


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_trail_maps(path: Path = _DATA_DIR / "trail_maps.csv") -> dict[tuple[str, str], TrailMap]:
    """Load trail_maps.csv (semicolon-delimited, '#' comments) keyed by (display, state)."""
    maps: dict[tuple[str, str], TrailMap] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = csv.reader((l for l in f if not l.startswith("#")), delimiter=";")
            for row in rows:
                if len(row) < 4 or not row[0].strip():
                    continue
                tm = TrailMap(*(c.strip() for c in row[:4]))
                if tm.link:
                    maps[(tm.display, tm.state)] = tm
    except FileNotFoundError:
        logger.warning("[content] trail_maps.csv not found at %s", path)
    return maps


def load_links(path: Path = _DATA_DIR / "links.csv") -> list[SnowLink]:
    """Load snow science / research links from links.csv (semicolon-delimited)."""
    links = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f, delimiter=";"):
                if len(row) < 3:
                    continue
                short, full, url = row[0].strip(), row[1].strip(), row[2].strip()
                desc = row[3].strip() if len(row) > 3 else ""
                if short and url:
                    links.append(SnowLink(short_name=short, full_name=full, url=url, description=desc))
    except FileNotFoundError:
        logger.warning("[content] links.csv not found at %s", path)
    return links


def load_playlists(path: Path = _DATA_DIR / "playlists.csv") -> list[Playlist]:
    """Load Spotify playlists from playlists.csv (comma-delimited)."""
    playlists = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 3:
                    continue
                name, desc, url = row[0].strip(), row[1].strip(), row[2].strip()
                if name and url:
                    playlists.append(Playlist(name=name, description=desc, url=url))
    except FileNotFoundError:
        logger.warning("[content] playlists.csv not found at %s", path)
    return playlists


def load_photos(csv_path: Path = _DATA_DIR / "photos.csv",
                photos_dir: Path = _DATA_DIR / "photos") -> list[Photo]:
    """Load photo list from photos.csv (semicolon-delimited: filename; caption)."""
    photos = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f, delimiter=";"):
                if len(row) < 2:
                    continue
                filename, caption = row[0].strip(), row[1].strip()
                if filename:
                    p = Photo(filename=filename, caption=caption, photos_dir=photos_dir)
                    if p.path.exists():
                        photos.append(p)
                    else:
                        logger.debug("[content] Photo file missing: %s", p.path)
    except FileNotFoundError:
        logger.warning("[content] photos.csv not found at %s", csv_path)
    return photos


# ---------------------------------------------------------------------------
# ContentLibrary — single object shared across the bot
# ---------------------------------------------------------------------------

class ContentLibrary:
    """
    Loaded once at startup; holds all curated content in memory.

    Usage:
        lib = ContentLibrary.load()
        lib.random_photo()          → Photo
        lib.all_playlists()         → list[Playlist]
        lib.all_links()             → list[SnowLink]
    """

    def __init__(self, links: list[SnowLink], playlists: list[Playlist], photos: list[Photo],
                 trail_maps: Optional[dict[tuple[str, str], TrailMap]] = None) -> None:
        self._links      = links
        self._playlists  = playlists
        self._photos     = photos
        self._trail_maps = trail_maps or {}

    @classmethod
    def load(cls, photos_dir: Optional[Path] = None) -> "ContentLibrary":
        links     = load_links()
        playlists = load_playlists()
        photos    = load_photos(photos_dir=photos_dir or _DATA_DIR / "photos")
        trail_maps = load_trail_maps()
        logger.info("[content] Loaded %d links, %d playlists, %d photos, %d trail maps",
                    len(links), len(playlists), len(photos), len(trail_maps))
        return cls(links=links, playlists=playlists, photos=photos, trail_maps=trail_maps)

    def trail_map(self, display: str, state: str) -> Optional[TrailMap]:
        return self._trail_maps.get((display, state))

    def all_links(self) -> list[SnowLink]:
        return list(self._links)

    def all_playlists(self) -> list[Playlist]:
        return list(self._playlists)

    def all_photos(self) -> list[Photo]:
        return list(self._photos)

    def random_photo(self) -> Optional[Photo]:
        return random.choice(self._photos) if self._photos else None

    def pick_photo(self, last_posted: dict[str, str]) -> Optional[Photo]:
        """
        Pick a photo for one user, avoiding repeats for that user.

        last_posted maps filename → when this user was last sent it. Photos the
        user hasn't seen are chosen first (in random order), so nothing repeats
        for them until they've seen every photo. After that, the pick is random
        among the half they saw longest ago. Other users' history doesn't matter.
        """
        if not self._photos:
            return None
        unseen = [p for p in self._photos if p.filename not in last_posted]
        if unseen:
            return random.choice(unseen)
        by_age = sorted(self._photos, key=lambda p: last_posted[p.filename])
        return random.choice(by_age[:max(1, len(by_age) // 2)])
