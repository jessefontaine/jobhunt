from starlette.datastructures import QueryParams

from jobhunt.filters import Filters, facets
from jobhunt.models import Listing, Rating, Score


def listing(source="euraxess", url="https://x.org/1"):
    return Listing(source=source, title="t", employer="e", url=url)


def score(n, *tags):
    return Score(listing_id="x", score=n, area_tags=list(tags))


def test_default_keeps_everything_including_unscored():
    f = Filters()
    assert f.keeps(listing(), score(0))
    assert f.keeps(listing(), None)


def test_score_range_is_inclusive_and_drops_unscored_once_the_floor_rises():
    f = Filters(min_score=40, max_score=80)
    assert f.keeps(listing(), score(40)) and f.keeps(listing(), score(80))
    assert not f.keeps(listing(), score(39)) and not f.keeps(listing(), score(81))
    assert not f.keeps(listing(), None)


def test_tags_match_any_selected_tag():
    f = Filters(tags=frozenset({"fmri", "eeg"}))
    assert f.keeps(listing(), score(50, "eeg", "ml"))
    assert not f.keeps(listing(), score(50, "ml"))
    assert not f.keeps(listing(), None)  # no score, no tags


def test_sources_and_ratings():
    f = Filters(sources=frozenset({"indeed"}), ratings=frozenset({1, 2}))
    rated = Rating(listing_id="x", rating=1)
    assert f.keeps(listing("indeed"), score(50), rated)
    assert not f.keeps(listing("euraxess"), score(50), rated)
    assert not f.keeps(listing("indeed"), score(50), Rating(listing_id="x", rating=4))
    assert not f.keeps(listing("indeed"), score(50), None)


def test_from_query_overrides_the_defaults_it_is_given():
    default = Filters(min_score=30)
    assert Filters.from_query(QueryParams(""), default) == default
    f = Filters.from_query(
        QueryParams("min=10&max=70&tag=eeg&tag=ml&source=indeed&rating=1&rating=x"), default
    )
    assert f == Filters(10, 70, frozenset({"eeg", "ml"}), frozenset({"indeed"}), frozenset({1}))


def test_from_query_clamps_and_orders_the_range():
    f = Filters.from_query(QueryParams("min=150&max=-5"), Filters())
    assert (f.min_score, f.max_score) == (0, 100)
    f = Filters.from_query(QueryParams("min=abc&max=20"), Filters(min_score=50))
    assert (f.min_score, f.max_score) == (20, 50)


def test_every_source_ticked_is_no_source_filter():
    f = Filters(sources=frozenset({"a", "b"}))
    assert f.within({"a", "b"}).sources == frozenset()
    assert f.within({"a", "b", "c"}).sources == frozenset({"a", "b"})


def test_facets_count_what_is_present_most_common_first():
    rows = [
        (listing("indeed", "https://x.org/1"), score(50, "ml", "eeg"), None),
        (listing("indeed", "https://x.org/2"), score(50, "ml"), Rating(listing_id="x", rating=2)),
        (listing("euraxess", "https://x.org/3"), None, Rating(listing_id="y", rating=2)),
    ]
    found = facets(rows)
    assert found.tags == [("ml", 2), ("eeg", 1)]
    assert found.sources == [("indeed", 2), ("euraxess", 1)]
    assert found.ratings == [(2, 2)]
