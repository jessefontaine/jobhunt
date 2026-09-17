import httpx
import pytest

from jobhunt.sources.http import PoliteClient


def make(handler, **kw):
    transport = httpx.MockTransport(handler)
    return PoliteClient(transport=transport, **kw)


def test_get_text_sends_user_agent_and_returns_body():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, text="<html>hi</html>")

    with make(handler) as http:
        assert http.get_text("https://example.org/x") == "<html>hi</html>"
    assert seen["ua"].startswith("jobhunt/")


def test_get_text_waits_min_interval_between_requests():
    clock = {"now": 100.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock["now"] += s

    def handler(request):
        return httpx.Response(200, text="ok")

    with make(handler, min_interval=1.0, clock=lambda: clock["now"], sleep=sleep) as http:
        http.get_text("https://example.org/1")
        clock["now"] += 0.25
        http.get_text("https://example.org/2")
        clock["now"] += 5
        http.get_text("https://example.org/3")
    assert sleeps == [pytest.approx(0.75)]


def test_get_text_raises_on_http_error():
    def handler(request):
        return httpx.Response(503, text="nope")

    with make(handler) as http:
        with pytest.raises(httpx.HTTPStatusError):
            http.get_text("https://example.org/x")
