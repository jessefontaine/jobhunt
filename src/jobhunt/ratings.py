"""Parse ratings out of a digest, persist them, and regenerate learned preferences."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from jobhunt.config import Paths
from jobhunt.models import Rating
from jobhunt.scoring import Runner, _extract_payload, format_rated
from jobhunt.store import Store

ID_RE = re.compile(r"^<!-- id: ([0-9a-f]{12}) -->\s*$")
RATING_RE = re.compile(r"^rating:\s*(.*?)\s*$")
NOTE_RE = re.compile(r"^note:\s*(.*?)\s*$")

ParsedRating = tuple[str, int, str]  # (listing_id, rating, note)


def parse_digest(text: str) -> tuple[list[ParsedRating], list[str]]:
    """Return filled-in ratings and human-readable errors (with 1-based line numbers)."""
    parsed: list[ParsedRating] = []
    errors: list[str] = []
    current_id: str | None = None
    rating: int | None = None
    rating_raw: str | None = None
    note = ""

    def flush() -> None:
        if current_id and rating is not None:
            parsed.append((current_id, rating, note))

    for lineno, line in enumerate(text.splitlines(), 1):
        if m := ID_RE.match(line):
            flush()
            current_id, rating, rating_raw, note = m.group(1), None, None, ""
            continue
        if current_id is None:
            continue
        if m := RATING_RE.match(line):
            rating_raw = m.group(1)
            if not rating_raw:
                continue
            num = re.match(r"^(\d+)", rating_raw)
            if num and 1 <= int(num.group(1)) <= 5:
                rating = int(num.group(1))
            else:
                errors.append(
                    f"line {lineno}: rating must be 1-5, got {rating_raw!r} (id {current_id})"
                )
            continue
        if m := NOTE_RE.match(line):
            note = m.group(1)
    flush()
    return parsed, errors


@dataclass
class IngestResult:
    added: int = 0
    errors: list[str] = field(default_factory=list)
    ratings: list[Rating] = field(default_factory=list)


def record_rating(
    store: Store, jsonl: Path, listing_id: str, value: int, note: str, digest: str
) -> Rating | None:
    """Save one rating to the store and append it to the jsonl log.

    Returns None (and writes nothing) when the same rating and note are already recorded.
    """
    existing = store.get_rating(listing_id)
    if existing and existing.rating == value and existing.note == note:
        return None
    rating = Rating(listing_id=listing_id, rating=value, note=note, digest=digest)
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a") as fh:
        fh.write(rating.model_dump_json() + "\n")
    store.save_rating(rating)
    return rating


def ingest_ratings(store: Store, digest: Path, jsonl: Path) -> IngestResult:
    """Read ratings from `digest`; append new/changed ones to `jsonl` and the store."""
    parsed, errors = parse_digest(digest.read_text())
    result = IngestResult(errors=errors)
    for listing_id, value, note in parsed:
        rating = record_rating(store, jsonl, listing_id, value, note, digest.name)
        if rating is not None:
            result.ratings.append(rating)
            result.added += 1
    return result


def rebuild_from_jsonl(store: Store, jsonl: Path) -> int:
    """Replay the append-only log into the store (latest record per listing wins)."""
    if not jsonl.exists():
        return 0
    n = 0
    for line in jsonl.read_text().splitlines():
        if not line.strip():
            continue
        store.save_rating(Rating.model_validate_json(line))
        n += 1
    return n


# -- preferences ------------------------------------------------------------

LEARNED_AT = "learned_at"  # store meta key: when `## Learned` was last regenerated


def learned_at(store: Store) -> datetime | None:
    raw = store.get_meta(LEARNED_AT)
    return datetime.fromisoformat(raw) if raw else None


MANUAL_HEADING = "## Manual"
LEARNED_HEADING = "## Learned"

PREFERENCES_INSTRUCTIONS = """\
Below are job listings the person described in the profile has rated from 5 (apply)
to 1 (irrelevant), with optional notes, plus the rules previously learned from earlier ratings.

Write at most 12 concise bullet rules describing what this person consistently rates HIGH
and what they rate LOW. Be specific (name research areas, methods, role types, employers,
constraints) — the rules are read by a scorer that ranks new listings. Keep prior rules that
the ratings still support; drop or rewrite ones the ratings contradict. Prefer fewer, sharper
rules over many vague ones. Return ONLY JSON: {"rules": ["...", "..."]}.
"""


class RuleSet(BaseModel):
    rules: list[str]


def split_preferences(text: str) -> tuple[str, str, str]:
    """Return (head, manual_body, learned_body) of preferences.md."""

    def section(heading: str) -> tuple[int, int] | None:
        m = re.search(rf"^{re.escape(heading)}\s*$", text, re.M)
        if not m:
            return None
        start = m.end()
        nxt = re.search(r"^## ", text[start:], re.M)
        end = start + nxt.start() if nxt else len(text)
        return m.start(), end

    manual = section(MANUAL_HEADING)
    learned = section(LEARNED_HEADING)
    cut = min(x[0] for x in (manual, learned) if x) if (manual or learned) else len(text)
    head = text[:cut]
    manual_body = text[manual[0] + len(MANUAL_HEADING) : manual[1]] if manual else ""
    learned_body = text[learned[0] + len(LEARNED_HEADING) : learned[1]] if learned else ""
    return head, manual_body.strip("\n"), learned_body.strip("\n")


def regenerate_preferences(paths: Paths, store: Store, runner: Runner, model: str) -> bool:
    """Rewrite the `## Learned` section from all ratings.

    Returns False and leaves the file untouched if Claude's output is unusable.
    """
    pref_path = paths.preferences
    text = pref_path.read_text() if pref_path.exists() else "# Preferences\n"
    head, manual, learned = split_preferences(text)
    prompt = "\n".join(
        [
            PREFERENCES_INSTRUCTIONS,
            "# Previously learned rules",
            learned or "(none)",
            "",
            "# Rated listings (most recent first)",
            format_rated(store.all_ratings()),
        ]
    )
    try:
        payload = _extract_payload(runner(prompt, model, RuleSet.model_json_schema()))
        rules = RuleSet.model_validate(payload).rules
    except (ValueError, ValidationError, RuntimeError):
        return False
    new_learned = "\n".join(f"- {r.strip()}" for r in rules if r.strip()) or "(none yet)"
    manual_body = manual if manual.strip() else "- (none yet)"
    out = (
        head.rstrip("\n")
        + f"\n\n{MANUAL_HEADING}\n\n{manual_body}\n\n{LEARNED_HEADING}\n\n{new_learned}\n"
    )
    pref_path.write_text(out)
    store.set_meta(LEARNED_AT, datetime.now().isoformat())
    return True
