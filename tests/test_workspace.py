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


MANUAL_HTML = (
    "<html><head><meta property='og:site_name' content='Example Institute'></head>"
    "<body><main><h1>PhD in vision</h1><p>Study vision with 7T fMRI.</p></main></body></html>"
)


class FakeHTTP:
    def __init__(self, html):
        self.html = html
        self.calls = []

    def get_text(self, url):
        self.calls.append(url)
        return self.html


def test_add_stores_a_pasted_link_and_scores_it(ws):
    listing, score = ws.add("https://example.org/j/1", http=FakeHTTP(MANUAL_HTML))
    assert listing.title == "PhD in vision"
    assert listing.employer == "Example Institute"
    assert listing.source == "manual"
    assert ws.store.get_listing(listing.id) is not None
    assert score is not None and score.score == 90


def test_add_can_skip_scoring(ws):
    listing, score = ws.add("https://example.org/j/1", http=FakeHTTP(MANUAL_HTML), score=False)
    assert score is None
    assert ws.store.get_score(listing.id) is None


def test_add_with_given_fields_never_fetches_the_page(ws):
    http = FakeHTTP(MANUAL_HTML)
    listing, _ = ws.add(
        "https://example.org/j/2", title="RA in EEG", employer="UvA", http=http, score=False
    )
    assert http.calls == []
    assert listing.title == "RA in EEG" and listing.employer == "UvA"


def test_add_says_when_the_link_is_already_in_the_store(ws):
    lines = []
    ws.add("https://example.org/j/1", http=FakeHTTP(MANUAL_HTML), progress=lines.append)
    lines.clear()
    listing, score = ws.add(
        "https://example.org/j/1/", http=FakeHTTP(MANUAL_HTML), progress=lines.append
    )
    assert any("already" in line for line in lines)
    assert score is not None  # the score it already had
    assert ws.store.count_listings() == 1
