"""
Render an animated GIF of a DM conversation with Snowbot.

The conversation is produced by the bot's real request handler (the same code
path the DM poller feeds), with fake notification channels so nothing is sent
to Bluesky. The snow report comes from a saved Open-Meteo/SNOTEL snapshot
(docs/dm_demo_report.json) so the GIF is reproducible offline.

Each step is rendered as a chat screen with headless Chrome, then stitched
into a GIF with ffmpeg.

Usage:
    python scripts/make_dm_demo.py
    python scripts/make_dm_demo.py --out docs/snowbot_dm_demo.gif --keep-frames

Requires: Google Chrome, ffmpeg, and a .local.env (Settings.load()).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import Snowbot                                                  # noqa: E402
from snowbot.channels.alert.base import AlertRequest                     # noqa: E402
from snowbot.channels.alert.dm_poller import DMAlertChannel, _extract_location  # noqa: E402
from snowbot.channels.notify.base import NotificationResult              # noqa: E402
from snowbot.channels.notify.bluesky_dm import BlueskyDMNotifyChannel    # noqa: E402
from snowbot.config.settings import Settings                             # noqa: E402
from snowbot.snow.resolver import _RESORTS_BY_REGION                     # noqa: E402
from snowbot.snow.service import _deserialize_report                     # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WIDTH, HEIGHT, SCALE = 440, 860, 2
OUT_WIDTH = 440

# Latency footers are real in production but read as "0.0s" here — drop them
_FOOTERS = [
    re.compile(r"\n\n\(\d+(\.\d+)?s on [^)]*\)$"),
    re.compile(r"\n\nResponded in [\d.]+s on an? [^.]*\.$"),
]


class _DemoDMChannel:
    """Collects DMs the way BlueskyDMNotifyChannel would send them."""
    CHANNEL_NAME = "bluesky_dm"

    def __init__(self):
        self.messages: list[str] = []

    def send(self, payload):
        full = "\n\n".join(payload.post_thread)
        self.messages.extend(BlueskyDMNotifyChannel._split_message(full))
        return NotificationResult(success=True, channel=self.CHANNEL_NAME)


def _clean(text: str) -> str:
    for rx in _FOOTERS:
        text = rx.sub("", text)
    return text


def run_conversation(report_json: Path, resort: str = "Breckenridge",
                     region: str = "US Rockies") -> list[tuple[str, str]]:
    """Drive the bot through menu → snow reports → region → resort."""
    settings = Settings.load()
    tmp = tempfile.mkdtemp()
    settings.db_path = Path(tmp) / "demo.db"
    bot = Snowbot(settings)
    bot._db.connect()

    data = json.loads(report_json.read_text())
    resolver = bot._snow._resolver
    bot._snow.lookup = lambda raw: _deserialize_report(data, resolver.resolve(raw))

    dm = _DemoDMChannel()
    bot.register_notify_channel(dm)

    regions = list(_RESORTS_BY_REGION)
    resorts = [r["display"] for r in _RESORTS_BY_REGION[region]]
    steps = ["menu", "1", str(regions.index(region) + 1), str(resorts.index(resort) + 1)]

    transcript: list[tuple[str, str]] = []
    for n, text in enumerate(steps, 1):
        transcript.append(("user", text))
        before = len(dm.messages)
        cmd = DMAlertChannel._extract_command(text)
        bot._handle_request(AlertRequest(
            source_channel="dm", requester_handle="skier.bsky.social",
            requester_did="did:plc:demo-skier",
            raw_location=None if cmd else _extract_location(text),
            raw_content=text, reply_to_uri=f"demo-convo-{n}", command=cmd,
            received_at=datetime.utcnow(),
        ))
        transcript.extend(("bot", _clean(m)) for m in dm.messages[before:])

    bot._db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return transcript


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_CSS = """
:root { --bg:#f4f6fa; --bot:#ffffff; --user:#1083fe; --ink:#16202c; --dim:#6b7a8c; --line:#e1e6ee; }
* { box-sizing:border-box; margin:0; }
body { width:%(w)dpx; height:%(h)dpx; background:var(--bg); color:var(--ink);
       font:15px/1.38 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
       display:flex; flex-direction:column; overflow:hidden; }
header { background:#fff; border-bottom:1px solid var(--line); padding:14px 16px;
         display:flex; align-items:center; gap:12px; flex:none; }
.avatar { width:38px; height:38px; border-radius:50%%; background:linear-gradient(135deg,#0d2a5c,#3b8de0);
          display:grid; place-items:center; font-size:20px; }
.name { font-weight:700; } .handle { color:var(--dim); font-size:13px; }
main { flex:1; display:flex; flex-direction:column; justify-content:flex-end; gap:8px;
       padding:12px 14px; overflow:hidden; }
.msg { max-width:86%%; padding:9px 12px; border-radius:18px; white-space:pre-wrap; word-wrap:break-word; }
.bot { align-self:flex-start; background:var(--bot); border:1px solid var(--line); border-bottom-left-radius:6px; }
.user { align-self:flex-end; background:var(--user); color:#fff; border-bottom-right-radius:6px; font-weight:600; }
.typing { color:var(--dim); letter-spacing:3px; font-weight:700; }
footer { background:#fff; border-top:1px solid var(--line); padding:12px 16px; flex:none; }
.input { border:1px solid var(--line); border-radius:20px; padding:9px 14px; color:var(--dim); }
"""


def render_html(messages: list[tuple[str, str]], typing: bool = False) -> str:
    bubbles = "\n".join(
        f'<div class="msg {who}">{html.escape(text)}</div>' for who, text in messages
    )
    if typing:
        bubbles += '\n<div class="msg bot typing">•••</div>'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS % {"w": WIDTH, "h": HEIGHT}}</style></head><body>
<header><div class="avatar">❄️</div><div><div class="name">Snowbot ❄️</div>
<div class="handle">@snowbot.bsky.social</div></div></header>
<main>{bubbles}</main>
<footer><div class="input">Write a message</div></footer>
</body></html>"""


def screenshot(html_text: str, png: Path) -> None:
    page = png.with_suffix(".html")
    page.write_text(html_text, encoding="utf-8")
    subprocess.run([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--force-device-scale-factor={SCALE}", f"--window-size={WIDTH},{HEIGHT}",
        f"--screenshot={png}", page.as_uri(),
    ], check=True, capture_output=True)


def build_frames(transcript: list[tuple[str, str]]) -> list[tuple[str, float]]:
    """(html, seconds) per frame: user message, typing dots, then each bot reply."""
    frames = [(render_html([]), 1.0)]
    shown: list[tuple[str, str]] = []
    for i, (who, text) in enumerate(transcript):
        if who == "user":
            shown.append((who, text))
            frames.append((render_html(shown), 0.9))
            frames.append((render_html(shown, typing=True), 0.8))
        else:
            shown.append((who, text))
            last = i == len(transcript) - 1
            lines = text.count("\n") + 1
            frames.append((render_html(shown), 5.0 if last else min(1.5 + lines * 0.08, 3.5)))
    return frames


def make_gif(frames: list[tuple[str, float]], out: Path, keep: bool) -> None:
    work = Path(tempfile.mkdtemp(prefix="snowbot_demo_"))
    concat = []
    for i, (page, secs) in enumerate(frames):
        png = work / f"frame_{i:03d}.png"
        screenshot(page, png)
        concat += [f"file '{png}'", f"duration {secs}"]
    concat.append(f"file '{work / f'frame_{len(frames) - 1:03d}.png'}'")  # ffmpeg needs the last frame twice
    (work / "frames.txt").write_text("\n".join(concat) + "\n")

    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={OUT_WIDTH}:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=max_colors=128:stats_mode=diff[p];"
          "[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(work / "frames.txt"), "-vf", vf, "-loop", "0", str(out),
    ], check=True)
    if keep:
        print(f"Frames kept in {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a Snowbot DM demo GIF")
    parser.add_argument("--report-json", type=Path, default=ROOT / "docs/dm_demo_report.json")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/snowbot_dm_demo.gif")
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()

    transcript = run_conversation(args.report_json)
    for who, text in transcript:
        print(f"--- {who} ---\n{text}")
    frames = build_frames(transcript)
    make_gif(frames, args.out, args.keep_frames)
    print(f"\nWrote {args.out} ({len(frames)} frames, {args.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
