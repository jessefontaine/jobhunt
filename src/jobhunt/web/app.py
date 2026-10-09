"""The browser UI: routes over a Workspace, rendered with Jinja2."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobhunt import pipeline
from jobhunt.applications import read_letter, write_letter
from jobhunt.calibration import ENOUGH_RATINGS, verdict
from jobhunt.config import ConfigError, parse_config
from jobhunt.crossval import NotEnoughRatings, TooExpensive, last_run
from jobhunt.digest import newest_digest
from jobhunt.feedback import issue_url
from jobhunt.filters import Filters, Query, facets
from jobhunt.models import STATUSES, Listing, Rating, Score, StatusEvent
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


class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser must revalidate on every load (a 304 via the ETag).

    Without a Cache-Control header browsers cache heuristically, for hours after an old
    install, so an update's new pages would run against the previous app.js.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _item(
    lst: Listing,
    score: Score | None,
    rating: Rating | None,
    today: date,
    display: DisplaySettings | None = None,
    status: StatusEvent | None = None,
) -> dict:
    """One listing card's worth of template context."""
    display = display or DisplaySettings()
    return {
        "listing": lst,
        "score": score,
        "rating": rating,
        "status": status,
        "expired": lst.is_expired(today),
        "soon": display.closing_soon(lst.deadline, today),
        "days": (lst.deadline - today).days if lst.deadline else None,
    }


def create_app(
    ws: Workspace, jobs: JobRunner | None = None, updater: Updater | None = None
) -> FastAPI:
    app = FastAPI(title="jobhunt", docs_url=None, redoc_url=None)
    app.mount("/static", RevalidatedStaticFiles(directory=HERE / "static"), name="static")
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
            "applications": len(store.applications()),
            "discarded": len(store.discarded()),
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

    def start(
        request: Request,
        name: str,
        fn: Callable[..., object],
        back: str = "/",
        cancellable: bool = False,
    ):
        try:
            jobs.start(name, fn, cancellable=cancellable)
        except JobBusy as exc:
            ctx = dashboard_context(error=f"a job is already running ({exc})")
            return render(request, "dashboard.html", status_code=409, **ctx)
        return RedirectResponse(back, status_code=303)

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

    @app.post("/actions/cross-validate")
    def action_cross_validate(request: Request, force: bool = Form(False)):
        """The expensive one: many Claude calls, so the page prices it before offering it."""

        def run(progress: Progress, should_stop) -> None:
            result = ws.cross_validate(progress=progress, should_stop=should_stop, force=force)
            progress(result.reason)

        return start(
            request, "cross-validate", run, back="/calibration", cancellable=True
        )

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

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: int):
        """Ask a running job to stop. Already-finished jobs ignore it, which is not an error."""
        job = jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "no such job"}, status_code=404)
        job.cancel()
        return RedirectResponse("/calibration", status_code=303)

    # -- listing pages -------------------------------------------------------

    def listing_page(
        request: Request,
        title: str,
        mode: str,
        sections: list,
        hidden: int = 0,
        bar: dict | None = None,
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
            bar=bar,
            labels=RATING_LABELS,
            statuses=STATUSES,
        )

    def filter_bar(
        path: str,
        query: Query,
        default: Filters,
        rows: list,
        keep: dict[str, str] | None = None,
    ) -> tuple[Filters, dict]:
        """The view's filters from its query (or the bulk form), and what the bar shows.

        `rows` are (listing, score, rating) before filtering: the bar offers what is there.
        `keep` is query state that is not a filter (Rated's `all`), carried through Apply and
        Reset alike.
        """
        found = facets(rows)
        chosen = Filters.from_query(query, default)
        chosen = chosen.within([name for name, _ in found.sources])
        keep = keep or {}
        reset = path
        if keep:
            reset += "?" + "&".join(f"{k}={v}" for k, v in keep.items())
        return chosen, {
            "filters": chosen,
            "facets": found,
            "active": chosen != default,
            "default": default,
            "keep": keep,
            "reset": reset,
        }

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

    def queue_view(query: Query) -> tuple[list, dict]:
        """The queue's sections and filter bar for `query`; `bar["ids"]` is what it shows."""
        store, today = ws.store, date.today()
        display = ws.settings.display
        candidates = store.candidate_listings(today)
        scores = store.get_scores([lst.id for lst in candidates])
        rows = [(lst, scores.get(lst.id), None) for lst in candidates]
        default = Filters(min_score=display.min_score, max_score=display.max_score)
        filters, bar = filter_bar("/queue", query, default, rows)
        listings = [lst for lst, score, _ in rows if filters.keeps(lst, score)]
        scored = _sorted([lst for lst in listings if lst.id in scores], scores)
        unscored = [lst for lst in listings if lst.id not in scores]
        sections = [
            ("", [_item(lst, scores[lst.id], None, today, display) for lst in scored]),
            ("Unscored", [_item(lst, None, None, today, display) for lst in unscored]),
        ]
        bar["filtered_out"] = len(candidates) - len(listings)
        bar["hidden"] = bar["filtered_out"] if bar["active"] else 0
        bar["ids"] = [lst.id for lst in scored + unscored]
        return sections, bar

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request):
        sections, bar = queue_view(request.query_params)
        return listing_page(
            request, "Queue", "queue", sections, hidden=bar["filtered_out"], bar=bar
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

    @app.get("/calibration", response_class=HTMLResponse)
    def calibration_page(request: Request):
        today = date.today()
        result = ws.calibration()
        items = [
            _item(d.listing, d.score, d.rating, today, ws.settings.display)
            | {"surprise": d.surprise, "over_scored": d.over_scored}
            for d in ws.disagreements(limit=8)
        ]
        priced, refusal = None, ""
        try:
            priced = ws.plan_cross_validation()
        except (NotEnoughRatings, TooExpensive) as exc:
            refusal = str(exc)
        return render(
            request,
            "calibration.html",
            result=result,
            verdict=verdict(result.rho),
            items=items,
            labels=RATING_LABELS,
            enough=ENOUGH_RATINGS,
            plan=priced,
            refusal=refusal,
            last=last_run(ws.store),
            job=jobs.current,
        )

    def rated_view(query: Query, all: bool) -> tuple[list, dict, int]:
        """Rated's cards and filter bar for `query`, and how many the age rule hides."""
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
        filters, bar = filter_bar(
            "/rated",
            query,
            Filters(),
            [(lst, scores.get(lst.id), rating) for lst, rating in kept],
            keep={"all": "1"} if all else None,
        )
        shown = [(lst, r) for lst, r in kept if filters.keeps(lst, scores.get(lst.id), r)]
        statuses = store.get_statuses([lst.id for lst, _ in shown])
        items = [
            _item(lst, scores.get(lst.id), rating, today, ws.settings.display, statuses.get(lst.id))
            for lst, rating in shown
        ]
        bar["hidden"] = len(kept) - len(shown)
        bar["ids"] = [lst.id for lst, _ in shown]
        return [("", items)], bar, len(rows) - len(kept)

    @app.get("/rated", response_class=HTMLResponse)
    def rated(request: Request, all: bool = False):
        sections, bar, hidden = rated_view(request.query_params, all)
        return listing_page(request, "Rated", "rated", sections, hidden=hidden, bar=bar)

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

    # -- discard pile --------------------------------------------------------

    @app.post("/discard")
    def post_discard(listing_id: str = Form(...), reason: str = Form("")):
        """JSON for the card's script."""
        if ws.store.get_listing(listing_id) is None:
            return JSONResponse({"error": "no such listing"}, status_code=404)
        event = ws.discard(listing_id, reason.strip())
        return {"listing_id": listing_id, "discarded": event.discarded}

    @app.post("/discard/bulk")
    async def discard_bulk(request: Request):
        """Discard exactly what a filtered Queue or Rated view shows, then go back to it.

        The view is rebuilt from the posted filters rather than trusting a list of ids, so
        what goes is what the page would show now. An unfiltered view is refused: emptying
        the whole queue is never one click.
        """
        form = await request.form()
        view = form.get("view")
        if view == "queue":
            _, bar = queue_view(form)
        elif view == "rated":
            _, bar, _ = rated_view(form, all=form.get("all") == "1")
        else:
            raise HTTPException(404, "no such view")
        if not bar["active"]:
            raise HTTPException(400, "set a filter first: bulk discard takes what it shows")
        ws.discard_many(bar["ids"], str(form.get("reason", "")).strip())
        params = [(k, v) for k, v in form.multi_items() if k not in ("view", "reason")]
        back = f"/{view}" + (f"?{urlencode(params)}" if params else "")
        return RedirectResponse(back, status_code=303)

    @app.get("/discarded", response_class=HTMLResponse)
    def discarded(request: Request):
        store, today = ws.store, date.today()
        rows = store.discarded()
        ids = [lst.id for lst, _ in rows]
        scores = store.get_scores(ids)
        items = [
            _item(lst, scores.get(lst.id), store.get_rating(lst.id), today, ws.settings.display)
            | {"discard": event}
            for lst, event in rows
        ]
        return listing_page(request, "Discarded", "discarded", [("", items)])

    @app.post("/restore")
    def post_restore(listing_id: str = Form(...), next: str = Form("/discarded")):
        current = ws.store.get_discard(listing_id)
        if current is None or not current.discarded:
            raise HTTPException(404, "that listing is not discarded")
        ws.restore(listing_id)
        local = next.startswith("/") and not next.startswith("//")
        return RedirectResponse(next if local else "/discarded", status_code=303)

    # -- applications --------------------------------------------------------

    @app.post("/status")
    def post_status(
        listing_id: str = Form(...),
        status: str = Form(..., pattern="^(" + "|".join(STATUSES) + ")$"),
        next: str = Form(""),
    ):
        """JSON for the card's script; a redirect back to `next` for a plain form submit."""
        store = ws.store
        if store.get_listing(listing_id) is None:
            return JSONResponse({"error": "no such listing"}, status_code=404)
        current = store.get_status(listing_id)
        if (current.status if current else "none") == status:
            event, changed = current, False
        else:
            event, changed = ws.set_status(listing_id, status), True
        if next:
            local = next.startswith("/") and not next.startswith("//")
            return RedirectResponse(next if local else "/", status_code=303)
        return {
            "listing_id": listing_id,
            "status": status,
            "on": event.on.isoformat() if event else None,
            "changed": changed,
        }

    @app.get("/applied", response_class=HTMLResponse)
    def applied(request: Request, status: str = "", saved: bool = False):
        store, today = ws.store, date.today()
        rows = store.applications()
        counts = {s: sum(1 for _, ev in rows if ev.status == s) for s in STATUSES[1:]}
        kept = [(lst, ev) for lst, ev in rows if not status or ev.status == status]
        ids = [lst.id for lst, _ in kept]
        scores = store.get_scores(ids)
        ratings = {lst.id: store.get_rating(lst.id) for lst, _ in kept}
        items = [
            _item(lst, scores.get(lst.id), ratings[lst.id], today, ws.settings.display, ev)
            | {"letter": read_letter(ws.paths, lst.id)}
            for lst, ev in kept
        ]
        return render(
            request,
            "applied.html",
            items=items,
            counts=counts,
            total=len(rows),
            only=status,
            labels=RATING_LABELS,
            statuses=STATUSES,
            notice="Saved." if saved else None,
        )

    @app.post("/applied/{listing_id}")
    def save_application(
        listing_id: str, on: str = Form(""), note: str = Form(""), letter: str = Form("")
    ):
        current = ws.store.get_status(listing_id)
        if current is None or current.status == "none":
            raise HTTPException(404, "no application for that listing")
        try:
            when = date.fromisoformat(on) if on else current.on
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD") from None
        note = note.strip()
        if (when, note) != (current.on, current.note):
            ws.set_status(listing_id, current.status, when, note, letter=letter)
        else:
            write_letter(ws.paths, listing_id, letter)
        return RedirectResponse(f"/applied?saved=1#{listing_id}", status_code=303)

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
