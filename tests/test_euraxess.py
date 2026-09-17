from datetime import date
from pathlib import Path

import pytest

from jobhunt.sources.euraxess import (
    EuraxessSource,
    parse_deadline,
    parse_detail,
    parse_search,
    search_url,
)

FIX = Path(__file__).parent / "fixtures" / "euraxess"


@pytest.fixture(scope="module")
def search_html():
    return (FIX / "search.html").read_text()


@pytest.fixture(scope="module")
def detail_html():
    return (FIX / "detail.html").read_text()


def test_search_url_uses_country_facet_and_page():
    url = search_url("neuroscience", country_id=798, page=2)
    assert url.startswith("https://euraxess.ec.europa.eu/jobs/search?")
    assert "keywords=neuroscience" in url
    assert "f%5B0%5D=job_country%3A798" in url
    assert "page=2" in url


def test_parse_deadline_strips_time_and_zone():
    assert parse_deadline("Application Deadline: 24 Sep 2026 - 23:59 (Europe/Andorra)") == date(
        2026, 9, 24
    )
    assert parse_deadline("nothing here") is None


def test_parse_search_extracts_cards(search_html):
    listings = parse_search(search_html)
    assert len(listings) == 10
    first = listings[0]
    assert first.source == "euraxess"
    assert (
        first.title == "Predoctoral Researcher (Translational Molecular Pathology Research Group)"
    )
    assert first.employer == "Fundació Hospital Universitari Vall d'Hebron- Institut de recerca"
    assert first.url == "https://euraxess.ec.europa.eu/jobs/466609"
    assert first.posted == date(2026, 9, 17)
    assert first.deadline == date(2026, 9, 24)
    assert first.location == "Spain"
    assert first.summary.startswith("The Translational Molecular Pathology Laboratory")
    assert first.raw["research_field"] == ["Biological sciences", "Biology"]
    assert first.raw["profile"] == ["First Stage Researcher (R1)"]


def test_parse_search_country_from_multi_part_location(search_html):
    assert parse_search(search_html)[1].location == "Iceland"


def test_parse_search_empty():
    assert parse_search("<html><body></body></html>") == []


def test_parse_detail_keeps_relevant_sections_only(detail_html):
    text = parse_detail(detail_html)
    assert text.startswith("## Job Information")
    assert "Research Field" in text and "Portugal" in text
    assert "## Offer Description" in text and "Universidade do Minho" in text
    assert "## Requirements" in text
    assert "## Where to apply" not in text
    assert "## Contact" not in text


class FakeHttp:
    def __init__(self, pages: dict[int, str]):
        self.pages = pages
        self.calls: list[str] = []

    def get_text(self, url):
        self.calls.append(url)
        import re

        page = int(re.search(r"page=(\d+)", url).group(1))
        if page not in self.pages:
            return "<html><body></body></html>"
        return self.pages[page]


def test_fetch_pages_until_empty_and_filters_country(search_html):
    http = FakeHttp({0: search_html, 1: search_html})
    src = EuraxessSource()
    cfg = {"country": "Spain", "country_id": 724, "keywords": ["neuro"], "pages": 5}
    result = src.fetch(cfg, http)
    assert len(http.calls) == 3  # page 0, 1, then empty page 2 stops the loop
    assert result.errors == []
    assert result.listings and all(lst.location == "Spain" for lst in result.listings)
    assert len({lst.id for lst in result.listings}) == len(result.listings)


def test_fetch_respects_page_limit(search_html):
    http = FakeHttp({i: search_html for i in range(10)})
    EuraxessSource().fetch({"country": "Spain", "keywords": ["x"], "pages": 2}, http)
    assert len(http.calls) == 2


def test_fetch_default_country_is_netherlands(search_html):
    http = FakeHttp({0: search_html})
    result = EuraxessSource().fetch({"keywords": ["x"]}, http)
    assert "job_country%3A798" in http.calls[0]
    assert result.listings == []  # fixture has no NL jobs → all filtered out, no error
