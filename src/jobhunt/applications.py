"""Application status per listing, and the motivation letter that went with it.

Statuses follow the ratings pattern: an append-only `data/status.jsonl` keeps the history,
the store keeps the latest step per listing. A status is not a preference signal, so `learn`
never reads it; the letters are, and `learn` reads them only when the user turns that on.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jobhunt.config import Paths
from jobhunt.models import STATUSES, StatusEvent
from jobhunt.store import Store


def record_status(
    store: Store,
    jsonl: Path,
    listing_id: str,
    status: str,
    on: date | None = None,
    note: str = "",
) -> StatusEvent:
    """Append one step to the log and make it the listing's current status."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; one of: {', '.join(STATUSES)}")
    event = StatusEvent(listing_id=listing_id, status=status, on=on or date.today(), note=note)
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a") as fh:
        fh.write(event.model_dump_json() + "\n")
    store.save_status(event)
    return event


def rebuild_statuses(store: Store, jsonl: Path) -> int:
    """Replay the log into the store (latest record per listing wins)."""
    if not jsonl.exists():
        return 0
    n = 0
    for line in jsonl.read_text().splitlines():
        if line.strip():
            store.save_status(StatusEvent.model_validate_json(line))
            n += 1
    return n


def letter_path(paths: Paths, listing_id: str) -> Path:
    return paths.applications / f"{listing_id}.md"


def read_letter(paths: Paths, listing_id: str) -> str:
    path = letter_path(paths, listing_id)
    return path.read_text() if path.exists() else ""


def write_letter(paths: Paths, listing_id: str, text: str) -> Path:
    path = letter_path(paths, listing_id)
    text = text.replace("\r\n", "\n").strip("\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n" if text else "")
    return path
