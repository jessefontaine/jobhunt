"""The browser UI: routes over a Workspace, rendered with Jinja2."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jobhunt.digest import newest_digest
from jobhunt.ratings import learned_at
from jobhunt.web.jobs import JobRunner
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

    return app
