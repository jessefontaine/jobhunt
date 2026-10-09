"""View filters for the Queue and Rated pages: score range, tags, sources and ratings.

They live in the page's query string and are never saved. The score range starts from the
settings (`display.min_score`/`max_score` on the queue) and overrides them for that view only;
the Reset link drops every filter and the page is back to what the settings show.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from jobhunt.models import Listing, Rating, Score


class Query(Protocol):
    """What a query string offers (starlette's `QueryParams` does)."""

    def get(self, key: str, default: str | None = None) -> str | None: ...
    def getlist(self, key: str) -> list[str]: ...


def _int(value: str | None, fallback: int) -> int:
    try:
        return max(0, min(100, int(value))) if value is not None else fallback
    except ValueError:
        return fallback


@dataclass(frozen=True)
class Filters:
    min_score: int = 0
    max_score: int = 100
    tags: frozenset[str] = frozenset()  # any of these; empty = no tag filter
    sources: frozenset[str] = frozenset()  # empty = every source
    ratings: frozenset[int] = frozenset()  # empty = any rating (or none)

    @classmethod
    def from_query(cls, query: Query, default: Filters) -> Filters:
        """`?min=&max=&tag=…&source=…&rating=…`; what the query leaves out keeps `default`."""
        low = _int(query.get("min"), default.min_score)
        high = _int(query.get("max"), default.max_score)
        ratings = {int(r) for r in query.getlist("rating") if r in {"1", "2", "3", "4", "5"}}
        return cls(
            min_score=min(low, high),
            max_score=max(low, high),
            tags=frozenset(query.getlist("tag")) or default.tags,
            sources=frozenset(query.getlist("source")) or default.sources,
            ratings=frozenset(ratings) or default.ratings,
        )

    def within(self, present: Collection[str]) -> Filters:
        """Every source on the page ticked is the same view as no source filter."""
        if self.sources >= set(present):
            return replace(self, sources=frozenset())
        return self

    def keeps(self, listing: Listing, score: Score | None, rating: Rating | None = None) -> bool:
        """Unscored listings pass only while nothing asks about a score: floor 0, no tags."""
        if score is None:
            if self.min_score > 0 or self.tags:
                return False
        elif not self.min_score <= score.score <= self.max_score:
            return False
        elif self.tags and not self.tags.intersection(score.area_tags):
            return False
        if self.sources and listing.source not in self.sources:
            return False
        return not self.ratings or (rating is not None and rating.rating in self.ratings)


@dataclass(frozen=True)
class Facets:
    """The choices a filter bar offers: what is on the page, with how often it occurs."""

    tags: list[tuple[str, int]]
    sources: list[tuple[str, int]]
    ratings: list[tuple[int, int]]


def _common(counter: Counter) -> list:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def facets(rows: Iterable[tuple[Listing, Score | None, Rating | None]]) -> Facets:
    tags: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    ratings: Counter[int] = Counter()
    for listing, score, rating in rows:
        sources[listing.source] += 1
        if score is not None:
            tags.update(set(score.area_tags))
        if rating is not None:
            ratings[rating.rating] += 1
    return Facets(_common(tags), _common(sources), sorted(ratings.items(), reverse=True))
