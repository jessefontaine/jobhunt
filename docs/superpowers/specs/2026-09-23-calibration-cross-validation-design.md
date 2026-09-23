# Cross-validated preference learning — design spec

## Context

`jobhunt learn` rewrites `## Learned` and `## Specifics` in `profile/preferences.md` from every
rating, and `jobhunt calibration` reports how Claude's scores line up with those ratings
(Spearman rho per `calibration.py`, plus a mean rating per band). Nothing connects the two: a
`learn` run can make the rules worse and the only symptom is a slow drift in the digests.

This adds the missing loop. A cross-validation run estimates whether learning from the current
ratings *generalises*, and the new rules are written only if it does. The report is always
shown, accepted or not.

Decisions already made (do not re-litigate):

- **Gated write, report always shown.** The run computes the estimate, prints/displays the
  report either way, and writes `preferences.md` only when the gate passes. `--force` overrides.
- **Evaluate the recipe, ship the full-data artifact.** Fold models exist only to produce
  held-out scores. What lands in `preferences.md` is a final `learn` over *all* ratings. A fold
  model is never written to disk.
- **Both arms are rescored.** The stored scores of rated listings are not a valid baseline: they
  come from different eras of `preferences.md` with different example sets, some from before any
  ratings existed. A comparison is only meaningful when both arms score the same listings under
  identical conditions in the same run.
- **Fixed fold count.** `folds` is a setting, never derived from N. Leave-one-out (k=N) would make
  the run O(N²) — O(N) folds each with an O(N) learn prompt. Fixed k keeps it linear.
- **Cap the evaluation, not the training.** Each fold learns from the full 80% of ratings; only
  the held-out listings that get *scored* are capped. See "Why the cap is where it is".
- **Never automatic.** This is not part of `check`, not scheduled, not triggered by a rating
  count. It runs only when the user presses the button or types the command.

## Why the cap is where it is

The two halves of a run have opposite economics.

Training is `folds + 1` learn calls. Each learn prompt is O(N) — `format_rated` over the in-fold
ratings — but the call count is constant, so training is ~20% of the bill and genuinely better
with more ratings. It stays uncapped.

Evaluation is `2 × folds × batches-per-fold` scoring calls. Each scoring prompt is constant in N
(`cfg.examples` caps the examples at 20, `cfg.batch_size` fixes the batch at 10), so the token
cost is linear in the number of listings scored — ~80% of the bill. But its return is
`SE(rho) ≈ 1/√(n−1)`: 0.14 at 50 pairs, 0.10 at 100. Doubling the evaluation set moves the error
bar by 0.04 while doubling the cost. Beneath that sits a floor `√n` cannot touch — `claude -p` is
stochastic, so the same prompt scores the same listing differently run to run, and no number of
ratings shrinks that.

So the evaluation set is capped at `eval_cap` listings and the wall clock goes flat:

| N ratings | calls | input tokens | wall clock |
|-----------|-------|--------------|------------|
| 40        | 16    | ~150k        | 8–15 min   |
| 100       | 16    | ~178k        | 8–16 min   |
| 400       | 16    | ~350k        | 8–16 min   |

Tokens still creep up through the 6 learn calls — a cost `jobhunt learn` already pays today,
since `_preferences_prompt` feeds it `store.all_ratings()` uncapped.

Tuning note, enforced by the planner's warning: choose `folds` so `eval_cap // folds` is a
multiple of `batch_size`, or half-empty batches cost full-batch latency. The defaults
(`eval_cap=50`, `folds=5`, `batch_size=10`) fit exactly; `folds=10` with the same cap would cost
20 scoring calls for the same 50 listings.

## Leakage

Four holes exist today. Each is closed by an injection point, and each has a test.

| Leak | Where it is today | Fix |
|------|-------------------|-----|
| Held-out rating shapes the rules being tested | `_preferences_prompt` calls `store.all_ratings()` | `learn_rules` takes the rated list |
| Held-out listing appears as its own few-shot example | `score_listings` calls `store.rated_examples()` | `ScoringContext` carries the example pool |
| Candidate rules read from disk instead of memory | `score_listings` reads `paths.preferences` | `ScoringContext` carries the preferences text |
| CV scores overwrite the real ones | `store.save_scores` at the end of each batch | CV scoring never persists (see `score_batch`) |

The second is the subtle one: `score_listings` already drops examples whose id is in the current
*batch*, but a CV fold needs every held-out rating dropped from the pool, and `by_surprise` ranks
exactly those most-informative examples first.

## Splitting: deterministic, no RNG

Sort every rated listing by `(rating, listing_id)` and deal round-robin into `folds` lists. This
stratifies by rating value by construction — the rare 4s and 5s spread evenly — and is fully
deterministic, so a re-run on unchanged data gives the same split and tests need no seed.

Within a fold, the evaluation sample is the first `eval_cap // folds` listings taken round-robin
from the fold's own rating-sorted order, so the sample is stratified too and never all 1s.

```python
def deal(rated: list[tuple[Listing, Rating]], folds: int) -> list[list[tuple[Listing, Rating]]]
def sample(fold: list[tuple[Listing, Rating]], cap: int) -> list[tuple[Listing, Rating]]
```

## `jobhunt/crossval.py`

New module. Imports `calibration`, `ratings`, `scoring`, `store`; imports nothing from `web`.

```python
@dataclass(frozen=True)
class Plan:
    """What a run will cost, worked out before anything is sent to Claude."""
    n_ratings: int
    n_eval: int                # listings actually scored per arm
    folds: int
    learn_calls: int           # folds + 1
    score_calls: int           # 2 × sum(ceil(len(eval_fold) / batch_size))
    input_tokens: int          # measured from real prompts, see below
    warnings: list[str]        # e.g. half-empty batches

    @property
    def total_calls(self) -> int
    @property
    def minutes(self) -> tuple[int, int]     # (low, high), total_calls × 30s and × 60s
```

`plan()` is pure and makes **no Claude call**. `build_prompt` and `_preferences_prompt` are pure
functions, so the estimate is measured, not guessed: build fold 0's learn prompt and its first
scoring prompt, take `len(text) // 4` for each, and multiply by the call counts. The number the
user sees before pressing the button is within a few percent of what the run sends.

```python
@dataclass(frozen=True)
class Arm:
    label: str                 # "current" | "candidate"
    agreement: Agreement       # reuses calibration.agreement over the pooled held-out pairs
    mean_surprise: float

@dataclass(frozen=True)
class Result:
    plan: Plan
    current: Arm
    candidate: Arm
    fold_rho: list[tuple[float | None, float | None]]   # per fold, (current, candidate)
    delta_rho: float | None    # candidate − current; positive is better (ranking)
    delta_surprise: float      # candidate − current; negative is better (bands)
    dropped: int               # eval listings lost to a failed batch, removed from both arms
    accepted: bool
    reason: str                # one sentence, always populated
    ran_at: datetime
    cancelled: bool = False
```

`fold_rho` is a sanity check, not the verdict. The verdict pools every held-out pair per arm and
runs it through the existing `agreement()`, because per-fold rho on 10 pairs is noise. Pooling
scores produced by `folds` different rule sets onto one ranking is the standard CV shortcut and
not a free one — the per-fold column is there so a run where the folds disagree wildly is visible
rather than averaged away.

### The run

```python
StopCheck = Callable[[], bool]

def cross_validate(
    paths: Paths, store: Store, runner: Runner, settings: Settings, today: date,
    progress: Progress = _silent,          # scoring.Progress, as elsewhere
    should_stop: StopCheck = lambda: False,
    force: bool = False,
) -> Result
```

1. `plan()`. Refuse if `n_ratings < min_ratings` or `total_calls > max_calls` — raise
   `TooExpensive` / `NotEnoughRatings`, both carrying the sentence to show the user.
2. `deal` into folds; `sample` each fold's evaluation set.
3. Per fold: `learn_rules` over the other folds' ratings → candidate `RuleSet`; build two
   `ScoringContext`s differing only in their preferences text; score the fold's evaluation
   sample under each; collect `(score, rating)` pairs per arm.
4. Pool per arm → `agreement()` and `mean_surprise`.
5. Apply the gate. If it passes, one final `learn_rules` over **all** ratings, rendered and
   written through the existing `render_preferences`; set `LEARNED_AT` as `regenerate_preferences`
   does today.
6. Persist `Result` as JSON in `meta` under `calibration_cv` — last run only, matching how
   `update.status()` keeps one state rather than a history.

`should_stop()` is checked between every Claude call. On stop, the run returns a `Result` with
`cancelled=True`, whatever arms completed, `accepted=False`, and writes nothing.

### The gate

```python
def gate(
    delta_rho: float | None, delta_surprise: float, cfg: CalibrationSettings,
) -> tuple[bool, str]      # (accepted, the one-sentence reason shown either way)
```

Accept when **both** hold:

- `delta_rho >= cfg.min_rho_gain` (default 0.05)
- `delta_surprise <= cfg.max_surprise_loss` (default 0.25)

Two numbers because rho is rank-only and blind to a systematic shift: rules that add 20 points to
every score leave rho untouched while flooding the shortlist and breaking the bands the scoring
prompt promises. Mean surprise (`calibration.surprise`, already there) is what catches that.

A `delta_rho` of `None` — either arm's rho undefined — is a reject with the reason spelled out.

**Deliberately not a bootstrap.** A paired bootstrap over listings would cost nothing in Claude
calls and is more principled about listing-sampling noise, but at ~50 eval listings its 90th
percentile lands in roughly the same place as a flat +0.05 margin, and the flat margin is one
sentence in the report instead of a percentile the user has to interpret. Revisit if the reported
deltas cluster near the margin in practice.

## Extracted seams

Both refactors are worth doing on their own terms; CV is what forces them.

**`ratings.learn_rules`** — the Claude call, pulled out of file I/O:

```python
def learn_rules(
    prefs: Preferences, rated: list[tuple[Listing, Rating]], scores: dict[str, Score],
    runner: Runner, model: str, caps: PreferenceSettings,
) -> RuleSet | None
```

`regenerate_preferences` becomes: read and split the file → `store.all_ratings()` →
`learn_rules` → `render_preferences` → write. Identical behaviour, identical return contract
(`False` and an untouched file when Claude's output is unusable).

**`scoring.ScoringContext`** — the inputs `score_listings` currently reads from disk and store:

```python
@dataclass(frozen=True)
class ScoringContext:
    profile: str
    preferences: str
    cv: str
    examples: list[tuple[Listing, Rating]]     # already surprise-sorted
    example_scores: dict[str, Score]

    @classmethod
    def load(cls, paths: Paths, store: Store, cfg: ScoringConfig) -> ScoringContext
```

`score_listings` grows `context: ScoringContext | None = None`, defaulting to
`ScoringContext.load(...)` — today's behaviour exactly, so no caller changes.

CV needs to score without persisting, which `score_listings` cannot do (it saves per batch and
returns counts, not scores). Rather than bolt a `persist=False` flag onto it, extract the loop
body:

```python
def score_batch(
    batch: list[Listing], context: ScoringContext, runner: Runner, cfg: ScoringConfig,
) -> list[Score]      # raises ValueError/RuntimeError; the retry stays in score_listings
```

`score_listings` keeps its retry, progress lines and `store.save_scores`; `crossval` calls
`score_batch` directly and holds the scores in memory. A CV run therefore cannot touch the
`scores` table — enforced by the store being absent from `score_batch`'s signature, not by a flag
someone can pass wrong.

## Settings

New block in `settings.py`, added to `Settings` and to the Settings page:

```python
class CalibrationSettings(BaseModel):
    """What a cross-validation run is allowed to spend, and what counts as an improvement."""
    folds: int = Field(5, ge=2, le=10)
    eval_cap: int = Field(50, ge=10, le=500)      # listings scored per arm
    min_ratings: int = Field(20, ge=10)           # below this a delta is noise; refuse
    max_calls: int = Field(40, ge=4)              # hard ceiling: refuse rather than run
    min_rho_gain: float = Field(0.05, ge=0.0, le=1.0)
    max_surprise_loss: float = Field(0.25, ge=0.0, le=4.0)
```

Defaults keep existing workspaces working (the block is absent from their `settings.yaml` and
pydantic fills it). `max_calls` is the guard rail: the planner refuses and names the setting to
lower, rather than starting a run that costs more than the user expected.

## CLI

`jobhunt learn` gains the flags — the write *is* a learn, and the cross-validation is the gate on
it:

```
jobhunt learn --cross-validate        # plan → confirm → run → report → gated write
jobhunt learn --cross-validate --dry-run   # print the plan and stop. No Claude call.
jobhunt learn --cross-validate --force     # write regardless of the gate; report still shown
jobhunt learn --cross-validate --yes       # skip the confirmation prompt
```

Without `--cross-validate`, `learn` is untouched: one Claude call, writes unconditionally.

The confirmation is the token guard at the CLI:

```
Cross-validating 100 ratings on 50 held-out listings, 5 folds.
  16 claude calls (6 learn, 10 scoring), ~178k input tokens, roughly 8-16 minutes.
Run it? [y/N]
```

`jobhunt calibration` keeps its current report and appends the last CV result when one exists:
`Last cross-validation: 2026-09-23 — rho 0.41 → 0.58 (+0.17), accepted.`

## Web

The Calibration page (`/calibration`) gains a section below the disagreements:

- The plan, rendered before anything runs: call count, token estimate, minute range. This is on
  the page, not behind the button — the cost is visible without committing to it.
- **Cross-validate & update preferences**, posting to `/actions/cross-validate`, which starts a
  `JobRunner` job named `cross-validate`. Disabled with the refusal sentence shown in place when
  the planner refuses (too few ratings, or over `max_calls`).
- The progress log the page already polls, one line per fold and per batch.
- The last `Result`: both arms' rho and mean surprise, the delta, the verdict sentence, the
  per-fold column, and whether it wrote.

### Stopping a job

`JobRunner` has no cancellation, and a 16-call run is exactly the job that needs one. Generic
mechanism, wired only where it is checked:

- `Job` gains `_stop: threading.Event`, `cancel()`, and `stopping() -> bool` (a method, so it
  can be handed straight over as the `should_stop` callable).
- `JobRunner.start(name, fn, cancellable: bool = False)`. When `cancellable`, `_run` calls
  `fn(job.lines.append, job.stopping)`; otherwise `fn(job.lines.append)` exactly as today. An
  explicit flag rather than signature inspection, and every existing call site is untouched.
- `POST /jobs/{id}/cancel` sets it; the page shows **Stop** while a job runs.
- A job that stops reaches status `done` with a final line saying it was stopped — never
  `failed`, which is reserved for something going wrong.

`cross_validate` checks `should_stop()` between Claude calls, so the worst case after pressing
Stop is one in-flight call. Other jobs ignore the flag for now; they are short.

## Error handling

- `NotEnoughRatings` / `TooExpensive` are raised by `plan()` before any call and carry the
  sentence the CLI prints and the page shows. Neither is an error state — the button is simply
  disabled with the reason.
- A learn call whose output is unusable fails that fold. One retry (matching `score_listings`);
  if it fails again the run aborts, reports which fold, and writes nothing. A partial CV is not
  a weaker CV, it is a different experiment.
- A scoring batch that fails retries once, then drops those listings from **both** arms, so the
  comparison stays paired. `Result.dropped` carries the count and the report names it. Past
  `MAX_DROPPED = 1/3` of the evaluation set the run reports no verdict (`accepted=False`, the
  reason naming the loss) rather than deciding on a third of the sample it planned for.
- `claude` reporting `OAuth session expired` surfaces as it does elsewhere: the user runs
  `claude login`.

## Testing

No network, no Claude. `tests/conftest.py`'s `fake_runner` scores 90, 80, … and answers the
preferences prompt with one rule; CV needs a runner whose scores *differ* between two preference
texts, so `tests/test_crossval.py` adds a `scripted_runner` keyed on prompt content.

- `deal` stratifies: every fold's rating histogram within one of every other's; the split is
  identical across two calls on the same data.
- `sample` respects the cap and is stratified within the fold.
- **Leakage (the important one):** capture every prompt the run sends; assert no held-out
  listing's id, title, or rating line appears in any prompt used to score it, in either arm.
- `plan()` makes no runner call — assert the fake runner was never invoked in `--dry-run`.
- `plan().total_calls` equals the number of runner invocations an actual run makes.
- `TooExpensive` when `max_calls` is lowered below the plan; `NotEnoughRatings` below
  `min_ratings`.
- `gate` accepts on a rho gain, rejects on a gain under the margin, rejects when rho improves but
  mean surprise worsens past `max_surprise_loss`, rejects on `delta_rho is None`.
- The store is untouched: rated listings' scores are byte-identical before and after a run.
- Rejected run leaves `preferences.md` byte-identical; accepted run rewrites `## Learned` and
  `## Specifics` and leaves `## Manual` and any extra section alone.
- `--force` writes despite a rejecting gate.
- Cancellation: a `should_stop` that flips after the first fold returns `cancelled=True`, writes
  nothing, and the job ends `done`.
- `ScoringContext.load` reproduces today's behaviour — an existing `test_scoring.py` case passes
  unchanged through the new default.
- Web: `JobRunner(background=False)`, the page renders the plan, the button posts, the result
  renders; `/jobs/{id}/cancel` on a finished job is a no-op, not an error.

## Files

| file | change |
|------|--------|
| `src/jobhunt/crossval.py` | new — `Plan`, `Arm`, `Result`, `deal`, `sample`, `plan`, `gate`, `cross_validate` |
| `src/jobhunt/calibration.py` | add `mean_surprise(pairs)`; `BANDS`/`surprise` unchanged |
| `src/jobhunt/ratings.py` | extract `learn_rules`; `regenerate_preferences` calls it |
| `src/jobhunt/scoring.py` | add `ScoringContext` and `score_batch`; `score_listings` delegates |
| `src/jobhunt/settings.py` | `CalibrationSettings`, added to `Settings` |
| `src/jobhunt/workspace.py` | `cross_validate(...)`, `plan_cross_validation()` |
| `src/jobhunt/cli.py` | `learn --cross-validate/--dry-run/--force/--yes`; CV line in `calibration` |
| `src/jobhunt/web/jobs.py` | `Job.cancel()` / `stopping`, `should_stop` passed through |
| `src/jobhunt/web/app.py` | `/actions/cross-validate`, `/jobs/{id}/cancel`, calibration context |
| `src/jobhunt/web/templates/calibration.html` | plan, button, progress, last result |
| `src/jobhunt/web/templates/settings.html` | the calibration block |
| `tests/test_crossval.py` | new |
| `tests/test_scoring.py`, `test_ratings.py`, `test_web.py`, `test_cli.py` | seams and wiring |
| `pyproject.toml`, `src/jobhunt/CHANGELOG.md` | 0.8.0 |
| `README.md`, `CLAUDE.md` | the command, and `crossval.py` in the module map |

## Not in scope

- A history of CV runs. One `meta` key, last run only, as `update.status()` does.
- A bootstrap confidence interval on `delta_rho` (reasoning above).
- Re-running an arm to measure the LLM noise floor directly — it would double the cost to
  quantify a number the report can describe in a sentence instead.
- Comparing against "no learned rules at all" (`--vs-manual`), which answers a different and
  rarer question: is `## Learned` earning its keep.
- Cancellation for the other `JobRunner` jobs. The mechanism is generic; only CV checks it.
