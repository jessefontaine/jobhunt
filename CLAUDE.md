# jobhunt engine — notes for Claude

Public engine for a personal job-hunt pipeline: scrape Dutch research/PhD listings, score them
against a user's profile with `claude -p`, write digests, learn from ratings. Read
[README.md](README.md) for the workflow and `docs/superpowers/specs/` for the design decisions.

- This checkout is **not a workspace**: `jobhunt check` etc. need a directory containing
  `config/sources.yaml` (make one with `uv run jobhunt init DIR`). Workspaces hold personal data
  and are never committed here.
- Sources are one file each in `src/jobhunt/sources/`; parsers are pure functions tested against
  saved HTML in `tests/fixtures/`. Re-save a fixture when a site changes markup.
- Workspace templates live in `src/jobhunt/templates/` as `*.tmpl`; `jobhunt init` copies them.
- The browser UI is `src/jobhunt/web/` (FastAPI + Jinja2, tested with `TestClient` in
  `tests/test_web.py`). Its `templates/` are web pages, not workspace scaffolding — don't mix
  them up. CLI and UI both drive `jobhunt.workspace.Workspace`; add pipeline behaviour there.
- Tests: `uv run pytest`; lint: `uv run ruff check src tests`. TDD: test first.
- Never work around scraper blocks (403/CAPTCHA/sign-in walls) — surface a manual link instead.
- Scoring and preference learning go through the shell `claude` CLI; if it reports
  `OAuth session expired`, the user has to run `claude login`.
- Every PR bumps `version` in `pyproject.toml` and adds a matching `## x.y.z — YYYY-MM-DD`
  entry at the top of `src/jobhunt/CHANGELOG.md` (`tests/test_update.py` checks they agree).
  The UI's update banner and dashboard changelog are built from that file.
