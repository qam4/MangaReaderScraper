"""
Pytest fixtures
"""

import logging
from pathlib import Path
from unittest import mock

import pytest

from scraper.manga import Chapter, Manga, Page
from tests.helpers import MockedMangaReaderParser, get_images


@pytest.fixture(autouse=True)
def _no_real_browser():
    """
    Safety net: make any *unmocked* browser launch fail loudly instead of
    silently opening a real Chrome during the test run.

    Every BrowserFetcher path is supposed to be mocked; if a test misses one,
    we want an immediate error pointing at the gap -- not a browser window (and
    a 45s timeout). Patches the lazy entry points used by the fetcher/probe.
    """

    def _boom(*args, **kwargs):
        raise RuntimeError(
            "A test tried to launch a real browser (nodriver.start). "
            "Mock BrowserFetcher / nodriver in this test."
        )

    # BrowserFetcher._start and the probe both call nodriver.start; block both.
    with mock.patch("scraper.fetchers.BrowserFetcher._start", side_effect=_boom):
        yield


@pytest.fixture(autouse=True)
def mocked_pool_imap(request):
    """
    Run ``MangaBuilder``'s download ``ThreadPool`` synchronously by patching
    ``ThreadPool.imap`` -> ``map``, so the (deterministic) builder tests run the
    workers inline in a single thread.

    This is a TEST CONVENIENCE, not production behaviour: it removes any thread
    scheduling nondeterminism from the builder tests. A test that wants to
    exercise the REAL pool opts out with ``@pytest.mark.real_pool`` -- then the
    parent must assemble the Manga from the worker RETURN values, which is what
    the B2 redesign guarantees (and which the thread pool preserves).
    """
    if request.node.get_closest_marker("real_pool"):
        yield None
        return

    def pool_imap(self, func, iterable):
        return map(func, iterable)

    with mock.patch("scraper.manga.ThreadPool.imap", pool_imap) as mocked_func:
        yield mocked_func


@pytest.fixture(scope="session", autouse=True)
def mocked_manga_settings():
    """
    Mock settings config in manga module to point to /tmp/ dir

    This will be applied to every single test prior to execution
    """
    mock_config = mock.MagicMock(
        return_value={
            "config": {
                "manga_directory": "/tmp",
                "source": "mangabuddy",
                "filetype": "pdf",
                "upload_root": "/",
            }
        }
    )
    with mock.patch("scraper.manga.settings", mock_config) as mocked_config:
        yield mocked_config


@pytest.fixture(scope="session", autouse=True)
def mocked_uploader_settings():
    config = {
        "email": True,
        "password": True,
        "token": True,
    }

    with mock.patch(
        "scraper.uploaders.base.BaseUploader._get_config", return_value=config
    ) as cfg:
        yield cfg


@pytest.fixture(scope="session", autouse=True)
def mocked_manga_env_var_cli():
    mock_settings = {
        "manga_directory": "/tmp",
        "source": "mangabuddy",
        "filetype": "pdf",
        "upload_root": "/",
    }

    with mock.patch("scraper.__main__.CONFIG", mock_settings) as mocked_settings:
        yield mocked_settings


@pytest.fixture
def parser():
    return MockedMangaReaderParser


@pytest.fixture
def page():
    return Page(1, b"data")


@pytest.fixture
def chapter():
    img1, img2 = get_images()
    page_data = [(1, img1, "success"), (2, img2, "success")]
    chapter = Chapter("1", Path("/Some/path"), Path("/some/path"))
    chapter.pages = page_data
    return chapter


@pytest.fixture
def manga():
    manga = Manga("dragon-ball", "pdf")
    manga.chapters = ["1", "2"]
    manga.chapters_dict["1"].pages = [(1, b"here", "success"), (2, b"bye", "success")]
    manga.chapters_dict["2"].pages = [
        (1, b"hello", "success"),
        (2, b"jimmy", "success"),
    ]
    return manga


@pytest.fixture
def logger():
    return logging.getLogger("unittest_logger")
