"""A workspace directory and the operations the CLI and the web UI run on it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobhunt import pipeline
from jobhunt.config import Config, Paths, load_config
from jobhunt.digest import RunInfo
from jobhunt.ratings import regenerate_preferences
from jobhunt.scoring import Runner, ScoreRunResult, claude_runner, score_listings
from jobhunt.store import Store

Progress = Callable[[str], None]


def _silent(msg: str) -> None:
    pass


@dataclass
class Workspace:
    paths: Paths
    config: Config
    runner: Runner = claude_runner  # everything that talks to Claude goes through this

    @classmethod
    def open(cls, root: Path, runner: Runner = claude_runner) -> Workspace:
        root = root.resolve()
        return cls(paths=Paths(root), config=load_config(root), runner=runner)

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
        self, rescore: bool = False, dry_run: bool = False, progress: Progress = _silent
    ) -> ScoreRunResult:
        """Score unscored listings (every open one with `rescore`) through `self.runner`."""
        result = score_listings(
            self.store,
            self.paths,
            self.config.scoring,
            self.runner,
            date.today(),
            dry_run=dry_run,
            rescore=rescore,
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
            store, self.paths.digests, today, info or RunInfo(), limit=self.config.digest.limit
        )
        pipeline.write_shortlist(store, self.paths.shortlist, today)
        progress(f"digest: {path}")
        return path

    def check(
        self,
        only: str | None = None,
        fixture: Path | None = None,
        no_score: bool = False,
        rescore: bool = False,
        progress: Progress = _silent,
    ) -> Path:
        """fetch → score → digest."""
        info = self.fetch(only, fixture, progress)
        if not no_score:
            self.score(rescore=rescore, progress=progress)
        return self.digest(info, progress)

    def shortlist(self) -> str:
        """Rewrite shortlist.md; return its text."""
        return pipeline.write_shortlist(self.store, self.paths.shortlist, date.today())

    def learn(self, progress: Progress = _silent) -> bool:
        """Regenerate `## Learned` in preferences.md from every rating (one Claude call)."""
        model = self.config.scoring.model
        progress(f"regenerating preferences with {model} (one Claude call)…")
        ok = regenerate_preferences(self.paths, self.store, self.runner, model)
        progress("preferences: updated" if ok else "preferences: failed (file left untouched)")
        return ok
