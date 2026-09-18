"""Score listings for fit with Claude via the headless `claude -p` CLI."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from jobhunt.config import Paths, ScoringConfig
from jobhunt.models import Listing, Rating, RoleType, Score
from jobhunt.store import Store

# (prompt, model, json_schema) -> raw stdout of `claude -p --output-format json`
Runner = Callable[[str, str, dict | None], str]

DESCRIPTION_LIMIT = 3000


class ScoreItem(BaseModel):
    id: str
    score: int = Field(ge=0, le=100)
    role_type: RoleType = "other"
    area_tags: list[str] = Field(default_factory=list)
    why: str = ""
    concerns: str = ""


class ScoreBatch(BaseModel):
    scores: list[ScoreItem]


SCORE_SCHEMA = ScoreBatch.model_json_schema()

INSTRUCTIONS = """\
You are helping a Master's student in cognitive neuroscience triage job listings.
Score each listing below for how well it fits THIS person, using their profile, their
CV, their stated preferences, and the examples of listings they have already rated.

Scoring guide (0-100):
  85-100  apply — squarely in their interests and methods, open to their level
  65-84   strong — clearly relevant, a real candidate
  40-64   maybe — partially relevant or uncertain fit
  15-39   weak — tangential, wrong level, or missing what they care about
  0-14    irrelevant

Role types: phd, postdoc, ra (research assistant / technician / scientific programmer),
industry, other. Postdocs and roles requiring a completed PhD should score low unless
the listing is explicitly open to MSc graduates.

For each listing return: id (copy exactly), score, role_type, area_tags (2-5 short
lowercase tags), why (ONE sentence naming the specific overlap with the profile),
concerns (ONE sentence on the main risk, or empty string).

Return ONLY JSON of the form {"scores": [ {...}, ... ]} — no prose.
"""


def format_rated(examples: list[tuple[Listing, Rating]], empty: str = "(none)") -> str:
    """One line per rated listing, e.g. `- [5/5] Title — Employer — note: …`."""
    if not examples:
        return empty
    lines = []
    for lst, rating in examples:
        note = f" — note: {rating.note}" if rating.note else ""
        lines.append(f"- [{rating.rating}/5] {lst.title} — {lst.employer}{note}")
        if lst.summary:
            lines.append(f"    {lst.summary[:300]}")
    return "\n".join(lines)


def _listing_block(lst: Listing) -> dict:
    return {
        "id": lst.id,
        "title": lst.title,
        "employer": lst.employer,
        "location": lst.location,
        "deadline": lst.deadline.isoformat() if lst.deadline else None,
        "description": (lst.description or lst.summary)[:DESCRIPTION_LIMIT],
    }


def build_prompt(
    profile: str,
    preferences: str,
    cv: str,
    examples: list[tuple[Listing, Rating]],
    batch: list[Listing],
) -> str:
    return "\n".join(
        [
            INSTRUCTIONS,
            "# Profile",
            profile.strip(),
            "",
            "# Preferences",
            preferences.strip(),
            "",
            "# CV",
            cv.strip(),
            "",
            "# Listings this person already rated (5 = apply, 1 = irrelevant)",
            format_rated(examples, empty="(no rated examples yet)"),
            "",
            "# Listings to score",
            json.dumps([_listing_block(lst) for lst in batch], ensure_ascii=False, indent=1),
        ]
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_payload(raw: str) -> object:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"claude output is not JSON: {raw[:200]!r}") from exc
    if not isinstance(envelope, dict):
        raise ValueError("unexpected claude output shape")
    if envelope.get("is_error"):
        raise ValueError(f"claude error: {envelope.get('result')}")
    if envelope.get("structured_output") is not None:
        return envelope["structured_output"]
    text = envelope.get("result") or ""
    if m := _FENCE_RE.search(text):
        text = m.group(1)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"claude result is not JSON: {text[:200]!r}") from exc


def parse_response(raw: str, expected_ids: set[str], model: str) -> list[Score]:
    """Validate the CLI output into Score objects; raise ValueError if unusable."""
    payload = _extract_payload(raw)
    if isinstance(payload, list):
        payload = {"scores": payload}
    try:
        batch = ScoreBatch.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"scores failed validation: {exc}") from exc
    scores = [
        Score(
            listing_id=item.id,
            score=item.score,
            role_type=item.role_type,
            area_tags=item.area_tags,
            why=item.why.strip(),
            concerns=item.concerns.strip(),
            model=model,
        )
        for item in batch.scores
        if item.id in expected_ids
    ]
    if not scores:
        raise ValueError("no returned ids matched the batch")
    return scores


def claude_runner(prompt: str, model: str, schema: dict | None) -> str:
    """Run `claude -p` headless (uses the logged-in CLI account, no API key)."""
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--no-session-persistence",
        "--tools",
        "",
    ]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, check=False)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    return proc.stdout


@dataclass
class ScoreRunResult:
    scored: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _chunks(items: list[Listing], size: int) -> list[list[Listing]]:
    return [items[i : i + size] for i in range(0, len(items), max(size, 1))]


Progress = Callable[[str], None]


def score_listings(
    store: Store,
    paths: Paths,
    cfg: ScoringConfig,
    runner: Runner,
    today: date,
    dry_run: bool = False,
    progress: Progress = lambda msg: None,
) -> ScoreRunResult:
    """Score every unscored, unexpired listing in batches. One retry per batch.

    `progress` is called with a human-readable line before/after each batch (each `claude -p`
    call takes tens of seconds, so silence looks like a hang).
    """
    result = ScoreRunResult()
    pending = store.unscored_listings(today)
    if not pending:
        return result
    profile = paths.profile.read_text() if paths.profile.exists() else ""
    preferences = paths.preferences.read_text() if paths.preferences.exists() else ""
    cv = paths.cv.read_text() if paths.cv.exists() else ""
    examples = store.rated_examples(cfg.examples)

    batches = _chunks(pending, cfg.batch_size)
    if not dry_run:
        progress(f"scoring {len(pending)} listing(s) in {len(batches)} batch(es) with {cfg.model}…")
    for n, batch in enumerate(batches, 1):
        prompt = build_prompt(profile, preferences, cv, examples, batch)
        if dry_run:
            print(prompt)
            return result
        ids = {lst.id for lst in batch}
        last_error = ""
        for attempt in range(2):
            if attempt:
                progress(f"  batch {n}/{len(batches)}: retrying ({last_error[:80]})")
            try:
                scores = parse_response(runner(prompt, cfg.model, SCORE_SCHEMA), ids, cfg.model)
            except (ValueError, RuntimeError) as exc:
                last_error = str(exc)
                continue
            store.save_scores(scores)
            result.scored += len(scores)
            progress(f"  batch {n}/{len(batches)}: {len(scores)} scored")
            break
        else:
            result.failed += len(batch)
            result.errors.append(f"batch of {len(batch)}: {last_error}")
            progress(f"  batch {n}/{len(batches)}: failed, left unscored")
    return result
