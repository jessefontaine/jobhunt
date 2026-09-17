import httpx

from jobhunt.sources.indeed import IndeedSource, search_url


class Blocked:
    def get_text(self, url):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))


def test_search_url():
    assert (
        search_url("neuroscience", "Nederland")
        == "https://nl.indeed.com/jobs?q=neuroscience&l=Nederland"
    )


def test_indeed_is_manual_only_with_one_link_per_query():
    result = IndeedSource().fetch(
        {"queries": ["neuroscience", "cognitive"], "location": "Nederland"}, Blocked()
    )
    assert result.listings == []
    assert result.errors == []
    assert result.manual_urls == {
        "Indeed: neuroscience": "https://nl.indeed.com/jobs?q=neuroscience&l=Nederland",
        "Indeed: cognitive": "https://nl.indeed.com/jobs?q=cognitive&l=Nederland",
    }
