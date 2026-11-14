"""
Set up the public face of the Snowbot account:

  1. Open DMs to everyone (chat.bsky.actor.declaration allowIncoming="all").
     With no declaration record, Bluesky only lets accounts the bot follows DM it.
  2. Update the display name, bio, and website on the profile. Avatar, banner,
     and any other profile fields are kept as they are.
  3. Post a two-post "getting started" thread and pin the first post.

Usage:
    python scripts/setup_profile.py              # dry run: show current state + planned text
    python scripts/setup_profile.py --apply      # make the changes
    python scripts/setup_profile.py --apply --skip-pin       # DMs + profile only
    python scripts/setup_profile.py --apply --replace-pin    # post a new thread even if a pin exists

Credentials are read from .local.env (BLUESKY_HANDLE / BLUESKY_APP_PASSWORD).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from snowbot.config.settings import Settings

DISPLAY_NAME = "Snowbot ❄️"
WEBSITE = "https://github.com/jimmoffitt/bluesky-snowbot"

DESCRIPTION = """❄️ Snow reports for 110 ski resorts: US (incl. Midwest), Canada, Europe, Japan, NZ. An experimental bot on a Raspberry Pi, so be patient!

DM a resort (Vail, Lutsen, Niseko) or "menu".

Data: Open-Meteo + USDA SNOTEL.

github.com/jimmoffitt/bluesky-snowbot"""

# Post 1 is split around the bot's own @mention so it gets a clickable mention facet.
PIN_1_BEFORE = """👋 New here? Here's how to get snow conditions:

💬 DM me a resort name: Vail, Park City, Whistler…
📋 DM "menu" to browse by region, learn snow science, or find music
📣 Or post \""""
PIN_1_AFTER = """ Mammoth" and I'll reply publicly

Save a favorite with "set home Breck". "help" lists everything."""

PIN_2 = """Good to know:
• DMs stay private. Public mentions get public replies.
• Bluesky DMs can't carry images, so "photos" posts a picture on my feed and tags you.
• Data: Open-Meteo weather and nearby USDA SNOTEL snow stations.
• I run on a little Raspberry Pi, so replies can be slow. Hang tight!"""

DESCRIPTION_LIMIT = 256
POST_LIMIT = 300


def pin_1_text(handle: str) -> str:
    return f"{PIN_1_BEFORE}@{handle}{PIN_1_AFTER}"


def check_lengths(handle: str) -> None:
    # Python len() counts code points, which is >= Bluesky's grapheme count,
    # so passing here means passing on Bluesky.
    checks = [
        ("description", DESCRIPTION, DESCRIPTION_LIMIT),
        ("pinned post 1", pin_1_text(handle), POST_LIMIT),
        ("pinned post 2", PIN_2, POST_LIMIT),
    ]
    for name, text, limit in checks:
        n = len(text)
        print(f"  {name}: {n}/{limit}")
        if n > limit:
            sys.exit(f"ERROR: {name} is too long ({n} > {limit})")


def get_record(client, did: str, collection: str):
    """Return (value_dict, cid) for <collection>/self, or (None, None) if missing."""
    from atproto import models
    from atproto_client.models.utils import get_model_as_dict

    try:
        resp = client.com.atproto.repo.get_record(
            models.ComAtprotoRepoGetRecord.Params(repo=did, collection=collection, rkey="self")
        )
    except Exception as exc:
        if "RecordNotFound" in str(exc) or "Could not locate record" in str(exc):
            return None, None
        raise
    return get_model_as_dict(resp.value), resp.cid


def put_record(client, did: str, collection: str, record: dict, swap_cid=None) -> None:
    from atproto import models

    client.com.atproto.repo.put_record(
        models.ComAtprotoRepoPutRecord.Data(
            repo=did, collection=collection, rkey="self",
            record=record, swap_record=swap_cid,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up the Snowbot profile and pinned post")
    parser.add_argument("--apply", action="store_true", help="Make the changes (default is a dry run)")
    parser.add_argument("--skip-pin", action="store_true", help="Don't post or pin the getting-started thread")
    parser.add_argument("--replace-pin", action="store_true",
                        help="Post and pin a new thread even if the profile already has a pinned post")
    args = parser.parse_args()

    settings = Settings.load()
    handle = settings.bluesky_handle.lstrip("@")

    print("Lengths:")
    check_lengths(handle)

    from atproto import Client, client_utils, models

    client = Client()
    client.login(handle, settings.bluesky_app_password)
    did = client.me.did
    print(f"\nLogged in as {handle} ({did})")

    chat, _ = get_record(client, did, "chat.bsky.actor.declaration")
    profile, profile_cid = get_record(client, did, "app.bsky.actor.profile")
    profile = profile or {"$type": "app.bsky.actor.profile"}

    print("\nCurrent:")
    print(f"  DMs allowed from: {(chat or {}).get('allowIncoming', 'following (default, no record)')}")
    print(f"  displayName: {profile.get('displayName')!r}")
    print(f"  description: {profile.get('description')!r}")
    print(f"  website:     {profile.get('website')!r}")
    print(f"  pinnedPost:  {(profile.get('pinnedPost') or {}).get('uri')}")

    has_pin = bool(profile.get("pinnedPost"))
    will_pin = not args.skip_pin and (args.replace_pin or not has_pin)

    print("\nPlanned:")
    print("  DMs allowed from: all")
    print(f"  displayName: {DISPLAY_NAME!r}")
    print(f"  website:     {WEBSITE!r}")
    print(f"  description:\n{_indent(DESCRIPTION)}")
    if will_pin:
        print(f"  pinned post 1:\n{_indent(pin_1_text(handle))}")
        print(f"  pinned post 2 (reply):\n{_indent(PIN_2)}")
    elif has_pin and not args.skip_pin:
        print("  pinned thread: skipped, profile already has a pinned post (use --replace-pin)")
    else:
        print("  pinned thread: skipped (--skip-pin)")

    if not args.apply:
        print("\n[dry run] Nothing changed. Re-run with --apply.")
        return

    # 1. Open DMs to everyone
    chat_record = dict(chat or {})
    chat_record["$type"] = "chat.bsky.actor.declaration"
    chat_record["allowIncoming"] = "all"
    put_record(client, did, "chat.bsky.actor.declaration", chat_record)
    print("\n✓ DMs open to everyone")

    # 2. Post the thread first, so the pin goes into the same profile write
    if will_pin:
        text_1 = (client_utils.TextBuilder()
                  .text(PIN_1_BEFORE)
                  .mention(f"@{handle}", did)
                  .text(PIN_1_AFTER))
        root = client.send_post(text_1, langs=["en"])
        root_ref = models.create_strong_ref(root)
        print(f"✓ Posted pinned post 1: {root.uri}")

        reply = client.send_post(
            PIN_2,
            reply_to=models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=root_ref),
            langs=["en"],
        )
        print(f"✓ Posted pinned post 2: {reply.uri}")
        profile["pinnedPost"] = {"uri": root.uri, "cid": root.cid}

    # 3. Profile: change only our fields; avatar/banner/etc. are carried over
    profile["$type"] = "app.bsky.actor.profile"
    profile["displayName"] = DISPLAY_NAME
    profile["description"] = DESCRIPTION
    profile["website"] = WEBSITE
    put_record(client, did, "app.bsky.actor.profile", profile, swap_cid=profile_cid)
    print("✓ Profile updated" + (" and thread pinned" if will_pin else ""))

    if will_pin:
        rkey = root.uri.rsplit("/", 1)[-1]
        print(f"\nPinned post: https://bsky.app/profile/{handle}/post/{rkey}")
    print(f"Profile:     https://bsky.app/profile/{handle}")


def _indent(text: str) -> str:
    return "\n".join("      " + line for line in text.splitlines())


if __name__ == "__main__":
    main()
