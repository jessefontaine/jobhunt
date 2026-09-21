import threading
import time

import pytest

from jobhunt.web.jobs import JobBusy, JobRunner


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


def test_inline_runner_collects_lines_and_finishes_done():
    runner = JobRunner(background=False)
    job = runner.start("check", lambda progress: [progress("a"), progress("b")])
    assert job.status == "done" and job.lines == ["a", "b"]
    assert job.error is None and job.finished_at is not None
    assert runner.get(job.id) is job and runner.current is job
    assert job.as_dict()["lines"] == ["a", "b"]
    assert job.as_dict()["finished_at"].startswith("20")


def test_exception_marks_job_failed_with_message():
    def boom(progress):
        progress("starting")
        raise RuntimeError("claude exited 1")

    job = JobRunner(background=False).start("score", boom)
    assert job.status == "failed"
    assert job.error == "RuntimeError: claude exited 1"
    assert job.lines == ["starting", "RuntimeError: claude exited 1"]


def test_background_runner_refuses_a_second_job_while_running():
    runner = JobRunner()
    release = threading.Event()
    started = threading.Event()

    def slow(progress):
        started.set()
        release.wait(5)
        progress("finished")

    job = runner.start("check", slow)
    assert started.wait(5)
    assert job.running and runner.current is job
    with pytest.raises(JobBusy):
        runner.start("score", lambda progress: None)
    release.set()
    wait_until(lambda: not job.running)
    assert job.status == "done" and job.lines == ["finished"]
    second = runner.start("score", lambda progress: progress("ok"))
    wait_until(lambda: not second.running)
    assert second.id == job.id + 1 and runner.current is second


def test_unknown_job_is_none():
    runner = JobRunner()
    assert runner.get(99) is None and runner.current is None
