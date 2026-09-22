"""The `manual` source: one listing built from a link the user pasted."""

from pathlib import Path

import pytest

from jobhunt.sources.manual import ManualFetchError, fetch_listing, parse_listing

FIXTURE = (Path(__file__).parent / "fixtures" / "manual" / "vacancy.html").read_text()
URL = "https://example.org/jobs/phd-cortical-maps"


def test_parse_takes_the_title_from_the_heading():
    assert parse_listing(URL, FIXTURE).title == "PhD position: cortical maps"


def test_parse_takes_the_employer_from_the_site_name():
    assert parse_listing(URL, FIXTURE).employer == "Example Institute"


def test_parse_falls_back_to_the_domain_for_the_employer():
    html = "<html><head><title>A job</title></head><body><h1>A job</h1></body></html>"
    assert parse_listing("https://www.uu.nl/vac/1", html).employer == "uu.nl"


def test_parse_falls_back_to_the_page_title_without_the_site_suffix():
    html = "<html><head><title>Postdoc in vision | Careers</title></head><body></body></html>"
    assert parse_listing(URL, html).title == "Postdoc in vision"


def test_parse_keeps_the_body_text_and_drops_scripts_and_nav():
    listing = parse_listing(URL, FIXTURE)
    assert "7T fMRI" in listing.description
    assert "tracking" not in listing.description
    assert "Home Jobs" not in listing.description


def test_parse_marks_the_listing_as_manual_with_the_url_kept():
    listing = parse_listing(URL, FIXTURE)
    assert listing.source == "manual"
    assert listing.url == URL
    assert listing.summary and len(listing.summary) <= 300


def test_parse_rejects_a_page_with_no_usable_title():
    with pytest.raises(ManualFetchError, match="no title"):
        parse_listing(URL, "<html><body><p>hi</p></body></html>")


class FakeClient:
    def __init__(self, result):
        self.result = result

    def get_text(self, url):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_fetch_listing_reads_the_page():
    listing = fetch_listing(URL, FakeClient(FIXTURE))
    assert listing.title == "PhD position: cortical maps"


def test_fetch_listing_explains_a_block_instead_of_working_around_it():
    import httpx

    response = httpx.Response(403, request=httpx.Request("GET", URL))
    error = httpx.HTTPStatusError("403", request=response.request, response=response)
    with pytest.raises(ManualFetchError) as exc:
        fetch_listing(URL, FakeClient(error))
    assert "403" in str(exc.value)
    assert "--title" in str(exc.value)
