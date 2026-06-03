from unittest import mock

import pytest
import requests  # type: ignore

from scraper.exceptions import MangaDoesNotExist, MangaParserNotSet
from tests.helpers import ALL_PARSERS, ALL_SCRAPERS, ALL_SCRAPERS_AND_PARSERS


def _fake_curlcffi(status_code):
    """Build a fake ``curl_cffi`` module whose Session.get returns a response
    with the given status (and empty body), so fetch_soup's default backend
    raises the same HTTPError path the parsers handle."""
    resp = mock.Mock(status_code=status_code, text="", url="http://x")
    resp.cookies = {}
    session = mock.Mock()
    session.get.return_value = resp
    creq = mock.Mock()
    creq.Session.return_value = session
    return mock.Mock(requests=creq)


@pytest.mark.parametrize("siteparser", ALL_SCRAPERS)
def test_manga_not_set_error(siteparser):
    mr = siteparser()
    with pytest.raises(MangaParserNotSet):
        mr.manga


@pytest.mark.parametrize(
    "siteparser,mangaparser",
    ALL_SCRAPERS_AND_PARSERS,
)
def test_initialises_manga_parser(siteparser, mangaparser):
    mr = siteparser("dragon-ball")
    manga_parser = mr.manga
    assert isinstance(manga_parser, mangaparser)
    assert manga_parser.manga_url == "dragon-ball"


@pytest.mark.parametrize("siteparser,mangaparser", ALL_SCRAPERS_AND_PARSERS)
def test_set_manga_parser(siteparser, mangaparser):
    mr = siteparser()
    mr.manga = "dragon-ball"
    manga_parser = mr.manga
    assert isinstance(manga_parser, mangaparser)
    assert manga_parser.manga_url == "dragon-ball"


@pytest.mark.parametrize("mangaparser", ALL_PARSERS)
def test_404_errors(mangaparser):
    # fetch_soup now defaults to CurlCffiFetcher, so simulate the HTTP error at
    # the curl_cffi layer (the prior mock of scraper.utils.requests.get only
    # intercepted the old RequestsFetcher default).
    with mock.patch.dict("sys.modules", {"curl_cffi": _fake_curlcffi(404)}):
        parser = mangaparser("blahblahblah")
        with pytest.raises(MangaDoesNotExist):
            parser.all_volume_ids()
        with pytest.raises(MangaDoesNotExist):
            parser.page_urls("1")


@pytest.mark.parametrize("mangaparser", ALL_PARSERS)
def test_non_404_errors(mangaparser):
    with mock.patch.dict("sys.modules", {"curl_cffi": _fake_curlcffi(403)}):
        parser = mangaparser("blahblahblah")
        with pytest.raises(requests.exceptions.HTTPError):
            parser.all_volume_ids()
