"""
Tests for scraper.fetchers.

Covers the parts that don't need a real browser: FetchResult behaviour, the
pure JS/predicate helpers, and the http backends (requests/cloudscraper mocked).
BrowserFetcher's nodriver-driven async paths are exercised only through the
MangaFire parser's (mocked) integration tests, since they require a live browser.
"""

import json
from unittest import mock

import pytest

from scraper.fetchers import (
    BrowserFetcher,
    CloudscraperFetcher,
    CurlCffiFetcher,
    Fetcher,
    FetchResult,
    RequestsFetcher,
    _in_page_fetch_js,
    _looks_like_challenge,
    _make_marker_predicate,
    download_image,
    fetch_soup,
)

# ============================== FetchResult ==============================


def test_fetchresult_ok_true_for_2xx():
    assert FetchResult("u", 200, "body").ok
    assert FetchResult("u", 204, "").ok
    assert not FetchResult("u", 404, "").ok
    assert not FetchResult("u", 500, "").ok


def test_fetchresult_json_parses_body():
    res = FetchResult("u", 200, '{"result": {"images": [1, 2]}}')
    assert res.json() == {"result": {"images": [1, 2]}}


def test_fetchresult_raise_for_status_raises_on_error():
    import requests

    res = FetchResult("http://x", 503, "")
    with pytest.raises(requests.exceptions.HTTPError):
        res.raise_for_status()


def test_fetchresult_raise_for_status_silent_on_ok():
    FetchResult("http://x", 200, "ok").raise_for_status()  # no raise


# ============================ pure helpers ===============================


def test_in_page_fetch_js_embeds_url_safely():
    js = _in_page_fetch_js("https://x/ajax?vrf=a&b='c")
    # the url must be json-encoded (quoted) inside the fetch call
    assert json.dumps("https://x/ajax?vrf=a&b='c") in js
    assert "credentials: 'include'" in js
    assert "X-Requested-With" in js


def test_make_marker_predicate_matches_any_marker():
    pred = _make_marker_predicate(("ajax/read/chapter", "ajax/read/volume"))
    assert pred("https://site/ajax/read/chapter/123?vrf=x")
    assert pred("https://site/ajax/read/volume/9")
    assert not pred("https://site/ajax/manga/search")


@pytest.mark.parametrize(
    "html",
    [
        "<title>Just a moment...</title>",
        "<html><body>Checking your browser before accessing</body></html>",
        "<div id='cf-browser-verification'></div>",
        "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate'></script>",
        "<h1>Verifying you are human</h1>",
        "Please enable JavaScript and cookies to continue",
        "",
        "   \n  ",
    ],
)
def test_looks_like_challenge_true_for_interstitials(html):
    assert _looks_like_challenge(html)


@pytest.mark.parametrize(
    "html",
    [
        "<div class='chapter-list'><a href='/manga/x/chapter-1'>Chapter 1</a></div>",
        "<div class='story_item'><h3 class='story_name'>Naruto</h3></div>",
        "<html><body><img src='page1.jpg'></body></html>",
    ],
)
def test_looks_like_challenge_false_for_real_content(html):
    assert not _looks_like_challenge(html)


# ============================ http backends ==============================


def test_requests_fetcher_maps_response():
    fake = mock.Mock(status_code=200, text="<html>", url="http://x/final")
    fake.cookies = {"cf": "1"}
    with mock.patch("requests.get", return_value=fake) as g:
        res = RequestsFetcher().get("http://x", headers={"H": "v"}, timeout=12)
    g.assert_called_once_with("http://x", headers={"H": "v"}, timeout=12)
    assert res.status == 200
    assert res.text == "<html>"
    assert res.final_url == "http://x/final"
    assert res.cookies == {"cf": "1"}
    assert res.ok


def test_requests_fetcher_does_not_raise_on_404():
    fake = mock.Mock(status_code=404, text="nope", url="http://x")
    fake.cookies = {}
    with mock.patch("requests.get", return_value=fake):
        res = RequestsFetcher().get("http://x")
    assert res.status == 404
    assert not res.ok


def test_cloudscraper_fetcher_maps_response():
    fake = mock.Mock(status_code=200, text="cf-cleared", url="http://x")
    fake.cookies = {}
    scraper = mock.Mock()
    scraper.get.return_value = fake
    fake_module = mock.Mock()
    fake_module.create_scraper.return_value = scraper
    with mock.patch.dict("sys.modules", {"cloudscraper": fake_module}):
        res = CloudscraperFetcher().get("http://x")
    assert res.text == "cf-cleared"
    assert res.ok


def test_curlcffi_fetcher_maps_response():
    fake = mock.Mock(status_code=200, text="<html>", url="http://x/final")
    fake.cookies = {"cf": "1"}
    session = mock.Mock()
    session.get.return_value = fake
    creq = mock.Mock()
    creq.Session.return_value = session
    fake_module = mock.Mock(requests=creq)
    with mock.patch.dict("sys.modules", {"curl_cffi": fake_module}):
        res = CurlCffiFetcher().get("http://x", headers={"H": "v"}, timeout=12)
    # impersonates a real browser fingerprint by default
    creq.Session.assert_called_once_with(impersonate="chrome")
    session.get.assert_called_once_with("http://x", headers={"H": "v"}, timeout=12)
    assert res.status == 200
    assert res.text == "<html>"
    assert res.final_url == "http://x/final"
    assert res.cookies == {"cf": "1"}
    assert res.ok


def test_curlcffi_fetcher_does_not_raise_on_404():
    fake = mock.Mock(status_code=404, text="nope", url="http://x")
    fake.cookies = {}
    session = mock.Mock()
    session.get.return_value = fake
    creq = mock.Mock()
    creq.Session.return_value = session
    fake_module = mock.Mock(requests=creq)
    with mock.patch.dict("sys.modules", {"curl_cffi": fake_module}):
        res = CurlCffiFetcher().get("http://x")
    assert res.status == 404
    assert not res.ok


# ============================ download_image =============================


def _fake_curl_module(responses):
    """Build a fake ``curl_cffi`` module whose Session.get returns the queued
    responses in order; a response that is an Exception is raised instead."""
    session = mock.Mock()

    def _get(url, headers=None, timeout=None):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    session.get.side_effect = _get
    creq = mock.Mock()
    creq.Session.return_value = session
    return mock.Mock(requests=creq), session


def test_download_image_returns_bytes_on_success():
    ok = mock.Mock(status_code=200, content=b"\xff\xd8jpeg")
    module, session = _fake_curl_module([ok])
    with mock.patch.dict("sys.modules", {"curl_cffi": module}):
        out = download_image("http://cdn/p.jpg", headers={"Referer": "http://x/"})
    assert out == b"\xff\xd8jpeg"
    session.get.assert_called_once_with(
        "http://cdn/p.jpg", headers={"Referer": "http://x/"}, timeout=60
    )


def test_download_image_retries_then_succeeds():
    responses = [
        mock.Mock(status_code=503, content=b""),
        Exception("connection reset"),
        mock.Mock(status_code=200, content=b"img"),
    ]
    module, session = _fake_curl_module(responses)
    # patch sleep so the backoff doesn't actually wait during the test
    with mock.patch.dict("sys.modules", {"curl_cffi": module}):
        with mock.patch("time.sleep") as slept:
            out = download_image("http://cdn/p.jpg")
    assert out == b"img"
    assert session.get.call_count == 3
    # backoff slept between the 2 failed attempts (not after the success)
    assert slept.call_count == 2


def test_download_image_returns_none_when_exhausted():
    responses = [mock.Mock(status_code=403, content=b"") for _ in range(5)]
    module, session = _fake_curl_module(responses)
    with mock.patch.dict("sys.modules", {"curl_cffi": module}):
        with mock.patch("time.sleep") as slept:
            out = download_image("http://cdn/p.jpg", max_tries=5)
    assert out is None
    assert session.get.call_count == 5
    # slept between attempts but NOT after the final one: max_tries - 1
    assert slept.call_count == 4


def test_download_image_backoff_grows_between_attempts():
    # the wait before each retry must increase (exponential) -- the F1 fix for
    # the old no-sleep loop that hammered the CDN and left chapters incomplete.
    responses = [mock.Mock(status_code=503, content=b"") for _ in range(4)]
    responses.append(mock.Mock(status_code=200, content=b"img"))
    module, _session = _fake_curl_module(responses)
    with mock.patch.dict("sys.modules", {"curl_cffi": module}):
        with mock.patch("time.sleep") as slept:
            out = download_image(
                "http://cdn/p.jpg", backoff_base=1.0, backoff_cap=100.0
            )
    assert out == b"img"
    waits = [call.args[0] for call in slept.call_args_list]
    assert len(waits) == 4
    # each wait is >= the exponential floor (base * 2**n), jitter only adds;
    # and the sequence is strictly increasing across these (no jitter overlap
    # since each floor doubles and jitter is at most half the delay)
    assert waits == sorted(waits)
    assert waits[0] >= 1.0 and waits[1] >= 2.0 and waits[2] >= 4.0 and waits[3] >= 8.0


def test_download_image_updates_session_cookies():
    ok = mock.Mock(status_code=200, content=b"img")
    module, session = _fake_curl_module([ok])
    with mock.patch.dict("sys.modules", {"curl_cffi": module}):
        download_image("http://cdn/p.jpg", cookies={"cf_clearance": "abc"})
    session.cookies.update.assert_called_once_with({"cf_clearance": "abc"})


# ============================ protocol check =============================


def test_backends_satisfy_fetcher_protocol():
    # runtime_checkable Protocol: each backend has a .get
    assert isinstance(CurlCffiFetcher(), Fetcher)
    assert isinstance(RequestsFetcher(), Fetcher)
    assert isinstance(CloudscraperFetcher(), Fetcher)
    assert isinstance(BrowserFetcher(), Fetcher)


def test_fetch_soup_defaults_to_curlcffi():
    # fetch_soup with no fetcher should use CurlCffiFetcher (the browser-
    # fingerprint default), not plain requests.
    captured = {}

    class _Spy:
        def get(self, url):
            captured["url"] = url
            return FetchResult(url, 200, "<html><body>hi</body></html>")

    with mock.patch("scraper.fetchers.CurlCffiFetcher", return_value=_Spy()) as ctor:
        soup = fetch_soup("http://x")
    ctor.assert_called_once_with()
    assert captured["url"] == "http://x"
    assert soup.body.text == "hi"
