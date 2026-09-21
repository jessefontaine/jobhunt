# Self-update banner and changelog — design spec

## Context

The engine is installed into a workspace from git (`jobhunt @ git+https://github.com/…`),
pinned to a commit in the workspace's `uv.lock`. Updating means remembering to run
`uv sync --upgrade-package jobhunt` and restarting `jobhunt serve`. Nothing tells the user a
newer engine exists or what changed in it. The goal: the browser UI shows an unmissable banner
when GitHub has a newer engine, one button updates and restarts the server, and the dashboard
shows a short changelog — which means every engine change must come with a changelog entry.

Decisions already made (do not re-litigate):
- **Version bump per PR.** Every PR bumps `version` in `pyproject.toml` and adds a matching
  entry at the top of the changelog. The changelog is the human-readable description of an
  update; the banner shows the new entries before the user installs them.
- **Detection goes through `git`, not HTTP.** The repository is private; unauthenticated
  fetches of raw files or the GitHub API return 404. `git` with the user's credentials is what
  `uv` already uses to install the engine, so it is guaranteed to be present and to work.
- **Restart in place with `os.execv`.** No supervisor process. Linux/macOS are the targets; on
  Windows `execv` spawns a new process and exits the old one, which is not supported here.
- **Editable (development) checkouts are not updated by the UI.** A developer pulls by hand.

## The changelog convention

`src/jobhunt/CHANGELOG.md` lives inside the package so it ships in the wheel and is readable at
runtime with `importlib.resources`. Format, newest first:

```markdown
# Changelog

## 0.2.0 — 2026-09-20
- The UI shows a banner when a newer engine is on GitHub and can update and restart itself.
- The dashboard shows this changelog.

## 0.1.0 — 2026-09-20
- Browser UI: dashboard with action buttons, rating queue, shortlist, rated page, file editors.
```

Rules, enforced by `tests/test_update.py::test_changelog_matches_package_version`:
- Headings are `## <x.y.z> — <YYYY-MM-DD>` (em dash), versions strictly descending.
- The top entry's version equals `[project].version` in `pyproject.toml`.
- Every entry has at least one `- ` bullet.

`CLAUDE.md` states the rule for future PRs; `README.md` links the file. This PR is 0.2.0.

## `jobhunt/update.py` — no web imports

```python
@dataclass(frozen=True)
class Entry:
    version: str
    date: str
    notes: list[str]              # one string per bullet; continuation lines joined with a space

def parse_changelog(text: str) -> list[Entry]
def version_key(version: str) -> tuple[int, ...]       # "0.10.0" -> (0, 10, 0)
def installed_version() -> str                         # importlib.metadata.version("jobhunt")
def installed_changelog() -> list[Entry]               # parse the packaged CHANGELOG.md

@dataclass(frozen=True)
class EngineInstall:
    url: str | None               # git URL the engine was installed from; None when editable
    commit: str | None            # installed commit id from direct_url.json
    branch: str | None            # direct_url.json requested_revision, if any
    editable: bool
    path: Path | None             # checkout directory when editable

    @classmethod
    def detect(cls) -> EngineInstall          # reads the distribution's direct_url.json (PEP 610)

@dataclass(frozen=True)
class Available:
    commit: str
    version: str                  # version at that commit (top changelog entry)
    entries: list[Entry]          # entries newer than the installed version — may be empty
```

`EngineInstall.detect()` reads `direct_url.json` from the `jobhunt` distribution:
`{"url": "https://github.com/…", "vcs_info": {"vcs": "git", "commit_id": "…",
"requested_revision": "…"}}` for a git install; `{"url": "file:///…", "dir_info":
{"editable": true}}` for a dev checkout. Missing or unparseable → treated as editable (no
update checks) so a strange install never produces a wrong banner.

### Checking the remote

```python
GitRunner = Callable[[list[str], Path | None], str]      # (args, cwd) -> stdout; raises on failure

def run_git(args, cwd=None) -> str
```

`run_git` runs `git` with `GIT_TERMINAL_PROMPT=0` in the environment (so a private https remote
without cached credentials fails instead of hanging) and a 60 s timeout; non-zero exit raises
`UpdateError` with stderr.

```python
def remote_head(install, run=run_git) -> str
    # git ls-remote <url> <branch or HEAD>  -> first field
def remote_file(install, ref, path, run=run_git) -> tuple[str, str]
    # in a TemporaryDirectory: git init -q --bare; git fetch -q --depth=1 --filter=blob:none <url> <ref>;
    # returns (git rev-parse FETCH_HEAD, git show FETCH_HEAD:<path>)
```

The blobless shallow fetch is ~100 KB and about a second; `git show` then pulls the single blob
it needs. It only runs when `ls-remote` reports a commit other than the installed one, and the
result is cached by commit, so the expensive path runs once per new commit.

### `Updater`

```python
def stream(argv: list[str], progress: Progress, cwd: Path | None = None) -> int
    # Popen with stdout+stderr merged, each line to progress(); returns the exit code

class Updater:
    def __init__(self, install: EngineInstall, project: Path, *, run_git=run_git,
                 stream=stream, restart=restart, interval=3600.0)
    install: EngineInstall
    project: Path                 # the workspace root, i.e. the uv project whose venv we run in
    version: str                  # installed_version()
    changelog: list[Entry]        # installed_changelog()
    available: Available | None   # last check result; None when up to date, editable, or unknown

    def check(self) -> Available | None    # synchronous; sets self.available; swallows UpdateError
    def start(self) -> None                # daemon thread: check() now, then every `interval` seconds
    def update(self, progress: Progress) -> None    # the job body, see below
```

`check()`: editable → `None`. Otherwise `head = remote_head()`; if `head == install.commit` →
`None`. Else (unless already cached for `head`) fetch `src/jobhunt/CHANGELOG.md` at `head`,
parse it, and build `Available(commit, version=entries[0].version, entries=[e for e in
entries if version_key(e.version) > version_key(self.version)])`. An `UpdateError` (offline,
git missing, file absent at that commit) is printed to stderr as one line and leaves
`available` unchanged.

If the remote commit moved but its version is not newer than the installed one, `Available`
is still returned (with `entries == []`): the banner then says so plainly, which makes a
forgotten bump visible instead of hiding the update.

`update(progress)`, run as a `JobRunner` job named `update`:
1. Refuse when `install.editable` (`UpdateError("development checkout — git pull it")`).
2. Find `uv`: `$UV` (set by `uv run` for its children) or `shutil.which("uv")`; neither →
   `UpdateError`.
3. `stream(["uv", "sync", "--upgrade-package", "jobhunt", "--project", project])`: every
   output line goes into the job log; non-zero exit → `UpdateError`.
4. Smoke test the new code in a fresh interpreter:
   `stream([sys.executable, "-c", "import jobhunt.web.app"])`; a traceback lands in the log and
   a non-zero exit raises, so a broken release fails the job and the old server keeps running.
5. `progress("restarting…")`, then `threading.Timer(1.0, restart).start()` so the job reaches
   `done` and the browser sees it before the process is replaced.

`restart()` = `os.execv(sys.executable, [sys.executable, "-m", "jobhunt", *sys.argv[1:],
"--no-open"])`. The new process reuses the same `--host`/`--port`/`--root` arguments; the
trailing `--no-open` wins over an earlier `--open` so the browser does not get a second tab.
Python sockets are close-on-exec, so the listening port is free for the new process.
`src/jobhunt/__main__.py` (`from jobhunt.cli import app; app()`) makes `-m jobhunt` work.

## Web app changes

`create_app(ws, jobs=None, updater=None)`; `None` →
`Updater(EngineInstall.detect(), ws.paths.root)` with no polling thread. `cli.serve` builds the `Updater`, calls `start()`, and passes it in.

- `render()` adds `update=updater.available` and `busy=` (a job is running) to every page's
  context, so `base.html` can draw the banner under the nav.
- **Banner** (`base.html`), amber, on every page:
  `⬆ jobhunt <new> is available — you have <installed>.` then the new entries as bullets, then
  a form `POST /actions/update` with an **Update & restart** button (disabled while a job
  runs). When `entries` is empty: `A newer engine commit is on GitHub but its version is still
  <installed> — no changelog entry. Update anyway?`
- `POST /actions/update` → `start(request, "update", updater.update)`; redirects to the
  dashboard like the other actions.
- `GET /health` → `{"version": ..., "boot": ...}` where `boot` is a per-process id chosen in
  `create_app`. The dashboard's job panel carries `data-boot`.
- **Dashboard** gains a "What's new" section between the stats and the actions: `jobhunt
  <version>`, the five newest entries as `<h3>` + bullets, older ones inside `<details>`. For an
  editable install it adds a muted line `development checkout at <path> — update with git pull`.
- `app.js`: when the polled job is named `update` and reaches `done`, instead of reloading at
  once it polls `/health` every second (ignoring connection errors) until `boot` differs from
  the panel's `data-boot`, then reloads; after 60 attempts it reloads regardless. `failed`
  still reloads immediately so the log stays visible.
- `style.css`: `.update` banner (amber background, dark text, full width under the nav) and
  `.changelog` list styles.

## Error handling

| situation | behaviour |
|---|---|
| offline / git missing / remote unreachable | one stderr line, no banner, retry next hour |
| `direct_url.json` missing or odd | treated as editable: no checks, dashboard says so |
| `uv` not found | job fails with "uv not found" |
| `uv sync` fails | job fails with its output in the log; server keeps running |
| new version does not import | job fails with the traceback; server keeps running |
| update clicked while a job runs | existing 409 "a job is already running" page |

## Testing

`tests/test_update.py` (no network, no real git):
- `parse_changelog` on a sample; bad headings rejected; continuation lines joined.
- `version_key` ordering including two-digit components.
- `EngineInstall.detect()` from a fake distribution `direct_url.json` (git and editable).
- `Updater.check()` with a fake `run_git`: same commit → `None`; new commit with a newer
  version → `Available` with only the newer entries; new commit, same version → empty entries;
  `UpdateError` → `None` and a stderr line; the fetch runs once per commit.
- `Updater.update()` with a fake `stream` and `restart`: runs `uv sync … --project <root>`
  then the import check, and calls `restart` only after both exit 0; editable refuses; missing
  uv fails; a failing smoke test does not restart.
- `test_changelog_matches_package_version` — the convention test.

`tests/test_web.py`:
- no banner when `available` is `None`; banner with version and bullets when set; the
  "no changelog entry" wording when entries are empty; button disabled while busy.
- `POST /actions/update` runs the injected updater's `update` as a job and the dashboard shows
  its log; `/health` returns version and boot id.
- dashboard "What's new" shows the installed version and entries; editable note.

## Files

- New: `src/jobhunt/update.py`, `src/jobhunt/__main__.py`, `src/jobhunt/CHANGELOG.md`,
  `tests/test_update.py`.
- Modified: `src/jobhunt/web/app.py`, `web/templates/base.html`, `web/templates/dashboard.html`,
  `web/static/app.js`, `web/static/style.css`, `src/jobhunt/cli.py`, `pyproject.toml` (0.2.0),
  `README.md`, `CLAUDE.md`, `src/jobhunt/templates/README.md.tmpl` (mention the button),
  `tests/test_web.py`.
