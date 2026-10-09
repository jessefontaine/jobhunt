"""The discard pile: listings this person cannot apply to, kept so they never come back.

Discards follow the ratings pattern: an append-only `data/discards.jsonl` keeps the history,
the store keeps the latest state per listing. Scoring discards what Claude calls ineligible;
the user discards (and restores) from the UI or the CLI. A restore is recorded too, so
scoring never discards that listing again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from jobhunt.models import DiscardEvent
from jobhunt.store import Store


def _record(store: Store, jsonl: Path, event: DiscardEvent) -> DiscardEvent:
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a") as fh:
        fh.write(event.model_dump_json() + "\n")
    store.save_discard(event)
    return event


def record_discard(
    store: Store,
    jsonl: Path,
    listing_id: str,
    reason: str = "",
    by: Literal["scoring", "user"] = "user",
) -> DiscardEvent:
    """Put a listing on the pile (its rating, if any, stays in the store for a restore)."""
    return _record(store, jsonl, DiscardEvent(listing_id=listing_id, reason=reason, by=by))


def record_restore(store: Store, jsonl: Path, listing_id: str) -> DiscardEvent:
    """Take a listing off the pile, for good: scoring will not discard it again."""
    return _record(store, jsonl, DiscardEvent(listing_id=listing_id, discarded=False))


def rebuild_discards(store: Store, jsonl: Path) -> int:
    """Replay the log into the store (latest record per listing wins)."""
    if not jsonl.exists():
        return 0
    n = 0
    for line in jsonl.read_text().splitlines():
        if line.strip():
            store.save_discard(DiscardEvent.model_validate_json(line))
            n += 1
    return n
