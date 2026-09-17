from datetime import date
from pathlib import Path

import httpx
import pytest

from jobhunt.sources.linkedin import LinkedInSource, parse_detail, parse_search, search_url

FIX = Path(__file__).parent / "fixtures" / "linkedin"


@pytest.fixture(scope="module")
def search_html():
    return (FIX / "search.html").read_text()


@pytest.fixture(scope="module")
def detail_html():
    return (FIX / "detail.html").read_text()


def test_search_url_limits_to_recent_postings():
    url = search_url("cognitive science", "Netherlands")
    assert url.startswith("https://www.linkedin.com/jobs/search?")
    assert "keywords=cognitive+science" in url and "location=Netherlands" in url
    assert "f_TPR=r2592000" in url  # past 30 days


def test_parse_search_extracts_cards(search_html):
    listings = parse_search(search_html)
    assert len(listings) == 60
    first = listings[0]
    assert first.source == "linkedin"
    assert first.title == "Head of Sales Benelux"
    assert first.employer == "Merz Therapeutics"
    assert first.location == "Terheijden, North Brabant, Netherlands"
    assert first.posted == date(2026, 9, 9)
    assert (
        first.url
        == "https://nl.linkedin.com/jobs/view/head-of-sales-benelux-at-merz-therapeutics-4440742628"
    )
    assert first.raw["urn"] == "urn:li:jobPosting:4440742628"


def test_parse_search_strips_tracking_params_so_ids_are_stable(search_html):
    urls = [lst.url for lst in parse_search(search_html)]
    assert all("trackingId" not in u and "?" not in u for u in urls)


def test_parse_detail_returns_description_and_criteria(detail_html):
    text = parse_detail(detail_html)
    assert "Natus" in text and "Clinical Account Manager" in text
    assert "Senioriteitsniveau: Instapniveau" in text


def test_parse_detail_returns_empty_on_authwall():
    assert parse_detail("<html><body><h1>Sign in</h1><p>authwall</p></body></html>") == ""


class FakeHttp:
    def __init__(self, body=None, status=None):
        self.body, self.status, self.calls = body, status, []

    def get_text(self, url):
        self.calls.append(url)
        if self.status:
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                f"Client error '{self.status}' for url",
                request=req,
                response=httpx.Response(self.status, request=req),
            )
        return self.body


def test_fetch_dedupes_across_queries(search_html):
    http = FakeHttp(search_html)
    result = LinkedInSource().fetch(
        {"queries": ["neuroscience", "cognitive"], "location": "NL"}, http
    )
    assert len(http.calls) == 2
    assert len(result.listings) == 60
    assert result.errors == [] and result.manual_urls == {}


def test_fetch_when_blocked_reports_manual_url_instead_of_failing():
    http = FakeHttp(status=999)
    result = LinkedInSource().fetch({"queries": ["neuroscience"], "location": "Netherlands"}, http)
    assert result.listings == []
    assert len(result.errors) == 1 and "999" in result.errors[0]
    assert list(result.manual_urls) == ["LinkedIn: neuroscience"]
    assert "keywords=neuroscience" in result.manual_urls["LinkedIn: neuroscience"]


def test_fetch_when_page_has_no_cards_treats_as_blocked():
    http = FakeHttp("<html><body>Sign in to view</body></html>")
    result = LinkedInSource().fetch({"queries": ["x"], "location": "NL"}, http)
    assert result.listings == [] and result.manual_urls and result.errors
