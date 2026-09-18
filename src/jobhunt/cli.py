"""`jobhunt` command line: fetch → score → digest → rate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import typer

from jobhunt import pipeline
from jobhunt.config import Config, Paths, find_root, load_config
from jobhunt.digest import RunInfo, newest_digest
from jobhunt.ratings import ingest_ratings, rebuild_from_jsonl, regenerate_preferences
from jobhunt.scoring import claude_runner, score_listings
from jobhunt.sources import list_sources
from jobhunt.store import Store

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

# Swapped for a fake in tests; everything that talks to Claude goes through this.
RUNNER = claude_runner
MIN_RATINGS_TO_LEARN = 3


@dataclass
class Ctx:
    paths: Paths
    config: Config

    @property
    def store(self) -> Store:
        return Store(self.paths.db)


NO_WORKSPACE = (
    "Not inside a jobhunt workspace (no config/sources.yaml found). Run: jobhunt init DIR"
)


@app.callback()
def main(
    ctx: typer.Context,
    root: Path | None = typer.Option(
        None, help="Workspace root (default: nearest directory containing config/sources.yaml)"
    ),
) -> None:
    if ctx.invoked_subcommand == "init":
        return  # init creates a workspace; it must not require one
    root = root or find_root()
    if root is None or not (root / "config" / "sources.yaml").exists():
        typer.echo(NO_WORKSPACE, err=True)
        raise typer.Exit(1)
    root = root.resolve()
    ctx.obj = Ctx(paths=Paths(root), config=load_config(root))


def _source_list(ctx: Ctx, only: str | None, fixture: Path | None) -> list[tuple[str, dict]]:
    if fixture:
        return [("fixture", {"path": str(fixture)})]
    names = [only] if only else ctx.config.enabled_sources()
    return [(n, ctx.config.sources.get(n, {})) for n in names]


def _fetch(ctx: Ctx, only: str | None, fixture: Path | None) -> RunInfo:
    from jobhunt.sources.http import PoliteClient

    sources = _source_list(ctx, only, fixture)
    with PoliteClient() as http:
        info = pipeline.fetch_sources(ctx.store, http, sources, progress=typer.echo)
    typer.echo(f"fetched: {info.new} new listing(s) from {len(sources)} source(s)")
    for name, err in info.errors.items():
        typer.echo(f"  ! {name}: {err}", err=True)
    return info


@app.command()
def fetch(
    ctx: typer.Context,
    source: str | None = typer.Option(None, help="Only this source"),
    fixture: Path | None = typer.Option(None, help="Load listings from a JSON fixture instead"),
) -> None:
    """Pull new listings from enabled sources into the local store."""
    _fetch(ctx.obj, source, fixture)


def _score(ctx: Ctx, dry_run: bool = False) -> None:
    result = score_listings(
        ctx.store,
        ctx.paths,
        ctx.config.scoring,
        RUNNER,
        date.today(),
        dry_run=dry_run,
        progress=typer.echo,
    )
    if dry_run:
        return
    typer.echo(f"scored: {result.scored} listing(s), {result.failed} failed")
    for err in result.errors:
        typer.echo(f"  ! scoring: {err}", err=True)


@app.command()
def score(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the first prompt and stop"),
) -> None:
    """Score unscored listings with Claude (`claude -p`)."""
    _score(ctx.obj, dry_run=dry_run)


@app.command()
def digest(ctx: typer.Context) -> None:
    """Write a ranked digest of unrated listings to digests/."""
    c: Ctx = ctx.obj
    path = pipeline.build_digest(
        c.store, c.paths.digests, date.today(), RunInfo(), limit=c.config.digest.limit
    )
    typer.echo(f"digest: {path}")


@app.command()
def check(
    ctx: typer.Context,
    source: str | None = typer.Option(None, help="Only this source"),
    fixture: Path | None = typer.Option(None, help="Load listings from a JSON fixture instead"),
    no_score: bool = typer.Option(False, "--no-score", help="Skip Claude scoring"),
) -> None:
    """fetch → score → digest, in one go."""
    c: Ctx = ctx.obj
    info = _fetch(c, source, fixture)
    if not no_score:
        _score(c)
    path = pipeline.build_digest(
        c.store, c.paths.digests, date.today(), info, limit=c.config.digest.limit
    )
    typer.echo(f"digest: {path}")


@app.command()
def rate(
    ctx: typer.Context,
    digest_file: Path | None = typer.Argument(None, help="Digest to read (default: newest)"),
    no_learn: bool = typer.Option(False, "--no-learn", help="Skip preferences regeneration"),
    force: bool = typer.Option(False, "--force", help="Regenerate preferences even if few new"),
    rebuild: bool = typer.Option(False, help="Replay data/ratings.jsonl into the store first"),
) -> None:
    """Ingest the ratings you wrote into a digest."""
    c: Ctx = ctx.obj
    store = c.store
    if rebuild:
        n = rebuild_from_jsonl(store, c.paths.ratings)
        typer.echo(f"rebuilt {n} rating record(s) from {c.paths.ratings}")
    if digest_file is None:
        digest_file = newest_digest(c.paths.digests)
        if digest_file is None:
            typer.echo("no digests found", err=True)
            raise typer.Exit(1)
    result = ingest_ratings(store, digest_file, c.paths.ratings)
    for err in result.errors:
        typer.echo(f"  ! {digest_file.name}: {err}", err=True)
    typer.echo(f"{result.added} rating(s) ingested from {digest_file.name}")
    if no_learn:
        return
    if result.added < MIN_RATINGS_TO_LEARN and not force:
        typer.echo(
            f"preferences: skipped (fewer than {MIN_RATINGS_TO_LEARN} new ratings; "
            "use --force to regenerate anyway)"
        )
        return
    typer.echo(f"regenerating preferences with {c.config.scoring.model} (one Claude call)…")
    ok = regenerate_preferences(c.paths, store, RUNNER, c.config.scoring.model)
    typer.echo("preferences: updated" if ok else "preferences: failed (file left untouched)")


@app.command()
def sources(ctx: typer.Context) -> None:
    """List known sources and whether they are enabled."""
    enabled = set(ctx.obj.config.enabled_sources())
    for name in list_sources():
        typer.echo(f"{name:20s} {'enabled' if name in enabled else 'disabled'}")
