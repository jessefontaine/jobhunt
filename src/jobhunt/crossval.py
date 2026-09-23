"""Does a preference rewrite score better, or has it just memorised the ratings?

`learn` rewrites the rules from every rating, so measuring those rules against those same
ratings measures memorisation. This cross-validates instead: each fold learns rules from the
other folds and scores only its own held-out listings, against the current rules under
otherwise identical conditions. The fold rules are thrown away — they exist to estimate
whether learning generalises. What gets written, when the estimate says yes, is a final pass
over every rating.

Nothing here saves a score: the listings being rescored are rated ones that already carry
real scores, and a measurement must not overwrite them.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from jobhunt.calibration import (
    Agreement,
    agreement,
    effective_n,
    mean_surprise,
    rho_interval,
    spearman,
)
from jobhunt.config import Paths, ScoringConfig
from jobhunt.models import Listing, Rating, Score
from jobhunt.ratings import (
    Preferences,
    RuleSet,
    learn_rules,
    preferences_prompt,
    render_preferences,
    split_preferences,
    with_rules,
    write_rules,
)
from jobhunt.scoring import (
    Runner,
    ScoringContext,
    _chunks,
    batch_prompt,
    by_surprise,
    score_batch,
)
from jobhunt.settings import CalibrationSettings, Settings
from jobhunt.store import Store

Pair = tuple[Listing, Rating]
Progress = Callable[[str], None]
StopCheck = Callable[[], bool]

CV_META = "calibration_cv"  # store meta key: the last run's verdict
# Past this share of the evaluation set lost to failed batches, the run reports no verdict
# rather than deciding on a fraction of the sample it planned for.
MAX_DROPPED = 1 / 3
SECONDS_PER_CALL = (30, 60)  # what one `claude -p` call takes, low and high
CHARS_PER_TOKEN = 4


def _silent(msg: str) -> None:
    pass


def _never() -> bool:
    return False


class NotEnoughRatings(Exception):
    """Too few ratings for a difference between two correlations to mean anything."""


class TooExpensive(Exception):
    """The planned run is over the call ceiling the user set."""


class LearnFailed(Exception):
    """A preferences call failed twice. A partial run is a different experiment, not a weaker
    one, so the run stops and writes nothing."""


# -- splitting --------------------------------------------------------------


def deal(rated: list[Pair], folds: int) -> list[list[Pair]]:
    """Deal the ratings round-robin into `folds`, sorted by rating first.

    Stratified by construction — the rare 4s and 5s spread evenly instead of landing in one
    fold — and deterministic, so a re-run on unchanged data splits the same way.
    """
    ordered = sorted(rated, key=lambda p: (p[1].rating, p[0].id))
    out: list[list[Pair]] = [[] for _ in range(folds)]
    for n, pair in enumerate(ordered):
        out[n % folds].append(pair)
    return out


def sample(fold: list[Pair], cap: int) -> list[Pair]:
    """At most `cap` of the fold, taken at an even stride across its whole rating order.

    The stride spans both ends. Real ratings are heavily skewed towards 1, so a stride that
    stopped short of the last index would drop the rare 4s and 5s every time and quietly
    measure only how well the scorer rejects things.
    """
    if len(fold) <= cap:
        return list(fold)
    ordered = sorted(fold, key=lambda p: (p[1].rating, p[0].id))
    if cap == 1:
        return [ordered[-1]]  # one slot: spend it on the highest rating, not the commonest
    step = (len(ordered) - 1) / (cap - 1)
    return [ordered[round(n * step)] for n in range(cap)]


# -- planning ---------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What a run will cost, worked out before anything is sent to Claude."""

    n_ratings: int
    n_eval: int  # listings scored per arm
    folds: int
    learn_calls: int
    score_calls: int
    input_tokens: int
    warnings: list[str] = field(default_factory=list)

    @property
    def total_calls(self) -> int:
        """The worst case: every fold, both arms, and the final retrain on all ratings."""
        return self.learn_calls + self.score_calls

    @property
    def minutes(self) -> tuple[int, int]:
        low, high = SECONDS_PER_CALL
        return (self.total_calls * low // 60, self.total_calls * high // 60)


def _read_prefs(paths: Paths) -> Preferences:
    text = paths.preferences.read_text() if paths.preferences.exists() else "# Preferences\n"
    return split_preferences(text)


def _files(paths: Paths) -> tuple[str, str]:
    return (
        paths.profile.read_text() if paths.profile.exists() else "",
        paths.cv.read_text() if paths.cv.exists() else "",
    )


def _example_pool(
    training: list[Pair], scores: dict[str, Score], cfg: ScoringConfig
) -> list[Pair]:
    """The few-shot examples a fold may use: strong signals from its training ratings only.

    Mirrors `store.rated_examples` (rating >= 4 or <= 2, most recent first) but reads from the
    fold instead of the store, so a held-out listing can never become its own example.
    """
    strong = [pair for pair in training if pair[1].rating >= 4 or pair[1].rating <= 2]
    strong.sort(key=lambda p: p[1].rated_at, reverse=True)
    return by_surprise(strong[: cfg.examples + cfg.batch_size], scores)


def _folds_and_evals(
    rated: list[Pair], cfg: CalibrationSettings
) -> tuple[list[list[Pair]], list[list[Pair]], int]:
    folds = deal(rated, cfg.folds)
    per_fold = max(1, cfg.eval_cap // cfg.folds)
    return folds, [sample(f, per_fold) for f in folds], per_fold


def _estimate_tokens(
    paths: Paths,
    store: Store,
    settings: Settings,
    folds: list[list[Pair]],
    evals: list[list[Pair]],
    learn_calls: int,
    score_calls: int,
) -> int:
    """Measured, not guessed: both prompt builders are pure, so fold 0's real prompts price
    the run without sending anything."""
    rated = store.all_ratings()
    scores = store.get_scores([lst.id for lst, _ in rated])
    prefs = _read_prefs(paths)
    profile, cv = _files(paths)
    training = [pair for n, fold in enumerate(folds) if n != 0 for pair in fold]
    learn_chars = len(preferences_prompt(prefs, training, scores, settings.preferences))
    batch = next((e for e in evals if e), [])[: settings.scoring.batch_size]
    context = ScoringContext(
        profile=profile,
        preferences=render_preferences(prefs),
        cv=cv,
        examples=_example_pool(training, scores, settings.scoring),
        example_scores=scores,
    )
    score_chars = len(batch_prompt([lst for lst, _ in batch], context, settings.scoring))
    return (learn_chars * learn_calls + score_chars * score_calls) // CHARS_PER_TOKEN


def plan(paths: Paths, store: Store, settings: Settings) -> Plan:
    """Price a run. Makes no Claude call; raises rather than starting one it cannot afford."""
    cfg = settings.calibration
    rated = store.all_ratings()
    if len(rated) < cfg.min_ratings:
        raise NotEnoughRatings(
            f"{len(rated)} ratings — cross-validation needs at least {cfg.min_ratings} before a "
            "difference between two correlations means anything. Rate more listings first."
        )
    folds, evals, per_fold = _folds_and_evals(rated, cfg)
    batch_size = settings.scoring.batch_size
    score_calls = 2 * sum(math.ceil(len(e) / batch_size) for e in evals if e)
    learn_calls = cfg.folds + 1
    total = learn_calls + score_calls
    if total > cfg.max_calls:
        raise TooExpensive(
            f"{total} claude calls is over the {cfg.max_calls}-call ceiling "
            "(calibration.max_calls). Lower calibration.eval_cap, or raise the ceiling."
        )
    warnings = []
    if per_fold % batch_size:
        warnings.append(
            f"{per_fold} listings per fold does not fill a batch of {batch_size}, so each fold "
            f"pays full-batch latency for a part-full batch. Pick folds so eval_cap // folds is "
            f"a multiple of {batch_size}."
        )
    return Plan(
        n_ratings=len(rated),
        n_eval=sum(len(e) for e in evals),
        folds=cfg.folds,
        learn_calls=learn_calls,
        score_calls=score_calls,
        input_tokens=_estimate_tokens(
            paths, store, settings, folds, evals, learn_calls, score_calls
        ),
        warnings=warnings,
    )


# -- the verdict ------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    """One set of rules, measured on every held-out listing."""

    label: str
    agreement: Agreement
    mean_surprise: float


@dataclass(frozen=True)
class Uncertainty:
    """How much of the measured change could be the sample rather than the rules."""

    n: int  # held-out listings
    effective_n: int  # what they carry once tied ratings are discounted
    low: float  # 10th percentile of the change across resamples
    high: float  # 90th
    positive: float  # share of resamples the candidate won


BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 0  # fixed, so the same measurements always report the same interval


def bootstrap_delta(
    paired: list[tuple[int, int, int]],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Uncertainty:
    """Resample the held-out listings to see how much the change depends on which ones landed.

    Costs nothing — it reuses the scores already measured. It answers only "would another
    draw of listings have said the same?", not "would another Claude run have?", which is a
    separate noise floor this does not touch.
    """
    ratings = [r for _, _, r in paired]
    base = Uncertainty(len(paired), effective_n(ratings), 0.0, 0.0, 0.0)
    if len(paired) < 2:
        return base
    rng = random.Random(seed)
    deltas = []
    for _ in range(resamples):
        draw = [paired[rng.randrange(len(paired))] for _ in range(len(paired))]
        before = spearman([(c, r) for c, _, r in draw])
        after = spearman([(k, r) for _, k, r in draw])
        if before is not None and after is not None:
            deltas.append(after - before)
    if not deltas:
        return base
    deltas.sort()
    return Uncertainty(
        n=len(paired),
        effective_n=effective_n(ratings),
        low=deltas[int(0.10 * len(deltas))],
        high=deltas[min(int(0.90 * len(deltas)), len(deltas) - 1)],
        positive=sum(1 for d in deltas if d > 0) / len(deltas),
    )


@dataclass(frozen=True)
class Result:
    plan: Plan
    current: Arm
    candidate: Arm
    fold_rho: list[tuple[float | None, float | None]]  # per fold, (current, candidate)
    paired: list[tuple[int, int, int]]  # per listing: (current score, candidate score, rating)
    uncertainty: Uncertainty
    delta_rho: float | None  # candidate − current; positive is better (ranking)
    delta_surprise: float  # candidate − current; negative is better (bands)
    dropped: int  # eval listings lost to a failed batch, removed from both arms
    accepted: bool
    reason: str
    written: bool
    ran_at: datetime
    cancelled: bool = False


def gate(
    delta_rho: float | None, delta_surprise: float, cfg: CalibrationSettings
) -> tuple[bool, str]:
    """Accept only a gain big enough to read, and only if the bands held.

    Two numbers because rank correlation is blind to a systematic shift: rules that add twenty
    points to every score leave rho untouched while flooding the shortlist and breaking the
    bands the scoring prompt promises.
    """
    if delta_rho is None:
        return False, "no verdict: a rank correlation was undefined — the scores never varied."
    if delta_rho < cfg.min_rho_gain:
        return False, (
            f"rejected: {delta_rho:+.2f} rank correlation is under the "
            f"{cfg.min_rho_gain:+.2f} margin, which is inside the noise of a rescore."
        )
    if delta_surprise > cfg.max_surprise_loss:
        return False, (
            f"rejected: the ranking improved by {delta_rho:+.2f} but the bands drifted "
            f"{delta_surprise:+.2f}, past the {cfg.max_surprise_loss:.2f} allowed — the scores "
            "would no longer mean what the bands say."
        )
    return True, (
        f"accepted: {delta_rho:+.2f} rank correlation out of sample, bands "
        f"{delta_surprise:+.2f}."
    )


# -- the run ----------------------------------------------------------------


def _learn_with_retry(
    prefs: Preferences,
    rated: list[Pair],
    scores: dict[str, Score],
    runner: Runner,
    settings: Settings,
    progress: Progress,
) -> RuleSet | None:
    for attempt in range(2):
        if attempt:
            progress("  preferences call failed, retrying…")
        ruleset = learn_rules(
            prefs, rated, scores, runner, settings.scoring.model, settings.preferences
        )
        if ruleset is not None:
            return ruleset
    return None


def _score_with_retry(
    batch: list[Listing], context: ScoringContext, runner: Runner, cfg: ScoringConfig
) -> list[Score] | None:
    for _ in range(2):
        try:
            return score_batch(batch, context, runner, cfg)
        except (ValueError, RuntimeError):
            continue
    return None


def _before(paired: list[tuple[int, int, int]]) -> float | None:
    return spearman([(c, r) for c, _, r in paired])


def _after(paired: list[tuple[int, int, int]]) -> float | None:
    return spearman([(k, r) for _, k, r in paired])


def _arm(label: str, pairs: list[tuple[int, int]]) -> Arm:
    return Arm(label=label, agreement=agreement(pairs), mean_surprise=mean_surprise(pairs))


def cross_validate(
    paths: Paths,
    store: Store,
    runner: Runner,
    settings: Settings,
    progress: Progress = _silent,
    should_stop: StopCheck = _never,
    force: bool = False,
) -> Result:
    """k-fold cross-validation of a preferences rewrite, writing only if it earns it.

    `should_stop` is checked between Claude calls but never between the two arms of one batch,
    so a stopped run still has paired measurements for every batch it finished.
    """
    cfg = settings.calibration
    scoring = settings.scoring
    priced = plan(paths, store, settings)
    rated = store.all_ratings()
    scores = store.get_scores([lst.id for lst, _ in rated])
    prefs = _read_prefs(paths)
    profile, cv = _files(paths)
    current_text = render_preferences(prefs)
    folds, evals, _ = _folds_and_evals(rated, cfg)

    paired: list[tuple[int, int, int]] = []  # (current score, candidate score, rating)
    fold_rho: list[tuple[float | None, float | None]] = []
    dropped = 0
    cancelled = False

    for n, held_out in enumerate(evals, 1):
        if should_stop():
            cancelled = True
            break
        if not held_out:
            continue
        training = [pair for m, f in enumerate(folds, 1) if m != n for pair in f]
        progress(f"fold {n}/{cfg.folds}: learning from {len(training)} ratings…")
        ruleset = _learn_with_retry(prefs, training, scores, runner, settings, progress)
        if ruleset is None:
            raise LearnFailed(
                f"fold {n}: the preferences call failed twice. Nothing was written — a partial "
                "cross-validation is a different experiment, not a weaker one."
            )
        pool = _example_pool(training, scores, scoring)
        contexts = {
            "current": ScoringContext(profile, current_text, cv, pool, scores),
            "candidate": ScoringContext(
                profile, render_preferences(with_rules(prefs, ruleset)), cv, pool, scores
            ),
        }
        wanted = {lst.id: r.rating for lst, r in held_out}
        fold_paired: list[tuple[int, int, int]] = []
        progress(f"  scoring {len(held_out)} held-out listing(s) with both rule sets…")
        for batch in _chunks([lst for lst, _ in held_out], scoring.batch_size):
            if should_stop():
                cancelled = True
                break
            got = _score_with_retry(batch, contexts["current"], runner, scoring)
            other = (
                _score_with_retry(batch, contexts["candidate"], runner, scoring)
                if got is not None
                else None
            )
            if got is None or other is None:
                dropped += len(batch)
                progress(f"  dropped {len(batch)} listing(s): a batch failed in one arm")
                continue
            before = {s.listing_id: s.score for s in got}
            after = {s.listing_id: s.score for s in other}
            # only listings both arms returned: one measurement per listing, or no comparison
            fold_paired += [
                (before[lid], after[lid], rating)
                for lid, rating in wanted.items()
                if lid in before and lid in after
            ]
        paired += fold_paired
        fold_rho.append((_before(fold_paired), _after(fold_paired)))
        if cancelled:
            break

    current = _arm("current", [(c, r) for c, _, r in paired])
    candidate = _arm("candidate", [(k, r) for _, k, r in paired])
    uncertainty = bootstrap_delta(paired)
    delta_rho = (
        candidate.agreement.rho - current.agreement.rho
        if current.agreement.rho is not None and candidate.agreement.rho is not None
        else None
    )
    delta_surprise = candidate.mean_surprise - current.mean_surprise

    if cancelled:
        accepted, reason = False, "stopped before the run finished — nothing was written."
    elif dropped > priced.n_eval * MAX_DROPPED:
        accepted, reason = False, (
            f"no verdict: {dropped} of {priced.n_eval} listings were dropped to failed batches, "
            "too few left to decide on. Nothing was written."
        )
    else:
        accepted, reason = gate(delta_rho, delta_surprise, cfg)

    written = False
    if (accepted or force) and not cancelled:
        progress("retraining on every rating…")
        final = _learn_with_retry(prefs, rated, scores, runner, settings, progress)
        if final is None:
            raise LearnFailed("the final preferences call failed twice; nothing was written.")
        write_rules(paths, store, prefs, final)
        written = True
        progress("preferences: updated")
    else:
        progress("preferences: left unchanged")

    result = Result(
        plan=priced,
        current=current,
        candidate=candidate,
        fold_rho=fold_rho,
        paired=paired,
        uncertainty=uncertainty,
        delta_rho=delta_rho,
        delta_surprise=delta_surprise,
        dropped=dropped,
        accepted=accepted,
        reason=reason,
        written=written,
        ran_at=datetime.now(),
        cancelled=cancelled,
    )
    store.set_meta(CV_META, json.dumps(_summary(result)))
    return result


# -- what the report and the page read --------------------------------------


def _summary(result: Result) -> dict:
    return {
        "ran_at": result.ran_at.isoformat(timespec="seconds"),
        "n": result.current.agreement.n,
        "current_rho": result.current.agreement.rho,
        "candidate_rho": result.candidate.agreement.rho,
        "delta_rho": result.delta_rho,
        "current_surprise": round(result.current.mean_surprise, 2),
        "candidate_surprise": round(result.candidate.mean_surprise, 2),
        "delta_surprise": round(result.delta_surprise, 2),
        "effective_n": result.uncertainty.effective_n,
        "delta_low": round(result.uncertainty.low, 2),
        "delta_high": round(result.uncertainty.high, 2),
        "positive": round(result.uncertainty.positive, 2),
        "dropped": result.dropped,
        "accepted": result.accepted,
        "written": result.written,
        "cancelled": result.cancelled,
        "reason": result.reason,
    }


# Fields a verdict from an older engine cannot carry. None, not zero: the run did not measure
# these, and a page that printed "0% of resamples" would be stating a result nobody computed.
_UNMEASURED = {"effective_n": None, "delta_low": None, "delta_high": None, "positive": None}


def last_run(store: Store) -> dict | None:
    """The last run's verdict, for the Calibration page and the `calibration` report."""
    raw = store.get_meta(CV_META)
    return {**_UNMEASURED, **json.loads(raw)} if raw else None


def _rho(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_last(last: dict | None) -> str:
    """One line on the last run, for the calibration report and the dashboard."""
    if not last:
        return ""
    if last["cancelled"]:
        verdict = "stopped"
    else:
        verdict = "accepted" if last["accepted"] else "rejected"
    return (
        f"Last cross-validation: {last['ran_at'][:10]} — rank correlation "
        f"{_rho(last['current_rho'])} → {_rho(last['candidate_rho'])} on {last['n']} held-out "
        f"listing(s), {verdict}."
    )


def render_plan(priced: Plan) -> str:
    """What the run will cost, for the confirmation prompt and the page."""
    low, high = priced.minutes
    lines = [
        f"Cross-validating {priced.n_ratings} ratings on {priced.n_eval} held-out listings, "
        f"{priced.folds} folds.",
        f"  {priced.total_calls} claude calls ({priced.learn_calls} learn, "
        f"{priced.score_calls} scoring), ~{priced.input_tokens // 1000}k input tokens, "
        f"roughly {low}-{high} minutes.",
    ]
    lines += [f"  ! {w}" for w in priced.warnings]
    return "\n".join(lines)


def _span(interval: tuple[float, float] | None) -> str:
    return "n/a" if interval is None else f"{interval[0]:+.2f} to {interval[1]:+.2f}"


def render(result: Result) -> str:
    """The plain-text report `jobhunt learn --cross-validate` prints."""
    unsure = result.uncertainty
    lines = [
        f"Cross-validation on {result.current.agreement.n} held-out listings "
        f"({result.plan.folds} folds)",
        "",
        f"  {'':<10} {'rank corr':>10}  {'95% interval':<18} {'band drift':>10}",
        f"  {'current':<10} {_rho(result.current.agreement.rho):>10}  "
        f"{_span(rho_interval(result.current.agreement.rho, unsure.effective_n)):<18} "
        f"{result.current.mean_surprise:>10.2f}",
        f"  {'candidate':<10} {_rho(result.candidate.agreement.rho):>10}  "
        f"{_span(rho_interval(result.candidate.agreement.rho, unsure.effective_n)):<18} "
        f"{result.candidate.mean_surprise:>10.2f}",
        f"  {'change':<10} {_rho(result.delta_rho):>10}  "
        f"{_span((unsure.low, unsure.high)):<18} {result.delta_surprise:>+10.2f}",
        "",
    ]
    if unsure.n:
        lines += [
            f"  Ratings tie, so {unsure.n} listings carry the information of about "
            f"{unsure.effective_n} (effective) — hence the width above.",
            f"  The candidate came out ahead in {unsure.positive:.0%} of resamples. That covers "
            "which listings",
            "  landed here, not how much a second Claude run would have moved the scores.",
            "",
        ]
    lines.append(result.reason)
    if result.dropped:
        lines.append(f"{result.dropped} listing(s) dropped to failed batches, in both arms.")
    if any(pair != (None, None) for pair in result.fold_rho):
        lines += ["", "Per fold (current → candidate), a sanity check, not the verdict:"]
        lines += [
            f"  fold {n}: {_rho(a)} → {_rho(b)}"
            for n, (a, b) in enumerate(result.fold_rho, 1)
        ]
    return "\n".join(lines)
