import httpx

from jobhunt.sources.indeed import IndeedSource, search_url


class Blocked:
    def get_text(self, url):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))


def test_search_url_defaults_to_global_domain():
    assert (
        search_url("neuroscience", "Nederland")
        == "https://www.indeed.com/jobs?q=neuroscience&l=Nederland"
    )


def test_search_url_uses_given_domain():
    assert (
        search_url("neuroscience", "Nederland", domain="nl.indeed.com")
        == "https://nl.indeed.com/jobs?q=neuroscience&l=Nederland"
    )


def test_indeed_is_manual_only_with_one_link_per_query():
    result = IndeedSource().fetch(
        {
            "queries": ["neuroscience", "cognitive"],
            "location": "Nederland",
            "domain": "nl.indeed.com",
        },
        Blocked(),
    )
    assert result.listings == []
    assert result.errors == []
    assert result.manual_urls == {
        "Indeed: neuroscience": "https://nl.indeed.com/jobs?q=neuroscience&l=Nederland",
        "Indeed: cognitive": "https://nl.indeed.com/jobs?q=cognitive&l=Nederland",
    }


def test_indeed_without_domain_or_location_links_global_site():
    result = IndeedSource().fetch({"queries": ["x"]}, Blocked())
    assert result.manual_urls == {"Indeed: x": "https://www.indeed.com/jobs?q=x&l="}
