"""`jobhunt` command line: fetch → score → digest → rate."""

from __future__ import annotations

from pathlib import Path

import typer

from jobhunt.calibration import render
from jobhunt.config import Paths, find_root
from jobhunt.crossval import (
    LearnFailed,
    NotEnoughRatings,
    TooExpensive,
    last_run,
    render_last,
    render_plan,
)
from jobhunt.crossval import render as render_crossval
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
  4. cd {dir} && uv run jobhunt serve      (browser UI: run, rate, edit profile)
     or uv run jobhunt check, then rate in the digest / open the folder in Claude Code
     and type /jobhunt
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
    "Score every unexpired, unrated listing again, replacing existing scores "
    "(e.g. after editing profile/ or preferences; one Claude call per batch)"
)
INCLUDE_RATED_HELP = "With --rescore, also re-score listings you already rated"


@app.command()
def score(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the first prompt and stop"),
    rescore: bool = typer.Option(False, "--rescore", help=RESCORE_HELP),
    include_rated: bool = typer.Option(False, "--include-rated", help=INCLUDE_RATED_HELP),
) -> None:
    """Score unscored listings with Claude (`claude -p`)."""
    _workspace(ctx).score(
        rescore=rescore, dry_run=dry_run, include_rated=include_rated, progress=typer.echo
    )


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
def calibration(ctx: typer.Context) -> None:
    """Check Claude's scores against your ratings: rank correlation and a mean per band."""
    ws = _workspace(ctx)
    typer.echo(render(ws.calibration()))
    if line := render_last(last_run(ws.store)):
        typer.echo("")
        typer.echo(line)


@app.command()
def check(
    ctx: typer.Context,
    source: str | None = typer.Option(None, help="Only this source"),
    fixture: Path | None = typer.Option(None, help="Load listings from a JSON fixture instead"),
    no_score: bool = typer.Option(False, "--no-score", help="Skip Claude scoring"),
    rescore: bool = typer.Option(False, "--rescore", help=RESCORE_HELP),
    include_rated: bool = typer.Option(False, "--include-rated", help=INCLUDE_RATED_HELP),
) -> None:
    """fetch → score → digest, in one go."""
    _workspace(ctx).check(
        source,
        fixture,
        no_score=no_score,
        rescore=rescore,
        include_rated=include_rated,
        progress=typer.echo,
    )


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
def add(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="Link to a vacancy page"),
    title: str = typer.Option("", help="Skip fetching and use this title"),
    employer: str = typer.Option("", help="Employer (default: the site name or domain)"),
    description: str = typer.Option("", help="Description text, when the page cannot be read"),
    no_score: bool = typer.Option(False, "--no-score", help="Store it without scoring it"),
) -> None:
    """Add a listing from a link, so it is scored and ranked like the fetched ones."""
    from jobhunt.sources.manual import ManualFetchError

    ws = _workspace(ctx)
    try:
        listing, score = ws.add(
            url,
            title=title,
            employer=employer,
            description=description,
            score=not no_score,
            progress=typer.echo,
        )
    except ManualFetchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    if score is not None:
        typer.echo(f"score {score.score} ({score.role_type}) — {score.why}")


def _show_listing(listing, score, rating) -> str:
    lines = [f"{listing.title} — {listing.employer}", listing.url]
    meta = [f"source {listing.source}", f"id {listing.id}"]
    if listing.location:
        meta.append(listing.location)
    if listing.deadline:
        meta.append(f"deadline {listing.deadline.isoformat()}")
    lines.append(" · ".join(meta))
    if score is not None:
        lines.append(f"score {score.score} ({score.role_type}) — {score.why}")
        if score.concerns:
            lines.append(f"concerns: {score.concerns}")
    else:
        lines.append("not scored yet")
    if rating is not None:
        note = f" — {rating.note}" if rating.note else ""
        lines.append(f"rated {rating.rating}/5{note}")
    body = listing.description or listing.summary
    if body:
        lines += ["", body[:1000]]
    return "\n".join(lines)


@app.command()
def show(
    ctx: typer.Context,
    listing: str = typer.Argument(..., help="Listing URL or id"),
) -> None:
    """Print what the store knows about one listing: its score, why, and your rating."""
    ws = _workspace(ctx)
    store = ws.store
    found = store.get_listing(listing) or store.find_by_url(listing)
    if found is None:
        typer.echo(f"{listing} is not in the store (add it with: jobhunt add URL)", err=True)
        raise typer.Exit(1)
    typer.echo(_show_listing(found, store.get_score(found.id), store.get_rating(found.id)))


@app.command()
def learn(
    ctx: typer.Context,
    cross_validate: bool = typer.Option(
        False,
        "--cross-validate",
        "-x",
        help="Measure the new rules on held-out ratings first, and write them only if they win",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what a cross-validation would cost and stop"
    ),
    force: bool = typer.Option(
        False, "--force", help="Write the new rules even when the check rejects them"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Regenerate the learned preferences from every rating (one Claude call).

    With --cross-validate the rewrite is measured against ratings it was not allowed to see,
    and written only if the out-of-sample correlation actually improves. That costs many
    Claude calls, so the run is priced and confirmed first.
    """
    ws = _workspace(ctx)
    if not cross_validate:
        if not ws.learn(progress=typer.echo):
            raise typer.Exit(1)
        return
    try:
        priced = ws.plan_cross_validation()
    except (NotEnoughRatings, TooExpensive) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(render_plan(priced))
    if dry_run:
        return
    if not yes:
        typer.confirm("Run it?", abort=True)
    try:
        result = ws.cross_validate(progress=typer.echo, force=force)
    except LearnFailed as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo("")
    typer.echo(render_crossval(result))


@app.command()
def update(
    ctx: typer.Context,
    check: bool = typer.Option(False, "--check", help="Report what an update would do and stop"),
) -> None:
    """Update the engine to the newest commit on GitHub, or say why it cannot be checked."""
    from jobhunt.update import EngineInstall, UpdateError, Updater

    ws = _workspace(ctx)
    # No restart: a CLI run just exits when uv sync is done (the server needs one, we do not).
    updater = Updater(EngineInstall.detect(), ws.paths.root, restart=lambda: None)
    status = updater.status()
    commit = f" ({status.commit[:7]})" if status.commit else ""
    typer.echo(f"installed: jobhunt {status.version}{commit}")
    if status.reason:
        typer.echo(f"no update check: {status.reason}")
        raise typer.Exit(0 if check else 1)
    typer.echo(f"tracking:  {status.tracking}")
    available = updater.check()
    if error := updater.status().error:
        typer.echo(f"check failed: {error}", err=True)
        raise typer.Exit(1)
    if available is None:
        typer.echo("up to date")
        return
    typer.echo(f"available: jobhunt {available.version} ({available.commit[:7]})")
    for entry in available.entries:
        typer.echo(f"  {entry.version} — {entry.date}")
        for note in entry.notes:
            typer.echo(f"    - {note}")
    if check:
        typer.echo("run `jobhunt update` to install it")
        return
    try:
        updater.update(progress=typer.echo)
    except UpdateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


@app.command()
def sources(ctx: typer.Context) -> None:
    """List known sources and whether they are enabled."""
    enabled = set(_workspace(ctx).config.enabled_sources())
    for name in list_sources():
        typer.echo(f"{name:20s} {'enabled' if name in enabled else 'disabled'}")


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option(
        "127.0.0.1", help="Bind address (0.0.0.0 exposes the UI, and file editing, to your network)"
    ),
    port: int = typer.Option(8765, help="Port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the UI in a browser"),
) -> None:
    """Run the browser UI: buttons for every command, rating, shortlist, profile editor."""
    import threading
    import webbrowser

    import uvicorn

    from jobhunt.update import EngineInstall, Updater
    from jobhunt.web.app import create_app

    ws = _workspace(ctx)
    updates = ws.settings.updates
    updater = Updater(
        EngineInstall.detect(), ws.paths.root, interval=updates.interval_minutes * 60
    )
    if updates.check:  # `git ls-remote` on a timer; the UI shows a banner when the engine moved
        updater.start()
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    typer.echo(f"jobhunt UI: {url} (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(ws, updater=updater), host=host, port=port, log_level="warning")
