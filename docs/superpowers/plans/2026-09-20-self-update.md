# Self-Update Banner and Changelog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The browser UI shows an unmissable banner when GitHub has a newer engine, one button runs `uv sync --upgrade-package jobhunt` and restarts the server in place, and the dashboard shows a changelog that every PR must extend.

**Architecture:** A new web-free module `jobhunt/update.py` owns the changelog format, reads where the engine was installed from (`direct_url.json`, PEP 610), checks the remote with `git ls-remote` hourly in a daemon thread (plus a blobless shallow fetch of the remote changelog once per new commit), and runs the update as an ordinary `JobRunner` job that ends in `os.execv` of `python -m jobhunt serve …`. The web app injects an `Updater`, renders its `available` result as a banner on every page and its packaged changelog on the dashboard, and the dashboard JS waits for `/health` to report a new boot id before reloading.

**Tech Stack:** Python 3.12, `importlib.metadata`/`importlib.resources`, `subprocess` + system `git`, FastAPI + Jinja2, Starlette `TestClient`, vanilla JS, one CSS file. pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-20-self-update-design.md`

## Global Constraints

- `uv run pytest` and `uv run ruff check src tests` must pass after every task. Line length 100.
- TDD: write the failing test first, watch it fail, then implement.
- `src/jobhunt/update.py` imports nothing from `jobhunt.web`.
- No network and no real remote git in tests; a real local `git --version` and a python subprocess are fine.
- Changelog headings are exactly `## x.y.z — YYYY-MM-DD` (em dash, U+2014); versions strictly descending; top entry == `pyproject.toml` version. This PR is **0.2.0**.
- `git` runs with `GIT_TERMINAL_PROMPT=0` and a 60 s timeout, always.
- The restart is `os.execv(sys.executable, [sys.executable, "-m", "jobhunt", *sys.argv[1:], "--no-open"])`.
- Web templates go in `src/jobhunt/web/templates/`; `src/jobhunt/templates/` holds workspace scaffolding.
- Commit after every task with the trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: Changelog file, parser, and the version convention

**Files:**
- Create: `src/jobhunt/CHANGELOG.md`, `src/jobhunt/update.py`, `tests/test_update.py`
- Modify: `pyproject.toml` (version), `CLAUDE.md`, `README.md`

**Interfaces:**
- Produces: `Entry(version: str, date: str, notes: list[str])` (plain dataclass),
  `UpdateError(Exception)`, `parse_changelog(text: str) -> list[Entry]`,
  `version_key(version: str) -> tuple[int, ...]`, `installed_version() -> str`,
  `installed_changelog() -> list[Entry]`, constants `DIST = "jobhunt"`,
  `CHANGELOG_PATH = "src/jobhunt/CHANGELOG.md"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_update.py`:

```python
"""Engine self-update: changelog format, install detection, remote check, update job."""

import re
import tomllib
from pathlib import Path

import pytest

from jobhunt.update import (
    Entry,
    UpdateError,
    installed_changelog,
    installed_version,
    parse_changelog,
    version_key,
)

ROOT = Path(__file__).resolve().parents[1]

SAMPLE = """\
# Changelog

Some preamble that is ignored.

## 0.2.0 — 2026-09-20
- Update banner and the dashboard changelog.
- A bullet that
  wraps onto a second line.

## 0.1.0 — 2026-09-19
- Browser UI.
"""


def test_parse_changelog_reads_entries_newest_first():
    assert parse_changelog(SAMPLE) == [
        Entry(
            "0.2.0",
            "2026-09-20",
            [
                "Update banner and the dashboard changelog.",
                "A bullet that wraps onto a second line.",
            ],
        ),
        Entry("0.1.0", "2026-09-19", ["Browser UI."]),
    ]


def test_parse_changelog_rejects_a_malformed_heading():
    with pytest.raises(UpdateError, match="bad changelog heading"):
        parse_changelog("## v0.2.0 (2026-09-20)\n- x\n")


def test_version_key_orders_numerically():
    assert version_key("0.10.0") > version_key("0.9.3")
    assert sorted(["0.10.0", "0.2.0", "0.9.3"], key=version_key) == ["0.2.0", "0.9.3", "0.10.0"]


def test_installed_version_and_changelog_come_from_the_package():
    assert re.fullmatch(r"\d+\.\d+\.\d+", installed_version())
    assert installed_changelog()[0].notes


def test_changelog_matches_package_version():
    """The convention every PR follows: bump pyproject.toml and add a matching top entry."""
    entries = parse_changelog((ROOT / "src/jobhunt/CHANGELOG.md").read_text())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert entries, "CHANGELOG.md has no entries"
    assert entries[0].version == version, "bump pyproject.toml and add a changelog entry"
    keys = [version_key(e.version) for e in entries]
    assert keys == sorted(set(keys), reverse=True), "versions must be strictly descending"
    assert all(e.notes for e in entries), "every changelog entry needs at least one bullet"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_update.py -v`
Expected: FAIL at import with `ModuleNotFoundError: No module named 'jobhunt.update'`

- [ ] **Step 3: Write the changelog and bump the version**

Create `src/jobhunt/CHANGELOG.md`:

```markdown
# Changelog

Newest first. Every PR bumps `version` in `pyproject.toml` and adds a `## x.y.z — YYYY-MM-DD`
entry here; the browser UI shows this file on the dashboard and the new entries in its update
banner.

## 0.2.0 — 2026-09-20
- The UI shows a banner when a newer engine is on GitHub and can update and restart itself.
- The dashboard shows this changelog.

## 0.1.0 — 2026-09-20
- Browser UI: dashboard with action buttons, rating queue, shortlist, rated page, and editors
  for the profile, preferences, CV and sources.yaml.
- Pipeline: fetch Dutch research listings, score them with Claude, write digests, learn
  preferences from ratings.
```

In `pyproject.toml` change `version = "0.1.0"` to `version = "0.2.0"`.

- [ ] **Step 4: Write the parser module**

Create `src/jobhunt/update.py`:

```python
"""Engine self-update: what is installed, what the remote has, and how to switch to it.

Nothing here imports from jobhunt.web; the web app injects an Updater.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata, resources

DIST = "jobhunt"
CHANGELOG_PATH = "src/jobhunt/CHANGELOG.md"  # path inside the engine repository
HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$")


class UpdateError(Exception):
    """A check or update step failed; the message is meant for the log."""


@dataclass
class Entry:
    version: str
    date: str
    notes: list[str]


def parse_changelog(text: str) -> list[Entry]:
    """`## x.y.z — YYYY-MM-DD` headings, newest first, each with `- ` bullets under it."""
    entries: list[Entry] = []
    for line in text.splitlines():
        if match := HEADING.match(line):
            entries.append(Entry(match.group(1), match.group(2), []))
        elif line.startswith("## "):
            raise UpdateError(f"bad changelog heading: {line!r}")
        elif not entries:
            continue  # title and preamble
        elif line.startswith("- "):
            entries[-1].notes.append(line[2:].strip())
        elif line.strip() and entries[-1].notes:
            entries[-1].notes[-1] += " " + line.strip()  # wrapped bullet
    return entries


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def installed_version() -> str:
    return metadata.version(DIST)


def installed_changelog() -> list[Entry]:
    return parse_changelog(resources.files("jobhunt").joinpath("CHANGELOG.md").read_text())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update.py -v`
Expected: 5 passed. (`uv run` re-syncs the editable install, so `installed_version()` reports
0.2.0; if it still says 0.1.0, run `uv sync` once.)

- [ ] **Step 6: Document the convention**

Append to the bullet list in `CLAUDE.md`:

```markdown
- Every PR bumps `version` in `pyproject.toml` and adds a matching `## x.y.z — YYYY-MM-DD`
  entry at the top of `src/jobhunt/CHANGELOG.md` (`tests/test_update.py` checks they agree).
  The UI's update banner and dashboard changelog are built from that file.
```

In `README.md`, under `## Development` after the code block, add:

```markdown
Every PR bumps `version` in `pyproject.toml` and adds an entry at the top of
[`src/jobhunt/CHANGELOG.md`](src/jobhunt/CHANGELOG.md) — the UI shows it, and a test checks
the two agree.
```

- [ ] **Step 7: Lint and run the whole suite**

Run: `uv run ruff check src tests && uv run pytest`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/jobhunt/CHANGELOG.md src/jobhunt/update.py tests/test_update.py pyproject.toml uv.lock CLAUDE.md README.md
git commit -m "changelog: packaged CHANGELOG.md, parser, and the version-bump-per-PR rule

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Where the engine came from, and reading the remote with git

**Files:**
- Modify: `src/jobhunt/update.py`
- Test: `tests/test_update.py`

**Interfaces:**
- Consumes: `UpdateError`, `DIST` from Task 1.
- Produces: `EngineInstall(url, commit, branch, editable, path)` (frozen) with
  `EngineInstall.from_direct_url(text: str | None)` and `EngineInstall.detect()`;
  `GitRunner = Callable[[list[str], Path | None], str]`;
  `run_git(args: list[str], cwd: Path | None = None) -> str`;
  `remote_head(url: str, ref: str, run: GitRunner = run_git) -> str`;
  `remote_file(url: str, ref: str, path: str, run: GitRunner = run_git) -> tuple[str, str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update.py` (extend the import to include `EngineInstall`, `remote_file`,
`remote_head`, `run_git`):

```python
GIT_URL = "https://github.com/x/jobhunt"
GIT_JSON = (
    '{"url": "https://github.com/x/jobhunt", "vcs_info": {"vcs": "git", '
    '"commit_id": "abc123", "requested_revision": "main"}}'
)
EDITABLE_JSON = '{"url": "file:///home/me/code/jobhunt", "dir_info": {"editable": true}}'


def test_engine_install_from_a_git_direct_url():
    install = EngineInstall.from_direct_url(GIT_JSON)
    assert install == EngineInstall(
        url=GIT_URL, commit="abc123", branch="main", editable=False, path=None
    )


def test_engine_install_from_an_editable_direct_url():
    install = EngineInstall.from_direct_url(EDITABLE_JSON)
    assert install.editable and install.path == Path("/home/me/code/jobhunt")
    assert install.url is None and install.commit is None


@pytest.mark.parametrize("text", [None, "", "not json", "[1, 2]", '{"url": "https://pypi"}'])
def test_engine_install_treats_anything_odd_as_editable(text):
    assert EngineInstall.from_direct_url(text).editable


def test_engine_install_detect_reads_this_checkout():
    assert EngineInstall.detect().editable  # the test venv is an editable install


def test_run_git_returns_stdout_and_raises_on_failure():
    assert run_git(["--version"]).startswith("git version")
    with pytest.raises(UpdateError, match="git no-such-command"):
        run_git(["no-such-command"])


def test_remote_head_takes_the_first_field():
    calls = []

    def fake(args, cwd):
        calls.append((args, cwd))
        return "deadbeef\tHEAD\n"

    assert remote_head(GIT_URL, "HEAD", fake) == "deadbeef"
    assert calls == [(["ls-remote", GIT_URL, "HEAD"], None)]


def test_remote_head_with_no_such_ref_raises():
    with pytest.raises(UpdateError, match="no main at"):
        remote_head(GIT_URL, "main", lambda args, cwd: "")


def test_remote_file_fetches_blobless_into_a_temp_repo():
    calls = []

    def fake(args, cwd):
        calls.append(args[0])
        assert cwd is not None and cwd.is_dir()
        if args[0] == "fetch":
            assert args == ["fetch", "-q", "--depth=1", "--filter=blob:none", GIT_URL, "main"]
        if args[0] == "rev-parse":
            return "cafe42\n"
        if args[0] == "show":
            assert args == ["show", "FETCH_HEAD:src/jobhunt/CHANGELOG.md"]
            return "# Changelog\n"
        return ""

    commit, text = remote_file(GIT_URL, "main", "src/jobhunt/CHANGELOG.md", fake)
    assert (commit, text) == ("cafe42", "# Changelog\n")
    assert calls == ["init", "fetch", "rev-parse", "show"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_update.py -v`
Expected: FAIL at import with `ImportError: cannot import name 'EngineInstall'`

- [ ] **Step 3: Implement install detection and the git helpers**

In `src/jobhunt/update.py`, extend the imports:

```python
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
```

and add after `installed_changelog`:

```python
@dataclass(frozen=True)
class EngineInstall:
    """Where the running engine came from, per the distribution's direct_url.json (PEP 610)."""

    url: str | None = None  # git URL for a git install
    commit: str | None = None  # the installed commit
    branch: str | None = None  # requested revision, if the workspace pinned one
    editable: bool = True  # a development checkout: no update checks
    path: Path | None = None  # that checkout, when known

    @classmethod
    def from_direct_url(cls, text: str | None) -> EngineInstall:
        """Anything that is not clearly a git install is treated as editable."""
        try:
            info = json.loads(text or "")
        except json.JSONDecodeError:
            return cls()
        if not isinstance(info, dict):
            return cls()
        vcs = info.get("vcs_info") or {}
        if vcs.get("vcs") == "git" and vcs.get("commit_id"):
            return cls(
                url=info["url"],
                commit=vcs["commit_id"],
                branch=vcs.get("requested_revision"),
                editable=False,
            )
        url = info.get("url", "")
        path = Path(url.removeprefix("file://")) if url.startswith("file://") else None
        return cls(path=path)

    @classmethod
    def detect(cls) -> EngineInstall:
        try:
            text = metadata.distribution(DIST).read_text("direct_url.json")
        except metadata.PackageNotFoundError:
            text = None
        return cls.from_direct_url(text)


# -- talking to the remote with the same git that uv installs the engine with ----------

GitRunner = Callable[[list[str], Path | None], str]


def run_git(args: list[str], cwd: Path | None = None) -> str:
    """Run git non-interactively: stdout on success, UpdateError with stderr otherwise."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"git {args[0]}: {exc}") from None
    if proc.returncode:
        raise UpdateError(f"git {args[0]}: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


def remote_head(url: str, ref: str, run: GitRunner = run_git) -> str:
    """The commit `ref` (a branch, or HEAD for the default branch) points at on the remote."""
    out = run(["ls-remote", url, ref], None)
    if not out.strip():
        raise UpdateError(f"git ls-remote: no {ref} at {url}")
    return out.split()[0]


def remote_file(url: str, ref: str, path: str, run: GitRunner = run_git) -> tuple[str, str]:
    """(commit, contents) of `path` at `ref` on the remote.

    A blobless shallow fetch into a throwaway bare repo is ~100 KB; `git show` then pulls the
    one blob it needs. GitHub does not support `git archive --remote`, hence this dance.
    """
    with tempfile.TemporaryDirectory(prefix="jobhunt-update-") as tmp:
        cwd = Path(tmp)
        run(["init", "-q", "--bare"], cwd)
        run(["fetch", "-q", "--depth=1", "--filter=blob:none", url, ref], cwd)
        commit = run(["rev-parse", "FETCH_HEAD"], cwd).strip()
        return commit, run(["show", f"FETCH_HEAD:{path}"], cwd)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update.py -v`
Expected: all pass (13 tests).

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src tests && uv run pytest`

```bash
git add src/jobhunt/update.py tests/test_update.py
git commit -m "update: detect the engine install from direct_url.json; read the remote with git

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `Updater.check()` and the hourly background check

**Files:**
- Modify: `src/jobhunt/update.py`
- Test: `tests/test_update.py`

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces: `Available(commit: str, version: str, entries: list[Entry])` (frozen);
  `class Updater` with `__init__(self, install: EngineInstall, project: Path, *,
  version: str | None = None, changelog: list[Entry] | None = None,
  run_git: GitRunner = run_git, interval: float = 3600.0)` (Task 4 adds more keywords),
  attributes `install`, `project`, `version`, `changelog`, `available: Available | None`,
  methods `check() -> Available | None` and `start() -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update.py` (extend the import with `Available`, `Updater`):

```python
REMOTE_CHANGELOG = """\
# Changelog

## 0.3.0 — 2026-09-21
- Faster scoring.

## 0.2.0 — 2026-09-20
- Update banner.
"""
INSTALLED = EngineInstall(url=GIT_URL, commit="a" * 40, editable=False)


class FakeGit:
    """Answers ls-remote with `head` and `show` with `changelog`; records the subcommands."""

    def __init__(self, head, changelog=REMOTE_CHANGELOG, fail=False):
        self.head, self.changelog, self.fail = head, changelog, fail
        self.calls = []

    def __call__(self, args, cwd):
        self.calls.append(args[0])
        if self.fail:
            raise UpdateError("boom")
        match args[0]:
            case "ls-remote":
                return f"{self.head}\t{args[2]}\n"
            case "rev-parse":
                return self.head + "\n"
            case "show":
                return self.changelog
        return ""


def make_updater(tmp_path, git, install=INSTALLED, version="0.2.0"):
    return Updater(install, tmp_path, version=version, changelog=[], run_git=git)


def test_check_finds_nothing_when_the_remote_is_at_the_installed_commit(tmp_path):
    git = FakeGit(head="a" * 40)
    assert make_updater(tmp_path, git).check() is None
    assert git.calls == ["ls-remote"]


def test_check_reports_a_newer_commit_with_only_the_newer_entries(tmp_path):
    git = FakeGit(head="b" * 40)
    updater = make_updater(tmp_path, git)
    assert updater.check() == Available(
        "b" * 40, "0.3.0", [Entry("0.3.0", "2026-09-21", ["Faster scoring."])]
    )
    assert updater.available is not None
    assert git.calls == ["ls-remote", "init", "fetch", "rev-parse", "show"]
    updater.check()  # the changelog fetch happens once per remote commit
    assert git.calls[5:] == ["ls-remote"]


def test_check_uses_the_pinned_branch(tmp_path):
    git = FakeGit(head="b" * 40)
    calls = []

    def recording(args, cwd):
        calls.append(args)
        return git(args, cwd)

    pinned = EngineInstall(url=GIT_URL, commit="a" * 40, branch="dev", editable=False)
    make_updater(tmp_path, recording, install=pinned).check()
    assert calls[0] == ["ls-remote", GIT_URL, "dev"]
    assert calls[2][0] == "fetch" and calls[2][-1] == "dev"


def test_check_flags_a_commit_without_a_version_bump(tmp_path):
    git = FakeGit(head="b" * 40)
    result = make_updater(tmp_path, git, version="0.3.0").check()
    assert result == Available("b" * 40, "0.3.0", [])


def test_check_swallows_errors_and_says_so_on_stderr(tmp_path, capsys):
    updater = make_updater(tmp_path, FakeGit(head="b" * 40, fail=True))
    assert updater.check() is None and updater.available is None
    assert "update check: boom" in capsys.readouterr().err


def test_check_does_nothing_for_an_editable_install(tmp_path):
    git = FakeGit(head="b" * 40)
    assert make_updater(tmp_path, git, install=EngineInstall(editable=True)).check() is None
    assert git.calls == []


def test_start_checks_in_the_background(tmp_path):
    import threading

    checked = threading.Event()

    def git(args, cwd):
        checked.set()
        return "a" * 40 + "\tHEAD\n"

    Updater(INSTALLED, tmp_path, version="0.2.0", changelog=[], run_git=git, interval=3600).start()
    assert checked.wait(2)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_update.py -v`
Expected: FAIL at import with `ImportError: cannot import name 'Available'`

- [ ] **Step 3: Implement `Available` and `Updater`**

Add `import sys`, `import threading`, `import time` to the imports of `src/jobhunt/update.py`
and append:

```python
# -- the object the web app holds -------------------------------------------------------


@dataclass(frozen=True)
class Available:
    commit: str
    version: str  # the version at that commit
    entries: list[Entry]  # changelog entries newer than the installed version; may be empty


class Updater:
    """Knows what is installed, checks the remote in the background, runs the update."""

    def __init__(
        self,
        install: EngineInstall,
        project: Path,
        *,
        version: str | None = None,
        changelog: list[Entry] | None = None,
        run_git: GitRunner = run_git,
        interval: float = 3600.0,
    ):
        self.install = install
        self.project = project  # the workspace: the uv project whose venv we run in
        self.version = version or installed_version()
        self.changelog = installed_changelog() if changelog is None else changelog
        self.available: Available | None = None
        self._run_git = run_git
        self._interval = interval
        self._seen: dict[str, Available] = {}  # remote commit -> what it offers

    def check(self) -> Available | None:
        """Refresh `available`; a failed check is one stderr line and leaves it unchanged."""
        try:
            self.available = self._check()
        except UpdateError as exc:
            print(f"update check: {exc}", file=sys.stderr)
        return self.available

    def _check(self) -> Available | None:
        if self.install.editable or not self.install.url:
            return None
        ref = self.install.branch or "HEAD"
        head = remote_head(self.install.url, ref, self._run_git)
        if head == self.install.commit:
            return None
        if head not in self._seen:
            commit, text = remote_file(self.install.url, ref, CHANGELOG_PATH, self._run_git)
            entries = parse_changelog(text)
            if not entries:
                raise UpdateError(f"no changelog entries at {commit[:7]}")
            mine = version_key(self.version)
            newer = [e for e in entries if version_key(e.version) > mine]
            self._seen[head] = Available(commit, entries[0].version, newer)
        return self._seen[head]

    def start(self) -> None:
        """Check now and then every `interval` seconds, in a daemon thread."""

        def loop() -> None:
            while True:
                self.check()
                time.sleep(self._interval)

        threading.Thread(target=loop, daemon=True, name="jobhunt-update-check").start()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update.py -v`
Expected: all pass (20 tests).

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src tests && uv run pytest`

```bash
git add src/jobhunt/update.py tests/test_update.py
git commit -m "update: Updater.check() compares the remote commit and reads its changelog

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The update job: `uv sync`, smoke test, restart in place

**Files:**
- Create: `src/jobhunt/__main__.py`
- Modify: `src/jobhunt/update.py`
- Test: `tests/test_update.py`

**Interfaces:**
- Consumes: `Updater`, `UpdateError`, `DIST` from earlier tasks.
- Produces: `Progress = Callable[[str], None]`; `Streamer = Callable[[list[str], Progress], int]`;
  `stream(argv: list[str], progress: Progress, cwd: Path | None = None) -> int`;
  `restart() -> None`; `Updater.__init__` gains keywords `stream: Streamer = stream`,
  `restart: Callable[[], None] = restart`, `restart_delay: float = 1.0`;
  `Updater.update(progress: Progress) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_update.py` (extend the import with `stream`; add `import sys` and
`import threading` at the top of the file, and drop the local `import threading` from
`test_start_checks_in_the_background`):

```python
def test_stream_feeds_lines_to_progress_and_returns_the_exit_code():
    lines = []
    script = "print('a'); print('b'); raise SystemExit(3)"
    assert stream([sys.executable, "-c", script], lines.append) == 3
    assert lines == ["a", "b"]


def test_stream_reports_a_missing_program():
    lines = []
    assert stream(["/no/such/program"], lines.append) == 127
    assert lines and lines[0].startswith("/no/such/program:")


class FakeStream:
    """Records argv; answers each call with the next exit code; emits one line per call."""

    def __init__(self, *codes):
        self.codes, self.calls = list(codes), []

    def __call__(self, argv, progress):
        self.calls.append(argv)
        progress(f"ran {argv[0]}")
        return self.codes.pop(0)


def updater_for_update(tmp_path, monkeypatch, streamer, install=INSTALLED):
    monkeypatch.setenv("UV", "/opt/uv")
    restarted = threading.Event()
    updater = Updater(
        install,
        tmp_path,
        version="0.2.0",
        changelog=[],
        stream=streamer,
        restart=restarted.set,
        restart_delay=0.0,
    )
    return updater, restarted


def test_update_syncs_smoke_tests_and_restarts(tmp_path, monkeypatch):
    streamer = FakeStream(0, 0)
    updater, restarted = updater_for_update(tmp_path, monkeypatch, streamer)
    log = []
    updater.update(log.append)
    assert streamer.calls == [
        ["/opt/uv", "sync", "--upgrade-package", "jobhunt", "--project", str(tmp_path)],
        [sys.executable, "-c", "import jobhunt.web.app"],
    ]
    assert log[0] == f"$ uv sync --upgrade-package jobhunt --project {tmp_path}"
    assert "ran /opt/uv" in log and log[-1] == "restarting…"
    assert restarted.wait(2)


def test_update_stops_when_uv_sync_fails(tmp_path, monkeypatch):
    streamer = FakeStream(1)
    updater, restarted = updater_for_update(tmp_path, monkeypatch, streamer)
    with pytest.raises(UpdateError, match="uv sync failed"):
        updater.update(lambda line: None)
    assert len(streamer.calls) == 1 and not restarted.wait(0.2)


def test_update_does_not_restart_a_release_that_does_not_import(tmp_path, monkeypatch):
    streamer = FakeStream(0, 1)
    updater, restarted = updater_for_update(tmp_path, monkeypatch, streamer)
    with pytest.raises(UpdateError, match="does not import"):
        updater.update(lambda line: None)
    assert not restarted.wait(0.2)


def test_update_refuses_an_editable_install(tmp_path, monkeypatch):
    updater, _ = updater_for_update(
        tmp_path, monkeypatch, FakeStream(), install=EngineInstall(editable=True)
    )
    with pytest.raises(UpdateError, match="development checkout"):
        updater.update(lambda line: None)


def test_update_needs_uv(tmp_path, monkeypatch):
    updater, _ = updater_for_update(tmp_path, monkeypatch, FakeStream())
    monkeypatch.delenv("UV")
    monkeypatch.setattr("jobhunt.update.shutil.which", lambda name: None)
    with pytest.raises(UpdateError, match="uv not found"):
        updater.update(lambda line: None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_update.py -v`
Expected: FAIL at import with `ImportError: cannot import name 'stream'`

- [ ] **Step 3: Implement `stream`, `restart`, and `Updater.update`**

Add `import shutil` to the imports of `src/jobhunt/update.py`. Below the `GitRunner` alias add:

```python
Progress = Callable[[str], None]
Streamer = Callable[[list[str], Progress], int]


def stream(argv: list[str], progress: Progress, cwd: Path | None = None) -> int:
    """Run a command, feeding each output line to `progress`; returns its exit code."""
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
    except OSError as exc:
        progress(f"{argv[0]}: {exc}")
        return 127
    assert proc.stdout is not None
    for line in proc.stdout:
        progress(line.rstrip("\n"))
    return proc.wait()


def restart() -> None:
    """Replace this process with a fresh `jobhunt serve` on the same arguments.

    Sockets are close-on-exec, so the port is free for the new process. The trailing
    --no-open wins over an earlier --open so the browser does not get a second tab.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-m", "jobhunt", *sys.argv[1:], "--no-open"])
```

Extend `Updater.__init__`'s signature and body:

```python
    def __init__(
        self,
        install: EngineInstall,
        project: Path,
        *,
        version: str | None = None,
        changelog: list[Entry] | None = None,
        run_git: GitRunner = run_git,
        stream: Streamer = stream,
        restart: Callable[[], None] = restart,
        restart_delay: float = 1.0,
        interval: float = 3600.0,
    ):
        self.install = install
        self.project = project  # the workspace: the uv project whose venv we run in
        self.version = version or installed_version()
        self.changelog = installed_changelog() if changelog is None else changelog
        self.available: Available | None = None
        self._run_git = run_git
        self._stream = stream
        self._restart = restart
        self._restart_delay = restart_delay
        self._interval = interval
        self._seen: dict[str, Available] = {}  # remote commit -> what it offers
```

and add the method after `start`:

```python
    def update(self, progress: Progress) -> None:
        """The `update` job: uv sync, import the new code once, then restart the server."""
        if self.install.editable:
            raise UpdateError("development checkout — update it with git pull")
        uv = os.environ.get("UV") or shutil.which("uv")
        if not uv:
            raise UpdateError("uv not found (not on PATH and $UV unset)")
        argv = [uv, "sync", "--upgrade-package", DIST, "--project", str(self.project)]
        progress("$ uv " + " ".join(argv[1:]))
        if self._stream(argv, progress) != 0:
            raise UpdateError("uv sync failed")
        progress("checking that the new engine imports…")
        if self._stream([sys.executable, "-c", "import jobhunt.web.app"], progress) != 0:
            raise UpdateError("the new engine does not import; not restarting")
        # Let the job reach `done` and the browser see it before the process is replaced.
        progress("restarting…")
        threading.Timer(self._restart_delay, self._restart).start()
```

Create `src/jobhunt/__main__.py` so `python -m jobhunt` works:

```python
"""`python -m jobhunt`: what the self-update restarts into."""

from jobhunt.cli import app

app()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_update.py -v`
Expected: all pass (27 tests).

- [ ] **Step 5: Check `-m jobhunt` and the double flag by hand**

Run: `uv run python -m jobhunt --help`
Expected: the usual command list.

Run this to confirm Click lets the trailing `--no-open` win over an earlier `--open`:

```bash
uv run python -c "
import click
@click.command()
@click.option('--open/--no-open', 'open_browser', default=True)
def c(open_browser): print(open_browser)
c(['--open', '--no-open'], standalone_mode=False)
"
```

Expected: `False`.

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check src tests && uv run pytest`

```bash
git add src/jobhunt/update.py src/jobhunt/__main__.py tests/test_update.py
git commit -m "update: Updater.update() runs uv sync, smoke-tests the new engine, restarts in place

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Banner on every page, `/actions/update`, `/health`

**Files:**
- Modify: `src/jobhunt/web/app.py`, `src/jobhunt/web/templates/base.html`,
  `src/jobhunt/web/templates/dashboard.html` (job panel only), `src/jobhunt/web/static/style.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Updater`, `Available`, `EngineInstall`, `Entry` from `jobhunt.update`.
- Produces: `create_app(ws, jobs=None, updater=None)`; template context on every page:
  `update: Available | None`, `busy: bool`, `version: str`; routes `POST /actions/update`,
  `GET /health -> {"version": str, "boot": str}`; the dashboard job panel carries
  `data-name` and `data-boot`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_web.py`, add to the imports:

```python
from pathlib import Path

from jobhunt.update import Available, EngineInstall, Entry, Updater
```

Replace the `client` fixture with:

```python
GIT_INSTALL = EngineInstall(url="https://github.com/x/jobhunt", commit="a" * 40, editable=False)
ENTRIES = [
    Entry("0.2.0", "2026-09-20", ["Update banner."]),
    Entry("0.1.0", "2026-09-19", ["Browser UI."]),
]


def updater(ws, install=GIT_INSTALL, available=None, **kw):
    """An Updater that never touches git; `available` is what the last check would have found."""
    u = Updater(install, ws.paths.root, version="0.2.0", changelog=ENTRIES, **kw)
    u.available = available
    return u


def web(ws, updater=None, jobs=None):
    return TestClient(create_app(ws, jobs or JobRunner(background=False), updater))


@pytest.fixture
def client(ws):
    return web(ws, updater(ws))
```

Append:

```python
# -- self-update ---------------------------------------------------------------------

NEWER = Available("b" * 40, "0.3.0", [Entry("0.3.0", "2026-09-21", ["Faster scoring."])])


def test_no_banner_when_up_to_date(client):
    assert 'class="update"' not in client.get("/").text


def test_banner_shows_the_new_version_and_notes_on_every_page(ws):
    client = web(ws, updater(ws, available=NEWER))
    for path in ("/", "/queue", "/files/profile"):
        page = client.get(path).text
        assert "jobhunt 0.3.0 is available" in page and "you have 0.2.0" in page
        assert "Faster scoring." in page and 'action="/actions/update"' in page
        assert "<button type=\"submit\">Update &amp; restart</button>" in page


def test_banner_flags_a_commit_without_a_version_bump(ws):
    client = web(ws, updater(ws, available=Available("b" * 40, "0.2.0", [])))
    page = client.get("/").text
    assert "still 0.2.0" in page and "no changelog entry" in page


def test_banner_button_is_disabled_while_a_job_runs(ws):
    jobs = JobRunner()  # real background thread
    client = web(ws, updater(ws, available=NEWER), jobs)
    release = threading.Event()
    jobs.start("check", lambda progress: release.wait(5))
    try:
        page = client.get("/").text
        assert '<button type="submit" disabled>Update &amp; restart</button>' in page
    finally:
        release.set()


def test_update_action_runs_the_updater_as_a_job(ws, monkeypatch):
    monkeypatch.setenv("UV", "/opt/uv")
    calls = []

    def fake_stream(argv, progress):
        calls.append(argv)
        progress("synced")
        return 0

    restarted = threading.Event()
    u = updater(ws, available=NEWER, stream=fake_stream, restart=restarted.set, restart_delay=0.0)
    client = web(ws, u)
    r = client.post("/actions/update", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    job = client.get("/jobs/1").json()
    assert job["name"] == "update" and job["status"] == "done"
    assert "synced" in job["lines"] and job["lines"][-1] == "restarting…"
    assert calls[0][1:4] == ["sync", "--upgrade-package", "jobhunt"]
    assert restarted.wait(2)
    page = client.get("/").text
    assert 'data-name="update"' in page and "restarting…" in page


def test_health_reports_version_and_a_boot_id(client):
    health = client.get("/health").json()
    assert health["version"] == "0.2.0" and len(health["boot"]) == 32
    client.post("/actions/digest")
    assert f'data-boot="{health["boot"]}"' in client.get("/").text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_web.py -v`
Expected: the new tests FAIL (`create_app() takes from 1 to 2 positional arguments but 3 were
given`); the old ones pass.

- [ ] **Step 3: Wire the updater into the app**

In `src/jobhunt/web/app.py` add `import uuid` to the stdlib imports and
`from jobhunt.update import EngineInstall, Updater` next to the other jobhunt imports.

Change `create_app` and `render`:

```python
def create_app(ws: Workspace, jobs: JobRunner | None = None, updater: Updater | None = None):
    app = FastAPI(title="jobhunt", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    jobs = jobs or JobRunner()
    updater = updater or Updater(EngineInstall.detect(), ws.paths.root)
    boot = uuid.uuid4().hex  # changes when the process is replaced after an update

    def render(request: Request, template: str, status_code: int = 200, **context):
        context.setdefault("error", None)
        context.setdefault("notice", None)
        # every page: the update banner and whether its button may be pressed
        context["update"] = updater.available
        context["version"] = updater.version
        context["busy"] = bool(jobs.current and jobs.current.running)
        return templates.TemplateResponse(request, template, context, status_code=status_code)
```

Add `"boot": boot` to the dict `dashboard_context` returns. After `action_learn` add:

```python
    @app.post("/actions/update")
    def action_update(request: Request):
        return start(request, "update", updater.update)

    @app.get("/health")
    def health():
        return {"version": updater.version, "boot": boot}
```

- [ ] **Step 4: The banner and the job panel**

In `src/jobhunt/web/templates/base.html`, between `</header>` and `<main>`:

```html
{% if update %}
<section class="update">
  <div>
    {% if update.entries %}
    <b>⬆ jobhunt {{ update.version }} is available</b> — you have {{ version }}.
    {% for e in update.entries %}
    {% if update.entries|length > 1 %}<p class="muted">{{ e.version }} — {{ e.date }}</p>{% endif %}
    <ul>{% for note in e.notes %}<li>{{ note }}</li>{% endfor %}</ul>
    {% endfor %}
    {% else %}
    <b>⬆ A newer engine commit is on GitHub</b> but its version is still {{ version }} —
    no changelog entry. Update anyway?
    {% endif %}
  </div>
  <form method="post" action="/actions/update">
    <button type="submit"{% if busy %} disabled{% endif %}>Update &amp; restart</button>
  </form>
</section>
{% endif %}
```

In `src/jobhunt/web/templates/dashboard.html` delete the line
`{% set busy = job and job.running %}` (the context now provides `busy`) and change the job
panel's opening tag to:

```html
<section class="job" id="job" data-id="{{ job.id }}" data-name="{{ job.name }}"
         data-status="{{ job.status }}" data-lines="{{ job.lines|length }}" data-boot="{{ boot }}">
```

- [ ] **Step 5: Style it**

In `src/jobhunt/web/static/style.css` add `--warn: #ffd54a; --warn-fg: #3b2f00;` to the light
`:root` block and `--warn: #6b5300; --warn-fg: #ffe58a;` to the dark one. After the `.flash`
rules add:

```css
.update { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; padding: 0.8rem 1rem;
          background: var(--warn); color: var(--warn-fg); border-bottom: 1px solid var(--border); }
.update > div { flex: 1 1 20rem; }
.update p { margin: 0.4rem 0 0; color: inherit; opacity: 0.8; }
.update ul { margin: 0.3rem 0 0; padding-left: 1.2rem; }
.update button[type=submit] { background: var(--warn-fg); color: var(--warn);
                              border-color: var(--warn-fg); font-weight: 600; white-space: nowrap; }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web.py -v`
Expected: all pass.

- [ ] **Step 7: Lint and commit**

Run: `uv run ruff check src tests && uv run pytest`

```bash
git add src/jobhunt/web tests/test_web.py
git commit -m "web: update banner on every page, /actions/update job, /health boot id

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Dashboard changelog, restart-aware polling, `serve` wiring, docs

**Files:**
- Modify: `src/jobhunt/web/app.py` (dashboard context), `src/jobhunt/web/templates/dashboard.html`,
  `src/jobhunt/web/static/app.js`, `src/jobhunt/web/static/style.css`, `src/jobhunt/cli.py`
  (`serve`), `README.md`, `src/jobhunt/templates/README.md.tmpl`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Updater.version`, `.changelog`, `.install`, `.start()`; `data-name`/`data-boot` on
  the job panel; `GET /health`.
- Produces: dashboard context keys `changelog: list[Entry]`, `install: EngineInstall`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web.py`:

```python
def test_dashboard_shows_whats_new(client):
    page = client.get("/").text
    assert "What's new" in page and "jobhunt 0.2.0" in page
    assert "Update banner." in page and "Browser UI." in page
    assert "development checkout" not in page


def test_dashboard_folds_older_entries_and_notes_a_development_checkout(ws):
    entries = [Entry(f"0.{n}.0", "2026-09-20", [f"note {n}"]) for n in range(7, 0, -1)]
    u = updater(ws, install=EngineInstall(editable=True, path=Path("/src/jobhunt")))
    u.changelog = entries
    page = web(ws, u).get("/").text
    assert "note 7" in page and "note 3" in page
    assert page.index("<summary>") < page.index("note 2")
    assert "development checkout" in page and "/src/jobhunt" in page
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_web.py -k whats_new -v`
Expected: FAIL with `assert "What's new" in page`

- [ ] **Step 3: Render the changelog on the dashboard**

In `src/jobhunt/web/app.py` add to the dict `dashboard_context` returns:

```python
            "changelog": updater.changelog,
            "install": updater.install,
```

In `src/jobhunt/web/templates/dashboard.html`, right after the `Newest digest` paragraph and
before `<h2>Actions</h2>`, insert:

```html
{% macro entry(e) -%}
<h3>{{ e.version }} <small class="muted">{{ e.date }}</small></h3>
<ul>{% for note in e.notes %}<li>{{ note }}</li>{% endfor %}</ul>
{%- endmacro %}
<h2>What's new <small class="muted">jobhunt {{ version }}</small></h2>
{% if install.editable %}
<p class="muted">development checkout{% if install.path %} at <code>{{ install.path }}</code>{% endif %}
  — update it with <code>git pull</code></p>
{% endif %}
<section class="changelog">
{% for e in changelog[:5] %}{{ entry(e) }}{% endfor %}
{% if changelog|length > 5 %}
<details><summary>older versions</summary>
{% for e in changelog[5:] %}{{ entry(e) }}{% endfor %}
</details>
{% endif %}
</section>
```

Append to `src/jobhunt/web/static/style.css`:

```css
.changelog h3 { font-size: 1rem; margin: 0.7rem 0 0.1rem; }
.changelog ul { margin: 0; }
.changelog summary { cursor: pointer; color: var(--muted); margin-top: 0.6rem; }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_web.py -v`
Expected: all pass.

- [ ] **Step 5: Wait for the restart in the browser**

Replace the first block of `src/jobhunt/web/static/app.js` (the job poller) with:

```js
// Dashboard: while a job is running, poll its status once a second and append new log lines;
// reload once it finishes so the counts refresh. After an `update` job the server replaces
// itself, so wait until /health reports a new boot id before reloading.
(function () {
  const panel = document.getElementById("job");
  if (!panel || panel.dataset.status !== "running") return;
  const log = document.getElementById("job-log");
  const status = document.getElementById("job-status");
  let shown = Number(panel.dataset.lines);
  const awaitRestart = async (tries) => {
    status.textContent = "restarting…";
    try {
      const r = await fetch("/health", { cache: "no-store" });
      if (r.ok && (await r.json()).boot !== panel.dataset.boot) return location.reload();
    } catch (e) {}
    if (tries >= 60) return location.reload();
    setTimeout(() => awaitRestart(tries + 1), 1000);
  };
  const tick = async () => {
    let job;
    try {
      const r = await fetch(`/jobs/${panel.dataset.id}`);
      if (!r.ok) return;
      job = await r.json();
    } catch (e) {
      setTimeout(tick, 2000);
      return;
    }
    for (const line of job.lines.slice(shown)) log.textContent += line + "\n";
    shown = job.lines.length;
    status.textContent = job.status;
    if (job.status === "running") setTimeout(tick, 1000);
    else if (job.status === "done" && panel.dataset.name === "update") awaitRestart(0);
    else location.reload();
  };
  setTimeout(tick, 1000);
})();
```

- [ ] **Step 6: Start the background check in `serve`**

In `src/jobhunt/cli.py`'s `serve`, change the local imports and the body to:

```python
    import threading
    import webbrowser

    import uvicorn

    from jobhunt.update import EngineInstall, Updater
    from jobhunt.web.app import create_app

    ws = _workspace(ctx)
    updater = Updater(EngineInstall.detect(), ws.paths.root)
    updater.start()  # hourly `git ls-remote`; the UI shows a banner when the engine moved
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    typer.echo(f"jobhunt UI: {url} (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(ws, updater=updater), host=host, port=port, log_level="warning")
```

- [ ] **Step 7: Document it**

In `README.md`, in the `## Web UI` bullet list, add after the **Dashboard** bullet:

```markdown
- **What's new** — the engine's changelog, and an amber banner on every page when GitHub has a
  newer engine: it lists the new entries and **Update & restart** runs
  `uv sync --upgrade-package jobhunt`, checks the new code imports, and restarts the server in
  place (the page reloads by itself). Development checkouts are not updated this way.
```

In `src/jobhunt/templates/README.md.tmpl` change the `Engine updates:` line to:

```markdown
Engine updates: the browser UI shows a banner with an *Update & restart* button when a newer
engine is on GitHub; from a terminal, `uv sync --upgrade-package jobhunt` (the first run pins a
commit in `uv.lock`).
```

- [ ] **Step 8: Try it for real**

From a workspace whose `pyproject.toml` installs the engine from git (a temporary one is fine:
`uv run jobhunt init /tmp/ws-update --engine git@github.com:jessefontaine/jobhunt.git`, then
`cd /tmp/ws-update && uv sync`), run `uv run jobhunt serve --no-open` and open the URL:

- Dashboard shows **What's new** with 0.2.0 on top.
- With the `self-update` branch not yet on `main`, the terminal prints nothing and no banner
  shows (remote HEAD == installed commit) — or, if `main` has moved, the banner appears within a
  second of startup.
- To see the whole flow: push a throwaway commit to a scratch branch, `init` a workspace with
  `--engine git+…@<branch>`, then push a version bump + changelog entry to that branch, restart
  `serve`, and press **Update & restart**: the job log shows `uv sync`, `restarting…`, the
  status shows `restarting…`, and the page comes back on the new version with no banner.

- [ ] **Step 9: Lint, full suite, commit**

Run: `uv run ruff check src tests && uv run pytest`

```bash
git add src/jobhunt/web src/jobhunt/cli.py README.md src/jobhunt/templates/README.md.tmpl tests/test_web.py
git commit -m "web: dashboard changelog; wait for the restart after an update; serve starts the check

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
