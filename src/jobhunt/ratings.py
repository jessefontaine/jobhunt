"""Parse ratings out of a digest, persist them, and regenerate learned preferences."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

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
SPECIFICS_HEADING = "## Specifics"
SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
PLACEHOLDER = "(none yet)"
PLACEHOLDER_RE = re.compile(r"^-?\s*\(none yet.*\)$")  # the empty-section marker, not a rule

PREFERENCES_INSTRUCTIONS = """\
Below are job listings the person described in the profile has rated from 5 (apply)
to 1 (irrelevant), with optional notes, the rules that person wrote themselves, and the
rules previously learned from earlier ratings.

The person's own rules are context only. Never restate, weaken or contradict them, and do
not write a rule that covers the same ground — they always win. If the ratings seem to
disagree with one of them, leave it alone.

Return two lists of bullet rules, both read by a scorer that ranks new listings:

- "rules": at most 10 general patterns — what this person consistently rates HIGH and what
  they rate LOW, named concretely (research areas, methods, role types, employers,
  constraints). These should hold for listings you have not seen.
- "specifics": at most 8 narrow observations that are too particular to be general rules —
  one employer, one method, one recurring caveat, an exception to a pattern above. Name the
  example they come from. Leave the list empty rather than padding it.

Keep prior rules and specifics that the ratings still support; drop or rewrite ones the
ratings contradict. Prefer fewer, sharper rules over many vague ones.
Return ONLY JSON: {"rules": ["...", "..."], "specifics": ["...", "..."]}.
"""


class RuleSet(BaseModel):
    rules: list[str]
    specifics: list[str] = Field(default_factory=list)


RULE_SCHEMA = RuleSet.model_json_schema()


@dataclass
class Preferences:
    """preferences.md split into the parts the user owns and the part `learn` rewrites."""

    head: str = ""
    manual: str = ""  # the user's own rules — never rewritten
    learned: str = ""  # general patterns, regenerated from the ratings
    specifics: str = ""  # narrow one-off inferences, regenerated from the ratings
    extra: str = ""  # any other `## ` section, kept verbatim


def split_preferences(text: str) -> Preferences:
    """Split preferences.md; sections this module does not own land in `extra` untouched."""
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return Preferences(head=text)
    prefs = Preferences(head=text[: matches[0].start()])
    owned = {"manual": "manual", "learned": "learned", "specifics": "specifics"}
    extras: list[str] = []
    for n, m in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(text)
        field_name = owned.pop(m.group(1).strip().lower(), None)
        if field_name:
            body = text[m.end() : end].strip("\n")
            setattr(prefs, field_name, "" if PLACEHOLDER_RE.match(body.strip()) else body)
        else:
            extras.append(text[m.start() : end].strip("\n"))
    prefs.extra = "\n\n".join(extras)
    return prefs


def render_preferences(prefs: Preferences) -> str:
    """The inverse of `split_preferences`: manual and extra go back exactly as they came in."""
    parts = [
        prefs.head.rstrip("\n"),
        f"{MANUAL_HEADING}\n\n{prefs.manual or '- ' + PLACEHOLDER}",
        f"{LEARNED_HEADING}\n\n{prefs.learned or PLACEHOLDER}",
        f"{SPECIFICS_HEADING}\n\n{prefs.specifics or PLACEHOLDER}",
        prefs.extra,
    ]
    return "\n\n".join(p for p in parts if p) + "\n"


def _preferences_prompt(prefs: Preferences, store: Store) -> str:
    return "\n".join(
        [
            PREFERENCES_INSTRUCTIONS,
            "# The person's own rules (fixed — never rewrite or contradict these)",
            prefs.manual or "(none)",
            "",
            "# Previously learned rules",
            prefs.learned or "(none)",
            "",
            "# Previously learned specifics",
            prefs.specifics or "(none)",
            "",
            "# Rated listings (most recent first)",
            format_rated(store.all_ratings()),
        ]
    )


def _bullets(rules: list[str]) -> str:
    return "\n".join(f"- {r.strip()}" for r in rules if r.strip())


def regenerate_preferences(paths: Paths, store: Store, runner: Runner, model: str) -> bool:
    """Rewrite `## Learned` and `## Specifics` from all ratings, leaving other sections alone.

    Returns False and leaves the file untouched if Claude's output is unusable.
    """
    pref_path = paths.preferences
    prefs = split_preferences(pref_path.read_text() if pref_path.exists() else "# Preferences\n")
    try:
        payload = _extract_payload(runner(_preferences_prompt(prefs, store), model, RULE_SCHEMA))
        ruleset = RuleSet.model_validate(payload)
    except (ValueError, ValidationError, RuntimeError):
        return False
    prefs.learned = _bullets(ruleset.rules)
    prefs.specifics = _bullets(ruleset.specifics)
    pref_path.write_text(render_preferences(prefs))
    store.set_meta(LEARNED_AT, datetime.now().isoformat())
    return True
