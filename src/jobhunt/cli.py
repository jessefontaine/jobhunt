"""`jobhunt` command line: fetch → score → digest → rate."""

from __future__ import annotations

from pathlib import Path

import typer

from jobhunt.config import Paths, find_root
from jobhunt.digest import newest_digest
from jobhunt.ratings import ingest_ratings, rebuild_from_jsonl
from jobhunt.scaffold import DEFAULT_ENGINE_URL, init_workspace
from jobhunt.scoring import claude_runner
from jobhunt.sources import list_sources
from jobhunt.workspace import Workspace

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

# Swapped for a fake in tests; everything that talks to Claude goes through this.
RUNNER = claude_runner
MIN_RATINGS_TO_LEARN = 3

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
    ctx.obj = root


def _workspace(ctx: typer.Context) -> Workspace:
    root = ctx.obj or find_root()
    if root is None or not Paths(root).sources_yaml.exists():
        typer.echo(NO_WORKSPACE, err=True)
        raise typer.Exit(1)
    return Workspace.open(root, runner=RUNNER)


NEXT_STEPS = """\
workspace created: {dir}

next steps:
  1. edit {dir}/profile/profile.md (and profile/preferences.md -> ## Manual)
  2. put your CV PDF in docs/ and run scripts/extract-cv.sh (or write docs/cv.md by hand)
  3. claude login            (once; scoring uses the claude CLI on your subscription)
  4. cd {dir} && uv run jobhunt check
     then rate listings in the digest, or open the folder in Claude Code and type /jobhunt
"""


@app.command()
def init(
    directory: Path = typer.Argument(..., help="Workspace directory to create"),
    engine: str = typer.Option(
        DEFAULT_ENGINE_URL, "--engine", help="Engine git URL written into pyproject.toml"
    ),
) -> None:
    """Create a new workspace (profile, config, data dirs) in DIRECTORY."""
    try:
        init_workspace(directory, engine)
    except FileExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo(NEXT_STEPS.format(dir=directory))


@app.command()
def fetch(
    ctx: typer.Context,
    source: str | None = typer.Option(None, help="Only this source"),
    fixture: Path | None = typer.Option(None, help="Load listings from a JSON fixture instead"),
) -> None:
    """Pull new listings from enabled sources into the local store."""
    _workspace(ctx).fetch(source, fixture, progress=typer.echo)


RESCORE_HELP = (
    "Score every unexpired listing again, replacing existing scores "
    "(e.g. after editing profile/ or preferences; one Claude call per batch)"
)


@app.command()
def score(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the first prompt and stop"),
    rescore: bool = typer.Option(False, "--rescore", help=RESCORE_HELP),
) -> None:
    """Score unscored listings with Claude (`claude -p`)."""
    _workspace(ctx).score(rescore=rescore, dry_run=dry_run, progress=typer.echo)


@app.command()
def digest(ctx: typer.Context) -> None:
    """Write a ranked digest of unrated listings to digests/."""
    _workspace(ctx).digest(progress=typer.echo)


@app.command()
def shortlist(ctx: typer.Context) -> None:
    """Print the listings you rated 4-5 that are still open, and write shortlist.md."""
    ws = _workspace(ctx)
    typer.echo(ws.shortlist())
    typer.echo(f"shortlist: {ws.paths.shortlist}")


@app.command()
def check(
    ctx: typer.Context,
    source: str | None = typer.Option(None, help="Only this source"),
    fixture: Path | None = typer.Option(None, help="Load listings from a JSON fixture instead"),
    no_score: bool = typer.Option(False, "--no-score", help="Skip Claude scoring"),
    rescore: bool = typer.Option(False, "--rescore", help=RESCORE_HELP),
) -> None:
    """fetch → score → digest, in one go."""
    _workspace(ctx).check(source, fixture, no_score=no_score, rescore=rescore, progress=typer.echo)


@app.command()
def rate(
    ctx: typer.Context,
    digest_file: Path | None = typer.Argument(None, help="Digest to read (default: newest)"),
    no_learn: bool = typer.Option(False, "--no-learn", help="Skip preferences regeneration"),
    force: bool = typer.Option(False, "--force", help="Regenerate preferences even if few new"),
    rebuild: bool = typer.Option(False, help="Replay data/ratings.jsonl into the store first"),
) -> None:
    """Ingest the ratings you wrote into a digest."""
    ws = _workspace(ctx)
    store = ws.store
    if rebuild:
        n = rebuild_from_jsonl(store, ws.paths.ratings)
        typer.echo(f"rebuilt {n} rating record(s) from {ws.paths.ratings}")
    if digest_file is None:
        digest_file = newest_digest(ws.paths.digests)
        if digest_file is None:
            typer.echo("no digests found", err=True)
            raise typer.Exit(1)
    result = ingest_ratings(store, digest_file, ws.paths.ratings)
    for err in result.errors:
        typer.echo(f"  ! {digest_file.name}: {err}", err=True)
    typer.echo(f"{result.added} rating(s) ingested from {digest_file.name}")
    ws.shortlist()
    if no_learn:
        return
    if result.added < MIN_RATINGS_TO_LEARN and not force:
        typer.echo(
            f"preferences: skipped (fewer than {MIN_RATINGS_TO_LEARN} new ratings; "
            "use --force to regenerate anyway)"
        )
        return
    ws.learn(progress=typer.echo)


@app.command()
def sources(ctx: typer.Context) -> None:
    """List known sources and whether they are enabled."""
    enabled = set(_workspace(ctx).config.enabled_sources())
    for name in list_sources():
        typer.echo(f"{name:20s} {'enabled' if name in enabled else 'disabled'}")
