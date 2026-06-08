from pathlib import Path
from unittest import mock

import requests  # type: ignore
from bs4 import BeautifulSoup

from scraper.exceptions import MangaDoesNotExist
from scraper.fetchers import fetch_soup
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers._html import attr
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser

# ---------------------------------------------------------------------------
# R1 -- engine tier: a synthetic, site-INDEPENDENT parser used to test the
# shared base-class behaviour (BaseSiteParser wiring, the fetch_soup -> 404 ->
# MangaDoesNotExist contract) WITHOUT coupling those tests to any real site's
# markup. "If a site died tomorrow, this test still means something." Real
# sites' specifics are covered by their own fixture-backed test_<site>.py.
# ---------------------------------------------------------------------------


class EngineMangaParser(BaseMangaParser):
    """Minimal manga parser exercising the shared base + fetch contract."""

    base_url = "https://engine.test"

    def __init__(self, manga_url=None, base_url=None):
        super().__init__(manga_url, base_url or self.base_url)

    def _manga_page_url(self) -> str:
        return f"{self.base_url}/{self.manga_url}"

    def volume_url(self, volume: str) -> str:
        return f"{self.base_url}/{self.manga_url}/{volume}"

    def _fetch(self, url):
        try:
            return fetch_soup(url)
        except requests.exceptions.HTTPError as err:
            if err.response is not None and err.response.status_code == 404:
                raise MangaDoesNotExist(self.manga_url)
            raise

    def all_volume_ids(self):
        soup = self._fetch(self._manga_page_url())
        return [attr(a, "href") for a in soup.find_all("a", href=True)]

    def page_urls(self, volume: str):
        soup = self._fetch(self.volume_url(volume))
        return list(enumerate([attr(i, "src") for i in soup.find_all("img")], start=1))


class EngineSearchParser(BaseSearchParser):
    """Trivial search parser for the synthetic engine site."""

    def search(self, start: int = 1) -> SearchResults:
        return {}


class EngineSiteParser(BaseSiteParser):
    """Synthetic site parser (no real site) for engine-tier base-class tests."""

    def __init__(self, manga_url=None):
        super().__init__(
            manga_url=manga_url,
            base_url="https://engine.test",
            manga_parser=EngineMangaParser,
            search_parser=EngineSearchParser,
        )


# Engine-tier parametrization: the synthetic parser stands in for "any parser",
# so test_parsers.py asserts base behaviour without depending on a live site.
ALL_SCRAPERS = [EngineSiteParser]
ALL_PARSERS = [EngineMangaParser]
ALL_SCRAPERS_AND_PARSERS = [
    (scraper, parser) for scraper, parser in zip(ALL_SCRAPERS, ALL_PARSERS)
]

# used as a mocked output for MangaReaderSearch.search()
METADATA = {
    "1": SearchResult(
        title="Dragon Ball: Episode of Bardock",
        manga_url="dragon-ball-episode-of-bardock",
        latest_chapter="3",
        source="mangareader",
    ),
    "2": SearchResult(
        title="Dragon Ball SD",
        manga_url="dragon-ball-sd",
        latest_chapter="35",
        source="mangareader",
    ),
    "3": SearchResult(
        title="DragonBall Next Gen",
        manga_url="dragonball-next-gen",
        latest_chapter="4",
        source="mangareader",
    ),
    "4": SearchResult(
        title="Dragon Ball",
        manga_url="dragon-ball",
        latest_chapter="520",
        source="mangareader",
    ),
    "5": SearchResult(
        title="Dragon Ball Z - Rebirth of F",
        manga_url="dragon-ball-z-rebirth-of-f",
        latest_chapter="3",
        source="mangareader",
    ),
    "6": SearchResult(
        title="Dragon Ball Super",
        manga_url="dragon-ball-super",
        latest_chapter="62",
        source="mangareader",
    ),
}


TABLE = (
    "+----+---------------------------------+-----------------+-------------+\n"
    "|    | Title                           |   Latest Volume | Source      |\n"
    "|----+---------------------------------+-----------------+-------------|\n"
    "|  1 | Dragon Ball: Episode of Bardock |               3 | mangareader |\n"
    "|  2 | Dragon Ball SD                  |              35 | mangareader |\n"
    "|  3 | DragonBall Next Gen             |               4 | mangareader |\n"
    "|  4 | Dragon Ball                     |             520 | mangareader |\n"
    "|  5 | Dragon Ball Z - Rebirth of F    |               3 | mangareader |\n"
    "|  6 | Dragon Ball Super               |              62 | mangareader |\n"
    "+----+---------------------------------+-----------------+-------------+"
)


class MockedImgResponse:
    """
    Returns a mocked response with manga img as content
    """

    def __init__(self):
        with open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb") as f:
            self.content = f.read()
            self.status_code = 200


def mocked_request_session(*args, **kwargs):
    """
    Returns a mock that stands in for ``utils.request_session()``.

    ``base.page_data`` does ``with request_session() as session: session.get(...)``,
    so the mock is a context manager whose ``.get`` returns a MockedImgResponse
    (a real JPEG). Used to patch ``scraper.parsers.base.request_session`` in the
    page_data tests, which previously mocked the wrong target and fell through to
    a real network call.
    """
    session = mock.MagicMock()
    session.__enter__.return_value = session
    session.get.return_value = MockedImgResponse()
    return session


class MockedMangaReaderParser:
    """
    Mocks MangaReaderMangaParser

    Can't use MagicMock as it results in a pickling error when used with
    multiprocessig, hence the need for this hack.
    """

    def __init__(self, manga_url, base_url="www.nothing.com"):
        self.manga_url = manga_url
        self.base_url = base_url

    def all_volume_ids(self):
        return ["1", "2", "3"]

    def volume_url(self, volume):
        return f"http://mangareader.net/dragon-ball-episode-of-bardock/{volume}"

    def page_urls(self, volume):
        return [
            f"http://mangareader.net/dragon-ball-episode-of-bardock/{volume}",
            f"http://mangareader.net/dragon-ball-episode-of-bardock/{volume}/2",
        ]

    def page_data(self, page_url):
        volume_num, page_num = page_url.split("/")[-2:]
        if not volume_num.isdigit():
            page_num = "1"
        img = open(f"tests/test_files/jpgs/test-manga_1_{page_num}.jpg", "rb").read()
        return (int(page_num), img, "success")


class MockedSearch:
    """
    Mocks SearchParser
    """

    def __init__(self, *args, **kwargs):
        pass

    def search(*args):
        return METADATA


class MockedSiteParser(BaseSiteParser):
    """
    A poor mock of the MangaReaderSiteParser
    """

    def __init__(self, manga_url="dragon-ball"):
        super().__init__(
            manga_url=manga_url,
            base_url="www.nothing.com",
            manga_parser=MockedMangaReaderParser,
            search_parser=MockedSearch,
        )


class MockedPyCloud:
    listed = {"error": True, "result": 2005}

    def __init__(self, *args, **kwargs):
        pass

    def createfolder(self, *args, **kwargs):
        return {"result": 0, "metadata": "path"}

    def listfolder(self, *args, **kwargs):
        return self.listed

    def uploadfile(self, *args, **kwargs):
        return {"status": "success"}


class MockedPyCloudFail(MockedPyCloud):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def createfolder(self, *args, **kwargs):
        return {"error": "directory already exists"}

    def uploadfile(self, *args, **kwargs):
        return {"error": "something happened"}


class MockedDropbox:
    def __init__(self, *args, **kwargs):
        self.match_found = True
        self.file = "/non-existing/file.txt"

    def files_search(self, *args, **kwargs):
        searcher = mock.MagicMock()
        searcher.matches = self.match_found
        return searcher

    def files_upload(self, *args, **kwargs):
        response = mock.MagicMock()
        response.path_lower = self.file
        response.text = "success"
        return response


class MockedDropboxRealFile(MockedDropbox):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.match_found = False
        self.file = "tests/test_files/mangakaka/dragonball_super_page.html"


def setup_uploader(uploader):
    upl = uploader()
    manga = mock.MagicMock()
    manga.name = mock.Mock(return_value="hiya")
    upl._setup_adapter(manga)
    return upl


def as_search_results(d):
    """Convert a {key: {field: value}} dict into {key: SearchResult} for tests
    that still spell their expected metadata as plain dicts."""
    return {k: SearchResult(**v) for k, v in d.items()}


def get_bs4_tree(filepath):
    html_string = Path(filepath).read_text(encoding="utf-8")
    html = BeautifulSoup(html_string, features="lxml")
    return html


def get_images():
    """
    Opens a list of two jpeg images
    """
    return [
        Path(f"tests/test_files/jpgs/test-manga_1_{n}.jpg").read_bytes() for n in [1, 2]
    ]
