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


def test_queue_lists_open_unrated_best_first(client, ws):
    fetched(ws)
    ws.score()
    page = client.get("/queue").text
    assert "PhD vision" in page and "RA fMRI" in page and "Old job" not in page
    assert page.index("score 90") < page.index("score 80")
    assert "<b>Why:</b> why" in page
    assert "Deep nets for vision" in page  # description falls back to the summary
    assert "Unscored" not in page


def test_queue_puts_unscored_listings_last(client, ws):
    fetched(ws)
    page = client.get("/queue").text
    assert "<h2>Unscored</h2>" in page
    assert '<h1>Queue <small class="muted">2</small></h1>' in page


def test_rating_moves_listing_from_queue_to_shortlist_and_rated(client, ws):
    fetched(ws)
    lid = listing_id(ws, "PhD vision")
    r = client.post("/ratings", data={"listing_id": lid, "rating": "5", "note": " dream lab "})
    assert r.status_code == 200
    assert r.json() == {
        "listing_id": lid,
        "rating": 5,
        "note": "dream lab",
        "changed": True,
        "since_learned": 1,
    }
    assert ws.store.get_rating(lid).rating == 5
    assert '"digest":"web"' in ws.paths.ratings.read_text()
    assert "PhD vision" in ws.paths.shortlist.read_text()
    assert "PhD vision" not in client.get("/queue").text
    shortlist = client.get("/shortlist").text
    assert "PhD vision" in shortlist and "dream lab" in shortlist
    assert 'value="5" class="selected"' in shortlist
    assert "PhD vision" in client.get("/rated").text
    again = client.post("/ratings", data={"listing_id": lid, "rating": "5", "note": "dream lab"})
    assert again.json()["changed"] is False


def test_rated_page_marks_expired_listings(client, ws):
    fetched(ws)
    client.post("/ratings", data={"listing_id": listing_id(ws, "Old job"), "rating": "3"})
    page = client.get("/rated").text
    assert "Old job" in page and "(expired)" in page
    assert "Old job" not in client.get("/shortlist").text


def test_rating_validation(client, ws):
    fetched(ws)
    assert client.post("/ratings", data={"listing_id": "nope", "rating": "5"}).status_code == 404
    lid = listing_id(ws, "PhD vision")
    assert client.post("/ratings", data={"listing_id": lid, "rating": "9"}).status_code == 422
    assert client.post("/ratings", data={"listing_id": lid}).status_code == 422
