"""The browser UI: routes over a Workspace, rendered with Jinja2."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobhunt.digest import newest_digest
from jobhunt.ratings import learned_at
from jobhunt.web.jobs import JobBusy, JobRunner, Progress
from jobhunt.workspace import Workspace

HERE = Path(__file__).parent


def create_app(ws: Workspace, jobs: JobRunner | None = None) -> FastAPI:
    app = FastAPI(title="jobhunt", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    jobs = jobs or JobRunner()

    def render(request: Request, name: str, status_code: int = 200, **context):
        context.setdefault("error", None)
        context.setdefault("notice", None)
        return templates.TemplateResponse(request, name, context, status_code=status_code)

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
            "shortlist": len(store.shortlist(today)),
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

    @app.get("/jobs/{job_id}")
    def job_status(job_id: int):
        job = jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return job.as_dict()

    return app
