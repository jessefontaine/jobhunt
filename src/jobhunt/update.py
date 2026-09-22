"""Engine self-update: what is installed, what the remote has, and how to switch to it.

Nothing here imports from jobhunt.web; the web app injects an Updater.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata, resources
from pathlib import Path

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


# -- the object the web app holds -------------------------------------------------------


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Status:
    """What the update check is doing, for the dashboard and `jobhunt update --check`."""

    version: str
    tracking: str | None  # "<url>@<ref>" when checks are on
    commit: str | None  # the installed commit
    checked_at: datetime | None
    error: str | None  # the last check's git error, if it failed
    reason: str | None  # why checks are off; None means they are on
    available: Available | None

    @property
    def checking(self) -> bool:
        return self.reason is None


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
        stream: Streamer = stream,
        restart: Callable[[], None] = restart,
        restart_delay: float = 1.0,
        interval: float = 600.0,
        sleep: Callable[[float], None] = time.sleep,
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
        self._sleep = sleep
        self._seen: dict[str, Available] = {}  # remote commit -> what it offers
        self.checked_at: datetime | None = None
        self.last_error: str | None = None

    def disabled_reason(self) -> str | None:
        """Why this install cannot be checked for updates, or None when it can be."""
        if self.install.editable:
            where = f" at {self.install.path}" if self.install.path else ""
            return f"development checkout{where} — update it with git pull"
        if not self.install.url:
            return "this engine was not installed from git — update it the way you installed it"
        if self.install.branch and COMMIT_RE.match(self.install.branch):
            return (
                f"pinned to commit {self.install.branch[:7]} — checks are off until the pin is "
                "removed from the workspace pyproject.toml"
            )
        return None

    def status(self) -> Status:
        reason = self.disabled_reason()
        ref = self.install.branch or "HEAD"
        return Status(
            version=self.version,
            tracking=None if reason else f"{self.install.url}@{ref}",
            commit=self.install.commit,
            checked_at=self.checked_at,
            error=self.last_error,
            reason=reason,
            available=self.available,
        )

    def check(self) -> Available | None:
        """Refresh `available`; a failed check is one stderr line and leaves it unchanged."""
        if self.disabled_reason():
            return None
        try:
            self.available = self._check()
            self.last_error = None
        except UpdateError as exc:
            self.last_error = str(exc)
            print(f"update check: {exc}", file=sys.stderr)
        self.checked_at = datetime.now()
        return self.available

    def _check(self) -> Available | None:
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
                self._sleep(self._interval)

        threading.Thread(target=loop, daemon=True, name="jobhunt-update-check").start()

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
