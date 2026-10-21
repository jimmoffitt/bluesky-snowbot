"""
Menu system — formats the main menu and sub-menu responses for DM interactions.

Main menu options:
  1. Snow reports
  2. Learn about snow
  3. See snow photos
  4. Music playlists
  5. Help / About

Each option maps to a keyword the user can send (number or word).
Responses are kept to ≤1000 chars (Bluesky DM limit), split into
multiple messages when needed.
"""

from __future__ import annotations

import re

from bluesky_snowbot.channels.notify.bluesky_dm import DM_MAX_CHARS
from bluesky_snowbot.content import ContentLibrary
from bluesky_snowbot.snow.resolver import snotel_icon

# ---------------------------------------------------------------------------
# Trigger keyword sets (checked case-insensitively against stripped DM text)
# ---------------------------------------------------------------------------

MENU_TRIGGERS = {"menu", "main menu", "main", "top", "home", "start", "start over", "startover", "options"}

SNOW_REPORT_TRIGGERS  = {"1", "snow reports", "snow report", "report", "reports", "snow"}
LEARN_TRIGGERS        = {"2", "learn", "learn about snow", "science", "links", "resources"}
# Photos are posted publicly (not via DM embed — Bluesky DMs don't support image embeds).
PHOTOS_TRIGGERS       = {"3", "photos", "photo", "picture", "pictures", "pic", "pics"}
PLAYLIST_TRIGGERS     = {"4", "music", "playlist", "playlists", "songs", "spotify"}
HELP_TRIGGERS         = {"5", "help", "about", "?"}
CHANGE_STATE_TRIGGERS = {"change state", "states", "regions", "change region", "all states"}
BACK_TRIGGERS         = {"back", "go back", "...", "up", "return"}

# Distinctive words that select a branch even inside a longer message
# ("show me a photo", "learn more"). Checked only when the message doesn't
# name a known resort, so "snow at vail" still gets the Vail report.
KEYWORD_BRANCHES: list[tuple[set[str], str]] = [
    ({"photo", "photos", "pic", "pics", "picture", "pictures"}, "photos"),
    ({"learn", "science"},                                       "learn"),
    ({"music", "playlist", "playlists", "songs", "spotify"},    "playlist"),
    ({"help", "commands"},                                       "help"),
    ({"menu", "options"},                                        "menu"),
]

# Conversational replies that should never be treated as a location
SMALL_TALK = {
    "hi", "hello", "hey", "yo", "thanks", "thank you", "thx", "ty", "ok", "okay",
    "k", "cool", "nice", "great", "awesome", "yes", "yep", "no", "nope", "lol",
}

# ---------------------------------------------------------------------------
# State / region display names
# ---------------------------------------------------------------------------

STATE_NAMES: dict[str, str] = {
    # United States
    "AK": "Alaska",
    "AZ": "Arizona",
    "CA": "California",
    "CO": "Colorado",
    "ID": "Idaho",
    "ME": "Maine",
    "MN": "Minnesota",
    "MT": "Montana",
    "NH": "New Hampshire",
    "NM": "New Mexico",
    "NV": "Nevada",
    "OR": "Oregon",
    "UT": "Utah",
    "VT": "Vermont",
    "WA": "Washington",
    "WY": "Wyoming",
    # Canada
    "AB": "Alberta",
    "BC": "British Columbia",
    "QC": "Québec",
    # Europe
    "AT": "Austria",
    "CH": "Switzerland",
    "FR": "France",
    "IT": "Italy",
    "AD": "Andorra",
    # Japan
    "HK": "Hokkaido",
    "NG": "Nagano",
    "NI": "Niigata",
    # New Zealand
    "NZ": "New Zealand",
}


def is_menu_trigger(text: str) -> bool:
    return text.strip().lower() in MENU_TRIGGERS


def is_small_talk(text: str) -> bool:
    return text.strip().lower().rstrip("!.? ") in SMALL_TALK


def format_small_talk_reply() -> str:
    return "❄️ Send a resort name for snow conditions (e.g. Vail), or 'menu' for options."


def classify_menu_input(text: str) -> str | None:
    """
    Return a menu branch name for the given DM text, or None if not a menu command.
    Branch names: "menu", "snow_report", "learn", "photos", "playlist",
                  "help", "change_state"
    """
    t = text.strip().lower()
    branch = _classify_exact(t)
    if branch is None:
        # Retry without punctuation: "Photo!" → "photo"
        stripped = _strip_punctuation(t)
        if stripped and stripped != t:
            branch = _classify_exact(stripped)
    return branch


def find_menu_keyword(text: str) -> str | None:
    """Return the branch for a distinctive keyword anywhere in the text, or None."""
    words = set(_strip_punctuation(text.lower()).split())
    for keywords, branch in KEYWORD_BRANCHES:
        if words & keywords:
            return branch
    return None


def _strip_punctuation(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", text).split())


def _classify_exact(t: str) -> str | None:
    if t in MENU_TRIGGERS:         return "menu"
    if t in BACK_TRIGGERS:         return "back"
    if t in SNOW_REPORT_TRIGGERS:  return "snow_report"
    if t in LEARN_TRIGGERS:        return "learn"
    if t in PHOTOS_TRIGGERS:       return "photos"
    if t in PLAYLIST_TRIGGERS:     return "playlist"
    if t in HELP_TRIGGERS:         return "help"
    if t in CHANGE_STATE_TRIGGERS: return "change_state"
    return None


def format_regions_menu(resorts_by_region: dict) -> list[str]:
    """Returns one or more messages listing all regions, numbered for selection."""
    lines = []
    for i, (region, resorts) in enumerate(resorts_by_region.items(), 1):
        count = len(resorts)
        lines.append(f"{i}. {region} ({count} resort{'s' if count != 1 else ''})")
    return _pack_lines("🎿 Select a region:\n\n", lines, footer="\nReply with a number — or 'menu' for main menu.")


def format_resorts_menu(region: str, resorts: list[dict]) -> list[str]:
    """Messages listing resorts in a region, numbered for selection.
    Each resort line includes a SNOTEL quality icon: ❄ good  ≈ marginal  ○ none
    """
    header = f"🎿 {region} resorts:\n\n"
    lines = []
    for i, resort in enumerate(resorts, 1):
        icon = snotel_icon(resort.get("snotel_dist_mi"), resort.get("snotel"))
        lines.append(f"{i}. {resort['display']} {icon}")
    footer = "\nReply with a number — or 'back' for regions.\n❄ on-mtn SNOTEL  ≈ nearby  ○ model only"
    return _pack_lines(header, lines, footer=footer)


def format_main_menu() -> str:
    return (
        "Hi! I'm Snowbot ❄️ What would you like?\n\n"
        "1. Snow reports\n"
        "2. Learn about snow\n"
        "3. See snow photos\n"  # posts publicly with @mention
        "4. Weather & mountain playlists\n"
        "5. Help / About\n\n"
        "Reply with a number or keyword — or just type a resort name for instant conditions."
    )


def format_learn_menu(lib: ContentLibrary) -> list[str]:
    """Returns a list of DM messages listing all snow science links with URLs."""
    links = lib.all_links()
    if not links:
        return ["No learn resources available right now."]

    lines = []
    for i, link in enumerate(links, 1):
        lines.append(f"{i}. {link.short_name} — {link.full_name}\n   {link.url}")
    return _pack_lines("❄️ Learn about snow:\n\n", lines, footer="\nReply with a number for more details.")


def format_link_detail(lib: ContentLibrary, index: int) -> str:
    """Return name + URL + description for the nth link (1-based)."""
    links = lib.all_links()
    if not 1 <= index <= len(links):
        return f"Please reply with a number between 1 and {len(links)}."
    link = links[index - 1]
    return f"{link.full_name}\n{link.url}\n\n{link.description}"


def format_playlist_menu(lib: ContentLibrary) -> list[str]:
    """Returns DM messages listing all playlists with Spotify links."""
    playlists = lib.all_playlists()
    if not playlists:
        return ["No playlists available right now."]
    lines = []
    for i, pl in enumerate(playlists, 1):
        lines.append(f"{i}. {pl.name} — {pl.description}\n   {pl.url}")
    return _pack_lines("🎵 Music playlists:\n\n", lines, footer="\nReply with a number for details.")


def format_playlist_detail(lib: ContentLibrary, index: int) -> str:
    """Return name + URL for the nth playlist (1-based)."""
    playlists = lib.all_playlists()
    if not 1 <= index <= len(playlists):
        return f"Please reply with a number between 1 and {len(playlists)}."
    pl = playlists[index - 1]
    return f"🎵 {pl.name}\n{pl.description}\n\n{pl.url}"


def format_photo_caption(photo) -> str:
    return f"📸 {photo.caption}" if photo else "No photos available right now."


def format_help() -> str:
    return (
        "Snowbot ❄️\n\n"
        "Send a resort name for snow conditions:\n"
        "  Vail  •  Park City  •  Mammoth\n\n"
        "Other commands:\n"
        "  menu — show main menu\n"
        "  learn — snow science links\n"
        "  photos — post a snow photo for you\n"
        "  music — weather & mountain playlists\n"
        "  set home Vail — save home resort\n"
        "  settings — view preferences\n"
        "  reset — clear preferences"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pack_lines(header: str, lines: list[str], footer: str = "") -> list[str]:
    """Pack lines into DMs of ≤DM_MAX_CHARS, with header on first message."""
    messages = []
    current = header
    for line in lines:
        candidate = current + line + "\n"
        if len(candidate) + len(footer) > DM_MAX_CHARS and current != header:
            messages.append(current.rstrip())
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        current = current.rstrip()
        if footer and len(current) + len(footer) <= DM_MAX_CHARS:
            current += footer
        messages.append(current)
    return messages
