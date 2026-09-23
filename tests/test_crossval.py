"""Cross-validating a preference rewrite against the ratings it was not allowed to see."""

from __future__ import annotations

import json

import pytest

from jobhunt.config import Paths, ScoringConfig
from jobhunt.crossval import (
    CV_META,
    LearnFailed,
    NotEnoughRatings,
    TooExpensive,
    bootstrap_delta,
    cross_validate,
    deal,
    gate,
    last_run,
    plan,
    render,
    render_last,
    render_plan,
    sample,
)
from jobhunt.models import Listing, Rating, Score
from jobhunt.settings import CalibrationSettings, Settings
from jobhunt.store import Store
from jobhunt.workspace import Workspace

PREFS = """# Preferences

## Manual

- I hate wet lab

## Learned

- old rule
"""


def L(n):
    return Listing(
        source="s",
        title=f"Job {n}",
        employer=f"Uni {n}",
        url=f"https://x.org/j/{n}",
        description=f"Description of job {n}. " * 5,
        summary=f"Summary {n}",
    )


def _rated(n=25):
    """n listings rated 1,2,3,4,5,1,2,… so every fold can hold every rating value."""
    return [(L(i), (i % 5) + 1) for i in range(n)]


@pytest.fixture
def env(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "docs").mkdir()
    paths = Paths(tmp_path)
    paths.profile.write_text("PROFILE")
    paths.preferences.write_text(PREFS)
    paths.cv.write_text("CV")
    store = Store(paths.db)
    pairs = _rated()
    store.upsert_listings([lst for lst, _ in pairs])
    for lst, value in pairs:
        store.save_rating(Rating(listing_id=lst.id, rating=value))
        store.save_scores([Score(listing_id=lst.id, score=50, model="m")])
    return paths, store


def _settings(**kw):
    return Settings(
        scoring=ScoringConfig(model="haiku", batch_size=10, examples=5),
        calibration=CalibrationSettings(**kw),
    )


CANDIDATE = "CANDIDATE-RULE"


def _runner(ratings, better=True, calls=None):
    """A scorer whose answer depends on which rules the prompt carries.

    With the candidate rules it scores `rating * 20` — a perfect ranking. With the current
    rules it scores `60 - rating * 2` — exactly backwards. `better=False` makes both arms
    behave the same, so the candidate earns nothing.
    """
    by_id = {lst.id: value for lst, value in ratings}

    def runner(prompt, model, schema):
        if calls is not None:
            calls.append(("learn" if "rules" in json.dumps(schema) else "score", prompt))
        if "rules" in json.dumps(schema):
            return json.dumps(
                {"type": "result", "structured_output": {"rules": [CANDIDATE], "specifics": []}}
            )
        _, _, batch = prompt.partition("# Listings to score")
        ids = [i for i in by_id if i in batch]
        candidate = CANDIDATE in prompt.split("# Listings to score")[0]
        scores = [
            {
                "id": i,
                "score": by_id[i] * 20 if (candidate and better) else 60 - by_id[i] * 2,
                "role_type": "phd",
                "area_tags": [],
                "why": "w",
                "concerns": "",
            }
            for i in ids
        ]
        return json.dumps({"type": "result", "structured_output": {"scores": scores}})

    return runner


# -- splitting --------------------------------------------------------------


def test_deal_gives_every_fold_the_same_spread_of_ratings():
    folds = deal([(L(i), Rating(listing_id=L(i).id, rating=(i % 5) + 1)) for i in range(25)], 5)
    assert len(folds) == 5
    for fold in folds:
        assert sorted(r.rating for _, r in fold) == [1, 2, 3, 4, 5]


def test_deal_is_deterministic():
    pairs = [(L(i), Rating(listing_id=L(i).id, rating=(i % 5) + 1)) for i in range(25)]
    assert [[lst.id for lst, _ in f] for f in deal(pairs, 5)] == [
        [lst.id for lst, _ in f] for f in deal(list(reversed(pairs)), 5)
    ]


def test_sample_caps_the_fold_and_keeps_the_ratings_spread():
    fold = [(L(i), Rating(listing_id=L(i).id, rating=(i % 5) + 1)) for i in range(10)]
    taken = sample(fold, 5)
    assert len(taken) == 5
    assert sorted(r.rating for _, r in taken) == [1, 2, 3, 4, 5]


def test_sample_returns_the_whole_fold_when_the_cap_is_bigger():
    fold = [(L(1), Rating(listing_id=L(1).id, rating=3))]
    assert len(sample(fold, 10)) == 1


# -- planning ---------------------------------------------------------------


def test_plan_prices_the_run_without_calling_claude(env):
    paths, store = env
    p = plan(paths, store, _settings())
    assert p.n_ratings == 25 and p.folds == 5
    assert p.learn_calls == 6  # one per fold, plus the final retrain
    assert p.score_calls == 10  # 5 folds x 2 arms x 1 batch
    assert p.total_calls == 16
    assert p.input_tokens > 0
    assert p.minutes == (8, 16)


def test_plan_counts_exactly_the_calls_an_accepted_run_makes(env):
    paths, store = env
    calls = []
    p = plan(paths, store, _settings())
    result = cross_validate(
        paths, store, _runner(_rated(), calls=calls), _settings()
    )
    assert result.accepted
    assert len(calls) == p.total_calls


def test_plan_refuses_below_the_rating_floor(env):
    paths, store = env
    with pytest.raises(NotEnoughRatings, match="25"):
        plan(paths, store, _settings(min_ratings=50))


def test_plan_refuses_a_run_over_the_call_ceiling(env):
    paths, store = env
    with pytest.raises(TooExpensive, match="max_calls"):
        plan(paths, store, _settings(max_calls=8))


def test_plan_warns_about_half_empty_batches(env):
    paths, store = env
    assert any("batch" in w for w in plan(paths, store, _settings(folds=10)).warnings)


# -- the gate ---------------------------------------------------------------


def test_gate_accepts_a_real_gain():
    accepted, why = gate(0.3, -0.2, CalibrationSettings())
    assert accepted and "0.30" in why


def test_gate_rejects_a_gain_under_the_margin():
    accepted, why = gate(0.01, -0.2, CalibrationSettings())
    assert not accepted and "margin" in why


def test_gate_rejects_when_the_bands_drift_even_though_the_ranking_improves():
    accepted, why = gate(0.5, 1.0, CalibrationSettings())
    assert not accepted and "band" in why


def test_gate_rejects_when_a_correlation_is_undefined():
    accepted, why = gate(None, 0.0, CalibrationSettings())
    assert not accepted


# -- the run ----------------------------------------------------------------


def test_a_better_candidate_is_measured_and_written(env):
    paths, store = env
    result = cross_validate(paths, store, _runner(_rated()), _settings())
    assert result.accepted
    assert result.current.agreement.rho == pytest.approx(-1.0)
    assert result.candidate.agreement.rho == pytest.approx(1.0)
    assert result.delta_rho == pytest.approx(2.0)
    assert CANDIDATE in paths.preferences.read_text()
    assert "I hate wet lab" in paths.preferences.read_text()


def test_a_candidate_that_earns_nothing_leaves_the_file_alone(env):
    paths, store = env
    before = paths.preferences.read_text()
    result = cross_validate(paths, store, _runner(_rated(), better=False), _settings())
    assert not result.accepted
    assert paths.preferences.read_text() == before


def test_force_writes_despite_a_rejecting_gate(env):
    paths, store = env
    result = cross_validate(
        paths, store, _runner(_rated(), better=False), _settings(), force=True
    )
    assert not result.accepted
    assert CANDIDATE in paths.preferences.read_text()


def test_a_run_never_touches_the_stored_scores(env):
    paths, store = env
    before = {lst.id: store.get_score(lst.id).score for lst, _ in store.all_ratings()}
    cross_validate(paths, store, _runner(_rated()), _settings())
    after = {lst.id: store.get_score(lst.id).score for lst, _ in store.all_ratings()}
    assert before == after


def test_no_listing_is_ever_scored_by_rules_or_examples_that_saw_its_rating(env):
    paths, store = env
    calls = []
    cross_validate(paths, store, _runner(_rated(), calls=calls), _settings())

    titles = {L(i).id: L(i).title for i in range(25)}
    learned_from = None
    for kind, prompt in calls:
        if kind == "learn":
            learned_from = prompt
            continue
        examples, _, batch = prompt.partition("# Listings to score")
        held_out = [i for i in titles if i in batch]
        assert held_out
        for listing_id in held_out:
            # `] Job 2 —` is how a rated example is written, and does not match `Job 20`
            line = f"] {titles[listing_id]} —"
            assert listing_id not in examples, "a held-out listing appeared as its own example"
            assert line not in examples, "a held-out listing appeared as its own example"
            assert line not in learned_from, "a held-out rating trained the rules scoring it"


def test_stopping_between_calls_writes_nothing(env):
    paths, store = env
    before = paths.preferences.read_text()
    calls = []
    result = cross_validate(
        paths,
        store,
        _runner(_rated(), calls=calls),
        _settings(),
        should_stop=lambda: len(calls) >= 3,
    )
    assert result.cancelled and not result.accepted
    assert paths.preferences.read_text() == before
    assert len(calls) < 16


def test_a_batch_that_fails_twice_drops_those_listings_from_both_arms(env):
    paths, store = env
    good = _runner(_rated())

    def runner(prompt, model, schema):
        batch = prompt.partition("# Listings to score")[2]
        if "rules" not in json.dumps(schema) and L(0).id in batch:
            return "garbage"
        return good(prompt, model, schema)

    result = cross_validate(paths, store, runner, _settings())
    assert result.dropped > 0
    assert result.current.agreement.n == result.candidate.agreement.n


def test_losing_most_of_the_evaluation_set_reaches_no_verdict(env):
    paths, store = env
    good = _runner(_rated())

    def runner(prompt, model, schema):
        if "rules" not in json.dumps(schema):
            return "garbage"
        return good(prompt, model, schema)

    result = cross_validate(paths, store, runner, _settings())
    assert not result.accepted and "dropped" in result.reason


def test_a_preferences_call_that_fails_twice_stops_the_run(env):
    paths, store = env
    before = paths.preferences.read_text()
    with pytest.raises(LearnFailed, match="failed twice"):
        cross_validate(paths, store, lambda *a: "garbage", _settings())
    assert paths.preferences.read_text() == before


def test_the_verdict_is_kept_for_the_next_page_load(env):
    paths, store = env
    result = cross_validate(paths, store, _runner(_rated()), _settings())
    saved = last_run(store)
    assert saved["accepted"] is True and saved["written"] is True
    assert saved["delta_rho"] == pytest.approx(result.delta_rho)


def test_render_shows_both_arms_and_the_verdict(env):
    paths, store = env
    text = render(cross_validate(paths, store, _runner(_rated()), _settings()))
    indented = [line.split() for line in text.splitlines() if line.startswith("  ")]
    rows = {parts[0]: parts[1:] for parts in indented if parts}
    assert rows["current"][0] == "-1.00"
    assert rows["candidate"][0] == "1.00"
    assert rows["change"][0] == "2.00"
    assert "accepted" in text
    assert "fold 1: -1.00 → 1.00" in text


def test_render_plan_prices_the_run_before_it_starts(env):
    paths, store = env
    text = render_plan(plan(paths, store, _settings()))
    assert "16 claude calls" in text and "minutes" in text
    assert "25 ratings" in text


def test_render_last_summarises_the_stored_verdict(env):
    paths, store = env
    cross_validate(paths, store, _runner(_rated()), _settings())
    line = render_last(last_run(store))
    assert "-1.00 → 1.00" in line
    assert "accepted" in line


def test_render_last_is_empty_without_a_run():
    assert render_last(None) == ""


@pytest.fixture
def cv_ws(env):
    paths, _ = env
    (paths.root / "config").mkdir()
    (paths.root / "config" / "sources.yaml").write_text("sources: {}\n")
    return Workspace.open(paths.root, runner=_runner(_rated()))


def test_workspace_prices_a_cross_validation(cv_ws):
    assert cv_ws.plan_cross_validation().total_calls == 16


def test_workspace_runs_a_cross_validation_and_writes_the_rules(cv_ws):
    lines = []
    result = cv_ws.cross_validate(progress=lines.append)
    assert result.accepted and result.written
    assert CANDIDATE in cv_ws.paths.preferences.read_text()
    assert any("fold 1/5" in m for m in lines)


def test_sample_keeps_the_rare_high_rating_in_a_skewed_fold():
    # A real fold: mostly 1s, one 5. The listing the whole pipeline exists to find must be
    # scored, not dropped by the cap — an even stride that never reaches the last index
    # silently evaluates only the rejects.
    ratings = [1] * 21 + [2] * 5 + [3] + [5]
    fold = [(L(i), Rating(listing_id=L(i).id, rating=r)) for i, r in enumerate(ratings)]
    taken = sample(fold, 10)
    assert len(taken) == 10
    assert max(r.rating for _, r in taken) == 5
    assert min(r.rating for _, r in taken) == 1


# -- how much of the change could be the sample? -----------------------------


def _paired(current, candidate, ratings):
    return [(current(r), candidate(r), r) for r in ratings]


RATINGS = [1, 2, 3, 4, 5] * 10


def test_bootstrap_is_confident_when_the_candidate_always_wins():
    u = bootstrap_delta(_paired(lambda r: 60 - r * 2, lambda r: r * 20, RATINGS))
    assert u.positive > 0.95
    assert u.low > 0


def test_bootstrap_is_unconvinced_when_both_arms_score_the_same():
    u = bootstrap_delta(_paired(lambda r: r * 20, lambda r: r * 20, RATINGS))
    assert u.positive == 0.0
    assert (u.low, u.high) == (0.0, 0.0)


def test_bootstrap_is_deterministic():
    paired = _paired(lambda r: 60 - r * 2, lambda r: r * 20, RATINGS)
    assert bootstrap_delta(paired) == bootstrap_delta(paired)


def test_bootstrap_discounts_tied_ratings_in_its_effective_sample():
    ratings = [1] * 35 + [2] * 9 + [3] + [4] * 4 + [5]
    u = bootstrap_delta(_paired(lambda r: r * 10, lambda r: r * 20, ratings))
    assert u.n == 50
    assert u.effective_n == 35


def test_the_run_reports_how_much_of_the_change_could_be_the_sample(env):
    paths, store = env
    result = cross_validate(paths, store, _runner(_rated()), _settings())
    assert len(result.paired) == 25
    assert result.uncertainty.n == 25
    assert result.uncertainty.positive == 1.0  # backwards vs perfect: never a close call


def test_render_puts_an_interval_next_to_the_change(env):
    paths, store = env
    text = render(cross_validate(paths, store, _runner(_rated()), _settings()))
    assert "of resamples" in text
    assert "effective" in text


def test_gate_rejects_the_gain_that_used_to_squeak_through(env):
    # 0.077 is what a real run produced and wrote on the old 0.05 margin; at that size the
    # sample alone can move the number, so it now reports instead of writing.
    accepted, why = gate(0.077, 0.0, CalibrationSettings())
    assert not accepted and "margin" in why


def test_last_run_tolerates_a_verdict_stored_before_the_intervals_existed(env):
    _, store = env
    store.set_meta(
        CV_META,
        json.dumps(
            {
                "ran_at": "2026-09-23T18:35:36",
                "n": 50,
                "current_rho": 0.63,
                "candidate_rho": 0.71,
                "delta_rho": 0.08,
                "current_surprise": 0.26,
                "candidate_surprise": 0.26,
                "delta_surprise": 0.0,
                "dropped": 0,
                "accepted": True,
                "written": True,
                "cancelled": False,
                "reason": "accepted: +0.08 rank correlation out of sample, bands +0.00.",
            }
        ),
    )
    last = last_run(store)
    assert last["delta_rho"] == 0.08  # what it did record still reads
    assert last["effective_n"] is None  # what it could not know says so
    assert last["delta_low"] is None and last["positive"] is None
