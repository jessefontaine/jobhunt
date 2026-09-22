"""Does Claude's 0-100 score track the ratings the user actually gives?"""

from __future__ import annotations

from datetime import date

import pytest

from jobhunt.calibration import agreement, render, spearman
from jobhunt.ratings import record_rating


def test_spearman_is_one_for_a_perfectly_ordered_pairing():
    assert spearman([(1, 10), (2, 20), (3, 30)]) == pytest.approx(1.0)


def test_spearman_is_minus_one_when_the_order_is_reversed():
    assert spearman([(1, 30), (2, 20), (3, 10)]) == pytest.approx(-1.0)


def test_spearman_averages_the_ranks_of_tied_values():
    # x ranks 1.5, 1.5, 3 against y ranks 1.5, 1.5, 3 — still a perfect match.
    assert spearman([(1, 5), (1, 5), (2, 9)]) == pytest.approx(1.0)


def test_spearman_is_none_when_one_side_never_varies():
    assert spearman([(70, 1), (70, 4), (70, 5)]) is None


def test_spearman_is_none_with_fewer_than_two_pairs():
    assert spearman([(80, 5)]) is None


def _band(result, label):
    return next(b for b in result.bands if b.label == label)


def test_agreement_counts_the_pairs_and_correlates_them():
    result = agreement([(90, 5), (80, 4), (50, 2), (20, 1)])
    assert result.n == 4
    assert result.rho == pytest.approx(1.0)


def test_agreement_groups_scores_into_the_bands_the_scoring_prompt_uses():
    result = agreement([(90, 5), (88, 4), (70, 2)])
    assert (_band(result, "apply").n, _band(result, "apply").mean_rating) == (2, 4.5)
    assert (_band(result, "strong").n, _band(result, "strong").mean_rating) == (1, 2.0)


def test_agreement_leaves_a_band_nobody_landed_in_without_a_mean():
    result = agreement([(90, 5)])
    assert _band(result, "irrelevant").n == 0
    assert _band(result, "irrelevant").mean_rating is None


def _many(pairs, times=4):
    """Repeat pairs so a case clears the too-few-ratings caveat."""
    return [(s + n, r) for n in range(times) for s, r in pairs]


def test_render_leads_with_the_pair_count_and_the_correlation():
    text = render(agreement([(90, 5), (80, 4), (50, 2), (20, 1)]))
    assert "4 rated listings" in text
    assert "1.00" in text


def test_render_warns_when_there_are_too_few_ratings_to_read_anything_into():
    assert "too few" in render(agreement([(90, 5), (20, 1)])).lower()


def test_render_drops_the_caveat_once_there_are_enough_ratings():
    assert "too few" not in render(agreement(_many([(90, 5), (20, 1)], times=6))).lower()


def test_render_shows_the_mean_rating_of_each_band_that_has_listings():
    text = render(agreement([(90, 5), (88, 4)]))
    assert "apply" in text
    assert "4.5" in text
    assert "irrelevant" not in text


def test_render_says_how_many_rated_listings_had_no_score():
    text = render(agreement([(90, 5)], unscored=3))
    assert "Left out: 3 rated listings with no score" in text


def test_render_calls_a_strong_correlation_strong():
    assert "strong" in render(agreement(_many([(90, 5), (70, 4), (30, 1)]))).lower()


def test_render_calls_out_scores_that_point_the_wrong_way():
    text = render(agreement([(90, 1), (70, 2), (50, 4), (20, 5)]))
    assert "wrong way" in text.lower()


def test_render_with_no_ratings_asks_for_some_instead_of_passing_a_verdict():
    text = render(agreement([]))
    assert "never vary" not in text
    assert "Rate some listings" in text


def test_render_explains_a_correlation_it_cannot_compute():
    text = render(agreement([(70, 1), (70, 5)]))
    assert "no correlation" in text.lower()


def _scored_and_rate(ws, values):
    """Score the fixture listings, then rate the scored ones `values` in score order."""
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ws.score()
    scored = sorted(
        ((ws.store.get_score(lst.id), lst) for lst in ws.store.unexpired_listings(date.today())),
        key=lambda pair: -pair[0].score,
    )
    for (_score, lst), value in zip(scored, values, strict=True):
        record_rating(ws.store, ws.paths.ratings, lst.id, value, "", "d.md")
    return [(score.score, value) for (score, _), value in zip(scored, values, strict=True)]


def test_calibration_pairs_each_rating_with_the_score_that_listing_got(ws):
    expected = _scored_and_rate(ws, [5, 2])

    result = ws.calibration()

    assert result.n == 2
    assert result.rho == spearman(expected)


def test_calibration_counts_rated_listings_that_were_never_scored(ws):
    _scored_and_rate(ws, [5, 2])
    unscored = ws.store.find_by_url("https://x.org/2")  # expired, so never scored
    record_rating(ws.store, ws.paths.ratings, unscored.id, 1, "", "d.md")

    result = ws.calibration()

    assert result.n == 2
    assert result.unscored == 1


def test_calibration_of_a_workspace_with_no_ratings_is_empty(ws):
    result = ws.calibration()

    assert (result.n, result.rho, result.unscored) == (0, None, 0)
