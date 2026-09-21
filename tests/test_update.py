"""Engine self-update: changelog format, install detection, remote check, update job."""

import re
import threading
import tomllib
from pathlib import Path

import pytest

from jobhunt.update import (
    Available,
    EngineInstall,
    Entry,
    UpdateError,
    Updater,
    installed_changelog,
    installed_version,
    parse_changelog,
    remote_file,
    remote_head,
    run_git,
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
    checked = threading.Event()

    def git(args, cwd):
        checked.set()
        return "a" * 40 + "\tHEAD\n"

    Updater(INSTALLED, tmp_path, version="0.2.0", changelog=[], run_git=git, interval=3600).start()
    assert checked.wait(2)
