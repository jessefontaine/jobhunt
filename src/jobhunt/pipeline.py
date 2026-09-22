"""Orchestration: fetch from sources into the store, and build digests from it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from jobhunt.digest import RunInfo, digest_path, render_digest, render_shortlist
from jobhunt.models import Listing
from jobhunt.settings import Settings
from jobhunt.sources import get_source
from jobhunt.store import Store

Progress = Callable[[str], None]


def fetch_sources(
    store: Store,
    http: Any,
    sources: list[tuple[str, dict]],
    progress: Progress = lambda msg: None,
) -> RunInfo:
    """Run each (name, cfg) source, upsert results, fetch details for new listings.

    Never raises for a source; problems end up in RunInfo.errors. `progress` gets one line per
    stage (fetches run at 1 request/s, so silence looks like a hang).
    """
    info = RunInfo()
    for name, cfg in sources:
        progress(f"{name}: fetching…")
        try:
            source = get_source(name)
            result = source.fetch(cfg, http)
        except Exception as exc:  # a source bug must not kill the run
            info.errors[name] = f"{type(exc).__name__}: {exc}"
            progress(f"{name}: failed ({info.errors[name]})")
            continue
        new = store.upsert_listings(result.listings)
        info.new += len(new)
        progress(f"{name}: {len(new)} new of {len(result.listings)} seen")
        fresh = [lst for lst in result.listings if lst.id in new]
        if fresh and hasattr(source, "fetch_detail"):
            progress(f"{name}: fetching details for {len(fresh)} new listing(s)…")
        errors = list(result.errors)
        errors += _fetch_details(store, http, source, fresh)
        if errors:
            info.errors[name] = "; ".join(errors)
        info.manual.update(result.manual_urls)
    return info


def _fetch_details(store: Store, http: Any, source: Any, listings: list[Listing]) -> list[str]:
    fetch_detail = getattr(source, "fetch_detail", None)
    if fetch_detail is None:
        return []
    errors: list[str] = []
    for lst in listings:
        try:
            store.set_description(lst.id, fetch_detail(lst.url, http))
        except Exception as exc:
            errors.append(f"detail {lst.url}: {type(exc).__name__}: {exc}")
    return errors


def build_digest(
    store: Store,
    digests_dir: Path,
    today: date,
    info: RunInfo,
    settings: Settings | None = None,
) -> Path:
    """Write a digest of unexpired, not-yet-rated listings (best first); return its path.

    The settings decide which scores are shown at all, how many entries fit, and which
    deadlines are marked as closing soon.
    """
    settings = settings or Settings()
    display = settings.display
    candidates = store.candidate_listings(today)
    scores = store.get_scores([lst.id for lst in candidates])
    listings = [
        lst
        for lst in candidates
        if display.in_range(scores[lst.id].score if lst.id in scores else None)
    ]
    limit = settings.digest.limit
    if limit is not None and len(listings) > limit:
        scored = sorted(
            (lst for lst in listings if lst.id in scores),
            key=lambda lst: scores[lst.id].score,
            reverse=True,
        )
        unscored = [lst for lst in listings if lst.id not in scores]
        listings = (scored + unscored)[:limit]
    if len(listings) < len(candidates):
        info.total = len(candidates)
    path = digest_path(digests_dir, today)
    path.write_text(
        render_digest(
            today,
            listings,
            scores,
            info,
            shortlist=store.shortlist(today, settings.shortlist.min_rating),
            soon_days=display.deadline_soon_days,
        )
    )
    return path


def write_shortlist(store: Store, path: Path, today: date, min_rating: int = 4) -> str:
    """Overwrite `path` with the current shortlist (rated >= `min_rating`, unexpired)."""
    body = render_shortlist(store.shortlist(today, min_rating)) or "(none yet)"
    text = f"# Shortlist — {today.isoformat()}\n\n{body}\n"
    path.write_text(text)
    return text
