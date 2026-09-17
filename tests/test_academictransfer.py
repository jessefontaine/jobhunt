from datetime import date
from pathlib import Path

import pytest

from jobhunt.sources.academictransfer import (
    AcademicTransferSource,
    parse_date,
    parse_detail,
    parse_search,
)

FIX = Path(__file__).parent / "fixtures" / "academictransfer"
TODAY = date(2026, 9, 17)


@pytest.fixture(scope="module")
def search_html():
    return (FIX / "search.html").read_text()


@pytest.fixture(scope="module")
def detail_html():
    return (FIX / "detail.html").read_text()


def test_parse_date_handles_absolute_and_relative():
    assert parse_date("26 Oct ’26", TODAY) == date(2026, 10, 26)
    assert parse_date("4 Oct '26", TODAY) == date(2026, 10, 4)
    assert parse_date("yesterday", TODAY) == date(2026, 9, 16)
    assert parse_date("today", TODAY) == TODAY
    assert parse_date("garbage", TODAY) is None


def test_parse_search_extracts_all_cards(search_html):
    listings = parse_search(search_html, TODAY)
    assert len(listings) == 10
    assert all(lst.source == "academictransfer" for lst in listings)
    assert all(lst.url.startswith("https://www.academictransfer.com/en/jobs/") for lst in listings)


def test_parse_search_first_card_fields(search_html):
    first = parse_search(search_html, TODAY)[0]
    assert first.title == "PhD student cerebellar morphology"
    assert first.employer == "Royal Netherlands Academy of Arts and Sciences (KNAW)"
    assert first.location == "Amsterdam"
    assert first.deadline == date(2026, 10, 26)
    assert first.posted == date(2026, 9, 16)  # "yesterday"
    assert first.summary.startswith("About the project The human cerebellum")
    assert (
        first.url
        == "https://www.academictransfer.com/en/jobs/364001/phd-student-cerebellar-morphology/"
    )


def test_parse_search_absolute_dates(search_html):
    ra = parse_search(search_html, TODAY)[6]
    assert ra.title.startswith("Research Assistant at the Donders Centre")
    assert ra.employer == "Radboud University"
    assert ra.location == "Nijmegen"
    assert ra.deadline == date(2026, 9, 22)
    assert ra.posted == date(2026, 9, 10)


def test_parse_search_on_empty_page_returns_nothing():
    assert parse_search("<html><body><p>No results</p></body></html>", TODAY) == []


def test_parse_detail_returns_metadata_and_sections(detail_html):
    text = parse_detail(detail_html)
    assert "Research fields: Psychological sciences" in text
    assert "Salary indication: €3202—€4159 per month" in text
    assert "Job description" in text
    assert "conversational repair" in text
    assert "Requirements" in text
    assert "Interesting for you" not in text
    assert "Apply now" not in text


class FakeHttp:
    """Serves fixture HTML by URL substring; records requests."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        for key, body in self.routes.items():
            if key in url:
                return body
        raise RuntimeError(f"unexpected url {url}")


def test_fetch_runs_each_query_sorted_by_published_and_dedupes(search_html):
    http = FakeHttp({"/en/jobs/?": search_html})
    result = AcademicTransferSource().fetch({"queries": ["neuroscience", "fMRI"]}, http)
    assert len(http.calls) == 2
    assert all("order=published" in u for u in http.calls)
    assert any("q=neuroscience" in u for u in http.calls) and any("q=fMRI" in u for u in http.calls)
    assert len(result.listings) == 10  # same page twice → deduped by id
    assert result.errors == []


def test_fetch_reports_query_failure_and_continues(search_html):
    class Flaky(FakeHttp):
        def get_text(self, url):
            if "q=boom" in url:
                raise RuntimeError("503")
            return super().get_text(url)

    http = Flaky({"/en/jobs/?": search_html})
    result = AcademicTransferSource().fetch({"queries": ["boom", "neuroscience"]}, http)
    assert len(result.listings) == 10
    assert len(result.errors) == 1 and "boom" in result.errors[0]


def test_fetch_detail_returns_description(detail_html):
    http = FakeHttp({"/en/jobs/363874/": detail_html})
    text = AcademicTransferSource().fetch_detail(
        "https://www.academictransfer.com/en/jobs/363874/x/", http
    )
    assert "conversational repair" in text
