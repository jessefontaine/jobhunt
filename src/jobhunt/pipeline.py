"""Orchestration: fetch from sources into the store, and build digests from it."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jobhunt.digest import RunInfo, digest_path, render_digest
from jobhunt.models import Listing
from jobhunt.sources import get_source
from jobhunt.store import Store


def fetch_sources(store: Store, http: Any, sources: list[tuple[str, dict]]) -> RunInfo:
    """Run each (name, cfg) source, upsert results, fetch details for new listings.

    Never raises for a source; problems end up in RunInfo.errors.
    """
    info = RunInfo()
    for name, cfg in sources:
        try:
            source = get_source(name)
            result = source.fetch(cfg, http)
        except Exception as exc:  # a source bug must not kill the run
            info.errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        new = store.upsert_listings(result.listings)
        info.new += len(new)
        errors = list(result.errors)
        errors += _fetch_details(
            store, http, source, [lst for lst in result.listings if lst.id in new]
        )
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
    store: Store, digests_dir: Path, today: date, info: RunInfo, limit: int | None = None
) -> Path:
    """Write a digest of unexpired, not-yet-rated listings (best `limit` first); return its path."""
    listings = store.candidate_listings(today)
    scores = store.get_scores([lst.id for lst in listings])
    if limit is not None and len(listings) > limit:
        scored = sorted(
            (lst for lst in listings if lst.id in scores),
            key=lambda lst: scores[lst.id].score,
            reverse=True,
        )
        unscored = [lst for lst in listings if lst.id not in scores]
        info.total = len(listings)
        listings = (scored + unscored)[:limit]
    path = digest_path(digests_dir, today)
    path.write_text(render_digest(today, listings, scores, info))
    return path
