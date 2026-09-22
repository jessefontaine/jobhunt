"""The browser UI: routes over a Workspace, rendered with Jinja2."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobhunt import pipeline
from jobhunt.config import ConfigError, parse_config
from jobhunt.digest import newest_digest
from jobhunt.feedback import issue_url
from jobhunt.models import Listing, Rating, Score
from jobhunt.ratings import learned_at, record_rating
from jobhunt.settings import DisplaySettings, save_settings, settings_from_form
from jobhunt.sources import list_sources
from jobhunt.sources.manual import ManualFetchError
from jobhunt.update import EngineInstall, Updater
from jobhunt.web.jobs import JobBusy, JobRunner, Progress
from jobhunt.workspace import Workspace

HERE = Path(__file__).parent

RATING_LABELS = {5: "apply", 4: "strong", 3: "maybe", 2: "not really", 1: "irrelevant"}
RESCORE_HINT = (
    "Saved. Existing scores were computed with the old text — run Check with "
    "“rescore everything open” on the dashboard to refresh them."
)


def _item(
    lst: Listing,
    score: Score | None,
    rating: Rating | None,
    today: date,
    display: DisplaySettings | None = None,
) -> dict:
    """One listing card's worth of template context."""
    display = display or DisplaySettings()
    return {
        "listing": lst,
        "score": score,
        "rating": rating,
        "expired": lst.is_expired(today),
        "soon": display.closing_soon(lst.deadline, today),
        "days": (lst.deadline - today).days if lst.deadline else None,
    }


def create_app(
    ws: Workspace, jobs: JobRunner | None = None, updater: Updater | None = None
) -> FastAPI:
    app = FastAPI(title="jobhunt", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    jobs = jobs or JobRunner()
    updater = updater or Updater(EngineInstall.detect(), ws.paths.root)
    boot = uuid.uuid4().hex  # changes when the process is replaced after an update

    def render(request: Request, template: str, status_code: int = 200, **context):
        context.setdefault("error", None)
        context.setdefault("notice", None)
        # every page: the update banner, the theme, and whether the button may be pressed
        context["update"] = updater.available
        context["version"] = updater.version
        context["settings"] = ws.settings
        context["busy"] = bool(jobs.current and jobs.current.running)
        return templates.TemplateResponse(request, template, context, status_code=status_code)

    def stats() -> dict:
        store = ws.store
        today = date.today()
        candidates = store.candidate_listings(today)
        scores = store.get_scores([lst.id for lst in candidates])
        learned = learned_at(store)
        digest = newest_digest(ws.paths.digests)
        return {
            "open": len(candidates),
            "unscored": sum(1 for lst in candidates if lst.id not in scores),
            "shortlist": len(store.shortlist(today, ws.settings.shortlist.min_rating)),
            "listings": store.count_listings(),
            "ratings": len(store.rated_ids()),
            "since_learned": store.ratings_since(learned),
            "learned_at": learned,
            "newest_digest": digest.name if digest else None,
            "claude_cli": shutil.which("claude") is not None,
        }

    def dashboard_context(error: str | None = None) -> dict:
        return {
            "stats": stats(),
            "job": jobs.current,
            "sources": ws.config.enabled_sources(),
            "error": error,
            "boot": boot,
            "changelog": updater.changelog,
            "install": updater.install,
            "status": updater.status(),
            "feedback": {
                kind: issue_url(kind, updater.install, updater.version)
                for kind in ("bug", "feature")
            },
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        return render(request, "dashboard.html", **dashboard_context())

    # -- actions: each starts one background job and sends the user back to the dashboard --

    def start(request: Request, name: str, fn: Callable[[Progress], object]):
        try:
            jobs.start(name, fn)
        except JobBusy as exc:
            ctx = dashboard_context(error=f"a job is already running ({exc})")
            return render(request, "dashboard.html", status_code=409, **ctx)
        return RedirectResponse("/", status_code=303)

    @app.post("/actions/check")
    def action_check(
        request: Request,
        source: str = Form(""),
        no_score: bool = Form(False),
        rescore: bool = Form(False),
    ):
        return start(
            request,
            "check",
            lambda progress: ws.check(
                source or None, no_score=no_score, rescore=rescore, progress=progress
            ),
        )

    @app.post("/actions/fetch")
    def action_fetch(request: Request, source: str = Form("")):
        return start(request, "fetch", lambda progress: ws.fetch(source or None, progress=progress))

    @app.post("/actions/score")
    def action_score(request: Request, rescore: bool = Form(False)):
        return start(
            request, "score", lambda progress: ws.score(rescore=rescore, progress=progress)
        )

    @app.post("/actions/digest")
    def action_digest(request: Request):
        return start(request, "digest", lambda progress: ws.digest(progress=progress))

    @app.post("/actions/learn")
    def action_learn(request: Request):
        return start(request, "learn", ws.learn)

    @app.post("/actions/update")
    def action_update(request: Request):
        return start(request, "update", updater.update)

    @app.post("/actions/update-check")
    def action_update_check(request: Request):
        def run(progress: Progress) -> None:
            if reason := updater.disabled_reason():
                progress(f"no update check: {reason}")
                return
            status = updater.status()
            progress(f"checking {status.tracking}…")
            available = updater.check()
            if error := updater.status().error:
                progress(f"check failed: {error}")
            elif available is None:
                progress(f"up to date: jobhunt {updater.version}")
            else:
                progress(f"available: jobhunt {available.version} ({available.commit[:7]})")

        return start(request, "update check", run)

    @app.post("/actions/add")
    def action_add(
        request: Request,
        url: str = Form(""),
        title: str = Form(""),
        employer: str = Form(""),
        no_score: bool = Form(False),
    ):
        if not url.strip():
            ctx = dashboard_context(error="paste a link to a vacancy page first")
            return render(request, "dashboard.html", status_code=400, **ctx)

        def run(progress: Progress) -> None:
            try:
                _, score = ws.add(
                    url.strip(),
                    title=title.strip(),
                    employer=employer.strip(),
                    score=not no_score,
                    progress=progress,
                )
            except ManualFetchError as exc:
                raise RuntimeError(str(exc)) from None
            if score is not None:
                progress(f"score {score.score} ({score.role_type}) — {score.why}")

        return start(request, "add", run)

    @app.get("/health")
    def health():
        return {"version": updater.version, "boot": boot}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: int):
        job = jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return job.as_dict()

    # -- listing pages -------------------------------------------------------

    def listing_page(
        request: Request, title: str, mode: str, sections: list, hidden: int = 0
    ) -> HTMLResponse:
        total = sum(len(items) for _, items in sections)
        return render(
            request,
            "listings.html",
            title=title,
            mode=mode,
            sections=sections,
            total=total,
            hidden=hidden,
            labels=RATING_LABELS,
        )

    def _sorted(listings: list[Listing], scores: dict[str, Score]) -> list[Listing]:
        """Order the scored part of the queue the way the settings ask for."""
        far = date.max
        match ws.settings.display.sort:
            case "deadline":
                return sorted(listings, key=lambda lst: lst.deadline or far)
            case "newest":
                return sorted(listings, key=lambda lst: lst.fetched_at, reverse=True)
            case _:
                return sorted(listings, key=lambda lst: scores[lst.id].score, reverse=True)

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request):
        store, today = ws.store, date.today()
        display = ws.settings.display
        candidates = store.candidate_listings(today)
        scores = store.get_scores([lst.id for lst in candidates])
        listings = [
            lst
            for lst in candidates
            if display.in_range(scores[lst.id].score if lst.id in scores else None)
        ]
        scored = _sorted([lst for lst in listings if lst.id in scores], scores)
        unscored = [lst for lst in listings if lst.id not in scores]
        sections = [
            ("", [_item(lst, scores[lst.id], None, today, display) for lst in scored]),
            ("Unscored", [_item(lst, None, None, today, display) for lst in unscored]),
        ]
        return listing_page(
            request, "Queue", "queue", sections, hidden=len(candidates) - len(listings)
        )

    @app.get("/shortlist", response_class=HTMLResponse)
    def shortlist(request: Request):
        store, today = ws.store, date.today()
        min_rating = ws.settings.shortlist.min_rating
        rows = store.shortlist(today, min_rating)
        pipeline.write_shortlist(store, ws.paths.shortlist, today, min_rating)
        scores = store.get_scores([lst.id for lst, _ in rows])
        items = [
            _item(lst, scores.get(lst.id), rating, today, ws.settings.display)
            for lst, rating in rows
        ]
        return listing_page(request, "Shortlist", "shortlist", [("", items)])

    @app.get("/rated", response_class=HTMLResponse)
    def rated(request: Request, all: bool = False):
        store, today = ws.store, date.today()
        now = datetime.now()
        rules = ws.settings.rated
        rows = store.all_ratings()
        kept = [
            (lst, rating)
            for lst, rating in rows
            if all or not rules.hidden(rating.rating, rating.rated_at, now)
        ]
        scores = store.get_scores([lst.id for lst, _ in kept])
        items = [
            _item(lst, scores.get(lst.id), rating, today, ws.settings.display)
            for lst, rating in kept
        ]
        return listing_page(
            request, "Rated", "rated", [("", items)], hidden=len(rows) - len(kept)
        )

    @app.post("/ratings")
    def post_rating(
        listing_id: str = Form(...),
        rating: int = Form(..., ge=1, le=5),
        note: str = Form(""),
    ):
        store = ws.store
        if store.get_listing(listing_id) is None:
            return JSONResponse({"error": "no such listing"}, status_code=404)
        note = note.strip()
        saved = record_rating(store, ws.paths.ratings, listing_id, rating, note, digest="web")
        pipeline.write_shortlist(
            store, ws.paths.shortlist, date.today(), ws.settings.shortlist.min_rating
        )
        return {
            "listing_id": listing_id,
            "rating": rating,
            "note": note,
            "changed": saved is not None,
            "since_learned": store.ratings_since(learned_at(store)),
        }

    # -- settings ------------------------------------------------------------

    THEMES = ("auto", "light", "dark")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, saved: bool = False):
        return render(
            request,
            "settings.html",
            themes=THEMES,
            sorts=("score", "deadline", "newest"),
            notice="Saved." if saved else None,
        )

    @app.post("/settings")
    async def save_settings_page(request: Request):
        form = await request.form()
        try:
            updated = settings_from_form(form, ws.settings)
        except ConfigError as exc:
            return render(
                request,
                "settings.html",
                status_code=400,
                themes=THEMES,
                sorts=("score", "deadline", "newest"),
                error=f"not saved: {exc}",
            )
        save_settings(ws.paths, updated)
        ws.settings = updated
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.post("/settings/theme")
    async def toggle_theme(request: Request, next: str = Form("/")):
        """The header toggle: auto → dark → light → auto, then back where you were."""
        order = {"auto": "dark", "dark": "light", "light": "auto"}
        ws.settings.display.theme = order[ws.settings.display.theme]
        save_settings(ws.paths, ws.settings)
        return RedirectResponse(next or "/", status_code=303)

    # -- file editors: only these four workspace files, nothing else ----------

    files = {
        "profile": ws.paths.profile,
        "preferences": ws.paths.preferences,
        "cv": ws.paths.cv,
        "sources": ws.paths.sources_yaml,
    }

    def file_path(name: str) -> Path:
        try:
            return files[name]
        except KeyError:
            raise HTTPException(404, "no such file") from None

    def source_table() -> list[tuple[str, bool]]:
        enabled = set(ws.config.enabled_sources())
        return [(name, name in enabled) for name in list_sources()]

    def editor(request: Request, name: str, text: str, status_code: int = 200, **flash):
        return render(
            request,
            "editor.html",
            status_code=status_code,
            name=name,
            path=file_path(name).relative_to(ws.paths.root),
            text=text,
            sources=source_table() if name == "sources" else None,
            **flash,
        )

    @app.get("/files/{name}", response_class=HTMLResponse)
    def edit_file(request: Request, name: str, saved: bool = False):
        path = file_path(name)
        text = path.read_text() if path.exists() else ""
        notice = None
        if saved:
            notice = "Saved." if name == "sources" else RESCORE_HINT
        return editor(request, name, text, notice=notice)

    @app.post("/files/{name}")
    def save_file(request: Request, name: str, text: str = Form("")):
        path = file_path(name)
        text = text.replace("\r\n", "\n")
        if not text.endswith("\n"):
            text += "\n"
        if name == "sources":
            try:
                ws.config = parse_config(text)
            except ConfigError as exc:
                return editor(request, name, text, status_code=400, error=f"not saved: {exc}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return RedirectResponse(f"/files/{name}?saved=1", status_code=303)

    return app
