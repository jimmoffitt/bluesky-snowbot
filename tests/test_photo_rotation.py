"""Per-user photo rotation: each user sees every photo before any repeats for them."""

from pathlib import Path

from snowbot.content import ContentLibrary, Photo

ALICE, BOB = "did:plc:alice", "did:plc:bob"


def _library(n: int) -> ContentLibrary:
    photos = [Photo(filename=f"p{i}.jpg", caption=f"photo {i}", photos_dir=Path("."))
              for i in range(n)]
    return ContentLibrary(links=[], playlists=[], photos=photos)


def _serve(lib, db, did):
    photo = lib.pick_photo(db.get_photo_last_posted(did))
    db.record_photo_post(did, photo.filename)
    return photo.filename


def test_no_repeats_for_a_user_until_they_have_seen_every_photo(db):
    lib = _library(10)
    picked = [_serve(lib, db, ALICE) for _ in range(10)]
    assert sorted(picked) == sorted(f"p{i}.jpg" for i in range(10))


def test_users_rotate_independently(db):
    lib = _library(10)
    alice = [_serve(lib, db, ALICE) for _ in range(10)]
    # Alice has seen everything; Bob still gets a full, fresh rotation
    bob = [_serve(lib, db, BOB) for _ in range(10)]
    assert sorted(bob) == sorted(alice)
    assert len(db.get_photo_last_posted(BOB)) == 10


def test_after_full_cycle_recent_photos_are_not_reused():
    lib = _library(10)
    # p0 sent longest ago ... p9 most recently
    last_posted = {f"p{i}.jpg": f"2026-11-{i + 1:02d}T00:00:00" for i in range(10)}
    for _ in range(200):
        assert lib.pick_photo(last_posted).filename in {f"p{i}.jpg" for i in range(5)}


def test_single_photo_library_still_works():
    lib = _library(1)
    assert lib.pick_photo({"p0.jpg": "2026-11-01T00:00:00"}).filename == "p0.jpg"


def test_empty_library_returns_none():
    assert _library(0).pick_photo({}) is None


def test_record_photo_post_counts_per_user(db):
    db.record_photo_post(ALICE, "a.jpg")
    db.record_photo_post(ALICE, "a.jpg")
    db.record_photo_post(BOB, "a.jpg")
    counts = {r["user_did"]: r["post_count"] for r in db._conn.execute(
        "SELECT user_did, post_count FROM photo_posts WHERE filename = 'a.jpg'")}
    assert counts == {ALICE: 2, BOB: 1}
