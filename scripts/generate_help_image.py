"""
Generate a polished help card image for Snowbot.

Output: docs/snowbot_help.png  (1200 × 675 — standard 16:9 social card)

Usage:
    python scripts/generate_help_image.py
    python scripts/generate_help_image.py --output path/to/image.png
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

W, H   = 1200, 675
MARGIN = 72

# ---------------------------------------------------------------------------
# Colour palette — deep-night ski-resort vibes
# ---------------------------------------------------------------------------

BG_TOP   = ( 8,  18,  52)   # near-black navy
BG_BOT   = (16,  56, 120)   # deep alpine blue
STAR     = (210, 235, 255)   # tiny star/snow dots
ACCENT   = ( 96, 200, 255)   # ice-blue headings
WHITE    = (255, 255, 255)
DIM      = (170, 205, 235)   # secondary text
RULE     = ( 50,  95, 160)   # divider lines
SNOW     = (230, 245, 255)   # snowflake colour
MTN_FAR  = ( 14,  38,  88, 200)   # distant peaks (RGBA)
MTN_NEAR = ( 10,  26,  62, 230)   # foreground ridge


# ---------------------------------------------------------------------------
# Font loader
# ---------------------------------------------------------------------------

def _ttf(name: str, size: int) -> ImageFont.FreeTypeFont:
    for root in ("/System/Library/Fonts", "/Library/Fonts"):
        p = f"{root}/{name}"
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def load_fonts() -> dict:
    return {
        "hero":    _ttf("AvenirNext-Bold.ttf",   72),
        "h1":      _ttf("HelveticaNeue.ttc",      72),
        "h2":      _ttf("HelveticaNeue.ttc",      34),
        "section": _ttf("HelveticaNeue.ttc",      31),
        "body":    _ttf("HelveticaNeue.ttc",      29),
        "dim":     _ttf("HelveticaNeue.ttc",      26),
        "tag":     _ttf("HelveticaNeue.ttc",      23),
        "emoji":   _ttf("Apple Color Emoji.ttc",  64),
    }


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def gradient(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def starfield(img: Image.Image) -> None:
    """Tiny stars / snow-dust in the upper half."""
    draw = ImageDraw.Draw(img)
    rng  = random.Random(7)
    for _ in range(200):
        x = rng.randint(0, W)
        y = rng.randint(0, H // 2)
        s = rng.choice([1, 1, 1, 2])
        a = rng.randint(40, 130)
        c = (*STAR, a)
        draw.ellipse([x - s, y - s, x + s, y + s], fill=c)


def snowflake_cross(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int,
                    color=(220, 240, 255, 80)) -> None:
    """Draw a simple 6-arm snowflake cross."""
    arms = [(0, -1), (0, 1), (-1, 0), (1, 0),
            (-0.707, -0.707), (0.707, 0.707),
            (0.707, -0.707), (-0.707, 0.707)]
    w = max(1, size // 8)
    for dx, dy in arms:
        draw.line(
            [(cx, cy), (cx + int(dx * size), cy + int(dy * size))],
            fill=color, width=w,
        )


def scatter_snowflakes(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    rng  = random.Random(13)
    for _ in range(20):
        x = rng.randint(20, W - 20)
        # Keep snowflakes in top strip only, away from text area
        y = rng.randint(20, 170)
        sz = rng.choice([6, 8, 10, 12, 14])
        a  = rng.randint(25, 65)
        snowflake_cross(draw, x, y, sz, (220, 240, 255, a))


def mountains(img: Image.Image) -> None:
    """Two mountain ridge layers at the bottom."""
    for pts, rgba in [
        # Far ridge — lighter, higher peaks
        ([(0, H),
          (100, 510), (230, 460), (370, 390), (480, 440),
          (600, 360), (710, 420), (840, 350), (970, 400),
          (1080, 360), (1200, 410), (1200, H)], MTN_FAR),
        # Near ridge — darker, lower
        ([(0, H),
          (60,  570), (180, 530), (310, 570), (440, 490),
          (580, 550), (700, 480), (820, 540), (960, 470),
          (1100, 530), (1200, 490), (1200, H)], MTN_NEAR),
    ]:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon(pts, fill=rgba)
        img.alpha_composite(layer)


def tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def cx(draw, y, text, font, color=WHITE, img=None):
    x = (W - tw(draw, text, font)) // 2
    draw.text((x, y), text, font=font, fill=color)


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(output: Path) -> None:
    img  = Image.new("RGBA", (W, H))
    gradient(img)
    starfield(img)
    scatter_snowflakes(img)
    mountains(img)

    draw  = ImageDraw.Draw(img)
    fonts = load_fonts()

    # ── Hero title ──────────────────────────────────────────────────────────
    # Draw "Snowbot" in white, then emoji separately
    title_text = "Snowbot"
    hero_font  = fonts["h1"]
    emoji_font = fonts["emoji"]

    title_w  = tw(draw, title_text, hero_font)
    emoji_str = "❄️"
    emoji_w  = tw(draw, emoji_str, emoji_font)
    gap      = 18
    total_w  = title_w + gap + emoji_w
    tx       = (W - total_w) // 2
    ty       = 46

    draw.text((tx, ty), title_text, font=hero_font, fill=WHITE)
    draw.text((tx + title_w + gap, ty + 4), emoji_str, font=emoji_font, fill=WHITE,
              embedded_color=True)

    # ── Subtitle ─────────────────────────────────────────────────────────────
    cx(draw, 130, "Real-time snow resort conditions on Bluesky", fonts["h2"], ACCENT)

    # horizontal rule
    draw.line([(MARGIN, 178), (W - MARGIN, 178)], fill=RULE, width=1)

    # ── Two-column layout ────────────────────────────────────────────────────
    col_l  = MARGIN
    col_r  = W // 2 + 30
    divx   = W // 2 - 10
    y_top  = 200

    # vertical divider
    draw.line([(divx, y_top), (divx, H - 80)], fill=RULE, width=1)

    # ── LEFT — conditions + navigation ──────────────────────────────────────

    y = y_top + 4
    draw.text((col_l, y), "Get snow conditions", font=fonts["section"], fill=ACCENT)
    y += 38

    for resort in ["Vail", "Park City", "Mammoth", "Snowbird", "Stowe"]:
        draw.text((col_l + 6,  y), "·", font=fonts["body"], fill=DIM)
        draw.text((col_l + 24, y), resort, font=fonts["body"], fill=WHITE)
        y += 32

    y += 10
    draw.line([(col_l, y), (divx - 30, y)], fill=RULE, width=1)
    y += 18

    draw.text((col_l, y), "Menu navigation", font=fonts["section"], fill=ACCENT)
    y += 38

    for kw, desc in [
        ("menu",  "show main menu"),
        ("back",  "go up one level"),
        ("1 – 5", "select numbered option"),
    ]:
        kw_w = tw(draw, kw, fonts["body"])
        draw.text((col_l + 6, y), kw,         font=fonts["body"], fill=WHITE)
        draw.text((col_l + kw_w + 20, y), f"— {desc}", font=fonts["body"], fill=DIM)
        y += 32

    # ── RIGHT — commands ──────────────────────────────────────────────────────

    y2 = y_top + 4
    draw.text((col_r, y2), "DM commands", font=fonts["section"], fill=ACCENT)
    y2 += 38

    commands = [
        ("learn",           "snow science links"),
        ("photos",          "post a snow photo"),
        ("music",           "weather & mountain playlists"),
        ("",                ""),                         # spacer
        ("set home Vail",   "save home resort"),
        ("settings",        "view preferences"),
        ("reset",           "clear preferences"),
        ("",                ""),
        ("help",            "show this guide"),
    ]
    for kw, desc in commands:
        if not kw:
            y2 += 10
            continue
        kw_w = tw(draw, kw, fonts["body"])
        draw.text((col_r + 6, y2), kw,               font=fonts["body"], fill=WHITE)
        draw.text((col_r + kw_w + 20, y2), f"— {desc}", font=fonts["body"], fill=DIM)
        y2 += 32

    # ── Footer ────────────────────────────────────────────────────────────────
    draw.line([(MARGIN, H - 64), (W - MARGIN, H - 64)], fill=RULE, width=1)
    foot = "@snowbot.bsky.social  ·  DM or mention us for instant conditions"
    cx(draw, H - 48, foot, fonts["tag"], DIM)

    # ── Save ─────────────────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    final = img.convert("RGB")
    final.save(output, "PNG", optimize=True)
    print(f"Saved → {output}  ({output.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Snowbot help card image")
    parser.add_argument("--output", default="docs/snowbot_help.png")
    args = parser.parse_args()
    render(Path(args.output))
