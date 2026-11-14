"""
Bluesky rich-text helpers shared by the DM and post channels.

Bluesky doesn't auto-link URLs: a link is only clickable if the record carries
a facet giving its byte range. build_link_facets() makes one per URL.
"""

from __future__ import annotations

import re

URL_RE = re.compile(r'https?://\S+')


def build_link_facets(text: str) -> list:
    """Return a list of AppBskyRichtextFacet.Main for every URL found in text."""
    try:
        from atproto import models as atproto_models
    except ImportError:
        return []
    facets = []
    text_bytes = text.encode("utf-8")
    for match in URL_RE.finditer(text):
        byte_start = len(text[:match.start()].encode("utf-8"))
        byte_end   = len(text[:match.end()].encode("utf-8"))
        facets.append(
            atproto_models.AppBskyRichtextFacet.Main(
                index=atproto_models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=byte_start,
                    byte_end=byte_end,
                ),
                features=[atproto_models.AppBskyRichtextFacet.Link(uri=match.group())],
            )
        )
    return facets
