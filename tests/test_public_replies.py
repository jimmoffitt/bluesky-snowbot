"""Public mention replies: known resorts only, one short post that invites a DM, quiet on errors."""

from datetime import datetime, timedelta

from snowbot.snow.resolver import query_for
from tests.test_admin_alerts import ADMIN, _admin_dms, _send, _timeout, make_bot  # noqa: F401


def _mention(bot, text):
    _send(bot, text, channel="firehose")


def test_known_resort_gets_one_short_post_inviting_a_dm(make_bot, sample_snow_report):
    b = make_bot(lookup=lambda raw: sample_snow_report)
    _mention(b, "Vail")
    assert len(b.post.payloads) == 1
    payload = b.post.payloads[0]
    assert len(payload.post_thread) == 1                    # one post, not a thread
    post = payload.post_thread[0]
    assert len(post) <= 300
    assert post.startswith("⛷️ Vail, CO")
    assert 'new (24h)' in post and "°F" in post
    assert post.endswith('💬 DM me "Vail" for the full report, 7-day forecast and trail map.')
    assert "http" not in post and "Responded in" not in post
    assert payload.reply_to_uri is not None                 # threaded under the mention
    assert b.dm.payloads == []


def test_chatter_after_a_mention_is_ignored(make_bot):
    looked_up = []
    b = make_bot(lookup=lambda raw: looked_up.append(raw))
    for text in ("you rock", "nice photo!", "thanks for the help"):
        _mention(b, text)
    assert looked_up == []                                  # never geocoded
    assert b.post.payloads == [] and b.dm.payloads == []


def test_lookup_failure_on_a_mention_is_silent_but_alerts_admin(make_bot):
    b = make_bot()                                          # lookup raises ReadTimeout
    _mention(b, "Vail")
    assert b.post.payloads == []                            # no public "sorry"
    assert len(_admin_dms(b)) == 1


def test_stale_report_is_flagged_in_public_post(make_bot, sample_snow_report):
    sample_snow_report.stale_fetched_at = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    try:
        _timeout()
    except Exception as exc:
        sample_snow_report.stale_error = exc
    b = make_bot(lookup=lambda raw: sample_snow_report)
    _mention(b, "Vail")
    post = b.post.payloads[0].post_thread[0]
    assert "⚠️ From 2 hr ago (live data unavailable)" in post
    assert len(post) <= 300


def test_dm_reports_still_get_the_full_thread_and_map(make_bot, sample_snow_report):
    b = make_bot(lookup=lambda raw: sample_snow_report)
    _send(b, "Vail")
    report = "\n\n".join(b.dm.payloads[0].post_thread)
    assert "7-Day Forecast" in report or len(b.dm.payloads[0].post_thread) >= 2
    assert "🗺️ Trail map: https://www.vail.com/" in report


def test_dm_suggestion_names_the_right_resort():
    assert query_for("Vail", "CO") == "Vail"
    assert query_for("Crystal Mountain", "MI") == "crystal mountain michigan"
    assert query_for("Crystal Mountain", "WA") == "Crystal Mountain"
