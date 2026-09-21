# Web UI — design spec

## Context

Today the pipeline is driven from a terminal (`jobhunt check`, `rate`, …) or from Claude Code
via the `/jobhunt` skill, and rating happens by editing `rating:` lines in a markdown digest.
That works, but it is not intuitive: the user has to remember flags, find the newest digest,
and edit a file by hand. The goal is a local browser UI with buttons for every action (with
option boxes for the flags), a rating page, a shortlist page, and editors for the profile and
preferences.

Decisions already made (do not re-litigate):
- **Option A for rating.** The UI writes ratings straight into the store and `data/ratings.jsonl`.
  `check` keeps writing digest files as a record, and `jobhunt rate` keeps working on them, but
  a UI user never needs to edit a digest. Preference learning is an explicit button that shows
  how many ratings arrived since the last regeneration; nothing auto-fires.
- Server-rendered Python, no JavaScript build step: FastAPI + Jinja2 + uvicorn, a single
  stylesheet and a few dozen lines of vanilla JS. No React/Vue, no Streamlit/NiceGUI.
- The UI is a second front door next to the CLI and the `/jobhunt` skill, not a replacement.
  Everything it does goes through the same library functions the CLI uses.
- Single user, local only. Binds to `127.0.0.1` by default, no authentication. Passing
  `--host 0.0.0.0` is the user's own choice and is documented as exposing file editing to the LAN.

## Command

```
jobhunt serve [--host 127.0.0.1] [--port 8765] [--no-open]
```

Runs inside a workspace like every other command. Starts uvicorn, prints
`jobhunt UI: http://127.0.0.1:8765 (Ctrl-C to stop)`, and opens that URL in the default browser
unless `--no-open`. `fastapi`, `uvicorn`, `jinja2` and `python-multipart` become core
dependencies so `uv run jobhunt serve` works in every workspace without an extra.

## Shared layer: `Workspace`

The CLI's private `Ctx` dataclass and its `_fetch` / `_score` / `_digest` helpers move to a new
module `src/jobhunt/workspace.py` as `Workspace`, so the CLI and the web app drive the same
object:

```python
@dataclass
class Workspace:
    paths: Paths
    config: Config
    runner: Runner = claude_runner          # swapped for a fake in tests

    @classmethod
    def open(cls, root: Path, runner: Runner = claude_runner) -> Workspace

    @property
    def store(self) -> Store                 # a fresh connection per access (thread-safe by construction)

    def fetch(self, only=None, fixture=None, progress=...) -> RunInfo
    def score(self, rescore=False, dry_run=False, progress=...) -> ScoreRunResult
    def digest(self, info: RunInfo | None = None, progress=...) -> Path
    def check(self, only=None, fixture=None, no_score=False, rescore=False, progress=...) -> Path
    def shortlist(self) -> str               # writes shortlist.md, returns the text
    def learn(self, progress=...) -> bool    # regenerate preferences; True on success
```

Every method reports through `progress(line)` exactly what the CLI prints today
(`fetched: 3 new listing(s) from 1 source(s)`, `  ! euraxess: 429`, `scored: 2 listing(s), 0
failed`, `digest: <path>`, `preferences: updated`). The CLI passes `progress=typer.echo`; the
web app passes the job's log appender. `cli.RUNNER` stays as the test seam and is handed to
`Workspace.open`. `rate` (digest ingestion) stays CLI-only.

`Workspace.store` returns a new `Store` each time on purpose: `sqlite3` connections are bound to
the thread that created them, and a background job and a request handler must never share one.
SQLite's own locking (5 s default busy timeout) covers the single-user case where a job writes
scores while a request writes a rating.

## Ratings: `record_rating`

`ratings.py` gains

```python
def record_rating(store, jsonl, listing_id, rating, note, digest) -> Rating | None
```

which appends to `ratings.jsonl` and saves to the store unless an identical rating already
exists (then returns `None`). `ingest_ratings` is rewritten on top of it; behaviour and output
are unchanged. Web ratings use `digest="web"`.

### Ratings since the last regeneration

The store gets a `meta(key TEXT PRIMARY KEY, value TEXT)` table with `get_meta` / `set_meta`.
`regenerate_preferences` sets `learned_at` (ISO datetime) on success, from both the CLI and the
UI. `Store.ratings_since(learned_at | None) -> int` counts ratings with `rated_at` after it
(all ratings when unset). The dashboard shows this next to the *Regenerate preferences* button.
If `jobs.sqlite` is rebuilt the marker is lost and the count shows every rating once — harmless.

## Background jobs: `web/jobs.py`

Fetching and scoring take minutes, so buttons must not block a request.

```python
@dataclass
class Job:
    id: int
    name: str                                # "check", "score", "learn", …
    status: Literal["running", "done", "failed"]
    lines: list[str]                         # progress lines, appended by the job
    error: str | None
    started_at: datetime
    finished_at: datetime | None

class JobRunner:
    def __init__(self, background: bool = True)
    def start(self, name: str, fn: Callable[[Progress], object]) -> Job   # raises JobBusy
    @property
    def current(self) -> Job | None          # the running job, else the most recent one
    def get(self, job_id: int) -> Job | None
```

One job at a time: `start` raises `JobBusy` while a job is running. The function runs in a
daemon thread and gets `job.lines.append` as its `progress`; an exception marks the job
`failed` with `f"{type}: {exc}"` as both `error` and the last line. `background=False` runs the
function inline — that is what tests use. Jobs live in memory only (the runner keeps the last
20); a server restart forgets them, which is fine because every job's durable effect is in the
workspace files and database.

## Web app: `web/app.py`

`create_app(ws: Workspace, jobs: JobRunner | None = None) -> FastAPI`. Templates live in
`src/jobhunt/web/templates/` (Jinja2, autoescape on) — not in `src/jobhunt/templates/`, which
holds workspace scaffolding. Static files in `src/jobhunt/web/static/` (`style.css`, `app.js`).
All state is read at request time with `date.today()`; nothing is cached across requests.

### Pages

| route | shows |
|---|---|
| `GET /` | **Dashboard.** Counts (open unrated listings and how many are unscored, shortlist size, total listings, total ratings, ratings since last regeneration), newest digest path, a warning if the `claude` CLI is not on `PATH`, the action panel, and the current/last job panel. |
| `GET /queue` | **Rate.** Open, unrated listings: scored ones best-first, then unscored (newest first) under an *Unscored* heading. One card per listing. |
| `GET /shortlist` | Open listings rated 4–5, in the store's shortlist order, as cards with their rating and note. Rendering this page also rewrites `shortlist.md`, like the CLI command. |
| `GET /rated` | Every rated listing, most recent first, expired ones marked. Where the user changes an old rating. |
| `GET /files/{name}` | Editor for one of `profile`, `preferences`, `cv`, `sources` — a textarea with the file's text (empty if the file does not exist yet) and a Save button. `sources` also shows a table of known sources with their enabled state above the editor. |

### Listing card

Title (links to the listing URL, new tab), employer, meta line (score, role, location, posted,
deadline, source), area tags, **Why** and **Concerns** from the score, a collapsed `<details>`
with the fetched description, and the rating widget: five buttons labelled `1 irrelevant … 5
apply` plus a one-line note input. The current rating (if any) is highlighted. Clicking a
button posts `{listing_id, rating, note}` to `POST /ratings`; the card updates in place and
stays on the page until reload, so a mis-click can be corrected. On reload it leaves the queue.

### Actions

Each action is a small form on the dashboard that posts to `/actions/<name>` and redirects to
`/` (303), where the job panel shows progress.

| action | fields |
|---|---|
| `check` | `source` (select: *all enabled* or one), `no_score` (checkbox), `rescore` (checkbox) |
| `fetch` | `source` |
| `score` | `rescore` |
| `digest` | — |
| `learn` | — (button text: *Regenerate preferences*, with "N rating(s) since last regeneration" beside it) |

If a job is running, the action re-renders the dashboard with status 409 and the message
"a job is already running" instead of starting a second one. The `--dry-run` and `--fixture`
CLI options are deliberately not exposed: they are developer tools.

### Ratings

`POST /ratings` (form: `listing_id`, `rating` 1–5, `note`) → `record_rating(..., digest="web")`,
then `pipeline.write_shortlist` so `shortlist.md` stays current. Returns JSON
`{"listing_id", "rating", "note", "changed", "since_learned"}`. Unknown listing → 404, bad
rating → 422. The rating buttons post via `fetch()`; there is no non-JS fallback (local tool).

### Jobs

`GET /jobs/{id}` → JSON `{id, name, status, lines, error, started_at, finished_at}`; 404 if
unknown. While the dashboard shows a running job, `app.js` polls this once a second and appends
new lines to the log panel; when the status leaves `running` it reloads the page once so the
counts refresh. No SSE/websockets.

### File editor

`POST /files/{name}` (form: `text`) writes the file and redirects back to the editor with a
"saved" notice. Only the four whitelisted names are accepted (404 otherwise), so the route can
never write elsewhere. `sources` is validated first: the text must parse as YAML into a mapping
that `load_config` accepts; on failure the editor re-renders with the error and the unsaved
text, status 400. After saving `profile`, `preferences` or `cv` the notice reminds the user
that existing scores were computed with the old text and offers the *Check with rescore*
action. The `## Learned` section of `preferences` is editable like the rest of the file — the
same as editing it in a text editor today; the *Regenerate preferences* action overwrites it.

## Look

One stylesheet, system font stack, `max-width: 60rem`, light and dark via
`prefers-color-scheme`. Nav bar: Dashboard · Queue · Shortlist · Rated · Profile · Preferences ·
CV · Sources. Cards are plain bordered blocks; rating buttons are pills, the selected one
filled. No icon fonts, no CSS framework.

## Files

```
src/jobhunt/workspace.py             Workspace (moved out of cli.py)
src/jobhunt/web/__init__.py
src/jobhunt/web/app.py               create_app, routes
src/jobhunt/web/jobs.py              Job, JobRunner, JobBusy
src/jobhunt/web/templates/base.html  nav + flash + content block
src/jobhunt/web/templates/dashboard.html
src/jobhunt/web/templates/listings.html   queue / shortlist / rated (one template, a `mode`)
src/jobhunt/web/templates/_card.html      listing card macro
src/jobhunt/web/templates/editor.html
src/jobhunt/web/static/style.css
src/jobhunt/web/static/app.js
```

Changes: `cli.py` (use `Workspace`; add `serve`), `ratings.py` (`record_rating`, `learned_at`),
`store.py` (`meta`, `ratings_since`), `pyproject.toml` (deps), `README.md` and the workspace
templates (`README.md.tmpl`, `CLAUDE.md.tmpl`, `SKILL.md.tmpl`: one line each pointing at
`uv run jobhunt serve`).

## Testing

TDD, as everywhere in the repo.

- `tests/test_workspace.py` — `Workspace.check` on a fixture file with a fake runner produces
  the same progress lines the CLI tests assert on today; existing `tests/test_cli.py` keeps
  passing unchanged (it is the regression net for the `Ctx` → `Workspace` move).
- `tests/test_ratings.py` — `record_rating` appends once, dedupes identical ratings, records a
  changed note; `ingest_ratings` results unchanged. `regenerate_preferences` sets `learned_at`.
- `tests/test_store.py` — `meta` round-trip; `ratings_since` with and without a marker.
- `tests/test_jobs.py` — background job collects lines and finishes `done`; an exception gives
  `failed` with the message; a second `start` while running raises `JobBusy` (use a
  `threading.Event` to hold the first job open); `background=False` runs inline.
- `tests/test_web.py` — `TestClient` against `create_app(Workspace(..., runner=fake),
  JobRunner(background=False))` on a `tmp_path` workspace pre-populated through
  `ws.fetch(fixture=…)`: dashboard counts; `POST /actions/check` (no enabled sources) scores and
  writes a digest, and `GET /jobs/1` reports `done` with the `scored:` line; queue order and
  content; `POST /ratings` writes the store, `ratings.jsonl` and `shortlist.md`, and the
  listing then appears on `/shortlist` and `/rated` but not `/queue`; a job in progress makes
  `POST /actions/score` return 409; `GET/POST /files/profile` round-trips; `POST /files/sources`
  with invalid YAML returns 400 and leaves the file alone; `GET /files/nope` is 404.

## Out of scope

Authentication, multi-user, remote access, editing individual source queries through a form
(the YAML editor covers it), pagination of the queue, SSE/websockets, changing the CLI's
digest-based rating flow.
