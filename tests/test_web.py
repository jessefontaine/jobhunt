import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient

from jobhunt.web.app import create_app
from jobhunt.web.jobs import JobRunner


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws, JobRunner(background=False)))


def fetched(ws):
    """Load the three fixture listings into the workspace store."""
    ws.fetch(fixture=ws.paths.root / "listings.json")
    return ws


def listing_id(ws, title):
    listings = ws.store.unexpired_listings(date(1999, 1, 1))  # includes the expired one
    return next(lst.id for lst in listings if lst.title == title)


def test_dashboard_shows_counts_and_no_job(client, ws):
    fetched(ws)
    r = client.get("/")
    assert r.status_code == 200
    assert "<b>2</b> open, unrated" in r.text  # 'Old job' is expired
    assert "(2 unscored)" in r.text
    assert "<b>3</b> listings seen" in r.text
    assert "<b>0</b> on the shortlist" in r.text
    assert "0 rating(s) since the start" in r.text
    assert "No job has run yet" in r.text
    assert "Newest digest: none yet" in r.text


def test_static_stylesheet_is_served(client):
    assert client.get("/static/style.css").status_code == 200


def test_check_action_runs_the_pipeline_and_reports_progress(client, ws):
    fetched(ws)
    r = client.post("/actions/check", data={"source": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    job = client.get("/jobs/1").json()
    assert job["name"] == "check" and job["status"] == "done" and job["error"] is None
    assert "fetched: 0 new listing(s) from 0 source(s)" in job["lines"]
    assert "scored: 2 listing(s), 0 failed" in job["lines"]
    assert job["lines"][-1].startswith("digest: ")
    assert list(ws.paths.digests.glob("*.md"))
    page = client.get("/").text
    assert "scored: 2 listing(s)" in page and 'data-status="done"' in page
    assert "(2 unscored)" not in page


def test_check_action_honours_the_option_boxes(client, ws):
    fetched(ws)
    client.post("/actions/check", data={"source": "", "no_score": "1"})
    job = client.get("/jobs/1").json()
    assert not any(line.startswith("scored:") for line in job["lines"])


def test_score_and_learn_actions(client, ws):
    fetched(ws)
    client.post("/actions/score", data={})
    assert "scored: 2 listing(s), 0 failed" in client.get("/jobs/1").json()["lines"]
    client.post("/actions/score", data={"rescore": "1"})
    assert any("rescoring 2" in line for line in client.get("/jobs/2").json()["lines"])
    client.post("/actions/learn")
    assert client.get("/jobs/3").json()["lines"][-1] == "preferences: updated"
    assert "0 rating(s) since 20" in client.get("/").text  # dated marker now


def test_failed_job_is_reported_not_raised(client, ws):
    fetched(ws)
    ws.runner = lambda *a: (_ for _ in ()).throw(RuntimeError("claude exited 1"))
    client.post("/actions/learn")
    job = client.get("/jobs/1").json()
    assert job["status"] == "done" and job["lines"][-1].startswith("preferences: failed")


def test_unknown_job_is_404(client):
    assert client.get("/jobs/42").status_code == 404


def test_second_action_while_running_is_409(ws):
    jobs = JobRunner()  # real background thread
    client = TestClient(create_app(ws, jobs))
    release = threading.Event()
    jobs.start("check", lambda progress: release.wait(5))
    try:
        r = client.post("/actions/score", data={})
        assert r.status_code == 409
        assert "a job is already running" in r.text
        assert '<button type="submit" disabled>' in r.text
    finally:
        release.set()
