"""Engine self-update: changelog format, install detection, remote check, update job."""

import re
import sys
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
    stream,
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


def test_start_rechecks_every_ten_minutes_by_default(tmp_path):
    slept: list[float] = []
    parked = threading.Event()

    def git(args, cwd):
        return "a" * 40 + "\tHEAD\n"

    def sleep(seconds):
        slept.append(seconds)
        parked.set()
        threading.Event().wait()  # keep the daemon thread from looping again

    Updater(INSTALLED, tmp_path, version="0.2.0", changelog=[], run_git=git, sleep=sleep).start()
    assert parked.wait(2)
    assert slept == [600]


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


# -- status: why a check is off, and what the last one did ------------------


def test_status_reports_what_is_tracked_after_a_successful_check(tmp_path):
    updater = make_updater(tmp_path, FakeGit(head="b" * 40))
    updater.check()
    status = updater.status()
    assert status.tracking == f"{GIT_URL}@HEAD"
    assert status.commit == "a" * 40
    assert status.checked_at is not None
    assert status.error is None
    assert status.reason is None
    assert status.available is not None


def test_status_keeps_the_last_git_error(tmp_path):
    updater = make_updater(tmp_path, FakeGit(head="b" * 40, fail=True))
    updater.check()
    assert updater.status().error == "boom"


def test_a_later_success_clears_the_error(tmp_path):
    git = FakeGit(head="b" * 40, fail=True)
    updater = make_updater(tmp_path, git)
    updater.check()
    git.fail = False
    updater.check()
    assert updater.status().error is None


def test_status_explains_an_editable_install(tmp_path):
    install = EngineInstall(path=Path("/src/jobhunt"))
    updater = make_updater(tmp_path, FakeGit(head="b" * 40), install=install)
    assert "development checkout" in updater.status().reason
    assert "/src/jobhunt" in updater.status().reason


def test_status_explains_an_install_that_did_not_come_from_git(tmp_path):
    install = EngineInstall(url=None, editable=False)
    updater = make_updater(tmp_path, FakeGit(head="b" * 40), install=install)
    assert "not installed from git" in updater.status().reason


def test_a_commit_pin_turns_checks_off_instead_of_failing_every_time(tmp_path):
    git = FakeGit(head="b" * 40)
    pinned = EngineInstall(url=GIT_URL, commit="a" * 40, branch="c" * 40, editable=False)
    updater = make_updater(tmp_path, git, install=pinned)
    assert updater.check() is None
    assert git.calls == []  # ls-remote can never resolve a bare commit sha
    assert "pinned to commit ccccccc" in updater.status().reason


def test_status_says_checks_are_on_for_a_branch_pin(tmp_path):
    pinned = EngineInstall(url=GIT_URL, commit="a" * 40, branch="dev", editable=False)
    updater = make_updater(tmp_path, FakeGit(head="b" * 40), install=pinned)
    assert updater.status().reason is None
    assert updater.status().tracking == f"{GIT_URL}@dev"
