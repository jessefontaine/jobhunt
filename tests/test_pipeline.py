from datetime import date

import pytest

from jobhunt import pipeline
from jobhunt.models import Listing
from jobhunt.sources import _SOURCES
from jobhunt.sources.base import SourceResult
from jobhunt.store import Store


def L(n):
    return Listing(
        source="fake",
        title=f"Job {n}",
        employer="Uni",
        url=f"https://x.org/j/{n}",
        summary=f"summary {n}",
    )


class FakeSource:
    name = "fake"
    detail_calls: list[str] = []

    def fetch(self, cfg, http):
        return SourceResult(listings=[L(1), L(2)], manual_urls={"Fake search": "https://x.org/s"})

    def fetch_detail(self, url, http):
        FakeSource.detail_calls.append(url)
        if url.endswith("/2"):
            raise RuntimeError("detail 500")
        return f"full text for {url}"


@pytest.fixture
def registered():
    FakeSource.detail_calls = []
    _SOURCES["fake"] = FakeSource
    yield
    del _SOURCES["fake"]


def test_fetch_sources_fetches_details_for_new_listings_only(registered, tmp_path):
    store = Store(tmp_path / "db")
    info = pipeline.fetch_sources(store, http=None, sources=[("fake", {})])
    assert info.new == 2
    assert sorted(FakeSource.detail_calls) == ["https://x.org/j/1", "https://x.org/j/2"]
    assert store.get_listing(L(1).id).description == "full text for https://x.org/j/1"
    assert store.get_listing(L(2).id).description == ""  # detail failed → left empty
    assert info.manual == {"Fake search": "https://x.org/s"}
    assert "detail" in info.errors["fake"]

    FakeSource.detail_calls = []
    info = pipeline.fetch_sources(store, http=None, sources=[("fake", {})])
    assert info.new == 0
    assert FakeSource.detail_calls == []


def test_fetch_sources_survives_source_exception(tmp_path):
    class Boom:
        name = "boom"

        def fetch(self, cfg, http):
            raise ValueError("bad config")

    _SOURCES["boom"] = Boom
    try:
        info = pipeline.fetch_sources(Store(tmp_path / "db"), None, [("boom", {})])
    finally:
        del _SOURCES["boom"]
    assert info.new == 0
    assert "ValueError" in info.errors["boom"]


def test_build_digest_excludes_expired(tmp_path):
    store = Store(tmp_path / "db")
    store.upsert_listings(
        [
            Listing(source="s", title="Gone", employer="U", url="u1", deadline=date(2026, 1, 1)),
            Listing(source="s", title="Open", employer="U", url="u2", deadline=date(2026, 12, 1)),
        ]
    )
    path = pipeline.build_digest(store, tmp_path / "digests", date(2026, 9, 17), pipeline.RunInfo())
    md = path.read_text()
    assert "Open" in md and "Gone" not in md
