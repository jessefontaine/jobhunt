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
