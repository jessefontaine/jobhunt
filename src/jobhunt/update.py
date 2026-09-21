"""Engine self-update: what is installed, what the remote has, and how to switch to it.

Nothing here imports from jobhunt.web; the web app injects an Updater.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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
