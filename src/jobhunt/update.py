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
