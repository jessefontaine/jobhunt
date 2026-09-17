from datetime import date

import pytest

from jobhunt.models import Listing, Rating, Score
from jobhunt.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "jobs.sqlite")


def listing(n: int, **kw) -> Listing:
    return Listing(source="src", title=f"Job {n}", employer="Uni", url=f"https://x.org/j/{n}", **kw)


def test_upsert_returns_only_new_ids(store):
    new = store.upsert_listings([listing(1), listing(2)])
    assert new == {listing(1).id, listing(2).id}
    again = store.upsert_listings([listing(2), listing(3)])
    assert again == {listing(3).id}
    assert store.count_listings() == 3


def test_upsert_updates_mutable_fields_but_keeps_description(store):
    first = listing(1, deadline=date(2026, 10, 1), description="full text")
    store.upsert_listings([first])
    store.upsert_listings([listing(1, deadline=date(2026, 11, 1), description="")])
    got = store.get_listing(first.id)
    assert got.deadline == date(2026, 11, 1)
    assert got.description == "full text"


def test_unscored_excludes_scored_and_expired(store):
    today = date(2026, 9, 17)
    store.upsert_listings(
        [
            listing(1, deadline=date(2026, 10, 1)),
            listing(2, deadline=date(2026, 9, 1)),  # expired
            listing(3),  # no deadline
        ]
    )
    store.save_scores([Score(listing_id=listing(3).id, score=50)])
    ids = {lst.id for lst in store.unscored_listings(today)}
    assert ids == {listing(1).id}


def test_scores_roundtrip(store):
    store.upsert_listings([listing(1)])
    sc = Score(
        listing_id=listing(1).id,
        score=84,
        role_type="phd",
        area_tags=["vision", "fMRI"],
        why="fits",
        concerns="dutch",
        model="sonnet",
    )
    store.save_scores([sc])
    got = store.get_score(listing(1).id)
    assert got.score == 84
    assert got.area_tags == ["vision", "fMRI"]
    assert got.role_type == "phd"


def test_ratings_latest_wins(store):
    store.upsert_listings([listing(1)])
    store.save_rating(Rating(listing_id=listing(1).id, rating=3, note="meh"))
    store.save_rating(Rating(listing_id=listing(1).id, rating=5, note="apply"))
    got = store.get_rating(listing(1).id)
    assert got.rating == 5
    assert got.note == "apply"
    assert store.rated_ids() == {listing(1).id}


def test_rated_examples_returns_strong_ratings_most_recent_first(store):
    store.upsert_listings([listing(n) for n in range(1, 5)])
    store.save_rating(Rating(listing_id=listing(1).id, rating=5))
    store.save_rating(Rating(listing_id=listing(2).id, rating=3))
    store.save_rating(Rating(listing_id=listing(3).id, rating=1))
    store.save_rating(Rating(listing_id=listing(4).id, rating=4))
    examples = store.rated_examples(limit=10)
    assert [r.listing_id for _, r in examples] == [listing(4).id, listing(3).id, listing(1).id]
    assert examples[0][0].title == "Job 4"
    assert len(store.rated_examples(limit=2)) == 2
