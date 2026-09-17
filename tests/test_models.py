from datetime import date

from jobhunt.models import Listing, make_id


def test_make_id_is_stable_hash_of_source_and_url():
    a = make_id("academictransfer", "https://example.org/jobs/1")
    b = make_id("academictransfer", "https://example.org/jobs/1")
    assert a == b
    assert len(a) == 12
    assert make_id("euraxess", "https://example.org/jobs/1") != a


def test_make_id_ignores_trailing_slash_and_fragment():
    assert make_id("s", "https://x.org/j/1/") == make_id("s", "https://x.org/j/1#top")


def test_listing_computes_id_when_missing():
    lst = Listing(source="s", title="T", employer="E", url="https://x.org/j/1")
    assert lst.id == make_id("s", "https://x.org/j/1")
    assert lst.location is None
    assert lst.posted is None
    assert lst.deadline is None
    assert lst.summary == ""
    assert lst.description == ""


def test_listing_is_expired_uses_deadline():
    today = date(2026, 9, 17)
    open_l = Listing(source="s", title="T", employer="E", url="u", deadline=date(2026, 9, 17))
    past_l = Listing(source="s", title="T", employer="E", url="u", deadline=date(2026, 9, 16))
    none_l = Listing(source="s", title="T", employer="E", url="u")
    assert not open_l.is_expired(today)
    assert past_l.is_expired(today)
    assert not none_l.is_expired(today)
