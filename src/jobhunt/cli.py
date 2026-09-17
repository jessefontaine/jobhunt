"""`jobhunt` command line: fetch → score → digest → rate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import typer

from jobhunt import pipeline
from jobhunt.config import Config, Paths, find_root, load_config
from jobhunt.digest import RunInfo, newest_digest
from jobhunt.ratings import ingest_ratings, rebuild_from_jsonl
from jobhunt.sources import list_sources
from jobhunt.store import Store

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)


@dataclass
class Ctx:
    paths: Paths
    config: Config

    @property
    def store(self) -> Store:
        return Store(self.paths.db)


@app.callback()
def main(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, help="Project root (default: nearest pyproject.toml)"),
) -> None:
    root = (root or find_root()).resolve()
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
        info = pipeline.fetch_sources(ctx.store, http, sources)
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


@app.command()
def digest(ctx: typer.Context) -> None:
    """Write a ranked digest of unrated listings to digests/."""
    path = pipeline.build_digest(ctx.obj.store, ctx.obj.paths.digests, date.today(), RunInfo())
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
    path = pipeline.build_digest(c.store, c.paths.digests, date.today(), info)
    typer.echo(f"digest: {path}")


@app.command()
def rate(
    ctx: typer.Context,
    digest_file: Path | None = typer.Argument(None, help="Digest to read (default: newest)"),
    no_learn: bool = typer.Option(False, "--no-learn", help="Skip preferences regeneration"),
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


@app.command()
def sources(ctx: typer.Context) -> None:
    """List known sources and whether they are enabled."""
    enabled = set(ctx.obj.config.enabled_sources())
    for name in list_sources():
        typer.echo(f"{name:20s} {'enabled' if name in enabled else 'disabled'}")
