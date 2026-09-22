"""A workspace directory and the operations the CLI and the web UI run on it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobhunt import pipeline
from jobhunt.config import Config, Paths, load_config
from jobhunt.digest import RunInfo
from jobhunt.models import Listing, Score
from jobhunt.ratings import regenerate_preferences
from jobhunt.scoring import Runner, ScoreRunResult, claude_runner, score_listings
from jobhunt.settings import Settings, load_settings
from jobhunt.store import Store

Progress = Callable[[str], None]


def _silent(msg: str) -> None:
    pass


@dataclass
class Workspace:
    paths: Paths
    config: Config  # config/sources.yaml: where to look
    settings: Settings  # config/settings.yaml: how results are shown and the pipeline behaves
    runner: Runner = claude_runner  # everything that talks to Claude goes through this

    @classmethod
    def open(cls, root: Path, runner: Runner = claude_runner) -> Workspace:
        root = root.resolve()
        paths = Paths(root)
        config = load_config(root)
        return cls(
            paths=paths, config=config, settings=load_settings(paths, config), runner=runner
        )

    @property
    def store(self) -> Store:
        # A fresh connection per access: sqlite connections belong to the thread that made them,
        # and a background job and a request handler must never share one.
        return Store(self.paths.db)

    def _sources(self, only: str | None, fixture: Path | None) -> list[tuple[str, dict]]:
        if fixture:
            return [("fixture", {"path": str(fixture)})]
        names = [only] if only else self.config.enabled_sources()
        return [(n, self.config.sources.get(n, {})) for n in names]

    def fetch(
        self,
        only: str | None = None,
        fixture: Path | None = None,
        progress: Progress = _silent,
    ) -> RunInfo:
        """Pull new listings from the enabled sources (or `only`, or a JSON `fixture`)."""
        from jobhunt.sources.http import PoliteClient

        sources = self._sources(only, fixture)
        with PoliteClient(contact=self.config.contact) as http:
            info = pipeline.fetch_sources(self.store, http, sources, progress=progress)
        progress(f"fetched: {info.new} new listing(s) from {len(sources)} source(s)")
        for name, err in info.errors.items():
            progress(f"  ! {name}: {err}")
        return info

    def score(
        self,
        rescore: bool = False,
        dry_run: bool = False,
        include_rated: bool = False,
        progress: Progress = _silent,
    ) -> ScoreRunResult:
        """Score unscored listings (every open, unrated one with `rescore`)."""
        result = score_listings(
            self.store,
            self.paths,
            self.settings.scoring,
            self.runner,
            date.today(),
            dry_run=dry_run,
            rescore=rescore,
            include_rated=include_rated,
            progress=progress,
        )
        if not dry_run:
            progress(f"scored: {result.scored} listing(s), {result.failed} failed")
            for err in result.errors:
                progress(f"  ! scoring: {err}")
        return result

    def digest(self, info: RunInfo | None = None, progress: Progress = _silent) -> Path:
        """Write digests/<today>.md and shortlist.md; return the digest path."""
        store = self.store
        today = date.today()
        path = pipeline.build_digest(
            store, self.paths.digests, today, info or RunInfo(), settings=self.settings
        )
        pipeline.write_shortlist(
            store, self.paths.shortlist, today, self.settings.shortlist.min_rating
        )
        progress(f"digest: {path}")
        return path

    def check(
        self,
        only: str | None = None,
        fixture: Path | None = None,
        no_score: bool = False,
        rescore: bool = False,
        include_rated: bool = False,
        progress: Progress = _silent,
    ) -> Path:
        """fetch → score → digest."""
        info = self.fetch(only, fixture, progress)
        if not no_score:
            self.score(rescore=rescore, include_rated=include_rated, progress=progress)
        return self.digest(info, progress)

    def add(
        self,
        url: str,
        *,
        title: str = "",
        employer: str = "",
        description: str = "",
        score: bool = True,
        http=None,
        progress: Progress = _silent,
    ) -> tuple[Listing, Score | None]:
        """Store the listing behind a link the user pasted, and score it (one Claude call).

        With `title` (and optionally `employer`/`description`) the page is never fetched —
        that is the way in for a site that blocks scrapers.
        """
        from jobhunt.sources.http import PoliteClient
        from jobhunt.sources.manual import fetch_listing, manual_listing

        store = self.store
        known = store.find_by_url(url)
        if title or description:
            listing = manual_listing(url, title, employer, description)
        elif http is not None:
            listing = fetch_listing(url, http)
        else:
            progress(f"fetching {url}…")
            with PoliteClient(contact=self.config.contact) as client:
                listing = fetch_listing(url, client)
        if known is not None:
            listing.id = known.id  # keep the id it was scored and rated under
        store.upsert_listings([listing])
        existing = store.get_score(listing.id)
        if known is not None:
            rating = store.get_rating(listing.id)
            rated = f", rated {rating.rating}/5" if rating else ""
            progress(f"already in the store as {known.source}: {known.title}{rated}")
        progress(f"added: {listing.title} — {listing.employer}")
        if not score or existing is not None:
            return listing, existing
        result = score_listings(
            self.store,
            self.paths,
            self.settings.scoring,
            self.runner,
            date.today(),
            listings=[listing],
            progress=progress,
        )
        for err in result.errors:
            progress(f"  ! scoring: {err}")
        return listing, self.store.get_score(listing.id)

    def shortlist(self) -> str:
        """Rewrite shortlist.md; return its text."""
        return pipeline.write_shortlist(
            self.store, self.paths.shortlist, date.today(), self.settings.shortlist.min_rating
        )

    def learn(self, progress: Progress = _silent) -> bool:
        """Regenerate `## Learned` in preferences.md from every rating (one Claude call)."""
        model = self.settings.scoring.model
        progress(f"regenerating preferences with {model} (one Claude call)…")
        ok = regenerate_preferences(
            self.paths, self.store, self.runner, model, self.settings.preferences
        )
        progress("preferences: updated" if ok else "preferences: failed (file left untouched)")
        return ok
