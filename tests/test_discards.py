import json
import re
from datetime import date

import pytest

from jobhunt.discards import rebuild_discards, record_discard, record_restore
from jobhunt.models import DiscardEvent, Listing, Rating
from jobhunt.scoring import SCORE_SCHEMA, parse_response
from jobhunt.store import Store

TODAY = date(2026, 10, 9)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "jobs.sqlite")


def listing(n: int, **kw) -> Listing:
    return Listing(source="src", title=f"Job {n}", employer="Uni", url=f"https://x.org/j/{n}", **kw)


def rate(store, lst, value):
    store.save_rating(Rating(listing_id=lst.id, rating=value))


# -- the log ------------------------------------------------------------------


def test_discard_and_restore_go_to_the_log_and_the_store(store, tmp_path):
    lst = listing(1)
    store.upsert_listings([lst])
    log = tmp_path / "discards.jsonl"
    record_discard(store, log, lst.id, "needs a PhD", by="scoring")
    got = store.get_discard(lst.id)
    assert (got.discarded, got.reason, got.by) == (True, "needs a PhD", "scoring")
    record_restore(store, log, lst.id)
    assert store.get_discard(lst.id).discarded is False
    assert len(log.read_text().splitlines()) == 2


def test_rebuild_replays_the_log_latest_wins(store, tmp_path):
    lst = listing(1)
    store.upsert_listings([lst])
    log = tmp_path / "discards.jsonl"
    record_discard(store, log, lst.id, "x")
    fresh = Store(tmp_path / "other.sqlite")
    fresh.upsert_listings([lst])
    assert rebuild_discards(fresh, log) == 1
    assert fresh.get_discard(lst.id).discarded


# -- what a discard hides -------------------------------------------------------


def test_discarded_listings_leave_queue_scoring_and_rescoring(store, tmp_path):
    keep, drop = listing(1), listing(2)
    store.upsert_listings([keep, drop])
    record_discard(store, tmp_path / "d.jsonl", drop.id, "postdoc")
    for found in (
        store.candidate_listings(TODAY),
        store.unscored_listings(TODAY),
        store.unexpired_listings(TODAY, include_rated=True),
    ):
        assert [lst.id for lst in found] == [keep.id]
    assert [lst.id for lst, _ in store.discarded()] == [drop.id]


def test_discarded_ratings_reach_neither_calibration_nor_learning(store, tmp_path):
    keep, drop = listing(1), listing(2)
    store.upsert_listings([keep, drop])
    rate(store, keep, 5)
    rate(store, drop, 1)
    record_discard(store, tmp_path / "d.jsonl", drop.id, "")
    assert [lst.id for lst, _ in store.all_ratings()] == [keep.id]
    assert [lst.id for lst, _ in store.rated_examples(10)] == [keep.id]
    assert store.rated_ids() == {keep.id}
    assert store.ratings_since(None) == 1
    assert store.get_rating(drop.id).rating == 1  # kept, so a restore brings it back
    record_restore(store, tmp_path / "d.jsonl", drop.id)
    assert len(store.all_ratings()) == 2


def test_discarded_listings_leave_the_shortlist(store, tmp_path):
    lst = listing(1)
    store.upsert_listings([lst])
    rate(store, lst, 5)
    record_discard(store, tmp_path / "d.jsonl", lst.id, "")
    assert store.shortlist(TODAY) == []


def test_fetching_a_discarded_listing_again_keeps_it_discarded(store, tmp_path):
    lst = listing(1)
    store.upsert_listings([lst])
    record_discard(store, tmp_path / "d.jsonl", lst.id, "")
    assert store.upsert_listings([listing(1)]) == set()  # not new: no detail fetch either
    assert store.candidate_listings(TODAY) == []


# -- scoring flags ineligible listings ----------------------------------------------


def test_schema_and_parser_carry_the_ineligible_reason():
    assert "ineligible" in json.dumps(SCORE_SCHEMA)
    raw = json.dumps(
        {
            "structured_output": {
                "scores": [{"id": "a", "score": 5, "ineligible": "requires a PhD"}]
            }
        }
    )
    (score,) = parse_response(raw, {"a"}, "m")
    assert score.ineligible == "requires a PhD"


def flagging_runner(flag_title):
    """Scores like the shared fake runner, but calls the listing titled `flag_title` ineligible."""

    def run(prompt, model, schema):
        listings = json.loads(prompt[prompt.index("# Listings to score") :].split("\n", 1)[1])
        scores = [
            {
                "id": lst["id"],
                "score": 10 if lst["title"] == flag_title else 80,
                "ineligible": "requires a PhD" if lst["title"] == flag_title else "",
            }
            for lst in listings
        ]
        return json.dumps({"structured_output": {"scores": scores}})

    return run


def test_scoring_discards_what_claude_flags_ineligible(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ws.runner = flagging_runner("RA fMRI")
    lines = []
    result = ws.score(progress=lines.append)
    assert result.discarded == 1
    store = ws.store
    titles = [lst.title for lst in store.candidate_listings(date.today())]
    assert titles == ["PhD vision"]
    (lst, event) = store.discarded()[0]
    assert (lst.title, event.reason, event.by) == ("RA fMRI", "requires a PhD", "scoring")
    assert store.get_score(lst.id).score == 10  # the score is kept for the Discarded page
    assert any("1 discarded" in line for line in lines)
    assert ws.paths.discards.exists()


def test_auto_discard_can_be_turned_off(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ws.settings.scoring.auto_discard = False
    ws.runner = flagging_runner("RA fMRI")
    assert ws.score().discarded == 0
    assert ws.store.discarded() == []


def test_a_restored_listing_is_not_discarded_again_on_rescore(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ws.runner = flagging_runner("RA fMRI")
    ws.score()
    (lst, _) = ws.store.discarded()[0]
    ws.restore(lst.id)
    ws.score(rescore=True)
    assert ws.store.discarded() == []
    assert lst.id in {x.id for x in ws.store.candidate_listings(date.today())}


def test_workspace_discard_many_and_restore(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ids = [lst.id for lst in ws.store.candidate_listings(date.today())]
    assert ws.discard_many(ids, "not for me") == 2
    assert ws.store.candidate_listings(date.today()) == []
    lines = ws.paths.discards.read_text().splitlines()
    events = [DiscardEvent.model_validate_json(x) for x in lines]
    assert {e.by for e in events} == {"user"} and {e.reason for e in events} == {"not for me"}
    ws.restore(ids[0])
    assert [lst.id for lst in ws.store.candidate_listings(date.today())] == [ids[0]]


def test_prompt_asks_for_ineligibility_not_poor_fit(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    seen = []
    ws.runner = lambda prompt, model, schema: seen.append(prompt) or flagging_runner("")(
        prompt, model, schema
    )
    ws.score()
    assert re.search(r"ineligible", seen[0])
    assert "not for poor fit" in seen[0]


def test_calibration_skips_discarded_ratings(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ws.score()
    store = ws.store
    ids = [lst.id for lst in store.candidate_listings(date.today())]
    for lst_id in ids:
        store.save_rating(Rating(listing_id=lst_id, rating=1))
    assert ws.calibration().n == 2
    ws.discard(ids[0])
    assert ws.calibration().n == 1


def test_add_says_when_a_link_is_on_the_pile(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ws.discard(ws.find("https://x.org/3").id, "postdoc")
    lines = []
    ws.add("https://x.org/3", title="RA fMRI", score=False, progress=lines.append)
    assert any("discard pile" in line for line in lines)


def test_add_reports_an_ineligible_link(ws):
    ws.runner = flagging_runner("Postdoc")
    lines = []
    ws.add("https://z.org/9", title="Postdoc", progress=lines.append)
    assert any("discarded as ineligible: requires a PhD" in line for line in lines)
