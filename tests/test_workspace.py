from jobhunt.models import Rating
from jobhunt.settings import Settings, save_settings
from jobhunt.workspace import Workspace


def test_settings_come_from_the_settings_file(ws):
    settings = Settings()
    settings.display.theme = "dark"
    settings.digest.limit = 2
    save_settings(ws.paths, settings)
    reopened = Workspace.open(ws.paths.root, runner=ws.runner)
    assert reopened.settings.display.theme == "dark"
    assert reopened.settings.digest.limit == 2


def test_digest_honours_the_settings_limit(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    ws.settings.digest.limit = 1
    text = ws.digest().read_text()
    assert "showing 1 of 2" in text  # 3 fetched, one expired


def test_shortlist_honours_the_rating_threshold(ws):
    ws.fetch(fixture=ws.paths.root / "listings.json")
    lst = ws.store.candidate_listings(__import__("datetime").date.today())[0]
    ws.store.save_rating(Rating(listing_id=lst.id, rating=3))
    assert lst.title not in ws.shortlist()
    ws.settings.shortlist.min_rating = 3
    assert lst.title in ws.shortlist()


def test_check_with_fixture_reports_each_stage(ws):
    lines = []
    path = ws.check(fixture=ws.paths.root / "listings.json", progress=lines.append)
    assert path.exists() and path.parent == ws.paths.digests
    assert "fetched: 3 new listing(s) from 1 source(s)" in lines
    assert "scored: 2 listing(s), 0 failed" in lines  # 'Old job' is expired
    assert lines[-1] == f"digest: {path}"
    assert ws.paths.shortlist.exists()
    assert ws.store.count_listings() == 3


def test_check_no_score_skips_claude(ws):
    lines = []
    ws.check(fixture=ws.paths.root / "listings.json", no_score=True, progress=lines.append)
    assert not any(line.startswith("scored:") for line in lines)


def test_store_gives_a_fresh_connection_each_time(ws):
    assert ws.store is not ws.store


def test_learn_reports_outcome_and_rewrites_learned(ws):
    lines = []
    assert ws.learn(progress=lines.append)
    assert lines[0].startswith("regenerating preferences with sonnet")
    assert lines[-1] == "preferences: updated"
    assert "- learned rule" in ws.paths.preferences.read_text()
    assert "- mine" in ws.paths.preferences.read_text()


def test_learn_reports_failure(ws):
    ws.runner = lambda *a: "garbage"
    lines = []
    assert not ws.learn(progress=lines.append)
    assert lines[-1] == "preferences: failed (file left untouched)"
