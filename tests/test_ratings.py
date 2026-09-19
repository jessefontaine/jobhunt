import json
from datetime import date

import pytest

from jobhunt.digest import RunInfo, render_digest
from jobhunt.models import Listing, Rating, Score
from jobhunt.ratings import ingest_ratings, parse_digest, rebuild_from_jsonl
from jobhunt.store import Store


def L(n):
    return Listing(source="s", title=f"Job {n}", employer="Uni", url=f"https://x.org/j/{n}")


DIGEST = """# Job digest — 2026-09-17
New: 3 · Unscored: 0

## 1. Job 1 — Uni
<!-- id: aaaaaaaaaaaa -->
score 80 · PhD · [s](https://x.org/j/1)
**Why:** yes
rating: 4
note: looks great, close to my lab

## 2. Job 2 — Uni
<!-- id: bbbbbbbbbbbb -->
score 50 · RA · [s](https://x.org/j/2)
**Why:** hmm
rating:
note:

## 3. Job 3 — Uni
<!-- id: cccccccccccc -->
score 20 · Other · [s](https://x.org/j/3)
**Why:** no
rating: seven
note: typo
"""


def test_parse_digest_extracts_filled_ratings_and_reports_invalid():
    parsed, errors = parse_digest(DIGEST)
    assert parsed == [("aaaaaaaaaaaa", 4, "looks great, close to my lab")]
    assert len(errors) == 1
    assert "line 22" in errors[0] and "seven" in errors[0]


def test_parse_digest_accepts_rating_with_trailing_text_and_missing_note():
    text = "<!-- id: aaaaaaaaaaaa -->\nrating: 5 (apply asap)\n"
    parsed, errors = parse_digest(text)
    assert parsed == [("aaaaaaaaaaaa", 5, "")]
    assert errors == []


def test_parse_digest_rejects_out_of_range():
    text = "<!-- id: aaaaaaaaaaaa -->\nrating: 0\nnote:\n"
    parsed, errors = parse_digest(text)
    assert parsed == []
    assert len(errors) == 1


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "jobs.sqlite")
    listings = [L(1), L(2), L(3)]
    store.upsert_listings(listings)
    scores = {lst.id: Score(listing_id=lst.id, score=50, why="w") for lst in listings}
    md = render_digest(date(2026, 9, 17), listings, scores, RunInfo(new=3))
    digest = tmp_path / "digests" / "2026-09-17.md"
    digest.parent.mkdir()
    digest.write_text(md)
    return store, listings, digest, tmp_path / "ratings.jsonl"


def _fill(digest, listing_id, rating, note=""):
    text = digest.read_text()
    block_start = text.index(f"<!-- id: {listing_id} -->")
    head, tail = text[:block_start], text[block_start:]
    tail = tail.replace("rating:\nnote:", f"rating: {rating}\nnote: {note}", 1)
    digest.write_text(head + tail)


def test_ingest_appends_jsonl_and_updates_store(env):
    store, listings, digest, jsonl = env
    _fill(digest, listings[0].id, 4, "nice")
    _fill(digest, listings[2].id, 1)
    result = ingest_ratings(store, digest, jsonl)
    assert result.added == 2 and result.errors == []
    records = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert {r["listing_id"] for r in records} == {listings[0].id, listings[2].id}
    assert records[0]["digest"] == "2026-09-17.md"
    assert store.get_rating(listings[0].id).note == "nice"


def test_ingest_is_idempotent_and_records_changes(env):
    store, listings, digest, jsonl = env
    _fill(digest, listings[0].id, 4)
    assert ingest_ratings(store, digest, jsonl).added == 1
    assert ingest_ratings(store, digest, jsonl).added == 0
    assert len(jsonl.read_text().splitlines()) == 1
    text = digest.read_text().replace("rating: 4", "rating: 2")
    digest.write_text(text)
    assert ingest_ratings(store, digest, jsonl).added == 1
    assert len(jsonl.read_text().splitlines()) == 2
    assert store.get_rating(listings[0].id).rating == 2


def test_rebuild_from_jsonl_replays_latest(env):
    store, listings, digest, jsonl = env
    jsonl.write_text(
        "\n".join(
            [
                Rating(listing_id=listings[0].id, rating=3).model_dump_json(),
                Rating(listing_id=listings[0].id, rating=5, note="later").model_dump_json(),
                Rating(listing_id=listings[1].id, rating=2).model_dump_json(),
            ]
        )
        + "\n"
    )
    fresh = Store(digest.parent.parent / "fresh.sqlite")
    fresh.upsert_listings(listings)
    n = rebuild_from_jsonl(fresh, jsonl)
    assert n == 3
    assert fresh.get_rating(listings[0].id).rating == 5
    assert fresh.get_rating(listings[1].id).rating == 2
