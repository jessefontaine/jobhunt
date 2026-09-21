# jobhunt engine — notes for Claude

Public engine for a personal job-hunt pipeline: scrape Dutch research/PhD listings, score them
against a user's profile with `claude -p`, write digests, learn from ratings. Read
[README.md](README.md) for the workflow and `docs/superpowers/specs/` for the design decisions
(`docs/superpowers/plans/` has the implementation plans that followed them).

- This checkout is **not a workspace**: `jobhunt check` etc. need a directory containing
  `config/sources.yaml` (make one with `uv run jobhunt init DIR`). Workspaces hold personal data
  and are never committed here. To run a workspace against this checkout, add
  `[tool.uv.sources] jobhunt = { path = "…/jobhunt", editable = true }` to its `pyproject.toml`
  and `uv sync`; `jobhunt --root DIR …` runs any command against it from elsewhere.
- Sources are one file each in `src/jobhunt/sources/`; parsers are pure functions tested against
  saved HTML in `tests/fixtures/`. Re-save a fixture when a site changes markup.
- Workspace templates live in `src/jobhunt/templates/` as `*.tmpl`; `jobhunt init` copies them
  verbatim (only `pyproject.toml.tmpl` has a placeholder, `{engine_url}`). `CLAUDE.md.tmpl` and
  `.claude/skills/jobhunt/SKILL.md.tmpl` are what the *user's* Claude reads in their workspace.
- The browser UI is `src/jobhunt/web/` (FastAPI + Jinja2, tested with `TestClient` in
  `tests/test_web.py`). Its `templates/` are web pages, not workspace scaffolding — don't mix
  them up. CLI and UI both drive `jobhunt.workspace.Workspace`; add pipeline behaviour there.
- Tests: `uv run pytest`; lint: `uv run ruff check src tests`. TDD: test first.
- Never work around scraper blocks (403/CAPTCHA/sign-in walls) — surface a manual link instead.
- Scoring and preference learning go through the shell `claude` CLI; if it reports
  `OAuth session expired`, the user has to run `claude login`.
- Every PR bumps `version` in `pyproject.toml` and adds a matching `## x.y.z — YYYY-MM-DD`
  entry at the top of `src/jobhunt/CHANGELOG.md` (`tests/test_update.py` checks they agree).
  The UI's update banner and dashboard changelog are built from that file; its bullets render as
  plain text, so no markdown in them.

## Module map

Data flow: sources → `data/jobs.sqlite` → score (`claude -p`, batches of 10) → digest → rate
(`data/ratings.jsonl` + store) → learn (`## Learned` in `profile/preferences.md`).

| module | does |
|--------|------|
| `cli.py` | typer commands; each one is a thin call into `Workspace` |
| `workspace.py` | `Workspace.open(root, runner)`: paths, config, store, and `fetch`/`score`/`digest`/`check`/`learn`/`shortlist` with a `progress` callback — the one object both CLI and web drive (rating itself goes straight to `ratings.py`: `ingest_ratings` from the CLI, `record_rating` from the UI) |
| `pipeline.py` | fetch every enabled source into the store; build digests from it |
| `sources/base.py`, `sources/http.py` | the `Source` protocol (`name`, `fetch(cfg, http) -> SourceResult`); the polite client (1 req/s, retries, `contact:` in the User-Agent) |
| `sources/<site>.py` | one scraper each; `fixture.py` loads listings from JSON for tests and `check --fixture` |
| `scoring.py` | `build_prompt(profile, preferences, cv, examples, batch)` and `claude_runner` (`claude -p --output-format json --json-schema …`); the `Runner` is injected so tests never call Claude |
| `ratings.py` | parse `rating:`/`note:` lines out of a digest, append to `ratings.jsonl`, `regenerate_preferences` (rewrites `## Learned`, keeps `## Manual`) |
| `digest.py` | render the ranked markdown the user rates in (`## Shortlist`, numbered queue, `## Unscored`) |
| `store.py` | SQLite: listings, scores, ratings, a `meta` table (e.g. `learned_at`) |
| `config.py`, `models.py` | `Paths` + `sources.yaml` loading (pydantic); `Listing`, `Score`, `Rating` |
| `scaffold.py` + `templates/` | `jobhunt init` |
| `update.py` | detect the install from `direct_url.json`, `git ls-remote` the remote, `uv sync` + `os.execv` restart; nothing here imports `web` |
| `web/app.py`, `web/jobs.py` | routes over a `Workspace`; `JobRunner` runs one background job at a time with a progress log the page polls |

## Testing conventions

- No network in tests. Parsers get saved HTML from `tests/fixtures/<source>/`; the pipeline
  gets JSON listings through the `fixture` source (`ws.fetch(fixture=…)`).
- `tests/conftest.py` has the `ws` fixture: a temp workspace with a `fake_runner` that scores
  90, 80, … and answers the preferences prompt with one rule. `test_cli.py` keeps its own
  copies because it drives the typer app.
- `Updater` takes `run_git`, `stream` and `restart` callables — inject fakes; `test_web.py`
  builds one with `available` preset instead of checking a remote.
- Web tests use `JobRunner(background=False)` so actions run inline and the job is `done` when
  the redirect comes back.
- Style: ruff (line length 100, `E F I B UP`), module docstring first, `from __future__ import
  annotations`; `typer.Option()` in defaults is allowed in `cli.py` only.
