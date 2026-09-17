from datetime import date

from jobhunt.sources.pages import PagesSource, parse_page

HTML = """
<html><body>
<ul class="vacancies">
  <li class="vacancy">
    <h3><a href="/jobs/1">PhD in NeuroAI</a></h3>
    <span class="loc">Amsterdam</span>
    <span class="deadline">Deadline: 2026-10-31</span>
    <p>Join our lab.</p>
  </li>
  <li class="vacancy">
    <h3><a href="https://other.org/jobs/2">Research Technician</a></h3>
    <span class="loc">Nijmegen</span>
  </li>
  <li class="vacancy"><h3>No link here</h3></li>
</ul>
</body></html>
"""

PAGE = {
    "name": "Test Institute",
    "url": "https://inst.org/vacancies",
    "item": "li.vacancy",
    "title": "h3",
    "link": "a",
    "location": ".loc",
    "deadline": ".deadline",
    "summary": "p",
}


def test_parse_page_extracts_items_and_resolves_relative_links():
    listings = parse_page(HTML, PAGE)
    assert [lst.title for lst in listings] == ["PhD in NeuroAI", "Research Technician"]
    assert listings[0].url == "https://inst.org/jobs/1"
    assert listings[1].url == "https://other.org/jobs/2"
    assert listings[0].employer == "Test Institute"
    assert listings[0].source == "pages"
    assert listings[0].location == "Amsterdam"
    assert listings[0].deadline == date(2026, 10, 31)
    assert listings[0].summary == "Join our lab."
    assert listings[1].deadline is None and listings[1].summary == ""


def test_parse_page_minimal_config_uses_item_text_and_first_link():
    listings = parse_page(HTML, {"name": "X", "url": "https://inst.org/v", "item": "li.vacancy"})
    assert listings[0].title.startswith("PhD in NeuroAI")
    assert listings[0].url == "https://inst.org/jobs/1"


def test_parse_page_reads_date_in_common_formats():
    html = HTML.replace("Deadline: 2026-10-31", "Closes 31 October 2026")
    assert parse_page(html, PAGE)[0].deadline == date(2026, 10, 31)
    html = HTML.replace("Deadline: 2026-10-31", "31-10-2026")
    assert parse_page(html, PAGE)[0].deadline == date(2026, 10, 31)


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get_text(self, url):
        self.calls.append(url)
        if url not in self.routes:
            raise RuntimeError("404")
        return self.routes[url]


def test_fetch_iterates_configured_pages_and_isolates_failures():
    http = FakeHttp({"https://inst.org/vacancies": HTML})
    cfg = {"pages": [PAGE, {**PAGE, "name": "Broken", "url": "https://inst.org/broken"}]}
    result = PagesSource().fetch(cfg, http)
    assert len(result.listings) == 2
    assert len(result.errors) == 1 and "Broken" in result.errors[0]


def test_fetch_reports_zero_matches_as_error_so_selector_rot_is_visible():
    http = FakeHttp({"https://inst.org/vacancies": "<html><body>nothing</body></html>"})
    result = PagesSource().fetch({"pages": [PAGE]}, http)
    assert result.listings == []
    assert len(result.errors) == 1 and "no items" in result.errors[0]


def test_fetch_detail_returns_page_text_when_detail_enabled():
    src = PagesSource()
    page = "<html><body><main><h1>PhD</h1><p>Full text</p></main></body></html>"
    http = FakeHttp({"https://inst.org/jobs/1": page})
    assert "Full text" in src.fetch_detail("https://inst.org/jobs/1", http)


def test_manual_page_entries_become_digest_links_without_fetching():
    http = FakeHttp({})
    cfg = {
        "pages": [{"name": "Amsterdam UMC", "url": "https://werkenbij.example/v", "manual": True}]
    }
    result = PagesSource().fetch(cfg, http)
    assert http.calls == []
    assert result.manual_urls == {"Amsterdam UMC": "https://werkenbij.example/v"}
    assert result.errors == []


def test_allow_empty_suppresses_zero_match_error():
    http = FakeHttp({"https://inst.org/vacancies": "<html><body>No open positions</body></html>"})
    result = PagesSource().fetch({"pages": [{**PAGE, "allow_empty": True}]}, http)
    assert result.errors == [] and result.listings == []


def test_fetch_dedupes_same_vacancy_across_page_entries():
    http = FakeHttp({"https://inst.org/vacancies": HTML, "https://inst.org/vacancies?q=x": HTML})
    cfg = {"pages": [PAGE, {**PAGE, "url": "https://inst.org/vacancies?q=x"}]}
    result = PagesSource().fetch(cfg, http)
    assert len(result.listings) == 2
