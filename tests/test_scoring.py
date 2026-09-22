import json
from datetime import date, datetime

import pytest

from jobhunt.config import Paths, ScoringConfig
from jobhunt.models import Listing, Rating, Score
from jobhunt.scoring import build_prompt, format_rated, parse_response, score_listings
from jobhunt.store import Store

TODAY = date(2026, 9, 17)


def L(n, **kw):
    base = dict(
        source="s",
        title=f"Job {n}",
        employer=f"Uni {n}",
        url=f"https://x.org/j/{n}",
        description=f"Description of job {n}. " * 5,
    )
    base.update(kw)
    return Listing(**base)


def test_build_prompt_includes_profile_preferences_examples_and_batch():
    batch = [L(1, location="Nijmegen", deadline=date(2026, 10, 1)), L(2)]
    examples = [(L(9, title="Rated job"), Rating(listing_id=L(9).id, rating=5, note="loved it"))]
    prompt = build_prompt("PROFILE TEXT", "PREFS TEXT", "CV TEXT", examples, batch)
    assert "PROFILE TEXT" in prompt and "PREFS TEXT" in prompt and "CV TEXT" in prompt
    assert "Rated job" in prompt and "loved it" in prompt and "5/5" in prompt
    assert L(1).id in prompt and L(2).id in prompt
    assert "Nijmegen" in prompt and "2026-10-01" in prompt
    assert prompt.index("PROFILE TEXT") < prompt.index("Rated job") < prompt.index(L(1).id)


def test_build_prompt_truncates_long_descriptions():
    long = L(1, description="x" * 10_000)
    prompt = build_prompt("p", "q", "c", [], [long])
    assert "x" * 3000 in prompt
    assert "x" * 3001 not in prompt


def _envelope(payload, structured=False):
    """Mimic `claude -p --output-format json` stdout."""
    if structured:
        return json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "structured_output": payload,
            }
        )
    return json.dumps(
        {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(payload)}
    )


GOOD = {
    "scores": [
        {
            "id": "a" * 12,
            "score": 84,
            "role_type": "phd",
            "area_tags": ["vision"],
            "why": "great",
            "concerns": "dutch",
        },
        {
            "id": "b" * 12,
            "score": 12,
            "role_type": "other",
            "area_tags": [],
            "why": "no",
            "concerns": "",
        },
    ]
}


def test_parse_response_reads_structured_output():
    scores = parse_response(_envelope(GOOD, structured=True), {"a" * 12, "b" * 12}, "sonnet")
    assert [s.score for s in scores] == [84, 12]
    assert scores[0].model == "sonnet"
    assert isinstance(scores[0], Score)


def test_parse_response_reads_fenced_json_in_result_text():
    raw = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": "Here you go:\n```json\n" + json.dumps(GOOD) + "\n```",
        }
    )
    scores = parse_response(raw, {"a" * 12, "b" * 12}, "sonnet")
    assert len(scores) == 2


def test_parse_response_accepts_bare_array():
    raw = json.dumps({"type": "result", "is_error": False, "result": json.dumps(GOOD["scores"])})
    assert len(parse_response(raw, {"a" * 12, "b" * 12}, "m")) == 2


def test_parse_response_drops_unknown_ids_and_requires_some_match():
    scores = parse_response(_envelope(GOOD), {"a" * 12}, "m")
    assert [s.listing_id for s in scores] == ["a" * 12]
    with pytest.raises(ValueError):
        parse_response(_envelope(GOOD), {"z" * 12}, "m")


def test_parse_response_raises_on_cli_error_and_garbage():
    err = json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate"})
    with pytest.raises(ValueError, match="authenticate"):
        parse_response(err, {"a" * 12}, "m")
    with pytest.raises(ValueError):
        parse_response("not json at all", {"a" * 12}, "m")
    with pytest.raises(ValueError):
        parse_response(
            json.dumps({"type": "result", "result": '{"scores": [{"id": "x"}]}'}), {"x"}, "m"
        )


@pytest.fixture
def env(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "docs").mkdir()
    paths = Paths(tmp_path)
    paths.profile.write_text("PROFILE")
    paths.preferences.write_text("PREFS")
    paths.cv.write_text("CV")
    store = Store(paths.db)
    return paths, store


def test_score_listings_batches_and_saves(env):
    paths, store = env
    listings = [L(n) for n in range(1, 6)]
    store.upsert_listings(listings)
    calls = []

    def runner(prompt, model, schema):
        ids = [lst.id for lst in listings if lst.id in prompt]
        calls.append((len(ids), model))
        payload = {
            "scores": [
                {
                    "id": i,
                    "score": 50,
                    "role_type": "ra",
                    "area_tags": [],
                    "why": "w",
                    "concerns": "",
                }
                for i in ids
            ]
        }
        return _envelope(payload, structured=True)

    cfg = ScoringConfig(model="haiku", batch_size=2, examples=5)
    result = score_listings(store, paths, cfg, runner, TODAY)
    assert result.scored == 5 and result.failed == 0
    assert calls == [(2, "haiku"), (2, "haiku"), (1, "haiku")]
    assert store.get_score(listings[0].id).score == 50
    # second run: nothing left to score
    assert score_listings(store, paths, cfg, runner, TODAY).scored == 0


def test_score_listings_retries_once_then_leaves_unscored(env):
    paths, store = env
    store.upsert_listings([L(1)])
    attempts = []

    def runner(prompt, model, schema):
        attempts.append(1)
        return "garbage"

    result = score_listings(store, paths, ScoringConfig(batch_size=10), runner, TODAY)
    assert len(attempts) == 2
    assert result.scored == 0 and result.failed == 1
    assert len(result.errors) == 1
    assert store.get_score(L(1).id) is None


def test_score_listings_skips_expired(env):
    paths, store = env
    store.upsert_listings([L(1, deadline=date(2000, 1, 1))])
    called = []
    result = score_listings(store, paths, ScoringConfig(), lambda *a: called.append(a), TODAY)
    assert called == [] and result.scored == 0


def test_score_listings_reports_progress_per_batch(env):
    paths, store = env
    listings = [L(n) for n in range(1, 6)]
    store.upsert_listings(listings)

    def runner(prompt, model, schema):
        ids = [lst.id for lst in listings if lst.id in prompt]
        payload = {
            "scores": [
                {
                    "id": i,
                    "score": 1,
                    "role_type": "ra",
                    "area_tags": [],
                    "why": "w",
                    "concerns": "",
                }
                for i in ids
            ]
        }
        return _envelope(payload, structured=True)

    messages = []
    score_listings(
        store, paths, ScoringConfig(batch_size=2), runner, TODAY, progress=messages.append
    )
    assert messages[0] == "scoring 5 listing(s) in 3 batch(es) with sonnet…"
    assert "batch 1/3" in messages[1] and "batch 3/3" in messages[-1]
    assert any("2 scored" in m for m in messages)


def test_score_listings_reports_failed_batch_in_progress(env):
    paths, store = env
    store.upsert_listings([L(1)])
    messages = []
    score_listings(
        store, paths, ScoringConfig(), lambda *a: "garbage", TODAY, progress=messages.append
    )
    assert any("failed" in m for m in messages)


def _scoring_runner(listings, score):
    """Runner that scores every listing it recognises in the prompt with `score`."""

    def runner(prompt, model, schema):
        ids = [lst.id for lst in listings if lst.id in prompt]
        payload = {
            "scores": [
                {
                    "id": i,
                    "score": score,
                    "role_type": "ra",
                    "area_tags": [],
                    "why": "w",
                    "concerns": "",
                }
                for i in ids
            ]
        }
        return _envelope(payload, structured=True)

    return runner


def test_rescore_overwrites_existing_scores(env):
    paths, store = env
    listings = [L(1), L(2)]
    store.upsert_listings(listings)
    store.save_scores([Score(listing_id=L(1).id, score=10)])
    cfg = ScoringConfig(batch_size=10)

    result = score_listings(store, paths, cfg, _scoring_runner(listings, 70), TODAY, rescore=True)
    assert result.scored == 2
    assert store.get_score(L(1).id).score == 70
    assert store.get_score(L(2).id).score == 70


def test_rescore_failed_batch_keeps_old_score(env):
    paths, store = env
    store.upsert_listings([L(1)])
    store.save_scores([Score(listing_id=L(1).id, score=10)])
    messages = []

    result = score_listings(
        store,
        paths,
        ScoringConfig(),
        lambda *a: "garbage",
        TODAY,
        rescore=True,
        progress=messages.append,
    )
    assert result.failed == 1
    assert store.get_score(L(1).id).score == 10
    assert any("scores left unchanged" in m for m in messages)
    assert not any("left unscored" in m for m in messages)


def test_rescore_skips_rated_listings(env):
    paths, store = env
    rated, other = L(1), L(2)
    store.upsert_listings([rated, other])
    store.save_rating(Rating(listing_id=rated.id, rating=5))
    prompts = []

    def runner(prompt, model, schema):
        prompts.append(prompt)
        return _scoring_runner([other], 50)(prompt, model, schema)

    score_listings(store, paths, ScoringConfig(), runner, TODAY, rescore=True)
    assert len(prompts) == 1
    _, _, batch = prompts[0].partition("# Listings to score")
    assert rated.id not in batch
    assert other.id in batch


def test_rescore_with_include_rated_does_not_show_a_listing_its_own_rating(env):
    paths, store = env
    rated, other = L(1, title="Rated job"), L(2, title="Other job")
    store.upsert_listings([rated, other])
    store.save_rating(Rating(listing_id=rated.id, rating=5, note="loved it"))
    store.save_rating(Rating(listing_id=other.id, rating=1, note="nope"))
    prompts = []

    def runner(prompt, model, schema):
        prompts.append(prompt)
        return _scoring_runner([rated, other], 50)(prompt, model, schema)

    score_listings(
        store, paths, ScoringConfig(batch_size=1), runner, TODAY, rescore=True, include_rated=True
    )
    assert len(prompts) == 2
    for prompt in prompts:
        examples, _, batch = prompt.partition("# Listings to score")
        if rated.id in batch:
            assert "[5/5] Rated job" not in examples
            assert "[1/5] Other job" in examples
        else:
            assert "[1/5] Other job" not in examples
            assert "[5/5] Rated job" in examples


def test_prompt_describes_the_person_only_via_the_profile():
    prompt = build_prompt("PROFILE", "PREFS", "CV", [], [L(1)])
    assert "the person described in the profile below" in prompt
    assert "Respect the career level and constraints stated in the profile" in prompt
    for personal in ("Master's student", "MSc", "cognitive neuroscience"):
        assert personal not in prompt


def test_score_listings_can_score_one_given_listing(env):
    paths, store = env
    one, other = L(1), L(2)
    store.upsert_listings([one, other])
    prompts = []

    def runner(prompt, model, schema):
        prompts.append(prompt)
        return _scoring_runner([one], 77)(prompt, model, schema)

    result = score_listings(store, paths, ScoringConfig(), runner, TODAY, listings=[one])
    assert result.scored == 1
    assert other.id not in prompts[0]
    assert store.get_score(one.id).score == 77
    assert store.get_score(other.id) is None


def _rated(n, rating, **kw):
    return (L(n, **kw), Rating(listing_id=L(n).id, rating=rating))


def test_format_rated_shows_the_score_claude_gave_that_listing():
    scores = {L(9).id: Score(listing_id=L(9).id, score=88)}
    line = format_rated([_rated(9, 2)], scores=scores)
    assert "[2/5]" in line
    assert "you scored 88" in line


def test_format_rated_leaves_out_a_score_for_a_listing_that_was_never_scored():
    assert "you scored" not in format_rated([_rated(9, 2)], scores={})


def test_build_prompt_tells_claude_what_it_scored_the_rated_examples():
    examples = [_rated(9, 1, title="Rated job")]
    scores = {L(9).id: Score(listing_id=L(9).id, score=95)}
    prompt = build_prompt("p", "q", "c", examples, [L(1)], scores=scores)
    assert "you scored 95" in prompt


def test_score_listings_tells_claude_what_it_scored_the_rated_examples(env):
    paths, store = env
    rated, other = L(1, title="Rated job"), L(2, title="Other job")
    store.upsert_listings([rated, other])
    store.save_rating(Rating(listing_id=rated.id, rating=1, note="nope"))
    store.save_scores([Score(listing_id=rated.id, score=92, model="m")])
    prompts = []

    def runner(prompt, model, schema):
        prompts.append(prompt)
        return _scoring_runner([other], 50)(prompt, model, schema)

    score_listings(store, paths, ScoringConfig(batch_size=5), runner, TODAY)
    assert "you scored 92" in prompts[0]


def test_prompt_explains_that_a_score_on_an_example_was_its_own():
    examples = [_rated(9, 1, title="Rated job")]
    scores = {L(9).id: Score(listing_id=L(9).id, score=95)}
    header, _, _ = build_prompt("p", "q", "c", examples, [L(1)], scores=scores).partition(
        "- [1/5] Rated job"
    )
    assert "you scored" in header.split("# Listings this person already rated")[1]


def test_score_listings_keeps_the_example_it_got_most_wrong_when_room_is_tight(env):
    paths, store = env
    agreed, missed, new = L(1, title="Agreed job"), L(2, title="Missed job"), L(3, title="New job")
    store.upsert_listings([agreed, missed, new])
    # `missed` is rated first, so recency alone would drop it — only surprise keeps it.
    store.save_rating(Rating(listing_id=missed.id, rating=1, rated_at=datetime(2026, 9, 1)))
    store.save_rating(Rating(listing_id=agreed.id, rating=5, rated_at=datetime(2026, 9, 2)))
    store.save_scores(
        [
            Score(listing_id=agreed.id, score=90, model="m"),
            Score(listing_id=missed.id, score=90, model="m"),
        ]
    )
    prompts = []

    def runner(prompt, model, schema):
        prompts.append(prompt)
        return _scoring_runner([new], 50)(prompt, model, schema)

    score_listings(store, paths, ScoringConfig(batch_size=5, examples=1), runner, TODAY)
    examples, _, _ = prompts[0].partition("# Listings to score")
    assert "Missed job" in examples
    assert "Agreed job" not in examples


def test_score_listings_puts_examples_it_never_scored_behind_the_ones_it_did(env):
    paths, store = env
    unscored, missed, new = L(1, title="Unscored job"), L(2, title="Missed job"), L(3)
    store.upsert_listings([unscored, missed, new])
    store.save_rating(Rating(listing_id=missed.id, rating=1, rated_at=datetime(2026, 9, 1)))
    store.save_rating(Rating(listing_id=unscored.id, rating=5, rated_at=datetime(2026, 9, 2)))
    store.save_scores([Score(listing_id=missed.id, score=90, model="m")])
    prompts = []

    def runner(prompt, model, schema):
        prompts.append(prompt)
        return _scoring_runner([new], 50)(prompt, model, schema)

    score_listings(store, paths, ScoringConfig(batch_size=5, examples=1), runner, TODAY)
    examples, _, _ = prompts[0].partition("# Listings to score")
    assert "Missed job" in examples
    assert "Unscored job" not in examples
