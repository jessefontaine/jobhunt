import threading
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobhunt.update import Available, EngineInstall, Entry, Updater
from jobhunt.web.app import create_app
from jobhunt.web.jobs import JobRunner

GIT_INSTALL = EngineInstall(url="https://github.com/x/jobhunt", commit="a" * 40, editable=False)
ENTRIES = [
    Entry("0.2.0", "2026-09-20", ["Update banner."]),
    Entry("0.1.0", "2026-09-19", ["Browser UI."]),
]


def updater(ws, install=GIT_INSTALL, available=None, **kw):
    """An Updater that never touches git; `available` is what the last check would have found."""
    u = Updater(install, ws.paths.root, version="0.2.0", changelog=ENTRIES, **kw)
    u.available = available
    return u


def web(ws, updater=None, jobs=None):
    return TestClient(create_app(ws, jobs or JobRunner(background=False), updater))


@pytest.fixture
def client(ws):
    return web(ws, updater(ws))


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


def test_profile_editor_round_trips(client, ws):
    assert "PROFILE" in client.get("/files/profile").text
    r = client.post("/files/profile", data={"text": "new\r\nprofile"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/files/profile?saved=1"
    assert ws.paths.profile.read_text() == "new\nprofile\n"
    page = client.get("/files/profile?saved=1").text
    assert "rescore" in page and "new\nprofile" in page


def test_missing_file_shows_an_empty_editor(client, ws):
    r = client.get("/files/cv")
    assert r.status_code == 200 and "<textarea" in r.text
    client.post("/files/cv", data={"text": "# CV"})
    assert ws.paths.cv.read_text() == "# CV\n"


def test_sources_editor_rejects_invalid_yaml_and_keeps_the_file(client, ws):
    before = ws.paths.sources_yaml.read_text()
    r = client.post("/files/sources", data={"text": "sources: [\n"})
    assert r.status_code == 400 and "not saved: invalid YAML" in r.text
    assert "sources: [" in r.text  # the unsaved text is still in the editor
    assert ws.paths.sources_yaml.read_text() == before
    r = client.post("/files/sources", data={"text": "scoring: {batch_size: x}\n"})
    assert r.status_code == 400


def test_sources_editor_save_reloads_config(client, ws):
    page = client.get("/files/sources").text
    assert "<td>fixture</td><td>disabled</td>" in page
    r = client.post(
        "/files/sources",
        data={"text": "sources:\n  fixture:\n    enabled: true\n"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert ws.config.enabled_sources() == ["fixture"]
    page = client.get("/files/sources?saved=1").text
    assert "<td>fixture</td><td>enabled</td>" in page and "Saved." in page
    assert '<option value="fixture">' in client.get("/").text


def test_unknown_file_is_404(client):
    assert client.get("/files/etc-passwd").status_code == 404
    assert client.post("/files/etc-passwd", data={"text": "x"}).status_code == 404


# -- self-update ---------------------------------------------------------------------

NEWER = Available("b" * 40, "0.3.0", [Entry("0.3.0", "2026-09-21", ["Faster scoring."])])


def test_no_banner_when_up_to_date(client):
    assert 'class="update"' not in client.get("/").text


def test_banner_shows_the_new_version_and_notes_on_every_page(ws):
    client = web(ws, updater(ws, available=NEWER))
    for path in ("/", "/queue", "/files/profile"):
        page = client.get(path).text
        assert "jobhunt 0.3.0 is available" in page and "you have 0.2.0" in page
        assert "Faster scoring." in page and 'action="/actions/update"' in page
        assert "<button type=\"submit\">Update &amp; restart</button>" in page


def test_banner_flags_a_commit_without_a_version_bump(ws):
    client = web(ws, updater(ws, available=Available("b" * 40, "0.2.0", [])))
    page = client.get("/").text
    assert "still 0.2.0" in page and "no changelog entry" in page


def test_banner_button_is_disabled_while_a_job_runs(ws):
    jobs = JobRunner()  # real background thread
    client = web(ws, updater(ws, available=NEWER), jobs)
    release = threading.Event()
    jobs.start("check", lambda progress: release.wait(5))
    try:
        page = client.get("/").text
        assert '<button type="submit" disabled>Update &amp; restart</button>' in page
    finally:
        release.set()


def test_update_action_runs_the_updater_as_a_job(ws, monkeypatch):
    monkeypatch.setenv("UV", "/opt/uv")
    calls = []

    def fake_stream(argv, progress):
        calls.append(argv)
        progress("synced")
        return 0

    restarted = threading.Event()
    u = updater(ws, available=NEWER, stream=fake_stream, restart=restarted.set, restart_delay=0.0)
    client = web(ws, u)
    r = client.post("/actions/update", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    job = client.get("/jobs/1").json()
    assert job["name"] == "update" and job["status"] == "done"
    assert "synced" in job["lines"] and job["lines"][-1] == "restarting…"
    assert calls[0][1:4] == ["sync", "--upgrade-package", "jobhunt"]
    assert restarted.wait(2)
    page = client.get("/").text
    assert 'data-name="update"' in page and "restarting…" in page


def test_health_reports_version_and_a_boot_id(client):
    health = client.get("/health").json()
    assert health["version"] == "0.2.0" and len(health["boot"]) == 32
    client.post("/actions/digest")
    assert f'data-boot="{health["boot"]}"' in client.get("/").text


def test_every_action_says_when_to_use_it(client):
    page = client.get("/").text
    # one "when to use this" line under each of the five action buttons
    assert page.count('<p class="when">') == 5
    assert "Your normal daily run" in page
    assert "never touches the rules you wrote yourself" in page


def test_dashboard_shows_whats_new_folded(client):
    page = client.get("/").text
    assert "What's new" in page and "jobhunt 0.2.0" in page
    assert "Update banner." in page and "Browser UI." in page
    assert "development checkout" not in page
    # the whole section is a closed <details>; only its heading shows until clicked
    assert '<details class="changelog">' in page
    assert page.index('<details class="changelog">') < page.index("What's new")
    assert page.index("What's new") < page.index("</summary>") < page.index("Update banner.")


def test_dashboard_folds_older_entries_and_notes_a_development_checkout(ws):
    entries = [Entry(f"0.{n}.0", "2026-09-20", [f"note {n}"]) for n in range(7, 0, -1)]
    u = updater(ws, install=EngineInstall(editable=True, path=Path("/src/jobhunt")))
    u.changelog = entries
    page = web(ws, u).get("/").text
    assert "note 7" in page and "note 3" in page
    assert page.index("<summary>older versions</summary>") < page.index("note 2")
    assert "development checkout" in page and "/src/jobhunt" in page


def test_dashboard_links_to_prefilled_issue_forms(client):
    page = client.get("/").text
    assert "https://github.com/jessefontaine/jobhunt/issues/new?template=bug_report.yml" in page
    assert (
        "https://github.com/jessefontaine/jobhunt/issues/new?template=feature_request.yml" in page
    )
    assert "environment=jobhunt+0.2.0+%28aaaaaaa%29" in page
