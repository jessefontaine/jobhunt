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
