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

    with make(handler, min_interval=0, sleep=lambda s: None) as http:
        with pytest.raises(httpx.HTTPStatusError):
            http.get_text("https://example.org/x")


def test_get_text_retries_server_errors_with_backoff():
    attempts = []
    sleeps = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, text="finally")

    with make(handler, min_interval=0, sleep=sleeps.append, clock=lambda: 0.0) as http:
        assert http.get_text("https://example.org/x") == "finally"
    assert len(attempts) == 3
    assert sleeps == [2.0, 4.0]  # backoff between retries


def test_get_text_does_not_retry_client_errors():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(404, text="nope")

    with make(handler, min_interval=0, sleep=lambda s: None) as http:
        with pytest.raises(httpx.HTTPStatusError):
            http.get_text("https://example.org/x")
    assert len(attempts) == 1


def test_get_text_gives_up_after_three_attempts():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503, text="down")

    with make(handler, min_interval=0, sleep=lambda s: None, clock=lambda: 0.0) as http:
        with pytest.raises(httpx.HTTPStatusError):
            http.get_text("https://example.org/x")
    assert len(attempts) == 3


def test_get_text_retries_429_honouring_retry_after():
    attempts = []
    sleeps = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, text="slow down", headers={"Retry-After": "7"})
        return httpx.Response(200, text="ok")

    with make(handler, min_interval=0, sleep=sleeps.append, clock=lambda: 0.0) as http:
        assert http.get_text("https://example.org/x") == "ok"
    assert sleeps == [7.0]


def test_user_agent_is_anonymous_without_contact():
    with make(lambda r: httpx.Response(200, text="ok")) as http:
        assert http.user_agent == "jobhunt/0.1 (personal job search)"


def test_user_agent_includes_contact_when_given():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, text="ok")

    with make(handler, contact="me@example.org") as http:
        http.get_text("https://example.org/")
    assert seen["ua"] == "jobhunt/0.1 (personal job search; contact: me@example.org)"
