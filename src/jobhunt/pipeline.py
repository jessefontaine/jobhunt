"""Orchestration: fetch from sources into the store, and build digests from it."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jobhunt.digest import RunInfo, digest_path, render_digest
from jobhunt.sources import get_source
from jobhunt.store import Store


def fetch_sources(store: Store, http: Any, sources: list[tuple[str, dict]]) -> RunInfo:
    """Run each (name, cfg) source, upsert results, collect errors. Never raises for a source."""
    info = RunInfo()
    for name, cfg in sources:
        try:
            result = get_source(name).fetch(cfg, http)
        except Exception as exc:  # a source bug must not kill the run
            info.errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        new = store.upsert_listings(result.listings)
        info.new += len(new)
        if result.errors:
            info.errors[name] = "; ".join(result.errors)
        info.manual.update(result.manual_urls)
    return info


def build_digest(store: Store, digests_dir: Path, today: date, info: RunInfo) -> Path:
    """Write a digest of every unexpired, not-yet-rated listing; return its path."""
    listings = store.candidate_listings(today)
    scores = store.get_scores([lst.id for lst in listings])
    path = digest_path(digests_dir, today)
    path.write_text(render_digest(today, listings, scores, info))
    return path
