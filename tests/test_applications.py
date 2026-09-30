from datetime import date

import pytest

from jobhunt.applications import (
    rebuild_statuses,
    record_status,
    write_letter,
)
from jobhunt.config import Paths
from jobhunt.models import Listing, Rating, StatusEvent
from jobhunt.pipeline import write_shortlist
from jobhunt.store import Store

TODAY = date(2026, 9, 30)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "jobs.sqlite")


def listing(n: int, **kw) -> Listing:
    return Listing(source="src", title=f"Job {n}", employer="Uni", url=f"https://x.org/j/{n}", **kw)


def test_record_status_appends_to_the_log_and_the_store(store, tmp_path):
    lst = listing(1)
    store.upsert_listings([lst])
    log = tmp_path / "status.jsonl"
    record_status(store, log, lst.id, "applied", on=date(2026, 9, 28), note="via portal")
    record_status(store, log, lst.id, "interview")
    got = store.get_status(lst.id)
    assert got.status == "interview"
    assert len(log.read_text().splitlines()) == 2  # the history stays in the log
    first = StatusEvent.model_validate_json(log.read_text().splitlines()[0])
    assert (first.status, first.on, first.note) == ("applied", date(2026, 9, 28), "via portal")


def test_rebuild_replays_the_log_latest_wins(store, tmp_path):
    lst = listing(1)
    store.upsert_listings([lst])
    log = tmp_path / "status.jsonl"
    record_status(store, log, lst.id, "applied")
    record_status(store, log, lst.id, "rejected")
    fresh = Store(tmp_path / "other.sqlite")
    fresh.upsert_listings([lst])
    assert rebuild_statuses(fresh, log) == 2
    assert fresh.get_status(lst.id).status == "rejected"


def test_unknown_status_is_refused(store, tmp_path):
    with pytest.raises(ValueError):
        record_status(store, tmp_path / "s.jsonl", "abc", "hired")


def test_tracked_listings_leave_the_digest_and_scoring(store, tmp_path):
    a, b, c = listing(1), listing(2), listing(3)
    store.upsert_listings([a, b, c])
    log = tmp_path / "status.jsonl"
    record_status(store, log, a.id, "applied")
    record_status(store, log, c.id, "applied")
    record_status(store, log, c.id, "none")  # cleared: back in the queue
    assert {x.id for x in store.candidate_listings(TODAY)} == {b.id, c.id}
    assert a.id not in {x.id for x in store.unscored_listings(TODAY)}
    assert a.id not in {x.id for x in store.unexpired_listings(TODAY, include_rated=True)}


def test_applications_keep_past_deadline_listings_and_leave_the_shortlist(store, tmp_path):
    old = listing(1, deadline=date(2026, 9, 1))
    fresh = listing(2, deadline=date(2026, 12, 1))
    store.upsert_listings([old, fresh])
    for lst in (old, fresh):
        store.save_rating(Rating(listing_id=lst.id, rating=5))
    log = tmp_path / "status.jsonl"
    record_status(store, log, old.id, "applied", on=date(2026, 8, 20))
    record_status(store, log, fresh.id, "interview", on=date(2026, 9, 29))
    rows = store.applications()
    assert [(lst.id, ev.status) for lst, ev in rows] == [
        (fresh.id, "interview"),
        (old.id, "applied"),
    ]
    assert store.shortlist(TODAY) == []  # shown once, under Applications


def test_shortlist_file_has_an_applications_section(store, tmp_path):
    lst = listing(1, deadline=date(2026, 9, 1))
    store.upsert_listings([lst])
    record_status(store, tmp_path / "s.jsonl", lst.id, "applied", on=date(2026, 8, 20))
    text = write_shortlist(store, tmp_path / "shortlist.md", TODAY)
    assert "## Applications" in text
    assert "**Job 1** — Uni · applied 2026-08-20" in text


def test_write_letter_puts_it_under_applications(tmp_path):
    paths = Paths(tmp_path)
    path = write_letter(paths, "abc123", "Dear committee,\r\n")
    assert path == tmp_path / "applications" / "abc123.md"
    assert path.read_text() == "Dear committee,\n"
